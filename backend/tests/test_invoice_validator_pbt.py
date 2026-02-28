"""Minimal property-based tests for invoice validation (Faz A / 4.1).

P1: Invalid ETTN always detected
P2: Inconsistent periods always detected
P3: Reactive mismatch always detected
"""

from __future__ import annotations

import re
import string

from hypothesis import given, settings
from hypothesis import strategies as st

from app.invoice.validation import ValidationErrorCode, validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_VALID_PERIODS = [
    {"code": "T1", "start": "2026-01-01", "end": "2026-01-31", "kwh": 100, "amount": 50.0},
    {"code": "T2", "start": "2026-01-01", "end": "2026-01-31", "kwh": 80, "amount": 40.0},
    {"code": "T3", "start": "2026-01-01", "end": "2026-01-31", "kwh": 60, "amount": 30.0},
]


def _base_invoice(**overrides: object) -> dict:
    inv: dict = {
        "ettn": "550e8400-e29b-41d4-a716-446655440000",
        "periods": list(_VALID_PERIODS),
        "reactive": {"penalty_amount": 0, "penalty_kvarh": 0},
    }
    inv.update(overrides)
    return inv


# Strategy: strings that do NOT match UUID format
_non_uuid_text = st.text(
    alphabet=string.ascii_letters + string.digits + "-",
    min_size=1,
    max_size=60,
).filter(lambda s: not _UUID_RE.match(s))

_ETTN_ERROR_CODES = {
    ValidationErrorCode.MISSING_FIELD,
    ValidationErrorCode.INVALID_FORMAT,
    ValidationErrorCode.INVALID_ETTN,
}


# ---------------------------------------------------------------------------
# P1 — Invalid ETTN always detected
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    ettn=st.one_of(
        st.none(),
        st.just(""),
        st.integers(),
        st.booleans(),
        _non_uuid_text,
    )
)
def test_invalid_ettn_always_detected(ettn: object) -> None:
    invoice = _base_invoice(ettn=ettn)
    result = validate(invoice)
    assert not result.valid
    codes = {e.code for e in result.errors}
    assert codes & _ETTN_ERROR_CODES, (
        f"Expected at least one ETTN error code, got {codes}"
    )


# ---------------------------------------------------------------------------
# P2 — Inconsistent periods always detected
# ---------------------------------------------------------------------------

@settings(max_examples=100)
@given(
    t1_start=st.dates(),
    t2_start=st.dates(),
    t3_start=st.dates(),
)
def test_inconsistent_periods_always_detected(
    t1_start,
    t2_start,
    t3_start,
) -> None:
    from hypothesis import assume

    # At least one start must differ
    assume(not (t1_start == t2_start == t3_start))

    end = "2026-01-31"
    periods = [
        {"code": "T1", "start": t1_start.isoformat(), "end": end, "kwh": 100, "amount": 50.0},
        {"code": "T2", "start": t2_start.isoformat(), "end": end, "kwh": 80, "amount": 40.0},
        {"code": "T3", "start": t3_start.isoformat(), "end": end, "kwh": 60, "amount": 30.0},
    ]
    invoice = _base_invoice(periods=periods)
    result = validate(invoice)
    codes = {e.code for e in result.errors}
    assert ValidationErrorCode.INCONSISTENT_PERIODS in codes


# ---------------------------------------------------------------------------
# P3 — Reactive mismatch always detected
# ---------------------------------------------------------------------------

_positive = st.floats(min_value=0.01, max_value=1e9, allow_nan=False, allow_infinity=False)
_zero = st.just(0.0)


@settings(max_examples=100)
@given(
    data=st.one_of(
        # amount > 0, kvarh == 0
        st.tuples(_positive, _zero),
        # kvarh > 0, amount == 0
        st.tuples(_zero, _positive),
    )
)
def test_reactive_mismatch_always_detected(data: tuple[float, float]) -> None:
    amount, kvarh = data
    invoice = _base_invoice(
        reactive={"penalty_amount": amount, "penalty_kvarh": kvarh}
    )
    result = validate(invoice)
    codes = {e.code for e in result.errors}
    assert ValidationErrorCode.REACTIVE_PENALTY_MISMATCH in codes
