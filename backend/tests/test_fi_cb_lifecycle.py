"""
Tests for S2: External 5xx Burst → Circuit Breaker Full Lifecycle.

Property-based tests (Hypothesis):
  - Property 6: Circuit Breaker Lifecycle Metric Sequence

Integration test:
  - S2: CLOSED→OPEN→HALF_OPEN→CLOSED with StubServer

Feature: fault-injection, Tasks 6.1, 6.2
Requirements: 4.1, 4.2, 4.3, 4.4
"""

import time
from unittest.mock import patch

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck
from prometheus_client import CollectorRegistry

from app.guard_config import GuardConfig
from app.ptf_metrics import PTFMetrics
from app.guards.circuit_breaker import CircuitBreaker, CircuitBreakerState
from app.testing.stub_server import StubServer


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def prom_registry():
    return CollectorRegistry()


@pytest.fixture
def metrics(prom_registry):
    return PTFMetrics(registry=prom_registry)


def _make_config(open_duration: float = 0.5) -> GuardConfig:
    return GuardConfig.model_construct(
        cb_error_threshold_pct=50.0,
        cb_open_duration_seconds=open_duration,
        cb_half_open_max_requests=3,
        cb_window_seconds=60.0,
        cb_min_samples=4,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Property 6: Circuit Breaker Lifecycle Metric Sequence
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperty6CBLifecycleMetricSequence:
    """Feature: fault-injection, Property 6: Circuit Breaker Lifecycle Metric Sequence"""

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        min_samples=st.integers(min_value=4, max_value=10),
        half_open_max=st.integers(min_value=1, max_value=5),
    )
    def test_lifecycle_gauge_sequence(self, min_samples, half_open_max, metrics, prom_registry):
        """Full lifecycle: gauge should emit 0→2→1→0."""
        config = GuardConfig.model_construct(
            cb_error_threshold_pct=50.0,
            cb_open_duration_seconds=0.1,
            cb_half_open_max_requests=half_open_max,
            cb_window_seconds=60.0,
            cb_min_samples=min_samples,
        )
        cb = CircuitBreaker(name="external_api", config=config, metrics=metrics)

        def _gauge():
            return prom_registry.get_sample_value(
                "ptf_admin_circuit_breaker_state",
                {"dependency": "external_api"},
            )

        # CLOSED (0)
        assert _gauge() == 0.0

        # Force OPEN: all failures
        for _ in range(min_samples):
            cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        assert _gauge() == 2.0

        # Wait for OPEN→HALF_OPEN transition
        time.sleep(0.15)
        assert cb.state == CircuitBreakerState.HALF_OPEN
        assert _gauge() == 1.0

        # Successful probes → CLOSED
        for _ in range(half_open_max):
            assert cb.allow_request() is True
            cb.record_success()
        assert cb.state == CircuitBreakerState.CLOSED
        assert _gauge() == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# S2: External 5xx Burst → CB Full Lifecycle Integration Test
# ═══════════════════════════════════════════════════════════════════════════════


class TestS2ExternalBurstCBLifecycle:
    """
    S2 Integration: StubServer 5xx burst → CB CLOSED→OPEN→HALF_OPEN→CLOSED.

    Uses real HTTP connections via StubServer.

    Requirements: 4.1, 4.2, 4.3, 4.4
    """

    def test_full_lifecycle_with_stub_server(self, metrics, prom_registry):
        config = _make_config(open_duration=0.3)
        cb = CircuitBreaker(name="external_api", config=config, metrics=metrics)

        server = StubServer()
        server.start()
        try:
            import urllib.request
            import urllib.error

            def _call_stub() -> int:
                try:
                    resp = urllib.request.urlopen(server.url)
                    return resp.status
                except urllib.error.HTTPError as e:
                    return e.code

            # Phase 1: Fail mode → CB OPEN
            StubServer.set_fail_mode(True)
            for _ in range(4):
                status = _call_stub()
                assert status == 500
                cb.record_failure()

            assert cb.state == CircuitBreakerState.OPEN

            # Phase 2: Wait for HALF_OPEN
            time.sleep(0.35)
            assert cb.state == CircuitBreakerState.HALF_OPEN

            # Phase 3: Recovery — stub returns 200
            StubServer.set_fail_mode(False)
            for _ in range(3):
                assert cb.allow_request() is True
                status = _call_stub()
                assert status == 200
                cb.record_success()

            # Phase 4: Verify CLOSED
            assert cb.state == CircuitBreakerState.CLOSED

            # Verify final gauge
            gauge = prom_registry.get_sample_value(
                "ptf_admin_circuit_breaker_state",
                {"dependency": "external_api"},
            )
            assert gauge == 0.0

        finally:
            server.stop()
