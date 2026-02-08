"""
BulkImporter - CSV/JSON toplu veri yuklemesi.

Sorumluluklar:
- CSV/JSON parse (dot decimal only)
- Row-level validation via MarketPriceValidator
- Preview (new/update/unchanged/final_conflicts counts)
- Apply (row-level accept/reject default, strict_mode option)
- Upsert via MarketPriceAdminService

Feature: ptf-admin-management
"""

import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .database import MarketReferencePrice
from .market_price_validator import (
    MarketPriceValidator,
    NormalizedMarketPriceInput,
    ValidationResult,
    ValidationError,
    ErrorCode,
)
from .market_price_admin_service import (
    MarketPriceAdminService,
    UpsertResult,
)

logger = logging.getLogger(__name__)

COMMA_DECIMAL_RE = re.compile(r",")



@dataclass
class ImportRow:
    """Single row from CSV/JSON import."""
    row_number: int
    period: str
    value: float
    status: str
    raw_value: Optional[str] = None
    validation_result: Optional[ValidationResult] = None


@dataclass
class ImportPreview:
    """Preview result before committing import."""
    total_rows: int
    valid_rows: int
    invalid_rows: int
    new_records: int
    updates: int
    unchanged: int
    final_conflicts: int
    rows: List[ImportRow] = field(default_factory=list)
    errors: List[Dict] = field(default_factory=list)


@dataclass
class ImportResult:
    """Result of import apply operation."""
    success: bool
    accepted_count: int
    rejected_count: int
    rejected_rows: List[Dict] = field(default_factory=list)

    @property
    def imported_count(self) -> int:
        return self.accepted_count

    @property
    def skipped_count(self) -> int:
        return self.rejected_count

    @property
    def error_count(self) -> int:
        return self.rejected_count

    @property
    def details(self) -> List[Dict]:
        return self.rejected_rows


class ParseError(Exception):
    """Raised when CSV/JSON parsing fails."""
    def __init__(self, message: str, row_errors: Optional[List[Dict]] = None):
        super().__init__(message)
        self.row_errors = row_errors or []



class BulkImporter:
    """CSV/JSON bulk import for market prices."""

    def __init__(
        self,
        validator: Optional[MarketPriceValidator] = None,
        service: Optional[MarketPriceAdminService] = None,
    ):
        self.validator = validator or MarketPriceValidator()
        self.service = service or MarketPriceAdminService()

    def parse_csv(self, content: str) -> List[ImportRow]:
        """Parse CSV content into ImportRow list.
        Dot decimal only - comma decimals rejected per row.
        Validates: Requirements 5.1, 9.3, 9.4
        """
        rows: List[ImportRow] = []
        if not content or not content.strip():
            raise ParseError("CSV dosyasi bos.")
        try:
            reader = csv.DictReader(io.StringIO(content.strip()))
        except Exception as e:
            raise ParseError(f"CSV parse hatasi: {e}")
        if reader.fieldnames is None:
            raise ParseError("CSV baslik satiri bulunamadi.")
        normalized_fields = [f.strip().lower() for f in reader.fieldnames]
        value_col = None
        for candidate in ("value", "ptf_value"):
            if candidate in normalized_fields:
                value_col = candidate
                break
        if "period" not in normalized_fields:
            raise ParseError("CSV'de 'period' sutunu bulunamadi.")
        if value_col is None:
            raise ParseError("CSV'de 'value' veya 'ptf_value' sutunu bulunamadi.")
        if "status" not in normalized_fields:
            raise ParseError("CSV'de 'status' sutunu bulunamadi.")
        original_fields = list(reader.fieldnames)
        norm_to_orig = {}
        for orig, norm in zip(original_fields, normalized_fields):
            norm_to_orig[norm] = orig
        for row_idx, raw_row in enumerate(reader, start=1):
            period_raw = (raw_row.get(norm_to_orig.get("period", "period")) or "").strip()
            value_raw = (raw_row.get(norm_to_orig.get(value_col, value_col)) or "").strip()
            status_raw = (raw_row.get(norm_to_orig.get("status", "status")) or "").strip()
            parsed_value = self._parse_value_string(value_raw, row_idx)
            row = ImportRow(
                row_number=row_idx,
                period=period_raw,
                value=parsed_value,
                status=status_raw,
                raw_value=value_raw,
            )
            row.validation_result = self._validate_row(row)
            rows.append(row)
        if not rows:
            raise ParseError("CSV dosyasinda veri satiri bulunamadi.")
        return rows

    def parse_json(self, content: str) -> List[ImportRow]:
        """Parse JSON content into ImportRow list.
        Validates: Requirements 5.2, 9.3, 9.4
        """
        if not content or not content.strip():
            raise ParseError("JSON dosyasi bos.")
        try:
            data = json.loads(content.strip())
        except json.JSONDecodeError as e:
            raise ParseError(f"JSON parse hatasi: {e}")
        if not isinstance(data, list):
            raise ParseError("JSON bir dizi (array) olmali.")
        if len(data) == 0:
            raise ParseError("JSON dizisi bos.")
        rows: List[ImportRow] = []
        for row_idx, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                rows.append(self._make_error_row(
                    row_idx, "", 0.0, "",
                    "Satir bir JSON nesnesi (object) olmali."
                ))
                continue
            period_raw = str(item.get("period", "")).strip()
            value_raw = item.get("value", item.get("ptf_value"))
            status_raw = str(item.get("status", "")).strip()
            if value_raw is None:
                row = ImportRow(
                    row_number=row_idx, period=period_raw,
                    value=0.0, status=status_raw,
                )
                row.validation_result = ValidationResult(
                    is_valid=False,
                    errors=[ValidationError(
                        error_code=ErrorCode.VALUE_REQUIRED,
                        field="value",
                        message="PTF degeri zorunludur.",
                    )],
                    warnings=[],
                )
                rows.append(row)
                continue
            if isinstance(value_raw, str):
                raw_str = value_raw.strip()
                parsed_value = self._parse_value_string(raw_str, row_idx)
            elif isinstance(value_raw, (int, float)):
                raw_str = str(value_raw)
                parsed_value = float(value_raw)
            else:
                raw_str = str(value_raw)
                parsed_value = 0.0
            row = ImportRow(
                row_number=row_idx, period=period_raw,
                value=parsed_value, status=status_raw,
                raw_value=raw_str,
            )
            row.validation_result = self._validate_row(row)
            rows.append(row)
        return rows

    def preview(
        self, db: Session, rows: List[ImportRow],
        price_type: str = "PTF", force_update: bool = False,
    ) -> ImportPreview:
        """Preview import results without committing.
        Validates: Requirements 6.1, 6.2, 6.3
        """
        valid_rows = 0
        invalid_rows = 0
        new_records = 0
        updates = 0
        unchanged = 0
        final_conflicts = 0
        errors: List[Dict] = []
        for row in rows:
            if row.validation_result and not row.validation_result.is_valid:
                invalid_rows += 1
                for err in row.validation_result.errors:
                    errors.append({
                        "row": row.row_number, "field": err.field,
                        "error": err.message,
                        "error_code": err.error_code.value if hasattr(err.error_code, 'value') else str(err.error_code),
                    })
                continue
            valid_rows += 1
            existing = db.query(MarketReferencePrice).filter(
                MarketReferencePrice.price_type == price_type,
                MarketReferencePrice.period == row.period,
            ).first()
            if existing is None:
                new_records += 1
            else:
                existing_value = Decimal(str(existing.ptf_tl_per_mwh))
                new_value = Decimal(str(row.value))
                existing_status = existing.status
                if existing.is_locked:
                    final_conflicts += 1
                    errors.append({
                        "row": row.row_number, "field": "period",
                        "error": f"Donem {row.period} kilitli, guncellenemez.",
                        "error_code": "PERIOD_LOCKED",
                    })
                    continue
                if existing_status == "final" and not force_update:
                    if row.status == "provisional":
                        final_conflicts += 1
                        errors.append({
                            "row": row.row_number, "field": "status",
                            "error": "Final kayit provisional'a dusurulmez.",
                            "error_code": "STATUS_DOWNGRADE_FORBIDDEN",
                        })
                        continue
                    if existing_value != new_value:
                        final_conflicts += 1
                        errors.append({
                            "row": row.row_number, "field": "value",
                            "error": "Final kayit degistirmek icin force_update gerekli.",
                            "error_code": "FINAL_RECORD_PROTECTED",
                        })
                        continue
                if existing_value == new_value and existing_status == row.status:
                    unchanged += 1
                else:
                    updates += 1
        return ImportPreview(
            total_rows=len(rows), valid_rows=valid_rows,
            invalid_rows=invalid_rows, new_records=new_records,
            updates=updates, unchanged=unchanged,
            final_conflicts=final_conflicts, rows=rows, errors=errors,
        )

    def apply(
        self, db: Session, rows: List[ImportRow], updated_by: str,
        price_type: str = "PTF", force_update: bool = False,
        strict_mode: bool = False, source: str = "epias_manual",
        change_reason: Optional[str] = None,
    ) -> ImportResult:
        """Apply import: validate and upsert rows.
        Validates: Requirements 5.3, 5.4, 5.5, 5.6, 5.7, 5.8
        """
        rejected_rows: List[Dict] = []
        accepted_count = 0
        valid_rows: List[ImportRow] = []
        for row in rows:
            if row.validation_result and not row.validation_result.is_valid:
                for err in row.validation_result.errors:
                    rejected_rows.append({
                        "row_index": row.row_number,
                        "error_code": err.error_code.value if hasattr(err.error_code, 'value') else str(err.error_code),
                        "field": err.field,
                        "message": err.message,
                    })
            else:
                valid_rows.append(row)
        if strict_mode and len(rejected_rows) > 0:
            return ImportResult(
                success=False, accepted_count=0,
                rejected_count=len(rows), rejected_rows=rejected_rows,
            )
        for row in valid_rows:
            result, normalized = self.validator.validate_entry(
                period=row.period, value=row.value,
                status=row.status, price_type=price_type,
            )
            if not result.is_valid or normalized is None:
                for err in result.errors:
                    rejected_rows.append({
                        "row_index": row.row_number,
                        "error_code": err.error_code.value if hasattr(err.error_code, 'value') else str(err.error_code),
                        "field": err.field,
                        "message": err.message,
                    })
                continue
            upsert_result = self.service.upsert_price(
                db=db, normalized=normalized,
                updated_by=updated_by, source=source,
                change_reason=change_reason or "Bulk import",
                force_update=force_update,
            )
            if upsert_result.success:
                accepted_count += 1
            else:
                error = upsert_result.error
                rejected_rows.append({
                    "row_index": row.row_number,
                    "error_code": error.error_code.value if error else "UNKNOWN",
                    "field": error.field if error else None,
                    "message": error.message if error else "Bilinmeyen hata.",
                })
        rejected_count = len(rejected_rows)
        if strict_mode and rejected_count > 0:
            db.rollback()
            return ImportResult(
                success=False, accepted_count=0,
                rejected_count=len(rows), rejected_rows=rejected_rows,
            )
        return ImportResult(
            success=rejected_count == 0,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            rejected_rows=rejected_rows,
        )

    def _parse_value_string(self, value_str: str, row_number: int) -> float:
        """Parse a value string to float. Dot decimal only."""
        if not value_str:
            return 0.0
        if COMMA_DECIMAL_RE.search(value_str):
            return 0.0
        try:
            return float(value_str)
        except (ValueError, TypeError):
            return 0.0

    def _validate_row(self, row: ImportRow) -> ValidationResult:
        """Validate a single import row using the validator."""
        value_for_validation = row.raw_value if row.raw_value is not None else row.value
        result, _ = self.validator.validate_entry(
            period=row.period, value=value_for_validation,
            status=row.status,
        )
        return result

    def _make_error_row(
        self, row_number: int, period: str,
        value: float, status: str, error_message: str,
    ) -> ImportRow:
        """Create an ImportRow with a pre-set validation error."""
        row = ImportRow(
            row_number=row_number, period=period,
            value=value, status=status,
        )
        row.validation_result = ValidationResult(
            is_valid=False,
            errors=[ValidationError(
                error_code=ErrorCode.INVALID_DECIMAL_FORMAT,
                field="value", message=error_message,
            )],
            warnings=[],
        )
        return row


_importer = BulkImporter()


def get_bulk_importer() -> BulkImporter:
    """Get singleton BulkImporter instance."""
    return _importer
