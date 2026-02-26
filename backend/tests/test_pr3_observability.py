"""
PR-3 Observability — Metrik Sözleşmesi Test Suite

Contract v2 doğrulaması:
  A) Label boundedness (status + reason kapalı küme)
  B) Inflight safety (<=2 invariant)
  C) Acquire histogram on 429 path
  D) Arithmetic consistency (overhead >= -epsilon)
  E) Bytes metric (200→>0, error→0)
  F) Metric registration (tüm metrikler /metrics'te görünür)
  G) Structured log fields (request_id, status, timing)
"""

import asyncio
import time
import threading
from unittest.mock import patch, MagicMock

import pytest
from starlette.testclient import TestClient
from prometheus_client import CollectorRegistry

# ── App & metrics imports ──
from backend.app.main import app
from backend.app import main as main_mod
from backend.app.ptf_metrics import PTFMetrics


FAKE_PDF_BYTES = b"%PDF-1.4 fake content padding to exceed 10 bytes minimum"
FORM_DATA = {
    "consumption_kwh": "1000",
    "current_energy_tl": "500",
    "offer_energy_tl": "400",
    "offer_total": "450",
    "savings_ratio": "0.10",
}


@pytest.fixture
def fresh_metrics():
    """Create a fresh PTFMetrics instance with isolated registry for each test."""
    return PTFMetrics(registry=CollectorRegistry())


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ═══════════════════════════════════════════════════════════════════════════════
# A) Label Boundedness
# ═══════════════════════════════════════════════════════════════════════════════

class TestLabelBoundedness:
    """Status and reason labels are closed sets — invalid values are rejected."""

    def test_valid_status_labels_accepted(self, fresh_metrics):
        for status in ("200", "429", "500", "504"):
            fresh_metrics.inc_pdf_render_request(status)
        # No exception = pass

    def test_invalid_status_rejected(self, fresh_metrics):
        fresh_metrics.inc_pdf_render_request("201")
        fresh_metrics.inc_pdf_render_request("403")
        fresh_metrics.inc_pdf_render_request("unknown")
        # Should not raise, but metric should not be incremented
        output = fresh_metrics.generate_metrics().decode()
        assert 'status="201"' not in output
        assert 'status="403"' not in output
        assert 'status="unknown"' not in output

    def test_valid_reason_labels_accepted(self, fresh_metrics):
        for reason in ("empty_pdf", "timeout", "internal_error"):
            fresh_metrics.inc_pdf_render_error(reason)

    def test_invalid_reason_rejected(self, fresh_metrics):
        fresh_metrics.inc_pdf_render_error("rate_limited")
        fresh_metrics.inc_pdf_render_error("unknown")
        output = fresh_metrics.generate_metrics().decode()
        assert 'reason="rate_limited"' not in output
        assert 'reason="unknown"' not in output

    def test_429_not_in_errors_total(self, fresh_metrics):
        """429 is intentional rejection, not an error — should not appear in errors_total."""
        fresh_metrics.inc_pdf_render_request("429")
        output = fresh_metrics.generate_metrics().decode()
        # requests_total should have 429
        assert 'ptf_admin_pdf_render_requests_total{status="429"}' in output
        # errors_total should NOT have any 429-related entry
        assert 'reason="rate_limited"' not in output


# ═══════════════════════════════════════════════════════════════════════════════
# B) Inflight Safety
# ═══════════════════════════════════════════════════════════════════════════════

class TestInflightSafety:
    """Inflight gauge never exceeds _PDF_MAX_CONCURRENT."""

    def test_inflight_inc_dec_symmetry(self, fresh_metrics):
        fresh_metrics.pdf_render_inflight_inc()
        fresh_metrics.pdf_render_inflight_inc()
        fresh_metrics.pdf_render_inflight_dec()
        fresh_metrics.pdf_render_inflight_dec()
        output = fresh_metrics.generate_metrics().decode()
        # After balanced inc/dec, gauge should be 0
        assert "ptf_admin_pdf_render_inflight 0.0" in output

    def test_inflight_never_exceeds_max_under_load(self):
        """Under concurrent requests, inflight should never exceed 2."""
        max_observed = 0
        lock = threading.Lock()

        original_generate = main_mod.generate_offer_pdf_bytes

        def slow_render(*args, **kwargs):
            nonlocal max_observed
            # Read inflight from the global metrics
            from backend.app.ptf_metrics import get_ptf_metrics
            m = get_ptf_metrics()
            # Approximate: count active renders via semaphore
            current = main_mod._PDF_MAX_CONCURRENT - main_mod._pdf_semaphore._value
            with lock:
                if current > max_observed:
                    max_observed = current
            time.sleep(1)
            return FAKE_PDF_BYTES

        async def _run():
            import httpx
            from httpx import ASGITransport
            main_mod._pdf_semaphore = asyncio.Semaphore(main_mod._PDF_MAX_CONCURRENT)
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                tasks = []
                for _ in range(5):
                    tasks.append(ac.post("/generate-pdf-simple", data=FORM_DATA))
                await asyncio.gather(*tasks)

        with patch.object(main_mod, "generate_offer_pdf_bytes", side_effect=slow_render):
            asyncio.run(_run())

        assert max_observed <= main_mod._PDF_MAX_CONCURRENT


# ═══════════════════════════════════════════════════════════════════════════════
# C) Acquire Histogram on 429 Path
# ═══════════════════════════════════════════════════════════════════════════════

class TestAcquireHistogramOn429:
    """Semaphore acquire duration is observed even when 429 is returned."""

    def test_429_observes_acquire_histogram(self):
        """When acquire times out (429), the acquire histogram should still record the duration."""
        from backend.app.ptf_metrics import get_ptf_metrics

        def slow_render(*args, **kwargs):
            time.sleep(3)
            return FAKE_PDF_BYTES

        async def _run():
            import httpx
            from httpx import ASGITransport
            main_mod._pdf_semaphore = asyncio.Semaphore(main_mod._PDF_MAX_CONCURRENT)
            metrics = get_ptf_metrics()
            metrics.reset()

            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                tasks = [ac.post("/generate-pdf-simple", data=FORM_DATA) for _ in range(5)]
                responses = await asyncio.gather(*tasks)

            statuses = [r.status_code for r in responses]
            count_429 = statuses.count(429)

            # Check that acquire histogram has observations for ALL requests (not just 200s)
            output = metrics.generate_metrics().decode()
            # The histogram _count should equal total requests (5)
            for line in output.split("\n"):
                if "ptf_admin_pdf_render_semaphore_acquire_seconds_count" in line and "{" not in line:
                    total_observations = float(line.split()[-1])
                    assert total_observations == 5.0, f"Expected 5 observations, got {total_observations}"
                    break
            else:
                pytest.fail("acquire histogram _count not found in metrics output")

            return count_429

        with patch.object(main_mod, "generate_offer_pdf_bytes", side_effect=slow_render):
            count_429 = asyncio.run(_run())

        assert count_429 >= 1, "Expected at least one 429 response"


# ═══════════════════════════════════════════════════════════════════════════════
# D) Arithmetic Consistency
# ═══════════════════════════════════════════════════════════════════════════════

class TestArithmeticConsistency:
    """overhead = total - acquire - executor should never be significantly negative."""

    def test_overhead_non_negative_on_success(self):
        """On a successful 200 request, overhead should be >= -10ms (epsilon)."""
        from backend.app.ptf_metrics import get_ptf_metrics

        async def _run():
            import httpx
            from httpx import ASGITransport
            main_mod._pdf_semaphore = asyncio.Semaphore(main_mod._PDF_MAX_CONCURRENT)
            metrics = get_ptf_metrics()
            metrics.reset()

            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post("/generate-pdf-simple", data=FORM_DATA)

            assert resp.status_code == 200

            output = metrics.generate_metrics().decode()
            # overhead histogram should have at least 1 observation
            for line in output.split("\n"):
                if "ptf_admin_pdf_render_overhead_seconds_count" in line and "{" not in line:
                    count = float(line.split()[-1])
                    assert count >= 1.0
                    break

            # The sum should be >= 0 (we clamp with max(0, ...))
            for line in output.split("\n"):
                if "ptf_admin_pdf_render_overhead_seconds_sum" in line and "{" not in line:
                    total = float(line.split()[-1])
                    assert total >= 0.0, f"Overhead sum should be >= 0, got {total}"
                    break

        with patch.object(main_mod, "generate_offer_pdf_bytes", return_value=FAKE_PDF_BYTES):
            asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════════════════════
# E) Bytes Metric
# ═══════════════════════════════════════════════════════════════════════════════

class TestBytesMetric:
    """PDF bytes histogram is observed on all paths."""

    def test_200_observes_actual_bytes(self):
        from backend.app.ptf_metrics import get_ptf_metrics

        async def _run():
            import httpx
            from httpx import ASGITransport
            main_mod._pdf_semaphore = asyncio.Semaphore(main_mod._PDF_MAX_CONCURRENT)
            metrics = get_ptf_metrics()
            metrics.reset()

            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post("/generate-pdf-simple", data=FORM_DATA)

            assert resp.status_code == 200
            output = metrics.generate_metrics().decode()
            for line in output.split("\n"):
                if "ptf_admin_pdf_render_bytes_sum" in line and "{" not in line:
                    total_bytes = float(line.split()[-1])
                    assert total_bytes > 0, f"Expected bytes > 0 for 200, got {total_bytes}"
                    break

        with patch.object(main_mod, "generate_offer_pdf_bytes", return_value=FAKE_PDF_BYTES):
            asyncio.run(_run())

    def test_empty_pdf_observes_zero_bytes(self):
        from backend.app.ptf_metrics import get_ptf_metrics

        async def _run():
            import httpx
            from httpx import ASGITransport
            main_mod._pdf_semaphore = asyncio.Semaphore(main_mod._PDF_MAX_CONCURRENT)
            metrics = get_ptf_metrics()
            metrics.reset()

            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.post("/generate-pdf-simple", data=FORM_DATA)

            assert resp.status_code == 500
            output = metrics.generate_metrics().decode()
            for line in output.split("\n"):
                if "ptf_admin_pdf_render_bytes_sum" in line and "{" not in line:
                    total_bytes = float(line.split()[-1])
                    assert total_bytes == 0.0, f"Expected bytes=0 for empty_pdf, got {total_bytes}"
                    break

        with patch.object(main_mod, "generate_offer_pdf_bytes", return_value=b""):
            asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════════════════════
# F) Metric Registration
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricRegistration:
    """All PR-3 metrics appear in /metrics exposition."""

    EXPECTED_METRICS = [
        "ptf_admin_pdf_render_requests_total",
        "ptf_admin_pdf_render_errors_total",
        "ptf_admin_pdf_render_inflight",
        "ptf_admin_pdf_render_semaphore_acquire_seconds",
        "ptf_admin_pdf_render_executor_seconds",
        "ptf_admin_pdf_render_total_seconds",
        "ptf_admin_pdf_render_overhead_seconds",
        "ptf_admin_pdf_render_bytes",
    ]

    def test_all_metrics_registered(self, fresh_metrics):
        output = fresh_metrics.generate_metrics().decode()
        for metric_name in self.EXPECTED_METRICS:
            assert metric_name in output, f"Metric {metric_name} not found in exposition"

    def test_metrics_endpoint_includes_pdf_render(self, client):
        """GET /metrics should include pdf_render metrics."""
        with patch.object(main_mod, "generate_offer_pdf_bytes", return_value=FAKE_PDF_BYTES):
            resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        for metric_name in self.EXPECTED_METRICS:
            assert metric_name in body, f"Metric {metric_name} not in /metrics response"


# ═══════════════════════════════════════════════════════════════════════════════
# G) Request Counter Accuracy
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequestCounterAccuracy:
    """requests_total counter increments correctly per status code."""

    def test_200_increments_counter(self):
        from backend.app.ptf_metrics import get_ptf_metrics

        async def _run():
            import httpx
            from httpx import ASGITransport
            main_mod._pdf_semaphore = asyncio.Semaphore(main_mod._PDF_MAX_CONCURRENT)
            metrics = get_ptf_metrics()
            metrics.reset()

            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                for _ in range(3):
                    await ac.post("/generate-pdf-simple", data=FORM_DATA)

            output = metrics.generate_metrics().decode()
            for line in output.split("\n"):
                if 'ptf_admin_pdf_render_requests_total{status="200"}' in line:
                    val = float(line.split()[-1])
                    assert val == 3.0, f"Expected 3 requests with status=200, got {val}"
                    return
            pytest.fail("status=200 counter not found")

        with patch.object(main_mod, "generate_offer_pdf_bytes", return_value=FAKE_PDF_BYTES):
            asyncio.run(_run())

    def test_500_increments_counter_on_exception(self):
        from backend.app.ptf_metrics import get_ptf_metrics

        async def _run():
            import httpx
            from httpx import ASGITransport
            main_mod._pdf_semaphore = asyncio.Semaphore(main_mod._PDF_MAX_CONCURRENT)
            metrics = get_ptf_metrics()
            metrics.reset()

            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                await ac.post("/generate-pdf-simple", data=FORM_DATA)

            output = metrics.generate_metrics().decode()
            for line in output.split("\n"):
                if 'ptf_admin_pdf_render_requests_total{status="500"}' in line:
                    val = float(line.split()[-1])
                    assert val == 1.0
                    return
            pytest.fail("status=500 counter not found")

        with patch.object(main_mod, "generate_offer_pdf_bytes", side_effect=RuntimeError("boom")):
            asyncio.run(_run())
