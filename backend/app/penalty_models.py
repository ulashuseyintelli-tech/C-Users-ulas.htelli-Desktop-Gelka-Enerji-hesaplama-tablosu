"""
Penalty & Tariff Simulation Domain Models

Bu modül ceza hesaplama ve tarife simülasyonu için domain modellerini içerir.
Policy-driven tasarım: Kurallar değişirse kod değişmez.

TASARIM PRENSİPLERİ:
1. Tarife simülasyonu ≠ ceza hesaplama (ayrı concern'ler)
2. Demand ölçüm periyodu zorunlu alan (15dk vs 60dk kritik fark)
3. Limitler ve katsayılar policy'den gelir (EPDK default, override edilebilir)
4. Rate provider bölge + dönem bazlı çalışır
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Tuple
from enum import Enum


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class VoltageLevel(str, Enum):
    """Gerilim seviyesi"""
    AG = "AG"  # Alçak Gerilim
    OG = "OG"  # Orta Gerilim
    YG = "YG"  # Yüksek Gerilim


class TermType(str, Enum):
    """Terim tipi"""
    SINGLE = "tek_terim"  # Tek terimli
    MULTI = "cift_terim"  # Çift terimli / çok zamanlı


class TariffGroup(str, Enum):
    """Tarife grubu"""
    SANAYI = "sanayi"
    TICARETHANE = "ticarethane"
    MESKEN = "mesken"
    TARIMSAL = "tarimsal"
    AYDINLATMA = "aydinlatma"
    KAMU_OZEL = "kamu_ozel"  # Genel kategori


class DemandPeriod(str, Enum):
    """Demand ölçüm periyodu"""
    MIN_15 = "15min"  # 15 dakika ortalaması
    MIN_60 = "60min"  # 60 dakika ortalaması
    INSTANT = "instant"  # Anlık pik


class LoadProfile(str, Enum):
    """Yük profili"""
    DAYTIME = "daytime"  # Gündüz ağırlıklı
    CONTINUOUS = "24x7"  # 7/24 sürekli
    SHIFT = "shift"  # Vardiyalı


class PenaltyStatus(str, Enum):
    """Ceza durumu"""
    OK = "ok"  # Limit içinde
    WARNING = "warning"  # Limite yakın (%80-100)
    CRITICAL = "critical"  # Limit aşıldı


class RecurrenceLevel(int, Enum):
    """Aşım tekrar seviyesi"""
    FIRST = 0  # İlk aşım
    REPEAT = 1  # Tekrar (2. kez)
    CHRONIC = 2  # Kronik (3+ kez)


class ConfidenceLevel(str, Enum):
    """
    Hesaplama güven seviyesi.
    
    Typo riskini önlemek için enum kullanılır.
    """
    HIGH = "HIGH"      # Tam eşleşen rate bulundu
    MEDIUM = "MEDIUM"  # Yakın dönem rate'i kullanıldı
    LOW = "LOW"        # Default rate kullanıldı, doğrulama gerekli


# ═══════════════════════════════════════════════════════════════════════════════
# POLICY MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class DemandTier(BaseModel):
    """Güç aşım kademesi"""
    threshold_ratio: float  # Aşım oranı eşiği (örn: 0.05 = %5)
    multiplier: float  # Ceza katsayısı


class PenaltyPolicy(BaseModel):
    """
    Ceza hesaplama politikası - Kural motoru konfigürasyonu
    
    Default değerler yaygın EPDK uygulamasına göre ayarlanmıştır.
    Farklı bölge/dönem için override edilebilir.
    """
    policy_id: str = "default_2025"
    
    # Reaktif limitler (aktif enerjinin yüzdesi olarak)
    inductive_limit_ratio: float = 0.20  # %20 - endüktif reaktif limiti
    capacitive_limit_ratio: float = 0.15  # %15 - kapasitif reaktif limiti
    
    # Güç aşım kademeleri: (eşik_oranı, katsayı) listesi
    # Sıralı olmalı, son eşik inf olmalı
    demand_tiers: List[DemandTier] = Field(default_factory=lambda: [
        DemandTier(threshold_ratio=0.05, multiplier=1.0),   # %0-5 arası: 1x
        DemandTier(threshold_ratio=0.10, multiplier=1.5),   # %5-10 arası: 1.5x
        DemandTier(threshold_ratio=0.20, multiplier=2.0),   # %10-20 arası: 2x
        DemandTier(threshold_ratio=float('inf'), multiplier=3.0),  # %20+: 3x
    ])
    
    # Tekrar ceza katsayıları
    recurrence_multipliers: dict = Field(default_factory=lambda: {
        RecurrenceLevel.FIRST: 1.0,
        RecurrenceLevel.REPEAT: 2.0,
        RecurrenceLevel.CHRONIC: 3.0,
    })
    
    # Uyarı eşiği (limitin yüzdesi)
    warning_threshold_ratio: float = 0.80  # %80'e ulaşınca uyarı
    
    def get_demand_multiplier(self, excess_ratio: float) -> float:
        """Aşım oranına göre katsayı döndür"""
        for tier in self.demand_tiers:
            if excess_ratio <= tier.threshold_ratio:
                return tier.multiplier
        return self.demand_tiers[-1].multiplier if self.demand_tiers else 1.0
    
    def get_recurrence_multiplier(self, level: RecurrenceLevel) -> float:
        """Tekrar seviyesine göre katsayı döndür"""
        return self.recurrence_multipliers.get(level, 1.0)


class PenaltyRates(BaseModel):
    """
    Ceza birim fiyatları - Bölge ve dönem bazlı
    
    Bu fiyatlar dağıtım şirketi ve dönem bazında değişir.
    """
    distribution_company: str = "default"
    period: str = "2025-01"  # YYYY-MM
    
    # Birim fiyatlar
    reactive_unit_price_tl_per_kvarh: float = 0.50  # TL/kVArh
    capacitive_unit_price_tl_per_kvarh: float = 0.50  # TL/kVArh
    demand_excess_unit_price_tl_per_kw: float = 50.0  # TL/kW
    
    # Kaynak bilgisi
    source: str = "default"  # "epdk_tariff", "manual", "default"


# ═══════════════════════════════════════════════════════════════════════════════
# FACILITY PROFILE
# ═══════════════════════════════════════════════════════════════════════════════

class FacilityProfile(BaseModel):
    """
    Tesis profili - Ceza hesaplama için gerekli tüm bilgiler
    """
    # Kimlik
    facility_id: Optional[str] = None
    facility_name: Optional[str] = None
    
    # Kontrat bilgileri
    contract_power_kw: float  # Sözleşme gücü (kW)
    voltage_level: VoltageLevel = VoltageLevel.AG
    transformer_kva: Optional[float] = None  # Trafo kapasitesi (kVA)
    term_type: TermType = TermType.SINGLE
    tariff_group: TariffGroup = TariffGroup.SANAYI
    
    # Ölçüm bilgileri
    demand_period: DemandPeriod = DemandPeriod.MIN_15  # ZORUNLU
    has_demand_meter: bool = True  # Demand ölçümü var mı?
    
    # Kompanzasyon bilgileri
    has_compensation: bool = False
    compensation_stages: Optional[int] = None  # Kademe sayısı
    compensation_target_cosphi: float = 0.98  # Hedef cosφ
    has_harmonic_filter: bool = False  # Detuned reaktör var mı?
    has_harmonic_load: bool = False  # VFD, inverter, UPS var mı?
    
    # Yük profili
    load_profile: LoadProfile = LoadProfile.DAYTIME
    
    # Bölge
    distribution_company: str = ""  # BEDAŞ, AYEDAŞ, TOROSLAR, vb.
    
    # Aşım geçmişi (recurrence için)
    demand_excess_history: List[str] = Field(default_factory=list)  # Son 12 ay aşım dönemleri


# ═══════════════════════════════════════════════════════════════════════════════
# INPUT / OUTPUT MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class PenaltyInput(BaseModel):
    """Ceza hesaplama girdileri"""
    # Dönem
    period: str  # YYYY-MM
    
    # Tüketim verileri
    active_kwh: float  # Aktif enerji (kWh)
    reactive_inductive_kvarh: float = 0  # Endüktif reaktif (kVArh)
    reactive_capacitive_kvarh: float = 0  # Kapasitif reaktif (kVArh)
    demand_max_kw: float = 0  # Maksimum demand (kW)
    
    # Tesis profili
    facility: FacilityProfile
    
    # Opsiyonel: Policy ve rate override
    policy_override: Optional[PenaltyPolicy] = None
    rates_override: Optional[PenaltyRates] = None


class ReactivePenaltyDetail(BaseModel):
    """Reaktif ceza detayı"""
    limit_kvarh: float  # Limit (kVArh)
    actual_kvarh: float  # Gerçekleşen (kVArh)
    excess_kvarh: float  # Aşım (kVArh)
    unit_price_tl: float  # Birim fiyat (TL/kVArh)
    penalty_tl: float  # Ceza tutarı (TL)
    status: PenaltyStatus
    utilization_ratio: float  # Kullanım oranı (actual/limit)


class DemandPenaltyDetail(BaseModel):
    """Güç aşım ceza detayı"""
    contract_kw: float  # Sözleşme gücü (kW)
    actual_kw: float  # Gerçekleşen max demand (kW)
    excess_kw: float  # Aşım (kW)
    excess_ratio: float  # Aşım oranı
    tier_multiplier: float  # Kademe katsayısı
    recurrence_level: RecurrenceLevel
    recurrence_multiplier: float  # Tekrar katsayısı
    unit_price_tl: float  # Birim fiyat (TL/kW)
    penalty_tl: float  # Ceza tutarı (TL)
    status: PenaltyStatus


class Recommendation(BaseModel):
    """Öneri"""
    category: str  # "reactive", "capacitive", "demand", "tariff"
    priority: int  # 1=acil, 2=önemli, 3=öneri
    action: str  # Yapılacak aksiyon
    expected_saving_tl: Optional[float] = None  # Tahmini tasarruf
    payback_months: Optional[int] = None  # Geri ödeme süresi


class PenaltyResult(BaseModel):
    """Ceza hesaplama sonucu"""
    # Dönem
    period: str
    facility_id: Optional[str] = None
    
    # Reaktif ceza
    reactive_inductive: ReactivePenaltyDetail
    reactive_capacitive: ReactivePenaltyDetail
    
    # Güç aşım cezası
    demand: DemandPenaltyDetail
    
    # Toplamlar
    total_reactive_penalty_tl: float
    total_demand_penalty_tl: float
    total_penalty_tl: float
    
    # Öneriler
    recommendations: List[Recommendation] = Field(default_factory=list)
    
    # Meta
    policy_id: str
    rates_source: str
    
    # Uyarılar (fallback kullanıldıysa, vb.)
    warnings: List[str] = Field(default_factory=list)
    
    # Varsayımlar (quick API için)
    assumptions: List[str] = Field(default_factory=list)
    
    # Güven seviyesi (fallback varsa LOW)
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH
    
    # Rate snapshot (audit trail için)
    rates_snapshot: Optional[dict] = None  # {company, period, source, values}
    
    # Enerji bedeli dahil mi? (satış ekibi için önemli)
    energy_included: bool = False
    
    # Notlar (enerji dahil değilse uyarı, vb.)
    notes: List[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# TARIFF SIMULATION MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class TariffScenario(BaseModel):
    """Tarife senaryosu"""
    tariff_group: TariffGroup
    voltage_level: VoltageLevel
    term_type: TermType
    
    @property
    def key(self) -> str:
        return f"{self.tariff_group.value}/{self.voltage_level.value}/{self.term_type.value}"


class TariffSimulationInput(BaseModel):
    """Tarife simülasyon girdisi"""
    # Dönem
    period: str  # YYYY-MM
    
    # Tüketim verileri
    active_kwh: float
    reactive_inductive_kvarh: float = 0
    reactive_capacitive_kvarh: float = 0
    demand_max_kw: float = 0
    
    # Mevcut tesis profili
    current_facility: FacilityProfile
    
    # Simüle edilecek senaryolar (None = tümü)
    scenarios: Optional[List[TariffScenario]] = None
    
    # Enerji bedeli dahil mi?
    include_energy_cost: bool = False
    energy_unit_price_tl_per_kwh: Optional[float] = None


class TariffSimulationResult(BaseModel):
    """Tek senaryo simülasyon sonucu"""
    scenario: TariffScenario
    tariff_key: str
    
    # Maliyet kalemleri
    distribution_tl: float
    reactive_penalty_tl: float
    capacitive_penalty_tl: float
    demand_penalty_tl: float
    total_penalty_tl: float
    
    # Enerji (opsiyonel)
    energy_tl: Optional[float] = None
    
    # Toplam
    total_penalty_and_distribution_tl: float  # Ceza + Dağıtım
    total_cost_tl: Optional[float] = None  # Enerji dahil (varsa)
    
    # Karşılaştırma (mevcut duruma göre)
    vs_current_saving_tl: float = 0
    vs_current_saving_percent: float = 0
    
    # Sıralama için
    rank: int = 0
    is_current: bool = False  # Mevcut tarife mi?
    is_cheapest: bool = False  # En ucuz mu?


class TariffComparisonReport(BaseModel):
    """Tarife karşılaştırma raporu"""
    period: str
    facility_id: Optional[str] = None
    
    # Mevcut durum
    current_scenario: TariffSimulationResult
    
    # Tüm senaryolar (sıralı - en ucuzdan pahalıya)
    all_scenarios: List[TariffSimulationResult]
    
    # En iyi senaryo
    best_scenario: TariffSimulationResult
    
    # Potansiyel tasarruf
    max_saving_tl: float
    max_saving_percent: float
    
    # Özet
    summary: str  # "Mevcut tarife optimal" veya "OG Çift Terim'e geçişle %15 tasarruf"
