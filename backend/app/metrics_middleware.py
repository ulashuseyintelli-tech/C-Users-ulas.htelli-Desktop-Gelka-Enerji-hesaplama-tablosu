"""
HTTP Request Metrics Middleware.

Tracks per-request count and duration via PTFMetrics.
Excludes /metrics endpoint to prevent infinite recursion.

Endpoint label uses centralized endpoint_normalization module (3-level fallback):
  Level 1: route.path template (e.g. /admin/market-prices/{period})
  Level 2: canonicalized bucket — dynamic segments replaced
  Level 3: unmatched:{sanitized} for 404s

Exception path: handler exception → status_class="0xx" (no response produced).
Duration is still observed so that latency of crashing requests is visible.

Feature: telemetry-unification, Task 3.2 + Task 7.1
Updated: ops-guard, Task 3.1 — centralized normalization
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .endpoint_normalization import normalize_endpoint, sanitize_path, validate_label


class MetricsMiddleware(BaseHTTPMiddleware):
    """Increment api_request_total and observe api_request_duration for every request.

    Exception path: if call_next() raises, metrics are still recorded with
    status_class="0xx" (meaning "no HTTP response was produced") and the
    exception is re-raised so upstream error handling is unchanged.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip /metrics to avoid infinite recursion
        if request.url.path == "/metrics":
            return await call_next(request)

        start = time.monotonic()
        response = None
        exc_to_raise = None

        try:
            response = await call_next(request)
        except Exception as exc:
            exc_to_raise = exc
        finally:
            duration = time.monotonic() - start

            from .ptf_metrics import get_ptf_metrics
            metrics = get_ptf_metrics()

            if response is not None:
                normalized = normalize_endpoint(request, response.status_code)
                endpoint = normalized.template
                status_code = response.status_code
            else:
                # Exception path — no response produced
                endpoint = validate_label(sanitize_path(request.url.path))
                status_code = 0  # → normalizes to "0xx"

            metrics.inc_api_request(endpoint, request.method, status_code)
            metrics.observe_api_request_duration(endpoint, duration)

        if exc_to_raise is not None:
            raise exc_to_raise

        return response
