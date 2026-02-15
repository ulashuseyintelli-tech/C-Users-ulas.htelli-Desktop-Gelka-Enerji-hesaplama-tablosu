"""
Ops-Guard Middleware — decision authority for guard chain.

Sits after MetricsMiddleware, before endpoint handlers.
Guard chain (HD-2): KillSwitch → RateLimiter → CircuitBreaker → Handler.

Decision contract:
  - First deny wins; subsequent guards are NOT evaluated
  - Deny reasons map to deterministic HTTP status + headers
  - RATE_LIMITED → 429 + Retry-After
  - KILL_SWITCHED → 503
  - CIRCUIT_OPEN → 503
  - INTERNAL_ERROR → 503 (fail-closed from rate limiter)
  - Metrics emitted by individual guards (no double-count at middleware level)

Skip list: /metrics, /health, /health/ready (infra endpoints)

Feature: ops-guard, Task 7.1
"""

import logging
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .guard_config import GuardDenyReason

logger = logging.getLogger(__name__)

# Endpoints that bypass all guard checks
_SKIP_PATHS = frozenset({"/metrics", "/health", "/health/ready", "/admin/telemetry/events"})

# HTTP status code mapping for deny reasons
_DENY_STATUS: dict[GuardDenyReason, int] = {
    GuardDenyReason.KILL_SWITCHED: 503,
    GuardDenyReason.RATE_LIMITED: 429,
    GuardDenyReason.CIRCUIT_OPEN: 503,
    GuardDenyReason.INTERNAL_ERROR: 503,
}

# Error body mapping
_DENY_BODY: dict[GuardDenyReason, dict] = {
    GuardDenyReason.KILL_SWITCHED: {
        "error": "service_unavailable",
        "reason": "KILL_SWITCHED",
        "message": "Endpoint is currently disabled by kill-switch.",
    },
    GuardDenyReason.RATE_LIMITED: {
        "error": "rate_limit_exceeded",
        "reason": "RATE_LIMITED",
        "message": "Rate limit exceeded. Please retry after the indicated period.",
    },
    GuardDenyReason.CIRCUIT_OPEN: {
        "error": "service_unavailable",
        "reason": "CIRCUIT_OPEN",
        "message": "Service dependency is currently unavailable.",
    },
    GuardDenyReason.INTERNAL_ERROR: {
        "error": "service_unavailable",
        "reason": "INTERNAL_ERROR",
        "message": "Guard system internal error. Request denied for safety.",
    },
}


class OpsGuardMiddleware(BaseHTTPMiddleware):
    """
    Operational guard middleware — single decision authority.

    Request flow (HD-2): KillSwitch → RateLimiter → CircuitBreaker → Handler.
    First deny wins; subsequent guards are skipped.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip infra endpoints
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        try:
            deny_reason = self._evaluate_guards(request)
        except Exception as exc:
            # Middleware-level internal error → fail-open (don't block on guard bug)
            logger.error(f"[OPS-GUARD] Middleware internal error, failing open: {exc}")
            try:
                from .ptf_metrics import get_ptf_metrics
                get_ptf_metrics().inc_guard_failopen()
            except Exception:
                pass
            deny_reason = None

        if deny_reason is not None:
            return self._build_deny_response(request, deny_reason)

        # All guards passed → forward to handler
        response = await call_next(request)
        return response

    def _evaluate_guards(self, request: Request) -> Optional[GuardDenyReason]:
        """
        Evaluate guard chain in HD-2 order.
        Returns first deny reason, or None if all pass.
        """
        from .endpoint_normalization import normalize_endpoint, EndpointClass

        # Normalize endpoint once — used by all guards
        normalized = normalize_endpoint(request)
        endpoint_template = normalized.template
        method = normalized.method
        is_high_risk = normalized.endpoint_class == EndpointClass.HIGH_RISK

        # ── 1. Kill-Switch (HD-2: first) ─────────────────────────────────
        ks_deny = self._check_kill_switch(endpoint_template, method, is_high_risk)
        if ks_deny is not None:
            return ks_deny

        # ── 2. Rate Limiter (HD-2: second) ───────────────────────────────
        rl_deny = self._check_rate_limit(endpoint_template, method)
        if rl_deny is not None:
            # Stash endpoint for Retry-After calculation
            request.state.rate_limited_endpoint = endpoint_template
            return rl_deny

        # ── 3. Circuit Breaker (HD-2: third, pre-check) ─────────────────
        cb_deny = self._check_circuit_breaker(endpoint_template)
        if cb_deny is not None:
            return cb_deny

        return None  # ALLOW

    # ── Guard delegates ───────────────────────────────────────────────────

    def _check_kill_switch(
        self, endpoint_template: str, method: str, is_high_risk: bool
    ) -> Optional[GuardDenyReason]:
        """Delegate to KillSwitchManager.check_request()."""
        try:
            from .main import _get_kill_switch_manager
            manager = _get_kill_switch_manager()
            return manager.check_request(
                endpoint_template=endpoint_template,
                method=method,
                is_high_risk=is_high_risk,
                tenant_id=None,  # tenant extraction deferred
            )
        except Exception as exc:
            logger.error(f"[OPS-GUARD] Kill-switch check error: {exc}")
            if is_high_risk:
                return GuardDenyReason.INTERNAL_ERROR  # fail-closed for high-risk
            return None  # fail-open for standard

    def _check_rate_limit(
        self, endpoint_template: str, method: str
    ) -> Optional[GuardDenyReason]:
        """Delegate to RateLimitGuard.check_request()."""
        try:
            guard = _get_rate_limit_guard()
            return guard.check_request(endpoint_template, method)
        except Exception as exc:
            logger.error(f"[OPS-GUARD] Rate limit check error: {exc}")
            return GuardDenyReason.INTERNAL_ERROR  # fail-closed (HD-3)

    def _check_circuit_breaker(
        self, endpoint_template: str
    ) -> Optional[GuardDenyReason]:
        """CB pre-check: endpoint'in bağımlılıklarının CB durumunu kontrol et (DW-2)."""
        try:
            from .guard_config import get_guard_config
            config = get_guard_config()

            # DW-2: Flag kapalıysa pre-check atla
            if not config.cb_precheck_enabled:
                return None

            from .guards.endpoint_dependency_map import get_dependencies
            from .main import _get_cb_registry

            dependencies = get_dependencies(endpoint_template)
            if not dependencies:
                # Mapping miss — log endpoint for troubleshooting, metric label'sız (HD-5)
                logger.debug(f"[OPS-GUARD] CB pre-check skip: no mapping for {endpoint_template}")
                try:
                    from .ptf_metrics import get_ptf_metrics
                    get_ptf_metrics().inc_dependency_map_miss()
                except Exception:
                    pass
                return None  # Bilinmeyen endpoint → CB pre-check atla

            registry = _get_cb_registry()
            for dep in dependencies:
                cb = registry.get(dep.value)
                if not cb.allow_request():
                    return GuardDenyReason.CIRCUIT_OPEN

            return None  # Tüm CB'ler geçiyor

        except Exception as exc:
            # CB pre-check hatası → fail-open + metrik (DW-3)
            logger.error(f"[OPS-GUARD] CB pre-check error, failing open: {exc}")
            try:
                from .ptf_metrics import get_ptf_metrics
                get_ptf_metrics().inc_guard_failopen()
            except Exception:
                pass
            return None

    # ── Response builder ──────────────────────────────────────────────────

    def _build_deny_response(
        self, request: Request, reason: GuardDenyReason
    ) -> JSONResponse:
        """Build deterministic deny response with correct status + headers."""
        status_code = _DENY_STATUS.get(reason, 503)
        body = dict(_DENY_BODY.get(reason, {"error": "unknown", "reason": reason.value}))

        headers: dict[str, str] = {}

        # Retry-After only for RATE_LIMITED
        if reason == GuardDenyReason.RATE_LIMITED:
            endpoint = getattr(request.state, "rate_limited_endpoint", None)
            if endpoint:
                guard = _get_rate_limit_guard()
                retry_after = guard.get_retry_after(endpoint)
            else:
                retry_after = 60
            headers["Retry-After"] = str(retry_after)
            body["retry_after"] = retry_after

        logger.warning(
            f"[OPS-GUARD] DENY {reason.value} {request.method} {request.url.path} "
            f"→ {status_code}"
        )

        return JSONResponse(
            status_code=status_code,
            content=body,
            headers=headers,
        )


# ── Rate Limit Guard singleton ────────────────────────────────────────────────

_rate_limit_guard = None


def _get_rate_limit_guard():
    """Lazy singleton for RateLimitGuard."""
    global _rate_limit_guard
    if _rate_limit_guard is None:
        from .guards.rate_limit_guard import RateLimitGuard
        from .guard_config import get_guard_config
        from .ptf_metrics import get_ptf_metrics
        _rate_limit_guard = RateLimitGuard(get_guard_config(), get_ptf_metrics())
    return _rate_limit_guard
