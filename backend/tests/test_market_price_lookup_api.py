"""
Unit tests for GET /api/market-prices/{price_type}/{period} endpoint.

Feature: ptf-admin-management
Tests calculation lookup response format, validation, error handling.
Requirements: 7.1, 7.2, 7.3, 7.5, 7.6, 7.7
"""

import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


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
    Create a TestClient with DB and API-key dependencies overridden.
    """
    with patch.dict("os.environ", {"ADMIN_API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db

        yield TestClient(fastapi_app)

        fastapi_app.dependency_overrides.clear()


def _make_lookup_result(**overrides):
    """Create a mock MarketPriceLookupResult."""
    from app.market_price_admin_service import MarketPriceLookupResult

    defaults = dict(
        period="2025-01",
        value=Decimal("2508.80"),
        status="final",
        price_type="PTF",
        is_provisional_used=False,
        source="seed",
        captured_at=datetime(2025, 1, 15, 10, 0, 0),
    )
    defaults.update(overrides)
    return MarketPriceLookupResult(**defaults)


def _make_service_error(error_code, field="period", message="Error"):
    """Create a mock ServiceError."""
    from app.market_price_admin_service import ServiceError, ServiceErrorCode

    return ServiceError(
        error_code=ServiceErrorCode(error_code),
        field=field,
        message=message,
    )


# ---------------------------------------------------------------------------
# Tests – Response structure (Requirements 7.1, 7.2, 7.3)
# ---------------------------------------------------------------------------

class TestLookupResponseFormat:
    """Verify the response contains all required fields."""

    def test_response_contains_required_keys(self, client, mock_service):
        """Response must include period, value, price_type, status, is_provisional_used."""
        mock_service.get_for_calculation.return_value = (
            _make_lookup_result(),
            None,
        )

        resp = client.get("/api/market-prices/PTF/2025-01")
        assert resp.status_code == 200

        data = resp.json()
        required_keys = {"period", "value", "price_type", "status", "is_provisional_used"}
        assert required_keys.issubset(data.keys())

    def test_final_record_values_are_correct(self, client, mock_service):
        """Final record: is_provisional_used must be False."""
        mock_service.get_for_calculation.return_value = (
            _make_lookup_result(
                period="2025-01",
                value=Decimal("2508.80"),
                status="final",
                price_type="PTF",
                is_provisional_used=False,
            ),
            None,
        )

        resp = client.get("/api/market-prices/PTF/2025-01")
        data = resp.json()

        assert data["period"] == "2025-01"
        assert data["value"] == 2508.80
        assert data["price_type"] == "PTF"
        assert data["status"] == "final"
        assert data["is_provisional_used"] is False

    def test_provisional_record_values_are_correct(self, client, mock_service):
        """Provisional record: is_provisional_used must be True."""
        mock_service.get_for_calculation.return_value = (
            _make_lookup_result(
                period="2026-02",
                value=Decimal("2536.21"),
                status="provisional",
                price_type="PTF",
                is_provisional_used=True,
            ),
            None,
        )

        resp = client.get("/api/market-prices/PTF/2026-02")
        data = resp.json()

        assert data["period"] == "2026-02"
        assert data["value"] == 2536.21
        assert data["status"] == "provisional"
        assert data["is_provisional_used"] is True


# ---------------------------------------------------------------------------
# Tests – Service delegation
# ---------------------------------------------------------------------------

class TestLookupServiceDelegation:
    """Verify the endpoint correctly delegates to the service."""

    def test_price_type_and_period_passed_to_service(self, client, mock_service):
        """Path params must be forwarded to get_for_calculation."""
        mock_service.get_for_calculation.return_value = (
            _make_lookup_result(),
            None,
        )

        client.get("/api/market-prices/PTF/2025-03")

        call_kwargs = mock_service.get_for_calculation.call_args
        assert call_kwargs.kwargs["period"] == "2025-03"
        assert call_kwargs.kwargs["price_type"] == "PTF"


# ---------------------------------------------------------------------------
# Tests – Error handling (Requirements 7.5, 7.6, 7.7)
# ---------------------------------------------------------------------------

class TestLookupErrors:
    """Verify error responses for various failure scenarios."""

    def test_period_not_found_returns_404(self, client, mock_service):
        """Requirement 7.5: no record → error, not fallback."""
        mock_service.get_for_calculation.return_value = (
            None,
            _make_service_error(
                "PERIOD_NOT_FOUND",
                field="period",
                message="Dönem 2020-01 için PTF kaydı bulunamadı.",
            ),
        )

        resp = client.get("/api/market-prices/PTF/2020-01")
        assert resp.status_code == 404

        data = resp.json()["detail"]
        assert data["status"] == "error"
        assert data["error_code"] == "PERIOD_NOT_FOUND"
        assert data["field"] == "period"

    def test_future_period_returns_400(self, client, mock_service):
        """Requirement 7.6: future period → error."""
        mock_service.get_for_calculation.return_value = (
            None,
            _make_service_error(
                "FUTURE_PERIOD",
                field="period",
                message="Gelecek dönem (2099-12) için fiyat sorgulanamaz.",
            ),
        )

        resp = client.get("/api/market-prices/PTF/2099-12")
        assert resp.status_code == 400

        data = resp.json()["detail"]
        assert data["status"] == "error"
        assert data["error_code"] == "FUTURE_PERIOD"

    def test_invalid_period_format_returns_400(self, client, mock_service):
        """Requirement 7.7: invalid period format → validation error."""
        resp = client.get("/api/market-prices/PTF/2025-13")
        assert resp.status_code == 400

        data = resp.json()["detail"]
        assert data["status"] == "error"
        assert data["error_code"] == "INVALID_PERIOD_FORMAT"
        assert data["field"] == "period"

    def test_invalid_period_format_not_yyyy_mm(self, client, mock_service):
        """Non YYYY-MM format rejected."""
        resp = client.get("/api/market-prices/PTF/2025")
        assert resp.status_code == 400

        data = resp.json()["detail"]
        assert data["error_code"] == "INVALID_PERIOD_FORMAT"

    def test_invalid_price_type_returns_400(self, client, mock_service):
        """Invalid price_type rejected before service call."""
        resp = client.get("/api/market-prices/INVALID/2025-01")
        assert resp.status_code == 400

        data = resp.json()["detail"]
        assert data["status"] == "error"
        assert data["error_code"] == "INVALID_PRICE_TYPE"
        assert data["field"] == "price_type"

    def test_error_response_has_standard_schema(self, client, mock_service):
        """Error response must follow the standard error schema."""
        mock_service.get_for_calculation.return_value = (
            None,
            _make_service_error(
                "PERIOD_NOT_FOUND",
                field="period",
                message="Not found",
            ),
        )

        resp = client.get("/api/market-prices/PTF/2020-01")
        data = resp.json()["detail"]

        required_keys = {"status", "error_code", "message", "field", "row_index", "details"}
        assert required_keys.issubset(data.keys())
        assert data["row_index"] is None
        assert data["details"] == {}


# ---------------------------------------------------------------------------
# Tests – Value serialization
# ---------------------------------------------------------------------------

class TestLookupValueSerialization:
    """Verify Decimal values are serialized correctly as floats."""

    def test_decimal_value_serialized_as_float(self, client, mock_service):
        """Decimal value must be returned as a JSON number."""
        mock_service.get_for_calculation.return_value = (
            _make_lookup_result(value=Decimal("1942.90")),
            None,
        )

        resp = client.get("/api/market-prices/PTF/2025-01")
        data = resp.json()

        assert isinstance(data["value"], float)
        assert data["value"] == 1942.90

    def test_integer_decimal_serialized_correctly(self, client, mock_service):
        """Integer-like Decimal (e.g. 2000.00) serialized correctly."""
        mock_service.get_for_calculation.return_value = (
            _make_lookup_result(value=Decimal("2000.00")),
            None,
        )

        resp = client.get("/api/market-prices/PTF/2025-01")
        data = resp.json()

        assert data["value"] == 2000.00
