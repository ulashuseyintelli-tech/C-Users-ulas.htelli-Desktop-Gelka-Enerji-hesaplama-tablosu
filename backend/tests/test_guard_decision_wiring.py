"""
Tests for GuardDecisionMiddleware — wiring integration.

Covers:
  W1: Rate-limited request → 429 (decision layer bypassed)
  W2: Kill-switched request → 503 KILL_SWITCHED (decision layer bypassed)
  W3: Circuit-open request → 503 CIRCUIT_OPEN (decision layer bypassed)
  W4: Allow path + insufficient signal → 503 OPS_GUARD_INSUFFICIENT
  W5: Allow path + stale signal → 503 OPS_GUARD_STALE
  W6: Allow path + all OK → passthrough (handler reached)
  W7: SnapshotFactory.build() crash → fail-open (handler reached)

Feature: runtime-guard-decision, Wiring Task
"""

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
    WindowParams,
)
from app.guards.guard_enforcement import EnforcementVerdict


@pytest.fixture()
def _fresh_singletons():
    """Reset module-level singletons before/after each test."""
    import app.ops_guard_middleware as ogm
    import app.main as main_mod
    import app.guard_config as gc_mod
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None
    # Enable decision layer in ENFORCE mode for wiring tests
    _prev_config = gc_mod._guard_config
    gc_mod._guard_config = GuardConfig.model_construct(
        schema_version="1.0",
        config_version="test",
        last_updated_at="2026-02-16T00:00:00Z",
        cb_precheck_enabled=False,
        decision_layer_enabled=True,
        decision_layer_mode="enforce",
        decision_layer_default_mode="enforce",
    )
    yield
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None
    gc_mod._guard_config = _prev_config


@pytest.fixture()
def client(_fresh_singletons):
    """TestClient with auth bypassed."""
    with patch.dict(os.environ, {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db
        from fastapi.testclient import TestClient

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db
        yield TestClient(fastapi_app)
        fastapi_app.dependency_overrides.clear()


# Helper: build a test config with model_construct (skip env loading)
def _test_config(**overrides) -> GuardConfig:
    defaults = dict(
        schema_version="1.0",
        config_version="test",
        last_updated_at="2026-02-16T00:00:00Z",
        cb_precheck_enabled=False,
    )
    defaults.update(overrides)
    return GuardConfig.model_construct(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# W1: Rate-limited → 429 (decision layer bypassed)
# ═══════════════════════════════════════════════════════════════════════════════

class TestW1RateLimitedBypass:
    def test_rate_limited_returns_429_not_503(self, client):
        """Rate-limited request gets 429 from OpsGuard; decision layer never runs."""
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
            # Must NOT have decision layer error codes
            assert "errorCode" not in body


# ═══════════════════════════════════════════════════════════════════════════════
# W2: Kill-switched → 503 KILL_SWITCHED (decision layer bypassed)
# ═══════════════════════════════════════════════════════════════════════════════

class TestW2KillSwitchedBypass:
    def test_kill_switched_returns_503_kill_switched(self, client):
        """Kill-switched request gets 503 from OpsGuard; decision layer never runs."""
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=GuardDenyReason.KILL_SWITCHED,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code == 503
            body = resp.json()
            assert body["reason"] == "KILL_SWITCHED"
            assert "errorCode" not in body


# ═══════════════════════════════════════════════════════════════════════════════
# W3: Circuit-open → 503 CIRCUIT_OPEN (decision layer bypassed)
# ═══════════════════════════════════════════════════════════════════════════════

class TestW3CircuitOpenBypass:
    def test_circuit_open_returns_503_circuit_open(self, client):
        """Circuit-open request gets 503 from OpsGuard; decision layer never runs."""
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.ops_guard_middleware.OpsGuardMiddleware._check_circuit_breaker",
            return_value=GuardDenyReason.CIRCUIT_OPEN,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code == 503
            body = resp.json()
            assert body["reason"] == "CIRCUIT_OPEN"
            assert "errorCode" not in body


# ═══════════════════════════════════════════════════════════════════════════════
# W4: Allow path + insufficient → 503 OPS_GUARD_INSUFFICIENT
# ═══════════════════════════════════════════════════════════════════════════════

class TestW4AllowInsufficient:
    def test_insufficient_signal_returns_503(self, client):
        """Allow path + insufficient signal → 503 with OPS_GUARD_INSUFFICIENT."""
        insufficient_snapshot = GuardDecisionSnapshot(
            now_ms=1000000,
            tenant_id="default",
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
        )

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.ops_guard_middleware.OpsGuardMiddleware._check_circuit_breaker",
            return_value=None,
        ), patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=insufficient_snapshot,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code == 503
            body = resp.json()
            assert body["errorCode"] == "OPS_GUARD_INSUFFICIENT"
            assert "CB_MAPPING_MISS" in body["reasonCodes"]


# ═══════════════════════════════════════════════════════════════════════════════
# W5: Allow path + stale → 503 OPS_GUARD_STALE
# ═══════════════════════════════════════════════════════════════════════════════

class TestW5AllowStale:
    def test_stale_signal_returns_503(self, client):
        """Allow path + stale signal → 503 with OPS_GUARD_STALE."""
        stale_snapshot = GuardDecisionSnapshot(
            now_ms=1000000,
            tenant_id="default",
            endpoint="/admin/market-prices",
            method="GET",
            window_params=WindowParams(),
            config_hash="abc123",
            risk_context_hash="def456",
            guard_deny_reason=None,
            signals=(
                GuardSignal(
                    name=SignalName.CONFIG_FRESHNESS,
                    status=SignalStatus.STALE,
                    reason_code=SignalReasonCode.CONFIG_STALE,
                    observed_at_ms=1000000,
                ),
                GuardSignal(
                    name=SignalName.CB_MAPPING,
                    status=SignalStatus.OK,
                    reason_code=SignalReasonCode.OK,
                    observed_at_ms=1000000,
                ),
            ),
            derived_has_stale=True,
            derived_has_insufficient=False,
            is_degrade_mode=False,
        )

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.ops_guard_middleware.OpsGuardMiddleware._check_circuit_breaker",
            return_value=None,
        ), patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=stale_snapshot,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code == 503
            body = resp.json()
            assert body["errorCode"] == "OPS_GUARD_STALE"
            assert "CONFIG_STALE" in body["reasonCodes"]


# ═══════════════════════════════════════════════════════════════════════════════
# W6: Allow path + all OK → passthrough (handler reached)
# ═══════════════════════════════════════════════════════════════════════════════

class TestW6AllowPassthrough:
    def test_all_ok_passes_through(self, client):
        """Allow path + all signals OK → request reaches handler."""
        ok_snapshot = GuardDecisionSnapshot(
            now_ms=1000000,
            tenant_id="default",
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
        )

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.ops_guard_middleware.OpsGuardMiddleware._check_circuit_breaker",
            return_value=None,
        ), patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=ok_snapshot,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            # Handler reached — not 503 from decision layer
            assert resp.status_code != 503


# ═══════════════════════════════════════════════════════════════════════════════
# W7: SnapshotFactory.build() crash → fail-open (handler reached)
# ═══════════════════════════════════════════════════════════════════════════════

class TestW7FailOpen:
    def test_snapshot_build_none_passes_through(self, client):
        """SnapshotFactory.build() returns None → fail-open, handler reached."""
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.ops_guard_middleware.OpsGuardMiddleware._check_circuit_breaker",
            return_value=None,
        ), patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=None,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            # Handler reached — not 503 from decision layer
            assert resp.status_code != 503

    def test_middleware_exception_passes_through(self, client):
        """GuardDecisionMiddleware internal exception → fail-open."""
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.ops_guard_middleware.OpsGuardMiddleware._check_circuit_breaker",
            return_value=None,
        ), patch(
            "app.guards.guard_decision_middleware.GuardDecisionMiddleware._evaluate_decision",
            side_effect=RuntimeError("decision boom"),
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            # Handler reached — not 503 from decision layer
            assert resp.status_code != 503


# ═══════════════════════════════════════════════════════════════════════════════
# W8: Shadow mode — block verdict → passthrough (metrics emitted, no 503)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def _shadow_singletons():
    """Reset singletons with decision layer in SHADOW mode."""
    import app.ops_guard_middleware as ogm
    import app.main as main_mod
    import app.guard_config as gc_mod
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None
    _prev_config = gc_mod._guard_config
    gc_mod._guard_config = GuardConfig.model_construct(
        schema_version="1.0",
        config_version="test",
        last_updated_at="2026-02-16T00:00:00Z",
        cb_precheck_enabled=False,
        decision_layer_enabled=True,
        decision_layer_mode="shadow",
        decision_layer_default_mode="shadow",
    )
    yield
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None
    gc_mod._guard_config = _prev_config


@pytest.fixture()
def shadow_client(_shadow_singletons):
    """TestClient with auth bypassed and shadow mode enabled."""
    with patch.dict(os.environ, {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db
        from fastapi.testclient import TestClient

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db
        yield TestClient(fastapi_app)
        fastapi_app.dependency_overrides.clear()


class TestW8ShadowMode:
    def test_shadow_insufficient_passes_through(self, shadow_client):
        """Shadow mode: insufficient signal → metrics emitted but request passes through."""
        insufficient_snapshot = GuardDecisionSnapshot(
            now_ms=1000000,
            tenant_id="default",
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
        )

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.ops_guard_middleware.OpsGuardMiddleware._check_circuit_breaker",
            return_value=None,
        ), patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=insufficient_snapshot,
        ):
            resp = shadow_client.get("/admin/market-prices/deprecation-stats")
            # Shadow mode: NOT 503 — request passes through
            assert resp.status_code != 503

    def test_shadow_stale_passes_through(self, shadow_client):
        """Shadow mode: stale signal → metrics emitted but request passes through."""
        stale_snapshot = GuardDecisionSnapshot(
            now_ms=1000000,
            tenant_id="default",
            endpoint="/admin/market-prices",
            method="GET",
            window_params=WindowParams(),
            config_hash="abc123",
            risk_context_hash="def456",
            guard_deny_reason=None,
            signals=(
                GuardSignal(
                    name=SignalName.CONFIG_FRESHNESS,
                    status=SignalStatus.STALE,
                    reason_code=SignalReasonCode.CONFIG_STALE,
                    observed_at_ms=1000000,
                ),
                GuardSignal(
                    name=SignalName.CB_MAPPING,
                    status=SignalStatus.OK,
                    reason_code=SignalReasonCode.OK,
                    observed_at_ms=1000000,
                ),
            ),
            derived_has_stale=True,
            derived_has_insufficient=False,
            is_degrade_mode=False,
        )

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.ops_guard_middleware.OpsGuardMiddleware._check_circuit_breaker",
            return_value=None,
        ), patch(
            "app.guards.guard_decision_middleware.SnapshotFactory.build",
            return_value=stale_snapshot,
        ):
            resp = shadow_client.get("/admin/market-prices/deprecation-stats")
            # Shadow mode: NOT 503 — request passes through
            assert resp.status_code != 503
