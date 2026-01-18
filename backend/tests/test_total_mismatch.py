"""
Tests for INVOICE_TOTAL_MISMATCH flag - Sprint 8.3 + 8.4

KONTRAT:
- invoice_total vs computed_total farkı > %5 veya > 50 TL ise S2 flag
- Severe mismatch: (ratio >= 20% AND delta >= 50) OR delta >= 500 → S1
- OCR_LOCALE_SUSPECT: confidence < 0.7 + mismatch
"""

import pytest
from app.calculator import (
    check_total_mismatch,
    TOTAL_MISMATCH_RATIO_THRESHOLD,
    TOTAL_MISMATCH_ABSOLUTE_THRESHOLD,
    TOTAL_MISMATCH_SEVERE_RATIO,
    TOTAL_MISMATCH_SEVERE_ABSOLUTE,
)
from app.incident_service import (
    QUALITY_FLAGS,
    FLAG_PRIORITY,
    ACTION_MAP,
    HintCode,
    calculate_quality_score,
)


class TestTotalMismatchThresholds:
    """Threshold sabitleri testi."""
    
    def test_ratio_threshold_is_5_percent(self):
        """Ratio threshold %5 olmalı."""
        assert TOTAL_MISMATCH_RATIO_THRESHOLD == 0.05
    
    def test_absolute_threshold_is_50_tl(self):
        """Absolute threshold 50 TL olmalı."""
        assert TOTAL_MISMATCH_ABSOLUTE_THRESHOLD == 50.0
    
    def test_severe_ratio_threshold_is_20_percent(self):
        """Severe ratio threshold %20 olmalı."""
        assert TOTAL_MISMATCH_SEVERE_RATIO == 0.20
    
    def test_severe_absolute_threshold_is_500_tl(self):
        """Severe absolute threshold 500 TL olmalı."""
        assert TOTAL_MISMATCH_SEVERE_ABSOLUTE == 500.0


class TestCheckTotalMismatch:
    """check_total_mismatch fonksiyonu testleri."""
    
    def test_no_mismatch_when_equal(self):
        """Eşit değerlerde mismatch yok."""
        result = check_total_mismatch(1000.0, 1000.0)
        assert result.has_mismatch is False
        assert result.delta == 0
        assert result.ratio == 0
        assert result.severity == "S2"
    
    def test_no_mismatch_within_threshold(self):
        """Threshold içinde mismatch yok."""
        # %4 fark, 40 TL fark → threshold altında
        result = check_total_mismatch(1000.0, 960.0)
        assert result.has_mismatch is False
        assert result.delta == 40.0
        assert result.ratio == 0.04
    
    def test_mismatch_when_ratio_exceeded(self):
        """Ratio threshold aşıldığında mismatch var."""
        # %6 fark → threshold üstünde
        result = check_total_mismatch(1000.0, 940.0)
        assert result.has_mismatch is True
        assert result.delta == 60.0
        assert result.ratio == 0.06
        assert result.severity == "S2"  # Normal mismatch
    
    def test_mismatch_when_absolute_exceeded(self):
        """Absolute threshold aşıldığında mismatch var."""
        # 60 TL fark, %1 → ratio altında ama absolute üstünde
        result = check_total_mismatch(6000.0, 5940.0)
        assert result.has_mismatch is True
        assert result.delta == 60.0
        assert result.ratio == 0.01
        assert result.severity == "S2"
    
    def test_mismatch_info_to_dict(self):
        """to_dict() doğru çalışmalı."""
        result = check_total_mismatch(1000.0, 900.0)
        d = result.to_dict()
        
        assert d["has_mismatch"] is True
        assert d["invoice_total"] == 1000.0
        assert d["computed_total"] == 900.0
        assert d["delta"] == 100.0
        assert d["ratio"] == 0.1
        assert d["severity"] == "S2"


class TestSeverityEscalation:
    """S1 escalation testleri - Sprint 8.4."""
    
    def test_s1_when_severe_ratio_and_delta(self):
        """ratio >= 20% AND delta >= 50 → S1."""
        # 1000 TL fatura, 750 TL hesaplanan → %25 fark, 250 TL delta
        result = check_total_mismatch(1000.0, 750.0)
        assert result.has_mismatch is True
        assert result.severity == "S1"
        assert result.ratio == 0.25
        assert result.delta == 250.0
    
    def test_s2_when_high_ratio_but_low_delta(self):
        """ratio >= 20% ama delta < 50 → S2 (küçük fatura koruması)."""
        # 200 TL fatura, 150 TL hesaplanan → %25 fark, 50 TL delta (sınırda)
        result = check_total_mismatch(200.0, 150.0)
        assert result.has_mismatch is True
        # delta=50 tam sınırda, ratio=25% → S1 olmalı
        assert result.severity == "S1"
        
        # 100 TL fatura, 75 TL hesaplanan → %25 fark, 25 TL delta
        # ratio=25% > 5% → mismatch VAR (S2 threshold)
        # Ama delta=25 < 50 → S1 değil, S2 kalır
        result2 = check_total_mismatch(100.0, 75.0)
        assert result2.has_mismatch is True  # ratio > 5% → mismatch var
        assert result2.severity == "S2"  # delta < 50 → S1'e yükselmez
    
    def test_s1_when_delta_exceeds_500(self):
        """delta >= 500 → S1 (ratio ne olursa olsun)."""
        # 10000 TL fatura, 9400 TL hesaplanan → %6 fark, 600 TL delta
        result = check_total_mismatch(10000.0, 9400.0)
        assert result.has_mismatch is True
        assert result.severity == "S1"
        assert result.delta == 600.0
    
    def test_s2_when_delta_below_500_and_ratio_below_20(self):
        """delta < 500 AND ratio < 20% → S2."""
        # 5000 TL fatura, 4700 TL hesaplanan → %6 fark, 300 TL delta
        result = check_total_mismatch(5000.0, 4700.0)
        assert result.has_mismatch is True
        assert result.severity == "S2"
        assert result.delta == 300.0
        assert result.ratio == 0.06


class TestOcrLocaleSuspect:
    """OCR_LOCALE_SUSPECT tag testleri - Sprint 8.4."""
    
    def test_suspect_when_low_confidence_and_mismatch(self):
        """confidence < 0.7 + mismatch → OCR_LOCALE_SUSPECT."""
        result = check_total_mismatch(1000.0, 900.0, extraction_confidence=0.5)
        assert result.has_mismatch is True
        assert result.suspect_reason == "OCR_LOCALE_SUSPECT"
    
    def test_no_suspect_when_high_confidence(self):
        """confidence >= 0.7 → suspect yok."""
        result = check_total_mismatch(1000.0, 900.0, extraction_confidence=0.8)
        assert result.has_mismatch is True
        assert result.suspect_reason is None
    
    def test_no_suspect_when_no_mismatch(self):
        """mismatch yok → suspect yok."""
        result = check_total_mismatch(1000.0, 1000.0, extraction_confidence=0.5)
        assert result.has_mismatch is False
        assert result.suspect_reason is None
    
    def test_suspect_in_to_dict(self):
        """suspect_reason to_dict'te görünmeli."""
        result = check_total_mismatch(1000.0, 900.0, extraction_confidence=0.5)
        d = result.to_dict()
        assert d["suspect_reason"] == "OCR_LOCALE_SUSPECT"
    
    def test_no_suspect_key_when_none(self):
        """suspect_reason None ise to_dict'te key olmamalı."""
        result = check_total_mismatch(1000.0, 900.0, extraction_confidence=0.9)
        d = result.to_dict()
        assert "suspect_reason" not in d


class TestTotalMismatchFlag:
    """INVOICE_TOTAL_MISMATCH flag tanımı testleri."""
    
    def test_flag_defined(self):
        """Flag tanımlı olmalı."""
        assert "INVOICE_TOTAL_MISMATCH" in QUALITY_FLAGS
    
    def test_flag_severity_s2(self):
        """Flag severity S2 olmalı."""
        flag = QUALITY_FLAGS["INVOICE_TOTAL_MISMATCH"]
        assert flag.severity == "S2"
    
    def test_flag_deduction_25(self):
        """Flag deduction 25 olmalı."""
        flag = QUALITY_FLAGS["INVOICE_TOTAL_MISMATCH"]
        assert flag.deduction == 25
    
    def test_flag_priority_defined(self):
        """Flag priority tanımlı olmalı."""
        assert "INVOICE_TOTAL_MISMATCH" in FLAG_PRIORITY
    
    def test_flag_action_defined(self):
        """Flag action tanımlı olmalı."""
        assert "INVOICE_TOTAL_MISMATCH" in ACTION_MAP
    
    def test_flag_hint_code(self):
        """Flag hint code doğru olmalı."""
        action = ACTION_MAP["INVOICE_TOTAL_MISMATCH"]
        assert action.code == HintCode.INVOICE_TOTAL_MISMATCH_REVIEW


class TestQualityScoreWithMismatch:
    """calculate_quality_score ile INVOICE_TOTAL_MISMATCH testi."""
    
    def test_mismatch_flag_added_when_present(self):
        """meta_total_mismatch=True ise flag eklenmeli."""
        calculation = {
            "meta_total_mismatch": True,
            "meta_total_mismatch_info": {
                "invoice_total": 1000.0,
                "computed_total": 900.0,
                "delta": 100.0,
                "ratio": 0.1,
                "severity": "S2",
            }
        }
        
        result = calculate_quality_score(
            extraction={},
            validation={},
            calculation=calculation,
            calculation_error=None,
            debug_meta=None,
        )
        
        assert "INVOICE_TOTAL_MISMATCH" in result.flags
    
    def test_mismatch_flag_not_added_when_absent(self):
        """meta_total_mismatch=False ise flag eklenmemeli."""
        calculation = {
            "meta_total_mismatch": False,
            "meta_total_mismatch_info": None,
        }
        
        result = calculate_quality_score(
            extraction={},
            validation={},
            calculation=calculation,
            calculation_error=None,
            debug_meta=None,
        )
        
        assert "INVOICE_TOTAL_MISMATCH" not in result.flags
    
    def test_mismatch_reduces_score(self):
        """Mismatch flag score'u düşürmeli."""
        calculation_with_mismatch = {
            "meta_total_mismatch": True,
            "meta_total_mismatch_info": {"delta": 100, "ratio": 0.1, "severity": "S2"},
        }
        calculation_without_mismatch = {
            "meta_total_mismatch": False,
        }
        
        result_with = calculate_quality_score({}, {}, calculation_with_mismatch, None, None)
        result_without = calculate_quality_score({}, {}, calculation_without_mismatch, None, None)
        
        assert result_with.score < result_without.score
        assert result_without.score - result_with.score == 25  # deduction
    
    def test_severity_override_from_mismatch_info(self):
        """Severity mismatch_info'dan alınmalı."""
        calculation = {
            "meta_total_mismatch": True,
            "meta_total_mismatch_info": {
                "delta": 600,
                "ratio": 0.25,
                "severity": "S1",
            }
        }
        
        result = calculate_quality_score({}, {}, calculation, None, None)
        
        # Flag details'de severity S1 olmalı
        mismatch_detail = next(fd for fd in result.flag_details if fd["code"] == "INVOICE_TOTAL_MISMATCH")
        assert mismatch_detail["severity"] == "S1"
    
    def test_suspect_reason_in_flag_details(self):
        """suspect_reason flag_details'e eklenmeli."""
        calculation = {
            "meta_total_mismatch": True,
            "meta_total_mismatch_info": {
                "delta": 100,
                "ratio": 0.1,
                "severity": "S2",
                "suspect_reason": "OCR_LOCALE_SUSPECT",
            }
        }
        
        result = calculate_quality_score({}, {}, calculation, None, None)
        
        mismatch_detail = next(fd for fd in result.flag_details if fd["code"] == "INVOICE_TOTAL_MISMATCH")
        assert mismatch_detail.get("suspect_reason") == "OCR_LOCALE_SUSPECT"


class TestGoldenScenarios:
    """Golden scenarios - regression koruması için deterministik testler."""
    
    def test_golden_perfect_match(self):
        """Senaryo 1: Mükemmel eşleşme → flag yok."""
        result = check_total_mismatch(
            invoice_total=1234.56,
            computed_total=1234.56,
        )
        assert result.has_mismatch is False
        assert result.delta == 0
    
    def test_golden_rounding_diff(self):
        """Senaryo 2: Yuvarlama farkı → flag yok."""
        # 2 TL fark, %0.2 → threshold altında
        result = check_total_mismatch(
            invoice_total=1000.00,
            computed_total=998.00,
        )
        assert result.has_mismatch is False
        assert result.delta == 2.0
        assert result.ratio < 0.05
    
    def test_golden_real_mismatch_s2(self):
        """Senaryo 3: Gerçek mismatch → S2 flag."""
        # 100 TL fark, %10 → S2
        result = check_total_mismatch(
            invoice_total=1000.00,
            computed_total=900.00,
        )
        assert result.has_mismatch is True
        assert result.severity == "S2"
        assert result.delta == 100.0
        assert result.ratio == 0.1
    
    def test_golden_severe_mismatch_s1(self):
        """Senaryo 4: Ciddi mismatch → S1 flag."""
        # 600 TL fark, %25 → S1 (delta >= 500)
        result = check_total_mismatch(
            invoice_total=2400.00,
            computed_total=1800.00,
        )
        assert result.has_mismatch is True
        assert result.severity == "S1"
        assert result.delta == 600.0
        assert result.ratio == 0.25
    
    def test_golden_ocr_suspect(self):
        """Senaryo 5: Düşük confidence + mismatch → OCR_LOCALE_SUSPECT."""
        result = check_total_mismatch(
            invoice_total=1000.00,
            computed_total=900.00,
            extraction_confidence=0.5,
        )
        assert result.has_mismatch is True
        assert result.suspect_reason == "OCR_LOCALE_SUSPECT"
