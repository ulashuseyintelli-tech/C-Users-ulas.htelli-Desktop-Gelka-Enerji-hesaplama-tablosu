"""
Endpoint-Class Policy — ASGI Enforcement Tests.

Covers effective_mode as the single source of enforcement decisions:
  E1: Empty risk map + tenant ENFORCE → effective SHADOW → NO BLOCK
  E2: Risk HIGH + tenant ENFORCE → effective ENFORCE → BLOCK 503
  E3: Risk MEDIUM + tenant ENFORCE → ENFORCE → BLOCK 503
  E4: Tenant SHADOW + risk HIGH → SHADOW → NO BLOCK
  E5: Tenant OFF → NOOP regardless of risk
  E6: OpsGuard deny bypass unchanged (rate limit 429)

Feature: endpoint-class-policy, Task 5
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app.guard_config import GuardConfig, GuardDenyReason
from app.guards.guard_decision import (
    GuardDecisionSnapshot,
    GuardSignal,
    RiskClass,
    SignalName,
    SignalReasonCode,
    SignalStatus,
    TenantMode,
    WindowParams,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _make_config(**overrides) -> GuardConfig:
    defaults = dict(
        schema_version="1.0",
        config_version="test",
        last_updated_at="2026-02-16T00:00:00Z",
        cb_precheck_enabled=False,
        decision_layer_enabled=True,
        decision_layer_mode="enforce",
        decision_layer_default_mode="enforce",
    )
    defaults.update(overrides)
    return GuardConfig.model_construct(**defaults)


def _snapshot(
    *,
    tenant_mode: TenantMode,
    effective_mode: TenantMode,
    risk_class: RiskClass = RiskClass.LOW,
    has_stale: bool = False,
    has_insufficient: bool = True,
) -> GuardDecisionSnapshot:
    """Build a snapshot with configurable mode/risk and an INSUFFICIENT signal by default."""
    signals = []
    # CONFIG_FRESHNESS
    if has_stale:
        signals.append(GuardSignal(
            name=SignalName.CONFIG_FRESHNESS,
            status=SignalStatus.STALE,
            reason_code=SignalReasonCode.CONFIG_STALE,
            observed_at_ms=1000000,
        ))
    else:
        signals.append(GuardSignal(
            name=SignalName.CONFIG_FRESHNESS,
            status=SignalStatus.OK,
            reason_code=SignalReasonCode.OK,
            observed_at_ms=1000000,
        ))
    # CB_MAPPING
    if has_insufficient:
        signals.append(GuardSignal(
            name=SignalName.CB_MAPPING,
            status=SignalStatus.INSUFFICIENT,
            reason_code=SignalReasonCode.CB_MAPPING_MISS,
            observed_at_ms=1000000,
        ))
    else:
        signals.append(GuardSignal(
            name=SignalName.CB_MAPPING,
            status=SignalStatus.OK,
            reason_code=SignalReasonCode.OK,
            observed_at_ms=1000000,
        ))

    return GuardDecisionSnapshot(
        now_ms=1000000,
        tenant_id="default",
        endpoint="/admin/market-prices",
        method="GET",
        window_params=WindowParams(),
        config_hash="abc123",
        risk_context_hash="def456",
        guard_deny_reason=None,
        signals=tuple(signals),
        derived_has_stale=has_stale,
        derived_has_insufficient=has_insufficient,
        is_degrade_mode=False,
        tenant_mode=tenant_mode,
        effective_mode=effective_mode,
        risk_class=risk_class,
    )


def _allow_patches():
    """Bypass OpsGuard layer (allow path)."""
    return [
        patch("app.kill_switch.KillSwitchManager.check_request", return_value=None),
        patch("app.guards.rate_limit_guard.RateLimitGuard.check_request", return_value=None),
        patch("app.ops_guard_middleware.OpsGuardMiddleware._check_circuit_breaker", return_value=None),
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def _enforce_singletons():
    """Reset singletons with decision layer in ENFORCE mode."""
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
def client(_enforce_singletons):
    """TestClient with auth bypassed."""
    with patch.dict(os.environ, {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db
        from fastapi.testclient import TestClient

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db
        yield TestClient(fastapi_app)
        fastapi_app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# E1: Empty risk map + tenant ENFORCE → effective SHADOW → NO BLOCK
# ═══════════════════════════════════════════════════════════════════════════════

class TestE1EmptyRiskMapShadowFallback:
    """
    Empty risk map → all endpoints LOW → ENFORCE + LOW → effective SHADOW.
    Even with BLOCK-worthy signals, shadow mode passes through.
    """

    def test_empty_risk_map_enforce_tenant_no_block(self, client):
        snapshot = _snapshot(
            tenant_mode=TenantMode.ENFORCE,
            effective_mode=TenantMode.SHADOW,  # ENFORCE + LOW → SHADOW
            risk_class=RiskClass.LOW,
            has_insufficient=True,
        )
        patches = _allow_patches()
        patches.append(patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=snapshot,
        ))
        with patches[0], patches[1], patches[2], patches[3]:
            resp = client.get("/admin/market-prices/deprecation-stats")
            # Shadow mode: NOT 503 — passthrough
            assert resp.status_code != 503

    def test_empty_risk_map_stale_signal_no_block(self, client):
        snapshot = _snapshot(
            tenant_mode=TenantMode.ENFORCE,
            effective_mode=TenantMode.SHADOW,
            risk_class=RiskClass.LOW,
            has_stale=True,
            has_insufficient=False,
        )
        patches = _allow_patches()
        patches.append(patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=snapshot,
        ))
        with patches[0], patches[1], patches[2], patches[3]:
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code != 503


# ═══════════════════════════════════════════════════════════════════════════════
# E2: Risk HIGH + tenant ENFORCE → effective ENFORCE → BLOCK 503
# ═══════════════════════════════════════════════════════════════════════════════

class TestE2RiskHighEnforce:
    """
    Risk HIGH + tenant ENFORCE → effective ENFORCE.
    BLOCK-worthy signal → 503 with deterministic errorCode + reasonCodes.
    """

    def test_risk_high_enforce_blocks_503(self, client):
        snapshot = _snapshot(
            tenant_mode=TenantMode.ENFORCE,
            effective_mode=TenantMode.ENFORCE,
            risk_class=RiskClass.HIGH,
            has_insufficient=True,
        )
        patches = _allow_patches()
        patches.append(patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=snapshot,
        ))
        with patches[0], patches[1], patches[2], patches[3]:
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code == 503
            body = resp.json()
            assert body["errorCode"] == "OPS_GUARD_INSUFFICIENT"
            assert "CB_MAPPING_MISS" in body["reasonCodes"]

    def test_risk_high_enforce_stale_blocks_503(self, client):
        snapshot = _snapshot(
            tenant_mode=TenantMode.ENFORCE,
            effective_mode=TenantMode.ENFORCE,
            risk_class=RiskClass.HIGH,
            has_stale=True,
            has_insufficient=False,
        )
        patches = _allow_patches()
        patches.append(patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=snapshot,
        ))
        with patches[0], patches[1], patches[2], patches[3]:
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code == 503
            body = resp.json()
            assert body["errorCode"] == "OPS_GUARD_STALE"
            assert "CONFIG_STALE" in body["reasonCodes"]


# ═══════════════════════════════════════════════════════════════════════════════
# E3: Risk MEDIUM + tenant ENFORCE → ENFORCE → BLOCK 503
# ═══════════════════════════════════════════════════════════════════════════════

class TestE3RiskMediumEnforce:
    """
    Risk MEDIUM + tenant ENFORCE → effective ENFORCE (identity).
    Confirms MEDIUM stays in ENFORCE — only LOW downgrades.
    """

    def test_risk_medium_enforce_blocks_503(self, client):
        snapshot = _snapshot(
            tenant_mode=TenantMode.ENFORCE,
            effective_mode=TenantMode.ENFORCE,
            risk_class=RiskClass.MEDIUM,
            has_insufficient=True,
        )
        patches = _allow_patches()
        patches.append(patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=snapshot,
        ))
        with patches[0], patches[1], patches[2], patches[3]:
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code == 503
            body = resp.json()
            assert body["errorCode"] == "OPS_GUARD_INSUFFICIENT"


# ═══════════════════════════════════════════════════════════════════════════════
# E4: Tenant SHADOW + risk HIGH → SHADOW → NO BLOCK
# ═══════════════════════════════════════════════════════════════════════════════

class TestE4TenantShadowRiskHigh:
    """
    Tenant SHADOW + risk HIGH → effective SHADOW (identity).
    Risk class alone cannot escalate to ENFORCE.
    """

    def test_shadow_tenant_risk_high_no_block(self, client):
        snapshot = _snapshot(
            tenant_mode=TenantMode.SHADOW,
            effective_mode=TenantMode.SHADOW,
            risk_class=RiskClass.HIGH,
            has_insufficient=True,
        )
        patches = _allow_patches()
        patches.append(patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=snapshot,
        ))
        with patches[0], patches[1], patches[2], patches[3]:
            resp = client.get("/admin/market-prices/deprecation-stats")
            # Shadow: NOT 503 — passthrough
            assert resp.status_code != 503


# ═══════════════════════════════════════════════════════════════════════════════
# E5: Tenant OFF → NOOP regardless of risk
# ═══════════════════════════════════════════════════════════════════════════════

class TestE5TenantOff:
    """
    Tenant OFF → decision layer no-op. Risk class irrelevant.
    Middleware skips snapshot build entirely (tenant_mode OFF check).
    """

    def test_tenant_off_passthrough(self, client):
        """OFF tenant → passthrough before snapshot build."""
        cfg = _make_config(
            decision_layer_default_mode="off",
        )
        import app.guard_config as gc_mod
        _prev = gc_mod._guard_config
        gc_mod._guard_config = cfg
        try:
            patches = _allow_patches()
            with patches[0], patches[1], patches[2]:
                resp = client.get("/admin/market-prices/deprecation-stats")
                assert resp.status_code != 503
        finally:
            gc_mod._guard_config = _prev

    def test_tenant_off_snapshot_not_built(self, client):
        """OFF tenant → SnapshotFactory.build() never called."""
        cfg = _make_config(
            decision_layer_default_mode="off",
        )
        import app.guard_config as gc_mod
        _prev = gc_mod._guard_config
        gc_mod._guard_config = cfg
        try:
            patches = _allow_patches()
            with patches[0], patches[1], patches[2], patch(
                "app.guards.guard_decision_middleware.SnapshotFactory.build",
            ) as mock_build:
                resp = client.get("/admin/market-prices/deprecation-stats")
                assert resp.status_code != 503
                mock_build.assert_not_called()
        finally:
            gc_mod._guard_config = _prev


# ═══════════════════════════════════════════════════════════════════════════════
# E6: OpsGuard deny bypass unchanged (rate limit 429)
# ═══════════════════════════════════════════════════════════════════════════════

class TestE6OpsGuardBypass:
    """
    OpsGuard deny (rate limit 429) → decision middleware never runs.
    Endpoint-class policy does not interfere with existing guard chain.
    """

    def test_rate_limited_429_bypass(self, client):
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=GuardDenyReason.RATE_LIMITED,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code == 429
            body = resp.json()
            assert body["reason"] == "RATE_LIMITED"
            # Decision layer error codes must NOT appear
            assert "errorCode" not in body
