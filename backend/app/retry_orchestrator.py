"""
Retry Orchestrator - Sprint 8.0

Retry + Recompute koordinasyonu.
Tek entry point, tek otorite.

KONTRAT:
- RESOLVED kararını SADECE RecomputeService verir
- RetryExecutor sadece PENDING_RECOMPUTE set eder (success durumunda)
- Orchestrator ikisini koordine eder

Status akışı:
PENDING_RETRY → (retry fail) → PENDING_RETRY (backoff) veya OPEN (exhaust)
PENDING_RETRY → (retry success) → PENDING_RECOMPUTE
PENDING_RECOMPUTE → (recompute resolved) → RESOLVED
PENDING_RECOMPUTE → (recompute not resolved, attempts left) → PENDING_RETRY
PENDING_RECOMPUTE → (recompute not resolved, exhausted) → OPEN
PENDING_RECOMPUTE → (recompute reclassify) → primary/category update + above rules
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable, Any

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from .database import Incident
from .retry_executor import RetryExecutor, RetryResult, RetryResultStatus
from .recompute_service import (
    RecomputeContext,
    RecomputeResult,
    recompute_quality_flags,
    apply_recompute_result,
)
from .resolution_reasons import ResolutionReason, STUCK_THRESHOLD_MINUTES

logger = logging.getLogger(__name__)


# Recompute limit - sonsuz döngü koruması
MAX_RECOMPUTE_COUNT = 5


@dataclass
class OrchestrationResult:
    """Orchestration sonucu."""
    incident_id: int
    retry_success: bool
    final_status: str
    is_resolved: bool
    is_reclassified: bool
    is_exhausted: bool
    is_recompute_limited: bool
    error_message: Optional[str] = None


@dataclass
class BatchOrchestrationSummary:
    """Batch orchestration özeti."""
    claimed: int
    retry_success: int
    retry_fail: int
    resolved: int
    reclassified: int
    exhausted: int
    recompute_limited: int
    errors: int


class RetryOrchestrator:
    """
    Retry + Recompute koordinasyonu.
    
    Tek entry point: process_incident() veya run_batch()
    Tek otorite: RESOLVED sadece recompute'dan gelir
    
    Idempotency:
    - PENDING_RECOMPUTE zaten set ise yeniden set etmek sorun değil
    - Recompute tekrar koşarsa recompute_count artar ama sonuç deterministic
    """
    
    def __init__(
        self,
        executor: Optional[RetryExecutor] = None,
        context_provider: Optional[Callable[[Incident], RecomputeContext]] = None,
        worker_id: Optional[str] = None,
    ):
        """
        Args:
            executor: RetryExecutor instance (default: yeni oluşturur)
            context_provider: Incident'tan RecomputeContext üreten fonksiyon
            worker_id: Worker tanımlayıcı
        """
        self.executor = executor or RetryExecutor(worker_id=worker_id)
        self.context_provider = context_provider or self._default_context_provider
        self.worker_id = worker_id or self.executor.worker_id
    
    def _default_context_provider(self, incident: Incident) -> RecomputeContext:
        """
        Default context provider - incident'tan context üretir.
        
        Production'da bu fonksiyon:
        - Invoice'u storage'dan okur
        - Extraction/validation/calculation'ı yeniden koşar
        - Context döner
        
        Şimdilik: routed_payload'dan context çıkarır (varsa)
        """
        # routed_payload'dan context çıkar
        payload = incident.routed_payload or {}
        normalized_inputs = payload.get("issue", {}).get("normalized_inputs", {})
        
        return RecomputeContext(
            extraction=normalized_inputs.get("extraction", {}),
            validation=normalized_inputs.get("validation", {}),
            calculation=normalized_inputs.get("calculation"),
            calculation_error=normalized_inputs.get("calculation_error"),
            debug_meta=normalized_inputs.get("debug_meta"),
        )
    
    def process_incident(
        self,
        db: Session,
        incident_id: int,
        context: Optional[RecomputeContext] = None,
        now: Optional[datetime] = None,
    ) -> OrchestrationResult:
        """
        Tek bir incident için retry + recompute koordinasyonu.
        
        Flow:
        1. Incident'ı getir
        2. Retry execute
        3. Success ise recompute
        4. Sonucu uygula
        
        Args:
            db: Database session
            incident_id: Incident ID
            context: Recompute context (None ise provider'dan alınır)
            now: Şimdi (test için override)
        
        Returns:
            OrchestrationResult
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            return OrchestrationResult(
                incident_id=incident_id,
                retry_success=False,
                final_status="UNKNOWN",
                is_resolved=False,
                is_reclassified=False,
                is_exhausted=False,
                is_recompute_limited=False,
                error_message=f"Incident #{incident_id} not found",
            )
        
        # 1. Retry execute
        retry_result = self.executor.execute(incident)
        
        # 2. Retry sonucunu uygula (RESOLVED set etmez, PENDING_RECOMPUTE set eder)
        self.executor.apply_result(db, incident_id, retry_result, now)
        
        # Refresh incident
        db.refresh(incident)
        
        # 3. Fail ise burada bitir
        if retry_result.status != RetryResultStatus.SUCCESS:
            return OrchestrationResult(
                incident_id=incident_id,
                retry_success=False,
                final_status=incident.status,
                is_resolved=False,
                is_reclassified=False,
                is_exhausted=incident.retry_exhausted_at is not None,
                is_recompute_limited=False,
            )
        
        # 4. Success → Recompute
        # Context al
        if context is None:
            context = self.context_provider(incident)
        
        # Recompute limit kontrolü
        current_recompute = incident.recompute_count or 0
        if current_recompute >= MAX_RECOMPUTE_COUNT:
            # Sonsuz döngü koruması
            incident.status = "OPEN"
            incident.resolution_reason = ResolutionReason.RECOMPUTE_LIMIT_EXCEEDED
            incident.resolution_note = "recompute_limit_exceeded"
            incident.updated_at = now
            db.commit()
            
            logger.warning(
                f"[ORCHESTRATOR] Incident #{incident_id} recompute limit exceeded "
                f"(count={current_recompute})"
            )
            
            return OrchestrationResult(
                incident_id=incident_id,
                retry_success=True,
                final_status="OPEN",
                is_resolved=False,
                is_reclassified=False,
                is_exhausted=False,
                is_recompute_limited=True,
            )
        
        # Recompute çalıştır
        recompute_result = recompute_quality_flags(context)
        
        # Recompute sonucunu uygula
        apply_recompute_result(db, incident_id, recompute_result, now)
        
        # Refresh incident
        db.refresh(incident)
        
        # Sonuç
        return OrchestrationResult(
            incident_id=incident_id,
            retry_success=True,
            final_status=incident.status,
            is_resolved=recompute_result.is_resolved,
            is_reclassified=recompute_result.is_reclassified,
            is_exhausted=False,
            is_recompute_limited=False,
        )
    
    def run_batch(
        self,
        db: Session,
        tenant_id: str,
        now: Optional[datetime] = None,
        limit: int = 50,
    ) -> BatchOrchestrationSummary:
        """
        Batch retry + recompute orchestration.
        
        1. PENDING_RETRY incident'ları claim et
        2. Her biri için process_incident
        3. Özet döndür
        
        Args:
            db: Database session
            tenant_id: Tenant ID
            now: Şimdi (test için override)
            limit: Max batch size
        
        Returns:
            BatchOrchestrationSummary
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        
        summary = BatchOrchestrationSummary(
            claimed=0,
            retry_success=0,
            retry_fail=0,
            resolved=0,
            reclassified=0,
            exhausted=0,
            recompute_limited=0,
            errors=0,
        )
        
        # 1. Claim (executor üzerinden)
        incidents = self.executor.claim(db, tenant_id, now, limit)
        summary.claimed = len(incidents)
        
        if not incidents:
            logger.debug(f"[ORCHESTRATOR] No eligible incidents for tenant={tenant_id}")
            return summary
        
        # 2. Process each
        for incident in incidents:
            try:
                result = self.process_incident(db, incident.id, now=now)
                
                if result.error_message:
                    summary.errors += 1
                elif result.retry_success:
                    summary.retry_success += 1
                    if result.is_resolved:
                        summary.resolved += 1
                    if result.is_reclassified:
                        summary.reclassified += 1
                    if result.is_recompute_limited:
                        summary.recompute_limited += 1
                else:
                    summary.retry_fail += 1
                    if result.is_exhausted:
                        summary.exhausted += 1
                        
            except Exception as e:
                logger.exception(f"[ORCHESTRATOR] Error processing incident #{incident.id}")
                summary.errors += 1
                
                # Clear lock on error
                try:
                    incident.retry_lock_until = None
                    incident.retry_lock_by = None
                    db.commit()
                except:
                    pass
        
        logger.info(
            f"[ORCHESTRATOR] Batch complete: claimed={summary.claimed} "
            f"retry_success={summary.retry_success} retry_fail={summary.retry_fail} "
            f"resolved={summary.resolved} reclassified={summary.reclassified} "
            f"exhausted={summary.exhausted} recompute_limited={summary.recompute_limited} "
            f"errors={summary.errors}"
        )
        
        return summary
    
    def process_pending_recomputes(
        self,
        db: Session,
        tenant_id: str,
        stuck_threshold_minutes: int = STUCK_THRESHOLD_MINUTES,
        now: Optional[datetime] = None,
        limit: int = 50,
    ) -> int:
        """
        Stuck PENDING_RECOMPUTE incident'ları işle.
        
        Crash recovery: Recompute çağrısı crash ederse incident
        PENDING_RECOMPUTE'de kalır. Bu fonksiyon onları toplar.
        
        Args:
            db: Database session
            tenant_id: Tenant ID
            stuck_threshold_minutes: Kaç dakika sonra "stuck" sayılır
            now: Şimdi (test için override)
            limit: Max batch size
        
        Returns:
            İşlenen incident sayısı
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        threshold = now - timedelta(minutes=stuck_threshold_minutes)
        
        # Stuck PENDING_RECOMPUTE'ları bul
        stuck_incidents = (
            db.query(Incident)
            .filter(
                and_(
                    Incident.tenant_id == tenant_id,
                    Incident.status == "PENDING_RECOMPUTE",
                    Incident.updated_at < threshold,
                )
            )
            .limit(limit)
            .all()
        )
        
        if not stuck_incidents:
            return 0
        
        processed = 0
        for incident in stuck_incidents:
            try:
                # Context al
                context = self.context_provider(incident)
                
                # Recompute limit kontrolü
                current_recompute = incident.recompute_count or 0
                if current_recompute >= MAX_RECOMPUTE_COUNT:
                    incident.status = "OPEN"
                    incident.resolution_reason = ResolutionReason.RECOMPUTE_LIMIT_EXCEEDED
                    incident.resolution_note = "recompute_limit_exceeded"
                    incident.updated_at = now
                    db.commit()
                    processed += 1
                    continue
                
                # Recompute çalıştır
                recompute_result = recompute_quality_flags(context)
                apply_recompute_result(db, incident.id, recompute_result, now)
                processed += 1
                
            except Exception as e:
                logger.exception(
                    f"[ORCHESTRATOR] Error processing stuck incident #{incident.id}"
                )
        
        logger.info(
            f"[ORCHESTRATOR] Processed {processed} stuck PENDING_RECOMPUTE incidents"
        )
        
        return processed
