"""
Tests for Ops-Guard Circuit Breaker.

Covers:
  - FSM transitions: CLOSED→OPEN→HALF_OPEN→CLOSED
  - Rolling window pruning + failure rate calculation
  - min_samples guard (low traffic protection)
  - Half-open probe counting + concurrency guard
  - Fail-open on internal error
  - Metric gauge emission
  - Snapshot API
  - Registry (per-dependency isolation)

Feature: ops-guard, Task 6.2
"""

import time
import threading
from unittest.mock import patch

import pytest
from prometheus_client import CollectorRegistry

from app.guard_config import GuardConfig, GuardDenyReason
from app.ptf_metrics import PTFMetrics
from app.guards.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerState,
    CircuitBreakerRegistry,
    Dependency,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def registry():
    return CollectorRegistry()


@pytest.fixture
def metrics(registry):
    return PTFMetrics(registry=registry)


@pytest.fixture
def config():
    """Config with low thresholds for easy testing."""
    return GuardConfig.model_construct(
        cb_error_threshold_pct=50.0,
        cb_open_duration_seconds=5.0,
        cb_half_open_max_requests=3,
        cb_window_seconds=10.0,
        cb_min_samples=4,
    )


@pytest.fixture
def cb(config, metrics):
    return CircuitBreaker(name="db_primary", config=config, metrics=metrics)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Initial State
# ═══════════════════════════════════════════════════════════════════════════════

class TestInitialState:
    def test_starts_closed(self, cb):
        assert cb.state == CircuitBreakerState.CLOSED

    def test_allows_requests_when_closed(self, cb):
        assert cb.allow_request() is True

    def test_name(self, cb):
        assert cb.name == "db_primary"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CLOSED → OPEN transition
# ═══════════════════════════════════════════════════════════════════════════════

class TestClosedToOpen:
    def test_opens_when_threshold_exceeded(self, cb):
        """4 events, 3 failures (75%) > 50% threshold → OPEN."""
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

    def test_stays_closed_below_threshold(self, cb):
        """4 events, 1 failure (25%) < 50% → stays CLOSED."""
        cb.record_success()
        cb.record_success()
        cb.record_success()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_stays_closed_below_min_samples(self, cb):
        """3 failures but only 3 events < min_samples(4) → stays CLOSED."""
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_exact_threshold_opens(self, cb):
        """4 events, 2 failures (50%) == threshold → OPEN.
        Last event must be a failure to trigger threshold check."""
        cb.record_success()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        # 50% == 50% threshold, last event is failure → opens
        assert cb.state == CircuitBreakerState.OPEN

    def test_denies_when_open(self, cb):
        """OPEN state denies requests."""
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.allow_request() is False


# ═══════════════════════════════════════════════════════════════════════════════
# 3. OPEN → HALF_OPEN transition
# ═══════════════════════════════════════════════════════════════════════════════

class TestOpenToHalfOpen:
    def _force_open(self, cb):
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

    def test_transitions_after_duration(self, cb):
        """After open_duration_seconds, transitions to HALF_OPEN."""
        self._force_open(cb)
        # Simulate time passing
        cb._opened_at -= 6.0  # push back past 5s duration
        assert cb.state == CircuitBreakerState.HALF_OPEN

    def test_stays_open_before_duration(self, cb):
        """Before open_duration_seconds, stays OPEN."""
        self._force_open(cb)
        cb._opened_at -= 3.0  # only 3s, need 5s
        assert cb.state == CircuitBreakerState.OPEN

    def test_allows_probes_in_half_open(self, cb):
        """HALF_OPEN allows up to max probes."""
        self._force_open(cb)
        cb._opened_at -= 6.0
        # Should allow 3 probes
        assert cb.allow_request() is True
        assert cb.allow_request() is True
        assert cb.allow_request() is True
        # 4th should be denied
        assert cb.allow_request() is False


# ═══════════════════════════════════════════════════════════════════════════════
# 4. HALF_OPEN → CLOSED (recovery)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHalfOpenToClosed:
    def _force_half_open(self, cb):
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb._opened_at -= 6.0  # expire open duration

    def test_closes_after_all_probes_succeed(self, cb):
        """3 successful probes in HALF_OPEN → CLOSED."""
        self._force_half_open(cb)
        assert cb.state == CircuitBreakerState.HALF_OPEN

        # Allow 3 probes and record success for each
        cb.allow_request()
        cb.record_success()
        cb.allow_request()
        cb.record_success()
        cb.allow_request()
        cb.record_success()

        assert cb.state == CircuitBreakerState.CLOSED

    def test_allows_requests_after_recovery(self, cb):
        """After recovery to CLOSED, requests are allowed."""
        self._force_half_open(cb)
        for _ in range(3):
            cb.allow_request()
            cb.record_success()
        assert cb.allow_request() is True


# ═══════════════════════════════════════════════════════════════════════════════
# 5. HALF_OPEN → OPEN (probe failure)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHalfOpenToOpen:
    def _force_half_open(self, cb):
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb._opened_at -= 6.0

    def test_reopens_on_probe_failure(self, cb):
        """Any failure during HALF_OPEN → back to OPEN."""
        self._force_half_open(cb)
        assert cb.state == CircuitBreakerState.HALF_OPEN

        cb.allow_request()
        cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN

    def test_reopens_after_success_then_failure(self, cb):
        """Even after some successes, a failure reopens."""
        self._force_half_open(cb)
        cb.allow_request()
        cb.record_success()
        cb.allow_request()
        cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Rolling Window Pruning
# ═══════════════════════════════════════════════════════════════════════════════

class TestRollingWindow:
    def test_old_events_pruned(self, cb):
        """Events outside window are dropped."""
        # Record 4 failures (would normally open)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

        # Reset to closed for next test
        cb.reset()

        # Record failures but push them outside window
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        # Push events outside window
        for i in range(len(cb._events)):
            ts, is_fail = cb._events[i]
            cb._events[i] = (ts - 20.0, is_fail)  # 20s ago, window is 10s

        # New success should not trigger open (old failures pruned)
        cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED

    def test_zero_events_no_crash(self, cb):
        """Empty window doesn't cause division by zero."""
        snap = cb.snapshot()
        assert snap["failure_pct"] == 0.0
        assert snap["total_count"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Fail-Open on Internal Error
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailOpen:
    def test_allow_request_on_internal_error(self, cb):
        """Internal error in allow_request → fail-open (allow)."""
        with patch.object(cb, "_maybe_transition_to_half_open", side_effect=RuntimeError("boom")):
            result = cb.allow_request()
            assert result is True

    def test_record_failure_on_internal_error(self, cb):
        """Internal error in record_failure doesn't crash."""
        with patch.object(cb, "_prune_window", side_effect=RuntimeError("boom")):
            # Should not raise
            cb.record_failure()

    def test_record_success_on_internal_error(self, cb):
        """Internal error in record_success doesn't crash."""
        with patch.object(cb, "_prune_window", side_effect=RuntimeError("boom")):
            cb.record_success()


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Metrics
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetrics:
    def test_initial_gauge_closed(self, cb, metrics):
        """Initial state emits gauge=0 (CLOSED)."""
        val = metrics._circuit_breaker_state.labels(dependency="db_primary")._value.get()
        assert val == 0.0

    def test_gauge_updates_on_open(self, cb, metrics):
        """OPEN state emits gauge=2."""
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        val = metrics._circuit_breaker_state.labels(dependency="db_primary")._value.get()
        assert val == 2.0

    def test_gauge_updates_on_half_open(self, cb, metrics):
        """HALF_OPEN state emits gauge=1."""
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb._opened_at -= 6.0
        _ = cb.state  # trigger transition
        val = metrics._circuit_breaker_state.labels(dependency="db_primary")._value.get()
        assert val == 1.0

    def test_gauge_updates_on_recovery(self, cb, metrics):
        """Recovery to CLOSED emits gauge=0."""
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb._opened_at -= 6.0
        for _ in range(3):
            cb.allow_request()
            cb.record_success()
        val = metrics._circuit_breaker_state.labels(dependency="db_primary")._value.get()
        assert val == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Snapshot
# ═══════════════════════════════════════════════════════════════════════════════

class TestSnapshot:
    def test_snapshot_closed(self, cb):
        snap = cb.snapshot()
        assert snap["state"] == "closed"
        assert snap["state_value"] == 0
        assert snap["name"] == "db_primary"

    def test_snapshot_with_events(self, cb):
        cb.record_success()
        cb.record_failure()
        snap = cb.snapshot()
        assert snap["total_count"] == 2
        assert snap["failure_count"] == 1
        assert snap["failure_pct"] == 50.0

    def test_snapshot_open(self, cb):
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        snap = cb.snapshot()
        assert snap["state"] == "open"


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Registry
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegistry:
    def test_creates_breaker_on_first_get(self, config, metrics):
        reg = CircuitBreakerRegistry(config, metrics)
        cb = reg.get("db_primary")
        assert cb.name == "db_primary"
        assert cb.state == CircuitBreakerState.CLOSED

    def test_returns_same_instance(self, config, metrics):
        reg = CircuitBreakerRegistry(config, metrics)
        cb1 = reg.get("db_primary")
        cb2 = reg.get("db_primary")
        assert cb1 is cb2

    def test_different_dependencies_isolated(self, config, metrics):
        reg = CircuitBreakerRegistry(config, metrics)
        cb_db = reg.get("db_primary")
        cb_cache = reg.get("cache")

        # Open db_primary
        cb_db.record_success()
        cb_db.record_failure()
        cb_db.record_failure()
        cb_db.record_failure()
        assert cb_db.state == CircuitBreakerState.OPEN

        # cache should still be closed
        assert cb_cache.state == CircuitBreakerState.CLOSED

    def test_get_all_snapshots(self, config, metrics):
        reg = CircuitBreakerRegistry(config, metrics)
        reg.get("db_primary")
        reg.get("cache")
        snaps = reg.get_all_snapshots()
        assert "db_primary" in snaps
        assert "cache" in snaps
        assert snaps["db_primary"]["state"] == "closed"

    def test_reset_all(self, config, metrics):
        reg = CircuitBreakerRegistry(config, metrics)
        cb = reg.get("db_primary")
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

        reg.reset_all()
        assert cb.state == CircuitBreakerState.CLOSED


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Reset
# ═══════════════════════════════════════════════════════════════════════════════

class TestReset:
    def test_reset_clears_state(self, cb):
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

        cb.reset()
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.allow_request() is True


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Dependency Enum
# ═══════════════════════════════════════════════════════════════════════════════

class TestDependencyEnum:
    def test_all_values(self):
        assert Dependency.DB_PRIMARY == "db_primary"
        assert Dependency.DB_REPLICA == "db_replica"
        assert Dependency.CACHE == "cache"
        assert Dependency.EXTERNAL_API == "external_api"
        assert Dependency.IMPORT_WORKER == "import_worker"

    def test_bounded_set(self):
        """HD-5: exactly 5 dependency values."""
        assert len(Dependency) == 5
