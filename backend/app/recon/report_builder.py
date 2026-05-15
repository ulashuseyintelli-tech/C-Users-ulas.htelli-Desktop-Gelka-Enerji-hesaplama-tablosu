"""
Invoice Reconciliation Engine — Report Builder.

Tüm pipeline sonuçlarını birleştirip final ReconReport üretir.
IC-1: TL → 2 ondalık, kWh → 3 ondalık yuvarlama.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .schemas import (
    ExcelFormat,
    PeriodResult,
    ReconReport,
)


def build_report(
    format_detected: ExcelFormat,
    total_rows: int,
    successful_rows: int,
    failed_rows: int,
    period_results: list[PeriodResult],
    warnings: list[str],
    multiplier_metadata: Optional[Decimal] = None,
) -> ReconReport:
    """Tüm sonuçları birleştirip final rapor üret.

    Args:
        format_detected: Algılanan Excel formatı
        total_rows: Toplam satır sayısı
        successful_rows: Başarılı parse edilen satır
        failed_rows: Hatalı satır
        period_results: Dönem bazlı sonuçlar
        warnings: Tüm uyarılar (pipeline boyunca toplanan)
        multiplier_metadata: Format A çarpan değeri (bilgi amaçlı)

    Returns:
        ReconReport — JSON serializable final rapor
    """
    # Parse istatistikleri
    parse_stats = {
        "total_rows": total_rows,
        "successful_rows": successful_rows,
        "failed_rows": failed_rows,
    }

    # Çoklu dönem özeti
    summary: Optional[dict] = None
    if len(period_results) > 1:
        summary = _build_multi_period_summary(period_results)

    return ReconReport(
        status="ok",
        format_detected=format_detected,
        parse_stats=parse_stats,
        periods=period_results,
        summary=summary,
        warnings=warnings,
        multiplier_metadata=float(multiplier_metadata) if multiplier_metadata else None,
    )


def _build_multi_period_summary(periods: list[PeriodResult]) -> dict:
    """Çoklu dönem toplam özeti.

    Property 21: summary total_kwh == sum of period totals.
    """
    total_kwh = sum(p.total_kwh for p in periods)
    total_t1 = sum(p.t1_kwh for p in periods)
    total_t2 = sum(p.t2_kwh for p in periods)
    total_t3 = sum(p.t3_kwh for p in periods)

    # PTF/YEKDEM toplamları (mevcut olanlar)
    total_ptf_cost = sum(
        p.ptf_cost.total_ptf_cost_tl for p in periods if p.ptf_cost
    )
    total_yekdem_cost = sum(
        p.yekdem_cost.total_yekdem_cost_tl for p in periods if p.yekdem_cost
    )

    # Fatura vs Gelka toplamları
    total_invoice = sum(
        p.cost_comparison.invoice_total_tl for p in periods if p.cost_comparison
    )
    total_gelka = sum(
        p.cost_comparison.gelka_total_tl for p in periods if p.cost_comparison
    )

    periods_with_quotes = [p for p in periods if not p.quote_blocked]
    periods_blocked = [p for p in periods if p.quote_blocked]

    return {
        "period_count": len(periods),
        "total_kwh": round(total_kwh, 3),
        "t1_kwh": round(total_t1, 3),
        "t2_kwh": round(total_t2, 3),
        "t3_kwh": round(total_t3, 3),
        "total_ptf_cost_tl": round(total_ptf_cost, 2),
        "total_yekdem_cost_tl": round(total_yekdem_cost, 2),
        "total_invoice_tl": round(total_invoice, 2),
        "total_gelka_tl": round(total_gelka, 2),
        "total_diff_tl": round(total_invoice - total_gelka, 2),
        "periods_with_quotes": len(periods_with_quotes),
        "periods_blocked": len(periods_blocked),
    }
