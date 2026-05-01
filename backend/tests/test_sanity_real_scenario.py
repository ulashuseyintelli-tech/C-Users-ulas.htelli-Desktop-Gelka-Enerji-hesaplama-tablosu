"""
Pricing Risk Engine — Gerçek Müşteri Senaryosu Sanity Check.

Gerçek dünya benzeri verilerle tam akış testi:
- Ocak 2026 PTF verileri (gerçek EPİAŞ değerleri bazında)
- 3 vardiya sanayi profili (100.000 kWh/ay)
- YEKDEM: 162.73 TL/MWh (Ocak 2026 gerçek)
- Tam hesaplama zinciri: weighted → hourly costs → simulation → safe multiplier → risk

Bu test "çıkan katsayı mantıklı mı?" sorusunu cevaplar.
"""

import pytest
import calendar
from app.pricing.models import ImbalanceParams, RiskLevel
from app.pricing.excel_parser import ParsedMarketRecord, ParsedConsumptionRecord
from app.pricing.pricing_engine import calculate_weighted_prices, calculate_hourly_costs
from app.pricing.time_zones import calculate_time_zone_breakdown
from app.pricing.multiplier_simulator import (
    run_simulation,
    calculate_safe_multiplier,
    PeriodData,
)
from app.pricing.risk_calculator import (
    calculate_risk_score,
    generate_offer_warning,
    check_risk_safe_multiplier_coherence,
)


def _generate_realistic_market_data(period: str = "2026-01") -> list[ParsedMarketRecord]:
    """Ocak 2026 benzeri gerçekçi PTF/SMF verileri üret.

    Gerçek Ocak 2026 ortalama PTF: ~2894.92 TL/MWh
    Saatlik dağılım: gece düşük, gündüz orta, puant yüksek
    """
    year, month = int(period[:4]), int(period[5:7])
    days = calendar.monthrange(year, month)[1]

    records = []
    for day in range(1, days + 1):
        date_str = f"{period}-{day:02d}"
        for hour in range(24):
            # Gerçekçi saatlik PTF profili
            if 0 <= hour <= 5:
                # Gece: düşük (2000-2400)
                ptf = 2200.0 + (hour * 40.0) + (day * 5.0)
            elif 6 <= hour <= 16:
                # Gündüz: orta (2600-3200)
                ptf = 2800.0 + ((hour - 6) * 40.0) + (day * 3.0)
            elif 17 <= hour <= 21:
                # Puant: yüksek (3200-4200)
                ptf = 3400.0 + ((hour - 17) * 160.0) + (day * 5.0)
            else:
                # Gece geç: düşük (2100-2500)
                ptf = 2300.0 + (day * 4.0)

            smf = ptf + 50.0 + (hour * 2.0)
            records.append(ParsedMarketRecord(
                period=period, date=date_str, hour=hour,
                ptf_tl_per_mwh=round(ptf, 2),
                smf_tl_per_mwh=round(smf, 2),
            ))

    return records


def _generate_3shift_consumption(
    period: str = "2026-01",
    total_kwh: float = 100000.0,
) -> list[ParsedConsumptionRecord]:
    """3 vardiya sanayi tüketim profili — 7/24 çalışan fabrika.

    Gece biraz düşük, gündüz normal, puant normal.
    """
    year, month = int(period[:4]), int(period[5:7])
    days = calendar.monthrange(year, month)[1]
    total_hours = days * 24

    records = []
    weights = []
    for hour in range(24):
        if 0 <= hour <= 5:
            w = 0.85  # Gece: %85 kapasite
        elif 6 <= hour <= 16:
            w = 1.05  # Gündüz: %105 kapasite
        elif 17 <= hour <= 21:
            w = 1.10  # Puant: %110 kapasite (vardiya değişimi)
        else:
            w = 0.90  # Gece geç: %90
        weights.append(w)

    # Normalize
    avg_weight = sum(weights) / len(weights)
    hourly_kwh_base = total_kwh / total_hours

    for day in range(1, days + 1):
        date_str = f"{period}-{day:02d}"
        for hour in range(24):
            kwh = hourly_kwh_base * weights[hour] / avg_weight
            records.append(ParsedConsumptionRecord(
                date=date_str, hour=hour,
                consumption_kwh=round(kwh, 4),
            ))

    return records


class TestRealScenarioSanityCheck:
    """Gerçek dünya senaryosu — tam akış sanity check."""

    def setup_method(self):
        """Test verilerini hazırla."""
        self.period = "2026-01"
        self.yekdem = 162.73  # Ocak 2026 gerçek YEKDEM
        self.market = _generate_realistic_market_data(self.period)
        self.consumption = _generate_3shift_consumption(self.period, 100000.0)
        self.params = ImbalanceParams(
            forecast_error_rate=0.05,
            imbalance_cost_tl_per_mwh=50.0,
            smf_based_imbalance_enabled=False,
        )

    def test_data_completeness(self):
        """Veri bütünlüğü: 744 saat piyasa + 744 saat tüketim."""
        assert len(self.market) == 744  # 31 gün × 24 saat
        assert len(self.consumption) == 744

    def test_total_consumption(self):
        """Toplam tüketim ~100.000 kWh."""
        total = sum(c.consumption_kwh for c in self.consumption)
        assert total == pytest.approx(100000.0, rel=0.01)

    def test_weighted_ptf_in_reasonable_range(self):
        """Ağırlıklı PTF makul aralıkta (2000-4000 TL/MWh)."""
        result = calculate_weighted_prices(self.market, self.consumption)

        print(f"\n{'='*60}")
        print(f"AĞIRLIKLI FİYAT SONUÇLARI")
        print(f"{'='*60}")
        print(f"Ağırlıklı PTF:     {result.weighted_ptf_tl_per_mwh:.2f} TL/MWh")
        print(f"Aritmetik Ort PTF:  {result.arithmetic_avg_ptf:.2f} TL/MWh")
        print(f"Ağırlıklı SMF:     {result.weighted_smf_tl_per_mwh:.2f} TL/MWh")
        print(f"Toplam Tüketim:     {result.total_consumption_kwh:.0f} kWh")
        print(f"Toplam Maliyet:     {result.total_cost_tl:.2f} TL")
        print(f"Eşleşen Saat:       {result.hours_count}")

        assert 2000 < result.weighted_ptf_tl_per_mwh < 4000
        assert result.hours_count == 744

    def test_hourly_costs_with_multiplier_105(self):
        """×1.05 katsayı ile saatlik maliyet hesabı mantıklı."""
        result = calculate_hourly_costs(
            self.market, self.consumption,
            yekdem_tl_per_mwh=self.yekdem,
            multiplier=1.05,
            imbalance_params=self.params,
            dealer_commission_pct=2.0,
        )

        print(f"\n{'='*60}")
        print(f"SAATLİK MALİYET SONUÇLARI (×1.05, Bayi %2)")
        print(f"{'='*60}")
        print(f"Toplam Baz Maliyet:   {result.total_base_cost_tl:>12,.2f} TL")
        print(f"Toplam Satış Geliri:  {result.total_sales_revenue_tl:>12,.2f} TL")
        print(f"Brüt Marj:            {result.total_gross_margin_tl:>12,.2f} TL")
        print(f"Net Marj:             {result.total_net_margin_tl:>12,.2f} TL")
        print(f"Tedarikçi Maliyet:    {result.supplier_real_cost_tl_per_mwh:.2f} TL/MWh")

        loss_hours = sum(1 for e in result.hour_costs if e.is_loss_hour)
        total_loss = sum(e.margin_tl for e in result.hour_costs if e.is_loss_hour)
        print(f"Zararlı Saat:         {loss_hours}")
        print(f"Toplam Zarar:         {total_loss:,.2f} TL")

        # Sanity checks
        assert result.total_base_cost_tl > 0
        assert result.total_sales_revenue_tl > result.total_base_cost_tl  # ×1.05 → kâr
        assert result.total_gross_margin_tl > 0
        assert len(result.hour_costs) == 744

    def test_simulation_table(self):
        """Katsayı simülasyonu tablosu mantıklı."""
        rows = run_simulation(
            self.market, self.consumption,
            yekdem_tl_per_mwh=self.yekdem,
            imbalance_params=self.params,
            dealer_commission_pct=2.0,
        )

        print(f"\n{'='*60}")
        print(f"KATSAYI SİMÜLASYONU (Bayi %2)")
        print(f"{'='*60}")
        print(f"{'Katsayı':>8} {'Satış':>14} {'Maliyet':>14} {'Brüt Marj':>12} "
              f"{'Net Marj':>12} {'Zarar Saat':>10} {'Zarar TL':>12}")
        print("-" * 90)
        for row in rows:
            print(f"×{row.multiplier:.2f}   {row.total_sales_tl:>14,.2f} "
                  f"{row.total_cost_tl:>14,.2f} {row.gross_margin_tl:>12,.2f} "
                  f"{row.net_margin_tl:>12,.2f} {row.loss_hours:>10} "
                  f"{row.total_loss_tl:>12,.2f}")

        assert len(rows) == 9  # 1.02–1.10
        # Revenue monoton artan
        for i in range(1, len(rows)):
            assert rows[i].total_sales_tl > rows[i - 1].total_sales_tl

    def test_safe_multiplier_reasonable(self):
        """Güvenli katsayı makul aralıkta."""
        pd = PeriodData(
            period=self.period,
            market_records=self.market,
            consumption_records=self.consumption,
        )
        result = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=self.yekdem,
            imbalance_params=self.params,
            dealer_commission_pct=2.0,
        )

        print(f"\n{'='*60}")
        print(f"GÜVENLİ KATSAYI SONUCU")
        print(f"{'='*60}")
        print(f"Güvenli Katsayı:    ×{result.safe_multiplier:.3f}")
        print(f"Önerilen Katsayı:   ×{result.recommended_multiplier:.2f}")
        print(f"Güven Düzeyi:       %{result.confidence_level*100:.0f}")
        print(f"Aylık Net Marj:     {result.monthly_margins}")
        if result.warning:
            print(f"UYARI: {result.warning}")

        # Güvenli katsayı 1.001–1.100 arasında olmalı
        assert 1.001 <= result.safe_multiplier <= 1.100
        assert result.recommended_multiplier >= result.safe_multiplier

    def test_risk_score_with_real_data(self):
        """Risk skoru gerçek veri ile hesaplanır."""
        weighted = calculate_weighted_prices(self.market, self.consumption)
        tz_breakdown = calculate_time_zone_breakdown(
            self.market, self.consumption, self.yekdem,
        )
        risk = calculate_risk_score(weighted, tz_breakdown)

        print(f"\n{'='*60}")
        print(f"RİSK SKORU SONUCU")
        print(f"{'='*60}")
        print(f"Risk Seviyesi:      {risk.score.value}")
        print(f"Sapma:              %{risk.deviation_pct:.2f}")
        print(f"T2 Tüketim Payı:    %{risk.t2_consumption_pct:.1f}")
        print(f"Peak Concentration: %{risk.peak_concentration:.1f}")
        print(f"Açıklamalar:")
        for r in risk.reasons:
            print(f"  → {r}")

        assert risk.score in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)
        assert len(risk.reasons) >= 1

    def test_full_pipeline_end_to_end(self):
        """Tam akış: veri → hesaplama → simülasyon → risk → uyarı."""
        # 1. Ağırlıklı fiyat
        weighted = calculate_weighted_prices(self.market, self.consumption)

        # 2. Zaman dilimi dağılımı
        tz_breakdown = calculate_time_zone_breakdown(
            self.market, self.consumption, self.yekdem,
        )

        # 3. Güvenli katsayı
        pd = PeriodData(
            period=self.period,
            market_records=self.market,
            consumption_records=self.consumption,
        )
        safe_result = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=self.yekdem,
            imbalance_params=self.params,
            dealer_commission_pct=2.0,
        )

        # 4. Risk skoru
        risk = calculate_risk_score(weighted, tz_breakdown)

        # 5. Tutarlılık kontrolü
        coherence = check_risk_safe_multiplier_coherence(
            risk.score, safe_result.safe_multiplier,
        )

        # 6. Uyarı (×1.03 seçilmiş varsayalım)
        warning = generate_offer_warning(
            selected_multiplier=1.03,
            safe_multiplier=safe_result.safe_multiplier,
            recommended_multiplier=safe_result.recommended_multiplier,
            risk_level=risk.score,
        )

        print(f"\n{'='*60}")
        print(f"TAM AKIŞ SONUCU — 3 Vardiya Sanayi, 100.000 kWh/ay")
        print(f"{'='*60}")
        print(f"Dönem:              {self.period}")
        print(f"YEKDEM:             {self.yekdem} TL/MWh")
        print(f"Ağırlıklı PTF:     {weighted.weighted_ptf_tl_per_mwh:.2f} TL/MWh")
        print(f"Aritmetik Ort:      {weighted.arithmetic_avg_ptf:.2f} TL/MWh")
        print(f"Güvenli Katsayı:    ×{safe_result.safe_multiplier:.3f}")
        print(f"Önerilen Katsayı:   ×{safe_result.recommended_multiplier:.2f}")
        print(f"Risk:               {risk.score.value}")
        print(f"Sapma:              %{risk.deviation_pct:.2f}")
        if coherence:
            print(f"Tutarlılık:         {coherence}")
        if warning:
            print(f"Uyarı:              {warning}")
        else:
            print(f"Uyarı:              Yok (katsayı güvenli)")
        print(f"{'='*60}")

        # Temel doğrulama
        assert weighted.hours_count == 744
        assert safe_result.safe_multiplier >= 1.001
        assert risk.score in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)
