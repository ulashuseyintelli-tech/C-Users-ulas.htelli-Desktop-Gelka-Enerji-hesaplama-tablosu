"""
Property-based tests for BulkImporter.

Feature: ptf-admin-management
Uses Hypothesis for property-based testing with minimum 100 iterations.

Properties tested:
- Property 11: Decimal Parsing
"""

import json
import pytest
from decimal import Decimal, ROUND_HALF_UP
from hypothesis import given, strategies as st, settings, HealthCheck

from app.bulk_importer import BulkImporter, ImportRow
from app.market_price_validator import (
    MarketPriceValidator,
    ErrorCode,
    ValidationResult,
    ValidationError,
    MAX_VALUE,
    MIN_VALUE,
    MAX_DECIMAL_PLACES,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Shared importer instance (stateless for parsing, safe to reuse)
# ═══════════════════════════════════════════════════════════════════════════════

def _make_importer() -> BulkImporter:
    """Create BulkImporter with real validator (no DB service needed for parsing)."""
    return BulkImporter(validator=MarketPriceValidator())


# ═══════════════════════════════════════════════════════════════════════════════
# Strategies for generating test data
# ═══════════════════════════════════════════════════════════════════════════════

# Valid period that won't be rejected as future
VALID_PERIOD = "2025-01"
VALID_STATUS = "final"


@st.composite
def dot_decimal_value_strategy(draw):
    """Generate valid numeric strings using dot (.) as decimal separator.

    Constrains to values within the valid PTF range with at most 2 decimal places.
    Format: digits.digits (e.g., "1942.90", "2508.80", "100.01")
    """
    # Generate integer part: 1 to 9999 (within MAX_VALUE range)
    integer_part = draw(st.integers(min_value=1, max_value=9999))
    # Generate decimal part: 0 to 2 decimal places
    decimal_places = draw(st.integers(min_value=0, max_value=2))

    if decimal_places == 0:
        return str(integer_part)
    elif decimal_places == 1:
        frac = draw(st.integers(min_value=0, max_value=9))
        return f"{integer_part}.{frac}"
    else:  # 2 decimal places
        frac = draw(st.integers(min_value=0, max_value=99))
        return f"{integer_part}.{frac:02d}"


@st.composite
def comma_decimal_value_strategy(draw):
    """Generate numeric strings using comma (,) as decimal separator.

    These should always be rejected by the importer.
    Covers patterns like: "2508,80", "1942,90", "2.508,80"
    """
    integer_part = draw(st.integers(min_value=1, max_value=9999))
    frac = draw(st.integers(min_value=0, max_value=99))

    pattern = draw(st.sampled_from([
        # Simple comma decimal: "2508,80"
        "simple",
        # European thousand+comma: "2.508,80"
        "european",
        # Just comma in number: "100,5"
        "single_frac",
    ]))

    if pattern == "simple":
        return f"{integer_part},{frac:02d}"
    elif pattern == "european":
        # European format with dot as thousand separator
        if integer_part >= 1000:
            thousands = integer_part // 1000
            remainder = integer_part % 1000
            return f"{thousands}.{remainder:03d},{frac:02d}"
        else:
            return f"{integer_part},{frac:02d}"
    else:  # single_frac
        single_frac = draw(st.integers(min_value=1, max_value=9))
        return f"{integer_part},{single_frac}"


@st.composite
def precision_value_strategy(draw):
    """Generate values specifically to test 2 decimal places precision.

    Generates values with exactly 0, 1, or 2 decimal places within valid range.
    """
    integer_part = draw(st.integers(min_value=1, max_value=5000))
    decimal_places = draw(st.sampled_from([0, 1, 2]))

    if decimal_places == 0:
        return (str(integer_part), Decimal(str(integer_part)))
    elif decimal_places == 1:
        frac = draw(st.integers(min_value=0, max_value=9))
        value_str = f"{integer_part}.{frac}"
        expected = Decimal(value_str)
        return (value_str, expected)
    else:
        frac = draw(st.integers(min_value=0, max_value=99))
        value_str = f"{integer_part}.{frac:02d}"
        expected = Decimal(value_str)
        return (value_str, expected)


# ═══════════════════════════════════════════════════════════════════════════════
# Property 11: Decimal Parsing
# Feature: ptf-admin-management, Property 11: Decimal Parsing
# **Validates: Requirements 9.3, 9.4, 9.1**
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperty11DecimalParsingCSV:
    """Property 11: Decimal Parsing - CSV format.

    *For any* numeric string in CSV import:
    - Strings with dot (.) as decimal separator SHALL be parsed correctly
    - Strings with comma (,) as decimal separator SHALL be rejected with error
    - Parsed values SHALL preserve 2 decimal places precision

    **Validates: Requirements 9.3, 9.4, 9.1**
    """

    @settings(max_examples=100)
    @given(value_str=dot_decimal_value_strategy())
    def test_dot_decimal_parsed_correctly_csv(self, value_str):
        """Requirement 9.3: Dot decimal separator SHALL be parsed correctly in CSV.

        For any valid numeric string with dot decimal separator,
        parse_csv SHALL produce a row where the value is correctly parsed
        and validation passes.
        """
        importer = _make_importer()
        csv_content = f"period,value,status\n{VALID_PERIOD},{value_str},{VALID_STATUS}"
        rows = importer.parse_csv(csv_content)

        assert len(rows) == 1
        row = rows[0]

        # The raw_value should preserve the original string
        assert row.raw_value == value_str

        # The parsed float value should match
        expected_float = float(value_str)
        assert row.value == pytest.approx(expected_float, rel=1e-9)

        # Validation should pass (no decimal format errors)
        assert row.validation_result is not None
        decimal_errors = [
            e for e in row.validation_result.errors
            if e.error_code in (
                ErrorCode.INVALID_DECIMAL_FORMAT,
                ErrorCode.DECIMAL_COMMA_NOT_ALLOWED,
            )
        ]
        assert decimal_errors == [], (
            f"Dot decimal '{value_str}' should not produce decimal format errors, "
            f"got: {[e.message for e in decimal_errors]}"
        )

    @settings(max_examples=100)
    @given(value_str=comma_decimal_value_strategy())
    def test_comma_decimal_rejected_csv(self, value_str):
        """Requirement 9.4: Comma decimal separator SHALL be rejected with error in CSV.

        For any numeric string with comma as decimal separator,
        parse_csv SHALL reject the row. In CSV format, commas in values
        break column parsing (value "1,00" splits into columns "1" and "00"),
        which causes either:
        - DECIMAL_COMMA_NOT_ALLOWED / INVALID_DECIMAL_FORMAT (if comma detected)
        - INVALID_STATUS or other errors (if CSV column misalignment occurs)

        Either way, the row SHALL NOT be accepted as valid.
        """
        importer = _make_importer()
        csv_content = f"period,value,status\n{VALID_PERIOD},{value_str},{VALID_STATUS}"
        rows = importer.parse_csv(csv_content)

        # CSV with comma-containing values may produce multiple rows due to
        # column misalignment, but at least one row should exist
        assert len(rows) >= 1

        # ALL resulting rows must be invalid - a comma decimal value
        # must never produce a valid import row
        for row in rows:
            assert row.validation_result is not None
            assert not row.validation_result.is_valid, (
                f"Comma decimal '{value_str}' should be rejected in CSV but row "
                f"{row.row_number} was accepted with value={row.value}, "
                f"status='{row.status}'"
            )

    @settings(max_examples=100)
    @given(data=precision_value_strategy())
    def test_precision_preserved_csv(self, data):
        """Requirement 9.1: Parsed values SHALL preserve 2 decimal places precision in CSV.

        For any valid numeric value with at most 2 decimal places,
        the parsed value SHALL preserve the exact decimal precision.
        """
        value_str, expected_decimal = data
        importer = _make_importer()

        csv_content = f"period,value,status\n{VALID_PERIOD},{value_str},{VALID_STATUS}"
        rows = importer.parse_csv(csv_content)

        assert len(rows) == 1
        row = rows[0]

        # The parsed float should be close to the expected decimal
        assert row.value == pytest.approx(float(expected_decimal), rel=1e-9)

        # Validation should pass (no precision errors)
        assert row.validation_result is not None
        precision_errors = [
            e for e in row.validation_result.errors
            if e.error_code == ErrorCode.TOO_MANY_DECIMALS
        ]
        assert precision_errors == [], (
            f"Value '{value_str}' with <= 2 decimal places should not produce "
            f"precision errors"
        )

        # Verify the value can be represented as DECIMAL(12,2) without loss
        parsed_decimal = Decimal(value_str)
        quantized = parsed_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        assert parsed_decimal == quantized, (
            f"Value '{value_str}' should be exactly representable with 2 decimal places"
        )


class TestProperty11DecimalParsingJSON:
    """Property 11: Decimal Parsing - JSON format.

    *For any* numeric string in JSON import:
    - Strings with dot (.) as decimal separator SHALL be parsed correctly
    - Strings with comma (,) as decimal separator SHALL be rejected with error
    - Parsed values SHALL preserve 2 decimal places precision

    **Validates: Requirements 9.3, 9.4, 9.1**
    """

    @settings(max_examples=100)
    @given(value_str=dot_decimal_value_strategy())
    def test_dot_decimal_parsed_correctly_json_string(self, value_str):
        """Requirement 9.3: Dot decimal separator SHALL be parsed correctly in JSON (string values).

        For any valid numeric string with dot decimal separator passed as JSON string,
        parse_json SHALL produce a row where the value is correctly parsed.
        """
        importer = _make_importer()
        json_content = json.dumps([{
            "period": VALID_PERIOD,
            "value": value_str,
            "status": VALID_STATUS,
        }])
        rows = importer.parse_json(json_content)

        assert len(rows) == 1
        row = rows[0]

        # The parsed value should match
        expected_float = float(value_str)
        assert row.value == pytest.approx(expected_float, rel=1e-9)

        # Validation should pass (no decimal format errors)
        assert row.validation_result is not None
        decimal_errors = [
            e for e in row.validation_result.errors
            if e.error_code in (
                ErrorCode.INVALID_DECIMAL_FORMAT,
                ErrorCode.DECIMAL_COMMA_NOT_ALLOWED,
            )
        ]
        assert decimal_errors == [], (
            f"Dot decimal '{value_str}' should not produce decimal format errors in JSON"
        )

    @settings(max_examples=100)
    @given(value_str=comma_decimal_value_strategy())
    def test_comma_decimal_rejected_json_string(self, value_str):
        """Requirement 9.4: Comma decimal separator SHALL be rejected with error in JSON (string values).

        For any numeric string with comma as decimal separator passed as JSON string,
        parse_json SHALL produce a row that fails validation.
        """
        importer = _make_importer()
        json_content = json.dumps([{
            "period": VALID_PERIOD,
            "value": value_str,
            "status": VALID_STATUS,
        }])
        rows = importer.parse_json(json_content)

        assert len(rows) == 1
        row = rows[0]

        # Validation must fail
        assert row.validation_result is not None
        assert not row.validation_result.is_valid, (
            f"Comma decimal '{value_str}' should be rejected in JSON but was accepted"
        )

        # Should have a decimal-related error
        error_codes = [e.error_code for e in row.validation_result.errors]
        assert any(
            code in (ErrorCode.DECIMAL_COMMA_NOT_ALLOWED, ErrorCode.INVALID_DECIMAL_FORMAT)
            for code in error_codes
        ), (
            f"Comma decimal '{value_str}' should produce decimal error in JSON, "
            f"got: {error_codes}"
        )

    @settings(max_examples=100)
    @given(data=precision_value_strategy())
    def test_precision_preserved_json_numeric(self, data):
        """Requirement 9.1: Parsed values SHALL preserve 2 decimal places precision in JSON (numeric values).

        For any valid numeric value with at most 2 decimal places passed as JSON number,
        the parsed value SHALL preserve the exact decimal precision.
        """
        value_str, expected_decimal = data
        numeric_value = float(value_str)
        importer = _make_importer()

        json_content = json.dumps([{
            "period": VALID_PERIOD,
            "value": numeric_value,
            "status": VALID_STATUS,
        }])
        rows = importer.parse_json(json_content)

        assert len(rows) == 1
        row = rows[0]

        # The parsed value should match
        assert row.value == pytest.approx(float(expected_decimal), rel=1e-9)

        # Validation should pass (no precision errors)
        assert row.validation_result is not None
        precision_errors = [
            e for e in row.validation_result.errors
            if e.error_code == ErrorCode.TOO_MANY_DECIMALS
        ]
        assert precision_errors == [], (
            f"Numeric value {numeric_value} with <= 2 decimal places should not "
            f"produce precision errors in JSON"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Strategies for Property 12: Bulk Import Mode Behavior
# ═══════════════════════════════════════════════════════════════════════════════

from unittest.mock import MagicMock
from app.market_price_admin_service import (
    MarketPriceAdminService,
    UpsertResult,
)


# Non-future periods for valid rows (past months that won't fail future-period check)
PAST_PERIODS = [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 7)]


@st.composite
def valid_import_row_strategy(draw, row_number: int):
    """Generate a valid ImportRow that will pass validation.

    Uses known-good periods, values in the accepted range, and valid statuses.
    """
    period = draw(st.sampled_from(PAST_PERIODS))
    # Value in valid range: (0.01, 10000] with at most 2 decimal places
    integer_part = draw(st.integers(min_value=1, max_value=9999))
    frac = draw(st.integers(min_value=0, max_value=99))
    value = float(f"{integer_part}.{frac:02d}")
    status = draw(st.sampled_from(["provisional", "final"]))

    row = ImportRow(
        row_number=row_number,
        period=period,
        value=value,
        status=status,
        raw_value=f"{integer_part}.{frac:02d}",
    )
    # Pre-validate using real validator
    validator = MarketPriceValidator()
    row.validation_result = validator.validate_entry(period, value, status)[0]
    return row


@st.composite
def invalid_import_row_strategy(draw, row_number: int):
    """Generate an ImportRow that will fail validation.

    Uses various invalid inputs: bad periods, out-of-range values, bad statuses.
    """
    invalid_type = draw(st.sampled_from([
        "bad_period",
        "negative_value",
        "zero_value",
        "over_max_value",
        "bad_status",
        "future_period",
    ]))

    if invalid_type == "bad_period":
        period = draw(st.sampled_from(["2025-13", "2025-00", "abc", "2025/01", ""]))
        value = 2000.0
        status = "final"
    elif invalid_type == "negative_value":
        period = "2025-01"
        value = draw(st.floats(min_value=-10000, max_value=-0.01))
        status = "final"
    elif invalid_type == "zero_value":
        period = "2025-01"
        value = 0.0
        status = "final"
    elif invalid_type == "over_max_value":
        period = "2025-01"
        value = draw(st.floats(min_value=10001, max_value=999999))
        status = "final"
    elif invalid_type == "bad_status":
        period = "2025-01"
        value = 2000.0
        status = draw(st.sampled_from(["FINAL", "Provisional", "active", "unknown", ""]))
    else:  # future_period
        period = "2099-12"
        value = 2000.0
        status = "final"

    row = ImportRow(
        row_number=row_number,
        period=period,
        value=value,
        status=status,
        raw_value=str(value),
    )
    # Pre-validate using real validator
    validator = MarketPriceValidator()
    row.validation_result = validator.validate_entry(period, value, status)[0]
    return row


@st.composite
def mixed_import_rows_strategy(draw):
    """Generate a list of ImportRows with a guaranteed mix of valid and invalid rows.

    Returns (rows, expected_valid_count, expected_invalid_count).
    At least 1 valid and 1 invalid row are guaranteed.
    """
    # Guarantee at least 1 valid and 1 invalid
    num_valid = draw(st.integers(min_value=1, max_value=8))
    num_invalid = draw(st.integers(min_value=1, max_value=8))

    rows = []
    row_number = 1

    # Generate valid rows
    valid_count = 0
    for _ in range(num_valid):
        row = draw(valid_import_row_strategy(row_number))
        rows.append(row)
        row_number += 1
        valid_count += 1

    # Generate invalid rows
    invalid_count = 0
    for _ in range(num_invalid):
        row = draw(invalid_import_row_strategy(row_number))
        rows.append(row)
        row_number += 1
        invalid_count += 1

    # Shuffle to mix valid and invalid
    shuffled = draw(st.permutations(rows))
    # Re-number after shuffle
    for idx, row in enumerate(shuffled, start=1):
        row.row_number = idx

    return (list(shuffled), valid_count, invalid_count)


@st.composite
def all_valid_rows_strategy(draw):
    """Generate a list of only valid ImportRows."""
    num_rows = draw(st.integers(min_value=1, max_value=10))
    rows = []
    for i in range(1, num_rows + 1):
        row = draw(valid_import_row_strategy(i))
        rows.append(row)
    return rows


@st.composite
def all_invalid_rows_strategy(draw):
    """Generate a list of only invalid ImportRows."""
    num_rows = draw(st.integers(min_value=1, max_value=10))
    rows = []
    for i in range(1, num_rows + 1):
        row = draw(invalid_import_row_strategy(i))
        rows.append(row)
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# Property 12: Bulk Import Mode Behavior
# Feature: ptf-admin-management, Property 12: Bulk Import Mode Behavior
# **Validates: Requirements 5.4, 5.5, 5.7**
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperty12BulkImportModeBehavior:
    """Property 12: Bulk Import Mode Behavior.

    *For any* bulk import with mixed valid/invalid rows:
    - In default mode (strict_mode=false): valid rows SHALL be imported, invalid rows SHALL be skipped
    - In strict mode (strict_mode=true): if any row is invalid, entire batch SHALL be rejected
    - Import result SHALL accurately report imported_count, skipped_count, error_count

    **Validates: Requirements 5.4, 5.5, 5.7**
    """

    def _make_importer_with_mock_service(self):
        """Create BulkImporter with real validator and mock service that succeeds."""
        mock_service = MagicMock(spec=MarketPriceAdminService)
        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True,
        )
        importer = BulkImporter(
            validator=MarketPriceValidator(),
            service=mock_service,
        )
        return importer, mock_service

    def _make_mock_db(self):
        """Create a mock DB session."""
        return MagicMock()

    # ───────────────────────────────────────────────────────────────────────
    # Default mode (strict_mode=False): valid imported, invalid skipped
    # Requirement 5.4
    # ───────────────────────────────────────────────────────────────────────

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(data=mixed_import_rows_strategy())
    def test_default_mode_valid_rows_imported_invalid_skipped(self, data):
        """Requirement 5.4: In default mode, valid rows SHALL be imported, invalid rows SHALL be skipped.

        For any mixed batch of valid and invalid rows with strict_mode=False:
        - accepted_count SHALL equal the number of valid rows
        - rejected_count SHALL be >= the number of pre-validation-invalid rows
        - No invalid row SHALL be imported
        """
        rows, expected_valid, expected_invalid = data
        importer, mock_service = self._make_importer_with_mock_service()
        mock_db = self._make_mock_db()

        result = importer.apply(
            mock_db, rows, updated_by="test_admin",
            strict_mode=False,
        )

        # Valid rows should be imported (service called for each valid row)
        assert result.accepted_count == expected_valid, (
            f"Expected {expected_valid} accepted, got {result.accepted_count}. "
            f"Total rows: {len(rows)}, invalid: {expected_invalid}"
        )

        # Invalid rows should be skipped/rejected
        assert result.rejected_count >= expected_invalid, (
            f"Expected at least {expected_invalid} rejected, got {result.rejected_count}"
        )

        # Total should add up
        assert result.accepted_count + result.rejected_count >= len(rows), (
            f"accepted ({result.accepted_count}) + rejected ({result.rejected_count}) "
            f"should be >= total rows ({len(rows)})"
        )

    # ───────────────────────────────────────────────────────────────────────
    # Strict mode (strict_mode=True): entire batch rejected if any invalid
    # Requirement 5.5
    # ───────────────────────────────────────────────────────────────────────

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(data=mixed_import_rows_strategy())
    def test_strict_mode_rejects_entire_batch_on_any_invalid(self, data):
        """Requirement 5.5: In strict mode, if any row is invalid, entire batch SHALL be rejected.

        For any mixed batch containing at least one invalid row with strict_mode=True:
        - success SHALL be False
        - accepted_count SHALL be 0
        - rejected_count SHALL equal total number of rows
        """
        rows, expected_valid, expected_invalid = data
        importer, mock_service = self._make_importer_with_mock_service()
        mock_db = self._make_mock_db()

        result = importer.apply(
            mock_db, rows, updated_by="test_admin",
            strict_mode=True,
        )

        # Entire batch must be rejected
        assert result.success is False, (
            "Strict mode with invalid rows should return success=False"
        )
        assert result.accepted_count == 0, (
            f"Strict mode should accept 0 rows, got {result.accepted_count}"
        )
        assert result.rejected_count == len(rows), (
            f"Strict mode should reject all {len(rows)} rows, got {result.rejected_count}"
        )

        # Service should NOT have been called for any upsert
        mock_service.upsert_price.assert_not_called()

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(rows=all_valid_rows_strategy())
    def test_strict_mode_accepts_all_when_all_valid(self, rows):
        """Requirement 5.5: In strict mode, if all rows are valid, batch SHALL be accepted.

        For any batch where all rows are valid with strict_mode=True:
        - success SHALL be True
        - accepted_count SHALL equal total number of rows
        - rejected_count SHALL be 0
        """
        importer, mock_service = self._make_importer_with_mock_service()
        mock_db = self._make_mock_db()

        result = importer.apply(
            mock_db, rows, updated_by="test_admin",
            strict_mode=True,
        )

        assert result.accepted_count == len(rows), (
            f"All-valid strict mode should accept all {len(rows)} rows, "
            f"got {result.accepted_count}"
        )
        assert result.rejected_count == 0, (
            f"All-valid strict mode should reject 0 rows, got {result.rejected_count}"
        )

    # ───────────────────────────────────────────────────────────────────────
    # Result accuracy: imported_count, skipped_count, error_count
    # Requirement 5.7
    # ───────────────────────────────────────────────────────────────────────

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(data=mixed_import_rows_strategy())
    def test_result_counts_accurate_default_mode(self, data):
        """Requirement 5.7: Import result SHALL accurately report counts in default mode.

        For any mixed batch in default mode:
        - imported_count (alias) SHALL equal accepted_count
        - skipped_count (alias) SHALL equal rejected_count
        - error_count (alias) SHALL equal rejected_count
        - rejected_rows SHALL have exactly rejected_count entries
        """
        rows, expected_valid, expected_invalid = data
        importer, mock_service = self._make_importer_with_mock_service()
        mock_db = self._make_mock_db()

        result = importer.apply(
            mock_db, rows, updated_by="test_admin",
            strict_mode=False,
        )

        # Alias consistency
        assert result.imported_count == result.accepted_count, (
            "imported_count alias should equal accepted_count"
        )
        assert result.skipped_count == result.rejected_count, (
            "skipped_count alias should equal rejected_count"
        )
        assert result.error_count == result.rejected_count, (
            "error_count alias should equal rejected_count"
        )

        # rejected_rows list length matches rejected_count
        assert len(result.rejected_rows) == result.rejected_count, (
            f"rejected_rows length ({len(result.rejected_rows)}) should equal "
            f"rejected_count ({result.rejected_count})"
        )

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(data=mixed_import_rows_strategy())
    def test_result_counts_accurate_strict_mode(self, data):
        """Requirement 5.7: Import result SHALL accurately report counts in strict mode.

        For any mixed batch in strict mode:
        - imported_count SHALL be 0
        - rejected_count SHALL equal total rows
        - rejected_rows SHALL contain at least the validation errors
        """
        rows, expected_valid, expected_invalid = data
        importer, mock_service = self._make_importer_with_mock_service()
        mock_db = self._make_mock_db()

        result = importer.apply(
            mock_db, rows, updated_by="test_admin",
            strict_mode=True,
        )

        # Counts must be accurate
        assert result.imported_count == 0, (
            f"Strict mode with invalid rows: imported_count should be 0, "
            f"got {result.imported_count}"
        )
        assert result.rejected_count == len(rows), (
            f"Strict mode: rejected_count should be {len(rows)}, "
            f"got {result.rejected_count}"
        )

        # rejected_rows should contain at least the validation errors
        assert len(result.rejected_rows) >= expected_invalid, (
            f"rejected_rows should have at least {expected_invalid} entries, "
            f"got {len(result.rejected_rows)}"
        )

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(rows=all_invalid_rows_strategy())
    def test_default_mode_all_invalid_rejects_all(self, rows):
        """Requirement 5.4: In default mode with all invalid rows, all SHALL be rejected.

        For any batch where all rows are invalid with strict_mode=False:
        - accepted_count SHALL be 0
        - rejected_count SHALL equal total number of rows
        """
        importer, mock_service = self._make_importer_with_mock_service()
        mock_db = self._make_mock_db()

        result = importer.apply(
            mock_db, rows, updated_by="test_admin",
            strict_mode=False,
        )

        assert result.accepted_count == 0, (
            f"All-invalid default mode should accept 0 rows, got {result.accepted_count}"
        )
        assert result.rejected_count >= len(rows), (
            f"All-invalid default mode should reject all {len(rows)} rows, "
            f"got {result.rejected_count}"
        )

        # Service should NOT have been called
        mock_service.upsert_price.assert_not_called()



# ═══════════════════════════════════════════════════════════════════════════════
# Strategies for Property 13: Import Preview Accuracy
# ═══════════════════════════════════════════════════════════════════════════════

from decimal import Decimal as D


class FakeExistingRecord:
    """Lightweight fake for MarketReferencePrice used in preview DB mock."""
    def __init__(self, period: str, ptf_tl_per_mwh: float, status: str, is_locked: int = 0):
        self.period = period
        self.ptf_tl_per_mwh = ptf_tl_per_mwh
        self.status = status
        self.is_locked = is_locked
        self.price_type = "PTF"


# Possible DB states for a given period
DB_STATE_NEW = "new"              # period does not exist in DB
DB_STATE_SAME = "same"            # period exists, same value and status
DB_STATE_DIFF_VALUE = "diff_value"  # period exists, different value
DB_STATE_DIFF_STATUS = "diff_status"  # period exists, different status (provisional→final upgrade)
DB_STATE_LOCKED = "locked"        # period exists and is locked
DB_STATE_FINAL = "final_no_force" # period exists as final, row tries to change value without force_update


@st.composite
def preview_scenario_strategy(draw):
    """Generate a complete preview scenario: rows + DB state + force_update flag.

    Returns a dict with:
    - rows: List[ImportRow] (all valid, pre-validated)
    - db_records: dict mapping period -> FakeExistingRecord or None
    - force_update: bool
    - expected: dict with expected counts (new_records, updates, unchanged, final_conflicts)

    The strategy generates rows with known DB states so we can compute
    expected preview counts deterministically.
    """
    num_rows = draw(st.integers(min_value=1, max_value=15))
    force_update = draw(st.booleans())

    rows = []
    db_records = {}
    expected_new = 0
    expected_updates = 0
    expected_unchanged = 0
    expected_final_conflicts = 0

    # Use unique periods to avoid ambiguity
    used_periods = set()

    for i in range(1, num_rows + 1):
        # Pick a unique period from the past
        period_idx = draw(st.integers(min_value=0, max_value=len(PAST_PERIODS) - 1))
        # Ensure uniqueness by cycling through available periods
        attempts = 0
        while PAST_PERIODS[period_idx % len(PAST_PERIODS)] in used_periods and attempts < len(PAST_PERIODS):
            period_idx += 1
            attempts += 1
        if attempts >= len(PAST_PERIODS):
            # All periods used, skip this row
            continue
        period = PAST_PERIODS[period_idx % len(PAST_PERIODS)]
        used_periods.add(period)

        # Generate a valid value (integer part 1-9999, 2 decimal places)
        int_part = draw(st.integers(min_value=1, max_value=9999))
        frac = draw(st.integers(min_value=0, max_value=99))
        value = float(f"{int_part}.{frac:02d}")
        value_str = f"{int_part}.{frac:02d}"

        row_status = draw(st.sampled_from(["provisional", "final"]))

        # Decide DB state for this period
        db_state = draw(st.sampled_from([
            DB_STATE_NEW,
            DB_STATE_SAME,
            DB_STATE_DIFF_VALUE,
            DB_STATE_DIFF_STATUS,
            DB_STATE_LOCKED,
            DB_STATE_FINAL,
        ]))

        row = ImportRow(
            row_number=i,
            period=period,
            value=value,
            status=row_status,
            raw_value=value_str,
        )
        # Mark as valid
        row.validation_result = ValidationResult(is_valid=True, errors=[], warnings=[])
        rows.append(row)

        # Build DB record and compute expected outcome
        if db_state == DB_STATE_NEW:
            db_records[period] = None
            expected_new += 1

        elif db_state == DB_STATE_SAME:
            # Existing record with same value and same status
            db_records[period] = FakeExistingRecord(
                period=period,
                ptf_tl_per_mwh=value,
                status=row_status,
                is_locked=0,
            )
            expected_unchanged += 1

        elif db_state == DB_STATE_DIFF_VALUE:
            # Existing record with different value, provisional status (no conflict)
            diff_value = value + 100.0 if value < 9900 else value - 100.0
            db_records[period] = FakeExistingRecord(
                period=period,
                ptf_tl_per_mwh=diff_value,
                status="provisional",
                is_locked=0,
            )
            expected_updates += 1

        elif db_state == DB_STATE_DIFF_STATUS:
            # Existing record with same value but different status (provisional in DB, final in row = upgrade)
            # To ensure this is an update (not a conflict), existing must be provisional
            db_records[period] = FakeExistingRecord(
                period=period,
                ptf_tl_per_mwh=value,
                status="provisional",
                is_locked=0,
            )
            # Override row status to final so it's a status upgrade
            row.status = "final"
            # Same value, different status → update
            expected_updates += 1

        elif db_state == DB_STATE_LOCKED:
            # Existing record that is locked
            db_records[period] = FakeExistingRecord(
                period=period,
                ptf_tl_per_mwh=value,
                status="final",
                is_locked=1,
            )
            expected_final_conflicts += 1

        elif db_state == DB_STATE_FINAL:
            # Existing final record, row tries to change value without force_update
            diff_value = value + 50.0 if value < 9950 else value - 50.0
            db_records[period] = FakeExistingRecord(
                period=period,
                ptf_tl_per_mwh=diff_value,
                status="final",
                is_locked=0,
            )
            # Row also final with different value
            row.status = "final"
            if force_update:
                # With force_update, final→final diff value is allowed → update
                expected_updates += 1
            else:
                # Without force_update, final→final diff value → final_conflict
                expected_final_conflicts += 1

    return {
        "rows": rows,
        "db_records": db_records,
        "force_update": force_update,
        "expected_new": expected_new,
        "expected_updates": expected_updates,
        "expected_unchanged": expected_unchanged,
        "expected_final_conflicts": expected_final_conflicts,
    }


@st.composite
def preview_with_invalid_rows_strategy(draw):
    """Generate a preview scenario that includes both valid and invalid rows.

    This tests that invalid rows are correctly counted and don't affect
    the new/update/unchanged/final_conflicts counts.
    """
    # Generate a base valid scenario
    scenario = draw(preview_scenario_strategy())
    rows = list(scenario["rows"])
    num_invalid = draw(st.integers(min_value=1, max_value=5))

    start_row_num = len(rows) + 1
    for j in range(num_invalid):
        invalid_row = ImportRow(
            row_number=start_row_num + j,
            period="INVALID",
            value=0.0,
            status="bad",
            raw_value="0",
        )
        invalid_row.validation_result = ValidationResult(
            is_valid=False,
            errors=[ValidationError(error_code=ErrorCode.INVALID_PERIOD_FORMAT, field="period", message="Bad period")],
            warnings=[],
        )
        rows.append(invalid_row)

    scenario["rows"] = rows
    scenario["expected_invalid"] = num_invalid
    return scenario


def _build_mock_db(db_records: dict):
    """Build a mock DB session that returns FakeExistingRecord based on period.

    The preview() method does:
        existing = db.query(MarketReferencePrice).filter(
            MarketReferencePrice.price_type == price_type,
            MarketReferencePrice.period == row.period,
        ).first()

    We mock the chain: db.query().filter().first() to return the right record.
    """
    mock_db = MagicMock()

    def mock_filter(*args, **kwargs):
        """Capture the filter args to determine which period is being queried."""
        filter_mock = MagicMock()

        # Extract the period from the filter BinaryExpression args
        # The filter call looks like:
        #   .filter(MarketReferencePrice.price_type == price_type,
        #           MarketReferencePrice.period == row.period)
        # We need to extract the period value from the second clause
        period_value = None
        for arg in args:
            # SQLAlchemy BinaryExpression: check if it compares period column
            if hasattr(arg, 'right') and hasattr(arg, 'left'):
                left = arg.left
                if hasattr(left, 'key') and left.key == 'period':
                    right = arg.right
                    if hasattr(right, 'value'):
                        period_value = right.value
                    elif hasattr(right, 'effective_value'):
                        period_value = right.effective_value

        if period_value is not None and period_value in db_records:
            filter_mock.first.return_value = db_records[period_value]
        else:
            filter_mock.first.return_value = None

        return filter_mock

    mock_query = MagicMock()
    mock_query.filter = mock_filter
    mock_db.query.return_value = mock_query

    return mock_db


# ═══════════════════════════════════════════════════════════════════════════════
# Property 13: Import Preview Accuracy
# Feature: ptf-admin-management, Property 13: Import Preview Accuracy
# **Validates: Requirements 6.1, 6.2, 6.3**
# ═══════════════════════════════════════════════════════════════════════════════


class TestProperty13ImportPreviewAccuracy:
    """Property 13: Import Preview Accuracy.

    *For any* import preview request:
    - new_records count SHALL equal rows where period does not exist in DB
    - updates count SHALL equal rows where period exists and value differs
    - unchanged count SHALL equal rows where period exists and value is same
    - locked_conflicts SHALL equal rows targeting locked periods
    - final_conflicts SHALL equal rows targeting final records without force_update

    **Validates: Requirements 6.1, 6.2, 6.3**
    """

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(scenario=preview_scenario_strategy())
    def test_preview_counts_match_expected(self, scenario):
        """Property 13: For any set of valid rows with known DB state,
        preview counts SHALL exactly match the expected categorization.

        **Validates: Requirements 6.1, 6.2, 6.3**
        """
        rows = scenario["rows"]
        db_records = scenario["db_records"]
        force_update = scenario["force_update"]

        if not rows:
            return  # Skip empty scenarios

        importer = BulkImporter(validator=MarketPriceValidator())
        mock_db = _build_mock_db(db_records)

        preview = importer.preview(mock_db, rows, price_type="PTF", force_update=force_update)

        assert preview.new_records == scenario["expected_new"], (
            f"new_records: expected {scenario['expected_new']}, got {preview.new_records}. "
            f"force_update={force_update}, rows={len(rows)}"
        )
        assert preview.updates == scenario["expected_updates"], (
            f"updates: expected {scenario['expected_updates']}, got {preview.updates}. "
            f"force_update={force_update}, rows={len(rows)}"
        )
        assert preview.unchanged == scenario["expected_unchanged"], (
            f"unchanged: expected {scenario['expected_unchanged']}, got {preview.unchanged}. "
            f"force_update={force_update}, rows={len(rows)}"
        )
        assert preview.final_conflicts == scenario["expected_final_conflicts"], (
            f"final_conflicts: expected {scenario['expected_final_conflicts']}, got {preview.final_conflicts}. "
            f"force_update={force_update}, rows={len(rows)}"
        )

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(scenario=preview_scenario_strategy())
    def test_preview_total_rows_equals_input_length(self, scenario):
        """Property 13: total_rows SHALL always equal the number of input rows.

        **Validates: Requirements 6.1**
        """
        rows = scenario["rows"]
        db_records = scenario["db_records"]
        force_update = scenario["force_update"]

        if not rows:
            return

        importer = BulkImporter(validator=MarketPriceValidator())
        mock_db = _build_mock_db(db_records)

        preview = importer.preview(mock_db, rows, price_type="PTF", force_update=force_update)

        assert preview.total_rows == len(rows), (
            f"total_rows should be {len(rows)}, got {preview.total_rows}"
        )

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(scenario=preview_scenario_strategy())
    def test_preview_counts_sum_to_valid_rows(self, scenario):
        """Property 13: new_records + updates + unchanged + final_conflicts SHALL equal valid_rows.

        For all-valid input rows, the sum of all categories must account for every row.

        **Validates: Requirements 6.1, 6.2, 6.3**
        """
        rows = scenario["rows"]
        db_records = scenario["db_records"]
        force_update = scenario["force_update"]

        if not rows:
            return

        importer = BulkImporter(validator=MarketPriceValidator())
        mock_db = _build_mock_db(db_records)

        preview = importer.preview(mock_db, rows, price_type="PTF", force_update=force_update)

        category_sum = preview.new_records + preview.updates + preview.unchanged + preview.final_conflicts
        assert category_sum == preview.valid_rows, (
            f"Category sum ({category_sum}) should equal valid_rows ({preview.valid_rows}). "
            f"new={preview.new_records}, updates={preview.updates}, "
            f"unchanged={preview.unchanged}, final_conflicts={preview.final_conflicts}"
        )

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(scenario=preview_with_invalid_rows_strategy())
    def test_preview_invalid_rows_excluded_from_categories(self, scenario):
        """Property 13: Invalid rows SHALL be counted as invalid_rows and not affect category counts.

        **Validates: Requirements 6.1, 6.2, 6.3**
        """
        rows = scenario["rows"]
        db_records = scenario["db_records"]
        force_update = scenario["force_update"]
        expected_invalid = scenario["expected_invalid"]

        importer = BulkImporter(validator=MarketPriceValidator())
        mock_db = _build_mock_db(db_records)

        preview = importer.preview(mock_db, rows, price_type="PTF", force_update=force_update)

        # Invalid rows should be counted
        assert preview.invalid_rows == expected_invalid, (
            f"invalid_rows: expected {expected_invalid}, got {preview.invalid_rows}"
        )

        # Valid + invalid should equal total
        assert preview.valid_rows + preview.invalid_rows == preview.total_rows, (
            f"valid ({preview.valid_rows}) + invalid ({preview.invalid_rows}) "
            f"should equal total ({preview.total_rows})"
        )

        # Category counts should still match expected (only valid rows categorized)
        assert preview.new_records == scenario["expected_new"], (
            f"new_records with invalid rows: expected {scenario['expected_new']}, got {preview.new_records}"
        )

    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(scenario=preview_scenario_strategy())
    def test_preview_locked_periods_always_conflict(self, scenario):
        """Property 13: Rows targeting locked periods SHALL always be counted as final_conflicts,
        regardless of force_update flag.

        **Validates: Requirements 6.3**
        """
        rows = scenario["rows"]
        db_records = scenario["db_records"]
        force_update = scenario["force_update"]

        if not rows:
            return

        importer = BulkImporter(validator=MarketPriceValidator())
        mock_db = _build_mock_db(db_records)

        preview = importer.preview(mock_db, rows, price_type="PTF", force_update=force_update)

        # Count how many rows target locked periods
        locked_count = sum(
            1 for row in rows
            if row.validation_result and row.validation_result.is_valid
            and db_records.get(row.period) is not None
            and db_records[row.period].is_locked == 1
        )

        # Locked periods should always appear in errors with PERIOD_LOCKED code
        locked_errors = [e for e in preview.errors if e.get("error_code") == "PERIOD_LOCKED"]
        assert len(locked_errors) == locked_count, (
            f"Expected {locked_count} PERIOD_LOCKED errors, got {len(locked_errors)}"
        )
