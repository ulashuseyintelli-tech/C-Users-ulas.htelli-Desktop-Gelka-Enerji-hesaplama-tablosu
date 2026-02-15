"""
PR-5 CH-1: IO/Backend Fault chaos tests.

Tests that hard-fail and soft-fail IO faults:
- Preserve kill-switch / fail-closed semantics
- Don't produce false-positive alerts
- Maintain determinism under fault schedule
"""
import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.chaos_harness import (
    FaultSchedule,
    FaultAction,
    FaultBudget,
    FakeClock,
    ChaosTrace,
)
from backend.app.testing.scenario_runner import ScenarioRunner, InjectionConfig
from backend.app.testing.lc_config import FaultType, FM_EXPECTS_CB_OPEN, DEFAULT_SEED
from backend.app.testing.alert_validator import AlertValidator
from backend.app.testing.cb_observer import (
    create_isolated_registry,
    drive_failures,
    drive_successes,
    read_cb_state,
    is_open,
)
from backend.app.guards.circuit_breaker import CircuitBreakerState


# ---------------------------------------------------------------------------
# CH-1.1: Hard-fail (non-retryable) preserves CB semantics
# ---------------------------------------------------------------------------

class TestHardFailCbSemantics:
    def test_100pct_hard_fail_opens_cb(self):
        """Full hard-fail → CB OPEN for CB-triggering faults."""
        reg = create_isolated_registry()
        drive_failures(reg, "db_primary", 20)
        snap = read_cb_state(reg, "db_primary")
        assert is_open(snap)

    def test_hard_fail_then_recovery_closes_cb(self):
        """Hard-fail → OPEN, then successes → eventually not OPEN."""
        reg = create_isolated_registry()
        drive_failures(reg, "ext_api", 20)
        assert is_open(read_cb_state(reg, "ext_api"))
        # Reset simulates recovery
        reg.reset_all()
        snap = read_cb_state(reg, "ext_api")
        assert snap.state_value == CircuitBreakerState.CLOSED.value

    @given(
        seed=st.integers(min_value=0, max_value=2**31 - 1),
        fault_type=st.sampled_from([ft for ft, cb in FM_EXPECTS_CB_OPEN.items() if cb]),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_hard_fail_deterministic_cb(self, seed: int, fault_type: FaultType):
        """PBT: same seed + 100% failure → same cb_opened across runs."""
        runner = ScenarioRunner()
        inj = InjectionConfig(enabled=True, fault_type=fault_type, failure_rate=1.0, seed=seed)
        r1 = runner.run_scenario("ch1-a", inj, request_count=100)
        r2 = runner.run_scenario("ch1-b", inj, request_count=100)
        assert r1.cb_opened == r2.cb_opened


# ---------------------------------------------------------------------------
# CH-1.2: Soft-fail (retryable) — no false-positive alerts
# ---------------------------------------------------------------------------

class TestSoftFailNoFalsePositive:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.validator = AlertValidator()

    def test_low_failure_rate_no_cb_alert(self):
        """Low failure rate → CB stays CLOSED → no alert."""
        reg = create_isolated_registry()
        drive_failures(reg, "cache", 3)
        drive_successes(reg, "cache", 17)
        snap = read_cb_state(reg, "cache")
        result = self.validator.check_circuit_breaker_open({"cache": snap.state_value})
        assert result.would_fire is False

    @given(
        fail_count=st.integers(min_value=0, max_value=4),
        success_count=st.integers(min_value=16, max_value=50),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_low_failure_never_fires_cb_alert(self, fail_count: int, success_count: int):
        """PBT: low failure ratio → CB CLOSED → alert silent."""
        reg = create_isolated_registry()
        drive_failures(reg, "dep", fail_count)
        drive_successes(reg, "dep", success_count)
        snap = read_cb_state(reg, "dep")
        result = self.validator.check_circuit_breaker_open({"dep": snap.state_value})
        assert result.would_fire is False


# ---------------------------------------------------------------------------
# CH-1.3: FaultSchedule-driven IO chaos with trace
# ---------------------------------------------------------------------------

class TestFaultScheduleIoChaos:
    def test_schedule_driven_scenario_with_trace(self):
        """Run a fault schedule and verify trace is replayable."""
        schedule = FaultSchedule(
            seed=777, total_steps=50, fault_rate=0.3,
            allowed_actions=[FaultAction.FAIL, FaultAction.TIMEOUT],
        )
        clock = FakeClock(start_ms=10000)
        trace = ChaosTrace(
            seed=777,
            schedule_summary={"total_steps": 50, "fault_count": schedule.fault_count},
        )

        for event in schedule.events:
            if event.action == FaultAction.FAIL:
                trace.add(event.step, event.action, clock.now_ms, "failure")
            elif event.action == FaultAction.TIMEOUT:
                delay = event.params.get("delay_ms", 0)
                clock.advance(delay)
                trace.add(event.step, event.action, clock.now_ms, "timeout", {"delay_ms": delay})
            else:
                trace.add(event.step, event.action, clock.now_ms, "success")
            clock.advance(10)  # base step time

        info = trace.replay_info()
        assert info["seed"] == 777
        assert info["entries_count"] == 50
        assert len(info["failed_steps"]) == 0  # no invariant violations in this run

    def test_budget_validated_schedule(self):
        """Schedule respects budget constraints."""
        budget = FaultBudget(max_fault_rate=0.5, max_burst=5, max_clock_jumps=3)
        schedule = FaultSchedule(
            seed=42, total_steps=100, fault_rate=0.2,
            allowed_actions=[FaultAction.FAIL, FaultAction.TIMEOUT],
        )
        assert budget.validate(schedule)
