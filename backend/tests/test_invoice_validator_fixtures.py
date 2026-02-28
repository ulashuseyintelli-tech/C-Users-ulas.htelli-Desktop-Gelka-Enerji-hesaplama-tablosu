"""Fixture-driven tests for invoice validation (Faz A / 4.1).

Covers Task 3.2 (parametrized fixture test) and Task 3.3 (fixture schema smoke test).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.invoice.validation import ValidationErrorCode, validate

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "invoices" / "validation"


def _fixture_paths() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


# -----------------------------------------------------------------------
# Task 3.2 — Parametrized fixture test
# -----------------------------------------------------------------------

@pytest.mark.parametrize(
    "fixture_path",
    _fixture_paths(),
    ids=lambda p: p.stem,
)
def test_fixture(fixture_path: Path) -> None:
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    # Closed-set enforcement: every expected code must be a valid enum value
    for err in data["expected"]["errors"]:
        assert err["code"] in ValidationErrorCode._value2member_map_, (
            f"Unknown error code '{err['code']}' in fixture {fixture_path.name}"
        )

    result = validate(data["invoice"])

    assert result.valid == data["expected"]["valid"], (
        f"valid mismatch in {fixture_path.name}: "
        f"got {result.valid}, expected {data['expected']['valid']}"
    )

    actual_pairs = {(e.code.value, e.field) for e in result.errors}
    expected_pairs = {(e["code"], e["field"]) for e in data["expected"]["errors"]}
    assert actual_pairs == expected_pairs, (
        f"error pairs mismatch in {fixture_path.name}:\n"
        f"  actual:   {sorted(actual_pairs)}\n"
        f"  expected: {sorted(expected_pairs)}"
    )

    # Invariant: valid == (no errors)
    assert result.valid == (len(result.errors) == 0)


# -----------------------------------------------------------------------
# Task 3.3 — Fixture schema smoke test (CI fast lane)
# -----------------------------------------------------------------------

_REQUIRED_TOP_KEYS = {"meta", "invoice", "expected"}
_REQUIRED_META_KEYS = {"supplier", "scenario"}
_REQUIRED_EXPECTED_KEYS = {"valid", "errors"}


def test_invoice_fixture_schema() -> None:
    """Validate structural integrity of all validation fixture JSON files.

    Target: pytest -k test_invoice_fixture_schema
    """
    paths = _fixture_paths()
    assert len(paths) > 0, "No fixture files found — check FIXTURE_DIR"

    for fp in paths:
        data = json.loads(fp.read_text(encoding="utf-8"))

        # Top-level keys
        missing_top = _REQUIRED_TOP_KEYS - data.keys()
        assert not missing_top, f"{fp.name}: missing top-level keys {missing_top}"

        # meta keys
        missing_meta = _REQUIRED_META_KEYS - data["meta"].keys()
        assert not missing_meta, f"{fp.name}: missing meta keys {missing_meta}"

        # expected keys
        missing_exp = _REQUIRED_EXPECTED_KEYS - data["expected"].keys()
        assert not missing_exp, f"{fp.name}: missing expected keys {missing_exp}"

        # expected.errors[].code must be valid enum values
        for i, err in enumerate(data["expected"]["errors"]):
            assert "code" in err, f"{fp.name}: expected.errors[{i}] missing 'code'"
            assert "field" in err, f"{fp.name}: expected.errors[{i}] missing 'field'"
            assert err["code"] in ValidationErrorCode._value2member_map_, (
                f"{fp.name}: expected.errors[{i}].code='{err['code']}' "
                f"not in ValidationErrorCode"
            )
