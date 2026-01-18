#!/usr/bin/env python3
"""
Background Worker - DB Polling.

MVP: DB'den job'ları polling ile alır ve işler.
Prod: Redis RQ/Celery worker'a geçiş kolay.

Kullanım:
    python -m app.worker
    python -m app.worker --workers 2  # 2 concurrent worker
"""
import argparse
import logging
import time
import threading
from datetime import datetime
from typing import Optional

from .database import SessionLocal, Invoice
from .models import InvoiceStatus, JobType, JobStatus, InvoiceExtraction
from .job_queue import claim_job, mark_succeeded, mark_failed
from .extractor import extract_invoice_data, ExtractionError
from .validator import validate_extraction

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Config
POLL_INTERVAL_SECONDS = float(__import__('os').getenv("WORKER_POLL_INTERVAL", "1.0"))


def process_job(db, job) -> None:
    """
    Tek bir job'ı işle.
    
    Job tipleri:
    - EXTRACT: Sadece extraction
    - VALIDATE: Sadece validation
    - EXTRACT_AND_VALIDATE: İkisi birden
    """
    logger.info(f"[Worker] Processing job {job.id} (type={job.job_type.value})")
    
    # Job zaten RUNNING olarak claim edildi (claim_job'da)
    
    # Get invoice
    invoice = db.query(Invoice).filter(Invoice.id == job.invoice_id).first()
    if not invoice:
        mark_failed(db, job, "Invoice not found")
        return
    
    try:
        # ═══════════════════════════════════════════════════════════════════
        # EXTRACTION
        # ═══════════════════════════════════════════════════════════════════
        if job.job_type in {JobType.EXTRACT, JobType.EXTRACT_AND_VALIDATE}:
            logger.info(f"Running extraction for invoice {invoice.id}")
            
            # Get storage backend
            from .services.storage import get_storage
            storage = get_storage()
            
            # Hangi görseli kullanacağız?
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
            
            # Extract
            extraction = extract_invoice_data(image_bytes, mime_type)
            
            # Update invoice
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
            logger.info(f"[Worker] Running validation for invoice {invoice.id}")
            
            if not invoice.extraction_json:
                raise RuntimeError("Extraction missing, cannot validate")
            
            # Reconstruct extraction model
            extraction = InvoiceExtraction(**invoice.extraction_json)
            
            # Validate
            validation = validate_extraction(extraction)
            
            # Update invoice
            invoice.validation_json = validation.model_dump()
            invoice.status = InvoiceStatus.READY if validation.is_ready_for_pricing else InvoiceStatus.NEEDS_INPUT
            db.add(invoice)
            db.commit()
            db.refresh(invoice)
            
            logger.info(f"Validation complete: ready={validation.is_ready_for_pricing}")
        
        # ═══════════════════════════════════════════════════════════════════
        # SUCCESS
        # ═══════════════════════════════════════════════════════════════════
        mark_succeeded(db, job, result={
            "invoice_id": invoice.id,
            "invoice_status": invoice.status.value,
            "vendor": invoice.vendor_guess,
            "period": invoice.invoice_period
        })
        
        logger.info(f"[Worker] Job {job.id} succeeded")
        
    except ExtractionError as e:
        logger.error(f"[Worker] Extraction error: {e}")
        invoice.status = InvoiceStatus.FAILED
        invoice.error_message = str(e)
        db.add(invoice)
        db.commit()
        mark_failed(db, job, str(e))
        
    except Exception as e:
        logger.exception(f"[Worker] Job {job.id} failed")
        invoice.status = InvoiceStatus.FAILED
        invoice.error_message = str(e)
        db.add(invoice)
        db.commit()
        mark_failed(db, job, str(e))


def worker_loop(worker_id: int = 0):
    """Tek worker döngüsü."""
    logger.info(f"[Worker-{worker_id}] Started. Poll interval: {POLL_INTERVAL_SECONDS}s")
    
    while True:
        db = SessionLocal()
        try:
            # Claim job (atomic)
            job = claim_job(db)
            
            if job:
                process_job(db, job)
            else:
                time.sleep(POLL_INTERVAL_SECONDS)
                
        except Exception as e:
            logger.exception(f"[Worker-{worker_id}] Error: {e}")
            time.sleep(POLL_INTERVAL_SECONDS)
        finally:
            db.close()


def run_worker(num_workers: int = 1):
    """Worker'ları başlat."""
    if num_workers == 1:
        worker_loop(0)
    else:
        # Multi-threaded workers
        threads = []
        for i in range(num_workers):
            t = threading.Thread(target=worker_loop, args=(i,), daemon=True)
            t.start()
            threads.append(t)
            logger.info(f"[Main] Started worker thread {i}")
        
        # Ana thread bekle
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("[Main] Shutting down...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Invoice Processing Worker")
    parser.add_argument("--workers", "-w", type=int, default=1, help="Number of concurrent workers")
    args = parser.parse_args()
    
    run_worker(args.workers)
