"""Phase E integration tests — shadow_validate_hook, sampling, whitelist, metrics.

Tests:
  E4.1  should_sample deterministic
  E4.2  should_sample boundary (rate=0, rate=1)
  E4.3  is_whitelisted pattern matching
  E4.4  shadow_validate_hook rate=1.0 → returns result
  E4.5  shadow_validate_hook rate=0.0 → returns None
  E4.6  shadow_validate_hook exception safety
  E4.7  record_shadow_metrics counter increments
  E4.8  full pipeline — all 6 fixtures through hook
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.invoice.validation import (
    SHADOW_ACTIONABLE_TOTAL,
    SHADOW_METRIC_NAME,
    SHADOW_SAMPLED_TOTAL,
    SHADOW_WHITELISTED_TOTAL,
    ShadowCompareResult,
    ShadowConfig,
    get_shadow_counters,
    is_whitelisted,
    record_shadow_metrics,
    reset_shadow_counters,
    shadow_validate_hook,
    should_sample,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "invoices" / "validation_totals"


def _load(name: str) -> dict:
    fp = FIXTURE_DIR / name
    return json.loads(fp.read_text(encoding="utf-8"))["invoice"]


@pytest.fixture(autouse=True)
def _reset_counters():
    """Reset shadow counters before each test."""
    reset_shadow_counters()
    yield
    reset_shadow_counters()


# -----------------------------------------------------------------------
# E4.1 — should_sample deterministic
# -----------------------------------------------------------------------

def test_should_sample_deterministic() -> None:
    """Same invoice_id + rate always returns the same result."""
    invoice_id = "INV-2026-001"
    rate = 0.5
    first = should_sample(invoice_id, rate)
    for _ in range(100):
        assert should_sample(invoice_id, rate) == first


# -----------------------------------------------------------------------
# E4.2 — should_sample boundary
# -----------------------------------------------------------------------

def test_should_sample_rate_zero() -> None:
    assert should_sample("any-id", 0.0) is False
    assert should_sample(None, 0.0) is False


def test_should_sample_rate_one() -> None:
    assert should_sample("any-id", 1.0) is True
    assert should_sample(None, 1.0) is True


# -----------------------------------------------------------------------
# E4.3 — is_whitelisted
# -----------------------------------------------------------------------

def test_is_whitelisted_missing_totals_skips() -> None:
    """missing_totals_skips pattern: old=ZERO_CONSUMPTION, new=∅, valid_match=False."""
    result = ShadowCompareResult(
        old_valid=False,
        new_valid=True,
        valid_match=False,
        old_codes=frozenset({"ZERO_CONSUMPTION"}),
        new_codes=frozenset(),
        codes_only_old=frozenset({"ZERO_CONSUMPTION"}),
        codes_only_new=frozenset(),
        codes_common=frozenset(),
    )
    assert is_whitelisted(result, frozenset({"missing_totals_skips"})) is True


def test_is_whitelisted_actionable_mismatch() -> None:
    """PAYABLE_TOTAL_MISMATCH divergence is NOT whitelisted."""
    result = ShadowCompareResult(
        old_valid=False,
        new_valid=True,
        valid_match=False,
        old_codes=frozenset({"PAYABLE_TOTAL_MISMATCH"}),
        new_codes=frozenset(),
        codes_only_old=frozenset({"PAYABLE_TOTAL_MISMATCH"}),
        codes_only_new=frozenset(),
        codes_common=frozenset(),
    )
    assert is_whitelisted(result, frozenset({"missing_totals_skips"})) is False


def test_is_whitelisted_valid_match_returns_false() -> None:
    """No mismatch → nothing to whitelist."""
    result = ShadowCompareResult(
        old_valid=True,
        new_valid=True,
        valid_match=True,
        old_codes=frozenset(),
        new_codes=frozenset(),
        codes_only_old=frozenset(),
        codes_only_new=frozenset(),
        codes_common=frozenset(),
    )
    assert is_whitelisted(result, frozenset({"missing_totals_skips"})) is False


# -----------------------------------------------------------------------
# E4.4 — shadow_validate_hook rate=1.0
# -----------------------------------------------------------------------

def test_hook_rate_one_returns_result() -> None:
    invoice = _load("totals_ok.json")
    cfg = ShadowConfig(sample_rate=1.0, whitelist=frozenset({"missing_totals_skips"}))
    result = shadow_validate_hook(invoice, [], invoice_id="test-001", config=cfg)
    assert result is not None
    assert isinstance(result, ShadowCompareResult)
    assert result.valid_match is True


# -----------------------------------------------------------------------
# E4.5 — shadow_validate_hook rate=0.0
# -----------------------------------------------------------------------

def test_hook_rate_zero_returns_none() -> None:
    invoice = _load("totals_ok.json")
    cfg = ShadowConfig(sample_rate=0.0, whitelist=frozenset())
    result = shadow_validate_hook(invoice, [], invoice_id="test-001", config=cfg)
    assert result is None


# -----------------------------------------------------------------------
# E4.6 — shadow_validate_hook exception safety
# -----------------------------------------------------------------------

def test_hook_exception_safety() -> None:
    """Invalid input should not raise — returns None."""
    # Pass something that will cause compare_validators to fail internally
    result = shadow_validate_hook(
        "not-a-dict",  # type: ignore[arg-type]
        [],
        invoice_id="test-bad",
        config=ShadowConfig(sample_rate=1.0),
    )
    assert result is None


# -----------------------------------------------------------------------
# E4.7 — record_shadow_metrics
# -----------------------------------------------------------------------

def test_record_metrics_actionable() -> None:
    result = ShadowCompareResult(
        old_valid=False,
        new_valid=True,
        valid_match=False,
        old_codes=frozenset({"PAYABLE_TOTAL_MISMATCH"}),
        new_codes=frozenset(),
        codes_only_old=frozenset({"PAYABLE_TOTAL_MISMATCH"}),
        codes_only_new=frozenset(),
        codes_common=frozenset(),
    )
    record_shadow_metrics(result, whitelisted=False)
    c = get_shadow_counters()
    assert c[SHADOW_SAMPLED_TOTAL] == 1
    assert c[SHADOW_METRIC_NAME] == 1
    assert c[SHADOW_ACTIONABLE_TOTAL] == 1
    assert c[SHADOW_WHITELISTED_TOTAL] == 0


def test_record_metrics_whitelisted() -> None:
    result = ShadowCompareResult(
        old_valid=False,
        new_valid=True,
        valid_match=False,
        old_codes=frozenset({"ZERO_CONSUMPTION"}),
        new_codes=frozenset(),
        codes_only_old=frozenset({"ZERO_CONSUMPTION"}),
        codes_only_new=frozenset(),
        codes_common=frozenset(),
    )
    record_shadow_metrics(result, whitelisted=True)
    c = get_shadow_counters()
    assert c[SHADOW_SAMPLED_TOTAL] == 1
    assert c[SHADOW_METRIC_NAME] == 1
    assert c[SHADOW_WHITELISTED_TOTAL] == 1
    assert c[SHADOW_ACTIONABLE_TOTAL] == 0


def test_record_metrics_valid_match() -> None:
    result = ShadowCompareResult(
        old_valid=True,
        new_valid=True,
        valid_match=True,
        old_codes=frozenset(),
        new_codes=frozenset(),
        codes_only_old=frozenset(),
        codes_only_new=frozenset(),
        codes_common=frozenset(),
    )
    record_shadow_metrics(result, whitelisted=False)
    c = get_shadow_counters()
    assert c[SHADOW_SAMPLED_TOTAL] == 1
    assert c[SHADOW_METRIC_NAME] == 0  # no mismatch
    assert c[SHADOW_ACTIONABLE_TOTAL] == 0
    assert c[SHADOW_WHITELISTED_TOTAL] == 0


# -----------------------------------------------------------------------
# E4.8 — full pipeline: all 6 fixtures through hook
# -----------------------------------------------------------------------

def test_full_pipeline_all_fixtures() -> None:
    """Run all 6 validation_totals fixtures through the hook and verify counters."""
    cfg = ShadowConfig(sample_rate=1.0, whitelist=frozenset({"missing_totals_skips"}))

    fixtures = [
        "totals_ok.json",
        "payable_total_mismatch.json",
        "total_mismatch.json",
        "zero_consumption.json",
        "line_crosscheck_fail.json",
        "missing_totals_skips.json",
    ]

    results = []
    for name in fixtures:
        invoice = _load(name)
        r = shadow_validate_hook(invoice, [], invoice_id=f"pipe-{name}", config=cfg)
        assert r is not None, f"Hook returned None for {name}"
        results.append((name, r))

    c = get_shadow_counters()

    # All 6 sampled
    assert c[SHADOW_SAMPLED_TOTAL] == 6

    # Only missing_totals_skips has valid_match=False
    assert c[SHADOW_METRIC_NAME] == 1

    # missing_totals_skips is whitelisted → 0 actionable
    assert c[SHADOW_WHITELISTED_TOTAL] == 1
    assert c[SHADOW_ACTIONABLE_TOTAL] == 0

    # Verify individual results
    for name, r in results:
        if name == "missing_totals_skips.json":
            assert r.valid_match is False
        else:
            assert r.valid_match is True, f"{name} should have valid_match=True"
