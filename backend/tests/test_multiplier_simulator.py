"""
Pricing Risk Engine — Katsayı Simülatörü Testleri.

Task 14: run_simulation() fonksiyonu testleri.
Task 15: calculate_safe_multiplier() fonksiyonu testleri (sonra eklenecek).

Monotonluk garantileri:
- multiplier↑ → revenue↑ (kesin artan)
- multiplier↑ → loss_hours↓ (azalmayan)
"""

import pytest
from app.pricing.models import ImbalanceParams
from app.pricing.excel_parser import ParsedMarketRecord, ParsedConsumptionRecord
from app.pricing.multiplier_simulator import run_simulation


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _market(date: str, hour: int, ptf: float, smf: float) -> ParsedMarketRecord:
    return ParsedMarketRecord(
        period=date[:7], date=date, hour=hour,
        ptf_tl_per_mwh=ptf, smf_tl_per_mwh=smf,
    )


def _consumption(date: str, hour: int, kwh: float) -> ParsedConsumptionRecord:
    return ParsedConsumptionRecord(date=date, hour=hour, consumption_kwh=kwh)


def _build_test_data(n_hours: int = 24):
    """n saatlik test verisi üret — değişken PTF ile."""
    market = []
    consumption = []
    for h in range(n_hours):
        ptf = 1500.0 + h * 100.0  # 1500–3800 arası
        smf = ptf + 50.0
        market.append(_market("2025-01-01", h, ptf, smf))
        consumption.append(_consumption("2025-01-01", h, 50.0 + h * 2))
    return market, consumption


DEFAULT_PARAMS = ImbalanceParams(
    forecast_error_rate=0.05,
    imbalance_cost_tl_per_mwh=50.0,
    smf_based_imbalance_enabled=False,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 14: run_simulation() Testleri
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunSimulation:
    """run_simulation() fonksiyonu testleri."""

    def test_basic_simulation_returns_correct_count(self):
        """Varsayılan aralık (1.02–1.10, adım 0.01) → 9 satır."""
        market, consumption = _build_test_data()
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        assert len(rows) == 9  # 1.02, 1.03, ..., 1.10

    def test_custom_range(self):
        """Özel aralık: 1.05–1.08, adım 0.01 → 4 satır."""
        market, consumption = _build_test_data()
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
            multiplier_start=1.05,
            multiplier_end=1.08,
            multiplier_step=0.01,
        )
        assert len(rows) == 4
        assert rows[0].multiplier == pytest.approx(1.05)
        assert rows[-1].multiplier == pytest.approx(1.08)

    def test_sorted_by_multiplier(self):
        """Sonuçlar katsayıya göre artan sıralı."""
        market, consumption = _build_test_data()
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        multipliers = [r.multiplier for r in rows]
        assert multipliers == sorted(multipliers)

    def test_revenue_monotonically_increasing(self):
        """Katsayı arttıkça toplam satış geliri kesin artar."""
        market, consumption = _build_test_data()
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        for i in range(1, len(rows)):
            assert rows[i].total_sales_tl > rows[i - 1].total_sales_tl, (
                f"Revenue should increase: multiplier {rows[i].multiplier} "
                f"({rows[i].total_sales_tl}) <= {rows[i-1].multiplier} "
                f"({rows[i-1].total_sales_tl})"
            )

    def test_loss_hours_non_increasing(self):
        """Katsayı arttıkça zararlı saat sayısı azalır veya eşit kalır."""
        market, consumption = _build_test_data()
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        for i in range(1, len(rows)):
            assert rows[i].loss_hours <= rows[i - 1].loss_hours, (
                f"Loss hours should not increase: multiplier {rows[i].multiplier} "
                f"({rows[i].loss_hours}) > {rows[i-1].multiplier} "
                f"({rows[i-1].loss_hours})"
            )

    def test_dealer_commission_applied(self):
        """Bayi komisyonu brüt marjdan hesaplanır."""
        market, consumption = _build_test_data()
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
            dealer_commission_pct=10.0,
        )
        for row in rows:
            expected_commission = round(row.gross_margin_tl * 10.0 / 100.0, 2)
            assert row.dealer_commission_tl == pytest.approx(expected_commission, abs=0.01)

    def test_net_margin_less_than_gross(self):
        """Net marj her zaman brüt marjdan küçük veya eşit (komisyon + dengesizlik)."""
        market, consumption = _build_test_data()
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
            dealer_commission_pct=5.0,
        )
        for row in rows:
            assert row.net_margin_tl <= row.gross_margin_tl

    def test_total_loss_is_negative(self):
        """Toplam zarar negatif veya sıfır olmalı."""
        market, consumption = _build_test_data()
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        for row in rows:
            assert row.total_loss_tl <= 0.0

    def test_single_step(self):
        """Tek adımlık simülasyon (start == end)."""
        market, consumption = _build_test_data()
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
            multiplier_start=1.05,
            multiplier_end=1.05,
            multiplier_step=0.01,
        )
        assert len(rows) == 1
        assert rows[0].multiplier == pytest.approx(1.05)

    def test_invalid_range_raises(self):
        """Geçersiz aralık ValueError fırlatır."""
        market, consumption = _build_test_data()
        with pytest.raises(ValueError, match="küçük olamaz"):
            run_simulation(
                market, consumption,
                yekdem_tl_per_mwh=370.0,
                imbalance_params=DEFAULT_PARAMS,
                multiplier_start=1.10,
                multiplier_end=1.02,
            )

    def test_invalid_step_raises(self):
        """Negatif adım ValueError fırlatır."""
        market, consumption = _build_test_data()
        with pytest.raises(ValueError, match="pozitif"):
            run_simulation(
                market, consumption,
                yekdem_tl_per_mwh=370.0,
                imbalance_params=DEFAULT_PARAMS,
                multiplier_step=-0.01,
            )

    def test_below_one_start_raises(self):
        """Başlangıç < 1.0 ValueError fırlatır."""
        market, consumption = _build_test_data()
        with pytest.raises(ValueError, match="1.0"):
            run_simulation(
                market, consumption,
                yekdem_tl_per_mwh=370.0,
                imbalance_params=DEFAULT_PARAMS,
                multiplier_start=0.99,
            )

    def test_fine_step_no_float_drift(self):
        """Küçük adım (0.001) ile float kayması olmaz."""
        market, consumption = _build_test_data(4)
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
            multiplier_start=1.001,
            multiplier_end=1.010,
            multiplier_step=0.001,
        )
        assert len(rows) == 10  # 1.001, 1.002, ..., 1.010
        # Her katsayı tam olarak beklenen değerde
        for i, row in enumerate(rows):
            expected = 1.001 + i * 0.001
            assert row.multiplier == pytest.approx(expected, abs=1e-6)

    def test_kwh_mwh_conversion_in_simulation(self):
        """Simülasyonda kWh→MWh dönüşümü doğru yapılır."""
        market = [_market("2025-01-01", 10, 2000.0, 2100.0)]
        consumption = [_consumption("2025-01-01", 10, 1000.0)]
        params = ImbalanceParams(forecast_error_rate=0.0)

        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=0.0,
            imbalance_params=params,
            dealer_commission_pct=0.0,
            multiplier_start=1.05,
            multiplier_end=1.05,
            multiplier_step=0.01,
        )

        assert len(rows) == 1
        row = rows[0]
        # base_cost = 1000 × 2000 / 1000 = 2000 TL
        assert row.total_cost_tl == pytest.approx(2000.0)
        # sales = 1000 × 2000 × 1.05 / 1000 = 2100 TL
        assert row.total_sales_tl == pytest.approx(2100.0)
        # gross_margin = 2100 - 2000 = 100 TL
        assert row.gross_margin_tl == pytest.approx(100.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 14.2: Property Test — Katsayı Simülasyonu Monotonluğu (Property 11)
# Validates: Requirements 10.1, 10.2, 10.3
# ═══════════════════════════════════════════════════════════════════════════════

from hypothesis import given, settings, assume
from hypothesis import strategies as st


def _build_market_consumption(
    n_hours: int,
    ptf_base: float,
    ptf_spread: float,
    kwh_base: float,
    kwh_spread: float,
):
    """Hypothesis-uyumlu test verisi üretici."""
    market = []
    consumption = []
    for h in range(n_hours):
        ptf = ptf_base + (h % 24) * ptf_spread
        smf = ptf + 50.0
        market.append(_market("2025-01-01", h % 24, ptf, smf))
        kwh = kwh_base + (h % 24) * kwh_spread
        consumption.append(_consumption("2025-01-01", h % 24, kwh))
    return market, consumption


class TestSimulationMonotonicity:
    """Property 11: Katsayı Simülasyonu Monotonluğu.

    Evrensel özellikler:
    - multiplier↑ → total_sales_tl↑ (kesin artan)
    - multiplier↑ → loss_hours↓ (azalmayan)
    """

    @given(
        ptf_base=st.floats(min_value=500.0, max_value=5000.0),
        ptf_spread=st.floats(min_value=10.0, max_value=200.0),
        kwh_base=st.floats(min_value=10.0, max_value=500.0),
        kwh_spread=st.floats(min_value=0.1, max_value=20.0),
        yekdem=st.floats(min_value=0.0, max_value=2000.0),
        dealer_pct=st.floats(min_value=0.0, max_value=50.0),
    )
    @settings(max_examples=30, deadline=None)
    def test_revenue_strictly_increasing(
        self, ptf_base, ptf_spread, kwh_base, kwh_spread, yekdem, dealer_pct,
    ):
        """Katsayı arttıkça toplam satış geliri kesin artar."""
        market, consumption = _build_market_consumption(
            24, ptf_base, ptf_spread, kwh_base, kwh_spread,
        )
        # Toplam tüketim > 0 olmalı
        total_kwh = sum(c.consumption_kwh for c in consumption)
        assume(total_kwh > 0)

        params = ImbalanceParams(forecast_error_rate=0.05)
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=yekdem,
            imbalance_params=params,
            dealer_commission_pct=dealer_pct,
            multiplier_start=1.02,
            multiplier_end=1.10,
            multiplier_step=0.01,
        )

        for i in range(1, len(rows)):
            assert rows[i].total_sales_tl > rows[i - 1].total_sales_tl, (
                f"Revenue monotonicity violated at multiplier {rows[i].multiplier}"
            )

    @given(
        ptf_base=st.floats(min_value=500.0, max_value=5000.0),
        ptf_spread=st.floats(min_value=10.0, max_value=200.0),
        kwh_base=st.floats(min_value=10.0, max_value=500.0),
        kwh_spread=st.floats(min_value=0.1, max_value=20.0),
        yekdem=st.floats(min_value=0.0, max_value=2000.0),
    )
    @settings(max_examples=30, deadline=None)
    def test_loss_hours_non_increasing(
        self, ptf_base, ptf_spread, kwh_base, kwh_spread, yekdem,
    ):
        """Katsayı arttıkça zararlı saat sayısı azalır veya eşit kalır."""
        market, consumption = _build_market_consumption(
            24, ptf_base, ptf_spread, kwh_base, kwh_spread,
        )
        total_kwh = sum(c.consumption_kwh for c in consumption)
        assume(total_kwh > 0)

        params = ImbalanceParams(forecast_error_rate=0.05)
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=yekdem,
            imbalance_params=params,
            multiplier_start=1.02,
            multiplier_end=1.10,
            multiplier_step=0.01,
        )

        for i in range(1, len(rows)):
            assert rows[i].loss_hours <= rows[i - 1].loss_hours, (
                f"Loss hours monotonicity violated at multiplier {rows[i].multiplier}: "
                f"{rows[i].loss_hours} > {rows[i-1].loss_hours}"
            )

    @given(
        ptf_base=st.floats(min_value=500.0, max_value=5000.0),
        kwh_base=st.floats(min_value=10.0, max_value=500.0),
        yekdem=st.floats(min_value=0.0, max_value=2000.0),
        dealer_pct=st.floats(min_value=0.0, max_value=50.0),
    )
    @settings(max_examples=20, deadline=None)
    def test_gross_margin_equals_sales_minus_cost(
        self, ptf_base, kwh_base, yekdem, dealer_pct,
    ):
        """Brüt marj = satış - maliyet (her satırda)."""
        market = [_market("2025-01-01", h, ptf_base + h * 50, ptf_base + h * 50 + 30)
                  for h in range(24)]
        consumption = [_consumption("2025-01-01", h, kwh_base + h) for h in range(24)]
        total_kwh = sum(c.consumption_kwh for c in consumption)
        assume(total_kwh > 0)

        params = ImbalanceParams(forecast_error_rate=0.05)
        rows = run_simulation(
            market, consumption,
            yekdem_tl_per_mwh=yekdem,
            imbalance_params=params,
            dealer_commission_pct=dealer_pct,
            multiplier_start=1.02,
            multiplier_end=1.06,
            multiplier_step=0.01,
        )

        for row in rows:
            expected_gross = round(row.total_sales_tl - row.total_cost_tl, 2)
            assert row.gross_margin_tl == pytest.approx(expected_gross, abs=0.02)



# ═══════════════════════════════════════════════════════════════════════════════
# Task 15: calculate_safe_multiplier() Testleri
# ═══════════════════════════════════════════════════════════════════════════════

from app.pricing.multiplier_simulator import calculate_safe_multiplier, PeriodData


class TestSafeMultiplier:
    """calculate_safe_multiplier() fonksiyonu testleri."""

    def _make_period_data(
        self, period: str = "2025-01", n_hours: int = 24,
        ptf_base: float = 2000.0, ptf_spread: float = 50.0,
        kwh_base: float = 100.0, kwh_spread: float = 5.0,
    ) -> PeriodData:
        """Test dönem verisi üret."""
        market = []
        consumption = []
        for h in range(n_hours):
            ptf = ptf_base + h * ptf_spread
            smf = ptf + 30.0
            market.append(_market(f"{period}-01", h, ptf, smf))
            consumption.append(_consumption(f"{period}-01", h, kwh_base + h * kwh_spread))
        return PeriodData(
            period=period,
            market_records=market,
            consumption_records=consumption,
        )

    def test_single_period_returns_result(self):
        """Tek dönem verisi ile güvenli katsayı hesaplanır."""
        pd = self._make_period_data()
        result = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        assert result.safe_multiplier >= 1.001
        assert result.safe_multiplier <= 1.100
        assert result.recommended_multiplier >= result.safe_multiplier
        assert result.periods_analyzed == 1
        assert len(result.monthly_margins) == 1

    def test_multi_period_returns_result(self):
        """Çoklu dönem verisi ile güvenli katsayı hesaplanır."""
        pd1 = self._make_period_data("2025-01")
        pd2 = self._make_period_data("2025-02", ptf_base=2200.0)
        pd3 = self._make_period_data("2025-03", ptf_base=1800.0)

        result = calculate_safe_multiplier(
            [pd1, pd2, pd3],
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        assert result.periods_analyzed == 3
        assert len(result.monthly_margins) == 3

    def test_three_decimal_precision(self):
        """Güvenli katsayı 3 ondalık basamak hassasiyetinde."""
        pd = self._make_period_data()
        result = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        # 3 ondalık basamak: ×1.042 gibi
        decimal_str = f"{result.safe_multiplier:.3f}"
        assert result.safe_multiplier == float(decimal_str)

    def test_recommended_is_ceil_to_001(self):
        """Önerilen katsayı ≥ güvenli katsayı ve 0.01 adımında."""
        pd = self._make_period_data()
        result = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        # Önerilen katsayı güvenli katsayıdan büyük veya eşit olmalı
        assert result.recommended_multiplier >= result.safe_multiplier
        # Önerilen katsayı 0.01 adımında olmalı (2 ondalık)
        assert result.recommended_multiplier == round(result.recommended_multiplier, 2)
        # Önerilen katsayı, güvenli katsayıdan en fazla 0.01 yukarıda olmalı
        assert result.recommended_multiplier - result.safe_multiplier <= 0.01 + 1e-9

    def test_integer_step_no_float_drift(self):
        """Integer step tarama — float kayması yok."""
        pd = self._make_period_data()
        result = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        # Güvenli katsayı tam olarak 0.001 adımlarında
        safe_int = round(result.safe_multiplier * 1000)
        assert result.safe_multiplier == pytest.approx(safe_int / 1000.0, abs=1e-9)

    def test_dealer_commission_included(self):
        """Bayi komisyonu dahil edildiğinde güvenli katsayı artar."""
        pd = self._make_period_data()

        result_no_dealer = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
            dealer_commission_pct=0.0,
        )
        result_with_dealer = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
            dealer_commission_pct=25.0,
        )
        # Bayi komisyonu eklenince güvenli katsayı artmalı veya eşit kalmalı
        assert result_with_dealer.safe_multiplier >= result_no_dealer.safe_multiplier

    def test_high_ptf_spread_needs_higher_multiplier(self):
        """Yüksek PTF yayılımı daha yüksek güvenli katsayı gerektirir."""
        pd_low_spread = self._make_period_data(ptf_spread=10.0)
        pd_high_spread = self._make_period_data(ptf_spread=200.0)

        result_low = calculate_safe_multiplier(
            [pd_low_spread],
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        result_high = calculate_safe_multiplier(
            [pd_high_spread],
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        # Yüksek yayılım → daha yüksek güvenli katsayı (veya eşit)
        assert result_high.safe_multiplier >= result_low.safe_multiplier

    def test_warning_when_above_110(self):
        """×1.10 üzeri güvenli katsayı uyarı mesajı üretir."""
        # Çok yüksek PTF yayılımı ile ×1.10 üzeri zorlayalım
        market = []
        consumption = []
        for h in range(24):
            # Bazı saatlerde çok yüksek PTF
            if h in (17, 18, 19, 20, 21):
                ptf = 10000.0  # Çok yüksek puant
            else:
                ptf = 500.0  # Çok düşük
            market.append(_market("2025-01-01", h, ptf, ptf + 50))
            consumption.append(_consumption("2025-01-01", h, 100.0))

        pd = PeriodData(
            period="2025-01",
            market_records=market,
            consumption_records=consumption,
        )

        result = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        # Bu profilde güvenli katsayı ×1.10 üzeri olmalı → uyarı
        assert result.warning is not None
        assert "1.10" in result.warning

    def test_empty_periods_raises(self):
        """Boş dönem listesi ValueError fırlatır."""
        with pytest.raises(ValueError, match="En az bir dönem"):
            calculate_safe_multiplier(
                [],
                yekdem_tl_per_mwh=370.0,
                imbalance_params=DEFAULT_PARAMS,
            )

    def test_confidence_level_default(self):
        """Varsayılan güven düzeyi 0.95."""
        pd = self._make_period_data()
        result = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=370.0,
            imbalance_params=DEFAULT_PARAMS,
        )
        assert result.confidence_level == 0.95

    def test_yekdem_and_imbalance_included(self):
        """YEKDEM ve dengesizlik maliyeti hesaba dahil."""
        pd = self._make_period_data()

        # YEKDEM=0, dengesizlik=0 → düşük güvenli katsayı
        result_no_extras = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=0.0,
            imbalance_params=ImbalanceParams(forecast_error_rate=0.0),
        )

        # YEKDEM=500, dengesizlik=yüksek → daha yüksek güvenli katsayı
        result_with_extras = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=500.0,
            imbalance_params=ImbalanceParams(
                forecast_error_rate=0.10,
                imbalance_cost_tl_per_mwh=100.0,
            ),
        )

        # Ekstra maliyetler → güvenli katsayı artmalı veya eşit kalmalı
        assert result_with_extras.safe_multiplier >= result_no_extras.safe_multiplier



# ═══════════════════════════════════════════════════════════════════════════════
# Task 15.2: Property Test — Güvenli Katsayı Sınır Doğrulaması (Property 12)
# Validates: Requirements 11.1, 11.2, 11.3
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafeMultiplierProperties:
    """Property 12: Güvenli Katsayı Sınır Doğrulaması.

    Evrensel özellikler:
    - Güvenli katsayı ≥ 1.001 (minimum tarama sınırı)
    - Güvenli katsayı ≤ 1.100 (maksimum tarama sınırı)
    - Önerilen katsayı ≥ güvenli katsayı
    - 3 ondalık basamak hassasiyeti
    - Integer step → float kayması yok
    """

    @given(
        ptf_base=st.floats(min_value=800.0, max_value=4000.0),
        ptf_spread=st.floats(min_value=5.0, max_value=100.0),
        kwh_base=st.floats(min_value=20.0, max_value=300.0),
        kwh_spread=st.floats(min_value=0.5, max_value=10.0),
        yekdem=st.floats(min_value=50.0, max_value=1000.0),
        dealer_pct=st.floats(min_value=0.0, max_value=30.0),
    )
    @settings(max_examples=25, deadline=None)
    def test_safe_multiplier_within_bounds(
        self, ptf_base, ptf_spread, kwh_base, kwh_spread, yekdem, dealer_pct,
    ):
        """Güvenli katsayı tarama sınırları içinde."""
        market = [
            _market("2025-01-01", h, ptf_base + h * ptf_spread, ptf_base + h * ptf_spread + 30)
            for h in range(24)
        ]
        consumption = [
            _consumption("2025-01-01", h, kwh_base + h * kwh_spread)
            for h in range(24)
        ]
        total_kwh = sum(c.consumption_kwh for c in consumption)
        assume(total_kwh > 0)

        pd = PeriodData(
            period="2025-01",
            market_records=market,
            consumption_records=consumption,
        )
        params = ImbalanceParams(forecast_error_rate=0.05)

        result = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=yekdem,
            imbalance_params=params,
            dealer_commission_pct=dealer_pct,
        )

        # Sınır kontrolü
        assert result.safe_multiplier >= 1.001 or result.warning is not None
        assert result.safe_multiplier <= 1.100

    @given(
        ptf_base=st.floats(min_value=800.0, max_value=4000.0),
        ptf_spread=st.floats(min_value=5.0, max_value=100.0),
        kwh_base=st.floats(min_value=20.0, max_value=300.0),
        yekdem=st.floats(min_value=50.0, max_value=1000.0),
    )
    @settings(max_examples=25, deadline=None)
    def test_recommended_gte_safe(
        self, ptf_base, ptf_spread, kwh_base, yekdem,
    ):
        """Önerilen katsayı her zaman güvenli katsayıdan büyük veya eşit."""
        market = [
            _market("2025-01-01", h, ptf_base + h * ptf_spread, ptf_base + h * ptf_spread + 30)
            for h in range(24)
        ]
        consumption = [
            _consumption("2025-01-01", h, kwh_base + h * 2)
            for h in range(24)
        ]
        total_kwh = sum(c.consumption_kwh for c in consumption)
        assume(total_kwh > 0)

        pd = PeriodData(
            period="2025-01",
            market_records=market,
            consumption_records=consumption,
        )
        params = ImbalanceParams(forecast_error_rate=0.05)

        result = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=yekdem,
            imbalance_params=params,
        )

        assert result.recommended_multiplier >= result.safe_multiplier

    @given(
        ptf_base=st.floats(min_value=800.0, max_value=4000.0),
        ptf_spread=st.floats(min_value=5.0, max_value=100.0),
        kwh_base=st.floats(min_value=20.0, max_value=300.0),
        yekdem=st.floats(min_value=50.0, max_value=1000.0),
    )
    @settings(max_examples=25, deadline=None)
    def test_integer_step_precision(
        self, ptf_base, ptf_spread, kwh_base, yekdem,
    ):
        """Güvenli katsayı tam olarak 0.001 adımlarında (integer step)."""
        market = [
            _market("2025-01-01", h, ptf_base + h * ptf_spread, ptf_base + h * ptf_spread + 30)
            for h in range(24)
        ]
        consumption = [
            _consumption("2025-01-01", h, kwh_base + h * 2)
            for h in range(24)
        ]
        total_kwh = sum(c.consumption_kwh for c in consumption)
        assume(total_kwh > 0)

        pd = PeriodData(
            period="2025-01",
            market_records=market,
            consumption_records=consumption,
        )
        params = ImbalanceParams(forecast_error_rate=0.05)

        result = calculate_safe_multiplier(
            [pd],
            yekdem_tl_per_mwh=yekdem,
            imbalance_params=params,
        )

        # Integer step → tam 0.001 adımlarında
        safe_int = round(result.safe_multiplier * 1000)
        assert result.safe_multiplier == pytest.approx(safe_int / 1000.0, abs=1e-9)

    @given(
        ptf_base=st.floats(min_value=800.0, max_value=4000.0),
        kwh_base=st.floats(min_value=20.0, max_value=300.0),
        yekdem=st.floats(min_value=50.0, max_value=1000.0),
        n_periods=st.integers(min_value=1, max_value=4),
    )
    @settings(max_examples=15, deadline=None)
    def test_periods_analyzed_matches_input(
        self, ptf_base, kwh_base, yekdem, n_periods,
    ):
        """periods_analyzed dönem sayısıyla eşleşir."""
        periods_data = []
        for i in range(n_periods):
            month = str(i + 1).zfill(2)
            market = [
                _market(f"2025-{month}-01", h, ptf_base + h * 50, ptf_base + h * 50 + 30)
                for h in range(24)
            ]
            consumption = [
                _consumption(f"2025-{month}-01", h, kwh_base + h * 2)
                for h in range(24)
            ]
            periods_data.append(PeriodData(
                period=f"2025-{month}",
                market_records=market,
                consumption_records=consumption,
            ))

        params = ImbalanceParams(forecast_error_rate=0.05)
        result = calculate_safe_multiplier(
            periods_data,
            yekdem_tl_per_mwh=yekdem,
            imbalance_params=params,
        )

        assert result.periods_analyzed == n_periods
        assert len(result.monthly_margins) == n_periods
