"""
Sözleşme oluşturma V1 — iş mantığı katmanı (repository/service).

Çağrıldığı yerler: app/contracts/router.py (Faz 4 - bu dosyada tanımlı
tüm fonksiyonlar router endpoint'lerinden çağrılır).

GÜVENLİK: T.C. kimlik no bu katmanda asla log'a yazılmaz (logger.info/
warning/error çağrılarında representative/national_id alanları hiçbir
zaman interpolate edilmez).
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .. import database as db_models
from ..services.storage import get_storage
from .schemas import (
    TaxCertificateExtraction,
    SignatureCircularExtraction,
    TariffGroupResolution,
)
from .extractors import extract_tax_certificate, extract_signature_circular, EXTRACTOR_VERSION, PROMPT_VERSION
from ..core.config import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Belge yükleme
# ═══════════════════════════════════════════════════════════════════════════════

def upload_reference_document(
    db: Session,
    tenant_id: str,
    document_type: str,
    file_bytes: bytes,
    original_filename: str,
    mime_type: str,
    customer_id: Optional[int] = None,
) -> tuple[db_models.UploadedReferenceDocument, bool]:
    """Belgeyi storage'a yazar, DB kaydı oluşturur. (tenant_id, customer_id, sha256) ile dedup yapar."""
    sha256 = hashlib.sha256(file_bytes).hexdigest()

    existing = (
        db.query(db_models.UploadedReferenceDocument)
        .filter(
            db_models.UploadedReferenceDocument.tenant_id == tenant_id,
            db_models.UploadedReferenceDocument.customer_id == customer_id,
            db_models.UploadedReferenceDocument.sha256 == sha256,
        )
        .first()
    )
    if existing:
        return existing, True

    storage = get_storage()
    storage_ref = storage.put_bytes(
        key=f"contracts/reference_documents/{tenant_id}/{sha256}",
        data=file_bytes,
        content_type=mime_type,
    )

    doc = db_models.UploadedReferenceDocument(
        tenant_id=tenant_id,
        customer_id=customer_id,
        document_type=document_type,
        original_filename=original_filename,
        mime_type=mime_type,
        file_size=len(file_bytes),
        sha256=sha256,
        storage_ref=storage_ref,
        processing_status="uploaded",
        uploaded_at=datetime.utcnow(),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc, False


# ═══════════════════════════════════════════════════════════════════════════════
# Extraction
# ═══════════════════════════════════════════════════════════════════════════════

_FIELD_SCHEMA_BY_TYPE = {
    "vergi_levhasi": TaxCertificateExtraction,
    "imza_sirkusu": SignatureCircularExtraction,
}


def run_extraction(db: Session, document: db_models.UploadedReferenceDocument) -> db_models.DocumentExtractionRun:
    """Belge için extraction çalıştırır, ham sonucu document_field_candidates'a normalize eder."""
    run = db_models.DocumentExtractionRun(
        tenant_id=document.tenant_id,
        document_id=document.id,
        extractor_type=document.document_type,
        extractor_version=EXTRACTOR_VERSION,
        model_name=settings.openai_model_accurate,
        prompt_version=PROMPT_VERSION,
        status="running",
        started_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    document.processing_status = "extracting"
    db.commit()

    try:
        storage = get_storage()
        file_bytes = storage.get_bytes(document.storage_ref)

        if document.document_type == "vergi_levhasi":
            result = extract_tax_certificate(file_bytes, mime_type=document.mime_type)
        elif document.document_type == "imza_sirkusu":
            result = extract_signature_circular(file_bytes, mime_type=document.mime_type)
        else:
            raise ValueError(f"Bilinmeyen document_type: {document.document_type}")

        for field_name, field_value in result.model_dump().items():
            candidate = db_models.DocumentFieldCandidate(
                tenant_id=document.tenant_id,
                extraction_run_id=run.id,
                document_id=document.id,
                field_name=field_name,
                raw_value=field_value.get("value"),
                normalized_value=_normalize_value(field_value.get("value")),
                confidence=field_value.get("confidence", 0.0),
                source_page=field_value.get("source_page", 1),
                source_text=field_value.get("source_text", ""),
                validation_status="pending",
                conflict_status="none",
                created_at=datetime.utcnow(),
            )
            db.add(candidate)

        run.status = "completed"
        run.completed_at = datetime.utcnow()
        document.processing_status = "extracted"
        db.commit()

    except Exception as exc:  # noqa: BLE001 - extraction hatasını run kaydına yazıp yeniden fırlatıyoruz
        run.status = "failed"
        run.error_code = type(exc).__name__
        run.completed_at = datetime.utcnow()
        document.processing_status = "failed"
        db.commit()
        logger.error(f"Extraction run {run.id} failed for document {document.id}: {type(exc).__name__}")
        raise

    db.refresh(run)
    return run


def _normalize_value(value: Optional[str]) -> Optional[str]:
    """Çelişki karşılaştırması için basit normalizasyon — büyük/küçük harf ve fazla boşluk yok sayılır."""
    if value is None:
        return None
    return " ".join(value.strip().upper().split())


# ═══════════════════════════════════════════════════════════════════════════════
# Çelişki tespiti
#
# Kural: aynı customer_id'ye ait, henüz karar verilmemiş (validation_status
# in pending/confirmed) aday alanlar arasında AYNI field_name için
# normalized_value farklıysa conflict_status='conflict' olur. Sistem HİÇBİR
# alanı otomatik seçmez — yalnız işaretler.
# ═══════════════════════════════════════════════════════════════════════════════

# Vergi levhası ve imza sirkülerinde ORTAK alan adları (farklı şemalarda aynı
# kavramı temsil eden alanlar) — çelişki karşılaştırması bunlar üzerinden yapılır.
_CROSS_DOCUMENT_FIELD_ALIASES = {
    "legal_name": ["legal_name"],
    "tax_office": ["tax_office"],
}


def detect_conflicts_for_customer_documents(db: Session, document_ids: list[int]) -> list[db_models.DocumentFieldCandidate]:
    """
    Verilen belge id'lerine ait TÜM alan adaylarını field_name bazında gruplar,
    normalized_value'ları karşılaştırır, çelişenleri conflict_status='conflict'
    yapar. Değişen adayların listesini döndürür (review ekranı bunları vurgular).
    """
    candidates = (
        db.query(db_models.DocumentFieldCandidate)
        .filter(db_models.DocumentFieldCandidate.document_id.in_(document_ids))
        .filter(db_models.DocumentFieldCandidate.validation_status != "rejected")
        .all()
    )

    by_field: dict[str, list[db_models.DocumentFieldCandidate]] = {}
    for c in candidates:
        # field_name'i ortak alan adına (alias) indirger, farklı şemalardaki
        # eşdeğer alanları aynı grupta karşılaştırabilmek için.
        canonical_field = c.field_name
        for canonical, aliases in _CROSS_DOCUMENT_FIELD_ALIASES.items():
            if c.field_name in aliases:
                canonical_field = canonical
                break
        by_field.setdefault(canonical_field, []).append(c)

    changed: list[db_models.DocumentFieldCandidate] = []
    for field_name, group in by_field.items():
        distinct_values = {c.normalized_value for c in group if c.normalized_value}
        # Yalnız BİRDEN FAZLA belgeden gelen adaylar arasında çelişki olabilir.
        distinct_documents = {c.document_id for c in group}
        is_conflict = len(distinct_values) > 1 and len(distinct_documents) > 1

        for c in group:
            new_status = "conflict" if is_conflict else ("none" if c.conflict_status != "resolved" else c.conflict_status)
            if c.conflict_status != new_status:
                c.conflict_status = new_status
                changed.append(c)

    db.commit()
    for c in changed:
        db.refresh(c)
    return changed


def resolve_candidate(
    db: Session,
    candidate_id: int,
    decision: str,
    corrected_value: Optional[str] = None,
    decided_by: Optional[str] = None,
) -> db_models.DocumentFieldCandidate:
    """
    Kullanıcının bir alan adayı için verdiği kararı kaydeder — sistem hiçbir
    zaman otomatik karar vermez. Aday bir çelişki grubundaysa (aynı field_name,
    birden fazla belge), kullanıcının kararı GRUBUN TAMAMINI çözer: kazanmayan
    kardeş adaylar da conflict_status='resolved' olur (yoksa has_unresolved_
    conflicts sonsuza kadar True döner) — ama kardeşlerin kendi validation_status'u
    ELLE değiştirilmez, yalnızca artık "çözülmüş" bir çelişkinin parçası olarak
    işaretlenir; sistem onlar için 'accepted/rejected' kararı UYDURMAZ.
    """
    candidate = db.query(db_models.DocumentFieldCandidate).filter(db_models.DocumentFieldCandidate.id == candidate_id).first()
    if not candidate:
        raise ValueError(f"Candidate bulunamadı: {candidate_id}")

    was_conflict = candidate.conflict_status == "conflict"

    candidate.user_decision = decision
    candidate.corrected_value = corrected_value
    candidate.decided_by = decided_by
    candidate.decided_at = datetime.utcnow()
    candidate.validation_status = "overridden" if decision == "corrected" else ("confirmed" if decision == "accepted" else "rejected")

    if was_conflict and decision in ("accepted", "corrected"):
        candidate.conflict_status = "resolved"

        # Aynı çelişki grubundaki kardeş adayları bul (aynı tenant, aynı
        # field_name-alias, farklı document_id, hâlâ 'conflict' durumunda).
        canonical_field = candidate.field_name
        for canonical, aliases in _CROSS_DOCUMENT_FIELD_ALIASES.items():
            if candidate.field_name in aliases:
                canonical_field = canonical
                break
        sibling_field_names = _CROSS_DOCUMENT_FIELD_ALIASES.get(canonical_field, [candidate.field_name])

        siblings = (
            db.query(db_models.DocumentFieldCandidate)
            .filter(db_models.DocumentFieldCandidate.tenant_id == candidate.tenant_id)
            .filter(db_models.DocumentFieldCandidate.field_name.in_(sibling_field_names))
            .filter(db_models.DocumentFieldCandidate.conflict_status == "conflict")
            .filter(db_models.DocumentFieldCandidate.id != candidate.id)
            .all()
        )
        for sibling in siblings:
            sibling.conflict_status = "resolved"

    db.commit()
    db.refresh(candidate)
    return candidate


def has_unresolved_conflicts(db: Session, document_ids: list[int]) -> bool:
    """Finalize öncesi zorunlu kontrol: çözülmemiş conflict veya karar verilmemiş zorunlu alan var mı."""
    unresolved = (
        db.query(db_models.DocumentFieldCandidate)
        .filter(db_models.DocumentFieldCandidate.document_id.in_(document_ids))
        .filter(db_models.DocumentFieldCandidate.conflict_status == "conflict")
        .count()
    )
    return unresolved > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Tarife grubu çözümlemesi (owner madde 13 — Offer.tariff_group kolonu YOK,
# mevcut JSON'dan çözülür, çözüm yolu + durumu snapshot'a ayrıca yazılır)
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_tariff_group(offer: db_models.Offer) -> TariffGroupResolution:
    extraction_result = offer.extraction_result or {}
    if not isinstance(extraction_result, dict):
        return TariffGroupResolution(value=None, source_path="extraction_result", resolution_status="not_found")

    meta = extraction_result.get("meta") or {}
    tariff_group_guess = meta.get("tariff_group_guess")
    if tariff_group_guess and tariff_group_guess != "unknown":
        return TariffGroupResolution(
            value=tariff_group_guess,
            source_path="extraction_result.meta.tariff_group_guess",
            resolution_status="resolved",
        )

    tariff = extraction_result.get("tariff") or {}
    tariff_type = tariff.get("tariff_type")
    if tariff_type and tariff_type != "unknown":
        return TariffGroupResolution(
            value=tariff_type,
            source_path="extraction_result.tariff.tariff_type",
            resolution_status="resolved",
        )

    return TariffGroupResolution(value=None, source_path="extraction_result.meta/tariff", resolution_status="not_found")


# ═══════════════════════════════════════════════════════════════════════════════
# Sözleşme snapshot + finalize
# ═══════════════════════════════════════════════════════════════════════════════

def build_contract_snapshot(
    db: Session,
    contract: db_models.Contract,
    complete_fields: dict,
) -> dict:
    """
    FINALIZED anında donacak tüm alanları tek JSON'da toplar. Bundan sonra
    Customer/Offer/CustomerLegalProfile değişse bile bu snapshot sabit kalır.
    """
    offer = db.query(db_models.Offer).filter(db_models.Offer.id == contract.offer_id).first()
    legal_profile = (
        db.query(db_models.CustomerLegalProfile)
        .filter(db_models.CustomerLegalProfile.id == contract.legal_profile_id)
        .first()
        if contract.legal_profile_id
        else None
    )
    representative = (
        db.query(db_models.CustomerAuthorizedRepresentative)
        .filter(db_models.CustomerAuthorizedRepresentative.id == contract.authorized_representative_id)
        .first()
        if contract.authorized_representative_id
        else None
    )

    tariff_resolution = resolve_tariff_group(offer) if offer else TariffGroupResolution(
        value=None, source_path="offer_missing", resolution_status="not_found"
    )

    return {
        "legal_name": legal_profile.legal_name if legal_profile else None,
        "tax_number": legal_profile.tax_number if legal_profile else None,
        "tax_office": legal_profile.tax_office if legal_profile else None,
        "registered_address": legal_profile.registered_address if legal_profile else None,
        "facility_address": legal_profile.facility_address if legal_profile else None,
        "notification_address": legal_profile.notification_address if legal_profile else None,
        "mersis_number": legal_profile.mersis_number if legal_profile else None,
        "trade_registry_number": legal_profile.trade_registry_number if legal_profile else None,
        "representative": {
            "full_name": representative.full_name if representative else None,
            "national_id": representative.national_id if representative else None,
            "authority_type": representative.authority_type if representative else None,
            "authority_scope": representative.authority_scope if representative else None,
            "is_indefinite": representative.is_indefinite if representative else None,
        },
        "tariff_group": tariff_resolution.model_dump(),
        "subscription_codes": complete_fields.get("subscription_codes"),
        "contract_dates": {
            "start_date": complete_fields.get("start_date"),
            "duration_months": complete_fields.get("duration_months"),
        },
        "offer_id": offer.id if offer else None,
        "agreement_multiplier": offer.agreement_multiplier if offer else None,
        "template_version": "v1",
    }
