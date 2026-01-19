"""
Sprint 8.7: Feedback Loop Tests

Tests for feedback submission, validation, and stats calculation.

Test Categories:
1. Validation tests (enum, state guard, data validation)
2. UPSERT semantics tests
3. Stats calculation tests (null-safe)
"""

import pytest
from datetime import datetime, timezone, timedelta, date
from unittest.mock import MagicMock, patch

from backend.app.incident_metrics import (
    FeedbackAction,
    IncidentFeedback,
    FeedbackStats,
    FeedbackValidationError,
    validate_feedback,
    submit_feedback,
    get_feedback_stats,
)


class TestFeedbackAction:
    """FeedbackAction enum testleri"""
    
    def test_valid_actions(self):
        """Gecerli action'lar"""
        assert FeedbackAction.VERIFIED_OCR.value == "VERIFIED_OCR"
        assert FeedbackAction.VERIFIED_LOGIC.value == "VERIFIED_LOGIC"
        assert FeedbackAction.ACCEPTED_ROUNDING.value == "ACCEPTED_ROUNDING"
        assert FeedbackAction.ESCALATED.value == "ESCALATED"
        assert FeedbackAction.NO_ACTION_REQUIRED.value == "NO_ACTION_REQUIRED"
    
    def test_action_count(self):
        """5 action olmali"""
        assert len(FeedbackAction) == 5


class TestIncidentFeedback:
    """IncidentFeedback dataclass testleri"""
    
    def test_to_dict(self):
        """to_dict donusumu"""
        now = datetime.now(timezone.utc)
        fb = IncidentFeedback(
            action_taken=FeedbackAction.VERIFIED_OCR,
            was_hint_correct=True,
            actual_root_cause="Locale issue",
            resolution_time_seconds=120,
            feedback_at=now,
            feedback_by="user123",
        )
        d = fb.to_dict()
        
        assert d["action_taken"] == "VERIFIED_OCR"
        assert d["was_hint_correct"] is True
        assert d["actual_root_cause"] == "Locale issue"
        assert d["resolution_time_seconds"] == 120
        assert d["feedback_by"] == "user123"
    
    def test_from_dict(self):
        """from_dict parse"""
        now = datetime.now(timezone.utc)
        data = {
            "action_taken": "ACCEPTED_ROUNDING",
            "was_hint_correct": False,
            "actual_root_cause": None,
            "resolution_time_seconds": 60,
            "feedback_at": now.isoformat(),
            "feedback_by": "admin",
        }
        fb = IncidentFeedback.from_dict(data)
        
        assert fb.action_taken == FeedbackAction.ACCEPTED_ROUNDING
        assert fb.was_hint_correct is False
        assert fb.actual_root_cause is None
        assert fb.resolution_time_seconds == 60


class TestValidateFeedback:
    """validate_feedback fonksiyonu testleri"""
    
    def test_state_guard_not_resolved(self):
        """RESOLVED olmayan incident icin hata"""
        payload = {
            "action_taken": "VERIFIED_OCR",
            "was_hint_correct": True,
        }
        with pytest.raises(FeedbackValidationError) as exc:
            validate_feedback(payload, "OPEN")
        assert exc.value.code == "incident_not_resolved"
    
    def test_state_guard_pending(self):
        """PENDING_RETRY icin hata"""
        payload = {
            "action_taken": "VERIFIED_OCR",
            "was_hint_correct": True,
        }
        with pytest.raises(FeedbackValidationError) as exc:
            validate_feedback(payload, "PENDING_RETRY")
        assert exc.value.code == "incident_not_resolved"
    
    def test_missing_action_taken(self):
        """action_taken eksik"""
        payload = {
            "was_hint_correct": True,
        }
        with pytest.raises(FeedbackValidationError) as exc:
            validate_feedback(payload, "RESOLVED")
        assert exc.value.code == "invalid_feedback_data"
    
    def test_invalid_action_taken(self):
        """Gecersiz action_taken"""
        payload = {
            "action_taken": "INVALID_ACTION",
            "was_hint_correct": True,
        }
        with pytest.raises(FeedbackValidationError) as exc:
            validate_feedback(payload, "RESOLVED")
        assert exc.value.code == "invalid_feedback_action"
    
    def test_missing_was_hint_correct(self):
        """was_hint_correct eksik"""
        payload = {
            "action_taken": "VERIFIED_OCR",
        }
        with pytest.raises(FeedbackValidationError) as exc:
            validate_feedback(payload, "RESOLVED")
        assert exc.value.code == "invalid_feedback_data"
    
    def test_was_hint_correct_not_bool(self):
        """was_hint_correct bool degil"""
        payload = {
            "action_taken": "VERIFIED_OCR",
            "was_hint_correct": "yes",
        }
        with pytest.raises(FeedbackValidationError) as exc:
            validate_feedback(payload, "RESOLVED")
        assert exc.value.code == "invalid_feedback_data"
    
    def test_negative_resolution_time(self):
        """Negatif resolution_time"""
        payload = {
            "action_taken": "VERIFIED_OCR",
            "was_hint_correct": True,
            "resolution_time_seconds": -10,
        }
        with pytest.raises(FeedbackValidationError) as exc:
            validate_feedback(payload, "RESOLVED")
        assert exc.value.code == "invalid_feedback_data"
    
    def test_root_cause_too_long(self):
        """actual_root_cause cok uzun"""
        payload = {
            "action_taken": "VERIFIED_OCR",
            "was_hint_correct": True,
            "actual_root_cause": "x" * 300,
        }
        with pytest.raises(FeedbackValidationError) as exc:
            validate_feedback(payload, "RESOLVED")
        assert exc.value.code == "invalid_feedback_data"
    
    def test_valid_payload(self):
        """Gecerli payload hata vermemeli"""
        payload = {
            "action_taken": "VERIFIED_OCR",
            "was_hint_correct": True,
            "actual_root_cause": "Locale issue",
            "resolution_time_seconds": 120,
        }
        # Should not raise
        validate_feedback(payload, "RESOLVED")
    
    def test_valid_minimal_payload(self):
        """Minimal gecerli payload"""
        payload = {
            "action_taken": "NO_ACTION_REQUIRED",
            "was_hint_correct": False,
        }
        # Should not raise
        validate_feedback(payload, "RESOLVED")


class TestFeedbackStats:
    """FeedbackStats dataclass testleri"""
    
    def test_to_dict(self):
        """to_dict donusumu"""
        stats = FeedbackStats(
            hint_accuracy_rate=0.75,
            total_feedback_count=100,
            action_class_accuracy={"VERIFIED_OCR": 0.8, "VERIFIED_LOGIC": 0.6},
            avg_resolution_time_by_class={"VERIFIED_OCR": 120.5, "VERIFIED_LOGIC": 180.0},
            feedback_coverage=0.5,
            resolved_with_feedback=50,
            resolved_total=100,
        )
        d = stats.to_dict()
        
        assert d["hint_accuracy_rate"] == 0.75
        assert d["total_feedback_count"] == 100
        assert d["feedback_coverage"] == 0.5
        assert d["resolved_with_feedback"] == 50
        assert d["resolved_total"] == 100
    
    def test_null_safe_rates(self):
        """Sifir denominator icin 0.0 donmeli"""
        stats = FeedbackStats(
            hint_accuracy_rate=0.0,
            total_feedback_count=0,
            action_class_accuracy={},
            avg_resolution_time_by_class={},
            feedback_coverage=0.0,
            resolved_with_feedback=0,
            resolved_total=0,
        )
        d = stats.to_dict()
        
        assert d["hint_accuracy_rate"] == 0.0
        assert d["feedback_coverage"] == 0.0


class TestSubmitFeedback:
    """submit_feedback fonksiyonu testleri"""
    
    def test_incident_not_found(self):
        """Incident bulunamazsa ValueError"""
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None
        
        with pytest.raises(ValueError) as exc:
            submit_feedback(mock_session, 999, {}, "user1")
        assert "not found" in str(exc.value)
    
    def test_upsert_overwrites_previous(self):
        """UPSERT: onceki feedback uzerine yazar"""
        # Mock incident
        mock_incident = MagicMock()
        mock_incident.status = "RESOLVED"
        mock_incident.feedback_json = {"old": "data"}
        
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = mock_incident
        
        payload = {
            "action_taken": "VERIFIED_OCR",
            "was_hint_correct": True,
            "resolution_time_seconds": 60,
        }
        
        result = submit_feedback(mock_session, 1, payload, "user1")
        
        # feedback_json guncellenmiş olmali
        assert mock_incident.feedback_json["action_taken"] == "VERIFIED_OCR"
        assert mock_incident.feedback_json["was_hint_correct"] is True
        assert mock_incident.feedback_json["feedback_by"] == "user1"
        
        # commit cagirilmis olmali
        mock_session.commit.assert_called_once()
