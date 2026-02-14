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


# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Wrapper Config Tests — Feature: dependency-wrappers, Task 1.1
# ═══════════════════════════════════════════════════════════════════════════════

from hypothesis import given, settings as h_settings, HealthCheck, assume
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError


class TestWrapperConfigDefaults:
    """DW config fields have correct defaults."""

    def test_cb_precheck_enabled_default_true(self):
        config = GuardConfig()
        assert config.cb_precheck_enabled is True

    def test_wrapper_retry_on_write_default_false(self):
        """DW-1: write path retry OFF by default."""
        config = GuardConfig()
        assert config.wrapper_retry_on_write is False

    def test_wrapper_fail_open_enabled_default_true(self):
        """DW-3: wrapper fail-open ON by default."""
        config = GuardConfig()
        assert config.wrapper_fail_open_enabled is True

    def test_timeout_default(self):
        config = GuardConfig()
        assert config.wrapper_timeout_seconds_default == 5.0

    def test_retry_defaults(self):
        config = GuardConfig()
        assert config.wrapper_retry_max_attempts_default == 2
        assert config.wrapper_retry_backoff_base_ms == 500
        assert config.wrapper_retry_backoff_cap_ms == 5000
        assert config.wrapper_retry_jitter_pct == 0.2

    def test_per_dependency_overrides_empty_by_default(self):
        config = GuardConfig()
        assert config.wrapper_timeout_seconds_by_dependency == ""
        assert config.wrapper_retry_max_attempts_by_dependency == ""


class TestWrapperConfigEnvOverride:
    """Environment variable override for wrapper fields."""

    def test_env_override_cb_precheck_enabled(self):
        with patch.dict(os.environ, {"OPS_GUARD_CB_PRECHECK_ENABLED": "false"}):
            config = GuardConfig()
            assert config.cb_precheck_enabled is False

    def test_env_override_wrapper_retry_on_write(self):
        with patch.dict(os.environ, {"OPS_GUARD_WRAPPER_RETRY_ON_WRITE": "true"}):
            config = GuardConfig()
            assert config.wrapper_retry_on_write is True

    def test_env_override_timeout_default(self):
        with patch.dict(os.environ, {"OPS_GUARD_WRAPPER_TIMEOUT_SECONDS_DEFAULT": "10.0"}):
            config = GuardConfig()
            assert config.wrapper_timeout_seconds_default == 10.0

    def test_env_override_retry_max_attempts(self):
        with patch.dict(os.environ, {"OPS_GUARD_WRAPPER_RETRY_MAX_ATTEMPTS_DEFAULT": "3"}):
            config = GuardConfig()
            assert config.wrapper_retry_max_attempts_default == 3

    def test_env_override_backoff_base(self):
        with patch.dict(os.environ, {"OPS_GUARD_WRAPPER_RETRY_BACKOFF_BASE_MS": "200"}):
            config = GuardConfig()
            assert config.wrapper_retry_backoff_base_ms == 200

    def test_env_override_backoff_cap(self):
        with patch.dict(os.environ, {"OPS_GUARD_WRAPPER_RETRY_BACKOFF_CAP_MS": "10000"}):
            config = GuardConfig()
            assert config.wrapper_retry_backoff_cap_ms == 10000

    def test_env_override_jitter_pct(self):
        with patch.dict(os.environ, {"OPS_GUARD_WRAPPER_RETRY_JITTER_PCT": "0.1"}):
            config = GuardConfig()
            assert config.wrapper_retry_jitter_pct == 0.1


class TestWrapperConfigValidation:
    """Invalid values → ValidationError (config load will fallback via HD-4)."""

    def test_negative_timeout_raises(self):
        with pytest.raises(PydanticValidationError):
            GuardConfig(wrapper_timeout_seconds_default=-1.0)

    def test_zero_timeout_raises(self):
        with pytest.raises(PydanticValidationError):
            GuardConfig(wrapper_timeout_seconds_default=0.0)

    def test_negative_retry_attempts_raises(self):
        with pytest.raises(PydanticValidationError):
            GuardConfig(wrapper_retry_max_attempts_default=-1)

    def test_zero_retry_attempts_valid(self):
        """0 retries = no retry, which is valid."""
        config = GuardConfig(wrapper_retry_max_attempts_default=0)
        assert config.wrapper_retry_max_attempts_default == 0

    def test_negative_backoff_base_raises(self):
        with pytest.raises(PydanticValidationError):
            GuardConfig(wrapper_retry_backoff_base_ms=-100)

    def test_zero_backoff_base_raises(self):
        with pytest.raises(PydanticValidationError):
            GuardConfig(wrapper_retry_backoff_base_ms=0)

    def test_negative_backoff_cap_raises(self):
        with pytest.raises(PydanticValidationError):
            GuardConfig(wrapper_retry_backoff_cap_ms=-1)

    def test_negative_jitter_raises(self):
        with pytest.raises(PydanticValidationError):
            GuardConfig(wrapper_retry_jitter_pct=-0.1)

    def test_jitter_over_one_raises(self):
        with pytest.raises(PydanticValidationError):
            GuardConfig(wrapper_retry_jitter_pct=1.5)

    def test_invalid_timeout_env_triggers_fallback(self):
        """Invalid env → load_guard_config falls back to defaults (HD-4)."""
        import app.guard_config as gc_mod
        gc_mod._guard_config = None

        with patch.dict(os.environ, {"OPS_GUARD_WRAPPER_TIMEOUT_SECONDS_DEFAULT": "-5"}):
            with patch("app.ptf_metrics.get_ptf_metrics"):
                config = load_guard_config()

        assert config.wrapper_timeout_seconds_default == 5.0  # fallback default
        gc_mod._guard_config = None


class TestWrapperConfigPerDependencyOverride:
    """Per-dependency timeout/retry override via JSON env var."""

    def test_timeout_override_valid_json(self):
        config = GuardConfig(
            wrapper_timeout_seconds_by_dependency='{"db_primary": 3.0, "external_api": 15.0}'
        )
        assert config.get_timeout_for_dependency("db_primary") == 3.0
        assert config.get_timeout_for_dependency("external_api") == 15.0
        # Not overridden → default
        assert config.get_timeout_for_dependency("cache") == 5.0

    def test_timeout_override_unknown_dependency_ignored(self):
        """HD-5: only Dependency enum keys accepted."""
        config = GuardConfig(
            wrapper_timeout_seconds_by_dependency='{"unknown_dep": 99.0, "db_primary": 3.0}'
        )
        assert config.get_timeout_for_dependency("db_primary") == 3.0
        assert config.get_timeout_for_dependency("unknown_dep") == 5.0  # falls to default

    def test_timeout_override_invalid_json_returns_default(self):
        config = GuardConfig(wrapper_timeout_seconds_by_dependency="not_json")
        assert config.get_timeout_for_dependency("db_primary") == 5.0

    def test_timeout_override_empty_string(self):
        config = GuardConfig(wrapper_timeout_seconds_by_dependency="")
        assert config.get_timeout_for_dependency("db_primary") == 5.0

    def test_timeout_override_negative_value_returns_default(self):
        config = GuardConfig(
            wrapper_timeout_seconds_by_dependency='{"db_primary": -1.0}'
        )
        assert config.get_timeout_for_dependency("db_primary") == 5.0  # negative → default

    def test_retry_override_valid_json(self):
        config = GuardConfig(
            wrapper_retry_max_attempts_by_dependency='{"external_api": 3, "cache": 1}'
        )
        assert config.get_retry_max_attempts_for_dependency("external_api") == 3
        assert config.get_retry_max_attempts_for_dependency("cache") == 1
        assert config.get_retry_max_attempts_for_dependency("db_primary") == 2  # default

    def test_retry_override_unknown_dependency_ignored(self):
        config = GuardConfig(
            wrapper_retry_max_attempts_by_dependency='{"bogus": 99}'
        )
        assert config.get_retry_max_attempts_for_dependency("bogus") == 2  # default

    def test_retry_override_invalid_json_returns_default(self):
        config = GuardConfig(wrapper_retry_max_attempts_by_dependency="{bad")
        assert config.get_retry_max_attempts_for_dependency("db_primary") == 2

    def test_env_override_per_dependency_timeout(self):
        with patch.dict(os.environ, {
            "OPS_GUARD_WRAPPER_TIMEOUT_SECONDS_BY_DEPENDENCY": '{"external_api": 20.0}'
        }):
            config = GuardConfig()
            assert config.get_timeout_for_dependency("external_api") == 20.0
            assert config.get_timeout_for_dependency("db_primary") == 5.0


class TestWrapperConfigFallbackInclusion:
    """Fallback config includes wrapper fields."""

    def test_fallback_has_wrapper_defaults(self):
        import app.guard_config as gc_mod
        gc_mod._guard_config = None

        with patch.dict(os.environ, {"OPS_GUARD_WRAPPER_TIMEOUT_SECONDS_DEFAULT": "not_float"}):
            with patch("app.ptf_metrics.get_ptf_metrics"):
                config = load_guard_config()

        # Wrapper fields should be present with defaults
        assert config.cb_precheck_enabled is True
        assert config.wrapper_retry_on_write is False
        assert config.wrapper_fail_open_enabled is True
        assert config.wrapper_timeout_seconds_default == 5.0
        assert config.wrapper_retry_max_attempts_default == 2
        assert config.wrapper_retry_backoff_base_ms == 500
        assert config.wrapper_retry_backoff_cap_ms == 5000
        assert config.wrapper_retry_jitter_pct == 0.2

        gc_mod._guard_config = None


class TestWrapperConfigPropertyBased:
    """Property-based tests for wrapper config — Feature: dependency-wrappers, Property 8."""

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        timeout=st.floats(min_value=0.001, max_value=300.0),
        max_retries=st.integers(min_value=0, max_value=10),
        backoff_base=st.integers(min_value=1, max_value=10000),
        backoff_cap=st.integers(min_value=1, max_value=60000),
        jitter=st.floats(min_value=0.0, max_value=1.0),
    )
    def test_wrapper_config_round_trip(
        self, timeout, max_retries, backoff_base, backoff_cap, jitter
    ):
        """Feature: dependency-wrappers, Property 8: Guard Config Wrapper Ayarları Round-Trip"""
        assume(backoff_base <= backoff_cap)
        config = GuardConfig(
            wrapper_timeout_seconds_default=timeout,
            wrapper_retry_max_attempts_default=max_retries,
            wrapper_retry_backoff_base_ms=backoff_base,
            wrapper_retry_backoff_cap_ms=backoff_cap,
            wrapper_retry_jitter_pct=jitter,
        )
        assert config.wrapper_timeout_seconds_default == timeout
        assert config.wrapper_retry_max_attempts_default == max_retries
        assert config.wrapper_retry_backoff_base_ms == backoff_base
        assert config.wrapper_retry_backoff_cap_ms == backoff_cap
        assert config.wrapper_retry_jitter_pct == jitter

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        cb_precheck=st.booleans(),
        retry_on_write=st.booleans(),
        fail_open=st.booleans(),
    )
    def test_wrapper_flags_round_trip(self, cb_precheck, retry_on_write, fail_open):
        """Feature: dependency-wrappers, Property 8: Boolean flags round-trip."""
        config = GuardConfig(
            cb_precheck_enabled=cb_precheck,
            wrapper_retry_on_write=retry_on_write,
            wrapper_fail_open_enabled=fail_open,
        )
        assert config.cb_precheck_enabled is cb_precheck
        assert config.wrapper_retry_on_write is retry_on_write
        assert config.wrapper_fail_open_enabled is fail_open

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        dep=st.sampled_from(["db_primary", "db_replica", "cache", "external_api", "import_worker"]),
        override_val=st.floats(min_value=0.1, max_value=60.0),
    )
    def test_per_dependency_timeout_override(self, dep, override_val):
        """Feature: dependency-wrappers, Property 8: Per-dependency timeout override."""
        import json
        config = GuardConfig(
            wrapper_timeout_seconds_by_dependency=json.dumps({dep: override_val})
        )
        assert config.get_timeout_for_dependency(dep) == override_val

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        dep=st.sampled_from(["db_primary", "db_replica", "cache", "external_api", "import_worker"]),
        override_val=st.integers(min_value=0, max_value=5),
    )
    def test_per_dependency_retry_override(self, dep, override_val):
        """Feature: dependency-wrappers, Property 8: Per-dependency retry override."""
        import json
        config = GuardConfig(
            wrapper_retry_max_attempts_by_dependency=json.dumps({dep: override_val})
        )
        assert config.get_retry_max_attempts_for_dependency(dep) == override_val


class TestWrapperConfigOverrideMetricIncrement:
    """Invalid JSON override → fallback + metric increment (GuardConfigInvalid alert hook)."""

    def test_invalid_json_increments_fallback_metric(self):
        metrics = PTFMetrics(registry=CollectorRegistry())
        config = GuardConfig(wrapper_timeout_seconds_by_dependency="not_json")

        with patch("app.ptf_metrics.get_ptf_metrics", return_value=metrics):
            # Trigger parse
            result = config.get_timeout_for_dependency("db_primary")

        assert result == 5.0  # fallback
        val = metrics._guard_config_fallback_total._value.get()
        assert val >= 1.0

    def test_unknown_dependency_key_increments_fallback_metric(self):
        metrics = PTFMetrics(registry=CollectorRegistry())
        config = GuardConfig(
            wrapper_timeout_seconds_by_dependency='{"bogus_dep": 10.0, "db_primary": 3.0}'
        )

        with patch("app.ptf_metrics.get_ptf_metrics", return_value=metrics):
            result = config.get_timeout_for_dependency("db_primary")

        assert result == 3.0  # valid key still works
        val = metrics._guard_config_fallback_total._value.get()
        assert val >= 1.0  # incremented for bogus_dep

    def test_non_dict_json_increments_fallback_metric(self):
        metrics = PTFMetrics(registry=CollectorRegistry())
        config = GuardConfig(wrapper_timeout_seconds_by_dependency='[1, 2, 3]')

        with patch("app.ptf_metrics.get_ptf_metrics", return_value=metrics):
            result = config.get_timeout_for_dependency("db_primary")

        assert result == 5.0
        val = metrics._guard_config_fallback_total._value.get()
        assert val >= 1.0


class TestWrapperConfigBackoffMonotonicity:
    """Cross-field: backoff_base_ms <= backoff_cap_ms."""

    def test_base_greater_than_cap_raises(self):
        with pytest.raises(PydanticValidationError):
            GuardConfig(
                wrapper_retry_backoff_base_ms=10000,
                wrapper_retry_backoff_cap_ms=500,
            )

    def test_base_equals_cap_valid(self):
        config = GuardConfig(
            wrapper_retry_backoff_base_ms=1000,
            wrapper_retry_backoff_cap_ms=1000,
        )
        assert config.wrapper_retry_backoff_base_ms == 1000
        assert config.wrapper_retry_backoff_cap_ms == 1000

    def test_base_less_than_cap_valid(self):
        config = GuardConfig(
            wrapper_retry_backoff_base_ms=500,
            wrapper_retry_backoff_cap_ms=5000,
        )
        assert config.wrapper_retry_backoff_base_ms == 500

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        base=st.integers(min_value=1, max_value=10000),
        cap=st.integers(min_value=1, max_value=10000),
    )
    def test_backoff_monotonicity_property(self, base, cap):
        """Feature: dependency-wrappers, Property: backoff_base <= backoff_cap invariant."""
        if base <= cap:
            config = GuardConfig(
                wrapper_retry_backoff_base_ms=base,
                wrapper_retry_backoff_cap_ms=cap,
            )
            assert config.wrapper_retry_backoff_base_ms <= config.wrapper_retry_backoff_cap_ms
        else:
            with pytest.raises(PydanticValidationError):
                GuardConfig(
                    wrapper_retry_backoff_base_ms=base,
                    wrapper_retry_backoff_cap_ms=cap,
                )

    def test_invalid_monotonicity_env_triggers_fallback(self):
        """base > cap via env → load_guard_config falls back to defaults."""
        import app.guard_config as gc_mod
        gc_mod._guard_config = None

        with patch.dict(os.environ, {
            "OPS_GUARD_WRAPPER_RETRY_BACKOFF_BASE_MS": "10000",
            "OPS_GUARD_WRAPPER_RETRY_BACKOFF_CAP_MS": "500",
        }):
            with patch("app.ptf_metrics.get_ptf_metrics"):
                config = load_guard_config()

        # Should have fallen back to defaults
        assert config.wrapper_retry_backoff_base_ms == 500
        assert config.wrapper_retry_backoff_cap_ms == 5000
        gc_mod._guard_config = None
