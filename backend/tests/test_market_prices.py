"""
Unit tests for market_prices.py backward compatibility updates.

Feature: ptf-admin-management, Task 9.1
Tests: get_market_prices() status field support, upsert_market_prices() new fields,
       backward compatibility (null status = final).
Requirements: 1.6
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import asdict

import sys
import os

# Ensure backend/app is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.market_prices import (
    MarketPrices,
    get_market_prices,
    get_latest_market_prices,
    get_market_prices_or_default,
    upsert_market_prices,
    get_all_market_prices,
    DEFAULT_PTF_TL_PER_MWH,
    DEFAULT_YEKDEM_TL_PER_MWH,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_record(**overrides):
    """Create a mock MarketReferencePrice DB record."""
    defaults = dict(
        id=1,
        period="2025-01",
        ptf_tl_per_mwh=2508.80,
        yekdem_tl_per_mwh=364.0,
        is_locked=0,
        price_type="PTF",
        status="final",
        captured_at=datetime(2025, 1, 15, 10, 0, 0),
        change_reason=None,
        source="seed",
        source_note="EPİAŞ seed data",
        updated_by="admin",
        created_at=datetime(2025, 1, 1),
        updated_at=datetime(2025, 1, 15, 12, 0, 0),
    )
    defaults.update(overrides)
    rec = MagicMock()
    for k, v in defaults.items():
        setattr(rec, k, v)
    return rec


def _mock_db_session():
    """Create a mock DB session with chainable query."""
    db = MagicMock()
    return db


def _setup_query_returns(db, record):
    """Setup mock DB to return a record from query().filter().first()."""
    query_mock = MagicMock()
    db.query.return_value = query_mock
    filter_mock = MagicMock()
    query_mock.filter.return_value = filter_mock
    filter_mock.first.return_value = record
    return db


def _setup_query_returns_list(db, records):
    """Setup mock DB to return records from query().filter().order_by().limit().all()."""
    query_mock = MagicMock()
    db.query.return_value = query_mock
    filter_mock = MagicMock()
    query_mock.filter.return_value = filter_mock
    order_mock = MagicMock()
    filter_mock.order_by.return_value = order_mock
    limit_mock = MagicMock()
    order_mock.limit.return_value = limit_mock
    limit_mock.all.return_value = records
    return db


# ---------------------------------------------------------------------------
# MarketPrices Dataclass Tests
# ---------------------------------------------------------------------------

class TestMarketPricesDataclass:
    """Test MarketPrices dataclass backward compatibility."""

    def test_default_values_backward_compat(self):
        """Existing code creating MarketPrices without new fields should still work."""
        prices = MarketPrices(
            period="2025-01",
            ptf_tl_per_mwh=2508.80,
            yekdem_tl_per_mwh=364.0,
            source="db",
        )
        assert prices.status == "final"
        assert prices.price_type == "PTF"
        assert prices.captured_at is None
        assert prices.change_reason is None
        assert prices.source_detail is None
        assert prices.is_locked is False

    def test_new_fields_can_be_set(self):
        """New fields can be explicitly set."""
        now = datetime.utcnow()
        prices = MarketPrices(
            period="2025-01",
            ptf_tl_per_mwh=2508.80,
            yekdem_tl_per_mwh=364.0,
            source="db",
            status="provisional",
            price_type="PTF",
            captured_at=now,
            change_reason="Test reason",
            source_detail="epias_api",
        )
        assert prices.status == "provisional"
        assert prices.captured_at == now
        assert prices.change_reason == "Test reason"
        assert prices.source_detail == "epias_api"

    def test_is_locked_default(self):
        """is_locked defaults to False."""
        prices = MarketPrices(
            period="2025-01",
            ptf_tl_per_mwh=2508.80,
            yekdem_tl_per_mwh=364.0,
            source="db",
        )
        assert prices.is_locked is False


# ---------------------------------------------------------------------------
# get_market_prices Tests
# ---------------------------------------------------------------------------

class TestGetMarketPrices:
    """Test get_market_prices() with status field support."""

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_returns_status_from_db(self, mock_model):
        """get_market_prices should return the status field from DB record."""
        record = _make_db_record(status="provisional")
        db = _mock_db_session()
        _setup_query_returns(db, record)

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_market_prices(db, "2025-01")

        assert result is not None
        assert result.status == "provisional"
        assert result.price_type == "PTF"
        assert result.source == "db"

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_null_status_treated_as_final(self, mock_model):
        """Backward compatibility: null status in DB should be treated as 'final'."""
        record = _make_db_record(status=None)
        db = _mock_db_session()
        _setup_query_returns(db, record)

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_market_prices(db, "2025-01")

        assert result is not None
        assert result.status == "final"

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_empty_string_status_treated_as_final(self, mock_model):
        """Empty string status should also be treated as 'final'."""
        record = _make_db_record(status="")
        db = _mock_db_session()
        _setup_query_returns(db, record)

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_market_prices(db, "2025-01")

        assert result is not None
        assert result.status == "final"

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_returns_none_when_not_found(self, mock_model):
        """get_market_prices should return None when no record found."""
        db = _mock_db_session()
        _setup_query_returns(db, None)

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_market_prices(db, "2099-01")

        assert result is None

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_price_type_filter(self, mock_model):
        """get_market_prices should filter by price_type."""
        record = _make_db_record(price_type="PTF")
        db = _mock_db_session()
        _setup_query_returns(db, record)

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_market_prices(db, "2025-01", price_type="PTF")

        assert result is not None
        assert result.price_type == "PTF"
        # Verify filter was called (query chain includes price_type filter)
        db.query.assert_called_once()

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_returns_captured_at(self, mock_model):
        """get_market_prices should return captured_at from DB."""
        captured = datetime(2025, 1, 15, 10, 0, 0)
        record = _make_db_record(captured_at=captured)
        db = _mock_db_session()
        _setup_query_returns(db, record)

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_market_prices(db, "2025-01")

        assert result.captured_at == captured

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_returns_source_detail(self, mock_model):
        """get_market_prices should return source_detail (DB source field)."""
        record = _make_db_record(source="epias_api")
        db = _mock_db_session()
        _setup_query_returns(db, record)

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_market_prices(db, "2025-01")

        assert result.source_detail == "epias_api"
        assert result.source == "db"  # source field is always "db" for DB records

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_returns_change_reason(self, mock_model):
        """get_market_prices should return change_reason from DB."""
        record = _make_db_record(change_reason="Monthly update")
        db = _mock_db_session()
        _setup_query_returns(db, record)

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_market_prices(db, "2025-01")

        assert result.change_reason == "Monthly update"


# ---------------------------------------------------------------------------
# get_market_prices_or_default Tests
# ---------------------------------------------------------------------------

class TestGetMarketPricesOrDefault:
    """Test get_market_prices_or_default() backward compatibility."""

    def test_default_has_final_status(self):
        """Default MarketPrices should have status='final'."""
        db = _mock_db_session()
        _setup_query_returns(db, None)

        result = get_market_prices_or_default(db, "2099-01")

        assert result.status == "final"
        assert result.price_type == "PTF"
        assert result.source == "default"
        assert result.ptf_tl_per_mwh == DEFAULT_PTF_TL_PER_MWH

    def test_default_with_custom_price_type(self):
        """Default should use the requested price_type."""
        db = _mock_db_session()
        _setup_query_returns(db, None)

        result = get_market_prices_or_default(db, "2099-01", price_type="SMF")

        assert result.price_type == "SMF"


# ---------------------------------------------------------------------------
# upsert_market_prices Tests
# ---------------------------------------------------------------------------

class TestUpsertMarketPrices:
    """Test upsert_market_prices() with new fields support."""

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_insert_new_record_with_defaults(self, mock_model_cls):
        """New record without explicit status should default to 'provisional'."""
        db = _mock_db_session()
        _setup_query_returns(db, None)  # No existing record

        success, msg = upsert_market_prices(
            db=db,
            period="2025-06",
            ptf_tl_per_mwh=2500.0,
            yekdem_tl_per_mwh=350.0,
        )

        assert success is True
        assert "eklendi" in msg
        # Verify db.add was called with a new record
        db.add.assert_called_once()
        db.commit.assert_called_once()

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_insert_with_explicit_status(self, mock_model_cls):
        """New record with explicit status should use that status."""
        db = _mock_db_session()
        _setup_query_returns(db, None)

        success, msg = upsert_market_prices(
            db=db,
            period="2025-06",
            ptf_tl_per_mwh=2500.0,
            yekdem_tl_per_mwh=350.0,
            status="final",
            source="seed",
        )

        assert success is True
        db.add.assert_called_once()

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_update_existing_record_with_new_fields(self, mock_model_cls):
        """Updating existing record should set new fields when provided."""
        existing = _make_db_record(status="provisional", is_locked=0)
        db = _mock_db_session()
        _setup_query_returns(db, existing)

        captured = datetime(2025, 6, 1, 12, 0, 0)
        success, msg = upsert_market_prices(
            db=db,
            period="2025-01",
            ptf_tl_per_mwh=2600.0,
            yekdem_tl_per_mwh=370.0,
            status="final",
            captured_at=captured,
            change_reason="Monthly finalization",
            source="epias_manual",
        )

        assert success is True
        assert "güncellendi" in msg
        assert existing.status == "final"
        assert existing.captured_at == captured
        assert existing.change_reason == "Monthly finalization"
        assert existing.source == "epias_manual"

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_update_without_new_fields_preserves_existing(self, mock_model_cls):
        """Updating without new fields should not change them (backward compat)."""
        existing = _make_db_record(
            status="provisional",
            is_locked=0,
            captured_at=datetime(2025, 1, 1),
            change_reason="Original reason",
            source="seed",
        )
        db = _mock_db_session()
        _setup_query_returns(db, existing)

        success, msg = upsert_market_prices(
            db=db,
            period="2025-01",
            ptf_tl_per_mwh=2600.0,
            yekdem_tl_per_mwh=370.0,
            # No status, captured_at, change_reason, source provided
        )

        assert success is True
        # Existing values should be preserved (not overwritten)
        assert existing.status == "provisional"
        assert existing.captured_at == datetime(2025, 1, 1)
        assert existing.change_reason == "Original reason"
        assert existing.source == "seed"

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_locked_period_rejected(self, mock_model_cls):
        """Locked period should be rejected."""
        existing = _make_db_record(is_locked=1)
        db = _mock_db_session()
        _setup_query_returns(db, existing)

        success, msg = upsert_market_prices(
            db=db,
            period="2025-01",
            ptf_tl_per_mwh=2600.0,
            yekdem_tl_per_mwh=370.0,
        )

        assert success is False
        assert "kilitli" in msg

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_validation_failure_rejected(self, mock_model_cls):
        """Invalid values should be rejected."""
        db = _mock_db_session()

        success, msg = upsert_market_prices(
            db=db,
            period="2025-01",
            ptf_tl_per_mwh=-100.0,  # Invalid: negative
            yekdem_tl_per_mwh=370.0,
        )

        assert success is False

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_insert_with_price_type(self, mock_model_cls):
        """New record should use specified price_type."""
        db = _mock_db_session()
        _setup_query_returns(db, None)

        success, msg = upsert_market_prices(
            db=db,
            period="2025-06",
            ptf_tl_per_mwh=2500.0,
            yekdem_tl_per_mwh=350.0,
            price_type="PTF",
        )

        assert success is True

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_backward_compat_old_signature(self, mock_model_cls):
        """Old callers using only (period, ptf, yekdem) should still work."""
        db = _mock_db_session()
        _setup_query_returns(db, None)

        # Old-style call without any new parameters
        success, msg = upsert_market_prices(
            db=db,
            period="2025-01",
            ptf_tl_per_mwh=2508.80,
            yekdem_tl_per_mwh=364.0,
            source_note="EPİAŞ manual",
            updated_by="admin",
        )

        assert success is True


# ---------------------------------------------------------------------------
# get_latest_market_prices Tests
# ---------------------------------------------------------------------------

class TestGetLatestMarketPrices:
    """Test get_latest_market_prices() with new fields."""

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_returns_status_field(self, mock_model):
        """get_latest_market_prices should include status in result."""
        record = _make_db_record(status="provisional")
        db = _mock_db_session()
        query_mock = MagicMock()
        db.query.return_value = query_mock
        filter_mock = MagicMock()
        query_mock.filter.return_value = filter_mock
        order_mock = MagicMock()
        filter_mock.order_by.return_value = order_mock
        order_mock.first.return_value = record

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_latest_market_prices(db)

        assert result is not None
        assert result.status == "provisional"

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_null_status_is_final(self, mock_model):
        """Null status in latest record should be treated as 'final'."""
        record = _make_db_record(status=None)
        db = _mock_db_session()
        query_mock = MagicMock()
        db.query.return_value = query_mock
        filter_mock = MagicMock()
        query_mock.filter.return_value = filter_mock
        order_mock = MagicMock()
        filter_mock.order_by.return_value = order_mock
        order_mock.first.return_value = record

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_latest_market_prices(db)

        assert result.status == "final"

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_returns_none_when_empty(self, mock_model):
        """Should return None when no records exist."""
        db = _mock_db_session()
        query_mock = MagicMock()
        db.query.return_value = query_mock
        filter_mock = MagicMock()
        query_mock.filter.return_value = filter_mock
        order_mock = MagicMock()
        filter_mock.order_by.return_value = order_mock
        order_mock.first.return_value = None

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_latest_market_prices(db)

        assert result is None


# ---------------------------------------------------------------------------
# get_all_market_prices Tests
# ---------------------------------------------------------------------------

class TestGetAllMarketPrices:
    """Test get_all_market_prices() with new fields."""

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_returns_status_for_all_records(self, mock_model):
        """All records should include status field."""
        records = [
            _make_db_record(period="2025-02", status="provisional"),
            _make_db_record(period="2025-01", status="final"),
        ]
        db = _mock_db_session()
        _setup_query_returns_list(db, records)

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_all_market_prices(db)

        assert len(result) == 2
        assert result[0].status == "provisional"
        assert result[1].status == "final"

    @patch("app.market_prices.MarketReferencePrice", create=True)
    def test_null_status_treated_as_final_in_list(self, mock_model):
        """Null status records in list should be treated as 'final'."""
        records = [
            _make_db_record(period="2025-01", status=None),
        ]
        db = _mock_db_session()
        _setup_query_returns_list(db, records)

        with patch("app.market_prices.MarketReferencePrice", mock_model):
            result = get_all_market_prices(db)

        assert len(result) == 1
        assert result[0].status == "final"
