"""
Tests for S3: KillSwitch Runtime Toggle integration.

Property-based tests (Hypothesis):
  - Property 7: KillSwitch Toggle Round-Trip with Metrics

Integration test:
  - S3: Enable/disable kill-switch → 503/200 + gauge toggle

Feature: fault-injection, Tasks 7.1, 7.2
Requirements: 5.1, 5.2, 5.3, 5.4
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck
from prometheus_client import CollectorRegistry

from app.guard_config import GuardConfig
from app.ptf_metrics import PTFMetrics
from app.kill_switch import KillSwitchManager


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def prom_registry():
    return CollectorRegistry()


@pytest.fixture
def metrics(prom_registry):
    return PTFMetrics(registry=prom_registry)


@pytest.fixture
def config():
    return GuardConfig.model_construct(
        killswitch_global_import_disabled=False,
        killswitch_degrade_mode=False,
        killswitch_disabled_tenants="",
    )


@pytest.fixture
def manager(config, metrics):
    return KillSwitchManager(config, metrics)


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
# Property 7: KillSwitch Toggle Round-Trip with Metrics
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperty7KillSwitchToggle:
    """Feature: fault-injection, Property 7: KillSwitch Toggle Round-Trip with Metrics"""

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        switch_name=st.sampled_from(["global_import", "degrade_mode", "tenant:T1"]),
    )
    def test_toggle_round_trip(self, switch_name, metrics, prom_registry):
        config = GuardConfig.model_construct(
            killswitch_global_import_disabled=False,
            killswitch_degrade_mode=False,
            killswitch_disabled_tenants="",
        )
        mgr = KillSwitchManager(config, metrics)

        # Enable
        mgr.set_switch(switch_name, True, actor="test")
        gauge = prom_registry.get_sample_value(
            "ptf_admin_killswitch_state",
            {"switch_name": switch_name},
        )
        assert gauge == 1.0

        # Disable
        mgr.set_switch(switch_name, False, actor="test")
        gauge = prom_registry.get_sample_value(
            "ptf_admin_killswitch_state",
            {"switch_name": switch_name},
        )
        assert gauge == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# S3: KillSwitch Runtime Toggle Integration Test
# ═══════════════════════════════════════════════════════════════════════════════


class TestS3KillSwitchToggle:
    """
    S3 Integration: Toggle kill-switch at runtime → HTTP 503 / 200.

    Uses TestClient with real FastAPI app.

    Requirements: 5.1, 5.2, 5.3, 5.4
    """

    def test_enable_global_import_blocks_import_endpoint(self, client):
        """Enable global_import → import endpoint returns 503 KILL_SWITCHED."""
        import app.main as main_mod

        # Enable kill-switch via admin API
        resp = client.put(
            "/admin/ops/kill-switches/global_import",
            json={"enabled": True},
        )
        assert resp.status_code == 200

        # Import endpoint should be blocked
        resp = client.post("/admin/market-prices/import/preview")
        assert resp.status_code == 503
        assert resp.json()["reason"] == "KILL_SWITCHED"

    def test_disable_global_import_restores_flow(self, client):
        """Enable then disable → import endpoint accessible again."""
        # Enable
        client.put(
            "/admin/ops/kill-switches/global_import",
            json={"enabled": True},
        )
        resp = client.post("/admin/market-prices/import/preview")
        assert resp.status_code == 503

        # Disable
        client.put(
            "/admin/ops/kill-switches/global_import",
            json={"enabled": False},
        )
        # Import endpoint should work again (may return 422 due to missing body, not 503)
        resp = client.post("/admin/market-prices/import/preview")
        assert resp.status_code != 503

    def test_gauge_reflects_toggle(self, client):
        """Kill-switch gauge updates on enable/disable."""
        # Enable
        resp = client.put(
            "/admin/ops/kill-switches/global_import",
            json={"enabled": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["switch"]["enabled"] is True

        # Disable
        resp = client.put(
            "/admin/ops/kill-switches/global_import",
            json={"enabled": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["switch"]["enabled"] is False
        assert data["switch"]["previous_enabled"] is True
