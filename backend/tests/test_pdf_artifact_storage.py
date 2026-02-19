"""
Tests for PDF Artifact Storage (Task 3).

S1) PdfArtifactStore CRUD — generate_key, store, exists, get, delete
S2) Worker success stores artifact via StorageBackend (artifact_store param)
S3) Cleanup deletes expired artifacts
S4) Cleanup preserves fresh artifacts
S5) Storage failure → job FAILED with retry path
S6) Worker fallback to local write_artifact when no artifact_store
S7) Artifact key format: pdf/{job_id}.pdf
S8) Cleanup with artifact_store=None (backward compat)
S9) Storage delete failure during cleanup is non-fatal
S10) PdfArtifactStore with real LocalStorage (integration)
S11) Worker artifact_store.store_pdf exception → FAILED + retryable
S12) Multiple jobs cleanup — mixed fresh/expired with artifacts
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.services.pdf_artifact_store import PdfArtifactStore, PDF_KEY_PREFIX
from app.services.pdf_job_store import (
    MAX_RETRIES,
    PdfErrorCode,
    PdfJob,
    PdfJobStatus,
    PdfJobStore,
    compute_job_key,
)
from app.services.pdf_render_worker import (
    RenderError,
    render_pdf_job,
    write_artifact,
)
from app.services.storage_backend import StorageBackend


# ===================================================================
# In-memory StorageBackend (test double)
# ===================================================================

class InMemoryStorage(StorageBackend):
    """Dict-backed StorageBackend for testing."""

    def __init__(self):
        self._store: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        self._store[key] = data
        return key  # ref == key for in-memory

    def get_bytes(self, ref: str) -> bytes:
        if ref not in self._store:
            raise FileNotFoundError(f"Not found: {ref}")
        return self._store[ref]

    def exists(self, ref: str) -> bool:
        return ref in self._store

    def delete(self, ref: str) -> bool:
        if ref in self._store:
            del self._store[ref]
            return True
        return False


class FailingStorage(StorageBackend):
    """StorageBackend that always raises on put_bytes."""

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        raise IOError("Storage write failed")

    def get_bytes(self, ref: str) -> bytes:
        raise IOError("Storage read failed")

    def exists(self, ref: str) -> bool:
        return False

    def delete(self, ref: str) -> bool:
        raise IOError("Storage delete failed")


# ===================================================================
# FakeRedis (same as test_pdf_worker.py)
# ===================================================================

class FakeRedis:
    def __init__(self):
        self._data: dict[str, Any] = {}
        self._sets: dict[str, dict] = {}

    def hset(self, name, mapping=None, **kwargs):
        if name not in self._data:
            self._data[name] = {}
        if mapping:
            self._data[name].update(mapping)
        self._data[name].update(kwargs)

    def hgetall(self, name):
        return dict(self._data.get(name, {}))

    def set(self, name, value):
        self._data[name] = value

    def get(self, name):
        v = self._data.get(name)
        return None if isinstance(v, dict) else v

    def zadd(self, name, mapping):
        if name not in self._sets:
            self._sets[name] = {}
        self._sets[name].update(mapping)

    def zrem(self, name, *members):
        s = self._sets.get(name, {})
        for m in members:
            s.pop(m, None)

    def scan(self, cursor, match="*", count=100):
        import fnmatch
        keys = [k for k in self._data if fnmatch.fnmatch(k, match)]
        return (0, keys)

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis):
        self._r = redis
        self._ops = []

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
def mem_storage():
    return InMemoryStorage()


@pytest.fixture
def artifact_store(mem_storage):
    return PdfArtifactStore(mem_storage)


@pytest.fixture
def tmp_artifact_dir(tmp_path, monkeypatch):
    import app.services.pdf_render_worker as mod
    monkeypatch.setattr(mod, "ARTIFACT_BASE_DIR", str(tmp_path))
    return tmp_path


# ===================================================================
# S1) PdfArtifactStore CRUD — generate_key, store, exists, get, delete
# ===================================================================

class TestPdfArtifactStoreCRUD:
    def test_generate_key_format(self):
        """Artifact key = pdf/{job_id}.pdf"""
        job_id = "abc123"
        key = PdfArtifactStore.generate_key(job_id)
        assert key == "pdf/abc123.pdf"

    def test_store_and_get(self, artifact_store, mem_storage):
        job_id = uuid.uuid4().hex
        pdf = b"%PDF-1.4 test"
        ref = artifact_store.store_pdf(job_id, pdf)

        assert artifact_store.exists(ref)
        assert artifact_store.get_pdf(ref) == pdf

    def test_delete(self, artifact_store):
        job_id = uuid.uuid4().hex
        ref = artifact_store.store_pdf(job_id, b"%PDF")

        assert artifact_store.exists(ref)
        assert artifact_store.delete_pdf(ref) is True
        assert not artifact_store.exists(ref)

    def test_delete_nonexistent(self, artifact_store):
        assert artifact_store.delete_pdf("pdf/nonexistent.pdf") is False

    def test_store_returns_ref(self, artifact_store):
        job_id = uuid.uuid4().hex
        ref = artifact_store.store_pdf(job_id, b"%PDF")
        expected_key = PdfArtifactStore.generate_key(job_id)
        assert ref == expected_key


# ===================================================================
# S2) Worker success stores artifact via StorageBackend
# ===================================================================

class TestWorkerWithArtifactStore:
    def test_worker_uses_artifact_store(self, store, artifact_store, mem_storage):
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})
        fake_pdf = b"%PDF-1.4 rendered via store"

        with patch("app.services.pdf_render_worker.render_html_to_pdf", return_value=fake_pdf):
            render_pdf_job(job.job_id, store=store, artifact_store=artifact_store)

        updated = store.get_job(job.job_id)
        assert updated.status == PdfJobStatus.SUCCEEDED
        assert updated.artifact_key is not None
        # Artifact stored in StorageBackend, not local disk
        assert artifact_store.exists(updated.artifact_key)
        assert artifact_store.get_pdf(updated.artifact_key) == fake_pdf


# ===================================================================
# S3) Cleanup deletes expired artifacts
# ===================================================================

class TestCleanupDeletesArtifacts:
    def test_cleanup_deletes_artifact_for_expired_job(self, store, artifact_store, mem_storage):
        """Expired job's artifact should be deleted from storage."""
        job = store.create_job("invoice", {"html": "<h1>old</h1>"})
        # Simulate completed job with artifact
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        ref = artifact_store.store_pdf(job.job_id, b"%PDF-old")
        store.update_status(job.job_id, PdfJobStatus.SUCCEEDED, artifact_key=ref)

        # Backdate created_at so it's expired
        rkey = f"pdf:job:{job.job_id}"
        store._r.hset(rkey, mapping={"created_at": str(time.time() - 100000)})

        assert artifact_store.exists(ref)
        count = store.cleanup_expired(ttl_seconds=3600, artifact_store=artifact_store)
        assert count == 1

        # Artifact deleted
        assert not artifact_store.exists(ref)
        # Job expired
        assert store.get_job(job.job_id).status == PdfJobStatus.EXPIRED


# ===================================================================
# S4) Cleanup preserves fresh artifacts
# ===================================================================

class TestCleanupPreservesFresh:
    def test_fresh_job_not_expired(self, store, artifact_store):
        job = store.create_job("invoice", {"html": "<h1>fresh</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        ref = artifact_store.store_pdf(job.job_id, b"%PDF-fresh")
        store.update_status(job.job_id, PdfJobStatus.SUCCEEDED, artifact_key=ref)

        count = store.cleanup_expired(ttl_seconds=3600, artifact_store=artifact_store)
        assert count == 0
        assert artifact_store.exists(ref)
        assert store.get_job(job.job_id).status == PdfJobStatus.SUCCEEDED


# ===================================================================
# S5) Storage failure → job FAILED with retry path
# ===================================================================

class TestStorageFailure:
    def test_storage_write_failure_marks_failed_then_requeued(self, store):
        """When artifact_store.store_pdf raises, job → FAILED → QUEUED (retryable)."""
        failing_store = PdfArtifactStore(FailingStorage())
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})
        fake_pdf = b"%PDF-1.4"

        with patch("app.services.pdf_render_worker.render_html_to_pdf", return_value=fake_pdf):
            render_pdf_job(job.job_id, store=store, artifact_store=failing_store)

        updated = store.get_job(job.job_id)
        # ARTIFACT_WRITE_FAILED is transient → requeued
        assert updated.status == PdfJobStatus.QUEUED
        assert updated.error_code == PdfErrorCode.ARTIFACT_WRITE_FAILED
        assert updated.retry_count == 1


# ===================================================================
# S6) Worker fallback to local write_artifact when no artifact_store
# ===================================================================

class TestWorkerLocalFallback:
    def test_no_artifact_store_uses_local(self, store, tmp_artifact_dir):
        job = store.create_job("invoice", {"html": "<h1>local</h1>"})
        fake_pdf = b"%PDF-1.4 local"

        with patch("app.services.pdf_render_worker.render_html_to_pdf", return_value=fake_pdf):
            render_pdf_job(job.job_id, store=store)  # no artifact_store

        updated = store.get_job(job.job_id)
        assert updated.status == PdfJobStatus.SUCCEEDED
        assert updated.artifact_key is not None
        assert os.path.isfile(updated.artifact_key)


# ===================================================================
# S7) Artifact key format: pdf/{job_id}.pdf
# ===================================================================

class TestArtifactKeyFormat:
    def test_key_prefix(self):
        assert PDF_KEY_PREFIX == "pdf"

    def test_key_format_various_ids(self):
        for jid in ["abc", "123", uuid.uuid4().hex, "job-with-dashes"]:
            key = PdfArtifactStore.generate_key(jid)
            assert key == f"pdf/{jid}.pdf"
            assert key.startswith("pdf/")
            assert key.endswith(".pdf")


# ===================================================================
# S8) Cleanup with artifact_store=None (backward compat)
# ===================================================================

class TestCleanupBackwardCompat:
    def test_cleanup_without_artifact_store(self, store):
        """cleanup_expired works without artifact_store (original behavior)."""
        job = store.create_job("invoice", {"html": "<h1>old</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        store.update_status(job.job_id, PdfJobStatus.SUCCEEDED, artifact_key="/tmp/x.pdf")

        rkey = f"pdf:job:{job.job_id}"
        store._r.hset(rkey, mapping={"created_at": str(time.time() - 100000)})

        count = store.cleanup_expired(ttl_seconds=3600)  # no artifact_store
        assert count == 1
        assert store.get_job(job.job_id).status == PdfJobStatus.EXPIRED


# ===================================================================
# S9) Storage delete failure during cleanup is non-fatal
# ===================================================================

class TestCleanupDeleteFailure:
    def test_artifact_delete_failure_still_expires_job(self, store):
        """If artifact delete fails, job still transitions to EXPIRED."""
        failing_store = PdfArtifactStore(FailingStorage())
        job = store.create_job("invoice", {"html": "<h1>old</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        store.update_status(job.job_id, PdfJobStatus.SUCCEEDED, artifact_key="pdf/some.pdf")

        rkey = f"pdf:job:{job.job_id}"
        store._r.hset(rkey, mapping={"created_at": str(time.time() - 100000)})

        count = store.cleanup_expired(ttl_seconds=3600, artifact_store=failing_store)
        assert count == 1
        assert store.get_job(job.job_id).status == PdfJobStatus.EXPIRED


# ===================================================================
# S10) PdfArtifactStore with real LocalStorage (integration)
# ===================================================================

class TestLocalStorageIntegration:
    def test_full_lifecycle_with_local_storage(self, tmp_path):
        """put → exists → get → delete with real LocalStorage."""
        from app.services.storage_local import LocalStorage

        storage = LocalStorage(base_dir=str(tmp_path))
        art_store = PdfArtifactStore(storage)

        job_id = uuid.uuid4().hex
        pdf = b"%PDF-1.4 integration test"

        ref = art_store.store_pdf(job_id, pdf)
        assert art_store.exists(ref)
        assert art_store.get_pdf(ref) == pdf

        assert art_store.delete_pdf(ref) is True
        assert not art_store.exists(ref)


# ===================================================================
# S11) Worker artifact_store.store_pdf exception → FAILED + retryable
# ===================================================================

class TestStorageFailureRetryPath:
    def test_storage_failure_retries_then_permanent_fail(self, store):
        """Storage write failure maps to ARTIFACT_WRITE_FAILED (transient).
        After MAX_RETRIES exhausted → permanent FAILED."""
        failing_store = PdfArtifactStore(FailingStorage())
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})
        fake_pdf = b"%PDF"

        for attempt in range(MAX_RETRIES + 1):
            current = store.get_job(job.job_id)
            if current.status != PdfJobStatus.QUEUED:
                break
            with patch("app.services.pdf_render_worker.render_html_to_pdf", return_value=fake_pdf):
                render_pdf_job(job.job_id, store=store, artifact_store=failing_store)

        updated = store.get_job(job.job_id)
        assert updated.status == PdfJobStatus.FAILED
        assert updated.error_code == PdfErrorCode.ARTIFACT_WRITE_FAILED
        assert updated.retry_count == MAX_RETRIES


# ===================================================================
# S12) Multiple jobs cleanup — mixed fresh/expired with artifacts
# ===================================================================

class TestMixedCleanup:
    def test_mixed_fresh_and_expired(self, store, artifact_store):
        """Only expired jobs get cleaned up; fresh ones preserved."""
        # Expired job with artifact
        old_job = store.create_job("old-tmpl", {"html": "<h1>old</h1>"})
        store.update_status(old_job.job_id, PdfJobStatus.RUNNING)
        old_ref = artifact_store.store_pdf(old_job.job_id, b"%PDF-old")
        store.update_status(old_job.job_id, PdfJobStatus.SUCCEEDED, artifact_key=old_ref)
        rkey = f"pdf:job:{old_job.job_id}"
        store._r.hset(rkey, mapping={"created_at": str(time.time() - 200000)})

        # Fresh job with artifact
        new_job = store.create_job("new-tmpl", {"html": "<h1>new</h1>"})
        store.update_status(new_job.job_id, PdfJobStatus.RUNNING)
        new_ref = artifact_store.store_pdf(new_job.job_id, b"%PDF-new")
        store.update_status(new_job.job_id, PdfJobStatus.SUCCEEDED, artifact_key=new_ref)

        # Expired job without artifact (QUEUED, never rendered)
        queued_job = store.create_job("queued-tmpl", {"html": "<h1>q</h1>"})
        rkey2 = f"pdf:job:{queued_job.job_id}"
        store._r.hset(rkey2, mapping={"created_at": str(time.time() - 200000)})

        count = store.cleanup_expired(ttl_seconds=3600, artifact_store=artifact_store)
        assert count == 2  # old_job + queued_job

        # Old artifact deleted
        assert not artifact_store.exists(old_ref)
        assert store.get_job(old_job.job_id).status == PdfJobStatus.EXPIRED

        # Fresh artifact preserved
        assert artifact_store.exists(new_ref)
        assert store.get_job(new_job.job_id).status == PdfJobStatus.SUCCEEDED

        # Queued job expired (no artifact to delete)
        assert store.get_job(queued_job.job_id).status == PdfJobStatus.EXPIRED


# ===================================================================
# S13) Cleanup delete failure logs structured warning
# ===================================================================

class TestCleanupDeleteFailureLogging:
    def test_cleanup_logs_artifact_delete_failure(self, store, caplog):
        """Artifact delete failure during cleanup emits structured log."""
        import logging

        failing_store = PdfArtifactStore(FailingStorage())
        job = store.create_job("invoice", {"html": "<h1>old</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        store.update_status(job.job_id, PdfJobStatus.SUCCEEDED, artifact_key="pdf/some.pdf")

        rkey = f"pdf:job:{job.job_id}"
        store._r.hset(rkey, mapping={"created_at": str(time.time() - 100000)})

        with caplog.at_level(logging.WARNING, logger="app.services.pdf_job_store"):
            count = store.cleanup_expired(ttl_seconds=3600, artifact_store=failing_store)

        assert count == 1
        # Verify structured log fields
        assert any("Artifact delete failed" in r.message for r in caplog.records)
        assert any(job.job_id in r.message for r in caplog.records)
        assert any("pdf/some.pdf" in r.message for r in caplog.records)
        assert any("OSError" in r.message for r in caplog.records)


# ===================================================================
# S14) Prod requires artifact_store
# ===================================================================

class TestProdRequiresArtifactStore:
    def test_prod_env_raises_without_artifact_store(self, store, monkeypatch):
        """In production, artifact_store=None raises RuntimeError."""
        monkeypatch.setenv("PDF_ENV", "production")
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})

        with pytest.raises(RuntimeError, match="artifact_store is required in production"):
            render_pdf_job(job.job_id, store=store)  # no artifact_store

    def test_prod_env_works_with_artifact_store(self, store, monkeypatch):
        """In production, providing artifact_store works fine."""
        monkeypatch.setenv("PDF_ENV", "production")
        mem = InMemoryStorage()
        art_store = PdfArtifactStore(mem)
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})
        fake_pdf = b"%PDF-1.4"

        with patch("app.services.pdf_render_worker.render_html_to_pdf", return_value=fake_pdf):
            render_pdf_job(job.job_id, store=store, artifact_store=art_store)

        assert store.get_job(job.job_id).status == PdfJobStatus.SUCCEEDED

    def test_dev_env_allows_no_artifact_store(self, store, tmp_artifact_dir, monkeypatch):
        """In dev, artifact_store=None falls back to local write."""
        monkeypatch.setenv("PDF_ENV", "dev")
        job = store.create_job("invoice", {"html": "<h1>test</h1>"})
        fake_pdf = b"%PDF-1.4"

        with patch("app.services.pdf_render_worker.render_html_to_pdf", return_value=fake_pdf):
            render_pdf_job(job.job_id, store=store)  # no artifact_store, OK in dev

        assert store.get_job(job.job_id).status == PdfJobStatus.SUCCEEDED


# ===================================================================
# S15) Error code mapping stable — ARTIFACT_WRITE_FAILED in taxonomy
# ===================================================================

class TestErrorCodeMappingStable:
    def test_artifact_write_failed_in_enum(self):
        assert hasattr(PdfErrorCode, "ARTIFACT_WRITE_FAILED")
        assert PdfErrorCode.ARTIFACT_WRITE_FAILED.value == "ARTIFACT_WRITE_FAILED"

    def test_artifact_write_failed_is_transient(self):
        from app.services.pdf_job_store import TRANSIENT_ERRORS, should_retry
        assert PdfErrorCode.ARTIFACT_WRITE_FAILED in TRANSIENT_ERRORS
        assert should_retry(PdfErrorCode.ARTIFACT_WRITE_FAILED, 0) is True
        assert should_retry(PdfErrorCode.ARTIFACT_WRITE_FAILED, MAX_RETRIES) is False
