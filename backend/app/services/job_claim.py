"""
Job Claim Service - Atomic job claiming for concurrent workers.

PostgreSQL: Uses FOR UPDATE SKIP LOCKED for true concurrency.
SQLite: Falls back to simple claim (limited concurrency).
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.config import settings
from app.models import JobStatus

logger = logging.getLogger(__name__)


def claim_next_job_postgres(db: Session) -> Optional[str]:
    """
    Claim next job using FOR UPDATE SKIP LOCKED.
    
    This is the gold standard for concurrent job processing:
    - Multiple workers can run simultaneously
    - Each job goes to exactly one worker
    - No race conditions
    
    Returns:
        job_id or None if no jobs available
    """
    result = db.execute(text("""
        WITH next_job AS (
            SELECT id
            FROM jobs
            WHERE status = :queued
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE jobs
        SET status = :running,
            started_at = NOW()
        WHERE id IN (SELECT id FROM next_job)
        RETURNING id;
    """), {
        "queued": JobStatus.QUEUED.value,
        "running": JobStatus.RUNNING.value
    }).fetchone()
    
    db.commit()
    
    if result:
        logger.debug(f"Claimed job: {result[0]}")
        return result[0]
    return None


def claim_next_job_sqlite(db: Session) -> Optional[str]:
    """
    Claim next job for SQLite (simple version).
    
    SQLite doesn't support FOR UPDATE SKIP LOCKED.
    This works for single worker or low concurrency.
    
    Returns:
        job_id or None if no jobs available
    """
    from app.database import Job
    
    job = (
        db.query(Job)
        .filter(Job.status == JobStatus.QUEUED)
        .order_by(Job.created_at.asc())
        .first()
    )
    
    if not job:
        return None
    
    # Claim it
    job.status = JobStatus.RUNNING
    job.started_at = datetime.utcnow()
    db.add(job)
    db.commit()
    db.refresh(job)
    
    logger.debug(f"Claimed job (SQLite): {job.id}")
    return job.id


def claim_next_job(db: Session) -> Optional[str]:
    """
    Claim next job - auto-selects method based on database.
    
    Returns:
        job_id or None
    """
    if settings.is_postgres:
        return claim_next_job_postgres(db)
    else:
        return claim_next_job_sqlite(db)
