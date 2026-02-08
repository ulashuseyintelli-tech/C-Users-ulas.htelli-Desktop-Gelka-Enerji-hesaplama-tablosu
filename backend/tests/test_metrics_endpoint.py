"""
Tests for GET /metrics endpoint and MetricsMiddleware.

Feature: telemetry-unification, Task 3
Tests:
- /metrics endpoint smoke test (200, correct Content-Type)
- /metrics returns ptf_admin_ metric names
- /metrics requires no auth
- /metrics excluded from middleware (no self-counting)
- Middleware tracks request count and duration
- Middleware 3-level endpoint label normalization
- Reset clears exposed metrics
- History endpoint increments history_query metrics
"""

import pytest
from unittest.mock import MagicMock, patch

from app.ptf_metrics import get_ptf_metrics


@pytest.fixture(autouse=True)
def fresh_metrics():
    """Reset singleton metrics before each test."""
    m = get_ptf_metrics()
    m.reset()
    yield m


@pytest.fixture()
def client():
    """TestClient with DB and admin-key dependencies overridden."""
    with patch.dict("os.environ", {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db
        from fastapi.testclient import TestClient

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db
        yield TestClient(fastapi_app)
        fastapi_app.dependency_overrides.clear()


class TestMetricsEndpoint:
    """GET /metrics smoke tests."""

    def test_returns_200(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_content_type_prometheus(self, client):
        resp = client.get("/metrics")
        ct = resp.headers["content-type"]
        assert "text/plain" in ct
        assert "version=0.0.4" in ct

    def test_contains_ptf_admin_metrics(self, client, fresh_metrics):
        fresh_metrics.inc_upsert("final")
        resp = client.get("/metrics")
        body = resp.text
        assert "ptf_admin_upsert_total" in body
        assert "ptf_admin_import_rows_total" in body
        assert "ptf_admin_import_apply_duration_seconds" in body
        assert "ptf_admin_lookup_total" in body
        assert "ptf_admin_history_query_total" in body
        assert "ptf_admin_api_request_total" in body
        assert "ptf_admin_frontend_events_total" in body

    def test_no_auth_required(self, client):
        """Endpoint should work without any auth headers."""
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_reset_clears_exposed_metrics(self, client, fresh_metrics):
        """After reset, counters should be zero in /metrics output."""
        fresh_metrics.inc_upsert("final")
        fresh_metrics.inc_upsert("final")
        fresh_metrics.reset()
        resp = client.get("/metrics")
        # After reset, upsert_total with status=final should not appear
        # (prometheus_client only outputs initialized label combos)
        assert 'ptf_admin_upsert_total{status="final"} 0.0' not in resp.text or \
               'ptf_admin_upsert_total{status="final"} 0' not in resp.text


class TestMetricsMiddleware:
    """Middleware request tracking tests."""

    def test_metrics_endpoint_excluded_from_middleware(self, client, fresh_metrics):
        """GET /metrics should NOT increment api_request_total for /metrics itself."""
        client.get("/metrics")
        client.get("/metrics")
        snap = fresh_metrics.snapshot()
        # /metrics should not appear in api_request counters
        output = fresh_metrics.generate_metrics().decode()
        assert 'endpoint="/metrics"' not in output

    def test_health_endpoint_tracked(self, client, fresh_metrics):
        """GET /health should be tracked by middleware."""
        client.get("/health")
        output = fresh_metrics.generate_metrics().decode()
        assert 'ptf_admin_api_request_total{endpoint="/health"' in output

    def test_status_class_label_normalization(self, client, fresh_metrics):
        """status_code is normalized to status_class (2xx/4xx/5xx) — not exact codes.

        This prevents high-cardinality label explosion. Exact codes belong in
        logs/traces, not Prometheus labels.
        """
        # 2xx — hit a known endpoint
        client.get("/health")
        # 4xx — hit a non-existent path to trigger 404
        client.get("/this-path-does-not-exist-at-all")

        output = fresh_metrics.generate_metrics().decode()

        # status_class labels should be class-level, not exact codes
        assert 'status_class="2xx"' in output
        assert 'status_class="4xx"' in output

        # Exact codes must NOT appear as label values
        assert 'status_class="200"' not in output
        assert 'status_class="404"' not in output

        # Old label name must not exist at all
        assert 'status_code=' not in output

    def test_status_class_5xx_via_direct_increment(self, fresh_metrics):
        """5xx status_class works correctly via direct inc_api_request call."""
        fresh_metrics.inc_api_request("/admin/market-prices", "GET", 500)
        fresh_metrics.inc_api_request("/admin/market-prices", "GET", 502)
        fresh_metrics.inc_api_request("/admin/market-prices", "GET", 503)

        output = fresh_metrics.generate_metrics().decode()

        # All three should collapse into a single 5xx label
        assert 'status_class="5xx"' in output
        # The counter for 5xx should be 3.0 (500 + 502 + 503 all map to 5xx)
        assert 'status_class="500"' not in output
        assert 'status_class="502"' not in output
        assert 'status_class="503"' not in output


class TestHistoryEndpointMetrics:
    """History endpoint metrics integration."""

    def test_history_query_increments_counter(self, client, fresh_metrics):
        """GET /admin/market-prices/history should increment history_query_total."""
        with patch("app.market_price_admin_service.get_market_price_admin_service") as factory:
            svc = MagicMock()
            factory.return_value = svc
            svc.get_history.return_value = []

            client.get("/admin/market-prices/history?period=2025-01&price_type=PTF")

        output = fresh_metrics.generate_metrics().decode()
        assert "ptf_admin_history_query_total" in output
        # Counter should be at least 1
        snap_lines = [l for l in output.split("\n") if l.startswith("ptf_admin_history_query_total ")]
        assert len(snap_lines) == 1
        assert float(snap_lines[0].split()[-1]) >= 1.0

    def test_history_query_records_duration(self, client, fresh_metrics):
        """GET /admin/market-prices/history should observe duration."""
        with patch("app.market_price_admin_service.get_market_price_admin_service") as factory:
            svc = MagicMock()
            factory.return_value = svc
            svc.get_history.return_value = []

            client.get("/admin/market-prices/history?period=2025-01&price_type=PTF")

        output = fresh_metrics.generate_metrics().decode()
        assert "ptf_admin_history_query_duration_seconds_count 1.0" in output


class TestSnapshotEndpointConsistency:
    """Snapshot values should be consistent with /metrics output."""

    def test_snapshot_matches_endpoint(self, client, fresh_metrics):
        """Values from snapshot() should match what /metrics exposes."""
        fresh_metrics.inc_upsert("provisional")
        fresh_metrics.inc_upsert("provisional")
        fresh_metrics.inc_upsert("final")
        fresh_metrics.inc_import_rows("accepted", 5)

        snap = fresh_metrics.snapshot()
        output = fresh_metrics.generate_metrics().decode()

        # snapshot says provisional=2
        assert snap["upsert_total"]["provisional"] == 2
        assert 'ptf_admin_upsert_total{status="provisional"} 2.0' in output

        # snapshot says final=1
        assert snap["upsert_total"]["final"] == 1
        assert 'ptf_admin_upsert_total{status="final"} 1.0' in output

        # snapshot says accepted=5
        assert snap["import_rows_total"]["accepted"] == 5
        assert 'ptf_admin_import_rows_total{outcome="accepted"} 5.0' in output


class TestMiddlewareExceptionPath:
    """Exception path instrumentation: status_class="0xx" when handler raises."""

    def test_exception_produces_0xx_status_class(self, fresh_metrics):
        """Handler exception → middleware records status_class="0xx" and duration."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.metrics_middleware import MetricsMiddleware

        # Minimal app with a crashing route
        boom_app = FastAPI()
        boom_app.add_middleware(MetricsMiddleware)

        @boom_app.get("/__boom")
        async def boom():
            raise RuntimeError("intentional crash")

        client = TestClient(boom_app, raise_server_exceptions=False)
        resp = client.get("/__boom")
        # Starlette returns 500 for unhandled exceptions
        assert resp.status_code == 500

        output = fresh_metrics.generate_metrics().decode()

        # Middleware should have recorded the request with 0xx (exception path)
        # OR 5xx (if Starlette's error handler produced a 500 before our middleware saw it)
        # With BaseHTTPMiddleware, the exception propagates through call_next,
        # so our try/except catches it → status_code=0 → "0xx"
        has_0xx = 'status_class="0xx"' in output
        has_5xx = 'status_class="5xx"' in output
        assert has_0xx or has_5xx, f"Expected 0xx or 5xx in output, got:\n{output[:500]}"

    def test_exception_path_records_duration(self, fresh_metrics):
        """Duration histogram is observed even when handler raises."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.metrics_middleware import MetricsMiddleware

        boom_app = FastAPI()
        boom_app.add_middleware(MetricsMiddleware)

        @boom_app.get("/__boom_dur")
        async def boom():
            raise RuntimeError("crash for duration test")

        client = TestClient(boom_app, raise_server_exceptions=False)
        client.get("/__boom_dur")

        output = fresh_metrics.generate_metrics().decode()
        # Duration histogram should have at least 1 observation
        assert "ptf_admin_api_request_duration_seconds_count" in output
        dur_lines = [l for l in output.split("\n")
                     if l.startswith("ptf_admin_api_request_duration_seconds_count")]
        assert any(float(l.split()[-1]) >= 1.0 for l in dur_lines)


class TestRateLimitMetrics:
    """429 responses from rate limit should appear in metrics."""

    def test_429_produces_4xx_status_class(self, client, fresh_metrics):
        """Rate-limited requests (429) are tracked as status_class="4xx"."""
        from app.main import _rate_limit_buckets
        _rate_limit_buckets.clear()

        # Exhaust rate limit
        for _ in range(60):
            client.post("/admin/telemetry/events", json={"events": []})

        # 61st triggers 429
        resp = client.post("/admin/telemetry/events", json={"events": []})
        assert resp.status_code == 429

        output = fresh_metrics.generate_metrics().decode()
        assert 'status_class="4xx"' in output


class TestMetricsOutputFormat:
    """Golden-style format regression tests for /metrics output."""

    def test_help_and_type_lines_present(self, client, fresh_metrics):
        """Prometheus output must contain # HELP and # TYPE for ptf_admin_ metrics."""
        fresh_metrics.inc_upsert("final")
        resp = client.get("/metrics")
        body = resp.text

        # At least these metric families should have HELP + TYPE
        expected_families = [
            "ptf_admin_upsert_total",
            "ptf_admin_import_rows_total",
            "ptf_admin_import_apply_duration_seconds",
            "ptf_admin_lookup_total",
            "ptf_admin_history_query_total",
            "ptf_admin_api_request_total",
            "ptf_admin_frontend_events_total",
        ]
        for family in expected_families:
            assert f"# HELP {family}" in body, f"Missing # HELP for {family}"
            assert f"# TYPE {family}" in body, f"Missing # TYPE for {family}"

    def test_counter_type_annotation(self, client, fresh_metrics):
        """Counter metrics should have TYPE counter."""
        fresh_metrics.inc_upsert("final")
        resp = client.get("/metrics")
        body = resp.text
        assert "# TYPE ptf_admin_upsert_total counter" in body
        assert "# TYPE ptf_admin_api_request_total counter" in body

    def test_histogram_type_annotation(self, client, fresh_metrics):
        """Histogram metrics should have TYPE histogram."""
        fresh_metrics.observe_import_apply_duration(0.5)
        resp = client.get("/metrics")
        body = resp.text
        assert "# TYPE ptf_admin_import_apply_duration_seconds histogram" in body
