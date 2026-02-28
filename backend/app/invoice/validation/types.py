"""Data contracts for invoice validation (Faz A / 4.1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .error_codes import ValidationErrorCode

# --- type aliases ---
ValidationSeverity = Literal["ERROR", "WARN"]
NormalizedInvoice = dict  # 4.2'de typed model'e evrilir

# Shadow-mode metric name constants
SHADOW_METRIC_NAME = "invoice_validation_shadow_mismatch_total"
SHADOW_SAMPLED_TOTAL = "invoice_validation_shadow_sampled_total"
SHADOW_WHITELISTED_TOTAL = "invoice_validation_shadow_whitelisted_total"
SHADOW_ACTIONABLE_TOTAL = "invoice_validation_shadow_actionable_total"

# Enforcement metric name constants (Phase F)
ENFORCE_TOTAL = "invoice_validation_enforced_total"
ENFORCE_BLOCKED_TOTAL = "invoice_validation_blocked_total"
ENFORCE_SOFTWARN_TOTAL = "invoice_validation_softwarn_total"
ENFORCE_MODE_GAUGE = "invoice_validation_mode"


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
