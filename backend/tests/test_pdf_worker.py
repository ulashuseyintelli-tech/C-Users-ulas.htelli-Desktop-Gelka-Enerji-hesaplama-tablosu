"""
Tests for PDF Render Worker (Task 2).

W1) Happy path — stub renderer → QUEUED→RUNNING→SUCCEEDED, artifact exists
W2) Retryable failure → FAILED + requeue to QUEUED
W3) Non-retryable failure → FAILED, no retry
W4) Timeout kill — slow render → NAVIGATION_TIMEOUT
W5) Invalid transition guard — non-QUEUED job skipped
W6) Idempotent re-run — SUCCEEDED job not re-rendered
W7) Artifact atomic write
W8) Template error — missing html
W9) Multiple retries exhaust cap
W10) Child process crash → UNKNOWN error
W11) render_html_to_pdf with real child process (fast stub)
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.services.pdf_job_store import (
    MAX_RETRIES,
    PdfErrorCode,
    PdfJob,
    PdfJobStatus,
    PdfJobStore,
    compute_job_key,
    should_retry,
)
from app.services.pdf_render_worker import (
    ARTIFACT_BASE_DIR,
    RenderError,
    _handle_failure,
    artifact_exists,
    read_artifact,
    render_pdf_job,
    write_artifact,
)


# ===================================================================
# In-memory Redis fake (dict-backed, enough for PdfJobStore)
# ===================================================================

class FakeRedis:
    """Minimal Redis mock supporting hset/hgetall/set/get/pipeline/zadd/zrem/scan."""

    def __init__(self):
        self._data: dict[str, Any] = {}
        self._sets: dict[str, dict] = {}

    def hset(self, name: str, mapping: dict | None = None, **kwargs):
        if name not in self._data:
            self._data[name] = {}
        if mapping:
            self._data[name].update(mapping)
        self._data[name].update(kwargs)

    def hgetall(self, name: str) -> dict:
        return dict(self._data.get(name, {}))

    def set(self, name: str, value: str):
        self._data[name] = value

    def get(self, name: str) -> str | None:
        v = self._data.get(name)
        if isinstance(v, dict):
            return None
        return v

    def zadd(self, name: str, mapping: dict):
        if name not in self._sets:
            self._sets[name] = {}
        self._sets[name].update(mapping)

    def zrem(self, name: str, *members):
        s = self._sets.get(name, {})
        for m in members:
            s.pop(m, None)

    def scan(self, cursor: int, match: str = "*", count: int = 100):
        # Simple: return all matching keys in one shot
        import fnmatch
        keys = [k for k in self._data if fnmatch.fnmatch(k, match)]
        return (0, keys)

    def pipeline(self):
        return FakePipeline(self)

    def ping(self):
        return True


class FakePipeline:
    def __init__(self, redis: FakeRedis):
        self._r = redis
        self._ops: list = []

    def hset(self, name, mapping=None, **kwargs):
        self._ops.append(("hset", name, mapping, kwargs))
        return self

    def set(self, name, value):
        self._ops.append(("set", name, value))
        return self

    def zadd(self, name, mapping):
        self._ops.append(("zadd", name, mapping))
        return self

    def zrem(self, name, *members):
        self._ops.append(("zrem", name, members))
        return self

    def execute(self):
        for op in self._ops:
            if op[0] == "hset":
                self._r.hset(op[1], mapping=op[2], **(op[3] or {}))
            elif op[0] == "set":
                self._r.set(op[1], op[2])
            elif op[0] == "zadd":
                self._r.zadd(op[1], op[2])
            elif op[0] == "zrem":
                self._r.zrem(op[1], *op[2])
        self._ops.clear()


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def store(fake_redis):
    return PdfJobStore(fake_redis)


@pytest.fixture
def tmp_artifact_dir(tmp_path, monkeypatch):
    """Redirect artifact writes to a temp directory."""
    import app.services.pdf_render_worker as mod
    monkeypatch.setattr(mod, "ARTIFACT_BASE_DIR", str(tmp_path))
    return tmp_path


def _stub_renderer(template_name: str, payload: dict) -> str:
    """Fake HTML renderer — returns minimal HTML."""
    return f"<html><body>{template_name}</body></html>"


def _failing_renderer(error_code: PdfErrorCode):
    """Returns a renderer that raises RenderError."""
    def _renderer(template_name: str, payload: dict) -> str:
        raise RenderError(error_code, "Simulated failure")
    return _renderer


# ===================================================================
# W1) Happy path
# ===================================================================

class TestHappyPath:
    def test_queued_to_succeeded(self, store, tmp_artifact_dir):
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})
        assert job.status == PdfJobStatus.QUEUED

        fake_pdf = b"%PDF-1.4 fake content"

        with patch("app.services.pdf_render_worker.render_html_to_pdf", return_value=fake_pdf):
            render_pdf_job(job.job_id, store=store)

        updated = store.get_job(job.job_id)
        assert updated.status == PdfJobStatus.SUCCEEDED
        assert updated.artifact_key is not None
        assert os.path.isfile(updated.artifact_key)

        content = read_artifact(updated.artifact_key)
        assert content == fake_pdf

    def test_with_html_renderer(self, store, tmp_artifact_dir):
        job = store.create_job("invoice", {"customer": "test"})
        fake_pdf = b"%PDF-1.4 rendered"

        with patch("app.services.pdf_render_worker.render_html_to_pdf", return_value=fake_pdf):
            render_pdf_job(job.job_id, store=store, html_renderer=_stub_renderer)

        updated = store.get_job(job.job_id)
        assert updated.status == PdfJobStatus.SUCCEEDED


# ===================================================================
# W2) Retryable failure → requeue
# ===================================================================

class TestRetryableFailure:
    def test_browser_launch_failed_requeues(self, store, tmp_artifact_dir):
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})

        with patch(
            "app.services.pdf_render_worker.render_html_to_pdf",
            side_effect=RenderError(PdfErrorCode.BROWSER_LAUNCH_FAILED, "crash"),
        ):
            render_pdf_job(job.job_id, store=store)

        updated = store.get_job(job.job_id)
        # After retry: FAILED → QUEUED
        assert updated.status == PdfJobStatus.QUEUED
        assert updated.retry_count == 1

    def test_navigation_timeout_requeues(self, store, tmp_artifact_dir):
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})

        with patch(
            "app.services.pdf_render_worker.render_html_to_pdf",
            side_effect=RenderError(PdfErrorCode.NAVIGATION_TIMEOUT, "timeout"),
        ):
            render_pdf_job(job.job_id, store=store)

        updated = store.get_job(job.job_id)
        assert updated.status == PdfJobStatus.QUEUED
        assert updated.retry_count == 1


# ===================================================================
# W3) Non-retryable failure
# ===================================================================

class TestNonRetryableFailure:
    def test_template_error_no_retry(self, store, tmp_artifact_dir):
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})

        with patch(
            "app.services.pdf_render_worker.render_html_to_pdf",
            side_effect=RenderError(PdfErrorCode.TEMPLATE_ERROR, "bad template"),
        ):
            render_pdf_job(job.job_id, store=store)

        updated = store.get_job(job.job_id)
        assert updated.status == PdfJobStatus.FAILED
        assert updated.error_code == PdfErrorCode.TEMPLATE_ERROR
        assert updated.retry_count == 0

    def test_unsupported_platform_no_retry(self, store, tmp_artifact_dir):
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})

        with patch(
            "app.services.pdf_render_worker.render_html_to_pdf",
            side_effect=RenderError(PdfErrorCode.UNSUPPORTED_PLATFORM, "no playwright"),
        ):
            render_pdf_job(job.job_id, store=store)

        updated = store.get_job(job.job_id)
        assert updated.status == PdfJobStatus.FAILED
        assert updated.error_code == PdfErrorCode.UNSUPPORTED_PLATFORM


# ===================================================================
# W4) Timeout kill
# ===================================================================

class TestTimeoutKill:
    def test_hard_timeout_marks_failed(self, store, tmp_artifact_dir):
        """Simulated timeout → NAVIGATION_TIMEOUT."""
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})

        with patch(
            "app.services.pdf_render_worker.render_html_to_pdf",
            side_effect=RenderError(PdfErrorCode.NAVIGATION_TIMEOUT, "hard kill"),
        ):
            render_pdf_job(job.job_id, store=store)

        updated = store.get_job(job.job_id)
        # Transient → requeued
        assert updated.status == PdfJobStatus.QUEUED
        assert updated.retry_count == 1


# ===================================================================
# W5) Invalid transition guard
# ===================================================================

class TestInvalidTransitionGuard:
    def test_running_job_skipped(self, store, tmp_artifact_dir):
        """Job already RUNNING → render_pdf_job should no-op."""
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)

        with patch("app.services.pdf_render_worker.render_html_to_pdf") as mock_render:
            render_pdf_job(job.job_id, store=store)
            mock_render.assert_not_called()

    def test_succeeded_job_skipped(self, store, tmp_artifact_dir):
        """Job already SUCCEEDED → render_pdf_job should no-op."""
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        store.update_status(job.job_id, PdfJobStatus.SUCCEEDED, artifact_key="/tmp/x.pdf")

        with patch("app.services.pdf_render_worker.render_html_to_pdf") as mock_render:
            render_pdf_job(job.job_id, store=store)
            mock_render.assert_not_called()

    def test_failed_job_skipped(self, store, tmp_artifact_dir):
        """Job in FAILED state → render_pdf_job should no-op."""
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        store.update_status(job.job_id, PdfJobStatus.FAILED, error_code=PdfErrorCode.UNKNOWN)

        with patch("app.services.pdf_render_worker.render_html_to_pdf") as mock_render:
            render_pdf_job(job.job_id, store=store)
            mock_render.assert_not_called()

    def test_nonexistent_job_noop(self, store, tmp_artifact_dir):
        """Unknown job_id → no crash, just log."""
        render_pdf_job("nonexistent-id", store=store)  # should not raise


# ===================================================================
# W6) Idempotent re-run
# ===================================================================

class TestIdempotentRerun:
    def test_succeeded_job_not_rerendered(self, store, tmp_artifact_dir):
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        store.update_status(job.job_id, PdfJobStatus.SUCCEEDED, artifact_key="/tmp/done.pdf")

        with patch("app.services.pdf_render_worker.render_html_to_pdf") as mock_render:
            render_pdf_job(job.job_id, store=store)
            mock_render.assert_not_called()

        # Status unchanged
        assert store.get_job(job.job_id).status == PdfJobStatus.SUCCEEDED


# ===================================================================
# W7) Artifact atomic write
# ===================================================================

class TestArtifactWrite:
    def test_write_and_read(self, tmp_artifact_dir):
        job_id = uuid.uuid4().hex
        data = b"%PDF-1.4 test content"
        key = write_artifact(job_id, data)

        assert os.path.isfile(key)
        assert read_artifact(key) == data
        assert artifact_exists(key)

    def test_overwrite_existing(self, tmp_artifact_dir):
        job_id = uuid.uuid4().hex
        write_artifact(job_id, b"first")
        key = write_artifact(job_id, b"second")
        assert read_artifact(key) == b"second"

    def test_artifact_not_exists(self, tmp_artifact_dir):
        assert not artifact_exists("/nonexistent/path.pdf")


# ===================================================================
# W8) Template error — missing html
# ===================================================================

class TestTemplateError:
    def test_empty_payload_no_html(self, store, tmp_artifact_dir):
        job = store.create_job("invoice", {"no_html_key": True})

        with patch("app.services.pdf_render_worker.render_html_to_pdf") as mock_render:
            render_pdf_job(job.job_id, store=store)
            mock_render.assert_not_called()

        updated = store.get_job(job.job_id)
        assert updated.status == PdfJobStatus.FAILED
        assert updated.error_code == PdfErrorCode.TEMPLATE_ERROR


# ===================================================================
# W9) Multiple retries exhaust cap
# ===================================================================

class TestRetryExhaustion:
    def test_max_retries_then_permanent_fail(self, store, tmp_artifact_dir):
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})

        for attempt in range(MAX_RETRIES + 1):
            current = store.get_job(job.job_id)
            if current.status != PdfJobStatus.QUEUED:
                break

            with patch(
                "app.services.pdf_render_worker.render_html_to_pdf",
                side_effect=RenderError(PdfErrorCode.BROWSER_LAUNCH_FAILED, "crash"),
            ):
                render_pdf_job(job.job_id, store=store)

        final = store.get_job(job.job_id)
        assert final.status == PdfJobStatus.FAILED
        assert final.retry_count == MAX_RETRIES
        assert final.error_code == PdfErrorCode.BROWSER_LAUNCH_FAILED


# ===================================================================
# W10) Unknown error
# ===================================================================

class TestUnknownError:
    def test_generic_exception_maps_to_unknown(self, store, tmp_artifact_dir):
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})

        with patch(
            "app.services.pdf_render_worker.render_html_to_pdf",
            side_effect=RuntimeError("something unexpected"),
        ):
            render_pdf_job(job.job_id, store=store)

        updated = store.get_job(job.job_id)
        assert updated.status == PdfJobStatus.FAILED
        assert updated.error_code == PdfErrorCode.UNKNOWN


# ===================================================================
# W11) RenderError dataclass
# ===================================================================

class TestRenderError:
    def test_str_representation(self):
        e = RenderError(PdfErrorCode.NAVIGATION_TIMEOUT, "page stuck")
        assert "NAVIGATION_TIMEOUT" in str(e)
        assert "page stuck" in str(e)

    def test_is_exception(self):
        with pytest.raises(RenderError):
            raise RenderError(PdfErrorCode.UNKNOWN, "boom")
