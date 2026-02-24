"""
Tests for adaptive control events, metrics, and structured logging.

Feature: slo-adaptive-control, Tasks 9.4–9.7
MUST Property: P5 (Audit Completeness Invariant)
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.adaptive_control.events import (
    ControlDecisionEvent,
    clear_event_log,
    emit_control_decision_event,
    emit_failsafe_log,
    emit_signal_log,
    get_event_log,
)
from backend.app.adaptive_control.signals import (
    ControlSignal,
    PriorityLevel,
    SignalType,
)


@pytest.fixture(autouse=True)
def _clear_events():
    clear_event_log()
    yield
    clear_event_log()


signal_type_st = st.sampled_from(list(SignalType))
subsystem_st = st.sampled_from(["guard", "pdf"])
mode_st = st.sampled_from(["enforce", "shadow", "accepting", "backpressure"])


# ══════════════════════════════════════════════════════════════════════════════
# MUST Property P5: Audit Completeness Invariant
# Every mode transition → ControlDecisionEvent with all required fields.
# Validates: Req CC.6, 3.5, 11.6, 11.7, 11.8
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditCompletenessPropertyP5:
    """MUST — Property 5: Audit Completeness Invariant."""

    @given(
        signal_type=signal_type_st,
        subsystem=subsystem_st,
        prev_mode=mode_st,
        new_mode=mode_st,
        trigger_value=st.floats(min_value=0.0, max_value=100.0),
        threshold=st.floats(min_value=0.0, max_value=100.0),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_event_has_all_required_fields(
        self, signal_type, subsystem, prev_mode, new_mode, trigger_value, threshold,
    ):
        """Every emitted event has all required fields (Req 11.6)."""
        signal = ControlSignal(
            signal_type=signal_type,
            subsystem_id=subsystem,
            metric_name="test_metric",
            trigger_value=trigger_value,
            threshold=threshold,
            timestamp_ms=12345,
        )
        event = emit_control_decision_event(signal, prev_mode, new_mode)

        # All required fields present
        assert event.event_id
        assert event.correlation_id
        assert event.reason == signal_type.value
        assert event.previous_mode == prev_mode
        assert event.new_mode == new_mode
        assert event.subsystem_id == subsystem
        assert event.transition_timestamp_ms == 12345
        assert event.trigger_metric == "test_metric"
        assert event.trigger_value == trigger_value
        assert event.threshold == threshold
        assert event.actor == "adaptive_control"

    def test_burn_rate_included_on_budget_exhaustion(self):
        """Error budget exhaustion → burn_rate in event (Req 3.5)."""
        signal = ControlSignal(
            signal_type=SignalType.SWITCH_TO_SHADOW,
            subsystem_id="guard",
            metric_name="5xx_rate",
            trigger_value=2.5,
            threshold=1.0,
            timestamp_ms=1000,
        )
        event = emit_control_decision_event(
            signal, "enforce", "shadow", burn_rate=2.5,
        )
        assert event.burn_rate == 2.5

    def test_event_logged_to_store(self):
        """Events are stored in event log (Req 11.7)."""
        signal = ControlSignal(
            signal_type=SignalType.SWITCH_TO_SHADOW,
            subsystem_id="guard", metric_name="p95",
            timestamp_ms=1000,
        )
        emit_control_decision_event(signal, "enforce", "shadow")
        log = get_event_log()
        assert len(log) == 1
        assert log[0].subsystem_id == "guard"

    @given(count=st.integers(min_value=1, max_value=10))
    @settings(max_examples=20, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_event_count_matches_transitions(self, count: int):
        """N transitions → exactly N events (Req 11.8)."""
        clear_event_log()
        for i in range(count):
            signal = ControlSignal(
                signal_type=SignalType.SWITCH_TO_SHADOW,
                subsystem_id="guard", metric_name="p95",
                timestamp_ms=i * 1000,
            )
            emit_control_decision_event(signal, "enforce", "shadow")
        assert len(get_event_log()) == count


class TestStructuredLogUnit:
    """Unit tests for structured logging."""

    def test_signal_log_does_not_crash(self):
        """emit_signal_log runs without error."""
        signal = ControlSignal(
            signal_type=SignalType.STOP_ACCEPTING_JOBS,
            subsystem_id="pdf", metric_name="queue_depth",
            trigger_value=100.0, threshold=50.0, timestamp_ms=1000,
        )
        emit_signal_log(signal)  # should not raise

    def test_failsafe_log_does_not_crash(self):
        """emit_failsafe_log runs without error."""
        emit_failsafe_log(
            reason="test error",
            exception_type="RuntimeError",
            guard_mode="enforce",
            pdf_mode="accepting",
        )  # should not raise

    def test_no_transition_without_event(self):
        """No event emitted → event log empty (inverse of Req 11.8)."""
        assert get_event_log() == []
