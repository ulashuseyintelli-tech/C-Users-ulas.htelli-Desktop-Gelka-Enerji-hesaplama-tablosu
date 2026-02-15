"""
PR-3: Multi-instance CB divergence tests (Tasks 6.1 + 6.2 + 6.3).

Uses real CircuitBreakerRegistry instances (isolated per test)
to validate CB state transitions and cross-instance divergence measurement.
"""
import time

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.guards.circuit_breaker import CircuitBreakerState
from backend.app.testing.cb_observer import (
    create_isolated_registry,
    read_cb_state,
    drive_failures,
    drive_successes,
    is_open,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MAX_CLOCK_SKEW_MS_DEFAULT = 50


def compensated_divergence_ms(
    t1: int, t2: int, max_clock_skew_ms: int = MAX_CLOCK_SKEW_MS_DEFAULT,
) -> int:
    """
    Compute divergence between two CB OPEN timestamps,
    compensating for clock skew.
    """
    raw = abs(t1 - t2)
    return max(0, raw - max_clock_skew_ms)


# ---------------------------------------------------------------------------
# Task 6.1: Multi-instance divergence smoke (real CB)
# ---------------------------------------------------------------------------

class TestMultiInstanceDivergence:
    def test_two_instances_both_open_after_failures(self):
        """
        Two isolated registries, same dependency, enough failures → both OPEN.
        """
        dep = "external_api"
        reg1 = create_isolated_registry()
        reg2 = create_isolated_registry()

        # Drive both past threshold (cb_min_samples=10, cb_error_threshold_pct=50)
        # Need enough samples in the window with >50% failure rate
        drive_failures(reg1, dep, 20)
        drive_failures(reg2, dep, 20)

        snap1 = read_cb_state(reg1, dep)
        snap2 = read_cb_state(reg2, dep)

        assert is_open(snap1), f"Instance-1 should be OPEN, got {snap1.state}"
        assert is_open(snap2), f"Instance-2 should be OPEN, got {snap2.state}"

    def test_divergence_when_one_instance_healthy(self):
        """
        Instance-1 gets failures → OPEN.
        Instance-2 gets successes → CLOSED.
        Divergence is real.
        """
        dep = "db_primary"
        reg1 = create_isolated_registry()
        reg2 = create_isolated_registry()

        drive_failures(reg1, dep, 20)
        drive_successes(reg2, dep, 20)

        snap1 = read_cb_state(reg1, dep)
        snap2 = read_cb_state(reg2, dep)

        assert is_open(snap1)
        assert not is_open(snap2)
        # They diverge: one OPEN, one CLOSED
        assert snap1.state_value != snap2.state_value

    def test_compensated_divergence_smoke(self):
        """
        Both instances go OPEN at roughly the same time → low divergence.
        """
        dep = "external_api"
        reg1 = create_isolated_registry()
        reg2 = create_isolated_registry()

        drive_failures(reg1, dep, 20)
        t1 = int(time.time() * 1000)

        drive_failures(reg2, dep, 20)
        t2 = int(time.time() * 1000)

        div = compensated_divergence_ms(t1, t2)
        # Same process, near-instant → divergence should be very small
        assert div <= 5000, f"Divergence too high: {div}ms"


# ---------------------------------------------------------------------------
# Task 6.2: PBT — compensated divergence invariants
# ---------------------------------------------------------------------------

class TestDivergencePBT:
    @given(skew=st.integers(min_value=0, max_value=500))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_compensated_divergence_never_negative(self, skew: int):
        """compensated_divergence_ms >= 0 for any skew."""
        t1, t2 = 1_000_000, 1_000_050
        div = compensated_divergence_ms(t1, t2, skew)
        assert div >= 0

    @given(
        t1=st.integers(min_value=0, max_value=2**40),
        t2=st.integers(min_value=0, max_value=2**40),
        skew=st.integers(min_value=0, max_value=1000),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_compensated_le_raw(self, t1: int, t2: int, skew: int):
        """Compensated divergence <= raw divergence."""
        raw = abs(t1 - t2)
        comp = compensated_divergence_ms(t1, t2, skew)
        assert comp <= raw


# ---------------------------------------------------------------------------
# Task 6.3: CB state transition correctness with real registry
# ---------------------------------------------------------------------------

class TestCbStateTransitions:
    def test_closed_to_open_on_threshold(self):
        """Enough failures in window → OPEN."""
        dep = "test_dep"
        reg = create_isolated_registry()

        # Start CLOSED
        snap = read_cb_state(reg, dep)
        assert snap.state_value == CircuitBreakerState.CLOSED.value

        # Drive past threshold
        drive_failures(reg, dep, 20)
        snap = read_cb_state(reg, dep)
        assert snap.state_value == CircuitBreakerState.OPEN.value

    def test_mixed_traffic_stays_closed(self):
        """Low failure rate → stays CLOSED."""
        dep = "test_dep"
        reg = create_isolated_registry()

        # 3 failures + 17 successes = 15% failure rate < 50% threshold
        drive_failures(reg, dep, 3)
        drive_successes(reg, dep, 17)

        snap = read_cb_state(reg, dep)
        assert snap.state_value == CircuitBreakerState.CLOSED.value
