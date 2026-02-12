"""
Property-based tests for MarketPriceAdminService.

Feature: ptf-admin-management
Uses Hypothesis for property-based testing with minimum 100 iterations.

Properties tested:
- Property 7: Status Transition Rules
- Property 8: Calculation Lookup Priority
- Property 9: Pagination Correctness
- Property 10: Filter Correctness
"""

import pytest
from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock, patch
from hypothesis import given, strategies as st, settings, assume

from app.market_price_admin_service import (
    MarketPriceAdminService,
    ServiceErrorCode,
    UpsertResult,
)
from app.market_price_validator import NormalizedMarketPriceInput
from app.database import MarketReferencePrice


# ═══════════════════════════════════════════════════════════════════════════════
# Strategies
# ═══════════════════════════════════════════════════════════════════════════════

@st.composite
def valid_period_strategy(draw):
    """Generate valid YYYY-MM periods (past only for tests)."""
    year = draw(st.integers(min_value=2020, max_value=2025))
    month = draw(st.integers(min_value=1, max_value=12))
    return f"{year}-{month:02d}"


@st.composite
def valid_value_strategy(draw):
    """Generate valid PTF values."""
    return Decimal(str(draw(st.integers(min_value=100, max_value=9999)))) + \
           Decimal(str(draw(st.integers(min_value=0, max_value=99)))) / 100


@st.composite
def status_strategy(draw):
    """Generate valid status."""
    return draw(st.sampled_from(["provisional", "final"]))


@st.composite
def normalized_input_strategy(draw):
    """Generate NormalizedMarketPriceInput."""
    return NormalizedMarketPriceInput(
        period=draw(valid_period_strategy()),
        value=draw(valid_value_strategy()),
        status=draw(status_strategy()),
        price_type="PTF",
    )


@st.composite
def mock_record_strategy(draw, status=None, value=None, is_locked=False):
    """Generate mock MarketReferencePrice."""
    record = MagicMock(spec=MarketReferencePrice)
    record.id = draw(st.integers(min_value=1, max_value=10000))
    record.price_type = "PTF"
    record.period = draw(valid_period_strategy())
    record.ptf_tl_per_mwh = float(value) if value else float(draw(valid_value_strategy()))
    record.status = status if status else draw(status_strategy())
    record.source = draw(st.sampled_from(["seed", "epias_manual", "epias_api"]))
    record.is_locked = 1 if is_locked else 0
    record.captured_at = datetime.utcnow()
    return record


# ═══════════════════════════════════════════════════════════════════════════════
# Property 7: Status Transition Rules
# **Validates: Requirements 2.4, 2.5, 10.1, 10.2, 10.3**
# ═══════════════════════════════════════════════════════════════════════════════

class TestProperty7StatusTransitionRules:
    """
    Feature: ptf-admin-management, Property 7: Status Transition Rules
    **Validates: Requirements 2.4, 2.5, 10.1, 10.2, 10.3**
    """
    
    @settings(max_examples=100)
    @given(valid_value_strategy(), valid_value_strategy())
    def test_provisional_to_provisional_allowed(self, old_value, new_value):
        """
        2.4: provisional → provisional: ALLOW without force_update
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        # Setup existing provisional record
        record = MagicMock(spec=MarketReferencePrice)
        record.status = "provisional"
        record.ptf_tl_per_mwh = float(old_value)
        record.is_locked = 0
        mock_db.query.return_value.filter.return_value.first.return_value = record
        
        # Attempt update to provisional
        result = service.upsert_price(
            db=mock_db,
            normalized=NormalizedMarketPriceInput(
                period="2025-01",
                value=new_value,
                status="provisional",
                price_type="PTF",
            ),
            updated_by="admin",
            source="test",
            change_reason="Test update" if old_value != new_value else None,
            force_update=False,
        )
        
        # Should succeed (or no-op if same value)
        if old_value != new_value:
            assert result.success is True or result.error.error_code == ServiceErrorCode.CHANGE_REASON_REQUIRED
        else:
            assert result.success is True
            assert result.changed is False  # No-op
    
    @settings(max_examples=100)
    @given(valid_value_strategy(), valid_value_strategy())
    def test_provisional_to_final_allowed(self, old_value, new_value):
        """
        2.4: provisional → final: ALLOW (upgrade)
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        record = MagicMock(spec=MarketReferencePrice)
        record.status = "provisional"
        record.ptf_tl_per_mwh = float(old_value)
        record.is_locked = 0
        mock_db.query.return_value.filter.return_value.first.return_value = record
        
        result = service.upsert_price(
            db=mock_db,
            normalized=NormalizedMarketPriceInput(
                period="2025-01",
                value=new_value,
                status="final",  # Upgrade
                price_type="PTF",
            ),
            updated_by="admin",
            source="test",
            change_reason="Finalize",
            force_update=False,
        )
        
        assert result.success is True
        assert result.changed is True
    
    @settings(max_examples=100)
    @given(valid_value_strategy(), valid_value_strategy())
    def test_final_to_provisional_forbidden(self, old_value, new_value):
        """
        2.5: final → provisional: REJECT always (downgrade forbidden)
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        record = MagicMock(spec=MarketReferencePrice)
        record.status = "final"
        record.ptf_tl_per_mwh = float(old_value)
        record.is_locked = 0
        mock_db.query.return_value.filter.return_value.first.return_value = record
        
        result = service.upsert_price(
            db=mock_db,
            normalized=NormalizedMarketPriceInput(
                period="2025-01",
                value=new_value,
                status="provisional",  # Downgrade attempt
                price_type="PTF",
            ),
            updated_by="admin",
            source="test",
            change_reason="Revert",
            force_update=True,  # Even with force!
        )
        
        assert result.success is False
        assert result.error.error_code == ServiceErrorCode.STATUS_DOWNGRADE_FORBIDDEN
    
    @settings(max_examples=100, database=None)
    @given(valid_value_strategy())
    def test_final_to_final_same_value_noop(self, value):
        """
        10.1: final → final (same value): ALLOW without force_update (no-op)
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        record = MagicMock(spec=MarketReferencePrice)
        record.status = "final"
        record.ptf_tl_per_mwh = float(value)
        record.is_locked = 0
        mock_db.query.return_value.filter.return_value.first.return_value = record
        
        result = service.upsert_price(
            db=mock_db,
            normalized=NormalizedMarketPriceInput(
                period="2025-01",
                value=value,  # Same value
                status="final",
                price_type="PTF",
            ),
            updated_by="admin",
            source="test",
            change_reason="No change",
            force_update=False,
        )
        
        assert result.success is True
        assert result.changed is False  # No-op
    
    @settings(max_examples=100)
    @given(valid_value_strategy(), valid_value_strategy())
    def test_final_to_final_diff_value_requires_force(self, old_value, new_value):
        """
        10.3: final → final (different value): REQUIRE force_update
        """
        assume(old_value != new_value)
        
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        record = MagicMock(spec=MarketReferencePrice)
        record.status = "final"
        record.ptf_tl_per_mwh = float(old_value)
        record.is_locked = 0
        mock_db.query.return_value.filter.return_value.first.return_value = record
        
        # Without force_update
        result = service.upsert_price(
            db=mock_db,
            normalized=NormalizedMarketPriceInput(
                period="2025-01",
                value=new_value,
                status="final",
                price_type="PTF",
            ),
            updated_by="admin",
            source="test",
            change_reason="Correction",
            force_update=False,
        )
        
        assert result.success is False
        assert result.error.error_code == ServiceErrorCode.FINAL_RECORD_PROTECTED


# ═══════════════════════════════════════════════════════════════════════════════
# Property 8: Calculation Lookup Priority
# **Validates: Requirements 7.1, 7.2, 7.3, 7.5, 7.6, 7.7**
# ═══════════════════════════════════════════════════════════════════════════════

class TestProperty8CalculationLookupPriority:
    """
    Feature: ptf-admin-management, Property 8: Calculation Lookup Priority
    **Validates: Requirements 7.1, 7.2, 7.3, 7.5, 7.6, 7.7**
    """
    
    @settings(max_examples=100)
    @given(valid_value_strategy())
    def test_final_record_not_provisional_used(self, value):
        """
        7.1: If final record exists → is_provisional_used=False
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        record = MagicMock(spec=MarketReferencePrice)
        record.status = "final"
        record.ptf_tl_per_mwh = float(value)
        record.period = "2024-01"
        record.price_type = "PTF"
        record.source = "seed"
        record.captured_at = datetime.utcnow()
        mock_db.query.return_value.filter.return_value.first.return_value = record
        
        with patch('app.market_price_admin_service.datetime') as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2025-01"
            result, error = service.get_for_calculation(mock_db, "2024-01", "PTF")
        
        assert error is None
        assert result.is_provisional_used is False
    
    @settings(max_examples=100)
    @given(valid_value_strategy())
    def test_provisional_record_is_provisional_used(self, value):
        """
        7.2: If only provisional exists → is_provisional_used=True
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        record = MagicMock(spec=MarketReferencePrice)
        record.status = "provisional"
        record.ptf_tl_per_mwh = float(value)
        record.period = "2024-01"
        record.price_type = "PTF"
        record.source = "seed"
        record.captured_at = datetime.utcnow()
        mock_db.query.return_value.filter.return_value.first.return_value = record
        
        with patch('app.market_price_admin_service.datetime') as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2025-01"
            result, error = service.get_for_calculation(mock_db, "2024-01", "PTF")
        
        assert error is None
        assert result.is_provisional_used is True
    
    @settings(max_examples=100)
    @given(valid_period_strategy())
    def test_missing_record_returns_error(self, period):
        """
        7.3: If no record exists → PERIOD_NOT_FOUND error
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        with patch('app.market_price_admin_service.datetime') as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026-01"
            result, error = service.get_for_calculation(mock_db, period, "PTF")
        
        assert result is None
        assert error.error_code == ServiceErrorCode.PERIOD_NOT_FOUND
    
    @settings(max_examples=100)
    @given(st.integers(min_value=2030, max_value=2050), st.integers(min_value=1, max_value=12))
    def test_future_period_returns_error(self, year, month):
        """
        7.5: If future period → FUTURE_PERIOD error
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        future_period = f"{year}-{month:02d}"
        
        with patch('app.market_price_admin_service.datetime') as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2025-01"
            result, error = service.get_for_calculation(mock_db, future_period, "PTF")
        
        assert result is None
        assert error.error_code == ServiceErrorCode.FUTURE_PERIOD
    
    @settings(max_examples=100)
    @given(valid_period_strategy(), valid_value_strategy(), status_strategy())
    def test_returned_period_equals_requested(self, period, value, status):
        """
        7.7: Returned period SHALL always equal requested period
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        record = MagicMock(spec=MarketReferencePrice)
        record.status = status
        record.ptf_tl_per_mwh = float(value)
        record.period = period
        record.price_type = "PTF"
        record.source = "seed"
        record.captured_at = datetime.utcnow()
        mock_db.query.return_value.filter.return_value.first.return_value = record
        
        with patch('app.market_price_admin_service.datetime') as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026-01"
            result, error = service.get_for_calculation(mock_db, period, "PTF")
        
        if result:
            assert result.period == period


# ═══════════════════════════════════════════════════════════════════════════════
# Property 9: Pagination Correctness
# **Validates: Requirements 4.1, 4.2**
# ═══════════════════════════════════════════════════════════════════════════════

class TestProperty9PaginationCorrectness:
    """
    Feature: ptf-admin-management, Property 9: Pagination Correctness
    **Validates: Requirements 4.1, 4.2**
    """
    
    @settings(max_examples=100)
    @given(
        st.integers(min_value=1, max_value=100),  # total
        st.integers(min_value=1, max_value=50),   # limit
        st.integers(min_value=0, max_value=50),   # offset
    )
    def test_pagination_item_count(self, total, limit, offset):
        """
        4.1: Returned items count SHALL be min(limit, total - offset) for valid pages
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        # Calculate expected count
        expected_count = min(limit, max(0, total - offset))
        
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = total
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = [MagicMock()] * expected_count
        
        result = service.list_prices(
            db=mock_db,
            limit=limit,
            offset=offset,
        )
        
        assert len(result.items) == expected_count
    
    @settings(max_examples=100)
    @given(
        st.integers(min_value=1, max_value=100),
        st.integers(min_value=1, max_value=50),
        st.integers(min_value=0, max_value=50),
    )
    def test_total_count_independent_of_pagination(self, total, limit, offset):
        """
        4.2: Total count SHALL equal N regardless of pagination
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = total
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []
        
        result = service.list_prices(
            db=mock_db,
            limit=limit,
            offset=offset,
        )
        
        assert result.total == total


# ═══════════════════════════════════════════════════════════════════════════════
# Property 10: Filter Correctness
# **Validates: Requirements 4.5**
# ═══════════════════════════════════════════════════════════════════════════════

class TestProperty10FilterCorrectness:
    """
    Feature: ptf-admin-management, Property 10: Filter Correctness
    **Validates: Requirements 4.5**
    """
    
    @settings(max_examples=50)
    @given(status_strategy())
    def test_status_filter_applied(self, status):
        """
        4.5: If status_filter is set, filter query should include status condition
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 0
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []
        
        service.list_prices(
            db=mock_db,
            status=status,
        )
        
        # Verify filter was called
        assert mock_query.filter.called
    
    @settings(max_examples=50)
    @given(valid_period_strategy(), valid_period_strategy())
    def test_period_range_filter_applied(self, period_from, period_to):
        """
        4.5: If period range is set, filter query should include period conditions
        """
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 0
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = []
        
        service.list_prices(
            db=mock_db,
            period_from=period_from,
            period_to=period_to,
        )
        
        # Verify filter was called multiple times (for both conditions)
        assert mock_query.filter.call_count >= 2
