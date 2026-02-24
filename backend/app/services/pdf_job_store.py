"""
PDF Job Store — Job model, status enum, error codes, and Redis store.

State machine:
    queued → running → succeeded | failed
    failed → queued (retry, transient only, max 2)
    {queued, succeeded, failed} → expired (TTL)

Job key: sha256(canonical_json(template_name + sorted_payload))
Redis keys:
    pdf:job:{job_id}    → Hash (PdfJob fields)
    pdf:key:{job_key}   → String (job_id) — idempotency lookup
    pdf:jobs:queued      → Sorted Set (score=created_at)
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PdfJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


class PdfErrorCode(str, Enum):
    BROWSER_LAUNCH_FAILED = "BROWSER_LAUNCH_FAILED"
    NAVIGATION_TIMEOUT = "NAVIGATION_TIMEOUT"
    TEMPLATE_ERROR = "TEMPLATE_ERROR"
    UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"
    ARTIFACT_WRITE_FAILED = "ARTIFACT_WRITE_FAILED"
    QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRANSIENT_ERRORS: frozenset[PdfErrorCode] = frozenset({
    PdfErrorCode.BROWSER_LAUNCH_FAILED,
    PdfErrorCode.NAVIGATION_TIMEOUT,
    PdfErrorCode.ARTIFACT_WRITE_FAILED,
})

MAX_RETRIES: int = 2

VALID_TRANSITIONS: dict[PdfJobStatus, frozenset[PdfJobStatus]] = {
    PdfJobStatus.QUEUED: frozenset({PdfJobStatus.RUNNING, PdfJobStatus.EXPIRED}),
    PdfJobStatus.RUNNING: frozenset({PdfJobStatus.SUCCEEDED, PdfJobStatus.FAILED}),
    PdfJobStatus.SUCCEEDED: frozenset({PdfJobStatus.EXPIRED}),
    PdfJobStatus.FAILED: frozenset({PdfJobStatus.QUEUED, PdfJobStatus.EXPIRED}),
    PdfJobStatus.EXPIRED: frozenset(),  # terminal
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class BackpressureActiveError(Exception):
    """Raised when backpressure is active — HOLD semantics (Req 8.1, 8.2).

    HTTP 429 + Retry-After + BACKPRESSURE_ACTIVE error code.
    Hard block: job is NOT queued, NOT retried, NOT delayed.
    """

    def __init__(self, retry_after_seconds: int = 30) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"BACKPRESSURE_ACTIVE: not accepting new jobs. "
            f"Retry-After: {retry_after_seconds}s"
        )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PdfJob:
    job_id: str
    job_key: str
    status: PdfJobStatus
    template_name: str
    payload: dict[str, Any]
    artifact_key: Optional[str] = None
    error_code: Optional[PdfErrorCode] = None
    retry_count: int = 0
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


# ---------------------------------------------------------------------------
# Pure functions (no Redis dependency)
# ---------------------------------------------------------------------------

def compute_job_key(template_name: str, payload: dict[str, Any]) -> str:
    """Deterministic SHA-256 hash over template name + sorted payload."""
    canonical = json.dumps(
        {"template_name": template_name, "payload": payload},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def should_retry(error_code: PdfErrorCode, retry_count: int) -> bool:
    """Return True only for transient errors below the retry cap."""
    return error_code in TRANSIENT_ERRORS and retry_count < MAX_RETRIES


def is_valid_transition(current: PdfJobStatus, target: PdfJobStatus) -> bool:
    """Check whether *current → target* is a legal state transition."""
    return target in VALID_TRANSITIONS.get(current, frozenset())


# ---------------------------------------------------------------------------
# Redis-backed store
# ---------------------------------------------------------------------------

_JOB_PREFIX = "pdf:job:"
_KEY_PREFIX = "pdf:key:"
_QUEUED_SET = "pdf:jobs:queued"


class PdfJobStore:
    """Redis-backed PDF job store with idempotency and TTL cleanup."""

    def __init__(self, redis_conn: Any) -> None:
        self._r = redis_conn
        self._backpressure_active: bool = False
        self._backpressure_retry_after: int = 30  # seconds

    # -- backpressure (Feature: slo-adaptive-control, Req 8.1, 8.2, 8.4) --

    def set_backpressure(self, active: bool, retry_after_seconds: int = 30) -> None:
        """Enable/disable backpressure. HOLD semantics: hard block, no queue."""
        self._backpressure_active = active
        self._backpressure_retry_after = retry_after_seconds
        logger.info(
            f"[PDF-JOB-STORE] Backpressure {'ACTIVE' if active else 'INACTIVE'}, "
            f"retry_after={retry_after_seconds}s"
        )

    @property
    def backpressure_active(self) -> bool:
        return self._backpressure_active

    @property
    def backpressure_retry_after(self) -> int:
        return self._backpressure_retry_after

    # -- helpers ----------------------------------------------------------

    def _job_key(self, job_id: str) -> str:
        return f"{_JOB_PREFIX}{job_id}"

    def _dedup_key(self, job_key: str) -> str:
        return f"{_KEY_PREFIX}{job_key}"

    def _serialize(self, job: PdfJob) -> dict[str, str]:
        return {
            "job_id": job.job_id,
            "job_key": job.job_key,
            "status": job.status.value,
            "template_name": job.template_name,
            "payload": json.dumps(job.payload, sort_keys=True, ensure_ascii=False),
            "artifact_key": job.artifact_key or "",
            "error_code": job.error_code.value if job.error_code else "",
            "retry_count": str(job.retry_count),
            "created_at": str(job.created_at),
            "started_at": str(job.started_at) if job.started_at is not None else "",
            "finished_at": str(job.finished_at) if job.finished_at is not None else "",
        }

    def _deserialize(self, data: dict[str, str]) -> PdfJob:
        return PdfJob(
            job_id=data["job_id"],
            job_key=data["job_key"],
            status=PdfJobStatus(data["status"]),
            template_name=data["template_name"],
            payload=json.loads(data["payload"]),
            artifact_key=data["artifact_key"] or None,
            error_code=PdfErrorCode(data["error_code"]) if data.get("error_code") else None,
            retry_count=int(data.get("retry_count", "0")),
            created_at=float(data["created_at"]),
            started_at=float(data["started_at"]) if data.get("started_at") else None,
            finished_at=float(data["finished_at"]) if data.get("finished_at") else None,
        )

    # -- public API -------------------------------------------------------

    def create_job(self, template_name: str, payload: dict[str, Any]) -> PdfJob:
        """Create a new job (or return existing via idempotency).

        Raises BackpressureActiveError if backpressure is active (HOLD semantics).
        """
        # Backpressure check: HOLD = hard block, no queue (Req 8.1, 8.2)
        if self._backpressure_active:
            raise BackpressureActiveError(self._backpressure_retry_after)

        job_key = compute_job_key(template_name, payload)

        existing = self.find_by_key(job_key)
        if existing is not None:
            if existing.status in (
                PdfJobStatus.QUEUED,
                PdfJobStatus.RUNNING,
                PdfJobStatus.SUCCEEDED,
            ):
                return existing  # idempotent hit

        job = PdfJob(
            job_id=uuid.uuid4().hex,
            job_key=job_key,
            status=PdfJobStatus.QUEUED,
            template_name=template_name,
            payload=payload,
        )
        pipe = self._r.pipeline()
        pipe.hset(self._job_key(job.job_id), mapping=self._serialize(job))
        pipe.set(self._dedup_key(job.job_key), job.job_id)
        pipe.zadd(_QUEUED_SET, {job.job_id: job.created_at})
        pipe.execute()
        return job

    def get_job(self, job_id: str) -> Optional[PdfJob]:
        data = self._r.hgetall(self._job_key(job_id))
        if not data:
            return None
        # Redis may return bytes or str depending on decode_responses
        decoded = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in data.items()
        }
        return self._deserialize(decoded)

    def find_by_key(self, job_key: str) -> Optional[PdfJob]:
        job_id = self._r.get(self._dedup_key(job_key))
        if job_id is None:
            return None
        if isinstance(job_id, bytes):
            job_id = job_id.decode()
        return self.get_job(job_id)

    def update_status(
        self,
        job_id: str,
        status: PdfJobStatus,
        *,
        artifact_key: Optional[str] = None,
        error_code: Optional[PdfErrorCode] = None,
        retry_count: Optional[int] = None,
    ) -> PdfJob:
        """Transition job to *status*. Raises ValueError on invalid transition."""
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"Job {job_id} not found")

        if not is_valid_transition(job.status, status):
            raise ValueError(
                f"Invalid transition: {job.status.value} → {status.value}"
            )

        now = time.time()
        updates: dict[str, str] = {"status": status.value}

        if status == PdfJobStatus.RUNNING:
            updates["started_at"] = str(now)
        elif status in (PdfJobStatus.SUCCEEDED, PdfJobStatus.FAILED):
            updates["finished_at"] = str(now)

        if artifact_key is not None:
            updates["artifact_key"] = artifact_key
        if error_code is not None:
            updates["error_code"] = error_code.value
        if retry_count is not None:
            updates["retry_count"] = str(retry_count)

        pipe = self._r.pipeline()
        pipe.hset(self._job_key(job_id), mapping=updates)

        # Maintain queued sorted set
        if status == PdfJobStatus.QUEUED:
            pipe.zadd(_QUEUED_SET, {job_id: now})
        else:
            pipe.zrem(_QUEUED_SET, job_id)

        pipe.execute()
        return self.get_job(job_id)  # type: ignore[return-value]

    def cleanup_expired(self, ttl_seconds: int, artifact_store: Any = None) -> int:
        """Mark jobs older than *ttl_seconds* as expired. Delete artifacts. Returns count."""
        cutoff = time.time() - ttl_seconds
        expired_count = 0

        # Scan all job keys
        cursor = 0
        while True:
            cursor, keys = self._r.scan(cursor, match=f"{_JOB_PREFIX}*", count=100)
            for key in keys:
                k = key.decode() if isinstance(key, bytes) else key
                data = self._r.hgetall(k)
                if not data:
                    continue
                decoded = {
                    (dk.decode() if isinstance(dk, bytes) else dk): (dv.decode() if isinstance(dv, bytes) else dv)
                    for dk, dv in data.items()
                }
                job = self._deserialize(decoded)
                if job.status in (PdfJobStatus.EXPIRED,):
                    continue
                if job.created_at <= cutoff:
                    try:
                        # Delete artifact if present
                        if artifact_store is not None and job.artifact_key:
                            try:
                                artifact_store.delete_pdf(job.artifact_key)
                            except Exception as e:
                                logger.warning(
                                    "Artifact delete failed: job_id=%s artifact_key=%s error_type=%s error=%s",
                                    job.job_id, job.artifact_key, type(e).__name__, e,
                                )
                        self.update_status(job.job_id, PdfJobStatus.EXPIRED)
                        expired_count += 1
                        try:
                            from ..ptf_metrics import get_ptf_metrics
                            get_ptf_metrics().inc_pdf_job("expired")
                        except Exception:
                            pass
                    except (ValueError, KeyError):
                        pass
            if cursor == 0:
                break
        return expired_count
