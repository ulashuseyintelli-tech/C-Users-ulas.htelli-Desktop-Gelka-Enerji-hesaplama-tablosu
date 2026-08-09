"""
S2 — Activity & Task Engine — API endpoint'leri.

Tenant izolasyonu: contracts modülünün _require_default_tenant_boundary'si
REUSE edilir (import edilir, kopyalanmaz — kod tekrarından kaçınma, owner
kararı). Gerekçe: Activity/Task subject'lerinden biri Customer olabilir ve
Customer tablosunda tenant_id yok (contracts modülüyle AYNI risk) — bu
yüzden CRM router'ı da SADECE default tenant ile çalışır (SINGLE GELKA
TENANT, S1/S2 owner kararı).

Çağrıldığı yerler: app/main.py (app.include_router(crm_router)).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..contracts.router import _require_default_tenant_boundary, _require_contracts_key
from ..database import get_db
from ..services.tenant import get_tenant_id
from . import service
from .schemas import (
    ActivityCreateRequest,
    ActivityOut,
    PipelineResponse,
    TaskCreateRequest,
    TaskOut,
    TaskUpdateRequest,
    TodayResponse,
)

crm_router = APIRouter(
    prefix="/crm",
    tags=["crm"],
    dependencies=[Depends(_require_default_tenant_boundary)],
)


@crm_router.post("/activities", response_model=ActivityOut)
def create_activity(
    req: ActivityCreateRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.create_activity(
        db, tenant_id, req.customer_id, req.offer_id, req.contract_id,
        req.activity_type, req.title, req.body, req.occurred_at,
    )


@crm_router.get("/activities", response_model=list[ActivityOut])
def list_activities(
    customer_id: Optional[int] = Query(default=None),
    offer_id: Optional[int] = Query(default=None),
    contract_id: Optional[int] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """
    Subject bazlı kronolojik (newest-first) timeline. Offer subject'inde
    audit_logs (OFFER_STATUS_CHANGED) ile bu tablo BİRLEŞTİRİLİR — bkz.
    app/crm/service.py list_activities_for_subject().
    """
    return service.list_activities_for_subject(
        db, tenant_id, customer_id, offer_id, contract_id, skip, limit,
    )


@crm_router.post("/tasks", response_model=TaskOut)
def create_task(
    req: TaskCreateRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.create_task(
        db, tenant_id, req.customer_id, req.offer_id, req.contract_id,
        req.title, req.description, req.due_at,
    )


@crm_router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    customer_id: Optional[int] = Query(default=None),
    offer_id: Optional[int] = Query(default=None),
    contract_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None, pattern="^(OPEN|COMPLETED|CANCELLED)$"),
    due_today: bool = Query(default=False),
    overdue: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.list_tasks(
        db, tenant_id, customer_id, offer_id, contract_id, status, due_today, overdue, skip, limit,
    )


@crm_router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    req: TaskUpdateRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    # "due_at alanı hiç gönderilmedi" ile "due_at=null gönderildi (temizle)"
    # ayrımı için model_fields_set kullanılır — PATCH semantiğine uygun.
    due_at_provided = "due_at" in req.model_fields_set
    return service.update_task(
        db, tenant_id, task_id, req.title, req.description, req.due_at, due_at_provided,
    )


@crm_router.post("/tasks/{task_id}/complete", response_model=TaskOut)
def complete_task(
    task_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """Idempotent — bkz. app/crm/service.py complete_task() docstring'i."""
    return service.complete_task(db, tenant_id, task_id)


@crm_router.post("/tasks/{task_id}/cancel", response_model=TaskOut)
def cancel_task(
    task_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.cancel_task(db, tenant_id, task_id)


@crm_router.get("/today", response_model=TodayResponse)
def get_today(
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """S2 "Bugün" projeksiyonu — bkz. app/crm/service.py get_today()."""
    return service.get_today(db, tenant_id)


@crm_router.get("/pipeline", response_model=PipelineResponse)
def get_pipeline(
    customer_search: Optional[str] = Query(default=None, max_length=255),
    stage: Optional[str] = Query(default=None, pattern="^(DRAFT|SENT|ACCEPTED|CONTRACT|COMPLETED|LOST)$"),
    offer_status: Optional[str] = Query(default=None),
    has_contract: Optional[bool] = Query(default=None),
    overdue_only: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """S3 "Sales Pipeline" projeksiyonu — bkz. app/crm/service.py get_pipeline()."""
    return service.get_pipeline(
        db, tenant_id, customer_search, stage, offer_status, has_contract, overdue_only, skip, limit,
    )
