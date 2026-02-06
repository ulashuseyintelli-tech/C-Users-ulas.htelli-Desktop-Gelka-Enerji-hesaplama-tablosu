"""
Penalty Engine - Ceza Hesaplama Motoru

Bu modül reaktif, kapasitif ve güç aşım cezalarını hesaplar.
Policy-driven tasarım: Kurallar değişirse kod değişmez.

KULLANIM:
    from penalty_engine import PenaltyEngine
    
    engine = PenaltyEngine()
    result = engine.calculate(PenaltyInput(...))
    
    # Veya custom policy ile:
    engine = PenaltyEngine(policy=custom_policy)
"""

import logging
from typing import Optional, List
from .penalty_models import (
    PenaltyPolicy,
    PenaltyRates,
    PenaltyInput,
    PenaltyResult,
    ReactivePenaltyDetail,
    DemandPenaltyDetail,
    Recommendation,
    PenaltyStatus,
    RecurrenceLevel,
    DemandPeriod,
    ConfidenceLevel,
)
from .penalty_rates import get_penalty_rates, DEFAULT_RATES, PenaltyRatesNotFoundError

logger = logging.getLogger(__name__)


class PenaltyEngine:
    """
    Ceza hesaplama motoru.
    
    Policy-driven: Tüm kurallar ve limitler policy'den gelir.
    Deterministik: Aynı input → aynı output (test edilebilir).
    """
    
    def __init__(self, policy: Optional[PenaltyPolicy] = None):
        """
        Args:
            policy: Ceza hesaplama politikası (None = default)
        """
        self.policy = policy or PenaltyPolicy()
    
    def calculate(self, input: PenaltyInput) -> PenaltyResult:
        """
        Ceza hesapla.
        
        Args:
            input: PenaltyInput objesi
        
        Returns:
            PenaltyResult objesi
        """
        warnings: List[str] = []
        
        # Input validation
        if input.active_kwh < 0:
            raise ValueError("active_kwh negatif olamaz")
        if input.reactive_inductive_kvarh < 0:
            raise ValueError("reactive_inductive_kvarh negatif olamaz")
        if input.reactive_capacitive_kvarh < 0:
            raise ValueError("reactive_capacitive_kvarh negatif olamaz")
        if input.demand_max_kw < 0:
            raise ValueError("demand_max_kw negatif olamaz")
        if input.facility.contract_power_kw <= 0:
            raise ValueError("contract_power_kw pozitif olmalı")
        
        # Policy ve rates belirle
        policy = input.policy_override or self.policy
        rates, rate_warning, confidence, rates_snapshot = self._get_rates_with_warning(input)
        if rate_warning:
            warnings.append(rate_warning)
        
        # Reaktif ceza hesapla
        reactive_inductive = self._calculate_reactive(
            active_kwh=input.active_kwh,
            reactive_kvarh=input.reactive_inductive_kvarh,
            limit_ratio=policy.inductive_limit_ratio,
            unit_price=rates.reactive_unit_price_tl_per_kvarh,
            warning_threshold=policy.warning_threshold_ratio,
            penalty_type="inductive"
        )
        
        reactive_capacitive = self._calculate_reactive(
            active_kwh=input.active_kwh,
            reactive_kvarh=input.reactive_capacitive_kvarh,
            limit_ratio=policy.capacitive_limit_ratio,
            unit_price=rates.capacitive_unit_price_tl_per_kvarh,
            warning_threshold=policy.warning_threshold_ratio,
            penalty_type="capacitive"
        )
        
        # Güç aşım cezası hesapla
        demand = self._calculate_demand(
            contract_kw=input.facility.contract_power_kw,
            actual_kw=input.demand_max_kw,
            unit_price=rates.demand_excess_unit_price_tl_per_kw,
            policy=policy,
            excess_history=input.facility.demand_excess_history,
            current_period=input.period,
            warning_threshold=policy.warning_threshold_ratio
        )
        
        # Toplamlar
        total_reactive = reactive_inductive.penalty_tl + reactive_capacitive.penalty_tl
        total_demand = demand.penalty_tl
        total_penalty = total_reactive + total_demand
        
        # Öneriler oluştur
        recommendations = self._generate_recommendations(
            reactive_inductive=reactive_inductive,
            reactive_capacitive=reactive_capacitive,
            demand=demand,
            facility=input.facility,
            total_penalty=total_penalty
        )
        
        return PenaltyResult(
            period=input.period,
            facility_id=input.facility.facility_id,
            reactive_inductive=reactive_inductive,
            reactive_capacitive=reactive_capacitive,
            demand=demand,
            total_reactive_penalty_tl=round(total_reactive, 2),
            total_demand_penalty_tl=round(total_demand, 2),
            total_penalty_tl=round(total_penalty, 2),
            recommendations=recommendations,
            policy_id=policy.policy_id,
            rates_source=rates.source,
            warnings=warnings,
            confidence=confidence,
            rates_snapshot=rates_snapshot,
            energy_included=False,
            notes=["Enerji bedeli dahil değildir"]
        )
    
    def _get_rates_with_warning(self, input: PenaltyInput) -> tuple[PenaltyRates, Optional[str], ConfidenceLevel, dict]:
        """
        Rate provider'dan fiyatları al, fallback olduysa warning döndür.
        
        Returns:
            (rates, warning_message, confidence, rates_snapshot)
            
        Snapshot her zaman rates belirlenir belirlenmez üretilir (scope güvenliği).
        """
        def _make_snapshot(rates: PenaltyRates) -> dict:
            return {
                "company": rates.distribution_company,
                "period": rates.period,
                "source": rates.source,
                "reactive_unit_price": rates.reactive_unit_price_tl_per_kvarh,
                "capacitive_unit_price": rates.capacitive_unit_price_tl_per_kvarh,
                "demand_unit_price": rates.demand_excess_unit_price_tl_per_kw,
            }
        
        if input.rates_override:
            rates = input.rates_override
            return rates, None, ConfidenceLevel.HIGH, _make_snapshot(rates)
        
        try:
            rates = get_penalty_rates(
                distribution_company=input.facility.distribution_company,
                period=input.period,
                fallback_to_default=False  # PROD: fallback kapalı
            )
            return rates, None, ConfidenceLevel.HIGH, _make_snapshot(rates)
        except PenaltyRatesNotFoundError as e:
            # Fallback to default with warning + LOW confidence
            warning = (
                f"UYARI: Bölge/dönem için rate bulunamadı "
                f"({e.company}/{e.period}), "
                f"default değerler kullanıldı. Sonuçlar doğrulanmalı."
            )
            logger.warning(warning)
            rates = DEFAULT_RATES
            return rates, warning, ConfidenceLevel.LOW, _make_snapshot(rates)
    
    def _calculate_reactive(
        self,
        active_kwh: float,
        reactive_kvarh: float,
        limit_ratio: float,
        unit_price: float,
        warning_threshold: float,
        penalty_type: str
    ) -> ReactivePenaltyDetail:
        """
        Reaktif/Kapasitif ceza hesapla.
        
        Formül:
            limit = active_kwh × limit_ratio
            excess = max(0, reactive_kvarh - limit)
            penalty = excess × unit_price
        """
        # Limit hesapla
        limit_kvarh = active_kwh * limit_ratio
        
        # Aşım hesapla
        excess_kvarh = max(0, reactive_kvarh - limit_kvarh)
        
        # Ceza hesapla
        penalty_tl = excess_kvarh * unit_price
        
        # Kullanım oranı
        utilization = reactive_kvarh / limit_kvarh if limit_kvarh > 0 else 0
        
        # Durum belirle
        if excess_kvarh > 0:
            status = PenaltyStatus.CRITICAL
        elif utilization >= warning_threshold:
            status = PenaltyStatus.WARNING
        else:
            status = PenaltyStatus.OK
        
        logger.debug(
            f"{penalty_type.upper()} reactive: "
            f"limit={limit_kvarh:.2f}, actual={reactive_kvarh:.2f}, "
            f"excess={excess_kvarh:.2f}, penalty={penalty_tl:.2f} TL"
        )
        
        return ReactivePenaltyDetail(
            limit_kvarh=round(limit_kvarh, 2),
            actual_kvarh=round(reactive_kvarh, 2),
            excess_kvarh=round(excess_kvarh, 2),
            unit_price_tl=unit_price,
            penalty_tl=round(penalty_tl, 2),
            status=status,
            utilization_ratio=round(utilization, 4)
        )
    
    def _calculate_demand(
        self,
        contract_kw: float,
        actual_kw: float,
        unit_price: float,
        policy: PenaltyPolicy,
        excess_history: List[str],
        current_period: str,
        warning_threshold: float
    ) -> DemandPenaltyDetail:
        """
        Güç aşım cezası hesapla.
        
        Formül:
            excess_kw = max(0, actual_kw - contract_kw)
            excess_ratio = excess_kw / contract_kw
            tier_multiplier = policy.get_demand_multiplier(excess_ratio)
            recurrence_multiplier = policy.get_recurrence_multiplier(recurrence_level)
            penalty = excess_kw × unit_price × tier_multiplier × recurrence_multiplier
        """
        # Aşım hesapla
        excess_kw = max(0, actual_kw - contract_kw)
        excess_ratio = excess_kw / contract_kw if contract_kw > 0 else 0
        
        # Kademe katsayısı
        tier_multiplier = policy.get_demand_multiplier(excess_ratio)
        
        # Tekrar seviyesi belirle
        recurrence_level = self._determine_recurrence_level(
            excess_history, current_period
        )
        recurrence_multiplier = policy.get_recurrence_multiplier(recurrence_level)
        
        # Ceza hesapla
        penalty_tl = excess_kw * unit_price * tier_multiplier * recurrence_multiplier
        
        # Kullanım oranı
        utilization = actual_kw / contract_kw if contract_kw > 0 else 0
        
        # Durum belirle
        if excess_kw > 0:
            status = PenaltyStatus.CRITICAL
        elif utilization >= warning_threshold:
            status = PenaltyStatus.WARNING
        else:
            status = PenaltyStatus.OK
        
        logger.debug(
            f"DEMAND: contract={contract_kw:.2f}, actual={actual_kw:.2f}, "
            f"excess={excess_kw:.2f} ({excess_ratio:.1%}), "
            f"tier={tier_multiplier}x, recurrence={recurrence_multiplier}x, "
            f"penalty={penalty_tl:.2f} TL"
        )
        
        return DemandPenaltyDetail(
            contract_kw=round(contract_kw, 2),
            actual_kw=round(actual_kw, 2),
            excess_kw=round(excess_kw, 2),
            excess_ratio=round(excess_ratio, 4),
            tier_multiplier=tier_multiplier,
            recurrence_level=recurrence_level,
            recurrence_multiplier=recurrence_multiplier,
            unit_price_tl=unit_price,
            penalty_tl=round(penalty_tl, 2),
            status=status
        )
    
    def _determine_recurrence_level(
        self,
        excess_history: List[str],
        current_period: str
    ) -> RecurrenceLevel:
        """
        Son 12 aydaki aşım geçmişine göre tekrar seviyesi belirle.
        
        Args:
            excess_history: Aşım olan dönemler listesi (YYYY-MM)
            current_period: Mevcut dönem
        
        Returns:
            RecurrenceLevel
        """
        if not excess_history:
            return RecurrenceLevel.FIRST
        
        # Son 12 ayı filtrele
        from datetime import datetime
        try:
            current = datetime.strptime(current_period, "%Y-%m")
        except ValueError:
            return RecurrenceLevel.FIRST
        
        recent_count = 0
        for period in excess_history:
            try:
                dt = datetime.strptime(period, "%Y-%m")
                months_diff = (current.year - dt.year) * 12 + (current.month - dt.month)
                if 0 < months_diff <= 12:
                    recent_count += 1
            except ValueError:
                continue
        
        if recent_count >= 3:
            return RecurrenceLevel.CHRONIC
        elif recent_count >= 1:
            return RecurrenceLevel.REPEAT
        else:
            return RecurrenceLevel.FIRST
    
    def _generate_recommendations(
        self,
        reactive_inductive: ReactivePenaltyDetail,
        reactive_capacitive: ReactivePenaltyDetail,
        demand: DemandPenaltyDetail,
        facility,
        total_penalty: float
    ) -> List[Recommendation]:
        """
        Ceza durumuna göre öneriler oluştur.
        
        Rule-based advice generator.
        """
        recommendations = []
        
        # Reaktif (endüktif) önerileri
        if reactive_inductive.status == PenaltyStatus.CRITICAL:
            if not facility.has_compensation:
                recommendations.append(Recommendation(
                    category="reactive",
                    priority=1,
                    action="Kompanzasyon panosu kurulumu gerekli. "
                           "Reaktif ceza aylık tekrar ediyor.",
                    expected_saving_tl=reactive_inductive.penalty_tl * 12,
                    payback_months=6
                ))
            else:
                recommendations.append(Recommendation(
                    category="reactive",
                    priority=1,
                    action="Kompanzasyon panosu kontrol edilmeli: "
                           "1) Kondansatör kademeleri devrede mi? "
                           "2) Kontaktör/sigorta arızası var mı? "
                           "3) cosφ rölesi hedef değeri (0.98) doğru mu?",
                    expected_saving_tl=reactive_inductive.penalty_tl * 12
                ))
                
                if facility.has_harmonic_load and not facility.has_harmonic_filter:
                    recommendations.append(Recommendation(
                        category="reactive",
                        priority=2,
                        action="Harmonik yük tespit edildi (VFD/inverter). "
                               "Detuned reaktör (filtreli kompanzasyon) önerilir. "
                               "Mevcut kondansatörler harmonikten zarar görebilir.",
                        payback_months=12
                    ))
        
        elif reactive_inductive.status == PenaltyStatus.WARNING:
            recommendations.append(Recommendation(
                category="reactive",
                priority=3,
                action=f"Reaktif kullanım limite yaklaşıyor "
                       f"({reactive_inductive.utilization_ratio:.0%}). "
                       f"Kompanzasyon panosu kontrol edilmeli.",
            ))
        
        # Kapasitif önerileri
        if reactive_capacitive.status == PenaltyStatus.CRITICAL:
            recommendations.append(Recommendation(
                category="capacitive",
                priority=1,
                action="Aşırı kompanzasyon tespit edildi. "
                       "1) Kompanzasyon kademe sayısı azaltılmalı "
                       "2) cosφ rölesi hedefi 0.98 endüktif olmalı (kapasitif değil) "
                       "3) Gece/hafta sonu otomatik devre dışı ayarı kontrol edilmeli",
                expected_saving_tl=reactive_capacitive.penalty_tl * 12
            ))
        
        # Güç aşım önerileri
        if demand.status == PenaltyStatus.CRITICAL:
            # Aşım oranına göre öneri
            if demand.excess_ratio <= 0.10:
                recommendations.append(Recommendation(
                    category="demand",
                    priority=1,
                    action=f"Sözleşme gücü revizyonu önerilir. "
                           f"Mevcut: {demand.contract_kw:.0f} kW → "
                           f"Önerilen: {demand.actual_kw * 1.05:.0f} kW. "
                           f"Dağıtım şirketine başvuru yapılmalı.",
                    expected_saving_tl=demand.penalty_tl * 12
                ))
            else:
                recommendations.append(Recommendation(
                    category="demand",
                    priority=1,
                    action=f"Güç aşımı kritik seviyede ({demand.excess_ratio:.0%}). "
                           f"İki seçenek: "
                           f"1) Sözleşme gücü artırımı ({demand.actual_kw * 1.10:.0f} kW) "
                           f"2) Pik yük kontrolü (demand controller, yük sıralama, soft-start)",
                    expected_saving_tl=demand.penalty_tl * 12
                ))
            
            # Tekrar durumu
            if demand.recurrence_level == RecurrenceLevel.CHRONIC:
                recommendations.append(Recommendation(
                    category="demand",
                    priority=1,
                    action="Kronik güç aşımı tespit edildi (son 12 ayda 3+ kez). "
                           "Ceza katsayısı artıyor. ACİL sözleşme revizyonu gerekli.",
                ))
        
        elif demand.status == PenaltyStatus.WARNING:
            recommendations.append(Recommendation(
                category="demand",
                priority=2,
                action=f"Demand sözleşme gücüne yaklaşıyor "
                       f"({demand.actual_kw:.0f}/{demand.contract_kw:.0f} kW). "
                       f"Pik yük takibi önerilir.",
            ))
        
        # Genel öneri: Ceza yüksekse
        if total_penalty > 10000:
            recommendations.append(Recommendation(
                category="general",
                priority=2,
                action=f"Toplam ceza yüksek ({total_penalty:,.0f} TL/ay). "
                       f"Yıllık maliyet: {total_penalty * 12:,.0f} TL. "
                       f"Enerji danışmanlığı önerilir.",
            ))
        
        # Önceliğe göre sırala
        recommendations.sort(key=lambda x: x.priority)
        
        return recommendations


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_penalty(input: PenaltyInput) -> PenaltyResult:
    """
    Ceza hesapla (convenience function).
    
    Args:
        input: PenaltyInput objesi
    
    Returns:
        PenaltyResult objesi
    """
    engine = PenaltyEngine()
    return engine.calculate(input)


def quick_penalty_check(
    active_kwh: float,
    reactive_kvarh: float,
    demand_kw: float,
    contract_kw: float,
    distribution_company: str = "default",
    period: str = "2025-01"
) -> dict:
    """
    Hızlı ceza kontrolü (basit API).
    
    UYARI: Bu fonksiyon default değerler kullanır.
    Production için tam PenaltyInput kullanın.
    
    Returns:
        {
            "reactive_penalty_tl": float,
            "demand_penalty_tl": float,
            "total_penalty_tl": float,
            "has_penalty": bool,
            "warnings": list[str],
            "assumptions": list[str]
        }
    """
    from .penalty_models import FacilityProfile, VoltageLevel, DemandPeriod
    
    assumptions = [
        f"voltage_level=AG (default)",
        f"demand_period=15min (default)",
        f"policy=default_2025",
        f"distribution_company={distribution_company}",
        f"period={period}",
    ]
    
    facility = FacilityProfile(
        contract_power_kw=contract_kw,
        voltage_level=VoltageLevel.AG,
        demand_period=DemandPeriod.MIN_15,
        distribution_company=distribution_company
    )
    
    input = PenaltyInput(
        period=period,
        active_kwh=active_kwh,
        reactive_inductive_kvarh=reactive_kvarh,
        demand_max_kw=demand_kw,
        facility=facility
    )
    
    result = calculate_penalty(input)
    
    return {
        "reactive_penalty_tl": result.total_reactive_penalty_tl,
        "demand_penalty_tl": result.total_demand_penalty_tl,
        "total_penalty_tl": result.total_penalty_tl,
        "has_penalty": result.total_penalty_tl > 0,
        "warnings": result.warnings,
        "assumptions": assumptions + result.assumptions
    }
