"""
OCR Extraction Module - Tesseract Entegrasyonu
"""

import io
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List
from PIL import Image

logger = logging.getLogger(__name__)

TESSERACT_AVAILABLE = False
try:
    import pytesseract
    pytesseract.get_tesseract_version()
    TESSERACT_AVAILABLE = True
except Exception as e:
    logger.warning(f"Tesseract not available: {e}")


@dataclass
class OCRResult:
    raw_text: str = ""
    payable_total: Optional[float] = None
    vat_amount: Optional[float] = None
    vat_base: Optional[float] = None
    energy_total: Optional[float] = None
    distribution_total: Optional[float] = None
    consumption_kwh: Optional[float] = None
    confidence: float = 0.0
    source_region: str = ""
    extraction_quality: str = "unknown"
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "payable_total": self.payable_total,
            "vat_amount": self.vat_amount,
            "vat_base": self.vat_base,
            "energy_total": self.energy_total,
            "distribution_total": self.distribution_total,
            "consumption_kwh": self.consumption_kwh,
            "confidence": self.confidence,
            "source_region": self.source_region,
            "extraction_quality": self.extraction_quality,
            "evidence": self.evidence,
        }

    def field_count(self) -> int:
        count = 0
        if self.payable_total: count += 1
        if self.vat_amount: count += 1
        if self.energy_total: count += 1
        if self.distribution_total: count += 1
        if self.consumption_kwh: count += 1
        return count


def parse_tr_float(text: str) -> Optional[float]:
    if not text:
        return None
    text = text.strip()
    text = re.sub(r'[TL\u20ba\s]', '', text)
    if not text:
        return None
    text = text.replace(".", "")
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def preprocess_image(img: Image.Image) -> Image.Image:
    if img.mode != 'L':
        img = img.convert('L')
    try:
        from PIL import ImageEnhance
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)
    except Exception:
        pass
    return img


def extract_text_from_image(image_bytes: bytes, lang: str = "tur+eng") -> str:
    if not TESSERACT_AVAILABLE:
        return ""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = preprocess_image(img)
        config = '--oem 3 --psm 6'
        text = pytesseract.image_to_string(img, lang=lang, config=config)
        return text
    except Exception as e:
        logger.error(f"OCR extraction failed: {e}")
        return ""


def extract_with_ocr(image_bytes: bytes, region_name: str = "") -> OCRResult:
    result = OCRResult(source_region=region_name)
    raw_text = extract_text_from_image(image_bytes)
    result.raw_text = raw_text
    if not raw_text or len(raw_text.strip()) < 10:
        result.extraction_quality = "poor"
        return result
    result = parse_invoice_values(raw_text, result)
    field_count = result.field_count()
    if field_count >= 3:
        result.extraction_quality = "good"
        result.confidence = 0.9
    elif field_count >= 1:
        result.extraction_quality = "medium"
        result.confidence = 0.7
    else:
        result.extraction_quality = "poor"
        result.confidence = 0.3
    return result


def parse_invoice_values(text: str, result: OCRResult = None) -> OCRResult:
    if result is None:
        result = OCRResult(raw_text=text)

    payable_patterns = [
        (r'[Oo]denecek\s*[Tt]utar[:\s]*([0-9.,]+)', "odenecek_tutar"),
        (r'ODENECEK\s*TUTAR[:\s]*([0-9.,]+)', "ODENECEK_TUTAR"),
        (r'[Gg]enel\s*[Tt]oplam[:\s]*([0-9.,]+)', "genel_toplam"),
        (r'GENEL\s*TOPLAM[:\s]*([0-9.,]+)', "GENEL_TOPLAM"),
        (r'Toplam\s*Tutar[:\s]*([0-9.,]+)', "toplam_tutar"),
        (r'TOPLAM\s*TUTAR[:\s]*([0-9.,]+)', "TOPLAM_TUTAR"),
    ]
    for pattern, name in payable_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = parse_tr_float(match.group(1))
            if value and value > 100:
                result.payable_total = value
                result.evidence["payable_total"] = {"pattern": name, "raw": match.group(1)}
                break

    vat_patterns = [
        (r'KDV\s*\([^)]*\)\s*([0-9.,]+)', "kdv_parantez"),
        (r'KDV\s*Tutar[:\s]*([0-9.,]+)', "kdv_tutari"),
        (r'KDV[:\s]+([0-9.,]+)', "kdv_basit"),
    ]
    for pattern, name in vat_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = parse_tr_float(match.group(1))
            if value and value > 10:
                result.vat_amount = value
                result.evidence["vat_amount"] = {"pattern": name, "raw": match.group(1)}
                break

    vat_base_patterns = [
        (r'KDV\s*\(Matrah\s*([0-9.,]+)\)', "kdv_matrah_parantez"),
        (r'KDV\s*Matrah[:\s]*([0-9.,]+)', "kdv_matrahi"),
        (r'Matrah[:\s]*([0-9.,]+)', "matrah_basit"),
    ]
    for pattern, name in vat_base_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = parse_tr_float(match.group(1))
            if value and value > 100:
                result.vat_base = value
                result.evidence["vat_base"] = {"pattern": name, "raw": match.group(1)}
                break

    consumption_patterns = [
        (r'Toplam\s*Tuketim[:\s]*([0-9.,]+)\s*kWh', "toplam_tuketim"),
        (r'AKTIF\s*TOPLAM[:\s]*([0-9.,]+)', "aktif_toplam"),
        (r'Tuketim\s*\(kWh\)[:\s]*([0-9.,]+)', "tuketim_kwh"),
        (r'([0-9.,]+)\s*kWh', "kwh_basit"),
    ]
    for pattern, name in consumption_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = parse_tr_float(match.group(1))
            if value and value > 10:
                result.consumption_kwh = value
                result.evidence["consumption_kwh"] = {"pattern": name, "raw": match.group(1)}
                break

    energy_patterns = [
        (r'Enerji\s*Bedeli[:\s]*([0-9.,]+)', "enerji_bedeli"),
        (r'Aktif\s*Enerji[:\s]*([0-9.,]+)', "aktif_enerji"),
    ]
    for pattern, name in energy_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = parse_tr_float(match.group(1))
            if value and value > 10:
                result.energy_total = value
                result.evidence["energy_total"] = {"pattern": name, "raw": match.group(1)}
                break

    distribution_patterns = [
        (r'Dagitim\s*Bedeli[:\s]*([0-9.,]+)', "dagitim_bedeli"),
        (r'Iletim\s*Bedeli[:\s]*([0-9.,]+)', "iletim_bedeli"),
    ]
    for pattern, name in distribution_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            value = parse_tr_float(match.group(1))
            if value and value > 10:
                result.distribution_total = value
                result.evidence["distribution_total"] = {"pattern": name, "raw": match.group(1)}
                break

    return result


def merge_ocr_results(results: List[OCRResult]) -> OCRResult:
    if not results:
        return OCRResult()
    if len(results) == 1:
        return results[0]
    merged = OCRResult()
    merged.raw_text = "\n---\n".join(r.raw_text for r in results if r.raw_text)
    for fld in ["payable_total", "vat_amount", "vat_base", "energy_total",
                "distribution_total", "consumption_kwh"]:
        best_value = None
        best_confidence = 0
        best_evidence = None
        for r in results:
            value = getattr(r, fld, None)
            if value and r.confidence > best_confidence:
                best_value = value
                best_confidence = r.confidence
                best_evidence = r.evidence.get(fld)
        if best_value:
            setattr(merged, fld, best_value)
            if best_evidence:
                merged.evidence[fld] = best_evidence
    merged.confidence = max(r.confidence for r in results)
    fc = merged.field_count()
    if fc >= 3:
        merged.extraction_quality = "good"
    elif fc >= 1:
        merged.extraction_quality = "medium"
    else:
        merged.extraction_quality = "poor"
    return merged


def create_ocr_hint(ocr_result: OCRResult) -> str:
    if ocr_result.extraction_quality == "poor":
        return ""
    hints = []
    if ocr_result.payable_total:
        hints.append(f"OCR Odenecek Tutar: {ocr_result.payable_total:.2f} TL")
    if ocr_result.vat_amount:
        hints.append(f"OCR KDV: {ocr_result.vat_amount:.2f} TL")
    if ocr_result.vat_base:
        hints.append(f"OCR KDV Matrahi: {ocr_result.vat_base:.2f} TL")
    if ocr_result.consumption_kwh:
        hints.append(f"OCR Tuketim: {ocr_result.consumption_kwh:.3f} kWh")
    if ocr_result.energy_total:
        hints.append(f"OCR Enerji Bedeli: {ocr_result.energy_total:.2f} TL")
    if ocr_result.distribution_total:
        hints.append(f"OCR Dagitim Bedeli: {ocr_result.distribution_total:.2f} TL")
    if hints:
        return "\n\n[OCR CROSS-CHECK]\n" + "\n".join(hints) + "\nBu degerleri dogrula!"
    return ""
