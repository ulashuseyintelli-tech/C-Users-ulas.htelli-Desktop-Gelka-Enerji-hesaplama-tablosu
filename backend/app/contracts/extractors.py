"""
Sözleşme oluşturma V1 — vergi levhası ve imza sirküleri extractor'ları.

Mevcut fatura-OCR pipeline'ının (app/extractor.py) GENEL, belge-tipinden
bağımsız kısımlarını (base64 encode, görsel optimize, OpenAI retry/JSON-repair)
yeniden kullanır. Fatura-spesifik modüllere (region_extractor, supplier_profiles,
canonical_extractor) HİÇ dokunulmaz, hiçbiri buradan çağrılmaz.

Çağrıldığı yerler:
- app/contracts/router.py POST /api/contracts/documents/{document_id}/extract
"""
import logging
import os
from pathlib import Path

from .schemas import (
    DocumentFieldValue,
    TaxCertificateExtraction,
    SignatureCircularExtraction,
)
from ..extractor import _call_openai_with_retry, encode_image, _optimize_image_size
from ..core.config import settings

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "v1"
PROMPT_VERSION = "v1"

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8")


_TAX_CERTIFICATE_PROMPT = _load_prompt("tax_certificate_extraction_v1.txt")
_SIGNATURE_CIRCULAR_PROMPT = _load_prompt("signature_circular_extraction_v1.txt")


def _parse_field(raw: dict | None, source_document: str) -> DocumentFieldValue:
    """Ham JSON alanını (value/confidence/source_page/source_text) DocumentFieldValue'ya çevirir."""
    if not raw:
        return DocumentFieldValue(value=None, confidence=0.0, source_document=source_document)
    return DocumentFieldValue(
        value=raw.get("value"),
        confidence=float(raw.get("confidence") or 0.0),
        source_document=source_document,
        source_page=int(raw.get("source_page") or 1),
        source_text=raw.get("source_text") or "",
    )


def _call_vision_extraction(image_bytes: bytes, mime_type: str, prompt: str) -> dict:
    """
    Fatura-extractor'ının (app/extractor.py) genel Vision-çağrı iskeletini
    yeniden kullanır — yalnız prompt/model değişir, invoice-spesifik hiçbir
    şey yok.
    """
    image_bytes = _optimize_image_size(image_bytes, max_size=1200)
    base64_image = encode_image(image_bytes)

    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_image}",
                        "detail": "auto",
                    },
                }
            ],
        },
    ]

    return _call_openai_with_retry(
        messages=messages,
        response_format={"type": "json_object"},
        max_tokens=2000,
        temperature=0.1,
        model=settings.openai_model_accurate,  # hukuki belge — hız yerine doğruluk öncelikli
    )


def extract_tax_certificate(image_bytes: bytes, mime_type: str = "image/png") -> TaxCertificateExtraction:
    """Vergi levhasından yapılandırılmış veri çıkarır."""
    data = _call_vision_extraction(image_bytes, mime_type, _TAX_CERTIFICATE_PROMPT)
    src = "vergi_levhasi"
    return TaxCertificateExtraction(
        legal_name=_parse_field(data.get("legal_name"), src),
        tax_number=_parse_field(data.get("tax_number"), src),
        tax_office=_parse_field(data.get("tax_office"), src),
        facility_address=_parse_field(data.get("facility_address"), src),
        activity_code=_parse_field(data.get("activity_code"), src),
        establishment_date=_parse_field(data.get("establishment_date"), src),
    )


def extract_signature_circular(image_bytes: bytes, mime_type: str = "image/png") -> SignatureCircularExtraction:
    """İmza sirkülerinden yapılandırılmış veri çıkarır."""
    data = _call_vision_extraction(image_bytes, mime_type, _SIGNATURE_CIRCULAR_PROMPT)
    src = "imza_sirkusu"
    return SignatureCircularExtraction(
        legal_name=_parse_field(data.get("legal_name"), src),
        registered_address=_parse_field(data.get("registered_address"), src),
        representative_full_name=_parse_field(data.get("representative_full_name"), src),
        representative_national_id=_parse_field(data.get("representative_national_id"), src),
        authority_type=_parse_field(data.get("authority_type"), src),
        authority_scope=_parse_field(data.get("authority_scope"), src),
        authority_start_date=_parse_field(data.get("authority_start_date"), src),
        authority_end_date=_parse_field(data.get("authority_end_date"), src),
        is_indefinite=_parse_field(data.get("is_indefinite"), src),
        trade_registry_number=_parse_field(data.get("trade_registry_number"), src),
        mersis_number=_parse_field(data.get("mersis_number"), src),
        tax_office=_parse_field(data.get("tax_office"), src),
        notary_name=_parse_field(data.get("notary_name"), src),
        notary_date=_parse_field(data.get("notary_date"), src),
        notary_document_number=_parse_field(data.get("notary_document_number"), src),
    )
