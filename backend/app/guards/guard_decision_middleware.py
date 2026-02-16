"""
Guard Decision Middleware — wiring layer for decision snapshot + enforcement.

Runs AFTER OpsGuardMiddleware in request flow (added BEFORE in add_middleware
calls due to Starlette LIFO). Only activates on the allow path — if existing
guards denied, the response is already returned before this middleware runs.

Eval flow:
  1. Skip infra paths (_SKIP_PATHS)
  2. SnapshotFactory.build() → snapshot (None on error = fail-open)
  3. evaluate(snapshot) → verdict
  4. BLOCK_STALE → 503 + errorCode: OPS_GUARD_STALE
  5. BLOCK_INSUFFICIENT → 503 + errorCode: OPS_GUARD_INSUFFICIENT
  6. ALLOW / PASSTHROUGH → call_next(request)
  7. Attach snapshot to request.state.guard_decision_snapshot

Feature: runtime-guard-decision, Wiring Task
"""
from __future__ import annotations

import logging
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .guard_decision import (
    SnapshotFactory, WindowParams, SignalReasonCode,
    TenantMode, resolve_tenant_mode, parse_tenant_modes,
    parse_tenant_allowlist, sanitize_metric_tenant, sanitize_tenant_id,
)
from .guard_enforcement import EnforcementVerdict, evaluate

logger = logging.getLogger(__name__)

# Same skip paths as OpsGuardMiddleware — infra endpoints bypass decision layer
_SKIP_PATHS = frozenset({"/metrics", "/health", "/health/ready", "/admin/telemetry/events"})


class GuardDecisionMiddleware(BaseHTTPMiddleware):
    """
    Decision snapshot middleware. Wraps existing guard chain output
    with signal-based stale/insufficient detection.

    Fail-open: any internal error → request passes through unchanged.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip infra endpoints
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        try:
            return await self._evaluate_decision(request, call_next)
        except Exception as exc:
            # Fail-open: middleware crash → existing behavior preserved
            logger.error(f"[GUARD-DECISION] Middleware internal error, failing open: {exc}")
            try:
                from ..ptf_metrics import get_ptf_metrics
                get_ptf_metrics().inc_guard_failopen()
            except Exception:
                pass
            return await call_next(request)

    async def _evaluate_decision(self, request: Request, call_next) -> Response:
        """Build snapshot, evaluate, enforce — tenant-aware."""
        from ..guard_config import get_guard_config
        from ..endpoint_normalization import normalize_endpoint
        from .endpoint_dependency_map import get_dependencies

        config = get_guard_config()

        # Decision layer disabled → pass through (explicit opt-in)
        if not config.decision_layer_enabled:
            return await call_next(request)

        # ── Tenant mode resolution ──────────────────────────────────────
        try:
            tenant_id = sanitize_tenant_id(request.headers.get("X-Tenant-Id"))
            tenant_modes = parse_tenant_modes(config.decision_layer_tenant_modes_json)
            try:
                default_mode = TenantMode(config.decision_layer_default_mode)
            except (ValueError, KeyError):
                default_mode = TenantMode.SHADOW
            tenant_mode = resolve_tenant_mode(tenant_id, default_mode, tenant_modes)
        except Exception as exc:
            # Fail-open: tenant resolution error → passthrough
            logger.error(f"[GUARD-DECISION] Tenant resolution error, failing open: {exc}")
            return await call_next(request)

        # Tenant mode OFF → passthrough (don't even build snapshot)
        if tenant_mode == TenantMode.OFF:
            return await call_next(request)

        # Emit "decision layer active" counter — deploy sonrası sıra doğrulama
        try:
            from ..ptf_metrics import get_ptf_metrics
            get_ptf_metrics().inc_guard_decision_request()
        except Exception:
            pass

        normalized = normalize_endpoint(request)
        endpoint = normalized.template
        method = normalized.method

        dependencies = get_dependencies(endpoint)

        # Metric tenant label (cardinality-safe)
        allowlist = parse_tenant_allowlist(config.decision_layer_tenant_allowlist_json)
        metric_tenant = sanitize_metric_tenant(tenant_id, allowlist)

        # Build snapshot — fail-open on error (returns None)
        snapshot = SnapshotFactory.build(
            guard_deny_reason=None,  # We're on the allow path
            config=config,
            endpoint=endpoint,
            method=method,
            dependencies=dependencies if dependencies else None,
            tenant_id=tenant_id,
        )

        if snapshot is None:
            # Snapshot build failed — emit metric, fail-open
            try:
                from ..ptf_metrics import get_ptf_metrics
                get_ptf_metrics().inc_guard_decision_snapshot_build_failure()
            except Exception:
                pass
            response = await call_next(request)
            return response

        # Evaluate enforcement verdict
        verdict = evaluate(snapshot)

        is_shadow = tenant_mode == TenantMode.SHADOW
        current_mode = "shadow" if is_shadow else "enforce"

        if verdict == EnforcementVerdict.BLOCK_STALE:
            self._emit_block_metric("stale", current_mode, tenant=metric_tenant)
            if not is_shadow:
                return self._build_block_response(
                    error_code="OPS_GUARD_STALE",
                    reason_codes=self._extract_reason_codes(snapshot),
                )
            # Shadow: log + passthrough
            logger.info(
                f"[GUARD-DECISION] SHADOW block: OPS_GUARD_STALE "
                f"{method} {endpoint} tenant={tenant_id}"
            )

        if verdict == EnforcementVerdict.BLOCK_INSUFFICIENT:
            self._emit_block_metric("insufficient", current_mode, tenant=metric_tenant)
            if not is_shadow:
                return self._build_block_response(
                    error_code="OPS_GUARD_INSUFFICIENT",
                    reason_codes=self._extract_reason_codes(snapshot),
                )
            # Shadow: log + passthrough
            logger.info(
                f"[GUARD-DECISION] SHADOW block: OPS_GUARD_INSUFFICIENT "
                f"{method} {endpoint} tenant={tenant_id}"
            )

        # ALLOW or PASSTHROUGH — forward to handler
        response = await call_next(request)

        # Attach snapshot to request.state for downstream consumers
        try:
            request.state.guard_decision_snapshot = snapshot
        except Exception:
            pass  # request.state may not be writable in all contexts

        return response


    @staticmethod
    def _build_block_response(
        error_code: str,
        reason_codes: list[str],
    ) -> JSONResponse:
        """Build deterministic 503 response for decision layer blocks."""
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "errorCode": error_code,
                "reasonCodes": reason_codes,
                "message": "Request blocked by guard decision layer.",
            },
        )

    @staticmethod
    def _extract_reason_codes(snapshot) -> list[str]:
        """Extract non-OK reason codes from snapshot signals.

        Canonical ordering: SignalName enum ordinal, then SignalReasonCode ordinal.
        Deterministic — same snapshot always produces same list.
        """
        codes = [
            (s.name.value, s.reason_code.value, s.reason_code.value)
            for s in snapshot.signals
            if s.reason_code != SignalReasonCode.OK
        ]
        # Sort by SignalName value (str), then ReasonCode value (str) — stable
        codes.sort(key=lambda t: (t[0], t[1]))
        return [c[2] for c in codes]

    @staticmethod
    def _emit_block_metric(kind: str, mode: str = "enforce", *, tenant: str | None = None) -> None:
        """Emit block metric with mode and optional tenant label. Safe to call anytime."""
        try:
            from ..ptf_metrics import get_ptf_metrics
            get_ptf_metrics().inc_guard_decision_block(kind, mode)
        except Exception:
            pass
