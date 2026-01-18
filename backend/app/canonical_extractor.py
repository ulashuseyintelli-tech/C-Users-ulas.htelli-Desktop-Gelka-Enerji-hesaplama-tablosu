"""
Kanonik Extractor - Tedarikçi profillerine göre fatura parse.

Bu modül:
1. PDF metnini bölgelere ayırır (section slicing)
2. Tedarikçi profiline göre doğru anchor'lardan okur
3. Tutarlılık doğrulaması yapar
4. Kanonik formatta çıktı üretir

LLM'e bağımlı değil, regex tabanlı endüstriyel parser.
"""

import re
import logging
from typing import Optional
from .supplier_profiles import (
    CanonicalInvoice,
    InvoiceLine,
    LineCode,
    TaxBreakdown,
    VATInfo,
    Totals,
    SupplierProfile,
    ALL_PROFILES,
    detect_supplier,
    get_profile_by_code,
    tr_money,
    tr_kwh,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Section Slicing - Metin Bölgeleme
# ═══════════════════════════════════════════════════════════════════════════════

def slice_block(text: str, start_keywords: list[str], end_keywords: list[str]) -> Optional[str]:
    """
    Metinden belirli bir bloğu çıkar.
    
    Args:
        text: Tam metin
        start_keywords: Blok başlangıç anahtar kelimeleri
        end_keywords: Blok bitiş anahtar kelimeleri
    
    Returns:
        Blok metni veya None
    """
    text_lower = text.lower()
    
    # Başlangıç noktasını bul
    start_pos = -1
    start_kw_found = ""
    for kw in start_keywords:
        pos = text_lower.find(kw.lower())
        if pos != -1 and (start_pos == -1 or pos < start_pos):
            start_pos = pos
            start_kw_found = kw
    
    if start_pos == -1:
        return None
    
    # Bitiş noktasını bul
    end_pos = len(text)
    for kw in end_keywords:
        pos = text_lower.find(kw.lower(), start_pos + len(start_kw_found))
        if pos != -1 and pos < end_pos:
            end_pos = pos
    
    return text[start_pos:end_pos]


def extract_all_numbers(text: str) -> list[tuple[str, float]]:
    """
    Metindeki tüm sayıları çıkar (debug için).
    """
    pattern = re.compile(r'[\d\.\,]+')
    results = []
    for match in pattern.finditer(text):
        value = tr_money(match.group())
        if value is not None:
            results.append((match.group(), value))
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Line Extraction
# ═══════════════════════════════════════════════════════════════════════════════

def classify_line_code(label: str) -> LineCode:
    """
    Etiket metninden kalem kodunu belirle.
    """
    label_lower = label.lower()
    
    # Enerji kademeleri
    if "yüksek" in label_lower and ("kademe" in label_lower or "enerji" in label_lower):
        return LineCode.ACTIVE_ENERGY_HIGH
    if "düşük" in label_lower and ("kademe" in label_lower or "enerji" in label_lower):
        return LineCode.ACTIVE_ENERGY_LOW
    
    # Çok zamanlı
    if "t1" in label_lower or "gündüz" in label_lower:
        return LineCode.ACTIVE_ENERGY_T1
    if "t2" in label_lower or "puant" in label_lower:
        return LineCode.ACTIVE_ENERGY_T2
    if "t3" in label_lower or "gece" in label_lower:
        return LineCode.ACTIVE_ENERGY_T3
    
    # Genel enerji
    if "enerji" in label_lower and "bedel" in label_lower:
        return LineCode.ACTIVE_ENERGY
    if "aktif" in label_lower:
        return LineCode.ACTIVE_ENERGY
    
    # Dağıtım
    if "dağıtım" in label_lower:
        return LineCode.DISTRIBUTION
    
    # YEK
    if "yek" in label_lower:
        if "fark" in label_lower:
            return LineCode.YEK_DIFF
        return LineCode.YEK
    
    # Reaktif
    if "reaktif" in label_lower:
        if "endüktif" in label_lower:
            return LineCode.REACTIVE_INDUCTIVE
        if "kapasitif" in label_lower:
            return LineCode.REACTIVE_CAPACITIVE
        return LineCode.REACTIVE
    
    # Demand
    if "demand" in label_lower or "güç bedeli" in label_lower:
        return LineCode.DEMAND
    
    # Vergiler
    if "btv" in label_lower or "belediye" in label_lower:
        return LineCode.TAX_BTV
    if "trt" in label_lower:
        return LineCode.TAX_TRT
    if "enerji fonu" in label_lower:
        return LineCode.TAX_ENERGY_FUND
    
    # Hizmet bedeli
    if "hizmet" in label_lower or "sayaç" in label_lower:
        return LineCode.SERVICE_FEE
    
    return LineCode.OTHER


def extract_lines_from_block(block: str, profile: SupplierProfile) -> list[InvoiceLine]:
    """
    Bloktan kalem satırlarını çıkar.
    """
    lines = []
    
    for pattern in profile.line_patterns:
        for match in pattern.finditer(block):
            groups = match.groupdict()
            
            label = groups.get("label", "").strip()
            qty = tr_kwh(groups.get("qty", ""))
            unit_price = tr_money(groups.get("unit_price", ""))
            amount = tr_money(groups.get("amount", ""))
            
            if label and (qty or amount):
                line = InvoiceLine(
                    code=classify_line_code(label),
                    label=label,
                    qty_kwh=qty,
                    unit_price=unit_price,
                    amount=amount,
                    evidence=match.group()[:100],
                )
                lines.append(line)
    
    return lines


# ═══════════════════════════════════════════════════════════════════════════════
# Totals Extraction
# ═══════════════════════════════════════════════════════════════════════════════

def extract_totals(text: str, profile: SupplierProfile) -> Totals:
    """
    Toplam tutarları çıkar.
    """
    totals = Totals()
    
    # Total
    if profile.total_pattern:
        match = profile.total_pattern.search(text)
        if match:
            totals.total = tr_money(match.group("v"))
    
    # Payable
    if profile.payable_pattern:
        match = profile.payable_pattern.search(text)
        if match:
            totals.payable = tr_money(match.group("v"))
    
    # Payable yoksa total'i kullan
    if totals.payable is None and totals.total is not None:
        totals.payable = totals.total
    
    return totals


def extract_vat(text: str, profile: SupplierProfile) -> VATInfo:
    """
    KDV bilgisini çıkar.
    """
    vat = VATInfo()
    
    if profile.vat_pattern:
        match = profile.vat_pattern.search(text)
        if match:
            vat.amount = tr_money(match.group("v"))
    
    # Matrah pattern'ı ara
    matrah_pattern = re.compile(r"Matrah\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE)
    match = matrah_pattern.search(text)
    if match:
        vat.base = tr_money(match.group("v"))
    
    return vat


def extract_taxes(text: str) -> TaxBreakdown:
    """
    Vergi/fon bilgilerini çıkar.
    """
    taxes = TaxBreakdown()
    
    # BTV
    btv_pattern = re.compile(r"(?:BTV|Belediye\s*Tüketim\s*Vergisi)\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE)
    match = btv_pattern.search(text)
    if match:
        taxes.btv = tr_money(match.group("v"))
    
    # TRT
    trt_pattern = re.compile(r"TRT\s*(?:Payı)?\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE)
    match = trt_pattern.search(text)
    if match:
        taxes.trt = tr_money(match.group("v"))
    
    # Enerji Fonu
    fund_pattern = re.compile(r"Enerji\s*Fonu\s*[:\s]*(?P<v>[\d\.\,]+)", re.IGNORECASE)
    match = fund_pattern.search(text)
    if match:
        taxes.energy_fund = tr_money(match.group("v"))
    
    return taxes


# ═══════════════════════════════════════════════════════════════════════════════
# Invoice Metadata Extraction
# ═══════════════════════════════════════════════════════════════════════════════

def extract_invoice_no(text: str) -> str:
    """Fatura numarasını çıkar"""
    patterns = [
        re.compile(r"Fatura\s*No\s*[:\s]*(?P<v>[A-Z0-9]+)", re.IGNORECASE),
        re.compile(r"(?:BBE|ES0|PBA|EAL|KSE)\d{10,}", re.IGNORECASE),
    ]
    
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            if "v" in match.groupdict():
                return match.group("v")
            return match.group()
    
    return ""


def extract_ettn(text: str) -> str:
    """ETTN çıkar"""
    pattern = re.compile(r"ETTN\s*[:\s]*(?P<v>[A-F0-9\-]{36})", re.IGNORECASE)
    match = pattern.search(text)
    if match:
        return match.group("v")
    return ""


def extract_period(text: str) -> str:
    """Fatura dönemini çıkar (YYYY-MM)"""
    patterns = [
        re.compile(r"(?:Dönem|Fatura\s*Dönemi)\s*[:\s]*(?P<m>\d{2})[/\-\.](?P<y>\d{4})", re.IGNORECASE),
        re.compile(r"(?:Dönem|Fatura\s*Dönemi)\s*[:\s]*(?P<y>\d{4})[/\-\.](?P<m>\d{2})", re.IGNORECASE),
    ]
    
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            groups = match.groupdict()
            year = groups.get("y", "")
            month = groups.get("m", "")
            if year and month:
                return f"{year}-{month}"
    
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# Ana Extraction Fonksiyonu
# ═══════════════════════════════════════════════════════════════════════════════

def extract_canonical(text: str, supplier_code: Optional[str] = None) -> CanonicalInvoice:
    """
    PDF metninden kanonik fatura çıkar.
    
    Args:
        text: PDF'den çıkarılmış metin
        supplier_code: Tedarikçi kodu (opsiyonel, otomatik tespit edilir)
    
    Returns:
        CanonicalInvoice
    """
    invoice = CanonicalInvoice()
    
    # Tedarikçi tespiti
    invoice_no = extract_invoice_no(text)
    profile = None
    
    if supplier_code:
        profile = get_profile_by_code(supplier_code)
    
    if not profile:
        profile = detect_supplier(text, invoice_no)
    
    if not profile:
        invoice.warnings.append("SUPPLIER_NOT_DETECTED: Tedarikçi tespit edilemedi, genel parser kullanılacak")
        # Varsayılan profil
        profile = SupplierProfile(
            code="unknown",
            name="Unknown",
            invoice_prefixes=[],
            detail_block_start=["Fatura Detayı", "FATURA DETAYI", "Fatura Bilgileri"],
            detail_block_end=["KDV", "TOPLAM", "Vergi"],
        )
    
    invoice.supplier = profile.code
    invoice.invoice_no = invoice_no
    invoice.ettn = extract_ettn(text)
    invoice.period = extract_period(text)
    
    # Fatura detay bloğunu bul
    detail_block = slice_block(text, profile.detail_block_start, profile.detail_block_end)
    
    if detail_block:
        invoice.source_anchor = profile.detail_block_start[0] if profile.detail_block_start else "unknown"
        invoice.raw_text_snippet = detail_block[:500]
        
        # Kalem satırlarını çıkar
        invoice.lines = extract_lines_from_block(detail_block, profile)
    else:
        invoice.warnings.append("DETAIL_BLOCK_NOT_FOUND: Fatura detay bloğu bulunamadı")
        # Tüm metinden dene
        invoice.lines = extract_lines_from_block(text, profile)
    
    # Toplamları çıkar
    invoice.totals = extract_totals(text, profile)
    invoice.vat = extract_vat(text, profile)
    invoice.taxes = extract_taxes(text)
    
    # Doğrulama
    invoice.validate()
    
    # Debug log
    logger.info(f"Canonical extraction: {invoice.to_debug_dict()}")
    
    return invoice


def extract_and_validate(text: str, supplier_code: Optional[str] = None) -> tuple[CanonicalInvoice, bool]:
    """
    Extract ve validate tek seferde.
    
    Returns:
        (invoice, is_valid)
    """
    invoice = extract_canonical(text, supplier_code)
    is_valid = invoice.is_valid()
    
    if not is_valid:
        logger.warning(f"Invoice validation failed: {invoice.errors}")
    
    return invoice, is_valid


# ═══════════════════════════════════════════════════════════════════════════════
# InvoiceExtraction'a Dönüştürme
# ═══════════════════════════════════════════════════════════════════════════════

def canonical_to_extraction(canonical: CanonicalInvoice) -> dict:
    """
    CanonicalInvoice'ı InvoiceExtraction formatına dönüştür.
    
    Bu fonksiyon mevcut sistemle uyumluluk sağlar.
    """
    # Confidence hesapla
    base_confidence = 0.85 if canonical.is_valid() else 0.5
    
    return {
        "vendor": canonical.supplier,
        "invoice_period": canonical.period,
        "consumption_kwh": {
            "value": canonical.total_kwh if canonical.total_kwh > 0 else None,
            "confidence": base_confidence,
            "evidence": f"Toplam: {canonical.total_kwh:.2f} kWh from {len(canonical.lines)} lines",
            "page": 1,
        },
        "current_active_unit_price_tl_per_kwh": {
            "value": canonical.weighted_unit_price,
            "confidence": base_confidence * 0.9,
            "evidence": f"Weighted avg: {canonical.weighted_unit_price:.4f} TL/kWh" if canonical.weighted_unit_price else "",
            "page": 1,
        },
        "distribution_unit_price_tl_per_kwh": {
            "value": canonical.distribution_unit_price,
            "confidence": base_confidence * 0.85,
            "evidence": f"Distribution: {canonical.distribution_unit_price:.4f} TL/kWh" if canonical.distribution_unit_price else "",
            "page": 1,
        },
        "invoice_total_with_vat_tl": {
            "value": canonical.totals.payable,
            "confidence": base_confidence,
            "evidence": f"Payable: {canonical.totals.payable:.2f} TL" if canonical.totals.payable else "",
            "page": 1,
        },
        "raw_breakdown": {
            "energy_total_tl": {
                "value": canonical.energy_amount if canonical.energy_amount > 0 else None,
                "confidence": base_confidence,
                "evidence": "",
                "page": 1,
            },
            "distribution_total_tl": {
                "value": canonical.distribution_amount if canonical.distribution_amount > 0 else None,
                "confidence": base_confidence,
                "evidence": "",
                "page": 1,
            },
            "btv_tl": {
                "value": canonical.taxes.btv,
                "confidence": base_confidence * 0.8,
                "evidence": "",
                "page": 1,
            },
            "vat_tl": {
                "value": canonical.vat.amount,
                "confidence": base_confidence,
                "evidence": "",
                "page": 1,
            },
        },
        "line_items": [
            {
                "label": line.label,
                "qty": line.qty_kwh,
                "unit": "kWh",
                "unit_price": line.unit_price,
                "amount_tl": line.amount,
                "confidence": base_confidence if line.crosscheck() else 0.5,
                "evidence": line.evidence,
                "page": 1,
            }
            for line in canonical.lines
            if line.is_valid()
        ],
        "meta": {
            "tariff_group_guess": "unknown",
            "voltage_guess": "unknown",
            "term_type_guess": "unknown",
            "invoice_type_guess": "unknown",
        },
        "_canonical_debug": canonical.to_debug_dict(),
        "_canonical_errors": canonical.errors,
        "_canonical_warnings": canonical.warnings,
    }
