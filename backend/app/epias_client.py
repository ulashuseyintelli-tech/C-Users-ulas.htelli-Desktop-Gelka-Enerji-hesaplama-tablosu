"""
EPİAŞ Şeffaflık Platformu API Client

Bu modül EPİAŞ'tan PTF (Piyasa Takas Fiyatı) ve YEKDEM verilerini çeker.

EPİAŞ API v2.0 kimlik doğrulama gerektiriyor. İki yöntem destekleniyor:
1. eptr2 kütüphanesi (önerilen) - pip install eptr2
2. Mock client (test/demo için)

Kullanım:
    # eptr2 ile (önerilen)
    client = EpiasClient(username="email@example.com", password="password")
    ptf = await client.get_monthly_ptf_average("2025-01")
    
    # Mock client ile (test için)
    client = MockEpiasClient()
    ptf = await client.get_monthly_ptf_average("2025-01")
    
    # Environment variables ile
    # EPIAS_USERNAME ve EPIAS_PASSWORD set edilmişse otomatik kullanılır
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EpiasConfig:
    """EPİAŞ API yapılandırması"""
    username: Optional[str] = None
    password: Optional[str] = None
    timeout_seconds: int = 30
    max_retries: int = 3
    
    def __post_init__(self):
        # Environment variables'dan al
        if not self.username:
            self.username = os.getenv("EPIAS_USERNAME")
        if not self.password:
            self.password = os.getenv("EPIAS_PASSWORD")


@dataclass
class PtfData:
    """PTF verisi"""
    period: str  # YYYY-MM
    average_tl_per_mwh: float
    min_tl_per_mwh: float
    max_tl_per_mwh: float
    data_points: int  # Kaç saatlik veri
    source: str = "epias"


@dataclass
class YekdemData:
    """YEKDEM verisi"""
    period: str  # YYYY-MM
    unit_cost_tl_per_mwh: float
    source: str = "epias"


@dataclass
class MarketPricesResult:
    """Piyasa fiyatları sonucu"""
    period: str
    ptf_tl_per_mwh: Optional[float]
    yekdem_tl_per_mwh: Optional[float]
    ptf_source: str
    yekdem_source: str
    ptf_data_points: int = 0
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ═══════════════════════════════════════════════════════════════════════════════

class EpiasApiError(Exception):
    """EPİAŞ API hatası"""
    pass


class EpiasDataNotFoundError(EpiasApiError):
    """Veri bulunamadı hatası"""
    pass


class EpiasAuthError(EpiasApiError):
    """Kimlik doğrulama hatası"""
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# EPİAŞ CLIENT (eptr2 tabanlı)
# ═══════════════════════════════════════════════════════════════════════════════

class EpiasClient:
    """
    EPİAŞ Şeffaflık Platformu API Client
    
    eptr2 kütüphanesini kullanır. Kimlik bilgileri gereklidir.
    """
    
    def __init__(self, config: Optional[EpiasConfig] = None, username: str = None, password: str = None):
        self.config = config or EpiasConfig(username=username, password=password)
        self._eptr = None
    
    def _get_eptr(self):
        """eptr2 client'ı lazy initialize et"""
        if self._eptr is None:
            try:
                from eptr2 import EPTR2
            except ImportError:
                raise EpiasApiError(
                    "eptr2 kütüphanesi yüklü değil. "
                    "Yüklemek için: pip install eptr2"
                )
            
            if not self.config.username or not self.config.password:
                raise EpiasAuthError(
                    "EPİAŞ kimlik bilgileri gerekli. "
                    "EPIAS_USERNAME ve EPIAS_PASSWORD environment variables'ları set edin "
                    "veya EpiasClient(username='...', password='...') kullanın."
                )
            
            self._eptr = EPTR2(
                username=self.config.username,
                password=self.config.password
            )
        
        return self._eptr
    
    def _get_period_dates(self, period: str) -> tuple[str, str]:
        """YYYY-MM formatından başlangıç ve bitiş tarihlerini hesapla."""
        year, month = int(period[:4]), int(period[5:7])
        start_date = datetime(year, month, 1)
        
        if month == 12:
            end_date = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = datetime(year, month + 1, 1) - timedelta(days=1)
        
        return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")
    
    async def get_hourly_ptf(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """Saatlik PTF verilerini çek."""
        import asyncio
        
        def _fetch():
            eptr = self._get_eptr()
            df = eptr.call("mcp", start_date=start_date, end_date=end_date)
            return df.to_dict('records') if hasattr(df, 'to_dict') else []
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)
    
    async def get_monthly_ptf_average(self, period: str) -> PtfData:
        """Belirli bir ay için PTF ortalamasını hesapla."""
        start_date, end_date = self._get_period_dates(period)
        
        logger.info(f"EPİAŞ PTF verisi çekiliyor: {period}")
        
        items = await self.get_hourly_ptf(start_date, end_date)
        
        if not items:
            raise EpiasDataNotFoundError(f"PTF verisi bulunamadı: {period}")
        
        prices = []
        for item in items:
            price = item.get("price") or item.get("mcp") or item.get("ptf")
            if price is not None:
                prices.append(float(price))
        
        if not prices:
            raise EpiasDataNotFoundError(f"PTF fiyat verisi bulunamadı: {period}")
        
        average = sum(prices) / len(prices)
        
        logger.info(f"EPİAŞ PTF: {len(prices)} saatlik veri, ortalama: {average:.2f} TL/MWh")
        
        return PtfData(
            period=period,
            average_tl_per_mwh=round(average, 2),
            min_tl_per_mwh=round(min(prices), 2),
            max_tl_per_mwh=round(max(prices), 2),
            data_points=len(prices),
            source="epias"
        )
    
    async def get_yekdem_unit_price(self, period: str) -> YekdemData:
        """Belirli bir ay için YEKDEM birim bedelini çek."""
        import asyncio
        
        start_date, end_date = self._get_period_dates(period)
        
        logger.info(f"EPİAŞ YEKDEM verisi çekiliyor: {period}")
        
        def _fetch():
            eptr = self._get_eptr()
            try:
                df = eptr.call("renewables-support-amount", start_date=start_date, end_date=end_date)
                return df.to_dict('records') if hasattr(df, 'to_dict') else []
            except Exception as e:
                logger.warning(f"YEKDEM call hatası: {e}")
                return []
        
        loop = asyncio.get_event_loop()
        items = await loop.run_in_executor(None, _fetch)
        
        if not items:
            raise EpiasDataNotFoundError(f"YEKDEM verisi bulunamadı: {period}")
        
        costs = []
        for item in items:
            cost = item.get("unitCost") or item.get("cost") or item.get("price") or item.get("amount")
            if cost is not None:
                costs.append(float(cost))
        
        if not costs:
            raise EpiasDataNotFoundError(f"YEKDEM birim bedeli bulunamadı: {period}")
        
        average = sum(costs) / len(costs)
        
        return YekdemData(
            period=period,
            unit_cost_tl_per_mwh=round(average, 2),
            source="epias"
        )
    
    async def get_market_prices(self, period: str) -> MarketPricesResult:
        """Belirli bir dönem için PTF ve YEKDEM fiyatlarını çek."""
        warnings = []
        ptf_data = None
        yekdem_data = None
        
        try:
            ptf_data = await self.get_monthly_ptf_average(period)
        except (EpiasApiError, EpiasDataNotFoundError) as e:
            warnings.append(f"PTF verisi alınamadı: {e}")
            logger.error(f"PTF fetch failed for {period}: {e}")
        
        try:
            yekdem_data = await self.get_yekdem_unit_price(period)
        except (EpiasApiError, EpiasDataNotFoundError) as e:
            warnings.append(f"YEKDEM verisi alınamadı: {e}")
            logger.error(f"YEKDEM fetch failed for {period}: {e}")
        
        return MarketPricesResult(
            period=period,
            ptf_tl_per_mwh=ptf_data.average_tl_per_mwh if ptf_data else None,
            yekdem_tl_per_mwh=yekdem_data.unit_cost_tl_per_mwh if yekdem_data else None,
            ptf_source="epias" if ptf_data else "unavailable",
            yekdem_source="epias" if yekdem_data else "unavailable",
            ptf_data_points=ptf_data.data_points if ptf_data else 0,
            warnings=warnings
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MOCK CLIENT (Test ve Demo için)
# ═══════════════════════════════════════════════════════════════════════════════

class MockEpiasClient:
    """
    Mock EPİAŞ client - test ve demo için.
    Gerçek API'ye bağlanmadan örnek veri döndürür.
    
    NOT: Bu değerler yaklaşık gerçek piyasa değerleridir.
    Production'da gerçek EPİAŞ API kullanılmalıdır.
    """
    
    # Örnek PTF verileri (TL/MWh) - Gerçekçi değerler
    # 2024-2025 Türkiye elektrik piyasası ağırlıklı ortalama PTF
    SAMPLE_PTF = {
        "2024-01": 2450.0, "2024-02": 2380.0, "2024-03": 2320.0,
        "2024-04": 2280.0, "2024-05": 2350.0, "2024-06": 2420.0,
        "2024-07": 2550.0, "2024-08": 2620.0, "2024-09": 2580.0,
        "2024-10": 2650.0, "2024-11": 2700.0, "2024-12": 2750.0,
        "2025-01": 2780.0, "2025-02": 2720.0, "2025-03": 2680.0,
        "2025-04": 2650.0, "2025-05": 2700.0, "2025-06": 2750.0,
        "2025-07": 2820.0, "2025-08": 2880.0, "2025-09": 2850.0,
        "2025-10": 2900.0, "2025-11": 2700.0, "2025-12": 2750.0,
        "2026-01": 2800.0, "2026-02": 2850.0, "2026-03": 2820.0,
    }
    
    # Örnek YEKDEM verileri (TL/MWh) - Gerçekçi değerler
    SAMPLE_YEKDEM = {
        "2024-01": 320.0, "2024-02": 325.0, "2024-03": 330.0,
        "2024-04": 335.0, "2024-05": 340.0, "2024-06": 345.0,
        "2024-07": 350.0, "2024-08": 355.0, "2024-09": 358.0,
        "2024-10": 360.0, "2024-11": 362.0, "2024-12": 364.0,
        "2025-01": 366.0, "2025-02": 368.0, "2025-03": 370.0,
        "2025-04": 372.0, "2025-05": 374.0, "2025-06": 376.0,
        "2025-07": 378.0, "2025-08": 380.0, "2025-09": 382.0,
        "2025-10": 384.0, "2025-11": 364.0, "2025-12": 366.0,
        "2026-01": 368.0, "2026-02": 370.0, "2026-03": 372.0,
    }
    
    async def get_monthly_ptf_average(self, period: str) -> PtfData:
        """Mock PTF verisi döndür"""
        ptf = self.SAMPLE_PTF.get(period, 2900.0)
        return PtfData(
            period=period,
            average_tl_per_mwh=ptf,
            min_tl_per_mwh=ptf * 0.85,
            max_tl_per_mwh=ptf * 1.15,
            data_points=720,
            source="mock"
        )
    
    async def get_yekdem_unit_price(self, period: str) -> YekdemData:
        """Mock YEKDEM verisi döndür"""
        yekdem = self.SAMPLE_YEKDEM.get(period, 360.0)
        return YekdemData(
            period=period,
            unit_cost_tl_per_mwh=yekdem,
            source="mock"
        )
    
    async def get_market_prices(self, period: str) -> MarketPricesResult:
        """Mock piyasa fiyatları döndür"""
        ptf_data = await self.get_monthly_ptf_average(period)
        yekdem_data = await self.get_yekdem_unit_price(period)
        
        return MarketPricesResult(
            period=period,
            ptf_tl_per_mwh=ptf_data.average_tl_per_mwh,
            yekdem_tl_per_mwh=yekdem_data.unit_cost_tl_per_mwh,
            ptf_source="mock",
            yekdem_source="mock",
            ptf_data_points=ptf_data.data_points,
            warnings=["Mock veri kullanılıyor - gerçek EPİAŞ verisi değil"]
        )


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_market_prices_from_epias(
    period: str,
    username: str = None,
    password: str = None,
    use_mock: bool = False
) -> MarketPricesResult:
    """
    EPİAŞ'tan piyasa fiyatlarını çek.
    
    Args:
        period: Dönem (YYYY-MM)
        username: EPİAŞ kullanıcı adı (opsiyonel)
        password: EPİAŞ şifresi (opsiyonel)
        use_mock: True ise mock veri kullan
    
    Returns:
        MarketPricesResult
    """
    if use_mock:
        client = MockEpiasClient()
    else:
        client = EpiasClient(username=username, password=password)
    
    return await client.get_market_prices(period)


async def fetch_ptf_from_epias(
    period: str,
    username: str = None,
    password: str = None,
    use_mock: bool = False
) -> Optional[float]:
    """EPİAŞ'tan PTF ortalamasını çek."""
    try:
        if use_mock:
            client = MockEpiasClient()
        else:
            client = EpiasClient(username=username, password=password)
        
        ptf_data = await client.get_monthly_ptf_average(period)
        return ptf_data.average_tl_per_mwh
    except (EpiasApiError, EpiasDataNotFoundError) as e:
        logger.error(f"PTF fetch failed: {e}")
        return None


async def fetch_yekdem_from_epias(
    period: str,
    username: str = None,
    password: str = None,
    use_mock: bool = False
) -> Optional[float]:
    """EPİAŞ'tan YEKDEM birim bedelini çek."""
    try:
        if use_mock:
            client = MockEpiasClient()
        else:
            client = EpiasClient(username=username, password=password)
        
        yekdem_data = await client.get_yekdem_unit_price(period)
        return yekdem_data.unit_cost_tl_per_mwh
    except (EpiasApiError, EpiasDataNotFoundError) as e:
        logger.error(f"YEKDEM fetch failed: {e}")
        return None


def get_epias_client(use_mock: bool = False, **kwargs):
    """EPİAŞ client factory."""
    if use_mock:
        return MockEpiasClient()
    return EpiasClient(**kwargs)
