"""
Job Queue Service - DB tabanlı (Redis'e geçiş kolay).

MVP: DB polling ile çalışır.
Prod: Redis RQ/Celery'ye geçiş için sadece enqueue_job değişir.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc

from .database import Job
from .models import JobType, JobStatus


# Aktif job durumları
ACTIVE_STATUSES = {JobStatus.QUEUED, JobStatus.RUNNING}


def find_active_job(db: Session, invoice_id: str, job_type: JobType) -> Optional[Job]:
    """Invoice + job_type için aktif job bul."""
    return (
        db.query(Job)
        .filter(
            and_(
                Job.invoice_id == invoice_id,
                Job.job_type == job_type,
                Job.status.in_(list(ACTIVE_STATUSES)),
            )
        )
        .order_by(Job.created_at.desc())
        .first()
    )


def enqueue_job_idempotent(
    db: Session,
    invoice_id: str,
    job_type: JobType,
    payload: dict | None = None
) -> tuple[Job, bool]:
    """
    Idempotent job oluşturma.
    
    Returns:
        (job, created_new)
        - Aktif job varsa: (existing_job, False)
        - Yoksa yeni oluşturur: (new_job, True)
    """
    existing = find_active_job(db, invoice_id, job_type)
    if existing:
        return existing, False
    
    job = Job(
        invoice_id=invoice_id,
        job_type=job_type,
        status=JobStatus.QUEUED,
        payload_json=payload
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, True


def enqueue_job(
    db: Session,
    invoice_id: str,
    job_type: JobType,
    payload: dict | None = None,
    prevent_duplicate: bool = True
) -> Job:
    """
    Yeni job oluştur ve kuyruğa ekle.
    
    Args:
        prevent_duplicate: True ise aynı invoice+job_type için aktif job varsa yeni oluşturmaz
    
    MVP: DB'ye kaydet, worker polling ile alır.
    Prod: Redis'e de push edilebilir.
    """
    if prevent_duplicate:
        job, _ = enqueue_job_idempotent(db, invoice_id, job_type, payload)
        return job
    
    job = Job(
        invoice_id=invoice_id,
        job_type=job_type,
        status=JobStatus.QUEUED,
        payload_json=payload
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def mark_running(db: Session, job: Job) -> Job:
    """Job'ı RUNNING olarak işaretle."""
    job.status = JobStatus.RUNNING
    job.started_at = datetime.utcnow()
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def mark_succeeded(db: Session, job: Job, result: dict | None = None) -> Job:
    """Job'ı SUCCEEDED olarak işaretle."""
    job.status = JobStatus.SUCCEEDED
    job.result_json = result
    job.finished_at = datetime.utcnow()
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def mark_failed(db: Session, job: Job, error: str) -> Job:
    """Job'ı FAILED olarak işaretle."""
    job.status = JobStatus.FAILED
    job.error = error[:2000]  # Max 2000 karakter
    job.finished_at = datetime.utcnow()
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_next_queued_job(db: Session) -> Optional[Job]:
    """Kuyruktaki en eski job'ı al (FIFO)."""
    return (
        db.query(Job)
        .filter(Job.status == JobStatus.QUEUED)
        .order_by(Job.created_at.asc())
        .first()
    )


def get_job_by_id(db: Session, job_id: str) -> Optional[Job]:
    """Job'ı ID ile getir."""
    return db.query(Job).filter(Job.id == job_id).first()


def get_jobs_by_invoice(db: Session, invoice_id: str) -> list[Job]:
    """Invoice'a ait tüm job'ları getir."""
    return (
        db.query(Job)
        .filter(Job.invoice_id == invoice_id)
        .order_by(Job.created_at.desc())
        .all()
    )


def list_jobs(
    db: Session,
    invoice_id: str | None = None,
    status: JobStatus | None = None,
    job_type: JobType | None = None,
    limit: int = 50
) -> list[Job]:
    """
    Job listesi - filtreleme destekli.
    
    Args:
        invoice_id: Belirli invoice'a ait job'lar
        status: Belirli durumdaki job'lar
        job_type: Belirli tipteki job'lar
        limit: Maksimum sonuç sayısı
    """
    q = db.query(Job)
    
    if invoice_id:
        q = q.filter(Job.invoice_id == invoice_id)
    if status:
        q = q.filter(Job.status == status)
    if job_type:
        q = q.filter(Job.job_type == job_type)
    
    return q.order_by(desc(Job.created_at)).limit(limit).all()


def has_active_job(db: Session, invoice_id: str) -> bool:
    """Invoice için aktif (QUEUED/RUNNING) job var mı?"""
    return (
        db.query(Job)
        .filter(
            Job.invoice_id == invoice_id,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
        )
        .first()
    ) is not None


def get_active_job(db: Session, invoice_id: str) -> Optional[Job]:
    """Invoice için aktif job'ı getir."""
    return (
        db.query(Job)
        .filter(
            Job.invoice_id == invoice_id,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
        )
        .first()
    )


def claim_job(db: Session) -> Optional[Job]:
    """
    Kuyruktaki bir job'ı claim et (atomic).
    Concurrency için: SELECT FOR UPDATE + immediate status change.
    
    SQLite'da FOR UPDATE yok, bu yüzden basit versiyon.
    Postgres'te FOR UPDATE SKIP LOCKED kullanılabilir.
    """
    job = get_next_queued_job(db)
    if job:
        # Hemen RUNNING yap (race condition minimize)
        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()
        db.add(job)
        db.commit()
        db.refresh(job)
    return job
