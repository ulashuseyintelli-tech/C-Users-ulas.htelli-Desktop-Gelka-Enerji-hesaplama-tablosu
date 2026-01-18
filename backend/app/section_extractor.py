"""
Section-Based Extractor - Anchor tespiti + bölge OCR.

Mimari:
1. Anchor tespiti (metin tabanlı, koordinat değil)
2. 3 section: ozet, fatura_detayi, vergiler
3. Her section için ayrı prompt
4. Sonuçları birleştir + validate

Bu yaklaşım vendor-agnostic çalışır.
"""

import re
import json
import logging
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Section Definitions
# ═══════════════════════════════════════════════════════════════════════════════

class SectionType(str, Enum):
    OZET = "ozet"
    FATURA_DETAYI = "fatura_detayi"
    VERGILER = "vergiler"


@dataclass
class SectionAnchor:
    """Bölge anchor tanımı"""
    section: SectionType
    start_keywords: list[str]
    end_keywords: list[str]
    required: bool = True


# Anchor tanımları - tüm vendor'lar için ortak
SECTION_ANCHORS = [
    SectionAnchor(
        section=SectionType.OZET,
        start_keywords=[
            "FATURA BİLGİLERİ", "Fatura Bilgileri", "FATURA NO",
            "Fatura No", "ETTN", "e-Fatura", "Düzenleme Tarihi"
        ],
        end_keywords=[
            "FATURA DETAYI", "Fatura Detayı", "AÇIKLAMA", "TÜKETİM",
            "Enerji Bedeli", "Aktif Enerji"
        ],
        required=False,
    ),
    SectionAnchor(
        section=SectionType.FATURA_DETAYI,
        start_keywords=[
            "FATURA DETAYI", "Fatura Detayı", "AÇIKLAMA", "Açıklama",
            "Enerji Bedeli", "Aktif Enerji", "TÜKETİM BİLGİLERİ",
            "Tüketim Bilgileri", "KALEM", "Miktar", "Birim Fiyat"
        ],
        end_keywords=[
            "VERGİ", "Vergi", "KDV", "TOPLAM", "Toplam", "BTV",
            "Belediye Tüketim", "Matrah"
        ],
        required=True,  # Bu bölge ZORUNLU
    ),
    SectionAnchor(
        section=SectionType.VERGILER,
        start_keywords=[
            "VERGİ VE FONLAR", "Vergi ve Fonlar", "VERGİLER",
            "BTV", "Belediye Tüketim", "KDV", "Matrah"
        ],
        end_keywords=[
            "ÖDENECEK", "Ödenecek", "FATURA TUTARI", "Fatura Tutarı",
            "GENEL TOPLAM", "Genel Toplam"
        ],
        required=True,
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Section Prompts - Vendor Agnostic
# ═══════════════════════════════════════════════════════════════════════════════

SECTION_PROMPTS = {
    SectionType.OZET: """
Bu görsel bir Türkiye elektrik faturasının ÖZET bölümüdür.
Aşağıdaki bilgileri JSON olarak çıkar:

{
  "invoice_no": "Fatura numarası (BBE2025..., ES02025..., vb.)",
  "ettn": "ETTN (UUID formatı, varsa)",
  "invoice_date": "Fatura tarihi (YYYY-MM-DD)",
  "due_date": "Son ödeme tarihi (YYYY-MM-DD)",
  "period": "Fatura dönemi (YYYY-MM)",
  "payable_tl": "Ödenecek tutar (sayı, TL)",
  "vendor": "Tedarikçi (enerjisa, ck_bogazici, ekvator, yelden, uludag, unknown)"
}

TÜRKÇE SAYI FORMATI: 1.234,56 → 1234.56 (noktaları kaldır, virgülü noktaya çevir)
Bulamadığın alanları null yap.
SADECE JSON döndür.
""",

    SectionType.FATURA_DETAYI: """
Bu görsel bir Türkiye elektrik faturasının FATURA DETAYI / KALEM tablosudur.
Her satırı ayrı ayrı çıkar.

⚠️ KRİTİK KURALLAR:
1. SADECE parasal satırları al (TL tutarı olan)
2. "Ort. Tüketim", "kWh/gün" gibi bilgi satırlarını ALMA
3. NEGATİF değerler olabilir (mahsuplaşma) - onları da al
4. Her satır için: qty × unit_price ≈ amount_tl olmalı

{
  "lines": [
    {
      "label": "Satır etiketi (Enerji Bedeli, Dağıtım Bedeli, vb.)",
      "qty_kwh": "Miktar (kWh, sayı)",
      "unit_price": "Birim fiyat (TL/kWh, sayı)",
      "amount_tl": "Tutar (TL, sayı - negatif olabilir)"
    }
  ],
  "total_kwh": "Toplam tüketim (kWh) - satırların toplamı",
  "energy_total_tl": "Enerji bedeli toplamı (TL)",
  "distribution_total_tl": "Dağıtım bedeli toplamı (TL)"
}

TÜRKÇE SAYI FORMATI: 1.234,56 → 1234.56
SADECE JSON döndür.
""",

    SectionType.VERGILER: """
Bu görsel bir Türkiye elektrik faturasının VERGİ VE FONLAR bölümüdür.

{
  "btv_tl": "Belediye Tüketim Vergisi (TL)",
  "trt_tl": "TRT Payı (TL)",
  "energy_fund_tl": "Enerji Fonu (TL)",
  "other_taxes_tl": "Diğer vergiler toplamı (TL)",
  "vat_base_tl": "KDV Matrahı (TL)",
  "vat_amount_tl": "KDV Tutarı (TL)",
  "vat_rate": "KDV Oranı (0.20, 0.10, vb.)",
  "total_tl": "Fatura Tutarı / Genel Toplam (TL)"
}

TÜRKÇE SAYI FORMATI: 1.234,56 → 1234.56
Bulamadığın alanları null yap.
SADECE JSON döndür.
"""
}


# ═══════════════════════════════════════════════════════════════════════════════
# Extraction Result
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class LineItem:
    """Kalem satırı"""
    label: str
    qty_kwh: Optional[float] = None
    unit_price: Optional[float] = None
    amount_tl: Optional[float] = None
    crosscheck_ok: bool = True
    crosscheck_delta: Optional[float] = None
    
    def validate(self) -> bool:
        """qty × unit_price ≈ amount_tl kontrolü"""
        if self.qty_kwh and self.unit_price and self.amount_tl:
            calculated = self.qty_kwh * self.unit_price
            if abs(self.amount_tl) > 0:
                delta = abs((calculated - self.amount_tl) / abs(self.amount_tl)) * 100
                self.crosscheck_delta = delta
                self.crosscheck_ok = delta <= 2.0  # %2 tolerans
                return self.crosscheck_ok
        return True


@dataclass
class SectionResult:
    """Tek section extraction sonucu"""
    section: SectionType
    success: bool = False
    data: dict = field(default_factory=dict)
    error: Optional[str] = None
    raw_response: Optional[str] = None


@dataclass 
class ExtractionResult:
    """Tüm extraction sonucu"""
    # Özet
    invoice_no: Optional[str] = None
    ettn: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    period: Optional[str] = None
    vendor: str = "unknown"
    
    # Fatura Detayı
    lines: list[LineItem] = field(default_factory=list)
    total_kwh: Optional[float] = None
    energy_total_tl: Optional[float] = None
    distribution_total_tl: Optional[float] = None
    
    # Vergiler
    btv_tl: Optional[float] = None
    trt_tl: Optional[float] = None
    energy_fund_tl: Optional[float] = None
    vat_base_tl: Optional[float] = None
    vat_amount_tl: Optional[float] = None
    vat_rate: Optional[float] = None
    total_tl: Optional[float] = None
    payable_tl: Optional[float] = None
    
    # Türetilmiş değerler
    active_unit_price: Optional[float] = None
    distribution_unit_price: Optional[float] = None
    
    # Validation
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_valid: bool = False
    
    # Debug
    section_results: dict[str, SectionResult] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# Validation Rules
# ═══════════════════════════════════════════════════════════════════════════════

def validate_extraction(result: ExtractionResult) -> ExtractionResult:
    """
    3 global kural ile doğrulama:
    
    Kural-1: total_kwh sadece kalemlerden
    Kural-2: subtotal + vat ≈ total
    Kural-3: Birim fiyat yoksa uyarı
    """
    result.errors = []
    result.warnings = []
    
    # Kural-1: total_kwh kontrolü
    if result.lines:
        # Sadece kWh birimli ve amount_tl > 0 olan satırlar
        # NEGATİF kalemler dahil (mahsuplaşma)
        calculated_kwh = sum(
            line.qty_kwh or 0 
            for line in result.lines 
            if line.qty_kwh is not None
        )
        
        if result.total_kwh:
            delta = abs(calculated_kwh - result.total_kwh)
            if delta > 1:  # 1 kWh tolerans
                result.warnings.append(
                    f"KWH_MISMATCH: lines_sum={calculated_kwh:.2f}, reported={result.total_kwh:.2f}"
                )
        else:
            result.total_kwh = calculated_kwh
    
    if not result.total_kwh or result.total_kwh <= 0:
        result.errors.append("TOTAL_KWH_MISSING: Toplam tüketim bulunamadı veya 0")
    
    # Kural-2: subtotal + vat ≈ total
    if result.energy_total_tl and result.distribution_total_tl and result.vat_amount_tl:
        subtotal = (result.energy_total_tl or 0) + (result.distribution_total_tl or 0)
        taxes = (result.btv_tl or 0) + (result.trt_tl or 0) + (result.energy_fund_tl or 0)
        calculated_total = subtotal + taxes + (result.vat_amount_tl or 0)
        
        reported_total = result.total_tl or result.payable_tl
        
        if reported_total:
            delta = abs(calculated_total - reported_total)
            tolerance = reported_total * 0.05  # %5 tolerans
            
            if delta > tolerance:
                result.warnings.append(
                    f"TOTAL_MISMATCH: calculated={calculated_total:.2f}, reported={reported_total:.2f}, delta={delta:.2f}"
                )
    
    if not result.total_tl and not result.payable_tl:
        result.errors.append("TOTAL_MISSING: Fatura tutarı bulunamadı")
    
    # Kural-3: Birim fiyat kontrolü
    if result.total_kwh and result.total_kwh > 0:
        # Aktif enerji birim fiyatı
        if result.energy_total_tl and not result.active_unit_price:
            result.active_unit_price = result.energy_total_tl / result.total_kwh
            result.warnings.append(
                f"UNIT_PRICE_DERIVED: active={result.active_unit_price:.4f} TL/kWh (energy_total/kwh)"
            )
        
        # Dağıtım birim fiyatı
        if result.distribution_total_tl and not result.distribution_unit_price:
            result.distribution_unit_price = result.distribution_total_tl / result.total_kwh
            result.warnings.append(
                f"UNIT_PRICE_DERIVED: distribution={result.distribution_unit_price:.4f} TL/kWh"
            )
    
    # Line item cross-check
    failed_lines = [l for l in result.lines if not l.crosscheck_ok]
    if failed_lines:
        for line in failed_lines:
            result.warnings.append(
                f"LINE_CROSSCHECK_FAILED: {line.label}, delta={line.crosscheck_delta:.1f}%"
            )
    
    # Final validity
    result.is_valid = len(result.errors) == 0
    
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Text-Based Anchor Detection
# ═══════════════════════════════════════════════════════════════════════════════

def find_section_in_text(text: str, anchor: SectionAnchor) -> Optional[str]:
    """
    Metinden section'ı bul ve çıkar.
    
    Args:
        text: Tam metin
        anchor: Section anchor tanımı
    
    Returns:
        Section metni veya None
    """
    text_lower = text.lower()
    
    # Başlangıç noktasını bul
    start_pos = -1
    for kw in anchor.start_keywords:
        pos = text_lower.find(kw.lower())
        if pos != -1:
            if start_pos == -1 or pos < start_pos:
                start_pos = pos
    
    if start_pos == -1:
        return None
    
    # Bitiş noktasını bul
    end_pos = len(text)
    for kw in anchor.end_keywords:
        pos = text_lower.find(kw.lower(), start_pos + 10)  # Başlangıçtan biraz sonra ara
        if pos != -1 and pos < end_pos:
            end_pos = pos
    
    section_text = text[start_pos:end_pos]
    
    # Minimum uzunluk kontrolü
    if len(section_text) < 20:
        return None
    
    return section_text


def detect_sections_from_text(text: str) -> dict[SectionType, str]:
    """
    Metinden tüm section'ları tespit et.
    """
    sections = {}
    
    for anchor in SECTION_ANCHORS:
        section_text = find_section_in_text(text, anchor)
        if section_text:
            sections[anchor.section] = section_text
            logger.debug(f"Found section {anchor.section.value}: {len(section_text)} chars")
        elif anchor.required:
            logger.warning(f"Required section not found: {anchor.section.value}")
    
    return sections


# ═══════════════════════════════════════════════════════════════════════════════
# Number Parsing (TR format)
# ═══════════════════════════════════════════════════════════════════════════════

def parse_tr_number(value: any) -> Optional[float]:
    """
    Türkçe sayı formatını parse et.
    
    1.234,56 → 1234.56
    1234.56 → 1234.56
    """
    if value is None:
        return None
    
    if isinstance(value, (int, float)):
        return float(value)
    
    s = str(value).strip()
    if not s:
        return None
    
    # Negatif işareti
    negative = s.startswith("-")
    if negative:
        s = s[1:].strip()
    
    # TR formatı: 1.234,56
    if "," in s and "." in s:
        # Noktaları kaldır (binlik), virgülü noktaya çevir
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        # Sadece virgül var - ondalık ayracı
        s = s.replace(",", ".")
    
    try:
        result = float(s)
        return -result if negative else result
    except ValueError:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# Section Parsing
# ═══════════════════════════════════════════════════════════════════════════════

def parse_ozet_section(data: dict) -> dict:
    """Özet section'ı parse et"""
    return {
        "invoice_no": data.get("invoice_no"),
        "ettn": data.get("ettn"),
        "invoice_date": data.get("invoice_date"),
        "due_date": data.get("due_date"),
        "period": data.get("period"),
        "payable_tl": parse_tr_number(data.get("payable_tl")),
        "vendor": data.get("vendor", "unknown"),
    }


def parse_fatura_detayi_section(data: dict) -> dict:
    """Fatura detayı section'ı parse et"""
    lines = []
    
    for row in data.get("lines", []):
        line = LineItem(
            label=row.get("label", ""),
            qty_kwh=parse_tr_number(row.get("qty_kwh")),
            unit_price=parse_tr_number(row.get("unit_price")),
            amount_tl=parse_tr_number(row.get("amount_tl")),
        )
        line.validate()
        
        # Sadece geçerli satırları al
        if line.label and (line.qty_kwh or line.amount_tl):
            lines.append(line)
    
    return {
        "lines": lines,
        "total_kwh": parse_tr_number(data.get("total_kwh")),
        "energy_total_tl": parse_tr_number(data.get("energy_total_tl")),
        "distribution_total_tl": parse_tr_number(data.get("distribution_total_tl")),
    }


def parse_vergiler_section(data: dict) -> dict:
    """Vergiler section'ı parse et"""
    return {
        "btv_tl": parse_tr_number(data.get("btv_tl")),
        "trt_tl": parse_tr_number(data.get("trt_tl")),
        "energy_fund_tl": parse_tr_number(data.get("energy_fund_tl")),
        "other_taxes_tl": parse_tr_number(data.get("other_taxes_tl")),
        "vat_base_tl": parse_tr_number(data.get("vat_base_tl")),
        "vat_amount_tl": parse_tr_number(data.get("vat_amount_tl")),
        "vat_rate": parse_tr_number(data.get("vat_rate")),
        "total_tl": parse_tr_number(data.get("total_tl")),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# OpenAI Vision Integration
# ═══════════════════════════════════════════════════════════════════════════════

def extract_section_with_vision(
    image_bytes: bytes,
    section: SectionType,
    api_key: Optional[str] = None,
    model: str = "gpt-4o"
) -> SectionResult:
    """
    Görüntüden tek section çıkar.
    """
    import base64
    
    try:
        from openai import OpenAI
    except ImportError:
        return SectionResult(section=section, success=False, error="openai not installed")
    
    if api_key is None:
        from .core.config import settings
        api_key = settings.openai_api_key
    
    if not api_key:
        return SectionResult(section=section, success=False, error="API key not configured")
    
    prompt = SECTION_PROMPTS.get(section, "")
    if not prompt:
        return SectionResult(section=section, success=False, error=f"No prompt for section: {section}")
    
    client = OpenAI(api_key=api_key)
    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1500,
            temperature=0.1,
        )
        
        content = response.choices[0].message.content
        
        # JSON parse
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        data = json.loads(content.strip())
        
        return SectionResult(
            section=section,
            success=True,
            data=data,
            raw_response=content
        )
        
    except json.JSONDecodeError as e:
        return SectionResult(
            section=section,
            success=False,
            error=f"JSON parse error: {e}",
            raw_response=content if 'content' in locals() else None
        )
    except Exception as e:
        return SectionResult(
            section=section,
            success=False,
            error=str(e)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Main Extraction Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def extract_all_sections(
    image_bytes: bytes,
    api_key: Optional[str] = None,
    model: str = "gpt-4o"
) -> ExtractionResult:
    """
    Tüm section'ları çıkar ve birleştir.
    
    Pipeline:
    1. Her section için ayrı Vision call
    2. Sonuçları parse et
    3. Birleştir
    4. Validate et
    """
    result = ExtractionResult()
    
    # 1) Özet section
    ozet_result = extract_section_with_vision(image_bytes, SectionType.OZET, api_key, model)
    result.section_results[SectionType.OZET.value] = ozet_result
    
    if ozet_result.success:
        parsed = parse_ozet_section(ozet_result.data)
        result.invoice_no = parsed.get("invoice_no")
        result.ettn = parsed.get("ettn")
        result.invoice_date = parsed.get("invoice_date")
        result.due_date = parsed.get("due_date")
        result.period = parsed.get("period")
        result.payable_tl = parsed.get("payable_tl")
        result.vendor = parsed.get("vendor", "unknown")
    
    # 2) Fatura Detayı section (ZORUNLU)
    detay_result = extract_section_with_vision(image_bytes, SectionType.FATURA_DETAYI, api_key, model)
    result.section_results[SectionType.FATURA_DETAYI.value] = detay_result
    
    if detay_result.success:
        parsed = parse_fatura_detayi_section(detay_result.data)
        result.lines = parsed.get("lines", [])
        result.total_kwh = parsed.get("total_kwh")
        result.energy_total_tl = parsed.get("energy_total_tl")
        result.distribution_total_tl = parsed.get("distribution_total_tl")
    else:
        result.errors.append(f"FATURA_DETAYI_FAILED: {detay_result.error}")
    
    # 3) Vergiler section
    vergi_result = extract_section_with_vision(image_bytes, SectionType.VERGILER, api_key, model)
    result.section_results[SectionType.VERGILER.value] = vergi_result
    
    if vergi_result.success:
        parsed = parse_vergiler_section(vergi_result.data)
        result.btv_tl = parsed.get("btv_tl")
        result.trt_tl = parsed.get("trt_tl")
        result.energy_fund_tl = parsed.get("energy_fund_tl")
        result.vat_base_tl = parsed.get("vat_base_tl")
        result.vat_amount_tl = parsed.get("vat_amount_tl")
        result.vat_rate = parsed.get("vat_rate")
        result.total_tl = parsed.get("total_tl")
    
    # 4) Validate
    result = validate_extraction(result)
    
    logger.info(f"Section extraction complete: valid={result.is_valid}, errors={result.errors}, warnings={result.warnings}")
    
    return result


def extraction_result_to_dict(result: ExtractionResult) -> dict:
    """
    ExtractionResult'ı mevcut InvoiceExtraction formatına dönüştür.
    
    Bu fonksiyon mevcut API ile uyumluluk sağlar.
    """
    base_confidence = 0.85 if result.is_valid else 0.5
    
    return {
        "vendor": result.vendor,
        "invoice_period": result.period or "",
        "consumption_kwh": {
            "value": result.total_kwh,
            "confidence": base_confidence,
            "evidence": f"Toplam: {result.total_kwh:.2f} kWh" if result.total_kwh else "",
            "page": 1,
        },
        "current_active_unit_price_tl_per_kwh": {
            "value": result.active_unit_price,
            "confidence": base_confidence * 0.9,
            "evidence": f"Birim: {result.active_unit_price:.4f} TL/kWh" if result.active_unit_price else "",
            "page": 1,
        },
        "distribution_unit_price_tl_per_kwh": {
            "value": result.distribution_unit_price,
            "confidence": base_confidence * 0.85,
            "evidence": f"Dağıtım: {result.distribution_unit_price:.4f} TL/kWh" if result.distribution_unit_price else "",
            "page": 1,
        },
        "invoice_total_with_vat_tl": {
            "value": result.total_tl or result.payable_tl,
            "confidence": base_confidence,
            "evidence": f"Toplam: {result.total_tl or result.payable_tl:.2f} TL" if (result.total_tl or result.payable_tl) else "",
            "page": 1,
        },
        "raw_breakdown": {
            "energy_total_tl": {
                "value": result.energy_total_tl,
                "confidence": base_confidence,
                "evidence": "",
                "page": 1,
            },
            "distribution_total_tl": {
                "value": result.distribution_total_tl,
                "confidence": base_confidence,
                "evidence": "",
                "page": 1,
            },
            "btv_tl": {
                "value": result.btv_tl,
                "confidence": base_confidence * 0.8,
                "evidence": "",
                "page": 1,
            },
            "vat_tl": {
                "value": result.vat_amount_tl,
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
                "amount_tl": line.amount_tl,
                "confidence": base_confidence if line.crosscheck_ok else 0.5,
                "evidence": f"{line.label}: {line.qty_kwh} × {line.unit_price} = {line.amount_tl}",
                "page": 1,
            }
            for line in result.lines
        ],
        "meta": {
            "tariff_group_guess": "unknown",
            "voltage_guess": "unknown",
            "term_type_guess": "unknown",
            "invoice_type_guess": "unknown",
        },
        "_section_extraction": True,
        "_errors": result.errors,
        "_warnings": result.warnings,
        "_is_valid": result.is_valid,
    }
