"""
Pricing Risk Engine — Hesaplama Motoru.

Ağırlıklı PTF/SMF hesaplama ve saatlik maliyet/marj hesaplama.

KRİTİK: kWh vs MWh dönüşümü
    TL = kWh × (TL/MWh) / 1000
Bu bölme işlemi her maliyet hesabında ZORUNLUDUR.

Requirements: 7.1–7.6, 8.1–8.8, 9.1–9.3, 14.1–14.2
"""

from __future__ import annotations

from .models import (
    WeightedPriceResult,
    HourlyCostResult,
    HourlyCostEntry,
    ImbalanceParams,
)
from .excel_parser import ParsedMarketRecord, ParsedConsumptionRecord
from .time_zones import classify_hour
from .imbalance import calculate_imbalance_cost


def calculate_weighted_prices(
    market_records: list[ParsedMarketRecord],
    consumption_records: list[ParsedConsumptionRecord],
) -> WeightedPriceResult:
    """Ağırlıklı PTF ve SMF hesapla.

    Formül:
        Ağırlıklı_PTF = Σ(kWh_h × PTF_h) / Σ(kWh_h)
        Ağırlıklı_SMF = Σ(kWh_h × SMF_h) / Σ(kWh_h)

    Args:
        market_records: Saatlik piyasa verileri.
        consumption_records: Saatlik tüketim verileri.

    Returns:
        WeightedPriceResult: Ağırlıklı fiyat hesaplama sonucu.

    Raises:
        ValueError: Toplam tüketim sıfır ise.
    """
    # Piyasa verilerini (date, hour) → record olarak indeksle
    market_index: dict[tuple[str, int], ParsedMarketRecord] = {}
    for mr in market_records:
        market_index[(mr.date, mr.hour)] = mr

    # Akümülatörler
    total_consumption_kwh = 0.0
    ptf_weighted_sum = 0.0
    smf_weighted_sum = 0.0
    ptf_sum = 0.0
    smf_sum = 0.0
    matched_hours = 0

    for cr in consumption_records:
        mr = market_index.get((cr.date, cr.hour))
        if mr is None:
            continue  # Eşleşmeyen kayıt — atla

        kwh = cr.consumption_kwh
        total_consumption_kwh += kwh
        ptf_weighted_sum += kwh * mr.ptf_tl_per_mwh
        smf_weighted_sum += kwh * mr.smf_tl_per_mwh
        ptf_sum += mr.ptf_tl_per_mwh
        smf_sum += mr.smf_tl_per_mwh
        matched_hours += 1

    # Eşleşen saat kontrolü (sıfıra bölme korumasından önce)
    if matched_hours == 0:
        raise ValueError(
            "Piyasa verisi ile tüketim profili arasında eşleşen saat bulunamadı. "
            "Lütfen dönem ve tarih aralıklarını kontrol edin."
        )

    # Sıfıra bölme koruması
    if total_consumption_kwh == 0:
        raise ValueError(
            "Toplam tüketim sıfır — ağırlıklı fiyat hesaplanamaz. "
            "Lütfen tüketim profilini kontrol edin."
        )

    weighted_ptf = round(ptf_weighted_sum / total_consumption_kwh, 2)
    weighted_smf = round(smf_weighted_sum / total_consumption_kwh, 2)
    arithmetic_avg_ptf = round(ptf_sum / matched_hours, 2)
    arithmetic_avg_smf = round(smf_sum / matched_hours, 2)

    # Toplam maliyet: Σ(kWh × PTF / 1000)  — kWh→MWh dönüşümü
    total_cost_tl = round(ptf_weighted_sum / 1000.0, 2)

    return WeightedPriceResult(
        weighted_ptf_tl_per_mwh=weighted_ptf,
        weighted_smf_tl_per_mwh=weighted_smf,
        arithmetic_avg_ptf=arithmetic_avg_ptf,
        arithmetic_avg_smf=arithmetic_avg_smf,
        total_consumption_kwh=round(total_consumption_kwh, 4),
        total_cost_tl=total_cost_tl,
        hours_count=matched_hours,
    )


def calculate_hourly_costs(
    market_records: list[ParsedMarketRecord],
    consumption_records: list[ParsedConsumptionRecord],
    yekdem_tl_per_mwh: float,
    multiplier: float,
    imbalance_params: ImbalanceParams,
    dealer_commission_pct: float = 0.0,
    distribution_unit_price_tl_per_kwh: float = 0.0,
) -> HourlyCostResult:
    """Saatlik maliyet, satış fiyatı ve marj hesapla.

    KRİTİK FORMÜLLER:
        base_cost_tl = consumption_kwh × (ptf_tl_per_mwh + yekdem_tl_per_mwh) / 1000
        sales_price_tl = consumption_kwh × (weighted_ptf + yekdem) × multiplier / 1000
        margin_tl = sales_price_tl - base_cost_tl

    Dual Margin Model (v3):
        gross_margin_energy = total_sales - total_base_cost
        gross_margin_total  = total_sales - total_base_cost - distribution_cost_total

    Safety Guards (v3.1):
        dealer_commission = max(0, min(dealer_commission, gross_margin_energy))
        imbalance_cost_per_mwh = max(calculated, weighted_ptf * RISK_FLOOR)

    Args:
        market_records: Saatlik piyasa verileri.
        consumption_records: Saatlik tüketim verileri.
        yekdem_tl_per_mwh: YEKDEM bedeli (TL/MWh).
        multiplier: Katsayı (≥ 1.0).
        imbalance_params: Dengesizlik parametreleri.
        dealer_commission_pct: Bayi komisyon yüzdesi (0–100, varsayılan 0).
        distribution_unit_price_tl_per_kwh: Dağıtım birim fiyatı (TL/kWh, varsayılan 0).

    Returns:
        HourlyCostResult: Saatlik maliyet hesaplama sonucu.
    """
    # Önce ağırlıklı fiyatları hesapla (satış fiyatı için gerekli)
    weighted_result = calculate_weighted_prices(market_records, consumption_records)
    weighted_ptf = weighted_result.weighted_ptf_tl_per_mwh
    weighted_smf = weighted_result.weighted_smf_tl_per_mwh

    # Dengesizlik maliyeti hesapla (TL/MWh)
    calculated_imbalance_per_mwh = calculate_imbalance_cost(
        weighted_ptf, weighted_smf, imbalance_params
    )

    # Safety Guard: Imbalance floor (per-MWh bazlı)
    RISK_FLOOR = 0.01
    imbalance_cost_per_mwh = max(calculated_imbalance_per_mwh, weighted_ptf * RISK_FLOOR)

    # Enerji maliyeti = Ağırlıklı PTF + YEKDEM (satış fiyatı hesabı için)
    energy_cost_tl_per_mwh = weighted_ptf + yekdem_tl_per_mwh

    # Piyasa verilerini (date, hour) → record olarak indeksle
    market_index: dict[tuple[str, int], ParsedMarketRecord] = {}
    for mr in market_records:
        market_index[(mr.date, mr.hour)] = mr

    # Saatlik maliyet hesaplama
    hour_costs: list[HourlyCostEntry] = []
    total_base_cost = 0.0
    total_sales = 0.0

    for cr in consumption_records:
        mr = market_index.get((cr.date, cr.hour))
        if mr is None:
            continue  # Eşleşmeyen kayıt — atla

        kwh = cr.consumption_kwh

        # Baz maliyet: kWh × (PTF + YEKDEM) / 1000
        base_cost_tl = kwh * (mr.ptf_tl_per_mwh + yekdem_tl_per_mwh) / 1000.0

        # Satış fiyatı: kWh × (Ağırlıklı_PTF + YEKDEM) × Katsayı / 1000
        sales_price_tl = kwh * energy_cost_tl_per_mwh * multiplier / 1000.0

        # Marj
        margin_tl = sales_price_tl - base_cost_tl

        # Zarar saati tespiti
        is_loss = margin_tl < 0

        # Zaman dilimi
        tz = classify_hour(cr.hour)

        hour_costs.append(HourlyCostEntry(
            date=cr.date,
            hour=cr.hour,
            consumption_kwh=round(kwh, 4),
            ptf_tl_per_mwh=mr.ptf_tl_per_mwh,
            smf_tl_per_mwh=mr.smf_tl_per_mwh,
            yekdem_tl_per_mwh=yekdem_tl_per_mwh,
            base_cost_tl=round(base_cost_tl, 2),
            sales_price_tl=round(sales_price_tl, 2),
            margin_tl=round(margin_tl, 2),
            is_loss_hour=is_loss,
            time_zone=tz,
        ))

        total_base_cost += base_cost_tl
        total_sales += sales_price_tl

    # Toplamlar
    total_consumption = weighted_result.total_consumption_kwh

    # Dağıtım maliyeti
    distribution_cost_total = distribution_unit_price_tl_per_kwh * total_consumption

    # Dual Brüt Marj
    gross_margin_energy = total_sales - total_base_cost
    gross_margin_total = total_sales - total_base_cost - distribution_cost_total

    # Safety Guard: Dealer commission cap — bayi payı enerji marjını aşamaz, negatif olamaz
    raw_dealer_commission = gross_margin_energy * dealer_commission_pct / 100.0
    dealer_commission = max(0.0, min(raw_dealer_commission, max(0.0, gross_margin_energy)))

    # Dengesizlik payı = dengesizlik_maliyeti_per_mwh × toplam_tüketim / 1000
    imbalance_share = imbalance_cost_per_mwh * total_consumption / 1000.0

    # Net marj = toplam brüt marj - bayi komisyonu - dengesizlik payı
    net_margin = gross_margin_total - dealer_commission - imbalance_share

    # Tedarikçi gerçek maliyet = Ağırlıklı_PTF + YEKDEM + Dengesizlik
    supplier_real_cost = weighted_ptf + yekdem_tl_per_mwh + imbalance_cost_per_mwh

    return HourlyCostResult(
        hour_costs=hour_costs,
        total_base_cost_tl=round(total_base_cost, 2),
        total_sales_revenue_tl=round(total_sales, 2),
        # Dual margin fields
        gross_margin_energy_total_tl=round(gross_margin_energy, 2),
        gross_margin_total_total_tl=round(gross_margin_total, 2),
        net_margin_total_tl=round(net_margin, 2),
        # Cost breakdown
        distribution_cost_total_tl=round(distribution_cost_total, 2),
        imbalance_cost_total_tl=round(imbalance_share, 2),
        dealer_commission_total_tl=round(dealer_commission, 2),
        # Backward compat aliases
        total_gross_margin_tl=round(gross_margin_energy, 2),
        total_net_margin_tl=round(net_margin, 2),
        supplier_real_cost_tl_per_mwh=round(supplier_real_cost, 2),
    )
