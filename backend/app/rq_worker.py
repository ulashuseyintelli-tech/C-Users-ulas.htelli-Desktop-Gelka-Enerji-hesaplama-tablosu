#!/usr/bin/env python3
"""
Redis RQ Worker - Opsiyonel Redis tabanlı worker.

DB job tablosu "source of truth" kalır.
Worker job_id ile DB'den job'u çeker ve işler.

Kullanım:
    python -m app.rq_worker

Gereksinimler:
    - redis
    - rq
    - REDIS_URL environment variable
"""
import logging
from datetime import datetime

from .database import SessionLocal, Invoice, REDIS_URL
from .models import InvoiceStatus, JobType, JobStatus, InvoiceExtraction
from .job_queue import mark_succeeded, mark_failed, get_job_by_id
from .extractor import extract_invoice_data, ExtractionError
from .validator import validate_extraction

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_job(job_id: str) -> dict:
    """
    Job'ı işle - RQ tarafından çağrılır.
    
    Args:
        job_id: DB'deki job ID
    
    Returns:
        İşlem sonucu dict
    """
    logger.info(f"[RQ Worker] Processing job {job_id}")
    
    db = SessionLocal()
    try:
        # Job'ı DB'den al
        job = get_job_by_id(db, job_id)
        if not job:
            logger.error(f"Job not found: {job_id}")
            return {"error": "Job not found"}
        
        # Başka worker aldıysa atla
        if job.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
            logger.info(f"Job {job_id} already processed (status={job.status.value})")
            return {"skipped": True, "reason": "already_processed"}
        
        # RUNNING'e çek
        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()
        db.add(job)
        db.commit()
        db.refresh(job)
        
        # Invoice'ı al
        invoice = db.query(Invoice).filter(Invoice.id == job.invoice_id).first()
        if not invoice:
            mark_failed(db, job, "Invoice not found")
            return {"error": "Invoice not found"}
        
        # Hangi görseli kullanacağız?
        from app.services.storage import get_storage
        storage = get_storage()
        
        if invoice.content_type == "application/pdf":
            if not invoice.storage_page1_ref:
                raise RuntimeError("PDF page1 image missing")
            image_ref = invoice.storage_page1_ref
            mime_type = "image/jpeg"
        else:
            image_ref = invoice.storage_original_ref
            mime_type = invoice.content_type
        
        # Read image from storage backend
        image_bytes = storage.get_bytes(image_ref)
        
        # ═══════════════════════════════════════════════════════════════════
        # EXTRACTION
        # ═══════════════════════════════════════════════════════════════════
        if job.job_type in {JobType.EXTRACT, JobType.EXTRACT_AND_VALIDATE}:
            logger.info(f"Running extraction for invoice {invoice.id}")
            
            extraction = extract_invoice_data(image_bytes, mime_type)
            
            invoice.extraction_json = extraction.model_dump()
            invoice.vendor_guess = extraction.vendor
            invoice.invoice_period = extraction.invoice_period or None
            invoice.status = InvoiceStatus.EXTRACTED
            invoice.error_message = None
            db.add(invoice)
            db.commit()
            db.refresh(invoice)
            
            logger.info(f"Extraction complete: vendor={extraction.vendor}")
        
        # ═══════════════════════════════════════════════════════════════════
        # VALIDATION
        # ═══════════════════════════════════════════════════════════════════
        if job.job_type in {JobType.VALIDATE, JobType.EXTRACT_AND_VALIDATE}:
            logger.info(f"Running validation for invoice {invoice.id}")
            
            if not invoice.extraction_json:
                raise RuntimeError("Extraction missing, cannot validate")
            
            extraction = InvoiceExtraction(**invoice.extraction_json)
            validation = validate_extraction(extraction)
            
            invoice.validation_json = validation.model_dump()
            invoice.status = InvoiceStatus.READY if validation.is_ready_for_pricing else InvoiceStatus.NEEDS_INPUT
            db.add(invoice)
            db.commit()
            db.refresh(invoice)
            
            logger.info(f"Validation complete: ready={validation.is_ready_for_pricing}")
        
        # ═══════════════════════════════════════════════════════════════════
        # SUCCESS
        # ═══════════════════════════════════════════════════════════════════
        result = {
            "invoice_id": invoice.id,
            "invoice_status": invoice.status.value,
            "vendor": invoice.vendor_guess,
            "period": invoice.invoice_period
        }
        mark_succeeded(db, job, result=result)
        
        logger.info(f"[RQ Worker] Job {job_id} succeeded")
        return result
        
    except ExtractionError as e:
        logger.error(f"[RQ Worker] Extraction error: {e}")
        job = get_job_by_id(db, job_id)
        if job:
            mark_failed(db, job, str(e))
        
        invoice = db.query(Invoice).filter(Invoice.id == job.invoice_id).first()
        if invoice:
            invoice.status = InvoiceStatus.FAILED
            invoice.error_message = str(e)
            db.add(invoice)
            db.commit()
        
        return {"error": str(e)}
        
    except Exception as e:
        logger.exception(f"[RQ Worker] Job {job_id} failed")
        job = get_job_by_id(db, job_id)
        if job:
            mark_failed(db, job, str(e))
        
        invoice = db.query(Invoice).filter(Invoice.id == job.invoice_id).first() if job else None
        if invoice:
            invoice.status = InvoiceStatus.FAILED
            invoice.error_message = str(e)
            db.add(invoice)
            db.commit()
        
        return {"error": str(e)}
        
    finally:
        db.close()


def main():
    """RQ Worker başlat."""
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL environment variable not set")
    
    try:
        from redis import Redis
        from rq import Worker, Queue, Connection
    except ImportError:
        raise RuntimeError("redis and rq packages required. Install: pip install redis rq")
    
    logger.info(f"Connecting to Redis: {REDIS_URL}")
    conn = Redis.from_url(REDIS_URL)
    
    with Connection(conn):
        worker = Worker([Queue("jobs")])
        logger.info("RQ Worker started. Waiting for jobs...")
        worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
