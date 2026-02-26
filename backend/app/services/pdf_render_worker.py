"""
PDF Render Worker — Playwright render with child-process isolation.

Architecture:
    RQ calls  render_pdf_job(job_id)  in the worker process.
    Playwright runs in a *child* process (multiprocessing) so that:
      - A stuck browser can be hard-killed without poisoning the worker.
      - Windows event-loop quirks are isolated.

State transition order (single direction, single function):
    QUEUED → RUNNING → SUCCEEDED | FAILED
    On transient failure + should_retry → FAILED → QUEUED (requeue)

Artifact write:
    Atomic: write to temp file → os.replace → final path.
    Path: ./artifacts/pdfs/{job_id}.pdf  (local dev, StorageBackend later)
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .pdf_job_store import (
    MAX_RETRIES,
    PdfErrorCode,
    PdfJob,
    PdfJobStatus,
    PdfJobStore,
    should_retry,
)

from ..ptf_metrics import get_ptf_metrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_HARD_TIMEOUT = 60  # seconds
GRACEFUL_CANCEL_OFFSET = 5  # seconds before hard timeout
BROWSER_LAUNCH_TIMEOUT = 10  # seconds
ARTIFACT_BASE_DIR = os.environ.get("PDF_ARTIFACT_DIR", "./artifacts/pdfs")


# ---------------------------------------------------------------------------
# Child-process render (isolation boundary)
# ---------------------------------------------------------------------------

def _render_in_child(html: str, nav_timeout_ms: int, result_queue: multiprocessing.Queue) -> None:
    """
    Target function for the child process.
    Runs Playwright synchronously, puts (pdf_bytes,) or (None, error_code, message)
    onto *result_queue*.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 720})
                page.set_content(html, wait_until="load", timeout=nav_timeout_ms)
                page.emulate_media(media="print")
                # Wait for all images to fully load/decode
                page.wait_for_function(
                    "() => Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)",
                    timeout=15000,
                )
                pdf_bytes = page.pdf(
                    print_background=True,
                    prefer_css_page_size=True,
                    scale=1.0,
                )
                result_queue.put(("ok", pdf_bytes))
            finally:
                browser.close()

    except ImportError:
        result_queue.put(("error", PdfErrorCode.UNSUPPORTED_PLATFORM, "Playwright not installed"))
    except Exception as e:
        ename = type(e).__name__
        if "TimeoutError" in ename or "Timeout" in ename:
            result_queue.put(("error", PdfErrorCode.NAVIGATION_TIMEOUT, str(e)))
        elif "browser" in str(e).lower() or "launch" in str(e).lower():
            result_queue.put(("error", PdfErrorCode.BROWSER_LAUNCH_FAILED, str(e)))
        else:
            result_queue.put(("error", PdfErrorCode.UNKNOWN, str(e)))


def render_html_to_pdf(
    html: str,
    hard_timeout: int = DEFAULT_HARD_TIMEOUT,
) -> bytes:
    """
    Render HTML → PDF in an isolated child process with hard timeout.

    Raises:
        RenderError on any failure (with .error_code).
    """
    nav_timeout_ms = (hard_timeout - GRACEFUL_CANCEL_OFFSET) * 1000
    if nav_timeout_ms <= 0:
        nav_timeout_ms = 5000

    result_queue: multiprocessing.Queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_render_in_child,
        args=(html, nav_timeout_ms, result_queue),
        daemon=True,
    )
    proc.start()
    proc.join(timeout=hard_timeout)

    if proc.is_alive():
        # Hard kill — browser is stuck
        proc.terminate()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        raise RenderError(PdfErrorCode.NAVIGATION_TIMEOUT, "Render timed out (hard kill)")

    if result_queue.empty():
        raise RenderError(PdfErrorCode.UNKNOWN, "Child process exited without result")

    result = result_queue.get_nowait()
    if result[0] == "ok":
        return result[1]
    else:
        raise RenderError(result[1], result[2])


@dataclass
class RenderError(Exception):
    """Typed render failure with error_code from taxonomy."""
    error_code: PdfErrorCode
    message: str

    def __str__(self) -> str:
        return f"{self.error_code.value}: {self.message}"


# ---------------------------------------------------------------------------
# Artifact write (atomic: mkstemp → replace)
# ---------------------------------------------------------------------------

def _ensure_artifact_dir() -> Path:
    p = Path(ARTIFACT_BASE_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_artifact(job_id: str, pdf_bytes: bytes) -> str:
    """
    Atomically write PDF bytes to local disk.
    Returns the artifact_key (relative path).
    """
    base = _ensure_artifact_dir()
    final_path = base / f"{job_id}.pdf"

    fd, tmp_path = tempfile.mkstemp(dir=str(base), suffix=".tmp")
    try:
        os.write(fd, pdf_bytes)
        os.close(fd)
        os.replace(tmp_path, str(final_path))
    except Exception:
        os.close(fd) if not os.get_inheritable(fd) else None  # noqa: best-effort
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return str(final_path)


def read_artifact(artifact_key: str) -> bytes:
    """Read PDF bytes from artifact_key (local path)."""
    with open(artifact_key, "rb") as f:
        return f.read()


def artifact_exists(artifact_key: str) -> bool:
    return os.path.isfile(artifact_key)


# ---------------------------------------------------------------------------
# Main entrypoint — called by RQ (or directly in tests)
# ---------------------------------------------------------------------------

def render_pdf_job(
    job_id: str,
    *,
    store: PdfJobStore,
    artifact_store: Optional[Any] = None,
    html_renderer: Optional[Callable[[str, dict], str]] = None,
    hard_timeout: int = DEFAULT_HARD_TIMEOUT,
) -> None:
    """
    Single entrypoint for the PDF render pipeline.

    1. Fetch job from store
    2. QUEUED → RUNNING
    3. Render HTML → PDF (child process, timeout enforced)
    4. Write artifact (via PdfArtifactStore or local fallback)
    5. RUNNING → SUCCEEDED | FAILED
    6. If transient failure + retries left → FAILED → QUEUED (requeue)

    Args:
        job_id: The job to process.
        store: PdfJobStore instance.
        artifact_store: Optional PdfArtifactStore. Falls back to local write_artifact.
                        Required when PDF_ENV=production.
        html_renderer: Optional callable(template_name, payload) → html string.
                       If None, payload must contain an "html" key.
        hard_timeout: Per-job hard timeout in seconds.

    Raises:
        RuntimeError: If artifact_store is None in production environment.
    """
    # Prod guard: artifact_store required
    _env = os.environ.get("PDF_ENV", "dev").lower()
    if artifact_store is None and _env in ("production", "prod"):
        raise RuntimeError(
            "artifact_store is required in production (PDF_ENV=%s). "
            "Local fallback is disabled for prod." % _env
        )
    job = store.get_job(job_id)
    if job is None:
        logger.error(f"Job {job_id} not found")
        return

    # Guard: only process QUEUED jobs
    if job.status != PdfJobStatus.QUEUED:
        logger.info(f"Job {job_id} not QUEUED (status={job.status.value}), skipping")
        return

    # ── QUEUED → RUNNING ──
    try:
        store.update_status(job_id, PdfJobStatus.RUNNING)
    except ValueError as e:
        logger.error(f"Transition error for {job_id}: {e}")
        return

    start_time = time.monotonic()

    # ── Resolve HTML ──
    try:
        if html_renderer is not None:
            html = html_renderer(job.template_name, job.payload)
        else:
            html = job.payload.get("html", "")
            if not html:
                _handle_failure(store, job, PdfErrorCode.TEMPLATE_ERROR, "No HTML content in payload")
                return
    except RenderError as e:
        _handle_failure(store, job, e.error_code, e.message)
        return
    except Exception as e:
        _handle_failure(store, job, PdfErrorCode.TEMPLATE_ERROR, str(e))
        return

    # ── Render (child process) ──
    try:
        pdf_bytes = render_html_to_pdf(html, hard_timeout=hard_timeout)
    except RenderError as e:
        _handle_failure(store, job, e.error_code, e.message)
        return
    except Exception as e:
        _handle_failure(store, job, PdfErrorCode.UNKNOWN, str(e))
        return

    # ── Write artifact ──
    try:
        if artifact_store is not None:
            artifact_key = artifact_store.store_pdf(job_id, pdf_bytes)
        else:
            artifact_key = write_artifact(job_id, pdf_bytes)
    except Exception as e:
        _handle_failure(store, job, PdfErrorCode.ARTIFACT_WRITE_FAILED, f"Artifact write failed: {e}")
        return

    # ── RUNNING → SUCCEEDED ──
    store.update_status(
        job_id,
        PdfJobStatus.SUCCEEDED,
        artifact_key=artifact_key,
    )
    duration = time.monotonic() - start_time
    try:
        metrics = get_ptf_metrics()
        metrics.inc_pdf_job("succeeded")
        metrics.observe_pdf_job_duration(duration)
    except Exception:
        pass  # fail-open: metrics never block pipeline
    logger.info(f"Job {job_id} succeeded, artifact={artifact_key}, duration={duration:.2f}s")


def _handle_failure(
    store: PdfJobStore,
    job: PdfJob,
    error_code: PdfErrorCode,
    message: str,
) -> None:
    """
    Transition RUNNING → FAILED.
    If retryable, immediately FAILED → QUEUED (manual requeue).
    """
    logger.warning(f"Job {job.job_id} failed: {error_code.value} — {message}")

    try:
        metrics = get_ptf_metrics()
        metrics.inc_pdf_job("failed")
        metrics.inc_pdf_failure(error_code.value)
    except Exception:
        pass  # fail-open

    new_retry = job.retry_count + 1 if should_retry(error_code, job.retry_count) else job.retry_count

    # RUNNING → FAILED
    store.update_status(
        job.job_id,
        PdfJobStatus.FAILED,
        error_code=error_code,
        retry_count=new_retry,
    )

    # Retry? FAILED → QUEUED
    if should_retry(error_code, job.retry_count):
        logger.info(f"Retrying job {job.job_id} (attempt {new_retry}/{MAX_RETRIES})")
        store.update_status(
            job.job_id,
            PdfJobStatus.QUEUED,
            retry_count=new_retry,
        )
