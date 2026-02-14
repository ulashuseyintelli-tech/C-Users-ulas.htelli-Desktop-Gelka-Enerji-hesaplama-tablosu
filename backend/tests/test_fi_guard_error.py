"""
Tests for S5: Guard Internal Error → Fail-Open integration.

Integration test:
  - S5: Inject guard error → request reaches handler (fail-open)

Feature: fault-injection, Task 10.1
Requirements: 7.1, 7.2, 7.3
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.testing.fault_injection import FaultInjector, InjectionPoint
from app.testing.guard_error_hook import maybe_inject_guard_error


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_injector():
    FaultInjector.reset_instance()
    yield
    FaultInjector.reset_instance()


@pytest.fixture()
def _fresh_singletons():
    import app.ops_guard_middleware as ogm
    import app.main as main_mod
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None
    yield
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None


@pytest.fixture()
def client(_fresh_singletons):
    with patch.dict(os.environ, {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db
        from fastapi.testclient import TestClient

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db
        yield TestClient(fastapi_app)
        fastapi_app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# S5: Guard Internal Error → Fail-Open
# ═══════════════════════════════════════════════════════════════════════════════


class TestS5GuardInternalError:
    """
    S5 Integration: Guard internal error → fail-open (request reaches handler).

    Uses monkeypatch on _evaluate_guards to inject RuntimeError.
    Middleware catch-all should fail-open.

    Requirements: 7.1, 7.2, 7.3
    """

    def test_guard_error_fails_open(self, client):
        """Injected guard error → request passes through to handler."""
        with patch(
            "app.ops_guard_middleware.OpsGuardMiddleware._evaluate_guards",
            side_effect=RuntimeError("Injected guard internal error"),
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            # Should NOT be 503 from guard — handler reached
            assert resp.status_code != 503

    def test_guard_error_via_injection_hook(self, client):
        """Using FaultInjector + guard_error_hook → fail-open."""
        injector = FaultInjector.get_instance()
        injector.enable(InjectionPoint.GUARD_INTERNAL_ERROR, ttl_seconds=60.0)

        # Patch _evaluate_guards to call our hook before real logic
        original_evaluate = None

        def patched_evaluate(self_mw, request):
            maybe_inject_guard_error()
            if original_evaluate:
                return original_evaluate(self_mw, request)
            return None

        from app.ops_guard_middleware import OpsGuardMiddleware
        original_evaluate = OpsGuardMiddleware._evaluate_guards

        with patch.object(
            OpsGuardMiddleware,
            "_evaluate_guards",
            patched_evaluate,
        ):
            resp = client.get("/admin/market-prices/deprecation-stats")
            # Middleware catches the RuntimeError and fails open
            assert resp.status_code != 503

    def test_normal_flow_after_disable(self, client):
        """After disabling injection, normal guard chain works."""
        injector = FaultInjector.get_instance()
        injector.enable(InjectionPoint.GUARD_INTERNAL_ERROR, ttl_seconds=60.0)
        injector.disable(InjectionPoint.GUARD_INTERNAL_ERROR)

        # Normal request — no injection, no error
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_hook_noop_when_disabled(self):
        """Guard error hook is no-op when injection is disabled."""
        maybe_inject_guard_error()  # should not raise

    def test_hook_raises_when_enabled(self):
        """Guard error hook raises RuntimeError when injection is active."""
        injector = FaultInjector.get_instance()
        injector.enable(InjectionPoint.GUARD_INTERNAL_ERROR, ttl_seconds=60.0)
        with pytest.raises(RuntimeError, match="Injected guard internal error"):
            maybe_inject_guard_error()
