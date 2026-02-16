"""
Tenant-Enable — Integration Tests (ASGI TestClient).

4 scenarios:
  I1: OpsGuard deny bypass — rate limit → 429, decision layer not invoked
  I2: Tenant shadow — X-Tenant-Id header, shadow mode, passthrough on BLOCK
  I3: Tenant enforce — X-Tenant-Id header, enforce mode, 503 on BLOCK
  I4: Unknown tenant → metric label "_other"

Feature: tenant-enable, Task 9.1
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app.guard_config import GuardConfig, GuardDenyReason
from app.guards.guard_decision import (
    GuardDecisionSnapshot,
    GuardSignal,
    SignalName,
    SignalReasonCode,
    SignalStatus,
    TenantMode,
    WindowParams,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

def _make_config(**overrides) -> GuardConfig:
    defaults = dict(
        schema_version="1.0",
        config_version="test",
        last_updated_at="2026-02-16T00:00:00Z",
        cb_precheck_enabled=False,
        decision_layer_enabled=True,
        decision_layer_mode="enforce",
        decision_layer_default_mode="off",
        decision_layer_tenant_modes_json='{"tenantA":"enforce","tenantB":"shadow"}',
        decision_layer_tenant_allowlist_json='["tenantA","tenantB"]',
    )
    defaults.update(overrides)
    return GuardConfig.model_construct(**defaults)


@pytest.fixture()
def _tenant_singletons():
    """Reset singletons with tenant-aware config."""
    import app.ops_guard_middleware as ogm
    import app.main as main_mod
    import app.guard_config as gc_mod
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None
    _prev = gc_mod._guard_config
    gc_mod._guard_config = _make_config()
    yield
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None
    gc_mod._guard_config = _prev


@pytest.fixture()
def tenant_client(_tenant_singletons):
    """TestClient with auth bypassed and tenant config active."""
    with patch.dict(os.environ, {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db
        from fastapi.testclient import TestClient

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db
        yield TestClient(fastapi_app)
        fastapi_app.dependency_overrides.clear()


def _insufficient_snapshot(tenant_id: str = "default", tenant_mode: TenantMode = TenantMode.ENFORCE):
    """Build a snapshot with INSUFFICIENT CB mapping signal."""
    return GuardDecisionSnapshot(
        now_ms=1000000,
        tenant_id=tenant_id,
        endpoint="/admin/market-prices",
        method="GET",
        window_params=WindowParams(),
        config_hash="abc123",
        risk_context_hash="def456",
        guard_deny_reason=None,
        signals=(
            GuardSignal(
                name=SignalName.CONFIG_FRESHNESS,
                status=SignalStatus.OK,
                reason_code=SignalReasonCode.OK,
                observed_at_ms=1000000,
            ),
            GuardSignal(
                name=SignalName.CB_MAPPING,
                status=SignalStatus.INSUFFICIENT,
                reason_code=SignalReasonCode.CB_MAPPING_MISS,
                observed_at_ms=1000000,
            ),
        ),
        derived_has_stale=False,
        derived_has_insufficient=True,
        is_degrade_mode=False,
        tenant_mode=tenant_mode,
    )


def _ok_snapshot(tenant_id: str = "default", tenant_mode: TenantMode = TenantMode.ENFORCE):
    """Build a snapshot with all-OK signals."""
    return GuardDecisionSnapshot(
        now_ms=1000000,
        tenant_id=tenant_id,
        endpoint="/admin/market-prices",
        method="GET",
        window_params=WindowParams(),
        config_hash="abc123",
        risk_context_hash="def456",
        guard_deny_reason=None,
        signals=(
            GuardSignal(
                name=SignalName.CONFIG_FRESHNESS,
                status=SignalStatus.OK,
                reason_code=SignalReasonCode.OK,
                observed_at_ms=1000000,
            ),
            GuardSignal(
                name=SignalName.CB_MAPPING,
                status=SignalStatus.OK,
                reason_code=SignalReasonCode.OK,
                observed_at_ms=1000000,
            ),
        ),
        derived_has_stale=False,
        derived_has_insufficient=False,
        is_degrade_mode=False,
        tenant_mode=tenant_mode,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Shared patches — bypass OpsGuard layer (allow path)
# ═══════════════════════════════════════════════════════════════════════════════

def _allow_path_patches():
    """Context managers that let request through OpsGuard to decision layer."""
    return [
        patch("app.kill_switch.KillSwitchManager.check_request", return_value=None),
        patch("app.guards.rate_limit_guard.RateLimitGuard.check_request", return_value=None),
        patch("app.ops_guard_middleware.OpsGuardMiddleware._check_circuit_breaker", return_value=None),
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# I1: OpsGuard deny bypass — rate limit → 429, decision layer not invoked
# ═══════════════════════════════════════════════════════════════════════════════

class TestI1OpsGuardDenyBypass:
    """
    When OpsGuard denies (rate limit → 429), the decision layer
    is never invoked. Tenant headers are irrelevant.
    """

    def test_rate_limited_returns_429_ignores_tenant(self, tenant_client):
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=GuardDenyReason.RATE_LIMITED,
        ):
            resp = tenant_client.get(
                "/admin/market-prices/deprecation-stats",
                headers={"X-Tenant-Id": "tenantA"},
            )
            assert resp.status_code == 429
            body = resp.json()
            assert body["reason"] == "RATE_LIMITED"
            # Decision layer error codes must NOT appear
            assert "errorCode" not in body


# ═══════════════════════════════════════════════════════════════════════════════
# I2: Tenant shadow — BLOCK verdict → passthrough (no 503)
# ═══════════════════════════════════════════════════════════════════════════════

class TestI2TenantShadow:
    """
    tenantB is configured as shadow. BLOCK verdict → log + metrics,
    but request passes through (no 503).
    """

    def test_shadow_tenant_block_passes_through(self, tenant_client):
        snapshot = _insufficient_snapshot(
            tenant_id="tenantB", tenant_mode=TenantMode.SHADOW,
        )

        patches = _allow_path_patches()
        patches.append(
            patch(
                "app.guards.guard_decision_middleware.SnapshotFactory.build",
                return_value=snapshot,
            )
        )

        with patches[0], patches[1], patches[2], patches[3]:
            resp = tenant_client.get(
                "/admin/market-prices/deprecation-stats",
                headers={"X-Tenant-Id": "tenantB"},
            )
            # Shadow: NOT 503 — request passes through
            assert resp.status_code != 503


# ═══════════════════════════════════════════════════════════════════════════════
# I3: Tenant enforce — BLOCK verdict → 503
# ═══════════════════════════════════════════════════════════════════════════════

class TestI3TenantEnforce:
    """
    tenantA is configured as enforce. BLOCK verdict → 503.
    """

    def test_enforce_tenant_block_returns_503(self, tenant_client):
        snapshot = _insufficient_snapshot(
            tenant_id="tenantA", tenant_mode=TenantMode.ENFORCE,
        )

        patches = _allow_path_patches()
        patches.append(
            patch(
                "app.guards.guard_decision_middleware.SnapshotFactory.build",
                return_value=snapshot,
            )
        )

        with patches[0], patches[1], patches[2], patches[3]:
            resp = tenant_client.get(
                "/admin/market-prices/deprecation-stats",
                headers={"X-Tenant-Id": "tenantA"},
            )
            assert resp.status_code == 503
            body = resp.json()
            assert body["errorCode"] == "OPS_GUARD_INSUFFICIENT"
            assert "CB_MAPPING_MISS" in body["reasonCodes"]


# ═══════════════════════════════════════════════════════════════════════════════
# I4: Unknown tenant → default mode (off) + metric label "_other"
# ═══════════════════════════════════════════════════════════════════════════════

class TestI4UnknownTenantMetricLabel:
    """
    Unknown tenant (not in tenant_modes_json) → default_mode=off → passthrough.
    Metric label should be "_other" (not in allowlist).

    Since default_mode=off, the middleware skips snapshot build entirely.
    We verify passthrough behavior.
    """

    def test_unknown_tenant_passthrough_default_off(self, tenant_client):
        """Unknown tenant with default_mode=off → passthrough (no snapshot build)."""
        patches = _allow_path_patches()

        with patches[0], patches[1], patches[2]:
            resp = tenant_client.get(
                "/admin/market-prices/deprecation-stats",
                headers={"X-Tenant-Id": "unknownTenant"},
            )
            # default_mode=off → passthrough, not 503
            assert resp.status_code != 503

    def test_unknown_tenant_metric_label_is_other(self):
        """
        Verify sanitize_metric_tenant returns "_other" for unknown tenant.
        This is the unit-level proof that the metric label is correct.
        """
        from app.guards.guard_decision import sanitize_metric_tenant

        allowlist = frozenset({"tenantA", "tenantB"})
        assert sanitize_metric_tenant("unknownTenant", allowlist) == "_other"
        assert sanitize_metric_tenant("tenantA", allowlist) == "tenantA"
