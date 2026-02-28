"""Shadow compare: old CanonicalInvoice.validate() vs new invoice.validation.validate().

Phase D (4.3) — regression detection, no decision impact.
Phase E (4.4) — shadow_validate_hook, sampling, whitelist, metric counters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .error_codes import ValidationErrorCode
from .types import (
    SHADOW_ACTIONABLE_TOTAL,
    SHADOW_METRIC_NAME,
    SHADOW_SAMPLED_TOTAL,
    SHADOW_WHITELISTED_TOTAL,
)
from .validator import validate

if TYPE_CHECKING:
    from app.supplier_profiles import CanonicalInvoice

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known old-validator code prefixes (birebir enum value match)
# ---------------------------------------------------------------------------

_OLD_CODE_PREFIX_MAP: dict[str, ValidationErrorCode] = {
    "PAYABLE_TOTAL_MISMATCH": ValidationErrorCode.PAYABLE_TOTAL_MISMATCH,
    "TOTAL_MISMATCH": ValidationErrorCode.TOTAL_MISMATCH,
    "ZERO_CONSUMPTION": ValidationErrorCode.ZERO_CONSUMPTION,
    "LINE_CROSSCHECK_FAIL": ValidationErrorCode.LINE_CROSSCHECK_FAIL,
}


# ---------------------------------------------------------------------------
# ShadowCompareResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShadowCompareResult:
    """Shadow compare outcome — test assertion and debug reporting."""

    old_valid: bool
    new_valid: bool
    valid_match: bool
    old_codes: frozenset[str]
    new_codes: frozenset[str]
    codes_only_old: frozenset[str]
    codes_only_new: frozenset[str]
    codes_common: frozenset[str]

    def to_dict(self) -> dict:
        return {
            "old_valid": self.old_valid,
            "new_valid": self.new_valid,
            "valid_match": self.valid_match,
            "old_codes": sorted(self.old_codes),
            "new_codes": sorted(self.new_codes),
            "codes_only_old": sorted(self.codes_only_old),
            "codes_only_new": sorted(self.codes_only_new),
            "codes_common": sorted(self.codes_common),
        }


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

def extract_old_codes(errors: list[str]) -> set[str]:
    """Extract code prefixes from old validator error strings.

    "PAYABLE_TOTAL_MISMATCH: payable=100, total=200" → "PAYABLE_TOTAL_MISMATCH"
    Only known prefixes are returned; unknown strings are silently dropped.
    """
    codes: set[str] = set()
    for e in errors:
        prefix = e.split(":")[0].strip()
        if prefix in _OLD_CODE_PREFIX_MAP:
            codes.add(prefix)
    return codes


def build_canonical_invoice(invoice_dict: dict) -> "CanonicalInvoice":
    """Build a CanonicalInvoice from a fixture dict (shadow compare only).

    Mapping:
      totals.total   → Totals.total
      totals.payable → Totals.payable
      lines[i]       → InvoiceLine(code=ACTIVE_ENERGY, ...)
      taxes_total    → TaxBreakdown.other
      vat_amount     → VATInfo.amount

    All lines get LineCode.ACTIVE_ENERGY (fixture assumption).
    """
    from app.supplier_profiles import (
        CanonicalInvoice,
        InvoiceLine,
        LineCode,
        TaxBreakdown,
        Totals,
        VATInfo,
    )

    totals_dict = invoice_dict.get("totals", {})
    if not isinstance(totals_dict, dict):
        totals_dict = {}

    totals = Totals(
        total=totals_dict.get("total"),
        payable=totals_dict.get("payable"),
    )

    raw_lines = invoice_dict.get("lines", [])
    lines: list[InvoiceLine] = []
    if isinstance(raw_lines, list):
        for item in raw_lines:
            if not isinstance(item, dict):
                continue
            lines.append(
                InvoiceLine(
                    code=LineCode.ACTIVE_ENERGY,
                    label=item.get("label", ""),
                    qty_kwh=item.get("qty_kwh"),
                    unit_price=item.get("unit_price"),
                    amount=item.get("amount"),
                )
            )

    taxes_total = invoice_dict.get("taxes_total", 0)
    if not isinstance(taxes_total, (int, float)):
        taxes_total = 0
    taxes = TaxBreakdown(other=taxes_total)

    vat_amount = invoice_dict.get("vat_amount", 0)
    if not isinstance(vat_amount, (int, float)):
        vat_amount = 0
    vat = VATInfo(amount=vat_amount)

    return CanonicalInvoice(
        totals=totals,
        lines=lines,
        taxes=taxes,
        vat=vat,
    )


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def compare_validators(invoice_dict: dict) -> ShadowCompareResult:
    """Run both validators on the same data and compare results.

    1. invoice_dict → CanonicalInvoice via builder
    2. old: CanonicalInvoice.validate() → list[str] → codes
    3. new: validate(invoice_dict) → InvoiceValidationResult → codes
    4. Compare valid + codes
    """
    # Old validator
    canonical = build_canonical_invoice(invoice_dict)
    old_errors = canonical.validate()
    old_valid = len(old_errors) == 0
    old_codes = frozenset(extract_old_codes(old_errors))

    # New validator
    new_result = validate(invoice_dict)
    new_valid = new_result.valid
    new_codes = frozenset(e.code.value for e in new_result.errors)

    return ShadowCompareResult(
        old_valid=old_valid,
        new_valid=new_valid,
        valid_match=(old_valid == new_valid),
        old_codes=old_codes,
        new_codes=new_codes,
        codes_only_old=old_codes - new_codes,
        codes_only_new=new_codes - old_codes,
        codes_common=old_codes & new_codes,
    )


# ---------------------------------------------------------------------------
# Shadow metric counters (Phase E — test-only dict; prod Prometheus is ops task)
# ---------------------------------------------------------------------------

_shadow_counters: dict[str, int] = {
    SHADOW_METRIC_NAME: 0,
    SHADOW_SAMPLED_TOTAL: 0,
    SHADOW_WHITELISTED_TOTAL: 0,
    SHADOW_ACTIONABLE_TOTAL: 0,
}


def get_shadow_counters() -> dict[str, int]:
    """Return a copy of current counter values (test inspection)."""
    return dict(_shadow_counters)


def reset_shadow_counters() -> None:
    """Reset all counters to zero (test cleanup)."""
    for k in _shadow_counters:
        _shadow_counters[k] = 0


def record_shadow_metrics(result: ShadowCompareResult, whitelisted: bool) -> None:
    """Increment appropriate shadow counters after a compare."""
    _shadow_counters[SHADOW_SAMPLED_TOTAL] += 1

    if not result.valid_match:
        _shadow_counters[SHADOW_METRIC_NAME] += 1
        if whitelisted:
            _shadow_counters[SHADOW_WHITELISTED_TOTAL] += 1
        else:
            _shadow_counters[SHADOW_ACTIONABLE_TOTAL] += 1


# ---------------------------------------------------------------------------
# Shadow validate hook (Phase E — post-validation, non-decision)
# ---------------------------------------------------------------------------

def shadow_validate_hook(
    invoice_dict: dict,
    old_errors: list[str],
    *,
    invoice_id: str | None = None,
    config: object | None = None,
) -> ShadowCompareResult | None:
    """Post-validation shadow hook.

    Runs the new validator in shadow mode, compares with old results,
    records metrics. Never raises — returns None on error or skip.

    Args:
        invoice_dict: The invoice data dict.
        old_errors: Error strings from old CanonicalInvoice.validate().
        invoice_id: Optional invoice identifier for deterministic sampling.
        config: Optional ShadowConfig override (for testing). If None, load_config() is used.

    Returns:
        ShadowCompareResult if sampled and successful, None otherwise.
    """
    try:
        from .shadow_config import ShadowConfig, is_whitelisted, load_config, should_sample

        cfg: ShadowConfig = config if isinstance(config, ShadowConfig) else load_config()

        if not should_sample(invoice_id, cfg.sample_rate):
            return None

        result = compare_validators(invoice_dict)
        wl = is_whitelisted(result, cfg.whitelist)
        record_shadow_metrics(result, wl)

        # Log actionable mismatches for debug
        if not result.valid_match and not wl:
            logger.warning(
                "shadow_validation_mismatch",
                extra={
                    "invoice_id": invoice_id or "unknown",
                    "old_valid": result.old_valid,
                    "new_valid": result.new_valid,
                    "old_codes": sorted(result.old_codes),
                    "new_codes": sorted(result.new_codes),
                    "codes_only_old": sorted(result.codes_only_old),
                    "codes_only_new": sorted(result.codes_only_new),
                    "whitelisted": False,
                },
            )

        return result
    except Exception:
        logger.exception("shadow_validate_hook failed")
        return None
