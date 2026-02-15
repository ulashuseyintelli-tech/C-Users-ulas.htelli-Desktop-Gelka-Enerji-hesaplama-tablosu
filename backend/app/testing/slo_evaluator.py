"""
PR-6: SLO Evaluator + Error Budget + Canary Comparator.

Pure-math, deterministic, FakeClock-compatible.
No real time dependencies — all windowing uses explicit timestamps.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# SLI / SLO definitions
# ---------------------------------------------------------------------------

class SliKind(str, Enum):
    AVAILABILITY = "availability"
    LATENCY_P99 = "latency_p99"
    CORRECTNESS = "correctness"


@dataclass(frozen=True)
class SloTarget:
    """A single SLO target."""
    kind: SliKind
    target: float          # e.g. 0.999 for 99.9% availability
    window_seconds: int    # evaluation window (e.g. 30 days = 2592000)
    description: str = ""


DEFAULT_SLOS: list[SloTarget] = [
    SloTarget(SliKind.AVAILABILITY, 0.999, 2_592_000, "Availability >= 99.9% over 30d"),
    SloTarget(SliKind.LATENCY_P99, 2.0, 2_592_000, "p99 latency <= 2 * eval_interval"),
    SloTarget(SliKind.CORRECTNESS, 0.0, 2_592_000, "false_positive_alert_total == 0"),
]


# ---------------------------------------------------------------------------
# Metric sample (timestamped)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetricSample:
    """A single metric observation at a point in time."""
    timestamp_ms: int
    total_requests: int
    successful_requests: int
    latency_p99_seconds: float
    false_positive_alerts: int = 0


# ---------------------------------------------------------------------------
# SLO Evaluator — windowed, deterministic
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SloEvalResult:
    kind: SliKind
    target: float
    observed: float
    met: bool
    error_budget_remaining: float  # fraction remaining (1.0 = full, 0.0 = exhausted)
    samples_in_window: int


class SloEvaluator:
    """
    Evaluates SLOs over a time window using explicit metric samples.
    No real clock — uses sample timestamps for windowing.
    """

    MIN_SAMPLES = 5  # minimum samples for valid evaluation

    def __init__(self, slos: Optional[list[SloTarget]] = None):
        self._slos = slos or DEFAULT_SLOS

    @property
    def slos(self) -> list[SloTarget]:
        return list(self._slos)

    def evaluate(
        self,
        samples: list[MetricSample],
        window_end_ms: int,
        slo: SloTarget,
    ) -> SloEvalResult:
        """Evaluate a single SLO over the window ending at window_end_ms."""
        window_start_ms = window_end_ms - (slo.window_seconds * 1000)
        in_window = [s for s in samples if window_start_ms <= s.timestamp_ms <= window_end_ms]

        if len(in_window) < self.MIN_SAMPLES:
            # Insufficient data — treat as met with full budget (conservative)
            return SloEvalResult(
                kind=slo.kind, target=slo.target, observed=slo.target,
                met=True, error_budget_remaining=1.0,
                samples_in_window=len(in_window),
            )

        observed = self._compute_sli(slo.kind, in_window)
        met = self._check_met(slo, observed)
        budget = self._compute_budget(slo, observed)

        return SloEvalResult(
            kind=slo.kind, target=slo.target, observed=observed,
            met=met, error_budget_remaining=budget,
            samples_in_window=len(in_window),
        )

    def evaluate_all(
        self,
        samples: list[MetricSample],
        window_end_ms: int,
    ) -> list[SloEvalResult]:
        """Evaluate all configured SLOs."""
        return [self.evaluate(samples, window_end_ms, slo) for slo in self._slos]

    @staticmethod
    def _compute_sli(kind: SliKind, samples: list[MetricSample]) -> float:
        if kind == SliKind.AVAILABILITY:
            total = sum(s.total_requests for s in samples)
            success = sum(s.successful_requests for s in samples)
            return success / total if total > 0 else 1.0
        elif kind == SliKind.LATENCY_P99:
            return max(s.latency_p99_seconds for s in samples)
        elif kind == SliKind.CORRECTNESS:
            return float(sum(s.false_positive_alerts for s in samples))
        return 0.0

    @staticmethod
    def _check_met(slo: SloTarget, observed: float) -> bool:
        if slo.kind == SliKind.AVAILABILITY:
            return observed >= slo.target
        elif slo.kind == SliKind.LATENCY_P99:
            return observed <= slo.target
        elif slo.kind == SliKind.CORRECTNESS:
            return observed <= slo.target
        return False

    @staticmethod
    def _compute_budget(slo: SloTarget, observed: float) -> float:
        if slo.kind == SliKind.AVAILABILITY:
            allowed_error = 1.0 - slo.target  # e.g. 0.001 for 99.9%
            actual_error = 1.0 - observed
            if allowed_error <= 0:
                return 0.0 if actual_error > 0 else 1.0
            remaining = 1.0 - (actual_error / allowed_error)
            return max(0.0, min(1.0, remaining))
        elif slo.kind == SliKind.LATENCY_P99:
            if slo.target <= 0:
                return 0.0
            ratio = observed / slo.target
            return max(0.0, min(1.0, 1.0 - (ratio - 1.0))) if ratio > 1.0 else 1.0
        elif slo.kind == SliKind.CORRECTNESS:
            return 1.0 if observed <= slo.target else 0.0
        return 1.0


# ---------------------------------------------------------------------------
# Canary Comparator — baseline vs canary
# ---------------------------------------------------------------------------

class CanaryDecision(str, Enum):
    PROMOTE = "promote"
    ABORT = "abort"
    HOLD = "hold"  # insufficient data


@dataclass(frozen=True)
class CanaryResult:
    decision: CanaryDecision
    reason: str
    baseline_availability: float
    canary_availability: float
    baseline_p99: float
    canary_p99: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanaryThresholds:
    """Abort thresholds for canary comparison."""
    max_error_rate_delta: float = 0.01    # canary error rate > baseline + delta → abort
    max_latency_multiplier: float = 1.5   # canary p99 > baseline * multiplier → abort
    min_samples: int = 10                 # minimum samples for valid comparison


class CanaryComparator:
    """
    Compares baseline vs canary metric samples.
    Deterministic — no real time, uses explicit samples.
    """

    def __init__(self, thresholds: Optional[CanaryThresholds] = None):
        self._thresholds = thresholds or CanaryThresholds()

    @property
    def thresholds(self) -> CanaryThresholds:
        return self._thresholds

    def compare(
        self,
        baseline_samples: list[MetricSample],
        canary_samples: list[MetricSample],
    ) -> CanaryResult:
        if (len(baseline_samples) < self._thresholds.min_samples
                or len(canary_samples) < self._thresholds.min_samples):
            return CanaryResult(
                decision=CanaryDecision.HOLD,
                reason=f"Insufficient samples: baseline={len(baseline_samples)}, canary={len(canary_samples)}",
                baseline_availability=0.0, canary_availability=0.0,
                baseline_p99=0.0, canary_p99=0.0,
            )

        b_avail = self._availability(baseline_samples)
        c_avail = self._availability(canary_samples)
        b_p99 = self._max_p99(baseline_samples)
        c_p99 = self._max_p99(canary_samples)

        b_error = 1.0 - b_avail
        c_error = 1.0 - c_avail

        # Check abort conditions
        if c_error > b_error + self._thresholds.max_error_rate_delta:
            return CanaryResult(
                decision=CanaryDecision.ABORT,
                reason=f"Canary error rate {c_error:.4f} > baseline {b_error:.4f} + delta {self._thresholds.max_error_rate_delta}",
                baseline_availability=b_avail, canary_availability=c_avail,
                baseline_p99=b_p99, canary_p99=c_p99,
            )

        if b_p99 > 0 and c_p99 > b_p99 * self._thresholds.max_latency_multiplier:
            return CanaryResult(
                decision=CanaryDecision.ABORT,
                reason=f"Canary p99 {c_p99:.3f}s > baseline {b_p99:.3f}s * {self._thresholds.max_latency_multiplier}",
                baseline_availability=b_avail, canary_availability=c_avail,
                baseline_p99=b_p99, canary_p99=c_p99,
            )

        return CanaryResult(
            decision=CanaryDecision.PROMOTE,
            reason="Canary within thresholds",
            baseline_availability=b_avail, canary_availability=c_avail,
            baseline_p99=b_p99, canary_p99=c_p99,
        )

    @staticmethod
    def _availability(samples: list[MetricSample]) -> float:
        total = sum(s.total_requests for s in samples)
        success = sum(s.successful_requests for s in samples)
        return success / total if total > 0 else 1.0

    @staticmethod
    def _max_p99(samples: list[MetricSample]) -> float:
        return max(s.latency_p99_seconds for s in samples) if samples else 0.0
