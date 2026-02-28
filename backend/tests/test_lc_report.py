"""
Task 10.1: StressReport unit tests — R8 AC1-AC5, R9 AC3-AC4.

Pure deterministic tests — no async, no I/O, no ScenarioRunner.
Uses synthetic ScenarioResult-like objects to test report generation.
"""
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from backend.app.testing.stress_report import (
    FailDiagnostic,
    FlakyCorrelationSegment,
    MetricsRow,
    StressReport,
    StressReportConfig,
    TuningRecommendation,
    build_flaky_segment,
    build_stress_report,
    compute_write_path_safe,
    generate_metrics_table,
    generate_recommendations,
)


# ── Synthetic test doubles (no real ScenarioRunner needed) ───────────────

@dataclass
class _FakeLoadResult:
    executed_requests: int = 200
    successful_requests: int = 180
    failed_requests: int = 20
    p95_seconds: float = 0.050
    circuit_open_count: int = 0
    error_rate: float = 0.10


@dataclass
class _FakeMetricDelta:
    counter_deltas: dict = field(default_factory=dict)
    gauge_values: dict = field(default_factory=dict)
    retry_amplification: float = 0.0
    invariant_ok: bool = True
    diagnostics: list = field(default_factory=list)


@dataclass
class _FakeResult:
    scenario_id: str = "test-scenario"
    cb_opened: bool = False
    load_result: Optional[_FakeLoadResult] = None
    metrics_delta: Optional[_FakeMetricDelta] = None
    metadata: dict = field(default_factory=dict)
    outcomes: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)

    def __post_init__(self):
        if self.load_result is None:
            self.load_result = _FakeLoadResult()
        if self.metrics_delta is None:
            self.metrics_delta = _FakeMetricDelta()


def _make_result(
    scenario_id: str = "s1",
    executed: int = 200,
    p95_s: float = 0.050,
    cb_opened: bool = False,
    retry_amp: float = 0.0,
    retry_count: int = 0,
    failopen: int = 0,
    is_write: bool = False,
) -> _FakeResult:
    """Build a synthetic result with controlled values."""
    retry_key = ("ptf_admin_dependency_retry_total", frozenset())
    failopen_key = ("ptf_admin_guard_failopen_total", frozenset())
    deltas = {}
    if retry_count > 0:
        deltas[retry_key] = float(retry_count)
    if failopen > 0:
        deltas[failopen_key] = float(failopen)

    md = {"is_write": True} if is_write else {}
    return _FakeResult(
        scenario_id=scenario_id,
        cb_opened=cb_opened,
        load_result=_FakeLoadResult(executed_requests=executed, p95_seconds=p95_s),
        metrics_delta=_FakeMetricDelta(
            counter_deltas=deltas,
            retry_amplification=retry_amp,
        ),
        metadata=md,
    )


# ═══════════════════════════════════════════════════════════════════════════
# TestMetricsTable — R8 AC1
# ═══════════════════════════════════════════════════════════════════════════

class TestMetricsTable:
    """R8 AC1: generate_metrics_table produces one row per result."""

    def test_row_count_equals_input_count(self):
        results = [_make_result(f"s{i}") for i in range(5)]
        table = generate_metrics_table(results)
        assert len(table) == 5

    def test_empty_input_empty_table(self):
        table = generate_metrics_table([])
        assert table == []

    def test_single_result_has_all_fields(self):
        r = _make_result("fm1", executed=200, p95_s=0.045, cb_opened=False)
        table = generate_metrics_table([r])
        assert len(table) == 1
        row = table[0]
        assert row.scenario_name == "fm1"
        assert row.total_calls == 200
        assert row.p95_latency_ms == pytest.approx(45.0, abs=0.01)
        assert row.cb_opened == 0
        assert isinstance(row.retry_count, int)
        assert isinstance(row.retry_amplification_factor, float)
        assert isinstance(row.failopen_count, int)

    def test_cb_opened_flag_is_int(self):
        r_open = _make_result("open", cb_opened=True)
        r_closed = _make_result("closed", cb_opened=False)
        table = generate_metrics_table([r_open, r_closed])
        assert table[0].cb_opened == 1
        assert table[1].cb_opened == 0

    def test_retry_count_from_metric_delta(self):
        r = _make_result("retry", retry_count=15)
        table = generate_metrics_table([r])
        assert table[0].retry_count == 15

    def test_failopen_count_from_metric_delta(self):
        r = _make_result("fo", failopen=3)
        table = generate_metrics_table([r])
        assert table[0].failopen_count == 3

    def test_metrics_row_is_frozen(self):
        r = _make_result("frozen")
        table = generate_metrics_table([r])
        with pytest.raises(AttributeError):
            table[0].scenario_name = "mutated"


# ═══════════════════════════════════════════════════════════════════════════
# TestRetryRecommendation — R8 AC3
# ═══════════════════════════════════════════════════════════════════════════

class TestRetryRecommendation:
    """R8 AC3: retry_amplification > threshold → recommendation."""

    def test_above_threshold_produces_recommendation(self):
        r = _make_result("high-retry", retry_amp=2.5)
        recs = generate_recommendations([r])
        retry_recs = [x for x in recs if x.kind == "retry_amplification"]
        assert len(retry_recs) >= 1
        assert retry_recs[0].details["retry_amplification"] == pytest.approx(2.5, abs=0.001)

    def test_below_threshold_no_recommendation(self):
        r = _make_result("low-retry", retry_amp=1.5)
        recs = generate_recommendations([r])
        retry_recs = [x for x in recs if x.kind == "retry_amplification"]
        assert len(retry_recs) == 0

    def test_exactly_at_threshold_no_recommendation(self):
        r = _make_result("exact", retry_amp=2.0)
        recs = generate_recommendations([r])
        retry_recs = [x for x in recs if x.kind == "retry_amplification"]
        assert len(retry_recs) == 0, "At exact threshold, no recommendation (not strictly greater)"

    def test_custom_threshold(self):
        cfg = StressReportConfig(retry_amp_threshold=1.0)
        r = _make_result("custom", retry_amp=1.5)
        recs = generate_recommendations([r], cfg)
        retry_recs = [x for x in recs if x.kind == "retry_amplification"]
        assert len(retry_recs) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# TestAlertThresholdRecommendation — R8 AC4
# ═══════════════════════════════════════════════════════════════════════════

class TestAlertThresholdRecommendation:
    """R8 AC4: p95 > threshold → alert threshold suggestion."""

    def test_high_p95_produces_recommendation(self):
        r = _make_result("slow", p95_s=1.2)  # 1200ms > 800ms default
        recs = generate_recommendations([r])
        alert_recs = [x for x in recs if x.kind == "alert_threshold"]
        assert len(alert_recs) >= 1

    def test_low_p95_no_recommendation(self):
        r = _make_result("fast", p95_s=0.3)  # 300ms < 800ms
        recs = generate_recommendations([r])
        alert_recs = [x for x in recs if x.kind == "alert_threshold"]
        assert len(alert_recs) == 0

    def test_custom_p95_threshold(self):
        cfg = StressReportConfig(p95_alert_threshold_ms=100.0)
        r = _make_result("custom", p95_s=0.15)  # 150ms > 100ms
        recs = generate_recommendations([r], cfg)
        alert_recs = [x for x in recs if x.kind == "alert_threshold"]
        assert len(alert_recs) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# TestWritePathSafety — R8 AC5
# ═══════════════════════════════════════════════════════════════════════════

class TestWritePathSafety:
    """R8 AC5: write_path_safe = True iff all write scenarios have retry_delta == 0."""

    def test_no_write_scenarios_vacuously_safe(self):
        r = _make_result("read-only", is_write=False)
        assert compute_write_path_safe([r]) is True

    def test_write_with_zero_retries_safe(self):
        r = _make_result("write-ok", is_write=True, retry_count=0)
        assert compute_write_path_safe([r]) is True

    def test_write_with_retries_unsafe(self):
        r = _make_result("write-bad", is_write=True, retry_count=3)
        assert compute_write_path_safe([r]) is False

    def test_mixed_write_and_read(self):
        r_write = _make_result("write", is_write=True, retry_count=0)
        r_read = _make_result("read", is_write=False, retry_count=10)
        assert compute_write_path_safe([r_write, r_read]) is True

    def test_one_bad_write_makes_unsafe(self):
        r_ok = _make_result("write-ok", is_write=True, retry_count=0)
        r_bad = _make_result("write-bad", is_write=True, retry_count=1)
        assert compute_write_path_safe([r_ok, r_bad]) is False

    def test_write_classifier_uses_metadata(self):
        """Write-path classifier: metadata.is_write == True."""
        r = _FakeResult(scenario_id="no-meta")
        r.metadata = {}
        assert compute_write_path_safe([r]) is True  # not a write scenario


# ═══════════════════════════════════════════════════════════════════════════
# TestFlakyCorrelation — R9 AC3-AC4
# ═══════════════════════════════════════════════════════════════════════════

class TestFlakyCorrelation:
    """R9 AC3-AC4: Flaky correlation segment builder."""

    def test_above_threshold_produces_segment(self):
        seg = build_flaky_segment(150.0, seed=42, scenario="stress", dependency="db")
        assert seg is not None
        assert isinstance(seg, FlakyCorrelationSegment)
        assert seg.timing_deviation_ms == 150.0
        assert seg.suspected_source == "scheduler"
        assert "seed=42" in seg.repro_steps
        assert "scenario=stress" in seg.repro_steps

    def test_below_threshold_returns_none(self):
        seg = build_flaky_segment(50.0, seed=1, scenario="s", dependency="d")
        assert seg is None

    def test_exactly_at_threshold_returns_none(self):
        seg = build_flaky_segment(100.0, seed=1, scenario="s", dependency="d")
        assert seg is None, "At exact 100ms threshold, no segment (not strictly greater)"

    def test_segment_has_three_required_fields(self):
        seg = build_flaky_segment(200.0, seed=99, scenario="peak", dependency="ext")
        assert seg is not None
        assert hasattr(seg, "timing_deviation_ms")
        assert hasattr(seg, "suspected_source")
        assert hasattr(seg, "repro_steps")

    def test_segment_is_frozen(self):
        seg = build_flaky_segment(200.0, seed=1, scenario="s", dependency="d")
        with pytest.raises(AttributeError):
            seg.timing_deviation_ms = 0.0


# ═══════════════════════════════════════════════════════════════════════════
# TestStressReportJSON — GNK-1 schema + determinism
# ═══════════════════════════════════════════════════════════════════════════

class TestStressReportJSON:
    """JSON serialization: deterministic, complete, valid."""

    def test_to_json_valid(self):
        report = build_stress_report([_make_result("s1")])
        payload = json.loads(report.to_json())
        assert "table" in payload
        assert "recommendations" in payload
        assert "write_path_safe" in payload

    def test_to_json_deterministic(self):
        results = [_make_result("s1"), _make_result("s2")]
        r1 = build_stress_report(results)
        r2 = build_stress_report(results)
        assert r1.to_json() == r2.to_json()

    def test_empty_report(self):
        report = build_stress_report([])
        payload = json.loads(report.to_json())
        assert payload["table"] == []
        assert payload["recommendations"] == []
        assert payload["write_path_safe"] is True
        assert payload["flaky_segment"] is None

    def test_flaky_segment_in_json(self):
        report = build_stress_report(
            [_make_result("s1")],
            timing_deviation_ms=200.0,
            timing_seed=42,
            timing_scenario="stress",
            timing_dependency="db",
        )
        payload = json.loads(report.to_json())
        assert payload["flaky_segment"] is not None
        assert payload["flaky_segment"]["timing_deviation_ms"] == 200.0


# ═══════════════════════════════════════════════════════════════════════════
# TestBuildStressReport — top-level builder
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildStressReport:
    """Integration: build_stress_report assembles all components."""

    def test_full_report_structure(self):
        results = [
            _make_result("fm1", retry_amp=0.5),
            _make_result("fm2", retry_amp=3.0, cb_opened=True),
        ]
        report = build_stress_report(results)
        assert len(report.table) == 2
        assert report.write_path_safe is True
        # fm2 has retry_amp=3.0 > 2.0 → recommendation
        retry_recs = [r for r in report.recommendations if r.kind == "retry_amplification"]
        assert len(retry_recs) >= 1

    def test_report_is_frozen(self):
        report = build_stress_report([_make_result("s1")])
        with pytest.raises(AttributeError):
            report.write_path_safe = False
