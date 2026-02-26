"""
PR-3 + Task 6.1: Multi-instance CB divergence tests.

PR-3 base (preserved): TestMultiInstanceDivergence, TestDivergencePBT, TestCbStateTransitions
Task 6.1 additions: TestMI1ScenarioRunnerMultiInstance (AC1/AC2/AC6),
                     TestMI2DivergenceThreshold (AC3/AC4/AC5)

Uses real CircuitBreakerRegistry instances (isolated per test)
to validate CB state transitions and cross-instance divergence measurement.
"""
import time

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.guard_config import GuardConfig
from backend.app.guards.circuit_breaker import CircuitBreakerState
from backend.app.testing.cb_observer import (
    create_isolated_registry,
    read_cb_state,
    drive_failures,
    drive_until_open,
    drive_successes,
    is_open,
    compensated_divergence_ms as _obs_compensated_divergence_ms,
    evaluate_divergence,
)
from backend.app.testing.lc_config import (
    DEFAULT_SEED,
    FaultType,
    ProfileType,
)
from backend.app.testing.load_harness import DEFAULT_PROFILES, LoadProfile
from backend.app.testing.scenario_runner import (
    InjectionConfig,
    ScenarioResult,
    ScenarioRunner,
)
from backend.app.testing.stress_report import TuningRecommendation


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


# ═══════════════════════════════════════════════════════════════════════════
# Task 6.1: TestMI1 — ScenarioRunner multi-instance (AC1, AC2, AC6)
# ═══════════════════════════════════════════════════════════════════════════

# GNK-1 required keys
_SUMMARY_REQUIRED_KEYS = {"scenario_id", "cb_opened", "diagnostic_count", "load", "metrics"}

_CI_SCALE = 0.01
_CI_PROFILE = DEFAULT_PROFILES[ProfileType.BASELINE]


def _mi_injection(
    fault_type: FaultType = FaultType.DB_TIMEOUT,
    failure_rate: float = 0.40,
    seed: int = DEFAULT_SEED,
) -> InjectionConfig:
    return InjectionConfig(
        enabled=True,
        fault_type=fault_type,
        failure_rate=failure_rate,
        seed=seed,
        profile=_CI_PROFILE,
        scale_factor=_CI_SCALE,
    )


class TestMI1ScenarioRunnerMultiInstance:
    """
    R5 AC1/AC2/AC6: ScenarioRunner.run_multi_instance_scenario with 2 instances.
    %40 DB_TIMEOUT injection → each instance runs independently.
    """

    @pytest.mark.asyncio
    async def test_two_instances_both_return_results(self):
        """AC1/AC6: 2 instances, each returns a ScenarioResult."""
        runner = ScenarioRunner()
        inj = _mi_injection(failure_rate=0.40)
        results = await runner.run_multi_instance_scenario("mi1-basic", inj, instance_count=2)
        assert len(results) == 2, f"Expected 2 results, got {len(results)}"

    @pytest.mark.asyncio
    async def test_each_instance_has_load_result(self):
        """AC6: Each result has a non-None load_result."""
        runner = ScenarioRunner()
        results = await runner.run_multi_instance_scenario(
            "mi1-load", _mi_injection(), instance_count=2,
        )
        for i, r in enumerate(results):
            assert r.load_result is not None, f"Instance {i}: load_result is None"
            assert r.load_result.executed_requests >= 200, (
                f"Instance {i}: only {r.load_result.executed_requests} requests"
            )

    @pytest.mark.asyncio
    async def test_each_instance_has_metrics_delta(self):
        """AC6: Each result has a non-None metrics_delta."""
        runner = ScenarioRunner()
        results = await runner.run_multi_instance_scenario(
            "mi1-delta", _mi_injection(), instance_count=2,
        )
        for i, r in enumerate(results):
            assert r.metrics_delta is not None, f"Instance {i}: metrics_delta is None"

    @pytest.mark.asyncio
    async def test_actual_failure_rate_in_band(self):
        """AC2: actual_failure_rate ∈ [0.25, 0.55] for 40% injection (binomial band)."""
        runner = ScenarioRunner()
        results = await runner.run_multi_instance_scenario(
            "mi1-rate", _mi_injection(failure_rate=0.40), instance_count=2,
        )
        for i, r in enumerate(results):
            er = r.load_result.error_rate
            assert 0.25 <= er <= 0.55, (
                f"Instance {i}: error_rate={er:.4f}, expected ∈ [0.25, 0.55]"
            )

    @pytest.mark.asyncio
    async def test_registry_isolation(self):
        """AC1: Each instance uses a separate registry (different object ids)."""
        runner = ScenarioRunner()
        inj = _mi_injection()
        # Run and verify registries are different by checking scenario_ids
        results = await runner.run_multi_instance_scenario(
            "mi1-iso", inj, instance_count=2,
        )
        assert results[0].scenario_id != results[1].scenario_id, (
            "Instance scenario_ids should differ"
        )
        # Both should have independent load results
        assert results[0].load_result is not results[1].load_result

    @pytest.mark.asyncio
    async def test_gnk1_summary_schema(self):
        """GNK-1: Each instance summary has required keys."""
        runner = ScenarioRunner()
        results = await runner.run_multi_instance_scenario(
            "mi1-gnk1", _mi_injection(), instance_count=2,
        )
        for i, r in enumerate(results):
            s = r.summary()
            assert _SUMMARY_REQUIRED_KEYS.issubset(s.keys()), (
                f"Instance {i}: missing keys {_SUMMARY_REQUIRED_KEYS - set(s.keys())}"
            )

    @pytest.mark.asyncio
    async def test_invariants_hold(self):
        """invariant_ok + invariant_check for each instance."""
        runner = ScenarioRunner()
        results = await runner.run_multi_instance_scenario(
            "mi1-inv", _mi_injection(), instance_count=2,
        )
        for i, r in enumerate(results):
            assert r.load_result.invariant_check(), (
                f"Instance {i}: LoadResult invariant broken"
            )
            assert r.metrics_delta.invariant_ok, (
                f"Instance {i}: MetricDelta invariant_ok=False"
            )

    @pytest.mark.asyncio
    async def test_determinism(self):
        """GNK-2: Same seed → same counts across two runs."""
        inj = _mi_injection(failure_rate=0.40, seed=4242)
        r1 = await ScenarioRunner().run_multi_instance_scenario("mi1-det-a", inj, 2)
        r2 = await ScenarioRunner().run_multi_instance_scenario("mi1-det-b", inj, 2)
        for i in range(2):
            assert r1[i].load_result.successful_requests == r2[i].load_result.successful_requests
            assert r1[i].load_result.failed_requests == r2[i].load_result.failed_requests


# ═══════════════════════════════════════════════════════════════════════════
# Task 6.1: TestMI2 — Divergence threshold & TuningRecommendation (AC3/AC4/AC5)
# ═══════════════════════════════════════════════════════════════════════════

class TestMI2DivergenceThreshold:
    """
    R5 AC3/AC4/AC5: Divergence formula, clock skew compensation,
    and bidirectional TuningRecommendation semantics.
    """

    # ── AC3: Formula correctness ─────────────────────────────────────────

    def test_compensated_divergence_formula_basic(self):
        """Known inputs → known output."""
        # |1000 - 1200| = 200, - 50 skew = 150
        assert _obs_compensated_divergence_ms(1000, 1200, 50) == 150

    def test_compensated_divergence_skew_absorbs_small_diff(self):
        """When |t1-t2| <= skew → compensated = 0."""
        assert _obs_compensated_divergence_ms(1000, 1030, 50) == 0

    def test_compensated_divergence_symmetric(self):
        """Order of t1, t2 doesn't matter."""
        assert _obs_compensated_divergence_ms(500, 1000, 50) == \
               _obs_compensated_divergence_ms(1000, 500, 50)

    # ── AC4: Clock skew compensation applied ─────────────────────────────

    def test_raw_vs_compensated(self):
        """Compensated is always <= raw divergence."""
        t1, t2, skew = 1000, 1300, 100
        raw = abs(t1 - t2)
        comp = _obs_compensated_divergence_ms(t1, t2, skew)
        assert comp <= raw
        assert comp == 200  # 300 - 100

    # ── AC5: Bidirectional threshold semantics ───────────────────────────

    def test_threshold_exceeded_produces_recommendation(self):
        """
        compensated_divergence > cb_open_duration × 2 → TuningRecommendation.
        Use small cb_open_duration (0.1s) so threshold = 200ms.
        t1=0, t2=400 → raw=400, comp=350 (skew=50) > 200 → recommendation.
        """
        rec = evaluate_divergence(
            t1_ms=0, t2_ms=400,
            cb_open_duration_seconds=0.1,
            max_clock_skew_ms=50,
        )
        assert rec is not None, "Should produce recommendation when threshold exceeded"
        assert isinstance(rec, TuningRecommendation)
        assert rec.kind == "cb_open_duration"
        assert rec.details["compensated_divergence_ms"] == 350
        assert rec.details["threshold_ms"] == 200

    def test_threshold_not_exceeded_no_recommendation(self):
        """
        compensated_divergence <= cb_open_duration × 2 → None.
        t1=0, t2=100 → raw=100, comp=50 (skew=50) <= 200 → no recommendation.
        """
        rec = evaluate_divergence(
            t1_ms=0, t2_ms=100,
            cb_open_duration_seconds=0.1,
            max_clock_skew_ms=50,
        )
        assert rec is None, "Should NOT produce recommendation when threshold not exceeded"

    def test_exactly_at_threshold_no_recommendation(self):
        """
        compensated_divergence == threshold → no recommendation (not strictly greater).
        threshold = 0.1 × 2 × 1000 = 200ms.
        t1=0, t2=250 → comp = 200 (skew=50) == 200 → no recommendation.
        """
        rec = evaluate_divergence(
            t1_ms=0, t2_ms=250,
            cb_open_duration_seconds=0.1,
            max_clock_skew_ms=50,
        )
        assert rec is None, "At exact threshold, should NOT produce recommendation"

    def test_bidirectional_fail_semantics(self):
        """
        Explicit bidirectional test: both exceeded and not-exceeded in one test.
        """
        # Exceeded: comp=950 > threshold=200
        rec_yes = evaluate_divergence(0, 1000, 0.1, 50)
        assert rec_yes is not None

        # Not exceeded: comp=0 <= threshold=200
        rec_no = evaluate_divergence(0, 30, 0.1, 50)
        assert rec_no is None

    # ── AC5 integration: real CB drive_until_open ────────────────────────

    def test_drive_until_open_records_timestamp(self):
        """AC2: drive_until_open returns a valid monotonic timestamp."""
        dep = "test_dep"
        reg = create_isolated_registry()
        t_opened = drive_until_open(reg, dep)
        assert t_opened > 0, "Transition timestamp should be positive"
        # Verify CB is actually OPEN
        snap = read_cb_state(reg, dep)
        assert is_open(snap), f"CB should be OPEN after drive_until_open, got {snap.state}"

    def test_two_instances_divergence_with_real_timestamps(self):
        """
        AC2+AC3: Two instances driven to OPEN, timestamps recorded,
        divergence computed. Same process → low divergence.
        """
        dep = "external_api"
        reg1 = create_isolated_registry()
        reg2 = create_isolated_registry()

        t1 = drive_until_open(reg1, dep)
        t2 = drive_until_open(reg2, dep)

        div = _obs_compensated_divergence_ms(t1, t2, 50)
        # Same process, near-instant → compensated divergence should be small
        assert div <= 5000, f"Divergence too high: {div}ms"
        # Both timestamps are valid
        assert t1 > 0
        assert t2 > 0

    def test_divergence_with_small_duration_triggers_recommendation(self):
        """
        Integration: real CB timestamps + small cb_open_duration → recommendation.
        Use artificial delay between drives to create measurable divergence.
        """
        dep = "test_dep"
        reg1 = create_isolated_registry()
        reg2 = create_isolated_registry()

        t1 = drive_until_open(reg1, dep)
        # Artificial delay to create divergence
        import time as _time
        _time.sleep(0.25)  # 250ms delay
        t2 = drive_until_open(reg2, dep)

        # With cb_open_duration=0.05s → threshold=100ms
        # Divergence ≈ 250ms, comp ≈ 200ms (skew=50) → clearly exceeds threshold
        rec = evaluate_divergence(t1, t2, cb_open_duration_seconds=0.05, max_clock_skew_ms=50)
        comp = _obs_compensated_divergence_ms(t1, t2, 50)
        assert rec is not None, (
            f"Expected recommendation: t1={t1}, t2={t2}, "
            f"comp={comp}ms, threshold=100ms (comp must be > 100)"
        )
