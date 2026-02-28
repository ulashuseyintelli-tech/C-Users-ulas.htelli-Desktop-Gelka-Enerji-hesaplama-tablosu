"""Shadow compare tests — old CanonicalInvoice.validate() vs new validate().

Phase D (4.3): regression detection via fixture-driven comparison.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.invoice.validation import SHADOW_METRIC_NAME, compare_validators
from app.invoice.validation.shadow import (
    ShadowCompareResult,
    build_canonical_invoice,
    extract_old_codes,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "invoices" / "validation_totals"


def _load(name: str) -> dict:
    fp = FIXTURE_DIR / name
    return json.loads(fp.read_text(encoding="utf-8"))["invoice"]


# -----------------------------------------------------------------------
# D2.1 — totals_ok: both valid
# -----------------------------------------------------------------------

def test_shadow_totals_ok() -> None:
    invoice = _load("totals_ok.json")
    result = compare_validators(invoice)
    assert result.valid_match is True
    assert result.old_valid is True
    assert result.new_valid is True
    assert result.codes_common == frozenset()


# -----------------------------------------------------------------------
# D2.2 — payable_total_mismatch: both fail, common code
# -----------------------------------------------------------------------

def test_shadow_payable_total_mismatch() -> None:
    invoice = _load("payable_total_mismatch.json")
    result = compare_validators(invoice)
    assert result.valid_match is True
    assert result.old_valid is False
    assert result.new_valid is False
    assert "PAYABLE_TOTAL_MISMATCH" in result.codes_common


# -----------------------------------------------------------------------
# D2.3 — total_mismatch: both fail, common code
# -----------------------------------------------------------------------

def test_shadow_total_mismatch() -> None:
    invoice = _load("total_mismatch.json")
    result = compare_validators(invoice)
    assert result.valid_match is True
    assert result.old_valid is False
    assert result.new_valid is False
    assert "TOTAL_MISMATCH" in result.codes_common


# -----------------------------------------------------------------------
# D2.4 — zero_consumption: both fail, common code
# -----------------------------------------------------------------------

def test_shadow_zero_consumption() -> None:
    invoice = _load("zero_consumption.json")
    result = compare_validators(invoice)
    assert result.valid_match is True
    assert result.old_valid is False
    assert result.new_valid is False
    assert "ZERO_CONSUMPTION" in result.codes_common


# -----------------------------------------------------------------------
# D2.5 — line_crosscheck_fail: both fail, common code
# -----------------------------------------------------------------------

def test_shadow_line_crosscheck_fail() -> None:
    invoice = _load("line_crosscheck_fail.json")
    result = compare_validators(invoice)
    assert result.valid_match is True
    assert result.old_valid is False
    assert result.new_valid is False
    assert "LINE_CROSSCHECK_FAIL" in result.codes_common


# -----------------------------------------------------------------------
# D2.6 — missing_totals_skips: expected divergence
# Old validator: ZERO_CONSUMPTION (lines empty → total_kwh=0)
# New validator: skip (no lines key) → valid=True
# -----------------------------------------------------------------------

def test_shadow_missing_totals_expected_divergence() -> None:
    invoice = _load("missing_totals_skips.json")
    result = compare_validators(invoice)
    # Expected divergence: old fails, new passes
    assert result.valid_match is False
    assert result.old_valid is False
    assert result.new_valid is True
    assert "ZERO_CONSUMPTION" in result.old_codes
    assert len(result.codes_only_new) == 0  # new has no totals-related errors


# -----------------------------------------------------------------------
# D2.7 — ShadowCompareResult.to_dict() round-trip
# -----------------------------------------------------------------------

def test_shadow_result_to_dict() -> None:
    invoice = _load("payable_total_mismatch.json")
    result = compare_validators(invoice)
    d = result.to_dict()
    # JSON-serializable check
    assert isinstance(d, dict)
    assert isinstance(d["old_codes"], list)
    assert isinstance(d["new_codes"], list)
    assert isinstance(d["codes_common"], list)
    assert d["valid_match"] == result.valid_match
    assert d["old_valid"] == result.old_valid
    assert d["new_valid"] == result.new_valid


# -----------------------------------------------------------------------
# D2.8 — Mismatch counter (test-only)
# -----------------------------------------------------------------------

def test_shadow_mismatch_counter() -> None:
    """Simulate mismatch counting using SHADOW_METRIC_NAME."""
    counter = 0
    fixtures = [
        "totals_ok.json",
        "payable_total_mismatch.json",
        "total_mismatch.json",
        "zero_consumption.json",
        "line_crosscheck_fail.json",
        "missing_totals_skips.json",
    ]
    for name in fixtures:
        invoice = _load(name)
        result = compare_validators(invoice)
        if not result.valid_match:
            counter += 1

    # Only missing_totals_skips should diverge
    assert counter == 1, f"Expected 1 mismatch (missing_totals_skips), got {counter}"
    # Metric name is reserved
    assert SHADOW_METRIC_NAME == "invoice_validation_shadow_mismatch_total"
