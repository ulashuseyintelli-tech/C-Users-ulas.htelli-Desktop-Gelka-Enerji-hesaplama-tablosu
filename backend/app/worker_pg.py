#!/usr/bin/env python3
"""
Production Worker - Postgres + S3 ready.

Features:
- FOR UPDATE SKIP LOCKED (Postgres) for true concurrency
- Storage backend abstraction (local/S3)
- Graceful shutdown
- Multi-worker support

Usage:
    python -m app.worker_pg
    python -m app.worker_pg --workers 4
"""
import argparse
import logging
import signal
import sys
import time
import threading
from datetime import datetime
from typing import Optional

from app.core.config import settings
from app.database import SessionLocal, Invoice, Job
from app.models import InvoiceStatus, JobType, JobStatus, InvoiceExtraction
from app.job_queue import mark_succeeded, mark_failed, get_job_by_id
from app.services.job_claim import claim_next_job
from app.services.storage import get_storage
from app.extractor import extract_invoice_data, ExtractionError
from app.validator import validate_extraction

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Graceful shutdown
shutdown_event = threading.Event()


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info("Shutdown signal received...")
    shutdown_event.set()


def get_image_bytes(invoice: Invoice) -> tuple[bytes, str]:
    """
    Get image bytes from storage.
    
    Returns:
        (image_bytes, mime_type)
    """
    storage = get_storage()
    
    if invoice.content_type == "application/pdf":
        if not invoice.storage_page1_ref:
            raise RuntimeError("PDF page1 image missing")
        ref = invoice.storage_page1_ref
        mime_type = "image/jpeg"
    else:
        ref = invoice.storage_original_ref
        mime_type = invoice.content_type
    
    if not ref:
        raise RuntimeError("Storage reference missing")
    
    # S3 ref veya local path - storage backend handle eder
    image_bytes = storage.get_bytes(ref)
    return image_bytes, mime_type


def process_job(db, job: Job) -> None:
    """Process a single job."""
    logger.info(f"Processing job {job.id} (type={job.job_type.value})")
    
    invoice = db.query(Invoice).filter(Invoice.id == job.invoice_id).first()
    if not invoice:
        mark_failed(db, job, "Invoice not found")
        return
    
    try:
        # Get image bytes
        image_bytes, mime_type = get_image_bytes(invoice)
        
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
        mark_succeeded(db, job, result={
            "invoice_id": invoice.id,
            "invoice_status": invoice.status.value,
            "vendor": invoice.vendor_guess,
            "period": invoice.invoice_period
        })
        
        logger.info(f"Job {job.id} succeeded")
        
    except ExtractionError as e:
        logger.error(f"Extraction error: {e}")
        invoice.status = InvoiceStatus.FAILED
        invoice.error_message = str(e)
        db.add(invoice)
        db.commit()
        mark_failed(db, job, str(e))
        
    except Exception as e:
        logger.exception(f"Job {job.id} failed")
        invoice.status = InvoiceStatus.FAILED
        invoice.error_message = str(e)
        db.add(invoice)
        db.commit()
        mark_failed(db, job, str(e))


def worker_loop(worker_id: int = 0):
    """Single worker loop."""
    db_type = "Postgres" if settings.is_postgres else "SQLite"
    storage_type = "S3" if settings.is_s3_storage else "Local"
    
    logger.info(f"[Worker-{worker_id}] Started. DB={db_type}, Storage={storage_type}")
    
    while not shutdown_event.is_set():
        db = SessionLocal()
        try:
            job_id = claim_next_job(db)
            
            if job_id:
                job = get_job_by_id(db, job_id)
                if job:
                    process_job(db, job)
            else:
                # No jobs, wait
                shutdown_event.wait(timeout=settings.worker_poll_interval)
                
        except Exception as e:
            logger.exception(f"[Worker-{worker_id}] Error: {e}")
            shutdown_event.wait(timeout=settings.worker_poll_interval)
        finally:
            db.close()
    
    logger.info(f"[Worker-{worker_id}] Stopped")


def run_worker(num_workers: int = 1):
    """Start workers."""
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    if num_workers == 1:
        worker_loop(0)
    else:
        # Multi-threaded workers
        threads = []
        for i in range(num_workers):
            t = threading.Thread(target=worker_loop, args=(i,), daemon=True)
            t.start()
            threads.append(t)
            logger.info(f"Started worker thread {i}")
        
        # Wait for shutdown
        try:
            while not shutdown_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        
        logger.info("Waiting for workers to finish...")
        shutdown_event.set()
        for t in threads:
            t.join(timeout=5)
        
        logger.info("All workers stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Production Worker (Postgres + S3 ready)")
    parser.add_argument("--workers", "-w", type=int, default=1, help="Number of concurrent workers")
    args = parser.parse_args()
    
    run_worker(args.workers)
