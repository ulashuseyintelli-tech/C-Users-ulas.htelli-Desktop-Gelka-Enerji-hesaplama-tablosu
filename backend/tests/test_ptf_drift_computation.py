"""
Tests for compute_drift / record_drift — Phase 2 T2.2 (ptf-sot-unification).

Pure unit tests — no DB, no FastAPI. Validates:
  - severity classification (low / high / missing_legacy)
  - symmetric drift baseline (max(|c|, |l|), not biased to canonical)
  - 6-decimal rounding (no float jitter)
  - canonical-empty short-circuit (return None, caller raises 404)
  - both-zero edge case (severity=low, deltas=0, NOT None)
  - threshold boundary at 0.5%
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.ptf_drift_log import (
    DRIFT_HIGH_PCT,
    compute_drift,
    DriftRecord,
)


# Minimal stand-in for ParsedMarketRecord — compute_drift only reads
# `ptf_tl_per_mwh`, so a tiny dataclass is enough and avoids importing the
# full pricing stack into a unit test.
@dataclass
class _Rec:
    ptf_tl_per_mwh: float


def _records(prices: list[float]) -> list[_Rec]:
    return [_Rec(ptf_tl_per_mwh=p) for p in prices]


# ── Canonical-side edge cases ─────────────────────────────────────────────────


def test_compute_drift_canonical_empty_returns_none():
    """Canonical empty → None (caller raises 404; no row useful)."""
    result = compute_drift(
        canonical_records=[],
        legacy_records=_records([2000.0]),
        period="2026-03",
        request_hash="a" * 64,
    )
    assert result is None


def test_compute_drift_canonical_none_returns_none():
    """Canonical None (defensive) → None."""
    result = compute_drift(
        canonical_records=None,
        legacy_records=_records([2000.0]),
        period="2026-03",
        request_hash="a" * 64,
    )
    assert result is None


# ── missing_legacy operational state ──────────────────────────────────────────


def test_compute_drift_legacy_none_yields_missing_legacy():
    """Canonical present + legacy None → severity='missing_legacy'."""
    result = compute_drift(
        canonical_records=_records([3000.0, 3010.0]),
        legacy_records=None,
        period="2026-03",
        request_hash="b" * 64,
    )
    assert result is not None
    assert result.severity == "missing_legacy"
    assert result.legacy_price is None
    assert result.delta_abs is None
    assert result.delta_pct is None
    assert result.canonical_price == pytest.approx(3005.0)
    assert result.period == "2026-03"


def test_compute_drift_legacy_empty_yields_missing_legacy():
    """Canonical present + legacy=[] → severity='missing_legacy' (NOT 'low')."""
    result = compute_drift(
        canonical_records=_records([3000.0]),
        legacy_records=[],
        period="2026-03",
        request_hash="c" * 64,
    )
    assert result is not None
    assert result.severity == "missing_legacy"
    assert result.legacy_price is None


# ── Severity classification (low / high) ──────────────────────────────────────


def test_compute_drift_equal_values_yield_low_severity():
    """Identical canonical & legacy averages → severity='low', deltas=0."""
    result = compute_drift(
        canonical_records=_records([3000.0, 3000.0]),
        legacy_records=_records([3000.0]),
        period="2026-03",
        request_hash="d" * 64,
    )
    assert result is not None
    assert result.severity == "low"
    assert result.delta_abs == 0.0
    assert result.delta_pct == 0.0
    assert result.canonical_price == 3000.0
    assert result.legacy_price == 3000.0


def test_compute_drift_below_threshold_yields_low_severity():
    """0.3% drift → severity='low' (under 0.5% gate)."""
    # canonical=3000, legacy=2991 → delta=9, pct = 9/3000 = 0.3%
    result = compute_drift(
        canonical_records=_records([3000.0]),
        legacy_records=_records([2991.0]),
        period="2026-03",
        request_hash="e" * 64,
    )
    assert result is not None
    assert result.severity == "low"
    assert result.delta_abs == 9.0
    # Symmetric baseline = max(3000, 2991) = 3000; pct = 9/3000*100 = 0.3
    assert result.delta_pct == pytest.approx(0.3)


def test_compute_drift_above_threshold_yields_high_severity():
    """1% drift → severity='high' (over 0.5% gate)."""
    # canonical=3000, legacy=2700 → delta=300, baseline=3000, pct=10%
    result = compute_drift(
        canonical_records=_records([3000.0]),
        legacy_records=_records([2700.0]),
        period="2026-03",
        request_hash="f" * 64,
    )
    assert result is not None
    assert result.severity == "high"
    assert result.delta_abs == 300.0
    assert result.delta_pct == pytest.approx(10.0)


def test_compute_drift_threshold_boundary_is_low():
    """Exactly at 0.5% → severity='low' (boundary inclusive on the low side)."""
    # canonical=2000, legacy=1990 → delta=10, baseline=2000, pct=0.5%
    result = compute_drift(
        canonical_records=_records([2000.0]),
        legacy_records=_records([1990.0]),
        period="2026-03",
        request_hash="0" * 64,
    )
    assert result is not None
    assert result.delta_pct == pytest.approx(0.5)
    assert result.severity == "low"
    # And confirm the constant is what we tested against
    assert DRIFT_HIGH_PCT == 0.5


# ── Symmetric baseline (no canonical bias) ────────────────────────────────────


def test_compute_drift_baseline_is_symmetric():
    """Drift % uses max(|c|, |l|), so swapping does not change the magnitude.

    Without symmetry, asking 'is canonical 1% higher than legacy' and
    'is legacy 1% lower than canonical' would give different drift values.
    The Phase 2 gate decision must be neutral.
    """
    canon_high = compute_drift(
        canonical_records=_records([3030.0]),  # 1% above
        legacy_records=_records([3000.0]),
        period="2026-03",
        request_hash="1" * 64,
    )
    canon_low = compute_drift(
        canonical_records=_records([3000.0]),
        legacy_records=_records([3030.0]),  # 1% above
        period="2026-03",
        request_hash="2" * 64,
    )
    assert canon_high is not None and canon_low is not None
    # |delta_abs| equal in both directions
    assert abs(canon_high.delta_abs) == abs(canon_low.delta_abs)
    # delta_pct identical (always positive — abs of delta over symmetric baseline)
    assert canon_high.delta_pct == canon_low.delta_pct
    assert canon_high.severity == canon_low.severity


# ── Both-zero edge case (per user direction) ──────────────────────────────────


def test_compute_drift_both_zero_yields_low_severity_zero_deltas():
    """canonical_avg == 0 AND legacy_avg == 0 → severity='low', deltas=0.

    Per user direction: this is 'no drift' (identical empty market data),
    NOT an invalid state. None would imply 'cannot compute' which is wrong.
    """
    result = compute_drift(
        canonical_records=_records([0.0]),
        legacy_records=_records([0.0]),
        period="2026-03",
        request_hash="3" * 64,
    )
    assert result is not None
    assert result.severity == "low"
    assert result.delta_abs == 0.0
    assert result.delta_pct == 0.0


# ── Six-decimal rounding ──────────────────────────────────────────────────────


def test_compute_drift_rounds_to_six_decimals():
    """Float jitter → both delta_abs and delta_pct rounded to 6 decimals."""
    # canonical avg = (1000.123456789 + 1000.987654321) / 2 = 1000.555555555
    # legacy   avg = 1000.111111111
    result = compute_drift(
        canonical_records=_records([1000.123456789, 1000.987654321]),
        legacy_records=_records([1000.111111111]),
        period="2026-03",
        request_hash="4" * 64,
    )
    assert result is not None
    # Round-trip property: any further round at 6 decimals is a no-op.
    assert result.delta_abs == round(result.delta_abs, 6)
    assert result.delta_pct == round(result.delta_pct, 6)
    assert result.canonical_price == round(result.canonical_price, 6)
    assert result.legacy_price == round(result.legacy_price, 6)


# ── Defensive: malformed inputs ───────────────────────────────────────────────


def test_compute_drift_records_without_attribute_returns_none():
    """Records missing ptf_tl_per_mwh → caller-side bug → returns None safely."""
    class _Bad:
        pass

    result = compute_drift(
        canonical_records=[_Bad()],
        legacy_records=_records([2000.0]),
        period="2026-03",
        request_hash="5" * 64,
    )
    # Canonical avg is None (AttributeError caught) → short-circuit returns None.
    assert result is None
