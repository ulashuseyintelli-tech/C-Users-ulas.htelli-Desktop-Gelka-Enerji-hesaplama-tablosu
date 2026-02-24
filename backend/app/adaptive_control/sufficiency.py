"""
Telemetry Sufficiency Checker — determines if telemetry data is sufficient
for adaptive control decisions.

Three conditions (Req 6.4):
  (a) min N samples in window
  (b) histogram bucket coverage >= 80%
  (c) no source_stale

If insufficient → no-op + alert only (Req 6.3).

Feature: slo-adaptive-control, Task 3.2
Requirements: 6.3, 6.4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from backend.app.adaptive_control.metrics_collector import SourceHealth
from backend.app.testing.slo_evaluator import MetricSample


@dataclass(frozen=True)
class SufficiencyConfig:
    """Configuration for telemetry sufficiency checks."""
    min_samples: int = 24          # default: window/interval * 0.8
    min_bucket_coverage_pct: float = 80.0
    check_source_stale: bool = True


@dataclass(frozen=True)
class SufficiencyResult:
    """Result of a telemetry sufficiency check."""
    is_sufficient: bool
    sample_count: int
    required_samples: int
    bucket_coverage_pct: float
    stale_sources: list[str]
    reason: Optional[str] = None


class TelemetrySufficiencyChecker:
    """Checks whether telemetry data is sufficient for control decisions."""

    def __init__(self, config: Optional[SufficiencyConfig] = None) -> None:
        self._config = config or SufficiencyConfig()

    @property
    def config(self) -> SufficiencyConfig:
        return self._config

    def check(
        self,
        samples: list[MetricSample],
        source_health: list[SourceHealth],
        total_buckets: int = 10,
    ) -> SufficiencyResult:
        """Check telemetry sufficiency (Req 6.4).

        Args:
            samples: Metric samples in the evaluation window.
            source_health: Health status of all metric sources.
            total_buckets: Total histogram buckets for coverage calc.

        Returns:
            SufficiencyResult with is_sufficient and reason if insufficient.
        """
        stale_sources = [
            sh.source_id for sh in source_health
            if sh.is_stale
        ] if self._config.check_source_stale else []

        sample_count = len(samples)
        required = self._config.min_samples

        # Bucket coverage: count distinct timestamp buckets
        if total_buckets > 0 and samples:
            distinct_buckets = len(set(
                s.timestamp_ms // (total_buckets * 1000) for s in samples
            ))
            bucket_coverage = min(100.0, (distinct_buckets / total_buckets) * 100.0)
        else:
            bucket_coverage = 0.0 if not samples else 100.0

        # Check conditions
        reasons: list[str] = []

        if sample_count < required:
            reasons.append(
                f"insufficient_samples: {sample_count} < {required}"
            )

        if bucket_coverage < self._config.min_bucket_coverage_pct:
            reasons.append(
                f"low_bucket_coverage: {bucket_coverage:.1f}% < {self._config.min_bucket_coverage_pct}%"
            )

        if stale_sources:
            reasons.append(
                f"stale_sources: {stale_sources}"
            )

        is_sufficient = len(reasons) == 0
        reason = "; ".join(reasons) if reasons else None

        return SufficiencyResult(
            is_sufficient=is_sufficient,
            sample_count=sample_count,
            required_samples=required,
            bucket_coverage_pct=bucket_coverage,
            stale_sources=stale_sources,
            reason=reason,
        )
