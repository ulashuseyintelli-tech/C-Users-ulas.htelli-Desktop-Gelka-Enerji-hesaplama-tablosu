"""
Invoice Reconciliation Engine v2 — Always-On Reference Energy Cost.

Computes the EPİAŞ PTF + YEKDEM based wholesale market reference energy cost
for a period. This is the v2 "always-on" path that runs regardless of whether
invoice data is provided.

SoT: hourly_market_prices (PTF), monthly_yekdem_prices (YEKDEM).
YASAK: market_reference_prices kullanılmaz.

Fail-closed semantics (REQ-5):
- If ANY parsed hourly record has no PTF match → reference_energy_cost_tl = None
- If YEKDEM row missing for period → reference_energy_cost_tl = None
- NO interpolation, NO normalization, NO partial computation.

Rounding strategy:
- All arithmetic in decimal.Decimal — NO intermediate rounding.
- Result returned as raw Decimal. Caller rounds at Decimal_Boundary (router).
- Final rounding: quantize("0.01", ROUND_HALF_UP) — done by caller.

Terminology:
- NEVER use "gerçek maliyet", "actual cost", or "true cost" in logs or messages.
- This is a "referans enerji maliyeti" (reference energy cost).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..pricing.schemas import HourlyMarketPrice, MonthlyYekdemPrice
from .schemas import HourlyRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReferenceEnergyCostResult:
    """Return type for compute_period_reference_cost.

    reference_energy_cost_tl: Full Decimal result, or None if fail-closed.
    ptf_hours_missing: Total count of records without PTF match.
    yekdem_missing: True if YEKDEM row absent for period.
    total_kwh: Sum of all record consumption (Decimal).
    computed_hours: Number of records that were successfully matched to PTF.
    """
    reference_energy_cost_tl: Optional[Decimal]
    ptf_hours_missing: int
    yekdem_missing: bool
    total_kwh: Decimal
    computed_hours: int


def compute_period_reference_cost(
    records: list[HourlyRecord],
    period: str,
    db: Session,
) -> ReferenceEnergyCostResult:
    """Compute always-on reference energy cost for a period.

    REQ-1, REQ-8.4: No invoice parameter accepted.
    REQ-1.3/1.4/1.5: Reads exclusively from hourly_market_prices + monthly_yekdem_prices.
    REQ-5.1: Single missing PTF hour → entire period is None.
    REQ-5.2: Missing YEKDEM → entire period is None.
    REQ-8.6: All arithmetic in Decimal. No intermediate rounding.
    REQ-8.7: multiplier metadata is NEVER applied to consumption.

    Args:
        records: Parsed hourly records for this period (from splitter output).
        period: "YYYY-MM" period identifier.
        db: SQLAlchemy session for DB queries.

    Returns:
        ReferenceEnergyCostResult with full Decimal cost or None.
    """
    # ── Early exit: empty records ────────────────────────────────────────────
    if not records:
        _emit_log(
            period=period,
            records_count=0,
            computed_hours=0,
            ptf_hours_missing=0,
            yekdem_missing=False,
            total_kwh=Decimal("0"),
            success=False,
            reason="empty_records",
        )
        return ReferenceEnergyCostResult(
            reference_energy_cost_tl=None,
            ptf_hours_missing=0,
            yekdem_missing=False,
            total_kwh=Decimal("0"),
            computed_hours=0,
        )

    # ── 1. Bulk-load PTF data (single query per period) ──────────────────────
    ptf_rows = (
        db.query(HourlyMarketPrice)
        .filter(
            HourlyMarketPrice.period == period,
            HourlyMarketPrice.is_active == 1,
        )
        .all()
    )

    # Build (date, hour) → Decimal(ptf_tl_per_mwh) index
    ptf_index: dict[tuple[str, int], Decimal] = {}
    for row in ptf_rows:
        ptf_index[(row.date, row.hour)] = Decimal(str(row.ptf_tl_per_mwh))

    # ── 2. Load YEKDEM (single query per period) ─────────────────────────────
    yekdem_row = (
        db.query(MonthlyYekdemPrice)
        .filter(MonthlyYekdemPrice.period == period)
        .first()
    )

    yekdem_missing = yekdem_row is None

    # ── 3. Match records to PTF — fail-closed on first gap ───────────────────
    ptf_hours_missing = 0
    ptf_component = Decimal("0")
    total_kwh = Decimal("0")
    computed_hours = 0

    for record in records:
        total_kwh += record.consumption_kwh
        ptf = ptf_index.get((record.date, record.hour))
        if ptf is None:
            ptf_hours_missing += 1
        else:
            # Accumulate PTF cost — NO intermediate rounding
            ptf_component += record.consumption_kwh * ptf / Decimal("1000")
            computed_hours += 1

    # ── 4. Fail-closed evaluation ────────────────────────────────────────────
    if ptf_hours_missing > 0:
        _emit_log(
            period=period,
            records_count=len(records),
            computed_hours=computed_hours,
            ptf_hours_missing=ptf_hours_missing,
            yekdem_missing=yekdem_missing,
            total_kwh=total_kwh,
            success=False,
            reason="ptf_hours_missing",
        )
        return ReferenceEnergyCostResult(
            reference_energy_cost_tl=None,
            ptf_hours_missing=ptf_hours_missing,
            yekdem_missing=yekdem_missing,
            total_kwh=total_kwh,
            computed_hours=computed_hours,
        )

    if yekdem_missing:
        _emit_log(
            period=period,
            records_count=len(records),
            computed_hours=computed_hours,
            ptf_hours_missing=0,
            yekdem_missing=True,
            total_kwh=total_kwh,
            success=False,
            reason="yekdem_missing",
        )
        return ReferenceEnergyCostResult(
            reference_energy_cost_tl=None,
            ptf_hours_missing=0,
            yekdem_missing=True,
            total_kwh=total_kwh,
            computed_hours=computed_hours,
        )

    # ── 5. Compute YEKDEM component ─────────────────────────────────────────
    yekdem_rate = Decimal(str(yekdem_row.yekdem_tl_per_mwh))
    yekdem_component = total_kwh * yekdem_rate / Decimal("1000")

    # ── 6. Final reference cost (NO rounding — caller rounds) ────────────────
    reference_energy_cost_tl = ptf_component + yekdem_component

    # ── 7. Structured log ────────────────────────────────────────────────────
    _emit_log(
        period=period,
        records_count=len(records),
        computed_hours=computed_hours,
        ptf_hours_missing=0,
        yekdem_missing=False,
        total_kwh=total_kwh,
        success=True,
        reason=None,
    )

    return ReferenceEnergyCostResult(
        reference_energy_cost_tl=reference_energy_cost_tl,
        ptf_hours_missing=0,
        yekdem_missing=False,
        total_kwh=total_kwh,
        computed_hours=computed_hours,
    )


def _emit_log(
    *,
    period: str,
    records_count: int,
    computed_hours: int,
    ptf_hours_missing: int,
    yekdem_missing: bool,
    total_kwh: Decimal,
    success: bool,
    reason: Optional[str],
) -> None:
    """Emit structured INFO log for reference cost computation (NFR-2).

    No PII (customer name, supplier name, tariff group) included.
    No "gerçek maliyet" / "actual cost" / "true cost" in message.
    """
    logger.info(
        "reference_cost_compute",
        extra={
            "event": "reference_cost_compute",
            "period": period,
            "records": records_count,
            "computed_hours": computed_hours,
            "ptf_hours_missing": ptf_hours_missing,
            "yekdem_missing": yekdem_missing,
            "total_kwh": float(total_kwh),
            "success": success,
            "reason": reason,
        },
    )
