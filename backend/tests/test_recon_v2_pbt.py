"""
Property-Based Tests for v2 cost headline modules.

Scope (Task 8) — INVARIANT STABILITY (not formula correctness):
- markup_tl == invoice - reference (algebraic identity)
- markup_pct sign consistency
- gelka_estimate == ref × multiplier
- potential_savings == invoice - gelka
- multiplier >= 1.0 invariant
- no internal rounding invariant
- negative values pass through unchanged
- zero reference → pct None invariant
- Decimal arithmetic closed under generated inputs

Critical monotonicity properties:
- If invoice_total INCREASES (ref, multiplier fixed) → potential_savings
  must monotonically INCREASE.
- If multiplier INCREASES (invoice, ref fixed) → potential_savings
  must monotonically DECREASE.

These two are essentially impossible to surface in FE testing but extremely
valuable as backend invariants.

Out of scope:
- cost_engine_v2 (Task 6 unit tests cover core paths; PBT for it deferred
  unless follow-up explicitly requests it — current Task 8 framing focuses
  on comparator_v2 markup invariants which match the user's listed scope)
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.recon.comparator_v2 import MarkupResult, compute_markup


# ═══════════════════════════════════════════════════════════════════════════════
# Strategies (Decimal-only — all arithmetic stays in Decimal)
# ═══════════════════════════════════════════════════════════════════════════════


# Wide range, 2 decimal places — typical TL invoice/reference values
st_invoice = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("1000000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Reference cost — strictly non-negative; tests that need ref>0 use the
# `nonzero` variant below.
st_reference = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("1000000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# For percentage-sign and monotonicity-by-multiplier tests we need ref > 0.
st_reference_nonzero = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("1000000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# Valid multipliers (>= 1.0). 4dp to keep arithmetic non-trivial.
st_multiplier_valid = st.decimals(
    min_value=Decimal("1.0"),
    max_value=Decimal("5.0"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)

# Invalid multipliers (< 1.0) — used to assert ValueError.
st_multiplier_invalid = st.decimals(
    min_value=Decimal("-10"),
    max_value=Decimal("0.9999"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)


# Hypothesis settings — pure function, fast; disable deadline because some
# Decimal generation may exceed the default 200ms in CI under load.
PBT_SETTINGS = settings(max_examples=200, deadline=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Algebraic identity properties
# ═══════════════════════════════════════════════════════════════════════════════


class TestAlgebraicIdentities:
    """Properties that should hold by definition of the formulas."""

    @given(invoice=st_invoice, reference=st_reference, multiplier=st_multiplier_valid)
    @PBT_SETTINGS
    def test_markup_tl_equals_invoice_minus_reference(
        self, invoice, reference, multiplier
    ):
        """markup_tl == invoice − reference, exactly (no rounding)."""
        result = compute_markup(invoice, reference, multiplier)
        assert result.supplier_markup_tl == invoice - reference

    @given(
        invoice=st_invoice,
        reference=st_reference_nonzero,
        multiplier=st_multiplier_valid,
    )
    @PBT_SETTINGS
    def test_gelka_estimate_equals_reference_times_multiplier(
        self, invoice, reference, multiplier
    ):
        """gelka_estimate == reference × multiplier, exactly."""
        result = compute_markup(invoice, reference, multiplier)
        assert result.gelka_estimate_tl == reference * multiplier

    @given(invoice=st_invoice, reference=st_reference, multiplier=st_multiplier_valid)
    @PBT_SETTINGS
    def test_potential_savings_equals_invoice_minus_gelka(
        self, invoice, reference, multiplier
    ):
        """savings == invoice − gelka_estimate, exactly."""
        result = compute_markup(invoice, reference, multiplier)
        assert result.potential_savings_tl == invoice - result.gelka_estimate_tl


# ═══════════════════════════════════════════════════════════════════════════════
# Sign-consistency properties
# ═══════════════════════════════════════════════════════════════════════════════


class TestSignConsistency:
    """markup_pct sign matches markup_tl sign when ref > 0."""

    @given(
        invoice=st_invoice,
        reference=st_reference_nonzero,
        multiplier=st_multiplier_valid,
    )
    @PBT_SETTINGS
    def test_markup_pct_sign_matches_markup_tl_sign(
        self, invoice, reference, multiplier
    ):
        """markup_pct and markup_tl have the same sign (or both zero)."""
        result = compute_markup(invoice, reference, multiplier)
        assert result.supplier_markup_pct is not None  # ref > 0 here

        if result.supplier_markup_tl > 0:
            assert result.supplier_markup_pct > 0
        elif result.supplier_markup_tl < 0:
            assert result.supplier_markup_pct < 0
        else:
            assert result.supplier_markup_pct == 0

    @given(invoice=st_invoice, reference=st_reference, multiplier=st_multiplier_valid)
    @PBT_SETTINGS
    def test_negative_markup_passes_through_unchanged(
        self, invoice, reference, multiplier
    ):
        """invoice < reference → markup_tl is the (negative) raw difference,
        not clipped to zero, abs()-ed, or capped."""
        assume(invoice < reference)
        result = compute_markup(invoice, reference, multiplier)
        assert result.supplier_markup_tl < 0
        assert result.supplier_markup_tl == invoice - reference


# ═══════════════════════════════════════════════════════════════════════════════
# Validation invariants (multiplier guard)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultiplierValidation:
    """multiplier < 1.0 always raises; >= 1.0 never raises (REQ-2.6)."""

    @given(
        invoice=st_invoice,
        reference=st_reference,
        bad_multiplier=st_multiplier_invalid,
    )
    @PBT_SETTINGS
    def test_multiplier_below_one_always_raises(
        self, invoice, reference, bad_multiplier
    ):
        with pytest.raises(ValueError):
            compute_markup(invoice, reference, bad_multiplier)

    @given(invoice=st_invoice, reference=st_reference, multiplier=st_multiplier_valid)
    @PBT_SETTINGS
    def test_multiplier_at_or_above_one_never_raises(
        self, invoice, reference, multiplier
    ):
        # Should not raise — fully consume the result to defeat lazy paths
        result = compute_markup(invoice, reference, multiplier)
        assert isinstance(result, MarkupResult)


# ═══════════════════════════════════════════════════════════════════════════════
# Zero-reference invariant (REQ-6.7)
# ═══════════════════════════════════════════════════════════════════════════════


class TestZeroReferenceInvariant:
    """When reference == 0, markup_pct must be None for every invoice/multiplier."""

    @given(invoice=st_invoice, multiplier=st_multiplier_valid)
    @PBT_SETTINGS
    def test_zero_reference_yields_none_pct(self, invoice, multiplier):
        result = compute_markup(invoice, Decimal("0"), multiplier)
        assert result.supplier_markup_pct is None
        # Other fields still well-defined
        assert result.supplier_markup_tl == invoice
        assert result.gelka_estimate_tl == Decimal("0")
        assert result.potential_savings_tl == invoice


# ═══════════════════════════════════════════════════════════════════════════════
# No-rounding invariant
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoInternalRounding:
    """Function must not quantize/round internally — caller rounds at boundary."""

    @given(
        invoice=st_invoice,
        reference=st_reference_nonzero,
        multiplier=st_multiplier_valid,
    )
    @PBT_SETTINGS
    def test_no_internal_rounding(self, invoice, reference, multiplier):
        """All output fields equal raw Decimal arithmetic with NO quantize."""
        result = compute_markup(invoice, reference, multiplier)
        # Each field equals the raw expression
        assert result.supplier_markup_tl == invoice - reference
        assert result.gelka_estimate_tl == reference * multiplier
        assert result.potential_savings_tl == invoice - (reference * multiplier)
        if result.supplier_markup_pct is not None:
            assert (
                result.supplier_markup_pct
                == (invoice - reference) / reference * Decimal("100")
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Decimal arithmetic closure
# ═══════════════════════════════════════════════════════════════════════════════


class TestDecimalClosure:
    """Output Decimal fields stay Decimal (or None for pct); no float drift."""

    @given(invoice=st_invoice, reference=st_reference, multiplier=st_multiplier_valid)
    @PBT_SETTINGS
    def test_decimal_closed_under_inputs(self, invoice, reference, multiplier):
        result = compute_markup(invoice, reference, multiplier)
        assert isinstance(result.supplier_markup_tl, Decimal)
        assert isinstance(result.gelka_estimate_tl, Decimal)
        assert isinstance(result.potential_savings_tl, Decimal)
        # markup_pct: Decimal or None (None only when reference == 0)
        if reference == Decimal("0"):
            assert result.supplier_markup_pct is None
        else:
            assert isinstance(result.supplier_markup_pct, Decimal)


# ═══════════════════════════════════════════════════════════════════════════════
# Monotonicity (the two highest-value PBT properties for v2)
# ═══════════════════════════════════════════════════════════════════════════════


class TestMonotonicity:
    """If a single input moves, the dependent output moves the right direction.

    These properties cannot be exercised reliably from the FE; only PBT
    catches drift (e.g., a future caching layer reordering computations,
    a refactor accidentally swapping the sign, etc.).
    """

    # ── savings monotonically increases in invoice (ref, multiplier fixed) ──

    @given(
        reference=st_reference,
        multiplier=st_multiplier_valid,
        delta=st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("100000"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
        invoice_low=st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("500000"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @PBT_SETTINGS
    def test_savings_strictly_monotonic_in_invoice(
        self, reference, multiplier, delta, invoice_low
    ):
        """invoice_high > invoice_low → savings(high) > savings(low),
        when reference and multiplier are constant."""
        invoice_high = invoice_low + delta

        low = compute_markup(invoice_low, reference, multiplier)
        high = compute_markup(invoice_high, reference, multiplier)

        # Strict monotonicity — invoice strictly larger ⇒ savings strictly larger
        assert high.potential_savings_tl > low.potential_savings_tl
        # Difference equals delta exactly (no rounding error)
        assert high.potential_savings_tl - low.potential_savings_tl == delta

    # ── savings monotonically DECREASES in multiplier (invoice, ref fixed) ──

    @given(
        invoice=st_invoice,
        reference=st_reference_nonzero,  # delta only matters when ref > 0
        multiplier_low=st.decimals(
            min_value=Decimal("1.0"),
            max_value=Decimal("3.0"),
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
        delta=st.decimals(
            min_value=Decimal("0.0001"),
            max_value=Decimal("2.0"),
            places=4,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @PBT_SETTINGS
    def test_savings_strictly_monotonic_decreasing_in_multiplier(
        self, invoice, reference, multiplier_low, delta
    ):
        """multiplier_high > multiplier_low → savings(high) < savings(low),
        when invoice and reference are constant AND reference > 0.

        Reference must be > 0 for the relation to be strict; when reference
        is zero, multiplier has no effect on gelka_estimate (always 0).
        """
        multiplier_high = multiplier_low + delta

        low = compute_markup(invoice, reference, multiplier_low)
        high = compute_markup(invoice, reference, multiplier_high)

        # Strict monotonicity in the opposite direction
        assert high.potential_savings_tl < low.potential_savings_tl
        # Difference equals -reference × delta exactly (no rounding error)
        assert (
            low.potential_savings_tl - high.potential_savings_tl
            == reference * delta
        )

    # ── markup monotonic in invoice (sanity / consistency closure) ──────────

    @given(
        reference=st_reference,
        multiplier=st_multiplier_valid,
        delta=st.decimals(
            min_value=Decimal("0.01"),
            max_value=Decimal("100000"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
        invoice_low=st.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("500000"),
            places=2,
            allow_nan=False,
            allow_infinity=False,
        ),
    )
    @PBT_SETTINGS
    def test_markup_strictly_monotonic_in_invoice(
        self, reference, multiplier, delta, invoice_low
    ):
        """invoice_high > invoice_low → markup_tl(high) > markup_tl(low)."""
        invoice_high = invoice_low + delta

        low = compute_markup(invoice_low, reference, multiplier)
        high = compute_markup(invoice_high, reference, multiplier)

        assert high.supplier_markup_tl > low.supplier_markup_tl
        assert high.supplier_markup_tl - low.supplier_markup_tl == delta
