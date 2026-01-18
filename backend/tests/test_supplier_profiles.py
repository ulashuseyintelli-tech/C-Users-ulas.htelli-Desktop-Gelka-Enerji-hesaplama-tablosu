"""
Tests for supplier profiles and canonical extractor.

Feature: invoice-analysis-system
Tests TR number parsing, supplier detection, and canonical extraction.
"""

import pytest
from hypothesis import given, strategies as st, settings

from app.supplier_profiles import (
    tr_money,
    tr_kwh,
    CanonicalInvoice,
    InvoiceLine,
    LineCode,
    TaxBreakdown,
    VATInfo,
    Totals,
    detect_supplier,
    get_profile_by_code,
    ALL_PROFILES,
    approx,
)


# ═══════════════════════════════════════════════════════════════════════════════
# TR Number Parser Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTRMoneyParser:
    """Test Turkish number format parsing."""

    def test_simple_decimal(self):
        """Parse simple decimal: 1234,56 -> 1234.56"""
        assert tr_money("1234,56") == 1234.56

    def test_thousands_separator(self):
        """Parse with thousands separator: 1.234,56 -> 1234.56"""
        assert tr_money("1.234,56") == 1234.56

    def test_large_number(self):
        """Parse large number: 593.000,00 -> 593000.00"""
        assert tr_money("593.000,00") == 593000.0

    def test_very_large_number(self):
        """Parse very large number: 1.190.021,09 -> 1190021.09"""
        assert tr_money("1.190.021,09") == 1190021.09

    def test_three_decimal_places(self):
        """Parse three decimal places: 4.192,947 -> 4192.947"""
        assert tr_money("4.192,947") == 4192.947

    def test_with_spaces(self):
        """Parse with spaces: 1 590,66 -> 1590.66"""
        assert tr_money("1 590,66") == 1590.66

    def test_negative_with_minus(self):
        """Parse negative: -1.234,56 -> -1234.56"""
        assert tr_money("-1.234,56") == -1234.56

    def test_negative_with_parens(self):
        """Parse negative with parentheses: (1.234,56) -> -1234.56"""
        assert tr_money("(1.234,56)") == -1234.56

    def test_empty_string(self):
        """Empty string returns None"""
        assert tr_money("") is None

    def test_none_input(self):
        """None-like input returns None"""
        assert tr_money(None) is None

    def test_integer_only(self):
        """Parse integer: 1234 -> 1234.0"""
        assert tr_money("1234") == 1234.0

    def test_integer_with_thousands(self):
        """Parse integer with thousands: 1.234 -> 1234.0"""
        assert tr_money("1.234") == 1234.0

    @settings(max_examples=50)
    @given(st.floats(min_value=0.01, max_value=10000000, allow_nan=False, allow_infinity=False))
    def test_roundtrip_positive(self, value):
        """
        Property: Formatting then parsing should return approximately the same value.
        """
        # Format as TR
        formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        parsed = tr_money(formatted)
        
        assert parsed is not None
        assert abs(parsed - value) < 0.01  # Allow small rounding error


# ═══════════════════════════════════════════════════════════════════════════════
# Supplier Detection Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSupplierDetection:
    """Test supplier detection from text and invoice numbers."""

    def test_detect_ck_bogazici_by_prefix(self):
        """Detect CK Boğaziçi by invoice prefix BBE"""
        profile = detect_supplier("", "BBE2025000123456")
        assert profile is not None
        assert profile.code == "ck_bogazici"

    def test_detect_enerjisa_by_prefix(self):
        """Detect Enerjisa by invoice prefix ES0"""
        profile = detect_supplier("", "ES02025001234567")
        assert profile is not None
        assert profile.code == "enerjisa"

    def test_detect_uludag_by_prefix(self):
        """Detect Uludağ by invoice prefix PBA"""
        profile = detect_supplier("", "PBA2025000123456")
        assert profile is not None
        assert profile.code == "uludag"

    def test_detect_ck_bogazici_by_text(self):
        """Detect CK Boğaziçi by text content"""
        text = "CK Boğaziçi Elektrik Perakende Satış A.Ş."
        profile = detect_supplier(text)
        assert profile is not None
        assert profile.code == "ck_bogazici"

    def test_detect_enerjisa_by_text(self):
        """Detect Enerjisa by text content"""
        text = "Enerjisa Başkent Elektrik Perakende Satış A.Ş."
        profile = detect_supplier(text)
        assert profile is not None
        assert profile.code == "enerjisa"

    def test_unknown_supplier(self):
        """Unknown supplier returns None"""
        profile = detect_supplier("Random text", "XYZ123")
        assert profile is None


# ═══════════════════════════════════════════════════════════════════════════════
# Invoice Line Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestInvoiceLine:
    """Test InvoiceLine model."""

    def test_crosscheck_pass(self):
        """Crosscheck passes when qty × price ≈ amount"""
        line = InvoiceLine(
            code=LineCode.ACTIVE_ENERGY,
            label="Enerji Bedeli",
            qty_kwh=1000.0,
            unit_price=3.5,
            amount=3500.0,
        )
        assert line.crosscheck() is True

    def test_crosscheck_fail(self):
        """Crosscheck fails when qty × price ≠ amount"""
        line = InvoiceLine(
            code=LineCode.ACTIVE_ENERGY,
            label="Enerji Bedeli",
            qty_kwh=1000.0,
            unit_price=3.5,
            amount=5000.0,  # Wrong!
        )
        assert line.crosscheck() is False

    def test_crosscheck_with_tolerance(self):
        """Crosscheck passes within tolerance"""
        line = InvoiceLine(
            code=LineCode.ACTIVE_ENERGY,
            label="Enerji Bedeli",
            qty_kwh=1000.0,
            unit_price=3.5,
            amount=3510.0,  # 0.3% off
        )
        assert line.crosscheck(tolerance=0.01) is True

    def test_is_valid(self):
        """Line is valid when amount is not None and not zero"""
        line = InvoiceLine(code=LineCode.ACTIVE_ENERGY, amount=100.0)
        assert line.is_valid() is True

    def test_is_not_valid_zero_amount(self):
        """Line is not valid when amount is zero"""
        line = InvoiceLine(code=LineCode.ACTIVE_ENERGY, amount=0.0)
        assert line.is_valid() is False


# ═══════════════════════════════════════════════════════════════════════════════
# Canonical Invoice Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanonicalInvoice:
    """Test CanonicalInvoice model."""

    def test_total_kwh_calculation(self):
        """Total kWh is sum of energy lines"""
        invoice = CanonicalInvoice(
            lines=[
                InvoiceLine(code=LineCode.ACTIVE_ENERGY_HIGH, qty_kwh=150000.0, amount=500000.0),
                InvoiceLine(code=LineCode.ACTIVE_ENERGY_LOW, qty_kwh=18330.0, amount=60000.0),
                InvoiceLine(code=LineCode.DISTRIBUTION, qty_kwh=168330.0, amount=100000.0),
            ]
        )
        assert invoice.total_kwh == 168330.0

    def test_energy_amount_calculation(self):
        """Energy amount is sum of energy line amounts"""
        invoice = CanonicalInvoice(
            lines=[
                InvoiceLine(code=LineCode.ACTIVE_ENERGY_HIGH, qty_kwh=150000.0, amount=500000.0),
                InvoiceLine(code=LineCode.ACTIVE_ENERGY_LOW, qty_kwh=18330.0, amount=60000.0),
                InvoiceLine(code=LineCode.DISTRIBUTION, qty_kwh=168330.0, amount=100000.0),
            ]
        )
        assert invoice.energy_amount == 560000.0

    def test_weighted_unit_price(self):
        """Weighted unit price = energy_amount / total_kwh"""
        invoice = CanonicalInvoice(
            lines=[
                InvoiceLine(code=LineCode.ACTIVE_ENERGY, qty_kwh=1000.0, amount=3500.0),
            ]
        )
        assert invoice.weighted_unit_price == 3.5

    def test_validation_payable_total_mismatch(self):
        """Validation catches payable/total mismatch"""
        invoice = CanonicalInvoice(
            totals=Totals(payable=22000.0, total=593000.0),
            lines=[InvoiceLine(code=LineCode.ACTIVE_ENERGY, qty_kwh=1000.0, amount=1000.0)],
        )
        errors = invoice.validate()
        assert any("PAYABLE_TOTAL_MISMATCH" in e for e in errors)

    def test_validation_zero_consumption(self):
        """Validation catches zero consumption"""
        invoice = CanonicalInvoice(
            lines=[],
            totals=Totals(total=1000.0),
        )
        errors = invoice.validate()
        assert any("ZERO_CONSUMPTION" in e for e in errors)

    def test_valid_invoice(self):
        """Valid invoice passes validation"""
        invoice = CanonicalInvoice(
            lines=[
                InvoiceLine(code=LineCode.ACTIVE_ENERGY, qty_kwh=1000.0, unit_price=3.5, amount=3500.0),
                InvoiceLine(code=LineCode.DISTRIBUTION, qty_kwh=1000.0, unit_price=1.0, amount=1000.0),
            ],
            taxes=TaxBreakdown(btv=35.0),
            vat=VATInfo(amount=907.0),
            totals=Totals(total=5442.0, payable=5442.0),
        )
        errors = invoice.validate()
        assert len(errors) == 0
        assert invoice.is_valid() is True


# ═══════════════════════════════════════════════════════════════════════════════
# Approx Function Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestApproxFunction:
    """Test approx helper function."""

    def test_approx_equal(self):
        """Values within tolerance are approximately equal"""
        assert approx(100.0, 102.0, tol=5.0) is True

    def test_approx_not_equal(self):
        """Values outside tolerance are not approximately equal"""
        assert approx(100.0, 110.0, tol=5.0) is False

    def test_approx_with_none(self):
        """None values are considered approximately equal"""
        assert approx(None, 100.0) is True
        assert approx(100.0, None) is True
        assert approx(None, None) is True
