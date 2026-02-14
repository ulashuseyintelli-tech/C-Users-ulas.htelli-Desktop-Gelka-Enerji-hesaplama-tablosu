"""
Tests for Ops-Guard Endpoint Rate Limiter.

Covers:
  - Endpoint classification (import / heavy_read / default)
  - Fixed-window allow/deny logic
  - Window reset behavior
  - Fail-closed on internal error (HD-3)
  - Metric emission (ptf_admin_rate_limit_total)
  - Retry-After calculation
  - Config-driven limits

Feature: ops-guard, Task 5.2
"""

import time
from unittest.mock import patch, MagicMock

import pytest
from prometheus_client import CollectorRegistry

from app.guard_config import GuardConfig, GuardDenyReason
from app.ptf_metrics import PTFMetrics
from app.guards.rate_limit_guard import (
    RateLimitGuard,
    classify_endpoint,
    get_limit_for_category,
    EndpointCategory,
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
    return GuardConfig.model_construct(
        rate_limit_import_per_minute=5,
        rate_limit_heavy_read_per_minute=10,
        rate_limit_default_per_minute=20,
        rate_limit_fail_closed=True,
    )


@pytest.fixture
def guard(config, metrics):
    return RateLimitGuard(config=config, metrics=metrics)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Endpoint Classification
# ═══════════════════════════════════════════════════════════════════════════════

class TestClassifyEndpoint:
    """classify_endpoint returns correct category for known patterns."""

    def test_import_apply(self):
        assert classify_endpoint("/admin/market-prices/import/apply", "POST") == EndpointCategory.IMPORT

    def test_import_preview(self):
        assert classify_endpoint("/admin/market-prices/import/preview", "POST") == EndpointCategory.IMPORT

    def test_import_base(self):
        assert classify_endpoint("/admin/market-prices/import", "POST") == EndpointCategory.IMPORT

    def test_heavy_read_get(self):
        assert classify_endpoint("/admin/market-prices", "GET") == EndpointCategory.HEAVY_READ

    def test_heavy_read_with_subpath(self):
        assert classify_endpoint("/admin/market-prices/2024-01", "GET") == EndpointCategory.HEAVY_READ

    def test_market_prices_post_is_not_heavy_read(self):
        """POST to market-prices is import-like, not heavy_read."""
        # /admin/market-prices POST doesn't match import prefixes exactly
        # but it's not GET so it won't be heavy_read either → default
        result = classify_endpoint("/admin/market-prices", "POST")
        assert result == EndpointCategory.DEFAULT

    def test_default_health(self):
        assert classify_endpoint("/health", "GET") == EndpointCategory.DEFAULT

    def test_default_metrics(self):
        assert classify_endpoint("/metrics", "GET") == EndpointCategory.DEFAULT

    def test_default_unknown(self):
        assert classify_endpoint("/some/random/path", "GET") == EndpointCategory.DEFAULT

    def test_case_insensitive(self):
        assert classify_endpoint("/Admin/Market-Prices/Import/Apply", "POST") == EndpointCategory.IMPORT


class TestGetLimitForCategory:
    """get_limit_for_category reads correct config field."""

    def test_import_limit(self, config):
        assert get_limit_for_category(EndpointCategory.IMPORT, config) == 5

    def test_heavy_read_limit(self, config):
        assert get_limit_for_category(EndpointCategory.HEAVY_READ, config) == 10

    def test_default_limit(self, config):
        assert get_limit_for_category(EndpointCategory.DEFAULT, config) == 20


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Allow / Deny Logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimitAllowDeny:
    """Fixed-window allow/deny with correct thresholds."""

    def test_allows_under_limit(self, guard):
        """Requests under limit are allowed."""
        for _ in range(5):
            result = guard.check_request("/admin/market-prices/import/apply", "POST")
            assert result is None

    def test_denies_over_limit(self, guard):
        """Request N+1 is denied with RATE_LIMITED."""
        for _ in range(5):
            guard.check_request("/admin/market-prices/import/apply", "POST")

        result = guard.check_request("/admin/market-prices/import/apply", "POST")
        assert result == GuardDenyReason.RATE_LIMITED

    def test_different_endpoints_independent(self, guard):
        """Each endpoint has its own bucket."""
        # Fill import bucket
        for _ in range(5):
            guard.check_request("/admin/market-prices/import/apply", "POST")

        # Default endpoint should still be allowed
        result = guard.check_request("/health", "GET")
        assert result is None

    def test_heavy_read_limit(self, guard):
        """Heavy read uses its own limit (10)."""
        for _ in range(10):
            result = guard.check_request("/admin/market-prices", "GET")
            assert result is None

        result = guard.check_request("/admin/market-prices", "GET")
        assert result == GuardDenyReason.RATE_LIMITED

    def test_default_limit(self, guard):
        """Default category uses default limit (20)."""
        for _ in range(20):
            result = guard.check_request("/health", "GET")
            assert result is None

        result = guard.check_request("/health", "GET")
        assert result == GuardDenyReason.RATE_LIMITED


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Window Reset
# ═══════════════════════════════════════════════════════════════════════════════

class TestWindowReset:
    """Window resets after WINDOW_SECONDS elapsed."""

    def test_window_resets_after_expiry(self, guard):
        """After window expires, counter resets and requests are allowed again."""
        # Fill the bucket
        for _ in range(5):
            guard.check_request("/admin/market-prices/import/apply", "POST")

        # Denied
        assert guard.check_request("/admin/market-prices/import/apply", "POST") == GuardDenyReason.RATE_LIMITED

        # Simulate window expiry by manipulating bucket
        bucket = guard._buckets["/admin/market-prices/import/apply"]
        bucket.window_start -= 61.0  # push start back past window

        # Should be allowed again (new window)
        result = guard.check_request("/admin/market-prices/import/apply", "POST")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Fail-Closed (HD-3)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailClosed:
    """Internal errors result in INTERNAL_ERROR deny (fail-closed)."""

    def test_internal_error_returns_internal_error(self, guard):
        """When classify_endpoint raises, guard returns INTERNAL_ERROR."""
        with patch(
            "app.guards.rate_limit_guard.classify_endpoint",
            side_effect=RuntimeError("boom"),
        ):
            result = guard.check_request("/health", "GET")
            assert result == GuardDenyReason.INTERNAL_ERROR

    def test_internal_error_emits_rejected_metric(self, guard, metrics, registry):
        """Fail-closed still emits rejected metric."""
        with patch(
            "app.guards.rate_limit_guard.classify_endpoint",
            side_effect=RuntimeError("boom"),
        ):
            guard.check_request("/health", "GET")

        val = metrics._rate_limit_total.labels(endpoint="/health", decision="rejected")._value.get()
        assert val >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Metrics
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetrics:
    """ptf_admin_rate_limit_total{endpoint, decision} is emitted correctly."""

    def test_allowed_metric(self, guard, metrics):
        guard.check_request("/health", "GET")
        val = metrics._rate_limit_total.labels(endpoint="/health", decision="allowed")._value.get()
        assert val == 1.0

    def test_rejected_metric(self, guard, metrics):
        for _ in range(6):
            guard.check_request("/admin/market-prices/import/apply", "POST")

        allowed = metrics._rate_limit_total.labels(
            endpoint="/admin/market-prices/import/apply", decision="allowed"
        )._value.get()
        rejected = metrics._rate_limit_total.labels(
            endpoint="/admin/market-prices/import/apply", decision="rejected"
        )._value.get()
        assert allowed == 5.0
        assert rejected == 1.0

    def test_metrics_accumulate(self, guard, metrics):
        """Multiple requests accumulate metric counts."""
        for _ in range(3):
            guard.check_request("/health", "GET")

        val = metrics._rate_limit_total.labels(endpoint="/health", decision="allowed")._value.get()
        assert val == 3.0


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Retry-After
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetryAfter:
    """get_retry_after returns seconds until window reset."""

    def test_retry_after_for_unknown_endpoint(self, guard):
        """Unknown endpoint returns full window."""
        assert guard.get_retry_after("/unknown") == 60

    def test_retry_after_positive(self, guard):
        """After a request, retry_after is positive and ≤ window."""
        guard.check_request("/health", "GET")
        retry = guard.get_retry_after("/health")
        assert 1 <= retry <= 61

    def test_retry_after_decreases_over_time(self, guard):
        """Retry-after decreases as window progresses."""
        guard.check_request("/health", "GET")
        bucket = guard._buckets["/health"]
        # Simulate 30 seconds elapsed
        bucket.window_start -= 30.0
        retry = guard.get_retry_after("/health")
        assert retry <= 32  # ~30 remaining + 1 rounding


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Reset (test utility)
# ═══════════════════════════════════════════════════════════════════════════════

class TestReset:
    """reset() clears all buckets."""

    def test_reset_clears_state(self, guard):
        for _ in range(5):
            guard.check_request("/admin/market-prices/import/apply", "POST")

        guard.reset()

        # Should be allowed again
        result = guard.check_request("/admin/market-prices/import/apply", "POST")
        assert result is None
