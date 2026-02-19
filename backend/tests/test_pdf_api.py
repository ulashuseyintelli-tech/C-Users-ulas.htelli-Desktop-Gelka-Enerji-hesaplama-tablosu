"""
Tests for PDF Job API (Task 4).

A1) POST creates job (202) + status QUEUED
A2) POST dedup returns same job_id (idempotency)
A3) GET status queued/running/succeeded/failed
A4) DOWNLOAD succeeded returns pdf bytes + headers
A5) DOWNLOAD before ready returns 409
A6) Invalid template rejected (403)
A7) Payload too large rejected (413)
A8) Enqueue failure → job still created (202, best-effort)
A9) GET nonexistent job → 404
A10) DOWNLOAD nonexistent job → 404
A11) Store not configured → 503
A12) Payload size at boundary (exact limit)
A13) Template allowlist not set → all allowed
A14) Artifact read failure → 500
"""
from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.pdf_api import (
    CreateJobRequest,
    CreateJobResponse,
    JobStatusResponse,
    configure_pdf_api,
    router,
    _get_template_allowlist,
)
from app.services.pdf_artifact_store import PdfArtifactStore
from app.services.pdf_job_store import (
    PdfErrorCode,
    PdfJobStatus,
    PdfJobStore,
)
from app.services.storage_backend import StorageBackend


# ===================================================================
# Test doubles
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
    """Create a fresh FastAPI app with PDF router for each test."""
    test_app = FastAPI()
    test_app.include_router(router)
    configure_pdf_api(store, artifact_store, enqueue_fn)
    return test_app


@pytest.fixture
def client(app):
    return TestClient(app)


# ===================================================================
# A1) POST creates job (202) + status QUEUED
# ===================================================================

class TestCreateJob:
    def test_create_returns_202(self, client, enqueue_fn):
        resp = client.post("/pdf/jobs", json={
            "template_name": "invoice_v1",
            "payload": {"html": "<h1>test</h1>"},
        })
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert "job_id" in data
        enqueue_fn.assert_called_once_with(data["job_id"])

    def test_create_empty_payload(self, client):
        resp = client.post("/pdf/jobs", json={
            "template_name": "receipt",
            "payload": {},
        })
        assert resp.status_code == 202


# ===================================================================
# A2) POST dedup returns same job_id (idempotency)
# ===================================================================

class TestDedup:
    def test_same_input_same_job_id(self, client):
        body = {"template_name": "invoice_v1", "payload": {"a": 1}}
        r1 = client.post("/pdf/jobs", json=body)
        r2 = client.post("/pdf/jobs", json=body)
        assert r1.status_code == 202
        assert r2.status_code == 202
        assert r1.json()["job_id"] == r2.json()["job_id"]

    def test_different_input_different_job_id(self, client):
        r1 = client.post("/pdf/jobs", json={"template_name": "a", "payload": {"x": 1}})
        r2 = client.post("/pdf/jobs", json={"template_name": "b", "payload": {"x": 1}})
        assert r1.json()["job_id"] != r2.json()["job_id"]


# ===================================================================
# A3) GET status queued/running/succeeded/failed
# ===================================================================

class TestGetStatus:
    def test_queued_status(self, client, store):
        job = store.create_job("t", {"html": "<h1>x</h1>"})
        resp = client.get(f"/pdf/jobs/{job.job_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    def test_running_status(self, client, store):
        job = store.create_job("t", {"html": "<h1>x</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        resp = client.get(f"/pdf/jobs/{job.job_id}")
        assert resp.json()["status"] == "running"

    def test_succeeded_status(self, client, store):
        job = store.create_job("t", {"html": "<h1>x</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        store.update_status(job.job_id, PdfJobStatus.SUCCEEDED, artifact_key="pdf/x.pdf")
        resp = client.get(f"/pdf/jobs/{job.job_id}")
        data = resp.json()
        assert data["status"] == "succeeded"
        assert data["artifact_key"] == "pdf/x.pdf"

    def test_failed_status_with_error_code(self, client, store):
        job = store.create_job("t", {"html": "<h1>x</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        store.update_status(job.job_id, PdfJobStatus.FAILED, error_code=PdfErrorCode.TEMPLATE_ERROR)
        resp = client.get(f"/pdf/jobs/{job.job_id}")
        data = resp.json()
        assert data["status"] == "failed"
        assert data["error_code"] == "TEMPLATE_ERROR"


# ===================================================================
# A4) DOWNLOAD succeeded returns pdf bytes + headers
# ===================================================================

class TestDownloadSucceeded:
    def test_download_pdf(self, client, store, artifact_store):
        job = store.create_job("t", {"html": "<h1>x</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        ref = artifact_store.store_pdf(job.job_id, b"%PDF-1.4 content")
        store.update_status(job.job_id, PdfJobStatus.SUCCEEDED, artifact_key=ref)

        resp = client.get(f"/pdf/jobs/{job.job_id}/download")
        assert resp.status_code == 200
        assert resp.content == b"%PDF-1.4 content"
        assert resp.headers["content-type"] == "application/pdf"
        assert f"{job.job_id}.pdf" in resp.headers["content-disposition"]


# ===================================================================
# A5) DOWNLOAD before ready returns 409
# ===================================================================

class TestDownloadNotReady:
    def test_queued_returns_409(self, client, store):
        job = store.create_job("t", {"html": "<h1>x</h1>"})
        resp = client.get(f"/pdf/jobs/{job.job_id}/download")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "JOB_NOT_READY"
        assert resp.json()["detail"]["status"] == "queued"

    def test_running_returns_409(self, client, store):
        job = store.create_job("t", {"html": "<h1>x</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        resp = client.get(f"/pdf/jobs/{job.job_id}/download")
        assert resp.status_code == 409
        assert resp.json()["detail"]["status"] == "running"

    def test_failed_returns_409(self, client, store):
        job = store.create_job("t", {"html": "<h1>x</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        store.update_status(job.job_id, PdfJobStatus.FAILED, error_code=PdfErrorCode.UNKNOWN)
        resp = client.get(f"/pdf/jobs/{job.job_id}/download")
        assert resp.status_code == 409
        assert resp.json()["detail"]["status"] == "failed"


# ===================================================================
# A6) Invalid template rejected (403)
# ===================================================================

class TestTemplateAllowlist:
    def test_rejected_when_not_in_allowlist(self, client, monkeypatch):
        monkeypatch.setenv("PDF_TEMPLATE_ALLOWLIST", "invoice_v1,receipt_v2")
        resp = client.post("/pdf/jobs", json={
            "template_name": "evil_template",
            "payload": {},
        })
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "TEMPLATE_NOT_ALLOWED"

    def test_accepted_when_in_allowlist(self, client, monkeypatch):
        monkeypatch.setenv("PDF_TEMPLATE_ALLOWLIST", "invoice_v1,receipt_v2")
        resp = client.post("/pdf/jobs", json={
            "template_name": "invoice_v1",
            "payload": {},
        })
        assert resp.status_code == 202


# ===================================================================
# A7) Payload too large rejected (413)
# ===================================================================

class TestPayloadSizeLimit:
    def test_large_payload_rejected(self, client, monkeypatch):
        monkeypatch.setenv("PDF_MAX_PAYLOAD_BYTES", "100")
        # Force re-read of env
        import app.pdf_api as mod
        monkeypatch.setattr(mod, "PDF_MAX_PAYLOAD_BYTES", 100)

        resp = client.post("/pdf/jobs", json={
            "template_name": "t",
            "payload": {"data": "x" * 200},
        })
        assert resp.status_code == 413
        assert resp.json()["detail"]["error"] == "PAYLOAD_TOO_LARGE"

    def test_small_payload_accepted(self, client, monkeypatch):
        import app.pdf_api as mod
        monkeypatch.setattr(mod, "PDF_MAX_PAYLOAD_BYTES", 1_000_000)
        resp = client.post("/pdf/jobs", json={
            "template_name": "t",
            "payload": {"small": True},
        })
        assert resp.status_code == 202


# ===================================================================
# A8) Enqueue failure → job still created (202, best-effort)
# ===================================================================

class TestEnqueueFailure:
    def test_enqueue_exception_returns_503(self, client, enqueue_fn):
        enqueue_fn.side_effect = RuntimeError("Redis down")
        resp = client.post("/pdf/jobs", json={
            "template_name": "t",
            "payload": {"html": "<h1>x</h1>"},
        })
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "QUEUE_UNAVAILABLE"

    def test_enqueue_fail_marks_job_failed(self, client, store, enqueue_fn):
        enqueue_fn.side_effect = RuntimeError("Redis down")
        resp = client.post("/pdf/jobs", json={
            "template_name": "t",
            "payload": {"html": "<h1>x</h1>"},
        })
        assert resp.status_code == 503
        # Find the job in store — it should be FAILED with QUEUE_UNAVAILABLE
        # We need to get the job_id; scan store for it
        from app.services.pdf_job_store import compute_job_key, PdfErrorCode
        job_key = compute_job_key("t", {"html": "<h1>x</h1>"})
        job = store.find_by_key(job_key)
        assert job is not None
        assert job.status == PdfJobStatus.FAILED
        assert job.error_code == PdfErrorCode.QUEUE_UNAVAILABLE


# ===================================================================
# A9) GET nonexistent job → 404
# ===================================================================

class TestNotFound:
    def test_get_nonexistent(self, client):
        resp = client.get("/pdf/jobs/nonexistent-id")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "JOB_NOT_FOUND"


# ===================================================================
# A10) DOWNLOAD nonexistent job → 404
# ===================================================================

class TestDownloadNotFound:
    def test_download_nonexistent(self, client):
        resp = client.get("/pdf/jobs/nonexistent-id/download")
        assert resp.status_code == 404


# ===================================================================
# A11) Store not configured → 503
# ===================================================================

class TestStoreNotConfigured:
    def test_503_when_store_missing(self):
        test_app = FastAPI()
        test_app.include_router(router)
        # Reset globals to None
        configure_pdf_api(None, None, None)
        c = TestClient(test_app, raise_server_exceptions=False)

        resp = c.post("/pdf/jobs", json={"template_name": "t", "payload": {}})
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "PDF_RENDER_UNAVAILABLE"


# ===================================================================
# A12) Payload size at boundary (exact limit)
# ===================================================================

class TestPayloadBoundary:
    def test_exact_limit_accepted(self, client, monkeypatch):
        import app.pdf_api as mod
        # Set limit to exact size of the payload we'll send
        payload = {"k": "v"}
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        monkeypatch.setattr(mod, "PDF_MAX_PAYLOAD_BYTES", size)

        resp = client.post("/pdf/jobs", json={
            "template_name": "t",
            "payload": payload,
        })
        assert resp.status_code == 202

    def test_one_byte_over_rejected(self, client, monkeypatch):
        import app.pdf_api as mod
        payload = {"k": "v"}
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        monkeypatch.setattr(mod, "PDF_MAX_PAYLOAD_BYTES", size - 1)

        resp = client.post("/pdf/jobs", json={
            "template_name": "t",
            "payload": payload,
        })
        assert resp.status_code == 413


# ===================================================================
# A13) Template allowlist not set → all allowed
# ===================================================================

class TestNoAllowlist:
    def test_any_template_accepted(self, client, monkeypatch):
        monkeypatch.delenv("PDF_TEMPLATE_ALLOWLIST", raising=False)
        resp = client.post("/pdf/jobs", json={
            "template_name": "anything_goes",
            "payload": {},
        })
        assert resp.status_code == 202


# ===================================================================
# A14) Artifact read failure → 500
# ===================================================================

class TestArtifactReadFailure:
    def test_artifact_read_error_returns_500(self, client, store, artifact_store):
        job = store.create_job("t", {"html": "<h1>x</h1>"})
        store.update_status(job.job_id, PdfJobStatus.RUNNING)
        store.update_status(job.job_id, PdfJobStatus.SUCCEEDED, artifact_key="pdf/missing.pdf")
        # artifact not actually stored → get_pdf will raise

        resp = client.get(f"/pdf/jobs/{job.job_id}/download")
        assert resp.status_code == 500
        assert resp.json()["detail"]["error"] == "ARTIFACT_READ_FAILED"


# ===================================================================
# A15) Prod requires template allowlist
# ===================================================================

class TestProdAllowlistRequired:
    def test_prod_no_allowlist_returns_503(self, client, monkeypatch):
        monkeypatch.setenv("PDF_ENV", "production")
        monkeypatch.delenv("PDF_TEMPLATE_ALLOWLIST", raising=False)
        resp = client.post("/pdf/jobs", json={
            "template_name": "invoice_v1",
            "payload": {},
        })
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "PDF_RENDER_UNAVAILABLE"
        assert "allowlist" in resp.json()["detail"]["message"].lower()

    def test_dev_no_allowlist_still_works(self, client, monkeypatch):
        monkeypatch.setenv("PDF_ENV", "dev")
        monkeypatch.delenv("PDF_TEMPLATE_ALLOWLIST", raising=False)
        resp = client.post("/pdf/jobs", json={
            "template_name": "anything",
            "payload": {},
        })
        assert resp.status_code == 202
