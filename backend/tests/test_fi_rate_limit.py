"""
Tests for S4: Rate Limit Spike integration.

Property-based tests (Hypothesis):
  - Property 8: Rate Limit Enforcement with Metrics
  - Property 9: Rate Limit Determinism

Integration test:
  - S4: Low limit config → 429 + Retry-After + metric counter

Feature: fault-injection, Tasks 9.1, 9.2, 9.3
Requirements: 6.1, 6.2, 6.3, 6.4
"""

import pytest
from hypothesis import given, settings, strategies as st, HealthCheck
from prometheus_client import CollectorRegistry

from app.guard_config import GuardConfig, GuardDenyReason
from app.ptf_metrics import PTFMetrics
from app.guards.rate_limit_guard import RateLimitGuard


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def prom_registry():
    return CollectorRegistry()


@pytest.fixture
def metrics(prom_registry):
    return PTFMetrics(registry=prom_registry)


def _make_guard(metrics: PTFMetrics, limit: int = 5) -> RateLimitGuard:
    config = GuardConfig.model_construct(
        rate_limit_import_per_minute=limit,
        rate_limit_heavy_read_per_minute=limit,
        rate_limit_default_per_minute=limit,
        rate_limit_fail_closed=True,
    )
    return RateLimitGuard(config, metrics)


# ═══════════════════════════════════════════════════════════════════════════════
# Property 8: Rate Limit Enforcement with Metrics
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperty8RateLimitEnforcement:
    """Feature: fault-injection, Property 8: Rate Limit Enforcement with Metrics"""

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        limit=st.integers(min_value=1, max_value=20),
    )
    def test_first_n_allowed_then_denied(self, limit, metrics, prom_registry):
        guard = _make_guard(metrics, limit=limit)
        endpoint = "/admin/market-prices"

        # First `limit` requests should be allowed
        for i in range(limit):
            result = guard.check_request(endpoint, "GET")
            assert result is None, f"Request {i+1} should be allowed"

        # Request limit+1 should be denied
        result = guard.check_request(endpoint, "GET")
        assert result == GuardDenyReason.RATE_LIMITED

        # Verify rejected metric incremented
        rejected = prom_registry.get_sample_value(
            "ptf_admin_rate_limit_total",
            {"endpoint": endpoint, "decision": "rejected"},
        )
        assert rejected is not None and rejected >= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# Property 9: Rate Limit Determinism
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperty9RateLimitDeterminism:
    """Feature: fault-injection, Property 9: Rate Limit Determinism"""

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        limit=st.integers(min_value=1, max_value=15),
        num_requests=st.integers(min_value=1, max_value=30),
    )
    def test_same_sequence_same_results(self, limit, num_requests, metrics):
        """Two identical runs produce identical allow/deny sequences."""
        endpoint = "/admin/test-endpoint"

        # Run 1
        guard1 = _make_guard(metrics, limit=limit)
        results1 = [guard1.check_request(endpoint, "GET") for _ in range(num_requests)]

        # Run 2 (fresh guard, same config)
        guard2 = _make_guard(metrics, limit=limit)
        results2 = [guard2.check_request(endpoint, "GET") for _ in range(num_requests)]

        assert results1 == results2


# ═══════════════════════════════════════════════════════════════════════════════
# S4: Rate Limit Spike Integration Test
# ═══════════════════════════════════════════════════════════════════════════════


class TestS4RateLimitSpike:
    """
    S4 Integration: Low limit → 429 + Retry-After + metric counter.

    Requirements: 6.1, 6.3, 6.4
    """

    def test_spike_triggers_429(self, metrics, prom_registry):
        guard = _make_guard(metrics, limit=5)
        endpoint = "/admin/market-prices/import/apply"

        # 5 allowed
        for _ in range(5):
            assert guard.check_request(endpoint, "POST") is None

        # 6th denied
        result = guard.check_request(endpoint, "POST")
        assert result == GuardDenyReason.RATE_LIMITED

    def test_retry_after_positive(self, metrics):
        guard = _make_guard(metrics, limit=5)
        endpoint = "/admin/market-prices/import/apply"

        # Exhaust limit
        for _ in range(6):
            guard.check_request(endpoint, "POST")

        retry_after = guard.get_retry_after(endpoint)
        assert retry_after > 0
        assert retry_after <= 61  # window is 60s

    def test_rejected_metric_increments(self, metrics, prom_registry):
        guard = _make_guard(metrics, limit=3)
        endpoint = "/admin/market-prices"

        # 3 allowed + 2 denied
        for _ in range(5):
            guard.check_request(endpoint, "GET")

        rejected = prom_registry.get_sample_value(
            "ptf_admin_rate_limit_total",
            {"endpoint": endpoint, "decision": "rejected"},
        )
        assert rejected == 2.0

        allowed = prom_registry.get_sample_value(
            "ptf_admin_rate_limit_total",
            {"endpoint": endpoint, "decision": "allowed"},
        )
        assert allowed == 3.0

    def test_window_reset_allows_new_requests(self, metrics):
        """After window reset, new requests are allowed."""
        guard = _make_guard(metrics, limit=2)
        endpoint = "/admin/test"

        # Exhaust limit
        guard.check_request(endpoint, "GET")
        guard.check_request(endpoint, "GET")
        assert guard.check_request(endpoint, "GET") == GuardDenyReason.RATE_LIMITED

        # Reset (simulates window expiry)
        guard.reset()
        assert guard.check_request(endpoint, "GET") is None
