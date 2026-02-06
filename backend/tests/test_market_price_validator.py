"""
Unit tests for MarketPriceValidator.

Feature: ptf-admin-management
Tests validation logic for period, value, status, and complete entries.
"""

import pytest
from decimal import Decimal
from datetime import datetime
from zoneinfo import ZoneInfo

from app.market_price_validator import (
    MarketPriceValidator,
    ValidationResult,
    ValidationError,
    ErrorCode,
    NormalizedMarketPriceInput,
    validate_period,
    validate_value,
    validate_status,
    validate_entry,
    is_future_period,
    get_current_period,
    VALID_STATUSES,
    VALID_PRICE_TYPES,
    MIN_VALUE,
    MAX_VALUE,
    WARNING_MIN,
    WARNING_MAX,
    MAX_DECIMAL_PLACES,
)


class TestPeriodValidation:
    """Tests for period format validation."""
    
    def test_valid_period_format(self):
        """Valid YYYY-MM format should pass."""
        result = validate_period("2026-02")
        assert result.is_valid is True
        assert len(result.errors) == 0
    
    def test_valid_period_with_whitespace(self):
        """Whitespace should be trimmed."""
        result = validate_period("  2026-02  ")
        assert result.is_valid is True
    
    def test_invalid_period_slash_separator(self):
        """Slash separator should be rejected."""
        result = validate_period("2026/02")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_PERIOD_FORMAT
    
    def test_invalid_period_missing_leading_zero(self):
        """Missing leading zero should be rejected (no auto-fix)."""
        result = validate_period("2026-2")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_PERIOD_FORMAT
    
    def test_invalid_period_two_digit_year(self):
        """Two-digit year should be rejected."""
        result = validate_period("26-02")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_PERIOD_FORMAT
    
    def test_invalid_period_month_13(self):
        """Month 13 should be rejected."""
        result = validate_period("2026-13")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_PERIOD_FORMAT
    
    def test_invalid_period_month_00(self):
        """Month 00 should be rejected."""
        result = validate_period("2026-00")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_PERIOD_FORMAT
    
    def test_invalid_period_no_separator(self):
        """No separator should be rejected."""
        result = validate_period("202602")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_PERIOD_FORMAT
    
    def test_empty_period(self):
        """Empty period should be rejected."""
        result = validate_period("")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_PERIOD_FORMAT
    
    def test_whitespace_only_period(self):
        """Whitespace-only period should be rejected."""
        result = validate_period("   ")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_PERIOD_FORMAT
    
    def test_future_period_rejected(self):
        """Future period should be rejected."""
        # Use a period far in the future
        result = validate_period("2099-12")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.FUTURE_PERIOD
    
    def test_current_period_accepted(self):
        """Current period should be accepted."""
        current = get_current_period()
        result = validate_period(current)
        assert result.is_valid is True
    
    def test_past_period_accepted(self):
        """Past period should be accepted."""
        result = validate_period("2024-01")
        assert result.is_valid is True


class TestValueValidation:
    """Tests for PTF value validation."""
    
    def test_valid_value_string(self):
        """Valid decimal string should pass."""
        result, value = validate_value("2508.80")
        assert result.is_valid is True
        assert value == Decimal("2508.80")
    
    def test_valid_value_integer_string(self):
        """Integer string should pass."""
        result, value = validate_value("2500")
        assert result.is_valid is True
        assert value == Decimal("2500")
    
    def test_valid_value_float(self):
        """Float input should pass."""
        result, value = validate_value(2508.80)
        assert result.is_valid is True
        assert value == Decimal("2508.8")
    
    def test_valid_value_decimal(self):
        """Decimal input should pass."""
        result, value = validate_value(Decimal("2508.80"))
        assert result.is_valid is True
        assert value == Decimal("2508.80")
    
    def test_valid_value_with_whitespace(self):
        """Whitespace should be trimmed."""
        result, value = validate_value("  2508.80  ")
        assert result.is_valid is True
        assert value == Decimal("2508.80")
    
    def test_comma_decimal_rejected(self):
        """Comma as decimal separator should be rejected with specific error."""
        result, value = validate_value("2508,80")
        assert result.is_valid is False
        assert value is None
        assert result.errors[0].error_code == ErrorCode.DECIMAL_COMMA_NOT_ALLOWED
        assert "nokta" in result.errors[0].message.lower()
    
    def test_tr_format_rejected(self):
        """TR format (1.234,56) should be rejected."""
        result, value = validate_value("2.508,80")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.DECIMAL_COMMA_NOT_ALLOWED
    
    def test_scientific_notation_rejected(self):
        """Scientific notation should be rejected."""
        result, value = validate_value("1e3")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_DECIMAL_FORMAT
    
    def test_scientific_notation_uppercase_rejected(self):
        """Uppercase scientific notation should be rejected."""
        result, value = validate_value("1E3")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_DECIMAL_FORMAT
    
    def test_negative_value_rejected(self):
        """Negative value should be rejected."""
        result, value = validate_value("-100")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_DECIMAL_FORMAT
    
    def test_zero_value_rejected(self):
        """Zero value should be rejected."""
        result, value = validate_value("0")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.VALUE_OUT_OF_RANGE
    
    def test_value_above_max_rejected(self):
        """Value above MAX_VALUE should be rejected."""
        result, value = validate_value("10001")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.VALUE_OUT_OF_RANGE
    
    def test_value_at_max_accepted(self):
        """Value at MAX_VALUE should be accepted."""
        result, value = validate_value("10000")
        assert result.is_valid is True
        assert value == Decimal("10000")
    
    def test_too_many_decimals_rejected(self):
        """More than 2 decimal places should be rejected."""
        result, value = validate_value("2508.123")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.TOO_MANY_DECIMALS
    
    def test_two_decimals_accepted(self):
        """Exactly 2 decimal places should be accepted."""
        result, value = validate_value("2508.12")
        assert result.is_valid is True
        assert value == Decimal("2508.12")
    
    def test_one_decimal_accepted(self):
        """One decimal place should be accepted."""
        result, value = validate_value("2508.1")
        assert result.is_valid is True
        assert value == Decimal("2508.1")
    
    def test_none_value_rejected(self):
        """None value should be rejected."""
        result, value = validate_value(None)
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.VALUE_REQUIRED
    
    def test_empty_string_rejected(self):
        """Empty string should be rejected."""
        result, value = validate_value("")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.VALUE_REQUIRED
    
    def test_invalid_format_dot_only(self):
        """Just a dot should be rejected."""
        result, value = validate_value(".")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_DECIMAL_FORMAT
    
    def test_invalid_format_trailing_dot(self):
        """Trailing dot should be rejected."""
        result, value = validate_value("1234.")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_DECIMAL_FORMAT
    
    def test_invalid_format_leading_dot(self):
        """Leading dot should be rejected."""
        result, value = validate_value(".56")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_DECIMAL_FORMAT
    
    def test_invalid_format_multiple_dots(self):
        """Multiple dots should be rejected."""
        result, value = validate_value("1.2.3")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_DECIMAL_FORMAT
    
    def test_warning_low_value(self):
        """Value below WARNING_MIN should generate warning."""
        result, value = validate_value("500")
        assert result.is_valid is True
        assert len(result.warnings) == 1
        assert "düşük" in result.warnings[0].lower()
    
    def test_warning_high_value(self):
        """Value above WARNING_MAX should generate warning."""
        result, value = validate_value("6000")
        assert result.is_valid is True
        assert len(result.warnings) == 1
        assert "yüksek" in result.warnings[0].lower()
    
    def test_no_warning_normal_value(self):
        """Value in normal range should not generate warning."""
        result, value = validate_value("2500")
        assert result.is_valid is True
        assert len(result.warnings) == 0


class TestStatusValidation:
    """Tests for status enum validation."""
    
    def test_valid_status_provisional(self):
        """'provisional' should be accepted."""
        result = validate_status("provisional")
        assert result.is_valid is True
    
    def test_valid_status_final(self):
        """'final' should be accepted."""
        result = validate_status("final")
        assert result.is_valid is True
    
    def test_status_with_whitespace(self):
        """Whitespace should be trimmed."""
        result = validate_status("  final  ")
        assert result.is_valid is True
    
    def test_uppercase_status_rejected(self):
        """Uppercase status should be rejected (case-sensitive)."""
        result = validate_status("FINAL")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_STATUS
    
    def test_mixed_case_status_rejected(self):
        """Mixed case status should be rejected."""
        result = validate_status("Final")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_STATUS
    
    def test_unknown_status_rejected(self):
        """Unknown status should be rejected."""
        result = validate_status("pending")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_STATUS
    
    def test_empty_status_rejected(self):
        """Empty status should be rejected."""
        result = validate_status("")
        assert result.is_valid is False
        assert result.errors[0].error_code == ErrorCode.INVALID_STATUS


class TestEntryValidation:
    """Tests for complete entry validation."""
    
    def test_valid_entry(self):
        """Valid entry should pass and return normalized input."""
        result, normalized = validate_entry(
            period="2025-01",
            value="2508.80",
            status="final",
            price_type="PTF"
        )
        assert result.is_valid is True
        assert normalized is not None
        assert normalized.period == "2025-01"
        assert normalized.value == Decimal("2508.80")
        assert normalized.status == "final"
        assert normalized.price_type == "PTF"
    
    def test_entry_with_whitespace_normalized(self):
        """Entry with whitespace should be normalized."""
        result, normalized = validate_entry(
            period="  2025-01  ",
            value="  2508.80  ",
            status="  final  ",
            price_type="  PTF  "
        )
        assert result.is_valid is True
        assert normalized.period == "2025-01"
        assert normalized.status == "final"
        assert normalized.price_type == "PTF"
    
    def test_entry_default_price_type(self):
        """Empty price_type should default to PTF."""
        result, normalized = validate_entry(
            period="2025-01",
            value="2508.80",
            status="final",
            price_type=""
        )
        assert result.is_valid is True
        assert normalized.price_type == "PTF"
    
    def test_invalid_entry_multiple_errors(self):
        """Invalid entry should collect all errors."""
        result, normalized = validate_entry(
            period="invalid",
            value="invalid",
            status="invalid",
            price_type="PTF"
        )
        assert result.is_valid is False
        assert normalized is None
        assert len(result.errors) >= 3  # period, value, status errors
    
    def test_entry_with_warnings(self):
        """Entry with warnings should still be valid."""
        result, normalized = validate_entry(
            period="2025-01",
            value="500",  # Low value warning
            status="final",
            price_type="PTF"
        )
        assert result.is_valid is True
        assert normalized is not None
        assert len(result.warnings) == 1


class TestFuturePeriodCheck:
    """Tests for future period detection."""
    
    def test_future_period_detected(self):
        """Future period should be detected."""
        assert is_future_period("2099-12") is True
    
    def test_past_period_not_future(self):
        """Past period should not be detected as future."""
        assert is_future_period("2020-01") is False
    
    def test_current_period_not_future(self):
        """Current period should not be detected as future."""
        current = get_current_period()
        assert is_future_period(current) is False


class TestCurrentPeriod:
    """Tests for current period retrieval."""
    
    def test_current_period_format(self):
        """Current period should be in YYYY-MM format."""
        current = get_current_period()
        assert len(current) == 7
        assert current[4] == "-"
        year = int(current[:4])
        month = int(current[5:7])
        assert 2020 <= year <= 2100
        assert 1 <= month <= 12
    
    def test_current_period_uses_tr_timezone(self):
        """Current period should use Europe/Istanbul timezone."""
        # This test verifies the timezone is being used
        # by checking the period matches TR time
        current = get_current_period()
        now_tr = datetime.now(ZoneInfo("Europe/Istanbul"))
        expected = now_tr.strftime("%Y-%m")
        assert current == expected
