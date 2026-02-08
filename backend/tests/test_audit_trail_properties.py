"""
Property-based tests for Audit Trail Completeness.

Feature: ptf-admin-management, Property 14: Audit Trail Completeness
Uses Hypothesis for property-based testing with minimum 100 iterations.

Properties tested:
- Property 14: Audit Trail Completeness

**Validates: Requirements 1.7, 1.8, 2.7, 4.4**

Property Definition:
*For any* modification operation:
- updated_by SHALL be set and non-empty
- updated_at SHALL be set to current UTC time
- If change_reason is provided, it SHALL be stored
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from hypothesis import given, strategies as st, settings, assume, HealthCheck

from app.market_price_admin_service import (
    MarketPriceAdminService,
    ServiceErrorCode,
    UpsertResult,
)
from app.market_price_validator import NormalizedMarketPriceInput
from app.database import MarketReferencePrice

PAST_PERIODS = [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 7)]


@st.composite
def valid_value_strategy(draw):
    """Generate valid PTF values as Decimal with 2 decimal places."""
    integer_part = draw(st.integers(min_value=100, max_value=9999))
    frac = draw(st.integers(min_value=0, max_value=99))
    return Decimal(f"{integer_part}.{frac:02d}")


@st.composite
def non_empty_username_strategy(draw):
    """Generate non-empty usernames for updated_by field."""
    return draw(st.sampled_from(["admin", "system", "test_user", "operator_1"]))


@st.composite
def change_reason_strategy(draw):
    """Generate optional change_reason strings (None or non-empty)."""
    include_reason = draw(st.booleans())
    if not include_reason:
        return None
    return draw(st.sampled_from([
        "Ay sonu kesinlesme", "EPIAS duzeltme", "Veri hatasi duzeltme",
        "Bulk import guncelleme", "Manuel duzeltme", "Status upgrade",
    ]))


@st.composite
def source_strategy(draw):
    """Generate valid source values."""
    return draw(st.sampled_from(["epias_manual", "epias_api", "migration", "seed"]))


@st.composite
def normalized_input_strategy(draw):
    """Generate NormalizedMarketPriceInput for testing."""
    return NormalizedMarketPriceInput(
        period=draw(st.sampled_from(PAST_PERIODS)),
        value=draw(valid_value_strategy()),
        status=draw(st.sampled_from(["provisional", "final"])),
        price_type="PTF",
    )


class TestProperty14AuditTrailInsert:
    """Property 14: Audit Trail Completeness - INSERT operations.

    *For any* insert (new record creation):
    - updated_by SHALL be set and non-empty
    - updated_at SHALL be set to current UTC time
    - If change_reason is provided, it SHALL be stored

    **Validates: Requirements 1.7, 1.8, 2.7, 4.4**
    """

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        normalized=normalized_input_strategy(),
        updated_by=non_empty_username_strategy(),
        source=source_strategy(),
        change_reason=change_reason_strategy(),
    )
    def test_insert_sets_updated_by(self, normalized, updated_by, source, change_reason):
        """Requirement 1.7, 2.7: updated_by SHALL be set and non-empty on insert."""
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        result = service.upsert_price(
            db=mock_db, normalized=normalized,
            updated_by=updated_by, source=source, change_reason=change_reason,
        )
        assert result.success is True, f"Insert should succeed, got error: {result.error}"
        assert result.created is True
        record = mock_db.add.call_args_list[0][0][0]
        assert record.updated_by == updated_by
        assert record.updated_by is not None and record.updated_by != ""

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        normalized=normalized_input_strategy(),
        updated_by=non_empty_username_strategy(),
        source=source_strategy(),
        change_reason=change_reason_strategy(),
    )
    def test_insert_sets_updated_at_to_current_utc(self, normalized, updated_by, source, change_reason):
        """Requirement 4.4: updated_at SHALL be set to current UTC time on insert."""
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        before = datetime.utcnow()
        result = service.upsert_price(
            db=mock_db, normalized=normalized,
            updated_by=updated_by, source=source, change_reason=change_reason,
        )
        after = datetime.utcnow()
        assert result.success is True
        record = mock_db.add.call_args_list[0][0][0]
        assert record.updated_at is not None
        assert before <= record.updated_at <= after

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        normalized=normalized_input_strategy(),
        updated_by=non_empty_username_strategy(),
        source=source_strategy(),
        change_reason=change_reason_strategy(),
    )
    def test_insert_stores_change_reason_when_provided(self, normalized, updated_by, source, change_reason):
        """Requirement 1.8: If change_reason is provided, it SHALL be stored on insert."""
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        result = service.upsert_price(
            db=mock_db, normalized=normalized,
            updated_by=updated_by, source=source, change_reason=change_reason,
        )
        assert result.success is True
        record = mock_db.add.call_args_list[0][0][0]
        if change_reason is not None:
            assert record.change_reason == change_reason
        else:
            assert record.change_reason is None


class TestProperty14AuditTrailUpdate:
    """Property 14: Audit Trail Completeness - UPDATE operations.

    *For any* update (modification of existing record):
    - updated_by SHALL be set and non-empty
    - updated_at SHALL be set to current UTC time
    - If change_reason is provided, it SHALL be stored

    **Validates: Requirements 1.7, 1.8, 2.7, 4.4**
    """

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        old_value=valid_value_strategy(),
        new_value=valid_value_strategy(),
        updated_by=non_empty_username_strategy(),
        source=source_strategy(),
    )
    def test_update_sets_updated_by(self, old_value, new_value, updated_by, source):
        """Requirement 1.7, 2.7: updated_by SHALL be set and non-empty on update."""
        assume(old_value != new_value)
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
                period="2025-01", value=new_value,
                status="provisional", price_type="PTF",
            ),
            updated_by=updated_by, source=source,
            change_reason="Test update for audit trail",
        )
        assert result.success is True, f"Update should succeed, got error: {result.error}"
        assert result.changed is True
        assert record.updated_by == updated_by
        assert record.updated_by is not None and record.updated_by != ""

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        old_value=valid_value_strategy(),
        new_value=valid_value_strategy(),
        updated_by=non_empty_username_strategy(),
        source=source_strategy(),
    )
    def test_update_sets_updated_at_to_current_utc(self, old_value, new_value, updated_by, source):
        """Requirement 4.4: updated_at SHALL be set to current UTC time on update."""
        assume(old_value != new_value)
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        record = MagicMock(spec=MarketReferencePrice)
        record.status = "provisional"
        record.ptf_tl_per_mwh = float(old_value)
        record.is_locked = 0
        mock_db.query.return_value.filter.return_value.first.return_value = record
        before = datetime.utcnow()
        result = service.upsert_price(
            db=mock_db,
            normalized=NormalizedMarketPriceInput(
                period="2025-01", value=new_value,
                status="provisional", price_type="PTF",
            ),
            updated_by=updated_by, source=source,
            change_reason="Test update for audit trail",
        )
        after = datetime.utcnow()
        assert result.success is True
        assert result.changed is True
        assert record.updated_at is not None
        assert before <= record.updated_at <= after

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        old_value=valid_value_strategy(),
        new_value=valid_value_strategy(),
        updated_by=non_empty_username_strategy(),
        source=source_strategy(),
        change_reason=st.sampled_from([
            "Ay sonu kesinlesme", "EPIAS duzeltme",
            "Veri hatasi duzeltme", "Manuel duzeltme",
        ]),
    )
    def test_update_stores_change_reason(self, old_value, new_value, updated_by, source, change_reason):
        """Requirement 1.8: If change_reason is provided, it SHALL be stored on update."""
        assume(old_value != new_value)
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
                period="2025-01", value=new_value,
                status="provisional", price_type="PTF",
            ),
            updated_by=updated_by, source=source,
            change_reason=change_reason,
        )
        assert result.success is True
        assert result.changed is True
        assert record.change_reason == change_reason

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        old_value=valid_value_strategy(),
        new_value=valid_value_strategy(),
        updated_by=non_empty_username_strategy(),
        source=source_strategy(),
    )
    def test_update_requires_change_reason(self, old_value, new_value, updated_by, source):
        """Requirement 2.7: Update without change_reason SHALL be rejected."""
        assume(old_value != new_value)
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
                period="2025-01", value=new_value,
                status="provisional", price_type="PTF",
            ),
            updated_by=updated_by, source=source,
            change_reason=None,
        )
        assert result.success is False
        assert result.error is not None
        assert result.error.error_code == ServiceErrorCode.CHANGE_REASON_REQUIRED


class TestProperty14AuditTrailStatusUpgrade:
    """Property 14: Audit Trail Completeness - Status upgrade operations.

    *For any* status upgrade (provisional -> final):
    - updated_by SHALL be set and non-empty
    - updated_at SHALL be set to current UTC time
    - change_reason SHALL be stored

    **Validates: Requirements 1.7, 1.8, 2.7, 4.4**
    """

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        value=valid_value_strategy(),
        new_value=valid_value_strategy(),
        updated_by=non_empty_username_strategy(),
        source=source_strategy(),
        change_reason=st.sampled_from([
            "Ay sonu kesinlesme", "Final onay", "Status upgrade",
        ]),
    )
    def test_status_upgrade_audit_fields(self, value, new_value, updated_by, source, change_reason):
        """Requirements 1.7, 1.8, 4.4: Audit fields SHALL be set on status upgrade."""
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        record = MagicMock(spec=MarketReferencePrice)
        record.status = "provisional"
        record.ptf_tl_per_mwh = float(value)
        record.is_locked = 0
        mock_db.query.return_value.filter.return_value.first.return_value = record
        before = datetime.utcnow()
        result = service.upsert_price(
            db=mock_db,
            normalized=NormalizedMarketPriceInput(
                period="2025-01", value=new_value,
                status="final", price_type="PTF",
            ),
            updated_by=updated_by, source=source,
            change_reason=change_reason,
        )
        after = datetime.utcnow()
        assert result.success is True, f"Status upgrade should succeed, got error: {result.error}"
        assert record.updated_by == updated_by
        assert record.updated_by is not None and record.updated_by != ""
        assert record.updated_at is not None
        assert before <= record.updated_at <= after
        assert record.change_reason == change_reason
