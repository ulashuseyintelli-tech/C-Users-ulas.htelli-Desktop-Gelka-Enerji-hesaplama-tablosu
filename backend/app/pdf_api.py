"""
PDF Job API — Create, query, and download PDF render jobs.

Endpoints:
    POST   /pdf/jobs                → Create job (202)
    GET    /pdf/jobs/{job_id}       → Query status (200)
    GET    /pdf/jobs/{job_id}/download → Download PDF (200/409)

Security:
    - template_name allowlist (PDF_TEMPLATE_ALLOWLIST env, comma-separated)
    - payload size limit (PDF_MAX_PAYLOAD_BYTES env, default 256KB)
    - No URL navigation (HTML string render only)
    - Admin key auth when ADMIN_API_KEY_ENABLED=true
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .services.pdf_job_store import (
    PdfErrorCode,
    PdfJobStatus,
    PdfJobStore,
)
from .services.pdf_artifact_store import PdfArtifactStore
from .ptf_metrics import get_ptf_metrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PDF_MAX_PAYLOAD_BYTES = int(os.environ.get("PDF_MAX_PAYLOAD_BYTES", str(256 * 1024)))

def _get_template_allowlist() -> frozenset[str] | None:
    """Return allowlist from env, or None (all allowed) if not set."""
    raw = os.environ.get("PDF_TEMPLATE_ALLOWLIST", "").strip()
    if not raw:
        return None
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class CreateJobRequest(BaseModel):
    template_name: str = Field(..., min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    error_code: Optional[str] = None
    artifact_key: Optional[str] = None
    retry_count: int = 0
    created_at: float = 0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


# ---------------------------------------------------------------------------
# Dependency injection helpers
# ---------------------------------------------------------------------------

_pdf_job_store: Optional[PdfJobStore] = None
_pdf_artifact_store: Optional[PdfArtifactStore] = None
_enqueue_fn = None  # callable(job_id) -> bool


def configure_pdf_api(
    store: PdfJobStore,
    artifact_store: PdfArtifactStore,
    enqueue_fn=None,
) -> None:
    """Wire dependencies at app startup."""
    global _pdf_job_store, _pdf_artifact_store, _enqueue_fn
    _pdf_job_store = store
    _pdf_artifact_store = artifact_store
    _enqueue_fn = enqueue_fn


def _get_store() -> PdfJobStore:
    if _pdf_job_store is None:
        raise HTTPException(status_code=503, detail={
            "error": "PDF_RENDER_UNAVAILABLE",
            "message": "PDF job store not configured",
        })
    return _pdf_job_store


def _get_artifact_store() -> PdfArtifactStore:
    if _pdf_artifact_store is None:
        raise HTTPException(status_code=503, detail={
            "error": "PDF_RENDER_UNAVAILABLE",
            "message": "PDF artifact store not configured",
        })
    return _pdf_artifact_store


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/pdf", tags=["pdf"])


@router.post("/jobs", status_code=202, response_model=CreateJobResponse)
async def create_pdf_job(body: CreateJobRequest, request: Request):
    """
    Create a PDF render job.

    - Validates template_name against allowlist (if configured).
    - Rejects payloads exceeding PDF_MAX_PAYLOAD_BYTES.
    - Dedup: returns existing job if same job_key is active.
    - Enqueues to RQ if enqueue_fn is configured.
    """
    store = _get_store()

    # ── Template allowlist ──
    allowlist = _get_template_allowlist()
    _env = os.environ.get("PDF_ENV", "dev").lower()
    if allowlist is None and _env in ("production", "prod"):
        raise HTTPException(status_code=503, detail={
            "error": "PDF_RENDER_UNAVAILABLE",
            "message": "PDF_TEMPLATE_ALLOWLIST is required in production",
        })
    if allowlist is not None and body.template_name not in allowlist:
        raise HTTPException(status_code=403, detail={
            "error": "TEMPLATE_NOT_ALLOWED",
            "message": f"Template '{body.template_name}' is not in the allowlist",
        })

    # ── Payload size limit ──
    payload_bytes = len(json.dumps(body.payload, ensure_ascii=False).encode("utf-8"))
    if payload_bytes > PDF_MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail={
            "error": "PAYLOAD_TOO_LARGE",
            "message": f"Payload size {payload_bytes} exceeds limit {PDF_MAX_PAYLOAD_BYTES}",
        })

    # ── Create (or dedup) ──
    job = store.create_job(body.template_name, body.payload)

    # ── Enqueue to RQ ──
    if _enqueue_fn is not None and job.status == PdfJobStatus.QUEUED:
        try:
            _enqueue_fn(job.job_id)
        except Exception as e:
            logger.error(f"Enqueue failed for {job.job_id}: {e}")
            # Mark job as FAILED with QUEUE_UNAVAILABLE
            try:
                store.update_status(job.job_id, PdfJobStatus.RUNNING)
                store.update_status(
                    job.job_id,
                    PdfJobStatus.FAILED,
                    error_code=PdfErrorCode.QUEUE_UNAVAILABLE,
                )
            except Exception:
                pass  # best-effort status update
            try:
                m = get_ptf_metrics()
                m.inc_pdf_job("failed")
                m.inc_pdf_failure("QUEUE_UNAVAILABLE")
            except Exception:
                pass
            raise HTTPException(status_code=503, detail={
                "error": "QUEUE_UNAVAILABLE",
                "message": f"Failed to enqueue job: {e}",
            })

    # Emit queued metric for new jobs
    if job.status == PdfJobStatus.QUEUED:
        try:
            get_ptf_metrics().inc_pdf_job("queued")
        except Exception:
            pass

    return CreateJobResponse(job_id=job.job_id, status=job.status.value)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_pdf_job_status(job_id: str):
    """Query PDF job status."""
    store = _get_store()
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={
            "error": "JOB_NOT_FOUND",
            "message": f"Job {job_id} not found",
        })

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status.value,
        error_code=job.error_code.value if job.error_code else None,
        artifact_key=job.artifact_key,
        retry_count=job.retry_count,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get("/jobs/{job_id}/download")
async def download_pdf(job_id: str):
    """
    Download rendered PDF.

    - 409 if job is not SUCCEEDED (returns current status).
    - 200 with application/pdf bytes + Content-Disposition header.
    """
    store = _get_store()
    artifact_store = _get_artifact_store()

    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={
            "error": "JOB_NOT_FOUND",
            "message": f"Job {job_id} not found",
        })

    if job.status != PdfJobStatus.SUCCEEDED:
        raise HTTPException(status_code=409, detail={
            "error": "JOB_NOT_READY",
            "message": f"Job status is {job.status.value}, not SUCCEEDED",
            "status": job.status.value,
        })

    if not job.artifact_key:
        raise HTTPException(status_code=500, detail={
            "error": "ARTIFACT_MISSING",
            "message": "Job succeeded but artifact_key is missing",
        })

    try:
        pdf_bytes = artifact_store.get_pdf(job.artifact_key)
    except Exception as e:
        logger.error(f"Artifact read failed for {job_id}: {e}")
        raise HTTPException(status_code=500, detail={
            "error": "ARTIFACT_READ_FAILED",
            "message": "Failed to read PDF artifact",
        })

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{job_id}.pdf"',
        },
    )
