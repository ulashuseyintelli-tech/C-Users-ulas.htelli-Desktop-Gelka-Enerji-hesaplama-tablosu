"""Data contracts for invoice validation (Faz A / 4.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .error_codes import ValidationErrorCode

# --- type aliases ---
ValidationSeverity = Literal["ERROR", "WARN"]
NormalizedInvoice = dict  # 4.2'de typed model'e evrilir

# Shadow-mode metric name reservation (4.2 uses this constant)
SHADOW_METRIC_NAME = "invoice_validation_shadow_mismatch_total"


@dataclass(frozen=True)
class InvoiceValidationError:
    """Single validation error — immutable, JSON-safe via to_dict()."""

    code: ValidationErrorCode
    field: str
    message: str
    severity: ValidationSeverity = "ERROR"

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "field": self.field,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class InvoiceValidationResult:
    """Aggregate validation outcome."""

    valid: bool
    errors: list[InvoiceValidationError]
    normalized: NormalizedInvoice | None = None  # 4.1'de None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "normalized": self.normalized,
        }
