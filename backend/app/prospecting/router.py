"""
S4 — Prospecting — API endpoint'leri.

Tenant izolasyonu: crm/router.py ile AYNI desen — contracts modülünün
_require_default_tenant_boundary/_require_contracts_key'i REUSE edilir
(import edilir, kopyalanmaz). Gerekçe crm/router.py ile birebir aynı:
conversion sonrası Customer subject'i devreye girebilir ve Customer
tablosunda tenant_id yok — bu yüzden bu router da SADECE default tenant
ile çalışır (SINGLE GELKA TENANT).

Çağrıldığı yerler: app/main.py (app.include_router(prospecting_router)).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..contracts.router import _require_contracts_key, _require_default_tenant_boundary
from ..database import get_db
from ..services.tenant import get_tenant_id
from . import service
from .schemas import (
    ConvertRequest,
    ConvertResponse,
    DisqualifyRequest,
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

prospecting_router = APIRouter(
    prefix="/prospects",
    tags=["prospecting"],
    dependencies=[Depends(_require_default_tenant_boundary)],
)


@prospecting_router.post("/discover", response_model=DiscoverResponse)
def discover_prospects(
    req: DiscoverRequest,
    _key: Optional[str] = Depends(_require_contracts_key),
):
    """
    DB'ye YAZMAZ — yalnız aday listesi. Owner: "Discovery results must
    NOT be written directly to DB." Bkz. app/prospecting/service.py
    discover() ve discovery.py modül docstring'i (best-effort arama,
    CAPTCHA bypass edilmez).
    """
    return service.discover(req)


@prospecting_router.post("", response_model=ProspectCreateResponse)
def create_prospect(
    req: ProspectCompanyCreateRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """Dedup-gated create — bkz. app/prospecting/service.py create_prospect() docstring'i."""
    return service.create_prospect(db, tenant_id, req)


@prospecting_router.get("", response_model=ProspectCompanyListResponse)
def list_prospects(
    status: Optional[str] = Query(default=None, pattern="^(DISCOVERED|VERIFIED|QUALIFIED|DISQUALIFIED|CONVERTED)$"),
    city: Optional[str] = Query(default=None, max_length=100),
    sector: Optional[str] = Query(default=None, max_length=255),
    search: Optional[str] = Query(default=None, max_length=255),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.list_prospects(db, tenant_id, status=status, city=city, sector=sector, search=search, skip=skip, limit=limit)


@prospecting_router.get("/{prospect_id}", response_model=ProspectCompanyOut)
def get_prospect(
    prospect_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.get_prospect(db, tenant_id, prospect_id)


@prospecting_router.put("/{prospect_id}", response_model=ProspectCompanyOut)
def update_prospect(
    prospect_id: int,
    req: ProspectCompanyUpdateRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.update_prospect(db, tenant_id, prospect_id, req)


@prospecting_router.post("/{prospect_id}/verify", response_model=ProspectCompanyOut)
def verify_prospect(
    prospect_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.verify_prospect(db, tenant_id, prospect_id)


@prospecting_router.post("/{prospect_id}/qualify", response_model=ProspectCompanyOut)
def qualify_prospect(
    prospect_id: int,
    req: QualifyRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """Owner: black-box auto-disqualify YOK — bu her zaman açık bir kullanıcı aksiyonudur."""
    return service.qualify_prospect(db, tenant_id, prospect_id, req)


@prospecting_router.post("/{prospect_id}/disqualify", response_model=ProspectCompanyOut)
def disqualify_prospect(
    prospect_id: int,
    req: DisqualifyRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.disqualify_prospect(db, tenant_id, prospect_id, req)


@prospecting_router.post("/{prospect_id}/enrich", response_model=EnrichResponse)
def enrich_prospect(
    prospect_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """Bounded website enrichment (max 3 sayfa) — bkz. app/prospecting/enrichment.py."""
    return service.enrich_prospect(db, tenant_id, prospect_id)


@prospecting_router.post("/{prospect_id}/convert", response_model=ConvertResponse)
def convert_prospect(
    prospect_id: int,
    req: ConvertRequest,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    """
    İki aşamalı olabilir: mevcut Customer eşleşmesi bulunursa
    status="confirmation_required" döner (hiçbir şey YAZILMAZ) — kullanıcı
    ya existing_customer_id seçer ya da force_create_new_customer=true ile
    tekrar çağırır. Bkz. app/prospecting/service.py convert_to_customer().
    """
    return service.convert_to_customer(db, tenant_id, prospect_id, req)


@prospecting_router.get("/{prospect_id}/contacts", response_model=list[ProspectContactOut])
def list_prospect_contacts(
    prospect_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.list_contacts(db, tenant_id, prospect_id)


@prospecting_router.get("/{prospect_id}/sources", response_model=list[ProspectSourceOut])
def list_prospect_sources(
    prospect_id: int,
    tenant_id: str = Depends(get_tenant_id),
    _key: Optional[str] = Depends(_require_contracts_key),
    db: Session = Depends(get_db),
):
    return service.list_sources(db, tenant_id, prospect_id)
