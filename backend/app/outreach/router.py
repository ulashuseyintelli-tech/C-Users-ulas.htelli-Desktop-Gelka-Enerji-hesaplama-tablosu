"""
S5 — Outreach — API endpoint'leri.

Tenant izolasyonu: app/prospecting/router.py ile AYNI desen —
contracts modülünün _require_default_tenant_boundary/_require_contracts_key'i
REUSE edilir (import edilir, kopyalanmaz). SINGLE GELKA TENANT devam ediyor.

HARD GATE tekrarı (owner: "frontend'in can_send=true beyanına asla
güvenilmez"): approve/send endpoint'leri service.py içinde compliance'ı
HER ZAMAN TAZE yeniden değerlendirir — bu router hiçbir GET/POST parametresi
üzerinden bu kontrolü BYPASS ETMEZ (bkz. app/outreach/service.py docstring'i).

Çağrıldığı yerler: app/main.py (app.include_router(outreach_router)).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..contracts.router import _require_contracts_key, _require_default_tenant_boundary
from ..crm.schemas import TaskOut
from ..database import get_db
from ..services.tenant import get_tenant_id
from . import service
from .schemas import (
    CreateDraftRequest,
    FinalizeDraftRequest,
    OutreachMessageOut,
    SuppressionCreateRequest,
    SuppressionEntryOut,
)

outreach_router = APIRouter(
    prefix="/outreach",
    tags=["outreach"],
    dependencies=[Depends(_require_default_tenant_boundary)],
)


@outreach_router.post("/messages", response_model=OutreachMessageOut)
def create_draft_message(
    req: CreateDraftRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """
    Taslak oluşturur — HENÜZ gönderilmez. Bkz. app/outreach/service.py
    create_draft_message() docstring'i (compliance burada yalnız
    görüntüleme amaçlı, gerçek gate approve/send'de).
    """
    return service.create_draft_message(
        db, tenant_id,
        prospect_company_id=req.prospect_company_id,
        contact_id=req.contact_id,
        customer_id=req.customer_id,
        use_ai=req.use_ai,
    )


@outreach_router.get("/messages", response_model=list[OutreachMessageOut])
def list_messages(
    prospect_company_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(
        default=None,
        pattern="^(DRAFT|READY_FOR_REVIEW|APPROVED|SENDING|SENT|FAILED|BOUNCED|REPLIED|SUPPRESSED|CANCELLED)$",
    ),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """Owner'ın WB7 'outreach history view' ihtiyacı — Prospect detay ekranında geçmiş gönderimler."""
    return service.list_messages(db, tenant_id, prospect_company_id=prospect_company_id, status=status, offset=skip, limit=limit)


@outreach_router.get("/messages/{message_id}", response_model=OutreachMessageOut)
def get_message(
    message_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.get_message(db, tenant_id, message_id)


@outreach_router.get("/messages/{message_id}/compliance", response_model=OutreachMessageOut)
def refresh_compliance(
    message_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """
    Owner: 'compliance-state display' — UI'nin TAZE durumu görebilmesi
    için (recipient/source/contact-type/recipient-type/IYS/KVKK/suppression/
    CAN SEND-BLOCKED). Mesajın status'unu DEĞİŞTİRMEZ.
    """
    return service.refresh_compliance_snapshot(db, tenant_id, message_id)


@outreach_router.patch("/messages/{message_id}", response_model=OutreachMessageOut)
def finalize_draft(
    message_id: int,
    req: FinalizeDraftRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """Owner: taslak 'user-editable' olmalı. DRAFT → READY_FOR_REVIEW. Yalnız editable blok değişir."""
    return service.finalize_draft_message(db, tenant_id, message_id, subject=req.subject, editable_body=req.editable_body)


@outreach_router.post("/messages/{message_id}/approve", response_model=OutreachMessageOut)
def approve_message(
    message_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """
    Owner: Approve ve Send AYRI, insan-tetiklemeli aksiyonlardır. Compliance
    burada TAZE yeniden değerlendirilir — can_send=false ise 409.
    """
    return service.approve_message(db, tenant_id, message_id)


@outreach_router.post("/messages/{message_id}/send", response_model=OutreachMessageOut)
def send_message(
    message_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """
    HIGH PRIORITY (owner) — gerçek SMTP gönderimini tetikler. Yalnız
    status=APPROVED olan bir mesaj için çalışır, atomik SENDING claim ile
    çift-tıklama/retry'a karşı idempotenttir (bkz. app/outreach/service.py
    send_message() docstring'i). BACKEND-ENFORCED — frontend'in gösterdiği
    can_send değeri burada TEKRAR doğrulanır, güvenilmez.
    """
    return service.send_message(db, tenant_id, message_id)


@outreach_router.post("/messages/{message_id}/follow-up-task", response_model=TaskOut)
def create_follow_up_task(
    message_id: int,
    days_from_now: int = Query(default=3, ge=1, le=90),
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """
    Owner: 'optional user-opt-in follow-up Task' — YALNIZ kullanıcı bu
    endpoint'i AÇIKÇA çağırırsa oluşur, send() otomatik tetiklemez.
    """
    return service.create_follow_up_task(db, tenant_id, message_id, days_from_now=days_from_now)


@outreach_router.post("/suppressions", response_model=SuppressionEntryOut)
def create_suppression(
    req: SuppressionCreateRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """Owner'ın WB7 'suppression yönetimi' ihtiyacı — manuel do-not-contact ekleme."""
    return service.add_suppression(db, tenant_id, email=req.email, reason=req.reason, note=req.note, source="manual")


@outreach_router.get("/suppressions", response_model=list[SuppressionEntryOut])
def list_suppressions(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.list_suppressions(db, tenant_id, offset=skip, limit=limit)
