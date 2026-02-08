"""
Unit tests for deprecated alias endpoints.

Feature: ptf-admin-management
Task: 9.2 Deprecation aliases for backward compatibility.
Requirements: 1.6

Tests:
- POST /admin/market-prices/form (form-based → JSON-based alias)
- GET /admin/market-prices/legacy (legacy list → paginated alias)
- GET /admin/market-prices/deprecation-stats (usage metrics)
- Deprecation headers on responses
- alias_usage_total counter increments
"""

import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(**overrides):
    """Create a mock MarketReferencePrice record."""
    defaults = dict(
        id=1,
        period="2025-01",
        ptf_tl_per_mwh=2508.80,
        status="final",
        captured_at=datetime(2025, 1, 15, 10, 0, 0),
        is_locked=0,
        updated_by="admin",
        updated_at=datetime(2025, 1, 15, 12, 0, 0),
        price_type="PTF",
        source="seed",
    )
    defaults.update(overrides)
    rec = MagicMock()
    for k, v in defaults.items():
        setattr(rec, k, v)
    return rec


def _make_normalized(period="2025-01", value=2508.80, status="final", price_type="PTF"):
    """Create a NormalizedMarketPriceInput for test mocking."""
    from app.market_price_validator import NormalizedMarketPriceInput
    return NormalizedMarketPriceInput(
        period=period,
        value=Decimal(str(value)),
        status=status,
        price_type=price_type,
    )


def _make_valid_validation_result(period="2025-01", value=2508.80, status="final"):
    """Create a valid ValidationResult + NormalizedInput tuple for mocking."""
    from app.market_price_validator import ValidationResult as VR
    return (
        VR(is_valid=True, errors=[], warnings=[]),
        _make_normalized(period=period, value=value, status=status),
    )


def _make_success_upsert_result(created=True, changed=False, warnings=None):
    """Create a successful UpsertResult mock."""
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.created = created
    mock_result.changed = changed
    mock_result.warnings = warnings or []
    mock_result.error = None
    return mock_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_service():
    """Patch the admin service singleton returned by get_market_price_admin_service."""
    with patch(
        "app.market_price_admin_service.get_market_price_admin_service"
    ) as factory:
        svc = MagicMock()
        factory.return_value = svc
        yield svc


@pytest.fixture()
def client(mock_service):
    """
    Create a TestClient with DB and admin-key dependencies overridden.
    """
    with patch.dict("os.environ", {"ADMIN_API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db

        yield TestClient(fastapi_app)

        fastapi_app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def reset_alias_counters():
    """Reset the deprecated alias usage counters before each test."""
    from app.main import _deprecated_alias_usage
    _deprecated_alias_usage["post_form"] = 0
    _deprecated_alias_usage["get_legacy"] = 0
    yield


# ---------------------------------------------------------------------------
# Tests – POST /admin/market-prices/form (Deprecated Form-based Alias)
# ---------------------------------------------------------------------------

class TestDeprecatedFormPost:
    """Tests for the deprecated form-based POST endpoint."""

    def test_form_post_returns_ok_on_success(self, client, mock_service):
        """Form-based POST should forward to upsert and return success."""
        with patch("app.market_price_validator.MarketPriceValidator") as MockValidator:
            MockValidator.return_value.validate_entry.return_value = _make_valid_validation_result()
            mock_service.upsert_price.return_value = _make_success_upsert_result()

            resp = client.post(
                "/admin/market-prices/form",
                data={"period": "2025-01", "value": "2508.80", "status": "final"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["action"] == "created"
        assert body["period"] == "2025-01"

    def test_form_post_includes_deprecation_headers(self, client, mock_service):
        """Deprecated form POST should include Deprecation and Sunset headers."""
        with patch("app.market_price_validator.MarketPriceValidator") as MockValidator:
            MockValidator.return_value.validate_entry.return_value = _make_valid_validation_result()
            mock_service.upsert_price.return_value = _make_success_upsert_result()

            resp = client.post(
                "/admin/market-prices/form",
                data={"period": "2025-01", "value": "2508.80", "status": "final"},
            )

        assert resp.headers.get("deprecation") == "true"
        assert resp.headers.get("sunset") == "2025-12-31"
        assert "deprecated" in resp.headers.get("x-deprecation-notice", "").lower()

    def test_form_post_increments_usage_counter(self, client, mock_service):
        """Each form POST call should increment the post_form counter."""
        from app.main import _deprecated_alias_usage

        with patch("app.market_price_validator.MarketPriceValidator") as MockValidator:
            MockValidator.return_value.validate_entry.return_value = _make_valid_validation_result()
            mock_service.upsert_price.return_value = _make_success_upsert_result()

            assert _deprecated_alias_usage["post_form"] == 0

            client.post(
                "/admin/market-prices/form",
                data={"period": "2025-01", "value": "2508.80", "status": "final"},
            )
            assert _deprecated_alias_usage["post_form"] == 1

            client.post(
                "/admin/market-prices/form",
                data={"period": "2025-02", "value": "2478.28", "status": "final"},
            )
            assert _deprecated_alias_usage["post_form"] == 2

    def test_form_post_validation_error_returns_400(self, client, mock_service):
        """Form POST with invalid data should return 400."""
        from app.market_price_validator import (
            ValidationResult as VR,
            ValidationError,
            ErrorCode,
        )

        with patch("app.market_price_validator.MarketPriceValidator") as MockValidator:
            MockValidator.return_value.validate_entry.return_value = (
                VR(
                    is_valid=False,
                    errors=[ValidationError(
                        error_code=ErrorCode.INVALID_PERIOD_FORMAT,
                        message="Geçersiz dönem formatı",
                        field="period",
                    )],
                    warnings=[],
                ),
                None,
            )

            resp = client.post(
                "/admin/market-prices/form",
                data={"period": "bad", "value": "2508.80", "status": "final"},
            )

        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error_code"] == "INVALID_PERIOD_FORMAT"

    def test_form_post_service_error_returns_409(self, client, mock_service):
        """Form POST with locked period should return 409."""
        from app.market_price_admin_service import ServiceError, ServiceErrorCode

        with patch("app.market_price_validator.MarketPriceValidator") as MockValidator:
            MockValidator.return_value.validate_entry.return_value = _make_valid_validation_result()

            mock_result = MagicMock()
            mock_result.success = False
            mock_result.error = ServiceError(
                error_code=ServiceErrorCode.PERIOD_LOCKED,
                message="Period 2025-01 is locked",
                field="period",
            )
            mock_service.upsert_price.return_value = mock_result

            resp = client.post(
                "/admin/market-prices/form",
                data={"period": "2025-01", "value": "2508.80", "status": "final"},
            )

        assert resp.status_code == 409

    def test_form_post_updated_action(self, client, mock_service):
        """Form POST updating existing record should return action=updated."""
        with patch("app.market_price_validator.MarketPriceValidator") as MockValidator:
            MockValidator.return_value.validate_entry.return_value = _make_valid_validation_result()
            mock_service.upsert_price.return_value = _make_success_upsert_result(
                created=False, changed=True,
            )

            resp = client.post(
                "/admin/market-prices/form",
                data={"period": "2025-01", "value": "2508.80", "status": "final"},
            )

        assert resp.status_code == 200
        assert resp.json()["action"] == "updated"


# ---------------------------------------------------------------------------
# Tests – GET /admin/market-prices/legacy (Deprecated Legacy List)
# ---------------------------------------------------------------------------

class TestDeprecatedLegacyGet:
    """Tests for the deprecated legacy GET endpoint."""

    def test_legacy_get_returns_all_records(self, client, mock_service):
        """Legacy GET should return all records (up to 100) without pagination params."""
        list_result = MagicMock()
        list_result.total = 2
        list_result.items = [
            _make_record(period="2025-01", ptf_tl_per_mwh=2508.80),
            _make_record(period="2025-02", ptf_tl_per_mwh=2478.28),
        ]
        mock_service.list_prices.return_value = list_result

        resp = client.get("/admin/market-prices/legacy")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["total"] == 2
        assert len(body["items"]) == 2

    def test_legacy_get_calls_service_with_defaults(self, client, mock_service):
        """Legacy GET should call list_prices with limit=100, offset=0, sort_by=period, desc."""
        list_result = MagicMock()
        list_result.total = 0
        list_result.items = []
        mock_service.list_prices.return_value = list_result

        client.get("/admin/market-prices/legacy")

        call_kwargs = mock_service.list_prices.call_args.kwargs
        assert call_kwargs["price_type"] is None
        assert call_kwargs["status"] is None
        assert call_kwargs["period_from"] is None
        assert call_kwargs["period_to"] is None
        assert call_kwargs["limit"] == 100
        assert call_kwargs["offset"] == 0
        assert call_kwargs["sort_by"] == "period"
        assert call_kwargs["sort_order"] == "desc"

    def test_legacy_get_includes_deprecation_headers(self, client, mock_service):
        """Deprecated legacy GET should include Deprecation and Sunset headers."""
        list_result = MagicMock()
        list_result.total = 0
        list_result.items = []
        mock_service.list_prices.return_value = list_result

        resp = client.get("/admin/market-prices/legacy")

        assert resp.headers.get("deprecation") == "true"
        assert resp.headers.get("sunset") == "2025-12-31"
        assert "deprecated" in resp.headers.get("x-deprecation-notice", "").lower()

    def test_legacy_get_increments_usage_counter(self, client, mock_service):
        """Each legacy GET call should increment the get_legacy counter."""
        from app.main import _deprecated_alias_usage

        list_result = MagicMock()
        list_result.total = 0
        list_result.items = []
        mock_service.list_prices.return_value = list_result

        assert _deprecated_alias_usage["get_legacy"] == 0

        client.get("/admin/market-prices/legacy")
        assert _deprecated_alias_usage["get_legacy"] == 1

        client.get("/admin/market-prices/legacy")
        assert _deprecated_alias_usage["get_legacy"] == 2

    def test_legacy_get_response_has_no_pagination_fields(self, client, mock_service):
        """Legacy GET response should not include page/page_size fields."""
        list_result = MagicMock()
        list_result.total = 1
        list_result.items = [_make_record()]
        mock_service.list_prices.return_value = list_result

        resp = client.get("/admin/market-prices/legacy")
        body = resp.json()

        assert "page" not in body
        assert "page_size" not in body

    def test_legacy_get_item_fields(self, client, mock_service):
        """Legacy GET items should have the same fields as the new endpoint."""
        list_result = MagicMock()
        list_result.total = 1
        list_result.items = [_make_record(
            period="2025-03",
            ptf_tl_per_mwh=2183.83,
            status="final",
            captured_at=datetime(2025, 3, 15, 10, 0, 0),
            is_locked=1,
            updated_by="admin",
            updated_at=datetime(2025, 3, 15, 12, 0, 0),
        )]
        mock_service.list_prices.return_value = list_result

        resp = client.get("/admin/market-prices/legacy")
        item = resp.json()["items"][0]

        assert item["period"] == "2025-03"
        assert item["ptf_value"] == 2183.83
        assert item["status"] == "final"
        assert item["is_locked"] is True
        assert item["updated_by"] == "admin"
        assert "captured_at" in item
        assert "updated_at" in item


# ---------------------------------------------------------------------------
# Tests – GET /admin/market-prices/deprecation-stats
# ---------------------------------------------------------------------------

class TestDeprecationStats:
    """Tests for the deprecation stats endpoint."""

    def test_stats_returns_zero_initially(self, client, mock_service):
        """Stats should return zero counts when no deprecated endpoints have been called."""
        resp = client.get("/admin/market-prices/deprecation-stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["alias_usage_total"]["post_form"] == 0
        assert body["alias_usage_total"]["get_legacy"] == 0

    def test_stats_reflects_form_post_usage(self, client, mock_service):
        """Stats should reflect form POST usage count."""
        with patch("app.market_price_validator.MarketPriceValidator") as MockValidator:
            MockValidator.return_value.validate_entry.return_value = _make_valid_validation_result()
            mock_service.upsert_price.return_value = _make_success_upsert_result()

            client.post(
                "/admin/market-prices/form",
                data={"period": "2025-01", "value": "2508.80", "status": "final"},
            )

        resp = client.get("/admin/market-prices/deprecation-stats")
        body = resp.json()
        assert body["alias_usage_total"]["post_form"] == 1
        assert body["alias_usage_total"]["get_legacy"] == 0

    def test_stats_reflects_legacy_get_usage(self, client, mock_service):
        """Stats should reflect legacy GET usage count."""
        list_result = MagicMock()
        list_result.total = 0
        list_result.items = []
        mock_service.list_prices.return_value = list_result

        client.get("/admin/market-prices/legacy")

        resp = client.get("/admin/market-prices/deprecation-stats")
        body = resp.json()
        assert body["alias_usage_total"]["post_form"] == 0
        assert body["alias_usage_total"]["get_legacy"] == 1

    def test_stats_reflects_combined_usage(self, client, mock_service):
        """Stats should reflect combined usage of both deprecated endpoints."""
        # Call legacy GET twice
        list_result = MagicMock()
        list_result.total = 0
        list_result.items = []
        mock_service.list_prices.return_value = list_result
        client.get("/admin/market-prices/legacy")
        client.get("/admin/market-prices/legacy")

        # Call form POST once
        with patch("app.market_price_validator.MarketPriceValidator") as MockValidator:
            MockValidator.return_value.validate_entry.return_value = _make_valid_validation_result()
            mock_service.upsert_price.return_value = _make_success_upsert_result()

            client.post(
                "/admin/market-prices/form",
                data={"period": "2025-01", "value": "2508.80", "status": "final"},
            )

        resp = client.get("/admin/market-prices/deprecation-stats")
        body = resp.json()
        assert body["alias_usage_total"]["post_form"] == 1
        assert body["alias_usage_total"]["get_legacy"] == 2
