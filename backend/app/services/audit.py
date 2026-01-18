"""
Audit Log Service

Kim ne zaman ne yaptı - tüm önemli aksiyonları logla.
"""
import logging
from datetime import datetime
from typing import Optional, Any
from sqlalchemy.orm import Session

from ..models import AuditAction

logger = logging.getLogger(__name__)


def log_action(
    db: Session,
    action: AuditAction,
    tenant_id: str = "default",
    actor_type: str = "system",
    actor_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> int:
    """
    Audit log kaydı oluştur.
    
    Args:
        db: Database session
        action: AuditAction enum değeri
        tenant_id: Tenant ID
        actor_type: user, system, api_key, webhook
        actor_id: Aktörün ID'si
        target_type: invoice, offer, customer
        target_id: Hedefin ID'si
        details: Ek detaylar (JSON)
        ip_address: İstek IP adresi
        user_agent: User agent string
    
    Returns:
        Oluşturulan audit log ID'si
    """
    from ..database import AuditLog
    
    audit = AuditLog(
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details_json=details,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    
    db.add(audit)
    db.commit()
    db.refresh(audit)
    
    logger.info(
        f"Audit: {action.value} | tenant={tenant_id} | actor={actor_type}:{actor_id} | "
        f"target={target_type}:{target_id}"
    )
    
    return audit.id


def get_audit_logs(
    db: Session,
    tenant_id: str,
    action: Optional[AuditAction] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = 0,
    limit: int = 100,
) -> list:
    """
    Audit logları filtrele ve getir.
    """
    from ..database import AuditLog
    
    query = db.query(AuditLog).filter(AuditLog.tenant_id == tenant_id)
    
    if action:
        query = query.filter(AuditLog.action == action)
    if target_type:
        query = query.filter(AuditLog.target_type == target_type)
    if target_id:
        query = query.filter(AuditLog.target_id == target_id)
    if start_date:
        query = query.filter(AuditLog.created_at >= start_date)
    if end_date:
        query = query.filter(AuditLog.created_at <= end_date)
    
    return query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()
