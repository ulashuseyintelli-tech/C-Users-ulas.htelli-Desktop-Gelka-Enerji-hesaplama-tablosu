"""
Unit tests for GET /admin/market-prices endpoint.

Feature: ptf-admin-management
Tests pagination, sorting, filtering, and response format.
Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers – lightweight stand-ins so we never touch a real DB / OpenAI key
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
    
    Depends on mock_service so the patch is active before the app handles
    any request.
    """
    with patch.dict("os.environ", {"ADMIN_API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db

        # Override DB dependency with a no-op mock session
        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db

        yield TestClient(fastapi_app)

        # Clean up
        fastapi_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests – Response structure
# ---------------------------------------------------------------------------

class TestListMarketPricesResponseFormat:
    """Verify the response JSON shape matches the design spec."""

    def test_response_contains_required_keys(self, client, mock_service):
        """Response must include status, total, page, page_size, items."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices")
        assert resp.status_code == 200

        body = resp.json()
        assert body["status"] == "ok"
        assert "total" in body
        assert "page" in body
        assert "page_size" in body
        assert "items" in body

    def test_item_fields_match_requirement_4_3(self, client, mock_service):
        """
        Requirement 4.3: Each item SHALL include period, ptf_value, status,
        captured_at, is_locked, updated_by, updated_at.
        """
        from app.market_price_admin_service import PaginatedResult

        record = _make_record()
        mock_service.list_prices.return_value = PaginatedResult(
            items=[record], total=1, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices")
        assert resp.status_code == 200

        item = resp.json()["items"][0]
        required_fields = {"period", "ptf_value", "status", "captured_at",
                           "is_locked", "updated_by", "updated_at"}
        assert required_fields.issubset(item.keys())

    def test_item_values_are_correct(self, client, mock_service):
        """Verify item values are serialised correctly."""
        from app.market_price_admin_service import PaginatedResult

        record = _make_record(
            period="2025-03",
            ptf_tl_per_mwh=2183.83,
            status="provisional",
            captured_at=datetime(2025, 3, 1, 8, 0, 0),
            is_locked=1,
            updated_by="test_user",
            updated_at=datetime(2025, 3, 2, 9, 30, 0),
        )
        mock_service.list_prices.return_value = PaginatedResult(
            items=[record], total=1, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices")
        item = resp.json()["items"][0]

        assert item["period"] == "2025-03"
        assert item["ptf_value"] == 2183.83
        assert item["status"] == "provisional"
        assert item["captured_at"] == "2025-03-01T08:00:00"
        assert item["is_locked"] is True
        assert item["updated_by"] == "test_user"
        assert item["updated_at"] == "2025-03-02T09:30:00"


# ---------------------------------------------------------------------------
# Tests – Default parameters
# ---------------------------------------------------------------------------

class TestListMarketPricesDefaults:
    """Verify default query parameter values."""

    def test_defaults_page_1_size_20(self, client, mock_service):
        """Default page=1, page_size=20."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices")
        body = resp.json()

        assert body["page"] == 1
        assert body["page_size"] == 20

        # Verify service was called with correct offset/limit
        call_kwargs = mock_service.list_prices.call_args
        assert call_kwargs.kwargs["offset"] == 0
        assert call_kwargs.kwargs["limit"] == 20

    def test_defaults_sort_by_period_desc(self, client, mock_service):
        """Default sort_by=period, sort_order=desc."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices")
        assert resp.status_code == 200

        call_kwargs = mock_service.list_prices.call_args
        assert call_kwargs.kwargs["sort_by"] == "period"
        assert call_kwargs.kwargs["sort_order"] == "desc"


# ---------------------------------------------------------------------------
# Tests – Pagination
# ---------------------------------------------------------------------------

class TestListMarketPricesPagination:
    """Requirement 4.1: Paginated list with configurable page size."""

    def test_page_2_offset_calculation(self, client, mock_service):
        """page=2, page_size=10 → offset=10, limit=10."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=50, has_more=True, next_cursor="20",
        )

        resp = client.get("/admin/market-prices?page=2&page_size=10")
        assert resp.status_code == 200

        body = resp.json()
        assert body["page"] == 2
        assert body["page_size"] == 10
        assert body["total"] == 50

        call_kwargs = mock_service.list_prices.call_args
        assert call_kwargs.kwargs["offset"] == 10
        assert call_kwargs.kwargs["limit"] == 10

    def test_page_size_boundary_max(self, client, mock_service):
        """page_size=100 is the maximum allowed."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices?page_size=100")
        assert resp.status_code == 200

    def test_page_size_over_max_rejected(self, client, mock_service):
        """page_size > 100 should be rejected by FastAPI validation."""
        resp = client.get("/admin/market-prices?page_size=101")
        assert resp.status_code == 422  # Validation error

    def test_page_zero_rejected(self, client, mock_service):
        """page=0 should be rejected (minimum is 1)."""
        resp = client.get("/admin/market-prices?page=0")
        assert resp.status_code == 422

    def test_negative_page_size_rejected(self, client, mock_service):
        """page_size=-1 should be rejected."""
        resp = client.get("/admin/market-prices?page_size=-1")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests – Sorting
# ---------------------------------------------------------------------------

class TestListMarketPricesSorting:
    """Requirement 4.2: Sorting by period, ptf_value, status, updated_at."""

    def test_sort_by_period_asc(self, client, mock_service):
        """sort_by=period&sort_order=asc should be passed to service."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices?sort_by=period&sort_order=asc")
        assert resp.status_code == 200

        call_kwargs = mock_service.list_prices.call_args
        assert call_kwargs.kwargs["sort_by"] == "period"
        assert call_kwargs.kwargs["sort_order"] == "asc"

    def test_sort_by_ptf_value(self, client, mock_service):
        """sort_by=ptf_tl_per_mwh should be accepted."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices?sort_by=ptf_tl_per_mwh")
        assert resp.status_code == 200

    def test_sort_by_status(self, client, mock_service):
        """sort_by=status should be accepted."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices?sort_by=status")
        assert resp.status_code == 200

    def test_sort_by_updated_at(self, client, mock_service):
        """sort_by=updated_at should be accepted."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices?sort_by=updated_at")
        assert resp.status_code == 200

    def test_invalid_sort_field_rejected(self, client, mock_service):
        """Invalid sort_by field should return 400."""
        resp = client.get("/admin/market-prices?sort_by=invalid_field")
        assert resp.status_code == 400

        body = resp.json()
        assert body["detail"]["error_code"] == "INVALID_SORT_FIELD"

    def test_invalid_sort_order_rejected(self, client, mock_service):
        """Invalid sort_order should return 400."""
        resp = client.get("/admin/market-prices?sort_order=random")
        assert resp.status_code == 400

        body = resp.json()
        assert body["detail"]["error_code"] == "INVALID_SORT_ORDER"


# ---------------------------------------------------------------------------
# Tests – Filtering
# ---------------------------------------------------------------------------

class TestListMarketPricesFiltering:
    """Requirement 4.5: Filtering by status, date range, price_type."""

    def test_filter_by_status(self, client, mock_service):
        """status=final should be passed to service."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices?status=final")
        assert resp.status_code == 200

        call_kwargs = mock_service.list_prices.call_args
        assert call_kwargs.kwargs["status"] == "final"

    def test_filter_by_price_type(self, client, mock_service):
        """price_type=PTF should be passed to service."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices?price_type=PTF")
        assert resp.status_code == 200

        call_kwargs = mock_service.list_prices.call_args
        assert call_kwargs.kwargs["price_type"] == "PTF"

    def test_filter_by_period_range(self, client, mock_service):
        """from_period and to_period should be passed to service."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get(
            "/admin/market-prices?from_period=2024-01&to_period=2025-12"
        )
        assert resp.status_code == 200

        call_kwargs = mock_service.list_prices.call_args
        assert call_kwargs.kwargs["period_from"] == "2024-01"
        assert call_kwargs.kwargs["period_to"] == "2025-12"

    def test_no_filters_passes_none(self, client, mock_service):
        """When no filters are provided, None should be passed."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices")
        assert resp.status_code == 200

        call_kwargs = mock_service.list_prices.call_args
        assert call_kwargs.kwargs["price_type"] is None
        assert call_kwargs.kwargs["status"] is None
        assert call_kwargs.kwargs["period_from"] is None
        assert call_kwargs.kwargs["period_to"] is None

    def test_all_filters_combined(self, client, mock_service):
        """All filters can be combined in a single request."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=5, has_more=False, next_cursor=None,
        )

        resp = client.get(
            "/admin/market-prices"
            "?page=1&page_size=20"
            "&sort_by=period&sort_order=desc"
            "&price_type=PTF&status=final"
            "&from_period=2024-01&to_period=2025-12"
        )
        assert resp.status_code == 200

        call_kwargs = mock_service.list_prices.call_args
        assert call_kwargs.kwargs["price_type"] == "PTF"
        assert call_kwargs.kwargs["status"] == "final"
        assert call_kwargs.kwargs["period_from"] == "2024-01"
        assert call_kwargs.kwargs["period_to"] == "2025-12"
        assert call_kwargs.kwargs["sort_by"] == "period"
        assert call_kwargs.kwargs["sort_order"] == "desc"
        assert call_kwargs.kwargs["offset"] == 0
        assert call_kwargs.kwargs["limit"] == 20


# ---------------------------------------------------------------------------
# Tests – Multiple items
# ---------------------------------------------------------------------------

class TestListMarketPricesMultipleItems:
    """Verify correct handling of multiple items in the response."""

    def test_multiple_items_returned(self, client, mock_service):
        """Multiple records should be serialised correctly."""
        from app.market_price_admin_service import PaginatedResult

        records = [
            _make_record(id=1, period="2025-01", ptf_tl_per_mwh=2508.80, status="final"),
            _make_record(id=2, period="2025-02", ptf_tl_per_mwh=2478.28, status="final"),
            _make_record(id=3, period="2025-03", ptf_tl_per_mwh=2183.83, status="provisional"),
        ]
        mock_service.list_prices.return_value = PaginatedResult(
            items=records, total=3, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices")
        body = resp.json()

        assert body["total"] == 3
        assert len(body["items"]) == 3
        assert body["items"][0]["period"] == "2025-01"
        assert body["items"][1]["period"] == "2025-02"
        assert body["items"][2]["period"] == "2025-03"

    def test_empty_result(self, client, mock_service):
        """Empty result should return items=[] with total=0."""
        from app.market_price_admin_service import PaginatedResult

        mock_service.list_prices.return_value = PaginatedResult(
            items=[], total=0, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices")
        body = resp.json()

        assert body["total"] == 0
        assert body["items"] == []
        assert body["page"] == 1


# ---------------------------------------------------------------------------
# Tests – is_locked boolean conversion
# ---------------------------------------------------------------------------

class TestIsLockedConversion:
    """Verify is_locked integer is converted to boolean in response."""

    def test_is_locked_0_becomes_false(self, client, mock_service):
        """is_locked=0 should be serialised as false."""
        from app.market_price_admin_service import PaginatedResult

        record = _make_record(is_locked=0)
        mock_service.list_prices.return_value = PaginatedResult(
            items=[record], total=1, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices")
        assert resp.json()["items"][0]["is_locked"] is False

    def test_is_locked_1_becomes_true(self, client, mock_service):
        """is_locked=1 should be serialised as true."""
        from app.market_price_admin_service import PaginatedResult

        record = _make_record(is_locked=1)
        mock_service.list_prices.return_value = PaginatedResult(
            items=[record], total=1, has_more=False, next_cursor=None,
        )

        resp = client.get("/admin/market-prices")
        assert resp.json()["items"][0]["is_locked"] is True


# ===========================================================================
# POST /admin/market-prices endpoint tests
# ===========================================================================
# Feature: ptf-admin-management, Task 7.2
# Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7


class TestPostMarketPriceResponseFormat:
    """Verify the success response JSON shape matches the design spec."""

    def test_success_response_contains_required_keys(self, client, mock_service):
        """Response must include status, action, period, warnings."""
        from app.market_price_admin_service import UpsertResult

        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True, record=_make_record(),
            warnings=[],
        )

        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": 2508.80},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert body["status"] == "ok"
        assert "action" in body
        assert "period" in body
        assert "warnings" in body

    def test_created_action_for_new_record(self, client, mock_service):
        """action should be 'created' when a new record is inserted."""
        from app.market_price_admin_service import UpsertResult

        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True, record=_make_record(),
            warnings=[],
        )

        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": 2508.80},
        )
        body = resp.json()
        assert body["action"] == "created"
        assert body["period"] == "2025-01"

    def test_updated_action_for_existing_record(self, client, mock_service):
        """action should be 'updated' when an existing record is modified."""
        from app.market_price_admin_service import UpsertResult

        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=False, changed=True, record=_make_record(),
            warnings=[],
        )

        resp = client.post(
            "/admin/market-prices",
            json={
                "period": "2025-01",
                "value": 2600.00,
                "change_reason": "Düzeltme",
            },
        )
        body = resp.json()
        assert body["action"] == "updated"

    def test_warnings_propagated_in_response(self, client, mock_service):
        """Validation warnings should appear in the response."""
        from app.market_price_admin_service import UpsertResult

        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True, record=_make_record(),
            warnings=["Service warning"],
        )

        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": 500.00},
        )
        body = resp.json()
        # Should contain both validator warning (low value) and service warning
        assert isinstance(body["warnings"], list)
        assert len(body["warnings"]) >= 1


# ---------------------------------------------------------------------------
# Tests – Default values
# ---------------------------------------------------------------------------

class TestPostMarketPriceDefaults:
    """Verify default values for optional fields."""

    def test_default_price_type_is_ptf(self, client, mock_service):
        """price_type should default to PTF."""
        from app.market_price_admin_service import UpsertResult

        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True, record=_make_record(),
            warnings=[],
        )

        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": 2508.80},
        )
        assert resp.status_code == 200

        # Verify the normalized input passed to service has price_type=PTF
        call_kwargs = mock_service.upsert_price.call_args
        normalized = call_kwargs.kwargs.get("normalized") or call_kwargs.args[1]
        assert normalized.price_type == "PTF"

    def test_default_status_is_provisional(self, client, mock_service):
        """status should default to provisional."""
        from app.market_price_admin_service import UpsertResult

        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True, record=_make_record(),
            warnings=[],
        )

        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": 2508.80},
        )
        assert resp.status_code == 200

        call_kwargs = mock_service.upsert_price.call_args
        normalized = call_kwargs.kwargs.get("normalized") or call_kwargs.args[1]
        assert normalized.status == "provisional"

    def test_default_force_update_is_false(self, client, mock_service):
        """force_update should default to false."""
        from app.market_price_admin_service import UpsertResult

        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True, record=_make_record(),
            warnings=[],
        )

        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": 2508.80},
        )
        assert resp.status_code == 200

        call_kwargs = mock_service.upsert_price.call_args
        assert call_kwargs.kwargs["force_update"] is False


# ---------------------------------------------------------------------------
# Tests – Validation errors
# ---------------------------------------------------------------------------

class TestPostMarketPriceValidation:
    """Verify input validation and error response format."""

    def test_missing_period_returns_400(self, client, mock_service):
        """Missing period should return 400 with error schema."""
        resp = client.post(
            "/admin/market-prices",
            json={"value": 2508.80},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "INVALID_PERIOD_FORMAT"
        assert body["field"] == "period"
        assert body["row_index"] is None

    def test_missing_value_returns_400(self, client, mock_service):
        """Missing value should return 400 with error schema."""
        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01"},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "VALUE_REQUIRED"
        assert body["field"] == "value"

    def test_invalid_period_format_returns_400(self, client, mock_service):
        """Invalid period format should return 400."""
        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-13", "value": 2508.80},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "INVALID_PERIOD_FORMAT"
        assert body["field"] == "period"

    def test_negative_value_returns_400(self, client, mock_service):
        """Negative value should return 400."""
        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": -100},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "VALUE_OUT_OF_RANGE"
        assert body["field"] == "value"

    def test_zero_value_returns_400(self, client, mock_service):
        """Zero value should return 400."""
        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": 0},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["error_code"] == "VALUE_OUT_OF_RANGE"

    def test_invalid_status_returns_400(self, client, mock_service):
        """Invalid status should return 400."""
        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": 2508.80, "status": "draft"},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "INVALID_STATUS"
        assert body["field"] == "status"

    def test_invalid_json_returns_400(self, client, mock_service):
        """Non-JSON body should return 400."""
        resp = client.post(
            "/admin/market-prices",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "INVALID_JSON"

    def test_error_response_has_standard_schema(self, client, mock_service):
        """All error responses should have the standard error schema."""
        resp = client.post(
            "/admin/market-prices",
            json={"period": "bad", "value": 2508.80},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        # Verify all standard error fields are present
        assert "status" in body
        assert "error_code" in body
        assert "message" in body
        assert "field" in body
        assert "row_index" in body
        assert "details" in body


# ---------------------------------------------------------------------------
# Tests – Service error handling
# ---------------------------------------------------------------------------

class TestPostMarketPriceServiceErrors:
    """Verify service-level errors are returned correctly."""

    def test_period_locked_returns_409(self, client, mock_service):
        """Locked period should return 409 Conflict."""
        from app.market_price_admin_service import UpsertResult, ServiceError, ServiceErrorCode

        mock_service.upsert_price.return_value = UpsertResult(
            success=False, created=False, changed=False,
            error=ServiceError(
                error_code=ServiceErrorCode.PERIOD_LOCKED,
                field="period",
                message="Dönem 2025-01 kilitli, güncellenemez.",
            ),
        )

        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": 2508.80},
        )
        assert resp.status_code == 409

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "PERIOD_LOCKED"
        assert body["field"] == "period"

    def test_final_record_protected_returns_409(self, client, mock_service):
        """Updating final record without force_update should return 409."""
        from app.market_price_admin_service import UpsertResult, ServiceError, ServiceErrorCode

        mock_service.upsert_price.return_value = UpsertResult(
            success=False, created=False, changed=False,
            error=ServiceError(
                error_code=ServiceErrorCode.FINAL_RECORD_PROTECTED,
                field="value",
                message="Final kayıt değiştirmek için force_update gerekli.",
            ),
        )

        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": 2600.00, "status": "final"},
        )
        assert resp.status_code == 409

        body = resp.json()["detail"]
        assert body["error_code"] == "FINAL_RECORD_PROTECTED"

    def test_status_downgrade_forbidden_returns_409(self, client, mock_service):
        """Downgrading final to provisional should return 409."""
        from app.market_price_admin_service import UpsertResult, ServiceError, ServiceErrorCode

        mock_service.upsert_price.return_value = UpsertResult(
            success=False, created=False, changed=False,
            error=ServiceError(
                error_code=ServiceErrorCode.STATUS_DOWNGRADE_FORBIDDEN,
                field="status",
                message="Final kayıt provisional'a düşürülemez.",
            ),
        )

        resp = client.post(
            "/admin/market-prices",
            json={
                "period": "2025-01",
                "value": 2508.80,
                "status": "provisional",
            },
        )
        assert resp.status_code == 409

        body = resp.json()["detail"]
        assert body["error_code"] == "STATUS_DOWNGRADE_FORBIDDEN"

    def test_change_reason_required_returns_400(self, client, mock_service):
        """Missing change_reason for update should return 400."""
        from app.market_price_admin_service import UpsertResult, ServiceError, ServiceErrorCode

        mock_service.upsert_price.return_value = UpsertResult(
            success=False, created=False, changed=False,
            error=ServiceError(
                error_code=ServiceErrorCode.CHANGE_REASON_REQUIRED,
                field="change_reason",
                message="Güncelleme için değişiklik nedeni zorunludur.",
            ),
        )

        resp = client.post(
            "/admin/market-prices",
            json={"period": "2025-01", "value": 2600.00},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["error_code"] == "CHANGE_REASON_REQUIRED"


# ---------------------------------------------------------------------------
# Tests – force_update and full body
# ---------------------------------------------------------------------------

class TestPostMarketPriceForceUpdate:
    """Verify force_update flag is passed to service."""

    def test_force_update_true_passed_to_service(self, client, mock_service):
        """force_update=true should be passed to the service."""
        from app.market_price_admin_service import UpsertResult

        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=False, changed=True, record=_make_record(),
            warnings=[],
        )

        resp = client.post(
            "/admin/market-prices",
            json={
                "period": "2025-01",
                "value": 2600.00,
                "status": "final",
                "force_update": True,
                "change_reason": "Düzeltme",
            },
        )
        assert resp.status_code == 200

        call_kwargs = mock_service.upsert_price.call_args
        assert call_kwargs.kwargs["force_update"] is True

    def test_full_body_fields_passed_correctly(self, client, mock_service):
        """All body fields should be passed to the service correctly."""
        from app.market_price_admin_service import UpsertResult

        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True, record=_make_record(),
            warnings=[],
        )

        resp = client.post(
            "/admin/market-prices",
            json={
                "period": "2025-01",
                "value": 2508.80,
                "price_type": "PTF",
                "status": "final",
                "source_note": "EPİAŞ manuel giriş",
                "change_reason": "Ay sonu kesinleşme",
                "force_update": False,
            },
        )
        assert resp.status_code == 200

        call_kwargs = mock_service.upsert_price.call_args
        normalized = call_kwargs.kwargs.get("normalized") or call_kwargs.args[1]
        assert normalized.period == "2025-01"
        assert normalized.price_type == "PTF"
        assert normalized.status == "final"
        assert call_kwargs.kwargs["change_reason"] == "Ay sonu kesinleşme"
        assert call_kwargs.kwargs["source"] == "epias_manual"
        assert call_kwargs.kwargs["force_update"] is False


# ===========================================================================
# POST /admin/market-prices/import/preview endpoint tests
# ===========================================================================
# Feature: ptf-admin-management, Task 7.3
# Requirements: 6.1, 6.2, 6.3, 6.4


@pytest.fixture()
def mock_importer():
    """Patch the bulk importer singleton returned by get_bulk_importer."""
    with patch(
        "app.bulk_importer.get_bulk_importer"
    ) as factory:
        imp = MagicMock()
        factory.return_value = imp
        yield imp


@pytest.fixture()
def preview_client(mock_service, mock_importer):
    """
    Create a TestClient with DB, admin-key, and importer dependencies overridden.
    """
    with patch.dict("os.environ", {"ADMIN_API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db

        yield TestClient(fastapi_app)

        fastapi_app.dependency_overrides.clear()


def _make_import_row(row_number=1, period="2025-01", value=2508.80, status="final"):
    """Create a mock ImportRow."""
    from app.bulk_importer import ImportRow
    from app.market_price_validator import ValidationResult
    row = ImportRow(
        row_number=row_number,
        period=period,
        value=value,
        status=status,
    )
    row.validation_result = ValidationResult(is_valid=True, errors=[], warnings=[])
    return row


# ---------------------------------------------------------------------------
# Tests – Response structure
# ---------------------------------------------------------------------------

class TestImportPreviewResponseFormat:
    """Verify the preview response JSON shape matches the design spec."""

    def test_response_contains_required_keys(self, preview_client, mock_importer):
        """Response must include status and preview with all count fields."""
        from app.bulk_importer import ImportPreview, ImportRow

        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.preview.return_value = ImportPreview(
            total_rows=1, valid_rows=1, invalid_rows=0,
            new_records=1, updates=0, unchanged=0,
            final_conflicts=0, rows=[], errors=[],
        )

        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
            data={"price_type": "PTF", "force_update": "false"},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert body["status"] == "ok"
        assert "preview" in body

        preview = body["preview"]
        required_keys = {
            "total_rows", "valid_rows", "invalid_rows",
            "new_records", "updates", "unchanged",
            "final_conflicts", "errors",
        }
        assert required_keys.issubset(preview.keys())

    def test_preview_counts_are_correct(self, preview_client, mock_importer):
        """Verify preview counts match the importer output."""
        from app.bulk_importer import ImportPreview

        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.preview.return_value = ImportPreview(
            total_rows=26, valid_rows=25, invalid_rows=1,
            new_records=10, updates=15, unchanged=0,
            final_conflicts=3, rows=[],
            errors=[{"row": 5, "field": "period", "error": "Invalid format"}],
        )

        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
            data={"price_type": "PTF", "force_update": "false"},
        )
        body = resp.json()
        preview = body["preview"]

        assert preview["total_rows"] == 26
        assert preview["valid_rows"] == 25
        assert preview["invalid_rows"] == 1
        assert preview["new_records"] == 10
        assert preview["updates"] == 15
        assert preview["unchanged"] == 0
        assert preview["final_conflicts"] == 3
        assert len(preview["errors"]) == 1
        assert preview["errors"][0]["row"] == 5


# ---------------------------------------------------------------------------
# Tests – File type detection
# ---------------------------------------------------------------------------

class TestImportPreviewFileTypeDetection:
    """Verify file type is detected from filename extension."""

    def test_csv_file_detected(self, preview_client, mock_importer):
        """CSV file should be parsed with parse_csv."""
        from app.bulk_importer import ImportPreview

        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.preview.return_value = ImportPreview(
            total_rows=1, valid_rows=1, invalid_rows=0,
            new_records=1, updates=0, unchanged=0,
            final_conflicts=0, rows=[], errors=[],
        )

        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        assert resp.status_code == 200
        mock_importer.parse_csv.assert_called_once()
        mock_importer.parse_json.assert_not_called()

    def test_json_file_detected(self, preview_client, mock_importer):
        """JSON file should be parsed with parse_json."""
        from app.bulk_importer import ImportPreview
        import json

        mock_importer.parse_json.return_value = [_make_import_row()]
        mock_importer.preview.return_value = ImportPreview(
            total_rows=1, valid_rows=1, invalid_rows=0,
            new_records=1, updates=0, unchanged=0,
            final_conflicts=0, rows=[], errors=[],
        )

        json_content = json.dumps([{"period": "2025-01", "value": 2508.80, "status": "final"}])
        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.json", json_content.encode(), "application/json")},
        )
        assert resp.status_code == 200
        mock_importer.parse_json.assert_called_once()
        mock_importer.parse_csv.assert_not_called()

    def test_unsupported_file_type_returns_400(self, preview_client, mock_importer):
        """Unsupported file extension should return 400."""
        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.xlsx", b"some content", "application/octet-stream")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "PARSE_ERROR"
        assert body["field"] == "file"


# ---------------------------------------------------------------------------
# Tests – Empty file handling
# ---------------------------------------------------------------------------

class TestImportPreviewEmptyFile:
    """Verify empty file is rejected with appropriate error."""

    def test_empty_file_returns_400(self, preview_client, mock_importer):
        """Empty file should return 400 with EMPTY_FILE error code."""
        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"", "text/csv")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "EMPTY_FILE"
        assert body["field"] == "file"

    def test_whitespace_only_file_returns_400(self, preview_client, mock_importer):
        """File with only whitespace should return 400 with EMPTY_FILE error code."""
        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"   \n  \n  ", "text/csv")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["error_code"] == "EMPTY_FILE"


# ---------------------------------------------------------------------------
# Tests – ParseError handling
# ---------------------------------------------------------------------------

class TestImportPreviewParseError:
    """Verify ParseError from importer is handled correctly."""

    def test_csv_parse_error_returns_400(self, preview_client, mock_importer):
        """ParseError from CSV parsing should return 400."""
        from app.bulk_importer import ParseError

        mock_importer.parse_csv.side_effect = ParseError("CSV baslik satiri bulunamadi.")

        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"bad content", "text/csv")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "PARSE_ERROR"
        assert "baslik" in body["message"].lower() or "CSV" in body["message"]

    def test_json_parse_error_returns_400(self, preview_client, mock_importer):
        """ParseError from JSON parsing should return 400."""
        from app.bulk_importer import ParseError

        mock_importer.parse_json.side_effect = ParseError("JSON parse hatasi: ...")

        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.json", b"not json", "application/json")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["error_code"] == "PARSE_ERROR"

    def test_parse_error_with_row_errors(self, preview_client, mock_importer):
        """ParseError with row_errors should include them in details."""
        from app.bulk_importer import ParseError

        row_errors = [{"row": 1, "field": "value", "error": "Invalid decimal"}]
        mock_importer.parse_csv.side_effect = ParseError(
            "Validation failed", row_errors=row_errors
        )

        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"period,value,status\nbad", "text/csv")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["error_code"] == "PARSE_ERROR"
        assert "row_errors" in body["details"]
        assert len(body["details"]["row_errors"]) == 1


# ---------------------------------------------------------------------------
# Tests – Default parameters
# ---------------------------------------------------------------------------

class TestImportPreviewDefaults:
    """Verify default values for form fields."""

    def test_default_price_type_is_ptf(self, preview_client, mock_importer):
        """price_type should default to PTF."""
        from app.bulk_importer import ImportPreview

        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.preview.return_value = ImportPreview(
            total_rows=1, valid_rows=1, invalid_rows=0,
            new_records=1, updates=0, unchanged=0,
            final_conflicts=0, rows=[], errors=[],
        )

        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        assert resp.status_code == 200

        # Verify preview was called with price_type=PTF
        call_kwargs = mock_importer.preview.call_args
        assert call_kwargs.kwargs["price_type"] == "PTF"

    def test_default_force_update_is_false(self, preview_client, mock_importer):
        """force_update should default to false."""
        from app.bulk_importer import ImportPreview

        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.preview.return_value = ImportPreview(
            total_rows=1, valid_rows=1, invalid_rows=0,
            new_records=1, updates=0, unchanged=0,
            final_conflicts=0, rows=[], errors=[],
        )

        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        assert resp.status_code == 200

        call_kwargs = mock_importer.preview.call_args
        assert call_kwargs.kwargs["force_update"] is False

    def test_force_update_true_passed(self, preview_client, mock_importer):
        """force_update=true should be passed to preview."""
        from app.bulk_importer import ImportPreview

        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.preview.return_value = ImportPreview(
            total_rows=1, valid_rows=1, invalid_rows=0,
            new_records=1, updates=0, unchanged=0,
            final_conflicts=0, rows=[], errors=[],
        )

        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
            data={"force_update": "true"},
        )
        assert resp.status_code == 200

        call_kwargs = mock_importer.preview.call_args
        assert call_kwargs.kwargs["force_update"] is True


# ---------------------------------------------------------------------------
# Tests – Preview with errors in data
# ---------------------------------------------------------------------------

class TestImportPreviewWithErrors:
    """Verify preview correctly reports validation errors."""

    def test_preview_with_mixed_valid_invalid_rows(self, preview_client, mock_importer):
        """Preview should report both valid and invalid row counts."""
        from app.bulk_importer import ImportPreview

        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.preview.return_value = ImportPreview(
            total_rows=5, valid_rows=3, invalid_rows=2,
            new_records=2, updates=1, unchanged=0,
            final_conflicts=0, rows=[],
            errors=[
                {"row": 2, "field": "period", "error": "Invalid format"},
                {"row": 4, "field": "value", "error": "Negative value"},
            ],
        )

        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        body = resp.json()
        preview = body["preview"]

        assert preview["total_rows"] == 5
        assert preview["valid_rows"] == 3
        assert preview["invalid_rows"] == 2
        assert len(preview["errors"]) == 2

    def test_preview_with_final_conflicts(self, preview_client, mock_importer):
        """Preview should report final_conflicts count."""
        from app.bulk_importer import ImportPreview

        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.preview.return_value = ImportPreview(
            total_rows=10, valid_rows=10, invalid_rows=0,
            new_records=5, updates=2, unchanged=0,
            final_conflicts=3, rows=[],
            errors=[
                {"row": 3, "field": "value", "error": "Final kayit degistirmek icin force_update gerekli.", "error_code": "FINAL_RECORD_PROTECTED"},
                {"row": 6, "field": "value", "error": "Final kayit degistirmek icin force_update gerekli.", "error_code": "FINAL_RECORD_PROTECTED"},
                {"row": 9, "field": "value", "error": "Final kayit degistirmek icin force_update gerekli.", "error_code": "FINAL_RECORD_PROTECTED"},
            ],
        )

        resp = preview_client.post(
            "/admin/market-prices/import/preview",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        body = resp.json()
        preview = body["preview"]

        assert preview["final_conflicts"] == 3
        assert len(preview["errors"]) == 3


# ===========================================================================
# POST /admin/market-prices/import/apply endpoint tests
# ===========================================================================
# Feature: ptf-admin-management, Task 7.4
# Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8


@pytest.fixture()
def apply_client(mock_service, mock_importer):
    """
    Create a TestClient with DB, admin-key, and importer dependencies overridden.
    """
    with patch.dict("os.environ", {"ADMIN_API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db

        yield TestClient(fastapi_app)

        fastapi_app.dependency_overrides.clear()


def _make_import_result(success=True, accepted=1, rejected=0, rejected_rows=None):
    """Create a mock ImportResult."""
    from app.bulk_importer import ImportResult
    return ImportResult(
        success=success,
        accepted_count=accepted,
        rejected_count=rejected,
        rejected_rows=rejected_rows or [],
    )


# ---------------------------------------------------------------------------
# Tests – Response structure
# ---------------------------------------------------------------------------

class TestImportApplyResponseFormat:
    """Verify the apply response JSON shape matches the design spec."""

    def test_response_contains_required_keys(self, apply_client, mock_importer):
        """Response must include status and result with all required fields."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result()

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
            data={"price_type": "PTF", "force_update": "false", "strict_mode": "false"},
        )
        assert resp.status_code == 200

        body = resp.json()
        assert body["status"] == "ok"
        assert "result" in body

        result = body["result"]
        required_keys = {"success", "imported_count", "skipped_count", "error_count", "details"}
        assert required_keys.issubset(result.keys())

    def test_result_counts_are_correct(self, apply_client, mock_importer):
        """Verify result counts match the importer output."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result(
            success=True, accepted=25, rejected=1,
            rejected_rows=[{
                "row_index": 5,
                "error_code": "INVALID_PERIOD_FORMAT",
                "field": "period",
                "message": "Geçersiz dönem formatı.",
            }],
        )

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
            data={"price_type": "PTF", "force_update": "false", "strict_mode": "false"},
        )
        body = resp.json()
        result = body["result"]

        assert result["success"] is True
        assert result["imported_count"] == 25
        assert result["skipped_count"] == 1
        assert result["error_count"] == 1
        assert len(result["details"]) == 1
        assert result["details"][0]["row_index"] == 5
        assert result["details"][0]["error_code"] == "INVALID_PERIOD_FORMAT"

    def test_rejected_row_has_required_fields(self, apply_client, mock_importer):
        """Each rejected row should have row_index, error_code, field, message."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result(
            success=False, accepted=0, rejected=1,
            rejected_rows=[{
                "row_index": 3,
                "error_code": "VALUE_OUT_OF_RANGE",
                "field": "value",
                "message": "PTF değeri 0'dan büyük olmalıdır.",
            }],
        )

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        body = resp.json()
        detail = body["result"]["details"][0]

        assert "row_index" in detail
        assert "error_code" in detail
        assert "field" in detail
        assert "message" in detail


# ---------------------------------------------------------------------------
# Tests – File type detection
# ---------------------------------------------------------------------------

class TestImportApplyFileTypeDetection:
    """Verify file type is detected from filename extension."""

    def test_csv_file_detected(self, apply_client, mock_importer):
        """CSV file should be parsed with parse_csv."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result()

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        assert resp.status_code == 200
        mock_importer.parse_csv.assert_called_once()
        mock_importer.parse_json.assert_not_called()

    def test_json_file_detected(self, apply_client, mock_importer):
        """JSON file should be parsed with parse_json."""
        import json as json_mod

        mock_importer.parse_json.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result()

        json_content = json_mod.dumps([{"period": "2025-01", "value": 2508.80, "status": "final"}])
        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.json", json_content.encode(), "application/json")},
        )
        assert resp.status_code == 200
        mock_importer.parse_json.assert_called_once()
        mock_importer.parse_csv.assert_not_called()

    def test_unsupported_file_type_returns_400(self, apply_client, mock_importer):
        """Unsupported file extension should return 400."""
        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.xlsx", b"some content", "application/octet-stream")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "PARSE_ERROR"
        assert body["field"] == "file"


# ---------------------------------------------------------------------------
# Tests – Empty file handling
# ---------------------------------------------------------------------------

class TestImportApplyEmptyFile:
    """Verify empty file is rejected with appropriate error."""

    def test_empty_file_returns_400(self, apply_client, mock_importer):
        """Empty file should return 400 with EMPTY_FILE error code."""
        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"", "text/csv")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "EMPTY_FILE"
        assert body["field"] == "file"

    def test_whitespace_only_file_returns_400(self, apply_client, mock_importer):
        """File with only whitespace should return 400 with EMPTY_FILE error code."""
        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"   \n  \n  ", "text/csv")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["error_code"] == "EMPTY_FILE"


# ---------------------------------------------------------------------------
# Tests – ParseError handling
# ---------------------------------------------------------------------------

class TestImportApplyParseError:
    """Verify ParseError from importer is handled correctly."""

    def test_csv_parse_error_returns_400(self, apply_client, mock_importer):
        """ParseError from CSV parsing should return 400."""
        from app.bulk_importer import ParseError

        mock_importer.parse_csv.side_effect = ParseError("CSV baslik satiri bulunamadi.")

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"bad content", "text/csv")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["status"] == "error"
        assert body["error_code"] == "PARSE_ERROR"
        assert "baslik" in body["message"].lower() or "CSV" in body["message"]

    def test_json_parse_error_returns_400(self, apply_client, mock_importer):
        """ParseError from JSON parsing should return 400."""
        from app.bulk_importer import ParseError

        mock_importer.parse_json.side_effect = ParseError("JSON parse hatasi: ...")

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.json", b"not json", "application/json")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["error_code"] == "PARSE_ERROR"

    def test_parse_error_with_row_errors(self, apply_client, mock_importer):
        """ParseError with row_errors should include them in details."""
        from app.bulk_importer import ParseError

        row_errors = [{"row": 1, "field": "value", "error": "Invalid decimal"}]
        mock_importer.parse_csv.side_effect = ParseError(
            "Validation failed", row_errors=row_errors
        )

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\nbad", "text/csv")},
        )
        assert resp.status_code == 400

        body = resp.json()["detail"]
        assert body["error_code"] == "PARSE_ERROR"
        assert "row_errors" in body["details"]
        assert len(body["details"]["row_errors"]) == 1


# ---------------------------------------------------------------------------
# Tests – Default parameters
# ---------------------------------------------------------------------------

class TestImportApplyDefaults:
    """Verify default values for form fields."""

    def test_default_price_type_is_ptf(self, apply_client, mock_importer):
        """price_type should default to PTF."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result()

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        assert resp.status_code == 200

        call_kwargs = mock_importer.apply.call_args
        assert call_kwargs.kwargs["price_type"] == "PTF"

    def test_default_force_update_is_false(self, apply_client, mock_importer):
        """force_update should default to false."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result()

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        assert resp.status_code == 200

        call_kwargs = mock_importer.apply.call_args
        assert call_kwargs.kwargs["force_update"] is False

    def test_default_strict_mode_is_false(self, apply_client, mock_importer):
        """strict_mode should default to false."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result()

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        assert resp.status_code == 200

        call_kwargs = mock_importer.apply.call_args
        assert call_kwargs.kwargs["strict_mode"] is False

    def test_force_update_true_passed(self, apply_client, mock_importer):
        """force_update=true should be passed to apply."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result()

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
            data={"force_update": "true"},
        )
        assert resp.status_code == 200

        call_kwargs = mock_importer.apply.call_args
        assert call_kwargs.kwargs["force_update"] is True

    def test_strict_mode_true_passed(self, apply_client, mock_importer):
        """strict_mode=true should be passed to apply."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result()

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
            data={"strict_mode": "true"},
        )
        assert resp.status_code == 200

        call_kwargs = mock_importer.apply.call_args
        assert call_kwargs.kwargs["strict_mode"] is True


# ---------------------------------------------------------------------------
# Tests – Strict mode behavior
# ---------------------------------------------------------------------------

class TestImportApplyStrictMode:
    """Verify strict mode behavior in the response."""

    def test_strict_mode_failure_returns_success_false(self, apply_client, mock_importer):
        """When strict_mode fails, result.success should be false."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result(
            success=False, accepted=0, rejected=5,
            rejected_rows=[{
                "row_index": 3,
                "error_code": "INVALID_PERIOD_FORMAT",
                "field": "period",
                "message": "Geçersiz dönem formatı.",
            }],
        )

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
            data={"strict_mode": "true"},
        )
        assert resp.status_code == 200

        body = resp.json()
        result = body["result"]
        assert result["success"] is False
        assert result["imported_count"] == 0
        assert result["skipped_count"] == 5

    def test_default_mode_partial_success(self, apply_client, mock_importer):
        """In default mode, valid rows are imported and invalid rows are skipped."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result(
            success=False, accepted=3, rejected=2,
            rejected_rows=[
                {"row_index": 2, "error_code": "INVALID_PERIOD_FORMAT", "field": "period", "message": "Bad period"},
                {"row_index": 4, "error_code": "VALUE_OUT_OF_RANGE", "field": "value", "message": "Bad value"},
            ],
        )

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        assert resp.status_code == 200

        body = resp.json()
        result = body["result"]
        assert result["imported_count"] == 3
        assert result["skipped_count"] == 2
        assert result["error_count"] == 2
        assert len(result["details"]) == 2


# ---------------------------------------------------------------------------
# Tests – All form parameters combined
# ---------------------------------------------------------------------------

class TestImportApplyAllParams:
    """Verify all form parameters are passed correctly to the importer."""

    def test_all_params_passed_to_apply(self, apply_client, mock_importer):
        """All form parameters should be forwarded to importer.apply()."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result()

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
            data={
                "price_type": "PTF",
                "force_update": "true",
                "strict_mode": "true",
            },
        )
        assert resp.status_code == 200

        call_kwargs = mock_importer.apply.call_args
        assert call_kwargs.kwargs["price_type"] == "PTF"
        assert call_kwargs.kwargs["force_update"] is True
        assert call_kwargs.kwargs["strict_mode"] is True
        assert call_kwargs.kwargs["updated_by"] == "admin"


# ---------------------------------------------------------------------------
# Tests – Success with zero errors
# ---------------------------------------------------------------------------

class TestImportApplyFullSuccess:
    """Verify fully successful import."""

    def test_all_rows_accepted(self, apply_client, mock_importer):
        """When all rows are valid, success=true and error_count=0."""
        mock_importer.parse_csv.return_value = [_make_import_row()]
        mock_importer.apply.return_value = _make_import_result(
            success=True, accepted=10, rejected=0,
        )

        resp = apply_client.post(
            "/admin/market-prices/import/apply",
            files={"file": ("data.csv", b"period,value,status\n2025-01,2508.80,final", "text/csv")},
        )
        assert resp.status_code == 200

        body = resp.json()
        result = body["result"]
        assert result["success"] is True
        assert result["imported_count"] == 10
        assert result["skipped_count"] == 0
        assert result["error_count"] == 0
        assert result["details"] == []
