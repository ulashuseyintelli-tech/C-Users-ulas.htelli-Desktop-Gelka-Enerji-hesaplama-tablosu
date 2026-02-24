"""
Tests for adaptive control telemetry ingestion + sufficiency checking.

Feature: slo-adaptive-control, Tasks 3.3–3.6
MUST Property: P18 (Telemetry Insufficiency → No-Op + Alert)
Optional Properties: P8 (Metric Collection Round-Trip), P9 (Source Stale Detection)
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.app.adaptive_control.metrics_collector import (
    MetricsCollector,
    SourceHealth,
)
from backend.app.adaptive_control.sufficiency import (
    SufficiencyConfig,
    SufficiencyResult,
    TelemetrySufficiencyChecker,
)
from backend.app.testing.slo_evaluator import MetricSample


# ── Hypothesis Strategies ─────────────────────────────────────────────────────

def make_sample(ts_ms: int) -> MetricSample:
    return MetricSample(
        timestamp_ms=ts_ms,
        total_requests=100,
        successful_requests=99,
        latency_p99_seconds=0.1,
    )


sample_ts_st = st.integers(min_value=1_000_000, max_value=100_000_000)
source_id_st = st.sampled_from(["guard", "pdf", "cache"])


# ══════════════════════════════════════════════════════════════════════════════
# MUST Property P18: Telemetry Insufficiency → No-Op + Alert
# Insufficient data → no control signal, telemetry_insufficient alert.
# Validates: Req 6.3, 6.4
# ══════════════════════════════════════════════════════════════════════════════

class TestTelemetryInsufficiencyPropertyP18:
    """MUST — Property 18: Telemetry Insufficiency → No-Op + Alert."""

    @given(
        sample_count=st.integers(min_value=0, max_value=50),
        min_required=st.integers(min_value=5, max_value=30),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_insufficient_samples_detected(self, sample_count: int, min_required: int):
        """If sample_count < min_required → is_sufficient=False."""
        samples = [make_sample(1000 + i * 1000) for i in range(sample_count)]
        checker = TelemetrySufficiencyChecker(
            SufficiencyConfig(
                min_samples=min_required,
                min_bucket_coverage_pct=0.0,  # disable bucket check
                check_source_stale=False,
            )
        )
        result = checker.check(samples, [])
        if sample_count < min_required:
            assert result.is_sufficient is False
            assert result.reason is not None
            assert "insufficient_samples" in result.reason
        else:
            # Bucket coverage disabled, stale disabled → sufficient
            assert result.is_sufficient is True

    @given(
        stale_sources=st.lists(
            st.sampled_from(["guard", "pdf", "cache"]),
            min_size=0,
            max_size=3,
            unique=True,
        ),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_stale_sources_detected(self, stale_sources: list[str]):
        """Any stale source → is_sufficient=False."""
        samples = [make_sample(1000 + i * 1000) for i in range(30)]
        health = [
            SourceHealth(source_id=sid, last_sample_ms=1000, is_stale=(sid in stale_sources))
            for sid in ["guard", "pdf", "cache"]
        ]
        checker = TelemetrySufficiencyChecker(
            SufficiencyConfig(
                min_samples=1,
                min_bucket_coverage_pct=0.0,
                check_source_stale=True,
            )
        )
        result = checker.check(samples, health)
        if stale_sources:
            assert result.is_sufficient is False
            assert "stale_sources" in result.reason
            for sid in stale_sources:
                assert sid in str(result.stale_sources)
        else:
            assert result.is_sufficient is True

    def test_all_conditions_insufficient(self):
        """All three conditions fail → all reasons reported."""
        checker = TelemetrySufficiencyChecker(
            SufficiencyConfig(min_samples=100, min_bucket_coverage_pct=99.0, check_source_stale=True)
        )
        health = [SourceHealth("guard", last_sample_ms=1, is_stale=True)]
        result = checker.check([make_sample(1000)], health)
        assert result.is_sufficient is False
        assert "insufficient_samples" in result.reason
        assert "stale_sources" in result.reason

    def test_sufficient_data_passes(self):
        """Sufficient data → is_sufficient=True, reason=None."""
        samples = [make_sample(1000 + i * 10000) for i in range(30)]
        health = [SourceHealth("guard", last_sample_ms=300000, is_stale=False)]
        checker = TelemetrySufficiencyChecker(
            SufficiencyConfig(min_samples=5, min_bucket_coverage_pct=0.0, check_source_stale=True)
        )
        result = checker.check(samples, health)
        assert result.is_sufficient is True
        assert result.reason is None


# ══════════════════════════════════════════════════════════════════════════════
# Optional Property P8: Metric Collection Round-Trip
# Validates: Req 1.1, 1.2, 1.3
# ══════════════════════════════════════════════════════════════════════════════

class TestMetricCollectionRoundTripPropertyP8:
    """Optional — Property 8: Metric Collection Round-Trip."""

    @given(
        timestamps=st.lists(sample_ts_st, min_size=1, max_size=20),
        source=source_id_st,
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_ingest_query_roundtrip(self, timestamps: list[int], source: str):
        """All ingested samples are retrievable within the correct window."""
        collector = MetricsCollector()
        samples = [make_sample(ts) for ts in timestamps]
        for s in samples:
            collector.ingest(source, s)

        min_ts = min(timestamps)
        max_ts = max(timestamps)
        retrieved = collector.get_samples(source, min_ts, max_ts)
        assert len(retrieved) == len(samples)
        assert set(s.timestamp_ms for s in retrieved) == set(timestamps)

    @given(
        ts_a=st.lists(sample_ts_st, min_size=1, max_size=5),
        ts_b=st.lists(sample_ts_st, min_size=1, max_size=5),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_get_all_samples_merges_sources(self, ts_a: list[int], ts_b: list[int]):
        """get_all_samples merges from all sources."""
        collector = MetricsCollector()
        for ts in ts_a:
            collector.ingest("guard", make_sample(ts))
        for ts in ts_b:
            collector.ingest("pdf", make_sample(ts))

        all_ts = ts_a + ts_b
        min_ts = min(all_ts)
        max_ts = max(all_ts)
        retrieved = collector.get_all_samples(min_ts, max_ts)
        assert len(retrieved) == len(all_ts)


# ══════════════════════════════════════════════════════════════════════════════
# Optional Property P9: Source Stale Detection
# Validates: Req 1.4
# ══════════════════════════════════════════════════════════════════════════════

class TestSourceStaleDetectionPropertyP9:
    """Optional — Property 9: Source Stale Detection."""

    @given(
        last_sample_offset=st.integers(min_value=0, max_value=120_000),
        stale_threshold=st.integers(min_value=5_000, max_value=60_000),
    )
    @settings(max_examples=100, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
    def test_stale_detection_threshold(self, last_sample_offset: int, stale_threshold: int):
        """Source is stale iff time since last sample > threshold."""
        now_ms = 1_000_000
        collector = MetricsCollector(stale_threshold_ms=stale_threshold)
        sample_ts = now_ms - last_sample_offset
        collector.ingest("guard", make_sample(sample_ts))

        health = collector.check_health(now_ms)
        assert len(health) == 1
        h = health[0]
        expected_stale = last_sample_offset > stale_threshold
        assert h.is_stale == expected_stale, (
            f"offset={last_sample_offset}, threshold={stale_threshold}, "
            f"expected_stale={expected_stale}, got={h.is_stale}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Unit Tests (Task 3.6)
# ══════════════════════════════════════════════════════════════════════════════

class TestMetricsCollectorUnit:
    """Unit tests for MetricsCollector + SufficiencyChecker."""

    def test_all_sources_stale_suspend(self):
        """Edge case: all sources stale → all health entries stale."""
        collector = MetricsCollector(stale_threshold_ms=10_000)
        collector.ingest("guard", make_sample(1000))
        collector.ingest("pdf", make_sample(2000))

        now_ms = 1_000_000  # way past stale threshold
        health = collector.check_health(now_ms)
        assert all(h.is_stale for h in health)

    def test_empty_collector_no_sources(self):
        """Empty collector → no health entries."""
        collector = MetricsCollector()
        assert collector.check_health(1_000_000) == []

    def test_window_filtering(self):
        """Samples outside window are excluded."""
        collector = MetricsCollector()
        collector.ingest("guard", make_sample(1000))
        collector.ingest("guard", make_sample(5000))
        collector.ingest("guard", make_sample(10000))

        result = collector.get_samples("guard", 3000, 7000)
        assert len(result) == 1
        assert result[0].timestamp_ms == 5000

    def test_clear_removes_all(self):
        """clear() removes all data."""
        collector = MetricsCollector()
        collector.ingest("guard", make_sample(1000))
        collector.clear()
        assert collector.source_ids == []
        assert collector.get_all_samples(0, 999999) == []

    def test_sufficiency_result_fields(self):
        """SufficiencyResult has all required fields."""
        checker = TelemetrySufficiencyChecker(SufficiencyConfig(min_samples=5))
        result = checker.check([], [])
        assert hasattr(result, "is_sufficient")
        assert hasattr(result, "sample_count")
        assert hasattr(result, "required_samples")
        assert hasattr(result, "bucket_coverage_pct")
        assert hasattr(result, "stale_sources")
        assert hasattr(result, "reason")
