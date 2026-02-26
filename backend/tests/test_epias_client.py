"""
EPİAŞ Client Tests

Test matrisi:
- Period date parsing
- Mock client behavior
- Data models
- Error handling
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock

from app.epias_client import (
    EpiasClient,
    EpiasConfig,
    PtfData,
    YekdemData,
    MarketPricesResult,
    EpiasApiError,
    EpiasDataNotFoundError,
    EpiasAuthError,
    MockEpiasClient,
    fetch_market_prices_from_epias,
    fetch_ptf_from_epias,
    get_epias_client,
)


# ═══════════════════════════════════════════════════════════════════════════════
# PERIOD DATE PARSING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPeriodDateParsing:
    """Period → date range dönüşüm testleri"""
    
    def test_january_dates(self):
        """Ocak ayı için doğru tarihler"""
        client = EpiasClient()
        start, end = client._get_period_dates("2025-01")
        
        assert start == "2025-01-01"
        assert end == "2025-01-31"
    
    def test_february_dates_non_leap(self):
        """Şubat ayı (artık yıl değil)"""
        client = EpiasClient()
        start, end = client._get_period_dates("2025-02")
        
        assert start == "2025-02-01"
        assert end == "2025-02-28"
    
    def test_february_dates_leap_year(self):
        """Şubat ayı (artık yıl)"""
        client = EpiasClient()
        start, end = client._get_period_dates("2024-02")
        
        assert start == "2024-02-01"
        assert end == "2024-02-29"
    
    def test_december_dates(self):
        """Aralık ayı (yıl geçişi)"""
        client = EpiasClient()
        start, end = client._get_period_dates("2024-12")
        
        assert start == "2024-12-01"
        assert end == "2024-12-31"
    
    def test_april_dates(self):
        """30 günlük ay"""
        client = EpiasClient()
        start, end = client._get_period_dates("2025-04")
        
        assert start == "2025-04-01"
        assert end == "2025-04-30"


# ═══════════════════════════════════════════════════════════════════════════════
# PTF DATA TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPtfData:
    """PTF veri modeli testleri"""
    
    def test_ptf_data_creation(self):
        """PtfData objesi oluşturma"""
        data = PtfData(
            period="2025-01",
            average_tl_per_mwh=2974.1,
            min_tl_per_mwh=2500.0,
            max_tl_per_mwh=3500.0,
            data_points=744,
            source="epias"
        )
        
        assert data.period == "2025-01"
        assert data.average_tl_per_mwh == 2974.1
        assert data.data_points == 744
    
    def test_yekdem_data_creation(self):
        """YekdemData objesi oluşturma"""
        data = YekdemData(
            period="2025-01",
            unit_cost_tl_per_mwh=364.0,
            source="epias"
        )
        
        assert data.period == "2025-01"
        assert data.unit_cost_tl_per_mwh == 364.0


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET PRICES RESULT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarketPricesResult:
    """MarketPricesResult testleri"""
    
    def test_full_result(self):
        """Tam sonuç"""
        result = MarketPricesResult(
            period="2025-01",
            ptf_tl_per_mwh=2974.1,
            yekdem_tl_per_mwh=364.0,
            ptf_source="epias",
            yekdem_source="epias",
            ptf_data_points=744
        )
        
        assert result.ptf_tl_per_mwh == 2974.1
        assert result.yekdem_tl_per_mwh == 364.0
        assert result.warnings == []
    
    def test_partial_result_with_warnings(self):
        """Kısmi sonuç (YEKDEM yok)"""
        result = MarketPricesResult(
            period="2025-01",
            ptf_tl_per_mwh=2974.1,
            yekdem_tl_per_mwh=None,
            ptf_source="epias",
            yekdem_source="unavailable",
            warnings=["YEKDEM verisi alınamadı"]
        )
        
        assert result.ptf_tl_per_mwh == 2974.1
        assert result.yekdem_tl_per_mwh is None
        assert len(result.warnings) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestEpiasConfig:
    """Yapılandırma testleri"""
    
    def test_default_config(self):
        """Default yapılandırma"""
        config = EpiasConfig()
        
        assert config.timeout_seconds == 30
        assert config.max_retries == 3
    
    def test_custom_config(self):
        """Özel yapılandırma"""
        config = EpiasConfig(
            username="test@example.com",
            password="testpass",
            timeout_seconds=60,
            max_retries=5
        )
        
        assert config.username == "test@example.com"
        assert config.timeout_seconds == 60
        assert config.max_retries == 5


# ═══════════════════════════════════════════════════════════════════════════════
# MOCK CLIENT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMockEpiasClient:
    """Mock client testleri"""
    
    @pytest.mark.asyncio
    async def test_mock_ptf_known_period(self):
        """Mock PTF - bilinen dönem"""
        client = MockEpiasClient()
        result = await client.get_monthly_ptf_average("2024-12")
        
        assert result.period == "2024-12"
        assert result.average_tl_per_mwh == 2750.0
        assert result.source == "mock"
    
    @pytest.mark.asyncio
    async def test_mock_ptf_unknown_period(self):
        """Mock PTF - bilinmeyen dönem (default değer)"""
        client = MockEpiasClient()
        result = await client.get_monthly_ptf_average("2030-01")
        
        assert result.period == "2030-01"
        assert result.average_tl_per_mwh == 2900.0  # default
        assert result.source == "mock"
    
    @pytest.mark.asyncio
    async def test_mock_yekdem(self):
        """Mock YEKDEM"""
        client = MockEpiasClient()
        result = await client.get_yekdem_unit_price("2024-12")
        
        assert result.period == "2024-12"
        assert result.unit_cost_tl_per_mwh == 364.0
        assert result.source == "mock"
    
    @pytest.mark.asyncio
    async def test_mock_market_prices(self):
        """Mock piyasa fiyatları"""
        client = MockEpiasClient()
        result = await client.get_market_prices("2025-01")
        
        assert result.period == "2025-01"
        assert result.ptf_tl_per_mwh == 2780.0
        assert result.yekdem_tl_per_mwh == 366.0
        assert result.ptf_source == "mock"
        assert len(result.warnings) == 1  # Mock uyarısı


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestClientFactory:
    """Client factory testleri"""
    
    def test_get_mock_client(self):
        """Mock client factory"""
        client = get_epias_client(use_mock=True)
        assert isinstance(client, MockEpiasClient)
    
    def test_get_real_client(self):
        """Real client factory"""
        client = get_epias_client(use_mock=False, username="test", password="test")
        assert isinstance(client, EpiasClient)


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEPTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestExceptions:
    """Exception testleri"""
    
    def test_epias_api_error(self):
        """API hatası"""
        error = EpiasApiError("Connection failed")
        assert str(error) == "Connection failed"
    
    def test_epias_data_not_found(self):
        """Veri bulunamadı hatası"""
        error = EpiasDataNotFoundError("PTF verisi bulunamadı: 2025-01")
        assert "2025-01" in str(error)
        assert isinstance(error, EpiasApiError)
    
    def test_epias_auth_error(self):
        """Kimlik doğrulama hatası"""
        error = EpiasAuthError("Invalid credentials")
        assert "Invalid" in str(error)
        assert isinstance(error, EpiasApiError)
