"""
Unit tests for BulkImporter.

Feature: ptf-admin-management
Tests CSV/JSON parsing, preview, and apply operations.
"""

import pytest
from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

from app.bulk_importer import (
    BulkImporter,
    ImportRow,
    ImportPreview,
    ImportResult,
    ParseError,
)
from app.market_price_validator import (
    MarketPriceValidator,
    ValidationResult,
    ValidationError,
    ErrorCode,
)
from app.market_price_admin_service import (
    MarketPriceAdminService,
    UpsertResult,
    ServiceError,
    ServiceErrorCode,
)
from app.database import MarketReferencePrice


@pytest.fixture
def validator():
    """Create real validator instance."""
    return MarketPriceValidator()


@pytest.fixture
def mock_service():
    """Create mock admin service."""
    return MagicMock(spec=MarketPriceAdminService)


@pytest.fixture
def importer(validator, mock_service):
    """Create BulkImporter with real validator and mock service."""
    return BulkImporter(validator=validator, service=mock_service)


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


# ═══════════════════════════════════════════════════════════════════════════════
# CSV PARSING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseCSV:
    """Tests for parse_csv method."""

    def test_valid_csv(self, importer):
        """Valid CSV should parse correctly."""
        csv_content = "period,value,status\n2025-01,2508.80,final\n2025-02,2478.28,final\n"
        rows = importer.parse_csv(csv_content)

        assert len(rows) == 2
        assert rows[0].period == "2025-01"
        assert rows[0].value == 2508.80
        assert rows[0].status == "final"
        assert rows[0].row_number == 1
        assert rows[1].period == "2025-02"
        assert rows[1].row_number == 2

    def test_ptf_value_column_name(self, importer):
        """CSV with ptf_value column should work."""
        csv_content = "period,ptf_value,status\n2025-01,2508.80,final\n"
        rows = importer.parse_csv(csv_content)

        assert len(rows) == 1
        assert rows[0].value == 2508.80

    def test_comma_decimal_rejected(self, importer):
        """Comma decimal values should be rejected."""
        csv_content = "period,value,status\n2025-01,2508.80,final\n2025-02,\"2478,28\",final\n"
        rows = importer.parse_csv(csv_content)

        assert len(rows) == 2
        # First row valid
        assert rows[0].validation_result.is_valid is True
        # Second row invalid (comma decimal)
        assert rows[1].validation_result.is_valid is False

    def test_empty_csv_raises(self, importer):
        """Empty CSV should raise ParseError."""
        with pytest.raises(ParseError):
            importer.parse_csv("")

    def test_header_only_csv_raises(self, importer):
        """CSV with only header should raise ParseError."""
        with pytest.raises(ParseError):
            importer.parse_csv("period,value,status\n")

    def test_missing_period_column_raises(self, importer):
        """CSV without period column should raise ParseError."""
        with pytest.raises(ParseError, match="period"):
            importer.parse_csv("val,status\n2508.80,final\n")

    def test_missing_value_column_raises(self, importer):
        """CSV without value/ptf_value column should raise ParseError."""
        with pytest.raises(ParseError, match="value"):
            importer.parse_csv("period,status\n2025-01,final\n")

    def test_missing_status_column_raises(self, importer):
        """CSV without status column should raise ParseError."""
        with pytest.raises(ParseError, match="status"):
            importer.parse_csv("period,value\n2025-01,2508.80\n")

    def test_invalid_period_format(self, importer):
        """Invalid period format should fail validation."""
        csv_content = "period,value,status\n2025-13,2508.80,final\n"
        rows = importer.parse_csv(csv_content)

        assert len(rows) == 1
        assert rows[0].validation_result.is_valid is False

    def test_invalid_status(self, importer):
        """Invalid status should fail validation."""
        csv_content = "period,value,status\n2025-01,2508.80,unknown\n"
        rows = importer.parse_csv(csv_content)

        assert len(rows) == 1
        assert rows[0].validation_result.is_valid is False

    def test_whitespace_trimmed(self, importer):
        """Whitespace in values should be trimmed."""
        csv_content = "period,value,status\n 2025-01 , 2508.80 , final \n"
        rows = importer.parse_csv(csv_content)

        assert len(rows) == 1
        assert rows[0].period == "2025-01"
        assert rows[0].status == "final"


# ═══════════════════════════════════════════════════════════════════════════════
# JSON PARSING TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseJSON:
    """Tests for parse_json method."""

    def test_valid_json(self, importer):
        """Valid JSON array should parse correctly."""
        json_content = '[{"period": "2025-01", "value": 2508.80, "status": "final"}]'
        rows = importer.parse_json(json_content)

        assert len(rows) == 1
        assert rows[0].period == "2025-01"
        assert rows[0].value == 2508.80
        assert rows[0].status == "final"

    def test_ptf_value_field(self, importer):
        """JSON with ptf_value field should work."""
        json_content = '[{"period": "2025-01", "ptf_value": 2508.80, "status": "final"}]'
        rows = importer.parse_json(json_content)

        assert len(rows) == 1
        assert rows[0].value == 2508.80

    def test_multiple_rows(self, importer):
        """Multiple JSON objects should parse correctly."""
        json_content = """[
            {"period": "2025-01", "value": 2508.80, "status": "final"},
            {"period": "2025-02", "value": 2478.28, "status": "provisional"}
        ]"""
        rows = importer.parse_json(json_content)

        assert len(rows) == 2
        assert rows[0].row_number == 1
        assert rows[1].row_number == 2

    def test_comma_decimal_string_rejected(self, importer):
        """String value with comma decimal should be rejected."""
        json_content = '[{"period": "2025-01", "value": "2508,80", "status": "final"}]'
        rows = importer.parse_json(json_content)

        assert len(rows) == 1
        assert rows[0].validation_result.is_valid is False

    def test_missing_value_field(self, importer):
        """Missing value field should fail validation."""
        json_content = '[{"period": "2025-01", "status": "final"}]'
        rows = importer.parse_json(json_content)

        assert len(rows) == 1
        assert rows[0].validation_result.is_valid is False

    def test_empty_json_raises(self, importer):
        """Empty JSON should raise ParseError."""
        with pytest.raises(ParseError):
            importer.parse_json("")

    def test_empty_array_raises(self, importer):
        """Empty JSON array should raise ParseError."""
        with pytest.raises(ParseError, match="bos"):
            importer.parse_json("[]")

    def test_non_array_raises(self, importer):
        """Non-array JSON should raise ParseError."""
        with pytest.raises(ParseError, match="dizi"):
            importer.parse_json('{"period": "2025-01"}')

    def test_invalid_json_raises(self, importer):
        """Invalid JSON should raise ParseError."""
        with pytest.raises(ParseError):
            importer.parse_json("{invalid json}")

    def test_non_object_item(self, importer):
        """Non-object items in array should fail validation."""
        json_content = '["not an object"]'
        rows = importer.parse_json(json_content)

        assert len(rows) == 1
        assert rows[0].validation_result.is_valid is False


# ═══════════════════════════════════════════════════════════════════════════════
# PREVIEW TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPreview:
    """Tests for preview method."""

    def _make_valid_row(self, row_number, period, value, status):
        """Helper to create a valid ImportRow."""
        row = ImportRow(
            row_number=row_number,
            period=period,
            value=value,
            status=status,
        )
        row.validation_result = ValidationResult(is_valid=True, errors=[], warnings=[])
        return row

    def _make_invalid_row(self, row_number, period, value, status, error_msg="Invalid"):
        """Helper to create an invalid ImportRow."""
        row = ImportRow(
            row_number=row_number,
            period=period,
            value=value,
            status=status,
        )
        row.validation_result = ValidationResult(
            is_valid=False,
            errors=[ValidationError(
                error_code=ErrorCode.INVALID_PERIOD_FORMAT,
                field="period",
                message=error_msg,
            )],
            warnings=[],
        )
        return row

    def _make_existing_record(self, period, value, status, is_locked=0):
        """Helper to create a mock existing DB record."""
        record = MagicMock(spec=MarketReferencePrice)
        record.period = period
        record.ptf_tl_per_mwh = value
        record.status = status
        record.is_locked = is_locked
        record.price_type = "PTF"
        return record

    def test_all_new_records(self, importer, mock_db):
        """All rows are new records."""
        rows = [
            self._make_valid_row(1, "2025-01", 2508.80, "final"),
            self._make_valid_row(2, "2025-02", 2478.28, "final"),
        ]
        # No existing records
        mock_db.query.return_value.filter.return_value.first.return_value = None

        preview = importer.preview(mock_db, rows)

        assert preview.total_rows == 2
        assert preview.valid_rows == 2
        assert preview.invalid_rows == 0
        assert preview.new_records == 2
        assert preview.updates == 0
        assert preview.unchanged == 0
        assert preview.final_conflicts == 0

    def test_unchanged_records(self, importer, mock_db):
        """Records with same value and status are unchanged."""
        rows = [self._make_valid_row(1, "2025-01", 2508.80, "final")]
        existing = self._make_existing_record("2025-01", 2508.80, "final")
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        preview = importer.preview(mock_db, rows)

        assert preview.unchanged == 1
        assert preview.updates == 0
        assert preview.new_records == 0

    def test_update_records(self, importer, mock_db):
        """Records with different value are updates."""
        rows = [self._make_valid_row(1, "2025-01", 2600.00, "final")]
        existing = self._make_existing_record("2025-01", 2508.80, "provisional")
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        preview = importer.preview(mock_db, rows)

        assert preview.updates == 1
        assert preview.unchanged == 0

    def test_final_conflict_without_force(self, importer, mock_db):
        """Final record update without force_update is a conflict."""
        rows = [self._make_valid_row(1, "2025-01", 2600.00, "final")]
        existing = self._make_existing_record("2025-01", 2508.80, "final")
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        preview = importer.preview(mock_db, rows, force_update=False)

        assert preview.final_conflicts == 1
        assert len(preview.errors) == 1
        assert preview.errors[0]["error_code"] == "FINAL_RECORD_PROTECTED"

    def test_final_conflict_with_force(self, importer, mock_db):
        """Final record update with force_update is an update, not conflict."""
        rows = [self._make_valid_row(1, "2025-01", 2600.00, "final")]
        existing = self._make_existing_record("2025-01", 2508.80, "final")
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        preview = importer.preview(mock_db, rows, force_update=True)

        assert preview.final_conflicts == 0
        assert preview.updates == 1

    def test_locked_period_conflict(self, importer, mock_db):
        """Locked period should be a conflict."""
        rows = [self._make_valid_row(1, "2025-01", 2508.80, "final")]
        existing = self._make_existing_record("2025-01", 2508.80, "final", is_locked=1)
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        preview = importer.preview(mock_db, rows)

        assert preview.final_conflicts == 1
        assert preview.errors[0]["error_code"] == "PERIOD_LOCKED"

    def test_invalid_rows_counted(self, importer, mock_db):
        """Invalid rows should be counted separately."""
        rows = [
            self._make_valid_row(1, "2025-01", 2508.80, "final"),
            self._make_invalid_row(2, "invalid", 0.0, "final"),
        ]
        mock_db.query.return_value.filter.return_value.first.return_value = None

        preview = importer.preview(mock_db, rows)

        assert preview.total_rows == 2
        assert preview.valid_rows == 1
        assert preview.invalid_rows == 1
        assert preview.new_records == 1

    def test_status_downgrade_conflict(self, importer, mock_db):
        """Downgrade from final to provisional is a conflict."""
        rows = [self._make_valid_row(1, "2025-01", 2508.80, "provisional")]
        existing = self._make_existing_record("2025-01", 2508.80, "final")
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        preview = importer.preview(mock_db, rows, force_update=False)

        assert preview.final_conflicts == 1
        assert preview.errors[0]["error_code"] == "STATUS_DOWNGRADE_FORBIDDEN"


# ═══════════════════════════════════════════════════════════════════════════════
# APPLY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestApply:
    """Tests for apply method."""

    def _make_valid_row(self, row_number, period, value, status):
        """Helper to create a valid ImportRow."""
        row = ImportRow(
            row_number=row_number,
            period=period,
            value=value,
            status=status,
        )
        row.validation_result = ValidationResult(is_valid=True, errors=[], warnings=[])
        return row

    def _make_invalid_row(self, row_number, period, value, status, error_code=ErrorCode.INVALID_PERIOD_FORMAT):
        """Helper to create an invalid ImportRow."""
        row = ImportRow(
            row_number=row_number,
            period=period,
            value=value,
            status=status,
        )
        row.validation_result = ValidationResult(
            is_valid=False,
            errors=[ValidationError(
                error_code=error_code,
                field="period",
                message="Invalid",
            )],
            warnings=[],
        )
        return row

    def test_all_valid_rows_accepted(self, importer, mock_service, mock_db):
        """All valid rows should be accepted."""
        rows = [
            self._make_valid_row(1, "2025-01", 2508.80, "final"),
            self._make_valid_row(2, "2025-02", 2478.28, "final"),
        ]
        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True,
        )

        result = importer.apply(mock_db, rows, updated_by="admin")

        assert result.accepted_count == 2
        assert result.rejected_count == 0
        assert result.success is True
        assert len(result.rejected_rows) == 0

    def test_invalid_rows_rejected(self, importer, mock_service, mock_db):
        """Invalid rows should be rejected, valid rows accepted."""
        rows = [
            self._make_valid_row(1, "2025-01", 2508.80, "final"),
            self._make_invalid_row(2, "invalid", 0.0, "final"),
        ]
        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True,
        )

        result = importer.apply(mock_db, rows, updated_by="admin")

        assert result.accepted_count == 1
        assert result.rejected_count == 1
        assert len(result.rejected_rows) == 1
        assert result.rejected_rows[0]["row_index"] == 2

    def test_strict_mode_rejects_all_on_validation_error(self, importer, mock_service, mock_db):
        """Strict mode should reject entire batch if any row fails validation."""
        rows = [
            self._make_valid_row(1, "2025-01", 2508.80, "final"),
            self._make_invalid_row(2, "invalid", 0.0, "final"),
        ]

        result = importer.apply(mock_db, rows, updated_by="admin", strict_mode=True)

        assert result.success is False
        assert result.accepted_count == 0
        assert result.rejected_count == 2  # Entire batch
        # Service should NOT have been called
        mock_service.upsert_price.assert_not_called()

    def test_upsert_failure_rejected(self, importer, mock_service, mock_db):
        """Upsert failure should be reported in rejected_rows."""
        rows = [self._make_valid_row(1, "2025-01", 2508.80, "final")]
        mock_service.upsert_price.return_value = UpsertResult(
            success=False,
            created=False,
            changed=False,
            error=ServiceError(
                error_code=ServiceErrorCode.PERIOD_LOCKED,
                field="period",
                message="Dönem kilitli.",
            ),
        )

        result = importer.apply(mock_db, rows, updated_by="admin")

        assert result.accepted_count == 0
        assert result.rejected_count == 1
        assert result.rejected_rows[0]["error_code"] == "PERIOD_LOCKED"
        assert result.rejected_rows[0]["row_index"] == 1

    def test_result_contract_fields(self, importer, mock_service, mock_db):
        """ImportResult should have accepted_count, rejected_count, rejected_rows."""
        rows = [self._make_valid_row(1, "2025-01", 2508.80, "final")]
        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True,
        )

        result = importer.apply(mock_db, rows, updated_by="admin")

        # Verify contract fields exist
        assert hasattr(result, "accepted_count")
        assert hasattr(result, "rejected_count")
        assert hasattr(result, "rejected_rows")
        assert isinstance(result.rejected_rows, list)

    def test_rejected_row_error_details(self, importer, mock_service, mock_db):
        """Rejected rows should have row_index, error_code, field, message."""
        rows = [self._make_invalid_row(3, "bad", 0.0, "final")]

        result = importer.apply(mock_db, rows, updated_by="admin")

        assert len(result.rejected_rows) == 1
        row_err = result.rejected_rows[0]
        assert "row_index" in row_err
        assert "error_code" in row_err
        assert "field" in row_err
        assert "message" in row_err
        assert row_err["row_index"] == 3

    def test_force_update_passed_to_service(self, importer, mock_service, mock_db):
        """force_update flag should be passed to upsert_price."""
        rows = [self._make_valid_row(1, "2025-01", 2508.80, "final")]
        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True,
        )

        importer.apply(mock_db, rows, updated_by="admin", force_update=True)

        call_kwargs = mock_service.upsert_price.call_args
        assert call_kwargs.kwargs.get("force_update") is True or \
               (len(call_kwargs) > 1 and call_kwargs[1].get("force_update") is True)

    def test_legacy_aliases(self, importer, mock_service, mock_db):
        """Legacy property aliases should work."""
        rows = [self._make_valid_row(1, "2025-01", 2508.80, "final")]
        mock_service.upsert_price.return_value = UpsertResult(
            success=True, created=True, changed=True,
        )

        result = importer.apply(mock_db, rows, updated_by="admin")

        # Legacy aliases
        assert result.imported_count == result.accepted_count
        assert result.skipped_count == result.rejected_count
        assert result.error_count == result.rejected_count
        assert result.details == result.rejected_rows

    def test_empty_rows_success(self, importer, mock_service, mock_db):
        """Empty rows list should return success with zero counts."""
        result = importer.apply(mock_db, [], updated_by="admin")

        assert result.success is True
        assert result.accepted_count == 0
        assert result.rejected_count == 0
