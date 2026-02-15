"""
PR-5 CH-3: Time Anomaly chaos tests.

Tests that time jumps (forward, backward, jitter) do not break:
- compensated_divergence invariants (INV-2/INV-3)
- LoadHarness timestamp ordering
- FakeClock determinism
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
from backend.app.testing.load_harness import LoadHarness, DEFAULT_PROFILES
from backend.app.testing.lc_config import ProfileType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compensated_divergence_ms(t1: int, t2: int, max_clock_skew_ms: int = 50) -> int:
    return max(0, abs(t1 - t2) - max_clock_skew_ms)


# ---------------------------------------------------------------------------
# CH-3.1: FakeClock determinism
# ---------------------------------------------------------------------------

class TestFakeClockDeterminism:
    def test_advance_is_monotonic(self):
        clock = FakeClock(start_ms=1000)
        t0 = clock.now_ms
        clock.advance(100)
        t1 = clock.now_ms
        clock.advance(200)
        t2 = clock.now_ms
        assert t0 < t1 < t2

    def test_jump_forward_increases(self):
        clock = FakeClock(start_ms=1000)
        clock.jump_forward(500)
        assert clock.now_ms == 1500

    def test_jump_backward_decreases(self):
        clock = FakeClock(start_ms=1000)
        clock.jump_backward(300)
        assert clock.now_ms == 700

    def test_jump_backward_floors_at_zero(self):
        clock = FakeClock(start_ms=100)
        clock.jump_backward(500)
        assert clock.now_ms == 0

    @given(
        start=st.integers(min_value=0, max_value=10**9),
        fwd=st.integers(min_value=0, max_value=10**6),
        bwd=st.integers(min_value=0, max_value=10**6),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_clock_never_negative(self, start: int, fwd: int, bwd: int):
        """PBT: after any sequence of jumps, clock >= 0."""
        clock = FakeClock(start_ms=start)
        clock.jump_forward(fwd)
        clock.jump_backward(bwd)
        assert clock.now_ms >= 0


# ---------------------------------------------------------------------------
# CH-3.2: Time anomalies don't break compensated_divergence
# ---------------------------------------------------------------------------

class TestTimeAnomalyDivergence:
    @given(
        t1=st.integers(min_value=0, max_value=2**40),
        t2=st.integers(min_value=0, max_value=2**40),
        skew=st.integers(min_value=0, max_value=1000),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_divergence_stable_under_any_timestamps(self, t1, t2, skew):
        """Divergence computation is stable regardless of timestamp magnitude."""
        div = compensated_divergence_ms(t1, t2, skew)
        assert div >= 0
        assert div <= abs(t1 - t2)

    def test_backward_jump_divergence(self):
        """Backward jump creates large raw divergence but compensation handles it."""
        clock1 = FakeClock(start_ms=10000)
        clock2 = FakeClock(start_ms=10000)
        clock1.advance(100)
        clock2.jump_backward(200)  # clock2 goes to 9800
        div = compensated_divergence_ms(clock1.now_ms, clock2.now_ms, 50)
        # raw = |10100 - 9800| = 300, compensated = 250
        assert div == 250

    def test_forward_jump_divergence(self):
        """Forward jump on one clock, normal advance on other."""
        clock1 = FakeClock(start_ms=5000)
        clock2 = FakeClock(start_ms=5000)
        clock1.jump_forward(1000)
        clock2.advance(50)
        div = compensated_divergence_ms(clock1.now_ms, clock2.now_ms, 50)
        # raw = |6000 - 5050| = 950, compensated = 900
        assert div == 900


# ---------------------------------------------------------------------------
# CH-3.3: LoadHarness with FakeClock — timestamp ordering
# ---------------------------------------------------------------------------

class TestLoadHarnessWithFakeClock:
    def test_normal_advance_timestamps_ordered(self):
        clock = FakeClock(start_ms=1000)
        harness = LoadHarness(now_ms_fn=clock.now_ms_fn)
        prof = DEFAULT_PROFILES[ProfileType.BASELINE]
        # Advance between start and end calls
        clock.advance(500)
        result = harness.run_dry(prof)
        # Both timestamps should be the same (clock doesn't auto-advance)
        assert result.started_at_ms == result.finished_at_ms == 1500

    def test_fakeclock_gives_deterministic_timestamps(self):
        """Two runs with same clock state → same timestamps."""
        for _ in range(3):
            clock = FakeClock(start_ms=2000)
            harness = LoadHarness(now_ms_fn=clock.now_ms_fn)
            prof = DEFAULT_PROFILES[ProfileType.BASELINE]
            result = harness.run_dry(prof)
            assert result.started_at_ms == 2000
            assert result.finished_at_ms == 2000

    @given(start=st.integers(min_value=0, max_value=10**9))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_harness_timestamps_match_clock(self, start: int):
        """PBT: LoadResult timestamps always equal FakeClock value."""
        clock = FakeClock(start_ms=start)
        harness = LoadHarness(now_ms_fn=clock.now_ms_fn)
        prof = DEFAULT_PROFILES[ProfileType.BASELINE]
        result = harness.run_dry(prof)
        assert result.started_at_ms == start
        assert result.finished_at_ms == start


# ---------------------------------------------------------------------------
# CH-3.4: FaultSchedule with clock actions — determinism
# ---------------------------------------------------------------------------

class TestFaultScheduleClockActions:
    def test_schedule_with_clock_jumps_is_deterministic(self):
        """Same seed → same schedule including clock actions."""
        actions = [FaultAction.FAIL, FaultAction.CLOCK_JUMP_FWD, FaultAction.CLOCK_JUMP_BWD]
        s1 = FaultSchedule(seed=42, total_steps=50, fault_rate=0.4, allowed_actions=actions)
        s2 = FaultSchedule(seed=42, total_steps=50, fault_rate=0.4, allowed_actions=actions)
        assert [e.action for e in s1.events] == [e.action for e in s2.events]
        assert [e.params for e in s1.events] == [e.params for e in s2.events]

    def test_budget_limits_clock_jumps(self):
        """FaultBudget.max_clock_jumps is enforced."""
        actions = [FaultAction.CLOCK_JUMP_FWD, FaultAction.CLOCK_JUMP_BWD]
        budget = FaultBudget(max_fault_rate=1.0, max_burst=100, max_clock_jumps=2)
        # High fault rate with only clock actions → likely exceeds budget
        schedule = FaultSchedule(seed=99, total_steps=20, fault_rate=0.8, allowed_actions=actions)
        clock_count = sum(
            1 for e in schedule.events
            if e.action in (FaultAction.CLOCK_JUMP_FWD, FaultAction.CLOCK_JUMP_BWD)
        )
        if clock_count > 2:
            assert not budget.validate(schedule)

    @given(seed=st.integers(min_value=0, max_value=2**31 - 1))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_schedule_determinism(self, seed: int):
        """PBT: same seed always produces identical schedule."""
        s1 = FaultSchedule(seed=seed, total_steps=30, fault_rate=0.3)
        s2 = FaultSchedule(seed=seed, total_steps=30, fault_rate=0.3)
        assert [e.action for e in s1.events] == [e.action for e in s2.events]


# ---------------------------------------------------------------------------
# CH-3.5: ChaosTrace replayability
# ---------------------------------------------------------------------------

class TestChaosTraceReplay:
    def test_trace_captures_clock_state(self):
        clock = FakeClock(start_ms=5000)
        trace = ChaosTrace(seed=42, schedule_summary={"total_steps": 3, "fault_count": 1})
        trace.add(0, FaultAction.SKIP, clock.now_ms, "success")
        clock.jump_forward(100)
        trace.add(1, FaultAction.CLOCK_JUMP_FWD, clock.now_ms, "success", {"delta_ms": 100})
        clock.advance(50)
        trace.add(2, FaultAction.SKIP, clock.now_ms, "success")

        assert len(trace.entries) == 3
        assert trace.entries[0].clock_ms == 5000
        assert trace.entries[1].clock_ms == 5100
        assert trace.entries[2].clock_ms == 5150

    def test_replay_info_contains_seed(self):
        trace = ChaosTrace(seed=123, schedule_summary={"total_steps": 10, "fault_count": 3})
        info = trace.replay_info()
        assert info["seed"] == 123
        assert info["total_steps"] == 10
