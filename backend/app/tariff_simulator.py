"""
Tariff Simulator - Tarife Karşılaştırma Motoru

Bu modül farklı tarife senaryolarını simüle eder ve karşılaştırır.
Görselindeki tabloya benzer çıktı üretir: Dağıtım + Ceza kalemleri.

KULLANIM:
    from tariff_simulator import TariffSimulator
    
    simulator = TariffSimulator()
    report = simulator.run(TariffSimulationInput(...))
    
    # En ucuz senaryo
    print(report.best_scenario.tariff_key)
    print(report.max_saving_tl)
"""

import logging
from typing import Optional, List
from .penalty_models import (
    TariffGroup,
    VoltageLevel,
    TermType,
    TariffScenario,
    TariffSimulationInput,
    TariffSimulationResult,
    TariffComparisonReport,
    FacilityProfile,
    PenaltyInput,
    PenaltyPolicy,
)
from .penalty_engine import PenaltyEngine
from .distribution_tariffs import get_distribution_unit_price, TariffLookupResult

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT SCENARIOS
# ═══════════════════════════════════════════════════════════════════════════════

def get_all_scenarios() -> List[TariffScenario]:
    """Tüm olası tarife senaryolarını döndür"""
    scenarios = []
    
    # Sanayi senaryoları
    for voltage in [VoltageLevel.AG, VoltageLevel.OG]:
        for term in [TermType.SINGLE, TermType.MULTI]:
            scenarios.append(TariffScenario(
                tariff_group=TariffGroup.SANAYI,
                voltage_level=voltage,
                term_type=term
            ))
    
    # Ticarethane senaryoları
    for voltage in [VoltageLevel.AG, VoltageLevel.OG]:
        for term in [TermType.SINGLE, TermType.MULTI]:
            scenarios.append(TariffScenario(
                tariff_group=TariffGroup.TICARETHANE,
                voltage_level=voltage,
                term_type=term
            ))
    
    # Mesken senaryoları (genelde AG)
    for term in [TermType.SINGLE, TermType.MULTI]:
        scenarios.append(TariffScenario(
            tariff_group=TariffGroup.MESKEN,
            voltage_level=VoltageLevel.AG,
            term_type=term
        ))
    
    # Tarımsal senaryolar
    for voltage in [VoltageLevel.AG, VoltageLevel.OG]:
        for term in [TermType.SINGLE, TermType.MULTI]:
            scenarios.append(TariffScenario(
                tariff_group=TariffGroup.TARIMSAL,
                voltage_level=voltage,
                term_type=term
            ))
    
    # Aydınlatma senaryoları
    for voltage in [VoltageLevel.AG, VoltageLevel.OG]:
        scenarios.append(TariffScenario(
            tariff_group=TariffGroup.AYDINLATMA,
            voltage_level=voltage,
            term_type=TermType.SINGLE
        ))
    
    return scenarios


def get_common_scenarios() -> List[TariffScenario]:
    """Yaygın kullanılan tarife senaryolarını döndür"""
    return [
        # Sanayi - en yaygın
        TariffScenario(tariff_group=TariffGroup.SANAYI, voltage_level=VoltageLevel.OG, term_type=TermType.MULTI),
        TariffScenario(tariff_group=TariffGroup.SANAYI, voltage_level=VoltageLevel.OG, term_type=TermType.SINGLE),
        TariffScenario(tariff_group=TariffGroup.SANAYI, voltage_level=VoltageLevel.AG, term_type=TermType.MULTI),
        TariffScenario(tariff_group=TariffGroup.SANAYI, voltage_level=VoltageLevel.AG, term_type=TermType.SINGLE),
        
        # Ticarethane
        TariffScenario(tariff_group=TariffGroup.TICARETHANE, voltage_level=VoltageLevel.OG, term_type=TermType.MULTI),
        TariffScenario(tariff_group=TariffGroup.TICARETHANE, voltage_level=VoltageLevel.OG, term_type=TermType.SINGLE),
        TariffScenario(tariff_group=TariffGroup.TICARETHANE, voltage_level=VoltageLevel.AG, term_type=TermType.MULTI),
        TariffScenario(tariff_group=TariffGroup.TICARETHANE, voltage_level=VoltageLevel.AG, term_type=TermType.SINGLE),
        
        # Mesken
        TariffScenario(tariff_group=TariffGroup.MESKEN, voltage_level=VoltageLevel.AG, term_type=TermType.SINGLE),
        TariffScenario(tariff_group=TariffGroup.MESKEN, voltage_level=VoltageLevel.AG, term_type=TermType.MULTI),
        
        # Tarımsal
        TariffScenario(tariff_group=TariffGroup.TARIMSAL, voltage_level=VoltageLevel.OG, term_type=TermType.SINGLE),
        TariffScenario(tariff_group=TariffGroup.TARIMSAL, voltage_level=VoltageLevel.AG, term_type=TermType.SINGLE),
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# TARIFF SIMULATOR
# ═══════════════════════════════════════════════════════════════════════════════

class TariffSimulator:
    """
    Tarife simülasyon motoru.
    
    Farklı tarife senaryolarını simüle eder ve karşılaştırır.
    """
    
    def __init__(self, penalty_engine: Optional[PenaltyEngine] = None):
        """
        Args:
            penalty_engine: Ceza hesaplama motoru (None = default)
        """
        self.penalty_engine = penalty_engine or PenaltyEngine()
    
    def run(self, input: TariffSimulationInput) -> TariffComparisonReport:
        """
        Tarife simülasyonu çalıştır.
        
        Args:
            input: TariffSimulationInput objesi
        
        Returns:
            TariffComparisonReport objesi
        """
        # Senaryoları belirle
        scenarios = input.scenarios or get_common_scenarios()
        
        # Mevcut senaryoyu belirle
        current_scenario = TariffScenario(
            tariff_group=input.current_facility.tariff_group,
            voltage_level=input.current_facility.voltage_level,
            term_type=input.current_facility.term_type
        )
        
        # Her senaryo için simüle et
        results: List[TariffSimulationResult] = []
        current_result: Optional[TariffSimulationResult] = None
        
        for scenario in scenarios:
            result = self._simulate_scenario(input, scenario)
            if result:
                results.append(result)
                
                # Mevcut senaryo mu?
                if scenario.key == current_scenario.key:
                    result.is_current = True
                    current_result = result
        
        # Mevcut senaryo listede yoksa ekle
        if current_result is None:
            current_result = self._simulate_scenario(input, current_scenario)
            if current_result:
                current_result.is_current = True
                results.append(current_result)
        
        # Mevcut senaryo hala None ise (tarife bulunamadı)
        if current_result is None:
            # Fallback: İlk sonucu mevcut kabul et
            if results:
                current_result = results[0]
                current_result.is_current = True
            else:
                raise ValueError("Hiçbir tarife senaryosu simüle edilemedi")
        
        # Karşılaştırma hesapla
        current_cost = current_result.total_penalty_and_distribution_tl
        for result in results:
            result.vs_current_saving_tl = round(
                current_cost - result.total_penalty_and_distribution_tl, 2
            )
            result.vs_current_saving_percent = round(
                result.vs_current_saving_tl / current_cost * 100 
                if current_cost > 0 else 0, 2
            )
        
        # Sırala (en ucuzdan pahalıya) - STABLE + DETERMINISTIC
        # 4'lü key: aynı tutar çıktığında bile sıra değişmez
        results.sort(key=lambda x: (
            x.total_penalty_and_distribution_tl,
            x.total_penalty_tl,
            x.distribution_tl,
            x.tariff_key
        ))
        
        # Rank ata
        for i, result in enumerate(results):
            result.rank = i + 1
        
        # En ucuzu işaretle
        if results:
            results[0].is_cheapest = True
        
        # En iyi senaryo
        best_scenario = results[0] if results else current_result
        
        # Maksimum tasarruf
        max_saving_tl = round(current_cost - best_scenario.total_penalty_and_distribution_tl, 2)
        max_saving_percent = round(
            max_saving_tl / current_cost * 100 if current_cost > 0 else 0, 2
        )
        
        # Özet oluştur
        if max_saving_tl <= 0:
            summary = "Mevcut tarife optimal veya en uygun seçeneklerden biri."
        else:
            summary = (
                f"{best_scenario.tariff_key} tarifesine geçişle "
                f"aylık {max_saving_tl:,.0f} TL (%{max_saving_percent:.1f}) tasarruf mümkün."
            )
        
        return TariffComparisonReport(
            period=input.period,
            facility_id=input.current_facility.facility_id,
            current_scenario=current_result,
            all_scenarios=results,
            best_scenario=best_scenario,
            max_saving_tl=max_saving_tl,
            max_saving_percent=max_saving_percent,
            summary=summary
        )
    
    def _simulate_scenario(
        self,
        input: TariffSimulationInput,
        scenario: TariffScenario
    ) -> Optional[TariffSimulationResult]:
        """
        Tek bir senaryo için simülasyon yap.
        
        Args:
            input: Simülasyon girdisi
            scenario: Tarife senaryosu
        
        Returns:
            TariffSimulationResult veya None (tarife bulunamazsa)
        """
        # Dağıtım birim fiyatını al
        tariff_lookup = self._get_distribution_rate(scenario)
        if not tariff_lookup.success:
            logger.warning(f"Tarife bulunamadı: {scenario.key}")
            return None
        
        distribution_unit_price = tariff_lookup.unit_price
        
        # Dağıtım bedeli hesapla
        distribution_tl = input.active_kwh * distribution_unit_price
        
        # Ceza hesapla (senaryo için facility oluştur)
        scenario_facility = FacilityProfile(
            facility_id=input.current_facility.facility_id,
            contract_power_kw=input.current_facility.contract_power_kw,
            voltage_level=scenario.voltage_level,
            term_type=scenario.term_type,
            tariff_group=scenario.tariff_group,
            demand_period=input.current_facility.demand_period,
            has_compensation=input.current_facility.has_compensation,
            distribution_company=input.current_facility.distribution_company,
            demand_excess_history=input.current_facility.demand_excess_history
        )
        
        penalty_input = PenaltyInput(
            period=input.period,
            active_kwh=input.active_kwh,
            reactive_inductive_kvarh=input.reactive_inductive_kvarh,
            reactive_capacitive_kvarh=input.reactive_capacitive_kvarh,
            demand_max_kw=input.demand_max_kw,
            facility=scenario_facility
        )
        
        penalty_result = self.penalty_engine.calculate(penalty_input)
        
        # Enerji bedeli (opsiyonel)
        energy_tl = None
        total_cost_tl = None
        if input.include_energy_cost and input.energy_unit_price_tl_per_kwh:
            energy_tl = input.active_kwh * input.energy_unit_price_tl_per_kwh
            total_cost_tl = energy_tl + distribution_tl + penalty_result.total_penalty_tl
        
        # Toplam (ceza + dağıtım)
        total_penalty_and_distribution = distribution_tl + penalty_result.total_penalty_tl
        
        return TariffSimulationResult(
            scenario=scenario,
            tariff_key=scenario.key,
            distribution_tl=round(distribution_tl, 2),
            reactive_penalty_tl=penalty_result.total_reactive_penalty_tl,
            capacitive_penalty_tl=penalty_result.reactive_capacitive.penalty_tl,
            demand_penalty_tl=penalty_result.total_demand_penalty_tl,
            total_penalty_tl=penalty_result.total_penalty_tl,
            energy_tl=round(energy_tl, 2) if energy_tl else None,
            total_penalty_and_distribution_tl=round(total_penalty_and_distribution, 2),
            total_cost_tl=round(total_cost_tl, 2) if total_cost_tl else None
        )
    
    def _get_distribution_rate(self, scenario: TariffScenario) -> TariffLookupResult:
        """Dağıtım birim fiyatını al"""
        # Tarife grubunu normalize et
        tariff_group = scenario.tariff_group.value
        if tariff_group in ["ticarethane", "mesken", "aydinlatma"]:
            tariff_group = "kamu_ozel"
        
        return get_distribution_unit_price(
            tariff_group=tariff_group,
            voltage_level=scenario.voltage_level.value,
            term_type=scenario.term_type.value
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def simulate_tariffs(input: TariffSimulationInput) -> TariffComparisonReport:
    """
    Tarife simülasyonu çalıştır (convenience function).
    
    Args:
        input: TariffSimulationInput objesi
    
    Returns:
        TariffComparisonReport objesi
    """
    simulator = TariffSimulator()
    return simulator.run(input)


def quick_tariff_comparison(
    active_kwh: float,
    reactive_kvarh: float,
    demand_kw: float,
    contract_kw: float,
    current_tariff_group: str = "sanayi",
    current_voltage: str = "AG",
    current_term: str = "tek_terim",
    distribution_company: str = "default",
    period: str = "2025-01"
) -> dict:
    """
    Hızlı tarife karşılaştırması (basit API).
    
    Returns:
        {
            "current_cost_tl": float,
            "best_tariff": str,
            "best_cost_tl": float,
            "potential_saving_tl": float,
            "potential_saving_percent": float
        }
    """
    # Enum'lara çevir
    try:
        tariff_group = TariffGroup(current_tariff_group)
    except ValueError:
        tariff_group = TariffGroup.SANAYI
    
    try:
        voltage = VoltageLevel(current_voltage)
    except ValueError:
        voltage = VoltageLevel.AG
    
    try:
        term = TermType(current_term)
    except ValueError:
        term = TermType.SINGLE
    
    facility = FacilityProfile(
        contract_power_kw=contract_kw,
        voltage_level=voltage,
        term_type=term,
        tariff_group=tariff_group,
        distribution_company=distribution_company
    )
    
    input = TariffSimulationInput(
        period=period,
        active_kwh=active_kwh,
        reactive_inductive_kvarh=reactive_kvarh,
        demand_max_kw=demand_kw,
        current_facility=facility
    )
    
    report = simulate_tariffs(input)
    
    return {
        "current_cost_tl": report.current_scenario.total_penalty_and_distribution_tl,
        "best_tariff": report.best_scenario.tariff_key,
        "best_cost_tl": report.best_scenario.total_penalty_and_distribution_tl,
        "potential_saving_tl": report.max_saving_tl,
        "potential_saving_percent": report.max_saving_percent
    }


def format_comparison_table(report: TariffComparisonReport) -> str:
    """
    Karşılaştırma raporunu tablo formatında döndür.
    
    Görselindeki tabloya benzer format.
    """
    lines = []
    lines.append("=" * 100)
    lines.append(f"TARİFE KARŞILAŞTIRMA RAPORU - {report.period}")
    lines.append("=" * 100)
    lines.append("")
    lines.append(f"{'Sıra':<5} {'Tarife':<30} {'Dağıtım':>12} {'Reaktif':>12} {'Güç Aşım':>12} {'TOPLAM':>12} {'Tasarruf':>12}")
    lines.append("-" * 100)
    
    for result in report.all_scenarios:
        marker = " *" if result.is_current else ""
        best = " ✓" if result.is_cheapest else ""
        
        lines.append(
            f"{result.rank:<5} "
            f"{result.tariff_key:<28}{marker}{best} "
            f"{result.distribution_tl:>12,.2f} "
            f"{result.reactive_penalty_tl:>12,.2f} "
            f"{result.demand_penalty_tl:>12,.2f} "
            f"{result.total_penalty_and_distribution_tl:>12,.2f} "
            f"{result.vs_current_saving_tl:>+12,.2f}"
        )
    
    lines.append("-" * 100)
    lines.append(f"* Mevcut tarife  ✓ En ucuz senaryo")
    lines.append("")
    lines.append(f"ÖZET: {report.summary}")
    lines.append("")
    
    return "\n".join(lines)
