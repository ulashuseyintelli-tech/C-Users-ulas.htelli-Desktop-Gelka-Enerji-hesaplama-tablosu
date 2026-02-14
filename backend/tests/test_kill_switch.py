"""
Unit tests for KillSwitchManager + Admin API.

Feature: ops-guard, Task 4.3

Tests:
  - Hard/soft mode behavior
  - Per-tenant isolation
  - Degrade mode (write blocked, read allowed)
  - Failure semantics (HD-1): high-risk fail-closed, standard fail-open
  - Audit log emission
  - Metric gauge updates
  - Admin API: auth, round-trip (PUT → GET)
"""

import logging
import os
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import CollectorRegistry

from app.guard_config import GuardConfig, GuardDenyReason
from app.kill_switch import KillSwitchManager, KillSwitchEntry
from app.ptf_metrics import PTFMetrics


@pytest.fixture()
def metrics():
    return PTFMetrics(registry=CollectorRegistry())


@pytest.fixture()
def config():
    return GuardConfig()


@pytest.fixture()
def manager(config, metrics):
    return KillSwitchManager(config, metrics)


# ══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS — KillSwitchManager
# ══════════════════════════════════════════════════════════════════════════════


class TestKillSwitchDefaults:
    """Default config → all switches passive."""

    def test_global_import_disabled_by_default(self, manager):
        assert manager.is_import_disabled() is False

    def test_degrade_mode_off_by_default(self, manager):
        assert manager.is_degrade_mode() is False

    def test_no_disabled_tenants_by_default(self, manager):
        assert manager.get_disabled_tenants() == set()

    def test_all_switches_returned(self, manager):
        switches = manager.get_all_switches()
        assert "global_import" in switches
        assert "degrade_mode" in switches


class TestKillSwitchGlobalImport:
    """Global import kill-switch behavior."""

    def test_enable_global_import_disables_import(self, manager):
        manager.set_switch("global_import", True, "admin")
        assert manager.is_import_disabled() is True

    def test_disable_global_import_allows_import(self, manager):
        manager.set_switch("global_import", True, "admin")
        manager.set_switch("global_import", False, "admin")
        assert manager.is_import_disabled() is False

    def test_check_request_blocks_import_endpoint(self, manager):
        manager.set_switch("global_import", True, "admin")
        result = manager.check_request("/admin/market-prices/import/apply", "POST", True)
        assert result == GuardDenyReason.KILL_SWITCHED

    def test_check_request_allows_non_import_when_global_active(self, manager):
        manager.set_switch("global_import", True, "admin")
        result = manager.check_request("/admin/market-prices", "GET", False)
        assert result is None  # ALLOW


class TestKillSwitchPerTenant:
    """Per-tenant kill-switch isolation."""

    def test_tenant_switch_blocks_only_that_tenant(self, manager):
        manager.set_switch("tenant:T1", True, "admin")
        assert manager.is_import_disabled("T1") is True
        assert manager.is_import_disabled("T2") is False

    def test_tenant_switch_in_disabled_set(self, manager):
        manager.set_switch("tenant:T1", True, "admin")
        assert "T1" in manager.get_disabled_tenants()

    def test_disable_tenant_removes_from_set(self, manager):
        manager.set_switch("tenant:T1", True, "admin")
        manager.set_switch("tenant:T1", False, "admin")
        assert "T1" not in manager.get_disabled_tenants()

    def test_config_disabled_tenants_loaded(self, metrics):
        config = GuardConfig()
        with patch.dict(os.environ, {"OPS_GUARD_KILLSWITCH_DISABLED_TENANTS": "T1,T2"}):
            config = GuardConfig()
        mgr = KillSwitchManager(config, metrics)
        disabled = mgr.get_disabled_tenants()
        assert "T1" in disabled
        assert "T2" in disabled


class TestDegradeMode:
    """Degrade mode: write blocked, read allowed."""

    def test_degrade_blocks_post(self, manager):
        manager.set_switch("degrade_mode", True, "admin")
        result = manager.check_request("/admin/market-prices", "POST", False)
        assert result == GuardDenyReason.KILL_SWITCHED

    def test_degrade_blocks_put(self, manager):
        manager.set_switch("degrade_mode", True, "admin")
        result = manager.check_request("/admin/market-prices/2024-01", "PUT", False)
        assert result == GuardDenyReason.KILL_SWITCHED

    def test_degrade_blocks_delete(self, manager):
        manager.set_switch("degrade_mode", True, "admin")
        result = manager.check_request("/customers/1", "DELETE", False)
        assert result == GuardDenyReason.KILL_SWITCHED

    def test_degrade_allows_get(self, manager):
        manager.set_switch("degrade_mode", True, "admin")
        result = manager.check_request("/admin/market-prices", "GET", False)
        assert result is None

    def test_degrade_off_allows_post(self, manager):
        result = manager.check_request("/admin/market-prices", "POST", False)
        assert result is None


class TestFailureSemantics:
    """HD-1: high-risk fail-closed, standard fail-open."""

    def test_high_risk_internal_error_returns_deny(self, manager, metrics):
        """High-risk endpoint + internal error → fail-closed."""
        # Force an exception by corrupting internal state
        original_switches = manager._switches
        manager._switches = None  # will cause TypeError

        result = manager.check_request("/admin/market-prices/import/apply", "POST", True)
        assert result == GuardDenyReason.INTERNAL_ERROR

        # Restore
        manager._switches = original_switches

    def test_standard_internal_error_returns_allow(self, manager, metrics):
        """Standard endpoint + internal error → fail-open."""
        original_switches = manager._switches
        manager._switches = None

        result = manager.check_request("/admin/market-prices", "GET", False)
        assert result is None  # fail-open

        manager._switches = original_switches

    def test_high_risk_error_increments_metric(self, manager, metrics):
        original_switches = manager._switches
        manager._switches = None

        manager.check_request("/import/apply", "POST", True)
        # Error type is AttributeError (NoneType has no .get)
        val = metrics._killswitch_error_total.labels(
            endpoint_class="high_risk", error_type="AttributeError"
        )._value.get()
        assert val >= 1.0

        manager._switches = original_switches

    def test_standard_error_increments_fallback_open(self, manager, metrics):
        original_switches = manager._switches
        manager._switches = None

        manager.check_request("/health", "GET", False)
        val = metrics._killswitch_fallback_open_total._value.get()
        assert val >= 1.0

        manager._switches = original_switches


class TestAuditLog:
    """Audit log emission on switch change."""

    def test_set_switch_logs_audit(self, manager, caplog):
        with caplog.at_level(logging.INFO):
            manager.set_switch("global_import", True, "test-admin")

        assert any("[KILLSWITCH]" in r.message for r in caplog.records)
        assert any("test-admin" in r.message for r in caplog.records)
        assert any("global_import" in r.message for r in caplog.records)

    def test_set_switch_returns_previous_state(self, manager):
        result = manager.set_switch("global_import", True, "admin")
        assert result["previous_enabled"] is False
        assert result["enabled"] is True

        result2 = manager.set_switch("global_import", False, "admin")
        assert result2["previous_enabled"] is True
        assert result2["enabled"] is False


class TestMetricGauges:
    """Kill-switch metric gauge updates."""

    def test_initial_gauges_set(self, metrics):
        config = GuardConfig()
        mgr = KillSwitchManager(config, metrics)
        val = metrics._killswitch_state.labels(switch_name="global_import")._value.get()
        assert val == 0.0  # default: disabled

    def test_enable_sets_gauge_to_1(self, manager, metrics):
        manager.set_switch("global_import", True, "admin")
        val = metrics._killswitch_state.labels(switch_name="global_import")._value.get()
        assert val == 1.0

    def test_disable_sets_gauge_to_0(self, manager, metrics):
        manager.set_switch("global_import", True, "admin")
        manager.set_switch("global_import", False, "admin")
        val = metrics._killswitch_state.labels(switch_name="global_import")._value.get()
        assert val == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — Admin API
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def client():
    """TestClient with admin-key bypassed."""
    with patch.dict(os.environ, {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db
        from fastapi.testclient import TestClient

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db

        # Reset kill-switch singleton for clean test
        import app.main as main_mod
        main_mod._kill_switch_manager = None

        yield TestClient(fastapi_app)

        fastapi_app.dependency_overrides.clear()
        main_mod._kill_switch_manager = None


class TestAdminAPIKillSwitches:
    """Admin API endpoint tests."""

    def test_list_kill_switches(self, client):
        resp = client.get("/admin/ops/kill-switches")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "global_import" in data["switches"]
        assert "degrade_mode" in data["switches"]

    def test_update_kill_switch(self, client):
        resp = client.put(
            "/admin/ops/kill-switches/global_import",
            json={"enabled": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["switch"]["enabled"] is True
        assert data["switch"]["previous_enabled"] is False

    def test_round_trip_put_then_get(self, client):
        # Enable
        client.put(
            "/admin/ops/kill-switches/global_import",
            json={"enabled": True},
        )
        # Verify via GET
        resp = client.get("/admin/ops/kill-switches")
        switches = resp.json()["switches"]
        assert switches["global_import"]["enabled"] is True

    def test_create_tenant_switch_via_put(self, client):
        resp = client.put(
            "/admin/ops/kill-switches/tenant:T99",
            json={"enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["switch"]["enabled"] is True

    def test_ops_status_endpoint(self, client):
        resp = client.get("/admin/ops/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "guard_config" in data
        assert "kill_switches" in data
        assert data["guard_config"]["schema_version"] == "1.0"


class TestAdminAPIAuth:
    """Admin API auth enforcement."""

    def test_kill_switches_requires_auth(self):
        """With auth enabled, missing key → 401."""
        import app.main as main_mod
        from app.database import get_db
        from fastapi.testclient import TestClient

        # Patch module-level vars directly (env is read at import time)
        original_enabled = main_mod.ADMIN_API_KEY_ENABLED
        original_key = main_mod.ADMIN_API_KEY
        main_mod.ADMIN_API_KEY_ENABLED = True
        main_mod.ADMIN_API_KEY = "secret-key"
        main_mod._kill_switch_manager = None

        try:
            mock_db = MagicMock()
            main_mod.app.dependency_overrides[get_db] = lambda: mock_db
            client = TestClient(main_mod.app)

            resp = client.get("/admin/ops/kill-switches")
            assert resp.status_code == 401
        finally:
            main_mod.ADMIN_API_KEY_ENABLED = original_enabled
            main_mod.ADMIN_API_KEY = original_key
            main_mod.app.dependency_overrides.clear()
            main_mod._kill_switch_manager = None
