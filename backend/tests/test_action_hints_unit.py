"""
Unit Tests for ActionHint - Sprint 8.5

Edge cases, unsupported flags, missing fields testleri.
"""

import pytest
from app.incident_service import (
    ActionClass,
    PrimarySuspect,
    ActionHint,
    generate_action_hint,
    ROUNDING_DELTA_THRESHOLD,
    ROUNDING_RATIO_THRESHOLD,
)


class TestGenerateActionHintEdgeCases:
    """generate_action_hint edge case testleri."""
    
    def test_unsupported_flag_returns_none(self):
        """Desteklenmeyen flag için None döner."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 100.0,
            "ratio": 0.1,
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint = generate_action_hint("UNKNOWN_FLAG", mismatch_info, 0.9)
        assert hint is None
        
        hint = generate_action_hint("MARKET_PRICE_MISSING", mismatch_info, 0.9)
        assert hint is None
        
        hint = generate_action_hint("TARIFF_LOOKUP_FAILED", mismatch_info, 0.9)
        assert hint is None
    
    def test_none_mismatch_info_returns_none(self):
        """mismatch_info None ise None döner."""
        hint = generate_action_hint("INVOICE_TOTAL_MISMATCH", None, 0.9)
        assert hint is None
    
    def test_empty_mismatch_info_returns_none(self):
        """mismatch_info boş dict ise None döner."""
        hint = generate_action_hint("INVOICE_TOTAL_MISMATCH", {}, 0.9)
        assert hint is None
    
    def test_missing_required_fields_returns_none(self):
        """Zorunlu alanlar eksikse None döner."""
        # has_mismatch eksik
        hint = generate_action_hint(
            "INVOICE_TOTAL_MISMATCH",
            {"delta": 100, "ratio": 0.1, "severity": "S2"},
            0.9,
        )
        assert hint is None
        
        # delta eksik
        hint = generate_action_hint(
            "INVOICE_TOTAL_MISMATCH",
            {"has_mismatch": True, "ratio": 0.1, "severity": "S2"},
            0.9,
        )
        assert hint is None
        
        # ratio eksik
        hint = generate_action_hint(
            "INVOICE_TOTAL_MISMATCH",
            {"has_mismatch": True, "delta": 100, "severity": "S2"},
            0.9,
        )
        assert hint is None
        
        # severity eksik
        hint = generate_action_hint(
            "INVOICE_TOTAL_MISMATCH",
            {"has_mismatch": True, "delta": 100, "ratio": 0.1},
            0.9,
        )
        assert hint is None
    
    def test_has_mismatch_false_returns_none(self):
        """has_mismatch=False ise None döner."""
        mismatch_info = {
            "has_mismatch": False,
            "delta": 100.0,
            "ratio": 0.1,
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.9)
        assert hint is None


class TestActionHintToDict:
    """ActionHint.to_dict() testleri."""
    
    def test_to_dict_all_fields(self):
        """to_dict tüm alanları içermeli."""
        hint = ActionHint(
            action_class=ActionClass.VERIFY_OCR,
            primary_suspect=PrimarySuspect.OCR_LOCALE_SUSPECT,
            recommended_checks=["Check 1", "Check 2"],
            confidence_note="Test note",
        )
        
        d = hint.to_dict()
        
        assert d["action_class"] == "VERIFY_OCR"
        assert d["primary_suspect"] == "OCR_LOCALE_SUSPECT"
        assert d["recommended_checks"] == ["Check 1", "Check 2"]
        assert d["confidence_note"] == "Test note"
    
    def test_to_dict_none_confidence_note(self):
        """confidence_note None olabilir."""
        hint = ActionHint(
            action_class=ActionClass.VERIFY_INVOICE_LOGIC,
            primary_suspect=PrimarySuspect.INVOICE_LOGIC,
            recommended_checks=["Check 1"],
            confidence_note=None,
        )
        
        d = hint.to_dict()
        
        assert d["confidence_note"] is None


class TestDecisionTreeBoundaries:
    """Decision tree sınır değerleri testleri."""
    
    def test_rounding_boundary_delta_exactly_10(self):
        """delta tam 10 TL → rounding DEĞİL."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 10.0,  # Tam sınırda
            "ratio": 0.003,
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.9)
        
        # delta >= 10 → VERIFY_INVOICE_LOGIC
        assert hint.action_class == ActionClass.VERIFY_INVOICE_LOGIC
    
    def test_rounding_boundary_delta_just_below_10(self):
        """delta 9.99 TL → rounding olabilir (ratio'ya bağlı)."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 9.99,
            "ratio": 0.003,  # < 0.005
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.9)
        
        # delta < 10 AND ratio < 0.005 → ACCEPT_ROUNDING_TOLERANCE
        assert hint.action_class == ActionClass.ACCEPT_ROUNDING_TOLERANCE
    
    def test_rounding_boundary_ratio_exactly_0005(self):
        """ratio tam 0.005 → rounding DEĞİL."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 5.0,
            "ratio": 0.005,  # Tam sınırda
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.9)
        
        # ratio >= 0.005 → VERIFY_INVOICE_LOGIC
        assert hint.action_class == ActionClass.VERIFY_INVOICE_LOGIC
    
    def test_rounding_boundary_ratio_just_below_0005(self):
        """ratio 0.0049 → rounding olabilir (delta'ya bağlı)."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 5.0,  # < 10
            "ratio": 0.0049,  # < 0.005
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.9)
        
        # delta < 10 AND ratio < 0.005 → ACCEPT_ROUNDING_TOLERANCE
        assert hint.action_class == ActionClass.ACCEPT_ROUNDING_TOLERANCE
    
    def test_ocr_suspect_overrides_rounding(self):
        """OCR suspect varsa, küçük delta bile VERIFY_OCR."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 5.0,  # Normalde rounding olurdu
            "ratio": 0.003,
            "severity": "S2",
            "suspect_reason": "OCR_LOCALE_SUSPECT",
        }
        
        hint = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.5)
        
        # OCR suspect → VERIFY_OCR (rounding'i override eder)
        assert hint.action_class == ActionClass.VERIFY_OCR


class TestThresholdConstants:
    """Threshold sabitleri testleri."""
    
    def test_rounding_delta_threshold(self):
        """ROUNDING_DELTA_THRESHOLD = 10 TL."""
        assert ROUNDING_DELTA_THRESHOLD == 10.0
    
    def test_rounding_ratio_threshold(self):
        """ROUNDING_RATIO_THRESHOLD = 0.005 (%0.5)."""
        assert ROUNDING_RATIO_THRESHOLD == 0.005


class TestActionClassEnum:
    """ActionClass enum testleri."""
    
    def test_action_class_values(self):
        """ActionClass değerleri doğru."""
        assert ActionClass.VERIFY_OCR.value == "VERIFY_OCR"
        assert ActionClass.VERIFY_INVOICE_LOGIC.value == "VERIFY_INVOICE_LOGIC"
        assert ActionClass.ACCEPT_ROUNDING_TOLERANCE.value == "ACCEPT_ROUNDING_TOLERANCE"
    
    def test_action_class_is_string_enum(self):
        """ActionClass string enum."""
        assert isinstance(ActionClass.VERIFY_OCR, str)
        assert ActionClass.VERIFY_OCR == "VERIFY_OCR"


class TestPrimarySuspectEnum:
    """PrimarySuspect enum testleri."""
    
    def test_primary_suspect_values(self):
        """PrimarySuspect değerleri doğru."""
        assert PrimarySuspect.OCR_LOCALE_SUSPECT.value == "OCR_LOCALE_SUSPECT"
        assert PrimarySuspect.INVOICE_LOGIC.value == "INVOICE_LOGIC"
        assert PrimarySuspect.ROUNDING.value == "ROUNDING"
    
    def test_primary_suspect_is_string_enum(self):
        """PrimarySuspect string enum."""
        assert isinstance(PrimarySuspect.OCR_LOCALE_SUSPECT, str)
        assert PrimarySuspect.OCR_LOCALE_SUSPECT == "OCR_LOCALE_SUSPECT"

