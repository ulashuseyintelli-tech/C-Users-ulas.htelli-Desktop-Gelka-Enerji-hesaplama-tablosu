"""
Piyasa Referans Fiyatları Servisi (PTF/YEKDEM)

Veri Kaynakları (öncelik sırasına göre):
1. DB'deki manuel/cache değerler
2. EPİAŞ Şeffaflık Platformu API (otomatik çekme)
3. Default değerler (fallback)

Kullanım:
- get_market_prices(period) → PTF/YEKDEM for given period
- get_latest_market_prices() → En güncel dönem
- upsert_market_prices(period, ptf, yekdem) → Admin güncelleme
- fetch_and_cache_from_epias(period) → EPİAŞ'tan çek ve cache'le
"""

import logging
import asyncio
from datetime import datetime
from typing import Optional, Tuple
from dataclasses import dataclass
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class MarketPrices:
    """Piyasa referans fiyatları"""
    period: str  # YYYY-MM
    ptf_tl_per_mwh: float
    yekdem_tl_per_mwh: float
    source: str  # "db", "default", "override"
    is_locked: bool = False
    # Yeni alanlar (backward compatible - default değerlerle)
    status: str = "final"  # provisional | final (null status = final for backward compat)
    price_type: str = "PTF"  # PTF, SMF, YEKDEM
    captured_at: Optional[datetime] = None
    change_reason: Optional[str] = None
    source_detail: Optional[str] = None  # epias_manual | epias_api | migration | seed



# ═══════════════════════════════════════════════════════════════════════════════
# DEFAULT DEĞERLER (DB'de kayıt yoksa kullanılır)
# ═══════════════════════════════════════════════════════════════════════════════
# Bu değerler sadece fallback - production'da DB'den gelmeli

DEFAULT_PTF_TL_PER_MWH = 2974.1  # Ocak 2025 tahmini ortalama
DEFAULT_YEKDEM_TL_PER_MWH = 364.0  # Ocak 2025 tahmini

# Guardrail eşikleri
MIN_PTF_TL_PER_MWH = 500.0  # Çok düşük = muhtemelen hata
MAX_PTF_TL_PER_MWH = 10000.0  # Çok yüksek = muhtemelen hata
MIN_YEKDEM_TL_PER_MWH = 0.0  # YEKDEM 0 olabilir (muaf)
MAX_YEKDEM_TL_PER_MWH = 1000.0  # Çok yüksek = muhtemelen hata


# ═══════════════════════════════════════════════════════════════════════════════
# DB FONKSİYONLARI
# ═══════════════════════════════════════════════════════════════════════════════

def get_market_prices(db: Session, period: str, price_type: str = "PTF") -> Optional[MarketPrices]:
    """
    Belirli dönem için piyasa fiyatlarını getir.
    
    Backward compatibility: null status DB kayıtları "final" olarak döner.
    
    Args:
        db: Database session
        period: Dönem (YYYY-MM format)
        price_type: Fiyat tipi (default: "PTF")
    
    Returns:
        MarketPrices veya None (bulunamazsa)
    """
    from .database import MarketReferencePrice
    
    record = db.query(MarketReferencePrice).filter(
        MarketReferencePrice.period == period,
        MarketReferencePrice.price_type == price_type
    ).first()
    
    if record:
        # Backward compatibility: null status = "final"
        record_status = record.status if record.status else "final"
        return MarketPrices(
            period=record.period,
            ptf_tl_per_mwh=record.ptf_tl_per_mwh,
            yekdem_tl_per_mwh=record.yekdem_tl_per_mwh,
            source="db",
            is_locked=bool(record.is_locked),
            status=record_status,
            price_type=getattr(record, 'price_type', 'PTF') or 'PTF',
            captured_at=getattr(record, 'captured_at', None),
            change_reason=getattr(record, 'change_reason', None),
            source_detail=getattr(record, 'source', None),
        )
    
    return None


def get_latest_market_prices(db: Session, price_type: str = "PTF") -> Optional[MarketPrices]:
    """
    En güncel dönemin piyasa fiyatlarını getir.
    
    Args:
        db: Database session
        price_type: Fiyat tipi (default: "PTF")
    
    Returns:
        MarketPrices veya None
    """
    from .database import MarketReferencePrice
    
    record = db.query(MarketReferencePrice).filter(
        MarketReferencePrice.price_type == price_type
    ).order_by(
        MarketReferencePrice.period.desc()
    ).first()
    
    if record:
        # Backward compatibility: null status = "final"
        record_status = record.status if record.status else "final"
        return MarketPrices(
            period=record.period,
            ptf_tl_per_mwh=record.ptf_tl_per_mwh,
            yekdem_tl_per_mwh=record.yekdem_tl_per_mwh,
            source="db",
            is_locked=bool(record.is_locked),
            status=record_status,
            price_type=getattr(record, 'price_type', 'PTF') or 'PTF',
            captured_at=getattr(record, 'captured_at', None),
            change_reason=getattr(record, 'change_reason', None),
            source_detail=getattr(record, 'source', None),
        )
    
    return None


def get_market_prices_or_default(db: Session, period: str, price_type: str = "PTF") -> MarketPrices:
    """
    Dönem için piyasa fiyatlarını getir, yoksa default döndür.
    
    Args:
        db: Database session
        period: Dönem (YYYY-MM format)
        price_type: Fiyat tipi (default: "PTF")
    
    Returns:
        MarketPrices (her zaman bir değer döner)
    """
    prices = get_market_prices(db, period, price_type=price_type)
    
    if prices:
        return prices
    
    # DB'de yok, default kullan
    logger.warning(f"Dönem {period} için piyasa fiyatı bulunamadı, default kullanılıyor")
    return MarketPrices(
        period=period,
        ptf_tl_per_mwh=DEFAULT_PTF_TL_PER_MWH,
        yekdem_tl_per_mwh=DEFAULT_YEKDEM_TL_PER_MWH,
        source="default",
        is_locked=False,
        status="final",
        price_type=price_type,
    )


def upsert_market_prices(
    db: Session,
    period: str,
    ptf_tl_per_mwh: float,
    yekdem_tl_per_mwh: float,
    source_note: Optional[str] = None,
    updated_by: Optional[str] = None,
    status: Optional[str] = None,
    price_type: str = "PTF",
    captured_at: Optional[datetime] = None,
    change_reason: Optional[str] = None,
    source: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Piyasa fiyatlarını ekle veya güncelle.
    
    Backward compatibility:
    - status=None → yeni kayıtlarda "provisional", mevcut kayıtlarda değiştirilmez
    - Null status DB kayıtları "final" olarak kabul edilir (Requirement 1.6)
    - price_type default "PTF"
    - captured_at default datetime.utcnow()
    - source default "epias_manual"
    
    Args:
        db: Database session
        period: Dönem (YYYY-MM format)
        ptf_tl_per_mwh: PTF fiyatı
        yekdem_tl_per_mwh: YEKDEM fiyatı
        source_note: Kaynak notu
        updated_by: Güncelleyen kullanıcı
        status: Durum (provisional/final, None=backward compat)
        price_type: Fiyat tipi (default: "PTF")
        captured_at: Verinin alındığı tarih (default: now UTC)
        change_reason: Değişiklik nedeni (audit)
        source: Kaynak (epias_manual/epias_api/migration/seed)
    
    Returns:
        (success, message)
    """
    from .database import MarketReferencePrice
    
    # Guardrail kontrolleri
    validation_error = validate_market_prices(ptf_tl_per_mwh, yekdem_tl_per_mwh)
    if validation_error:
        return (False, validation_error)
    
    # Mevcut kayıt var mı? (price_type + period unique)
    existing = db.query(MarketReferencePrice).filter(
        MarketReferencePrice.period == period,
        MarketReferencePrice.price_type == price_type
    ).first()
    
    if existing:
        # Kilitli mi?
        if existing.is_locked:
            return (False, f"Dönem {period} kilitli, güncellenemez")
        
        # Güncelle
        existing.ptf_tl_per_mwh = ptf_tl_per_mwh
        existing.yekdem_tl_per_mwh = yekdem_tl_per_mwh
        existing.source_note = source_note
        existing.updated_by = updated_by
        existing.updated_at = datetime.utcnow()
        
        # Yeni alanları güncelle (sadece verilmişse)
        if status is not None:
            existing.status = status
        if captured_at is not None:
            existing.captured_at = captured_at
        if change_reason is not None:
            existing.change_reason = change_reason
        if source is not None:
            existing.source = source
        
        db.commit()
        logger.info(f"Piyasa fiyatları güncellendi: {period} PTF={ptf_tl_per_mwh}, YEKDEM={yekdem_tl_per_mwh}")
        return (True, f"Dönem {period} güncellendi")
    else:
        # Yeni kayıt - status default "provisional" (Requirement 1.5)
        effective_status = status if status is not None else "provisional"
        effective_captured_at = captured_at if captured_at is not None else datetime.utcnow()
        effective_source = source if source is not None else "epias_manual"
        
        new_record = MarketReferencePrice(
            period=period,
            ptf_tl_per_mwh=ptf_tl_per_mwh,
            yekdem_tl_per_mwh=yekdem_tl_per_mwh,
            source_note=source_note,
            updated_by=updated_by,
            is_locked=0,
            price_type=price_type,
            status=effective_status,
            captured_at=effective_captured_at,
            change_reason=change_reason,
            source=effective_source,
        )
        db.add(new_record)
        db.commit()
        logger.info(f"Piyasa fiyatları eklendi: {period} PTF={ptf_tl_per_mwh}, YEKDEM={yekdem_tl_per_mwh}")
        return (True, f"Dönem {period} eklendi")


def lock_market_prices(db: Session, period: str) -> Tuple[bool, str]:
    """
    Dönem fiyatlarını kilitle (geçmiş dönem koruması).
    """
    from .database import MarketReferencePrice
    
    record = db.query(MarketReferencePrice).filter(
        MarketReferencePrice.period == period
    ).first()
    
    if not record:
        return (False, f"Dönem {period} bulunamadı")
    
    record.is_locked = 1
    db.commit()
    logger.info(f"Dönem {period} kilitlendi")
    return (True, f"Dönem {period} kilitlendi")


def get_all_market_prices(db: Session, limit: int = 24, price_type: str = "PTF") -> list[MarketPrices]:
    """
    Tüm piyasa fiyatlarını getir (son N dönem).
    
    Args:
        db: Database session
        limit: Maksimum kayıt sayısı
        price_type: Fiyat tipi (default: "PTF")
    """
    from .database import MarketReferencePrice
    
    records = db.query(MarketReferencePrice).filter(
        MarketReferencePrice.price_type == price_type
    ).order_by(
        MarketReferencePrice.period.desc()
    ).limit(limit).all()
    
    return [
        MarketPrices(
            period=r.period,
            ptf_tl_per_mwh=r.ptf_tl_per_mwh,
            yekdem_tl_per_mwh=r.yekdem_tl_per_mwh,
            source="db",
            is_locked=bool(r.is_locked),
            status=r.status if r.status else "final",
            price_type=getattr(r, 'price_type', 'PTF') or 'PTF',
            captured_at=getattr(r, 'captured_at', None),
            change_reason=getattr(r, 'change_reason', None),
            source_detail=getattr(r, 'source', None),
        )
        for r in records
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# GUARDRAIL / VALİDASYON
# ═══════════════════════════════════════════════════════════════════════════════

def validate_market_prices(
    ptf_tl_per_mwh: float,
    yekdem_tl_per_mwh: float
) -> Optional[str]:
    """
    Piyasa fiyatlarını doğrula.
    
    Returns:
        Hata mesajı veya None (geçerli ise)
    """
    errors = []
    
    # PTF kontrolü
    if ptf_tl_per_mwh <= 0:
        errors.append("PTF 0 veya negatif olamaz")
    elif ptf_tl_per_mwh < MIN_PTF_TL_PER_MWH:
        errors.append(f"PTF çok düşük: {ptf_tl_per_mwh} < {MIN_PTF_TL_PER_MWH} TL/MWh")
    elif ptf_tl_per_mwh > MAX_PTF_TL_PER_MWH:
        errors.append(f"PTF çok yüksek: {ptf_tl_per_mwh} > {MAX_PTF_TL_PER_MWH} TL/MWh")
    
    # YEKDEM kontrolü
    if yekdem_tl_per_mwh < MIN_YEKDEM_TL_PER_MWH:
        errors.append(f"YEKDEM negatif olamaz: {yekdem_tl_per_mwh}")
    elif yekdem_tl_per_mwh > MAX_YEKDEM_TL_PER_MWH:
        errors.append(f"YEKDEM çok yüksek: {yekdem_tl_per_mwh} > {MAX_YEKDEM_TL_PER_MWH} TL/MWh")
    
    if errors:
        return "; ".join(errors)
    
    return None


def validate_market_prices_for_calculation(
    ptf_tl_per_mwh: Optional[float],
    yekdem_tl_per_mwh: Optional[float]
) -> Tuple[bool, Optional[str]]:
    """
    Hesaplama için piyasa fiyatlarını doğrula.
    
    Returns:
        (is_valid, error_message)
    """
    # PTF zorunlu
    if ptf_tl_per_mwh is None or ptf_tl_per_mwh <= 0:
        return (False, "PTF değeri gerekli ve 0'dan büyük olmalı")
    
    # YEKDEM opsiyonel ama negatif olamaz
    if yekdem_tl_per_mwh is not None and yekdem_tl_per_mwh < 0:
        return (False, "YEKDEM negatif olamaz")
    
    # Aralık kontrolü (soft warning değil, hard error)
    if ptf_tl_per_mwh < MIN_PTF_TL_PER_MWH or ptf_tl_per_mwh > MAX_PTF_TL_PER_MWH:
        return (False, f"PTF değeri makul aralıkta değil: {ptf_tl_per_mwh} TL/MWh (beklenen: {MIN_PTF_TL_PER_MWH}-{MAX_PTF_TL_PER_MWH})")
    
    return (True, None)


# ═══════════════════════════════════════════════════════════════════════════════
# YARDIMCI FONKSİYONLAR
# ═══════════════════════════════════════════════════════════════════════════════

def mwh_to_kwh(price_tl_per_mwh: float) -> float:
    """TL/MWh → TL/kWh dönüşümü"""
    return price_tl_per_mwh / 1000


def kwh_to_mwh(price_tl_per_kwh: float) -> float:
    """TL/kWh → TL/MWh dönüşümü"""
    return price_tl_per_kwh * 1000


def get_current_period() -> str:
    """Şu anki dönem (YYYY-MM)"""
    return datetime.now().strftime("%Y-%m")


def get_previous_period(period: str) -> str:
    """Bir önceki dönem"""
    year, month = int(period[:4]), int(period[5:7])
    if month == 1:
        return f"{year-1}-12"
    return f"{year}-{month-1:02d}"


# ═══════════════════════════════════════════════════════════════════════════════
# EPİAŞ ENTEGRASYONU
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_and_cache_from_epias(
    db: Session,
    period: str,
    force_refresh: bool = False,
    use_mock: bool = False
) -> Tuple[bool, MarketPrices, str]:
    """
    EPİAŞ'tan piyasa fiyatlarını çek ve DB'ye cache'le.
    
    Args:
        db: Database session
        period: Dönem (YYYY-MM)
        force_refresh: True ise mevcut cache'i yoksay
        use_mock: True ise mock veri kullan (test/demo için)
    
    Returns:
        (success, market_prices, message)
    """
    from .epias_client import fetch_market_prices_from_epias, EpiasApiError, EpiasAuthError
    
    # Önce DB'de var mı kontrol et
    if not force_refresh:
        existing = get_market_prices(db, period)
        if existing and existing.source in ("epias", "mock"):
            logger.info(f"Dönem {period} için cache kullanılıyor")
            return (True, existing, "Cache'den alındı")
    
    # EPİAŞ'tan çek (veya mock kullan)
    try:
        result = await fetch_market_prices_from_epias(period, use_mock=use_mock)
        
        if result.ptf_tl_per_mwh is None:
            return (False, None, f"EPİAŞ'tan PTF verisi alınamadı: {', '.join(result.warnings)}")
        
        # DB'ye kaydet
        source_note = f"Mock data" if use_mock else f"EPİAŞ API ({result.ptf_data_points} data points)"
        success, msg = upsert_market_prices(
            db=db,
            period=period,
            ptf_tl_per_mwh=result.ptf_tl_per_mwh,
            yekdem_tl_per_mwh=result.yekdem_tl_per_mwh or 0,
            source_note=source_note,
            updated_by="epias_sync" if not use_mock else "mock_sync",
            source="epias_api",
        )
        
        if not success:
            return (False, None, f"DB kayıt hatası: {msg}")
        
        # Yeni kaydı döndür
        prices = MarketPrices(
            period=period,
            ptf_tl_per_mwh=result.ptf_tl_per_mwh,
            yekdem_tl_per_mwh=result.yekdem_tl_per_mwh or 0,
            source="mock" if use_mock else "epias",
            is_locked=False
        )
        
        warnings_str = f" (Uyarılar: {', '.join(result.warnings)})" if result.warnings else ""
        source_str = "Mock veriden" if use_mock else "EPİAŞ'tan"
        return (True, prices, f"{source_str} alındı ve cache'lendi{warnings_str}")
        
    except EpiasAuthError as e:
        logger.error(f"EPİAŞ kimlik doğrulama hatası: {e}")
        return (False, None, f"EPİAŞ kimlik doğrulama hatası: {str(e)}")
    except Exception as e:
        logger.error(f"EPİAŞ fetch hatası: {e}")
        return (False, None, f"EPİAŞ API hatası: {str(e)}")


def get_market_prices_with_epias_fallback(
    db: Session,
    period: str,
    auto_fetch: bool = True
) -> Tuple[MarketPrices, str]:
    """
    Piyasa fiyatlarını al - DB yoksa EPİAŞ'tan çek.
    
    Öncelik sırası:
    1. DB'deki kayıt
    2. EPİAŞ API (auto_fetch=True ise)
    3. Default değerler
    
    Args:
        db: Database session
        period: Dönem (YYYY-MM)
        auto_fetch: EPİAŞ'tan otomatik çek
    
    Returns:
        (market_prices, source_description)
    """
    # 1. DB'de ara
    prices = get_market_prices(db, period)
    if prices:
        return (prices, f"DB ({prices.source})")
    
    # 2. EPİAŞ'tan çek (sync wrapper)
    if auto_fetch:
        try:
            # Async fonksiyonu sync context'te çalıştır
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                success, epias_prices, msg = loop.run_until_complete(
                    fetch_and_cache_from_epias(db, period)
                )
                if success and epias_prices:
                    return (epias_prices, f"EPİAŞ API: {msg}")
            finally:
                loop.close()
        except Exception as e:
            logger.warning(f"EPİAŞ auto-fetch başarısız: {e}")
    
    # 3. Default
    logger.warning(f"Dönem {period} için piyasa fiyatı bulunamadı, default kullanılıyor")
    return (
        MarketPrices(
            period=period,
            ptf_tl_per_mwh=DEFAULT_PTF_TL_PER_MWH,
            yekdem_tl_per_mwh=DEFAULT_YEKDEM_TL_PER_MWH,
            source="default",
            is_locked=False
        ),
        "Default (EPİAŞ ve DB'de bulunamadı)"
    )


async def sync_multiple_periods_from_epias(
    db: Session,
    periods: list[str],
    force_refresh: bool = False
) -> dict[str, Tuple[bool, str]]:
    """
    Birden fazla dönem için EPİAŞ'tan veri çek.
    
    Args:
        db: Database session
        periods: Dönem listesi (YYYY-MM)
        force_refresh: Mevcut cache'i yoksay
    
    Returns:
        {period: (success, message)}
    """
    results = {}
    
    for period in periods:
        success, _, msg = await fetch_and_cache_from_epias(db, period, force_refresh)
        results[period] = (success, msg)
    
    return results


def get_periods_needing_sync(db: Session, months_back: int = 12) -> list[str]:
    """
    Sync edilmesi gereken dönemleri listele.
    
    DB'de kaydı olmayan veya source="default" olan dönemler.
    
    Args:
        db: Database session
        months_back: Kaç ay geriye bak
    
    Returns:
        Dönem listesi
    """
    from datetime import datetime
    
    current = datetime.now()
    periods_to_check = []
    
    for i in range(months_back):
        year = current.year
        month = current.month - i
        
        while month <= 0:
            month += 12
            year -= 1
        
        periods_to_check.append(f"{year}-{month:02d}")
    
    # DB'de olmayanları veya default olanları filtrele
    missing = []
    for period in periods_to_check:
        prices = get_market_prices(db, period)
        if not prices or prices.source == "default":
            missing.append(period)
    
    return missing
