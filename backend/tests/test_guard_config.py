"""
Unit tests for GuardConfig + schema validation.

Feature: ops-guard, Task 1.3
"""

import os
from unittest.mock import patch

import pytest
from prometheus_client import CollectorRegistry

from app.guard_config import GuardConfig, GuardDenyReason, load_guard_config, get_guard_config
from app.ptf_metrics import PTFMetrics


class TestGuardConfigDefaults:
    """Valid config parse with defaults."""

    def test_default_config_creates_successfully(self):
        config = GuardConfig()
        assert config.schema_version == "1.0"
        assert config.config_version == "default"
        assert config.slo_availability_target == 0.995
        assert config.killswitch_global_import_disabled is False
        assert config.rate_limit_import_per_minute == 10
        assert config.cb_error_threshold_pct == 50.0

    def test_default_killswitch_all_passive(self):
        config = GuardConfig()
        assert config.killswitch_global_import_disabled is False
        assert config.killswitch_degrade_mode is False
        assert config.killswitch_disabled_tenants == ""

    def test_config_hash_deterministic(self):
        c1 = GuardConfig()
        c2 = GuardConfig()
        assert c1.config_hash == c2.config_hash
        assert len(c1.config_hash) == 12


class TestGuardConfigEnvOverride:
    """Environment variable override tests."""

    def test_env_override_slo_target(self):
        with patch.dict(os.environ, {"OPS_GUARD_SLO_AVAILABILITY_TARGET": "0.999"}):
            config = GuardConfig()
            assert config.slo_availability_target == 0.999

    def test_env_override_killswitch(self):
        with patch.dict(os.environ, {"OPS_GUARD_KILLSWITCH_GLOBAL_IMPORT_DISABLED": "true"}):
            config = GuardConfig()
            assert config.killswitch_global_import_disabled is True

    def test_env_override_rate_limit(self):
        with patch.dict(os.environ, {"OPS_GUARD_RATE_LIMIT_IMPORT_PER_MINUTE": "5"}):
            config = GuardConfig()
            assert config.rate_limit_import_per_minute == 5

    def test_env_override_circuit_breaker(self):
        with patch.dict(os.environ, {"OPS_GUARD_CB_OPEN_DURATION_SECONDS": "60.0"}):
            config = GuardConfig()
            assert config.cb_open_duration_seconds == 60.0

    def test_env_override_schema_version(self):
        with patch.dict(os.environ, {"OPS_GUARD_SCHEMA_VERSION": "2.0"}):
            config = GuardConfig()
            assert config.schema_version == "2.0"

    def test_extra_env_vars_ignored(self):
        with patch.dict(os.environ, {"OPS_GUARD_UNKNOWN_FIELD": "whatever"}):
            config = GuardConfig()
            assert config.schema_version == "1.0"  # no error


class TestGuardConfigFallback:
    """Invalid config → fallback defaults + metric (HD-4)."""

    def test_fallback_on_invalid_env(self):
        """Invalid type → fallback to defaults, metric incremented."""
        import app.guard_config as gc_mod

        metrics = PTFMetrics(registry=CollectorRegistry())
        gc_mod._guard_config = None  # reset singleton

        with patch.dict(os.environ, {"OPS_GUARD_SLO_AVAILABILITY_TARGET": "not_a_float"}):
            with patch("app.ptf_metrics.get_ptf_metrics", return_value=metrics):
                config = load_guard_config()

        # Should have fallen back to defaults
        assert config.slo_availability_target == 0.995
        # Fallback metric should have been incremented
        val = metrics._guard_config_fallback_total._value.get()
        assert val == 1.0

        gc_mod._guard_config = None  # cleanup

    def test_fallback_config_is_usable(self):
        """Fallback config should be fully functional."""
        import app.guard_config as gc_mod

        gc_mod._guard_config = None

        with patch.dict(os.environ, {"OPS_GUARD_CB_ERROR_THRESHOLD_PCT": "invalid"}):
            with patch("app.ptf_metrics.get_ptf_metrics"):
                config = load_guard_config()

        assert config.cb_error_threshold_pct == 50.0
        assert config.rate_limit_default_per_minute == 60

        gc_mod._guard_config = None


class TestGuardDenyReason:
    """GuardDenyReason enum tests."""

    def test_all_reasons_are_strings(self):
        for reason in GuardDenyReason:
            assert isinstance(reason.value, str)

    def test_expected_reasons_exist(self):
        assert GuardDenyReason.KILL_SWITCHED == "KILL_SWITCHED"
        assert GuardDenyReason.RATE_LIMITED == "RATE_LIMITED"
        assert GuardDenyReason.CIRCUIT_OPEN == "CIRCUIT_OPEN"
        assert GuardDenyReason.INTERNAL_ERROR == "INTERNAL_ERROR"

    def test_reason_count(self):
        assert len(GuardDenyReason) == 4


class TestGuardConfigSingleton:
    """get_guard_config singleton behavior."""

    def test_returns_config_instance(self):
        import app.guard_config as gc_mod
        gc_mod._guard_config = None
        config = get_guard_config()
        assert isinstance(config, GuardConfig)
        gc_mod._guard_config = None

    def test_returns_same_instance(self):
        import app.guard_config as gc_mod
        gc_mod._guard_config = None
        c1 = get_guard_config()
        c2 = get_guard_config()
        assert c1 is c2
        gc_mod._guard_config = None


class TestOpsGuardMetrics:
    """New ops-guard metrics in PTFMetrics."""

    def setup_method(self):
        self.metrics = PTFMetrics(registry=CollectorRegistry())

    def test_guard_config_fallback_counter(self):
        self.metrics.inc_guard_config_fallback()
        val = self.metrics._guard_config_fallback_total._value.get()
        assert val == 1.0

    def test_guard_config_schema_mismatch_counter(self):
        self.metrics.inc_guard_config_schema_mismatch()
        val = self.metrics._guard_config_schema_mismatch_total._value.get()
        assert val == 1.0

    def test_guard_config_loaded_gauge(self):
        self.metrics.set_guard_config_loaded("1.0", "abc123")
        # Gauge should be set
        val = self.metrics._guard_config_loaded.labels(
            schema_version="1.0", config_version="abc123"
        )._value.get()
        assert val == 1.0

    def test_slo_violation_counter(self):
        self.metrics.inc_slo_violation("availability")
        val = self.metrics._slo_violation_total.labels(slo_name="availability")._value.get()
        assert val == 1.0

    def test_sentinel_impossible_state_counter(self):
        self.metrics.inc_sentinel_impossible_state()
        val = self.metrics._sentinel_impossible_state_total._value.get()
        assert val == 1.0

    def test_killswitch_state_gauge(self):
        self.metrics.set_killswitch_state("global_import", True)
        val = self.metrics._killswitch_state.labels(switch_name="global_import")._value.get()
        assert val == 1.0
        self.metrics.set_killswitch_state("global_import", False)
        val = self.metrics._killswitch_state.labels(switch_name="global_import")._value.get()
        assert val == 0.0

    def test_killswitch_error_counter(self):
        self.metrics.inc_killswitch_error("high_risk", "exception")
        val = self.metrics._killswitch_error_total.labels(
            endpoint_class="high_risk", error_type="exception"
        )._value.get()
        assert val == 1.0

    def test_killswitch_fallback_open_counter(self):
        self.metrics.inc_killswitch_fallback_open()
        val = self.metrics._killswitch_fallback_open_total._value.get()
        assert val == 1.0

    def test_rate_limit_counter(self):
        self.metrics.inc_rate_limit("/admin/market-prices", "allowed")
        val = self.metrics._rate_limit_total.labels(
            endpoint="/admin/market-prices", decision="allowed"
        )._value.get()
        assert val == 1.0

    def test_circuit_breaker_state_gauge(self):
        self.metrics.set_circuit_breaker_state("db_primary", 2)
        val = self.metrics._circuit_breaker_state.labels(dependency="db_primary")._value.get()
        assert val == 2.0
