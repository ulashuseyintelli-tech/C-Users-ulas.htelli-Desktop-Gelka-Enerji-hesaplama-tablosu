"""Closed-set validation error codes for invoice validation (Faz A / 4.1)."""

from enum import Enum


class ValidationErrorCode(str, Enum):
    """Kapalı küme — serbest string kabul edilmez."""

    MISSING_FIELD = "MISSING_FIELD"
    INVALID_FORMAT = "INVALID_FORMAT"
    INVALID_ETTN = "INVALID_ETTN"
    INVALID_DATETIME = "INVALID_DATETIME"
    INCONSISTENT_PERIODS = "INCONSISTENT_PERIODS"
    NEGATIVE_VALUE = "NEGATIVE_VALUE"
    REACTIVE_PENALTY_MISMATCH = "REACTIVE_PENALTY_MISMATCH"
    UNSUPPORTED_SUPPLIER = "UNSUPPORTED_SUPPLIER"  # defined, unused in 4.1

    # Phase C — ported from CanonicalInvoice.validate()
    PAYABLE_TOTAL_MISMATCH = "PAYABLE_TOTAL_MISMATCH"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    ZERO_CONSUMPTION = "ZERO_CONSUMPTION"
    LINE_CROSSCHECK_FAIL = "LINE_CROSSCHECK_FAIL"
