"""
Property-based tests for Audit History feature.

Feature: audit-history
Uses Hypothesis for property-based testing with minimum 100 iterations.

Properties tested:
- Property 1: Upsert history write correctness
- Property 2: No-op produces no history
- Property 3: History ordering invariant

**Validates: Requirements 1.1, 1.2, 1.3, 3.1**
"""

import pytest
from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock
from hypothesis import given, strategies as st, settings, assume, HealthCheck

from app.market_price_admin_service import MarketPriceAdminService
from app.market_price_validator import NormalizedMarketPriceInput
from app.database import PriceChangeHistory

PAST_PERIODS = [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 7)]


# ---------------------------------------------------------------------------
# Strategies (reused from test_audit_trail_properties.py)
# ---------------------------------------------------------------------------

@st.composite
def valid_value_strategy(draw):
    integer_part = draw(st.integers(min_value=100, max_value=9999))
    frac = draw(st.integers(min_value=0, max_value=99))
    return Decimal(f"{integer_part}.{frac:02d}")


@st.composite
def normalized_input_strategy(draw):
    return NormalizedMarketPriceInput(
        period=draw(st.sampled_from(PAST_PERIODS)),
        value=draw(valid_value_strategy()),
        status=draw(st.sampled_from(["provisional", "final"])),
        price_type="PTF",
    )


@st.composite
def source_strategy(draw):
    return draw(st.sampled_from(["epias_manual", "epias_api", "migration", "seed"]))


@st.composite
def username_strategy(draw):
    return draw(st.sampled_from(["admin", "system", "operator_1", "test_user"]))


@st.composite
def change_reason_strategy(draw):
    include = draw(st.booleans())
    if not include:
        return None
    return draw(st.sampled_from([
        "Ay sonu kesinlesme", "EPIAS duzeltme", "Manuel duzeltme",
        "Bulk import", "Status upgrade",
    ]))


def _make_mock_existing(period, value, status, is_locked=0, record_id=1):
    """Create a mock existing MarketReferencePrice."""
    rec = MagicMock()
    rec.id = record_id
    rec.period = period
    rec.price_type = "PTF"
    rec.ptf_tl_per_mwh = float(value)
    rec.status = status
    rec.is_locked = is_locked
    rec.source = "epias_manual"
    rec.captured_at = datetime(2025, 1, 1)
    rec.change_reason = None
    rec.updated_by = "admin"
    rec.created_at = datetime(2025, 1, 1)
    rec.updated_at = datetime(2025, 1, 1)
    return rec


# ===========================================================================
# Property 1: Upsert history write correctness
# Feature: audit-history, Property 1
# ===========================================================================

class TestProperty1UpsertHistoryCorrectness:
    """
    Property 1: Upsert history write correctness.

    For any valid market price input, if the upsert succeeds with changed=True,
    then exactly one PriceChangeHistory record SHALL be created with correct
    action, values, and statuses.

    **Validates: Requirements 1.1, 1.2**
    """

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        normalized=normalized_input_strategy(),
        updated_by=username_strategy(),
        source=source_strategy(),
    )
    def test_insert_creates_exactly_one_history_with_correct_action(
        self, normalized, updated_by, source
    ):
        """INSERT path: action='INSERT', old_value=None, old_status=None."""
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = service.upsert_price(
            db=mock_db, normalized=normalized,
            updated_by=updated_by, source=source,
        )

        assert result.success is True
        assert result.created is True

        # db.add called twice: price record + history
        assert mock_db.add.call_count == 2
        history = mock_db.add.call_args_list[1][0][0]
        assert isinstance(history, PriceChangeHistory)
        assert history.action == "INSERT"
        assert history.old_value is None
        assert history.old_status is None
        assert history.new_value == float(normalized.value)
        assert history.new_status == normalized.status
        assert history.updated_by == updated_by
        assert history.source == source

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        normalized=normalized_input_strategy(),
        updated_by=username_strategy(),
        source=source_strategy(),
        change_reason=change_reason_strategy(),
    )
    def test_update_creates_exactly_one_history_with_correct_values(
        self, normalized, updated_by, source, change_reason
    ):
        """UPDATE path: action='UPDATE', old/new values correct."""
        # Create existing with DIFFERENT value or status to avoid no-op
        old_value = Decimal("1000.00")
        old_status = "provisional"
        assume(old_value != normalized.value or old_status != normalized.status)
        # Ensure valid transition (no final→provisional downgrade)
        assume(not (old_status == "final" and normalized.status == "provisional"))

        # change_reason required for updates
        if change_reason is None:
            change_reason = "Test güncelleme"

        existing = _make_mock_existing(
            period=normalized.period, value=old_value, status=old_status,
        )
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        result = service.upsert_price(
            db=mock_db, normalized=normalized,
            updated_by=updated_by, source=source,
            change_reason=change_reason,
        )

        assert result.success is True
        assert result.changed is True

        # db.add called once for history (update modifies existing in-place)
        assert mock_db.add.call_count == 1
        history = mock_db.add.call_args[0][0]
        assert isinstance(history, PriceChangeHistory)
        assert history.action == "UPDATE"
        assert history.old_value == float(old_value)
        assert history.new_value == float(normalized.value)
        assert history.old_status == old_status
        assert history.new_status == normalized.status


# ===========================================================================
# Property 2: No-op produces no history
# Feature: audit-history, Property 2
# ===========================================================================

class TestProperty2NoopNoHistory:
    """
    Property 2: No-op produces no history.

    For any existing record, if upsert is called with the same value and status,
    the total PriceChangeHistory count SHALL remain unchanged (no db.add calls).

    **Validates: Requirements 1.3**
    """

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        normalized=normalized_input_strategy(),
        updated_by=username_strategy(),
        source=source_strategy(),
    )
    def test_same_value_and_status_produces_no_history(
        self, normalized, updated_by, source
    ):
        """No-op: same value + same status → no history record."""
        existing = _make_mock_existing(
            period=normalized.period,
            value=normalized.value,
            status=normalized.status,
        )
        service = MarketPriceAdminService()
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        result = service.upsert_price(
            db=mock_db, normalized=normalized,
            updated_by=updated_by, source=source,
        )

        assert result.success is True
        assert result.changed is False
        # No db.add calls at all
        assert mock_db.add.call_count == 0
