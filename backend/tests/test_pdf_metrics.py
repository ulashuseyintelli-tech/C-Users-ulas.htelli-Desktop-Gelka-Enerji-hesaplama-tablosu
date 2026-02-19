"""
Tests for PDF Render Worker Metrics (Task 5).

PM1) Counter increments — pdf_jobs_total by status
PM2) Counter increments — pdf_job_failures_total by error_code
PM3) Duration histogram — pdf_job_duration_seconds
PM4) Queue depth gauge — pdf_queue_depth
PM5) Label bounded — invalid status/error_code rejected
PM6) Worker integration — render_pdf_job emits metrics
PM7) API integration — create_pdf_job emits queued metric
PM8) Enqueue fail emits failure metric
"""
from __future__ import annotations

import json
import os
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import CollectorRegistry

from app.ptf_metrics import PTFMetrics


# ═══════════════════════════════════════════════════════════════════════════════
# PM1) pdf_jobs_total counter
# ═══════════════════════════════════════════════════════════════════════════════


class TestPdfJobsTotal:
    def setup_method(self):
        self.m = PTFMetrics(registry=CollectorRegistry())

    def test_inc_queued(self):
        self.m.inc_pdf_job("queued")
        val = self.m._pdf_jobs_total.labels(status="queued")._value.get()
        assert val == 1.0

    def test_inc_running(self):
        self.m.inc_pdf_job("running")
        val = self.m._pdf_jobs_total.labels(status="running")._value.get()
        assert val == 1.0

    def test_inc_succeeded(self):
        self.m.inc_pdf_job("succeeded")
        val = self.m._pdf_jobs_total.labels(status="succeeded")._value.get()
        assert val == 1.0

    def test_inc_failed(self):
        self.m.inc_pdf_job("failed")
        val = self.m._pdf_jobs_total.labels(status="failed")._value.get()
        assert val == 1.0

    def test_inc_expired(self):
        self.m.inc_pdf_job("expired")
        val = self.m._pdf_jobs_total.labels(status="expired")._value.get()
        assert val == 1.0

    def test_multiple_increments(self):
        self.m.inc_pdf_job("succeeded")
        self.m.inc_pdf_job("succeeded")
        self.m.inc_pdf_job("failed")
        val_s = self.m._pdf_jobs_total.labels(status="succeeded")._value.get()
        val_f = self.m._pdf_jobs_total.labels(status="failed")._value.get()
        assert val_s == 2.0
        assert val_f == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# PM2) pdf_job_failures_total counter
# ═══════════════════════════════════════════════════════════════════════════════


class TestPdfJobFailuresTotal:
    def setup_method(self):
        self.m = PTFMetrics(registry=CollectorRegistry())

    def test_inc_browser_launch_failed(self):
        self.m.inc_pdf_failure("BROWSER_LAUNCH_FAILED")
        val = self.m._pdf_job_failures_total.labels(error_code="BROWSER_LAUNCH_FAILED")._value.get()
        assert val == 1.0

    def test_inc_navigation_timeout(self):
        self.m.inc_pdf_failure("NAVIGATION_TIMEOUT")
        val = self.m._pdf_job_failures_total.labels(error_code="NAVIGATION_TIMEOUT")._value.get()
        assert val == 1.0

    def test_inc_template_error(self):
        self.m.inc_pdf_failure("TEMPLATE_ERROR")
        val = self.m._pdf_job_failures_total.labels(error_code="TEMPLATE_ERROR")._value.get()
        assert val == 1.0

    def test_inc_queue_unavailable(self):
        self.m.inc_pdf_failure("QUEUE_UNAVAILABLE")
        val = self.m._pdf_job_failures_total.labels(error_code="QUEUE_UNAVAILABLE")._value.get()
        assert val == 1.0

    def test_inc_artifact_write_failed(self):
        self.m.inc_pdf_failure("ARTIFACT_WRITE_FAILED")
        val = self.m._pdf_job_failures_total.labels(error_code="ARTIFACT_WRITE_FAILED")._value.get()
        assert val == 1.0

    def test_inc_unknown(self):
        self.m.inc_pdf_failure("UNKNOWN")
        val = self.m._pdf_job_failures_total.labels(error_code="UNKNOWN")._value.get()
        assert val == 1.0

    def test_all_error_codes_accepted(self):
        """All 7 PdfErrorCode values are valid labels."""
        codes = [
            "BROWSER_LAUNCH_FAILED", "NAVIGATION_TIMEOUT", "TEMPLATE_ERROR",
            "UNSUPPORTED_PLATFORM", "ARTIFACT_WRITE_FAILED", "QUEUE_UNAVAILABLE", "UNKNOWN",
        ]
        for code in codes:
            self.m.inc_pdf_failure(code)
        for code in codes:
            val = self.m._pdf_job_failures_total.labels(error_code=code)._value.get()
            assert val == 1.0, f"{code} should be 1.0"


# ═══════════════════════════════════════════════════════════════════════════════
# PM3) pdf_job_duration_seconds histogram
# ═══════════════════════════════════════════════════════════════════════════════


class TestPdfJobDuration:
    def setup_method(self):
        self.m = PTFMetrics(registry=CollectorRegistry())

    def test_observe_duration(self):
        self.m.observe_pdf_job_duration(1.5)
        total = self.m._pdf_job_duration_seconds._sum.get()
        assert total == 1.5

    def test_multiple_observations(self):
        self.m.observe_pdf_job_duration(1.0)
        self.m.observe_pdf_job_duration(2.5)
        total = self.m._pdf_job_duration_seconds._sum.get()
        assert abs(total - 3.5) < 0.001


# ═══════════════════════════════════════════════════════════════════════════════
# PM4) pdf_queue_depth gauge
# ═══════════════════════════════════════════════════════════════════════════════


class TestPdfQueueDepth:
    def setup_method(self):
        self.m = PTFMetrics(registry=CollectorRegistry())

    def test_set_depth(self):
        self.m.set_pdf_queue_depth(42)
        val = self.m._pdf_queue_depth._value.get()
        assert val == 42.0

    def test_set_zero(self):
        self.m.set_pdf_queue_depth(42)
        self.m.set_pdf_queue_depth(0)
        val = self.m._pdf_queue_depth._value.get()
        assert val == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# PM5) Label bounded — invalid values rejected
# ═══════════════════════════════════════════════════════════════════════════════


class TestLabelBounded:
    def setup_method(self):
        self.m = PTFMetrics(registry=CollectorRegistry())

    def test_invalid_status_ignored(self):
        """Invalid status → no increment, no crash."""
        self.m.inc_pdf_job("bogus_status")
        # No exception raised, counter not incremented for valid statuses
        val = self.m._pdf_jobs_total.labels(status="queued")._value.get()
        assert val == 0.0

    def test_invalid_error_code_ignored(self):
        """Invalid error_code → no increment, no crash."""
        self.m.inc_pdf_failure("NOT_A_REAL_CODE")
        # No exception raised

    def test_status_cardinality_bounded(self):
        """Only 5 valid status values exist."""
        valid = {"queued", "running", "succeeded", "failed", "expired"}
        assert PTFMetrics._VALID_PDF_STATUSES == valid

    def test_error_code_cardinality_bounded(self):
        """Only 7 valid error codes exist."""
        assert len(PTFMetrics._VALID_PDF_ERROR_CODES) == 7


# ═══════════════════════════════════════════════════════════════════════════════
# PM6) Worker integration — render_pdf_job emits metrics
# ═══════════════════════════════════════════════════════════════════════════════


class FakeRedis:
    """Minimal Redis mock for PdfJobStore."""

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


class TestWorkerMetricsIntegration:
    """render_pdf_job emits succeeded/failed metrics."""

    def test_succeeded_emits_metrics(self, tmp_path, monkeypatch):
        from app.services.pdf_job_store import PdfJobStore
        from app.services.pdf_render_worker import render_pdf_job
        import app.services.pdf_render_worker as mod

        monkeypatch.setattr(mod, "ARTIFACT_BASE_DIR", str(tmp_path))

        m = PTFMetrics(registry=CollectorRegistry())
        monkeypatch.setattr("app.services.pdf_render_worker.get_ptf_metrics", lambda: m)

        store = PdfJobStore(FakeRedis())
        job = store.create_job("t", {"html": "<h1>x</h1>"})

        with patch("app.services.pdf_render_worker.render_html_to_pdf", return_value=b"%PDF"):
            render_pdf_job(job.job_id, store=store)

        val = m._pdf_jobs_total.labels(status="succeeded")._value.get()
        assert val == 1.0
        dur = m._pdf_job_duration_seconds._sum.get()
        assert dur > 0

    def test_failed_emits_metrics(self, tmp_path, monkeypatch):
        from app.services.pdf_job_store import PdfJobStore, PdfErrorCode
        from app.services.pdf_render_worker import render_pdf_job, RenderError
        import app.services.pdf_render_worker as mod

        monkeypatch.setattr(mod, "ARTIFACT_BASE_DIR", str(tmp_path))

        m = PTFMetrics(registry=CollectorRegistry())
        monkeypatch.setattr("app.services.pdf_render_worker.get_ptf_metrics", lambda: m)

        store = PdfJobStore(FakeRedis())
        job = store.create_job("t", {"html": "<h1>x</h1>"})

        with patch(
            "app.services.pdf_render_worker.render_html_to_pdf",
            side_effect=RenderError(PdfErrorCode.TEMPLATE_ERROR, "bad"),
        ):
            render_pdf_job(job.job_id, store=store)

        val_f = m._pdf_jobs_total.labels(status="failed")._value.get()
        assert val_f == 1.0
        val_ec = m._pdf_job_failures_total.labels(error_code="TEMPLATE_ERROR")._value.get()
        assert val_ec == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# PM7/PM8) API integration — create emits queued, enqueue fail emits failure
# ═══════════════════════════════════════════════════════════════════════════════


class TestApiMetricsIntegration:
    """PDF API emits metrics on create and enqueue failure."""

    def _make_client(self, store, artifact_store, enqueue_fn, metrics, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.pdf_api import router, configure_pdf_api

        monkeypatch.setattr("app.pdf_api.get_ptf_metrics", lambda: metrics)

        app = FastAPI()
        app.include_router(router)
        configure_pdf_api(store, artifact_store, enqueue_fn)
        return TestClient(app)

    def test_create_emits_queued(self, monkeypatch):
        from app.services.pdf_job_store import PdfJobStore
        from app.services.pdf_artifact_store import PdfArtifactStore

        m = PTFMetrics(registry=CollectorRegistry())
        store = PdfJobStore(FakeRedis())

        class DummyStorage:
            def put_bytes(self, k, d, ct): return k
            def get_bytes(self, r): return b""
            def exists(self, r): return False
            def delete(self, r): return False

        art = PdfArtifactStore(DummyStorage())
        enqueue = MagicMock(return_value=True)
        client = self._make_client(store, art, enqueue, m, monkeypatch)

        resp = client.post("/pdf/jobs", json={"template_name": "t", "payload": {}})
        assert resp.status_code == 202

        val = m._pdf_jobs_total.labels(status="queued")._value.get()
        assert val == 1.0

    def test_enqueue_fail_emits_failure(self, monkeypatch):
        from app.services.pdf_job_store import PdfJobStore
        from app.services.pdf_artifact_store import PdfArtifactStore

        m = PTFMetrics(registry=CollectorRegistry())
        store = PdfJobStore(FakeRedis())

        class DummyStorage:
            def put_bytes(self, k, d, ct): return k
            def get_bytes(self, r): return b""
            def exists(self, r): return False
            def delete(self, r): return False

        art = PdfArtifactStore(DummyStorage())
        enqueue = MagicMock(side_effect=RuntimeError("Redis down"))
        client = self._make_client(store, art, enqueue, m, monkeypatch)

        resp = client.post("/pdf/jobs", json={"template_name": "t", "payload": {"html": "<h1>x</h1>"}})
        assert resp.status_code == 503

        val_f = m._pdf_jobs_total.labels(status="failed")._value.get()
        assert val_f == 1.0
        val_qu = m._pdf_job_failures_total.labels(error_code="QUEUE_UNAVAILABLE")._value.get()
        assert val_qu == 1.0
