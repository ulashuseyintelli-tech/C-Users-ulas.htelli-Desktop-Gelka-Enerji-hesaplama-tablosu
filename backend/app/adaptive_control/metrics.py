"""
Adaptive Control Metrics — minimum viable observability (MVP Core).

Task 9.2a: 5 core metrics for production day-1 forensics.
Requirements: 11.1, 11.2, 11.3, 8.5

No external dependencies (no prometheus_client). In-memory storage
consistent with the rest of the codebase.
"""

from __future__ import annotations

from typing import Any

# ── Closed label sets (cardinality control, v1) ──

VALID_OUTCOMES = frozenset({"PASS", "HOLD", "NOOP"})

VALID_DECISION_REASONS = frozenset({
    "budget_exhausted",
    "latency_exceeded",
    "queue_depth_exceeded",
    "backpressure_active",
    "telemetry_insufficient",
    "killswitch_active",
    "disabled",
    "normal",
})

VALID_TELEMETRY_REASONS = frozenset({
    "MIN_SAMPLES",
    "BUCKET_COVERAGE",
    "SOURCE_STALE",
})

# ── In-memory metric stores ──

_decisions_total: dict[tuple[str, str], int] = {}  # (outcome, reason) → count
_enabled: int = 0  # gauge 0/1, default disabled
_backpressure_active: int = 0  # gauge 0/1
_telemetry_insufficient_total: dict[str, int] = {}  # reason → count
_retry_after_seconds: float = 0.0  # gauge


# ── Public API ──

def record_decision(outcome: str, reason: str) -> None:
    """Increment adaptive_control_decisions_total{outcome, reason}."""
    if outcome not in VALID_OUTCOMES:
        raise ValueError(
            f"Invalid outcome '{outcome}'. Must be one of {sorted(VALID_OUTCOMES)}"
        )
    if reason not in VALID_DECISION_REASONS:
        raise ValueError(
            f"Invalid reason '{reason}'. Must be one of {sorted(VALID_DECISION_REASONS)}"
        )
    key = (outcome, reason)
    _decisions_total[key] = _decisions_total.get(key, 0) + 1


def set_enabled(enabled: bool) -> None:
    """Set adaptive_control_enabled gauge (0 or 1)."""
    global _enabled
    _enabled = 1 if enabled else 0


def set_backpressure_active(active: bool) -> None:
    """Set adaptive_control_backpressure_active gauge (0 or 1)."""
    global _backpressure_active
    _backpressure_active = 1 if active else 0


def record_telemetry_insufficient(reason: str) -> None:
    """Increment adaptive_control_telemetry_insufficient_total{reason}."""
    if reason not in VALID_TELEMETRY_REASONS:
        raise ValueError(
            f"Invalid telemetry reason '{reason}'. "
            f"Must be one of {sorted(VALID_TELEMETRY_REASONS)}"
        )
    _telemetry_insufficient_total[reason] = (
        _telemetry_insufficient_total.get(reason, 0) + 1
    )


def set_retry_after_seconds(seconds: float) -> None:
    """Set adaptive_control_retry_after_seconds gauge."""
    global _retry_after_seconds
    _retry_after_seconds = seconds


def get_metrics() -> dict[str, Any]:
    """Return current metric values (for testing/export)."""
    return {
        "adaptive_control_decisions_total": dict(_decisions_total),
        "adaptive_control_enabled": _enabled,
        "adaptive_control_backpressure_active": _backpressure_active,
        "adaptive_control_telemetry_insufficient_total": dict(
            _telemetry_insufficient_total
        ),
        "adaptive_control_retry_after_seconds": _retry_after_seconds,
    }


def reset_metrics() -> None:
    """Reset all metrics to initial state (for testing)."""
    global _enabled, _backpressure_active, _retry_after_seconds
    _decisions_total.clear()
    _enabled = 0
    _backpressure_active = 0
    _telemetry_insufficient_total.clear()
    _retry_after_seconds = 0.0
