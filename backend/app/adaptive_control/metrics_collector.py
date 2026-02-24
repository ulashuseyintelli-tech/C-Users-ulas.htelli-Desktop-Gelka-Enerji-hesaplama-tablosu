"""
Metrics Collector — telemetry ingestion + windowing + source health.

Collects MetricSample data from guard and PDF subsystems,
provides windowed queries and source staleness detection.

Feature: slo-adaptive-control, Task 3.1
Requirements: 1.1, 1.2, 1.3, 1.4
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from backend.app.testing.slo_evaluator import MetricSample

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceHealth:
    """Health status of a single metric source."""
    source_id: str
    last_sample_ms: Optional[int]
    is_stale: bool


class MetricsCollector:
    """
    Collects and stores MetricSample data from multiple sources.
    Provides windowed queries and source staleness detection.
    """

    def __init__(self, stale_threshold_ms: int = 30_000) -> None:
        """
        Args:
            stale_threshold_ms: If a source hasn't produced data within
                this many ms, it's considered stale (Req 1.4).
        """
        self._samples: dict[str, list[MetricSample]] = {}
        self._last_seen: dict[str, int] = {}
        self._stale_threshold_ms = stale_threshold_ms

    def ingest(self, source_id: str, sample: MetricSample) -> None:
        """Add a metric sample from a source. Updates last_seen (Req 1.2, 1.3)."""
        if source_id not in self._samples:
            self._samples[source_id] = []
        self._samples[source_id].append(sample)
        self._last_seen[source_id] = sample.timestamp_ms

    def get_samples(
        self,
        source_id: str,
        window_start_ms: int,
        window_end_ms: int,
    ) -> list[MetricSample]:
        """Get samples from a specific source within the time window."""
        source_samples = self._samples.get(source_id, [])
        return [
            s for s in source_samples
            if window_start_ms <= s.timestamp_ms <= window_end_ms
        ]

    def get_all_samples(
        self,
        window_start_ms: int,
        window_end_ms: int,
    ) -> list[MetricSample]:
        """Get samples from all sources within the time window (Req 1.1)."""
        result: list[MetricSample] = []
        for source_samples in self._samples.values():
            result.extend(
                s for s in source_samples
                if window_start_ms <= s.timestamp_ms <= window_end_ms
            )
        return result

    def check_health(self, now_ms: int) -> list[SourceHealth]:
        """Check health of all known sources (Req 1.4).

        A source is stale if it hasn't produced data within stale_threshold_ms.
        """
        results: list[SourceHealth] = []
        for source_id in self._samples:
            last = self._last_seen.get(source_id)
            is_stale = last is None or (now_ms - last) > self._stale_threshold_ms
            results.append(SourceHealth(
                source_id=source_id,
                last_sample_ms=last,
                is_stale=is_stale,
            ))
        return results

    @property
    def source_ids(self) -> list[str]:
        return list(self._samples.keys())

    def clear(self) -> None:
        """Clear all collected data."""
        self._samples.clear()
        self._last_seen.clear()
