"""
Tests for DB Timeout Hook + S1 Circuit Breaker integration.

Property-based tests (Hypothesis):
  - Property 4: DB Timeout Hook Raises TimeoutError
  - Property 5: Circuit Breaker Opens Under Sufficient Failures

Integration test:
  - S1: DB Timeout → Circuit Breaker CLOSED→OPEN

Feature: fault-injection, Tasks 2.4, 5.1, 5.2
Requirements: 2.4, 3.1, 3.2, 3.3, 4.1
"""

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck
from prometheus_client import CollectorRegistry

from app.testing.fault_injection import FaultInjector, InjectionPoint
from app.testing.db_timeout_hook import maybe_inject_db_timeout
from app.guard_config import GuardConfig
from app.ptf_metrics import PTFMetrics
from app.guards.circuit_breaker import CircuitBreaker, CircuitBreakerState


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_injector():
    FaultInjector.reset_instance()
    yield
    FaultInjector.reset_instance()


@pytest.fixture
def prom_registry():
    return CollectorRegistry()


@pytest.fixture
def metrics(prom_registry):
    return PTFMetrics(registry=prom_registry)


# ═══════════════════════════════════════════════════════════════════════════════
# Property 4: DB Timeout Hook Raises TimeoutError
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperty4DBTimeoutHook:
    """Feature: fault-injection, Property 4: DB Timeout Hook Raises TimeoutError"""

    @settings(max_examples=100)
    @given(
        delay=st.floats(min_value=0.0, max_value=0.0),  # no actual sleep in PBT
    )
    def test_raises_timeout_when_enabled(self, delay):
        injector = FaultInjector.get_instance()
        injector.enable(
            InjectionPoint.DB_TIMEOUT,
            params={"delay_seconds": delay},
            ttl_seconds=60.0,
        )
        with pytest.raises(TimeoutError, match="Injected DB timeout"):
            maybe_inject_db_timeout()

    def test_noop_when_disabled(self):
        """Injection disabled → no exception."""
        maybe_inject_db_timeout()  # should not raise

    def test_noop_after_disable(self):
        injector = FaultInjector.get_instance()
        injector.enable(InjectionPoint.DB_TIMEOUT, ttl_seconds=60.0)
        injector.disable(InjectionPoint.DB_TIMEOUT)
        maybe_inject_db_timeout()  # should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# Property 5: Circuit Breaker Opens Under Sufficient Failures
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperty5CBOpensUnderFailures:
    """Feature: fault-injection, Property 5: Circuit Breaker Opens Under Sufficient Failures"""

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        min_samples=st.integers(min_value=4, max_value=20),
        threshold=st.floats(min_value=30.0, max_value=80.0),
    )
    def test_cb_opens_when_threshold_met(self, min_samples, threshold, metrics):
        config = GuardConfig.model_construct(
            cb_error_threshold_pct=threshold,
            cb_open_duration_seconds=30.0,
            cb_half_open_max_requests=3,
            cb_window_seconds=60.0,
            cb_min_samples=min_samples,
        )
        cb = CircuitBreaker(name="db_primary", config=config, metrics=metrics)

        # Record enough failures to exceed threshold
        # All failures → 100% failure rate, always >= threshold
        for _ in range(min_samples):
            cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN


# ═══════════════════════════════════════════════════════════════════════════════
# S1: DB Timeout → Circuit Breaker CLOSED→OPEN Integration Test
# ═══════════════════════════════════════════════════════════════════════════════


class TestS1DBTimeoutCBOpen:
    """
    S1 Integration: DB_TIMEOUT injection → CB transitions to OPEN.

    Validates:
      - CB starts CLOSED
      - After min_samples failures, CB transitions to OPEN
      - ptf_admin_circuit_breaker_state gauge == 2
      - allow_request() returns False when OPEN

    Requirements: 3.1, 3.2, 3.3
    """

    def test_db_timeout_triggers_cb_open(self, metrics, prom_registry):
        config = GuardConfig.model_construct(
            cb_error_threshold_pct=50.0,
            cb_open_duration_seconds=30.0,
            cb_half_open_max_requests=3,
            cb_window_seconds=60.0,
            cb_min_samples=4,
        )
        cb = CircuitBreaker(name="db_primary", config=config, metrics=metrics)

        # Enable DB_TIMEOUT injection
        injector = FaultInjector.get_instance()
        injector.enable(InjectionPoint.DB_TIMEOUT, ttl_seconds=60.0)

        # Verify initial state
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.allow_request() is True

        # Simulate DB timeout failures via injection hook
        for _ in range(4):
            try:
                maybe_inject_db_timeout()
            except TimeoutError:
                cb.record_failure()

        # CB should now be OPEN
        assert cb.state == CircuitBreakerState.OPEN

        # Verify metric gauge
        gauge_value = prom_registry.get_sample_value(
            "ptf_admin_circuit_breaker_state",
            {"dependency": "db_primary"},
        )
        assert gauge_value == 2.0  # OPEN

        # Verify deny behavior
        assert cb.allow_request() is False

    def test_mixed_success_failure_below_threshold(self, metrics):
        """Below threshold → CB stays CLOSED."""
        config = GuardConfig.model_construct(
            cb_error_threshold_pct=50.0,
            cb_open_duration_seconds=30.0,
            cb_half_open_max_requests=3,
            cb_window_seconds=60.0,
            cb_min_samples=4,
        )
        cb = CircuitBreaker(name="db_primary", config=config, metrics=metrics)

        # 1 failure + 3 successes = 25% failure rate < 50% threshold
        cb.record_failure()
        cb.record_success()
        cb.record_success()
        cb.record_success()

        assert cb.state == CircuitBreakerState.CLOSED
