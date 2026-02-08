"""
Unit tests for MarketPriceAdminService.

Feature: ptf-admin-management
Tests upsert, bulk operations, lookup, and listing.
"""

import pytest
from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock, patch
from sqlalchemy.exc import IntegrityError

from app.market_price_admin_service import (
    MarketPriceAdminService,
    ServiceErrorCode,
    UpsertResult,
    BulkUpsertResult,
    MarketPriceLookupResult,
    PaginatedResult,
)
from app.market_price_validator import NormalizedMarketPriceInput
from app.database import MarketReferencePrice


@pytest.fixture
def service():
    """Create service instance."""
    return MarketPriceAdminService()


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


@pytest.fixture
def sample_input():
    """Create sample normalized input."""
    return NormalizedMarketPriceInput(
        period="2025-01",
        value=Decimal("2508.80"),
        status="final",
        price_type="PTF",
    )


@pytest.fixture
def sample_record():
    """Create sample MarketReferencePrice record."""
    record = MagicMock(spec=MarketReferencePrice)
    record.id = 1
    record.price_type = "PTF"
    record.period = "2025-01"
    record.ptf_tl_per_mwh = 2508.80
    record.status = "provisional"
    record.source = "seed"
    record.is_locked = 0
    record.captured_at = datetime.utcnow()
    return record


class TestUpsertInsert:
    """Tests for INSERT path."""
    
    def test_insert_new_record(self, service, mock_db, sample_input):
        """New record should be created (+ audit history write)."""
        # Setup: no existing record
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        result = service.upsert_price(
            db=mock_db,
            normalized=sample_input,
            updated_by="admin",
            source="epias_manual",
            change_reason="Initial entry",
        )
        
        assert result.success is True
        assert result.created is True
        assert result.changed is True
        # 2 add calls: MarketReferencePrice + PriceChangeHistory (audit)
        assert mock_db.add.call_count == 2
        # 2 commit calls: main record + audit history (best-effort)
        assert mock_db.commit.call_count == 2
    
    def test_insert_without_change_reason_ok(self, service, mock_db, sample_input):
        """Insert without change_reason should succeed."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        result = service.upsert_price(
            db=mock_db,
            normalized=sample_input,
            updated_by="admin",
            source="epias_manual",
            change_reason=None,  # OK for insert
        )
        
        assert result.success is True
        assert result.created is True
    
    def test_insert_db_conflict(self, service, mock_db, sample_input):
        """DB conflict should return error."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.commit.side_effect = IntegrityError("", "", "")
        
        result = service.upsert_price(
            db=mock_db,
            normalized=sample_input,
            updated_by="admin",
            source="epias_manual",
        )
        
        assert result.success is False
        assert result.error.error_code == ServiceErrorCode.DB_CONFLICT
        mock_db.rollback.assert_called_once()


class TestUpsertUpdate:
    """Tests for UPDATE path."""
    
    def test_update_provisional_to_final(self, service, mock_db, sample_input, sample_record):
        """Upgrade from provisional to final should succeed (+ audit history write)."""
        sample_record.status = "provisional"
        sample_record.ptf_tl_per_mwh = 2400.00  # Different value
        mock_db.query.return_value.filter.return_value.first.return_value = sample_record
        
        result = service.upsert_price(
            db=mock_db,
            normalized=sample_input,  # status=final
            updated_by="admin",
            source="epias_manual",
            change_reason="Month finalized",
        )
        
        assert result.success is True
        assert result.created is False
        assert result.changed is True
        # 1 add call: PriceChangeHistory (audit); update path doesn't add main record
        assert mock_db.add.call_count == 1
        # 2 commit calls: main update + audit history (best-effort)
        assert mock_db.commit.call_count == 2
    
    def test_update_provisional_to_provisional(self, service, mock_db, sample_record):
        """Update provisional with new value should succeed."""
        sample_record.status = "provisional"
        sample_record.ptf_tl_per_mwh = 2400.00
        mock_db.query.return_value.filter.return_value.first.return_value = sample_record
        
        new_input = NormalizedMarketPriceInput(
            period="2025-01",
            value=Decimal("2500.00"),
            status="provisional",
            price_type="PTF",
        )
        
        result = service.upsert_price(
            db=mock_db,
            normalized=new_input,
            updated_by="admin",
            source="epias_api",
            change_reason="Daily update",
        )
        
        assert result.success is True
        assert result.changed is True
    
    def test_update_final_to_provisional_rejected(self, service, mock_db, sample_record):
        """Downgrade from final to provisional should be rejected."""
        sample_record.status = "final"
        sample_record.ptf_tl_per_mwh = 2508.80
        mock_db.query.return_value.filter.return_value.first.return_value = sample_record
        
        downgrade_input = NormalizedMarketPriceInput(
            period="2025-01",
            value=Decimal("2508.80"),
            status="provisional",  # Downgrade attempt
            price_type="PTF",
        )
        
        result = service.upsert_price(
            db=mock_db,
            normalized=downgrade_input,
            updated_by="admin",
            source="epias_manual",
            change_reason="Revert",
        )
        
        assert result.success is False
        assert result.error.error_code == ServiceErrorCode.STATUS_DOWNGRADE_FORBIDDEN
    
    def test_update_final_without_force_rejected(self, service, mock_db, sample_record):
        """Update final record without force_update should be rejected."""
        sample_record.status = "final"
        sample_record.ptf_tl_per_mwh = 2508.80
        mock_db.query.return_value.filter.return_value.first.return_value = sample_record
        
        new_input = NormalizedMarketPriceInput(
            period="2025-01",
            value=Decimal("2600.00"),  # Different value
            status="final",
            price_type="PTF",
        )
        
        result = service.upsert_price(
            db=mock_db,
            normalized=new_input,
            updated_by="admin",
            source="epias_manual",
            change_reason="Correction",
            force_update=False,
        )
        
        assert result.success is False
        assert result.error.error_code == ServiceErrorCode.FINAL_RECORD_PROTECTED
    
    def test_update_final_with_force_allowed(self, service, mock_db, sample_record):
        """Update final record with force_update should succeed."""
        sample_record.status = "final"
        sample_record.ptf_tl_per_mwh = 2508.80
        mock_db.query.return_value.filter.return_value.first.return_value = sample_record
        
        new_input = NormalizedMarketPriceInput(
            period="2025-01",
            value=Decimal("2600.00"),
            status="final",
            price_type="PTF",
        )
        
        result = service.upsert_price(
            db=mock_db,
            normalized=new_input,
            updated_by="admin",
            source="epias_manual",
            change_reason="EPİAŞ correction",
            force_update=True,
        )
        
        assert result.success is True
        assert result.changed is True
    
    def test_update_locked_period_rejected(self, service, mock_db, sample_record):
        """Update locked period should be rejected."""
        sample_record.is_locked = 1
        mock_db.query.return_value.filter.return_value.first.return_value = sample_record
        
        result = service.upsert_price(
            db=mock_db,
            normalized=NormalizedMarketPriceInput(
                period="2025-01",
                value=Decimal("2600.00"),
                status="final",
                price_type="PTF",
            ),
            updated_by="admin",
            source="epias_manual",
            change_reason="Attempt",
        )
        
        assert result.success is False
        assert result.error.error_code == ServiceErrorCode.PERIOD_LOCKED
    
    def test_update_without_change_reason_rejected(self, service, mock_db, sample_record):
        """Update without change_reason should be rejected."""
        sample_record.status = "provisional"
        sample_record.ptf_tl_per_mwh = 2400.00
        mock_db.query.return_value.filter.return_value.first.return_value = sample_record
        
        result = service.upsert_price(
            db=mock_db,
            normalized=NormalizedMarketPriceInput(
                period="2025-01",
                value=Decimal("2500.00"),
                status="provisional",
                price_type="PTF",
            ),
            updated_by="admin",
            source="epias_manual",
            change_reason=None,  # Missing!
        )
        
        assert result.success is False
        assert result.error.error_code == ServiceErrorCode.CHANGE_REASON_REQUIRED


class TestNoOpUpdate:
    """Tests for no-op detection."""
    
    def test_same_value_same_status_noop(self, service, mock_db, sample_record):
        """Same value and status should be no-op."""
        sample_record.status = "final"
        sample_record.ptf_tl_per_mwh = 2508.80
        mock_db.query.return_value.filter.return_value.first.return_value = sample_record
        
        result = service.upsert_price(
            db=mock_db,
            normalized=NormalizedMarketPriceInput(
                period="2025-01",
                value=Decimal("2508.80"),  # Same
                status="final",  # Same
                price_type="PTF",
            ),
            updated_by="admin",
            source="epias_manual",
            change_reason="No change",
        )
        
        assert result.success is True
        assert result.created is False
        assert result.changed is False  # No-op!
        mock_db.commit.assert_not_called()  # No write


class TestBulkUpsert:
    """Tests for bulk operations."""
    
    def test_bulk_all_success(self, service, mock_db):
        """All rows succeed."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        inputs = [
            NormalizedMarketPriceInput(period="2025-01", value=Decimal("2500"), status="final", price_type="PTF"),
            NormalizedMarketPriceInput(period="2025-02", value=Decimal("2600"), status="final", price_type="PTF"),
        ]
        
        result = service.bulk_upsert(
            db=mock_db,
            normalized_list=inputs,
            updated_by="admin",
            source="import",
            change_reason="Bulk import",
            atomic=False,
        )
        
        assert result.success is True
        assert result.created_count == 2
        assert result.failed_count == 0
    
    def test_bulk_atomic_rollback_on_error(self, service, mock_db, sample_record):
        """Atomic mode should rollback on any error."""
        # First call: no record (insert OK)
        # Second call: locked record (error)
        sample_record.is_locked = 1
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            None,  # First: no record
            sample_record,  # Second: locked
        ]
        
        inputs = [
            NormalizedMarketPriceInput(period="2025-01", value=Decimal("2500"), status="final", price_type="PTF"),
            NormalizedMarketPriceInput(period="2025-02", value=Decimal("2600"), status="final", price_type="PTF"),
        ]
        
        result = service.bulk_upsert(
            db=mock_db,
            normalized_list=inputs,
            updated_by="admin",
            source="import",
            change_reason="Bulk import",
            atomic=True,
        )
        
        assert result.success is False
        assert result.failed_count == 1
        assert result.created_count == 0  # Rolled back
        mock_db.rollback.assert_called()
    
    def test_bulk_non_atomic_continues_on_error(self, service, mock_db, sample_record):
        """Non-atomic mode should continue on error."""
        sample_record.is_locked = 1
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            sample_record,  # First: locked (error)
            None,  # Second: no record (OK)
        ]
        
        inputs = [
            NormalizedMarketPriceInput(period="2025-01", value=Decimal("2500"), status="final", price_type="PTF"),
            NormalizedMarketPriceInput(period="2025-02", value=Decimal("2600"), status="final", price_type="PTF"),
        ]
        
        result = service.bulk_upsert(
            db=mock_db,
            normalized_list=inputs,
            updated_by="admin",
            source="import",
            change_reason="Bulk import",
            atomic=False,
        )
        
        assert result.success is False
        assert result.failed_count == 1
        assert result.created_count == 1  # Second succeeded


class TestGetForCalculation:
    """Tests for calculation lookup."""
    
    def test_lookup_final_record(self, service, mock_db, sample_record):
        """Final record should return is_provisional_used=False."""
        sample_record.status = "final"
        mock_db.query.return_value.filter.return_value.first.return_value = sample_record
        
        with patch('app.market_price_admin_service.datetime') as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2025-02"
            result, error = service.get_for_calculation(mock_db, "2025-01", "PTF")
        
        assert error is None
        assert result is not None
        assert result.is_provisional_used is False
    
    def test_lookup_provisional_record(self, service, mock_db, sample_record):
        """Provisional record should return is_provisional_used=True."""
        sample_record.status = "provisional"
        mock_db.query.return_value.filter.return_value.first.return_value = sample_record
        
        with patch('app.market_price_admin_service.datetime') as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2025-02"
            result, error = service.get_for_calculation(mock_db, "2025-01", "PTF")
        
        assert error is None
        assert result.is_provisional_used is True
    
    def test_lookup_not_found(self, service, mock_db):
        """Missing record should return PERIOD_NOT_FOUND."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        with patch('app.market_price_admin_service.datetime') as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2025-02"
            result, error = service.get_for_calculation(mock_db, "2025-01", "PTF")
        
        assert result is None
        assert error.error_code == ServiceErrorCode.PERIOD_NOT_FOUND
    
    def test_lookup_future_period(self, service, mock_db):
        """Future period should return FUTURE_PERIOD."""
        with patch('app.market_price_admin_service.datetime') as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2025-01"
            result, error = service.get_for_calculation(mock_db, "2025-12", "PTF")
        
        assert result is None
        assert error.error_code == ServiceErrorCode.FUTURE_PERIOD


class TestListPrices:
    """Tests for listing."""
    
    def test_list_with_filters(self, service, mock_db):
        """List should apply filters."""
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 10
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []
        
        result = service.list_prices(
            db=mock_db,
            price_type="PTF",
            status="final",
            period_from="2024-01",
            period_to="2024-12",
            limit=20,
            offset=0,
        )
        
        assert result.total == 10
        # Verify filters were applied
        assert mock_query.filter.call_count >= 1
    
    def test_list_pagination(self, service, mock_db):
        """List should handle pagination."""
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 50
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [MagicMock()] * 20
        
        result = service.list_prices(
            db=mock_db,
            limit=20,
            offset=0,
        )
        
        assert result.total == 50
        assert result.has_more is True
        assert result.next_cursor == "20"
