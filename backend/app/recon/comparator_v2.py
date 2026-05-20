"""
Invoice Reconciliation Engine v2 — Markup & Gelka Estimate Computation.

Pure function module. No DB access, no logging, no side effects.
Caller (router) handles None checks and rounding at the Decimal_Boundary.

Formulas (REQ-2):
  supplier_markup_tl   = invoice_total_tl − reference_cost_tl
  supplier_markup_pct  = (invoice_total_tl − reference_cost_tl) / reference_cost_tl × 100
  gelka_estimate_tl    = reference_cost_tl × gelka_margin_multiplier
  potential_savings_tl = invoice_total_tl − gelka_estimate_tl

Wording note:
  The internal field name `supplier_markup_tl` is preserved for backend
  compatibility, but user-facing labels MUST use neutral terminology such as
  "Mevcut fatura ile referans maliyet farkı". The system does NOT claim to
  know the supplier's actual margin (which would require knowledge of hedge
  costs, financing, risk premium, operational overhead, portfolio cost, etc.).
  We only surface the difference between the invoice and the wholesale market
  reference.

Edge cases:
  - reference_cost_tl == 0:
      supplier_markup_pct = None (division undefined)
      supplier_markup_tl = invoice_total_tl
      gelka_estimate_tl = 0
      potential_savings_tl = invoice_total_tl
  - Negative values (markup, savings):
      Returned unchanged. NO clipping, NO abs(), NO capping.
  - gelka_margin_multiplier < 1.0:
      Raises ValueError. Such a value would imply Gelka selling below
      wholesale reference, which is operationally meaningless and likely
      a configuration mistake.

Rounding:
  This function performs NO rounding. All intermediate and final values
  remain raw decimal.Decimal. The caller (router) rounds at the
  Decimal_Boundary using ROUND_HALF_UP.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class MarkupResult:
    """Return type for compute_markup.

    All TL fields are raw Decimal — caller rounds at Decimal_Boundary.

    supplier_markup_tl: Invoice − Reference. May be negative.
    supplier_markup_pct: Percentage form. None when reference_cost_tl == 0.
    gelka_estimate_tl: Reference × multiplier. Always >= 0 (multiplier >= 1.0).
    potential_savings_tl: Invoice − Gelka estimate. May be negative.
    """
    supplier_markup_tl: Decimal
    supplier_markup_pct: Optional[Decimal]
    gelka_estimate_tl: Decimal
    potential_savings_tl: Decimal


def compute_markup(
    invoice_total_tl: Decimal,
    reference_cost_tl: Decimal,
    gelka_margin_multiplier: Decimal,
) -> MarkupResult:
    """Compute markup, gelka estimate, and potential savings.

    REQ-2, REQ-8.5: Three required Decimal parameters.
    REQ-2.6: ValueError when multiplier < 1.0.
    REQ-6.7: When reference_cost_tl == 0, supplier_markup_pct = None.
    REQ-8.6: All arithmetic in Decimal. No rounding.

    Pure function. No side effects. Always returns valid MarkupResult
    (or raises ValueError for invalid multiplier).

    Args:
        invoice_total_tl: Invoice total in TL (must be valid Decimal, not None).
        reference_cost_tl: Reference energy cost in TL (must be valid, not None).
        gelka_margin_multiplier: Gelka offer multiplier (must be >= 1.0).

    Returns:
        MarkupResult with raw Decimal values (no rounding).

    Raises:
        ValueError: If gelka_margin_multiplier < Decimal("1.0").
    """
    # ── Validate multiplier (REQ-2.6) ────────────────────────────────────────
    if gelka_margin_multiplier < Decimal("1.0"):
        raise ValueError(
            f"gelka_margin_multiplier must be >= 1.0, got {gelka_margin_multiplier}. "
            f"Values below 1.0 would imply Gelka selling below the wholesale "
            f"reference, which is operationally meaningless."
        )

    # ── supplier_markup_tl ───────────────────────────────────────────────────
    # Invoice − Reference. May be negative when invoice is below reference.
    supplier_markup_tl = invoice_total_tl - reference_cost_tl

    # ── supplier_markup_pct (REQ-6.7: None when ref_cost == 0) ───────────────
    if reference_cost_tl == Decimal("0"):
        supplier_markup_pct: Optional[Decimal] = None
    else:
        supplier_markup_pct = (
            supplier_markup_tl / reference_cost_tl * Decimal("100")
        )

    # ── gelka_estimate_tl (multiplier guaranteed >= 1.0 above) ───────────────
    gelka_estimate_tl = reference_cost_tl * gelka_margin_multiplier

    # ── potential_savings_tl (may be negative if invoice < gelka estimate) ───
    potential_savings_tl = invoice_total_tl - gelka_estimate_tl

    return MarkupResult(
        supplier_markup_tl=supplier_markup_tl,
        supplier_markup_pct=supplier_markup_pct,
        gelka_estimate_tl=gelka_estimate_tl,
        potential_savings_tl=potential_savings_tl,
    )
