"""
S4 — Prospecting — iş mantığı.

PROSPECT ≠ CUSTOMER (owner madde 1): bu modül Customer tablosuna YALNIZ
convert_to_customer() içinde, açık kullanıcı aksiyonu sonucunda yazar.
Discovery/enrichment/qualification akışlarının HİÇBİRİ Customer'a
dokunmaz.

Status makinesi (tek alan, owner notu — bkz. app/database.py
ProspectCompany docstring'i): DISCOVERED → VERIFIED → QUALIFIED/
DISQUALIFIED → CONVERTED. CONVERTED terminal'dir — bir kez dönüştürülen
prospect'in status'u bir daha DEĞİŞMEZ (idempotent conversion, owner:
"Double conversion must be idempotent").

Çağrıldığı yerler:
- app/prospecting/router.py (tüm /prospects endpoint'leri) [S4-WB6]
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import database as db_models
from ..crm import service as crm_service
from . import dedup
from .discovery import DuckDuckGoHtmlProvider, build_search_query
from .enrichment import classify_contact_type, enrich_website, normalize_base_url
from .normalize import normalize_domain, normalize_name, normalize_phone
from .schemas import (
    ConvertRequest,
    ConvertResponse,
    CustomerMatchOut,
    DedupMatchOut,
    DisqualifyRequest,
    DiscoverCandidateOut,
    DiscoverRequest,
    DiscoverResponse,
    EnrichResponse,
    ProspectCompanyCreateRequest,
    ProspectCompanyListResponse,
    ProspectCompanyOut,
    ProspectCompanyUpdateRequest,
    ProspectContactOut,
    ProspectCreateResponse,
    ProspectSourceOut,
    QualifyRequest,
)

logger = logging.getLogger(__name__)

_TERMINAL_STATUS = "CONVERTED"


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _get_prospect_or_404(db: Session, tenant_id: str, prospect_id: int) -> "db_models.ProspectCompany":
    prospect = (
        db.query(db_models.ProspectCompany)
        .filter(db_models.ProspectCompany.id == prospect_id, db_models.ProspectCompany.tenant_id == tenant_id)
        .first()
    )
    if not prospect:
        raise HTTPException(status_code=404, detail={"error": "prospect_not_found", "message": f"Prospect bulunamadı: {prospect_id}"})
    return prospect


def _require_not_converted(prospect: "db_models.ProspectCompany") -> None:
    if prospect.status == _TERMINAL_STATUS:
        raise HTTPException(
            status_code=409,
            detail={"error": "prospect_already_converted", "message": "Bu prospect zaten Customer'a dönüştürülmüş — durumu değiştirilemez."},
        )


def _company_to_out(company: "db_models.ProspectCompany", contact_count: int = 0, source_count: int = 0) -> ProspectCompanyOut:
    return ProspectCompanyOut(
        id=company.id,
        legal_name=company.legal_name,
        trade_name=company.trade_name,
        website=company.website,
        normalized_domain=company.normalized_domain,
        sector=company.sector,
        city=company.city,
        district=company.district,
        industrial_zone=company.industrial_zone,
        address=company.address,
        phone=company.phone,
        status=company.status,
        qualification_reason=company.qualification_reason,
        qualification_note=company.qualification_note,
        duplicate_of_id=company.duplicate_of_id,
        customer_id=company.customer_id,
        discovered_at=_iso(company.discovered_at),
        last_verified_at=_iso(company.last_verified_at),
        created_at=_iso(company.created_at),
        updated_at=_iso(company.updated_at),
        contact_count=contact_count,
        source_count=source_count,
    )


def _contact_to_out(contact: "db_models.ProspectContact") -> ProspectContactOut:
    return ProspectContactOut(
        id=contact.id,
        prospect_company_id=contact.prospect_company_id,
        full_name=contact.full_name,
        job_title=contact.job_title,
        email=contact.email,
        phone=contact.phone,
        contact_type=contact.contact_type,
        verification_status=contact.verification_status,
        source_id=contact.source_id,
        created_at=_iso(contact.created_at),
    )


def _source_to_out(source: "db_models.ProspectSource") -> ProspectSourceOut:
    return ProspectSourceOut(
        id=source.id,
        prospect_company_id=source.prospect_company_id,
        source_url=source.source_url,
        source_type=source.source_type,
        source_title=source.source_title,
        evidence_text=source.evidence_text,
        fetch_status=source.fetch_status,
        discovered_at=_iso(source.discovered_at),
        last_checked_at=_iso(source.last_checked_at),
    )


# =============================================================================
# CRUD + dedup-gated create
# =============================================================================


def create_prospect(db: Session, tenant_id: str, req: ProspectCompanyCreateRequest) -> ProspectCreateResponse:
    """
    Owner: "silent merge YOK." exact_duplicate → mevcut kaydı döndür, YENİ
    KAYIT AÇMA. review_required (probable_duplicate) → force_create_
    despite_duplicate=True gelmedikçe HİÇBİR KAYIT AÇMA, yalnız adayları
    göster. distinct veya force → yeni kayıt.
    """
    result = dedup.check_prospect_duplicate(
        db, tenant_id,
        legal_name=req.legal_name, trade_name=req.trade_name,
        website=req.website, phone=req.phone,
    )
    matches_out = [
        DedupMatchOut(company_id=m.company_id, match_signal=m.match_signal, display_name=m.display_name, website=m.website, city=m.city)
        for m in result.matches
    ]

    if result.verdict == dedup.VERDICT_EXACT_DUPLICATE:
        existing = db.query(db_models.ProspectCompany).filter(db_models.ProspectCompany.id == result.matches[0].company_id).first()
        return ProspectCreateResponse(dedup_verdict="exact_duplicate", matches=matches_out, prospect=_to_out_with_counts(db, tenant_id, existing) if existing else None)

    if result.verdict == dedup.VERDICT_PROBABLE_DUPLICATE and not req.force_create_despite_duplicate:
        return ProspectCreateResponse(dedup_verdict="review_required", matches=matches_out, prospect=None)

    company = db_models.ProspectCompany(
        tenant_id=tenant_id,
        legal_name=req.legal_name,
        trade_name=req.trade_name,
        normalized_name=normalize_name(req.trade_name) or normalize_name(req.legal_name),
        website=req.website,
        normalized_domain=normalize_domain(req.website),
        sector=req.sector,
        city=req.city,
        district=req.district,
        industrial_zone=req.industrial_zone,
        address=req.address,
        phone=req.phone,
        status="DISCOVERED",
        discovered_at=datetime.utcnow(),
    )
    if result.verdict == dedup.VERDICT_PROBABLE_DUPLICATE and req.force_create_despite_duplicate:
        # Kullanıcı review sonrası "yine de ayrı kayıt" dedi — izi
        # kaybetmiyoruz (owner: silent merge yok ama iz de kaybolmasın).
        company.duplicate_of_id = result.matches[0].company_id

    db.add(company)
    db.flush()

    if req.source_url:
        source = db_models.ProspectSource(
            tenant_id=tenant_id,
            prospect_company_id=company.id,
            source_url=req.source_url,
            source_type=req.source_type or "MANUAL",
            fetch_status="PENDING",
            discovered_at=datetime.utcnow(),
        )
        db.add(source)

    db.commit()
    db.refresh(company)
    return ProspectCreateResponse(dedup_verdict="created", matches=matches_out, prospect=_to_out_with_counts(db, tenant_id, company))


def _counts_by_company(db: Session, tenant_id: str, company_ids: list[int]) -> tuple[dict[int, int], dict[int, int]]:
    """N+1-free — S1/S3'teki bulk-aggregate pattern (bkz. list_customers open_offer_count)."""
    if not company_ids:
        return {}, {}
    contact_rows = (
        db.query(db_models.ProspectContact.prospect_company_id, func.count(db_models.ProspectContact.id))
        .filter(db_models.ProspectContact.prospect_company_id.in_(company_ids))
        .group_by(db_models.ProspectContact.prospect_company_id)
        .all()
    )
    source_rows = (
        db.query(db_models.ProspectSource.prospect_company_id, func.count(db_models.ProspectSource.id))
        .filter(db_models.ProspectSource.prospect_company_id.in_(company_ids))
        .group_by(db_models.ProspectSource.prospect_company_id)
        .all()
    )
    return {cid: cnt for cid, cnt in contact_rows}, {cid: cnt for cid, cnt in source_rows}


def _to_out_with_counts(db: Session, tenant_id: str, company: "db_models.ProspectCompany") -> ProspectCompanyOut:
    """
    _company_to_out()'u contact_count/source_count DOĞRU hesaplanmış
    olarak çağırır — tek kayıt için de _counts_by_company (N+1-free
    pattern) kullanılır, aksi halde GET /prospects/{id} ile diğer
    lifecycle endpoint'lerinin (verify/qualify/convert/vb.) response'ları
    ARASINDA tutarsızlık oluşurdu (gerçek bug: ilk yazımda bu fonksiyonlar
    contact_count/source_count'u hep 0 dönüyordu, canlı smoke testte
    fark edilip düzeltildi).
    """
    contact_counts, source_counts = _counts_by_company(db, tenant_id, [company.id])
    return _company_to_out(company, contact_counts.get(company.id, 0), source_counts.get(company.id, 0))


def list_prospects(
    db: Session,
    tenant_id: str,
    *,
    status: Optional[str] = None,
    city: Optional[str] = None,
    sector: Optional[str] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> ProspectCompanyListResponse:
    query = db.query(db_models.ProspectCompany).filter(db_models.ProspectCompany.tenant_id == tenant_id)
    if status:
        query = query.filter(db_models.ProspectCompany.status == status)
    if city:
        query = query.filter(db_models.ProspectCompany.city.ilike(f"%{city}%"))
    if sector:
        query = query.filter(db_models.ProspectCompany.sector.ilike(f"%{sector}%"))
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                db_models.ProspectCompany.legal_name.ilike(pattern),
                db_models.ProspectCompany.trade_name.ilike(pattern),
                db_models.ProspectCompany.website.ilike(pattern),
            )
        )

    total = query.count()
    rows = query.order_by(db_models.ProspectCompany.discovered_at.desc()).offset(skip).limit(limit).all()
    contact_counts, source_counts = _counts_by_company(db, tenant_id, [c.id for c in rows])
    items = [_company_to_out(c, contact_counts.get(c.id, 0), source_counts.get(c.id, 0)) for c in rows]
    return ProspectCompanyListResponse(items=items, total=total)


def get_prospect(db: Session, tenant_id: str, prospect_id: int) -> ProspectCompanyOut:
    company = _get_prospect_or_404(db, tenant_id, prospect_id)
    contact_counts, source_counts = _counts_by_company(db, tenant_id, [company.id])
    return _company_to_out(company, contact_counts.get(company.id, 0), source_counts.get(company.id, 0))


def update_prospect(db: Session, tenant_id: str, prospect_id: int, req: ProspectCompanyUpdateRequest) -> ProspectCompanyOut:
    company = _get_prospect_or_404(db, tenant_id, prospect_id)
    _require_not_converted(company)

    data = req.model_dump(exclude_unset=True)
    for field_name, value in data.items():
        setattr(company, field_name, value)

    if "trade_name" in data or "legal_name" in data:
        company.normalized_name = normalize_name(company.trade_name) or normalize_name(company.legal_name)
    if "website" in data:
        company.normalized_domain = normalize_domain(company.website)

    company.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(company)
    return _to_out_with_counts(db, tenant_id, company)


# =============================================================================
# Lifecycle: verify / qualify / disqualify
# =============================================================================


def verify_prospect(db: Session, tenant_id: str, prospect_id: int) -> ProspectCompanyOut:
    company = _get_prospect_or_404(db, tenant_id, prospect_id)
    _require_not_converted(company)

    if company.status == "DISCOVERED":
        company.status = "VERIFIED"
    # QUALIFIED/DISQUALIFIED/VERIFIED üzerinde tekrar çağrılırsa status
    # GERİ ALINMAZ (bilgi kaybı olmasın) — yalnız last_verified_at tazelenir.
    company.last_verified_at = datetime.utcnow()
    company.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(company)
    return _to_out_with_counts(db, tenant_id, company)


def qualify_prospect(db: Session, tenant_id: str, prospect_id: int, req: QualifyRequest) -> ProspectCompanyOut:
    """Owner: black-box auto-disqualify YOK — bu HER ZAMAN açık bir kullanıcı aksiyonudur."""
    company = _get_prospect_or_404(db, tenant_id, prospect_id)
    _require_not_converted(company)

    company.status = "QUALIFIED"
    company.qualification_reason = req.reason
    company.qualification_note = req.note
    company.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(company)
    return _to_out_with_counts(db, tenant_id, company)


def disqualify_prospect(db: Session, tenant_id: str, prospect_id: int, req: DisqualifyRequest) -> ProspectCompanyOut:
    company = _get_prospect_or_404(db, tenant_id, prospect_id)
    _require_not_converted(company)

    company.status = "DISQUALIFIED"
    company.qualification_reason = req.reason
    company.qualification_note = req.note
    company.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(company)
    return _to_out_with_counts(db, tenant_id, company)


# =============================================================================
# Contacts / Sources (read)
# =============================================================================


def list_contacts(db: Session, tenant_id: str, prospect_id: int) -> list[ProspectContactOut]:
    _get_prospect_or_404(db, tenant_id, prospect_id)  # tenant-scoped existence
    rows = (
        db.query(db_models.ProspectContact)
        .filter(db_models.ProspectContact.prospect_company_id == prospect_id, db_models.ProspectContact.tenant_id == tenant_id)
        .order_by(db_models.ProspectContact.created_at.asc())
        .all()
    )
    return [_contact_to_out(c) for c in rows]


def list_sources(db: Session, tenant_id: str, prospect_id: int) -> list[ProspectSourceOut]:
    _get_prospect_or_404(db, tenant_id, prospect_id)
    rows = (
        db.query(db_models.ProspectSource)
        .filter(db_models.ProspectSource.prospect_company_id == prospect_id, db_models.ProspectSource.tenant_id == tenant_id)
        .order_by(db_models.ProspectSource.discovered_at.asc())
        .all()
    )
    return [_source_to_out(s) for s in rows]


# =============================================================================
# Discovery + enrichment
# =============================================================================


def discover(req: DiscoverRequest) -> DiscoverResponse:
    """
    DB'ye HİÇBİR ŞEY YAZMAZ (owner: "Discovery results must NOT be written
    directly to DB, only after explicit 'Prospect Olarak Kaydet'"). Yalnız
    aday listesi döner — bkz. app/prospecting/discovery.py modül
    docstring'indeki canlı bot-challenge bulgusu (best-effort).
    """
    provider = DuckDuckGoHtmlProvider()
    outcome = provider.search(build_search_query(req.keyword, req.city, req.district))
    return DiscoverResponse(
        status=outcome.status,
        message=outcome.message,
        candidates=[DiscoverCandidateOut(title=c.title, url=c.url, snippet=c.snippet) for c in outcome.candidates],
    )


def enrich_prospect(db: Session, tenant_id: str, prospect_id: int) -> EnrichResponse:
    """
    Bounded website enrichment (bkz. enrichment.py — max 3 sayfa). Status
    OTOMATİK DEĞİŞMEZ (owner: HUMAN-IN-THE-LOOP — "verify" kullanıcı
    aksiyonudur, enrichment'ın kendisi değil). Her sayfa fetch'i BAŞARILI/
    BAŞARISIZ fark etmeksizin bir ProspectSource satırı üretir ("hiçbir
    kayıt sessizce kaybolmayacak").
    """
    company = _get_prospect_or_404(db, tenant_id, prospect_id)
    _require_not_converted(company)

    base_url = normalize_base_url(company.website or "")
    if not base_url:
        raise HTTPException(status_code=400, detail={"error": "no_website", "message": "Bu prospect'in bir website adresi yok — enrichment için gerekli."})

    result = enrich_website(base_url)

    existing_hashes = {
        row.content_hash
        for row in db.query(db_models.ProspectSource.content_hash).filter(
            db_models.ProspectSource.prospect_company_id == company.id,
            db_models.ProspectSource.content_hash.isnot(None),
        ).all()
    }

    new_sources: list[db_models.ProspectSource] = []
    existing_contact_emails = {
        row.email
        for row in db.query(db_models.ProspectContact.email).filter(
            db_models.ProspectContact.prospect_company_id == company.id,
            db_models.ProspectContact.email.isnot(None),
        ).all()
    }
    new_contacts: list[db_models.ProspectContact] = []

    for page in result.pages:
        if page.content_hash and page.content_hash in existing_hashes:
            # Aynı içerik daha önce görülmüş — yeni satır AÇMA (owner:
            # content_hash cache deseni), yalnız fetch denemesi tekrar
            # yapıldığı (taze veri) unutulmasın diye devam edilir; source
            # satırı büyümesin diye burada durulur.
            continue

        source = db_models.ProspectSource(
            tenant_id=tenant_id,
            prospect_company_id=company.id,
            source_url=page.fetch.final_url or page.url,
            source_type="WEBSITE",
            source_title=page.title or None,
            evidence_text=page.excerpt or None,
            content_hash=page.content_hash,
            fetch_status=page.fetch.status,
            discovered_at=datetime.utcnow(),
            last_checked_at=datetime.utcnow(),
        )
        db.add(source)
        db.flush()
        new_sources.append(source)
        if page.content_hash:
            existing_hashes.add(page.content_hash)

        for email in page.emails:
            if email in existing_contact_emails:
                continue
            contact = db_models.ProspectContact(
                tenant_id=tenant_id,
                prospect_company_id=company.id,
                email=email,
                contact_type=classify_contact_type(email),
                verification_status="SYNTAX_VALID",  # V1: SMTP probing YOK, yalnız regex ile bulundu
                source_id=source.id,
                created_at=datetime.utcnow(),
            )
            db.add(contact)
            new_contacts.append(contact)
            existing_contact_emails.add(email)

    db.commit()
    for s in new_sources:
        db.refresh(s)
    for c in new_contacts:
        db.refresh(c)
    db.refresh(company)

    contact_counts, source_counts = _counts_by_company(db, tenant_id, [company.id])
    return EnrichResponse(
        prospect=_company_to_out(company, contact_counts.get(company.id, 0), source_counts.get(company.id, 0)),
        pages_fetched=len(result.pages),
        new_contacts=[_contact_to_out(c) for c in new_contacts],
        new_sources=[_source_to_out(s) for s in new_sources],
    )


# =============================================================================
# Conversion — Prospect → Customer
# =============================================================================


def convert_to_customer(db: Session, tenant_id: str, prospect_id: int, req: ConvertRequest) -> ConvertResponse:
    """
    Owner'ın 7 adımı: (1) mevcut Customer dedup kontrolü (2) kullanıcı
    onayı (3) oluştur/seç (4) prospect.customer_id bağla (5)
    status=CONVERTED (6) opsiyonel Activity (7) opsiyonel Task.

    İdempotency: zaten CONVERTED ise YENİ Customer YARATILMAZ, mevcut
    bağlantı aynen döndürülür (owner: "Double conversion must be
    idempotent — same prospect cannot create a second Customer").

    Transaction sınırı: Customer oluşturma + prospect.customer_id/status
    güncellemesi TEK commit'te atomik yapılır (adım 4-5 burada). Activity/
    Task (adım 6-7) crm_service üzerinden AYRI commit'lerle, ana conversion
    BAŞARILI OLDUKTAN SONRA en-iyi-çaba (best-effort) eklenir — S2/Contract
    fazındaki "post-commit failure isolation" emsaliyle tutarlı: ikincil
    bir adımın başarısızlığı çekirdek conversion'ı geçersiz kılmaz ama
    SESSİZCE de yutulmaz (ConvertResponse.warnings).
    """
    company = _get_prospect_or_404(db, tenant_id, prospect_id)

    if company.status == _TERMINAL_STATUS and company.customer_id:
        return ConvertResponse(
            status="converted",
            prospect=_to_out_with_counts(db, tenant_id, company),
            customer_id=company.customer_id,
            customer_created=False,
            activity_created=False,
            task_created=False,
        )

    customer_created = False
    if req.existing_customer_id is not None:
        customer = db.query(db_models.Customer).filter(db_models.Customer.id == req.existing_customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail={"error": "customer_not_found", "message": f"Belirtilen müşteri bulunamadı: {req.existing_customer_id}"})
        customer_id = customer.id
    else:
        if not req.force_create_new_customer:
            matches = dedup.find_matching_customers(
                db,
                name=company.trade_name or company.legal_name,
                company=company.legal_name,
                email=None,
            )
            if matches:
                return ConvertResponse(
                    status="confirmation_required",
                    potential_matches=[CustomerMatchOut(customer_id=m.customer_id, name=m.name, company=m.company, email=m.email) for m in matches],
                    prospect=_to_out_with_counts(db, tenant_id, company),
                )

        new_customer = db_models.Customer(
            name=company.trade_name or company.legal_name or "İsimsiz Prospect",
            company=company.legal_name,
            phone=company.phone,
            address=company.address,
            notes=f"S4 Prospecting'ten dönüştürüldü (prospect #{company.id}).",
        )
        db.add(new_customer)
        db.flush()
        customer_id = new_customer.id
        customer_created = True

    company.customer_id = customer_id
    company.status = _TERMINAL_STATUS
    company.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(company)

    warnings: list[str] = []
    activity_created = False
    if req.create_activity:
        try:
            crm_service.create_activity(
                db, tenant_id, customer_id, None, None,
                "NOTE", "Prospect'ten dönüştürüldü",
                f"Bu müşteri S4 Prospecting akışıyla oluşturuldu (prospect #{company.id}).",
                None,
            )
            activity_created = True
        except Exception:
            logger.exception("Conversion activity oluşturulamadı (prospect_id=%s)", company.id)
            warnings.append("Dönüştürme notu (Activity) oluşturulamadı — müşteri bağlantısı yine de tamamlandı.")

    task_created = False
    if req.create_first_task and req.first_task_title:
        try:
            crm_service.create_task(
                db, tenant_id, customer_id, None, None,
                req.first_task_title, None, req.first_task_due_at,
            )
            task_created = True
        except Exception:
            logger.exception("İlk görev oluşturulamadı (prospect_id=%s)", company.id)
            warnings.append("İlk görev oluşturulamadı — müşteri bağlantısı yine de tamamlandı.")

    return ConvertResponse(
        status="converted",
        prospect=_to_out_with_counts(db, tenant_id, company),
        customer_id=customer_id,
        customer_created=customer_created,
        activity_created=activity_created,
        task_created=task_created,
        warnings=warnings,
    )
