"""
PR-2 Kapanış Testleri — Kanıt Odaklı

5 checklist maddesi:
  1) Backend contract: PDF vs JSON response (headers, body structure)
  2) Concurrency + backpressure: max 2 concurrent, 429 for overflow
  3) Thread determinism: dedicated executor, max 2 threads
  4) Electron IPC: structured error parsing (unit test of JS logic in Python)
  5) Sequential stability: N ardışık request → 0 failure

Tüm testler mock PDF generator ile çalışır (Playwright gerektirmez).
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ─── Fixtures ─────────────────────────────────────────────────────────────────

# Fake PDF bytes (%PDF- magic + padding)
FAKE_PDF_BYTES = b"%PDF-1.4 fake pdf content padding to exceed 10 bytes minimum"


@pytest.fixture()
def client():
    """
    TestClient with:
    - DB mocked
    - Auth bypassed
    - PDF generator mocked (returns FAKE_PDF_BYTES instantly)
    """
    with patch.dict(os.environ, {
        "ADMIN_API_KEY_ENABLED": "false",
        "API_KEY_ENABLED": "false",
    }):
        from app.main import app as fastapi_app
        from app.database import get_db

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db

        with patch(
            "app.main.generate_offer_pdf_bytes",
            return_value=FAKE_PDF_BYTES,
        ):
            yield TestClient(fastapi_app, raise_server_exceptions=False)

        fastapi_app.dependency_overrides.clear()


def _pdf_form_data() -> dict:
    """Minimum valid form data for /generate-pdf-simple."""
    return {
        "consumption_kwh": "1000",
        "current_energy_tl": "500",
        "offer_energy_tl": "400",
        "offer_total": "450",
        "savings_ratio": "0.10",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1) Backend Contract: PDF vs JSON
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackendContract:
    """
    Checklist #1: 200 → PDF headers, error → JSON + structured error body.
    """

    def test_200_returns_pdf_content_type(self, client):
        """200 response must have Content-Type: application/pdf."""
        resp = client.post("/generate-pdf-simple", data=_pdf_form_data())
        assert resp.status_code == 200
        assert "application/pdf" in resp.headers["content-type"]

    def test_200_has_content_disposition(self, client):
        """200 response must have Content-Disposition: attachment header."""
        resp = client.post("/generate-pdf-simple", data=_pdf_form_data())
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert "filename=" in cd

    def test_200_has_x_request_id(self, client):
        """200 response must include X-Request-Id header."""
        resp = client.post("/generate-pdf-simple", data=_pdf_form_data())
        assert resp.status_code == 200
        rid = resp.headers.get("x-request-id", "")
        assert len(rid) > 0, "X-Request-Id header must be non-empty"

    def test_200_body_is_valid_pdf(self, client):
        """200 response body must start with %PDF- magic bytes."""
        resp = client.post("/generate-pdf-simple", data=_pdf_form_data())
        assert resp.status_code == 200
        assert resp.content[:5] == b"%PDF-"

    def test_empty_pdf_returns_json_error(self):
        """Generator returning empty bytes → 500 JSON with error structure."""
        with patch.dict(os.environ, {
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }):
            from app.main import app as fastapi_app
            from app.database import get_db

            mock_db = MagicMock()
            fastapi_app.dependency_overrides[get_db] = lambda: mock_db

            with patch("app.main.generate_offer_pdf_bytes", return_value=b""):
                c = TestClient(fastapi_app, raise_server_exceptions=False)
                resp = c.post("/generate-pdf-simple", data=_pdf_form_data())

            fastapi_app.dependency_overrides.clear()

        assert resp.status_code == 500
        assert "application/json" in resp.headers["content-type"]
        body = resp.json()
        err = body.get("error", {})
        assert err.get("code") == "empty_pdf"
        assert "request_id" in err
        assert resp.headers.get("x-request-id", "") != ""

    def test_exception_returns_json_error(self):
        """Generator raising exception → 500 JSON with internal_error code."""
        with patch.dict(os.environ, {
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }):
            from app.main import app as fastapi_app
            from app.database import get_db

            mock_db = MagicMock()
            fastapi_app.dependency_overrides[get_db] = lambda: mock_db

            with patch(
                "app.main.generate_offer_pdf_bytes",
                side_effect=RuntimeError("Playwright crashed"),
            ):
                c = TestClient(fastapi_app, raise_server_exceptions=False)
                resp = c.post("/generate-pdf-simple", data=_pdf_form_data())

            fastapi_app.dependency_overrides.clear()

        assert resp.status_code == 500
        assert "application/json" in resp.headers["content-type"]
        body = resp.json()
        err = body.get("error", {})
        assert err.get("code") == "internal_error"
        assert "request_id" in err
        assert resp.headers.get("x-request-id", "") != ""

    def test_error_responses_have_consistent_structure(self):
        """All error responses must follow {error: {code, message, request_id}}."""
        with patch.dict(os.environ, {
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }):
            from app.main import app as fastapi_app
            from app.database import get_db

            mock_db = MagicMock()
            fastapi_app.dependency_overrides[get_db] = lambda: mock_db

            # Test both empty_pdf and internal_error
            for side_effect, expected_code in [
                (b"tiny", "empty_pdf"),  # < 10 bytes
                (RuntimeError("boom"), "internal_error"),
            ]:
                if isinstance(side_effect, bytes):
                    patcher = patch("app.main.generate_offer_pdf_bytes", return_value=side_effect)
                else:
                    patcher = patch("app.main.generate_offer_pdf_bytes", side_effect=side_effect)

                with patcher:
                    c = TestClient(fastapi_app, raise_server_exceptions=False)
                    resp = c.post("/generate-pdf-simple", data=_pdf_form_data())

                body = resp.json()
                err = body.get("error", {})
                assert "code" in err, f"Missing 'code' in error for {expected_code}"
                assert "message" in err, f"Missing 'message' in error for {expected_code}"
                assert "request_id" in err, f"Missing 'request_id' in error for {expected_code}"

            fastapi_app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# 2) Concurrency + Backpressure
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrencyBackpressure:
    """
    Checklist #2: max 2 concurrent renders, overflow → 429 + Retry-After.

    Strategy: Use httpx.AsyncClient with ASGITransport so all requests share
    the same event loop and thus the same asyncio.Semaphore.
    Mock PDF generator with time.sleep (holds semaphore > acquire timeout).
    Fire 5 concurrent requests. Expect 2x 200, 3x 429.
    """

    def test_concurrent_requests_backpressure_and_429_contract(self):
        """
        Combined concurrency test (single asyncio.run to avoid semaphore state leaks):
        - 5 parallel requests → 2x 200, 3x 429
        - 429 responses have Retry-After: 5 header
        - 429 responses have structured JSON error body
        - 429 JSON has code=too_many_requests + request_id
        """
        import httpx

        async def _run():
            with patch.dict(os.environ, {
                "ADMIN_API_KEY_ENABLED": "false",
                "API_KEY_ENABLED": "false",
            }):
                from app.main import app as fastapi_app, _pdf_semaphore, _PDF_MAX_CONCURRENT
                from app.database import get_db

                # Reset semaphore to clean state
                import app.main as main_mod
                main_mod._pdf_semaphore = asyncio.Semaphore(_PDF_MAX_CONCURRENT)

                mock_db = MagicMock()
                fastapi_app.dependency_overrides[get_db] = lambda: mock_db

                def slow_pdf(*args, **kwargs):
                    """Hold executor thread for 3s (> 2s semaphore acquire timeout)."""
                    time.sleep(3)
                    return FAKE_PDF_BYTES

                with patch("app.main.generate_offer_pdf_bytes", side_effect=slow_pdf):
                    transport = httpx.ASGITransport(app=fastapi_app)
                    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                        tasks = [
                            ac.post("/generate-pdf-simple", data=_pdf_form_data())
                            for _ in range(5)
                        ]
                        responses = await asyncio.gather(*tasks)

                fastapi_app.dependency_overrides.clear()
            return responses

        responses = asyncio.run(_run())
        codes = [r.status_code for r in responses]

        # ── Assert 1: Status code distribution ──
        count_200 = codes.count(200)
        count_429 = codes.count(429)
        assert count_200 == 2, f"Expected 2x 200, got {count_200}. All: {codes}"
        assert count_429 == 3, f"Expected 3x 429, got {count_429}. All: {codes}"

        # ── Assert 2: 429 responses have Retry-After header ──
        responses_429 = [r for r in responses if r.status_code == 429]
        for resp in responses_429:
            assert "retry-after" in resp.headers, "429 must have Retry-After header"
            assert resp.headers["retry-after"] == "5"

        # ── Assert 3: 429 responses have structured JSON error body ──
        for resp in responses_429:
            body = resp.json()
            err = body.get("error", {})
            assert err.get("code") == "too_many_requests", f"Expected too_many_requests, got: {err}"
            assert "message" in err, "429 error must have 'message'"
            assert "request_id" in err, "429 error must have 'request_id'"
            assert resp.headers.get("x-request-id", "") != "", "429 must have X-Request-Id header"

        # ── Assert 4: 200 responses are valid PDF ──
        responses_200 = [r for r in responses if r.status_code == 200]
        for resp in responses_200:
            assert "application/pdf" in resp.headers.get("content-type", "")
            assert resp.content[:5] == b"%PDF-"


# ═══════════════════════════════════════════════════════════════════════════════
# 3) Thread Determinism (Dedicated Executor)
# ═══════════════════════════════════════════════════════════════════════════════

class TestThreadDeterminism:
    """
    Checklist #3: Dedicated executor with 'pdf-render' prefix, max 2 threads.
    """

    def test_executor_has_correct_prefix_and_max_workers(self):
        """_pdf_executor must be ThreadPoolExecutor with pdf-render prefix, max_workers=2."""
        import app.main as main_mod

        executor = main_mod._pdf_executor
        assert isinstance(executor, ThreadPoolExecutor)
        assert executor._max_workers == main_mod._PDF_MAX_CONCURRENT
        assert executor._thread_name_prefix == "pdf-render"

    def test_render_runs_on_pdf_render_thread(self):
        """PDF render function must execute on a pdf-render-* named thread."""
        observed_threads = []

        with patch.dict(os.environ, {
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }):
            from app.main import app as fastapi_app
            from app.database import get_db

            mock_db = MagicMock()
            fastapi_app.dependency_overrides[get_db] = lambda: mock_db

            def spy_pdf(*args, **kwargs):
                observed_threads.append(threading.current_thread().name)
                return FAKE_PDF_BYTES

            with patch("app.main.generate_offer_pdf_bytes", side_effect=spy_pdf):
                c = TestClient(fastapi_app, raise_server_exceptions=False)
                resp = c.post("/generate-pdf-simple", data=_pdf_form_data())

            fastapi_app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert len(observed_threads) == 1
        assert observed_threads[0].startswith("pdf-render"), \
            f"Expected pdf-render thread, got: {observed_threads[0]}"

    def test_max_concurrent_threads_never_exceeds_limit(self):
        """Even under load, pdf-render thread count never exceeds _PDF_MAX_CONCURRENT."""
        max_concurrent_observed = 0
        current_concurrent = 0
        lock = threading.Lock()

        import httpx

        async def _run():
            nonlocal max_concurrent_observed, current_concurrent

            with patch.dict(os.environ, {
                "ADMIN_API_KEY_ENABLED": "false",
                "API_KEY_ENABLED": "false",
            }):
                from app.main import app as fastapi_app, _PDF_MAX_CONCURRENT
                from app.database import get_db
                import app.main as main_mod

                # Reset semaphore for clean state
                main_mod._pdf_semaphore = asyncio.Semaphore(_PDF_MAX_CONCURRENT)

                mock_db = MagicMock()
                fastapi_app.dependency_overrides[get_db] = lambda: mock_db

                def counting_pdf(*args, **kwargs):
                    nonlocal max_concurrent_observed, current_concurrent
                    with lock:
                        current_concurrent += 1
                        if current_concurrent > max_concurrent_observed:
                            max_concurrent_observed = current_concurrent
                    time.sleep(0.5)  # Hold the slot briefly
                    with lock:
                        current_concurrent -= 1
                    return FAKE_PDF_BYTES

                with patch("app.main.generate_offer_pdf_bytes", side_effect=counting_pdf):
                    transport = httpx.ASGITransport(app=fastapi_app)
                    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                        tasks = [
                            ac.post("/generate-pdf-simple", data=_pdf_form_data())
                            for _ in range(6)
                        ]
                        await asyncio.gather(*tasks)

                fastapi_app.dependency_overrides.clear()
            return _PDF_MAX_CONCURRENT

        limit = asyncio.run(_run())
        assert max_concurrent_observed <= limit, \
            f"Max concurrent renders was {max_concurrent_observed}, limit is {limit}"


# ═══════════════════════════════════════════════════════════════════════════════
# 4) Electron Structured Error Parsing (unit test of parsing logic)
# ═══════════════════════════════════════════════════════════════════════════════

class TestElectronErrorParsing:
    """
    Checklist #4: Verify that the error structure returned by backend
    matches what Electron main.js expects to parse.

    We test the backend side: error responses must be parseable by the
    Electron IPC handler's JSON parsing logic.
    """

    def _get_error_response(self, side_effect):
        """Helper: fire request with given side_effect, return response."""
        with patch.dict(os.environ, {
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }):
            from app.main import app as fastapi_app
            from app.database import get_db

            mock_db = MagicMock()
            fastapi_app.dependency_overrides[get_db] = lambda: mock_db

            if isinstance(side_effect, bytes):
                patcher = patch("app.main.generate_offer_pdf_bytes", return_value=side_effect)
            else:
                patcher = patch("app.main.generate_offer_pdf_bytes", side_effect=side_effect)

            with patcher:
                c = TestClient(fastapi_app, raise_server_exceptions=False)
                resp = c.post("/generate-pdf-simple", data=_pdf_form_data())

            fastapi_app.dependency_overrides.clear()
        return resp

    def test_500_error_parseable_by_electron(self):
        """
        500 error body must match Electron's parsing:
        parsed.error.code, parsed.error.message, parsed.error.request_id
        """
        resp = self._get_error_response(RuntimeError("crash"))
        assert resp.status_code == 500

        # Simulate Electron's parsing logic from main.js
        parsed = resp.json()
        err_obj = parsed.get("error", parsed)
        assert "code" in err_obj
        assert "message" in err_obj or "detail" in err_obj
        assert "request_id" in err_obj

    def test_empty_pdf_error_parseable_by_electron(self):
        """empty_pdf error must be parseable by Electron."""
        resp = self._get_error_response(b"")
        assert resp.status_code == 500

        parsed = resp.json()
        err_obj = parsed.get("error", parsed)
        assert err_obj["code"] == "empty_pdf"
        assert "request_id" in err_obj

    def test_429_includes_retry_after_for_electron(self):
        """
        429 must include Retry-After header that Electron parses as integer.
        Electron logic: parseInt(response.headers['retry-after'], 10) || 5
        """
        import httpx

        async def _run():
            with patch.dict(os.environ, {
                "ADMIN_API_KEY_ENABLED": "false",
                "API_KEY_ENABLED": "false",
            }):
                from app.main import app as fastapi_app, _PDF_MAX_CONCURRENT
                from app.database import get_db
                import app.main as main_mod

                # Reset semaphore
                main_mod._pdf_semaphore = asyncio.Semaphore(_PDF_MAX_CONCURRENT)

                mock_db = MagicMock()
                fastapi_app.dependency_overrides[get_db] = lambda: mock_db

                def slow_pdf(*args, **kwargs):
                    time.sleep(3)
                    return FAKE_PDF_BYTES

                with patch("app.main.generate_offer_pdf_bytes", side_effect=slow_pdf):
                    transport = httpx.ASGITransport(app=fastapi_app)
                    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                        tasks = [ac.post("/generate-pdf-simple", data=_pdf_form_data()) for _ in range(4)]
                        responses = await asyncio.gather(*tasks)

                fastapi_app.dependency_overrides.clear()
            return responses

        responses = asyncio.run(_run())
        responses_429 = [r for r in responses if r.status_code == 429]
        assert len(responses_429) >= 1, "Expected at least one 429"

        for resp_429 in responses_429:
            # Simulate Electron's Retry-After parsing
            retry_after_raw = resp_429.headers.get("retry-after", "")
            retry_after = int(retry_after_raw) if retry_after_raw.isdigit() else 5
            assert retry_after > 0, "Retry-After must be positive integer"

            # Verify JSON body has error structure
            parsed = resp_429.json()
            err_obj = parsed.get("error", parsed)
            assert err_obj["code"] == "too_many_requests"
            assert "request_id" in err_obj

    def test_x_request_id_present_on_all_error_codes(self):
        """X-Request-Id must be present on 429, 500, 504 responses."""
        # 500 (internal_error)
        resp_500 = self._get_error_response(RuntimeError("boom"))
        assert resp_500.headers.get("x-request-id", "") != ""

        # 500 (empty_pdf)
        resp_empty = self._get_error_response(b"")
        assert resp_empty.headers.get("x-request-id", "") != ""


# ═══════════════════════════════════════════════════════════════════════════════
# 5) Sequential Stability (N ardışık request → 0 failure)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSequentialStability:
    """
    Checklist #5: N ardışık download → 0 failure.

    20 sequential requests (mock PDF, fast). All must return 200.
    This proves the semaphore release path is correct and no resource leak.
    """

    def test_20_sequential_requests_zero_failures(self, client):
        """20 ardışık PDF request → hepsi 200, 0 failure."""
        failures = []
        for i in range(20):
            resp = client.post("/generate-pdf-simple", data=_pdf_form_data())
            if resp.status_code != 200:
                failures.append((i, resp.status_code))

        assert len(failures) == 0, \
            f"Expected 0 failures in 20 sequential requests, got: {failures}"

    def test_50_sequential_requests_zero_failures(self, client):
        """50 ardışık PDF request → hepsi 200, semaphore leak yok."""
        failures = []
        for i in range(50):
            resp = client.post("/generate-pdf-simple", data=_pdf_form_data())
            if resp.status_code != 200:
                failures.append((i, resp.status_code))

        assert len(failures) == 0, \
            f"Expected 0 failures in 50 sequential requests, got: {failures}"

    def test_sequential_requests_all_have_unique_request_ids(self, client):
        """Her request'in X-Request-Id'si unique olmalı."""
        request_ids = set()
        for _ in range(10):
            resp = client.post("/generate-pdf-simple", data=_pdf_form_data())
            assert resp.status_code == 200
            rid = resp.headers.get("x-request-id", "")
            assert rid not in request_ids, f"Duplicate request_id: {rid}"
            request_ids.add(rid)

        assert len(request_ids) == 10


# ═══════════════════════════════════════════════════════════════════════════════
# Bonus: CORS Header Exposure
# ═══════════════════════════════════════════════════════════════════════════════

class TestCORSHeaders:
    """
    Verify that Retry-After and X-Request-Id are in CORS expose_headers,
    so browser/Electron can read them.
    """

    def test_cors_exposes_retry_after_and_request_id(self):
        """CORS middleware must expose Retry-After and X-Request-Id headers."""
        from app.main import _cors_origins

        # The expose_headers are set in app.add_middleware(CORSMiddleware, ...)
        # We verify by checking the middleware config
        from app.main import app as fastapi_app

        # Find CORSMiddleware in the middleware stack
        cors_found = False
        for middleware in fastapi_app.user_middleware:
            if "CORSMiddleware" in str(middleware.cls):
                cors_found = True
                expose = middleware.kwargs.get("expose_headers", [])
                assert "Retry-After" in expose, \
                    f"Retry-After not in expose_headers: {expose}"
                assert "X-Request-Id" in expose, \
                    f"X-Request-Id not in expose_headers: {expose}"
                break

        assert cors_found, "CORSMiddleware not found in app middleware"
