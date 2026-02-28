"""Enforcement decision engine (Phase F).

Provides enforce_validation() which runs the new validator in the configured mode
and returns an EnforcementDecision (pass/warn/block).

Exception semantics:
  - ValidationBlockedError is raised ONLY in enforce_hard mode.
  - enforce_soft NEVER raises; it logs warnings + records metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .enforcement_config import (
    EnforcementConfig,
    ValidationMode,
    load_enforcement_config,
)
from .types import (
    ENFORCE_BLOCKED_TOTAL,
    ENFORCE_MODE_GAUGE,
    ENFORCE_SOFTWARN_TOTAL,
    ENFORCE_TOTAL,
    InvoiceValidationError,
)

if TYPE_CHECKING:
    from .shadow import ShadowCompareResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EnforcementDecision
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnforcementDecision:
    """Result of enforce_validation — caller acts on `action`."""

    action: Literal["pass", "warn", "block"]
    mode: ValidationMode
    errors: tuple[InvoiceValidationError, ...] = ()
    blocker_codes: tuple[str, ...] = ()
    shadow_result: ShadowCompareResult | None = None

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "mode": self.mode.value,
            "errors": [e.to_dict() for e in self.errors],
            "blocker_codes": list(self.blocker_codes),
            "shadow_result": self.shadow_result.to_dict() if self.shadow_result else None,
        }


# ---------------------------------------------------------------------------
# ValidationBlockedError
# ---------------------------------------------------------------------------

class ValidationBlockedError(Exception):
    """Raised when enforce_hard blocks an invoice."""

    def __init__(self, decision: EnforcementDecision) -> None:
        self.decision = decision
        super().__init__(f"Validation blocked: {list(decision.blocker_codes)}")


# ---------------------------------------------------------------------------
# CanonicalInvoice → validator dict adapter
# ---------------------------------------------------------------------------

def canonical_to_validator_dict(canonical: object) -> dict:
    """Convert CanonicalInvoice to the dict format expected by validate().

    Minimal mapping — only totals/lines fields (ETTN/periods/reactive
    are not available on CanonicalInvoice, intentionally skipped).
    """
    result: dict = {}

    # totals
    totals = getattr(canonical, "totals", None)
    if totals is not None:
        t_total = getattr(totals, "total", None)
        t_payable = getattr(totals, "payable", None)
        if t_total is not None or t_payable is not None:
            result["totals"] = {"total": t_total, "payable": t_payable}

    # lines
    raw_lines = getattr(canonical, "lines", None)
    if raw_lines:
        mapped: list[dict] = []
        for line in raw_lines:
            mapped.append({
                "label": getattr(line, "label", ""),
                "qty_kwh": getattr(line, "qty_kwh", None),
                "unit_price": getattr(line, "unit_price", None),
                "amount": getattr(line, "amount", None),
            })
        result["lines"] = mapped

    # taxes_total
    taxes = getattr(canonical, "taxes", None)
    if taxes is not None:
        taxes_total = getattr(taxes, "total", 0)
        result["taxes_total"] = taxes_total if isinstance(taxes_total, (int, float)) else 0

    # vat_amount
    vat = getattr(canonical, "vat", None)
    if vat is not None:
        vat_amount = getattr(vat, "amount", 0)
        result["vat_amount"] = vat_amount if isinstance(vat_amount, (int, float)) else 0

    return result


# ---------------------------------------------------------------------------
# Metric counters (test-only dict, same pattern as Phase E)
# ---------------------------------------------------------------------------

_enforcement_counters: dict[str, int] = {
    ENFORCE_TOTAL: 0,
    ENFORCE_BLOCKED_TOTAL: 0,
    ENFORCE_SOFTWARN_TOTAL: 0,
    ENFORCE_MODE_GAUGE: 0,
}


def get_enforcement_counters() -> dict[str, int]:
    return dict(_enforcement_counters)


def reset_enforcement_counters() -> None:
    for k in _enforcement_counters:
        _enforcement_counters[k] = 0


def record_enforcement_metrics(decision: EnforcementDecision) -> None:
    _enforcement_counters[ENFORCE_TOTAL] += 1
    if decision.action == "block":
        _enforcement_counters[ENFORCE_BLOCKED_TOTAL] += 1
    elif decision.action == "warn":
        _enforcement_counters[ENFORCE_SOFTWARN_TOTAL] += 1


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def enforce_validation(
    invoice_dict: dict,
    old_errors: list[str],
    *,
    invoice_id: str | None = None,
    config: EnforcementConfig | None = None,
) -> EnforcementDecision:
    """Run new validator in the configured mode and return a decision.

    Mode behavior:
      off          → action="pass", nothing runs
      shadow       → Phase E hook runs, action="pass" always
      enforce_soft → validate(); invalid → action="warn"; valid → action="pass"
      enforce_hard → validate(); blocker code → action="block"; advisory only → "warn"; valid → "pass"
    """
    cfg = config if isinstance(config, EnforcementConfig) else load_enforcement_config()

    # --- OFF ---
    if cfg.mode == ValidationMode.OFF:
        decision = EnforcementDecision(action="pass", mode=cfg.mode)
        record_enforcement_metrics(decision)
        return decision

    # --- SHADOW ---
    if cfg.mode == ValidationMode.SHADOW:
        from .shadow import shadow_validate_hook
        from .shadow_config import ShadowConfig, load_config as load_shadow_config

        shadow_cfg = load_shadow_config()
        sr = shadow_validate_hook(invoice_dict, old_errors, invoice_id=invoice_id, config=shadow_cfg)
        decision = EnforcementDecision(action="pass", mode=cfg.mode, shadow_result=sr)
        record_enforcement_metrics(decision)
        return decision

    # --- ENFORCE_SOFT / ENFORCE_HARD ---
    from .validator import validate

    result = validate(invoice_dict)

    if result.valid:
        decision = EnforcementDecision(action="pass", mode=cfg.mode)
        record_enforcement_metrics(decision)
        return decision

    # Invalid — determine blockers
    error_codes = [e.code.value for e in result.errors]
    blockers = [c for c in error_codes if c in cfg.blocker_codes]

    if cfg.mode == ValidationMode.ENFORCE_SOFT:
        decision = EnforcementDecision(
            action="warn",
            mode=cfg.mode,
            errors=tuple(result.errors),
            blocker_codes=tuple(blockers),
        )
        logger.warning(
            "enforcement_warn",
            extra={"invoice_id": invoice_id or "unknown", "codes": error_codes},
        )
        record_enforcement_metrics(decision)
        return decision

    # ENFORCE_HARD
    if blockers:
        decision = EnforcementDecision(
            action="block",
            mode=cfg.mode,
            errors=tuple(result.errors),
            blocker_codes=tuple(blockers),
        )
        record_enforcement_metrics(decision)
        return decision

    # Only advisory codes — warn, don't block
    decision = EnforcementDecision(
        action="warn",
        mode=cfg.mode,
        errors=tuple(result.errors),
        blocker_codes=(),
    )
    logger.warning(
        "enforcement_warn_advisory_only",
        extra={"invoice_id": invoice_id or "unknown", "codes": error_codes},
    )
    record_enforcement_metrics(decision)
    return decision
