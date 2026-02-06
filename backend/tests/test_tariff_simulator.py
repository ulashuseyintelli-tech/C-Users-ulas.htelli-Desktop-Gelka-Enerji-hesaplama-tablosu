"""
Tariff Simulator Tests

Test matrisi:
- Aynı input → deterministik order (stable sort)
- En ucuz senaryo seçimi doğru
- Enerji bedeli yokken rapor formatı doğru
- Mevcut tarife işaretleme doğru
- Karşılaştırma hesaplamaları doğru
"""

import pytest
from backend.app.penalty_models import (
    FacilityProfile,
    VoltageLevel,
    TermType,
    TariffGroup,
    DemandPeriod,
    TariffScenario,
    TariffSimulationInput,
)
from backend.app.tariff_simulator import (
    TariffSimulator,
    simulate_tariffs,
    quick_tariff_comparison,
    format_comparison_table,
    get_all_scenarios,
    get_common_scenarios,
)


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

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
def basic_input(basic_facility):
    return TariffSimulationInput(
        period="2025-01",
        active_kwh=10000,
        reactive_inductive_kvarh=1500,
        demand_max_kw=80,
        current_facility=basic_facility
    )


@pytest.fixture
def simulator():
    return TariffSimulator()


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO GENERATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestScenarioGeneration:
    """Senaryo üretim testleri"""
    
    def test_get_all_scenarios_not_empty(self):
        """Tüm senaryolar listesi boş değil"""
        scenarios = get_all_scenarios()
        assert len(scenarios) > 0
    
    def test_get_common_scenarios_not_empty(self):
        """Yaygın senaryolar listesi boş değil"""
        scenarios = get_common_scenarios()
        assert len(scenarios) > 0
    
    def test_scenario_key_format(self):
        """Senaryo key formatı doğru"""
        scenario = TariffScenario(
            tariff_group=TariffGroup.SANAYI,
            voltage_level=VoltageLevel.OG,
            term_type=TermType.MULTI
        )
        
        assert scenario.key == "sanayi/OG/cift_terim"
    
    def test_common_scenarios_include_sanayi(self):
        """Yaygın senaryolar sanayi içeriyor"""
        scenarios = get_common_scenarios()
        sanayi_scenarios = [s for s in scenarios if s.tariff_group == TariffGroup.SANAYI]
        assert len(sanayi_scenarios) >= 4  # AG/OG × TT/ÇT


# ═══════════════════════════════════════════════════════════════════════════════
# SIMULATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSimulation:
    """Simülasyon testleri"""
    
    def test_simulation_returns_report(self, simulator, basic_input):
        """Simülasyon rapor döndürür"""
        report = simulator.run(basic_input)
        
        assert report is not None
        assert report.period == "2025-01"
        assert report.current_scenario is not None
        assert len(report.all_scenarios) > 0
    
    def test_current_scenario_marked(self, simulator, basic_input):
        """Mevcut senaryo işaretlenir"""
        report = simulator.run(basic_input)
        
        current_marked = [s for s in report.all_scenarios if s.is_current]
        assert len(current_marked) == 1
        assert report.current_scenario.is_current == True
    
    def test_cheapest_scenario_marked(self, simulator, basic_input):
        """En ucuz senaryo işaretlenir"""
        report = simulator.run(basic_input)
        
        cheapest_marked = [s for s in report.all_scenarios if s.is_cheapest]
        assert len(cheapest_marked) == 1
        assert report.best_scenario.is_cheapest == True
    
    def test_scenarios_sorted_by_cost(self, simulator, basic_input):
        """Senaryolar maliyete göre sıralı"""
        report = simulator.run(basic_input)
        
        costs = [s.total_penalty_and_distribution_tl for s in report.all_scenarios]
        assert costs == sorted(costs)
    
    def test_scenarios_have_ranks(self, simulator, basic_input):
        """Senaryolar sıra numarası alır"""
        report = simulator.run(basic_input)
        
        ranks = [s.rank for s in report.all_scenarios]
        expected_ranks = list(range(1, len(report.all_scenarios) + 1))
        assert ranks == expected_ranks
    
    def test_deterministic_order(self, simulator, basic_input):
        """Aynı input → aynı sıralama (deterministik)"""
        report1 = simulator.run(basic_input)
        report2 = simulator.run(basic_input)
        
        keys1 = [s.tariff_key for s in report1.all_scenarios]
        keys2 = [s.tariff_key for s in report2.all_scenarios]
        
        assert keys1 == keys2


# ═══════════════════════════════════════════════════════════════════════════════
# COMPARISON TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestComparison:
    """Karşılaştırma testleri"""
    
    def test_saving_calculation(self, simulator, basic_input):
        """Tasarruf hesaplaması doğru"""
        report = simulator.run(basic_input)
        
        current_cost = report.current_scenario.total_penalty_and_distribution_tl
        best_cost = report.best_scenario.total_penalty_and_distribution_tl
        
        expected_saving = current_cost - best_cost
        assert abs(report.max_saving_tl - expected_saving) < 0.01
    
    def test_saving_percent_calculation(self, simulator, basic_input):
        """Tasarruf yüzdesi hesaplaması doğru"""
        report = simulator.run(basic_input)
        
        if report.current_scenario.total_penalty_and_distribution_tl > 0:
            expected_percent = (
                report.max_saving_tl / 
                report.current_scenario.total_penalty_and_distribution_tl * 100
            )
            assert abs(report.max_saving_percent - expected_percent) < 0.01
    
    def test_vs_current_saving_for_each_scenario(self, simulator, basic_input):
        """Her senaryo için mevcut duruma göre tasarruf hesaplanır"""
        report = simulator.run(basic_input)
        
        current_cost = report.current_scenario.total_penalty_and_distribution_tl
        
        for scenario in report.all_scenarios:
            expected_saving = current_cost - scenario.total_penalty_and_distribution_tl
            assert abs(scenario.vs_current_saving_tl - expected_saving) < 0.01
    
    def test_summary_generated(self, simulator, basic_input):
        """Özet metni oluşturulur"""
        report = simulator.run(basic_input)
        
        assert report.summary is not None
        assert len(report.summary) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# ENERGY COST TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnergyCost:
    """Enerji bedeli testleri"""
    
    def test_without_energy_cost(self, simulator, basic_facility):
        """Enerji bedeli olmadan simülasyon"""
        input = TariffSimulationInput(
            period="2025-01",
            active_kwh=10000,
            current_facility=basic_facility,
            include_energy_cost=False
        )
        
        report = simulator.run(input)
        
        for scenario in report.all_scenarios:
            assert scenario.energy_tl is None
            assert scenario.total_cost_tl is None
            assert scenario.total_penalty_and_distribution_tl > 0
    
    def test_with_energy_cost(self, simulator, basic_facility):
        """Enerji bedeli ile simülasyon"""
        input = TariffSimulationInput(
            period="2025-01",
            active_kwh=10000,
            current_facility=basic_facility,
            include_energy_cost=True,
            energy_unit_price_tl_per_kwh=2.50
        )
        
        report = simulator.run(input)
        
        for scenario in report.all_scenarios:
            assert scenario.energy_tl is not None
            assert scenario.energy_tl == 25000  # 10000 × 2.50
            assert scenario.total_cost_tl is not None


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOM SCENARIOS TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCustomScenarios:
    """Özel senaryo testleri"""
    
    def test_custom_scenario_list(self, simulator, basic_facility):
        """Özel senaryo listesi ile simülasyon"""
        custom_scenarios = [
            TariffScenario(tariff_group=TariffGroup.SANAYI, voltage_level=VoltageLevel.OG, term_type=TermType.MULTI),
            TariffScenario(tariff_group=TariffGroup.SANAYI, voltage_level=VoltageLevel.OG, term_type=TermType.SINGLE),
        ]
        
        input = TariffSimulationInput(
            period="2025-01",
            active_kwh=10000,
            current_facility=basic_facility,
            scenarios=custom_scenarios
        )
        
        report = simulator.run(input)
        
        # Mevcut senaryo (AG/TT) listede yoksa eklenir
        assert len(report.all_scenarios) >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestConvenienceFunctions:
    """Yardımcı fonksiyon testleri"""
    
    def test_quick_tariff_comparison(self):
        """quick_tariff_comparison basit API çalışıyor"""
        result = quick_tariff_comparison(
            active_kwh=10000,
            reactive_kvarh=1500,
            demand_kw=80,
            contract_kw=100,
            current_tariff_group="sanayi",
            current_voltage="AG",
            current_term="tek_terim"
        )
        
        assert "current_cost_tl" in result
        assert "best_tariff" in result
        assert "best_cost_tl" in result
        assert "potential_saving_tl" in result
        assert "potential_saving_percent" in result
    
    def test_format_comparison_table(self, simulator, basic_input):
        """format_comparison_table tablo formatı üretir"""
        report = simulator.run(basic_input)
        table = format_comparison_table(report)
        
        assert isinstance(table, str)
        assert "TARİFE KARŞILAŞTIRMA RAPORU" in table
        assert "Dağıtım" in table
        assert "Reaktif" in table
        assert "Güç Aşım" in table
        assert "TOPLAM" in table


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Sınır durumları testleri"""
    
    def test_zero_consumption(self, simulator, basic_facility):
        """Sıfır tüketim"""
        input = TariffSimulationInput(
            period="2025-01",
            active_kwh=0,
            current_facility=basic_facility
        )
        
        report = simulator.run(input)
        
        # Dağıtım bedeli 0 olmalı
        for scenario in report.all_scenarios:
            assert scenario.distribution_tl == 0
    
    def test_high_penalty_scenario(self, simulator, basic_facility):
        """Yüksek ceza senaryosu"""
        input = TariffSimulationInput(
            period="2025-01",
            active_kwh=100000,
            reactive_inductive_kvarh=50000,  # Çok yüksek reaktif
            demand_max_kw=200,  # Yüksek demand aşımı
            current_facility=basic_facility
        )
        
        report = simulator.run(input)
        
        # Cezalar hesaplanmış olmalı
        assert report.current_scenario.reactive_penalty_tl > 0
        assert report.current_scenario.demand_penalty_tl > 0
