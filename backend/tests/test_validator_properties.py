"""
Property-based tests for the validation module.

Feature: invoice-analysis-system
Uses Hypothesis for property-based testing with minimum 100 iterations.
"""

import pytest
from hypothesis import given, strategies as st, settings

from app.models import InvoiceExtraction, FieldValue, RawBreakdown
from app.validator import (
    validate_extraction,
    MIN_UNIT_PRICE,
    MAX_UNIT_PRICE,
    LOW_CONFIDENCE_THRESHOLD,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Strategies for generating test data
# ═══════════════════════════════════════════════════════════════════════════════

@st.composite
def field_value_strategy(draw, value_strategy=st.floats(min_value=0.01, max_value=100000)):
    """Generate a FieldValue with configurable value range."""
    return FieldValue(
        value=draw(st.one_of(st.none(), value_strategy)),
        confidence=draw(st.floats(min_value=0.0, max_value=1.0)),
        evidence=draw(st.text(min_size=0, max_size=50)),
        page=draw(st.integers(min_value=1, max_value=10))
    )


@st.composite
def invoice_extraction_strategy(draw):
    """Generate a complete InvoiceExtraction with random values."""
    return InvoiceExtraction(
        vendor=draw(st.sampled_from(["enerjisa", "ck_bogazici", "ekvator", "unknown"])),
        invoice_period=draw(st.from_regex(r"20[2-3][0-9]-[0-1][0-9]", fullmatch=True)),
        consumption_kwh=draw(field_value_strategy(st.floats(min_value=-100, max_value=500000))),
        current_active_unit_price_tl_per_kwh=draw(field_value_strategy(st.floats(min_value=-5, max_value=50))),
        distribution_unit_price_tl_per_kwh=draw(field_value_strategy(st.floats(min_value=0, max_value=10))),
        demand_qty=draw(field_value_strategy(st.floats(min_value=0, max_value=1000))),
        demand_unit_price_tl_per_unit=draw(field_value_strategy(st.floats(min_value=0, max_value=100))),
        invoice_total_with_vat_tl=draw(field_value_strategy(st.floats(min_value=0, max_value=10000000))),
        raw_breakdown=None
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Property 2: Validation Completeness
# **Validates: Requirements 4.1, 4.2, 4.4, 4.7**
#
# For any InvoiceExtraction where:
#   - consumption_kwh is null or zero, OR
#   - current_active_unit_price is outside [0.1, 30.0], OR
#   - (demand_qty > 0 AND demand_unit_price is null)
# Then:
#   - ValidationResult.is_ready_for_pricing SHALL be false
#   - The corresponding field SHALL appear in missing_fields or errors
# ═══════════════════════════════════════════════════════════════════════════════

class TestProperty2ValidationCompleteness:
    """
    Feature: invoice-analysis-system, Property 2: Validation Completeness
    **Validates: Requirements 4.1, 4.2, 4.4, 4.7**
    """

    @settings(max_examples=100)
    @given(st.floats(min_value=0.0, max_value=1.0), st.text(max_size=20))
    def test_null_consumption_marks_not_ready(self, confidence, evidence):
        """
        4.1: WHEN consumption_kwh is null THEN is_ready_for_pricing SHALL be false
        AND consumption_kwh SHALL appear in missing_fields.
        """
        extraction = InvoiceExtraction(
            consumption_kwh=FieldValue(value=None, confidence=confidence, evidence=evidence),
            current_active_unit_price_tl_per_kwh=FieldValue(value=2.5, confidence=0.9, evidence="test"),
            distribution_unit_price_tl_per_kwh=FieldValue(value=1.0, confidence=0.9, evidence="test"),
            demand_qty=FieldValue(value=None, confidence=0.0, evidence=""),
            demand_unit_price_tl_per_unit=FieldValue(value=None, confidence=0.0, evidence=""),
            invoice_total_with_vat_tl=FieldValue(value=1000.0, confidence=0.9, evidence="test"),
        )
        
        result = validate_extraction(extraction)
        
        assert result.is_ready_for_pricing is False
        assert "consumption_kwh" in result.missing_fields

    @settings(max_examples=100)
    @given(st.floats(min_value=-1000, max_value=0), st.floats(min_value=0.0, max_value=1.0))
    def test_zero_or_negative_consumption_marks_not_ready(self, consumption, confidence):
        """
        4.1: WHEN consumption_kwh is zero or negative THEN is_ready_for_pricing SHALL be false
        AND consumption_kwh SHALL appear in missing_fields.
        """
        extraction = InvoiceExtraction(
            consumption_kwh=FieldValue(value=consumption, confidence=confidence, evidence="test"),
            current_active_unit_price_tl_per_kwh=FieldValue(value=2.5, confidence=0.9, evidence="test"),
            distribution_unit_price_tl_per_kwh=FieldValue(value=1.0, confidence=0.9, evidence="test"),
            demand_qty=FieldValue(value=None, confidence=0.0, evidence=""),
            demand_unit_price_tl_per_unit=FieldValue(value=None, confidence=0.0, evidence=""),
            invoice_total_with_vat_tl=FieldValue(value=1000.0, confidence=0.9, evidence="test"),
        )
        
        result = validate_extraction(extraction)
        
        assert result.is_ready_for_pricing is False
        assert "consumption_kwh" in result.missing_fields

    @settings(max_examples=100)
    @given(st.floats(min_value=0.0, max_value=1.0), st.text(max_size=20))
    def test_null_unit_price_marks_not_ready(self, confidence, evidence):
        """
        4.2: WHEN current_active_unit_price is null THEN is_ready_for_pricing SHALL be false
        AND current_active_unit_price_tl_per_kwh SHALL appear in missing_fields.
        """
        extraction = InvoiceExtraction(
            consumption_kwh=FieldValue(value=1000.0, confidence=0.9, evidence="test"),
            current_active_unit_price_tl_per_kwh=FieldValue(value=None, confidence=confidence, evidence=evidence),
            distribution_unit_price_tl_per_kwh=FieldValue(value=1.0, confidence=0.9, evidence="test"),
            demand_qty=FieldValue(value=None, confidence=0.0, evidence=""),
            demand_unit_price_tl_per_unit=FieldValue(value=None, confidence=0.0, evidence=""),
            invoice_total_with_vat_tl=FieldValue(value=1000.0, confidence=0.9, evidence="test"),
        )
        
        result = validate_extraction(extraction)
        
        assert result.is_ready_for_pricing is False
        assert "current_active_unit_price_tl_per_kwh" in result.missing_fields

    @settings(max_examples=100)
    @given(st.floats(min_value=-100, max_value=MIN_UNIT_PRICE - 0.001, allow_nan=False, allow_infinity=False))
    def test_unit_price_below_min_adds_error(self, low_price):
        """
        4.2: WHEN current_active_unit_price is below 0.1 TL/kWh THEN is_ready_for_pricing SHALL be false
        AND an error SHALL be added for current_active_unit_price_tl_per_kwh.
        """
        extraction = InvoiceExtraction(
            consumption_kwh=FieldValue(value=1000.0, confidence=0.9, evidence="test"),
            current_active_unit_price_tl_per_kwh=FieldValue(value=low_price, confidence=0.9, evidence="test"),
            distribution_unit_price_tl_per_kwh=FieldValue(value=1.0, confidence=0.9, evidence="test"),
            demand_qty=FieldValue(value=None, confidence=0.0, evidence=""),
            demand_unit_price_tl_per_unit=FieldValue(value=None, confidence=0.0, evidence=""),
            invoice_total_with_vat_tl=FieldValue(value=1000.0, confidence=0.9, evidence="test"),
        )
        
        result = validate_extraction(extraction)
        
        assert result.is_ready_for_pricing is False
        error_fields = [e["field"] for e in result.errors]
        assert "current_active_unit_price_tl_per_kwh" in error_fields

    @settings(max_examples=100)
    @given(st.floats(min_value=MAX_UNIT_PRICE + 0.001, max_value=1000, allow_nan=False, allow_infinity=False))
    def test_unit_price_above_max_adds_error(self, high_price):
        """
        4.2: WHEN current_active_unit_price is above 30 TL/kWh THEN is_ready_for_pricing SHALL be false
        AND an error SHALL be added for current_active_unit_price_tl_per_kwh.
        """
        extraction = InvoiceExtraction(
            consumption_kwh=FieldValue(value=1000.0, confidence=0.9, evidence="test"),
            current_active_unit_price_tl_per_kwh=FieldValue(value=high_price, confidence=0.9, evidence="test"),
            distribution_unit_price_tl_per_kwh=FieldValue(value=1.0, confidence=0.9, evidence="test"),
            demand_qty=FieldValue(value=None, confidence=0.0, evidence=""),
            demand_unit_price_tl_per_unit=FieldValue(value=None, confidence=0.0, evidence=""),
            invoice_total_with_vat_tl=FieldValue(value=1000.0, confidence=0.9, evidence="test"),
        )
        
        result = validate_extraction(extraction)
        
        assert result.is_ready_for_pricing is False
        error_fields = [e["field"] for e in result.errors]
        assert "current_active_unit_price_tl_per_kwh" in error_fields

    @settings(max_examples=100)
    @given(st.floats(min_value=0.01, max_value=1000, allow_nan=False, allow_infinity=False))
    def test_demand_qty_without_price_adds_warning(self, demand_qty):
        """
        4.4: WHEN demand_qty > 0 AND demand_unit_price is null THEN warnings SHALL contain
        a warning for demand_unit_price_tl_per_unit (not blocking, just warning).
        
        Note: demand_unit_price is no longer a hard requirement - it's a warning.
        """
        # Calculate expected total to avoid sanity check errors
        consumption = 1000.0
        unit_price = 2.5
        dist_price = 1.0
        energy_est = consumption * unit_price
        dist_est = consumption * dist_price
        btv_est = energy_est * 0.01
        matrah = energy_est + dist_est + btv_est
        vat_est = matrah * 0.20
        expected_total = matrah + vat_est
        
        extraction = InvoiceExtraction(
            consumption_kwh=FieldValue(value=consumption, confidence=0.9, evidence="test"),
            current_active_unit_price_tl_per_kwh=FieldValue(value=unit_price, confidence=0.9, evidence="test"),
            distribution_unit_price_tl_per_kwh=FieldValue(value=dist_price, confidence=0.9, evidence="test"),
            demand_qty=FieldValue(value=demand_qty, confidence=0.9, evidence="test"),
            demand_unit_price_tl_per_unit=FieldValue(value=None, confidence=0.0, evidence=""),
            invoice_total_with_vat_tl=FieldValue(value=expected_total, confidence=0.9, evidence="test"),
        )
        
        result = validate_extraction(extraction)
        
        # Should have warning about demand_unit_price
        warning_fields = [w["field"] for w in result.warnings]
        assert "demand_unit_price_tl_per_unit" in warning_fields

    @settings(max_examples=100)
    @given(
        st.floats(min_value=100, max_value=500000, allow_nan=False, allow_infinity=False),
        st.floats(min_value=MIN_UNIT_PRICE, max_value=MAX_UNIT_PRICE, allow_nan=False, allow_infinity=False),
        st.floats(min_value=0.1, max_value=5, allow_nan=False, allow_infinity=False),
    )
    def test_valid_extraction_is_ready_for_pricing(self, consumption, unit_price, dist_price):
        """
        4.7: WHEN all critical fields are valid AND sanity check passes THEN is_ready_for_pricing SHALL be true.
        
        Note: invoice_total must be consistent with calculated values to pass sanity check.
        """
        # Calculate expected total to pass sanity check
        energy_est = consumption * unit_price
        dist_est = consumption * dist_price
        btv_est = energy_est * 0.01
        matrah = energy_est + dist_est + btv_est
        vat_est = matrah * 0.20
        expected_total = matrah + vat_est
        
        extraction = InvoiceExtraction(
            consumption_kwh=FieldValue(value=consumption, confidence=0.9, evidence="test"),
            current_active_unit_price_tl_per_kwh=FieldValue(value=unit_price, confidence=0.9, evidence="test"),
            distribution_unit_price_tl_per_kwh=FieldValue(value=dist_price, confidence=0.9, evidence="test"),
            demand_qty=FieldValue(value=None, confidence=0.0, evidence=""),
            demand_unit_price_tl_per_unit=FieldValue(value=None, confidence=0.0, evidence=""),
            invoice_total_with_vat_tl=FieldValue(value=expected_total, confidence=0.9, evidence="test"),
        )
        
        result = validate_extraction(extraction)
        
        assert result.is_ready_for_pricing is True
        assert len(result.missing_fields) == 0
        assert len(result.errors) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Property 3: Confidence Warning Generation
# **Validates: Requirements 4.3**
#
# For any InvoiceExtraction where any critical field (consumption_kwh,
# current_active_unit_price) has confidence < 0.6, the ValidationResult.warnings
# SHALL contain a warning about low confidence.
# ═══════════════════════════════════════════════════════════════════════════════

class TestProperty3ConfidenceWarningGeneration:
    """
    Feature: invoice-analysis-system, Property 3: Confidence Warning Generation
    **Validates: Requirements 4.3**
    """

    @settings(max_examples=100)
    @given(st.floats(min_value=0.0, max_value=LOW_CONFIDENCE_THRESHOLD - 0.001, allow_nan=False, allow_infinity=False))
    def test_low_confidence_consumption_generates_warning(self, low_confidence):
        """
        4.3: WHEN consumption_kwh has confidence < 0.6 THEN warnings SHALL contain
        a warning for consumption_kwh.
        """
        extraction = InvoiceExtraction(
            consumption_kwh=FieldValue(value=1000.0, confidence=low_confidence, evidence="test"),
            current_active_unit_price_tl_per_kwh=FieldValue(value=2.5, confidence=0.9, evidence="test"),
            distribution_unit_price_tl_per_kwh=FieldValue(value=1.0, confidence=0.9, evidence="test"),
            demand_qty=FieldValue(value=None, confidence=0.0, evidence=""),
            demand_unit_price_tl_per_unit=FieldValue(value=None, confidence=0.0, evidence=""),
            invoice_total_with_vat_tl=FieldValue(value=10000.0, confidence=0.9, evidence="test"),
        )
        
        result = validate_extraction(extraction)
        
        warning_fields = [w["field"] for w in result.warnings]
        assert "consumption_kwh" in warning_fields

    @settings(max_examples=100)
    @given(st.floats(min_value=0.0, max_value=LOW_CONFIDENCE_THRESHOLD - 0.001, allow_nan=False, allow_infinity=False))
    def test_low_confidence_unit_price_generates_warning(self, low_confidence):
        """
        4.3: WHEN current_active_unit_price has confidence < 0.6 THEN warnings SHALL contain
        a warning for current_active_unit_price_tl_per_kwh.
        """
        extraction = InvoiceExtraction(
            consumption_kwh=FieldValue(value=1000.0, confidence=0.9, evidence="test"),
            current_active_unit_price_tl_per_kwh=FieldValue(value=2.5, confidence=low_confidence, evidence="test"),
            distribution_unit_price_tl_per_kwh=FieldValue(value=1.0, confidence=0.9, evidence="test"),
            demand_qty=FieldValue(value=None, confidence=0.0, evidence=""),
            demand_unit_price_tl_per_unit=FieldValue(value=None, confidence=0.0, evidence=""),
            invoice_total_with_vat_tl=FieldValue(value=10000.0, confidence=0.9, evidence="test"),
        )
        
        result = validate_extraction(extraction)
        
        warning_fields = [w["field"] for w in result.warnings]
        assert "current_active_unit_price_tl_per_kwh" in warning_fields

    @settings(max_examples=100)
    @given(
        st.floats(min_value=0.0, max_value=LOW_CONFIDENCE_THRESHOLD - 0.001, allow_nan=False, allow_infinity=False),
        st.floats(min_value=0.0, max_value=LOW_CONFIDENCE_THRESHOLD - 0.001, allow_nan=False, allow_infinity=False),
    )
    def test_multiple_low_confidence_fields_generate_multiple_warnings(self, conf1, conf2):
        """
        4.3: WHEN multiple critical fields have confidence < 0.6 THEN warnings SHALL contain
        warnings for each low-confidence field.
        """
        extraction = InvoiceExtraction(
            consumption_kwh=FieldValue(value=1000.0, confidence=conf1, evidence="test"),
            current_active_unit_price_tl_per_kwh=FieldValue(value=2.5, confidence=conf2, evidence="test"),
            distribution_unit_price_tl_per_kwh=FieldValue(value=1.0, confidence=0.9, evidence="test"),
            demand_qty=FieldValue(value=None, confidence=0.0, evidence=""),
            demand_unit_price_tl_per_unit=FieldValue(value=None, confidence=0.0, evidence=""),
            invoice_total_with_vat_tl=FieldValue(value=10000.0, confidence=0.9, evidence="test"),
        )
        
        result = validate_extraction(extraction)
        
        warning_fields = [w["field"] for w in result.warnings]
        assert "consumption_kwh" in warning_fields
        assert "current_active_unit_price_tl_per_kwh" in warning_fields

    @settings(max_examples=100)
    @given(
        st.floats(min_value=LOW_CONFIDENCE_THRESHOLD, max_value=1.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=LOW_CONFIDENCE_THRESHOLD, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    def test_high_confidence_fields_no_confidence_warnings(self, conf1, conf2):
        """
        4.3: WHEN all critical fields have confidence >= 0.6 THEN warnings SHALL NOT contain
        any low-confidence warnings for those fields.
        """
        extraction = InvoiceExtraction(
            consumption_kwh=FieldValue(value=1000.0, confidence=conf1, evidence="test"),
            current_active_unit_price_tl_per_kwh=FieldValue(value=2.5, confidence=conf2, evidence="test"),
            distribution_unit_price_tl_per_kwh=FieldValue(value=1.0, confidence=0.9, evidence="test"),
            demand_qty=FieldValue(value=None, confidence=0.0, evidence=""),
            demand_unit_price_tl_per_unit=FieldValue(value=None, confidence=0.0, evidence=""),
            invoice_total_with_vat_tl=FieldValue(value=10000.0, confidence=0.9, evidence="test"),
        )
        
        result = validate_extraction(extraction)
        
        # Filter warnings to only those about low confidence
        low_conf_warnings = [
            w for w in result.warnings 
            if w.get("field") in ["consumption_kwh", "current_active_unit_price_tl_per_kwh"]
            and "güvenilirlik" in w.get("issue", "").lower()
        ]
        assert len(low_conf_warnings) == 0
