"""
CB Observer — test-only bridge to read real CircuitBreaker state.

PR-3: Replaces PR-2 heuristic with actual CircuitBreakerRegistry reads.
Provides isolated registry creation for multi-instance simulation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from prometheus_client import CollectorRegistry

from backend.app.guard_config import GuardConfig
from backend.app.ptf_metrics import PTFMetrics
from backend.app.guards.circuit_breaker import (
    CircuitBreakerRegistry,
    CircuitBreakerState,
)


@dataclass(frozen=True)
class CbSnapshot:
    dependency: str
    state: str
    state_value: int
    observed_at_ms: int


def is_open(snapshot: CbSnapshot) -> bool:
    return snapshot.state_value == CircuitBreakerState.OPEN.value


def create_isolated_registry(
    config: Optional[GuardConfig] = None,
) -> CircuitBreakerRegistry:
    """
    Create a fully isolated CB registry for test use.
    Each call gets its own prometheus CollectorRegistry + PTFMetrics + GuardConfig.
    """
    cfg = config or GuardConfig()
    metrics = PTFMetrics(registry=CollectorRegistry())
    return CircuitBreakerRegistry(config=cfg, metrics=metrics)


def read_cb_state(
    registry: CircuitBreakerRegistry,
    dependency: str,
) -> CbSnapshot:
    """Read current CB state for a dependency from a real registry."""
    cb = registry.get(dependency)
    snap = cb.snapshot()
    return CbSnapshot(
        dependency=dependency,
        state=snap["state"],
        state_value=snap["state_value"],
        observed_at_ms=int(time.time() * 1000),
    )


def drive_failures(
    registry: CircuitBreakerRegistry,
    dependency: str,
    count: int,
) -> None:
    """Record N failures on a CB to drive it toward OPEN state."""
    cb = registry.get(dependency)
    for _ in range(count):
        cb.record_failure()


def drive_until_open(
    registry: CircuitBreakerRegistry,
    dependency: str,
    max_attempts: int = 100,
) -> int:
    """
    Drive failures until CB transitions to OPEN.
    Returns monotonic timestamp (ms) of the transition moment.
    Raises RuntimeError if CB doesn't open within max_attempts.
    """
    cb = registry.get(dependency)
    for i in range(max_attempts):
        cb.record_failure()
        snap = cb.snapshot()
        if snap["state_value"] == CircuitBreakerState.OPEN.value:
            return int(time.monotonic() * 1000)
    raise RuntimeError(
        f"CB for {dependency} did not open after {max_attempts} failures"
    )


def drive_successes(
    registry: CircuitBreakerRegistry,
    dependency: str,
    count: int,
) -> None:
    """Record N successes on a CB."""
    cb = registry.get(dependency)
    for _ in range(count):
        cb.record_success()


# ── Divergence analysis (R5 AC3-AC5) ────────────────────────────────────

def compensated_divergence_ms(
    t1_ms: int,
    t2_ms: int,
    max_clock_skew_ms: int = 50,
) -> int:
    """
    R5 AC4: Clock skew compensated divergence.
    compensated = max(0, |t1 - t2| - max_clock_skew)
    """
    raw = abs(t1_ms - t2_ms)
    return max(0, raw - max_clock_skew_ms)


def evaluate_divergence(
    t1_ms: int,
    t2_ms: int,
    cb_open_duration_seconds: float,
    max_clock_skew_ms: int = 50,
) -> Optional["TuningRecommendation"]:
    """
    R5 AC5: Bidirectional threshold evaluation.

    If compensated_divergence > cb_open_duration × 2 (in ms) → TuningRecommendation.
    Otherwise → None.

    Returns:
        TuningRecommendation if threshold exceeded, None otherwise.
    """
    from .stress_report import TuningRecommendation

    comp = compensated_divergence_ms(t1_ms, t2_ms, max_clock_skew_ms)
    threshold_ms = int(cb_open_duration_seconds * 2 * 1000)

    if comp > threshold_ms:
        return TuningRecommendation(
            kind="cb_open_duration",
            reason=(
                f"CB OPEN divergence {comp}ms exceeds threshold "
                f"{threshold_ms}ms (cb_open_duration={cb_open_duration_seconds}s × 2). "
                f"Consider increasing cb_open_duration or synchronizing instance clocks."
            ),
            details={
                "compensated_divergence_ms": comp,
                "threshold_ms": threshold_ms,
                "max_clock_skew_ms": max_clock_skew_ms,
                "cb_open_duration_seconds": cb_open_duration_seconds,
            },
        )
    return None
