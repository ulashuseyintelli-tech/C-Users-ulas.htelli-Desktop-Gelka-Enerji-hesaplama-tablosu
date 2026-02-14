"""
Tests for Alert PromQL Validation — S1/S4/S5 metric outputs.

Validates that fault injection scenario metric outputs
would trigger the corresponding PrometheusRule alerts.

Feature: fault-injection, Task 11.2
Requirements: 8.1, 8.2, 8.3
"""

import pytest

from app.testing.alert_validator import AlertValidator


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def validator():
    return AlertValidator()


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Validation Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestAlertValidatorLoading:
    """AlertValidator loads and parses alert YAML correctly."""

    def test_loads_all_alerts(self, validator):
        names = validator.alert_names
        assert len(names) > 0

    def test_ops_guard_alerts_present(self, validator):
        names = validator.alert_names
        assert "PTFAdminCircuitBreakerOpen" in names
        assert "PTFAdminRateLimitSpike" in names
        assert "PTFAdminGuardInternalError" in names
        assert "PTFAdminKillSwitchActivated" in names


class TestS1AlertValidation:
    """
    S1: DB timeout → CB open → PTFAdminCircuitBreakerOpen should fire.

    Requirement 8.1
    """

    def test_cb_open_fires_alert(self, validator):
        result = validator.check_circuit_breaker_open({"db_primary": 2})
        assert result.would_fire is True
        assert result.alert_name == "PTFAdminCircuitBreakerOpen"
        assert result.metric_value == 2.0

    def test_cb_closed_no_alert(self, validator):
        result = validator.check_circuit_breaker_open({"db_primary": 0})
        assert result.would_fire is False

    def test_cb_half_open_no_alert(self, validator):
        result = validator.check_circuit_breaker_open({"db_primary": 1})
        assert result.would_fire is False

    def test_multiple_deps_one_open(self, validator):
        result = validator.check_circuit_breaker_open({
            "db_primary": 0,
            "external_api": 2,
        })
        assert result.would_fire is True

    def test_empty_states_no_alert(self, validator):
        result = validator.check_circuit_breaker_open({})
        assert result.would_fire is False


class TestS4AlertValidation:
    """
    S4: Rate limit spike → PTFAdminRateLimitSpike should fire.

    Requirement 8.2
    """

    def test_high_deny_rate_fires_alert(self, validator):
        result = validator.check_rate_limit_spike(deny_rate_per_min=10.0)
        assert result.would_fire is True
        assert result.alert_name == "PTFAdminRateLimitSpike"

    def test_low_deny_rate_no_alert(self, validator):
        result = validator.check_rate_limit_spike(deny_rate_per_min=3.0)
        assert result.would_fire is False

    def test_boundary_deny_rate_no_alert(self, validator):
        result = validator.check_rate_limit_spike(deny_rate_per_min=5.0)
        assert result.would_fire is False  # > 5, not >= 5

    def test_just_above_threshold_fires(self, validator):
        result = validator.check_rate_limit_spike(deny_rate_per_min=5.01)
        assert result.would_fire is True


class TestS5AlertValidation:
    """
    S5: Guard internal error → PTFAdminGuardInternalError should fire.

    Requirement 8.3
    """

    def test_error_rate_fires_alert(self, validator):
        result = validator.check_guard_internal_error(error_rate=0.1)
        assert result.would_fire is True
        assert result.alert_name == "PTFAdminGuardInternalError"

    def test_fallback_rate_fires_alert(self, validator):
        result = validator.check_guard_internal_error(fallback_rate=0.05)
        assert result.would_fire is True

    def test_both_rates_fire_alert(self, validator):
        result = validator.check_guard_internal_error(error_rate=0.1, fallback_rate=0.05)
        assert result.would_fire is True

    def test_zero_rates_no_alert(self, validator):
        result = validator.check_guard_internal_error(error_rate=0.0, fallback_rate=0.0)
        assert result.would_fire is False
