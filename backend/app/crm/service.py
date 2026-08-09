"""
S2 — Activity & Task Engine — iş mantığı.

Çağrıldığı yerler: app/crm/router.py (tüm CRM endpoint'leri).
"""
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import database as db_models
from ..models import AuditAction
from .schemas import ActivityOut, PipelineCardOut, PipelineResponse, PipelineStage, PipelineWarning, TaskOut

# S2 preflight: backend'de tek bir merkezi timezone yardımcı fonksiyonu yok
# (4 farklı "şu an" idiomu tespit edildi), ama ZoneInfo("Europe/Istanbul")
# PTF/recon modüllerinde zaten kullanılan bir desen. "Bugün" sınırı İÇİN
# BU kullanılıyor — DB'ye YAZILAN hiçbir değer bu tz'ye çevrilmiyor (DB
# istisnasız naive UTC kalır, owner kararı: repo-geneli timezone refactor
# yapılmadı, backward-compatible kalındı).
_TR_TZ = ZoneInfo("Europe/Istanbul")


def _now_utc() -> datetime:
    return datetime.utcnow()


def _today_utc_bounds_tr() -> tuple[datetime, datetime]:
    """
    Türkiye yerel gününün [başlangıç, bitiş) sınırlarını, DB'nin naive-UTC
    kolonlarıyla karşılaştırılabilir naive-UTC datetime'lara çevirir.
    """
    now_tr = datetime.now(_TR_TZ)
    start_tr = now_tr.replace(hour=0, minute=0, second=0, microsecond=0)
    end_tr = start_tr.replace(hour=23, minute=59, second=59, microsecond=999999)
    start_utc = start_tr.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    end_utc = end_tr.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return start_utc, end_utc


def validate_subject_exists(
    db: Session,
    tenant_id: str,
    customer_id: Optional[int],
    offer_id: Optional[int],
    contract_id: Optional[int],
) -> None:
    """
    Subject existence fail-closed (owner talimatı). Customer tablosunda
    tenant_id YOK (S1/Contract modülünde zaten belgelenmiş, tenant'sız
    havuz) — bu yüzden yalnız id ile kontrol edilir; router seviyesindeki
    _require_default_tenant_boundary (contracts.router'dan reuse) zaten
    SADECE default tenant isteklerine izin veriyor. Offer/Contract'ta
    tenant_id VAR — cross-tenant leakage'a karşı defense-in-depth olarak
    ayrıca filtrelenir (Contract modülünün GET /{id} deseniyle aynı).
    """
    if customer_id is not None:
        exists = db.query(db_models.Customer.id).filter(db_models.Customer.id == customer_id).first()
        if not exists:
            raise HTTPException(status_code=404, detail={"error": "subject_not_found", "message": f"Müşteri bulunamadı: {customer_id}"})
    elif offer_id is not None:
        exists = db.query(db_models.Offer.id).filter(
            db_models.Offer.id == offer_id, db_models.Offer.tenant_id == tenant_id
        ).first()
        if not exists:
            raise HTTPException(status_code=404, detail={"error": "subject_not_found", "message": f"Teklif bulunamadı: {offer_id}"})
    elif contract_id is not None:
        exists = db.query(db_models.Contract.id).filter(
            db_models.Contract.id == contract_id, db_models.Contract.tenant_id == tenant_id
        ).first()
        if not exists:
            raise HTTPException(status_code=404, detail={"error": "subject_not_found", "message": f"Sözleşme bulunamadı: {contract_id}"})
    else:
        # Şema seviyesinde zaten SubjectMixin validator'ı bunu engeller;
        # burada yalnız defense-in-depth (doğrudan service çağrılarına karşı).
        raise HTTPException(status_code=400, detail={"error": "subject_required", "message": "customer_id, offer_id veya contract_id belirtilmeli."})


def _activity_to_out(a: "db_models.Activity") -> ActivityOut:
    return ActivityOut(
        id=a.id, customer_id=a.customer_id, offer_id=a.offer_id, contract_id=a.contract_id,
        activity_type=a.activity_type, title=a.title, body=a.body,
        occurred_at=a.occurred_at.isoformat(), created_at=a.created_at.isoformat(),
        source="manual",
    )


def create_activity(
    db: Session,
    tenant_id: str,
    customer_id: Optional[int],
    offer_id: Optional[int],
    contract_id: Optional[int],
    activity_type: str,
    title: Optional[str],
    body: Optional[str],
    occurred_at: Optional[datetime],
) -> ActivityOut:
    validate_subject_exists(db, tenant_id, customer_id, offer_id, contract_id)

    activity = db_models.Activity(
        tenant_id=tenant_id,
        customer_id=customer_id, offer_id=offer_id, contract_id=contract_id,
        activity_type=activity_type, title=title, body=body,
        occurred_at=occurred_at or _now_utc(),
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return _activity_to_out(activity)


def _offer_status_audit_activities(db: Session, tenant_id: str, offer_id: int) -> list[ActivityOut]:
    """
    Offer subject'i için audit_logs'taki OFFER_STATUS_CHANGED kayıtlarını
    ActivityOut şekline PROJECTION eder — activities tablosuna DUPLICATE
    YAZILMAZ (owner kararı, S2 preflight: audit zaten organik olarak var).
    """
    rows = db.query(db_models.AuditLog).filter(
        db_models.AuditLog.tenant_id == tenant_id,
        db_models.AuditLog.action == AuditAction.OFFER_STATUS_CHANGED,
        db_models.AuditLog.target_type == "offer",
        db_models.AuditLog.target_id == str(offer_id),
    ).all()

    out = []
    for r in rows:
        details = r.details_json or {}
        old_s, new_s = details.get("old_status"), details.get("new_status")
        title = f"Durum değişikliği: {old_s} → {new_s}" if old_s else f"Durum: {new_s}"
        out.append(ActivityOut(
            id=r.id, customer_id=None, offer_id=offer_id, contract_id=None,
            activity_type="STATUS_CHANGE", title=title, body=details.get("notes"),
            occurred_at=r.created_at.isoformat(), created_at=r.created_at.isoformat(),
            source="audit",
        ))
    return out


def list_activities_for_subject(
    db: Session,
    tenant_id: str,
    customer_id: Optional[int],
    offer_id: Optional[int],
    contract_id: Optional[int],
    skip: int = 0,
    limit: int = 50,
) -> list[ActivityOut]:
    validate_subject_exists(db, tenant_id, customer_id, offer_id, contract_id)

    query = db.query(db_models.Activity).filter(db_models.Activity.tenant_id == tenant_id)
    if customer_id is not None:
        query = query.filter(db_models.Activity.customer_id == customer_id)
    elif offer_id is not None:
        query = query.filter(db_models.Activity.offer_id == offer_id)
    elif contract_id is not None:
        query = query.filter(db_models.Activity.contract_id == contract_id)

    manual = [_activity_to_out(a) for a in query.all()]

    combined = manual
    if offer_id is not None:
        combined = combined + _offer_status_audit_activities(db, tenant_id, offer_id)

    # newest-first (mevcut proje konvansiyonu: GET /customers, /offers,
    # /api/contracts hepsi created_at.desc() kullanıyor)
    combined.sort(key=lambda a: a.occurred_at, reverse=True)
    return combined[skip: skip + limit]


def _task_to_out(t: "db_models.Task") -> TaskOut:
    return TaskOut(
        id=t.id, customer_id=t.customer_id, offer_id=t.offer_id, contract_id=t.contract_id,
        title=t.title, description=t.description,
        due_at=t.due_at.isoformat() if t.due_at else None,
        status=t.status, completed_at=t.completed_at.isoformat() if t.completed_at else None,
        created_at=t.created_at.isoformat(), updated_at=t.updated_at.isoformat(),
    )


def create_task(
    db: Session,
    tenant_id: str,
    customer_id: Optional[int],
    offer_id: Optional[int],
    contract_id: Optional[int],
    title: str,
    description: Optional[str],
    due_at: Optional[datetime],
) -> TaskOut:
    validate_subject_exists(db, tenant_id, customer_id, offer_id, contract_id)

    task = db_models.Task(
        tenant_id=tenant_id,
        customer_id=customer_id, offer_id=offer_id, contract_id=contract_id,
        title=title, description=description, due_at=due_at, status="OPEN",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _task_to_out(task)


def list_tasks(
    db: Session,
    tenant_id: str,
    customer_id: Optional[int] = None,
    offer_id: Optional[int] = None,
    contract_id: Optional[int] = None,
    status: Optional[str] = None,
    due_today: bool = False,
    overdue: bool = False,
    skip: int = 0,
    limit: int = 50,
) -> list[TaskOut]:
    query = db.query(db_models.Task).filter(db_models.Task.tenant_id == tenant_id)
    if customer_id is not None:
        query = query.filter(db_models.Task.customer_id == customer_id)
    if offer_id is not None:
        query = query.filter(db_models.Task.offer_id == offer_id)
    if contract_id is not None:
        query = query.filter(db_models.Task.contract_id == contract_id)
    if status is not None:
        query = query.filter(db_models.Task.status == status)

    if due_today:
        start, end = _today_utc_bounds_tr()
        query = query.filter(db_models.Task.status == "OPEN", db_models.Task.due_at >= start, db_models.Task.due_at <= end)
    elif overdue:
        start, _end = _today_utc_bounds_tr()
        query = query.filter(db_models.Task.status == "OPEN", db_models.Task.due_at < start)

    tasks = query.order_by(db_models.Task.due_at.asc().nulls_last()).offset(skip).limit(limit).all()
    return [_task_to_out(t) for t in tasks]


def get_task_or_404(db: Session, tenant_id: str, task_id: int) -> "db_models.Task":
    task = db.query(db_models.Task).filter(db_models.Task.id == task_id, db_models.Task.tenant_id == tenant_id).first()
    if not task:
        raise HTTPException(status_code=404, detail={"error": "task_not_found", "message": f"Görev bulunamadı: {task_id}"})
    return task


def update_task(
    db: Session, tenant_id: str, task_id: int,
    title: Optional[str], description: Optional[str], due_at: Optional[datetime], due_at_provided: bool,
) -> TaskOut:
    task = get_task_or_404(db, tenant_id, task_id)
    if task.status != "OPEN":
        raise HTTPException(status_code=409, detail={"error": "task_not_editable", "message": f"'{task.status}' durumundaki görev düzenlenemez."})

    if title is not None:
        task.title = title
    if description is not None:
        task.description = description
    if due_at_provided:
        task.due_at = due_at
    task.updated_at = _now_utc()
    db.commit()
    db.refresh(task)
    return _task_to_out(task)


def complete_task(db: Session, tenant_id: str, task_id: int) -> TaskOut:
    """
    Idempotent: Task zaten COMPLETED ise no-op (completed_at KORUNUR,
    yeni bir TASK_COMPLETED Activity ÜRETİLMEZ) — owner talimatı.
    """
    task = get_task_or_404(db, tenant_id, task_id)

    if task.status == "COMPLETED":
        return _task_to_out(task)  # idempotent no-op
    if task.status == "CANCELLED":
        raise HTTPException(status_code=409, detail={"error": "task_cancelled", "message": "İptal edilmiş görev tamamlanamaz."})

    now = _now_utc()
    task.status = "COMPLETED"
    task.completed_at = now
    task.updated_at = now
    db.commit()
    db.refresh(task)

    # Activity=TASK_COMPLETED üret (owner: "gerekiyorsa") — subject'i
    # Task'ınkiyle AYNI. Best-effort DEĞİL: bu S2'nin kendi yeni yazma
    # yolu, offer_lifecycle'daki gibi mevcut bir akışı kesintiye
    # uğratma riski yok, o yüzden ayrı try/except sarmalamaya gerek yok.
    completion_activity = db_models.Activity(
        tenant_id=tenant_id,
        customer_id=task.customer_id, offer_id=task.offer_id, contract_id=task.contract_id,
        activity_type="TASK_COMPLETED", title=f"Görev tamamlandı: {task.title}", body=None,
        occurred_at=now,
    )
    db.add(completion_activity)
    db.commit()

    return _task_to_out(task)


def cancel_task(db: Session, tenant_id: str, task_id: int) -> TaskOut:
    task = get_task_or_404(db, tenant_id, task_id)
    if task.status == "COMPLETED":
        raise HTTPException(status_code=409, detail={"error": "task_completed", "message": "Tamamlanmış görev iptal edilemez."})
    if task.status == "CANCELLED":
        return _task_to_out(task)  # idempotent no-op

    task.status = "CANCELLED"
    task.updated_at = _now_utc()
    db.commit()
    db.refresh(task)
    return _task_to_out(task)


def get_today(db: Session, tenant_id: str) -> dict:
    """
    S2 "Bugün" projeksiyonu — ayrı tablo yok, mevcut Task/Activity/S1
    verisinden N+1 üretmeden türetilir (owner kararı, madde 3+4).
    """
    from ..services.offer_lifecycle import OPEN_OFFER_STATUSES

    start, end = _today_utc_bounds_tr()

    due_today_tasks = list_tasks(db, tenant_id, due_today=True, limit=200)
    overdue_tasks = list_tasks(db, tenant_id, overdue=True, limit=200)

    recent_activity_rows = db.query(db_models.Activity).filter(
        db_models.Activity.tenant_id == tenant_id
    ).order_by(db_models.Activity.occurred_at.desc()).limit(10).all()
    recent_activities = [_activity_to_out(a) for a in recent_activity_rows]

    total_customers = db.query(db_models.Customer.id).count()
    total_open_offers = db.query(db_models.Offer.id).filter(
        db_models.Offer.tenant_id == tenant_id, db_models.Offer.status.in_(OPEN_OFFER_STATUSES)
    ).count()
    total_accepted_offers = db.query(db_models.Offer.id).filter(
        db_models.Offer.tenant_id == tenant_id, db_models.Offer.status == "accepted"
    ).count()
    total_finalized_contracts = db.query(db_models.Contract.id).filter(
        db_models.Contract.tenant_id == tenant_id, db_models.Contract.status == "FINALIZED"
    ).count()

    return {
        "due_today_count": len(due_today_tasks),
        "overdue_count": len(overdue_tasks),
        "due_today_tasks": due_today_tasks,
        "overdue_tasks": overdue_tasks,
        "recent_activities": recent_activities,
        "total_customers": total_customers,
        "total_open_offers": total_open_offers,
        "total_accepted_offers": total_accepted_offers,
        "total_finalized_contracts": total_finalized_contracts,
    }


# =============================================================================
# S3 — Sales Pipeline — derived projection.
#
# Yeni tablo/kolon YOK (owner kararı, S3 GO madde 1). Her çağrıda Offer/
# Contract/Activity/Task'ten anlık hesaplanır, hiçbir şey yazılmaz.
#
# Çağrıldığı yerler:
# - app/crm/router.py get_pipeline() → GET /crm/pipeline
# =============================================================================

_TERMINAL_LOST_STATUSES = ("rejected", "expired")


def _compute_pipeline_stage(
    offer_status: str, contract_statuses: list[str]
) -> tuple[PipelineStage, Optional[PipelineWarning]]:
    """
    Pipeline stage + (precedence-kaynaklı) warning hesaplar — owner'ın S3 GO
    madde 3'teki precedence kuralının BİREBİR uygulanışı. Contract truth
    Offer.status'tan üstündür: bir offer'a bağlı herhangi bir Contract
    FINALIZED ise stage=COMPLETED, Offer.status ne derse desin.

    Saf fonksiyon (DB erişimi yok) — Test bölümündeki precedence/lost
    senaryoları doğrudan bunu çağırarak izole test edilir.
    """
    if contract_statuses:
        if "FINALIZED" in contract_statuses:
            return "COMPLETED", None
        return "CONTRACT", None

    if offer_status == "draft":
        return "DRAFT", None
    if offer_status in ("sent", "viewed"):
        return "SENT", None
    if offer_status == "accepted":
        return "ACCEPTED", None
    if offer_status == "contracting":
        return "ACCEPTED", "CONTRACT_STATUS_WITHOUT_CONTRACT"
    if offer_status == "completed":
        return "COMPLETED", "COMPLETED_WITHOUT_CONTRACT"
    if offer_status in _TERMINAL_LOST_STATUSES:
        return "LOST", None

    # Owner ilkesi: "Hiçbir kayıt sessizce kaybolmayacak". offer_lifecycle.py
    # sabit 8 durum tanımlıyor, bu dal pratikte tetiklenmemeli — yalnız
    # defense-in-depth (yeni bir OfferStatus icat edilmeden, sessizce
    # düşen kayıt olmasın diye).
    return "DRAFT", "UNKNOWN_OFFER_STATUS"


def get_pipeline(
    db: Session,
    tenant_id: str,
    customer_search: Optional[str] = None,
    stage: Optional[str] = None,
    offer_status: Optional[str] = None,
    has_contract: Optional[bool] = None,
    overdue_only: bool = False,
    skip: int = 0,
    limit: int = 50,
) -> PipelineResponse:
    """
    GET /crm/pipeline projeksiyonu. N+1 YOK (owner madde 7): offer listesi
    TEK sorgu, Contract/Activity/Task/Customer enrichment'ları TOPLU (bulk)
    sorgularla toplanır — offer sayısından bağımsız sabit sorgu sayısı.

    pipeline_stage DB kolonu olmadığı için (owner madde 1: yeni kolon yok)
    stage filtresi Python tarafında, offer_status/customer_search DB
    seviyesinde uygulanır. Bu CRM'in mevcut ölçeğinde (S1/list_customers,
    list_offers ile aynı desen — tüm satırlar tek sorguda çekilip Python'da
    işlenir) performans riski yaratmaz.
    """
    from ..services.offer_lifecycle import VALID_OFFER_TRANSITIONS

    query = db.query(db_models.Offer).filter(db_models.Offer.tenant_id == tenant_id)
    if offer_status is not None:
        query = query.filter(db_models.Offer.status == offer_status)
    if customer_search:
        query = query.join(
            db_models.Customer, db_models.Offer.customer_id == db_models.Customer.id
        ).filter(db_models.Customer.name.ilike(f"%{customer_search}%"))

    offers = query.order_by(db_models.Offer.created_at.desc()).all()
    if not offers:
        return PipelineResponse(cards=[], total=0)

    offer_ids = [o.id for o in offers]
    customer_ids = {o.customer_id for o in offers if o.customer_id is not None}

    # --- Bulk 1: Contract (offer_id -> [status, ...]) ---
    contract_rows = db.query(
        db_models.Contract.id, db_models.Contract.offer_id, db_models.Contract.status
    ).filter(db_models.Contract.offer_id.in_(offer_ids)).all()
    contracts_by_offer: dict[int, list[tuple[int, str]]] = {}
    for c_id, o_id, c_status in contract_rows:
        contracts_by_offer.setdefault(o_id, []).append((c_id, c_status))

    # --- Bulk 2: Customer adları ---
    customer_names = {
        row.id: row.name
        for row in db.query(db_models.Customer.id, db_models.Customer.name)
        .filter(db_models.Customer.id.in_(customer_ids)).all()
    } if customer_ids else {}

    # --- Bulk 3: Activity — offer_id VEYA customer_id subject'li, subject
    # başına EN YENİSİ. "last_activity": bir offer'ın kendisine VEYA ait
    # olduğu müşteriye en son ne yapıldığını gösterir (satış temsilcisi
    # açısından en anlamlı yorum — owner spec'i offer-only/customer-only
    # ayrımını netleştirmedi, bu S3 preflight'ının makul kararıdır).
    activity_filter = db_models.Activity.offer_id.in_(offer_ids)
    if customer_ids:
        activity_filter = activity_filter | db_models.Activity.customer_id.in_(customer_ids)
    activity_rows = db.query(db_models.Activity).filter(
        db_models.Activity.tenant_id == tenant_id, activity_filter
    ).order_by(db_models.Activity.occurred_at.desc()).all()
    latest_activity_by_offer: dict[int, "db_models.Activity"] = {}
    latest_activity_by_customer: dict[int, "db_models.Activity"] = {}
    for a in activity_rows:
        if a.offer_id is not None and a.offer_id not in latest_activity_by_offer:
            latest_activity_by_offer[a.offer_id] = a
        if a.customer_id is not None and a.customer_id not in latest_activity_by_customer:
            latest_activity_by_customer[a.customer_id] = a

    # --- Bulk 4: Task (OPEN) — offer_id VEYA customer_id subject'li ---
    task_filter = db_models.Task.offer_id.in_(offer_ids)
    if customer_ids:
        task_filter = task_filter | db_models.Task.customer_id.in_(customer_ids)
    open_task_rows = db.query(db_models.Task).filter(
        db_models.Task.tenant_id == tenant_id, db_models.Task.status == "OPEN", task_filter
    ).order_by(db_models.Task.due_at.asc().nulls_last()).all()
    next_task_by_offer: dict[int, "db_models.Task"] = {}
    next_task_by_customer: dict[int, "db_models.Task"] = {}
    overdue_count_by_offer: dict[int, int] = {}
    overdue_count_by_customer: dict[int, int] = {}
    # "overdue" tanımı S2'nin get_today()/list_tasks(overdue=True) ile
    # BİREBİR aynı olmalı (TR-yerel gün başlangıcından önce) — aksi halde
    # aynı görev Bugün ekranında ve Pipeline kartında FARKLI sayılır.
    today_start_utc, _today_end_utc = _today_utc_bounds_tr()
    for t in open_task_rows:
        if t.offer_id is not None:
            if t.offer_id not in next_task_by_offer:
                next_task_by_offer[t.offer_id] = t
            if t.due_at is not None and t.due_at < today_start_utc:
                overdue_count_by_offer[t.offer_id] = overdue_count_by_offer.get(t.offer_id, 0) + 1
        if t.customer_id is not None:
            if t.customer_id not in next_task_by_customer:
                next_task_by_customer[t.customer_id] = t
            if t.due_at is not None and t.due_at < today_start_utc:
                overdue_count_by_customer[t.customer_id] = overdue_count_by_customer.get(t.customer_id, 0) + 1

    cards: list[PipelineCardOut] = []
    for o in offers:
        contracts = contracts_by_offer.get(o.id, [])
        contract_statuses = [s for _id, s in contracts]
        pipeline_stage, precedence_warning = _compute_pipeline_stage(o.status, contract_statuses)

        # MISSING_CUSTOMER precedence warning'inden DÜŞÜK öncelikli — bkz.
        # schemas.py PipelineCardOut.pipeline_warning docstring'i.
        warning = precedence_warning
        if warning is None and o.customer_id is None:
            warning = "MISSING_CUSTOMER"

        # Kartta gösterilecek TEK contract: FINALIZED varsa o, yoksa en son
        # oluşturulan (en yüksek id ~ en son eklenen, created_at yok bu
        # projeksiyonda ekstra sorguya gerek bırakmamak için id kullanıldı).
        shown_contract_id: Optional[int] = None
        shown_contract_status: Optional[str] = None
        if contracts:
            finalized = [c for c in contracts if c[1] == "FINALIZED"]
            chosen = max(finalized, key=lambda c: c[0]) if finalized else max(contracts, key=lambda c: c[0])
            shown_contract_id, shown_contract_status = chosen

        last_activity_row = latest_activity_by_offer.get(o.id) or (
            latest_activity_by_customer.get(o.customer_id) if o.customer_id is not None else None
        )
        next_task_row = next_task_by_offer.get(o.id) or (
            next_task_by_customer.get(o.customer_id) if o.customer_id is not None else None
        )
        overdue_total = overdue_count_by_offer.get(o.id, 0) + (
            overdue_count_by_customer.get(o.customer_id, 0) if o.customer_id is not None else 0
        )

        cards.append(PipelineCardOut(
            offer_id=o.id,
            customer_id=o.customer_id,
            customer_name=customer_names.get(o.customer_id) if o.customer_id is not None else None,
            offer_date=o.created_at.isoformat(),
            offer_total=o.offer_total,
            agreement_multiplier=o.agreement_multiplier,
            offer_status=o.status,
            pipeline_stage=pipeline_stage,
            pipeline_warning=warning,
            has_contract=bool(contracts),
            contract_id=shown_contract_id,
            contract_status=shown_contract_status,
            last_activity=_activity_to_out(last_activity_row) if last_activity_row else None,
            next_open_task=_task_to_out(next_task_row) if next_task_row else None,
            overdue_task_count=overdue_total,
            allowed_transitions=VALID_OFFER_TRANSITIONS.get(o.status, []),
        ))

    # Post-filter: pipeline_stage/has_contract/overdue DB kolonu değil
    # (yeni kolon yok, owner madde 1) — Python tarafında filtrelenir.
    if stage is not None:
        cards = [c for c in cards if c.pipeline_stage == stage]
    if has_contract is not None:
        cards = [c for c in cards if c.has_contract == has_contract]
    if overdue_only:
        cards = [c for c in cards if c.overdue_task_count > 0]

    total = len(cards)
    return PipelineResponse(cards=cards[skip: skip + limit], total=total)
