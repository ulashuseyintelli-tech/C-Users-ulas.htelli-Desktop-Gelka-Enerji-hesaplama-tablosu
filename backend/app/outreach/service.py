"""
S5 — Outreach — service.py

Owner'ın HIGH PRIORITY maddesi: "send endpoint çift-tıklama/retry
karşısında idempotent olmalı" + "provider başarılı ama DB güncellemesi
başarısız olursa" senaryosu. Bu modül compliance.py (HARD GATE) +
drafting.py (metin üretimi) + smtp_provider.py (transport) katmanlarını
DB kalıcılığı ve durum makinesiyle birleştirir.

Durum makinesi (app/database.py OutreachMessage.status docstring'i ile
birebir): DRAFT → READY_FOR_REVIEW → APPROVED → SENDING → SENT | FAILED
(+ BOUNCED/REPLIED/SUPPRESSED/CANCELLED, bu WB'de henüz üretilmiyor).

  DRAFT             create_draft_message() ile oluşur — editable_body
                     kullanıcı tarafından DÜZENLENEBİLİR (owner: "user-
                     editable"). system_footer_snapshot BU AŞAMADA DAHİ
                     zaten deterministik üretilmiştir ve finalize_draft_
                     message() tarafından ASLA değiştirilmez (owner'ın
                     10.08 ek talimatı — ayrı kolon, bkz. app/database.py).
  READY_FOR_REVIEW   finalize_draft_message() ile (kullanıcı düzenlemeyi
                     bitirdi).
  APPROVED           approve_message() ile — compliance TAZE yeniden
                     değerlendirilir, can_send=false ise 409 ile reddedilir.
  SENDING            send_message() içinde claim_message_for_sending()'in
                     ATOMİK UPDATE'i ile — provider.send()'DEN ÖNCE COMMIT
                     edilir (bkz. aşağıdaki idempotency notu).
  SENT | FAILED      provider.send() sonucuna göre.

İdempotent send (owner HIGH PRIORITY): claim_message_for_sending() yalnız
status='APPROVED' olan bir satırı 'SENDING'e çeken atomik bir UPDATE'tir
ve HEMEN commit edilir — provider.send()'den ÖNCE. Böylece:
  - Çift tıklama/concurrent istek: yalnız BİR istek rowcount=1 alır, diğeri
    ALREADY_CLAIMED_OR_NOT_APPROVED ile döner — provider.send() ASLA iki
    kez çağrılmaz.
  - Süreç provider.send() SIRASINDA çökerse: mesaj SENDING'de TAKILI kalır
    (ne APPROVED ne SENT) — bu BİLİNÇLİ bir "güvenli başarısızlık"
    durumudur; hiçbir otomatik sweep SENDING'deki mesajları tekrar
    GÖNDERMEZ (gerçek bir double-send riski olurdu). SENDING'de takılı
    kalan mesajlar MANUEL inceleme gerektirir — V1 kapsamı bu kadardır,
    otomatik reconciliation sweep gelecekte ayrı bir iş.
  - provider.send() BAŞARILI ama son commit (SENT'e geçiş) BAŞARISIZ
    olursa: logger.critical ile mesaj ID'si LOGLANIR ("hiçbir kayıt
    sessizce kaybolmayacak" ilkesi, S3'ten taşınan genel ilke).

HARD GATE tekrar doğrulaması: hem approve_message() hem send_message()
compliance.evaluate_email_send_eligibility()'yi TAZE çağırır — APPROVED
olması TEK BAŞINA YETERLİ DEĞİLDİR, çünkü approve ile send arasında
(örn. yeni bir suppression kaydı eklenmesi) durum değişmiş olabilir.

Çağrıldığı yerler:
- (henüz yok) app/outreach/router.py [S5-WB5/WB6]
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import database as db_models
from ..core.config import settings
from ..crm import service as crm_service
from ..crm.schemas import TaskOut
from ..prospecting.normalize import normalize_email
from .compliance import evaluate_email_send_eligibility
from .drafting import create_draft, ensure_default_template
from .schemas import OutreachMessageOut, SuppressionEntryOut
from .sender_profile import SenderProfileIncompleteError, get_sender_profile
from .smtp_provider import get_outbound_mail_provider

logger = logging.getLogger(__name__)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _message_to_out(message: db_models.OutreachMessage) -> OutreachMessageOut:
    """
    app/prospecting/service.py::_company_to_out() ile AYNI desen — ORM
    satırını API sınırında Pydantic Out modeline manuel çevirir
    (from_attributes KULLANILMAZ, mevcut proje konvansiyonu).
    """
    return OutreachMessageOut(
        id=message.id,
        prospect_company_id=message.prospect_company_id,
        customer_id=message.customer_id,
        contact_id=message.contact_id,
        recipient_email_snapshot=message.recipient_email_snapshot,
        recipient_legal_type=message.recipient_legal_type,
        recipient_category=message.recipient_category,
        channel=message.channel,
        subject=message.subject,
        body_snapshot=message.body_snapshot,
        system_footer_snapshot=message.system_footer_snapshot,
        status=message.status,
        provider=message.provider,
        provider_message_id=message.provider_message_id,
        approved_at=_iso(message.approved_at),
        sent_at=_iso(message.sent_at),
        failed_at=_iso(message.failed_at),
        failure_code=message.failure_code,
        source_snapshot_json=message.source_snapshot_json,
        compliance_snapshot_json=message.compliance_snapshot_json,
        created_at=_iso(message.created_at),
        updated_at=_iso(message.updated_at),
    )


def _get_message_or_404(db: Session, tenant_id: str, message_id: int) -> db_models.OutreachMessage:
    message = (
        db.query(db_models.OutreachMessage)
        .filter(
            db_models.OutreachMessage.id == message_id,
            db_models.OutreachMessage.tenant_id == tenant_id,
        )
        .first()
    )
    if not message:
        raise HTTPException(status_code=404, detail={"error": "outreach_message_not_found", "message_id": message_id})
    return message


def _resolve_recipient_context(
    db: Session,
    tenant_id: str,
    *,
    prospect_company_id: Optional[int],
    contact_id: Optional[int],
    customer_id: Optional[int],
):
    """
    (company, contact, customer, candidate_email) döner — bulunamayan bir
    ID veya çözümlenemeyen bir e-posta HTTPException fırlatır. Tenant
    sınırı fail-closed (app/prospecting ile AYNI disiplin; Customer
    tablosunda tenant_id YOK — mevcut proje-genel davranış, bkz.
    app/outreach/compliance.py'deki aynı not).

    NOT (owner'ın 'S5 PRE-DELIVERY HARDENING' düzeltmesi, 10.08): bu
    fonksiyon YALNIZ Prospect/Customer bağlamlı hedefleri çözer — genel
    bir "direct_email" kaçış yolu BİLEREK YOK (ilk yazımda vardı, owner
    "ileride başka bir caller'ın yanlışlıkla kullanabileceği gizli bir
    bypass yüzeyi" gerekçesiyle REDDETTİ). Owner-controlled test gönderimi
    için AYRI, açık bir fonksiyon kullanılır — bkz.
    create_owner_controlled_test_draft().

    Çağrıldığı yerler:
    - create_draft_message() [S5-WB5]
    """
    contact = None
    company = None
    customer = None

    if contact_id is not None:
        contact = (
            db.query(db_models.ProspectContact)
            .filter(db_models.ProspectContact.id == contact_id, db_models.ProspectContact.tenant_id == tenant_id)
            .first()
        )
        if not contact:
            raise HTTPException(status_code=404, detail={"error": "prospect_contact_not_found"})
        if not contact.email:
            raise HTTPException(status_code=422, detail={"error": "contact_has_no_email"})
        if prospect_company_id is None:
            prospect_company_id = contact.prospect_company_id

    if prospect_company_id is not None:
        company = (
            db.query(db_models.ProspectCompany)
            .filter(
                db_models.ProspectCompany.id == prospect_company_id,
                db_models.ProspectCompany.tenant_id == tenant_id,
            )
            .first()
        )
        if not company:
            raise HTTPException(status_code=404, detail={"error": "prospect_company_not_found"})

    if customer_id is not None:
        # NOT: Customer tablosunda tenant_id YOK — mevcut proje-genel davranış.
        customer = db.query(db_models.Customer).filter(db_models.Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail={"error": "customer_not_found"})

    candidate_email = (contact.email if contact else None) or (customer.email if customer else None)
    if not candidate_email:
        # NOT: contact_id/customer_id ikisi de verilmemişse (yalnız
        # prospect_company_id verilmiş olsa bile — ProspectCompany'nin
        # kendi bir email alanı YOK, yalnız contact'ları var) candidate_email
        # her zaman None kalır ve buraya düşer — bu yüzden ayrı bir
        # "recipient_context_missing" dalı GEREKSİZ/erişilemez, tek bir
        # anlamlı hata kodu yeterli.
        raise HTTPException(status_code=422, detail={"error": "no_recipient_email_resolved"})

    return company, contact, customer, candidate_email


def get_message(db: Session, tenant_id: str, message_id: int) -> OutreachMessageOut:
    """
    Çağrıldığı yerler:
    - (henüz yok) GET /outreach/messages/{id} [S5-WB5/router]
    """
    return _message_to_out(_get_message_or_404(db, tenant_id, message_id))


def create_draft_message(
    db: Session,
    tenant_id: str,
    *,
    prospect_company_id: Optional[int] = None,
    contact_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    use_ai: bool = False,
) -> OutreachMessageOut:
    """
    Taslak oluşturur — HENÜZ gönderilmez, HENÜZ onaylanmaz. compliance
    burada YALNIZ GÖRÜNTÜLEME/bilgilendirme amaçlı hesaplanır (snapshot,
    owner'ın "compliance-state display" ilkesi) — gerçek ENFORCEMENT
    approve_message()/send_message()'dadır.

    Yalnız Prospect/Customer bağlamlı hedefler için — owner-controlled
    test gönderimi için AYRI bir fonksiyon kullanılır (bkz.
    create_owner_controlled_test_draft(), owner'ın 'S5 PRE-DELIVERY
    HARDENING' düzeltmesi: "genel fonksiyona gizli bypass yüzeyi
    gömülmesin").

    Raises:
        HTTPException 404/422: recipient bağlamı çözümlenemezse.
        HTTPException 422: sender_profile (Gelka'nın kendi kurumsal
            kimliği) eksikse — owner doldurmadan draft üretilemez.

    Çağrıldığı yerler:
    - POST /outreach/messages [S5-WB5/router]
    """
    company, contact, customer, candidate_email = _resolve_recipient_context(
        db, tenant_id,
        prospect_company_id=prospect_company_id, contact_id=contact_id, customer_id=customer_id,
    )

    # Görüntüleme amaçlı ilk değerlendirme — bkz. modül docstring'i.
    compliance = evaluate_email_send_eligibility(
        db, tenant_id,
        candidate_email=candidate_email,
        prospect_company_id=company.id if company else None,
        contact_id=contact.id if contact else None,
        customer_id=customer.id if customer else None,
    )

    template = ensure_default_template(db, tenant_id)

    company_name = None
    contact_full_name = None
    if company:
        company_name = company.trade_name or company.legal_name
    elif customer:
        company_name = customer.company or customer.name
    if contact:
        contact_full_name = contact.full_name
    elif customer:
        contact_full_name = customer.name

    # Owner (WB7): "Kaynak URL" tek bakışta görünmeli — SOURCE EVIDENCE
    # MANDATORY ilkesinin (S4) UI'da doğrudan görünür karşılığı. Taslak
    # ANINDAKİ kanıt kümesinin bir SNAPSHOT'ı (diğer source_snapshot_json
    # alanlarıyla AYNI semantik — "bu karar anında ne biliniyordu").
    source_urls: list[str] = []
    if company:
        source_urls = [
            s.source_url
            for s in db.query(db_models.ProspectSource)
            .filter(
                db_models.ProspectSource.tenant_id == tenant_id,
                db_models.ProspectSource.prospect_company_id == company.id,
            )
            .order_by(db_models.ProspectSource.discovered_at.desc())
            .all()
        ]

    try:
        sender_profile = get_sender_profile()
        draft = create_draft(
            company_name=company_name,
            sender_profile=sender_profile,
            sector=company.sector if company else None,
            city=company.city if company else None,
            contact_full_name=contact_full_name,
            template_name=template.name,
            template_version=template.version,
            subject_template=template.subject_template,
            body_template=template.body_template,
            use_ai=use_ai,
        )
    except SenderProfileIncompleteError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "sender_profile_incomplete", "missing_fields": e.missing_fields},
        )

    message = db_models.OutreachMessage(
        tenant_id=tenant_id,
        prospect_company_id=company.id if company else None,
        customer_id=customer.id if customer else None,
        contact_id=contact.id if contact else None,
        recipient_email_snapshot=compliance.normalized_email or candidate_email,
        recipient_legal_type=compliance.recipient_legal_type,
        recipient_category=compliance.recipient_category,
        channel="EMAIL",
        subject=draft.subject,
        body_snapshot=draft.editable_body,
        system_footer_snapshot=draft.system_footer,
        status="DRAFT",
        source_snapshot_json={
            "company_name": company_name,
            "sector": company.sector if company else None,
            "city": company.city if company else None,
            "contact_full_name": contact_full_name,
            "used_ai": draft.used_ai,
            "template_name": draft.template_name,
            "template_version": draft.template_version,
            "source_urls": source_urls,
        },
        compliance_snapshot_json=compliance.as_dict(),
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return _message_to_out(message)


def create_owner_controlled_test_draft(
    db: Session,
    tenant_id: str,
    *,
    test_recipient_email: str,
    use_ai: bool = False,
) -> OutreachMessageOut:
    """
    Owner'ın 'S5 PRE-DELIVERY HARDENING' talimatı (10.08) — create_draft_
    message()'dan TAMAMEN AYRI, açık bir test-yolu. Owner'ın gerekçesi:
    genel production fonksiyonuna gömülü bir "direct_email" parametresi
    (ilk yazımda vardı), ileride başka bir backend caller'ın YANLIŞLIKLA
    kullanabileceği GİZLİ bir bypass yüzeyi yaratır — bu fonksiyonun adı,
    imzası ve davranışı baştan sona "bu bir OWNER-CONTROLLED TEST'tir"
    der, gizli/genel bir kapı DEĞİLDİR.

    Zorunlu kısıtlar (owner'ın maddeleri, TÜMÜ burada kod-seviyesinde
    zorunlu kılınır — compliance engine'e "sonra bloke edilir" diye
    BIRAKILMAZ, bu fonksiyon KENDİSİ erken ve açıkça reddeder):
    - Yalnız settings.outreach_test_recipient_email_set İÇİNDEKİ bir
      adres kabul edilir; allowlist DIŞI → HTTPException 422 (erken red).
    - recipient_category HER ZAMAN "TEST_RECIPIENT"dir — fonksiyon
      imzasında çağıranın bunu override edebileceği bir parametre YOK.
    - Prospect/Customer kaydı OLUŞTURMAZ/OKUMAZ/DEĞİŞTİRMEZ —
      prospect_company_id/contact_id/customer_id HER ZAMAN None.
    - Suppression kontrolü YİNE tam uygulanır (evaluate_email_send_
      eligibility() aynı şekilde çağrılır — owner madde D: "TEST_RECIPIENT
      bile suppression'ı bypass edemez").
    - router.py/main.py'de HİÇBİR endpoint bunu ÇAĞIRMAZ — yalnız owner'ın
      açık talimatıyla, tek seferlik bir doğrulama script'inden çağrılır.

    create_draft_message()'ın NORMAL (Prospect/Customer) davranışını
    HİÇBİR ŞEKİLDE DEĞİŞTİRMEZ — ortak kod YOK, yalnız aynı draft/sender_
    profile altyapısını (create_draft/ensure_default_template/
    get_sender_profile) REUSE eder.

    Raises:
        HTTPException 422: email allowlist'te DEĞİLSE (erken red).
        HTTPException 422: sender_profile eksikse.
        HTTPException 500: (olması İMKANSIZ olması gereken) iç tutarsızlık
            — allowlist'teki bir adres compliance'ta TEST_RECIPIENT
            dönmezse (defense-in-depth, "asla güvenme" ilkesi).

    Çağrıldığı yerler:
    - (henüz yok) yalnız owner'ın açık "S5 — FINAL PROVIDER / DELIVERY
      GATE" STEP 3 talimatıyla, tek seferlik doğrulama script'i [S5-WB8]
    """
    normalized = normalize_email(test_recipient_email)
    if not normalized or normalized not in settings.outreach_test_recipient_email_set:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "not_an_owner_controlled_test_recipient",
                "message": "Bu adres OUTREACH_TEST_RECIPIENT_EMAILS whitelist'inde değil — reddedildi.",
            },
        )

    compliance = evaluate_email_send_eligibility(db, tenant_id, candidate_email=normalized)
    if compliance.recipient_category != "TEST_RECIPIENT":
        # Buraya asla ulaşılmamalı (yukarıdaki whitelist kontrolüyle AYNI
        # kaynağa bakıyor) — yine de "çağırana asla güvenme" ilkesi
        # gereği açıkça reddedilir, sessizce PROSPECT_RECIPIENT gibi
        # davranılmaz.
        raise HTTPException(status_code=500, detail={"error": "internal_recipient_category_mismatch"})

    template = ensure_default_template(db, tenant_id)
    try:
        sender_profile = get_sender_profile()
        draft = create_draft(
            company_name=None,
            sender_profile=sender_profile,
            contact_full_name=None,
            template_name=template.name,
            template_version=template.version,
            subject_template=template.subject_template,
            body_template=template.body_template,
            use_ai=use_ai,
        )
    except SenderProfileIncompleteError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "sender_profile_incomplete", "missing_fields": e.missing_fields},
        )

    message = db_models.OutreachMessage(
        tenant_id=tenant_id,
        prospect_company_id=None,
        customer_id=None,
        contact_id=None,
        recipient_email_snapshot=compliance.normalized_email or normalized,
        recipient_legal_type=compliance.recipient_legal_type,
        recipient_category="TEST_RECIPIENT",
        channel="EMAIL",
        subject=draft.subject,
        body_snapshot=draft.editable_body,
        system_footer_snapshot=draft.system_footer,
        status="DRAFT",
        source_snapshot_json={
            "owner_controlled_test": True,
            "used_ai": draft.used_ai,
            "template_name": draft.template_name,
            "template_version": draft.template_version,
        },
        compliance_snapshot_json=compliance.as_dict(),
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return _message_to_out(message)


def refresh_compliance_snapshot(db: Session, tenant_id: str, message_id: int) -> OutreachMessageOut:
    """
    UI'nin "güncel compliance durumunu göster" ihtiyacı için — mesajın
    durumunu DEĞİŞTİRMEZ, yalnız compliance_snapshot_json/recipient_
    legal_type/recipient_category alanlarını TAZE bir değerlendirmeyle
    günceller.

    Çağrıldığı yerler:
    - (henüz yok) GET /outreach/messages/{id}/compliance [S5-WB5/router]
    """
    message = _get_message_or_404(db, tenant_id, message_id)
    compliance = evaluate_email_send_eligibility(
        db, tenant_id,
        candidate_email=message.recipient_email_snapshot,
        prospect_company_id=message.prospect_company_id,
        contact_id=message.contact_id,
        customer_id=message.customer_id,
    )
    message.recipient_legal_type = compliance.recipient_legal_type
    message.recipient_category = compliance.recipient_category
    message.compliance_snapshot_json = compliance.as_dict()
    message.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(message)
    return _message_to_out(message)


def finalize_draft_message(
    db: Session,
    tenant_id: str,
    message_id: int,
    *,
    subject: Optional[str] = None,
    editable_body: Optional[str] = None,
) -> OutreachMessageOut:
    """
    Owner: taslak "user-editable" olmalı. DRAFT → READY_FOR_REVIEW.
    YALNIZ subject/body_snapshot (editable blok) değişir —
    system_footer_snapshot BU FONKSİYON TARAFINDAN ASLA YAZILMAZ (owner'ın
    "hukuki footer'ın bozulmasını engeller" ilkesi, veri katmanında ayrı
    kolonla zaten garanti altında — bkz. app/database.py).

    Çağrıldığı yerler:
    - (henüz yok) PATCH /outreach/messages/{id} [S5-WB5/router]
    """
    message = _get_message_or_404(db, tenant_id, message_id)
    if message.status != "DRAFT":
        raise HTTPException(
            status_code=409,
            detail={"error": "invalid_status_for_edit", "current_status": message.status, "expected": "DRAFT"},
        )
    if subject is not None:
        message.subject = subject
    if editable_body is not None:
        message.body_snapshot = editable_body
    message.status = "READY_FOR_REVIEW"
    message.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(message)
    return _message_to_out(message)


def approve_message(db: Session, tenant_id: str, message_id: int) -> OutreachMessageOut:
    """
    READY_FOR_REVIEW → APPROVED. Compliance TAZE yeniden değerlendirilir —
    can_send=false ise 409 ile REDDEDİLİR (owner: "frontend'in can_send=true
    beyanına asla güvenilmez" — backend burada da zorunlu kılar, yalnız
    send() anında değil).

    Çağrıldığı yerler:
    - (henüz yok) POST /outreach/messages/{id}/approve [S5-WB5/router]
    """
    message = _get_message_or_404(db, tenant_id, message_id)
    if message.status != "READY_FOR_REVIEW":
        raise HTTPException(
            status_code=409,
            detail={"error": "invalid_status_for_approve", "current_status": message.status, "expected": "READY_FOR_REVIEW"},
        )

    compliance = evaluate_email_send_eligibility(
        db, tenant_id,
        candidate_email=message.recipient_email_snapshot,
        prospect_company_id=message.prospect_company_id,
        contact_id=message.contact_id,
        customer_id=message.customer_id,
    )
    message.recipient_legal_type = compliance.recipient_legal_type
    message.recipient_category = compliance.recipient_category
    message.compliance_snapshot_json = compliance.as_dict()

    if not compliance.can_send:
        db.commit()  # snapshot güncellemesi kalıcı olsun, ama status DEĞİŞMEZ
        raise HTTPException(
            status_code=409,
            detail={"error": "compliance_blocked", "reason_codes": compliance.reason_codes},
        )

    message.status = "APPROVED"
    message.approved_at = datetime.utcnow()
    message.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(message)
    return _message_to_out(message)


def _resolve_customer_id_for_crm(db: Session, tenant_id: str, message: db_models.OutreachMessage) -> Optional[int]:
    """
    S5-WB6 (owner: CRM Activity/Task entegrasyonu). Mesaj doğrudan bir
    Customer'a bağlıysa onu döner; yalnız prospect_company_id varsa VE o
    ProspectCompany DAHA ÖNCE (bu outreach'ten bağımsız olarak) bir
    Customer'a dönüştürülmüşse o customer_id'yi döner. Aksi halde None —
    S2'nin Activity/Task şeması (customer_id/offer_id/contract_id typed-FK,
    prospect_company_id YOK) genişletilmiyor (owner: "S2 subject şemasını
    refactor etmeden").

    Çağrıldığı yerler:
    - _record_activity_for_send() [S5-WB6]
    - create_follow_up_task() [S5-WB6]
    """
    if message.customer_id is not None:
        return message.customer_id
    if message.prospect_company_id is None:
        return None
    company = (
        db.query(db_models.ProspectCompany)
        .filter(
            db_models.ProspectCompany.id == message.prospect_company_id,
            db_models.ProspectCompany.tenant_id == tenant_id,
        )
        .first()
    )
    return company.customer_id if company else None


def _record_activity_for_send(db: Session, tenant_id: str, message: db_models.OutreachMessage) -> None:
    """
    Owner WB6: başarılı gönderim sonrası EMAIL-tipi Activity oluşturur —
    YALNIZ Customer bağlamı çözümlenebiliyorsa (bkz. _resolve_customer_id_
    for_crm). Henüz dönüştürülmemiş Prospect için AYRI bir mekanizma
    GEREKMEZ — "Prospect-side timeline" zaten GET /outreach/messages?
    prospect_company_id=... ile (WB5) sağlanıyor.

    activity_type="EMAIL" kullanılır (crm/schemas.py::ActivityType'ın
    MEVCUT kapalı kümesi — owner'ın "EMAIL_SENT-type" ifadesi yeni bir enum
    değeri İCAT ETMEDEN title/body ile karşılanır, S2 şeması genişletilmez).

    Post-commit failure isolation (S1'in HIGH#2 kararıyla AYNI ilke —
    bkz. memory): bu fonksiyon send_message()'ın SENT commit'inden SONRA
    çağrılır ve kendi hatasını YUTAR — Activity oluşturma başarısız olursa
    gönderimin KENDİSİ asla etkilenmez, yalnız loglanır.

    Çağrıldığı yerler:
    - send_message() [S5-WB6]
    """
    try:
        customer_id = _resolve_customer_id_for_crm(db, tenant_id, message)
        if customer_id is None:
            return
        crm_service.create_activity(
            db, tenant_id,
            customer_id=customer_id, offer_id=None, contract_id=None,
            activity_type="EMAIL",
            title="Tanışma e-postası gönderildi (Outreach)",
            body=message.subject,
            occurred_at=message.sent_at,
        )
    except Exception:
        logger.exception(
            "outreach_message id=%s: CRM Activity oluşturma BAŞARISIZ — gönderimin kendisi ETKİLENMEDİ, yalnız loglanıyor.",
            message.id,
        )


def claim_message_for_sending(db: Session, tenant_id: str, message_id: int) -> Optional[db_models.OutreachMessage]:
    """
    Atomik compare-and-swap: yalnız status='APPROVED' olan bir mesajı
    'SENDING'e çeker ve HEMEN commit eder (provider.send()'DEN ÖNCE —
    owner'ın HIGH PRIORITY "double-click/retry idempotent" talebi).
    Aynı anda iki istek gelirse yalnız BİRİ rowcount=1 alır; diğeri None
    döner ve send() ASLA çağrılmaz.

    Çağrıldığı yerler:
    - send_message() [S5-WB5]
    """
    result = db.execute(
        sa.update(db_models.OutreachMessage)
        .where(
            db_models.OutreachMessage.id == message_id,
            db_models.OutreachMessage.tenant_id == tenant_id,
            db_models.OutreachMessage.status == "APPROVED",
        )
        .values(status="SENDING", updated_at=datetime.utcnow())
    )
    db.commit()
    if result.rowcount != 1:
        return None
    return (
        db.query(db_models.OutreachMessage)
        .filter(db_models.OutreachMessage.id == message_id, db_models.OutreachMessage.tenant_id == tenant_id)
        .first()
    )


def send_message(db: Session, tenant_id: str, message_id: int) -> OutreachMessageOut:
    """
    HIGH PRIORITY (owner). APPROVED → SENDING → SENT | FAILED.

    Sıra: 404 kontrolü → status=APPROVED kontrolü → compliance TAZE
    yeniden değerlendirme (HARD GATE, ikinci kez — approve ile send
    arasında durum değişmiş olabilir) → atomik SENDING claim (idempotency)
    → provider.send() → sonuca göre SENT/FAILED + commit.

    Raises:
        HTTPException 404: mesaj yok.
        HTTPException 409: status APPROVED değil, compliance blocked, veya
            claim başarısız (zaten SENDING/SENT — çift tıklama).

    Çağrıldığı yerler:
    - (henüz yok) POST /outreach/messages/{id}/send [S5-WB5/router, HIGH PRIORITY]
    """
    message = _get_message_or_404(db, tenant_id, message_id)
    if message.status != "APPROVED":
        raise HTTPException(
            status_code=409,
            detail={"error": "invalid_status_for_send", "current_status": message.status, "expected": "APPROVED"},
        )

    # HARD GATE — ikinci taze değerlendirme (owner: "hiçbir endpoint bypass edemez").
    compliance = evaluate_email_send_eligibility(
        db, tenant_id,
        candidate_email=message.recipient_email_snapshot,
        prospect_company_id=message.prospect_company_id,
        contact_id=message.contact_id,
        customer_id=message.customer_id,
    )
    if not compliance.can_send:
        message.compliance_snapshot_json = compliance.as_dict()
        db.commit()
        raise HTTPException(
            status_code=409,
            detail={"error": "compliance_blocked", "reason_codes": compliance.reason_codes},
        )

    claimed = claim_message_for_sending(db, tenant_id, message_id)
    if claimed is None:
        raise HTTPException(
            status_code=409,
            detail={"error": "already_claimed_or_not_approved", "message": "Bu mesaj zaten gönderiliyor/gönderildi ya da APPROVED durumunda değil."},
        )

    provider = get_outbound_mail_provider()
    full_body = f"{claimed.body_snapshot}\n\n{claimed.system_footer_snapshot}"
    try:
        result = provider.send(to_email=claimed.recipient_email_snapshot, subject=claimed.subject, body_text=full_body)
    except Exception:
        logger.critical(
            "outreach_message id=%s: provider.send() BEKLENMEDIK istisna firlatti — "
            "mesaj SENDING durumunda TAKILI kalmis olabilir, MANUEL kontrol gerekli.",
            message_id,
        )
        raise

    if result.success:
        claimed.status = "SENT"
        claimed.sent_at = datetime.utcnow()
        claimed.provider = "smtp"
        claimed.provider_message_id = result.provider_message_id
    else:
        claimed.status = "FAILED"
        claimed.failed_at = datetime.utcnow()
        claimed.failure_code = result.error_code
    claimed.updated_at = datetime.utcnow()

    try:
        db.commit()
    except Exception:
        logger.critical(
            "outreach_message id=%s: provider.send() sonucu=%s ama DB commit BASARISIZ — "
            "mesaj SENDING'de TAKILI KALDI, MANUEL kontrol gerekli (owner HIGH PRIORITY "
            "partial-failure senaryosu).",
            message_id, result.success,
        )
        raise

    db.refresh(claimed)
    if not result.success:
        raise HTTPException(
            status_code=502,
            detail={"error": "provider_send_failed", "error_code": result.error_code, "error_detail": result.error_detail},
        )

    # S5-WB6 (owner: CRM entegrasyonu) — SENT commit'i ZATEN kalıcı oldu;
    # bu, o başarıyı etkilemeyen, kendi hatasını yutan bir SONRAKİ adımdır.
    _record_activity_for_send(db, tenant_id, claimed)

    return _message_to_out(claimed)


def create_follow_up_task(
    db: Session, tenant_id: str, message_id: int, *, days_from_now: int = 3
) -> TaskOut:
    """
    Owner WB6: 'optional user-opt-in follow-up Task' — BİLEREK send_message()
    içine otomatik SARILMAZ; WB7 UI'nin AÇIK bir kullanıcı aksiyonuyla
    (örn. başarılı gönderim sonrası ayrı bir "Takip görevi oluştur" butonu)
    çağırması beklenir.

    Raises:
        HTTPException 422: Customer bağlamı çözümlenemezse (henüz
            dönüştürülmemiş Prospect — S2 şeması genişletilmiyor, bkz.
            _resolve_customer_id_for_crm()).

    Çağrıldığı yerler:
    - (henüz yok) POST /outreach/messages/{id}/follow-up-task [S5-WB6/router]
    """
    message = _get_message_or_404(db, tenant_id, message_id)
    customer_id = _resolve_customer_id_for_crm(db, tenant_id, message)
    if customer_id is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "follow_up_task_requires_customer",
                "message": "Takip görevi yalnız Müşteriye dönüştürülmüş kayıtlar için oluşturulabilir.",
            },
        )
    due_at = datetime.utcnow() + timedelta(days=days_from_now)
    return crm_service.create_task(
        db, tenant_id,
        customer_id=customer_id, offer_id=None, contract_id=None,
        title=f"Takip: {message.recipient_email_snapshot} — tanışma e-postası sonrası",
        description=f"Outreach mesaj #{message.id} — {message.subject}",
        due_at=due_at,
    )


def list_messages(
    db: Session,
    tenant_id: str,
    *,
    prospect_company_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[OutreachMessageOut]:
    """
    Owner'ın WB7 "outreach history view" ihtiyacı.

    Çağrıldığı yerler:
    - (henüz yok) GET /outreach/messages [S5-WB5/router]
    """
    query = db.query(db_models.OutreachMessage).filter(db_models.OutreachMessage.tenant_id == tenant_id)
    if prospect_company_id is not None:
        query = query.filter(db_models.OutreachMessage.prospect_company_id == prospect_company_id)
    if status is not None:
        query = query.filter(db_models.OutreachMessage.status == status)
    rows = (
        query.order_by(db_models.OutreachMessage.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_message_to_out(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# Suppression yönetimi (owner'ın WB7 "suppression yönetimi" ihtiyacı) —
# HARD GATE'in TEK doğruluk kaynağı bu tablodur (bkz. app/database.py
# SuppressionEntry docstring'i + app/outreach/compliance.py). Buradaki
# fonksiyonlar yalnız CRUD'dur — compliance kararının kendisi HER ZAMAN
# compliance.py'de, buradan bağımsız değerlendirilir.
# ═══════════════════════════════════════════════════════════════════════════


def _suppression_to_out(entry: db_models.SuppressionEntry) -> SuppressionEntryOut:
    return SuppressionEntryOut(
        id=entry.id,
        email_normalized=entry.email_normalized,
        reason=entry.reason,
        source=entry.source,
        note=entry.note,
        created_at=_iso(entry.created_at),
        effective_at=_iso(entry.effective_at),
    )


def add_suppression(
    db: Session, tenant_id: str, *, email: str, reason: str, note: Optional[str] = None, source: str = "manual"
) -> SuppressionEntryOut:
    """
    Owner: "aynı email için birden fazla suppression kaydı olabilir" —
    UNIQUE constraint BİLEREK yok (bkz. app/database.py docstring'i),
    bu yüzden burada da mevcut kayıt kontrolü/upsert YAPILMAZ, her çağrı
    yeni bir tarihsel satır ekler.

    Çağrıldığı yerler:
    - (henüz yok) POST /outreach/suppressions [S5-WB5/router]
    """
    normalized = normalize_email(email)
    if not normalized:
        raise HTTPException(status_code=422, detail={"error": "email_missing"})
    entry = db_models.SuppressionEntry(
        tenant_id=tenant_id, email_normalized=normalized, reason=reason, note=note, source=source,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _suppression_to_out(entry)


def list_suppressions(db: Session, tenant_id: str, *, limit: int = 100, offset: int = 0) -> list[SuppressionEntryOut]:
    """
    Çağrıldığı yerler:
    - (henüz yok) GET /outreach/suppressions [S5-WB5/router]
    """
    rows = (
        db.query(db_models.SuppressionEntry)
        .filter(db_models.SuppressionEntry.tenant_id == tenant_id)
        .order_by(db_models.SuppressionEntry.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_suppression_to_out(r) for r in rows]
