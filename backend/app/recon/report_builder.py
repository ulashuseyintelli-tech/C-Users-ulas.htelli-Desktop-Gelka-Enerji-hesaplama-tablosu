"""
Invoice Reconciliation Engine — Report Builder.

Tüm pipeline sonuçlarını birleştirip final ReconReport üretir.
IC-1: TL → 2 ondalık, kWh → 3 ondalık yuvarlama.

v2 (cost headline):
- Multi-period summary v2 alanlarını ekler (REQ-7.8, REQ-10.2)
- Decimal_Boundary helper'ları (TL ve % için ROUND_HALF_UP) burada yaşar
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .schemas import (
    ExcelFormat,
    PeriodResult,
    ReconReport,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Decimal_Boundary helpers (v2 — TL ve % için ROUND_HALF_UP)
#
# Suffix `_half_up` ileride kafa karışıklığını engellemek için zorunludur.
# Banker's rounding (round half-even), 4dp energy price, MWh, multiplier veya
# scientific precision gibi başka boundary'ler için bu helper'lar kullanılmaz.
# ═══════════════════════════════════════════════════════════════════════════════


def _round_currency_tl_half_up(value: Decimal) -> float:
    """Decimal → float at 2dp with ROUND_HALF_UP (Türk muhasebe konvansiyonu).

    YALNIZCA TL para birimi değerleri için kullanılır. Aşağıdakiler için
    KULLANILMAMALIDIR:
      - yüzdeler (`_round_pct_half_up` kullan)
      - multiplier (config Decimal değerleri, yuvarlanmaz)
      - kWh (3dp hassasiyet, farklı kural)
      - MWh (farklı birim)
      - banker's rounding gerektiren her şey

    Caller, input'un bir TL Decimal olduğundan emin olmalıdır.
    """
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _round_pct_half_up(value: Decimal) -> float:
    """Decimal → float at 2dp with ROUND_HALF_UP for yüzde değerleri.

    YALNIZCA yüzde değerleri (markup pct vb.) için kullanılır. Currency
    ile aynı hassasiyet (2dp) ama TL/kWh/MWh/multiplier alanlarına yanlışlıkla
    uygulanmaması için ayrı bir helper olarak tutulur.
    """
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-period summary
# ═══════════════════════════════════════════════════════════════════════════════


def _build_multi_period_summary(periods: list[PeriodResult]) -> dict:
    """Çoklu dönem toplam özeti.

    v1 alanları (preserved):
      - period_count, total_kwh, t1/t2/t3_kwh
      - total_ptf_cost_tl, total_yekdem_cost_tl
      - total_invoice_tl, total_gelka_tl, total_diff_tl
      - periods_with_quotes, periods_blocked

    v2 alanları (REQ-7.8, REQ-10.2):
      - valid_period_count: reference_energy_cost_tl != None olan dönem sayısı
      - partial_period_count: reference_energy_cost_tl == None olan dönem sayısı
      - total_period_count: tüm dönemler
      - total_reference_energy_cost_tl: yalnız valid dönemlerin toplamı (None
        if no valid periods)
      - total_supplier_markup_tl: yalnız markup'ı non-null dönemlerin toplamı
      - total_gelka_estimate_tl: yalnız gelka_estimate'i non-null dönemlerin toplamı
      - total_potential_savings_tl: yalnız savings'i non-null dönemlerin toplamı
      - annual_projection: yıllık projeksiyon (None if no valid periods)
        Kritik: bu "gerçek yıllık tasarruf" DEĞİLDİR. "tahmini yıllık projeksiyon".
        FE bu etiketi olduğu gibi göstermek zorundadır.

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

    # Fatura vs Gelka toplamları (v1 cost_comparison bazlı)
    total_invoice = sum(
        p.cost_comparison.invoice_total_tl for p in periods if p.cost_comparison
    )
    total_gelka = sum(
        p.cost_comparison.gelka_total_tl for p in periods if p.cost_comparison
    )

    periods_with_quotes = [p for p in periods if not p.quote_blocked]
    periods_blocked = [p for p in periods if p.quote_blocked]

    # ── v2: count fields ────────────────────────────────────────────────────
    # valid = reference_energy_cost_tl computed (fail-closed semantic)
    valid_periods = [p for p in periods if p.reference_energy_cost_tl is not None]
    partial_periods = [p for p in periods if p.reference_energy_cost_tl is None]

    valid_period_count = len(valid_periods)
    partial_period_count = len(partial_periods)
    total_period_count = len(periods)

    # ── v2: sum fields (Decimal arithmetic, ROUND_HALF_UP at boundary) ──────
    # Markup-side fields (markup_tl, gelka_estimate_tl, potential_savings_tl)
    # may be null even if reference_energy_cost_tl is present (no invoice).
    # Sum only what's actually populated.

    total_reference_energy_cost_tl: Optional[float] = None
    if valid_period_count > 0:
        ref_sum = sum(
            (Decimal(str(p.reference_energy_cost_tl)) for p in valid_periods),
            Decimal("0"),
        )
        total_reference_energy_cost_tl = _round_currency_tl_half_up(ref_sum)

    markup_periods = [p for p in periods if p.supplier_markup_tl is not None]
    total_supplier_markup_tl: Optional[float] = None
    if markup_periods:
        markup_sum = sum(
            (Decimal(str(p.supplier_markup_tl)) for p in markup_periods),
            Decimal("0"),
        )
        total_supplier_markup_tl = _round_currency_tl_half_up(markup_sum)

    gelka_periods = [p for p in periods if p.gelka_estimate_tl is not None]
    total_gelka_estimate_tl: Optional[float] = None
    if gelka_periods:
        gelka_sum = sum(
            (Decimal(str(p.gelka_estimate_tl)) for p in gelka_periods),
            Decimal("0"),
        )
        total_gelka_estimate_tl = _round_currency_tl_half_up(gelka_sum)

    savings_periods = [p for p in periods if p.potential_savings_tl is not None]
    total_potential_savings_tl: Optional[float] = None
    if savings_periods:
        savings_sum = sum(
            (Decimal(str(p.potential_savings_tl)) for p in savings_periods),
            Decimal("0"),
        )
        total_potential_savings_tl = _round_currency_tl_half_up(savings_sum)

    # ── v2: annual projection ──────────────────────────────────────────────
    # Yalnız valid_period_count > 0 ise hesaplanır. Faktör = 12 / n.
    # ASLA "gerçek yıllık tasarruf" gibi adlandırılmaz; etiket FE'ye iletilir.
    # Negatif tasarruf clamp'lenmez — referans maliyet farkı modelimizin doğal
    # bir sonucu olarak olduğu gibi geçer.
    annual_projection: Optional[dict] = None
    if valid_period_count > 0:
        factor = Decimal("12") / Decimal(valid_period_count)

        annualized_ref = _round_currency_tl_half_up(
            sum(
                (Decimal(str(p.reference_energy_cost_tl)) for p in valid_periods),
                Decimal("0"),
            )
            * factor
        )

        annualized_markup: Optional[float] = None
        if markup_periods:
            annualized_markup = _round_currency_tl_half_up(
                sum(
                    (Decimal(str(p.supplier_markup_tl)) for p in markup_periods),
                    Decimal("0"),
                )
                * (Decimal("12") / Decimal(len(markup_periods)))
            )

        annualized_savings: Optional[float] = None
        if savings_periods:
            annualized_savings = _round_currency_tl_half_up(
                sum(
                    (Decimal(str(p.potential_savings_tl)) for p in savings_periods),
                    Decimal("0"),
                )
                * (Decimal("12") / Decimal(len(savings_periods)))
            )

        annual_projection = {
            "based_on_periods": valid_period_count,
            "label": "tahmini yıllık projeksiyon",
            "annualized_reference_cost_tl": annualized_ref,
            "annualized_supplier_markup_tl": annualized_markup,
            "annualized_potential_savings_tl": annualized_savings,
        }

    return {
        # v1 (unchanged)
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
        # v2 — count fields
        "valid_period_count": valid_period_count,
        "partial_period_count": partial_period_count,
        "total_period_count": total_period_count,
        # v2 — sum fields (None when no underlying period contributes)
        "total_reference_energy_cost_tl": total_reference_energy_cost_tl,
        "total_supplier_markup_tl": total_supplier_markup_tl,
        "total_gelka_estimate_tl": total_gelka_estimate_tl,
        "total_potential_savings_tl": total_potential_savings_tl,
        # v2 — annual projection (None when no valid periods)
        "annual_projection": annual_projection,
    }
