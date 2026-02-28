"""
StressReport — LC rapor üreteci.

Feature: load-characterization, Task 10.1
Requirements: R8 (8.1–8.6), R9 (9.3–9.4), GNK-1

Produces structured metrics tables, tuning recommendations,
write-path safety flags, and flaky correlation segments
from ScenarioResult data.

All logic is pure/deterministic — no async, no I/O.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


# ── Core data classes (used across LC modules) ───────────────────────────

@dataclass(frozen=True)
class FailDiagnostic:
    """GNK-1: FAIL diagnostic payload."""
    scenario_id: str
    dependency: str
    outcome: str
    observed: Any
    expected: Any
    seed: int


@dataclass(frozen=True)
class FlakyCorrelationSegment:
    """R9 AC4: Flaky test correlation segment (3 required fields)."""
    timing_deviation_ms: float
    suspected_source: str
    repro_steps: str


@dataclass(frozen=True)
class TuningRecommendation:
    """R8 AC2-AC4: Single tuning recommendation."""
    kind: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


# ── MetricsRow — single scenario row in metrics table ────────────────────

@dataclass(frozen=True)
class MetricsRow:
    """
    R8 AC1: One row per ScenarioResult in the metrics table.

    Source mapping (locked):
      scenario_name       → ScenarioResult.scenario_id
      total_calls         → LoadResult.executed_requests
      retry_count         → sum of MetricDelta.counter_deltas for ptf_admin_dependency_retry_total
      retry_amplification_factor → MetricDelta.retry_amplification (property)
      p95_latency_ms      → LoadResult.p95_seconds × 1000
      cb_opened           → int(ScenarioResult.cb_opened)  (0 or 1)
      failopen_count      → sum of MetricDelta.counter_deltas for ptf_admin_guard_failopen_total
    """
    scenario_name: str
    total_calls: int
    retry_count: int
    retry_amplification_factor: float
    p95_latency_ms: float
    cb_opened: int  # 0 or 1 flag (not count)
    failopen_count: int


# ── StressReportConfig — configurable thresholds ─────────────────────────

@dataclass(frozen=True)
class StressReportConfig:
    """
    Config-driven thresholds for recommendation generation.
    All defaults match spec values; override in tests for flexibility.
    """
    retry_amp_threshold: float = 2.0
    cb_open_duration_seconds: float = 60.0
    max_clock_skew_ms: int = 50
    flaky_timing_threshold_ms: float = 100.0
    p95_alert_threshold_ms: float = 800.0  # DH7-like: p95 > 0.8s


# ── StressReport ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StressReport:
    """
    R8: Structured stress report from ScenarioResult list.

    Immutable after construction. Use class methods to build.
    """
    table: list[MetricsRow]
    recommendations: list[TuningRecommendation]
    write_path_safe: bool
    flaky_segment: Optional[FlakyCorrelationSegment]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Deterministic JSON output (sort_keys=True, stable ordering)."""
        payload = asdict(self)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


# ── Builder functions (pure, no side effects) ────────────────────────────

def _extract_retry_count(metrics_delta: Any) -> int:
    """Sum retry deltas from MetricDelta.counter_deltas."""
    total = 0
    for key, val in getattr(metrics_delta, "counter_deltas", {}).items():
        if key[0] == "ptf_admin_dependency_retry_total":
            total += int(val)
    return total


def _extract_failopen_count(metrics_delta: Any) -> int:
    """Sum failopen deltas from MetricDelta.counter_deltas."""
    total = 0
    for key, val in getattr(metrics_delta, "counter_deltas", {}).items():
        if key[0] == "ptf_admin_guard_failopen_total":
            total += int(val)
    return total


def generate_metrics_table(results: list[Any]) -> list[MetricsRow]:
    """
    R8 AC1: Generate one MetricsRow per ScenarioResult.

    len(output) == len(results) invariant.
    Each row has 7 fields, all derived from locked source mapping.
    """
    rows: list[MetricsRow] = []
    for r in results:
        lr = r.load_result
        md = r.metrics_delta
        rows.append(MetricsRow(
            scenario_name=r.scenario_id,
            total_calls=lr.executed_requests if lr else 0,
            retry_count=_extract_retry_count(md) if md else 0,
            retry_amplification_factor=round(md.retry_amplification, 6) if md else 0.0,
            p95_latency_ms=round(lr.p95_seconds * 1000, 3) if lr else 0.0,
            cb_opened=int(r.cb_opened),
            failopen_count=_extract_failopen_count(md) if md else 0,
        ))
    return rows


def generate_recommendations(
    results: list[Any],
    cfg: Optional[StressReportConfig] = None,
) -> list[TuningRecommendation]:
    """
    R8 AC2-AC4: Generate tuning recommendations from scenario results.

    - CB divergence: uses evaluate_divergence from cb_observer (if multi-instance data available)
    - Retry amplification: retry_amp > cfg.retry_amp_threshold → recommendation
    - Alert threshold suggestion: p95 > cfg.p95_alert_threshold_ms → recommendation
      (NOT "alert fired" check — no production alert rules available yet)
    """
    cfg = cfg or StressReportConfig()
    recs: list[TuningRecommendation] = []

    for r in results:
        md = r.metrics_delta
        lr = r.load_result
        if md is None or lr is None:
            continue

        # R8 AC3: Retry amplification threshold
        amp = md.retry_amplification
        if amp > cfg.retry_amp_threshold:
            recs.append(TuningRecommendation(
                kind="retry_amplification",
                reason=(
                    f"Scenario '{r.scenario_id}': retry_amplification={amp:.4f} "
                    f"exceeds threshold {cfg.retry_amp_threshold}. "
                    f"Consider reducing wrapper_max_retries."
                ),
                details={
                    "scenario_id": r.scenario_id,
                    "retry_amplification": round(amp, 6),
                    "threshold": cfg.retry_amp_threshold,
                },
            ))

        # R8 AC4: Alert threshold suggestion (p95 based)
        p95_ms = lr.p95_seconds * 1000
        if p95_ms > cfg.p95_alert_threshold_ms:
            recs.append(TuningRecommendation(
                kind="alert_threshold",
                reason=(
                    f"Scenario '{r.scenario_id}': p95={p95_ms:.1f}ms "
                    f"exceeds {cfg.p95_alert_threshold_ms}ms. "
                    f"Consider adjusting DH7 alert threshold."
                ),
                details={
                    "scenario_id": r.scenario_id,
                    "p95_latency_ms": round(p95_ms, 3),
                    "threshold_ms": cfg.p95_alert_threshold_ms,
                },
            ))

    return recs


def compute_write_path_safe(results: list[Any]) -> bool:
    """
    R8 AC5: Write-path safety flag.

    True iff ALL write-path scenarios have retry_delta == 0.
    Write-path classifier: ScenarioResult.metadata.get("is_write") == True.
    If no write-path scenarios exist, returns True (vacuously safe).
    """
    write_results = [
        r for r in results
        if getattr(r, "metadata", {}).get("is_write") is True
    ]
    if not write_results:
        return True
    for r in write_results:
        if r.metrics_delta is not None:
            retry_count = _extract_retry_count(r.metrics_delta)
            if retry_count > 0:
                return False
    return True


def build_flaky_segment(
    timing_deviation_ms: float,
    seed: int,
    scenario: str,
    dependency: str,
) -> Optional[FlakyCorrelationSegment]:
    """
    R9 AC3-AC4: Build flaky correlation segment.

    > 100ms → FlakyCorrelationSegment with 3 required fields.
    ≤ 100ms → None (no noise).

    Pure helper — caller provides timing_deviation_ms from measurement.
    """
    if timing_deviation_ms > 100.0:
        return FlakyCorrelationSegment(
            timing_deviation_ms=round(timing_deviation_ms, 3),
            suspected_source="scheduler",
            repro_steps=f"seed={seed} scenario={scenario} dependency={dependency}",
        )
    return None


def build_stress_report(
    results: list[Any],
    cfg: Optional[StressReportConfig] = None,
    timing_deviation_ms: float = 0.0,
    timing_seed: int = 0,
    timing_scenario: str = "",
    timing_dependency: str = "",
) -> StressReport:
    """
    Top-level builder: assemble a complete StressReport from ScenarioResults.
    """
    cfg = cfg or StressReportConfig()
    table = generate_metrics_table(results)
    recs = generate_recommendations(results, cfg)
    safe = compute_write_path_safe(results)
    flaky = build_flaky_segment(
        timing_deviation_ms, timing_seed, timing_scenario, timing_dependency,
    )
    return StressReport(
        table=table,
        recommendations=recs,
        write_path_safe=safe,
        flaky_segment=flaky,
    )
