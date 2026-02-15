"""
Wiring Integration Tests — Feature: dependency-wrappers, Task 10/11.

Blok 4 DoD entegrasyon testleri:
1. CB OPEN simülasyonu → endpoint 503
2. Read timeout → retry sayısı + metrik artışı
3. Write timeout → retry yok

+ Bypass koruma: doğrudan client kullanımı denylist testi
"""

import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from app.main import app
from app.guards.circuit_breaker import Dependency


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset main.py singletons between tests."""
    import app.main as main_mod
    main_mod._cb_registry = None
    yield
    main_mod._cb_registry = None


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test 1: CB OPEN → endpoint 503
# ═══════════════════════════════════════════════════════════════════════════════


class TestCBOpenReturns503:
    """DoD 11.1: CB OPEN simülasyonu → endpoint 503 CIRCUIT_OPEN."""

    def test_list_market_prices_cb_open_returns_503(self, client):
        """CB OPEN for db_primary → GET /admin/market-prices → 503.

        Not: Middleware CB pre-check devreye girer ve 503 döner.
        Response body middleware formatında olabilir (error/reason) veya
        handler formatında (detail/error_code).
        """
        with patch("app.main._get_cb_registry") as mock_registry_fn:
            mock_registry = MagicMock()
            mock_cb = MagicMock()
            mock_cb.allow_request.return_value = False  # CB OPEN
            mock_registry.get.return_value = mock_cb
            mock_registry_fn.return_value = mock_registry

            resp = client.get("/admin/market-prices")
            assert resp.status_code == 503
            body = resp.json()
            # Middleware veya handler formatı — ikisi de CIRCUIT_OPEN döner
            is_middleware = body.get("reason") == "CIRCUIT_OPEN"
            is_handler = (body.get("detail", {}) or {}).get("error_code") == "CIRCUIT_OPEN"
            assert is_middleware or is_handler, f"Expected CIRCUIT_OPEN in response, got: {body}"

    def test_upsert_market_price_cb_open_returns_503(self, client):
        """CB OPEN for db_primary → POST /admin/market-prices → 503."""
        with patch("app.main._get_cb_registry") as mock_registry_fn:
            mock_registry = MagicMock()
            mock_cb = MagicMock()
            mock_cb.allow_request.return_value = False
            mock_registry.get.return_value = mock_cb
            mock_registry_fn.return_value = mock_registry

            resp = client.post(
                "/admin/market-prices",
                json={"period": "2025-01", "value": 100.0},
            )
            assert resp.status_code == 503
            body = resp.json()
            is_middleware = body.get("reason") == "CIRCUIT_OPEN"
            is_handler = (body.get("detail", {}) or {}).get("error_code") == "CIRCUIT_OPEN"
            assert is_middleware or is_handler, f"Expected CIRCUIT_OPEN in response, got: {body}"

    def test_lookup_cb_open_returns_503(self, client):
        """CB OPEN for db_replica → GET /api/market-prices/PTF/2025-01 → 503."""
        with patch("app.main._get_cb_registry") as mock_registry_fn:
            mock_registry = MagicMock()
            mock_cb = MagicMock()
            mock_cb.allow_request.return_value = False
            mock_registry.get.return_value = mock_cb
            mock_registry_fn.return_value = mock_registry

            resp = client.get("/api/market-prices/PTF/2025-01")
            assert resp.status_code == 503
            body = resp.json()
            is_middleware = body.get("reason") == "CIRCUIT_OPEN"
            is_handler = (body.get("detail", {}) or {}).get("error_code") == "CIRCUIT_OPEN"
            assert is_middleware or is_handler, f"Expected CIRCUIT_OPEN in response, got: {body}"


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test 2: Read timeout → retry + metrik
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadTimeoutRetry:
    """DoD 11.2: Read timeout → retry sayısı + metrik artışı."""

    def test_list_market_prices_timeout_retries(self, client):
        """Read path timeout → retry happens, metric incremented."""
        from app.ptf_metrics import PTFMetrics

        fresh_metrics = PTFMetrics(registry=CollectorRegistry())

        with (
            patch("app.main._get_cb_registry") as mock_registry_fn,
            patch("app.ptf_metrics.get_ptf_metrics", return_value=fresh_metrics),
            patch("app.guards.dependency_wrapper.GuardConfig") as _,
        ):
            mock_registry = MagicMock()
            mock_cb = MagicMock()
            mock_cb.allow_request.return_value = True
            mock_registry.get.return_value = mock_cb

            mock_registry_fn.return_value = mock_registry

            # Patch the wrapper to simulate timeout with retry
            call_count = 0

            async def mock_to_thread(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                raise asyncio.TimeoutError("simulated timeout")

            with patch("asyncio.to_thread", side_effect=mock_to_thread):
                # Use low timeout + 1 retry config
                from app.guard_config import GuardConfig
                test_config = GuardConfig(
                    wrapper_timeout_seconds_default=0.01,
                    wrapper_retry_max_attempts_default=1,
                    wrapper_retry_backoff_base_ms=1,
                    wrapper_retry_backoff_cap_ms=10,
                    wrapper_retry_jitter_pct=0.0,
                )
                with patch("app.guard_config.get_guard_config", return_value=test_config):
                    resp = client.get("/admin/market-prices")

            # Should get 504 (timeout)
            assert resp.status_code == 504

            # Retry metric should be incremented
            retry_val = fresh_metrics._dependency_retry_total.labels(
                dependency="db_primary"
            )._value.get()
            assert retry_val >= 1, f"Expected retry metric >= 1, got {retry_val}"


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Test 3: Write timeout → retry yok
# ═══════════════════════════════════════════════════════════════════════════════


class TestWriteTimeoutNoRetry:
    """DoD 11.3: Write timeout → retry yok."""

    def test_upsert_timeout_no_retry(self, client):
        """Write path timeout → NO retry (DW-1)."""
        from app.ptf_metrics import PTFMetrics

        fresh_metrics = PTFMetrics(registry=CollectorRegistry())
        call_count = 0

        async def mock_to_thread(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise asyncio.TimeoutError("simulated timeout")

        with (
            patch("app.main._get_cb_registry") as mock_registry_fn,
            patch("app.ptf_metrics.get_ptf_metrics", return_value=fresh_metrics),
        ):
            mock_registry = MagicMock()
            mock_cb = MagicMock()
            mock_cb.allow_request.return_value = True
            mock_registry.get.return_value = mock_cb
            mock_registry_fn.return_value = mock_registry

            from app.guard_config import GuardConfig
            test_config = GuardConfig(
                wrapper_timeout_seconds_default=0.01,
                wrapper_retry_max_attempts_default=2,  # 2 retries configured
                wrapper_retry_on_write=False,  # DW-1: write retry OFF
                wrapper_retry_backoff_base_ms=1,
                wrapper_retry_backoff_cap_ms=10,
                wrapper_retry_jitter_pct=0.0,
            )
            with (
                patch("app.guard_config.get_guard_config", return_value=test_config),
                patch("asyncio.to_thread", side_effect=mock_to_thread),
            ):
                resp = client.post(
                    "/admin/market-prices",
                    json={"period": "2025-01", "value": 100.0},
                )

        # Should get 504 (timeout)
        assert resp.status_code == 504

        # Retry metric should be 0 (write path, no retry)
        retry_val = fresh_metrics._dependency_retry_total.labels(
            dependency="db_primary"
        )._value.get()
        assert retry_val == 0, f"Expected 0 retries on write path, got {retry_val}"


# ═══════════════════════════════════════════════════════════════════════════════
# Bypass Koruma: Doğrudan client kullanımı denylist testi
# ═══════════════════════════════════════════════════════════════════════════════


class TestBypassProtection:
    """
    DoD 10.3: Bypass koruma — router introspection ile kritik yol
    endpoint'lerini otomatik keşfet ve wrapper kullanımını doğrula.

    Yöntem: FastAPI route registry'den kritik path prefix'lerine uyan
    endpoint'leri otomatik keşfeder. Statik liste yerine router introspection
    kullanarak "liste disiplini" riskini ortadan kaldırır.
    """

    # Kritik yol prefix'leri — bu prefix'lere uyan tüm endpoint'ler
    # wrapper kullanmalı (otomatik keşif)
    CRITICAL_PATH_PREFIXES = ("/admin/market-prices", "/api/market-prices/")

    # Bilinen muafiyetler — legacy/deprecated endpoint'ler veya
    # wrapper gerektirmeyen yollar.
    # ⚠ Her muafiyet dokümante edilmeli. Yeni ekleme yapılırsa "neden?" yorumu zorunlu.
    EXEMPT_PATHS = {
        # Neden: Deprecated redirect endpoint, kendi DB çağrısı yok,
        # sadece yeni endpoint'e yönlendirir. Kaldırılınca muafiyet de silinecek.
        "/admin/market-prices/legacy",
        # Neden: Salt-okunur meta endpoint, DB'ye erişmez,
        # sadece in-memory alias sayaçlarını döner.
        "/admin/market-prices/deprecation-stats",
        # Neden: Deprecated form-based endpoint, gelen veriyi normalize edip
        # JSON-based upsert_market_price'a delege eder. Wrapper koruması
        # delege edilen endpoint'te zaten aktif.
        "/admin/market-prices/form",
    }

    @staticmethod
    def _get_critical_endpoints_from_router():
        """
        FastAPI route registry'den kritik yol endpoint'lerini keşfet.

        Returns:
            list of (path, func_name, func) tuples
        """
        import app.main as main_mod
        results = []
        for route in main_mod.app.routes:
            path = getattr(route, "path", None)
            if path is None:
                continue
            # Kritik prefix kontrolü
            is_critical = any(
                path.startswith(prefix)
                for prefix in TestBypassProtection.CRITICAL_PATH_PREFIXES
            )
            if not is_critical:
                continue
            # Muafiyet kontrolü
            if path in TestBypassProtection.EXEMPT_PATHS:
                continue
            endpoint_fn = getattr(route, "endpoint", None)
            if endpoint_fn is None:
                continue
            func_name = endpoint_fn.__name__
            results.append((path, func_name, endpoint_fn))
        return results

    def test_router_discovers_critical_endpoints(self):
        """Router introspection en az 1 kritik endpoint keşfetmeli."""
        endpoints = self._get_critical_endpoints_from_router()
        assert len(endpoints) >= 1, (
            "Router introspection found 0 critical endpoints. "
            f"Prefixes: {self.CRITICAL_PATH_PREFIXES}"
        )

    def test_all_critical_endpoints_use_wrapper(self):
        """Router'dan keşfedilen her kritik endpoint _get_wrapper() kullanmalı."""
        import inspect
        endpoints = self._get_critical_endpoints_from_router()
        for path, name, func in endpoints:
            source = inspect.getsource(func)
            assert "_get_wrapper(" in source, (
                f"Endpoint '{name}' ({path}) does not use _get_wrapper(). "
                f"All critical path dependency calls must go through wrapper."
            )

    def test_all_critical_endpoints_use_error_mapping(self):
        """Router'dan keşfedilen her kritik endpoint _map_wrapper_error_to_http() kullanmalı."""
        import inspect
        endpoints = self._get_critical_endpoints_from_router()
        for path, name, func in endpoints:
            source = inspect.getsource(func)
            assert "_map_wrapper_error_to_http(" in source, (
                f"Endpoint '{name}' ({path}) does not use _map_wrapper_error_to_http(). "
                f"Wrapper errors must be mapped to HTTP responses consistently."
            )

    def test_no_direct_db_calls_in_critical_endpoints(self):
        """Kritik endpoint'lerde doğrudan db.query/commit/execute çağrısı olmamalı."""
        import inspect
        # Closure pattern kullanan endpoint'ler (db, closure içinde wrapper'a geçirilir)
        CLOSURE_ENDPOINTS = {"unlock_market_price"}

        endpoints = self._get_critical_endpoints_from_router()
        for path, name, func in endpoints:
            if name in CLOSURE_ENDPOINTS:
                continue
            source = inspect.getsource(func)
            for pattern in [r"db\.query\(", r"db\.execute\(", r"db\.commit\("]:
                matches = re.findall(pattern, source)
                assert not matches, (
                    f"Endpoint '{name}' ({path}) has direct DB call '{matches[0]}'. "
                    f"DB calls must go through wrapper."
                )

    def test_error_mapping_table_consistency(self):
        """_map_wrapper_error_to_http returns correct status codes."""
        import asyncio as _asyncio
        from app.main import _map_wrapper_error_to_http
        from app.guards.dependency_wrapper import CircuitOpenError

        # CircuitOpenError → 503
        exc = _map_wrapper_error_to_http(CircuitOpenError("db_primary"))
        assert exc.status_code == 503
        assert exc.detail["error_code"] == "CIRCUIT_OPEN"

        # TimeoutError → 504
        exc = _map_wrapper_error_to_http(_asyncio.TimeoutError("timeout"))
        assert exc.status_code == 504
        assert exc.detail["error_code"] == "DEPENDENCY_TIMEOUT"

        # ConnectionError → 502
        exc = _map_wrapper_error_to_http(ConnectionError("refused"))
        assert exc.status_code == 502
        assert exc.detail["error_code"] == "DEPENDENCY_UNAVAILABLE"

        # OSError → 502
        exc = _map_wrapper_error_to_http(OSError("socket error"))
        assert exc.status_code == 502
        assert exc.detail["error_code"] == "DEPENDENCY_UNAVAILABLE"

        # Generic exception → 502 DEPENDENCY_ERROR
        exc = _map_wrapper_error_to_http(RuntimeError("unknown"))
        assert exc.status_code == 502
        assert exc.detail["error_code"] == "DEPENDENCY_ERROR"
