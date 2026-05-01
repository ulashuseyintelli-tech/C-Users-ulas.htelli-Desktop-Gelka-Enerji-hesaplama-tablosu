"""
Pricing Risk Engine — Katsayı Simülatörü ve Güvenli Katsayı Hesaplama.

İki ana fonksiyon:
1. run_simulation(): Katsayı aralığında simülasyon çalıştır
2. calculate_safe_multiplier(): 5. persentil güvenli katsayı hesapla

KRİTİK TASARIM KARARLARI:
- Güvenli katsayı taramasında integer step kullanılır (1001–1100)
  → float kayması riski ortadan kalkar
  → 1001 = ×1.001, 1100 = ×1.100
- kWh vs MWh dönüşümü: TL = kWh × (TL/MWh) / 1000
- Bayi komisyonu = brüt marj × bayi yüzdesi / 100

Requirements: 10.1–10.4, 11.1–11.5, 13.2, 14.5
"""

from __future__ import annotations

import math
import os

from .models import (
    SimulationRow,
    SafeMultiplierResult,
    ImbalanceParams,
)
from .excel_parser import ParsedMarketRecord, ParsedConsumptionRecord
from .pricing_engine import calculate_hourly_costs


def run_simulation(
    market_records: list[ParsedMarketRecord],
    consumption_records: list[ParsedConsumptionRecord],
    yekdem_tl_per_mwh: float,
    imbalance_params: ImbalanceParams,
    dealer_commission_pct: float = 0.0,
    multiplier_start: float = 1.02,
    multiplier_end: float = 1.10,
    multiplier_step: float = 0.01,
) -> list[SimulationRow]:
    """Katsayı simülasyonu çalıştır.

    Belirtilen aralıkta her katsayı için tam saatlik maliyet hesabı yapar.
    Sonuçlar katsayıya göre artan sıralıdır.

    Monotonluk garantileri:
    - multiplier↑ → revenue↑ (kesin artan)
    - multiplier↑ → loss_hours↓ (azalmayan — eşit kalabilir)

    Args:
        market_records: Saatlik piyasa verileri.
        consumption_records: Saatlik tüketim verileri.
        yekdem_tl_per_mwh: YEKDEM bedeli (TL/MWh).
        imbalance_params: Dengesizlik parametreleri.
        dealer_commission_pct: Bayi komisyon yüzdesi (0–100).
        multiplier_start: Başlangıç katsayısı (varsayılan 1.02).
        multiplier_end: Bitiş katsayısı (varsayılan 1.10).
        multiplier_step: Adım değeri (varsayılan 0.01).

    Returns:
        Katsayıya göre sıralı SimulationRow listesi.

    Raises:
        ValueError: Geçersiz aralık veya adım değeri.
    """
    if multiplier_start < 1.0:
        raise ValueError(
            f"Başlangıç katsayısı 1.0'dan küçük olamaz: {multiplier_start}"
        )
    if multiplier_end < multiplier_start:
        raise ValueError(
            f"Bitiş katsayısı ({multiplier_end}) başlangıçtan ({multiplier_start}) "
            f"küçük olamaz."
        )
    if multiplier_step <= 0:
        raise ValueError(f"Adım değeri pozitif olmalı: {multiplier_step}")

    # Float kaymasını önlemek için integer aritmetik kullan
    # Adımı 1e6 hassasiyetle integer'a çevir
    precision = 6
    factor = 10 ** precision
    start_int = round(multiplier_start * factor)
    end_int = round(multiplier_end * factor)
    step_int = round(multiplier_step * factor)

    if step_int == 0:
        raise ValueError(f"Adım değeri çok küçük: {multiplier_step}")

    rows: list[SimulationRow] = []
    current_int = start_int

    while current_int <= end_int:
        multiplier = current_int / factor

        # Tam saatlik maliyet hesabı
        cost_result = calculate_hourly_costs(
            market_records=market_records,
            consumption_records=consumption_records,
            yekdem_tl_per_mwh=yekdem_tl_per_mwh,
            multiplier=multiplier,
            imbalance_params=imbalance_params,
            dealer_commission_pct=dealer_commission_pct,
        )

        # Zararlı saat sayısı ve toplam zarar
        loss_hours = 0
        total_loss_tl = 0.0
        for entry in cost_result.hour_costs:
            if entry.is_loss_hour:
                loss_hours += 1
                total_loss_tl += entry.margin_tl  # Negatif değer

        rows.append(SimulationRow(
            multiplier=round(multiplier, 6),
            total_sales_tl=cost_result.total_sales_revenue_tl,
            total_cost_tl=cost_result.total_base_cost_tl,
            gross_margin_tl=cost_result.total_gross_margin_tl,
            dealer_commission_tl=round(
                cost_result.total_gross_margin_tl * dealer_commission_pct / 100.0, 2
            ),
            net_margin_tl=cost_result.total_net_margin_tl,
            loss_hours=loss_hours,
            total_loss_tl=round(total_loss_tl, 2),
        ))

        current_int += step_int

    return rows



# ═══════════════════════════════════════════════════════════════════════════════
# Güvenli Katsayı Hesaplama
# ═══════════════════════════════════════════════════════════════════════════════

# PeriodData: Tek dönem için piyasa + tüketim verisi çifti
from dataclasses import dataclass


@dataclass
class PeriodData:
    """Tek dönem verisi — güvenli katsayı hesabında kullanılır."""
    period: str  # YYYY-MM
    market_records: list[ParsedMarketRecord]
    consumption_records: list[ParsedConsumptionRecord]


def calculate_safe_multiplier(
    periods_data: list[PeriodData],
    yekdem_tl_per_mwh: float,
    imbalance_params: ImbalanceParams,
    dealer_commission_pct: float = 0.0,
    confidence_level: float = 0.95,
) -> SafeMultiplierResult:
    """Güvenli katsayı hesapla — 5. persentil algoritması.

    KRİTİK TASARIM KARARLARI:
    1. Integer step tarama: 1001–1100 arası (1001=×1.001, 1100=×1.100)
       → float kayması riski sıfır
    2. Tek ay: saatlik marj dağılımı (744 veri noktası) üzerinden 5. persentil
    3. Çoklu ay: aylık net marj dağılımı üzerinden 5. persentil
    4. Güvenli katsayı = 5. persentilde net_margin ≥ 0 olan en düşük katsayı
    5. Önerilen katsayı = ceil(safe × 100) / 100 (bir üst 0.01 adımı)
    6. ×1.10 üzeri uyarısı

    Args:
        periods_data: Dönem verileri listesi (1+ dönem).
        yekdem_tl_per_mwh: YEKDEM bedeli (TL/MWh).
        imbalance_params: Dengesizlik parametreleri.
        dealer_commission_pct: Bayi komisyon yüzdesi (0–100).
        confidence_level: Güven düzeyi (varsayılan 0.95).

    Returns:
        SafeMultiplierResult: Güvenli katsayı sonucu.

    Raises:
        ValueError: Dönem verisi boş ise.
    """
    if not periods_data:
        raise ValueError("En az bir dönem verisi gerekli.")

    n_periods = len(periods_data)
    is_single_period = n_periods == 1

    # Integer step tarama: 1001–max (×1.001 – ×max_multiplier)
    SCAN_START = 1001
    # Configurable üst sınır: env var veya varsayılan ×1.100
    MAX_SAFE = int(os.environ.get("PRICING_MAX_MULTIPLIER_INT", "1100"))
    # ×1.10 üzeri uyarı her zaman verilir
    SCAN_END = MAX_SAFE

    safe_int: int | None = None

    for mult_int in range(SCAN_START, SCAN_END + 1):
        multiplier = mult_int / 1000.0

        if is_single_period:
            # Tek ay: saatlik marj dağılımı üzerinden 5. persentil
            pd = periods_data[0]
            cost_result = calculate_hourly_costs(
                market_records=pd.market_records,
                consumption_records=pd.consumption_records,
                yekdem_tl_per_mwh=yekdem_tl_per_mwh,
                multiplier=multiplier,
                imbalance_params=imbalance_params,
                dealer_commission_pct=dealer_commission_pct,
            )

            # Saatlik marjları topla
            hourly_margins = [entry.margin_tl for entry in cost_result.hour_costs]
            if not hourly_margins:
                continue

            # 5. persentil hesapla
            sorted_margins = sorted(hourly_margins)
            p5_value = _percentile(sorted_margins, 5)

            # 5. persentilde marj ≥ 0 ise bu katsayı güvenli
            if p5_value >= 0:
                safe_int = mult_int
                break
        else:
            # Çoklu ay: aylık net marj dağılımı üzerinden 5. persentil
            monthly_net_margins: list[float] = []

            for pd in periods_data:
                cost_result = calculate_hourly_costs(
                    market_records=pd.market_records,
                    consumption_records=pd.consumption_records,
                    yekdem_tl_per_mwh=yekdem_tl_per_mwh,
                    multiplier=multiplier,
                    imbalance_params=imbalance_params,
                    dealer_commission_pct=dealer_commission_pct,
                )
                monthly_net_margins.append(cost_result.total_net_margin_tl)

            if not monthly_net_margins:
                continue

            sorted_margins = sorted(monthly_net_margins)
            p5_value = _percentile(sorted_margins, 5)

            if p5_value >= 0:
                safe_int = mult_int
                break

    # Sonuç oluştur
    if safe_int is not None:
        safe_multiplier = round(safe_int / 1000.0, 3)
        # Önerilen katsayı: bir üst 0.01 adımı
        recommended = math.ceil(safe_multiplier * 100) / 100.0
        recommended = round(recommended, 2)

        # ×1.10 üzeri uyarısı
        warning = None
        if safe_multiplier > 1.10:
            warning = (
                f"Bu profil için ×1.10 altında güvenli katsayı bulunamadı. "
                f"Bulunan güvenli katsayı: ×{safe_multiplier:.3f}"
            )
    else:
        # Tarama aralığında güvenli katsayı bulunamadı
        safe_multiplier = 1.100
        recommended = 1.10
        warning = (
            "Bu profil için ×1.10 altında güvenli katsayı bulunamadı. "
            "Daha yüksek katsayı veya farklı parametreler deneyin."
        )

    # Aylık marjlar (güvenli katsayı ile hesaplanmış)
    monthly_margins: list[float] = []
    for pd in periods_data:
        cost_result = calculate_hourly_costs(
            market_records=pd.market_records,
            consumption_records=pd.consumption_records,
            yekdem_tl_per_mwh=yekdem_tl_per_mwh,
            multiplier=safe_multiplier,
            imbalance_params=imbalance_params,
            dealer_commission_pct=dealer_commission_pct,
        )
        monthly_margins.append(round(cost_result.total_net_margin_tl, 2))

    return SafeMultiplierResult(
        safe_multiplier=safe_multiplier,
        recommended_multiplier=recommended,
        confidence_level=confidence_level,
        periods_analyzed=n_periods,
        monthly_margins=monthly_margins,
        warning=warning,
    )


def _percentile(sorted_values: list[float], percentile: int) -> float:
    """Sıralı listeden persentil değeri hesapla.

    5. persentil algoritması:
        idx = int(len(sorted_list) * percentile / 100)
        return sorted_list[idx]

    Args:
        sorted_values: Küçükten büyüğe sıralı değerler.
        percentile: Persentil değeri (0–100).

    Returns:
        Persentil değeri.
    """
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * percentile / 100)
    # Sınır kontrolü
    idx = max(0, min(idx, len(sorted_values) - 1))
    return sorted_values[idx]
