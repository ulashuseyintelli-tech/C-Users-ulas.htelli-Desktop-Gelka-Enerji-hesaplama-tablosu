"""
Tests for OpsGuardMiddleware — guard chain wiring + decision precedence.

Covers:
  - No-op passthrough (guards inactive by default)
  - Kill-switch deny → 503 (rate limiter NOT called)
  - Rate limit deny → 429 + Retry-After
  - Precedence: KS wins over RL
  - Infra endpoints skip guard chain
  - Fail-open on middleware internal error
  - Deterministic error bodies

Feature: ops-guard, Task 7.1–7.3
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import CollectorRegistry

from app.guard_config import GuardConfig, GuardDenyReason
from app.ptf_metrics import PTFMetrics
from app.kill_switch import KillSwitchManager
from app.guards.rate_limit_guard import RateLimitGuard


@pytest.fixture()
def _fresh_singletons():
    """Reset module-level singletons before/after each test."""
    import app.ops_guard_middleware as ogm
    import app.main as main_mod
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None
    yield
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None


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
# 1. Passthrough (guards inactive by default)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPassthrough:
    """Default config: all guards passive → requests pass through."""

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_metrics_endpoint(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert b"ptf_admin_" in resp.content

    def test_unknown_endpoint_404(self, client):
        resp = client.get("/nonexistent-path-xyz")
        assert resp.status_code in (404, 405)

    def test_admin_endpoint_reachable(self, client):
        resp = client.get("/admin/market-prices/deprecation-stats")
        assert resp.status_code != 503


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Kill-Switch Deny → 503
# ═══════════════════════════════════════════════════════════════════════════════

class TestKillSwitchDeny:
    """Kill-switch active → 503 with correct body."""

    def test_kill_switch_returns_503(self, client):
        """When KillSwitchManager.check_request returns KILL_SWITCHED → 503."""
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=GuardDenyReason.KILL_SWITCHED,
        ):
            # Use a non-skip endpoint
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code == 503
            body = resp.json()
            assert body["reason"] == "KILL_SWITCHED"

    def test_kill_switch_skips_rate_limiter(self, client):
        """When kill-switch denies, rate limiter check_request is NOT called."""
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=GuardDenyReason.KILL_SWITCHED,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
        ) as rl_mock:
            client.get("/admin/market-prices/deprecation-stats")
            rl_mock.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Rate Limit Deny → 429 + Retry-After
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimitDeny:
    """Rate limit exceeded → 429 + Retry-After."""

    def test_rate_limit_returns_429(self, client):
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

    def test_rate_limit_has_retry_after_header(self, client):
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=GuardDenyReason.RATE_LIMITED,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert "retry-after" in resp.headers


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Precedence: KS wins over RL
# ═══════════════════════════════════════════════════════════════════════════════

class TestPrecedence:
    def test_ks_wins_over_rl(self, client):
        """Both would deny → kill-switch reason returned, RL not called."""
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=GuardDenyReason.KILL_SWITCHED,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
        ) as rl_mock:
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code == 503
            assert resp.json()["reason"] == "KILL_SWITCHED"
            rl_mock.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Infra Endpoints Skip Guards
# ═══════════════════════════════════════════════════════════════════════════════

class TestSkipPaths:
    """/metrics, /health, /health/ready bypass guard chain entirely."""

    def test_metrics_skips_guards(self, client):
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=GuardDenyReason.KILL_SWITCHED,
        ) as ks_mock:
            resp = client.get("/metrics")
            assert resp.status_code == 200
            ks_mock.assert_not_called()

    def test_health_skips_guards(self, client):
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=GuardDenyReason.KILL_SWITCHED,
        ) as ks_mock:
            resp = client.get("/health")
            assert resp.status_code == 200
            ks_mock.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Fail-Open on Middleware Internal Error
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailOpen:
    def test_evaluate_guards_exception_passes_through(self, client):
        """If _evaluate_guards raises, request still reaches handler."""
        with patch(
            "app.ops_guard_middleware.OpsGuardMiddleware._evaluate_guards",
            side_effect=RuntimeError("middleware boom"),
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            # Should NOT be 503 from guard — handler reached
            assert resp.status_code != 503


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Internal Error Deny (fail-closed from rate limiter)
# ═══════════════════════════════════════════════════════════════════════════════

class TestInternalErrorDeny:
    def test_internal_error_returns_503(self, client):
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=GuardDenyReason.INTERNAL_ERROR,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert resp.status_code == 503
            assert resp.json()["reason"] == "INTERNAL_ERROR"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Deterministic Error Bodies
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorBodies:
    def test_kill_switch_body_structure(self, client):
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=GuardDenyReason.KILL_SWITCHED,
        ):
            body = client.get("/admin/market-prices/deprecation-stats").json()
            assert "error" in body
            assert "reason" in body
            assert "message" in body

    def test_rate_limit_body_has_retry_after(self, client):
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=None,
        ), patch(
            "app.guards.rate_limit_guard.RateLimitGuard.check_request",
            return_value=GuardDenyReason.RATE_LIMITED,
        ):
            body = client.get("/admin/market-prices/deprecation-stats").json()
            assert "retry_after" in body

    def test_no_retry_after_on_kill_switch(self, client):
        """Retry-After header only on 429, not on 503."""
        with patch(
            "app.kill_switch.KillSwitchManager.check_request",
            return_value=GuardDenyReason.KILL_SWITCHED,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            assert "retry-after" not in resp.headers
