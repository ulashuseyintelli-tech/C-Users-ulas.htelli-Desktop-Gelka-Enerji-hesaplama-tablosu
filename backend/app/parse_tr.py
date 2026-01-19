"""
Türkçe Sayı Parser Modülü

Türkiye formatı: 593.740,00 (nokta=binlik, virgül=ondalık)
Bu modül güvenli parse sağlar.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Optional, Union
import logging

logger = logging.getLogger(__name__)


def parse_tr_decimal(s: str) -> Optional[Decimal]:
    """
    Türkçe sayı formatını Decimal'e çevir.
    
    Örnekler:
    - "593.740,00" → Decimal("593740.00")
    - "98.956,24" → Decimal("98956.24")
    - "4,363902" → Decimal("4.363902")
    - "116.145,630 kWh" → Decimal("116145.630")
    
    Args:
        s: Türkçe formatlı sayı string'i
        
    Returns:
        Decimal veya None (parse edilemezse)
    """
    if s is None:
        return None
    
    s = str(s).strip()
    
    # Para birimi, boşluk, NBSP vb temizle
    s = s.replace("\u00a0", " ")  # Non-breaking space
    s = re.sub(r"[^\d\.,\-]", "", s)  # Sadece rakam, nokta, virgül, eksi
    
    if not s:
        return None
    
    # Türkçe format: nokta binlik, virgül ondalık
    # 593.740,00 → 593740.00
    s = s.replace(".", "")  # Binlik ayracı kaldır
    s = s.replace(",", ".")  # Virgülü noktaya çevir
    
    try:
        return Decimal(s)
    except InvalidOperation:
        logger.warning(f"Decimal parse hatası: '{s}'")
        return None


def parse_tr_float(s: str) -> Optional[float]:
    """
    Türkçe sayı formatını float'a çevir.
    
    Decimal versiyonunun float wrapper'ı.
    """
    d = parse_tr_decimal(s)
    return float(d) if d is not None else None


def format_tr_decimal(value: Union[Decimal, float, int], decimals: int = 2) -> str:
    """
    Sayıyı Türkçe formata çevir.
    
    Örnekler:
    - 593740.00 → "593.740,00"
    - 4.363902 → "4,363902"
    
    Args:
        value: Sayı değeri
        decimals: Ondalık basamak sayısı
        
    Returns:
        Türkçe formatlı string
    """
    if value is None:
        return ""
    
    # Float'a çevir
    f = float(value)
    
    # Format string oluştur
    formatted = f"{f:,.{decimals}f}"
    
    # İngilizce formatı Türkçe'ye çevir
    # 593,740.00 → 593.740,00
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    
    return formatted


def percent_diff(a: Optional[Decimal], b: Optional[Decimal]) -> Decimal:
    """
    İki değer arasındaki yüzde farkı hesapla.
    
    Args:
        a: Birinci değer
        b: İkinci değer
        
    Returns:
        Yüzde fark (mutlak değer)
    """
    if a is None or b is None:
        return Decimal("999")
    
    if a == 0 and b == 0:
        return Decimal("0")
    
    denom = a.copy_abs() if a != 0 else Decimal("1")
    return ((a - b).copy_abs() / denom) * 100


def reconcile_amount(
    text_val: Optional[Decimal], 
    vision_val: Optional[Decimal],
    tolerance_exact: Decimal = Decimal("0.10"),
    tolerance_rounding: Decimal = Decimal("0.50")
) -> dict:
    """
    Text ve Vision değerlerini karşılaştır ve uzlaştır.
    
    Args:
        text_val: pdfplumber'dan gelen değer
        vision_val: OpenAI Vision'dan gelen değer
        tolerance_exact: Tam eşleşme toleransı (%)
        tolerance_rounding: Yuvarlama toleransı (%)
        
    Returns:
        {
            "final": Decimal,  # Kullanılacak değer
            "confidence": float,  # Güven skoru (0-1)
            "flag": str | None,  # Uyarı flag'i
            "source": str  # Değerin kaynağı
        }
    """
    if text_val is not None and vision_val is not None:
        diff = percent_diff(text_val, vision_val)
        
        if diff <= tolerance_exact:
            # Tam eşleşme - text'e güven (daha güvenilir)
            return {
                "final": text_val, 
                "confidence": 0.98, 
                "flag": None,
                "source": "text_confirmed"
            }
        
        if diff <= tolerance_rounding:
            # Yuvarlama farkı - text'i kullan ama uyar
            return {
                "final": text_val, 
                "confidence": 0.90, 
                "flag": "ROUNDING_MISMATCH",
                "source": "text_with_rounding"
            }
        
        # Ciddi fark - manuel kontrol gerekli
        return {
            "final": None, 
            "confidence": 0.20, 
            "flag": "HARD_MISMATCH",
            "source": "conflict"
        }
    
    if text_val is not None:
        # Sadece text var - vision eksik
        return {
            "final": text_val, 
            "confidence": 0.85, 
            "flag": "VISION_MISSING",
            "source": "text_only"
        }
    
    if vision_val is not None:
        # Sadece vision var - text eksik (taranmış PDF?)
        return {
            "final": vision_val, 
            "confidence": 0.70, 
            "flag": "TEXT_MISSING",
            "source": "vision_only"
        }
    
    # İkisi de yok
    return {
        "final": None, 
        "confidence": 0.0, 
        "flag": "BOTH_MISSING",
        "source": "none"
    }
