"""
Piyasa Referans Fiyatları Servisi (PTF/YEKDEM)

Faz 1: Manuel güncelleme + DB tabanlı referans
Faz 2: EPİAŞ API/scraping entegrasyonu (opsiyonel)

Kullanım:
- get_market_prices(period) → PTF/YEKDEM for given period
- get_latest_market_prices() → En güncel dönem
- upsert_market_prices(period, ptf, yekdem) → Admin güncelleme
"""

import logging
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

def get_market_prices(db: Session, period: str) -> Optional[MarketPrices]:
    """
    Belirli dönem için piyasa fiyatlarını getir.
    
    Args:
        db: Database session
        period: Dönem (YYYY-MM format)
    
    Returns:
        MarketPrices veya None (bulunamazsa)
    """
    from .database import MarketReferencePrice
    
    record = db.query(MarketReferencePrice).filter(
        MarketReferencePrice.period == period
    ).first()
    
    if record:
        return MarketPrices(
            period=record.period,
            ptf_tl_per_mwh=record.ptf_tl_per_mwh,
            yekdem_tl_per_mwh=record.yekdem_tl_per_mwh,
            source="db",
            is_locked=bool(record.is_locked)
        )
    
    return None


def get_latest_market_prices(db: Session) -> Optional[MarketPrices]:
    """
    En güncel dönemin piyasa fiyatlarını getir.
    
    Returns:
        MarketPrices veya None
    """
    from .database import MarketReferencePrice
    
    record = db.query(MarketReferencePrice).order_by(
        MarketReferencePrice.period.desc()
    ).first()
    
    if record:
        return MarketPrices(
            period=record.period,
            ptf_tl_per_mwh=record.ptf_tl_per_mwh,
            yekdem_tl_per_mwh=record.yekdem_tl_per_mwh,
            source="db",
            is_locked=bool(record.is_locked)
        )
    
    return None


def get_market_prices_or_default(db: Session, period: str) -> MarketPrices:
    """
    Dönem için piyasa fiyatlarını getir, yoksa default döndür.
    
    Args:
        db: Database session
        period: Dönem (YYYY-MM format)
    
    Returns:
        MarketPrices (her zaman bir değer döner)
    """
    prices = get_market_prices(db, period)
    
    if prices:
        return prices
    
    # DB'de yok, default kullan
    logger.warning(f"Dönem {period} için piyasa fiyatı bulunamadı, default kullanılıyor")
    return MarketPrices(
        period=period,
        ptf_tl_per_mwh=DEFAULT_PTF_TL_PER_MWH,
        yekdem_tl_per_mwh=DEFAULT_YEKDEM_TL_PER_MWH,
        source="default",
        is_locked=False
    )


def upsert_market_prices(
    db: Session,
    period: str,
    ptf_tl_per_mwh: float,
    yekdem_tl_per_mwh: float,
    source_note: Optional[str] = None,
    updated_by: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Piyasa fiyatlarını ekle veya güncelle.
    
    Args:
        db: Database session
        period: Dönem (YYYY-MM format)
        ptf_tl_per_mwh: PTF fiyatı
        yekdem_tl_per_mwh: YEKDEM fiyatı
        source_note: Kaynak notu
        updated_by: Güncelleyen kullanıcı
    
    Returns:
        (success, message)
    """
    from .database import MarketReferencePrice
    
    # Guardrail kontrolleri
    validation_error = validate_market_prices(ptf_tl_per_mwh, yekdem_tl_per_mwh)
    if validation_error:
        return (False, validation_error)
    
    # Mevcut kayıt var mı?
    existing = db.query(MarketReferencePrice).filter(
        MarketReferencePrice.period == period
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
        
        db.commit()
        logger.info(f"Piyasa fiyatları güncellendi: {period} PTF={ptf_tl_per_mwh}, YEKDEM={yekdem_tl_per_mwh}")
        return (True, f"Dönem {period} güncellendi")
    else:
        # Yeni kayıt
        new_record = MarketReferencePrice(
            period=period,
            ptf_tl_per_mwh=ptf_tl_per_mwh,
            yekdem_tl_per_mwh=yekdem_tl_per_mwh,
            source_note=source_note,
            updated_by=updated_by,
            is_locked=0
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


def get_all_market_prices(db: Session, limit: int = 24) -> list[MarketPrices]:
    """
    Tüm piyasa fiyatlarını getir (son N dönem).
    """
    from .database import MarketReferencePrice
    
    records = db.query(MarketReferencePrice).order_by(
        MarketReferencePrice.period.desc()
    ).limit(limit).all()
    
    return [
        MarketPrices(
            period=r.period,
            ptf_tl_per_mwh=r.ptf_tl_per_mwh,
            yekdem_tl_per_mwh=r.yekdem_tl_per_mwh,
            source="db",
            is_locked=bool(r.is_locked)
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
