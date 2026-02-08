"""
HTTP Request Metrics Middleware.

Tracks per-request count and duration via PTFMetrics.
Excludes /metrics endpoint to prevent infinite recursion.

Endpoint label uses 3-level fallback for low cardinality:
  Level 1: route.path template (e.g. /admin/market-prices/{period})
  Level 2: sanitized bucket — first 2 segments + /*
  Level 3: unmatched:{sanitized} for 404s

Exception path: handler exception → status_class="0xx" (no response produced).
Duration is still observed so that latency of crashing requests is visible.

Feature: telemetry-unification, Task 3.2 + Task 7.1
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def _sanitize_path(path: str) -> str:
    """Reduce path to first 2 segments + wildcard for low cardinality."""
    segments = [s for s in path.split("/") if s]
    if len(segments) <= 2:
        return path
    return "/" + "/".join(segments[:2]) + "/*"


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
                # Normal path — 3-level endpoint label normalization
                route = request.scope.get("route")
                if route:
                    endpoint = route.path
                elif response.status_code == 404:
                    endpoint = f"unmatched:{_sanitize_path(request.url.path)}"
                else:
                    endpoint = _sanitize_path(request.url.path)

                status_code = response.status_code
            else:
                # Exception path — no response produced
                endpoint = _sanitize_path(request.url.path)
                status_code = 0  # → normalizes to "0xx"

            metrics.inc_api_request(endpoint, request.method, status_code)
            metrics.observe_api_request_duration(endpoint, duration)

        if exc_to_raise is not None:
            raise exc_to_raise

        return response
