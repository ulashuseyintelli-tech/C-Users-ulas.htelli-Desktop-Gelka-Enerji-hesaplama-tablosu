"""
Incident Repository - Sprint 6.1

Incident CRUD + dedupe upsert logic.
24h TTL dedupe_bucket ile spam kontrolü.
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Any

from sqlalchemy.orm import Session
from sqlalchemy import and_

from .database import Incident
from .incident_keys import dedupe_key_v2
from .action_router import RoutedAction

logger = logging.getLogger(__name__)


# Status priority - conflict'te sadece "daha düşük priority'ye" geçiş engellenir
STATUS_PRIORITY = {
    "RESOLVED": 100,
    "ACK": 80,
    "REPORTED": 60,
    "PENDING_RETRY": 40,
    "OPEN": 20,
    "AUTO_RESOLVED": 10,
}


def get_epoch_day(dt: Optional[datetime] = None) -> int:
    """
    Epoch-day hesaplar (24h TTL için).
    
    Args:
        dt: Datetime (default: now UTC)
    
    Returns:
        Epoch-day (int)
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    return int(dt.timestamp() // 86400)


def can_transition_status(current_status: str, new_status: str) -> bool:
    """
    Status geçişi yapılabilir mi kontrol eder.
    
    Kural: Sadece "daha yüksek priority'ye" geçiş yapılabilir.
    Exception: OPEN → herhangi bir status'a geçiş her zaman OK.
    
    Args:
        current_status: Mevcut status
        new_status: Hedef status
    
    Returns:
        True if transition allowed
    """
    current_priority = STATUS_PRIORITY.get(current_status, 0)
    new_priority = STATUS_PRIORITY.get(new_status, 0)
    
    # OPEN'dan her yere geçiş OK
    if current_status == "OPEN":
        return True
    
    # Daha yüksek priority'ye geçiş OK
    return new_priority >= current_priority


def upsert_incident(
    db: Session,
    *,
    trace_id: str,
    tenant_id: str,
    provider: str,
    invoice_id: str,
    period: str,
    primary_flag: str,
    category: str,
    severity: str,
    message: str,
    action_type: str,
    action_owner: str,
    action_code: str,
    all_flags: list[str],
    secondary_flags: list[str],
    deduction_total: int,
    routed_action: RoutedAction,
    details: Optional[dict[str, Any]] = None,
    calc_context: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> tuple[int, bool]:
    """
    Incident upsert - dedupe ile.
    
    Behavior:
    - Yeni row: INSERT
    - Conflict (tenant_id, dedupe_key, dedupe_bucket): UPDATE
      - last_seen_at = now
      - occurrence_count += 1
      - status: sadece "daha yüksek priority'ye" geçiş
      - routed_payload: her zaman overwrite (daha zengin context olabilir)
    
    Args:
        db: Database session
        trace_id: Trace ID
        tenant_id: Tenant ID
        provider: Fatura sağlayıcı
        invoice_id: Fatura ID
        period: YYYY-MM
        primary_flag: Ana hata flag'i
        category: Incident kategorisi
        severity: S1/S2/S3/S4
        message: Incident mesajı
        action_type: USER_FIX/RETRY_LOOKUP/BUG_REPORT/FALLBACK_OK
        action_owner: user/extraction/tariff/market_price/calc
        action_code: HintCode
        all_flags: Tüm flag'ler
        secondary_flags: Primary hariç flag'ler
        deduction_total: Toplam puan düşümü
        routed_action: ActionRouter çıktısı
        details: Ek detaylar (opsiyonel)
        calc_context: Hesaplama context'i (opsiyonel)
        now: Şimdi (test için override)
    
    Returns:
        (incident_id, is_new) tuple
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    
    # Dedupe key ve bucket hesapla
    dedupe_key = dedupe_key_v2(
        provider=provider,
        invoice_id=invoice_id,
        primary_flag=primary_flag,
        category=category,
        action_code=action_code,
        period_yyyy_mm=period,
    )
    dedupe_bucket = get_epoch_day(now)
    
    # Mevcut incident'ı ara
    existing = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.dedupe_key == dedupe_key,
            Incident.dedupe_bucket == dedupe_bucket,
        )
    ).first()
    
    # Routed payload
    routed_payload = routed_action.to_dict() if routed_action else None
    
    # Status belirleme
    new_status = routed_action.status if routed_action else "OPEN"
    
    if existing:
        # UPDATE - dedupe hit
        existing.last_seen_at = now
        existing.occurrence_count = (existing.occurrence_count or 1) + 1
        existing.updated_at = now
        
        # Status transition kontrolü
        if can_transition_status(existing.status, new_status):
            existing.status = new_status
        
        # Routed payload policy:
        # - BUG_REPORT: ASLA overwrite etme (forensik kayıp riski)
        # - USER_FIX/RETRY_LOOKUP: overwrite OK (eligible_at güncellenmesi mantıklı)
        # - FALLBACK_OK: payload yok, skip
        if action_type == "BUG_REPORT":
            # BUG_REPORT payload sadece ilk kez set edilir
            if not existing.routed_payload:
                existing.routed_payload = routed_payload
            # else: mevcut payload'ı koru
        elif routed_payload:
            # USER_FIX, RETRY_LOOKUP: overwrite OK
            existing.routed_payload = routed_payload
        
        # Details merge (yeni bilgi ekle, eskiyi koru)
        if details:
            existing_details = existing.details_json or {}
            existing_details.update(details)
            existing.details_json = existing_details
        
        db.commit()
        
        logger.info(
            f"[INCIDENT] Dedupe hit: #{existing.id} "
            f"occurrence_count={existing.occurrence_count} "
            f"status={existing.status}"
        )
        
        return existing.id, False
    
    # INSERT - yeni incident
    incident = Incident(
        trace_id=trace_id,
        tenant_id=tenant_id,
        provider=provider,
        invoice_id=invoice_id,
        period=period,
        severity=severity,
        category=category,
        message=message,
        primary_flag=primary_flag,
        action_type=action_type,
        action_owner=action_owner,
        action_code=action_code,
        all_flags=all_flags,
        secondary_flags=secondary_flags,
        deduction_total=deduction_total,
        routed_payload=routed_payload,
        details_json=details,
        dedupe_key=dedupe_key,
        dedupe_bucket=dedupe_bucket,
        status=new_status,
        occurrence_count=1,
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    
    db.add(incident)
    db.commit()
    db.refresh(incident)
    
    logger.warning(
        f"[INCIDENT] Created: #{incident.id} {severity} {category} "
        f"primary_flag={primary_flag} status={new_status} "
        f"(trace={trace_id})"
    )
    
    return incident.id, True


def get_incident_by_id(db: Session, incident_id: int) -> Optional[Incident]:
    """ID ile incident getir."""
    return db.query(Incident).filter(Incident.id == incident_id).first()


def update_incident_status(
    db: Session,
    incident_id: int,
    new_status: str,
    resolution_note: Optional[str] = None,
    resolved_by: Optional[str] = None,
) -> bool:
    """
    Incident status güncelle.
    
    Args:
        db: Database session
        incident_id: Incident ID
        new_status: Yeni status
        resolution_note: Çözüm notu (opsiyonel)
        resolved_by: Çözen kişi (opsiyonel)
    
    Returns:
        True if updated, False if not found or transition not allowed
    """
    incident = get_incident_by_id(db, incident_id)
    if not incident:
        return False
    
    if not can_transition_status(incident.status, new_status):
        logger.warning(
            f"[INCIDENT] Status transition not allowed: "
            f"#{incident_id} {incident.status} → {new_status}"
        )
        return False
    
    incident.status = new_status
    incident.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    
    if resolution_note:
        incident.resolution_note = resolution_note
    if resolved_by:
        incident.resolved_by = resolved_by
    if new_status == "RESOLVED":
        incident.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
    
    db.commit()
    
    logger.info(f"[INCIDENT] Status updated: #{incident_id} → {new_status}")
    return True


def get_incidents_by_status(
    db: Session,
    tenant_id: str,
    status: str,
    limit: int = 100,
) -> list[Incident]:
    """Status'a göre incident'ları getir."""
    return (
        db.query(Incident)
        .filter(
            and_(
                Incident.tenant_id == tenant_id,
                Incident.status == status,
            )
        )
        .order_by(Incident.last_seen_at.desc())
        .limit(limit)
        .all()
    )


def get_pending_retries(
    db: Session,
    tenant_id: str,
    limit: int = 100,
) -> list[Incident]:
    """PENDING_RETRY status'undaki incident'ları getir."""
    return get_incidents_by_status(db, tenant_id, "PENDING_RETRY", limit)


def get_bug_reports(
    db: Session,
    tenant_id: str,
    limit: int = 100,
) -> list[Incident]:
    """REPORTED status'undaki incident'ları getir."""
    return get_incidents_by_status(db, tenant_id, "REPORTED", limit)


def count_incidents_by_action_type(
    db: Session,
    tenant_id: str,
    action_type: str,
    since: Optional[datetime] = None,
) -> int:
    """Action type'a göre incident sayısı."""
    query = db.query(Incident).filter(
        and_(
            Incident.tenant_id == tenant_id,
            Incident.action_type == action_type,
        )
    )
    if since:
        query = query.filter(Incident.created_at >= since)
    return query.count()


def count_bug_reports_last_24h(db: Session, tenant_id: str) -> int:
    """Son 24 saatteki BUG_REPORT sayısı."""
    since = datetime.now(timezone.utc).replace(tzinfo=None)
    since = datetime(since.year, since.month, since.day) - __import__('datetime').timedelta(days=1)
    return count_incidents_by_action_type(db, tenant_id, "BUG_REPORT", since)
