"""
Property-based tests for MarketPriceValidator.

Feature: ptf-admin-management
Uses Hypothesis for property-based testing with minimum 100 iterations.

Properties tested:
- Property 1: Period Format Validation
- Property 2: PTF Value Bounds Validation
- Property 3: Status Enum Validation
"""

import pytest
from decimal import Decimal
from hypothesis import given, strategies as st, settings, assume

from app.market_price_validator import (
    MarketPriceValidator,
    ValidationResult,
    ErrorCode,
    validate_period,
    validate_value,
    validate_status,
    validate_entry,
    get_current_period,
    VALID_STATUSES,
    MIN_VALUE,
    MAX_VALUE,
    WARNING_MIN,
    WARNING_MAX,
    MAX_DECIMAL_PLACES,
    PERIOD_REGEX,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Strategies for generating test data
# ═══════════════════════════════════════════════════════════════════════════════

@st.composite
def valid_period_strategy(draw):
    """Generate valid YYYY-MM periods (not future)."""
    # Generate years from 2020 to current year
    current = get_current_period()
    current_year = int(current[:4])
    current_month = int(current[5:7])
    
    year = draw(st.integers(min_value=2020, max_value=current_year))
    
    if year == current_year:
        month = draw(st.integers(min_value=1, max_value=current_month))
    else:
        month = draw(st.integers(min_value=1, max_value=12))
    
    return f"{year}-{month:02d}"


@st.composite
def invalid_period_format_strategy(draw):
    """Generate invalid period formats."""
    choice = draw(st.integers(min_value=0, max_value=7))
    
    if choice == 0:
        # Slash separator
        year = draw(st.integers(min_value=2020, max_value=2030))
        month = draw(st.integers(min_value=1, max_value=12))
        return f"{year}/{month:02d}"
    elif choice == 1:
        # Missing leading zero
        year = draw(st.integers(min_value=2020, max_value=2030))
        month = draw(st.integers(min_value=1, max_value=9))
        return f"{year}-{month}"
    elif choice == 2:
        # Two-digit year
        year = draw(st.integers(min_value=20, max_value=30))
        month = draw(st.integers(min_value=1, max_value=12))
        return f"{year}-{month:02d}"
    elif choice == 3:
        # Invalid month (13-99)
        year = draw(st.integers(min_value=2020, max_value=2030))
        month = draw(st.integers(min_value=13, max_value=99))
        return f"{year}-{month:02d}"
    elif choice == 4:
        # Month 00
        year = draw(st.integers(min_value=2020, max_value=2030))
        return f"{year}-00"
    elif choice == 5:
        # No separator
        year = draw(st.integers(min_value=2020, max_value=2030))
        month = draw(st.integers(min_value=1, max_value=12))
        return f"{year}{month:02d}"
    elif choice == 6:
        # Random string
        return draw(st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=('L', 'N'))))
    else:
        # Empty or whitespace
        return draw(st.sampled_from(["", "   ", "\t", "\n"]))


@st.composite
def valid_value_strategy(draw):
    """Generate valid PTF values as strings."""
    # Generate value in valid range with max 2 decimals
    integer_part = draw(st.integers(min_value=1, max_value=9999))
    decimal_part = draw(st.integers(min_value=0, max_value=99))
    
    if decimal_part == 0:
        return str(integer_part)
    else:
        return f"{integer_part}.{decimal_part:02d}".rstrip("0")


@st.composite
def comma_decimal_strategy(draw):
    """Generate values with comma as decimal separator."""
    integer_part = draw(st.integers(min_value=1, max_value=9999))
    decimal_part = draw(st.integers(min_value=1, max_value=99))
    return f"{integer_part},{decimal_part:02d}"


@st.composite
def scientific_notation_strategy(draw):
    """Generate values in scientific notation."""
    base = draw(st.integers(min_value=1, max_value=9))
    exp = draw(st.integers(min_value=1, max_value=4))
    e_char = draw(st.sampled_from(["e", "E"]))
    return f"{base}{e_char}{exp}"


# ═══════════════════════════════════════════════════════════════════════════════
# Property 1: Period Format Validation
# **Validates: Requirements 3.1, 3.7**
#
# For any string input as period, the PTF_Validator SHALL accept it if and only
# if it matches the regex ^\d{4}-(0[1-9]|1[0-2])$ and is not a future period.
# ═══════════════════════════════════════════════════════════════════════════════

class TestProperty1PeriodFormatValidation:
    """
    Feature: ptf-admin-management, Property 1: Period Format Validation
    **Validates: Requirements 3.1, 3.7**
    """
    
    @settings(max_examples=100)
    @given(valid_period_strategy())
    def test_valid_period_format_accepted(self, period):
        """
        3.1: WHEN period matches YYYY-MM format AND is not future
        THEN validation SHALL pass.
        """
        result = validate_period(period)
        assert result.is_valid is True
        assert len(result.errors) == 0
    
    @settings(max_examples=100)
    @given(invalid_period_format_strategy())
    def test_invalid_period_format_rejected(self, period):
        """
        3.1: WHEN period does not match YYYY-MM format
        THEN validation SHALL fail with INVALID_PERIOD_FORMAT.
        """
        result = validate_period(period)
        # Should be invalid (either format error or future period error)
        assert result.is_valid is False
        error_codes = [e.error_code for e in result.errors]
        assert ErrorCode.INVALID_PERIOD_FORMAT in error_codes or ErrorCode.FUTURE_PERIOD in error_codes
    
    @settings(max_examples=100)
    @given(st.integers(min_value=2050, max_value=2099), st.integers(min_value=1, max_value=12))
    def test_future_period_rejected(self, year, month):
        """
        3.7: WHEN period is in the future
        THEN validation SHALL fail with FUTURE_PERIOD.
        """
        period = f"{year}-{month:02d}"
        result = validate_period(period)
        assert result.is_valid is False
        error_codes = [e.error_code for e in result.errors]
        assert ErrorCode.FUTURE_PERIOD in error_codes
    
    @settings(max_examples=100)
    @given(st.text(min_size=0, max_size=20))
    def test_whitespace_trimmed(self, whitespace):
        """
        3.1: WHEN period has leading/trailing whitespace
        THEN whitespace SHALL be trimmed before validation.
        """
        # Use a known valid period with random whitespace
        period = f"{whitespace}2024-01{whitespace}"
        result = validate_period(period)
        # Should pass if whitespace is properly trimmed
        # (unless whitespace contains invalid chars)
        if whitespace.strip() == "":
            assert result.is_valid is True
    
    @settings(max_examples=100)
    @given(st.integers(min_value=1, max_value=9))
    def test_single_digit_month_rejected(self, month):
        """
        3.1: WHEN month is single digit without leading zero
        THEN validation SHALL fail (no auto-correction).
        """
        period = f"2024-{month}"
        result = validate_period(period)
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_PERIOD_FORMAT


# ═══════════════════════════════════════════════════════════════════════════════
# Property 2: PTF Value Bounds Validation
# **Validates: Requirements 3.2, 3.3, 3.4**
#
# For any numeric input as ptf_value:
# - Values ≤ 0 SHALL be rejected with error
# - Values > 100000 SHALL be rejected with error
# - Values in (0, 1000) or (5000, 100000] SHALL be accepted with warning
# - Values in [1000, 5000] SHALL be accepted without warning
# ═══════════════════════════════════════════════════════════════════════════════

class TestProperty2PTFValueBoundsValidation:
    """
    Feature: ptf-admin-management, Property 2: PTF Value Bounds Validation
    **Validates: Requirements 3.2, 3.3, 3.4**
    """
    
    @settings(max_examples=100)
    @given(st.decimals(min_value=Decimal("0.01"), max_value=MAX_VALUE, places=2, allow_nan=False, allow_infinity=False))
    def test_valid_value_accepted(self, value):
        """
        3.2: WHEN value is in valid range (0, 10000]
        THEN validation SHALL pass.
        """
        result, parsed = validate_value(str(value))
        assert result.is_valid is True
        assert parsed == value
    
    @settings(max_examples=100)
    @given(st.decimals(min_value=Decimal("-1000"), max_value=Decimal("0"), places=2, allow_nan=False, allow_infinity=False))
    def test_zero_or_negative_rejected(self, value):
        """
        3.2: WHEN value is ≤ 0
        THEN validation SHALL fail with VALUE_OUT_OF_RANGE.
        """
        # Skip exact zero case handled separately
        assume(value <= 0)
        result, parsed = validate_value(str(value))
        # Negative values fail at parsing (regex), zero fails at bounds
        assert result.is_valid is False
    
    @settings(max_examples=100)
    @given(st.decimals(min_value=MAX_VALUE + Decimal("0.01"), max_value=Decimal("100000"), places=2, allow_nan=False, allow_infinity=False))
    def test_above_max_rejected(self, value):
        """
        3.3: WHEN value is > MAX_VALUE (10000)
        THEN validation SHALL fail with VALUE_OUT_OF_RANGE.
        """
        result, parsed = validate_value(str(value))
        assert result.is_valid is False
        error_codes = [e.error_code for e in result.errors]
        assert ErrorCode.VALUE_OUT_OF_RANGE in error_codes
    
    @settings(max_examples=100)
    @given(st.decimals(min_value=Decimal("0.01"), max_value=WARNING_MIN - Decimal("0.01"), places=2, allow_nan=False, allow_infinity=False))
    def test_low_value_warning(self, value):
        """
        3.4: WHEN value is in (0, 1000)
        THEN validation SHALL pass with warning.
        """
        result, parsed = validate_value(str(value))
        assert result.is_valid is True
        assert len(result.warnings) == 1
        assert "düşük" in result.warnings[0].lower()
    
    @settings(max_examples=100)
    @given(st.decimals(min_value=WARNING_MAX + Decimal("0.01"), max_value=MAX_VALUE, places=2, allow_nan=False, allow_infinity=False))
    def test_high_value_warning(self, value):
        """
        3.4: WHEN value is in (5000, 10000]
        THEN validation SHALL pass with warning.
        """
        result, parsed = validate_value(str(value))
        assert result.is_valid is True
        assert len(result.warnings) == 1
        assert "yüksek" in result.warnings[0].lower()
    
    @settings(max_examples=100)
    @given(st.decimals(min_value=WARNING_MIN, max_value=WARNING_MAX, places=2, allow_nan=False, allow_infinity=False))
    def test_normal_value_no_warning(self, value):
        """
        3.4: WHEN value is in [1000, 5000]
        THEN validation SHALL pass without warning.
        """
        result, parsed = validate_value(str(value))
        assert result.is_valid is True
        assert len(result.warnings) == 0
    
    @settings(max_examples=100)
    @given(comma_decimal_strategy())
    def test_comma_decimal_rejected(self, value):
        """
        9.3: WHEN value uses comma as decimal separator
        THEN validation SHALL fail with DECIMAL_COMMA_NOT_ALLOWED.
        """
        result, parsed = validate_value(value)
        assert result.is_valid is False
        error_codes = [e.error_code for e in result.errors]
        assert ErrorCode.DECIMAL_COMMA_NOT_ALLOWED in error_codes
    
    @settings(max_examples=100)
    @given(scientific_notation_strategy())
    def test_scientific_notation_rejected(self, value):
        """
        9.4: WHEN value uses scientific notation
        THEN validation SHALL fail with INVALID_DECIMAL_FORMAT.
        """
        result, parsed = validate_value(value)
        assert result.is_valid is False
        error_codes = [e.error_code for e in result.errors]
        assert ErrorCode.INVALID_DECIMAL_FORMAT in error_codes
    
    @settings(max_examples=100)
    @given(st.integers(min_value=1, max_value=9999), st.integers(min_value=100, max_value=99999))
    def test_too_many_decimals_rejected(self, integer_part, decimal_part):
        """
        9.1: WHEN value has more than 2 decimal places
        THEN validation SHALL fail with TOO_MANY_DECIMALS.
        """
        # Create value with 3+ decimal places
        value = f"{integer_part}.{decimal_part}"
        result, parsed = validate_value(value)
        assert result.is_valid is False
        error_codes = [e.error_code for e in result.errors]
        assert ErrorCode.TOO_MANY_DECIMALS in error_codes


# ═══════════════════════════════════════════════════════════════════════════════
# Property 3: Status Enum Validation
# **Validates: Requirements 3.6**
#
# For any string input as status, the PTF_Validator SHALL accept it if and only
# if it equals "provisional" or "final" (case-sensitive).
# ═══════════════════════════════════════════════════════════════════════════════

class TestProperty3StatusEnumValidation:
    """
    Feature: ptf-admin-management, Property 3: Status Enum Validation
    **Validates: Requirements 3.6**
    """
    
    @settings(max_examples=100)
    @given(st.sampled_from(list(VALID_STATUSES)))
    def test_valid_status_accepted(self, status):
        """
        3.6: WHEN status is in valid set {provisional, final}
        THEN validation SHALL pass.
        """
        result = validate_status(status)
        assert result.is_valid is True
        assert len(result.errors) == 0
    
    @settings(max_examples=100)
    @given(st.text(min_size=1, max_size=20).filter(lambda s: s.strip().lower() not in VALID_STATUSES))
    def test_invalid_status_rejected(self, status):
        """
        3.6: WHEN status is not in valid set
        THEN validation SHALL fail with INVALID_STATUS.
        """
        result = validate_status(status)
        assert result.is_valid is False
        error_codes = [e.error_code for e in result.errors]
        assert ErrorCode.INVALID_STATUS in error_codes
    
    @settings(max_examples=100)
    @given(st.sampled_from(["PROVISIONAL", "FINAL", "Provisional", "Final", "PROV", "FIN"]))
    def test_case_sensitive_rejection(self, status):
        """
        3.6: WHEN status has wrong case
        THEN validation SHALL fail (case-sensitive).
        """
        result = validate_status(status)
        assert result.is_valid is False
        error_codes = [e.error_code for e in result.errors]
        assert ErrorCode.INVALID_STATUS in error_codes
    
    @settings(max_examples=100)
    @given(st.sampled_from(list(VALID_STATUSES)), st.text(min_size=0, max_size=5, alphabet=" \t"))
    def test_whitespace_trimmed(self, status, whitespace):
        """
        3.6: WHEN status has leading/trailing whitespace
        THEN whitespace SHALL be trimmed before validation.
        """
        padded_status = f"{whitespace}{status}{whitespace}"
        result = validate_status(padded_status)
        assert result.is_valid is True
    
    @settings(max_examples=100)
    @given(st.sampled_from(["", "   ", "\t", "\n"]))
    def test_empty_status_rejected(self, status):
        """
        3.6: WHEN status is empty or whitespace-only
        THEN validation SHALL fail with INVALID_STATUS.
        """
        result = validate_status(status)
        assert result.is_valid is False
        error_codes = [e.error_code for e in result.errors]
        assert ErrorCode.INVALID_STATUS in error_codes


# ═══════════════════════════════════════════════════════════════════════════════
# Combined Entry Validation Properties
# ═══════════════════════════════════════════════════════════════════════════════

class TestCombinedEntryValidation:
    """Combined validation properties for complete entries."""
    
    @settings(max_examples=100)
    @given(
        valid_period_strategy(),
        st.decimals(min_value=WARNING_MIN, max_value=WARNING_MAX, places=2, allow_nan=False, allow_infinity=False),
        st.sampled_from(list(VALID_STATUSES))
    )
    def test_valid_entry_produces_normalized_output(self, period, value, status):
        """
        WHEN all fields are valid
        THEN validation SHALL pass AND return normalized input.
        """
        result, normalized = validate_entry(period, str(value), status)
        assert result.is_valid is True
        assert normalized is not None
        assert normalized.period == period.strip()
        assert normalized.value == value
        assert normalized.status == status.strip()
        assert normalized.price_type == "PTF"
    
    @settings(max_examples=100)
    @given(
        invalid_period_format_strategy(),
        comma_decimal_strategy(),
        st.text(min_size=1, max_size=10).filter(lambda s: s.strip().lower() not in VALID_STATUSES)
    )
    def test_invalid_entry_collects_all_errors(self, period, value, status):
        """
        WHEN multiple fields are invalid
        THEN validation SHALL collect all errors.
        """
        result, normalized = validate_entry(period, value, status)
        assert result.is_valid is False
        assert normalized is None
        # Should have at least one error (might have more)
        assert len(result.errors) >= 1
