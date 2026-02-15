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


def drive_successes(
    registry: CircuitBreakerRegistry,
    dependency: str,
    count: int,
) -> None:
    """Record N successes on a CB."""
    cb = registry.get(dependency)
    for _ in range(count):
        cb.record_success()
