"""
Circuit Breaker — dependency-scoped, 3-state FSM.

States: CLOSED → OPEN → HALF_OPEN → CLOSED
  - CLOSED: all requests pass; failures tracked in rolling window
  - OPEN: requests denied (503 + CIRCUIT_OPEN); auto-transitions to
    HALF_OPEN after cb_open_duration_seconds
  - HALF_OPEN: up to cb_half_open_max_requests probes allowed;
    any failure → OPEN, all success → CLOSED

Design decisions:
  - Per-dependency (not per-endpoint): HD-5 bounded enum
  - Rolling window with monotonic clock
  - min_samples guard: threshold only applies when window has ≥ cb_min_samples events
  - Fail-open on internal error (CB bug should not block traffic)
  - 429 (rate-limited) responses are NOT counted as failures
  - Thread safety via threading.Lock for half-open probe counting

Metric: ptf_admin_circuit_breaker_state{dependency} gauge updated on every transition.

Feature: ops-guard, Task 6.1
"""

import logging
import threading
import time
from collections import deque
from enum import Enum
from typing import Optional

from ..guard_config import GuardConfig, GuardDenyReason
from ..ptf_metrics import PTFMetrics

logger = logging.getLogger(__name__)


class CircuitBreakerState(int, Enum):
    """Circuit breaker states — values match gauge encoding."""
    CLOSED = 0
    HALF_OPEN = 1
    OPEN = 2


# HD-5: Fixed dependency enum — no other values allowed
class Dependency(str, Enum):
    DB_PRIMARY = "db_primary"
    DB_REPLICA = "db_replica"
    CACHE = "cache"
    EXTERNAL_API = "external_api"
    IMPORT_WORKER = "import_worker"


class CircuitBreaker:
    """
    Per-dependency circuit breaker with rolling window failure tracking.

    Usage:
        cb = CircuitBreaker("db_primary", config, metrics)
        if not cb.allow_request():
            return 503
        try:
            result = call_dependency()
            cb.record_success()
        except Exception:
            cb.record_failure()
    """

    def __init__(self, name: str, config: GuardConfig, metrics: PTFMetrics) -> None:
        self._name = name
        self._config = config
        self._metrics = metrics
        self._lock = threading.Lock()

        # FSM state
        self._state = CircuitBreakerState.CLOSED
        self._opened_at: float = 0.0  # monotonic time when OPEN entered
        self._half_open_probes: int = 0  # probes allowed in HALF_OPEN
        self._half_open_successes: int = 0  # successful probes in HALF_OPEN

        # Rolling window: deque of (monotonic_time, is_failure)
        self._events: deque[tuple[float, bool]] = deque()

        # Emit initial gauge
        self._emit_gauge()

    @property
    def state(self) -> CircuitBreakerState:
        """Current state, with automatic OPEN → HALF_OPEN transition check."""
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    @property
    def name(self) -> str:
        return self._name

    # ── Public API ────────────────────────────────────────────────────────

    def allow_request(self) -> bool:
        """
        Check if a request should be allowed through.

        Returns True if allowed, False if circuit is open.
        Fail-open on internal error.
        """
        try:
            with self._lock:
                self._maybe_transition_to_half_open()

                if self._state == CircuitBreakerState.CLOSED:
                    return True

                if self._state == CircuitBreakerState.OPEN:
                    return False

                # HALF_OPEN: allow up to max probes
                if self._half_open_probes < self._config.cb_half_open_max_requests:
                    self._half_open_probes += 1
                    return True
                return False

        except Exception as exc:
            # Fail-open: CB internal error should not block traffic
            logger.error(f"[CIRCUIT-BREAKER] {self._name} internal error in allow_request: {exc}")
            return True

    def record_success(self) -> None:
        """Record a successful dependency call."""
        try:
            with self._lock:
                now = time.monotonic()
                self._events.append((now, False))
                self._prune_window(now)

                if self._state == CircuitBreakerState.HALF_OPEN:
                    self._half_open_successes += 1
                    if self._half_open_successes >= self._config.cb_half_open_max_requests:
                        self._transition_to(CircuitBreakerState.CLOSED)
                        self._events.clear()  # fresh start after recovery

        except Exception as exc:
            logger.error(f"[CIRCUIT-BREAKER] {self._name} internal error in record_success: {exc}")

    def record_failure(self) -> None:
        """Record a failed dependency call."""
        try:
            with self._lock:
                now = time.monotonic()
                self._events.append((now, True))
                self._prune_window(now)

                if self._state == CircuitBreakerState.HALF_OPEN:
                    # Any failure in half-open → back to OPEN
                    self._transition_to(CircuitBreakerState.OPEN)
                    self._opened_at = now
                    return

                if self._state == CircuitBreakerState.CLOSED:
                    self._check_threshold()

        except Exception as exc:
            logger.error(f"[CIRCUIT-BREAKER] {self._name} internal error in record_failure: {exc}")

    # ── Snapshot (for admin API / debugging) ──────────────────────────────

    def snapshot(self) -> dict:
        """Return current state as serializable dict."""
        with self._lock:
            self._maybe_transition_to_half_open()
            now = time.monotonic()
            self._prune_window(now)
            total = len(self._events)
            failures = sum(1 for _, is_fail in self._events if is_fail)
            return {
                "name": self._name,
                "state": self._state.name.lower(),
                "state_value": self._state.value,
                "failure_count": failures,
                "total_count": total,
                "failure_pct": round(100 * failures / total, 1) if total > 0 else 0.0,
                "half_open_probes": self._half_open_probes,
                "half_open_successes": self._half_open_successes,
            }

    # ── Internal helpers ──────────────────────────────────────────────────

    def _prune_window(self, now: float) -> None:
        """Remove events outside the rolling window. Must hold lock."""
        cutoff = now - self._config.cb_window_seconds
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def _check_threshold(self) -> None:
        """Check if failure rate exceeds threshold. Must hold lock."""
        total = len(self._events)
        if total < self._config.cb_min_samples:
            return  # not enough data to judge

        failures = sum(1 for _, is_fail in self._events if is_fail)
        fail_pct = 100.0 * failures / total

        if fail_pct >= self._config.cb_error_threshold_pct:
            self._transition_to(CircuitBreakerState.OPEN)
            self._opened_at = time.monotonic()

    def _maybe_transition_to_half_open(self) -> None:
        """Auto-transition OPEN → HALF_OPEN if duration elapsed. Must hold lock."""
        if self._state != CircuitBreakerState.OPEN:
            return
        now = time.monotonic()
        if (now - self._opened_at) >= self._config.cb_open_duration_seconds:
            self._transition_to(CircuitBreakerState.HALF_OPEN)
            self._half_open_probes = 0
            self._half_open_successes = 0

    def _transition_to(self, new_state: CircuitBreakerState) -> None:
        """Transition to new state + emit gauge + log. Must hold lock."""
        old_state = self._state
        self._state = new_state
        self._emit_gauge()
        logger.info(
            f"[CIRCUIT-BREAKER] {self._name}: {old_state.name} → {new_state.name}"
        )

    def _emit_gauge(self) -> None:
        """Update Prometheus gauge for this dependency."""
        try:
            self._metrics.set_circuit_breaker_state(self._name, self._state.value)
        except Exception:
            pass  # metric emission failure is non-fatal

    # ── Test utilities ────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset to CLOSED with empty window. Test only."""
        with self._lock:
            self._state = CircuitBreakerState.CLOSED
            self._events.clear()
            self._opened_at = 0.0
            self._half_open_probes = 0
            self._half_open_successes = 0
            self._emit_gauge()


# ── Circuit Breaker Registry ──────────────────────────────────────────────────

class CircuitBreakerRegistry:
    """
    Manages per-dependency circuit breakers.

    Ensures each dependency has exactly one CircuitBreaker instance.
    """

    def __init__(self, config: GuardConfig, metrics: PTFMetrics) -> None:
        self._config = config
        self._metrics = metrics
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, dependency: str) -> CircuitBreaker:
        """Get or create circuit breaker for dependency."""
        with self._lock:
            if dependency not in self._breakers:
                self._breakers[dependency] = CircuitBreaker(
                    name=dependency,
                    config=self._config,
                    metrics=self._metrics,
                )
            return self._breakers[dependency]

    def get_all_snapshots(self) -> dict[str, dict]:
        """Return snapshots of all circuit breakers."""
        with self._lock:
            return {name: cb.snapshot() for name, cb in self._breakers.items()}

    def reset_all(self) -> None:
        """Reset all circuit breakers. Test only."""
        with self._lock:
            for cb in self._breakers.values():
                cb.reset()
