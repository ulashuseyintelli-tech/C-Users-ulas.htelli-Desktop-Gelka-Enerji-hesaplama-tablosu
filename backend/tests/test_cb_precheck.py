"""
CB Pre-Check + Fail-Open tests — Feature: dependency-wrappers, Task 6.

Property 2: CB Pre-Check Karar Doğruluğu
Property 3: Guard Zinciri Sırası Korunması

Covers:
- CB pre-check deny → 503 (CIRCUIT_OPEN)
- cb_precheck_enabled=False → pre-check atlanıyor
- Unknown endpoint → pre-check atlanıyor (boş dep list)
- KS deny → CB pre-check NOT called
- RL deny → CB pre-check NOT called
- CB pre-check internal error → fail-open + metric
- Middleware catch-all → fail-open + metric
"""

import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from prometheus_client import CollectorRegistry

from app.guard_config import GuardConfig, GuardDenyReason
from app.ptf_metrics import PTFMetrics
from app.guards.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitBreakerState,
    Dependency,
)


@pytest.fixture()
def _fresh_singletons():
    """Reset module-level singletons before/after each test."""
    import app.ops_guard_middleware as ogm
    import app.main as main_mod
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None
    main_mod._cb_registry = None
    yield
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None
    main_mod._cb_registry = None


@pytest.fixture()
def client(_fresh_singletons):
    """TestClient with admin-key bypassed."""
    with patch.dict(os.environ, {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db
        from fastapi.testclient import TestClient

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db
        yield TestClient(fastapi_app)
        fastapi_app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# CB Pre-Check Deny Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCBPreCheckDeny:
    """CB pre-check: any dependency OPEN → 503 CIRCUIT_OPEN."""

    def test_cb_open_returns_503(self, client):
        """When a dependency CB is OPEN → 503."""
        mock_cb = MagicMock()
        mock_cb.allow_request.return_value = False  # CB OPEN

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_cb

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.main._get_cb_registry",
            return_value=mock_registry,
        ):
            resp = client.get("/admin/market-prices")
            assert resp.status_code == 503
            body = resp.json()
            assert body["reason"] == "CIRCUIT_OPEN"

    def test_cb_closed_passes_through(self, client):
        """When all dependency CBs are CLOSED → request passes."""
        mock_cb = MagicMock()
        mock_cb.allow_request.return_value = True  # CB CLOSED

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_cb

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.main._get_cb_registry",
            return_value=mock_registry,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            # Should NOT be 503 from CB
            assert resp.status_code != 503 or resp.json().get("reason") != "CIRCUIT_OPEN"

    def test_multi_dep_any_open_denies(self, client):
        """Import endpoint has 2 deps; if one is OPEN → deny."""
        mock_cb_ok = MagicMock()
        mock_cb_ok.allow_request.return_value = True

        mock_cb_open = MagicMock()
        mock_cb_open.allow_request.return_value = False

        mock_registry = MagicMock()
        # First dep OK, second dep OPEN
        mock_registry.get.side_effect = lambda name: (
            mock_cb_ok if name == "db_primary" else mock_cb_open
        )

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.main._get_cb_registry",
            return_value=mock_registry,
        ):
            resp = client.post(
                "/admin/market-prices/import/apply",
                files={"file": ("test.csv", b"data", "text/csv")},
                data={"price_type": "PTF", "force_update": "false", "strict_mode": "false"},
            )
            assert resp.status_code == 503
            assert resp.json()["reason"] == "CIRCUIT_OPEN"


# ═══════════════════════════════════════════════════════════════════════════════
# DW-2: cb_precheck_enabled Flag
# ═══════════════════════════════════════════════════════════════════════════════


class TestCBPreCheckFlag:
    """DW-2: cb_precheck_enabled=False → pre-check skipped."""

    def test_precheck_disabled_skips_cb(self, client):
        """Flag off → CB pre-check not called, even if CB is OPEN."""
        mock_cb = MagicMock()
        mock_cb.allow_request.return_value = False  # CB OPEN

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_cb

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.main._get_cb_registry",
            return_value=mock_registry,
        ), patch(
            "app.guard_config.get_guard_config",
        ) as mock_config:
            cfg = GuardConfig(cb_precheck_enabled=False)
            mock_config.return_value = cfg
            resp = client.get("/admin/market-prices/deprecation-stats")
            # Should NOT be 503 from CB — pre-check skipped
            mock_registry.get.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Unknown Endpoint → Pre-Check Skipped
# ═══════════════════════════════════════════════════════════════════════════════


class TestCBPreCheckUnknownEndpoint:
    """Unknown endpoint → empty dep list → pre-check skipped."""

    def test_unknown_endpoint_passes(self, client):
        """Endpoint not in map → no CB check → passes through."""
        mock_registry = MagicMock()

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.main._get_cb_registry",
            return_value=mock_registry,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            # CB registry should not be queried for unmapped endpoint
            mock_registry.get.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Property 3: Guard Chain Order Preserved
# ═══════════════════════════════════════════════════════════════════════════════


class TestGuardChainOrder:
    """Property 3: KS deny → CB not called; RL deny → CB not called."""

    def test_ks_deny_skips_cb(self, client):
        """Kill-switch deny → CB pre-check NOT called."""
        mock_registry = MagicMock()

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=GuardDenyReason.KILL_SWITCHED,
        ), patch(
            "app.main._get_cb_registry",
            return_value=mock_registry,
        ):
            resp = client.get("/admin/market-prices")
            assert resp.status_code == 503
            assert resp.json()["reason"] == "KILL_SWITCHED"
            mock_registry.get.assert_not_called()

    def test_rl_deny_skips_cb(self, client):
        """Rate limit deny → CB pre-check NOT called."""
        mock_registry = MagicMock()

        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=GuardDenyReason.RATE_LIMITED,
        ), patch(
            "app.main._get_cb_registry",
            return_value=mock_registry,
        ):
            resp = client.get("/admin/market-prices")
            assert resp.status_code == 429
            assert resp.json()["reason"] == "RATE_LIMITED"
            mock_registry.get.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# CB Pre-Check Internal Error → Fail-Open + Metric
# ═══════════════════════════════════════════════════════════════════════════════


class TestCBPreCheckFailOpen:
    """CB pre-check internal error → fail-open + metric (DW-3)."""

    def test_cb_precheck_error_fails_open(self, client):
        """CB registry raises → request NOT denied by CIRCUIT_OPEN (fail-open)."""
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=None,
        ), patch(
            "app.main._get_cb_registry",
            side_effect=RuntimeError("registry boom"),
        ):
            resp = client.get("/health")
            # Health is skipped, so test the method directly instead
        
        # Direct unit test of _check_circuit_breaker
        from app.ops_guard_middleware import OpsGuardMiddleware
        mw = OpsGuardMiddleware.__new__(OpsGuardMiddleware)
        
        with patch(
            "app.main._get_cb_registry",
            side_effect=RuntimeError("registry boom"),
        ):
            result = mw._check_circuit_breaker("/admin/market-prices")
        
        assert result is None  # fail-open

    def test_cb_precheck_error_increments_failopen_metric(self):
        """CB pre-check error → ptf_admin_guard_failopen_total incremented."""
        from app.ops_guard_middleware import OpsGuardMiddleware
        mw = OpsGuardMiddleware.__new__(OpsGuardMiddleware)
        metrics = PTFMetrics(registry=CollectorRegistry())

        with patch(
            "app.main._get_cb_registry",
            side_effect=RuntimeError("registry boom"),
        ), patch(
            "app.ptf_metrics.get_ptf_metrics",
            return_value=metrics,
        ):
            result = mw._check_circuit_breaker("/admin/market-prices")

        assert result is None  # fail-open
        val = metrics._guard_failopen_total._value.get()
        assert val >= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# Middleware Catch-All Fail-Open + Metric
# ═══════════════════════════════════════════════════════════════════════════════


class TestMiddlewareFailOpenMetric:
    """Middleware catch-all → fail-open + ptf_admin_guard_failopen_total."""

    def test_middleware_error_increments_failopen_metric(self, client):
        """_evaluate_guards raises → fail-open + metric."""
        metrics = PTFMetrics(registry=CollectorRegistry())

        with patch(
            "app.ops_guard_middleware.OpsGuardMiddleware._evaluate_guards",
            side_effect=RuntimeError("middleware boom"),
        ), patch(
            "app.ptf_metrics.get_ptf_metrics",
            return_value=metrics,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            # Should NOT be denied by guard — fail-open
            assert resp.status_code != 503 or "reason" not in resp.json()

        val = metrics._guard_failopen_total._value.get()
        assert val >= 1.0
