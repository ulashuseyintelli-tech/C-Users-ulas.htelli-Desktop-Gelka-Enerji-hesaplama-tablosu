"""
PDF Metin Çıkarma Modülü

Hibrit yaklaşım için KATMAN 1:
- pdfplumber ile dijital PDF'lerden metin çıkar
- Tablo yapısını koru
- Kritik alanları regex ile bul

Bu modül OpenAI Vision'dan ÖNCE çalışır ve
bulunan değerleri prompt'a ekler (cross-validation için).
"""

import re
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ExtractedText:
    """PDF'den çıkarılan metin ve değerler."""
    raw_text: str
    page_count: int
    
    # Kritik alanlar (regex ile bulunan)
    odenecek_tutar: Optional[float] = None
    kdv_tutari: Optional[float] = None
    kdv_matrahi: Optional[float] = None
    toplam_tuketim_kwh: Optional[float] = None
    
    # Tablo verileri
    tables: List[List[str]] = None
    
    # Meta
    is_digital: bool = True  # Metin çıkarılabildi mi?
    extraction_quality: str = "unknown"  # good, partial, poor


def extract_text_from_pdf(pdf_bytes: bytes) -> ExtractedText:
    """
    PDF'den metin ve kritik değerleri çıkar.
    
    Args:
        pdf_bytes: PDF dosyası bytes
        
    Returns:
        ExtractedText: Çıkarılan metin ve değerler
    """
    try:
        import pdfplumber
        import io
        
        result = ExtractedText(raw_text="", page_count=0)
        all_text = []
        all_tables = []
        
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            result.page_count = len(pdf.pages)
            
            for i, page in enumerate(pdf.pages):
                # Metin çıkar
                text = page.extract_text() or ""
                all_text.append(f"--- SAYFA {i+1} ---\n{text}")
                
                # Tablo çıkar
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        all_tables.append(table)
        
        result.raw_text = "\n".join(all_text)
        result.tables = all_tables
        
        # Metin kalitesi kontrolü
        if len(result.raw_text.strip()) < 100:
            result.is_digital = False
            result.extraction_quality = "poor"
            logger.warning("PDF metin içermiyor veya çok az metin var (muhtemelen taranmış)")
        else:
            result.is_digital = True
            result.extraction_quality = "good"
            
            # Kritik değerleri regex ile bul
            result.odenecek_tutar = _find_odenecek_tutar(result.raw_text)
            result.kdv_tutari = _find_kdv_tutari(result.raw_text)
            result.kdv_matrahi = _find_kdv_matrahi(result.raw_text)
            result.toplam_tuketim_kwh = _find_toplam_tuketim(result.raw_text)
            
            logger.info(
                f"PDF metin çıkarıldı: {result.page_count} sayfa, "
                f"{len(result.raw_text)} karakter, "
                f"ödenecek={result.odenecek_tutar}, kdv={result.kdv_tutari}"
            )
        
        return result
        
    except ImportError:
        logger.warning("pdfplumber yüklü değil, metin çıkarma atlanıyor")
        return ExtractedText(raw_text="", page_count=0, is_digital=False, extraction_quality="unavailable")
    except Exception as e:
        logger.error(f"PDF metin çıkarma hatası: {e}")
        return ExtractedText(raw_text="", page_count=0, is_digital=False, extraction_quality="error")


def _parse_turkish_number(text: str) -> Optional[float]:
    """
    Türkçe sayı formatını parse et.
    "593.740,00" → 593740.00
    "98.956,24" → 98956.24
    """
    if not text:
        return None
    
    # Temizle
    text = text.strip().replace(" ", "").replace("TL", "").replace("₺", "")
    
    # Türkçe format: nokta binlik, virgül ondalık
    # 593.740,00 → 593740.00
    text = text.replace(".", "")  # Binlik ayracı kaldır
    text = text.replace(",", ".")  # Virgülü noktaya çevir
    
    try:
        return float(text)
    except ValueError:
        return None


def _find_odenecek_tutar(text: str) -> Optional[float]:
    """
    "Ödenecek Tutar" değerini bul.
    
    Örnek pattern'ler:
    - "Ödenecek Tutar 593.740,00"
    - "Ödenecek\nTutar\n593.740,00"
    - "ÖDENECEK TUTAR: 593.740,00 TL"
    """
    patterns = [
        r'[Öö]denecek\s*[Tt]utar[:\s]*([0-9.,]+)',
        r'ÖDENECEK\s*TUTAR[:\s]*([0-9.,]+)',
        r'Toplam\s*Tutar[:\s]*([0-9.,]+)',
        r'TOPLAM\s*TUTAR[:\s]*([0-9.,]+)',
        r'Fatura\s*Tutarı[:\s]*([0-9.,]+)',
        r'FATURA\s*TUTARI[:\s]*([0-9.,]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = _parse_turkish_number(match.group(1))
            if value and value > 100:  # Mantıklı bir tutar mı?
                logger.info(f"Ödenecek tutar bulundu: {value} (pattern: {pattern[:30]}...)")
                return value
    
    return None


def _find_kdv_tutari(text: str) -> Optional[float]:
    """
    KDV tutarını bul.
    
    Örnek pattern'ler:
    - "KDV (Matrah 494.781,19) 98.956,24"
    - "KDV: 98.956,24"
    - "Katma Değer Vergisi 98.956,24"
    """
    patterns = [
        r'KDV\s*\([^)]+\)\s*([0-9.,]+)',  # KDV (Matrah X) Y
        r'KDV[:\s]+([0-9.,]+)',
        r'Katma\s*Değer\s*Vergisi[:\s]*([0-9.,]+)',
        r'K\.?D\.?V\.?[:\s]*([0-9.,]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = _parse_turkish_number(match.group(1))
            if value and value > 10:  # Mantıklı bir KDV mı?
                logger.info(f"KDV tutarı bulundu: {value}")
                return value
    
    return None


def _find_kdv_matrahi(text: str) -> Optional[float]:
    """
    KDV matrahını bul.
    
    Örnek pattern'ler:
    - "KDV (Matrah 494.781,19)"
    - "KDV Matrahı: 494.781,19"
    """
    patterns = [
        r'KDV\s*\(Matrah\s*([0-9.,]+)\)',
        r'KDV\s*Matrah[ıi][:\s]*([0-9.,]+)',
        r'Matrah[:\s]*([0-9.,]+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = _parse_turkish_number(match.group(1))
            if value and value > 100:
                logger.info(f"KDV matrahı bulundu: {value}")
                return value
    
    return None


def _find_toplam_tuketim(text: str) -> Optional[float]:
    """
    Toplam tüketim (kWh) değerini bul.
    
    Örnek pattern'ler:
    - "Toplam Tüketim: 116.145,630 kWh"
    - "AKTİF TOPLAM 116.145,630"
    """
    patterns = [
        r'Toplam\s*Tüketim[:\s]*([0-9.,]+)\s*kWh',
        r'AKT[İI]F\s*TOPLAM[:\s]*([0-9.,]+)',
        r'Tüketim\s*\(kWh\)[:\s]*([0-9.,]+)',
        r'([0-9.,]+)\s*kWh\s*Toplam',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = _parse_turkish_number(match.group(1))
            if value and value > 10:  # Mantıklı bir tüketim mi?
                logger.info(f"Toplam tüketim bulundu: {value} kWh")
                return value
    
    return None


def create_extraction_hint(extracted: ExtractedText) -> str:
    """
    OpenAI prompt'una eklenecek hint metni oluştur.
    
    Bu hint, OpenAI'ın doğru değerleri okumasına yardımcı olur.
    """
    if not extracted.is_digital or extracted.extraction_quality == "poor":
        return ""
    
    hints = []
    
    if extracted.odenecek_tutar:
        hints.append(f"⚠️ PDF'den okunan Ödenecek Tutar: {extracted.odenecek_tutar:.2f} TL")
    
    if extracted.kdv_tutari:
        hints.append(f"⚠️ PDF'den okunan KDV: {extracted.kdv_tutari:.2f} TL")
    
    if extracted.kdv_matrahi:
        hints.append(f"⚠️ PDF'den okunan KDV Matrahı: {extracted.kdv_matrahi:.2f} TL")
    
    if extracted.toplam_tuketim_kwh:
        hints.append(f"⚠️ PDF'den okunan Toplam Tüketim: {extracted.toplam_tuketim_kwh:.3f} kWh")
    
    if hints:
        return "\n\n" + "\n".join(hints) + "\n\nBu değerleri doğrula ve JSON'a yaz!"
    
    return ""
