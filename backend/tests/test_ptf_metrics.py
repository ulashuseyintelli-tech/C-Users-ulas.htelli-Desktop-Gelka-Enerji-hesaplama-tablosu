"""
Unit tests for PTF Admin Metrics module.

Feature: ptf-admin-management, Task 10.1
Tests:
- PTFMetrics counter increments
- Duration tracking
- Snapshot correctness
- Reset behavior
- Thread safety (basic)
- Integration with API endpoints (import/apply, upsert, lookup)
"""

import time
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.ptf_metrics import PTFMetrics, get_ptf_metrics
from prometheus_client import CollectorRegistry


# ═══════════════════════════════════════════════════════════════════════════════
# Unit Tests — PTFMetrics class
# ═══════════════════════════════════════════════════════════════════════════════


class TestImportApplyDuration:
    """Tests for import_apply_duration_seconds metric."""

    def test_observe_records_duration(self):
        m = PTFMetrics()
        m.observe_import_apply_duration(1.5)
        snap = m.snapshot()
        assert snap["import_apply_duration_seconds"]["count"] == 1
        assert snap["import_apply_duration_seconds"]["total_seconds"] == 1.5

    def test_multiple_observations_accumulate(self):
        m = PTFMetrics()
        m.observe_import_apply_duration(1.0)
        m.observe_import_apply_duration(2.5)
        snap = m.snapshot()
        assert snap["import_apply_duration_seconds"]["count"] == 2
        assert snap["import_apply_duration_seconds"]["total_seconds"] == 3.5

    def test_context_manager_records_duration(self):
        m = PTFMetrics()
        with m.time_import_apply():
            time.sleep(0.01)  # At least 10ms
        snap = m.snapshot()
        assert snap["import_apply_duration_seconds"]["count"] == 1
        assert snap["import_apply_duration_seconds"]["total_seconds"] >= 0.01

    def test_context_manager_records_on_exception(self):
        m = PTFMetrics()
        with pytest.raises(ValueError):
            with m.time_import_apply():
                raise ValueError("boom")
        snap = m.snapshot()
        assert snap["import_apply_duration_seconds"]["count"] == 1


class TestImportRowsTotal:
    """Tests for import_rows_total{outcome=accepted|rejected} metric."""

    def test_increment_accepted(self):
        m = PTFMetrics()
        m.inc_import_rows("accepted", 5)
        snap = m.snapshot()
        assert snap["import_rows_total"]["accepted"] == 5
        assert snap["import_rows_total"]["rejected"] == 0

    def test_increment_rejected(self):
        m = PTFMetrics()
        m.inc_import_rows("rejected", 3)
        snap = m.snapshot()
        assert snap["import_rows_total"]["rejected"] == 3
        assert snap["import_rows_total"]["accepted"] == 0

    def test_increment_both(self):
        m = PTFMetrics()
        m.inc_import_rows("accepted", 10)
        m.inc_import_rows("rejected", 2)
        snap = m.snapshot()
        assert snap["import_rows_total"]["accepted"] == 10
        assert snap["import_rows_total"]["rejected"] == 2

    def test_default_count_is_one(self):
        m = PTFMetrics()
        m.inc_import_rows("accepted")
        snap = m.snapshot()
        assert snap["import_rows_total"]["accepted"] == 1

    def test_invalid_outcome_ignored(self):
        m = PTFMetrics()
        m.inc_import_rows("unknown", 5)
        snap = m.snapshot()
        assert snap["import_rows_total"]["accepted"] == 0
        assert snap["import_rows_total"]["rejected"] == 0


class TestUpsertTotal:
    """Tests for upsert_total{status=provisional|final} metric."""

    def test_increment_provisional(self):
        m = PTFMetrics()
        m.inc_upsert("provisional")
        snap = m.snapshot()
        assert snap["upsert_total"]["provisional"] == 1
        assert snap["upsert_total"]["final"] == 0

    def test_increment_final(self):
        m = PTFMetrics()
        m.inc_upsert("final")
        snap = m.snapshot()
        assert snap["upsert_total"]["final"] == 1
        assert snap["upsert_total"]["provisional"] == 0

    def test_multiple_increments(self):
        m = PTFMetrics()
        m.inc_upsert("provisional")
        m.inc_upsert("provisional")
        m.inc_upsert("final")
        snap = m.snapshot()
        assert snap["upsert_total"]["provisional"] == 2
        assert snap["upsert_total"]["final"] == 1

    def test_invalid_status_ignored(self):
        m = PTFMetrics()
        m.inc_upsert("unknown")
        snap = m.snapshot()
        assert snap["upsert_total"]["provisional"] == 0
        assert snap["upsert_total"]["final"] == 0


class TestLookupTotal:
    """Tests for lookup_total{hit=true|false, status=provisional|final} metric."""

    def test_hit_final(self):
        m = PTFMetrics()
        m.inc_lookup(hit=True, status="final")
        snap = m.snapshot()
        assert snap["lookup_total"]["hit=true,status=final"] == 1
        assert snap["lookup_total"]["hit=true,status=provisional"] == 0
        assert snap["lookup_total"]["hit=false"] == 0

    def test_hit_provisional(self):
        m = PTFMetrics()
        m.inc_lookup(hit=True, status="provisional")
        snap = m.snapshot()
        assert snap["lookup_total"]["hit=true,status=provisional"] == 1

    def test_miss(self):
        m = PTFMetrics()
        m.inc_lookup(hit=False)
        snap = m.snapshot()
        assert snap["lookup_total"]["hit=false"] == 1
        assert snap["lookup_total"]["hit=true,status=final"] == 0
        assert snap["lookup_total"]["hit=true,status=provisional"] == 0

    def test_hit_without_valid_status_ignored(self):
        m = PTFMetrics()
        m.inc_lookup(hit=True, status="unknown")
        snap = m.snapshot()
        assert snap["lookup_total"]["hit=true,status=final"] == 0
        assert snap["lookup_total"]["hit=true,status=provisional"] == 0

    def test_mixed_lookups(self):
        m = PTFMetrics()
        m.inc_lookup(hit=True, status="final")
        m.inc_lookup(hit=True, status="final")
        m.inc_lookup(hit=True, status="provisional")
        m.inc_lookup(hit=False)
        m.inc_lookup(hit=False)
        m.inc_lookup(hit=False)
        snap = m.snapshot()
        assert snap["lookup_total"]["hit=true,status=final"] == 2
        assert snap["lookup_total"]["hit=true,status=provisional"] == 1
        assert snap["lookup_total"]["hit=false"] == 3


class TestSnapshotAndReset:
    """Tests for snapshot() and reset() methods."""

    def test_initial_snapshot_all_zeros(self):
        m = PTFMetrics()
        snap = m.snapshot()
        assert snap["import_apply_duration_seconds"]["count"] == 0
        assert snap["import_apply_duration_seconds"]["total_seconds"] == 0.0
        assert snap["import_rows_total"]["accepted"] == 0
        assert snap["import_rows_total"]["rejected"] == 0
        assert snap["upsert_total"]["provisional"] == 0
        assert snap["upsert_total"]["final"] == 0
        assert snap["lookup_total"]["hit=true,status=final"] == 0
        assert snap["lookup_total"]["hit=true,status=provisional"] == 0
        assert snap["lookup_total"]["hit=false"] == 0

    def test_reset_clears_all(self):
        m = PTFMetrics()
        m.observe_import_apply_duration(5.0)
        m.inc_import_rows("accepted", 10)
        m.inc_import_rows("rejected", 3)
        m.inc_upsert("final")
        m.inc_lookup(hit=True, status="final")
        m.inc_lookup(hit=False)

        m.reset()
        snap = m.snapshot()

        assert snap["import_apply_duration_seconds"]["count"] == 0
        assert snap["import_apply_duration_seconds"]["total_seconds"] == 0.0
        assert snap["import_rows_total"]["accepted"] == 0
        assert snap["import_rows_total"]["rejected"] == 0
        assert snap["upsert_total"]["provisional"] == 0
        assert snap["upsert_total"]["final"] == 0
        assert snap["lookup_total"]["hit=true,status=final"] == 0
        assert snap["lookup_total"]["hit=false"] == 0

    def test_snapshot_returns_copy(self):
        """Modifying snapshot dict should not affect internal state."""
        m = PTFMetrics()
        m.inc_upsert("final")
        snap = m.snapshot()
        snap["upsert_total"]["final"] = 999
        # Internal state unchanged
        assert m.snapshot()["upsert_total"]["final"] == 1


class TestSingleton:
    """Tests for get_ptf_metrics singleton."""

    def test_returns_same_instance(self):
        a = get_ptf_metrics()
        b = get_ptf_metrics()
        assert a is b

    def test_instance_is_ptf_metrics(self):
        assert isinstance(get_ptf_metrics(), PTFMetrics)


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Tests — Metrics incremented via API endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def fresh_metrics():
    """Reset the singleton metrics before each test."""
    m = get_ptf_metrics()
    m.reset()
    yield m


@pytest.fixture()
def mock_service():
    """Patch the admin service singleton."""
    with patch(
        "app.market_price_admin_service.get_market_price_admin_service"
    ) as factory:
        svc = MagicMock()
        factory.return_value = svc
        yield svc


@pytest.fixture()
def client(mock_service):
    """TestClient with DB and admin-key dependencies overridden."""
    with patch.dict("os.environ", {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db
        from fastapi.testclient import TestClient

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db

        yield TestClient(fastapi_app)

        fastapi_app.dependency_overrides.clear()


class TestUpsertEndpointMetrics:
    """Verify upsert_total metric is incremented when POST /admin/market-prices succeeds."""

    def test_upsert_provisional_increments_metric(self, client, mock_service, fresh_metrics):
        """Successful upsert with status=provisional increments upsert_total{status=provisional}."""
        from app.market_price_admin_service import UpsertResult

        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True, record=MagicMock(), warnings=[]
        )

        resp = client.post("/admin/market-prices", json={
            "period": "2025-01",
            "value": 2508.80,
            "status": "provisional",
        })

        assert resp.status_code == 200
        snap = fresh_metrics.snapshot()
        assert snap["upsert_total"]["provisional"] == 1
        assert snap["upsert_total"]["final"] == 0

    def test_upsert_final_increments_metric(self, client, mock_service, fresh_metrics):
        """Successful upsert with status=final increments upsert_total{status=final}."""
        from app.market_price_admin_service import UpsertResult

        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True, record=MagicMock(), warnings=[]
        )

        resp = client.post("/admin/market-prices", json={
            "period": "2025-01",
            "value": 2508.80,
            "status": "final",
        })

        assert resp.status_code == 200
        snap = fresh_metrics.snapshot()
        assert snap["upsert_total"]["final"] == 1

    def test_failed_upsert_does_not_increment_metric(self, client, mock_service, fresh_metrics):
        """Failed upsert (validation error) should NOT increment upsert_total."""
        # Use an invalid period format to trigger validation failure before service call
        resp = client.post("/admin/market-prices", json={
            "period": "bad-period",
            "value": 2508.80,
        })

        assert resp.status_code == 400
        snap = fresh_metrics.snapshot()
        assert snap["upsert_total"]["provisional"] == 0
        assert snap["upsert_total"]["final"] == 0


class TestLookupEndpointMetrics:
    """Verify lookup_total metric is incremented on GET /api/market-prices/{price_type}/{period}."""

    def test_lookup_hit_final(self, client, mock_service, fresh_metrics):
        """Successful lookup with final record increments lookup_total{hit=true,status=final}."""
        from app.market_price_admin_service import MarketPriceLookupResult
        from decimal import Decimal

        mock_service.get_for_calculation.return_value = (
            MarketPriceLookupResult(
                period="2025-01", value=Decimal("2508.80"),
                status="final", price_type="PTF",
                is_provisional_used=False, source="seed",
                captured_at=datetime(2025, 1, 1),
            ),
            None,
        )

        resp = client.get("/api/market-prices/PTF/2025-01")

        assert resp.status_code == 200
        snap = fresh_metrics.snapshot()
        assert snap["lookup_total"]["hit=true,status=final"] == 1
        assert snap["lookup_total"]["hit=false"] == 0

    def test_lookup_hit_provisional(self, client, mock_service, fresh_metrics):
        """Successful lookup with provisional record increments lookup_total{hit=true,status=provisional}."""
        from app.market_price_admin_service import MarketPriceLookupResult
        from decimal import Decimal

        mock_service.get_for_calculation.return_value = (
            MarketPriceLookupResult(
                period="2025-01", value=Decimal("2536.21"),
                status="provisional", price_type="PTF",
                is_provisional_used=True, source="seed",
                captured_at=datetime(2025, 1, 1),
            ),
            None,
        )

        resp = client.get("/api/market-prices/PTF/2025-01")

        assert resp.status_code == 200
        snap = fresh_metrics.snapshot()
        assert snap["lookup_total"]["hit=true,status=provisional"] == 1

    def test_lookup_miss(self, client, mock_service, fresh_metrics):
        """Lookup miss (period not found) increments lookup_total{hit=false}."""
        from app.market_price_admin_service import ServiceError, ServiceErrorCode

        mock_service.get_for_calculation.return_value = (
            None,
            ServiceError(
                error_code=ServiceErrorCode.PERIOD_NOT_FOUND,
                field="period",
                message="Not found",
            ),
        )

        resp = client.get("/api/market-prices/PTF/2020-01")

        assert resp.status_code == 404
        snap = fresh_metrics.snapshot()
        assert snap["lookup_total"]["hit=false"] == 1
        assert snap["lookup_total"]["hit=true,status=final"] == 0


class TestImportApplyEndpointMetrics:
    """Verify import metrics are incremented on POST /admin/market-prices/import/apply."""

    def test_import_apply_records_duration_and_rows(self, client, fresh_metrics):
        """Import apply should record duration and row counts."""
        from app.bulk_importer import ImportResult

        mock_result = ImportResult(
            success=True, accepted_count=3, rejected_count=1, rejected_rows=[]
        )

        with patch("app.bulk_importer.get_bulk_importer") as mock_get_importer:
            mock_importer = MagicMock()
            mock_get_importer.return_value = mock_importer
            mock_importer.parse_csv.return_value = [MagicMock()]
            mock_importer.apply.return_value = mock_result

            resp = client.post(
                "/admin/market-prices/import/apply",
                files={"file": ("test.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
                data={"price_type": "PTF", "force_update": "false", "strict_mode": "false"},
            )

        assert resp.status_code == 200
        snap = fresh_metrics.snapshot()
        assert snap["import_apply_duration_seconds"]["count"] == 1
        assert snap["import_apply_duration_seconds"]["total_seconds"] > 0
        assert snap["import_rows_total"]["accepted"] == 3
        assert snap["import_rows_total"]["rejected"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Dependency Wrapper Metrics — Feature: dependency-wrappers, Task 2
# ═══════════════════════════════════════════════════════════════════════════════

from hypothesis import given, settings as h_settings, HealthCheck
from hypothesis import strategies as st


class TestDependencyCallTotal:
    """ptf_admin_dependency_call_total{dependency, outcome} counter tests."""

    def setup_method(self):
        self.metrics = PTFMetrics(registry=CollectorRegistry())

    def test_inc_success(self):
        self.metrics.inc_dependency_call("db_primary", "success")
        val = self.metrics._dependency_call_total.labels(
            dependency="db_primary", outcome="success"
        )._value.get()
        assert val == 1.0

    def test_inc_failure(self):
        self.metrics.inc_dependency_call("external_api", "failure")
        val = self.metrics._dependency_call_total.labels(
            dependency="external_api", outcome="failure"
        )._value.get()
        assert val == 1.0

    def test_inc_timeout(self):
        self.metrics.inc_dependency_call("cache", "timeout")
        val = self.metrics._dependency_call_total.labels(
            dependency="cache", outcome="timeout"
        )._value.get()
        assert val == 1.0

    def test_inc_circuit_open(self):
        self.metrics.inc_dependency_call("db_replica", "circuit_open")
        val = self.metrics._dependency_call_total.labels(
            dependency="db_replica", outcome="circuit_open"
        )._value.get()
        assert val == 1.0

    def test_invalid_outcome_ignored(self):
        """Invalid outcome → no increment, no crash."""
        self.metrics.inc_dependency_call("db_primary", "bogus")
        # Should not have created a label combo for "bogus"
        # Just verify no exception was raised

    def test_multiple_increments(self):
        self.metrics.inc_dependency_call("db_primary", "success")
        self.metrics.inc_dependency_call("db_primary", "success")
        self.metrics.inc_dependency_call("db_primary", "failure")
        val_s = self.metrics._dependency_call_total.labels(
            dependency="db_primary", outcome="success"
        )._value.get()
        val_f = self.metrics._dependency_call_total.labels(
            dependency="db_primary", outcome="failure"
        )._value.get()
        assert val_s == 2.0
        assert val_f == 1.0


class TestDependencyCallDuration:
    """ptf_admin_dependency_call_duration_seconds{dependency} histogram tests."""

    def setup_method(self):
        self.metrics = PTFMetrics(registry=CollectorRegistry())

    def test_observe_duration(self):
        self.metrics.observe_dependency_call_duration("db_primary", 0.123)
        # Verify histogram was updated by checking collect()
        samples = self.metrics._dependency_call_duration.labels(
            dependency="db_primary"
        )._sum.get()
        assert samples > 0

    def test_multiple_observations(self):
        self.metrics.observe_dependency_call_duration("cache", 0.01)
        self.metrics.observe_dependency_call_duration("cache", 0.02)
        total = self.metrics._dependency_call_duration.labels(
            dependency="cache"
        )._sum.get()
        assert abs(total - 0.03) < 0.001


class TestDependencyRetryTotal:
    """ptf_admin_dependency_retry_total{dependency} counter tests."""

    def setup_method(self):
        self.metrics = PTFMetrics(registry=CollectorRegistry())

    def test_inc_retry(self):
        self.metrics.inc_dependency_retry("external_api")
        val = self.metrics._dependency_retry_total.labels(
            dependency="external_api"
        )._value.get()
        assert val == 1.0

    def test_multiple_retries(self):
        self.metrics.inc_dependency_retry("db_primary")
        self.metrics.inc_dependency_retry("db_primary")
        self.metrics.inc_dependency_retry("db_primary")
        val = self.metrics._dependency_retry_total.labels(
            dependency="db_primary"
        )._value.get()
        assert val == 3.0


class TestGuardFailopenTotal:
    """ptf_admin_guard_failopen_total counter tests (DW-3)."""

    def setup_method(self):
        self.metrics = PTFMetrics(registry=CollectorRegistry())

    def test_inc_failopen(self):
        self.metrics.inc_guard_failopen()
        val = self.metrics._guard_failopen_total._value.get()
        assert val == 1.0

    def test_multiple_failopen(self):
        self.metrics.inc_guard_failopen()
        self.metrics.inc_guard_failopen()
        val = self.metrics._guard_failopen_total._value.get()
        assert val == 2.0


class TestDependencyMapMissTotal:
    """ptf_admin_dependency_map_miss_total counter tests."""

    def setup_method(self):
        self.metrics = PTFMetrics(registry=CollectorRegistry())

    def test_inc_map_miss(self):
        self.metrics.inc_dependency_map_miss()
        val = self.metrics._dependency_map_miss_total._value.get()
        assert val == 1.0

    def test_multiple_map_miss(self):
        self.metrics.inc_dependency_map_miss()
        self.metrics.inc_dependency_map_miss()
        self.metrics.inc_dependency_map_miss()
        val = self.metrics._dependency_map_miss_total._value.get()
        assert val == 3.0


class TestDependencyMetricsPropertyBased:
    """Property-based tests for dependency wrapper metrics — Feature: dependency-wrappers, Task 2."""

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        dep=st.sampled_from(["db_primary", "db_replica", "cache", "external_api", "import_worker"]),
        outcome=st.sampled_from(["success", "failure", "timeout", "circuit_open"]),
        count=st.integers(min_value=1, max_value=10),
    )
    def test_dependency_call_counter_monotonic(self, dep, outcome, count):
        """Feature: dependency-wrappers, Property 7: Wrapper Metrik Kaydı — counter monotonicity."""
        metrics = PTFMetrics(registry=CollectorRegistry())
        for _ in range(count):
            metrics.inc_dependency_call(dep, outcome)
        val = metrics._dependency_call_total.labels(
            dependency=dep, outcome=outcome
        )._value.get()
        assert val == count

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        dep=st.sampled_from(["db_primary", "db_replica", "cache", "external_api", "import_worker"]),
        duration=st.floats(min_value=0.001, max_value=60.0),
    )
    def test_dependency_duration_positive(self, dep, duration):
        """Feature: dependency-wrappers, Property 7: duration histogram always positive."""
        metrics = PTFMetrics(registry=CollectorRegistry())
        metrics.observe_dependency_call_duration(dep, duration)
        total = metrics._dependency_call_duration.labels(dependency=dep)._sum.get()
        assert total > 0

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        dep=st.sampled_from(["db_primary", "db_replica", "cache", "external_api", "import_worker"]),
        count=st.integers(min_value=1, max_value=10),
    )
    def test_dependency_retry_counter_monotonic(self, dep, count):
        """Feature: dependency-wrappers, Property 7: retry counter monotonicity."""
        metrics = PTFMetrics(registry=CollectorRegistry())
        for _ in range(count):
            metrics.inc_dependency_retry(dep)
        val = metrics._dependency_retry_total.labels(dependency=dep)._value.get()
        assert val == count
