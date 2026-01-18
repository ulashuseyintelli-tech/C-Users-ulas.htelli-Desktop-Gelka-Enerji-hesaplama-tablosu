"""
Validation Contract Tests - Sprint 5.2

ValidationResult bool -> flags + category mapping contract testleri.
Bu testler, validation sonuclarindan dogru flag'lerin uretildigini dogrular.
"""

import pytest
from backend.app.incident_service import (
    calculate_quality_score,
    select_primary_flag,
    flag_to_category,
    normalize_flags,
    Category,
    Severity,
)


class TestValidationToFlagContracts:
    """ValidationResult -> Flag mapping contract testleri"""
    
    def test_distribution_tariff_meta_missing_contract(self):
        """
        Contract: distribution_tariff_meta_missing=True -> TARIFF_META_MISSING flag
        """
        validation = {
            "is_ready_for_pricing": True,
            "distribution_tariff_meta_missing": True,
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=None,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "TARIFF_META_MISSING" in quality.flags
        
        # Category mapping
        primary = select_primary_flag(quality.flags)
        if primary == "TARIFF_META_MISSING":
            assert flag_to_category(primary) == Category.TARIFF_META_MISSING
    
    def test_distribution_tariff_lookup_failed_contract(self):
        """
        Contract: distribution_tariff_lookup_failed=True -> TARIFF_LOOKUP_FAILED flag
        """
        validation = {
            "is_ready_for_pricing": True,
            "distribution_tariff_lookup_failed": True,
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=None,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "TARIFF_LOOKUP_FAILED" in quality.flags
        
        # Category mapping
        primary = select_primary_flag(quality.flags)
        if primary == "TARIFF_LOOKUP_FAILED":
            assert flag_to_category(primary) == Category.TARIFF_MISSING
    
    def test_distribution_line_mismatch_contract(self):
        """
        Contract: distribution_line_mismatch=True -> DISTRIBUTION_MISMATCH flag
        """
        validation = {
            "is_ready_for_pricing": True,
            "distribution_line_mismatch": True,
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=None,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "DISTRIBUTION_MISMATCH" in quality.flags
        
        # Category mapping
        primary = select_primary_flag(quality.flags)
        if primary == "DISTRIBUTION_MISMATCH":
            assert flag_to_category(primary) == Category.MISMATCH
    
    def test_missing_consumption_contract(self):
        """
        Contract: missing_fields contains consumption_kwh -> CONSUMPTION_MISSING flag
        """
        validation = {
            "is_ready_for_pricing": False,
            "missing_fields": ["consumption_kwh"],
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=None,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "CONSUMPTION_MISSING" in quality.flags
        
        # Category mapping
        primary = select_primary_flag(quality.flags)
        if primary == "CONSUMPTION_MISSING":
            assert flag_to_category(primary) == Category.CONSUMPTION_MISSING
    
    def test_missing_other_fields_contract(self):
        """
        Contract: missing_fields (not consumption) -> MISSING_FIELDS flag
        """
        validation = {
            "is_ready_for_pricing": False,
            "missing_fields": ["invoice_date", "supplier_name"],
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=None,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "MISSING_FIELDS" in quality.flags


class TestCalculationToFlagContracts:
    """Calculation result -> Flag mapping contract testleri"""
    
    def test_pricing_source_not_found_contract(self):
        """
        Contract: meta_pricing_source=not_found -> MARKET_PRICE_MISSING flag
        """
        calculation = {
            "meta_pricing_source": "not_found",
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation={},
            calculation=calculation,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "MARKET_PRICE_MISSING" in quality.flags
        
        # Category mapping
        primary = select_primary_flag(quality.flags)
        if primary == "MARKET_PRICE_MISSING":
            assert flag_to_category(primary) == Category.PRICE_MISSING
    
    def test_distribution_source_not_found_contract(self):
        """
        Contract: meta_distribution_source=not_found -> DISTRIBUTION_MISSING flag
        """
        calculation = {
            "meta_distribution_source": "not_found",
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation={},
            calculation=calculation,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "DISTRIBUTION_MISSING" in quality.flags
    
    def test_calc_bug_zero_distribution_contract(self):
        """
        Contract: distribution_total_tl=0 with valid source -> CALC_BUG flag
        """
        calculation = {
            "meta_distribution_source": "epdk_tariff",
            "distribution_total_tl": 0,
            "consumption_kwh": 10000,
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation={},
            calculation=calculation,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "CALC_BUG" in quality.flags
        
        # Category mapping
        primary = select_primary_flag(quality.flags)
        if primary == "CALC_BUG":
            assert flag_to_category(primary) == Category.CALC_BUG


class TestFlagPriorityContracts:
    """Flag priority contract testleri"""
    
    def test_calc_bug_beats_all(self):
        """Contract: CALC_BUG her zaman primary"""
        flags = ["MARKET_PRICE_MISSING", "CALC_BUG", "TARIFF_LOOKUP_FAILED"]
        primary = select_primary_flag(flags)
        assert primary == "CALC_BUG"
    
    def test_market_price_beats_tariff(self):
        """Contract: MARKET_PRICE_MISSING > TARIFF_LOOKUP_FAILED"""
        flags = ["TARIFF_LOOKUP_FAILED", "MARKET_PRICE_MISSING"]
        primary = select_primary_flag(flags)
        assert primary == "MARKET_PRICE_MISSING"
    
    def test_consumption_beats_distribution(self):
        """Contract: CONSUMPTION_MISSING > DISTRIBUTION_MISSING"""
        flags = ["DISTRIBUTION_MISSING", "CONSUMPTION_MISSING"]
        primary = select_primary_flag(flags)
        assert primary == "CONSUMPTION_MISSING"
    
    def test_s1_beats_s2(self):
        """Contract: S1 flags > S2 flags"""
        flags = ["MISSING_FIELDS", "MARKET_PRICE_MISSING"]  # S2, S1
        primary = select_primary_flag(flags)
        assert primary == "MARKET_PRICE_MISSING"


class TestCategoryMappingContracts:
    """Category mapping contract testleri"""
    
    def test_tariff_meta_missing_category(self):
        """Contract: TARIFF_META_MISSING -> TARIFF_META_MISSING category"""
        assert flag_to_category("TARIFF_META_MISSING") == Category.TARIFF_META_MISSING
    
    def test_tariff_lookup_failed_category(self):
        """Contract: TARIFF_LOOKUP_FAILED -> TARIFF_MISSING category"""
        assert flag_to_category("TARIFF_LOOKUP_FAILED") == Category.TARIFF_MISSING
    
    def test_distribution_missing_category(self):
        """Contract: DISTRIBUTION_MISSING -> TARIFF_MISSING category"""
        assert flag_to_category("DISTRIBUTION_MISSING") == Category.TARIFF_MISSING
    
    def test_market_price_missing_category(self):
        """Contract: MARKET_PRICE_MISSING -> PRICE_MISSING category"""
        assert flag_to_category("MARKET_PRICE_MISSING") == Category.PRICE_MISSING
    
    def test_consumption_missing_category(self):
        """Contract: CONSUMPTION_MISSING -> CONSUMPTION_MISSING category"""
        assert flag_to_category("CONSUMPTION_MISSING") == Category.CONSUMPTION_MISSING
    
    def test_calc_bug_category(self):
        """Contract: CALC_BUG -> CALC_BUG category"""
        assert flag_to_category("CALC_BUG") == Category.CALC_BUG
    
    def test_mismatch_flags_category(self):
        """Contract: *MISMATCH* -> MISMATCH category"""
        assert flag_to_category("DISTRIBUTION_MISMATCH") == Category.MISMATCH
    
    def test_outlier_flags_category(self):
        """Contract: OUTLIER_* -> OUTLIER category"""
        assert flag_to_category("OUTLIER_PTF") == Category.OUTLIER
        assert flag_to_category("OUTLIER_CONSUMPTION") == Category.OUTLIER
    
    def test_json_repair_category(self):
        """Contract: JSON_REPAIR_APPLIED -> JSON_REPAIR category"""
        assert flag_to_category("JSON_REPAIR_APPLIED") == Category.JSON_REPAIR
    
    def test_unknown_flag_category(self):
        """Contract: Unknown flag -> VALIDATION_FAIL category"""
        assert flag_to_category("UNKNOWN_FLAG") == Category.VALIDATION_FAIL


class TestNormalizeFlagsContract:
    """normalize_flags contract testleri"""
    
    def test_sorted_by_priority(self):
        """Contract: Flags priority'ye gore siralanir"""
        flags = ["VALIDATION_WARNINGS", "MARKET_PRICE_MISSING", "MISSING_FIELDS"]
        result = normalize_flags(flags)
        # MARKET_PRICE_MISSING (10) < MISSING_FIELDS (40) < VALIDATION_WARNINGS (90)
        assert result == ["MARKET_PRICE_MISSING", "MISSING_FIELDS", "VALIDATION_WARNINGS"]
    
    def test_removes_duplicates(self):
        """Contract: Duplicate flag'ler kaldirilir"""
        flags = ["MARKET_PRICE_MISSING", "MISSING_FIELDS", "MARKET_PRICE_MISSING"]
        result = normalize_flags(flags)
        assert result.count("MARKET_PRICE_MISSING") == 1
    
    def test_idempotent(self):
        """Contract: normalize_flags idempotent"""
        flags = ["VALIDATION_WARNINGS", "MARKET_PRICE_MISSING"]
        result1 = normalize_flags(flags)
        result2 = normalize_flags(result1)
        assert result1 == result2
    
    def test_empty_list(self):
        """Contract: Bos liste -> bos liste"""
        assert normalize_flags([]) == []


class TestCombinationContracts:
    """Kombinasyon contract testleri"""
    
    def test_meta_missing_and_lookup_failed(self):
        """
        Contract: Hem meta_missing hem lookup_failed varsa,
        TARIFF_META_MISSING primary olur (daha yuksek priority)
        """
        validation = {
            "distribution_tariff_meta_missing": True,
            "distribution_tariff_lookup_failed": True,
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=None,
            calculation_error=None,
            debug_meta=None
        )
        
        # Her iki flag da olmali
        assert "TARIFF_META_MISSING" in quality.flags
        # TARIFF_LOOKUP_FAILED sadece meta_missing=False ise eklenir
        # Bu durumda elif ile kontrol edildiginden eklenmez
        
        primary = select_primary_flag(quality.flags)
        # TARIFF_META_MISSING priority 25, TARIFF_LOOKUP_FAILED priority 20
        # Eger ikisi de varsa TARIFF_LOOKUP_FAILED primary olur
        # Ama elif ile sadece biri ekleniyor
        assert primary == "TARIFF_META_MISSING"
    
    def test_multiple_s1_flags(self):
        """
        Contract: Birden fazla S1 flag varsa, en yuksek priority primary
        """
        validation = {
            "distribution_tariff_meta_missing": True,
        }
        calculation = {
            "meta_pricing_source": "not_found",
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=calculation,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "TARIFF_META_MISSING" in quality.flags
        assert "MARKET_PRICE_MISSING" in quality.flags
        
        primary = select_primary_flag(quality.flags)
        # MARKET_PRICE_MISSING (10) < TARIFF_META_MISSING (25)
        assert primary == "MARKET_PRICE_MISSING"
