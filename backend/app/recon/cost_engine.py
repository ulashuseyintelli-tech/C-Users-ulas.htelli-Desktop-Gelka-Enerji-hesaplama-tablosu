"""
Invoice Reconciliation Engine — PTF/YEKDEM Cost Engine.

IC-1: Tüm hesaplamalar Decimal ile yapılır.
SoT: hourly_market_prices (PTF), monthly_yekdem_prices (YEKDEM).
YASAK: market_reference_prices kullanılmaz.

Fail-closed: PTF/YEKDEM tamamen eksikse quote_blocked=True,
ama parse+recon raporu yine döner.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from ..pricing.schemas import HourlyMarketPrice, MonthlyYekdemPrice
from .schemas import HourlyRecord, PtfCostResult, YekdemCostResult


def calculate_ptf_cost(
    records: list[HourlyRecord],
    period: str,
    db: Session,
) -> PtfCostResult:
    """Saatlik PTF ile maliyet hesapla.

    SoT: hourly_market_prices tablosu (canonical).
    Formula: saat_maliyet = consumption_kwh × (ptf_tl_per_mwh / 1000)

    Args:
        records: Dönemin saatlik tüketim kayıtları
        period: "YYYY-MM" formatında dönem
        db: SQLAlchemy session

    Returns:
        PtfCostResult with cost totals and missing hour stats
    """
    if not records:
        return PtfCostResult(
            total_ptf_cost_tl=0.0,
            weighted_avg_ptf_tl_per_mwh=0.0,
            hours_matched=0,
            hours_missing_ptf=0,
            missing_ptf_pct=0.0,
            ptf_data_sufficient=False,
            warning="Tüketim kaydı yok",
        )

    # Load PTF data from canonical source: hourly_market_prices
    # YASAK: market_reference_prices kullanılmaz (SoT steering)
    ptf_rows = (
        db.query(HourlyMarketPrice)
        .filter(
            HourlyMarketPrice.period == period,
            HourlyMarketPrice.is_active == 1,
        )
        .all()
    )

    # Build (date, hour) → ptf_tl_per_mwh index
    ptf_index: dict[tuple[str, int], Decimal] = {}
    for row in ptf_rows:
        ptf_index[(row.date, row.hour)] = Decimal(str(row.ptf_tl_per_mwh))

    # Calculate hourly costs
    total_cost = Decimal("0")
    total_kwh_matched = Decimal("0")
    hours_matched = 0
    hours_missing = 0

    for record in records:
        ptf = ptf_index.get((record.date, record.hour))
        if ptf is None:
            hours_missing += 1
            continue

        # IC-1: Decimal arithmetic
        hour_cost = record.consumption_kwh * (ptf / Decimal("1000"))
        total_cost += hour_cost
        total_kwh_matched += record.consumption_kwh
        hours_matched += 1

    total_records = len(records)
    missing_pct = (hours_missing / total_records * 100) if total_records > 0 else 0.0

    # Weighted average PTF
    if total_kwh_matched > Decimal("0"):
        weighted_avg = total_cost / total_kwh_matched * Decimal("1000")
    else:
        weighted_avg = Decimal("0")

    # Warning if >10% missing
    warning: Optional[str] = None
    if missing_pct > 10:
        warning = "Yetersiz PTF verisi — maliyet hesaplaması güvenilir değil"

    # PTF data sufficient = not completely missing
    ptf_data_sufficient = hours_matched > 0

    return PtfCostResult(
        total_ptf_cost_tl=float(total_cost.quantize(Decimal("0.01"))),
        weighted_avg_ptf_tl_per_mwh=float(weighted_avg.quantize(Decimal("0.01"))),
        hours_matched=hours_matched,
        hours_missing_ptf=hours_missing,
        missing_ptf_pct=round(missing_pct, 1),
        ptf_data_sufficient=ptf_data_sufficient,
        warning=warning,
    )


def get_yekdem_cost(
    period: str,
    total_kwh: Decimal,
    db: Session,
) -> YekdemCostResult:
    """YEKDEM bedelini hesapla.

    SoT: monthly_yekdem_prices tablosu (canonical).
    Formula: yekdem_maliyet = total_kwh × (yekdem_tl_per_mwh / 1000)

    Args:
        period: "YYYY-MM" formatında dönem
        total_kwh: Dönem toplam tüketimi (Decimal)
        db: SQLAlchemy session

    Returns:
        YekdemCostResult
    """
    # Load YEKDEM from canonical source: monthly_yekdem_prices
    yekdem_row = (
        db.query(MonthlyYekdemPrice)
        .filter(MonthlyYekdemPrice.period == period)
        .first()
    )

    if yekdem_row is None:
        return YekdemCostResult(
            yekdem_tl_per_mwh=0.0,
            total_yekdem_cost_tl=0.0,
            available=False,
        )

    yekdem_rate = Decimal(str(yekdem_row.yekdem_tl_per_mwh))
    # IC-1: Decimal arithmetic
    yekdem_cost = total_kwh * (yekdem_rate / Decimal("1000"))

    return YekdemCostResult(
        yekdem_tl_per_mwh=float(yekdem_rate),
        total_yekdem_cost_tl=float(yekdem_cost.quantize(Decimal("0.01"))),
        available=True,
    )


def check_quote_eligibility(
    ptf_result: PtfCostResult,
    yekdem_result: YekdemCostResult,
) -> tuple[bool, Optional[str]]:
    """Fail-closed: PTF/YEKDEM eksikse teklif üretimini engelle.

    Returns:
        (quote_blocked, quote_block_reason)
        - quote_blocked=True → teklif üretilemez
        - quote_blocked=False → teklif üretilebilir
    """
    reasons: list[str] = []

    if not ptf_result.ptf_data_sufficient:
        reasons.append("PTF verisi tamamen eksik (hourly_market_prices)")

    if not yekdem_result.available:
        reasons.append("YEKDEM verisi eksik (monthly_yekdem_prices)")

    if reasons:
        return True, "; ".join(reasons)

    return False, None
