"""
Sözleşme oluşturma V1 — API endpoint'leri.

Çağrıldığı yerler: app/main.py (app.include_router(contracts_router)).

Auth: mevcut pricing router'daki (_require_pricing_key) desenle aynı basit
header-key kontrolü. Tenant izolasyonu: app/services/tenant.get_tenant_id
(fail-closed — settings.tenant_required=True ise header zorunlu).
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import database as db_models
from ..database import get_db
from ..services.tenant import get_tenant_id
from ..core.config import settings
from . import service
from .schemas import (
    ConflictResolutionRequest,
    ContractCompleteFieldsRequest,
    ContractDraftCreateRequest,
    ContractFinalizeResponse,
    ContractOut,
    ContractPreviewResponse,
    DocumentUploadResponse,
    ExtractionResultOut,
    ExtractionStartResponse,
    FieldCandidateOut,
    LegalProfileOut,
    LegalProfileSaveRequest,
    RepresentativeDetailOut,
    RepresentativeSaveRequest,
)

logger = logging.getLogger(__name__)

contracts_router = APIRouter(prefix="/api/contracts", tags=["contracts"])

_ALLOWED_DOCUMENT_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
_MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024  # mevcut validate_uploaded_file ile aynı sınır


def _require_contracts_key(x_api_key: Optional[str] = Header(default=None)) -> Optional[str]:
    if not settings.api_key_enabled:
        return None
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail={"error": "unauthorized", "message": "Geçersiz API anahtarı"})
    return x_api_key


def _candidate_to_out(c: db_models.DocumentFieldCandidate) -> FieldCandidateOut:
    return FieldCandidateOut(
        id=c.id,
        field_name=c.field_name,
        document_id=c.document_id,
        source_document=c.document.document_type if c.document else "vergi_levhasi",
        raw_value=c.raw_value,
        normalized_value=c.normalized_value,
        confidence=c.confidence,
        source_page=c.source_page,
        source_text=c.source_text,
        validation_status=c.validation_status,
        conflict_status=c.conflict_status,
        user_decision=c.user_decision,
        corrected_value=c.corrected_value,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Belge yükleme + extraction
# ═══════════════════════════════════════════════════════════════════════════════

@contracts_router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    document_type: str,
    customer_id: Optional[int] = None,
    file: UploadFile = File(...),
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """
    Çağrıldığı yerler:
    - Desktop UI → Sözleşme Hazırla → belge yükleme ekranı (Faz 8'de eklenecek)
    """
    if document_type not in ("vergi_levhasi", "imza_sirkusu"):
        raise HTTPException(status_code=422, detail={"error": "invalid_document_type", "message": "document_type 'vergi_levhasi' veya 'imza_sirkusu' olmalı"})

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=422, detail={"error": "empty_file", "message": "Dosya boş"})
    if len(file_bytes) > _MAX_DOCUMENT_SIZE_BYTES:
        raise HTTPException(status_code=422, detail={"error": "file_too_large", "message": "Dosya 10 MB sınırını aşıyor"})
    if file.content_type not in _ALLOWED_DOCUMENT_MIME_TYPES:
        raise HTTPException(status_code=422, detail={"error": "unsupported_file_type", "message": f"Desteklenmeyen dosya tipi: {file.content_type}"})

    doc, is_duplicate = service.upload_reference_document(
        db=db,
        tenant_id=tenant_id,
        document_type=document_type,
        file_bytes=file_bytes,
        original_filename=file.filename or "unknown",
        mime_type=file.content_type,
        customer_id=customer_id,
    )
    return DocumentUploadResponse(
        document_id=doc.id,
        document_type=doc.document_type,
        processing_status=doc.processing_status,
        sha256=doc.sha256,
        is_duplicate=is_duplicate,
    )


@contracts_router.post("/documents/{document_id}/extract", response_model=ExtractionStartResponse)
def start_extraction(
    document_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    document = (
        db.query(db_models.UploadedReferenceDocument)
        .filter(db_models.UploadedReferenceDocument.id == document_id, db_models.UploadedReferenceDocument.tenant_id == tenant_id)
        .first()
    )
    if not document:
        raise HTTPException(status_code=404, detail={"error": "document_not_found", "message": "Belge bulunamadı"})

    try:
        run = service.run_extraction(db, document)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail={"error": "extraction_failed", "message": "Belge işlenemedi, lütfen tekrar deneyin"}) from exc

    return ExtractionStartResponse(extraction_run_id=run.id, document_id=document.id, status=run.status)


@contracts_router.get("/documents/{document_id}/extraction-result", response_model=ExtractionResultOut)
def get_extraction_result(
    document_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    document = (
        db.query(db_models.UploadedReferenceDocument)
        .filter(db_models.UploadedReferenceDocument.id == document_id, db_models.UploadedReferenceDocument.tenant_id == tenant_id)
        .first()
    )
    if not document:
        raise HTTPException(status_code=404, detail={"error": "document_not_found", "message": "Belge bulunamadı"})

    run = (
        db.query(db_models.DocumentExtractionRun)
        .filter(db_models.DocumentExtractionRun.document_id == document_id)
        .order_by(db_models.DocumentExtractionRun.id.desc())
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail={"error": "extraction_not_started", "message": "Bu belge için extraction başlatılmamış"})

    candidates = (
        db.query(db_models.DocumentFieldCandidate)
        .filter(db_models.DocumentFieldCandidate.extraction_run_id == run.id)
        .all()
    )
    for c in candidates:
        c.document = document  # response'ta source_document için, ekstra sorgu yapmadan

    return ExtractionResultOut(
        extraction_run_id=run.id,
        document_id=document.id,
        document_type=document.document_type,
        status=run.status,
        error_code=run.error_code,
        candidates=[_candidate_to_out(c) for c in candidates],
    )


@contracts_router.post("/candidates/{candidate_id}/resolve", response_model=FieldCandidateOut)
def resolve_field_candidate(
    candidate_id: int,
    request: ConflictResolutionRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """
    Çağrıldığı yerler:
    - Desktop UI → Sözleşme Hazırla → çelişki/alan onay ekranı (Faz 8'de eklenecek)
    """
    candidate = db.query(db_models.DocumentFieldCandidate).filter(
        db_models.DocumentFieldCandidate.id == candidate_id, db_models.DocumentFieldCandidate.tenant_id == tenant_id
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail={"error": "candidate_not_found", "message": "Alan adayı bulunamadı"})

    # DocumentFieldCandidate'da document için gerçek bir relationship yok
    # (yalnız document_id FK var) — source_document response'ta gösterilebilsin
    # diye belgeyi burada ayrıca sorgularız (get_extraction_result'taki desenle aynı).
    document = db.query(db_models.UploadedReferenceDocument).filter(
        db_models.UploadedReferenceDocument.id == candidate.document_id
    ).first()

    updated = service.resolve_candidate(
        db, candidate_id=candidate_id, decision=request.decision, corrected_value=request.corrected_value, decided_by=request.decided_by
    )
    updated.document = document
    return _candidate_to_out(updated)


# ═══════════════════════════════════════════════════════════════════════════════
# Tüzel kişilik / yetkili kaydı
# ═══════════════════════════════════════════════════════════════════════════════

@contracts_router.post("/legal-profiles", response_model=LegalProfileOut)
def save_legal_profile(
    request: LegalProfileSaveRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    profile = db_models.CustomerLegalProfile(
        tenant_id=tenant_id,
        customer_id=request.customer_id,
        legal_name=request.legal_name,
        tax_number=request.tax_number,
        tax_office=request.tax_office,
        mersis_number=request.mersis_number,
        trade_registry_number=request.trade_registry_number,
        registered_address=request.registered_address,
        facility_address=request.facility_address,
        notification_address=request.notification_address,
        verification_status="confirmed",  # yalnız kullanıcı onayından sonra buraya kaydedilir
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return LegalProfileOut(
        id=profile.id, customer_id=profile.customer_id, legal_name=profile.legal_name, tax_number=profile.tax_number,
        tax_office=profile.tax_office, mersis_number=profile.mersis_number, trade_registry_number=profile.trade_registry_number,
        registered_address=profile.registered_address, facility_address=profile.facility_address,
        notification_address=profile.notification_address, verification_status=profile.verification_status,
    )


@contracts_router.post("/representatives", response_model=RepresentativeDetailOut)
def save_representative(
    request: RepresentativeSaveRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    rep = db_models.CustomerAuthorizedRepresentative(
        tenant_id=tenant_id,
        customer_id=request.customer_id,
        legal_profile_id=request.legal_profile_id,
        full_name=request.full_name,
        national_id=request.national_id,
        authority_type=request.authority_type,
        authority_scope=request.authority_scope,
        authority_start_date=datetime.fromisoformat(request.authority_start_date) if request.authority_start_date else None,
        authority_end_date=datetime.fromisoformat(request.authority_end_date) if request.authority_end_date else None,
        is_indefinite=request.is_indefinite,
        source_document_id=request.source_document_id,
        verification_status="confirmed",
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    return RepresentativeDetailOut(
        id=rep.id, full_name=rep.full_name, authority_type=rep.authority_type, is_indefinite=rep.is_indefinite,
        verification_status=rep.verification_status, national_id=rep.national_id, authority_scope=rep.authority_scope,
        authority_start_date=rep.authority_start_date.isoformat() if rep.authority_start_date else None,
        authority_end_date=rep.authority_end_date.isoformat() if rep.authority_end_date else None,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Sözleşme taslağı / önizleme / finalize
# ═══════════════════════════════════════════════════════════════════════════════

@contracts_router.post("/drafts", response_model=ContractOut)
def create_contract_draft(
    request: ContractDraftCreateRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    offer = db.query(db_models.Offer).filter(db_models.Offer.id == request.offer_id).first()
    if not offer:
        raise HTTPException(status_code=404, detail={"error": "offer_not_found", "message": "Teklif bulunamadı"})

    contract = db_models.Contract(
        tenant_id=tenant_id,
        customer_id=request.customer_id,
        offer_id=request.offer_id,
        legal_profile_id=request.legal_profile_id,
        authorized_representative_id=request.authorized_representative_id,
        status="DRAFT",
        created_at=datetime.utcnow(),
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)
    return ContractOut(
        id=contract.id, customer_id=contract.customer_id, offer_id=contract.offer_id, contract_number=contract.contract_number,
        status=contract.status, start_date=None, end_date=None, created_at=contract.created_at.isoformat(),
    )


@contracts_router.post("/{contract_id}/preview", response_model=ContractPreviewResponse)
def preview_contract(
    contract_id: int,
    request: ContractCompleteFieldsRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    contract = db.query(db_models.Contract).filter(db_models.Contract.id == contract_id, db_models.Contract.tenant_id == tenant_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail={"error": "contract_not_found", "message": "Sözleşme bulunamadı"})
    if contract.status == "FINALIZED":
        raise HTTPException(status_code=409, detail={"error": "contract_finalized", "message": "Finalize edilmiş sözleşme değiştirilemez"})

    from . import pdf_service  # Faz 7'de eklenecek — döngüsel import'tan kaçınmak için burada import edilir

    complete_fields = request.model_dump()
    snapshot = service.build_contract_snapshot(db, contract, complete_fields)
    contract.extraction_snapshot_json = snapshot
    contract.status = "READY_TO_GENERATE"
    db.commit()

    sections = pdf_service.render_contract_sections(snapshot)
    return ContractPreviewResponse(contract_id=contract.id, status=contract.status, rendered_html_sections=sections)


@contracts_router.post("/{contract_id}/finalize", response_model=ContractFinalizeResponse)
def finalize_contract(
    contract_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    contract = db.query(db_models.Contract).filter(db_models.Contract.id == contract_id, db_models.Contract.tenant_id == tenant_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail={"error": "contract_not_found", "message": "Sözleşme bulunamadı"})
    if contract.status == "FINALIZED":
        raise HTTPException(status_code=409, detail={"error": "already_finalized", "message": "Sözleşme zaten finalize edilmiş"})
    if not contract.extraction_snapshot_json:
        raise HTTPException(status_code=409, detail={"error": "preview_required", "message": "Önce önizleme yapılmalı"})

    # Belgeler arası çözülmemiş çelişki varsa finalize engellenir.
    documents = (
        db.query(db_models.UploadedReferenceDocument.id)
        .filter(db_models.UploadedReferenceDocument.customer_id == contract.customer_id)
        .all()
    )
    document_ids = [d.id for d in documents]
    if document_ids and service.has_unresolved_conflicts(db, document_ids):
        raise HTTPException(status_code=409, detail={"error": "unresolved_conflicts", "message": "Çözülmemiş belge çelişkileri var — finalize edilemez"})

    from . import pdf_service

    pdf_bytes, pdf_sha256 = pdf_service.generate_contract_pdf(contract.extraction_snapshot_json)

    from ..services.storage import get_storage
    storage = get_storage()
    pdf_ref = storage.put_bytes(
        key=f"contracts/generated/{tenant_id}/{contract.id}.pdf", data=pdf_bytes, content_type="application/pdf"
    )

    contract.contract_snapshot_json = contract.extraction_snapshot_json
    contract.pdf_storage_ref = pdf_ref
    contract.pdf_sha256 = pdf_sha256
    contract.status = "FINALIZED"
    contract.finalized_at = datetime.utcnow()
    contract.template_version = "v1"
    db.commit()

    return ContractFinalizeResponse(
        contract_id=contract.id, status=contract.status, pdf_storage_ref=pdf_ref, pdf_sha256=pdf_sha256,
        contract_number=contract.contract_number,
    )


@contracts_router.get("/{contract_id}", response_model=ContractOut)
def get_contract(
    contract_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    contract = db.query(db_models.Contract).filter(db_models.Contract.id == contract_id, db_models.Contract.tenant_id == tenant_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail={"error": "contract_not_found", "message": "Sözleşme bulunamadı"})
    return ContractOut(
        id=contract.id, customer_id=contract.customer_id, offer_id=contract.offer_id, contract_number=contract.contract_number,
        status=contract.status, start_date=contract.start_date.isoformat() if contract.start_date else None,
        end_date=contract.end_date.isoformat() if contract.end_date else None, created_at=contract.created_at.isoformat(),
    )
