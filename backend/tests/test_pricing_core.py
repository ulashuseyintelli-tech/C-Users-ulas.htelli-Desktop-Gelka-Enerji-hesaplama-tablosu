"""
Pricing Risk Engine — Core Module Tests.

Tests for:
- Task 9: Consumption service (save_consumption_profile)
- Task 10: Time zone engine (classify_hour, calculate_time_zone_breakdown)
- Task 11: Pricing engine (calculate_weighted_prices, calculate_hourly_costs)
- Task 12: Imbalance engine (calculate_imbalance_cost)

Includes kWh/MWh conversion verification test.
"""

import pytest
from app.pricing.models import ImbalanceParams, TimeZone
from app.pricing.excel_parser import ParsedMarketRecord, ParsedConsumptionRecord
from app.pricing.imbalance import calculate_imbalance_cost
from app.pricing.time_zones import classify_hour, calculate_time_zone_breakdown
from app.pricing.pricing_engine import calculate_weighted_prices, calculate_hourly_costs


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers — test data builders
# ═══════════════════════════════════════════════════════════════════════════════

def _market(date: str, hour: int, ptf: float, smf: float) -> ParsedMarketRecord:
    period = date[:7]
    return ParsedMarketRecord(period=period, date=date, hour=hour,
                              ptf_tl_per_mwh=ptf, smf_tl_per_mwh=smf)


def _consumption(date: str, hour: int, kwh: float) -> ParsedConsumptionRecord:
    return ParsedConsumptionRecord(date=date, hour=hour, consumption_kwh=kwh)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 12: Dengesizlik Motoru
# ═══════════════════════════════════════════════════════════════════════════════

class TestImbalanceCost:
    """calculate_imbalance_cost tests."""

    def test_flat_mode_default(self):
        """Sabit oran modu: 50 × 0.05 = 2.5 TL/MWh."""
        params = ImbalanceParams(
            forecast_error_rate=0.05,
            imbalance_cost_tl_per_mwh=50.0,
            smf_based_imbalance_enabled=False,
        )
        result = calculate_imbalance_cost(2000.0, 2100.0, params)
        assert result == pytest.approx(2.5)

    def test_smf_mode(self):
        """SMF bazlı mod: |2100 - 2000| × 0.05 = 5.0 TL/MWh."""
        params = ImbalanceParams(
            forecast_error_rate=0.05,
            imbalance_cost_tl_per_mwh=50.0,
            smf_based_imbalance_enabled=True,
        )
        result = calculate_imbalance_cost(2000.0, 2100.0, params)
        assert result == pytest.approx(5.0)

    def test_smf_mode_negative_diff(self):
        """SMF < PTF durumunda mutlak değer kullanılır."""
        params = ImbalanceParams(
            forecast_error_rate=0.10,
            smf_based_imbalance_enabled=True,
        )
        result = calculate_imbalance_cost(3000.0, 2800.0, params)
        assert result == pytest.approx(20.0)  # |2800-3000| × 0.10 = 20

    def test_zero_error_rate(self):
        """Hata oranı 0 ise dengesizlik maliyeti 0."""
        params = ImbalanceParams(forecast_error_rate=0.0)
        result = calculate_imbalance_cost(2000.0, 2100.0, params)
        assert result == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Task 10: Zaman Dilimi Motoru
# ═══════════════════════════════════════════════════════════════════════════════

class TestClassifyHour:
    """classify_hour tests."""

    def test_t1_range(self):
        """T1: saat 6–16."""
        for h in range(6, 17):
            assert classify_hour(h) == TimeZone.T1, f"Hour {h} should be T1"

    def test_t2_range(self):
        """T2: saat 17–21."""
        for h in range(17, 22):
            assert classify_hour(h) == TimeZone.T2, f"Hour {h} should be T2"

    def test_t3_range(self):
        """T3: saat 22–23 ve 0–5."""
        for h in [22, 23, 0, 1, 2, 3, 4, 5]:
            assert classify_hour(h) == TimeZone.T3, f"Hour {h} should be T3"

    def test_invalid_hour(self):
        """Geçersiz saat değeri ValueError fırlatır."""
        with pytest.raises(ValueError):
            classify_hour(-1)
        with pytest.raises(ValueError):
            classify_hour(24)

    def test_all_24_hours_classified(self):
        """Tüm 24 saat bir dilime atanır."""
        for h in range(24):
            result = classify_hour(h)
            assert result in (TimeZone.T1, TimeZone.T2, TimeZone.T3)


class TestTimeZoneBreakdown:
    """calculate_time_zone_breakdown tests."""

    def test_basic_breakdown(self):
        """Basit 3 saatlik veri ile dağılım kontrolü."""
        market = [
            _market("2025-01-01", 10, 2000.0, 2100.0),  # T1
            _market("2025-01-01", 18, 3000.0, 3100.0),  # T2
            _market("2025-01-01", 2, 1000.0, 1100.0),   # T3
        ]
        consumption = [
            _consumption("2025-01-01", 10, 100.0),
            _consumption("2025-01-01", 18, 50.0),
            _consumption("2025-01-01", 2, 50.0),
        ]
        result = calculate_time_zone_breakdown(market, consumption)

        assert "T1" in result
        assert "T2" in result
        assert "T3" in result

        assert result["T1"].consumption_kwh == pytest.approx(100.0)
        assert result["T2"].consumption_kwh == pytest.approx(50.0)
        assert result["T3"].consumption_kwh == pytest.approx(50.0)

        # T1 ağırlıklı PTF = 2000 (tek saat)
        assert result["T1"].weighted_ptf_tl_per_mwh == pytest.approx(2000.0)

    def test_partition_invariant(self):
        """T1+T2+T3 toplam tüketim = genel toplam."""
        market = [_market("2025-01-01", h, 2000.0 + h * 10, 2100.0) for h in range(24)]
        consumption = [_consumption("2025-01-01", h, 10.0 + h) for h in range(24)]

        result = calculate_time_zone_breakdown(market, consumption)
        total = sum(r.consumption_kwh for r in result.values())
        expected = sum(c.consumption_kwh for c in consumption)
        assert total == pytest.approx(expected, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 11: Hesaplama Motoru
# ═══════════════════════════════════════════════════════════════════════════════

class TestWeightedPrices:
    """calculate_weighted_prices tests."""

    def test_basic_weighted_ptf(self):
        """Basit ağırlıklı PTF hesaplama."""
        market = [
            _market("2025-01-01", 0, 1000.0, 1100.0),
            _market("2025-01-01", 1, 3000.0, 3100.0),
        ]
        consumption = [
            _consumption("2025-01-01", 0, 100.0),  # 100 kWh × 1000
            _consumption("2025-01-01", 1, 100.0),  # 100 kWh × 3000
        ]
        result = calculate_weighted_prices(market, consumption)

        # Ağırlıklı PTF = (100×1000 + 100×3000) / (100+100) = 400000/200 = 2000
        assert result.weighted_ptf_tl_per_mwh == pytest.approx(2000.0)
        assert result.total_consumption_kwh == pytest.approx(200.0)

    def test_unequal_consumption_weighting(self):
        """Farklı tüketim ağırlıkları doğru hesaplanır."""
        market = [
            _market("2025-01-01", 0, 1000.0, 1100.0),
            _market("2025-01-01", 1, 2000.0, 2100.0),
        ]
        consumption = [
            _consumption("2025-01-01", 0, 300.0),  # 300 kWh × 1000
            _consumption("2025-01-01", 1, 100.0),  # 100 kWh × 2000
        ]
        result = calculate_weighted_prices(market, consumption)

        # Ağırlıklı PTF = (300×1000 + 100×2000) / (300+100) = 500000/400 = 1250
        assert result.weighted_ptf_tl_per_mwh == pytest.approx(1250.0)

    def test_equal_consumption_equals_arithmetic_avg(self):
        """Eşit tüketimde ağırlıklı ortalama = aritmetik ortalama."""
        market = [
            _market("2025-01-01", 0, 1000.0, 1100.0),
            _market("2025-01-01", 1, 2000.0, 2100.0),
            _market("2025-01-01", 2, 3000.0, 3100.0),
        ]
        consumption = [
            _consumption("2025-01-01", 0, 50.0),
            _consumption("2025-01-01", 1, 50.0),
            _consumption("2025-01-01", 2, 50.0),
        ]
        result = calculate_weighted_prices(market, consumption)

        assert result.weighted_ptf_tl_per_mwh == pytest.approx(
            result.arithmetic_avg_ptf, abs=0.01
        )

    def test_zero_consumption_raises(self):
        """Toplam tüketim 0 ise ValueError fırlatılır."""
        market = [_market("2025-01-01", 0, 1000.0, 1100.0)]
        consumption = [_consumption("2025-01-01", 0, 0.0)]

        with pytest.raises(ValueError, match="[Tt]oplam tüketim sıfır"):
            calculate_weighted_prices(market, consumption)

    def test_no_matching_hours_raises(self):
        """Eşleşen saat yoksa ValueError fırlatılır."""
        market = [_market("2025-01-01", 0, 1000.0, 1100.0)]
        consumption = [_consumption("2025-01-02", 0, 100.0)]  # Farklı tarih

        with pytest.raises(ValueError, match="eşleşen saat bulunamadı"):
            calculate_weighted_prices(market, consumption)

    def test_bounds_check(self):
        """Ağırlıklı PTF, min(PTF) ile max(PTF) arasında olmalı."""
        ptf_values = [500.0, 1000.0, 1500.0, 2000.0, 3000.0]
        market = [
            _market("2025-01-01", i, ptf, ptf + 100)
            for i, ptf in enumerate(ptf_values)
        ]
        consumption = [
            _consumption("2025-01-01", i, 10.0 + i * 5)
            for i in range(len(ptf_values))
        ]
        result = calculate_weighted_prices(market, consumption)

        assert result.weighted_ptf_tl_per_mwh >= min(ptf_values)
        assert result.weighted_ptf_tl_per_mwh <= max(ptf_values)

    def test_total_cost_kwh_mwh_conversion(self):
        """Toplam maliyet kWh→MWh dönüşümü doğru yapılır."""
        market = [_market("2025-01-01", 0, 2000.0, 2100.0)]
        consumption = [_consumption("2025-01-01", 0, 100.0)]

        result = calculate_weighted_prices(market, consumption)
        # total_cost = Σ(kWh × PTF) / 1000 = 100 × 2000 / 1000 = 200 TL
        assert result.total_cost_tl == pytest.approx(200.0)


class TestHourlyCosts:
    """calculate_hourly_costs tests."""

    def test_basic_hourly_cost(self):
        """Basit saatlik maliyet hesaplama."""
        market = [_market("2025-01-01", 10, 2000.0, 2100.0)]
        consumption = [_consumption("2025-01-01", 10, 100.0)]
        params = ImbalanceParams(
            forecast_error_rate=0.05,
            imbalance_cost_tl_per_mwh=50.0,
            smf_based_imbalance_enabled=False,
        )

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            multiplier=1.05,
            imbalance_params=params,
            dealer_commission_pct=0.0,
        )

        assert len(result.hour_costs) == 1
        entry = result.hour_costs[0]

        # base_cost = 100 × (2000 + 370) / 1000 = 237.0 TL
        assert entry.base_cost_tl == pytest.approx(237.0)

        # sales_price = 100 × (2000 + 370) × 1.05 / 1000 = 248.85 TL
        assert entry.sales_price_tl == pytest.approx(248.85)

        # margin = 248.85 - 237.0 = 11.85
        assert entry.margin_tl == pytest.approx(11.85)
        assert entry.is_loss_hour is False
        assert entry.time_zone == TimeZone.T1

    def test_loss_hour_detection(self):
        """Zarar saati tespiti: satış < maliyet."""
        # PTF çok yüksek bir saat — düşük katsayı ile zarar
        market = [
            _market("2025-01-01", 10, 2000.0, 2100.0),
            _market("2025-01-01", 18, 5000.0, 5100.0),  # Çok yüksek PTF
        ]
        consumption = [
            _consumption("2025-01-01", 10, 100.0),
            _consumption("2025-01-01", 18, 100.0),
        ]
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            multiplier=1.02,
            imbalance_params=params,
        )

        # Ağırlıklı PTF = (100×2000 + 100×5000) / 200 = 3500
        # Enerji maliyeti = 3500 + 370 = 3870
        # Saat 18 base_cost = 100 × (5000+370) / 1000 = 537.0
        # Saat 18 sales = 100 × 3870 × 1.02 / 1000 = 394.74
        # Saat 18 margin = 394.74 - 537.0 = -142.26 → zarar saati
        loss_hours = [e for e in result.hour_costs if e.is_loss_hour]
        assert len(loss_hours) >= 1

    def test_dealer_commission(self):
        """Bayi komisyonu brüt marjdan düşülür."""
        market = [_market("2025-01-01", 10, 2000.0, 2100.0)]
        consumption = [_consumption("2025-01-01", 10, 1000.0)]
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            multiplier=1.10,
            imbalance_params=params,
            dealer_commission_pct=5.0,
        )

        # base_cost = 1000 × (2000+370) / 1000 = 2370.0
        # sales = 1000 × (2000+370) × 1.10 / 1000 = 2607.0
        # gross_margin = 2607.0 - 2370.0 = 237.0
        # dealer_commission = 237.0 × 5 / 100 = 11.85
        # net_margin = 237.0 - 11.85 - 0 = 225.15
        assert result.total_gross_margin_tl == pytest.approx(237.0)
        assert result.total_net_margin_tl == pytest.approx(225.15)

    def test_supplier_real_cost(self):
        """Tedarikçi gerçek maliyet = Ağırlıklı_PTF + YEKDEM + Dengesizlik."""
        market = [_market("2025-01-01", 10, 2000.0, 2100.0)]
        consumption = [_consumption("2025-01-01", 10, 100.0)]
        params = ImbalanceParams(
            forecast_error_rate=0.05,
            imbalance_cost_tl_per_mwh=50.0,
            smf_based_imbalance_enabled=False,
        )

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=370.0,
            multiplier=1.05,
            imbalance_params=params,
        )

        # supplier_real_cost = 2000 + 370 + 2.5 = 2372.5
        assert result.supplier_real_cost_tl_per_mwh == pytest.approx(2372.5)


# ═══════════════════════════════════════════════════════════════════════════════
# KRİTİK: kWh/MWh Dönüşüm Doğrulama
# ═══════════════════════════════════════════════════════════════════════════════

class TestKwhMwhConversion:
    """kWh vs MWh dönüşüm doğrulama — 1000x hata koruması."""

    def test_100kwh_2000ptf_equals_200tl(self):
        """100 kWh × 2000 TL/MWh / 1000 = 200 TL (doğru).
        NOT: 100 kWh × 2000 TL/MWh = 200000 TL (YANLIŞ — /1000 eksik).
        """
        market = [_market("2025-01-01", 10, 2000.0, 2100.0)]
        consumption = [_consumption("2025-01-01", 10, 100.0)]

        result = calculate_weighted_prices(market, consumption)
        assert result.total_cost_tl == pytest.approx(200.0)
        assert result.total_cost_tl != pytest.approx(200000.0)  # 1000x hata yok

    def test_hourly_base_cost_conversion(self):
        """Saatlik baz maliyet kWh→MWh dönüşümü."""
        market = [_market("2025-01-01", 10, 2000.0, 2100.0)]
        consumption = [_consumption("2025-01-01", 10, 100.0)]
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=0.0,
            multiplier=1.0,
            imbalance_params=params,
        )

        entry = result.hour_costs[0]
        # base_cost = 100 × 2000 / 1000 = 200 TL
        assert entry.base_cost_tl == pytest.approx(200.0)
        assert entry.base_cost_tl != pytest.approx(200000.0)

    def test_sales_price_conversion(self):
        """Satış fiyatı kWh→MWh dönüşümü."""
        market = [_market("2025-01-01", 10, 2000.0, 2100.0)]
        consumption = [_consumption("2025-01-01", 10, 100.0)]
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=0.0,
            multiplier=1.05,
            imbalance_params=params,
        )

        entry = result.hour_costs[0]
        # sales = 100 × 2000 × 1.05 / 1000 = 210 TL
        assert entry.sales_price_tl == pytest.approx(210.0)
        assert entry.sales_price_tl != pytest.approx(210000.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 9: Tüketim Profili Yükleme Servisi (DB testi)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConsumptionService:
    """save_consumption_profile DB tests — SQLite in-memory."""

    @pytest.fixture
    def db_session(self):
        """In-memory SQLite session for testing."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database import Base
        # Import pricing schemas so their tables are registered with Base
        import app.pricing.schemas  # noqa: F401

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    def test_save_new_profile(self, db_session):
        """Yeni profil kaydı oluşturulur."""
        from app.pricing.consumption_service import save_consumption_profile

        records = [
            ParsedConsumptionRecord(date="2025-01-01", hour=0, consumption_kwh=100.0),
            ParsedConsumptionRecord(date="2025-01-01", hour=1, consumption_kwh=150.0),
        ]

        profile = save_consumption_profile(
            db=db_session,
            customer_id="CUST-001",
            customer_name="Test Müşteri",
            period="2025-01",
            records=records,
            source="excel",
        )

        assert profile.id is not None
        assert profile.customer_id == "CUST-001"
        assert profile.period == "2025-01"
        assert profile.version == 1
        assert profile.is_active == 1
        assert profile.total_kwh == pytest.approx(250.0)
        assert len(profile.hourly_data) == 2

    def test_versioning_archives_old(self, db_session):
        """Aynı müşteri+dönem için tekrar yükleme → önceki arşivlenir."""
        from app.pricing.consumption_service import save_consumption_profile
        from app.pricing.schemas import ConsumptionProfile

        records_v1 = [
            ParsedConsumptionRecord(date="2025-01-01", hour=0, consumption_kwh=100.0),
        ]
        records_v2 = [
            ParsedConsumptionRecord(date="2025-01-01", hour=0, consumption_kwh=200.0),
        ]

        # İlk yükleme
        p1 = save_consumption_profile(
            db=db_session, customer_id="CUST-001", customer_name=None,
            period="2025-01", records=records_v1,
        )
        assert p1.version == 1
        assert p1.is_active == 1

        # İkinci yükleme
        p2 = save_consumption_profile(
            db=db_session, customer_id="CUST-001", customer_name=None,
            period="2025-01", records=records_v2,
        )
        assert p2.version == 2
        assert p2.is_active == 1

        # Eski profil arşivlenmiş olmalı
        db_session.refresh(p1)
        assert p1.is_active == 0

        # Aktif profil sayısı 1 olmalı
        active_count = (
            db_session.query(ConsumptionProfile)
            .filter(
                ConsumptionProfile.customer_id == "CUST-001",
                ConsumptionProfile.period == "2025-01",
                ConsumptionProfile.is_active == 1,
            )
            .count()
        )
        assert active_count == 1

    def test_data_version_created(self, db_session):
        """data_versions tablosuna kayıt eklenir."""
        from app.pricing.consumption_service import save_consumption_profile
        from app.pricing.schemas import DataVersion

        records = [
            ParsedConsumptionRecord(date="2025-01-01", hour=0, consumption_kwh=100.0),
        ]

        save_consumption_profile(
            db=db_session, customer_id="CUST-002", customer_name=None,
            period="2025-01", records=records,
        )

        dv = (
            db_session.query(DataVersion)
            .filter(
                DataVersion.data_type == "consumption",
                DataVersion.customer_id == "CUST-002",
                DataVersion.period == "2025-01",
            )
            .first()
        )
        assert dv is not None
        assert dv.version == 1
        assert dv.row_count == 1
        assert dv.is_active == 1



# ═══════════════════════════════════════════════════════════════════════════════
# Task 11.3–11.5: Zorunlu Property Testleri (Hesap Motoru Güvenlik Freni)
# ═══════════════════════════════════════════════════════════════════════════════

from hypothesis import given, settings, assume
from hypothesis import strategies as st


class TestWeightedPriceProperties:
    """Property 8 & 9: Ağırlıklı ortalama sınır + eşit tüketim."""

    @given(
        ptf_values=st.lists(
            st.floats(min_value=100.0, max_value=10000.0),
            min_size=2, max_size=48,
        ),
        kwh_values=st.lists(
            st.floats(min_value=1.0, max_value=500.0),
            min_size=2, max_size=48,
        ),
    )
    @settings(max_examples=30, deadline=None)
    def test_weighted_ptf_within_min_max(self, ptf_values, kwh_values):
        """Property 8: Ağırlıklı PTF, min(PTF) ile max(PTF) arasında."""
        n = min(len(ptf_values), len(kwh_values))
        assume(n >= 2)

        market = [
            _market("2025-01-01", h % 24, ptf_values[h], ptf_values[h] + 50)
            for h in range(n)
        ]
        consumption = [
            _consumption("2025-01-01", h % 24, kwh_values[h])
            for h in range(n)
        ]

        total_kwh = sum(c.consumption_kwh for c in consumption)
        assume(total_kwh > 0)

        result = calculate_weighted_prices(market, consumption)

        ptf_min = min(ptf_values[:n])
        ptf_max = max(ptf_values[:n])
        assert result.weighted_ptf_tl_per_mwh >= ptf_min - 0.01
        assert result.weighted_ptf_tl_per_mwh <= ptf_max + 0.01

    @given(
        ptf_values=st.lists(
            st.floats(min_value=100.0, max_value=10000.0),
            min_size=2, max_size=24,
        ),
        equal_kwh=st.floats(min_value=10.0, max_value=500.0),
    )
    @settings(max_examples=30, deadline=None)
    def test_equal_consumption_equals_arithmetic_avg(self, ptf_values, equal_kwh):
        """Property 9: Eşit tüketimde ağırlıklı ortalama = aritmetik ortalama."""
        n = len(ptf_values)

        market = [
            _market("2025-01-01", h % 24, ptf_values[h], ptf_values[h] + 50)
            for h in range(n)
        ]
        consumption = [
            _consumption("2025-01-01", h % 24, equal_kwh)
            for h in range(n)
        ]

        result = calculate_weighted_prices(market, consumption)

        assert result.weighted_ptf_tl_per_mwh == pytest.approx(
            result.arithmetic_avg_ptf, abs=0.02,
        )


class TestLossHourProperties:
    """Property 10: Zarar saati tutarlılığı."""

    @given(
        ptf_base=st.floats(min_value=500.0, max_value=5000.0),
        ptf_spread=st.floats(min_value=10.0, max_value=300.0),
        kwh_base=st.floats(min_value=10.0, max_value=300.0),
        multiplier=st.floats(min_value=1.01, max_value=1.20),
        yekdem=st.floats(min_value=0.0, max_value=1000.0),
    )
    @settings(max_examples=30, deadline=None)
    def test_loss_hour_margin_negative(
        self, ptf_base, ptf_spread, kwh_base, multiplier, yekdem,
    ):
        """Property 10: is_loss_hour=True ↔ margin_tl < 0."""
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

        params = ImbalanceParams(forecast_error_rate=0.0)
        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=yekdem,
            multiplier=multiplier,
            imbalance_params=params,
        )

        for entry in result.hour_costs:
            if entry.is_loss_hour:
                assert entry.margin_tl < 0, (
                    f"is_loss_hour=True but margin={entry.margin_tl} >= 0"
                )
            else:
                assert entry.margin_tl >= 0, (
                    f"is_loss_hour=False but margin={entry.margin_tl} < 0"
                )
