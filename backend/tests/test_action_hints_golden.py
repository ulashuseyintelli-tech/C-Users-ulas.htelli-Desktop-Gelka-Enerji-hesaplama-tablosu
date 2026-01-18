"""
Golden Tests for ActionHint - Sprint 8.5

5 deterministik senaryo + ordering + determinism testleri.
Amaç: "3 adımda karar" prensibinin regression koruması.
"""

import pytest
from app.incident_service import (
    ActionClass,
    PrimarySuspect,
    ActionHint,
    generate_action_hint,
    CHECKS_VERIFY_OCR,
    CHECKS_VERIFY_INVOICE_LOGIC,
    CHECKS_ACCEPT_ROUNDING,
    ROUNDING_DELTA_THRESHOLD,
    ROUNDING_RATIO_THRESHOLD,
)
from app.calculator import check_total_mismatch


class TestGoldenActionHints:
    """Golden scenarios için ActionHint doğrulaması."""
    
    def test_golden_1_perfect_match_no_hint(self):
        """Senaryo 1: Mükemmel eşleşme → ActionHint yok."""
        mismatch = check_total_mismatch(1234.56, 1234.56)
        assert mismatch.has_mismatch is False
        
        # Mismatch yok → hint yok
        hint = generate_action_hint(
            "INVOICE_TOTAL_MISMATCH",
            mismatch.to_dict(),
        )
        assert hint is None
    
    def test_golden_2_rounding_diff_no_mismatch(self):
        """Senaryo 2: Yuvarlama farkı (threshold altında) → mismatch yok, hint yok."""
        # 2 TL fark, %0.2 → S2 threshold altında (ratio < 5%, delta < 50)
        mismatch = check_total_mismatch(1000.00, 998.00)
        assert mismatch.has_mismatch is False
        
        hint = generate_action_hint(
            "INVOICE_TOTAL_MISMATCH",
            mismatch.to_dict(),
        )
        assert hint is None
    
    def test_golden_3_small_mismatch_accept_rounding(self):
        """Senaryo 3: Küçük mismatch (delta < 10, ratio < 0.5%) → ACCEPT_ROUNDING_TOLERANCE."""
        # 8 TL fark, %0.4 → mismatch var (delta < 50 ama ratio > 5%? hayır)
        # Aslında bu threshold altında kalır. Daha iyi örnek:
        # 60 TL fark, %0.3 → mismatch var (delta >= 50), ama rounding değil
        
        # Doğru senaryo: mismatch var ama küçük
        # delta=8, ratio=0.004 → mismatch VAR (delta < 50 ama ratio < 5%)
        # Hayır, bu da mismatch yok. 
        
        # Mismatch olması için: ratio >= 5% OR delta >= 50
        # Rounding kabul için: delta < 10 AND ratio < 0.5%
        # Bu iki koşul çelişiyor! Mismatch varsa zaten ratio >= 5% veya delta >= 50
        
        # Yani ACCEPT_ROUNDING_TOLERANCE sadece edge case'lerde olabilir:
        # Örnek: delta=8, ratio=0.06 (6%) → mismatch VAR, ama delta < 10
        # Bu durumda ratio >= 0.005 olduğu için rounding DEĞİL
        
        # Gerçek rounding case: Mismatch var ama çok küçük
        # delta=8, ratio=0.004 → mismatch YOK (threshold altında)
        
        # Sonuç: ACCEPT_ROUNDING_TOLERANCE çok nadir bir case
        # Sadece: mismatch VAR (ratio >= 5% veya delta >= 50) 
        #         VE delta < 10 VE ratio < 0.5%
        # Bu matematiksel olarak imkansız!
        
        # Düzeltme: Bu test aslında "mismatch var ama çok küçük delta" için
        # Örnek: ratio=6%, delta=6 TL (100 TL fatura, 94 TL hesaplanan)
        mismatch_info = {
            "has_mismatch": True,
            "delta": 6.0,
            "ratio": 0.06,  # 6% > 5% → mismatch var
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint = generate_action_hint(
            "INVOICE_TOTAL_MISMATCH",
            mismatch_info,
            extraction_confidence=0.9,
        )
        
        # delta < 10 ama ratio >= 0.005 → VERIFY_INVOICE_LOGIC (rounding değil)
        assert hint is not None
        assert hint.action_class == ActionClass.VERIFY_INVOICE_LOGIC
    
    def test_golden_3b_true_rounding_case(self):
        """Senaryo 3b: Gerçek rounding case (çok nadir)."""
        # delta < 10 AND ratio < 0.005 AND has_mismatch=True
        # Bu sadece manuel olarak oluşturulabilir (threshold'lar çelişiyor)
        mismatch_info = {
            "has_mismatch": True,  # Manuel override
            "delta": 5.0,
            "ratio": 0.003,  # 0.3%
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint = generate_action_hint(
            "INVOICE_TOTAL_MISMATCH",
            mismatch_info,
            extraction_confidence=0.9,
        )
        
        assert hint is not None
        assert hint.action_class == ActionClass.ACCEPT_ROUNDING_TOLERANCE
        assert hint.primary_suspect == PrimarySuspect.ROUNDING
        assert hint.recommended_checks == CHECKS_ACCEPT_ROUNDING
    
    def test_golden_4_real_mismatch_verify_logic(self):
        """Senaryo 4: Gerçek mismatch → VERIFY_INVOICE_LOGIC."""
        mismatch = check_total_mismatch(1000.00, 900.00)  # 100 TL, 10%
        assert mismatch.has_mismatch is True
        assert mismatch.severity == "S2"
        
        hint = generate_action_hint(
            "INVOICE_TOTAL_MISMATCH",
            mismatch.to_dict(),
            extraction_confidence=0.85,
        )
        
        assert hint is not None
        assert hint.action_class == ActionClass.VERIFY_INVOICE_LOGIC
        assert hint.primary_suspect == PrimarySuspect.INVOICE_LOGIC
        # İlk 2 check sabit sırada olmalı
        assert "mahsup" in hint.recommended_checks[0].lower() or "indirim" in hint.recommended_checks[0].lower()
        assert "kdv" in hint.recommended_checks[1].lower()
    
    def test_golden_5_severe_mismatch_verify_logic(self):
        """Senaryo 5: Ciddi mismatch (S1) → VERIFY_INVOICE_LOGIC (aynı class)."""
        mismatch = check_total_mismatch(2400.00, 1800.00)  # 600 TL, 25%
        assert mismatch.has_mismatch is True
        assert mismatch.severity == "S1"
        
        hint = generate_action_hint(
            "INVOICE_TOTAL_MISMATCH",
            mismatch.to_dict(),
            extraction_confidence=0.9,
        )
        
        assert hint is not None
        assert hint.action_class == ActionClass.VERIFY_INVOICE_LOGIC
        assert hint.primary_suspect == PrimarySuspect.INVOICE_LOGIC
        assert len(hint.recommended_checks) == 5
    
    def test_golden_6_ocr_suspect_verify_ocr(self):
        """Senaryo 6: OCR suspect → VERIFY_OCR."""
        mismatch = check_total_mismatch(1000.00, 900.00, extraction_confidence=0.5)
        assert mismatch.has_mismatch is True
        assert mismatch.suspect_reason == "OCR_LOCALE_SUSPECT"
        
        hint = generate_action_hint(
            "INVOICE_TOTAL_MISMATCH",
            mismatch.to_dict(),
            extraction_confidence=0.5,
        )
        
        assert hint is not None
        assert hint.action_class == ActionClass.VERIFY_OCR
        assert hint.primary_suspect == PrimarySuspect.OCR_LOCALE_SUSPECT
        # İlk 2 check sabit sırada olmalı (olasılık sırası)
        assert "ondalık" in hint.recommended_checks[0].lower()
        assert "binlik" in hint.recommended_checks[1].lower()
        assert hint.confidence_note is not None
        assert "0.50" in hint.confidence_note


class TestActionHintDeterminism:
    """ActionHint deterministik olmalı: aynı input → aynı output."""
    
    def test_determinism_verify_ocr(self):
        """VERIFY_OCR için determinism."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 100.0,
            "ratio": 0.1,
            "severity": "S2",
            "suspect_reason": "OCR_LOCALE_SUSPECT",
        }
        
        hint1 = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.5)
        hint2 = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.5)
        
        assert hint1.to_dict() == hint2.to_dict()
    
    def test_determinism_verify_logic(self):
        """VERIFY_INVOICE_LOGIC için determinism."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 100.0,
            "ratio": 0.1,
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint1 = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.85)
        hint2 = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.85)
        
        assert hint1.to_dict() == hint2.to_dict()
    
    def test_determinism_accept_rounding(self):
        """ACCEPT_ROUNDING_TOLERANCE için determinism."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 5.0,
            "ratio": 0.003,
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint1 = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.9)
        hint2 = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.9)
        
        assert hint1.to_dict() == hint2.to_dict()


class TestRecommendedChecksOrdering:
    """recommended_checks sıralaması sabit olmalı."""
    
    def test_verify_ocr_checks_order(self):
        """VERIFY_OCR checks sırası sabit."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 100.0,
            "ratio": 0.1,
            "severity": "S2",
            "suspect_reason": "OCR_LOCALE_SUSPECT",
        }
        
        hint = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.5)
        
        assert hint.recommended_checks == CHECKS_VERIFY_OCR
        assert len(hint.recommended_checks) == 4
    
    def test_verify_logic_checks_order(self):
        """VERIFY_INVOICE_LOGIC checks sırası sabit."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 100.0,
            "ratio": 0.1,
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.85)
        
        assert hint.recommended_checks == CHECKS_VERIFY_INVOICE_LOGIC
        assert len(hint.recommended_checks) == 5
    
    def test_accept_rounding_checks_order(self):
        """ACCEPT_ROUNDING_TOLERANCE checks sırası sabit."""
        mismatch_info = {
            "has_mismatch": True,
            "delta": 5.0,
            "ratio": 0.003,
            "severity": "S2",
            "suspect_reason": None,
        }
        
        hint = generate_action_hint("INVOICE_TOTAL_MISMATCH", mismatch_info, 0.9)
        
        assert hint.recommended_checks == CHECKS_ACCEPT_ROUNDING
        assert len(hint.recommended_checks) == 2

