"""
Invoice Reconciliation Engine — Quote Comparator.

Fatura maliyeti vs Gelka teklifi karşılaştırması.
IC-1: Tüm hesaplamalar Decimal ile yapılır.

Formüller:
- Fatura enerji: total_kwh × effective_price
- Fatura dağıtım: total_kwh × distribution_unit_price
- Gelka enerji: (PTF_maliyet + YEKDEM_maliyet) × gelka_margin_multiplier
- Gelka dağıtım: aynı dağıtım bedeli (EPDK sabit)
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .schemas import CostComparison, ComparisonConfig

# Recon iç tutarlılık: tüm para/yüzde yuvarlamaları ROUND_HALF_UP (report_builder/v2
# ile aynı Türk muhasebe konvansiyonu). v1 comparator önce context-default HALF_EVEN
# kullanıyordu; bu, recon yanıtı içinde v2 ile ≤0,01 TL tutarsızlık yaratabiliyordu.


def compare_costs(
    total_kwh: Decimal,
    effective_unit_price: Optional[Decimal],
    distribution_unit_price: Optional[Decimal],
    ptf_cost_tl: Decimal,
    yekdem_cost_tl: Decimal,
    config: ComparisonConfig,
) -> Optional[CostComparison]:
    """Fatura maliyeti vs Gelka teklifi karşılaştır.

    Args:
        total_kwh: Dönem toplam tüketimi
        effective_unit_price: Efektif birim fiyat (iskonto uygulanmış, TL/kWh)
        distribution_unit_price: Dağıtım birim fiyatı (TL/kWh)
        ptf_cost_tl: PTF toplam maliyet (TL)
        yekdem_cost_tl: YEKDEM toplam maliyet (TL)
        config: Karşılaştırma konfigürasyonu (marj katsayısı)

    Returns:
        CostComparison or None if insufficient data
    """
    if effective_unit_price is None or distribution_unit_price is None:
        return None

    if total_kwh <= Decimal("0"):
        return None

    # Fatura maliyeti
    invoice_energy = total_kwh * effective_unit_price
    invoice_distribution = total_kwh * distribution_unit_price
    invoice_total = invoice_energy + invoice_distribution

    # Gelka teklifi
    # Enerji: (PTF + YEKDEM) × marj katsayısı
    gelka_energy = (ptf_cost_tl + yekdem_cost_tl) * config.gelka_margin_multiplier
    # Dağıtım: aynı EPDK tarifesi (değişmez)
    gelka_distribution = invoice_distribution
    gelka_total = gelka_energy + gelka_distribution

    # Fark
    diff_tl = invoice_total - gelka_total
    diff_pct = (diff_tl / invoice_total * Decimal("100")) if invoice_total > Decimal("0") else Decimal("0")

    # Mesaj
    if diff_tl > Decimal("0"):
        message = f"Tasarruf potansiyeli: {float(diff_tl.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))} TL (%{float(diff_pct.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))})"
    elif diff_tl < Decimal("0"):
        abs_diff = abs(diff_tl)
        abs_pct = abs(diff_pct)
        message = f"Mevcut tedarikçi avantajlı: {float(abs_diff.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))} TL (%{float(abs_pct.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))})"
    else:
        message = "Maliyet eşit"

    return CostComparison(
        invoice_energy_tl=float(invoice_energy.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        invoice_distribution_tl=float(invoice_distribution.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        invoice_total_tl=float(invoice_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        gelka_energy_tl=float(gelka_energy.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        gelka_distribution_tl=float(gelka_distribution.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        gelka_total_tl=float(gelka_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        diff_tl=float(diff_tl.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
        diff_pct=float(diff_pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
        message=message,
    )
