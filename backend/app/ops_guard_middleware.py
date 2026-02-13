"""
Ops-Guard Middleware — no-op skeleton.

Sits after MetricsMiddleware, before endpoint handlers.
Currently passes all requests through (ALLOW).
Guard chain (HD-2): KillSwitch → RateLimiter → CircuitBreaker → Handler
will be wired in Task 7.

Feature: ops-guard, Task 2.1
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class OpsGuardMiddleware(BaseHTTPMiddleware):
    """
    Operational guard middleware (no-op skeleton).

    Request flow (HD-2): KillSwitch → RateLimiter → CircuitBreaker → Handler.
    Currently all requests are allowed through without any guard checks.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip /metrics to avoid guard overhead on prometheus scrape
        if request.url.path == "/metrics":
            return await call_next(request)

        # No-op: all requests pass through
        # Guard chain will be wired in Task 7
        response = await call_next(request)
        return response
