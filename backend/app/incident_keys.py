"""
Incident Keys - Sprint 6.0

Stabil dedupe key uretimi.
PTF tarihleri vs. gibi "kayan zaman" alanlari dedupe'a girmiyor.
"""

import hashlib
from typing import Optional


def sha256_hex(s: str) -> str:
    """SHA256 hash hex string olarak doner."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def dedupe_key_v2(
    provider: str,
    invoice_id: str,
    primary_flag: str,
    category: str,
    action_code: str,
    period_yyyy_mm: str,
) -> str:
    """
    Stabil dedupe key uretir.
    
    Kurallar:
    - Period disinda hicbir "kayan zaman" alani yok
    - PTF tarihleri, lookup zamanlari vs. dedupe'a girmiyor
    - all_flags dedupe'a girmiyor (multi-flag set degisince spam baslar)
    
    Args:
        provider: Fatura saglayici (ck_bogazici, enerjisa, etc.)
        invoice_id: Fatura ID veya hash
        primary_flag: Ana hata flag'i
        category: Incident kategorisi
        action_code: HintCode (stable action code)
        period_yyyy_mm: Fatura donemi (YYYY-MM)
    
    Returns:
        SHA256 hex string
    """
    base = f"{provider}|{invoice_id}|{primary_flag}|{category}|{action_code}|{period_yyyy_mm}"
    return sha256_hex(base)


def generate_invoice_hash(
    supplier: str = "",
    invoice_no: str = "",
    period: str = "",
    consumption_kwh: float = 0,
    total_amount: float = 0,
) -> str:
    """
    Invoice ID yoksa deterministik hash uretir.
    
    Args:
        supplier: Tedarikci adi
        invoice_no: Fatura numarasi (varsa)
        period: Fatura donemi
        consumption_kwh: Tuketim
        total_amount: Toplam tutar
    
    Returns:
        16 karakter hex hash
    """
    parts = [
        str(supplier or "").lower().strip(),
        str(invoice_no or "").strip(),
        str(period or "").strip(),
        f"{float(consumption_kwh or 0):.2f}",
        f"{float(total_amount or 0):.2f}",
    ]
    return sha256_hex("|".join(parts))[:16]


def extract_period_from_dates(
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    invoice_date: Optional[str] = None,
) -> str:
    """
    Tarihlerden YYYY-MM period cikarir.
    
    Oncelik: period_start > period_end > invoice_date
    
    Returns:
        YYYY-MM format veya bos string
    """
    for date_str in [period_start, period_end, invoice_date]:
        if date_str and len(date_str) >= 7:
            # YYYY-MM-DD veya YYYY-MM formatini destekle
            return date_str[:7]
    return ""
