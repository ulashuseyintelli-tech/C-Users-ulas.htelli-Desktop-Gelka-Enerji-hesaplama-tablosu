"""
Retry Executor - Sprint 7.0

PENDING_RETRY incident'larını yeniden dener.
Race-safe: row locking ile concurrent execution koruması.

RETRY SEMANTICS (önemli):
========================
BACKOFF_MINUTES = [30, 120, 360, 1440]  # 4 schedule
MAX_RETRY_ATTEMPTS = 4  # 4 kez retry denenir

Timeline:
- İlk fail → attempt_count=1 → eligible_at = now + 30m
- 2. fail  → attempt_count=2 → eligible_at = now + 120m  
- 3. fail  → attempt_count=3 → eligible_at = now + 360m
- 4. fail  → attempt_count=4 → EXHAUST (status=OPEN, exhausted_at set)

Yani: 4 retry denemesi yapılır. 4. deneme de fail olursa exhaust.
attempt_count = "kaç kez fail oldu" sayacı.
attempt_count >= MAX_RETRY_ATTEMPTS → exhaust.
"""

import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Callable, Any

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, text

from .database import Incident
from .resolution_reasons import ResolutionReason

logger = logging.getLogger(__name__)


class RetryResultStatus(Enum):
    """Retry sonuç durumu."""
    SUCCESS = "success"
    FAIL = "fail"
    EXCEPTION = "exception"


@dataclass
class RetryResult:
    """Retry denemesi sonucu."""
    status: RetryResultStatus
    message: str = ""
    new_primary_flag: Optional[str] = None  # Sprint 7.1'de kullanılacak


@dataclass
class BatchSummary:
    """Batch çalıştırma özeti."""
    claimed: int
    success: int
    fail: int
    exhausted: int
    errors: int


def generate_worker_id() -> str:
    """Unique worker ID üretir (hostname:pid:uuid)."""
    hostname = socket.gethostname()[:20]
    pid = os.getpid()
    short_uuid = uuid.uuid4().hex[:8]
    return f"{hostname}:{pid}:{short_uuid}"


class RetryExecutor:
    """
    PENDING_RETRY incident'larını yeniden dener.
    
    Backoff stratejisi (4 retry denemesi):
    - 1. fail → attempt=1 → 30 dakika sonra retry
    - 2. fail → attempt=2 → 2 saat sonra retry
    - 3. fail → attempt=3 → 6 saat sonra retry
    - 4. fail → attempt=4 → EXHAUST (status=OPEN, manual review)
    
    NOT: 4. fail'de exhaust olur, +1440m backoff KULLANILMAZ.
    BACKOFF_MINUTES[3] (1440m) sadece attempt=3 sonrası için.
    
    Race koruması:
    - PostgreSQL: SELECT ... FOR UPDATE SKIP LOCKED
    - SQLite: Optimistic locking with retry_lock_until check
    """
    
    # Backoff süreleri (dakika): attempt 1,2,3 sonrası kullanılır
    # attempt=4 fail olursa exhaust, backoff yok
    BACKOFF_MINUTES = [30, 120, 360, 1440]  # 30m, 2h, 6h, 24h
    
    # Kaç retry denemesi yapılacak (4. fail = exhaust)
    MAX_RETRY_ATTEMPTS = 4
    
    LOCK_MINUTES = 5
    
    def __init__(
        self,
        *,
        lookup_executor: Optional[Callable[[Incident], RetryResult]] = None,
        worker_id: Optional[str] = None,
    ):
        """
        Args:
            lookup_executor: Gerçek lookup yapan fonksiyon (test için mock)
            worker_id: Worker tanımlayıcı (debug için)
        """
        self.lookup_executor = lookup_executor or self._default_lookup_executor
        self.worker_id = worker_id or generate_worker_id()
    
    def _default_lookup_executor(self, incident: Incident) -> RetryResult:
        """
        Default lookup executor - gerçek provider'ları çağırır.
        
        Sprint 7.0'da basit implementasyon:
        - action_type'a göre market_price veya tariff lookup
        - Gerçek provider entegrasyonu Sprint 7.1'de
        """
        # TODO: Sprint 7.1'de gerçek provider entegrasyonu
        # Şimdilik her zaman fail döner (test için override edilecek)
        return RetryResult(
            status=RetryResultStatus.FAIL,
            message="Default executor - no real provider configured",
        )
    
    def _is_postgres(self, db: Session) -> bool:
        """PostgreSQL mi kontrol et."""
        dialect = db.bind.dialect.name if db.bind else "sqlite"
        return dialect == "postgresql"
    
    def _get_lock_expiry(self, now: datetime) -> datetime:
        """Lock expiry hesapla."""
        return now + timedelta(minutes=self.LOCK_MINUTES)
    
    def _get_backoff_minutes(self, attempt_count: int) -> int:
        """Attempt count'a göre backoff süresi."""
        if attempt_count < 0:
            attempt_count = 0
        if attempt_count >= len(self.BACKOFF_MINUTES):
            return self.BACKOFF_MINUTES[-1]
        return self.BACKOFF_MINUTES[attempt_count]
    
    def claim(
        self,
        db: Session,
        tenant_id: str,
        now: Optional[datetime] = None,
        limit: int = 50,
    ) -> list[Incident]:
        """
        Eligible incident'ları claim et (lock al).
        
        Kurallar:
        - status = PENDING_RETRY
        - retry_eligible_at <= now
        - retry_lock_until IS NULL OR retry_lock_until < now
        - retry_exhausted_at IS NULL
        
        PostgreSQL: FOR UPDATE SKIP LOCKED
        SQLite: Optimistic locking
        
        Args:
            db: Database session
            tenant_id: Tenant ID
            now: Şimdi (test için override)
            limit: Max claim sayısı
        
        Returns:
            Claimed incident listesi
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        lock_expiry = self._get_lock_expiry(now)
        
        if self._is_postgres(db):
            return self._claim_postgres(db, tenant_id, now, lock_expiry, limit)
        else:
            return self._claim_sqlite(db, tenant_id, now, lock_expiry, limit)
    
    def _claim_postgres(
        self,
        db: Session,
        tenant_id: str,
        now: datetime,
        lock_expiry: datetime,
        limit: int,
    ) -> list[Incident]:
        """PostgreSQL: FOR UPDATE SKIP LOCKED ile atomic claim."""
        # Raw SQL for SKIP LOCKED (SQLAlchemy ORM doesn't support it directly)
        sql = text("""
            SELECT id FROM incidents
            WHERE tenant_id = :tenant_id
            AND status = 'PENDING_RETRY'
            AND retry_eligible_at <= :now
            AND (retry_lock_until IS NULL OR retry_lock_until < :now)
            AND retry_exhausted_at IS NULL
            ORDER BY retry_eligible_at ASC
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
        """)
        
        result = db.execute(sql, {
            "tenant_id": tenant_id,
            "now": now,
            "limit": limit,
        })
        
        incident_ids = [row[0] for row in result]
        
        if not incident_ids:
            return []
        
        # Lock the claimed incidents
        incidents = db.query(Incident).filter(Incident.id.in_(incident_ids)).all()
        
        for incident in incidents:
            incident.retry_lock_until = lock_expiry
            incident.retry_lock_by = self.worker_id
        
        db.commit()
        
        logger.info(f"[RETRY] Claimed {len(incidents)} incidents (postgres)")
        return incidents
    
    def _claim_sqlite(
        self,
        db: Session,
        tenant_id: str,
        now: datetime,
        lock_expiry: datetime,
        limit: int,
    ) -> list[Incident]:
        """SQLite: Optimistic locking ile claim."""
        # First, get eligible candidates
        candidates = (
            db.query(Incident)
            .filter(
                and_(
                    Incident.tenant_id == tenant_id,
                    Incident.status == "PENDING_RETRY",
                    Incident.retry_eligible_at <= now,
                    or_(
                        Incident.retry_lock_until.is_(None),
                        Incident.retry_lock_until < now,
                    ),
                    Incident.retry_exhausted_at.is_(None),
                )
            )
            .order_by(Incident.retry_eligible_at.asc())
            .limit(limit)
            .all()
        )
        
        claimed = []
        
        for candidate in candidates:
            # Optimistic lock: try to claim
            # Re-check conditions and set lock atomically
            result = db.execute(
                text("""
                    UPDATE incidents
                    SET retry_lock_until = :lock_expiry,
                        retry_lock_by = :worker_id
                    WHERE id = :id
                    AND (retry_lock_until IS NULL OR retry_lock_until < :now)
                """),
                {
                    "id": candidate.id,
                    "lock_expiry": lock_expiry,
                    "worker_id": self.worker_id,
                    "now": now,
                }
            )
            
            if result.rowcount == 1:
                # Refresh to get updated values
                db.refresh(candidate)
                claimed.append(candidate)
        
        db.commit()
        
        logger.info(f"[RETRY] Claimed {len(claimed)} incidents (sqlite)")
        return claimed
    
    def execute(self, incident: Incident) -> RetryResult:
        """
        Tek bir incident için retry dene.
        
        Args:
            incident: Retry edilecek incident
        
        Returns:
            RetryResult
        """
        try:
            return self.lookup_executor(incident)
        except Exception as e:
            logger.exception(f"[RETRY] Exception during retry for incident #{incident.id}")
            return RetryResult(
                status=RetryResultStatus.EXCEPTION,
                message=str(e)[:500],
            )
    
    def apply_result(
        self,
        db: Session,
        incident_id: int,
        result: RetryResult,
        now: Optional[datetime] = None,
    ) -> None:
        """
        Retry sonucunu incident'a uygula.
        
        KONTRAT (Sprint 8.0):
        - RetryExecutor ASLA RESOLVED set etmez
        - Success → PENDING_RECOMPUTE + retry_success=True
        - RESOLVED kararını SADECE RecomputeService verir
        
        Success:
        - status = PENDING_RECOMPUTE (not RESOLVED!)
        - retry_success = True
        - retry_eligible_at = NULL
        - lock cleared
        
        Fail:
        - retry_attempt_count += 1
        - retry_success = False
        - Eğer attempt_count < MAX: retry_eligible_at = now + backoff, status=PENDING_RETRY
        - Eğer attempt_count >= MAX: status = OPEN, retry_exhausted_at = now
        - lock cleared
        
        Args:
            db: Database session
            incident_id: Incident ID
            result: Retry sonucu
            now: Şimdi (test için override)
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        
        incident = db.query(Incident).filter(Incident.id == incident_id).first()
        if not incident:
            logger.error(f"[RETRY] Incident #{incident_id} not found")
            return
        
        # Her durumda güncellenen alanlar
        incident.retry_last_attempt_at = now
        incident.retry_lock_until = None
        incident.retry_lock_by = None
        incident.updated_at = now
        
        if result.status == RetryResultStatus.SUCCESS:
            # Success → PENDING_RECOMPUTE (RESOLVED değil!)
            # RESOLVED kararını RecomputeService verecek
            incident.status = "PENDING_RECOMPUTE"
            incident.retry_success = True
            incident.retry_eligible_at = None
            
            logger.info(
                f"[RETRY] Incident #{incident_id} retry success → PENDING_RECOMPUTE "
                f"(awaiting recompute for RESOLVED decision)"
            )
        
        else:
            # Fail veya Exception → attempt_count++
            incident.retry_success = False
            current_attempt = incident.retry_attempt_count or 0
            new_attempt = current_attempt + 1
            incident.retry_attempt_count = new_attempt
            
            if new_attempt >= self.MAX_RETRY_ATTEMPTS:
                # Exhausted → OPEN + manual review
                # 4. fail = exhaust (attempt_count=4 >= MAX_RETRY_ATTEMPTS=4)
                incident.status = "OPEN"
                incident.retry_eligible_at = None
                incident.retry_exhausted_at = now
                incident.resolution_reason = ResolutionReason.RETRY_EXHAUSTED
                
                logger.warning(
                    f"[RETRY] Incident #{incident_id} exhausted after {new_attempt} attempts"
                )
            else:
                # Backoff → PENDING_RETRY devam
                backoff_minutes = self._get_backoff_minutes(new_attempt - 1)
                incident.retry_eligible_at = now + timedelta(minutes=backoff_minutes)
                incident.status = "PENDING_RETRY"
                
                logger.info(
                    f"[RETRY] Incident #{incident_id} fail #{new_attempt}, "
                    f"next retry in {backoff_minutes}m"
                )
        
        db.commit()
    
    def run_batch(
        self,
        db: Session,
        tenant_id: str,
        now: Optional[datetime] = None,
        limit: int = 50,
    ) -> BatchSummary:
        """
        Batch retry çalıştır.
        
        1. Eligible incident'ları claim et
        2. Her biri için execute
        3. Sonuçları uygula
        
        Args:
            db: Database session
            tenant_id: Tenant ID
            now: Şimdi (test için override)
            limit: Max batch size
        
        Returns:
            BatchSummary
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        
        summary = BatchSummary(
            claimed=0,
            success=0,
            fail=0,
            exhausted=0,
            errors=0,
        )
        
        # 1. Claim
        incidents = self.claim(db, tenant_id, now, limit)
        summary.claimed = len(incidents)
        
        if not incidents:
            logger.debug(f"[RETRY] No eligible incidents for tenant={tenant_id}")
            return summary
        
        # 2. Execute + Apply
        for incident in incidents:
            try:
                result = self.execute(incident)
                
                # Check if exhausted before apply
                current_attempt = incident.retry_attempt_count or 0
                will_exhaust = (
                    result.status != RetryResultStatus.SUCCESS
                    and current_attempt + 1 >= self.MAX_RETRY_ATTEMPTS
                )
                
                self.apply_result(db, incident.id, result, now)
                
                if result.status == RetryResultStatus.SUCCESS:
                    summary.success += 1
                elif will_exhaust:
                    summary.exhausted += 1
                else:
                    summary.fail += 1
                    
            except Exception as e:
                logger.exception(f"[RETRY] Error processing incident #{incident.id}")
                summary.errors += 1
                
                # Clear lock on error
                try:
                    incident.retry_lock_until = None
                    incident.retry_lock_by = None
                    db.commit()
                except:
                    pass
        
        logger.info(
            f"[RETRY] Batch complete: claimed={summary.claimed} "
            f"success={summary.success} fail={summary.fail} "
            f"exhausted={summary.exhausted} errors={summary.errors}"
        )
        
        return summary
