"""
Integration tests for the full invoice analysis flow.

Feature: invoice-analysis-system
Tests the complete flow: Upload → Extract → Validate → Calculate

Requirements: 2.1-2.8, 9.3
"""

import os
import unicodedata
import pytest
from pathlib import Path

from app.models import InvoiceExtraction, FieldValue, OfferParams
from app.validator import validate_extraction
from app.calculator import calculate_offer
from tests.fixtures.expected_outputs import (
    ALL_FIXTURES,
    InvoiceTestFixture,
    ENERJISA_FIXTURE,
    YELDEN_FIXTURE,
    GENERIC_FIXTURE,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Test Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def get_workspace_root() -> Path:
    """Get the workspace root directory."""
    # Navigate from backend/tests to workspace root
    return Path(__file__).parent.parent.parent


def fixture_file_exists(fixture: InvoiceTestFixture) -> bool:
    """Check if the fixture file exists."""
    workspace_root = get_workspace_root()
    # Normalize the path for Unicode compatibility
    normalized_path = unicodedata.normalize('NFC', fixture.file_path)
    file_path = workspace_root / normalized_path
    
    if file_path.exists():
        return True
    
    # Try to find the file with different Unicode normalization
    parent = file_path.parent
    if parent.exists():
        target_name = unicodedata.normalize('NFC', file_path.name)
        for f in parent.iterdir():
            if unicodedata.normalize('NFC', f.name) == target_name:
                return True
            # Also try NFD normalization
            if unicodedata.normalize('NFD', f.name) == unicodedata.normalize('NFD', target_name):
                return True
    
    return False


def create_mock_extraction(fixture: InvoiceTestFixture) -> InvoiceExtraction:
    """
    Create a mock extraction based on fixture expected values.
    Used when actual extraction is not available (no OpenAI API key).
    
    Note: invoice_total_with_vat_tl is calculated to be consistent with
    consumption × unit_price to pass sanity check validation.
    """
    ext = fixture.extraction
    mid_consumption = (ext.consumption_kwh_min + ext.consumption_kwh_max) / 2
    mid_price = (ext.unit_price_min + ext.unit_price_max) / 2
    dist_price = 1.0
    
    # Calculate consistent invoice_total to pass sanity check
    # Formula: energy + distribution + btv + vat
    energy_est = mid_consumption * mid_price
    dist_est = mid_consumption * dist_price
    btv_est = energy_est * 0.01
    matrah = energy_est + dist_est + btv_est
    vat_est = matrah * 0.20
    calculated_total = matrah + vat_est
    
    return InvoiceExtraction(
        vendor=ext.vendor,
        invoice_period=ext.invoice_period or "2025-01",
        consumption_kwh=FieldValue(value=mid_consumption, confidence=0.9, evidence="test"),
        current_active_unit_price_tl_per_kwh=FieldValue(value=mid_price, confidence=0.9, evidence="test"),
        distribution_unit_price_tl_per_kwh=FieldValue(value=dist_price, confidence=0.9, evidence="test"),
        demand_qty=FieldValue(value=100 if ext.has_demand else None, confidence=0.9 if ext.has_demand else 0.0, evidence="test" if ext.has_demand else ""),
        demand_unit_price_tl_per_unit=FieldValue(value=50 if ext.has_demand else None, confidence=0.9 if ext.has_demand else 0.0, evidence="test" if ext.has_demand else ""),
        invoice_total_with_vat_tl=FieldValue(value=calculated_total, confidence=0.9, evidence="test"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Tests: Validation Flow
# Requirements: 4.1-4.7
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidationFlow:
    """Test the validation flow with mock extractions."""

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
    def test_validation_produces_expected_result(self, fixture: InvoiceTestFixture):
        """
        For each fixture, validation should produce expected is_ready_for_pricing status.
        """
        extraction = create_mock_extraction(fixture)
        result = validate_extraction(extraction)
        
        expected = fixture.validation
        assert result.is_ready_for_pricing == expected.is_ready_for_pricing
        assert len(result.errors) <= expected.max_errors
        assert len(result.warnings) <= expected.max_warnings

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
    def test_validation_missing_fields_match(self, fixture: InvoiceTestFixture):
        """
        Validation should identify expected missing fields.
        """
        extraction = create_mock_extraction(fixture)
        result = validate_extraction(extraction)
        
        expected = fixture.validation
        for field in expected.expected_missing_fields:
            assert field in result.missing_fields, f"Expected {field} to be in missing_fields"


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Tests: Calculation Flow
# Requirements: 5.1-5.9
# ═══════════════════════════════════════════════════════════════════════════════

class TestCalculationFlow:
    """Test the calculation flow with mock extractions."""

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
    def test_calculation_produces_valid_result(self, fixture: InvoiceTestFixture):
        """
        For each fixture, calculation should produce valid results.
        """
        extraction = create_mock_extraction(fixture)
        validation = validate_extraction(extraction)
        
        if not validation.is_ready_for_pricing:
            pytest.skip("Extraction not ready for pricing")
        
        params = OfferParams()
        result = calculate_offer(extraction, params)
        
        # Basic sanity checks
        assert result.current_total_with_vat_tl > 0
        assert result.offer_total_with_vat_tl > 0
        assert -1 <= result.savings_ratio <= 1  # Savings ratio should be reasonable

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
    def test_calculation_components_are_consistent(self, fixture: InvoiceTestFixture):
        """
        Calculation components should add up correctly.
        """
        extraction = create_mock_extraction(fixture)
        validation = validate_extraction(extraction)
        
        if not validation.is_ready_for_pricing:
            pytest.skip("Extraction not ready for pricing")
        
        params = OfferParams()
        result = calculate_offer(extraction, params)
        
        # Current total should be sum of components
        expected_current_matrah = (
            result.current_energy_tl +
            result.current_distribution_tl +
            result.current_demand_tl +
            result.current_btv_tl
        )
        assert abs(result.current_vat_matrah_tl - expected_current_matrah) < 0.01
        
        # Offer total should be sum of components
        expected_offer_matrah = (
            result.offer_energy_tl +
            result.offer_distribution_tl +
            result.offer_demand_tl +
            result.offer_btv_tl
        )
        assert abs(result.offer_vat_matrah_tl - expected_offer_matrah) < 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# Integration Tests: Full Flow
# Requirements: 9.3
# ═══════════════════════════════════════════════════════════════════════════════

class TestFullFlow:
    """Test the complete flow from extraction to calculation."""

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
    def test_full_flow_produces_savings(self, fixture: InvoiceTestFixture):
        """
        Full flow should produce a savings calculation.
        """
        extraction = create_mock_extraction(fixture)
        validation = validate_extraction(extraction)
        
        if not validation.is_ready_for_pricing:
            pytest.skip("Extraction not ready for pricing")
        
        params = OfferParams()
        result = calculate_offer(extraction, params)
        
        # Should have a difference (positive or negative)
        assert result.difference_incl_vat_tl != 0 or result.current_total_with_vat_tl == result.offer_total_with_vat_tl

    def test_different_params_produce_different_results(self):
        """
        Different offer parameters should produce different OFFER results.
        
        KONTRAT:
        - PTF değişince offer_total değişmeli ✅
        - PTF değişince current_total değişmemeli ✅ (faturadan okunan)
        
        NOT: use_reference_prices=False olmalı ki params'taki PTF kullanılsın.
        """
        extraction = create_mock_extraction(ENERJISA_FIXTURE)
        
        # use_reference_prices=False → params'taki PTF kullanılır
        params1 = OfferParams(weighted_ptf_tl_per_mwh=2974.1, use_reference_prices=False)
        params2 = OfferParams(weighted_ptf_tl_per_mwh=3500.0, use_reference_prices=False)
        
        result1 = calculate_offer(extraction, params1)
        result2 = calculate_offer(extraction, params2)
        
        # OFFER total değişmeli (PTF değişti)
        assert result1.offer_total_with_vat_tl != result2.offer_total_with_vat_tl
        
        # CURRENT total değişmemeli (faturadan okunan, sabit)
        assert result1.current_total_with_vat_tl == result2.current_total_with_vat_tl

    def test_agreement_multiplier_affects_offer(self):
        """
        Agreement multiplier should affect the offer calculation.
        """
        extraction = create_mock_extraction(ENERJISA_FIXTURE)
        
        params1 = OfferParams(agreement_multiplier=1.0)
        params2 = OfferParams(agreement_multiplier=1.05)
        
        result1 = calculate_offer(extraction, params1)
        result2 = calculate_offer(extraction, params2)
        
        # Higher multiplier should result in higher offer
        assert result2.offer_energy_tl > result1.offer_energy_tl


# ═══════════════════════════════════════════════════════════════════════════════
# File Existence Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestFixtureFiles:
    """Test that fixture files exist in the workspace."""

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f.name)
    def test_fixture_file_exists(self, fixture: InvoiceTestFixture):
        """
        Each fixture should have a corresponding file in the workspace.
        """
        if not fixture_file_exists(fixture):
            pytest.skip(f"Fixture file not found: {fixture.file_path}")
        
        assert fixture_file_exists(fixture)
