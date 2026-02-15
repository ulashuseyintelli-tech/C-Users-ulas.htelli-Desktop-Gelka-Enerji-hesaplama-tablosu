from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from .lc_config import retry_amp_tolerance


@dataclass(frozen=True)
class MetricSnapshot:
    """
    Minimal metric snapshot used by LC tests.
    In PR-1 we keep it generic (dicts); wiring to real metrics comes later.
    """
    call_total_by_outcome: Mapping[str, float]
    retry_total: float
    # placeholders for later: p95 latency, circuit open rate, etc.


@dataclass(frozen=True)
class MetricDelta:
    call_total_by_outcome: Mapping[str, float]
    retry_total: float

    @property
    def retry_amplification(self) -> float:
        total_calls = sum(self.call_total_by_outcome.values()) if self.call_total_by_outcome else 0.0
        if total_calls <= 0:
            return 0.0
        return float(self.retry_total) / float(total_calls)

    def assert_retry_amp_close(self, expected: float) -> None:
        diff = abs(self.retry_amplification - expected)
        if diff > retry_amp_tolerance(expected):
            raise AssertionError(
                f"retry_amplification mismatch: observed={self.retry_amplification} expected={expected} diff={diff}"
            )


class MetricsCapture:
    """
    PR-1: pure math + isolation container. Does not touch global metrics yet.
    """

    def __init__(self, initial: Optional[MetricSnapshot] = None):
        self._initial = initial or MetricSnapshot(call_total_by_outcome={}, retry_total=0.0)

    def delta(self, current: MetricSnapshot) -> MetricDelta:
        # Minimal delta calculation
        delta_calls = {}
        for k, v in current.call_total_by_outcome.items():
            delta_calls[k] = float(v) - float(self._initial.call_total_by_outcome.get(k, 0.0))
        delta_retry = float(current.retry_total) - float(self._initial.retry_total)
        return MetricDelta(call_total_by_outcome=delta_calls, retry_total=delta_retry)
