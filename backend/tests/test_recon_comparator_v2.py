"""
Unit tests for app.recon.comparator_v2.compute_markup.

Scope (Task 7):
- positive markup
- negative markup (no clipping)
- zero ref cost → markup_pct = None
- gelka_estimate formula
- potential_savings formula
- multiplier < 1.0 → ValueError
- multiplier == 1.0 boundary accepted
- Decimal precision (no rounding inside function)
- return type contract (MarkupResult, Decimal fields)
- markup_pct > 100 passes through unchanged

Out of scope:
- router wiring (Task 4 already done; tests in 8/9)
- cost_engine_v2 (Task 6)
- frontend (Task 11/12)

The function under test is a pure function with no DB access, no logging,
and no side effects. Tests rely on direct call + return-value assertions.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.recon.comparator_v2 import MarkupResult, compute_markup


# ═══════════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeMarkup:
    """Unit tests for compute_markup pure function."""

    # ── Happy path: all four formulas ────────────────────────────────────────

    def test_positive_markup_basic_formulas(self):
        """Invoice > Reference → positive markup; verify all four formulas."""
        invoice = Decimal("1200.00")
        reference = Decimal("1000.00")
        multiplier = Decimal("1.05")

        result = compute_markup(invoice, reference, multiplier)

        # markup_tl = invoice − reference
        assert result.supplier_markup_tl == Decimal("200.00")
        # markup_pct = (invoice − ref) / ref × 100 = 20.00
        assert result.supplier_markup_pct == Decimal("20")
        # gelka_estimate = ref × multiplier = 1050.00
        assert result.gelka_estimate_tl == Decimal("1050.0000")
        # savings = invoice − gelka_estimate = 150.00
        assert result.potential_savings_tl == Decimal("150.0000")

    # ── Negative markup: no clipping ─────────────────────────────────────────

    def test_negative_markup_passes_through_unchanged(self):
        """Invoice < Reference → markup_tl negative; no clip/abs/cap."""
        invoice = Decimal("800.00")
        reference = Decimal("1000.00")
        multiplier = Decimal("1.05")

        result = compute_markup(invoice, reference, multiplier)

        assert result.supplier_markup_tl == Decimal("-200.00")
        assert result.supplier_markup_tl < Decimal("0")  # truly negative

    def test_negative_pct_when_invoice_below_reference(self):
        """When invoice < reference, markup_pct is negative — sign preserved."""
        invoice = Decimal("900.00")
        reference = Decimal("1000.00")
        multiplier = Decimal("1.05")

        result = compute_markup(invoice, reference, multiplier)

        # (900 - 1000) / 1000 × 100 = -10
        assert result.supplier_markup_pct == Decimal("-10")
        assert result.supplier_markup_pct < Decimal("0")

    # ── Zero reference cost edge (REQ-6.7) ───────────────────────────────────

    def test_zero_reference_cost_returns_none_markup_pct(self):
        """REQ-6.7: ref_cost == 0 → markup_pct = None.

        Other fields:
        - markup_tl = invoice − 0 = invoice
        - gelka_estimate = 0 × multiplier = 0
        - savings = invoice − 0 = invoice
        """
        invoice = Decimal("500.00")
        reference = Decimal("0")
        multiplier = Decimal("1.05")

        result = compute_markup(invoice, reference, multiplier)

        assert result.supplier_markup_pct is None
        assert result.supplier_markup_tl == Decimal("500.00")
        assert result.gelka_estimate_tl == Decimal("0")
        assert result.potential_savings_tl == Decimal("500.00")

    # ── Invoice equals reference: zero markup, no division-by-zero risk ──────

    def test_invoice_equals_reference_zero_markup(self):
        """Invoice == reference → markup_tl = 0, markup_pct = 0."""
        invoice = Decimal("1000.00")
        reference = Decimal("1000.00")
        multiplier = Decimal("1.05")

        result = compute_markup(invoice, reference, multiplier)

        assert result.supplier_markup_tl == Decimal("0.00")
        assert result.supplier_markup_pct == Decimal("0")
        assert result.gelka_estimate_tl == Decimal("1050.0000")
        # savings = 1000 − 1050 = −50 (Gelka offers slightly above invoice here)
        assert result.potential_savings_tl == Decimal("-50.0000")

    # ── gelka_estimate formula (multiple multipliers) ────────────────────────

    def test_gelka_estimate_formula(self):
        """gelka_estimate = reference × multiplier, across several multipliers."""
        reference = Decimal("1000.00")
        invoice = Decimal("1500.00")  # arbitrary; not under test here

        for multiplier, expected in [
            (Decimal("1.0"), Decimal("1000.00")),
            (Decimal("1.05"), Decimal("1050.0000")),
            (Decimal("1.20"), Decimal("1200.0000")),
            (Decimal("2.0"), Decimal("2000.00")),
        ]:
            result = compute_markup(invoice, reference, multiplier)
            assert result.gelka_estimate_tl == expected, (
                f"multiplier={multiplier} expected={expected}, "
                f"got={result.gelka_estimate_tl}"
            )

    # ── potential_savings formula (positive AND negative) ────────────────────

    def test_potential_savings_formula_positive_and_negative(self):
        """savings = invoice − gelka_estimate; verify both signs pass through."""
        # Case A: positive savings (Gelka cheaper than invoice)
        result_pos = compute_markup(
            invoice_total_tl=Decimal("1300.00"),
            reference_cost_tl=Decimal("1000.00"),
            gelka_margin_multiplier=Decimal("1.05"),
        )
        # gelka_estimate = 1050; savings = 1300 − 1050 = 250
        assert result_pos.potential_savings_tl == Decimal("250.0000")
        assert result_pos.potential_savings_tl > Decimal("0")

        # Case B: negative savings (Gelka more expensive than invoice)
        result_neg = compute_markup(
            invoice_total_tl=Decimal("900.00"),
            reference_cost_tl=Decimal("1000.00"),
            gelka_margin_multiplier=Decimal("1.05"),
        )
        # gelka_estimate = 1050; savings = 900 − 1050 = -150
        assert result_neg.potential_savings_tl == Decimal("-150.0000")
        assert result_neg.potential_savings_tl < Decimal("0")

    # ── REQ-2.6: multiplier validation ───────────────────────────────────────

    @pytest.mark.parametrize(
        "bad_multiplier",
        [
            Decimal("0.99"),
            Decimal("0.50"),
            Decimal("0"),
            Decimal("-0.10"),
        ],
    )
    def test_multiplier_below_one_raises_value_error(self, bad_multiplier):
        """REQ-2.6: multiplier < 1.0 → ValueError with informative message."""
        with pytest.raises(ValueError, match="must be >= 1.0"):
            compute_markup(
                invoice_total_tl=Decimal("1000.00"),
                reference_cost_tl=Decimal("1000.00"),
                gelka_margin_multiplier=bad_multiplier,
            )

    def test_multiplier_exactly_one_accepted(self):
        """Boundary: multiplier == 1.0 is accepted; gelka_estimate == reference."""
        result = compute_markup(
            invoice_total_tl=Decimal("1500.00"),
            reference_cost_tl=Decimal("1000.00"),
            gelka_margin_multiplier=Decimal("1.0"),
        )
        assert result.gelka_estimate_tl == Decimal("1000.00")
        # savings = 1500 − 1000 = 500
        assert result.potential_savings_tl == Decimal("500.00")

    # ── Decimal precision: no rounding inside the function ───────────────────

    def test_decimal_precision_preserved_no_rounding(self):
        """Function does NOT round; raw Decimal arithmetic preserved end-to-end."""
        invoice = Decimal("1234.567")
        reference = Decimal("987.654")
        multiplier = Decimal("1.0500001")

        result = compute_markup(invoice, reference, multiplier)

        # Manually compute expected values bit-for-bit (no rounding)
        expected_markup_tl = invoice - reference  # 246.913
        expected_markup_pct = (
            (invoice - reference) / reference * Decimal("100")
        )
        expected_gelka = reference * multiplier
        expected_savings = invoice - expected_gelka

        assert result.supplier_markup_tl == expected_markup_tl
        assert result.supplier_markup_pct == expected_markup_pct
        assert result.gelka_estimate_tl == expected_gelka
        assert result.potential_savings_tl == expected_savings
        # Confirm none of the values were quantized to 2dp internally
        # (a quantized value would be == its 2dp form; here it's not)
        assert result.gelka_estimate_tl != result.gelka_estimate_tl.quantize(
            Decimal("0.01")
        )

    # ── Return type contract ─────────────────────────────────────────────────

    def test_returns_markup_result_dataclass_with_decimal_fields(self):
        """Return value is MarkupResult; all numeric fields are Decimal (or None)."""
        result = compute_markup(
            invoice_total_tl=Decimal("1200.00"),
            reference_cost_tl=Decimal("1000.00"),
            gelka_margin_multiplier=Decimal("1.05"),
        )

        assert isinstance(result, MarkupResult)
        assert isinstance(result.supplier_markup_tl, Decimal)
        assert isinstance(result.supplier_markup_pct, Decimal)
        assert isinstance(result.gelka_estimate_tl, Decimal)
        assert isinstance(result.potential_savings_tl, Decimal)

        # supplier_markup_pct may be None only when reference_cost_tl == 0
        result_zero = compute_markup(
            invoice_total_tl=Decimal("500.00"),
            reference_cost_tl=Decimal("0"),
            gelka_margin_multiplier=Decimal("1.05"),
        )
        assert result_zero.supplier_markup_pct is None

    # ── REQ-6.5/REQ-6.6: large markup_pct passes through unchanged ───────────

    def test_large_markup_pct_above_100_passes_through(self):
        """REQ-6.5: markup_pct > 100 emitted unchanged (no clip/cap)."""
        # invoice = 3 × reference → markup_pct = 200
        invoice = Decimal("3000.00")
        reference = Decimal("1000.00")
        multiplier = Decimal("1.05")

        result = compute_markup(invoice, reference, multiplier)

        assert result.supplier_markup_pct == Decimal("200")
        assert result.supplier_markup_pct > Decimal("100")
