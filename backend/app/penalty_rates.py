"""
Penalty Rate Provider - Bölge ve Dönem Bazlı Birim Fiyatlar

Bu modül reaktif, kapasitif ve güç aşım cezaları için birim fiyatları sağlar.
Fiyatlar dağıtım şirketi ve dönem bazında değişebilir.

KULLANIM:
    rates = get_penalty_rates("BEDAS", "2025-01")
    reactive_cost = excess_kvarh * rates.reactive_unit_price_tl_per_kvarh
"""

import logging
from typing import Optional, Dict
from .penalty_models import PenaltyRates


# ═══════════════════════════════════════════════════════════════════════════════
# CUSTOM EXCEPTIONS
# ═══════════════════════════════════════════════════════════════════════════════

class PenaltyRatesNotFoundError(Exception):
    """
    Bölge/dönem için rate bulunamadığında fırlatılır.
    
    Bu exception sayede:
    - Başka ValueError'lar (örn: period parse hatası) yutulmaz
    - Engine sadece bu spesifik hatayı yakalayıp fallback uygular
    """
    def __init__(self, company: str, period: str):
        self.company = company
        self.period = period
        super().__init__(f"Penalty rates not found for {company}/{period}")

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# RATE TABLES
# ═══════════════════════════════════════════════════════════════════════════════

# Default rates (fallback)
DEFAULT_RATES = PenaltyRates(
    distribution_company="default",
    period="2025-01",
    reactive_unit_price_tl_per_kvarh=0.50,
    capacitive_unit_price_tl_per_kvarh=0.50,
    demand_excess_unit_price_tl_per_kw=50.0,
    source="default"
)

# Bölge bazlı rate tablosu
# Key: (distribution_company, period)
# Not: Gerçek değerler EPDK tarifelerinden alınmalı
RATE_TABLE: Dict[tuple, PenaltyRates] = {
    # BEDAŞ (Boğaziçi Elektrik Dağıtım)
    ("BEDAS", "2025-01"): PenaltyRates(
        distribution_company="BEDAS",
        period="2025-01",
        reactive_unit_price_tl_per_kvarh=0.52,
        capacitive_unit_price_tl_per_kvarh=0.52,
        demand_excess_unit_price_tl_per_kw=55.0,
        source="epdk_tariff"
    ),
    ("BEDAS", "2025-02"): PenaltyRates(
        distribution_company="BEDAS",
        period="2025-02",
        reactive_unit_price_tl_per_kvarh=0.52,
        capacitive_unit_price_tl_per_kvarh=0.52,
        demand_excess_unit_price_tl_per_kw=55.0,
        source="epdk_tariff"
    ),
    
    # AYEDAŞ (Anadolu Yakası Elektrik Dağıtım)
    ("AYEDAS", "2025-01"): PenaltyRates(
        distribution_company="AYEDAS",
        period="2025-01",
        reactive_unit_price_tl_per_kvarh=0.51,
        capacitive_unit_price_tl_per_kvarh=0.51,
        demand_excess_unit_price_tl_per_kw=52.0,
        source="epdk_tariff"
    ),
    
    # TOROSLAR EDAŞ
    ("TOROSLAR", "2025-01"): PenaltyRates(
        distribution_company="TOROSLAR",
        period="2025-01",
        reactive_unit_price_tl_per_kvarh=0.48,
        capacitive_unit_price_tl_per_kvarh=0.48,
        demand_excess_unit_price_tl_per_kw=48.0,
        source="epdk_tariff"
    ),
    
    # BAŞKENT EDAŞ
    ("BASKENT", "2025-01"): PenaltyRates(
        distribution_company="BASKENT",
        period="2025-01",
        reactive_unit_price_tl_per_kvarh=0.49,
        capacitive_unit_price_tl_per_kvarh=0.49,
        demand_excess_unit_price_tl_per_kw=50.0,
        source="epdk_tariff"
    ),
    
    # GEDİZ EDAŞ
    ("GEDIZ", "2025-01"): PenaltyRates(
        distribution_company="GEDIZ",
        period="2025-01",
        reactive_unit_price_tl_per_kvarh=0.50,
        capacitive_unit_price_tl_per_kvarh=0.50,
        demand_excess_unit_price_tl_per_kw=51.0,
        source="epdk_tariff"
    ),
    
    # ULUDAĞ EDAŞ
    ("ULUDAG", "2025-01"): PenaltyRates(
        distribution_company="ULUDAG",
        period="2025-01",
        reactive_unit_price_tl_per_kvarh=0.47,
        capacitive_unit_price_tl_per_kvarh=0.47,
        demand_excess_unit_price_tl_per_kw=49.0,
        source="epdk_tariff"
    ),
    
    # SAKARYA EDAŞ
    ("SAKARYA", "2025-01"): PenaltyRates(
        distribution_company="SAKARYA",
        period="2025-01",
        reactive_unit_price_tl_per_kvarh=0.48,
        capacitive_unit_price_tl_per_kvarh=0.48,
        demand_excess_unit_price_tl_per_kw=48.0,
        source="epdk_tariff"
    ),
}

# Dağıtım şirketi isim normalizasyonu
COMPANY_ALIASES: Dict[str, str] = {
    # BEDAŞ
    "bedas": "BEDAS",
    "bedaş": "BEDAS",
    "bogazici": "BEDAS",
    "boğaziçi": "BEDAS",
    "ck bogazici": "BEDAS",
    "ck boğaziçi": "BEDAS",
    
    # AYEDAŞ
    "ayedas": "AYEDAS",
    "ayedaş": "AYEDAS",
    "anadolu yakasi": "AYEDAS",
    "anadolu yakası": "AYEDAS",
    
    # TOROSLAR
    "toroslar": "TOROSLAR",
    "toroslar edas": "TOROSLAR",
    "toroslar edaş": "TOROSLAR",
    
    # BAŞKENT
    "baskent": "BASKENT",
    "başkent": "BASKENT",
    "baskent edas": "BASKENT",
    "başkent edaş": "BASKENT",
    "enerjisa baskent": "BASKENT",
    
    # GEDİZ
    "gediz": "GEDIZ",
    "gediz edas": "GEDIZ",
    "gediz edaş": "GEDIZ",
    
    # ULUDAĞ
    "uludag": "ULUDAG",
    "uludağ": "ULUDAG",
    "uludag edas": "ULUDAG",
    "uludağ edaş": "ULUDAG",
    
    # SAKARYA
    "sakarya": "SAKARYA",
    "sakarya edas": "SAKARYA",
    "sakarya edaş": "SAKARYA",
}


# ═══════════════════════════════════════════════════════════════════════════════
# RATE PROVIDER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_company_name(raw_name: str) -> str:
    """
    Dağıtım şirketi ismini normalize et.
    
    Args:
        raw_name: Ham şirket ismi (örn: "CK Boğaziçi", "BEDAŞ", "bedas")
    
    Returns:
        Normalize edilmiş isim (örn: "BEDAS")
    """
    if not raw_name:
        return "default"
    
    # Küçük harfe çevir ve boşlukları temizle
    normalized = raw_name.lower().strip()
    
    # Alias tablosunda ara
    if normalized in COMPANY_ALIASES:
        return COMPANY_ALIASES[normalized]
    
    # Büyük harfe çevir ve döndür
    return raw_name.upper().replace(" ", "_").replace("İ", "I").replace("Ş", "S")


def get_penalty_rates(
    distribution_company: str,
    period: str,
    fallback_to_default: bool = True
) -> PenaltyRates:
    """
    Bölge ve dönem için ceza birim fiyatlarını döndür.
    
    Args:
        distribution_company: Dağıtım şirketi (normalize edilecek)
        period: Dönem (YYYY-MM)
        fallback_to_default: Bulunamazsa default döndür
    
    Returns:
        PenaltyRates objesi
    """
    # Şirket ismini normalize et
    company = normalize_company_name(distribution_company)
    
    # Tam eşleşme ara
    key = (company, period)
    if key in RATE_TABLE:
        logger.info(f"Penalty rates found: {company}/{period}")
        return RATE_TABLE[key]
    
    # Dönem olmadan şirket ara (en son dönem)
    company_rates = [
        (k, v) for k, v in RATE_TABLE.items() 
        if k[0] == company
    ]
    if company_rates:
        # En son dönemi al
        latest = max(company_rates, key=lambda x: x[0][1])
        logger.warning(
            f"Penalty rates for {company}/{period} not found, "
            f"using {latest[0][1]} rates"
        )
        return latest[1]
    
    # Fallback
    if fallback_to_default:
        logger.warning(
            f"Penalty rates for {company}/{period} not found, "
            f"using default rates"
        )
        return DEFAULT_RATES
    
    raise PenaltyRatesNotFoundError(company, period)


def get_available_companies() -> list[str]:
    """Mevcut dağıtım şirketlerini listele"""
    companies = set(k[0] for k in RATE_TABLE.keys())
    return sorted(companies)


def get_available_periods(distribution_company: str) -> list[str]:
    """Bir şirket için mevcut dönemleri listele"""
    company = normalize_company_name(distribution_company)
    periods = [k[1] for k in RATE_TABLE.keys() if k[0] == company]
    return sorted(periods, reverse=True)


def add_rate(rates: PenaltyRates) -> None:
    """
    Rate tablosuna yeni kayıt ekle (runtime).
    
    Args:
        rates: PenaltyRates objesi
    """
    key = (rates.distribution_company, rates.period)
    RATE_TABLE[key] = rates
    logger.info(f"Added penalty rates: {key}")


def update_rate(
    distribution_company: str,
    period: str,
    reactive_unit_price: Optional[float] = None,
    capacitive_unit_price: Optional[float] = None,
    demand_unit_price: Optional[float] = None
) -> PenaltyRates:
    """
    Mevcut rate'i güncelle veya yeni oluştur.
    
    Args:
        distribution_company: Dağıtım şirketi
        period: Dönem
        reactive_unit_price: Reaktif birim fiyat (None = değiştirme)
        capacitive_unit_price: Kapasitif birim fiyat (None = değiştirme)
        demand_unit_price: Demand birim fiyat (None = değiştirme)
    
    Returns:
        Güncellenmiş PenaltyRates
    """
    company = normalize_company_name(distribution_company)
    key = (company, period)
    
    # Mevcut rate'i al veya default oluştur
    if key in RATE_TABLE:
        current = RATE_TABLE[key]
    else:
        current = PenaltyRates(
            distribution_company=company,
            period=period,
            source="manual"
        )
    
    # Güncelle
    updated = PenaltyRates(
        distribution_company=company,
        period=period,
        reactive_unit_price_tl_per_kvarh=(
            reactive_unit_price 
            if reactive_unit_price is not None 
            else current.reactive_unit_price_tl_per_kvarh
        ),
        capacitive_unit_price_tl_per_kvarh=(
            capacitive_unit_price 
            if capacitive_unit_price is not None 
            else current.capacitive_unit_price_tl_per_kvarh
        ),
        demand_excess_unit_price_tl_per_kw=(
            demand_unit_price 
            if demand_unit_price is not None 
            else current.demand_excess_unit_price_tl_per_kw
        ),
        source="manual"
    )
    
    RATE_TABLE[key] = updated
    logger.info(f"Updated penalty rates: {key}")
    return updated
