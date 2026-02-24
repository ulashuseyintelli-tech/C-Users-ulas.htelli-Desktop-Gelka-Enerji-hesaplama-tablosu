"""
Tests for adaptive control MVP core metrics (Task 9.2a).

Validates: Requirements 11.1, 11.2, 11.3, 8.5
"""

from __future__ import annotations

import pytest

from backend.app.adaptive_control.metrics import (
    VALID_DECISION_REASONS,
    VALID_OUTCOMES,
    VALID_TELEMETRY_REASONS,
    get_metrics,
    record_decision,
    record_telemetry_insufficient,
    reset_metrics,
    set_backpressure_active,
    set_enabled,
    set_retry_after_seconds,
)


@pytest.fixture(autouse=True)
def _clean_metrics():
    reset_metrics()
    yield
    reset_metrics()


class TestHoldPathUpdatesCounterAndGauge:
    """HOLD decision increments decisions_total{outcome=HOLD} and sets backpressure_active=1."""

    def test_hold_path_updates_counter_and_gauge(self):
        record_decision("HOLD", "backpressure_active")
        set_backpressure_active(True)

        m = get_metrics()
        assert m["adaptive_control_decisions_total"][("HOLD", "backpressure_active")] == 1
        assert m["adaptive_control_backpressure_active"] == 1


class TestTelemetryInsufficientPath:
    """Telemetry insufficient increments health counter but NOT decision counter."""

    def test_telemetry_insufficient_path(self):
        record_telemetry_insufficient("MIN_SAMPLES")

        m = get_metrics()
        assert m["adaptive_control_telemetry_insufficient_total"]["MIN_SAMPLES"] == 1
        # Decision counter should remain empty — no decision was made
        assert m["adaptive_control_decisions_total"] == {}


class TestDisabledByDefaultGauge:
    """Enabled gauge starts at 0 (disabled-by-default)."""

    def test_disabled_by_default_gauge(self):
        m = get_metrics()
        assert m["adaptive_control_enabled"] == 0

    def test_enable_sets_gauge_to_1(self):
        set_enabled(True)
        assert get_metrics()["adaptive_control_enabled"] == 1

    def test_disable_resets_gauge_to_0(self):
        set_enabled(True)
        set_enabled(False)
        assert get_metrics()["adaptive_control_enabled"] == 0


class TestInvalidOutcomeRejected:
    """Invalid outcome label raises ValueError."""

    def test_invalid_outcome_rejected(self):
        with pytest.raises(ValueError, match="Invalid outcome"):
            record_decision("INVALID", "normal")


class TestInvalidReasonRejected:
    """Invalid reason label raises ValueError."""

    def test_invalid_decision_reason_rejected(self):
        with pytest.raises(ValueError, match="Invalid reason"):
            record_decision("PASS", "unknown_reason")

    def test_invalid_telemetry_reason_rejected(self):
        with pytest.raises(ValueError, match="Invalid telemetry reason"):
            record_telemetry_insufficient("INVALID_REASON")


class TestMetricsReset:
    """Reset clears all counters and gauges."""

    def test_metrics_reset(self):
        record_decision("PASS", "normal")
        set_enabled(True)
        set_backpressure_active(True)
        record_telemetry_insufficient("SOURCE_STALE")
        set_retry_after_seconds(30.0)

        reset_metrics()
        m = get_metrics()

        assert m["adaptive_control_decisions_total"] == {}
        assert m["adaptive_control_enabled"] == 0
        assert m["adaptive_control_backpressure_active"] == 0
        assert m["adaptive_control_telemetry_insufficient_total"] == {}
        assert m["adaptive_control_retry_after_seconds"] == 0.0


class TestRetryAfterGaugeUpdates:
    """set_retry_after_seconds updates the gauge value."""

    def test_retry_after_gauge_updates(self):
        set_retry_after_seconds(60.0)
        assert get_metrics()["adaptive_control_retry_after_seconds"] == 60.0

        set_retry_after_seconds(120.5)
        assert get_metrics()["adaptive_control_retry_after_seconds"] == 120.5
