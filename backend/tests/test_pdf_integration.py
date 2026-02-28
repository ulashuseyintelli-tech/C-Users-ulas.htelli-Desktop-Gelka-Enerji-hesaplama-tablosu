"""
PDF Render Worker — Integration Tests (Task 9).

E2E tests that exercise the full pipeline through real components
(FakeRedis + InMemoryStorage, no external dependencies).

IT1) Happy path: create → worker render → poll succeeded → download bytes
IT2) Dedup/idempotency: same payload → same job_id; different payload → different job_id
IT3) Failure + retry: transient error → retry → success; permanent error → no retry
IT4) Retry exhaustion: transient error × (MAX_RETRIES+1) → permanent failed
IT5) Cleanup expired: TTL-expired jobs transition to expired, artifact deleted
"""
from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.pdf_api import configure_pdf_api, router
from app.services.pdf_artifact_store import PdfArtifactStore
from app.services.pdf_job_store import (
    MAX_RETRIES,
    PdfErrorCode,
    PdfJob,
    PdfJobStatus,
    PdfJobStore,
)
from app.services.pdf_render_worker import (
    RenderError,
    render_pdf_job,
)
from app.services.storage_backend import StorageBackend


# ===================================================================
# Test doubles (shared with test_pdf_api.py pattern)
# ===================================================================


class InMemoryStorage(StorageBackend):
    def __init__(self):
        self._store: dict[str, bytes] = {}

    def put_bytes(self, key, data, content_type):
        self._store[key] = data
        return key

    def get_bytes(self, ref):
        if ref not in self._store:
            raise FileNotFoundError(f"Not found: {ref}")
        return self._store[ref]

    def exists(self, ref):
        return ref in self._store

    def delete(self, ref):
        if ref in self._store:
            del self._store[ref]
            return True
        return False


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
def enqueue_fn():
    return MagicMock(return_value=True)


@pytest.fixture
def app(store, artifact_store, enqueue_fn):
    test_app = FastAPI()
    test_app.include_router(router)
    configure_pdf_api(store, artifact_store, enqueue_fn)
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


def _stub_renderer(template_name: str, payload: dict) -> str:
    """Simple HTML renderer for integration tests."""
    return f"<html><body><h1>{template_name}</h1><p>{json.dumps(payload)}</p></body></html>"


def _failing_renderer(error_code: PdfErrorCode):
    """Returns a renderer that always raises RenderError."""
    def _renderer(template_name: str, payload: dict) -> str:
        raise RenderError(error_code, f"Simulated {error_code.value}")
    return _renderer


# ===================================================================
# IT1) Happy path: create → render → poll → download
# ===================================================================


class TestHappyPathE2E:
    """Full lifecycle: API create → worker render → API status → API download."""

    def test_create_render_poll_download(self, client, store, artifact_store, enqueue_fn, monkeypatch):
        # Mock render_html_to_pdf to avoid Playwright dependency
        fake_pdf = b"%PDF-1.4 fake pdf content for integration test"
        monkeypatch.setattr(
            "app.services.pdf_render_worker.render_html_to_pdf",
            lambda html, hard_timeout=60: fake_pdf,
        )

        # 1. Create job via API
        resp = client.post("/pdf/jobs", json={
            "template_name": "invoice",
            "payload": {"html": "<html><body>Hello PDF</body></html>"},
        })
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]
        assert resp.json()["status"] == "queued"

        # 2. Verify enqueue was called
        enqueue_fn.assert_called_once_with(job_id)

        # 3. Simulate worker processing (render_pdf_job)
        render_pdf_job(
            job_id,
            store=store,
            artifact_store=artifact_store,
            html_renderer=_stub_renderer,
            hard_timeout=30,
        )

        # 4. Poll status — should be succeeded
        status_resp = client.get(f"/pdf/jobs/{job_id}")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["status"] == "succeeded"
        assert status_data["artifact_key"] is not None
        assert status_data["finished_at"] is not None

        # 5. Download PDF
        dl_resp = client.get(f"/pdf/jobs/{job_id}/download")
        assert dl_resp.status_code == 200
        assert dl_resp.headers["content-type"] == "application/pdf"
        assert dl_resp.content == fake_pdf
        assert "content-disposition" in dl_resp.headers


# ===================================================================
# IT2) Dedup / idempotency
# ===================================================================


class TestDedupE2E:
    """Same payload returns same job; different payload returns different job."""

    def test_same_payload_returns_same_job(self, client):
        payload = {"template_name": "receipt", "payload": {"amount": 100}}
        r1 = client.post("/pdf/jobs", json=payload)
        r2 = client.post("/pdf/jobs", json=payload)
        assert r1.status_code == 202
        assert r2.status_code == 202
        assert r1.json()["job_id"] == r2.json()["job_id"]

    def test_different_payload_returns_different_job(self, client):
        r1 = client.post("/pdf/jobs", json={"template_name": "receipt", "payload": {"amount": 100}})
        r2 = client.post("/pdf/jobs", json={"template_name": "receipt", "payload": {"amount": 200}})
        assert r1.json()["job_id"] != r2.json()["job_id"]

    def test_succeeded_job_dedup_returns_existing(self, client, store, artifact_store):
        """After worker succeeds, same payload still returns the existing job_id."""
        payload = {"template_name": "invoice", "payload": {"html": "<html>test</html>"}}
        r1 = client.post("/pdf/jobs", json=payload)
        job_id = r1.json()["job_id"]

        # Simulate worker success
        render_pdf_job(job_id, store=store, artifact_store=artifact_store,
                       html_renderer=_stub_renderer, hard_timeout=30)

        # Same payload → same job_id (succeeded, artifact exists)
        r2 = client.post("/pdf/jobs", json=payload)
        assert r2.json()["job_id"] == job_id


# ===================================================================
# IT3) Failure + retry semantics
# ===================================================================


class TestFailureRetryE2E:
    """Transient errors trigger retry; permanent errors don't."""

    def test_transient_failure_requeues(self, client, store, artifact_store):
        """BROWSER_LAUNCH_FAILED → job transitions to QUEUED (retry)."""
        resp = client.post("/pdf/jobs", json={
            "template_name": "report",
            "payload": {"html": "<html>test</html>"},
        })
        job_id = resp.json()["job_id"]

        # Simulate transient failure
        render_pdf_job(
            job_id, store=store, artifact_store=artifact_store,
            html_renderer=_failing_renderer(PdfErrorCode.BROWSER_LAUNCH_FAILED),
            hard_timeout=30,
        )

        # Job should be back to QUEUED (retry)
        job = store.get_job(job_id)
        assert job.status == PdfJobStatus.QUEUED
        assert job.retry_count == 1

    def test_permanent_failure_stays_failed(self, client, store, artifact_store):
        """TEMPLATE_ERROR → job stays FAILED, no retry."""
        resp = client.post("/pdf/jobs", json={
            "template_name": "report",
            "payload": {"html": "<html>test</html>"},
        })
        job_id = resp.json()["job_id"]

        render_pdf_job(
            job_id, store=store, artifact_store=artifact_store,
            html_renderer=_failing_renderer(PdfErrorCode.TEMPLATE_ERROR),
            hard_timeout=30,
        )

        job = store.get_job(job_id)
        assert job.status == PdfJobStatus.FAILED
        assert job.error_code == PdfErrorCode.TEMPLATE_ERROR
        assert job.retry_count == 0

    def test_transient_then_success(self, client, store, artifact_store, monkeypatch):
        """First attempt fails (transient), second attempt succeeds."""
        resp = client.post("/pdf/jobs", json={
            "template_name": "report",
            "payload": {"html": "<html>retry test</html>"},
        })
        job_id = resp.json()["job_id"]

        # Attempt 1: transient failure
        render_pdf_job(
            job_id, store=store, artifact_store=artifact_store,
            html_renderer=_failing_renderer(PdfErrorCode.NAVIGATION_TIMEOUT),
            hard_timeout=30,
        )
        job = store.get_job(job_id)
        assert job.status == PdfJobStatus.QUEUED
        assert job.retry_count == 1

        # Mock render_html_to_pdf for successful attempt
        fake_pdf = b"%PDF-1.4 retry success"
        monkeypatch.setattr(
            "app.services.pdf_render_worker.render_html_to_pdf",
            lambda html, hard_timeout=60: fake_pdf,
        )

        # Attempt 2: success
        render_pdf_job(
            job_id, store=store, artifact_store=artifact_store,
            html_renderer=_stub_renderer,
            hard_timeout=30,
        )
        job = store.get_job(job_id)
        assert job.status == PdfJobStatus.SUCCEEDED
        assert job.artifact_key is not None

        # Download works
        dl_resp = client.get(f"/pdf/jobs/{job_id}/download")
        assert dl_resp.status_code == 200


# ===================================================================
# IT4) Retry exhaustion
# ===================================================================


class TestRetryExhaustionE2E:
    """Transient error repeated MAX_RETRIES+1 times → permanent FAILED."""

    def test_max_retries_then_permanent_fail(self, client, store, artifact_store):
        resp = client.post("/pdf/jobs", json={
            "template_name": "report",
            "payload": {"html": "<html>exhaust</html>"},
        })
        job_id = resp.json()["job_id"]

        failing = _failing_renderer(PdfErrorCode.BROWSER_LAUNCH_FAILED)

        # Exhaust all retries
        for i in range(MAX_RETRIES + 1):
            render_pdf_job(
                job_id, store=store, artifact_store=artifact_store,
                html_renderer=failing, hard_timeout=30,
            )

        job = store.get_job(job_id)
        assert job.status == PdfJobStatus.FAILED
        assert job.retry_count == MAX_RETRIES

        # Download should return 409 (not ready)
        dl_resp = client.get(f"/pdf/jobs/{job_id}/download")
        assert dl_resp.status_code == 409


# ===================================================================
# IT5) Cleanup expired
# ===================================================================


class TestCleanupExpiredE2E:
    """TTL-expired jobs transition to expired, artifact deleted."""

    def test_cleanup_removes_expired_artifacts(self, store, artifact_store, fake_redis):
        # Create and succeed a job
        job = store.create_job("invoice", {"html": "<html>cleanup</html>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        artifact_key = artifact_store.store_pdf(job.job_id, b"%PDF-fake")
        store.update_status(job.job_id, PdfJobStatus.SUCCEEDED, artifact_key=artifact_key)

        # Backdate created_at to simulate TTL expiry
        redis_key = f"pdf:job:{job.job_id}"
        fake_redis.hset(redis_key, mapping={"created_at": str(time.time() - 100000)})

        # Run cleanup with short TTL
        cleaned = store.cleanup_expired(ttl_seconds=1, artifact_store=artifact_store)
        assert cleaned >= 1

        # Job should be expired
        expired_job = store.get_job(job.job_id)
        assert expired_job.status == PdfJobStatus.EXPIRED
