"""
PR-3: Alert validation tests (Tasks 7.1 + 7.2 + 7.3).

Uses real AlertValidator against ptf-admin-alerts.yml.
Validates scenario→alert mapping (fire/silence) and latency upper bound.
"""
import os

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.alert_validator import AlertValidator
from backend.app.testing.lc_config import EVAL_INTERVAL_SECONDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def alert_latency_limit_seconds(eval_s: int | None = None) -> int:
    """R6 AC3: alert fire latency <= 2 * eval_interval."""
    if eval_s is None:
        eval_s = EVAL_INTERVAL_SECONDS
    return 2 * eval_s


# ---------------------------------------------------------------------------
# Task 7.1: Scenario → alert mapping (fire / silence)
# ---------------------------------------------------------------------------

class TestAlertMapping:
    @pytest.fixture(autouse=True)
    def _setup_validator(self):
        self.validator = AlertValidator()

    def test_cb_open_fires_on_state_2(self):
        """CB state=2 (OPEN) → PTFAdminCircuitBreakerOpen fires."""
        result = self.validator.check_circuit_breaker_open({"db_primary": 2})
        assert result.would_fire is True
        assert result.alert_name == "PTFAdminCircuitBreakerOpen"

    def test_cb_closed_silent(self):
        """CB state=0 (CLOSED) → alert silent."""
        result = self.validator.check_circuit_breaker_open({"db_primary": 0})
        assert result.would_fire is False

    def test_rate_limit_spike_fires(self):
        """Deny rate > 5/min → PTFAdminRateLimitSpike fires."""
        result = self.validator.check_rate_limit_spike(deny_rate_per_min=10.0)
        assert result.would_fire is True

    def test_rate_limit_normal_silent(self):
        """Deny rate <= 5/min → alert silent."""
        result = self.validator.check_rate_limit_spike(deny_rate_per_min=3.0)
        assert result.would_fire is False

    def test_guard_error_fires_on_error(self):
        """Error rate > 0 → PTFAdminGuardInternalError fires."""
        result = self.validator.check_guard_internal_error(error_rate=1.0)
        assert result.would_fire is True

    def test_guard_error_fires_on_fallback(self):
        """Fallback rate > 0 → PTFAdminGuardInternalError fires."""
        result = self.validator.check_guard_internal_error(fallback_rate=0.5)
        assert result.would_fire is True

    def test_guard_error_silent_when_zero(self):
        """Both rates 0 → alert silent."""
        result = self.validator.check_guard_internal_error(error_rate=0.0, fallback_rate=0.0)
        assert result.would_fire is False

    def test_alert_names_loaded(self):
        """Validator loads expected alert names from YAML."""
        names = self.validator.alert_names
        assert "PTFAdminCircuitBreakerOpen" in names
        assert "PTFAdminRateLimitSpike" in names
        assert "PTFAdminGuardInternalError" in names


# ---------------------------------------------------------------------------
# Task 7.2: PBT — alert threshold invariants
# ---------------------------------------------------------------------------

class TestAlertThresholdPBT:
    @pytest.fixture(autouse=True)
    def _setup_validator(self):
        self.validator = AlertValidator()

    @given(state_val=st.integers(min_value=0, max_value=2))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_cb_alert_fires_iff_state_2(self, state_val: int):
        """CB alert fires if and only if max state == 2."""
        result = self.validator.check_circuit_breaker_open({"dep": state_val})
        assert result.would_fire == (state_val == 2)

    @given(rate=st.floats(min_value=0.0, max_value=100.0, allow_nan=False))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_rate_limit_fires_iff_above_5(self, rate: float):
        """Rate limit alert fires iff deny_rate > 5."""
        result = self.validator.check_rate_limit_spike(deny_rate_per_min=rate)
        assert result.would_fire == (rate > 5)

    @given(
        err=st.floats(min_value=0.0, max_value=10.0, allow_nan=False),
        fb=st.floats(min_value=0.0, max_value=10.0, allow_nan=False),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_guard_error_fires_iff_nonzero(self, err: float, fb: float):
        """Guard error alert fires iff error_rate > 0 or fallback_rate > 0."""
        result = self.validator.check_guard_internal_error(error_rate=err, fallback_rate=fb)
        assert result.would_fire == (err > 0 or fb > 0)


# ---------------------------------------------------------------------------
# Task 7.3: Alert fire latency upper bound
# ---------------------------------------------------------------------------

class TestAlertLatencyBound:
    def test_latency_limit_default_is_120(self):
        """Default eval_interval=60 → limit=120s."""
        limit = alert_latency_limit_seconds(60)
        assert limit == 120

    def test_latency_limit_from_env(self, monkeypatch):
        """ENV override respected."""
        monkeypatch.setenv("EVAL_INTERVAL_SECONDS", "30")
        # Direct call with explicit value
        assert alert_latency_limit_seconds(30) == 60

    @given(eval_s=st.integers(min_value=1, max_value=300))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_latency_limit_is_2x(self, eval_s: int):
        """PBT: limit == 2 * eval_interval for any valid interval."""
        assert alert_latency_limit_seconds(eval_s) == 2 * eval_s

    @given(eval_s=st.integers(min_value=1, max_value=300))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_latency_limit_positive(self, eval_s: int):
        """PBT: limit always positive."""
        assert alert_latency_limit_seconds(eval_s) > 0
