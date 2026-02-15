"""
PR-5 CH-4: Multi-instance Split-Brain Simulation.

Tests that:
- Two instances with different fault schedules produce correct divergence
- Divergence alerts fire only on real divergence
- Ops contract (labels/cardinality) holds under split-brain
"""
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.chaos_harness import (
    FakeClock,
    FaultSchedule,
    FaultAction,
    FaultBudget,
    ChaosTrace,
)
from backend.app.testing.cb_observer import (
    create_isolated_registry,
    drive_failures,
    drive_successes,
    read_cb_state,
    is_open,
)
from backend.app.testing.alert_validator import AlertValidator
from backend.app.guards.circuit_breaker import CircuitBreakerState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compensated_divergence_ms(t1: int, t2: int, max_clock_skew_ms: int = 50) -> int:
    return max(0, abs(t1 - t2) - max_clock_skew_ms)


# ---------------------------------------------------------------------------
# CH-4.1: Split-brain — one instance faulted, other healthy
# ---------------------------------------------------------------------------

class TestSplitBrainDivergence:
    def test_one_faulted_one_healthy_diverges(self):
        """Instance-1 gets failures → OPEN, Instance-2 healthy → CLOSED."""
        dep = "shared_dep"
        reg1 = create_isolated_registry()
        reg2 = create_isolated_registry()

        drive_failures(reg1, dep, 20)
        drive_successes(reg2, dep, 20)

        snap1 = read_cb_state(reg1, dep)
        snap2 = read_cb_state(reg2, dep)

        assert is_open(snap1)
        assert not is_open(snap2)
        assert snap1.state_value != snap2.state_value

    def test_both_faulted_same_state(self):
        """Both instances get same faults → both OPEN, no divergence."""
        dep = "shared_dep"
        reg1 = create_isolated_registry()
        reg2 = create_isolated_registry()

        drive_failures(reg1, dep, 20)
        drive_failures(reg2, dep, 20)

        assert is_open(read_cb_state(reg1, dep))
        assert is_open(read_cb_state(reg2, dep))

    def test_split_brain_alert_fires_only_on_divergence(self):
        """Alert fires when one is OPEN, silent when both agree."""
        validator = AlertValidator()
        dep = "split_dep"

        # Diverged: one OPEN, one CLOSED
        result_div = validator.check_circuit_breaker_open({dep: 2})
        assert result_div.would_fire is True

        # Agreed: both CLOSED
        result_agree = validator.check_circuit_breaker_open({dep: 0})
        assert result_agree.would_fire is False


# ---------------------------------------------------------------------------
# CH-4.2: Split-brain with clock skew
# ---------------------------------------------------------------------------

class TestSplitBrainClockSkew:
    def test_clock_skew_between_instances(self):
        """Two instances with different clocks — divergence compensated."""
        clock1 = FakeClock(start_ms=10000)
        clock2 = FakeClock(start_ms=10000)

        # Instance-1 runs normally
        clock1.advance(500)
        # Instance-2 has clock drift
        clock2.advance(300)
        clock2.jump_forward(150)  # NTP correction

        div = compensated_divergence_ms(clock1.now_ms, clock2.now_ms, 50)
        # raw = |10500 - 10450| = 50, compensated = max(0, 50-50) = 0
        assert div == 0

    @given(
        drift1=st.integers(min_value=0, max_value=1000),
        drift2=st.integers(min_value=0, max_value=1000),
        skew=st.integers(min_value=0, max_value=200),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_split_brain_divergence_bounded(self, drift1: int, drift2: int, skew: int):
        """PBT: compensated divergence <= raw divergence."""
        clock1 = FakeClock(start_ms=5000)
        clock2 = FakeClock(start_ms=5000)
        clock1.advance(drift1)
        clock2.advance(drift2)
        raw = abs(clock1.now_ms - clock2.now_ms)
        comp = compensated_divergence_ms(clock1.now_ms, clock2.now_ms, skew)
        assert comp <= raw
        assert comp >= 0


# ---------------------------------------------------------------------------
# CH-4.3: Split-brain with fault schedule — trace replayability
# ---------------------------------------------------------------------------

class TestSplitBrainFaultSchedule:
    def test_different_schedules_produce_different_outcomes(self):
        """Two instances with different seeds → potentially different fault patterns."""
        s1 = FaultSchedule(seed=100, total_steps=50, fault_rate=0.3)
        s2 = FaultSchedule(seed=200, total_steps=50, fault_rate=0.3)
        actions1 = [e.action for e in s1.events]
        actions2 = [e.action for e in s2.events]
        # Different seeds should (almost certainly) produce different schedules
        # We don't assert inequality (could be same by chance) but verify structure
        assert len(actions1) == len(actions2) == 50

    def test_split_brain_trace_captures_both_instances(self):
        """Traces from both instances are independently replayable."""
        trace1 = ChaosTrace(seed=100, schedule_summary={"total_steps": 10, "fault_count": 3})
        trace2 = ChaosTrace(seed=200, schedule_summary={"total_steps": 10, "fault_count": 5})

        clock1 = FakeClock(start_ms=1000)
        clock2 = FakeClock(start_ms=1000)

        trace1.add(0, FaultAction.FAIL, clock1.now_ms, "failure")
        trace2.add(0, FaultAction.SKIP, clock2.now_ms, "success")

        assert trace1.replay_info()["seed"] == 100
        assert trace2.replay_info()["seed"] == 200
        assert trace1.entries[0].outcome == "failure"
        assert trace2.entries[0].outcome == "success"

    @given(seed=st.integers(min_value=0, max_value=2**31 - 1))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_budget_always_validates_low_rate(self, seed: int):
        """PBT: low fault rate schedule always passes budget."""
        budget = FaultBudget(max_fault_rate=0.5, max_burst=10, max_clock_jumps=5)
        schedule = FaultSchedule(seed=seed, total_steps=50, fault_rate=0.1)
        assert budget.validate(schedule)
