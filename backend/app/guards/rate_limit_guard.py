"""
Endpoint Rate Limiter — fixed window, monotonic clock, fail-closed.

Design decisions:
  - Fixed window (not sliding) for determinism and simplicity (HD-3)
  - Monotonic clock for window tracking (immune to wall-clock drift)
  - Fail-closed on internal error → GuardDenyReason.INTERNAL_ERROR (HD-3)
  - Endpoint categories: import, heavy_read, default → different thresholds
  - Key: (endpoint_template, method) — actor bucketing deferred to middleware

Metric: ptf_admin_rate_limit_total{endpoint, decision} incremented on every check.

Feature: ops-guard, Task 5.1
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from ..guard_config import GuardConfig, GuardDenyReason
from ..ptf_metrics import PTFMetrics

logger = logging.getLogger(__name__)


# ── Endpoint category classification ─────────────────────────────────────────

class EndpointCategory:
    """Fixed endpoint categories for rate limiting (HD-5 bounded)."""
    IMPORT = "import"
    HEAVY_READ = "heavy_read"
    DEFAULT = "default"


# Prefix-based classification: longest match wins
_IMPORT_PREFIXES = (
    "/admin/market-prices/import/apply",
    "/admin/market-prices/import/preview",
    "/admin/market-prices/import",
)

_HEAVY_READ_PREFIXES = (
    "/admin/market-prices",
)


def classify_endpoint(endpoint_template: str, method: str) -> str:
    """
    Classify endpoint into rate limit category.

    Rules:
      - Import paths → IMPORT (strictest limit)
      - GET on market-prices list → HEAVY_READ
      - Everything else → DEFAULT

    Returns one of EndpointCategory constants.
    """
    normalized = endpoint_template.lower()

    for prefix in _IMPORT_PREFIXES:
        if normalized.startswith(prefix):
            return EndpointCategory.IMPORT

    if method.upper() == "GET":
        for prefix in _HEAVY_READ_PREFIXES:
            if normalized.startswith(prefix):
                return EndpointCategory.HEAVY_READ

    return EndpointCategory.DEFAULT


def get_limit_for_category(category: str, config: GuardConfig) -> int:
    """Return per-minute limit for the given category from config."""
    if category == EndpointCategory.IMPORT:
        return config.rate_limit_import_per_minute
    if category == EndpointCategory.HEAVY_READ:
        return config.rate_limit_heavy_read_per_minute
    return config.rate_limit_default_per_minute


# ── Fixed-window bucket ──────────────────────────────────────────────────────

@dataclass
class _WindowBucket:
    """Single fixed-window counter."""
    window_start: float = 0.0  # monotonic time
    count: int = 0


class RateLimitGuard:
    """
    Fixed-window endpoint rate limiter.

    Each (endpoint_template) gets its own bucket.
    Window resets every 60 seconds (monotonic clock).

    Fail-closed: any internal error → deny (HD-3).
    """

    WINDOW_SECONDS: float = 60.0

    def __init__(self, config: GuardConfig, metrics: PTFMetrics) -> None:
        self._config = config
        self._metrics = metrics
        self._buckets: dict[str, _WindowBucket] = {}

    def check_request(
        self,
        endpoint_template: str,
        method: str,
    ) -> Optional[GuardDenyReason]:
        """
        Check rate limit for the given endpoint.

        Returns None if allowed, GuardDenyReason.RATE_LIMITED if over limit,
        or GuardDenyReason.INTERNAL_ERROR on internal failure (fail-closed).

        Side effect: increments ptf_admin_rate_limit_total{endpoint, decision}.
        """
        try:
            category = classify_endpoint(endpoint_template, method)
            limit = get_limit_for_category(category, self._config)
            now = time.monotonic()

            bucket = self._buckets.get(endpoint_template)

            if bucket is None or (now - bucket.window_start) >= self.WINDOW_SECONDS:
                # New window
                bucket = _WindowBucket(window_start=now, count=0)
                self._buckets[endpoint_template] = bucket

            bucket.count += 1

            if bucket.count > limit:
                self._metrics.inc_rate_limit(endpoint_template, "rejected")
                return GuardDenyReason.RATE_LIMITED

            self._metrics.inc_rate_limit(endpoint_template, "allowed")
            return None

        except Exception as exc:
            logger.error(f"[RATE-LIMIT] Internal error: {exc}")
            # Fail-closed (HD-3)
            try:
                self._metrics.inc_rate_limit(endpoint_template, "rejected")
            except Exception:
                pass
            return GuardDenyReason.INTERNAL_ERROR

    def get_retry_after(self, endpoint_template: str) -> int:
        """
        Calculate Retry-After seconds for a rate-limited endpoint.

        Returns seconds until current window resets.
        """
        bucket = self._buckets.get(endpoint_template)
        if bucket is None:
            return int(self.WINDOW_SECONDS)

        elapsed = time.monotonic() - bucket.window_start
        remaining = self.WINDOW_SECONDS - elapsed
        return max(1, int(remaining) + 1)

    def reset(self) -> None:
        """Clear all buckets (test only)."""
        self._buckets.clear()
