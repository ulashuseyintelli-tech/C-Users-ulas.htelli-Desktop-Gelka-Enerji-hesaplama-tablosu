"""
PR-6: Canary Comparator tests.

- PROMOTE when canary within thresholds
- ABORT on error rate delta exceeded
- ABORT on latency multiplier exceeded
- HOLD on insufficient samples
- PBT: canary abort deterministic with same inputs
- PBT: HOLD always when samples < min_samples
"""
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.testing.slo_evaluator import (
    CanaryComparator,
    CanaryDecision,
    CanaryThresholds,
    MetricSample,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _samples(
    count: int,
    total_req: int = 1000,
    success_req: int = 1000,
    p99: float = 0.5,
    start_ms: int = 0,
    interval_ms: int = 60_000,
) -> list[MetricSample]:
    return [
        MetricSample(
            timestamp_ms=start_ms + i * interval_ms,
            total_requests=total_req,
            successful_requests=success_req,
            latency_p99_seconds=p99,
        )
        for i in range(count)
    ]


THRESHOLDS = CanaryThresholds(
    max_error_rate_delta=0.01,
    max_latency_multiplier=1.5,
    min_samples=10,
)


# ---------------------------------------------------------------------------
# PROMOTE
# ---------------------------------------------------------------------------

class TestCanaryPromote:
    def test_promote_when_canary_matches_baseline(self):
        baseline = _samples(15, total_req=1000, success_req=999, p99=0.5)
        canary = _samples(15, total_req=1000, success_req=999, p99=0.5)
        result = CanaryComparator(THRESHOLDS).compare(baseline, canary)
        assert result.decision == CanaryDecision.PROMOTE

    def test_promote_when_canary_slightly_better(self):
        baseline = _samples(15, total_req=1000, success_req=990, p99=0.8)
        canary = _samples(15, total_req=1000, success_req=995, p99=0.6)
        result = CanaryComparator(THRESHOLDS).compare(baseline, canary)
        assert result.decision == CanaryDecision.PROMOTE


# ---------------------------------------------------------------------------
# ABORT — error rate delta
# ---------------------------------------------------------------------------

class TestCanaryAbortErrorRate:
    def test_abort_when_canary_error_rate_exceeds_delta(self):
        baseline = _samples(15, total_req=1000, success_req=999, p99=0.5)
        # canary: 2% error rate vs baseline ~0.1% → delta > 0.01
        canary = _samples(15, total_req=1000, success_req=980, p99=0.5)
        result = CanaryComparator(THRESHOLDS).compare(baseline, canary)
        assert result.decision == CanaryDecision.ABORT
        assert "error rate" in result.reason.lower()


# ---------------------------------------------------------------------------
# ABORT — latency multiplier
# ---------------------------------------------------------------------------

class TestCanaryAbortLatency:
    def test_abort_when_canary_latency_exceeds_multiplier(self):
        baseline = _samples(15, total_req=1000, success_req=999, p99=1.0)
        # canary p99 = 2.0 > 1.0 * 1.5 → abort
        canary = _samples(15, total_req=1000, success_req=999, p99=2.0)
        result = CanaryComparator(THRESHOLDS).compare(baseline, canary)
        assert result.decision == CanaryDecision.ABORT
        assert "p99" in result.reason.lower()


# ---------------------------------------------------------------------------
# HOLD — insufficient samples
# ---------------------------------------------------------------------------

class TestCanaryHold:
    def test_hold_when_baseline_insufficient(self):
        baseline = _samples(5)  # below min_samples=10
        canary = _samples(15)
        result = CanaryComparator(THRESHOLDS).compare(baseline, canary)
        assert result.decision == CanaryDecision.HOLD

    def test_hold_when_canary_insufficient(self):
        baseline = _samples(15)
        canary = _samples(3)
        result = CanaryComparator(THRESHOLDS).compare(baseline, canary)
        assert result.decision == CanaryDecision.HOLD

    def test_hold_when_both_insufficient(self):
        result = CanaryComparator(THRESHOLDS).compare(_samples(2), _samples(4))
        assert result.decision == CanaryDecision.HOLD


# ---------------------------------------------------------------------------
# PBT: canary abort deterministic with same inputs
# ---------------------------------------------------------------------------

class TestPbtCanaryDeterminism:
    @given(
        b_success=st.integers(min_value=900, max_value=1000),
        c_success=st.integers(min_value=900, max_value=1000),
        b_p99=st.floats(min_value=0.1, max_value=5.0, allow_nan=False),
        c_p99=st.floats(min_value=0.1, max_value=5.0, allow_nan=False),
    )
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_same_inputs_same_decision(self, b_success, c_success, b_p99, c_p99):
        baseline = _samples(15, total_req=1000, success_req=b_success, p99=b_p99)
        canary = _samples(15, total_req=1000, success_req=c_success, p99=c_p99)
        comp = CanaryComparator(THRESHOLDS)
        r1 = comp.compare(baseline, canary)
        r2 = comp.compare(baseline, canary)
        assert r1.decision == r2.decision
        assert r1.reason == r2.reason


# ---------------------------------------------------------------------------
# PBT: HOLD always when samples < min_samples
# ---------------------------------------------------------------------------

class TestPbtCanaryHoldInvariant:
    @given(
        b_count=st.integers(min_value=0, max_value=9),
        c_count=st.integers(min_value=0, max_value=9),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_hold_when_either_below_min(self, b_count, c_count):
        """If either side has < min_samples, decision is always HOLD."""
        baseline = _samples(b_count)
        canary = _samples(c_count)
        result = CanaryComparator(THRESHOLDS).compare(baseline, canary)
        assert result.decision == CanaryDecision.HOLD
