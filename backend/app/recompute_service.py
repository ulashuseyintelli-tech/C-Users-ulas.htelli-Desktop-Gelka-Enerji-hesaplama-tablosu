"""
Recompute Service - Sprint 7.1.2 + Sprint 8.1

Retry sonrası quality flag'leri yeniden hesaplar.
RESOLVED kararını kanıtlı hale getirir.

Karar matrisi:
- new_all_flags boş → RESOLVED (gerçekten çözüldü)
- new_primary_flag == old_primary_flag → PENDING_RETRY veya OPEN (exhaust'a göre)
- new_primary_flag != old_primary_flag → reclassify (aynı incident güncellenir)

Sprint 8.1 Güncellemeleri:
- ResolutionReason enum kullanımı
- RECLASSIFIED bir "çözüm" değil, bir "durum olayı"
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .database import Incident
from .incident_service import (
    calculate_quality_score,
    normalize_flags,
    select_primary_flag,
    get_secondary_flags,
    flag_to_category,
    get_action_recommendation,
    QualityScore,
    Severity,
)
from .resolution_reasons import ResolutionReason

logger = logging.getLogger(__name__)


@dataclass
class RecomputeResult:
    """Recompute sonucu."""
    new_all_flags: list[str]
    new_primary_flag: Optional[str]
    new_category: Optional[str]
    new_severity: Optional[str]
    quality_score: int
    is_resolved: bool
    is_reclassified: bool
    old_primary_flag: Optional[str] = None


@dataclass
class RecomputeContext:
    """Recompute için gerekli context."""
    extraction: dict
    validation: dict
    calculation: Optional[dict]
    calculation_error: Optional[str]
    debug_meta: Optional[dict]


def recompute_quality_flags(context: RecomputeContext) -> RecomputeResult:
    """
    Quality flag'leri yeniden hesaplar.
    
    Args:
        context: Extraction, validation, calculation context
    
    Returns:
        RecomputeResult with new flags and resolution status
    """
    quality = calculate_quality_score(
        extraction=context.extraction,
        validation=context.validation,
        calculation=context.calculation,
        calculation_error=context.calculation_error,
        debug_meta=context.debug_meta,
    )
    
    # Critical flags only (S1, S2)
    critical_flags = [
        fd["code"] for fd in quality.flag_details
        if fd["severity"] in [Severity.S1, Severity.S2]
    ]
    
    all_flags = normalize_flags(critical_flags)
    primary_flag = select_primary_flag(critical_flags)
    
    if not all_flags:
        # No flags = resolved
        return RecomputeResult(
            new_all_flags=[],
            new_primary_flag=None,
            new_category=None,
            new_severity=None,
            quality_score=quality.score,
            is_resolved=True,
            is_reclassified=False,
        )
    
    category = flag_to_category(primary_flag) if primary_flag else None
    
    # Get severity from flag details
    severity = Severity.S2
    for fd in quality.flag_details:
        if fd["code"] == primary_flag:
            severity = fd["severity"]
            break
    
    return RecomputeResult(
        new_all_flags=all_flags,
        new_primary_flag=primary_flag,
        new_category=category,
        new_severity=severity,
        quality_score=quality.score,
        is_resolved=False,
        is_reclassified=False,
    )


def apply_recompute_result(
    db: Session,
    incident_id: int,
    result: RecomputeResult,
    now: Optional[datetime] = None,
) -> bool:
    """
    Recompute sonucunu incident'a uygular.
    
    Karar matrisi:
    - is_resolved=True → status=RESOLVED
    - primary_flag değişti → reclassify (update incident)
    - primary_flag aynı → status değişmez (retry executor karar verir)
    
    Args:
        db: Database session
        incident_id: Incident ID
        result: RecomputeResult
        now: Şimdi (test için override)
    
    Returns:
        True if incident was updated
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        logger.error(f"[RECOMPUTE] Incident #{incident_id} not found")
        return False
    
    old_primary = incident.primary_flag
    result.old_primary_flag = old_primary
    
    # Recompute count artır
    incident.recompute_count = (incident.recompute_count or 0) + 1
    incident.updated_at = now
    
    if result.is_resolved:
        # Gerçekten çözüldü - flag yok
        incident.status = "RESOLVED"
        incident.resolved_at = now
        incident.resolution_reason = ResolutionReason.RECOMPUTE_RESOLVED
        incident.resolution_note = "Resolved by recompute (no flags remaining)"
        
        logger.info(
            f"[RECOMPUTE] Incident #{incident_id} RESOLVED "
            f"(old_primary={old_primary}, recompute_count={incident.recompute_count})"
        )
        
        db.commit()
        return True
    
    # Flag var - reclassify mi?
    if result.new_primary_flag != old_primary:
        # Reclassification - KONTRAT: status değişmez, sadece primary güncellenir
        result.is_reclassified = True
        
        incident.previous_primary_flag = old_primary
        incident.reclassified_at = now
        incident.primary_flag = result.new_primary_flag
        incident.category = result.new_category
        incident.severity = result.new_severity
        incident.all_flags = result.new_all_flags
        incident.secondary_flags = get_secondary_flags(
            result.new_all_flags, result.new_primary_flag
        )
        
        # Action bilgilerini güncelle
        action_info = get_action_recommendation(result.new_primary_flag)
        incident.action_type = action_info["type"]
        incident.action_owner = action_info["owner"]
        incident.action_code = action_info["code"]
        
        # RECLASSIFIED bir "çözüm" değil - resolution_reason set et ama status değişmez
        # NOT: resolution_reason sadece bilgi amaçlı, status RESOLVED değilse çözüm sayılmaz
        
        logger.warning(
            f"[RECOMPUTE] Incident #{incident_id} RECLASSIFIED: "
            f"{old_primary} → {result.new_primary_flag}"
        )
        
        db.commit()
        return True
    
    # Primary aynı - sadece recompute_count güncellendi
    logger.info(
        f"[RECOMPUTE] Incident #{incident_id} same primary: {old_primary} "
        f"(recompute_count={incident.recompute_count})"
    )
    
    db.commit()
    return True


def check_resolution_by_recompute(
    context: RecomputeContext,
    old_primary_flag: str,
) -> tuple[bool, bool, Optional[str]]:
    """
    Recompute ile çözüm kontrolü yapar.
    
    Args:
        context: Recompute context
        old_primary_flag: Mevcut primary flag
    
    Returns:
        (is_resolved, is_reclassified, new_primary_flag)
    """
    result = recompute_quality_flags(context)
    
    if result.is_resolved:
        return True, False, None
    
    if result.new_primary_flag != old_primary_flag:
        return False, True, result.new_primary_flag
    
    return False, False, old_primary_flag
