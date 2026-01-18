"""
Fast Extractor - Ultra hızlı fatura analizi için optimize edilmiş extractor.

Özellikler:
- Basitleştirilmiş schema (sadece kritik alanlar)
- gpt-4o-mini model
- Çok küçük görsel boyutu (512px)
- Çok kısa prompt
- Low detail mode

Kullanım:
- Hızlı önizleme için
- Yüksek hacimli işlemler için
- ~1-2 saniye response time
"""

import base64
import json
import logging
import io
from typing import Optional
from PIL import Image

from .core.config import settings
from .models import InvoiceExtraction, FieldValue, RawBreakdown

logger = logging.getLogger(__name__)


# Ultra basit schema - sadece en kritik alanlar
ULTRA_FAST_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "ultra_fast_invoice",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "v": {"type": "string", "description": "vendor"},
                "kwh": {"type": ["number", "null"]},
                "up": {"type": ["number", "null"], "description": "unit_price"},
                "dp": {"type": ["number", "null"], "description": "dist_price"},
                "et": {"type": ["number", "null"], "description": "energy_total"},
                "dt": {"type": ["number", "null"], "description": "dist_total"},
                "vat": {"type": ["number", "null"]},
                "tot": {"type": ["number", "null"], "description": "total"},
            },
            "required": ["v", "kwh", "up", "dp", "et", "dt", "vat", "tot"],
            "additionalProperties": False,
        }
    }
}

# Ultra kısa prompt - ama daha net talimatlar
ULTRA_FAST_PROMPT = """TR elektrik faturası. FATURA DETAYI tablosundan oku. JSON döndür:
v=tedarikçi(enerjisa/ck_bogazici/uludag/unknown)
kwh=toplam tüketim kWh (binlerce olabilir, örn: 116145.63)
up=enerji birim fiyat TL/kWh (örn: 4.36)
dp=dağıtım birim fiyat TL/kWh (örn: 0.75)
et=enerji bedeli TL (yüz binlerce olabilir)
dt=dağıtım bedeli TL
vat=KDV TL
tot=FATURA TUTARI veya ÖDENECEK TUTAR satırından (yüz binlerce olabilir)

⚠️ TR sayı formatı: 1.234.567,89 = 1234567.89 (noktalar binlik ayracı!)
Örnek: 593.738,26 TL = 593738.26"""


def optimize_image_ultra(image_bytes: bytes, max_size: int = 800) -> bytes:
    """Görsel boyutunu optimize et - hız ve kalite dengesi"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        
        # Aspect ratio koruyarak resize
        ratio = min(max_size / img.width, max_size / img.height)
        if ratio < 1:
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # JPEG olarak kaydet - orta kalite
        buffer = io.BytesIO()
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=75, optimize=True)
        
        return buffer.getvalue()
    except Exception as e:
        logger.warning(f"Image optimization failed: {e}")
        return image_bytes


def ultra_fast_extract(image_bytes: bytes) -> InvoiceExtraction:
    """
    Ultra hızlı fatura extraction - ~2-3 saniye.
    
    Sadece kritik alanlar, minimum token, optimize görsel.
    """
    from openai import OpenAI
    
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY not configured")
    
    client = OpenAI(api_key=settings.openai_api_key)
    
    # Görsel optimize et (800px, %75 kalite)
    optimized = optimize_image_ultra(image_bytes, max_size=800)
    base64_image = base64.b64encode(optimized).decode("utf-8")
    
    logger.info(f"Ultra fast extraction: {len(image_bytes)} -> {len(optimized)} bytes")
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": ULTRA_FAST_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": "low"
                            }
                        }
                    ]
                }
            ],
            response_format=ULTRA_FAST_SCHEMA,
            max_tokens=300,
            temperature=0.0,
        )
        
        data = json.loads(response.choices[0].message.content)
        
        # InvoiceExtraction'a dönüştür
        return InvoiceExtraction(
            vendor=data.get("v", "unknown"),
            consumption_kwh=FieldValue(
                value=data.get("kwh"),
                confidence=0.75,
                evidence="ultra_fast",
                page=1
            ),
            current_active_unit_price_tl_per_kwh=FieldValue(
                value=data.get("up"),
                confidence=0.75,
                evidence="ultra_fast",
                page=1
            ),
            distribution_unit_price_tl_per_kwh=FieldValue(
                value=data.get("dp"),
                confidence=0.75,
                evidence="ultra_fast",
                page=1
            ),
            invoice_total_with_vat_tl=FieldValue(
                value=data.get("tot"),
                confidence=0.75,
                evidence="ultra_fast",
                page=1
            ),
            raw_breakdown=RawBreakdown(
                energy_total_tl=FieldValue(value=data.get("et"), confidence=0.75, evidence="", page=1),
                distribution_total_tl=FieldValue(value=data.get("dt"), confidence=0.75, evidence="", page=1),
                vat_tl=FieldValue(value=data.get("vat"), confidence=0.75, evidence="", page=1),
            )
        )
        
    except Exception as e:
        logger.error(f"Ultra fast extraction failed: {e}")
        raise


# Eski fonksiyonları koru (backward compatibility)
FAST_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "fast_invoice_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "vendor": {"type": "string"},
                "consumption_kwh": {"type": ["number", "null"]},
                "unit_price": {"type": ["number", "null"]},
                "distribution_price": {"type": ["number", "null"]},
                "energy_total": {"type": ["number", "null"]},
                "distribution_total": {"type": ["number", "null"]},
                "vat": {"type": ["number", "null"]},
                "total": {"type": ["number", "null"]},
            },
            "required": ["vendor", "consumption_kwh", "unit_price", "distribution_price", 
                        "energy_total", "distribution_total", "vat", "total"],
            "additionalProperties": False,
        }
    }
}

FAST_PROMPT = """Elektrik faturasından şu değerleri çıkar:
- vendor: Tedarikçi (enerjisa/ck_bogazici/uludag/unknown)
- consumption_kwh: Toplam tüketim (kWh) - FATURA DETAYI tablosundan
- unit_price: Enerji birim fiyatı (TL/kWh)
- distribution_price: Dağıtım birim fiyatı (TL/kWh)
- energy_total: Enerji bedeli toplamı (TL)
- distribution_total: Dağıtım bedeli toplamı (TL)
- vat: KDV tutarı (TL)
- total: Ödenecek tutar (TL)

TR sayı formatı: 1.234,56 → 1234.56 olarak yaz.
Bulamazsan null yaz."""


def optimize_image(image_bytes: bytes, max_size: int = 768) -> bytes:
    """Görsel boyutunu agresif şekilde küçült"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        
        # Aspect ratio koruyarak resize
        ratio = min(max_size / img.width, max_size / img.height)
        if ratio < 1:
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # JPEG olarak kaydet
        buffer = io.BytesIO()
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=75, optimize=True)
        
        return buffer.getvalue()
    except Exception as e:
        logger.warning(f"Image optimization failed: {e}")
        return image_bytes


def fast_extract(image_bytes: bytes) -> InvoiceExtraction:
    """
    Hızlı fatura extraction - sadece kritik alanlar.
    
    ~2-3 saniye (vs normal ~8-15 saniye)
    """
    from openai import OpenAI
    
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY not configured")
    
    client = OpenAI(api_key=settings.openai_api_key)
    
    # Görsel optimize et
    optimized = optimize_image(image_bytes, max_size=768)
    base64_image = base64.b64encode(optimized).decode("utf-8")
    
    logger.info(f"Fast extraction: image {len(image_bytes)} -> {len(optimized)} bytes")
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": FAST_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}",
                                "detail": "low"
                            }
                        }
                    ]
                }
            ],
            response_format=FAST_SCHEMA,
            max_tokens=500,
            temperature=0.1,
        )
        
        data = json.loads(response.choices[0].message.content)
        
        # InvoiceExtraction'a dönüştür
        return InvoiceExtraction(
            vendor=data.get("vendor", "unknown"),
            consumption_kwh=FieldValue(
                value=data.get("consumption_kwh"),
                confidence=0.8,
                evidence="fast_extraction",
                page=1
            ),
            current_active_unit_price_tl_per_kwh=FieldValue(
                value=data.get("unit_price"),
                confidence=0.8,
                evidence="fast_extraction",
                page=1
            ),
            distribution_unit_price_tl_per_kwh=FieldValue(
                value=data.get("distribution_price"),
                confidence=0.8,
                evidence="fast_extraction",
                page=1
            ),
            invoice_total_with_vat_tl=FieldValue(
                value=data.get("total"),
                confidence=0.8,
                evidence="fast_extraction",
                page=1
            ),
            raw_breakdown=RawBreakdown(
                energy_total_tl=FieldValue(value=data.get("energy_total"), confidence=0.8, evidence="", page=1),
                distribution_total_tl=FieldValue(value=data.get("distribution_total"), confidence=0.8, evidence="", page=1),
                vat_tl=FieldValue(value=data.get("vat"), confidence=0.8, evidence="", page=1),
            )
        )
        
    except Exception as e:
        logger.error(f"Fast extraction failed: {e}")
        raise


def extract_with_fallback(image_bytes: bytes, ultra_fast: bool = True) -> tuple[InvoiceExtraction, str]:
    """
    Önce ultra hızlı extraction dene, başarısız olursa normal extraction'a düş.
    
    Args:
        ultra_fast: True = ultra fast (~1-2s), False = fast (~2-3s)
    
    Returns:
        (extraction, mode) - mode: "ultra_fast", "fast", "full"
    """
    try:
        if ultra_fast:
            result = ultra_fast_extract(image_bytes)
        else:
            result = fast_extract(image_bytes)
        
        # Basit doğrulama
        if result.consumption_kwh.value and result.invoice_total_with_vat_tl.value:
            return result, "ultra_fast" if ultra_fast else "fast"
        
    except Exception as e:
        logger.warning(f"Fast extraction failed, falling back: {e}")
    
    # Normal extraction'a düş
    from .extractor import extract_invoice_data
    return extract_invoice_data(image_bytes, fast_mode=True), "full"
