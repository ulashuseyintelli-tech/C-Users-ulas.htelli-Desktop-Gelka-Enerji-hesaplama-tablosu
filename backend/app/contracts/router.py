"""
Sözleşme oluşturma V1 — API endpoint'leri.

Çağrıldığı yerler: app/main.py (app.include_router(contracts_router)).

Auth: mevcut pricing router'daki (_require_pricing_key) desenle aynı basit
header-key kontrolü. Tenant izolasyonu: app/services/tenant.get_tenant_id
(fail-closed — settings.tenant_required=True ise header zorunlu).
"""
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from .. import database as db_models
from ..database import get_db
from ..services.tenant import get_tenant_id
from ..core.config import settings
from . import service
from .schemas import (
    ConflictDetectionRequest,
    ConflictDetectionResponse,
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

_ALLOWED_DOCUMENT_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png"}
_MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024  # mevcut validate_uploaded_file ile aynı sınır


def _require_default_tenant_boundary(tenant_id: str = Depends(get_tenant_id)) -> str:
    """
    OWNER KARARI (final read-only architecture review, madde 3): Customer
    tablosunda tenant_id YOK (CustomerLegalProfile/UploadedReferenceDocument/
    Contract'ın customer_id üzerinden paylaştığı, tenant'sız bir havuz).
    Büyük bir Customer migration'ı olmadan bu havuzu güvenli şekilde
    tenant'lara bölmenin yolu yok.

    Bu yüzden V1'de sözleşme modülü YALNIZ default tenant ile çalışır —
    explicit, fail-closed bir invariant olarak. Başka bir X-Tenant-Id ile
    gelen HERHANGİ bir sözleşme isteği (belge yükleme, taslak, finalize, ...)
    403 ile reddedilir; sessizce "default"a düşürülmez ve tenant'sız
    Customer/Document havuzuna asla karışmaz.

    Bu, ..services.tenant.get_tenant_id'nin (settings.tenant_required=False
    iken header yoksa "default" döndüren, fail-open) genel davranışını
    DEĞİŞTİRMEZ — yalnız sözleşme router'ına ek, daha sıkı bir kapı ekler.
    """
    if tenant_id != settings.default_tenant:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "tenant_not_supported",
                "message": (
                    "Sözleşme modülü V1 yalnız varsayılan tenant ile çalışır "
                    "(Customer tablosunda tenant izolasyonu yok)."
                ),
            },
        )
    return tenant_id


contracts_router = APIRouter(
    prefix="/api/contracts",
    tags=["contracts"],
    dependencies=[Depends(_require_default_tenant_boundary)],
)


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


@contracts_router.post("/documents/detect-conflicts", response_model=ConflictDetectionResponse)
def detect_conflicts(
    request: ConflictDetectionRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """
    Verilen belgeler arasında (aynı field_name, farklı normalized_value)
    çelişki tespiti tetikler. service.detect_conflicts_for_customer_documents
    Faz 4'te yazılmış ama hiçbir endpoint'ten çağrılmıyordu — bu endpoint onu
    UI'a açar (belge review ekranı bu olmadan hiç çelişki göremezdi).

    Çağrıldığı yerler:
    - Desktop UI → Sözleşme Hazırla → her iki belge de extract edildikten
      sonra otomatik (Faz 8'de eklenecek)
    """
    documents = (
        db.query(db_models.UploadedReferenceDocument)
        .filter(
            db_models.UploadedReferenceDocument.id.in_(request.document_ids),
            db_models.UploadedReferenceDocument.tenant_id == tenant_id,
        )
        .all()
    )
    found_ids = {d.id for d in documents}
    missing = set(request.document_ids) - found_ids
    if missing:
        raise HTTPException(
            status_code=404,
            detail={"error": "document_not_found", "message": f"Belge(ler) bulunamadı: {sorted(missing)}"},
        )

    changed = service.detect_conflicts_for_customer_documents(db, request.document_ids)
    documents_by_id = {d.id: d for d in documents}
    for c in changed:
        c.document = documents_by_id.get(c.document_id)

    return ConflictDetectionResponse(changed_candidates=[_candidate_to_out(c) for c in changed])


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

    service.mark_offer_contracting(db, offer)

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
    if contract.status in ("FINALIZED", "FINALIZING"):
        # FINALIZING: HIGH#1 — bir finalize isteği tam bu anda işlemde;
        # preview'ın status'u READY_TO_GENERATE'e geri almasına izin
        # vermek finalize'ın final CAS'ını (status=='FINALIZING' beklentisi)
        # bozar. FINALIZED zaten immutable.
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
    """
    HIGH#1 (final architecture review) — eşzamanlılık/idempotency:
    - Zaten FINALIZED ise: yeniden PDF ÜRETMEZ, mevcut sonucu idempotent
      olarak döndürür (retry/response-loss güvenli).
    - CAS claim (service.try_claim_contract_for_finalize) yalnız TEK bir
      eşzamanlı isteğin PDF üretimine başlamasını sağlar; kaybeden istek
      hemen (PDF üretmeden) ya idempotent sonucu ya da 409 döner.
    - HIGH#2: mark_offer_completed artık ASLA exception fırlatmaz — bu
      endpoint'in başarı yanıtı offer-lifecycle yan etkisine bağımlı değil.
    """
    contract = db.query(db_models.Contract).filter(db_models.Contract.id == contract_id, db_models.Contract.tenant_id == tenant_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail={"error": "contract_not_found", "message": "Sözleşme bulunamadı"})

    def _idempotent_response(c: db_models.Contract) -> ContractFinalizeResponse:
        return ContractFinalizeResponse(
            contract_id=c.id, status=c.status, pdf_storage_ref=c.pdf_storage_ref, pdf_sha256=c.pdf_sha256,
            contract_number=c.contract_number,
        )

    if contract.status == "FINALIZED":
        return _idempotent_response(contract)
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

    claimed = service.try_claim_contract_for_finalize(db, contract_id, tenant_id)
    if not claimed:
        db.refresh(contract)
        if contract.status == "FINALIZED":
            return _idempotent_response(contract)
        raise HTTPException(
            status_code=409,
            detail={"error": "finalize_in_progress", "message": "Sözleşme şu anda başka bir istek tarafından finalize ediliyor"},
        )

    try:
        pdf_ref, pdf_sha256 = service.finalize_contract_pdf_and_commit(
            db, contract_id, tenant_id, contract.extraction_snapshot_json
        )
    except Exception as exc:  # noqa: BLE001 — service katmanı zaten revert+cleanup yaptı, burada yalnız 500'e çevir
        logger.error(f"Contract {contract_id} finalize başarısız: {type(exc).__name__}")
        raise HTTPException(
            status_code=500, detail={"error": "finalize_failed", "message": "Sözleşme finalize edilemedi, lütfen tekrar deneyin"}
        ) from exc

    db.refresh(contract)
    offer = db.query(db_models.Offer).filter(db_models.Offer.id == contract.offer_id).first()
    service.mark_offer_completed(db, offer)  # HIGH#2: best-effort, asla exception fırlatmaz

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


@contracts_router.get("/{contract_id}/download")
def download_contract_pdf(
    contract_id: int,
    expires: int = Query(default=300, ge=60, le=3600, description="Presigned URL geçerlilik süresi (saniye)"),
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """
    Finalize edilmiş sözleşme PDF'ini indir (download_offer_pdf, main.py ile
    aynı desen: S3 → presigned URL, local → dosya stream).

    Çağrıldığı yerler:
    - Desktop UI → Sözleşme Hazırla → finalize sonrası indirme adımı (Faz 8'de eklenecek)
    """
    contract = db.query(db_models.Contract).filter(
        db_models.Contract.id == contract_id, db_models.Contract.tenant_id == tenant_id
    ).first()
    if not contract:
        raise HTTPException(status_code=404, detail={"error": "contract_not_found", "message": "Sözleşme bulunamadı"})
    if not contract.pdf_storage_ref:
        raise HTTPException(
            status_code=404,
            detail={"error": "pdf_not_generated", "message": "PDF henüz oluşturulmamış. Önce finalize edin."},
        )

    ref = contract.pdf_storage_ref
    filename = f"sozlesme_{contract.id}.pdf"
    content_type = "application/pdf"

    from ..services.storage import get_storage
    from ..services.storage_local import LocalStorage
    storage = get_storage()

    presigned_url = storage.get_presigned_url(ref, expires_in=expires)
    if presigned_url:
        return JSONResponse({
            "type": "presigned_url",
            "url": presigned_url,
            "expires_seconds": expires,
            "filename": filename,
            "content_type": content_type,
        })

    if isinstance(storage, LocalStorage):
        try:
            local_path = storage.resolve_local_path(ref)
        except ValueError as e:
            logger.error(f"Path traversal attempt on contract {contract_id}: {ref}")
            raise HTTPException(status_code=400, detail={"error": "invalid_ref", "message": str(e)})

        if not os.path.exists(local_path):
            raise HTTPException(status_code=404, detail={"error": "file_missing", "message": "Sözleşme PDF dosyası bulunamadı"})

        return FileResponse(path=local_path, filename=filename, media_type=content_type)

    if os.path.exists(ref):
        return FileResponse(path=ref, filename=filename, media_type=content_type)

    raise HTTPException(status_code=404, detail={"error": "file_missing", "message": "Sözleşme PDF dosyası bulunamadı"})
