"""
Pricing Risk Engine — T1/T2/T3 Zaman Dilimi Motoru.

Saat sınıflandırması:
- T1 (Gündüz): 06:00–16:59
- T2 (Puant):  17:00–21:59
- T3 (Gece):   22:00–05:59

Requirements: 6.1, 6.2, 6.3, 6.4
"""

from __future__ import annotations

from .models import TimeZone, TimeZoneBreakdown
from .excel_parser import ParsedMarketRecord, ParsedConsumptionRecord


# ═══════════════════════════════════════════════════════════════════════════════
# Zaman Dilimi Etiketleri
# ═══════════════════════════════════════════════════════════════════════════════

_ZONE_LABELS: dict[TimeZone, str] = {
    TimeZone.T1: "Gündüz (06:00-16:59)",
    TimeZone.T2: "Puant (17:00-21:59)",
    TimeZone.T3: "Gece (22:00-05:59)",
}


def classify_hour(hour: int) -> TimeZone:
    """Saati T1/T2/T3 zaman dilimine sınıflandır.

    Args:
        hour: Saat değeri (0–23).

    Returns:
        TimeZone enum değeri.

    Raises:
        ValueError: Geçersiz saat değeri (0–23 dışı).
    """
    if not (0 <= hour <= 23):
        raise ValueError(f"Geçersiz saat değeri: {hour}. Beklenen: 0–23")

    if 6 <= hour <= 16:
        return TimeZone.T1
    elif 17 <= hour <= 21:
        return TimeZone.T2
    else:
        # 22, 23, 0, 1, 2, 3, 4, 5
        return TimeZone.T3


def calculate_time_zone_breakdown(
    market_records: list[ParsedMarketRecord],
    consumption_records: list[ParsedConsumptionRecord],
    yekdem_tl_per_mwh: float = 0.0,
) -> dict[str, TimeZoneBreakdown]:
    """T1/T2/T3 zaman dilimi dağılımı hesapla.

    Her dilim için toplam tüketim, ağırlıklı PTF/SMF ve toplam maliyet hesaplar.
    Eşleştirme (date, hour) bazında yapılır.

    Args:
        market_records: Saatlik piyasa verileri.
        consumption_records: Saatlik tüketim verileri.
        yekdem_tl_per_mwh: YEKDEM bedeli (TL/MWh), maliyet hesabına dahil edilir.

    Returns:
        T1/T2/T3 anahtarlı TimeZoneBreakdown sözlüğü.
    """
    # Piyasa verilerini (date, hour) → record olarak indeksle
    market_index: dict[tuple[str, int], ParsedMarketRecord] = {}
    for mr in market_records:
        market_index[(mr.date, mr.hour)] = mr

    # Her dilim için akümülatörler
    zone_consumption: dict[TimeZone, float] = {tz: 0.0 for tz in TimeZone}
    zone_ptf_weighted_sum: dict[TimeZone, float] = {tz: 0.0 for tz in TimeZone}
    zone_smf_weighted_sum: dict[TimeZone, float] = {tz: 0.0 for tz in TimeZone}
    zone_cost: dict[TimeZone, float] = {tz: 0.0 for tz in TimeZone}

    for cr in consumption_records:
        mr = market_index.get((cr.date, cr.hour))
        if mr is None:
            continue  # Eşleşmeyen tüketim kaydı — atla

        tz = classify_hour(cr.hour)
        kwh = cr.consumption_kwh

        zone_consumption[tz] += kwh
        zone_ptf_weighted_sum[tz] += kwh * mr.ptf_tl_per_mwh
        zone_smf_weighted_sum[tz] += kwh * mr.smf_tl_per_mwh
        # Maliyet: kWh × (PTF + YEKDEM) / 1000
        zone_cost[tz] += kwh * (mr.ptf_tl_per_mwh + yekdem_tl_per_mwh) / 1000.0

    # Toplam tüketim
    total_consumption = sum(zone_consumption.values())

    result: dict[str, TimeZoneBreakdown] = {}
    for tz in TimeZone:
        cons = zone_consumption[tz]
        if cons > 0:
            weighted_ptf = round(zone_ptf_weighted_sum[tz] / cons, 2)
            weighted_smf = round(zone_smf_weighted_sum[tz] / cons, 2)
        else:
            weighted_ptf = 0.0
            weighted_smf = 0.0

        consumption_pct = round((cons / total_consumption * 100) if total_consumption > 0 else 0.0, 2)

        result[tz.value] = TimeZoneBreakdown(
            label=_ZONE_LABELS[tz],
            consumption_kwh=round(cons, 4),
            consumption_pct=consumption_pct,
            weighted_ptf_tl_per_mwh=weighted_ptf,
            weighted_smf_tl_per_mwh=weighted_smf,
            total_cost_tl=round(zone_cost[tz], 2),
        )

    return result
