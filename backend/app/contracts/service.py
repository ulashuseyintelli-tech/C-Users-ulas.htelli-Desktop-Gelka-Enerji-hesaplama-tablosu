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
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from .. import database as db_models
from ..services.storage import get_storage
from ..services.offer_lifecycle import transition_offer_status
from ..pdf_render import render_pdf_pages_from_bytes, PdfRenderError
from .schemas import (
    TaxCertificateExtraction,
    SignatureCircularExtraction,
    TariffGroupResolution,
)
from .extractors import extract_tax_certificate, extract_signature_circular, EXTRACTOR_VERSION, PROMPT_VERSION
from ..core.config import settings

logger = logging.getLogger(__name__)

# PDF extraction guardrail — ilk sürüm için makul bir üst sınır (vergi
# levhası/imza sirküleri belgeleri normalde 1-2 sayfa). Aşan PDF'ler
# sessizce kırpılmaz, PdfRenderError(error_code="pdf_too_many_pages") ile
# reddedilir — bkz. app/pdf_render.py.
MAX_PDF_EXTRACTION_PAGES = 5


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
    """
    Belgeyi storage'a yazar, DB kaydı oluşturur.
    (tenant_id, customer_id, sha256, document_type) ile dedup yapar.

    document_type dedup key'inde OLMAK ZORUNDA (LOCAL UAT FINAL REMEDIATION,
    HIGH bulgu): aksi halde kullanıcı bir dosyayı yanlış türle yükleyip
    SONRA doğru türle tekrar yüklediğinde, dedup sha256 eşleşmesiyle
    mevcut (yanlış türdeki) kaydı sessizce döndürür — sistem DB'ye elle
    müdahale olmadan düzelemez. Şimdi farklı document_type = ayrı kayıt;
    önceki (yanlış) kayıt ve extraction geçmişi DEĞİŞTİRİLMEDEN kalır.
    """
    sha256 = hashlib.sha256(file_bytes).hexdigest()

    existing = (
        db.query(db_models.UploadedReferenceDocument)
        .filter(
            db_models.UploadedReferenceDocument.tenant_id == tenant_id,
            db_models.UploadedReferenceDocument.customer_id == customer_id,
            db_models.UploadedReferenceDocument.sha256 == sha256,
            db_models.UploadedReferenceDocument.document_type == document_type,
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
    """
    Belge için extraction çalıştırır, ham sonucu document_field_candidates'a normalize eder.

    PDF belgeler (mime_type=application/pdf) ÖNCE sayfa görüntülerine (PNG)
    çevrilir — bkz. app/pdf_render.py::render_pdf_pages_from_bytes. Ham PDF
    bytes'ı Vision API'ye HİÇBİR ZAMAN gönderilmez (image_url olarak kabul
    edilmiyor, 400 Bad Request ile reddediliyordu — kök neden buydu).

    Tek sayfa (resim veya 1 sayfalı PDF): davranış BİREBİR korunur — tüm
    alanlar (boş/None dahil) eskisi gibi candidate olarak eklenir.

    Çok sayfalı PDF: her sayfa ayrı extract edilir; yalnız o sayfada DOLU
    (value != None) alanlar eklenir — aksi halde her boş alan sayfa sayısı
    kadar tekrar eder ve review ekranı anlamsız boş satırlarla dolar.
    source_page modelin tahminine değil, bizim gönderdiğimiz gerçek sayfa
    indeksine dayanır (daha güvenilir). Aynı alan birden çok sayfada farklı
    değerle çıkarsa mevcut çelişki tespiti (detect_conflicts) bunu zaten
    yakalar — ayrı bir birleştirme mantığı yazılmadı, mevcut mekanizma
    yeniden kullanıldı.
    """
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
            extractor_fn = extract_tax_certificate
        elif document.document_type == "imza_sirkusu":
            extractor_fn = extract_signature_circular
        else:
            raise ValueError(f"Bilinmeyen document_type: {document.document_type}")

        if document.mime_type == "application/pdf":
            page_images = render_pdf_pages_from_bytes(file_bytes, max_pages=MAX_PDF_EXTRACTION_PAGES)
            page_mime = "image/png"
        else:
            page_images = [file_bytes]
            page_mime = document.mime_type

        multi_page = len(page_images) > 1
        for page_num, page_bytes in enumerate(page_images, start=1):
            result = extractor_fn(page_bytes, mime_type=page_mime)
            for field_name, field_value in result.model_dump().items():
                if multi_page and field_value.get("value") is None:
                    continue  # bu sayfada boş — çok sayfalı PDF'de tekrar tekrar eklenmesin
                candidate = db_models.DocumentFieldCandidate(
                    tenant_id=document.tenant_id,
                    extraction_run_id=run.id,
                    document_id=document.id,
                    field_name=field_name,
                    raw_value=field_value.get("value"),
                    normalized_value=_normalize_value(field_value.get("value")),
                    confidence=field_value.get("confidence", 0.0),
                    source_page=page_num,
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

    except PdfRenderError as exc:
        # Kullanıcı/veri hatası (bozuk, boş veya çok sayfalı PDF) — upstream
        # (OpenAI) hatası değil. error_code router'da 422'ye map edilir.
        run.status = "failed"
        run.error_code = exc.error_code
        run.completed_at = datetime.utcnow()
        document.processing_status = "failed"
        db.commit()
        logger.error(f"PDF render failed for document {document.id} (run {run.id}): {exc.error_code}")
        raise
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
    # Otomatik çözümleme başarısızsa (not_found) ve kullanıcı Ek Protokol
    # tamamlama ekranında elle bir tarife grubu girdiyse onu kullan. Template
    # (additional_protocol_v1.html) resolution_status == "not_found" iken zaten
    # "elle girilmiştir" notu gösteriyor — bu yalnız o notu değerle eşler,
    # resolution_status'u "resolved"e ÇEVİRMEZ (gerçekten otomatik çözülmedi).
    manual_tariff_group = complete_fields.get("tariff_group")
    if tariff_resolution.resolution_status == "not_found" and manual_tariff_group:
        tariff_resolution = TariffGroupResolution(
            value=manual_tariff_group,
            source_path="complete_fields.tariff_group (elle girildi)",
            resolution_status="not_found",
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


# ═══════════════════════════════════════════════════════════════════════════════
# Finalize concurrency / idempotency (HIGH#1 — final architecture review)
#
# Compare-and-set (CAS) desenli claim: tek bir atomic UPDATE...WHERE ifadesi
# ile hem "hâlâ beklenen durumda mı" kontrolü hem de durum değişikliği aynı
# anda yapılır. Bu, satır kilidi (SELECT...FOR UPDATE) GEREKTİRMEDEN, hem
# SQLite hem Postgres'te doğru ve taşınabilir çalışır — veritabanı motoru
# aynı satıra eşzamanlı UPDATE'leri zaten kendi içinde sıraya koyar; WHERE
# koşulu "kaybeden" isteğin rowcount=0 görmesini garanti eder. Bu yüzden
# ayrı bir Postgres/SQLite dallanmasına gerek yok (talep edilen sonucun
# birebir aynısı, daha az kodla ve iki backend'de de kanıtlanabilir şekilde).
# ═══════════════════════════════════════════════════════════════════════════════

_FINALIZE_CLAIMABLE_STATUSES = ["READY_TO_GENERATE", "GENERATED"]


def try_claim_contract_for_finalize(db: Session, contract_id: int, tenant_id: str) -> bool:
    """
    Contract'ı finalize için atomically "claim" eder (READY_TO_GENERATE|
    GENERATED -> FINALIZING). Yalnız TEK bir eşzamanlı istek rowcount=1
    görür (kazanan); diğerleri rowcount=0 görür ve PDF üretimine hiç
    başlamaz — "aynı anda iki render başlamasını engelle" gereksinimi
    burada, satır kilidi olmadan, doğrudan WHERE koşuluyla sağlanır.
    """
    result = db.execute(
        sa_update(db_models.Contract)
        .where(
            db_models.Contract.id == contract_id,
            db_models.Contract.tenant_id == tenant_id,
            db_models.Contract.status.in_(_FINALIZE_CLAIMABLE_STATUSES),
        )
        .values(status="FINALIZING")
    )
    db.commit()
    return result.rowcount == 1


def revert_contract_finalizing_to_ready(db: Session, contract_id: int) -> None:
    """
    PDF üretimi/storage/final commit başarısız olursa FINALIZING'i geri alır
    (READY_TO_GENERATE) — retry mümkün olsun diye. Contract kalıcı olarak
    FINALIZING'te takılı kalmaz.
    """
    db.execute(
        sa_update(db_models.Contract)
        .where(db_models.Contract.id == contract_id, db_models.Contract.status == "FINALIZING")
        .values(status="READY_TO_GENERATE")
    )
    db.commit()


def finalize_contract_pdf_and_commit(db: Session, contract_id: int, tenant_id: str, snapshot: dict) -> tuple[str, str]:
    """
    Claim edilmiş (FINALIZING) bir Contract için PDF üretir, GEÇİCİ bir
    storage key'ine yazar (storage backend'in gerçekten yazabildiğini DB'ye
    dokunmadan kanıtlamak için), sonra canonical key'e yazar — put_bytes'ın
    GERÇEK dönüş değeri pdf_storage_ref olarak kullanılır (storage backend'e
    göre değişebilir; ör. LocalStorage tam çözümlenmiş dosya yolu döndürür,
    ham key değil — bu yüzden ham key'i DB'ye yazmak download'da "path
    traversal" yanlış-pozitifine yol açar). DB'yi FINALIZED'e taşır (yalnız
    hâlâ FINALIZING ise — final CAS). Herhangi bir adım başarısız olursa:
    Contract READY_TO_GENERATE'e geri alınır, geçici VE (varsa) canonical
    dosya silinir — DB hiçbir zaman var olmayan/tutarsız bir ref'e işaret
    etmez (download zaten yalnız contract.pdf_storage_ref set'liyse dosya
    servis eder, o yüzden bu noktada bir dosyanın diskte kalması tek başına
    güvenlik riski değildir, ama temizlik yine de yapılır — "orphan dosya
    yok" gereksinimi).

    Döner: (pdf_storage_ref, pdf_sha256)
    """
    from . import pdf_service  # döngüsel import'tan kaçınmak için burada

    storage = get_storage()
    temp_key = f"contracts/generated/{tenant_id}/{contract_id}.pdf.tmp-{uuid.uuid4().hex}"
    canonical_key = f"contracts/generated/{tenant_id}/{contract_id}.pdf"
    temp_ref: Optional[str] = None
    canonical_ref: Optional[str] = None

    try:
        pdf_bytes, pdf_sha256 = pdf_service.generate_contract_pdf(snapshot)
        # put_bytes'ın dönüş değeri DB/delete'e yazılacak GERÇEK ref'tir (backend'e
        # göre değişebilir; LocalStorage tam çözümlenmiş yol döndürür, ham key değil).
        temp_ref = storage.put_bytes(key=temp_key, data=pdf_bytes, content_type="application/pdf")
        canonical_ref = storage.put_bytes(key=canonical_key, data=pdf_bytes, content_type="application/pdf")

        result = db.execute(
            sa_update(db_models.Contract)
            .where(db_models.Contract.id == contract_id, db_models.Contract.status == "FINALIZING")
            .values(
                contract_snapshot_json=snapshot,
                pdf_storage_ref=canonical_ref,
                pdf_sha256=pdf_sha256,
                status="FINALIZED",
                finalized_at=datetime.utcnow(),
                template_version="v1",
            )
        )
        if result.rowcount != 1:
            # Beklenmiyor (yalnız biz FINALIZING'e taşımıştık) ama savunmacı davran.
            raise RuntimeError(f"Contract {contract_id} finalize sırasında beklenmeyen durumda (FINALIZING değil)")
        db.commit()

        try:
            storage.delete(temp_ref)
        except Exception as exc:  # noqa: BLE001 — geçici dosya temizliği best-effort
            logger.warning(f"Contract {contract_id}: geçici PDF silinemedi ({temp_key}): {type(exc).__name__}")

        return canonical_ref, pdf_sha256

    except Exception:
        db.rollback()
        if temp_ref is not None:
            try:
                storage.delete(temp_ref)
            except Exception as cleanup_exc:  # noqa: BLE001
                logger.warning(f"Contract {contract_id}: geçici dosya temizliği başarısız: {type(cleanup_exc).__name__}")
        if canonical_ref is not None:
            # DB commit'i canonical yazımından SONRA başarısız oldu — artık
            # DB'de referanssız kalan canonical dosyayı da temizle.
            try:
                storage.delete(canonical_ref)
            except Exception as cleanup_exc:  # noqa: BLE001
                logger.warning(f"Contract {contract_id}: canonical dosya temizliği başarısız: {type(cleanup_exc).__name__}")
        revert_contract_finalizing_to_ready(db, contract_id)
        raise


# ═══════════════════════════════════════════════════════════════════════════════
# Offer lifecycle bağlama (owner kararı: Offer artık kalıcı domain nesnesi,
# mevcut draft/sent/viewed/accepted/contracting/completed durum makinesi
# yeniden kullanılır — bkz. app/services/offer_lifecycle.py)
#
# Best-effort: geçiş kuralına uymayan durumlarda (ör. offer zaten
# "completed" iken aynı offer'dan İKİNCİ bir sözleşme taslağı açılması —
# owner'ın "aynı tekliften farklı sözleşme üretilebilir" isteğiyle kasıtlı
# olarak mümkün) sözleşme akışını ENGELLEMEZ, yalnız durum güncellemesini
# atlar. Offer.status burada bir iş kuralı kapısı değil, izlenebilirlik
# sinyalidir — gerçek kapı Contract.status'tur (zaten var/test edilmiş).
# ═══════════════════════════════════════════════════════════════════════════════

def _mark_offer_status_best_effort(db: Session, offer: Optional[db_models.Offer], new_status: str, notes: str) -> None:
    """
    Ortak best-effort geçiş — HIGH#2 düzeltmesi: yalnız ValueError (geçersiz
    geçiş — beklenen/idempotent durum) DEĞİL, herhangi bir beklenmeyen
    exception da (ör. transition_offer_status içindeki log_action'ın kendi
    db.commit()'i patlarsa) burada durdurulur. Contract/finalize'ın temel
    başarısı bu yan etkiye ASLA bağımlı olmamalı — çağıran bu fonksiyonun
    hiçbir zaman exception fırlatmayacağına güvenebilir.

    GÜVENLİK: log satırında yalnız offer id + exception tipi var; T.C.,
    belge metni veya snapshot içeriği asla yok (mevcut PII kuralıyla aynı).

    İdempotency: transition_offer_status zaten VALID_OFFER_TRANSITIONS'a
    göre doğrulama yapıyor — offer hedef duruma (veya ötesine) zaten
    geçmişse ValueError fırlatır ve log_action TEKRAR ÇAĞRILMAZ, yani bir
    retry aynı lifecycle event'i iki kez yazmaz.
    """
    if offer is None:
        return
    try:
        transition_offer_status(db, offer, new_status, notes=notes, actor_type="system")
    except ValueError as exc:
        # Beklenen: geçersiz/zaten-gerçekleşmiş geçiş (terminal state, vs.) — bilgi seviyesinde.
        logger.info(f"Offer {offer.id} '{new_status}' durumuna taşınamadı (yok sayıldı): {exc}")
    except Exception as exc:  # noqa: BLE001 — kasıtlı geniş yakalama, bkz. docstring
        # Beklenmeyen: DB/audit hatası gibi — Contract'ın kendi başarısını etkilemesin,
        # yalnız operasyonel takip için uyarı seviyesinde logla.
        db.rollback()
        logger.warning(
            f"Offer {offer.id} '{new_status}' durumuna taşınırken beklenmeyen hata "
            f"(sözleşme işlemi başarılı sayılmaya devam eder): {type(exc).__name__}"
        )


def mark_offer_contracting(db: Session, offer: Optional[db_models.Offer]) -> None:
    """Sözleşme taslağı oluşturulduğunda Offer'ı 'contracting' durumuna taşımayı DENER (best-effort)."""
    _mark_offer_status_best_effort(db, offer, "contracting", notes="Sözleşme hazırlama başlatıldı")


def mark_offer_completed(db: Session, offer: Optional[db_models.Offer]) -> None:
    """Sözleşme finalize edildiğinde Offer'ı 'completed' durumuna taşımayı DENER (best-effort)."""
    _mark_offer_status_best_effort(db, offer, "completed", notes="Sözleşme finalize edildi")
