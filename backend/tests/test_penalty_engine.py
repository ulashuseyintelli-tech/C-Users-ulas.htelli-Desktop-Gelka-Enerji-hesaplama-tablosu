"""
Penalty Engine Tests

Test matrisi:
- Reaktif limit altında: ceza 0
- Reaktif limit üstünde: excess doğru
- Kapasitif limit üstünde: excess doğru
- Demand aşımı yok: 0
- Demand aşım kademeleri: boundary test (%5, %10, %20)
- Recurrence multiplier uygulanıyor mu?
"""

import pytest
from app.penalty_models import (
    PenaltyPolicy,
    PenaltyRates,
    PenaltyInput,
    FacilityProfile,
    VoltageLevel,
    TermType,
    TariffGroup,
    DemandPeriod,
    PenaltyStatus,
    RecurrenceLevel,
    DemandTier,
    ConfidenceLevel,
)
from app.penalty_engine import PenaltyEngine, calculate_penalty, quick_penalty_check


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def default_policy():
    return PenaltyPolicy()


@pytest.fixture
def default_rates():
    return PenaltyRates(
        distribution_company="TEST",
        period="2025-01",
        reactive_unit_price_tl_per_kvarh=0.50,
        capacitive_unit_price_tl_per_kvarh=0.50,
        demand_excess_unit_price_tl_per_kw=50.0,
        source="test"
    )


@pytest.fixture
def basic_facility():
    return FacilityProfile(
        facility_id="TEST-001",
        contract_power_kw=100.0,
        voltage_level=VoltageLevel.AG,
        term_type=TermType.SINGLE,
        tariff_group=TariffGroup.SANAYI,
        demand_period=DemandPeriod.MIN_15,
        distribution_company="TEST"
    )


@pytest.fixture
def engine(default_policy):
    return PenaltyEngine(policy=default_policy)


# ═══════════════════════════════════════════════════════════════════════════════
# REACTIVE PENALTY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestReactivePenalty:
    """Reaktif ceza testleri"""
    
    def test_reactive_under_limit_no_penalty(self, engine, basic_facility, default_rates):
        """Reaktif limit altında: ceza 0"""
        # 10000 kWh × 0.20 = 2000 kVArh limit
        # 1500 kVArh < 2000 kVArh → ceza yok
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=1500,  # Limit altında
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.reactive_inductive.penalty_tl == 0
        assert result.reactive_inductive.excess_kvarh == 0
        assert result.reactive_inductive.status == PenaltyStatus.OK
    
    def test_reactive_at_limit_no_penalty(self, engine, basic_facility, default_rates):
        """Reaktif tam limitte: ceza 0"""
        # 10000 kWh × 0.20 = 2000 kVArh limit
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=2000,  # Tam limit
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.reactive_inductive.penalty_tl == 0
        assert result.reactive_inductive.excess_kvarh == 0
    
    def test_reactive_over_limit_penalty(self, engine, basic_facility, default_rates):
        """Reaktif limit üstünde: ceza hesaplanır"""
        # 10000 kWh × 0.20 = 2000 kVArh limit
        # 2500 kVArh - 2000 kVArh = 500 kVArh excess
        # 500 × 0.50 = 250 TL ceza
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=2500,  # Limit üstü
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.reactive_inductive.excess_kvarh == 500
        assert result.reactive_inductive.penalty_tl == 250
        assert result.reactive_inductive.status == PenaltyStatus.CRITICAL
    
    def test_reactive_warning_threshold(self, engine, basic_facility, default_rates):
        """Reaktif uyarı eşiği: %80-100 arası"""
        # 10000 kWh × 0.20 = 2000 kVArh limit
        # 1700 kVArh = %85 kullanım → WARNING
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=1700,  # %85
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.reactive_inductive.penalty_tl == 0
        assert result.reactive_inductive.status == PenaltyStatus.WARNING
        assert result.reactive_inductive.utilization_ratio == 0.85


class TestCapacitivePenalty:
    """Kapasitif ceza testleri"""
    
    def test_capacitive_under_limit_no_penalty(self, engine, basic_facility, default_rates):
        """Kapasitif limit altında: ceza 0"""
        # 10000 kWh × 0.15 = 1500 kVArh limit
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_capacitive_kvarh=1000,  # Limit altında
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.reactive_capacitive.penalty_tl == 0
        assert result.reactive_capacitive.excess_kvarh == 0
        assert result.reactive_capacitive.status == PenaltyStatus.OK
    
    def test_capacitive_over_limit_penalty(self, engine, basic_facility, default_rates):
        """Kapasitif limit üstünde: ceza hesaplanır"""
        # 10000 kWh × 0.15 = 1500 kVArh limit
        # 2000 kVArh - 1500 kVArh = 500 kVArh excess
        # 500 × 0.50 = 250 TL ceza
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_capacitive_kvarh=2000,  # Limit üstü
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.reactive_capacitive.excess_kvarh == 500
        assert result.reactive_capacitive.penalty_tl == 250
        assert result.reactive_capacitive.status == PenaltyStatus.CRITICAL


# ═══════════════════════════════════════════════════════════════════════════════
# DEMAND PENALTY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestDemandPenalty:
    """Güç aşım ceza testleri"""
    
    def test_demand_no_excess_no_penalty(self, engine, basic_facility, default_rates):
        """Demand aşımı yok: ceza 0"""
        # Sözleşme: 100 kW, Gerçekleşen: 70 kW (warning threshold altında)
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=70,  # Sözleşme altında ve warning threshold altında
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.penalty_tl == 0
        assert result.demand.excess_kw == 0
        assert result.demand.status == PenaltyStatus.OK
    
    def test_demand_at_contract_no_penalty(self, engine, basic_facility, default_rates):
        """Demand tam sözleşmede: ceza 0"""
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=100,  # Tam sözleşme
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.penalty_tl == 0
        assert result.demand.excess_kw == 0
    
    def test_demand_tier_1_penalty(self, engine, basic_facility, default_rates):
        """Demand %0-5 aşım: 1x katsayı"""
        # Sözleşme: 100 kW, Gerçekleşen: 104 kW
        # Aşım: 4 kW (%4 < %5) → 1x katsayı
        # 4 × 50 × 1.0 = 200 TL
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=104,
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.excess_kw == 4
        assert result.demand.excess_ratio == 0.04
        assert result.demand.tier_multiplier == 1.0
        assert result.demand.penalty_tl == 200
    
    def test_demand_tier_2_penalty(self, engine, basic_facility, default_rates):
        """Demand %5-10 aşım: 1.5x katsayı"""
        # Sözleşme: 100 kW, Gerçekleşen: 108 kW
        # Aşım: 8 kW (%8, %5-10 arası) → 1.5x katsayı
        # 8 × 50 × 1.5 = 600 TL
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=108,
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.excess_kw == 8
        assert result.demand.tier_multiplier == 1.5
        assert result.demand.penalty_tl == 600
    
    def test_demand_tier_3_penalty(self, engine, basic_facility, default_rates):
        """Demand %10-20 aşım: 2x katsayı"""
        # Sözleşme: 100 kW, Gerçekleşen: 115 kW
        # Aşım: 15 kW (%15, %10-20 arası) → 2x katsayı
        # 15 × 50 × 2.0 = 1500 TL
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=115,
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.excess_kw == 15
        assert result.demand.tier_multiplier == 2.0
        assert result.demand.penalty_tl == 1500
    
    def test_demand_tier_4_penalty(self, engine, basic_facility, default_rates):
        """Demand %20+ aşım: 3x katsayı"""
        # Sözleşme: 100 kW, Gerçekleşen: 130 kW
        # Aşım: 30 kW (%30, %20+) → 3x katsayı
        # 30 × 50 × 3.0 = 4500 TL
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=130,
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.excess_kw == 30
        assert result.demand.tier_multiplier == 3.0
        assert result.demand.penalty_tl == 4500
    
    def test_demand_boundary_5_percent(self, engine, basic_facility, default_rates):
        """Boundary test: Tam %5 aşım"""
        # Sözleşme: 100 kW, Gerçekleşen: 105 kW
        # Aşım: 5 kW (tam %5) → 1.0x (eşik dahil)
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=105,
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.excess_ratio == 0.05
        assert result.demand.tier_multiplier == 1.0
    
    def test_demand_boundary_10_percent(self, engine, basic_facility, default_rates):
        """Boundary test: Tam %10 aşım"""
        # Sözleşme: 100 kW, Gerçekleşen: 110 kW
        # Aşım: 10 kW (tam %10) → 1.5x (eşik dahil)
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=110,
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.excess_ratio == 0.10
        assert result.demand.tier_multiplier == 1.5


class TestDemandRecurrence:
    """Güç aşım tekrar katsayısı testleri"""
    
    def test_first_excess_no_recurrence_multiplier(self, engine, default_rates):
        """İlk aşım: recurrence multiplier 1x"""
        facility = FacilityProfile(
            contract_power_kw=100,
            voltage_level=VoltageLevel.AG,
            distribution_company="TEST",
            demand_excess_history=[]  # Geçmiş yok
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=110,
            facility=facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.recurrence_level == RecurrenceLevel.FIRST
        assert result.demand.recurrence_multiplier == 1.0
    
    def test_repeat_excess_2x_multiplier(self, engine, default_rates):
        """Tekrar aşım: recurrence multiplier 2x"""
        facility = FacilityProfile(
            contract_power_kw=100,
            voltage_level=VoltageLevel.AG,
            distribution_company="TEST",
            demand_excess_history=["2024-12"]  # 1 ay önce aşım
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=110,
            facility=facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.recurrence_level == RecurrenceLevel.REPEAT
        assert result.demand.recurrence_multiplier == 2.0
    
    def test_chronic_excess_3x_multiplier(self, engine, default_rates):
        """Kronik aşım (3+ kez): recurrence multiplier 3x"""
        facility = FacilityProfile(
            contract_power_kw=100,
            voltage_level=VoltageLevel.AG,
            distribution_company="TEST",
            demand_excess_history=["2024-10", "2024-11", "2024-12"]  # 3 kez
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=110,
            facility=facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.recurrence_level == RecurrenceLevel.CHRONIC
        assert result.demand.recurrence_multiplier == 3.0
    
    def test_old_history_ignored(self, engine, default_rates):
        """12 aydan eski geçmiş sayılmaz"""
        facility = FacilityProfile(
            contract_power_kw=100,
            voltage_level=VoltageLevel.AG,
            distribution_company="TEST",
            demand_excess_history=["2023-06", "2023-07", "2023-08"]  # 18+ ay önce
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=110,
            facility=facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.demand.recurrence_level == RecurrenceLevel.FIRST
        assert result.demand.recurrence_multiplier == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# TOTAL PENALTY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestTotalPenalty:
    """Toplam ceza testleri"""
    
    def test_total_penalty_sum(self, engine, basic_facility, default_rates):
        """Toplam ceza = reaktif + kapasitif + demand"""
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=2500,  # 500 excess × 0.50 = 250 TL
            reactive_capacitive_kvarh=2000,  # 500 excess × 0.50 = 250 TL
            demand_max_kw=110,  # 10 kW × 50 × 1.5 = 750 TL
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        expected_reactive = 250 + 250
        expected_demand = 750
        expected_total = expected_reactive + expected_demand
        
        assert result.total_reactive_penalty_tl == expected_reactive
        assert result.total_demand_penalty_tl == expected_demand
        assert result.total_penalty_tl == expected_total
    
    def test_no_penalty_when_all_ok(self, engine, basic_facility, default_rates):
        """Tüm değerler limit içinde: toplam ceza 0"""
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=1000,  # Limit altı
            reactive_capacitive_kvarh=500,  # Limit altı
            demand_max_kw=80,  # Sözleşme altı
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.total_penalty_tl == 0


# ═══════════════════════════════════════════════════════════════════════════════
# RECOMMENDATIONS TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecommendations:
    """Öneri testleri"""
    
    def test_reactive_penalty_generates_recommendation(self, engine, basic_facility, default_rates):
        """Reaktif ceza varsa öneri üretilir"""
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=3000,  # Yüksek aşım
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert len(result.recommendations) > 0
        reactive_recs = [r for r in result.recommendations if r.category == "reactive"]
        assert len(reactive_recs) > 0
    
    def test_demand_penalty_generates_recommendation(self, engine, basic_facility, default_rates):
        """Demand ceza varsa öneri üretilir"""
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=150,  # Yüksek aşım
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        demand_recs = [r for r in result.recommendations if r.category == "demand"]
        assert len(demand_recs) > 0
    
    def test_no_penalty_no_critical_recommendations(self, engine, basic_facility, default_rates):
        """Ceza yoksa kritik öneri yok"""
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=1000,
            demand_max_kw=80,
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        critical_recs = [r for r in result.recommendations if r.priority == 1]
        assert len(critical_recs) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestConvenienceFunctions:
    """Yardımcı fonksiyon testleri"""
    
    def test_quick_penalty_check(self):
        """quick_penalty_check basit API çalışıyor"""
        result = quick_penalty_check(
            active_kwh=10000,
            reactive_kvarh=3000,  # Aşım var
            demand_kw=80,
            contract_kw=100
        )
        
        assert "reactive_penalty_tl" in result
        assert "demand_penalty_tl" in result
        assert "total_penalty_tl" in result
        assert "has_penalty" in result
        assert result["has_penalty"] == True
    
    def test_quick_penalty_check_no_penalty(self):
        """quick_penalty_check ceza yokken"""
        result = quick_penalty_check(
            active_kwh=10000,
            reactive_kvarh=1000,  # Limit altı
            demand_kw=80,
            contract_kw=100
        )
        
        assert result["has_penalty"] == False
        assert result["total_penalty_tl"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOM POLICY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCustomPolicy:
    """Özel policy testleri"""
    
    def test_custom_inductive_limit(self, basic_facility, default_rates):
        """Özel endüktif limit oranı"""
        custom_policy = PenaltyPolicy(
            policy_id="custom_test",
            inductive_limit_ratio=0.30  # %30 (default %20)
        )
        
        engine = PenaltyEngine(policy=custom_policy)
        
        # 10000 kWh × 0.30 = 3000 kVArh limit
        # 2500 kVArh < 3000 kVArh → ceza yok
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=2500,
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.reactive_inductive.penalty_tl == 0
        assert result.policy_id == "custom_test"
    
    def test_custom_demand_tiers(self, basic_facility, default_rates):
        """Özel demand kademeleri"""
        custom_policy = PenaltyPolicy(
            policy_id="custom_tiers",
            demand_tiers=[
                DemandTier(threshold_ratio=0.10, multiplier=1.0),  # %0-10: 1x
                DemandTier(threshold_ratio=float('inf'), multiplier=2.0),  # %10+: 2x
            ]
        )
        
        engine = PenaltyEngine(policy=custom_policy)
        
        # %8 aşım → custom policy'de 1x
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=108,  # %8 aşım
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        # Default policy'de 1.5x olurdu, custom'da 1.0x
        assert result.demand.tier_multiplier == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE CASE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Sınır durumları testleri"""
    
    def test_zero_active_kwh_no_divide_by_zero(self, engine, default_rates):
        """active_kwh=0 iken divide-by-zero yok"""
        facility = FacilityProfile(
            contract_power_kw=100,
            voltage_level=VoltageLevel.AG,
            distribution_company="TEST"
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=0,  # Sıfır tüketim
            reactive_inductive_kvarh=100,  # Reaktif var ama limit 0
            facility=facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        # Limit 0 olduğu için tüm reaktif aşım sayılır
        assert result.reactive_inductive.limit_kvarh == 0
        assert result.reactive_inductive.excess_kvarh == 100
    
    def test_zero_contract_kw_raises_error(self, engine, default_rates):
        """contract_kw=0 iken hata fırlatır"""
        facility = FacilityProfile(
            contract_power_kw=0,  # Geçersiz
            voltage_level=VoltageLevel.AG,
            distribution_company="TEST"
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=100,
            facility=facility,
            rates_override=default_rates
        )
        
        with pytest.raises(ValueError, match="contract_power_kw pozitif olmalı"):
            engine.calculate(input)
    
    def test_negative_active_kwh_raises_error(self, engine, default_rates):
        """Negatif active_kwh hata fırlatır"""
        facility = FacilityProfile(
            contract_power_kw=100,
            voltage_level=VoltageLevel.AG,
            distribution_company="TEST"
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=-1000,  # Negatif
            facility=facility,
            rates_override=default_rates
        )
        
        with pytest.raises(ValueError, match="active_kwh negatif olamaz"):
            engine.calculate(input)
    
    def test_negative_reactive_raises_error(self, engine, default_rates):
        """Negatif reaktif hata fırlatır"""
        facility = FacilityProfile(
            contract_power_kw=100,
            voltage_level=VoltageLevel.AG,
            distribution_company="TEST"
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=-500,  # Negatif
            facility=facility,
            rates_override=default_rates
        )
        
        with pytest.raises(ValueError, match="reactive_inductive_kvarh negatif olamaz"):
            engine.calculate(input)
    
    def test_negative_demand_raises_error(self, engine, default_rates):
        """Negatif demand hata fırlatır"""
        facility = FacilityProfile(
            contract_power_kw=100,
            voltage_level=VoltageLevel.AG,
            distribution_company="TEST"
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            demand_max_kw=-50,  # Negatif
            facility=facility,
            rates_override=default_rates
        )
        
        with pytest.raises(ValueError, match="demand_max_kw negatif olamaz"):
            engine.calculate(input)
    
    def test_large_numbers_no_overflow(self, engine, default_rates):
        """Büyük sayılarla overflow yok"""
        facility = FacilityProfile(
            contract_power_kw=100000,  # 100 MW
            voltage_level=VoltageLevel.OG,
            distribution_company="TEST"
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=100_000_000,  # 100 GWh
            reactive_inductive_kvarh=50_000_000,  # 50 GVArh
            demand_max_kw=150000,  # 150 MW
            facility=facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        # Hesaplama tamamlanmalı
        assert result.total_penalty_tl > 0
        assert result.reactive_inductive.penalty_tl > 0
        assert result.demand.penalty_tl > 0
    
    def test_fallback_warning_in_result(self, engine):
        """Fallback kullanıldığında warning döner"""
        facility = FacilityProfile(
            contract_power_kw=100,
            voltage_level=VoltageLevel.AG,
            distribution_company="BILINMEYEN_SIRKET"  # Rate tablosunda yok
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=3000,
            facility=facility
            # rates_override yok - fallback olacak
        )
        
        result = engine.calculate(input)
        
        # Warning olmalı
        assert len(result.warnings) > 0
        assert "default" in result.warnings[0].lower() or "bulunamadı" in result.warnings[0].lower()
        assert result.rates_source == "default"
        # Confidence LOW olmalı
        assert result.confidence == ConfidenceLevel.LOW
    
    def test_known_rates_high_confidence(self, engine, basic_facility, default_rates):
        """Bilinen rate ile HIGH confidence"""
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=1000,
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.confidence == ConfidenceLevel.HIGH
        assert len(result.warnings) == 0
    
    def test_energy_included_flag(self, engine, basic_facility, default_rates):
        """energy_included flag ve notes kontrolü"""
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=1000,
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        # Enerji bedeli dahil değil
        assert result.energy_included == False
        assert "Enerji bedeli dahil değildir" in result.notes
    
    def test_rates_snapshot_present(self, engine, basic_facility, default_rates):
        """rates_snapshot audit trail için mevcut"""
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=1000,
            facility=basic_facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.rates_snapshot is not None
        assert "company" in result.rates_snapshot
        assert "period" in result.rates_snapshot
        assert "source" in result.rates_snapshot
        assert "reactive_unit_price" in result.rates_snapshot
        assert result.rates_snapshot["reactive_unit_price"] == default_rates.reactive_unit_price_tl_per_kvarh
    
    def test_no_penalty_path_empty_recommendations(self, engine, default_rates):
        """Ceza yokken kritik öneri yok"""
        facility = FacilityProfile(
            contract_power_kw=100,
            voltage_level=VoltageLevel.AG,
            distribution_company="TEST"
        )
        
        input = PenaltyInput(
            period="2025-01",
            active_kwh=10000,
            reactive_inductive_kvarh=500,  # Limit altı
            reactive_capacitive_kvarh=0,
            demand_max_kw=50,  # Sözleşme altı
            facility=facility,
            rates_override=default_rates
        )
        
        result = engine.calculate(input)
        
        assert result.total_penalty_tl == 0
        # Kritik öneri olmamalı
        critical_recs = [r for r in result.recommendations if r.priority == 1]
        assert len(critical_recs) == 0


class TestQuickAPIAssumptions:
    """Quick API varsayım testleri"""
    
    def test_quick_penalty_check_returns_assumptions(self):
        """quick_penalty_check assumptions döndürür"""
        result = quick_penalty_check(
            active_kwh=10000,
            reactive_kvarh=3000,
            demand_kw=80,
            contract_kw=100
        )
        
        assert "assumptions" in result
        assert len(result["assumptions"]) > 0
        # Default değerler listelenmeli
        assert any("voltage_level" in a for a in result["assumptions"])
        assert any("demand_period" in a for a in result["assumptions"])
    
    def test_quick_penalty_check_returns_warnings(self):
        """quick_penalty_check warnings döndürür"""
        result = quick_penalty_check(
            active_kwh=10000,
            reactive_kvarh=3000,
            demand_kw=80,
            contract_kw=100,
            distribution_company="BILINMEYEN"  # Fallback olacak
        )
        
        assert "warnings" in result
        # Fallback warning olmalı
        assert len(result["warnings"]) > 0
