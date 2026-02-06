"""
Penalty Rate Provider Tests

Test matrisi:
- Bilinmeyen bölge → kontrollü hata / fallback
- Dönem seçimi doğru
- Şirket ismi normalizasyonu
- Rate ekleme/güncelleme
"""

import pytest
from app.penalty_rates import (
    get_penalty_rates,
    normalize_company_name,
    get_available_companies,
    get_available_periods,
    add_rate,
    update_rate,
    DEFAULT_RATES,
    RATE_TABLE,
    PenaltyRatesNotFoundError,
)
from app.penalty_models import PenaltyRates


# ═══════════════════════════════════════════════════════════════════════════════
# NORMALIZATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestNormalization:
    """Şirket ismi normalizasyon testleri"""
    
    def test_normalize_bedas_variants(self):
        """BEDAŞ varyantları normalize edilir"""
        variants = ["bedas", "bedaş", "BEDAS", "BEDAŞ", "bogazici", "boğaziçi", "ck bogazici"]
        
        for variant in variants:
            assert normalize_company_name(variant) == "BEDAS"
    
    def test_normalize_ayedas_variants(self):
        """AYEDAŞ varyantları normalize edilir"""
        variants = ["ayedas", "ayedaş", "AYEDAS", "anadolu yakasi"]
        
        for variant in variants:
            assert normalize_company_name(variant) == "AYEDAS"
    
    def test_normalize_unknown_company(self):
        """Bilinmeyen şirket büyük harfe çevrilir"""
        result = normalize_company_name("yeni_sirket")
        assert result == "YENI_SIRKET"
    
    def test_normalize_empty_string(self):
        """Boş string → default"""
        assert normalize_company_name("") == "default"
        assert normalize_company_name(None) == "default"


# ═══════════════════════════════════════════════════════════════════════════════
# RATE LOOKUP TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLookup:
    """Rate lookup testleri"""
    
    def test_get_known_company_and_period(self):
        """Bilinen şirket ve dönem için rate döner"""
        rates = get_penalty_rates("BEDAS", "2025-01")
        
        assert rates.distribution_company == "BEDAS"
        assert rates.period == "2025-01"
        assert rates.source == "epdk_tariff"
    
    def test_get_unknown_period_uses_latest(self):
        """Bilinmeyen dönem için en son dönem kullanılır"""
        rates = get_penalty_rates("BEDAS", "2099-12")
        
        assert rates.distribution_company == "BEDAS"
        # En son mevcut dönem kullanılır
    
    def test_get_unknown_company_uses_default(self):
        """Bilinmeyen şirket için default kullanılır"""
        rates = get_penalty_rates("BILINMEYEN_SIRKET", "2025-01")
        
        assert rates.source == "default"
    
    def test_fallback_disabled_raises_error(self):
        """Fallback kapalıyken bilinmeyen şirket hata verir"""
        with pytest.raises(PenaltyRatesNotFoundError) as exc_info:
            get_penalty_rates("BILINMEYEN_SIRKET", "2025-01", fallback_to_default=False)
        
        # Exception detayları kontrol
        assert exc_info.value.company == "BILINMEYEN_SIRKET"
        assert exc_info.value.period == "2025-01"
    
    def test_normalized_company_name_lookup(self):
        """Normalize edilmiş isimle lookup çalışır"""
        rates = get_penalty_rates("ck boğaziçi", "2025-01")
        
        assert rates.distribution_company == "BEDAS"


# ═══════════════════════════════════════════════════════════════════════════════
# RATE VALUES TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateValues:
    """Rate değer testleri"""
    
    def test_default_rates_have_values(self):
        """Default rates değerleri var"""
        assert DEFAULT_RATES.reactive_unit_price_tl_per_kvarh > 0
        assert DEFAULT_RATES.capacitive_unit_price_tl_per_kvarh > 0
        assert DEFAULT_RATES.demand_excess_unit_price_tl_per_kw > 0
    
    def test_bedas_rates_different_from_default(self):
        """BEDAŞ rates default'tan farklı olabilir"""
        bedas_rates = get_penalty_rates("BEDAS", "2025-01")
        
        # En azından source farklı
        assert bedas_rates.source != DEFAULT_RATES.source
    
    def test_rates_are_positive(self):
        """Tüm rate'ler pozitif"""
        for key, rates in RATE_TABLE.items():
            assert rates.reactive_unit_price_tl_per_kvarh > 0
            assert rates.capacitive_unit_price_tl_per_kvarh > 0
            assert rates.demand_excess_unit_price_tl_per_kw > 0


# ═══════════════════════════════════════════════════════════════════════════════
# AVAILABLE DATA TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAvailableData:
    """Mevcut veri testleri"""
    
    def test_get_available_companies(self):
        """Mevcut şirketler listesi"""
        companies = get_available_companies()
        
        assert len(companies) > 0
        assert "BEDAS" in companies
    
    def test_get_available_periods(self):
        """Bir şirket için mevcut dönemler"""
        periods = get_available_periods("BEDAS")
        
        assert len(periods) > 0
        assert "2025-01" in periods
    
    def test_get_available_periods_unknown_company(self):
        """Bilinmeyen şirket için boş liste"""
        periods = get_available_periods("BILINMEYEN")
        
        assert periods == []


# ═══════════════════════════════════════════════════════════════════════════════
# RATE MODIFICATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateModification:
    """Rate ekleme/güncelleme testleri"""
    
    def test_add_new_rate(self):
        """Yeni rate ekleme"""
        new_rates = PenaltyRates(
            distribution_company="TEST_NEW",
            period="2025-06",
            reactive_unit_price_tl_per_kvarh=0.60,
            capacitive_unit_price_tl_per_kvarh=0.60,
            demand_excess_unit_price_tl_per_kw=60.0,
            source="test"
        )
        
        add_rate(new_rates)
        
        # Lookup çalışmalı
        retrieved = get_penalty_rates("TEST_NEW", "2025-06", fallback_to_default=False)
        assert retrieved.reactive_unit_price_tl_per_kvarh == 0.60
        
        # Cleanup
        del RATE_TABLE[("TEST_NEW", "2025-06")]
    
    def test_update_existing_rate(self):
        """Mevcut rate güncelleme"""
        # Önce ekle
        add_rate(PenaltyRates(
            distribution_company="TEST_UPDATE",
            period="2025-07",
            reactive_unit_price_tl_per_kvarh=0.50,
            capacitive_unit_price_tl_per_kvarh=0.50,
            demand_excess_unit_price_tl_per_kw=50.0,
            source="test"
        ))
        
        # Güncelle
        updated = update_rate(
            distribution_company="TEST_UPDATE",
            period="2025-07",
            reactive_unit_price=0.75
        )
        
        assert updated.reactive_unit_price_tl_per_kvarh == 0.75
        assert updated.capacitive_unit_price_tl_per_kvarh == 0.50  # Değişmedi
        
        # Cleanup
        del RATE_TABLE[("TEST_UPDATE", "2025-07")]
    
    def test_update_creates_if_not_exists(self):
        """Update yoksa oluşturur"""
        updated = update_rate(
            distribution_company="TEST_CREATE",
            period="2025-08",
            reactive_unit_price=0.55
        )
        
        assert updated.distribution_company == "TEST_CREATE"
        assert updated.reactive_unit_price_tl_per_kvarh == 0.55
        
        # Cleanup
        del RATE_TABLE[("TEST_CREATE", "2025-08")]
