"""
Config Module Tests - Sprint 8.8

Tests for:
- Config validation invariants
- ConfigValidationError on invalid config
- Default config passes validation
"""

import pytest
from dataclasses import replace

from backend.app.config import (
    THRESHOLDS,
    Thresholds,
    MismatchThresholds,
    DriftThresholds,
    AlertThresholds,
    RecoveryThresholds,
    ValidationThresholds,
    FeedbackThresholds,
    validate_config,
    ConfigValidationError,
    get_config_summary,
    VALID_ENVIRONMENTS,
)


class TestDefaultConfigValidation:
    """Default config should pass all invariants."""
    
    def test_default_config_passes_validation(self):
        """Default THRESHOLDS should pass validation."""
        # Should not raise
        validate_config(THRESHOLDS)
    
    def test_valid_environments_set(self):
        """Valid environments should be defined."""
        assert "development" in VALID_ENVIRONMENTS
        assert "staging" in VALID_ENVIRONMENTS
        assert "production" in VALID_ENVIRONMENTS
        assert len(VALID_ENVIRONMENTS) == 3


class TestInvariant1_SevereRatioGreaterThanRatio:
    """I1: SEVERE_RATIO >= RATIO"""
    
    def test_severe_ratio_less_than_ratio_fails(self):
        """SEVERE_RATIO < RATIO should fail validation."""
        bad_mismatch = MismatchThresholds(
            RATIO=0.10,  # 10%
            SEVERE_RATIO=0.05,  # 5% - INVALID: less than RATIO
        )
        bad_config = Thresholds(Mismatch=bad_mismatch)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I1 FAIL" in str(exc_info.value)
        assert "SEVERE_RATIO" in str(exc_info.value)
    
    def test_severe_ratio_equal_to_ratio_passes(self):
        """SEVERE_RATIO == RATIO should pass."""
        equal_mismatch = MismatchThresholds(
            RATIO=0.10,
            SEVERE_RATIO=0.10,  # Equal is OK
            ROUNDING_RATIO=0.005,  # Must be < RATIO
        )
        equal_config = Thresholds(Mismatch=equal_mismatch)
        
        # Should not raise
        validate_config(equal_config)


class TestInvariant2_SevereAbsoluteGreaterThanAbsolute:
    """I2: SEVERE_ABSOLUTE >= ABSOLUTE"""
    
    def test_severe_absolute_less_than_absolute_fails(self):
        """SEVERE_ABSOLUTE < ABSOLUTE should fail validation."""
        bad_mismatch = MismatchThresholds(
            ABSOLUTE=100.0,
            SEVERE_ABSOLUTE=50.0,  # INVALID: less than ABSOLUTE
        )
        bad_config = Thresholds(Mismatch=bad_mismatch)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I2 FAIL" in str(exc_info.value)
        assert "SEVERE_ABSOLUTE" in str(exc_info.value)


class TestInvariant3_RoundingRatioLessThanRatio:
    """I3: ROUNDING_RATIO < RATIO"""
    
    def test_rounding_ratio_equal_to_ratio_fails(self):
        """ROUNDING_RATIO == RATIO should fail (must be strictly less)."""
        bad_mismatch = MismatchThresholds(
            RATIO=0.05,
            ROUNDING_RATIO=0.05,  # INVALID: equal to RATIO
        )
        bad_config = Thresholds(Mismatch=bad_mismatch)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I3 FAIL" in str(exc_info.value)
        assert "ROUNDING_RATIO" in str(exc_info.value)
        assert "swallowing" in str(exc_info.value)
    
    def test_rounding_ratio_greater_than_ratio_fails(self):
        """ROUNDING_RATIO > RATIO should fail."""
        bad_mismatch = MismatchThresholds(
            RATIO=0.05,
            ROUNDING_RATIO=0.10,  # INVALID: greater than RATIO
        )
        bad_config = Thresholds(Mismatch=bad_mismatch)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I3 FAIL" in str(exc_info.value)


class TestInvariant4_MinUnitPriceLessThanMax:
    """I4: MIN_UNIT_PRICE < MAX_UNIT_PRICE"""
    
    def test_min_unit_price_equal_to_max_fails(self):
        """MIN_UNIT_PRICE == MAX_UNIT_PRICE should fail."""
        bad_validation = ValidationThresholds(
            MIN_UNIT_PRICE=5.0,
            MAX_UNIT_PRICE=5.0,  # INVALID: equal
        )
        bad_config = Thresholds(Validation=bad_validation)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I4 FAIL" in str(exc_info.value)
        assert "MIN_UNIT_PRICE" in str(exc_info.value)
    
    def test_min_unit_price_greater_than_max_fails(self):
        """MIN_UNIT_PRICE > MAX_UNIT_PRICE should fail."""
        bad_validation = ValidationThresholds(
            MIN_UNIT_PRICE=20.0,
            MAX_UNIT_PRICE=10.0,  # INVALID: min > max
        )
        bad_config = Thresholds(Validation=bad_validation)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I4 FAIL" in str(exc_info.value)


class TestInvariant5_MinDistPriceLessThanMax:
    """I5: MIN_DIST_PRICE < MAX_DIST_PRICE"""
    
    def test_min_dist_price_equal_to_max_fails(self):
        """MIN_DIST_PRICE == MAX_DIST_PRICE should fail."""
        bad_validation = ValidationThresholds(
            MIN_DIST_PRICE=2.0,
            MAX_DIST_PRICE=2.0,  # INVALID: equal
        )
        bad_config = Thresholds(Validation=bad_validation)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I5 FAIL" in str(exc_info.value)
        assert "MIN_DIST_PRICE" in str(exc_info.value)


class TestInvariant6_HardStopDeltaGreaterThanSevereRatio:
    """I6: HARD_STOP_DELTA >= SEVERE_RATIO * 100"""
    
    def test_hard_stop_delta_less_than_severe_ratio_fails(self):
        """HARD_STOP_DELTA < SEVERE_RATIO * 100 should fail."""
        bad_mismatch = MismatchThresholds(
            SEVERE_RATIO=0.30,  # 30%
        )
        bad_validation = ValidationThresholds(
            HARD_STOP_DELTA=20.0,  # 20% - INVALID: less than 30%
        )
        bad_config = Thresholds(Mismatch=bad_mismatch, Validation=bad_validation)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I6 FAIL" in str(exc_info.value)
        assert "HARD_STOP_DELTA" in str(exc_info.value)
        assert "conflicting alarms" in str(exc_info.value)


class TestInvariant7_AllPositiveValues:
    """I7: All thresholds > 0"""
    
    def test_zero_ratio_fails(self):
        """RATIO = 0 should fail."""
        bad_mismatch = MismatchThresholds(RATIO=0)
        bad_config = Thresholds(Mismatch=bad_mismatch)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I7 FAIL" in str(exc_info.value)
        assert "Mismatch.RATIO" in str(exc_info.value)
    
    def test_negative_absolute_fails(self):
        """ABSOLUTE < 0 should fail."""
        bad_mismatch = MismatchThresholds(ABSOLUTE=-10.0)
        bad_config = Thresholds(Mismatch=bad_mismatch)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I7 FAIL" in str(exc_info.value)
    
    def test_zero_stuck_minutes_fails(self):
        """STUCK_MINUTES = 0 should fail."""
        bad_recovery = RecoveryThresholds(STUCK_MINUTES=0)
        bad_config = Thresholds(Recovery=bad_recovery)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I7 FAIL" in str(exc_info.value)
        assert "STUCK_MINUTES" in str(exc_info.value)


class TestInvariant8_ConfidenceInValidRange:
    """I8: 0 < LOW_CONFIDENCE < 1"""
    
    def test_confidence_zero_fails(self):
        """LOW_CONFIDENCE = 0 should fail."""
        bad_validation = ValidationThresholds(LOW_CONFIDENCE=0)
        bad_config = Thresholds(Validation=bad_validation)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        # Could be I7 or I8, both are valid failures
        assert "FAIL" in str(exc_info.value)
        assert "LOW_CONFIDENCE" in str(exc_info.value)
    
    def test_confidence_one_fails(self):
        """LOW_CONFIDENCE = 1 should fail (must be < 1)."""
        bad_validation = ValidationThresholds(LOW_CONFIDENCE=1.0)
        bad_config = Thresholds(Validation=bad_validation)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I8 FAIL" in str(exc_info.value)
        assert "range (0, 1)" in str(exc_info.value)
    
    def test_confidence_greater_than_one_fails(self):
        """LOW_CONFIDENCE > 1 should fail."""
        bad_validation = ValidationThresholds(LOW_CONFIDENCE=1.5)
        bad_config = Thresholds(Validation=bad_validation)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        assert "I8 FAIL" in str(exc_info.value)


class TestMultipleInvariantFailures:
    """Multiple invariant failures should all be reported."""
    
    def test_multiple_failures_all_reported(self):
        """All failing invariants should be in error message."""
        bad_mismatch = MismatchThresholds(
            RATIO=0.10,
            SEVERE_RATIO=0.05,  # I1 fail
            ABSOLUTE=100.0,
            SEVERE_ABSOLUTE=50.0,  # I2 fail
            ROUNDING_RATIO=0.15,  # I3 fail
        )
        bad_config = Thresholds(Mismatch=bad_mismatch)
        
        with pytest.raises(ConfigValidationError) as exc_info:
            validate_config(bad_config)
        
        error_msg = str(exc_info.value)
        assert "I1 FAIL" in error_msg
        assert "I2 FAIL" in error_msg
        assert "I3 FAIL" in error_msg
        assert "3 error(s)" in error_msg


class TestGetConfigSummary:
    """Test config summary for /health/ready endpoint."""
    
    def test_config_summary_structure(self):
        """Config summary should have expected structure."""
        summary = get_config_summary()
        
        assert "mismatch" in summary
        assert "drift" in summary
        assert "alert" in summary
        assert "recovery" in summary
        assert "validation" in summary
    
    def test_config_summary_values_match_thresholds(self):
        """Config summary values should match THRESHOLDS."""
        summary = get_config_summary()
        
        assert summary["mismatch"]["ratio"] == THRESHOLDS.Mismatch.RATIO
        assert summary["mismatch"]["absolute"] == THRESHOLDS.Mismatch.ABSOLUTE
        assert summary["drift"]["min_sample"] == THRESHOLDS.Drift.MIN_SAMPLE
        assert summary["recovery"]["stuck_minutes"] == THRESHOLDS.Recovery.STUCK_MINUTES
        assert summary["validation"]["low_confidence"] == THRESHOLDS.Validation.LOW_CONFIDENCE
