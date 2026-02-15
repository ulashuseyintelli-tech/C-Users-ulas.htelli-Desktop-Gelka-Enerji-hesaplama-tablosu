"""
PR-6: SLO Evaluator tests.

- Availability SLO met/not-met
- Latency SLO met/not-met
- Correctness SLO (false_positive == 0)
- Error budget computation (full, partial, exhausted)
- Insufficient samples → HOLD with full budget
- PBT: budget always in [0, 1]
- PBT: availability SLO monotonic with success rate
"""
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.slo_evaluator import (
    SloEvaluator,
    SloTarget,
    SliKind,
    MetricSample,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_samples(
    count: int,
    total_req: int = 1000,
    success_req: int = 999,
    p99: float = 0.5,
    fp_alerts: int = 0,
    start_ms: int = 0,
    interval_ms: int = 60_000,
) -> list[MetricSample]:
    return [
        MetricSample(
            timestamp_ms=start_ms + i * interval_ms,
            total_requests=total_req,
            successful_requests=success_req,
            latency_p99_seconds=p99,
            false_positive_alerts=fp_alerts,
        )
        for i in range(count)
    ]


WINDOW_30D_S = 2_592_000
WINDOW_END_MS = WINDOW_30D_S * 1000  # samples start at 0, window covers all


# ---------------------------------------------------------------------------
# Availability SLO
# ---------------------------------------------------------------------------

class TestAvailabilitySlo:
    SLO = SloTarget(SliKind.AVAILABILITY, 0.999, WINDOW_30D_S)

    def test_met_when_above_target(self):
        samples = _make_samples(10, total_req=1000, success_req=1000)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, self.SLO)
        assert result.met is True
        assert result.observed == 1.0

    def test_not_met_when_below_target(self):
        samples = _make_samples(10, total_req=1000, success_req=990)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, self.SLO)
        assert result.met is False
        assert result.observed == 0.99


# ---------------------------------------------------------------------------
# Latency SLO
# ---------------------------------------------------------------------------

class TestLatencySlo:
    SLO = SloTarget(SliKind.LATENCY_P99, 2.0, WINDOW_30D_S)

    def test_met_when_below_target(self):
        samples = _make_samples(10, p99=1.5)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, self.SLO)
        assert result.met is True

    def test_not_met_when_above_target(self):
        samples = _make_samples(10, p99=3.0)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, self.SLO)
        assert result.met is False
        assert result.observed == 3.0


# ---------------------------------------------------------------------------
# Correctness SLO
# ---------------------------------------------------------------------------

class TestCorrectnessSlo:
    SLO = SloTarget(SliKind.CORRECTNESS, 0.0, WINDOW_30D_S)

    def test_met_when_zero_false_positives(self):
        samples = _make_samples(10, fp_alerts=0)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, self.SLO)
        assert result.met is True
        assert result.observed == 0.0

    def test_not_met_when_false_positives_exist(self):
        samples = _make_samples(10, fp_alerts=1)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, self.SLO)
        assert result.met is False
        assert result.observed == 10.0  # 10 samples × 1 fp each


# ---------------------------------------------------------------------------
# Error budget
# ---------------------------------------------------------------------------

class TestErrorBudget:
    def test_full_budget_when_perfect(self):
        slo = SloTarget(SliKind.AVAILABILITY, 0.999, WINDOW_30D_S)
        samples = _make_samples(10, total_req=1000, success_req=1000)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, slo)
        assert result.error_budget_remaining == 1.0

    def test_partial_budget(self):
        slo = SloTarget(SliKind.AVAILABILITY, 0.999, WINDOW_30D_S)
        # 0.9995 availability → used half the budget (allowed error = 0.001, actual = 0.0005)
        samples = _make_samples(10, total_req=10000, success_req=9995)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, slo)
        assert 0.4 < result.error_budget_remaining < 0.6

    def test_exhausted_budget(self):
        slo = SloTarget(SliKind.AVAILABILITY, 0.999, WINDOW_30D_S)
        samples = _make_samples(10, total_req=1000, success_req=990)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, slo)
        assert result.error_budget_remaining == 0.0

    def test_correctness_budget_full_when_zero_fp(self):
        slo = SloTarget(SliKind.CORRECTNESS, 0.0, WINDOW_30D_S)
        samples = _make_samples(10, fp_alerts=0)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, slo)
        assert result.error_budget_remaining == 1.0

    def test_correctness_budget_exhausted_when_fp(self):
        slo = SloTarget(SliKind.CORRECTNESS, 0.0, WINDOW_30D_S)
        samples = _make_samples(10, fp_alerts=1)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, slo)
        assert result.error_budget_remaining == 0.0


# ---------------------------------------------------------------------------
# Insufficient samples
# ---------------------------------------------------------------------------

class TestInsufficientSamples:
    def test_below_min_samples_returns_met_with_full_budget(self):
        slo = SloTarget(SliKind.AVAILABILITY, 0.999, WINDOW_30D_S)
        samples = _make_samples(3)  # below MIN_SAMPLES=5
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, slo)
        assert result.met is True
        assert result.error_budget_remaining == 1.0
        assert result.samples_in_window == 3

    def test_empty_samples_returns_met(self):
        slo = SloTarget(SliKind.AVAILABILITY, 0.999, WINDOW_30D_S)
        result = SloEvaluator().evaluate([], WINDOW_END_MS, slo)
        assert result.met is True
        assert result.error_budget_remaining == 1.0


# ---------------------------------------------------------------------------
# PBT: budget always in [0, 1]
# ---------------------------------------------------------------------------

class TestPbtBudgetBounds:
    @given(
        success=st.integers(min_value=0, max_value=10000),
        total=st.integers(min_value=1, max_value=10000),
        target=st.floats(min_value=0.5, max_value=1.0, allow_nan=False),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_availability_budget_in_unit_interval(self, success, total, target):
        success = min(success, total)
        slo = SloTarget(SliKind.AVAILABILITY, target, WINDOW_30D_S)
        samples = _make_samples(10, total_req=total, success_req=success)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, slo)
        assert 0.0 <= result.error_budget_remaining <= 1.0

    @given(p99=st.floats(min_value=0.0, max_value=100.0, allow_nan=False))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_latency_budget_in_unit_interval(self, p99):
        slo = SloTarget(SliKind.LATENCY_P99, 2.0, WINDOW_30D_S)
        samples = _make_samples(10, p99=p99)
        result = SloEvaluator().evaluate(samples, WINDOW_END_MS, slo)
        assert 0.0 <= result.error_budget_remaining <= 1.0


# ---------------------------------------------------------------------------
# PBT: availability SLO monotonic with success rate
# ---------------------------------------------------------------------------

class TestPbtAvailabilityMonotonic:
    @given(
        s1=st.integers(min_value=0, max_value=1000),
        s2=st.integers(min_value=0, max_value=1000),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_higher_success_rate_means_higher_or_equal_budget(self, s1, s2):
        """If success_rate_a >= success_rate_b, then budget_a >= budget_b."""
        total = 1000
        s1 = min(s1, total)
        s2 = min(s2, total)
        slo = SloTarget(SliKind.AVAILABILITY, 0.999, WINDOW_30D_S)
        r1 = SloEvaluator().evaluate(_make_samples(10, total, s1), WINDOW_END_MS, slo)
        r2 = SloEvaluator().evaluate(_make_samples(10, total, s2), WINDOW_END_MS, slo)
        if s1 >= s2:
            assert r1.error_budget_remaining >= r2.error_budget_remaining
        else:
            assert r2.error_budget_remaining >= r1.error_budget_remaining
