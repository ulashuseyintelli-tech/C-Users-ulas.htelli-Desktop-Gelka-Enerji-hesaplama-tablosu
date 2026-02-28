"""
Drift Guard v0 - Middleware Integration Tests (Tasks 4.9-4.13).
Requirements: DR1.1-DR6.3, DR4.1-DR4.7
"""
from __future__ import annotations
import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
import pytest
from app.guard_config import GuardConfig


def _cfg(**kw):
    d = dict(
        schema_version="1.0", config_version="test",
        last_updated_at="2026-02-16T00:00:00Z",
        cb_precheck_enabled=False,
        decision_layer_enabled=True,
        decision_layer_mode="enforce",
        decision_layer_default_mode="enforce",
        drift_guard_enabled=True,
        drift_guard_killswitch=False,
        drift_guard_fail_open=True,
        drift_guard_provider_timeout_ms=100,
    )
    d.update(kw)
    return GuardConfig.model_construct(**d)


@contextmanager
def _env(config):
    import app.ops_guard_middleware as ogm
    import app.main as main_mod
    import app.guard_config as gc_mod
    ogm._rate_limit_guard = None
    main_mod._kill_switch_manager = None
    prev = gc_mod._guard_config
    gc_mod._guard_config = config
    try:
        with patch.dict(os.environ, {
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }):
            from app.main import app as fastapi_app
            from app.database import get_db
            from fastapi.testclient import TestClient
            mock_db = MagicMock()
            fastapi_app.dependency_overrides[get_db] = lambda: mock_db
            try:
                yield TestClient(fastapi_app)
            finally:
                fastapi_app.dependency_overrides.clear()
    finally:
        ogm._rate_limit_guard = None
        main_mod._kill_switch_manager = None
        gc_mod._guard_config = prev


EP = "/admin/market-prices/deprecation-stats"
HDR = {"X-Tenant-Id": "t1"}
RMAP = '{"/admin":"high"}'


def _ops():
    return [
        patch("app.kill_switch.KillSwitchManager.check_request", return_value=None),
        patch("app.guards.rate_limit_guard.RateLimitGuard.check_request", return_value=None),
        patch("app.ops_guard_middleware.OpsGuardMiddleware._check_circuit_breaker", return_value=None),
    ]


# === 4.10 Disabled mode ===
class TestDGM410DisabledMode:
    def test_provider_not_called(self):
        with _env(_cfg(drift_guard_enabled=False)) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.guards.drift_guard.HashDriftInputProvider.get_input") as spy:
                c.get(EP, headers=HDR)
            spy.assert_not_called()

    def test_evaluate_drift_not_called(self):
        with _env(_cfg(drift_guard_enabled=False)) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.guards.drift_guard.evaluate_drift") as spy:
                c.get(EP, headers=HDR)
            spy.assert_not_called()

    def test_drift_metrics_not_called(self):
        with _env(_cfg(drift_guard_enabled=False)) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.ptf_metrics.PTFMetrics.inc_drift_evaluation") as spy:
                c.get(EP, headers=HDR)
            spy.assert_not_called()


# === 4.11 Kill-switch ===
class TestDGM411KillSwitch:
    def _run(self, mode="enforce"):
        cfg = _cfg(drift_guard_enabled=True, drift_guard_killswitch=True,
                    decision_layer_default_mode=mode)
        spies = {}
        with _env(cfg) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.guards.drift_guard.HashDriftInputProvider.get_input") as s1, \
                 patch("app.guards.drift_guard.evaluate_drift") as s2, \
                 patch("app.ptf_metrics.PTFMetrics.inc_drift_evaluation") as s3, \
                 patch("app.guards.drift_guard.build_baseline") as s4:
                c.get(EP, headers=HDR)
                spies = dict(provider=s1, evaluate=s2, metric=s3, baseline=s4)
        return spies

    def test_enforce_all_zero(self):
        for name, spy in self._run("enforce").items():
            spy.assert_not_called()

    def test_shadow_all_zero(self):
        for name, spy in self._run("shadow").items():
            spy.assert_not_called()


# === 4.12 Mode dispatch ===
class TestDGM412ModeDispatch:
    def test_shadow_drift_proceeds(self):
        from app.guards.drift_guard import DriftDecision, DriftReasonCode
        drift = DriftDecision(is_drift=True,
                              reason_code=DriftReasonCode.THRESHOLD_EXCEEDED,
                              detail="config_hash mismatch")
        with _env(_cfg(decision_layer_default_mode="shadow")) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.guards.drift_guard.evaluate_drift", return_value=drift):
                resp = c.get(EP, headers=HDR)
        if resp.status_code == 503:
            assert resp.json().get("errorCode") != "OPS_GUARD_DRIFT"

    def test_enforce_drift_blocks_503(self):
        from app.guards.drift_guard import DriftDecision, DriftReasonCode
        drift = DriftDecision(is_drift=True,
                              reason_code=DriftReasonCode.THRESHOLD_EXCEEDED,
                              detail="config_hash mismatch")
        cfg = _cfg(decision_layer_default_mode="enforce",
                    decision_layer_endpoint_risk_map_json=RMAP)
        with _env(cfg) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.guards.drift_guard.evaluate_drift", return_value=drift):
                resp = c.get(EP, headers=HDR)
        assert resp.status_code == 503
        body = resp.json()
        assert body["errorCode"] == "OPS_GUARD_DRIFT"
        assert "DRIFT:THRESHOLD_EXCEEDED" in body["reasonCodes"]

    def test_no_drift_not_blocked(self):
        from app.guards.drift_guard import DriftDecision
        no_drift = DriftDecision(is_drift=False)
        cfg = _cfg(decision_layer_default_mode="enforce",
                    decision_layer_endpoint_risk_map_json=RMAP)
        with _env(cfg) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.guards.drift_guard.evaluate_drift", return_value=no_drift):
                resp = c.get(EP, headers=HDR)
        if resp.status_code == 503:
            assert resp.json().get("errorCode") != "OPS_GUARD_DRIFT"


# === 4.9 Provider failure ===
class TestDGM49ProviderFailure:
    def test_shadow_provider_error_proceeds(self):
        cfg = _cfg(decision_layer_default_mode="shadow", drift_guard_fail_open=True)
        with _env(cfg) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.guards.drift_guard.HashDriftInputProvider.get_input",
                       side_effect=RuntimeError("boom")):
                resp = c.get(EP, headers=HDR)
        if resp.status_code == 503:
            assert resp.json().get("errorCode") != "OPS_GUARD_DRIFT"

    def test_enforce_fail_open_proceeds(self):
        cfg = _cfg(decision_layer_default_mode="enforce",
                    decision_layer_endpoint_risk_map_json=RMAP,
                    drift_guard_fail_open=True)
        with _env(cfg) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.guards.drift_guard.HashDriftInputProvider.get_input",
                       side_effect=RuntimeError("boom")):
                resp = c.get(EP, headers=HDR)
        if resp.status_code == 503:
            assert resp.json().get("errorCode") != "OPS_GUARD_DRIFT"

    def test_enforce_fail_closed_blocks(self):
        cfg = _cfg(decision_layer_default_mode="enforce",
                    decision_layer_endpoint_risk_map_json=RMAP,
                    drift_guard_fail_open=False)
        with _env(cfg) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.guards.drift_guard.HashDriftInputProvider.get_input",
                       side_effect=RuntimeError("boom")):
                resp = c.get(EP, headers=HDR)
        assert resp.status_code == 503
        body = resp.json()
        assert body["errorCode"] == "OPS_GUARD_DRIFT"
        assert "DRIFT:PROVIDER_ERROR" in body["reasonCodes"]

    def test_provider_error_emits_metric(self):
        cfg = _cfg(decision_layer_default_mode="enforce",
                    decision_layer_endpoint_risk_map_json=RMAP,
                    drift_guard_fail_open=True)
        with _env(cfg) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.guards.drift_guard.HashDriftInputProvider.get_input",
                       side_effect=RuntimeError("boom")), \
                 patch("app.ptf_metrics.PTFMetrics.inc_drift_evaluation") as spy:
                c.get(EP, headers=HDR)
        calls = [a for a in spy.call_args_list
                 if len(a.args) >= 2 and a.args[1] == "provider_error"]
        assert len(calls) >= 1


# === 4.13 wouldEnforce ===
class TestDGM413WouldEnforce:
    def test_shadow_drift_emits_drift_detected(self):
        from app.guards.drift_guard import DriftDecision, DriftReasonCode
        drift = DriftDecision(is_drift=True,
                              reason_code=DriftReasonCode.THRESHOLD_EXCEEDED,
                              detail="mismatch")
        with _env(_cfg(decision_layer_default_mode="shadow")) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.guards.drift_guard.evaluate_drift", return_value=drift), \
                 patch("app.ptf_metrics.PTFMetrics.inc_drift_evaluation") as spy:
                resp = c.get(EP, headers=HDR)
        if resp.status_code == 503:
            assert resp.json().get("errorCode") != "OPS_GUARD_DRIFT"
        calls = [a for a in spy.call_args_list
                 if len(a.args) >= 2 and a.args[0] == "shadow"
                 and a.args[1] == "drift_detected"]
        assert len(calls) >= 1

    def test_disabled_no_drift_metric(self):
        with _env(_cfg(drift_guard_enabled=False)) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.ptf_metrics.PTFMetrics.inc_drift_evaluation") as spy:
                c.get(EP, headers=HDR)
        spy.assert_not_called()

    def test_killswitch_no_drift_metric(self):
        cfg = _cfg(drift_guard_enabled=True, drift_guard_killswitch=True)
        with _env(cfg) as c:
            p = _ops()
            with p[0], p[1], p[2], \
                 patch("app.ptf_metrics.PTFMetrics.inc_drift_evaluation") as spy:
                c.get(EP, headers=HDR)
        spy.assert_not_called()
