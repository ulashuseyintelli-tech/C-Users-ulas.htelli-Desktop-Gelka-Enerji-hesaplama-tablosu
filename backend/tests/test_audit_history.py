"""
Unit tests for Audit History feature.

Feature: audit-history
Tests: PriceChangeHistory model, _write_history(), get_history(), API endpoint.
Requirements: 1.1, 1.2, 1.3, 1.5, 2.1-2.4, 3.1-3.5
"""

import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_price_record(**overrides):
    """Create a mock MarketReferencePrice record."""
    defaults = dict(
        id=1,
        period="2025-01",
        price_type="PTF",
        ptf_tl_per_mwh=2508.80,
        status="final",
        source="epias_manual",
        captured_at=datetime(2025, 1, 15, 10, 0, 0),
        change_reason="İlk kayıt",
        updated_by="admin",
        is_locked=0,
        created_at=datetime(2025, 1, 15, 10, 0, 0),
        updated_at=datetime(2025, 1, 15, 12, 0, 0),
    )
    defaults.update(overrides)
    rec = MagicMock()
    for k, v in defaults.items():
        setattr(rec, k, v)
    return rec


def _make_history_record(**overrides):
    """Create a mock PriceChangeHistory record."""
    defaults = dict(
        id=1,
        price_record_id=1,
        price_type="PTF",
        period="2025-01",
        action="INSERT",
        old_value=None,
        new_value=2508.80,
        old_status=None,
        new_status="provisional",
        change_reason=None,
        updated_by="admin",
        source="epias_manual",
        created_at=datetime(2025, 1, 15, 10, 0, 0),
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
    with patch(
        "app.market_price_admin_service.get_market_price_admin_service"
    ) as factory:
        svc = MagicMock()
        factory.return_value = svc
        yield svc


@pytest.fixture()
def client(mock_service):
    with patch.dict("os.environ", {"ADMIN_API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db
        yield TestClient(fastapi_app)
        fastapi_app.dependency_overrides.clear()


# ===========================================================================
# 1. PriceChangeHistory Model Tests (Requirement 2.1-2.4)
# ===========================================================================

class TestPriceChangeHistoryModel:
    """Verify PriceChangeHistory SQLAlchemy model exists and has correct columns."""

    def test_model_exists_and_registered_with_base(self):
        """Requirement 2.1: Model SHALL be registered with Base."""
        from app.database import PriceChangeHistory, Base
        assert PriceChangeHistory.__tablename__ == "price_change_history"
        assert "price_change_history" in Base.metadata.tables

    def test_model_has_required_columns(self):
        """Requirement 2.2: Model SHALL have all required columns."""
        from app.database import PriceChangeHistory
        columns = {c.name for c in PriceChangeHistory.__table__.columns}
        required = {
            "id", "price_record_id", "price_type", "period",
            "action", "old_value", "new_value", "old_status", "new_status",
            "change_reason", "updated_by", "source", "created_at",
        }
        assert required.issubset(columns), f"Missing columns: {required - columns}"

    def test_price_record_id_is_foreign_key(self):
        """Requirement 2.3: price_record_id SHALL be FK to market_reference_prices.id."""
        from app.database import PriceChangeHistory
        col = PriceChangeHistory.__table__.c.price_record_id
        fk_targets = [fk.target_fullname for fk in col.foreign_keys]
        assert "market_reference_prices.id" in fk_targets

    def test_old_value_and_old_status_nullable(self):
        """INSERT records have NULL old_value/old_status."""
        from app.database import PriceChangeHistory
        assert PriceChangeHistory.__table__.c.old_value.nullable is True
        assert PriceChangeHistory.__table__.c.old_status.nullable is True

    def test_new_value_and_new_status_not_nullable(self):
        """new_value and new_status are always required."""
        from app.database import PriceChangeHistory
        assert PriceChangeHistory.__table__.c.new_value.nullable is False
        assert PriceChangeHistory.__table__.c.new_status.nullable is False


# ===========================================================================
# 2. _write_history() Service Tests (Requirement 1.1, 1.2, 1.5)
# ===========================================================================

class TestWriteHistory:
    """Test _write_history() best-effort behavior."""

    def test_write_history_insert_creates_record(self):
        """Requirement 1.1: INSERT action creates history with NULL old values."""
        from app.market_price_admin_service import MarketPriceAdminService
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        record = _make_price_record()

        service._write_history(
            db=mock_db, record=record, action="INSERT",
            old_value=None, new_value=2508.80,
            old_status=None, new_status="provisional",
            change_reason=None, updated_by="admin", source="epias_manual",
        )

        assert mock_db.add.called
        history = mock_db.add.call_args[0][0]
        assert history.action == "INSERT"
        assert history.old_value is None
        assert history.old_status is None
        assert history.new_value == 2508.80
        assert history.new_status == "provisional"
        assert history.price_record_id == record.id
        mock_db.commit.assert_called_once()

    def test_write_history_update_creates_record_with_old_values(self):
        """Requirement 1.2: UPDATE action stores old and new values."""
        from app.market_price_admin_service import MarketPriceAdminService
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        record = _make_price_record()

        service._write_history(
            db=mock_db, record=record, action="UPDATE",
            old_value=2508.80, new_value=3100.50,
            old_status="provisional", new_status="final",
            change_reason="EPİAŞ kesinleşmiş veri", updated_by="admin",
            source="epias_manual",
        )

        history = mock_db.add.call_args[0][0]
        assert history.action == "UPDATE"
        assert history.old_value == 2508.80
        assert history.new_value == 3100.50
        assert history.old_status == "provisional"
        assert history.new_status == "final"
        assert history.change_reason == "EPİAŞ kesinleşmiş veri"

    def test_write_history_failure_does_not_raise(self):
        """Requirement 1.5: History write failure SHALL NOT fail parent upsert."""
        from app.market_price_admin_service import MarketPriceAdminService
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        mock_db.commit.side_effect = Exception("DB error")
        record = _make_price_record()

        # Should not raise
        service._write_history(
            db=mock_db, record=record, action="INSERT",
            old_value=None, new_value=2508.80,
            old_status=None, new_status="provisional",
            change_reason=None, updated_by="admin", source="epias_manual",
        )
        mock_db.rollback.assert_called_once()


# ===========================================================================
# 3. Upsert Integration Tests (Requirement 1.1, 1.2, 1.3)
# ===========================================================================

class TestUpsertHistoryIntegration:
    """Verify upsert calls _write_history correctly."""

    def test_insert_triggers_history_write(self):
        """Requirement 1.1: Successful insert SHALL write INSERT history."""
        from app.market_price_admin_service import MarketPriceAdminService
        from app.market_price_validator import NormalizedMarketPriceInput

        service = MarketPriceAdminService()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        normalized = NormalizedMarketPriceInput(
            period="2025-01", value=Decimal("2508.80"),
            status="provisional", price_type="PTF",
        )

        result = service.upsert_price(
            db=mock_db, normalized=normalized,
            updated_by="admin", source="epias_manual",
        )

        assert result.success is True
        assert result.created is True
        # db.add called twice: once for price record, once for history
        assert mock_db.add.call_count == 2
        history = mock_db.add.call_args_list[1][0][0]
        from app.database import PriceChangeHistory
        assert isinstance(history, PriceChangeHistory)
        assert history.action == "INSERT"

    def test_update_triggers_history_write(self):
        """Requirement 1.2: Successful update SHALL write UPDATE history."""
        from app.market_price_admin_service import MarketPriceAdminService
        from app.market_price_validator import NormalizedMarketPriceInput

        service = MarketPriceAdminService()
        existing = _make_price_record(
            ptf_tl_per_mwh=2508.80, status="provisional", is_locked=0,
        )
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        normalized = NormalizedMarketPriceInput(
            period="2025-01", value=Decimal("3100.50"),
            status="final", price_type="PTF",
        )

        result = service.upsert_price(
            db=mock_db, normalized=normalized,
            updated_by="admin", source="epias_manual",
            change_reason="EPİAŞ kesinleşmiş veri",
        )

        assert result.success is True
        assert result.changed is True
        # db.add called once for history (update modifies existing, doesn't add)
        assert mock_db.add.call_count == 1
        history = mock_db.add.call_args[0][0]
        from app.database import PriceChangeHistory
        assert isinstance(history, PriceChangeHistory)
        assert history.action == "UPDATE"
        assert history.old_value == 2508.80
        assert history.new_value == 3100.50

    def test_noop_does_not_trigger_history(self):
        """Requirement 1.3: No-op SHALL NOT write history."""
        from app.market_price_admin_service import MarketPriceAdminService
        from app.market_price_validator import NormalizedMarketPriceInput

        service = MarketPriceAdminService()
        existing = _make_price_record(
            ptf_tl_per_mwh=2508.80, status="provisional", is_locked=0,
        )
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        normalized = NormalizedMarketPriceInput(
            period="2025-01", value=Decimal("2508.80"),
            status="provisional", price_type="PTF",
        )

        result = service.upsert_price(
            db=mock_db, normalized=normalized,
            updated_by="admin", source="epias_manual",
        )

        assert result.success is True
        assert result.changed is False
        # No db.add calls at all (no-op = no history)
        assert mock_db.add.call_count == 0


# ===========================================================================
# 4. get_history() Service Tests (Requirement 3.1, 3.3, 3.4)
# ===========================================================================

class TestGetHistory:
    """Test get_history() service method."""

    def test_returns_none_when_record_not_found(self):
        """Requirement 3.3: No record → None (→ 404 at API)."""
        from app.market_price_admin_service import MarketPriceAdminService
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = service.get_history(mock_db, period="2099-01", price_type="PTF")
        assert result is None

    def test_returns_empty_list_when_no_history(self):
        """Requirement 3.4: Record exists but no history → empty list."""
        from app.market_price_admin_service import MarketPriceAdminService
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        # First query (price record) returns something
        mock_db.query.return_value.filter.return_value.first.return_value = _make_price_record()
        # Second query (history) returns empty
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = service.get_history(mock_db, period="2025-01", price_type="PTF")
        assert result == []

    def test_returns_history_records(self):
        """Requirement 3.1: Returns history ordered by created_at DESC."""
        from app.market_price_admin_service import MarketPriceAdminService
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = _make_price_record()

        h1 = _make_history_record(id=1, created_at=datetime(2025, 1, 10))
        h2 = _make_history_record(id=2, created_at=datetime(2025, 1, 15))
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [h2, h1]

        result = service.get_history(mock_db, period="2025-01", price_type="PTF")
        assert len(result) == 2
        assert result[0].id == 2  # newest first


# ===========================================================================
# 5. API Endpoint Tests (Requirement 3.1-3.5)
# ===========================================================================

class TestHistoryApiEndpoint:
    """Test GET /admin/market-prices/history endpoint."""

    def test_200_with_history(self, client, mock_service):
        """Requirement 3.1: Returns history for valid period+price_type."""
        h1 = _make_history_record(
            id=1, action="INSERT", new_value=2508.80, new_status="provisional",
            created_at=datetime(2025, 1, 10, 10, 0, 0),
        )
        mock_service.get_history.return_value = [h1]

        resp = client.get("/admin/market-prices/history?period=2025-01&price_type=PTF")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["period"] == "2025-01"
        assert body["price_type"] == "PTF"
        assert len(body["history"]) == 1
        assert body["history"][0]["action"] == "INSERT"
        assert body["history"][0]["new_value"] == 2508.80

    def test_200_empty_history(self, client, mock_service):
        """Requirement 3.4: Record exists but no history → 200 with empty array."""
        mock_service.get_history.return_value = []

        resp = client.get("/admin/market-prices/history?period=2025-01")
        assert resp.status_code == 200
        body = resp.json()
        assert body["history"] == []

    def test_404_record_not_found(self, client, mock_service):
        """Requirement 3.3: No record → 404."""
        mock_service.get_history.return_value = None

        resp = client.get("/admin/market-prices/history?period=2099-01")
        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["error_code"] == "RECORD_NOT_FOUND"

    def test_400_invalid_period_format(self, client, mock_service):
        """Invalid period format → 400."""
        resp = client.get("/admin/market-prices/history?period=invalid")
        assert resp.status_code == 400
        body = resp.json()
        assert body["detail"]["error_code"] == "INVALID_PERIOD"

    def test_default_price_type_is_ptf(self, client, mock_service):
        """Requirement 3.2: Default price_type is PTF."""
        mock_service.get_history.return_value = []

        resp = client.get("/admin/market-prices/history?period=2025-01")
        assert resp.status_code == 200
        call_kwargs = mock_service.get_history.call_args
        assert call_kwargs.kwargs.get("price_type", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else "PTF") == "PTF"

    def test_history_response_fields(self, client, mock_service):
        """Verify all required fields in history response items."""
        h = _make_history_record(
            id=42, action="UPDATE",
            old_value=2508.80, new_value=3100.50,
            old_status="provisional", new_status="final",
            change_reason="EPİAŞ güncelleme",
            updated_by="admin", source="epias_manual",
            created_at=datetime(2025, 1, 15, 12, 0, 0),
        )
        mock_service.get_history.return_value = [h]

        resp = client.get("/admin/market-prices/history?period=2025-01")
        item = resp.json()["history"][0]
        required_fields = {
            "id", "action", "old_value", "new_value",
            "old_status", "new_status", "change_reason",
            "updated_by", "source", "created_at",
        }
        assert required_fields.issubset(item.keys())
