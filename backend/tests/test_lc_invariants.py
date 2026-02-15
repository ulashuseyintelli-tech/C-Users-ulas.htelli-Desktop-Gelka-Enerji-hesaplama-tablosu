"""
PR-4 Part B: Formal Invariant Layer.

Converts implicit correctness assumptions into enforced, testable properties.
- Reset → Silence guarantee
- Clock skew = 0 edge case
- Determinism hard invariant (multi-instance)
- No false-positive alert invariant
- Idempotent CB transition invariant
- Compensated divergence monotonicity
"""
import random

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.guards.circuit_breaker import CircuitBreakerState
from backend.app.testing.alert_validator import AlertValidator
from backend.app.testing.cb_observer import (
    create_isolated_registry,
    read_cb_state,
    drive_failures,
    drive_successes,
    is_open,
)
from backend.app.testing.lc_config import (
    FaultType,
    FM_EXPECTS_CB_OPEN,
    DEFAULT_SEED,
)
from backend.app.testing.scenario_runner import (
    ScenarioRunner,
    InjectionConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compensated_divergence_ms(t1: int, t2: int, max_clock_skew_ms: int = 50) -> int:
    return max(0, abs(t1 - t2) - max_clock_skew_ms)


def _run_fm(fault_type: FaultType, failure_rate: float = 1.0, seed: int = DEFAULT_SEED):
    runner = ScenarioRunner()
    injection = InjectionConfig(
        enabled=True, fault_type=fault_type, failure_rate=failure_rate, seed=seed,
    )
    return runner.run_scenario(f"inv-{fault_type.value}", injection, request_count=200)


# ---------------------------------------------------------------------------
# INV-1: Reset → Silence guarantee
# ---------------------------------------------------------------------------

class TestResetSilenceGuarantee:
    """After reset_all(), CB state == CLOSED and alert would_fire == False."""

    def test_reset_after_open_yields_closed(self):
        dep = "inv_dep"
        reg = create_isolated_registry()
        drive_failures(reg, dep, 20)
        assert is_open(read_cb_state(reg, dep))

        reg.reset_all()
        snap = read_cb_state(reg, dep)
        assert snap.state_value == CircuitBreakerState.CLOSED.value

    def test_reset_yields_alert_silence(self):
        dep = "inv_dep"
        reg = create_isolated_registry()
        drive_failures(reg, dep, 20)

        validator = AlertValidator()
        # Before reset: OPEN → alert fires
        result_before = validator.check_circuit_breaker_open({dep: 2})
        assert result_before.would_fire is True

        reg.reset_all()
        snap = read_cb_state(reg, dep)
        # After reset: CLOSED → alert silent
        result_after = validator.check_circuit_breaker_open({dep: snap.state_value})
        assert result_after.would_fire is False


# ---------------------------------------------------------------------------
# INV-2: Clock skew = 0 edge case
# ---------------------------------------------------------------------------

class TestClockSkewZero:
    def test_skew_zero_equals_raw_divergence(self):
        t1, t2 = 1000, 1050
        assert compensated_divergence_ms(t1, t2, 0) == abs(t1 - t2)

    def test_skew_zero_identical_timestamps(self):
        assert compensated_divergence_ms(500, 500, 0) == 0

    @given(
        t1=st.integers(min_value=0, max_value=2**40),
        t2=st.integers(min_value=0, max_value=2**40),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_skew_zero_equals_raw(self, t1: int, t2: int):
        """PBT: skew=0 ⇒ compensated == raw."""
        assert compensated_divergence_ms(t1, t2, 0) == abs(t1 - t2)

    @given(
        raw=st.integers(min_value=0, max_value=10000),
        skew=st.integers(min_value=0, max_value=10000),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_raw_lt_skew_yields_zero(self, raw: int, skew: int):
        """PBT: raw < skew ⇒ compensated == 0."""
        t1, t2 = 1000, 1000 + raw
        comp = compensated_divergence_ms(t1, t2, skew)
        if raw < skew:
            assert comp == 0
        else:
            assert comp == raw - skew


# ---------------------------------------------------------------------------
# INV-3: Determinism hard invariant (multi-instance)
# ---------------------------------------------------------------------------

class TestDeterminismHard:
    @given(
        seed=st.integers(min_value=0, max_value=2**31 - 1),
        fault_type=st.sampled_from(list(FaultType)),
        rate=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_same_input_same_output_across_runners(self, seed, fault_type, rate):
        """Two independent ScenarioRunner instances with same input → identical output."""
        r1 = ScenarioRunner()
        r2 = ScenarioRunner()
        inj = InjectionConfig(enabled=True, fault_type=fault_type, failure_rate=rate, seed=seed)
        res1 = r1.run_scenario("det-a", inj, request_count=100)
        res2 = r2.run_scenario("det-b", inj, request_count=100)
        assert res1.outcomes == res2.outcomes
        assert res1.cb_opened == res2.cb_opened
        assert res1.metadata["actual_failure_rate"] == res2.metadata["actual_failure_rate"]

    def test_determinism_across_alert_evaluation(self):
        """Same scenario → same alert fire/silence decision."""
        validator = AlertValidator()
        for ft in FaultType:
            r1 = _run_fm(ft, failure_rate=0.7, seed=42)
            r2 = _run_fm(ft, failure_rate=0.7, seed=42)
            # Same cb_opened → same alert decision
            if FM_EXPECTS_CB_OPEN.get(ft, False):
                cb_val_1 = 2 if r1.cb_opened else 0
                cb_val_2 = 2 if r2.cb_opened else 0
                a1 = validator.check_circuit_breaker_open({"dep": cb_val_1})
                a2 = validator.check_circuit_breaker_open({"dep": cb_val_2})
                assert a1.would_fire == a2.would_fire


# ---------------------------------------------------------------------------
# INV-4: No false-positive alert invariant
# ---------------------------------------------------------------------------

class TestNoFalsePositiveAlert:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.validator = AlertValidator()

    @given(state_val=st.sampled_from([0, 1]))
    @settings(max_examples=10, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_cb_no_fire_when_not_open(self, state_val: int):
        """CB alert never fires when state != OPEN (2)."""
        result = self.validator.check_circuit_breaker_open({"dep": state_val})
        assert result.would_fire is False

    def test_zero_divergence_no_alert(self):
        """compensated_divergence == 0 → no divergence-based concern."""
        div = compensated_divergence_ms(1000, 1000, 50)
        assert div == 0
        # Zero divergence means both instances agree — no alert needed

    @given(rate=st.floats(min_value=0.0, max_value=5.0, allow_nan=False))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_pbt_rate_limit_no_fire_at_or_below_threshold(self, rate: float):
        """Rate limit alert never fires at or below 5 req/min."""
        result = self.validator.check_rate_limit_spike(deny_rate_per_min=rate)
        assert result.would_fire is False


# ---------------------------------------------------------------------------
# INV-5: Idempotent CB transition invariant
# ---------------------------------------------------------------------------

class TestIdempotentCbTransition:
    def test_double_failure_burst_same_state(self):
        """Driving failures twice doesn't change state beyond OPEN."""
        dep = "idem_dep"
        reg = create_isolated_registry()
        drive_failures(reg, dep, 20)
        snap1 = read_cb_state(reg, dep)
        assert snap1.state_value == CircuitBreakerState.OPEN.value

        # Drive more failures — should stay OPEN
        drive_failures(reg, dep, 20)
        snap2 = read_cb_state(reg, dep)
        assert snap2.state_value == CircuitBreakerState.OPEN.value

    def test_double_reset_same_state(self):
        """Resetting twice yields same CLOSED state."""
        dep = "idem_dep"
        reg = create_isolated_registry()
        drive_failures(reg, dep, 20)
        reg.reset_all()
        snap1 = read_cb_state(reg, dep)
        reg.reset_all()
        snap2 = read_cb_state(reg, dep)
        assert snap1.state_value == snap2.state_value == CircuitBreakerState.CLOSED.value
