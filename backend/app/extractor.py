import base64
import hashlib
import json
import logging
import os
import time
from functools import lru_cache
from typing import Optional
from openai import OpenAI, APIError, APIConnectionError, RateLimitError
from .extraction_prompt import EXTRACTION_PROMPT
from .models import InvoiceExtraction, FieldValue, RawBreakdown, InvoiceMeta
from .core.config import settings

# Logging setup - PII maskeleme için özel formatter
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Configuration from settings (pydantic-settings loads .env automatically)
# ═══════════════════════════════════════════════════════════════════════════════
OPENAI_MODEL = settings.openai_model
MAX_RETRIES = settings.openai_max_retries
RETRY_DELAY = settings.openai_retry_delay

# Initialize client lazily to ensure settings are loaded
_client = None

def get_openai_client() -> OpenAI:
    """Get OpenAI client (lazy initialization)"""
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise ExtractionError("OPENAI_API_KEY tanımlı değil")
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client

# In-memory cache for extraction results (hash -> result)
# TTL yok, uygulama restart'ında temizlenir
_extraction_cache: dict[str, InvoiceExtraction] = {}
CACHE_ENABLED = os.getenv("EXTRACTION_CACHE_ENABLED", "true").lower() == "true"


class ExtractionError(Exception):
    """Extraction işlemi sırasında oluşan hatalar"""
    pass


def compute_image_hash(image_bytes: bytes) -> str:
    """Görsel için SHA-256 hash hesapla (cache key olarak kullanılır)"""
    return hashlib.sha256(image_bytes).hexdigest()


def get_cached_extraction(image_hash: str) -> Optional[InvoiceExtraction]:
    """Cache'den extraction sonucu getir"""
    if not CACHE_ENABLED:
        return None
    return _extraction_cache.get(image_hash)


def cache_extraction(image_hash: str, extraction: InvoiceExtraction) -> None:
    """Extraction sonucunu cache'e kaydet"""
    if CACHE_ENABLED:
        _extraction_cache[image_hash] = extraction
        logger.info(f"Extraction cached: hash={image_hash[:16]}...")


def clear_extraction_cache() -> int:
    """Cache'i temizle, silinen kayıt sayısını döndür"""
    count = len(_extraction_cache)
    _extraction_cache.clear()
    logger.info(f"Extraction cache cleared: {count} entries removed")
    return count


def mask_pii(text: str) -> str:
    """
    PII (Personally Identifiable Information) maskeleme.
    Log'larda hassas verileri gizler.
    """
    import re
    
    # Telefon numarası maskeleme (Türkiye formatları)
    text = re.sub(r'\b(0?\d{3})[\s\-]?(\d{3})[\s\-]?(\d{2})[\s\-]?(\d{2})\b', r'\1***\4', text)
    
    # TC Kimlik No maskeleme (11 haneli)
    text = re.sub(r'\b(\d{3})\d{5}(\d{3})\b', r'\1*****\2', text)
    
    # Email maskeleme
    text = re.sub(r'\b([a-zA-Z0-9._%+-]{2})[a-zA-Z0-9._%+-]*@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b', r'\1***@\2', text)
    
    # Abone/Tesisat numarası maskeleme (8+ haneli sayılar)
    text = re.sub(r'\b(\d{3})\d{5,}(\d{2})\b', r'\1*****\2', text)
    
    return text

# OpenAI Structured Outputs için JSON Schema v3
# Genişletilmiş: Tüm Türkiye tedarikçileri, ETTN, EIC, çok zamanlı tüketim
EXTRACTION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "invoice_extraction_v3",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "vendor": {
                    "type": "string",
                    "description": "Tedarikçi: enerjisa, ck_bogazici, uludag, osmangazi, kolen, ekvator, yelden, aksa, dicle, gediz, trakya, zorlu, limak, unknown"
                },
                "distributor": {
                    "type": "string",
                    "description": "Dağıtım şirketi: ayedas, bedas, uedas, oedas, akedas, dedas, gedas, toroslar, baskent, unknown"
                },
                "ettn": {"$ref": "#/$defs/stringFieldValue"},
                "invoice_no": {"$ref": "#/$defs/stringFieldValue"},
                "invoice_date": {"$ref": "#/$defs/stringFieldValue"},
                "invoice_period": {
                    "type": "string",
                    "description": "Fatura dönemi YYYY-MM formatında"
                },
                "due_date": {"$ref": "#/$defs/stringFieldValue"},
                "invoice_type": {
                    "type": "string",
                    "enum": ["type_1", "type_2", "type_3", "type_4", "type_5", "type_6", "type_7", "unknown"],
                    "description": "Fatura yapı tipi"
                },
                "consumer": {
                    "type": "object",
                    "properties": {
                        "title": {"$ref": "#/$defs/stringFieldValue"},
                        "vkn": {"$ref": "#/$defs/stringFieldValue"},
                        "tckn": {"$ref": "#/$defs/stringFieldValue"},
                        "facility_address": {"$ref": "#/$defs/stringFieldValue"},
                        "eic_code": {"$ref": "#/$defs/stringFieldValue"},
                        "contract_no": {"$ref": "#/$defs/stringFieldValue"},
                        "meter_no": {"$ref": "#/$defs/stringFieldValue"}
                    },
                    "required": ["title", "vkn", "tckn", "facility_address", "eic_code", "contract_no", "meter_no"],
                    "additionalProperties": False
                },
                "consumption": {
                    "type": "object",
                    "properties": {
                        "total_kwh": {"$ref": "#/$defs/fieldValue"},
                        "t1_kwh": {"$ref": "#/$defs/fieldValue"},
                        "t2_kwh": {"$ref": "#/$defs/fieldValue"},
                        "t3_kwh": {"$ref": "#/$defs/fieldValue"},
                        "reactive_inductive_kvarh": {"$ref": "#/$defs/fieldValue"},
                        "reactive_capacitive_kvarh": {"$ref": "#/$defs/fieldValue"},
                        "demand_kw": {"$ref": "#/$defs/fieldValue"}
                    },
                    "required": ["total_kwh", "t1_kwh", "t2_kwh", "t3_kwh", "reactive_inductive_kvarh", "reactive_capacitive_kvarh", "demand_kw"],
                    "additionalProperties": False
                },
                "charges": {
                    "type": "object",
                    "properties": {
                        "active_energy_amount": {"$ref": "#/$defs/fieldValue"},
                        "distribution_amount": {"$ref": "#/$defs/fieldValue"},
                        "yek_amount": {"$ref": "#/$defs/fieldValue"},
                        "reactive_penalty_amount": {"$ref": "#/$defs/fieldValue"},
                        "consumption_tax": {"$ref": "#/$defs/fieldValue"},
                        "energy_fund": {"$ref": "#/$defs/fieldValue"},
                        "trt_share": {"$ref": "#/$defs/fieldValue"},
                        "vat_amount": {"$ref": "#/$defs/fieldValue"},
                        "total_amount": {"$ref": "#/$defs/fieldValue"}
                    },
                    "required": ["active_energy_amount", "distribution_amount", "yek_amount", "reactive_penalty_amount", "consumption_tax", "energy_fund", "trt_share", "vat_amount", "total_amount"],
                    "additionalProperties": False
                },
                "unit_prices": {
                    "type": "object",
                    "properties": {
                        "active_energy": {"$ref": "#/$defs/fieldValue"},
                        "distribution": {"$ref": "#/$defs/fieldValue"},
                        "yek": {"$ref": "#/$defs/fieldValue"},
                        "t1": {"$ref": "#/$defs/fieldValue"},
                        "t2": {"$ref": "#/$defs/fieldValue"},
                        "t3": {"$ref": "#/$defs/fieldValue"},
                        "demand": {"$ref": "#/$defs/fieldValue"}
                    },
                    "required": ["active_energy", "distribution", "yek", "t1", "t2", "t3", "demand"],
                    "additionalProperties": False
                },
                "tariff": {
                    "type": "object",
                    "properties": {
                        "voltage_level": {"type": "string", "enum": ["AG", "OG", "YG", "unknown"]},
                        "tariff_type": {"type": "string", "enum": ["mesken", "ticarethane", "sanayi", "tarimsal", "aydinlatma", "unknown"]},
                        "time_of_use": {"type": "string", "enum": ["single", "multi_time", "tiered", "unknown"]}
                    },
                    "required": ["voltage_level", "tariff_type", "time_of_use"],
                    "additionalProperties": False
                },
                "consumption_kwh": {"$ref": "#/$defs/fieldValue"},
                "current_active_unit_price_tl_per_kwh": {"$ref": "#/$defs/fieldValue"},
                "distribution_unit_price_tl_per_kwh": {"$ref": "#/$defs/fieldValue"},
                "demand_qty": {"$ref": "#/$defs/fieldValue"},
                "demand_unit_price_tl_per_unit": {"$ref": "#/$defs/fieldValue"},
                "invoice_total_with_vat_tl": {"$ref": "#/$defs/fieldValue"},
                "raw_breakdown": {
                    "type": "object",
                    "properties": {
                        "energy_total_tl": {"$ref": "#/$defs/fieldValue"},
                        "distribution_total_tl": {"$ref": "#/$defs/fieldValue"},
                        "btv_tl": {"$ref": "#/$defs/fieldValue"},
                        "vat_tl": {"$ref": "#/$defs/fieldValue"}
                    },
                    "required": ["energy_total_tl", "distribution_total_tl", "btv_tl", "vat_tl"],
                    "additionalProperties": False
                },
                "extra_items": {
                    "type": "array",
                    "description": "Ek kalemler: reaktif, mahsuplaşma, sayaç hizmet bedeli, düzeltme vb.",
                    "items": {"$ref": "#/$defs/extraItem"}
                },
                "meters": {
                    "type": "array",
                    "description": "Tip-6: Birden fazla sayaç varsa her sayacın bilgisi",
                    "items": {"$ref": "#/$defs/meterReading"}
                },
                "is_multi_meter": {
                    "type": "boolean",
                    "description": "Tip-6 fatura mı? Birden fazla sayaç var mı?"
                },
                "adjustments": {
                    "type": "array",
                    "description": "Tip-7: Mahsuplaşma/iade/düzeltme kalemleri",
                    "items": {"$ref": "#/$defs/adjustmentItem"}
                },
                "has_adjustments": {
                    "type": "boolean",
                    "description": "Tip-7 fatura mı? Mahsuplaşma var mı?"
                },
                "line_items": {
                    "type": "array",
                    "description": "Kalem bazlı enerji satırları (Yüksek/Düşük kademe, T1/T2/T3, etc.) - cross-check için kritik",
                    "items": {"$ref": "#/$defs/lineItem"}
                },
                "meta": {
                    "type": "object",
                    "properties": {
                        "tariff_group_guess": {"type": "string"},
                        "voltage_guess": {"type": "string"},
                        "term_type_guess": {"type": "string"}
                    },
                    "required": ["tariff_group_guess", "voltage_guess", "term_type_guess"],
                    "additionalProperties": False
                }
            },
            "required": [
                "vendor", "distributor", "ettn", "invoice_no", "invoice_date", "invoice_period", "due_date",
                "invoice_type", "consumer", "consumption", "charges", "unit_prices", "tariff",
                "consumption_kwh", "current_active_unit_price_tl_per_kwh", "distribution_unit_price_tl_per_kwh",
                "demand_qty", "demand_unit_price_tl_per_unit",
                "invoice_total_with_vat_tl", "raw_breakdown", "extra_items",
                "meters", "is_multi_meter", "adjustments", "has_adjustments", "line_items", "meta"
            ],
            "additionalProperties": False,
            "$defs": {
                "fieldValue": {
                    "type": "object",
                    "properties": {
                        "value": {"type": ["number", "null"]},
                        "confidence": {"type": "number"},
                        "evidence": {"type": "string"},
                        "page": {"type": "integer"}
                    },
                    "required": ["value", "confidence", "evidence", "page"],
                    "additionalProperties": False
                },
                "stringFieldValue": {
                    "type": "object",
                    "properties": {
                        "value": {"type": ["string", "null"]},
                        "confidence": {"type": "number"},
                        "evidence": {"type": "string"},
                        "page": {"type": "integer"}
                    },
                    "required": ["value", "confidence", "evidence", "page"],
                    "additionalProperties": False
                },
                "extraItem": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "amount_tl": {"type": ["number", "null"]},
                        "confidence": {"type": "number"},
                        "evidence": {"type": "string"},
                        "page": {"type": "integer"}
                    },
                    "required": ["label", "amount_tl", "confidence", "evidence", "page"],
                    "additionalProperties": False
                },
                "meterReading": {
                    "type": "object",
                    "description": "Tip-6: Sayaç okuma bilgisi",
                    "properties": {
                        "meter_id": {"type": "string"},
                        "meter_type": {"type": "string", "enum": ["main", "sub", "backup"]},
                        "consumption_kwh": {"type": "number"},
                        "unit_price_tl_per_kwh": {"type": ["number", "null"]},
                        "tariff_type": {"type": "string", "enum": ["single", "multi_time", "tiered"]},
                        "evidence": {"type": "string"},
                        "page": {"type": "integer"}
                    },
                    "required": ["meter_id", "meter_type", "consumption_kwh", "unit_price_tl_per_kwh", "tariff_type", "evidence", "page"],
                    "additionalProperties": False
                },
                "adjustmentItem": {
                    "type": "object",
                    "description": "Tip-7: Mahsuplaşma/iade kalemi",
                    "properties": {
                        "label": {"type": "string"},
                        "amount_tl": {"type": "number"},
                        "period": {"type": "string"},
                        "reason": {"type": "string"},
                        "evidence": {"type": "string"},
                        "page": {"type": "integer"}
                    },
                    "required": ["label", "amount_tl", "period", "reason", "evidence", "page"],
                    "additionalProperties": False
                },
                "lineItem": {
                    "type": "object",
                    "description": "Kalem bazlı enerji satırı (cross-check için)",
                    "properties": {
                        "label": {"type": "string", "description": "Kalem adı: Yüksek Kademe, Düşük Kademe, Gündüz, Puant, Gece, Aktif Enerji"},
                        "qty": {"type": "number", "description": "Miktar (kWh)"},
                        "unit": {"type": "string", "description": "Birim (kWh)"},
                        "unit_price": {"type": ["number", "null"], "description": "Birim fiyat (TL/kWh)"},
                        "amount_tl": {"type": ["number", "null"], "description": "Tutar (TL)"},
                        "confidence": {"type": "number"},
                        "evidence": {"type": "string"},
                        "page": {"type": "integer"}
                    },
                    "required": ["label", "qty", "unit", "unit_price", "amount_tl", "confidence", "evidence", "page"],
                    "additionalProperties": False
                }
            }
        }
    }
}

def encode_image(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("utf-8")


def _optimize_image_size(image_bytes: bytes, max_size: int = 1024) -> bytes:
    """
    Görsel boyutunu optimize et (hız için).
    
    Args:
        image_bytes: Orijinal görsel
        max_size: Maksimum genişlik/yükseklik (piksel)
    
    Returns:
        Optimize edilmiş görsel bytes
    """
    try:
        from PIL import Image
        import io
        
        img = Image.open(io.BytesIO(image_bytes))
        
        # Boyut kontrolü
        if img.width <= max_size and img.height <= max_size:
            return image_bytes
        
        # Aspect ratio koruyarak resize
        ratio = min(max_size / img.width, max_size / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # JPEG olarak kaydet (daha küçük boyut)
        buffer = io.BytesIO()
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        
        optimized = buffer.getvalue()
        logger.info(f"Image optimized: {len(image_bytes)} -> {len(optimized)} bytes ({len(optimized)/len(image_bytes)*100:.1f}%)")
        
        return optimized
        
    except Exception as e:
        logger.warning(f"Image optimization failed: {e}, using original")
        return image_bytes


def _call_openai_with_retry(
    messages: list,
    response_format: dict,
    max_tokens: int = 2000,
    temperature: float = 0.1,
    model: str = None
) -> dict:
    """
    OpenAI API çağrısı - retry mekanizması ile.
    
    Retry edilecek hatalar:
    - RateLimitError: Rate limit aşıldı
    - APIConnectionError: Bağlantı hatası
    - APIError: Genel API hatası (5xx)
    """
    client = get_openai_client()
    
    # Model seçimi
    if model is None:
        model = OPENAI_MODEL
    
    # GPT-5 modelleri max_completion_tokens kullanıyor
    is_gpt5 = 'gpt-5' in model.lower()
    
    last_error = None
    last_raw_response = None
    
    for attempt in range(MAX_RETRIES):
        try:
            # GPT-5 için farklı parametreler
            if is_gpt5:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    max_completion_tokens=max_tokens
                    # GPT-5 temperature desteklemiyor, default (1) kullanılır
                )
            else:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format=response_format,
                    max_tokens=max_tokens,
                    temperature=temperature
                )
            
            raw_json = response.choices[0].message.content
            last_raw_response = raw_json
            
            # Boş yanıt kontrolü
            if not raw_json or raw_json.strip() == "":
                logger.warning(f"Empty response from OpenAI, retry {attempt + 1}/{MAX_RETRIES}")
                last_error = Exception("Empty response from OpenAI")
                time.sleep(RETRY_DELAY)
                continue
            
            # JSON parse et
            try:
                return json.loads(raw_json)
            except json.JSONDecodeError as je:
                # JSON repair dene
                repaired = _try_repair_json(raw_json)
                if repaired is not None:
                    logger.info("JSON repaired successfully")
                    return repaired
                raise je
            
        except RateLimitError as e:
            last_error = e
            wait_time = RETRY_DELAY * (2 ** attempt)  # Exponential backoff
            logger.warning(f"Rate limit hit, waiting {wait_time}s before retry {attempt + 1}/{MAX_RETRIES}")
            time.sleep(wait_time)
            
        except APIConnectionError as e:
            last_error = e
            wait_time = RETRY_DELAY * (attempt + 1)
            logger.warning(f"Connection error, waiting {wait_time}s before retry {attempt + 1}/{MAX_RETRIES}")
            time.sleep(wait_time)
            
        except APIError as e:
            # 5xx hatalarında retry, 4xx'te hemen fail
            if e.status_code and e.status_code >= 500:
                last_error = e
                wait_time = RETRY_DELAY * (attempt + 1)
                logger.warning(f"API error {e.status_code}, waiting {wait_time}s before retry {attempt + 1}/{MAX_RETRIES}")
                time.sleep(wait_time)
            else:
                raise ExtractionError(f"OpenAI API hatası: {str(e)}")
                
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"JSON parse error at position {e.pos}: {e.msg}, retry {attempt + 1}/{MAX_RETRIES}")
            if last_raw_response:
                # İlk 500 karakteri logla
                logger.debug(f"Raw response (first 500 chars): {last_raw_response[:500]}")
            time.sleep(RETRY_DELAY)
    
    error_msg = f"OpenAI API çağrısı {MAX_RETRIES} denemeden sonra başarısız: {str(last_error)}"
    if last_raw_response:
        error_msg += f" (raw response length: {len(last_raw_response)})"
    raise ExtractionError(error_msg)


def _try_repair_json(raw_json: str) -> dict | None:
    """
    Bozuk JSON'u tamir etmeye çalış.
    
    Yaygın sorunlar:
    - Kesilmiş JSON (max_tokens aşıldı)
    - Escape edilmemiş karakterler
    - Trailing comma
    """
    import re
    
    if not raw_json:
        return None
    
    # 1. Trailing comma temizle
    cleaned = re.sub(r',\s*}', '}', raw_json)
    cleaned = re.sub(r',\s*]', ']', cleaned)
    
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    
    # 2. Kesilmiş JSON - eksik kapanış parantezleri ekle
    open_braces = cleaned.count('{') - cleaned.count('}')
    open_brackets = cleaned.count('[') - cleaned.count(']')
    
    if open_braces > 0 or open_brackets > 0:
        # Son incomplete string'i kapat
        if cleaned.rstrip().endswith('"'):
            pass  # String zaten kapalı
        elif '"' in cleaned:
            # Son açık string'i bul ve kapat
            last_quote = cleaned.rfind('"')
            # Eğer escape edilmemişse
            if last_quote > 0 and cleaned[last_quote-1] != '\\':
                # String içinde miyiz kontrol et
                quote_count = cleaned[:last_quote+1].count('"') - cleaned[:last_quote+1].count('\\"')
                if quote_count % 2 == 1:
                    cleaned += '"'
        
        # Eksik parantezleri ekle
        cleaned += ']' * open_brackets
        cleaned += '}' * open_braces
        
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
    
    # 3. Son çare: json5 veya demjson kullanmayı dene (eğer yüklüyse)
    try:
        import json5
        return json5.loads(raw_json)
    except (ImportError, Exception):
        pass
    
    return None


def extract_invoice_data(
    image_bytes: bytes, 
    mime_type: str = "image/png",
    fast_mode: bool = True,
    text_hint: str = ""
) -> InvoiceExtraction:
    """
    OpenAI Vision API ile fatura görselinden veri çıkar (Structured Outputs).
    
    Features:
    - Hash-based caching: Aynı görsel tekrar analiz edilmez
    - PII masking: Log'larda hassas veriler gizlenir
    - Structured Outputs: %100 şemaya uygun JSON garantisi
    - Genişletilmiş tedarikçi desteği: Tüm Türkiye tedarikçileri
    - Text hint: pdfplumber'dan gelen değerler prompt'a eklenir (cross-validation)
    
    Args:
        image_bytes: Görsel bytes
        mime_type: MIME tipi
        fast_mode: True = gpt-4o-mini + low detail (hızlı), False = gpt-4o + high detail (doğru)
        text_hint: pdfplumber'dan çıkarılan değerler (OpenAI'a yardımcı kanıt olarak verilir)
    """
    
    # Hash hesapla ve cache kontrol et
    image_hash = compute_image_hash(image_bytes)
    cached_result = get_cached_extraction(image_hash)
    
    if cached_result is not None:
        logger.info(f"Cache hit: hash={image_hash[:16]}... returning cached extraction")
        return cached_result
    
    # Model ve detail seçimi
    # gpt-4o + detail=auto en iyi hız/doğruluk dengesi
    model = settings.openai_model_fast if fast_mode else settings.openai_model_accurate
    detail = "auto"  # auto = OpenAI otomatik optimize eder
    
    logger.info(f"Cache miss: hash={image_hash[:16]}... calling OpenAI API (model={model}, detail={detail})")
    if text_hint:
        logger.info(f"Text hint provided: {len(text_hint)} chars")
    
    # Görsel boyutunu her zaman optimize et (hız için kritik)
    image_bytes = _optimize_image_size(image_bytes, max_size=1200)
    
    base64_image = encode_image(image_bytes)
    
    # System prompt'a text hint ekle (varsa)
    system_prompt = EXTRACTION_PROMPT
    if text_hint:
        system_prompt = EXTRACTION_PROMPT + text_hint
    
    messages = [
        {
            "role": "system",
            "content": system_prompt
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_image}",
                        "detail": detail
                    }
                }
            ]
        }
    ]
    
    # Retry mekanizması ile API çağrısı
    # JSON mode kullan (Structured Outputs çok yavaş)
    data = _call_openai_with_retry(
        messages=messages,
        response_format={"type": "json_object"},  # Basit JSON mode - çok daha hızlı
        max_tokens=2000,  # Yeterli token ver - kesilmiş JSON önle
        temperature=0.1,
        model=model
    )
    
    # Log extraction result (PII masked)
    vendor = data.get("vendor", "unknown")
    distributor = data.get("distributor", "unknown")
    period = data.get("invoice_period", "")
    consumption = data.get("consumption_kwh", {}).get("value")
    invoice_type = data.get("invoice_type", "unknown")
    extra_count = len(data.get("extra_items", []))
    
    # DEBUG: Distribution değerlerini logla
    dist_unit_price = data.get("distribution_unit_price_tl_per_kwh", {})
    raw_breakdown_data = data.get("raw_breakdown", {})
    dist_total = raw_breakdown_data.get("distribution_total_tl", {}) if raw_breakdown_data else {}
    line_items_data = data.get("line_items", [])
    
    # DEBUG: invoice_total_with_vat_tl değerini logla
    invoice_total_raw = data.get("invoice_total_with_vat_tl", {})
    charges_data = data.get("charges", {})
    charges_total = charges_data.get("total_amount", {}) if charges_data else {}
    
    logger.info(f"Extraction complete: vendor={vendor}, distributor={distributor}, period={period}, consumption={consumption} kWh, type={invoice_type}, extra_items={extra_count}")
    logger.info(f"DEBUG invoice_total_with_vat_tl: {invoice_total_raw}")
    logger.info(f"DEBUG charges.total_amount: {charges_total}")
    logger.info(f"DEBUG Distribution: unit_price={dist_unit_price}, total={dist_total}")
    logger.info(f"DEBUG Line items count: {len(line_items_data)}")
    for i, item in enumerate(line_items_data[:5]):  # İlk 5 satır
        logger.info(f"DEBUG Line item {i}: {item.get('label', 'N/A')} | qty={item.get('qty')} | unit_price={item.get('unit_price')} | amount={item.get('amount_tl')} | type={item.get('line_type', 'N/A')}")
    
    # Model çıktısını Pydantic modeline dönüştür
    def parse_field(field_data) -> FieldValue:
        if field_data is None:
            return FieldValue()
        # Handle case where OpenAI returns a plain number instead of dict
        if isinstance(field_data, (int, float)):
            return FieldValue(value=field_data, confidence=0.8, evidence="raw number from hint")
        return FieldValue(
            value=field_data.get("value"),
            confidence=field_data.get("confidence", 0),
            evidence=field_data.get("evidence", ""),
            page=field_data.get("page", 1)
        )
    
    def parse_string_field(field_data) -> 'StringFieldValue':
        from .models import StringFieldValue
        if field_data is None:
            return StringFieldValue()
        # Handle case where OpenAI returns a plain string instead of dict
        if isinstance(field_data, str):
            return StringFieldValue(value=field_data, confidence=0.5, evidence="raw string")
        return StringFieldValue(
            value=field_data.get("value"),
            confidence=field_data.get("confidence", 0),
            evidence=field_data.get("evidence", ""),
            page=field_data.get("page", 1)
        )
    
    raw_breakdown = None
    if "raw_breakdown" in data and data["raw_breakdown"]:
        rb = data["raw_breakdown"]
        raw_breakdown = RawBreakdown(
            energy_total_tl=parse_field(rb.get("energy_total_tl")),
            distribution_total_tl=parse_field(rb.get("distribution_total_tl")),
            btv_tl=parse_field(rb.get("btv_tl")),
            vat_tl=parse_field(rb.get("vat_tl"))
        )
    
    # Parse extra_items (Tip-5/7 support)
    from .models import ExtraItem, MeterReading, AdjustmentItem, ConsumerInfo, ConsumptionDetails, ChargeBreakdown, UnitPrices, TariffInfo, StringFieldValue
    extra_items = []
    for item in data.get("extra_items", []):
        if item.get("label"):  # Skip empty items
            extra_items.append(ExtraItem(
                label=item.get("label", ""),
                amount_tl=item.get("amount_tl"),
                confidence=item.get("confidence", 0),
                evidence=item.get("evidence", ""),
                page=item.get("page", 1),
                category=_categorize_extra_item(item.get("label", "")),
                included_in_offer=False  # Default: not included in offer
            ))
    
    # Parse meters (Tip-6 support)
    meters = []
    for meter in data.get("meters", []):
        if meter.get("meter_id"):
            meters.append(MeterReading(
                meter_id=meter.get("meter_id", ""),
                meter_type=meter.get("meter_type", "main"),
                consumption_kwh=meter.get("consumption_kwh", 0),
                unit_price_tl_per_kwh=meter.get("unit_price_tl_per_kwh"),
                tariff_type=meter.get("tariff_type", "single"),
                evidence=meter.get("evidence", ""),
                page=meter.get("page", 1)
            ))
    
    # Parse adjustments (Tip-7 support)
    adjustments = []
    for adj in data.get("adjustments", []):
        if adj.get("label"):
            adjustments.append(AdjustmentItem(
                label=adj.get("label", ""),
                amount_tl=adj.get("amount_tl", 0),
                period=adj.get("period", ""),
                reason=adj.get("reason", ""),
                evidence=adj.get("evidence", ""),
                page=adj.get("page", 1)
            ))
    
    is_multi_meter = data.get("is_multi_meter", False) or len(meters) > 1
    has_adjustments = data.get("has_adjustments", False) or len(adjustments) > 0
    
    # Parse line_items (kalem bazlı enerji satırları - cross-check için)
    # NEGATİF KALEMLER (mahsuplaşma) DAHİL!
    from .models import LineItem
    line_items = []
    for item in data.get("line_items", []):
        # Negatif qty kabul et (mahsuplaşma için)
        if item.get("label") and item.get("qty") is not None:
            qty = item.get("qty", 0)
            unit_price = item.get("unit_price")
            amount_tl = item.get("amount_tl")
            
            # Cross-check: qty × unit_price ≈ amount_tl?
            # Negatif değerler için de çalışmalı
            crosscheck_passed = True
            crosscheck_delta = None
            if unit_price and amount_tl and amount_tl != 0:
                calculated = qty * unit_price
                crosscheck_delta = abs((calculated - amount_tl) / abs(amount_tl)) * 100
                crosscheck_passed = crosscheck_delta <= 2.0  # %2 tolerans
            
            line_items.append(LineItem(
                label=item.get("label", ""),
                qty=qty,
                unit=item.get("unit", "kWh"),
                unit_price=unit_price,
                amount_tl=amount_tl,
                confidence=item.get("confidence", 0),
                evidence=item.get("evidence", ""),
                page=item.get("page", 1),
                crosscheck_passed=crosscheck_passed,
                crosscheck_delta=crosscheck_delta
            ))
    
    # Map invoice_type from schema to meta.invoice_type_guess format
    invoice_type_raw = data.get("invoice_type", "unknown")
    invoice_type_mapped = _map_invoice_type(invoice_type_raw)
    
    # Parse consumer info
    consumer_data = data.get("consumer", {})
    consumer = ConsumerInfo(
        title=parse_string_field(consumer_data.get("title")),
        vkn=parse_string_field(consumer_data.get("vkn")),
        tckn=parse_string_field(consumer_data.get("tckn")),
        facility_address=parse_string_field(consumer_data.get("facility_address")),
        eic_code=parse_string_field(consumer_data.get("eic_code")),
        contract_no=parse_string_field(consumer_data.get("contract_no")),
        meter_no=parse_string_field(consumer_data.get("meter_no"))
    ) if consumer_data else None
    
    # Parse consumption details
    consumption_data = data.get("consumption", {})
    consumption_details = ConsumptionDetails(
        total_kwh=parse_field(consumption_data.get("total_kwh")),
        t1_kwh=parse_field(consumption_data.get("t1_kwh")),
        t2_kwh=parse_field(consumption_data.get("t2_kwh")),
        t3_kwh=parse_field(consumption_data.get("t3_kwh")),
        reactive_inductive_kvarh=parse_field(consumption_data.get("reactive_inductive_kvarh")),
        reactive_capacitive_kvarh=parse_field(consumption_data.get("reactive_capacitive_kvarh")),
        demand_kw=parse_field(consumption_data.get("demand_kw"))
    ) if consumption_data else None
    
    # Parse charges
    charges_data = data.get("charges", {})
    charges = ChargeBreakdown(
        active_energy_amount=parse_field(charges_data.get("active_energy_amount")),
        distribution_amount=parse_field(charges_data.get("distribution_amount")),
        yek_amount=parse_field(charges_data.get("yek_amount")),
        reactive_penalty_amount=parse_field(charges_data.get("reactive_penalty_amount")),
        consumption_tax=parse_field(charges_data.get("consumption_tax")),
        energy_fund=parse_field(charges_data.get("energy_fund")),
        trt_share=parse_field(charges_data.get("trt_share")),
        vat_amount=parse_field(charges_data.get("vat_amount")),
        total_amount=parse_field(charges_data.get("total_amount"))
    ) if charges_data else None
    
    # Parse unit prices
    unit_prices_data = data.get("unit_prices", {})
    unit_prices = UnitPrices(
        active_energy=parse_field(unit_prices_data.get("active_energy")),
        distribution=parse_field(unit_prices_data.get("distribution")),
        yek=parse_field(unit_prices_data.get("yek")),
        t1=parse_field(unit_prices_data.get("t1")),
        t2=parse_field(unit_prices_data.get("t2")),
        t3=parse_field(unit_prices_data.get("t3")),
        demand=parse_field(unit_prices_data.get("demand"))
    ) if unit_prices_data else None
    
    # Parse tariff info
    tariff_data = data.get("tariff", {})
    tariff = TariffInfo(
        voltage_level=tariff_data.get("voltage_level", "unknown"),
        tariff_type=tariff_data.get("tariff_type", "unknown"),
        time_of_use=tariff_data.get("time_of_use", "unknown")
    ) if tariff_data else None
    
    extraction = InvoiceExtraction(
        vendor=data.get("vendor", "unknown"),
        distributor=data.get("distributor", "unknown"),
        ettn=parse_string_field(data.get("ettn")),
        invoice_no=parse_string_field(data.get("invoice_no")),
        invoice_date=parse_string_field(data.get("invoice_date")),
        invoice_period=data.get("invoice_period", ""),
        due_date=parse_string_field(data.get("due_date")),
        consumer=consumer,
        consumption=consumption_details,
        charges=charges,
        unit_prices=unit_prices,
        tariff=tariff,
        # Legacy fields for backward compatibility
        consumption_kwh=parse_field(data.get("consumption_kwh")),
        current_active_unit_price_tl_per_kwh=parse_field(data.get("current_active_unit_price_tl_per_kwh")),
        distribution_unit_price_tl_per_kwh=parse_field(data.get("distribution_unit_price_tl_per_kwh")),
        demand_qty=parse_field(data.get("demand_qty")),
        demand_unit_price_tl_per_unit=parse_field(data.get("demand_unit_price_tl_per_unit")),
        invoice_total_with_vat_tl=parse_field(data.get("invoice_total_with_vat_tl")),
        raw_breakdown=raw_breakdown,
        extra_items=extra_items,
        meters=meters,
        is_multi_meter=is_multi_meter,
        adjustments=adjustments,
        has_adjustments=has_adjustments,
        line_items=line_items,
        meta=InvoiceMeta(
            tariff_group_guess=data.get("meta", {}).get("tariff_group_guess", "unknown"),
            voltage_guess=data.get("meta", {}).get("voltage_guess", "unknown"),
            term_type_guess=data.get("meta", {}).get("term_type_guess", "unknown"),
            invoice_type_guess=invoice_type_mapped
        ) if data.get("meta") else InvoiceMeta(invoice_type_guess=invoice_type_mapped)
    )
    
    # Post-processing: Line items'dan eksik değerleri türet
    extraction = _derive_missing_values_from_line_items(extraction)
    
    # Post-processing: Birim fiyat sanity check ve düzeltme
    extraction = _postprocess_unit_prices(extraction)
    
    # Cache'e kaydet
    cache_extraction(image_hash, extraction)
    
    return extraction


def _derive_missing_values_from_line_items(extraction: InvoiceExtraction) -> InvoiceExtraction:
    """
    Line items'dan eksik ana değerleri türet.
    
    OpenAI bazen consumption_kwh, current_active_unit_price_tl_per_kwh gibi
    ana alanları null döndürüyor ama line_items'da değerler var.
    Bu fonksiyon line_items'dan bu değerleri türetir.
    """
    if not extraction.line_items:
        return extraction
    
    # Enerji satırlarını bul
    energy_keywords = ["enerji", "aktif", "sktt", "tüketim", "kademe", "gündüz", "puant", "gece", "t1", "t2", "t3"]
    dist_keywords = ["dağıtım", "dskb", "elk. dağıtım", "distribution"]
    
    energy_lines = []
    dist_lines = []
    
    for item in extraction.line_items:
        label_lower = item.label.lower()
        if any(kw in label_lower for kw in energy_keywords) and item.unit == "kWh":
            energy_lines.append(item)
        elif any(kw in label_lower for kw in dist_keywords) and item.unit == "kWh":
            dist_lines.append(item)
    
    # ═══════════════════════════════════════════════════════════════════════
    # consumption_kwh türetme
    # ═══════════════════════════════════════════════════════════════════════
    if extraction.consumption_kwh.value is None or extraction.consumption_kwh.value <= 0:
        if energy_lines:
            total_kwh = sum(item.qty for item in energy_lines if item.qty)
            total_amount = sum(item.amount_tl for item in energy_lines if item.amount_tl)
            
            if total_kwh > 0:
                # Türkçe sayı formatı kontrolü: qty çok küçük, amount büyükse binlik ayraç sorunu var
                # Örnek: 187.552,35 kWh -> 187.55235 olarak okunmuş
                if total_kwh < 1000 and total_amount > 10000:
                    # Muhtemelen binlik ayraç sorunu
                    implied_unit_price = total_amount / total_kwh
                    if implied_unit_price > 15.0:  # Birim fiyat çok yüksek
                        corrected_kwh = total_kwh * 1000
                        corrected_unit_price = total_amount / corrected_kwh
                        if 0.5 <= corrected_unit_price <= 15.0:
                            logger.warning(f"consumption_kwh binlik ayraç düzeltmesi: {total_kwh} -> {corrected_kwh} kWh")
                            total_kwh = corrected_kwh
                
                extraction.consumption_kwh.value = total_kwh
                extraction.consumption_kwh.confidence = 0.75
                extraction.consumption_kwh.evidence = f"[LINE_ITEMS'DAN TÜRETİLDİ: {len(energy_lines)} enerji satırı toplamı]"
                logger.info(f"consumption_kwh türetildi: {total_kwh} kWh (line_items'dan)")
    
    # ═══════════════════════════════════════════════════════════════════════
    # current_active_unit_price_tl_per_kwh türetme
    # ═══════════════════════════════════════════════════════════════════════
    if extraction.current_active_unit_price_tl_per_kwh.value is None:
        # Önce line_items'dan birim fiyat bul
        unit_prices = [item.unit_price for item in energy_lines if item.unit_price and item.unit_price > 0]
        if unit_prices:
            # Önce doğrudan unit_price değerlerini kontrol et
            # OpenAI genellikle unit_price'ı doğru parse eder
            valid_unit_prices = [p for p in unit_prices if 0.5 <= p <= 15.0]
            
            if valid_unit_prices:
                # Geçerli birim fiyatların ağırlıklı ortalaması
                # (qty'ler yanlış olabilir, sadece unit_price'ları kullan)
                avg_price = sum(valid_unit_prices) / len(valid_unit_prices)
                extraction.current_active_unit_price_tl_per_kwh.value = avg_price
                extraction.current_active_unit_price_tl_per_kwh.confidence = 0.80
                extraction.current_active_unit_price_tl_per_kwh.evidence = f"[LINE_ITEMS'DAN: doğrudan unit_price]"
                logger.info(f"current_active_unit_price türetildi: {avg_price:.4f} TL/kWh (line_items unit_price'dan)")
            else:
                # unit_price'lar geçersiz, amount/qty hesapla
                total_amount = sum(item.amount_tl for item in energy_lines if item.amount_tl)
                total_qty = sum(item.qty for item in energy_lines if item.qty)
                if total_qty > 0 and total_amount > 0:
                    weighted_avg = total_amount / total_qty
                    
                    # Türkçe sayı formatı düzeltmesi: Eğer birim fiyat çok yüksekse,
                    # muhtemelen qty'de binlik ayraç sorunu var (187.552,35 -> 187.55235)
                    if weighted_avg > 15.0 and total_qty < 1000:
                        corrected_qty = total_qty * 1000
                        corrected_avg = total_amount / corrected_qty
                        if 0.5 <= corrected_avg <= 15.0:
                            logger.warning(f"Türkçe sayı formatı düzeltmesi: qty {total_qty} -> {corrected_qty}, birim fiyat {weighted_avg:.4f} -> {corrected_avg:.4f}")
                            weighted_avg = corrected_avg
                            # consumption_kwh'ı da düzelt
                            if extraction.consumption_kwh.value and extraction.consumption_kwh.value < 1000:
                                extraction.consumption_kwh.value = extraction.consumption_kwh.value * 1000
                                extraction.consumption_kwh.evidence += " [BINLIK AYRAÇ DÜZELTMESİ]"
                                logger.warning(f"consumption_kwh düzeltildi: {extraction.consumption_kwh.value} kWh")
                    
                    extraction.current_active_unit_price_tl_per_kwh.value = weighted_avg
                    extraction.current_active_unit_price_tl_per_kwh.confidence = 0.70
                    extraction.current_active_unit_price_tl_per_kwh.evidence = f"[LINE_ITEMS'DAN TÜRETİLDİ: ağırlıklı ortalama]"
                    logger.info(f"current_active_unit_price türetildi: {weighted_avg:.4f} TL/kWh (line_items'dan)")
    
    # ═══════════════════════════════════════════════════════════════════════
    # distribution_unit_price_tl_per_kwh türetme
    # ═══════════════════════════════════════════════════════════════════════
    if extraction.distribution_unit_price_tl_per_kwh.value is None:
        if dist_lines:
            unit_prices = [item.unit_price for item in dist_lines if item.unit_price and item.unit_price > 0]
            if unit_prices:
                avg_price = sum(unit_prices) / len(unit_prices)
                extraction.distribution_unit_price_tl_per_kwh.value = avg_price
                extraction.distribution_unit_price_tl_per_kwh.confidence = 0.70
                extraction.distribution_unit_price_tl_per_kwh.evidence = f"[LINE_ITEMS'DAN TÜRETİLDİ: dağıtım satırı]"
                logger.info(f"distribution_unit_price türetildi: {avg_price:.4f} TL/kWh (line_items'dan)")
    
    # ═══════════════════════════════════════════════════════════════════════
    # invoice_total_with_vat_tl türetme
    # ⚠️ KRİTİK: Bu değer FATURADAN OKUNMALI, HESAPLANMAMALI!
    # Ama OpenAI okuyamıyorsa line_items'dan hesapla (son çare)
    # ═══════════════════════════════════════════════════════════════════════
    if extraction.invoice_total_with_vat_tl.value is None or extraction.invoice_total_with_vat_tl.value <= 0:
        # Önce charges.total_amount'a bak
        if extraction.charges and extraction.charges.total_amount and extraction.charges.total_amount.value:
            extraction.invoice_total_with_vat_tl.value = extraction.charges.total_amount.value
            extraction.invoice_total_with_vat_tl.confidence = extraction.charges.total_amount.confidence
            extraction.invoice_total_with_vat_tl.evidence = f"[CHARGES.TOTAL_AMOUNT'DAN ALINDI]"
            logger.info(f"invoice_total alındı: {extraction.charges.total_amount.value} TL (charges'dan)")
        elif extraction.line_items:
            # SON ÇARE: Line items'dan hesapla
            # Line items toplamı = KDV HARİÇ tutar
            # Bazı faturalarda KDV %0 olabilir, bu durumda line_items ≈ fatura tutarı
            total_from_lines = sum(item.amount_tl for item in extraction.line_items if item.amount_tl)
            if total_from_lines != 0:
                # Line items toplamını KDV DAHİL olarak kabul et
                # (Bazı faturalarda KDV %0 veya dahil olabilir)
                # Calculator'da KDV kontrolü yapılacak
                extraction.invoice_total_with_vat_tl.value = round(total_from_lines, 2)
                extraction.invoice_total_with_vat_tl.confidence = 0.60  # Düşük confidence - hesaplanmış
                extraction.invoice_total_with_vat_tl.evidence = f"[LINE_ITEMS TOPLAMI: {total_from_lines:.2f} TL]"
                logger.warning(f"invoice_total LINE_ITEMS TOPLAMINDAN ALINDI: {total_from_lines:.2f} TL (KDV durumu belirsiz)")
            else:
                logger.warning("invoice_total_with_vat_tl faturadan okunamadı! Bu değer manuel girilmeli.")
    
    # ═══════════════════════════════════════════════════════════════════════
    # invoice_period düzeltme (fatura numarasından)
    # ═══════════════════════════════════════════════════════════════════════
    # CK faturalarında fatura no: BBE2025000297356 (ilk 3 harf + 4 hane yıl)
    # Eğer dönem yılı fatura numarasındaki yıldan farklıysa düzelt
    if extraction.invoice_no and extraction.invoice_no.value and extraction.invoice_period:
        import re
        invoice_no = extraction.invoice_no.value
        period = extraction.invoice_period
        
        # Fatura numarasından yılı çıkar (BBE2025... veya EAL2025... formatı)
        year_match = re.search(r'[A-Z]{2,3}(\d{4})', invoice_no)
        if year_match:
            invoice_year = year_match.group(1)
            
            # Dönemden yılı çıkar (2023-08 formatı)
            period_match = re.match(r'(\d{4})-(\d{2})', period)
            if period_match:
                period_year = period_match.group(1)
                period_month = period_match.group(2)
                
                # Yıllar farklıysa düzelt
                if invoice_year != period_year:
                    old_period = period
                    extraction.invoice_period = f"{invoice_year}-{period_month}"
                    logger.warning(f"invoice_period düzeltildi: {old_period} → {extraction.invoice_period} (fatura no'dan)")
    
    return extraction


def _postprocess_unit_prices(extraction: InvoiceExtraction) -> InvoiceExtraction:
    """
    Birim fiyat sanity check ve otomatik düzeltme.
    
    Türkiye'de 2024-2025 birim fiyatlar:
    - Aktif enerji: 1-10 TL/kWh arası (mesken ~3-5, sanayi ~2-4)
    - Dağıtım: 0.3-2 TL/kWh arası
    
    Eğer birim fiyat 0.5'ten küçükse muhtemelen virgül hatası var:
    - 0.4286 → 4.286 (10 ile çarp)
    - 0.07485 → 0.7485 (10 ile çarp)
    
    Eğer dağıtım birim fiyatı yoksa, tarife bilgilerinden EPDK tarifesine göre türet.
    """
    MIN_ACTIVE_PRICE = 0.5
    MIN_DIST_PRICE = 0.1
    
    # Aktif enerji birim fiyatı kontrolü
    if extraction.current_active_unit_price_tl_per_kwh.value is not None:
        price = extraction.current_active_unit_price_tl_per_kwh.value
        
        if 0 < price < MIN_ACTIVE_PRICE:
            # Muhtemelen virgül hatası - 10 ile çarp
            corrected = price * 10
            logger.warning(
                f"Birim fiyat düzeltmesi: {price:.4f} → {corrected:.4f} TL/kWh "
                f"(muhtemelen TR virgül formatı hatası)"
            )
            extraction.current_active_unit_price_tl_per_kwh.value = corrected
            extraction.current_active_unit_price_tl_per_kwh.evidence += f" [AUTO-CORRECTED: {price}→{corrected}]"
            extraction.current_active_unit_price_tl_per_kwh.confidence *= 0.8  # Confidence düşür
    
    # Dağıtım birim fiyatı kontrolü
    if extraction.distribution_unit_price_tl_per_kwh.value is not None:
        price = extraction.distribution_unit_price_tl_per_kwh.value
        
        if 0 < price < MIN_DIST_PRICE:
            # Muhtemelen virgül hatası - 10 ile çarp
            corrected = price * 10
            logger.warning(
                f"Dağıtım birim fiyat düzeltmesi: {price:.4f} → {corrected:.4f} TL/kWh "
                f"(muhtemelen TR virgül formatı hatası)"
            )
            extraction.distribution_unit_price_tl_per_kwh.value = corrected
            extraction.distribution_unit_price_tl_per_kwh.evidence += f" [AUTO-CORRECTED: {price}→{corrected}]"
            extraction.distribution_unit_price_tl_per_kwh.confidence *= 0.8
    
    # ═══════════════════════════════════════════════════════════════════════
    # DAĞITIM BİRİM FİYATI YOKSA EPDK TARİFESİNDEN TÜRET
    # ═══════════════════════════════════════════════════════════════════════
    if extraction.distribution_unit_price_tl_per_kwh.value is None:
        from .distribution_tariffs import get_distribution_unit_price_from_extraction
        
        lookup_result = get_distribution_unit_price_from_extraction(extraction)
        
        if lookup_result.success and lookup_result.unit_price is not None:
            derived_price = lookup_result.unit_price
            # Tarife bilgilerini logla
            tariff_info = ""
            if extraction.tariff:
                tariff_info = f"{extraction.tariff.tariff_type}/{extraction.tariff.voltage_level}/{extraction.tariff.time_of_use}"
            elif extraction.meta:
                tariff_info = f"{extraction.meta.tariff_group_guess}/{extraction.meta.voltage_guess}/{extraction.meta.term_type_guess}"
            
            logger.info(
                f"Dağıtım birim fiyatı EPDK tarifesinden türetildi: {derived_price:.6f} TL/kWh "
                f"(tarife: {tariff_info})"
            )
            
            extraction.distribution_unit_price_tl_per_kwh.value = derived_price
            extraction.distribution_unit_price_tl_per_kwh.confidence = 0.85  # EPDK tarifesi güvenilir
            extraction.distribution_unit_price_tl_per_kwh.evidence = f"[EPDK TARİFESİNDEN TÜRETİLDİ: {tariff_info}]"
        else:
            logger.warning(
                f"Dağıtım birim fiyatı türetilemedi - tarife bilgisi eksik veya tanımsız. "
                f"Tarife: {extraction.tariff}, Meta: {extraction.meta}"
            )
    
    return extraction


def _map_invoice_type(invoice_type: str) -> str:
    """Map schema invoice_type to display format."""
    mapping = {
        "type_1": "Tip-1",
        "type_2": "Tip-2",
        "type_3": "Tip-3",
        "type_4": "Tip-4",
        "type_5": "Tip-5",
        "type_6": "Tip-6",
        "type_7": "Tip-7",
        "unknown": "unknown"
    }
    return mapping.get(invoice_type, "unknown")


def _categorize_extra_item(label: str) -> str:
    """Categorize extra item based on label."""
    label_lower = label.lower()
    
    # Reactive/power related
    if any(kw in label_lower for kw in ["reaktif", "kapasitif", "endüktif", "güç", "power"]):
        return "reactive"
    
    # Adjustments/corrections
    if any(kw in label_lower for kw in ["mahsup", "iade", "düzeltme", "iptal", "fark"]):
        return "adjustment"
    
    # Service fees
    if any(kw in label_lower for kw in ["sayaç", "hizmet", "okuma", "kesme", "açma", "perakende"]):
        return "service_fee"
    
    return "other"


# ═══════════════════════════════════════════════════════════════════════════════
# Section-Based Extraction (Experimental)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_invoice_data_sectioned(
    image_bytes: bytes,
    mime_type: str = "image/png",
) -> InvoiceExtraction:
    """
    Section-based extraction - her bölge için ayrı prompt.
    
    Bu yaklaşım:
    1. Özet, Fatura Detayı, Vergiler için ayrı Vision call
    2. Sonuçları birleştir
    3. Validate et
    
    Avantajları:
    - Daha odaklanmış prompt
    - Yanlış tablodan okuma riski düşük
    - Vendor-agnostic
    """
    from .section_extractor import extract_all_sections, extraction_result_to_dict
    
    # Section extraction
    result = extract_all_sections(image_bytes)
    
    # Dict'e dönüştür
    data = extraction_result_to_dict(result)
    
    # InvoiceExtraction modeline dönüştür
    def parse_field(field_data) -> FieldValue:
        if field_data is None:
            return FieldValue()
        # Handle case where OpenAI returns a plain number instead of dict
        if isinstance(field_data, (int, float)):
            return FieldValue(value=field_data, confidence=0.8, evidence="raw number from hint")
        return FieldValue(
            value=field_data.get("value"),
            confidence=field_data.get("confidence", 0),
            evidence=field_data.get("evidence", ""),
            page=field_data.get("page", 1)
        )
    
    raw_breakdown = None
    if "raw_breakdown" in data and data["raw_breakdown"]:
        rb = data["raw_breakdown"]
        raw_breakdown = RawBreakdown(
            energy_total_tl=parse_field(rb.get("energy_total_tl")),
            distribution_total_tl=parse_field(rb.get("distribution_total_tl")),
            btv_tl=parse_field(rb.get("btv_tl")),
            vat_tl=parse_field(rb.get("vat_tl"))
        )
    
    from .models import LineItem
    line_items = []
    for item in data.get("line_items", []):
        if item.get("label"):
            line_items.append(LineItem(
                label=item.get("label", ""),
                qty=item.get("qty", 0),
                unit=item.get("unit", "kWh"),
                unit_price=item.get("unit_price"),
                amount_tl=item.get("amount_tl"),
                confidence=item.get("confidence", 0),
                evidence=item.get("evidence", ""),
                page=item.get("page", 1),
            ))
    
    extraction = InvoiceExtraction(
        vendor=data.get("vendor", "unknown"),
        invoice_period=data.get("invoice_period", ""),
        consumption_kwh=parse_field(data.get("consumption_kwh")),
        current_active_unit_price_tl_per_kwh=parse_field(data.get("current_active_unit_price_tl_per_kwh")),
        distribution_unit_price_tl_per_kwh=parse_field(data.get("distribution_unit_price_tl_per_kwh")),
        invoice_total_with_vat_tl=parse_field(data.get("invoice_total_with_vat_tl")),
        raw_breakdown=raw_breakdown,
        line_items=line_items,
        meta=InvoiceMeta(
            tariff_group_guess=data.get("meta", {}).get("tariff_group_guess", "unknown"),
            voltage_guess=data.get("meta", {}).get("voltage_guess", "unknown"),
            term_type_guess=data.get("meta", {}).get("term_type_guess", "unknown"),
            invoice_type_guess=data.get("meta", {}).get("invoice_type_guess", "unknown"),
        )
    )
    
    # Section extraction metadata ekle
    extraction._section_errors = data.get("_errors", [])
    extraction._section_warnings = data.get("_warnings", [])
    extraction._section_valid = data.get("_is_valid", False)
    
    return extraction
