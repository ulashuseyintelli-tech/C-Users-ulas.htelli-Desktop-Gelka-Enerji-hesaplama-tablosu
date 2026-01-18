"""
Resolution Reasons Tests - Sprint 8.1

ResolutionReason enum ve stuck threshold testleri.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from backend.app.resolution_reasons import (
    ResolutionReason,
    STUCK_THRESHOLD_MINUTES,
)
from backend.app.retry_executor import RetryResult, RetryResultStatus
from backend.app.recompute_service import RecomputeResult


class TestResolutionReasonEnum:
    """ResolutionReason enum testleri"""
    
    def test_all_values_defined(self):
        """Tüm değerler tanımlı"""
        assert ResolutionReason.RECOMPUTE_RESOLVED == "recompute_resolved"
        assert ResolutionReason.MANUAL_RESOLVED == "manual_resolved"
        assert ResolutionReason.AUTO_RESOLVED == "auto_resolved"
        assert ResolutionReason.RECOMPUTE_LIMIT_EXCEEDED == "recompute_limit_exceeded"
        assert ResolutionReason.RETRY_EXHAUSTED == "retry_exhausted"
        assert ResolutionReason.RECLASSIFIED == "reclassified"
    
    def test_is_valid(self):
        """is_valid doğru çalışır"""
        assert ResolutionReason.is_valid("recompute_resolved") is True
        assert ResolutionReason.is_valid("manual_resolved") is True
        assert ResolutionReason.is_valid("invalid_value") is False
        assert ResolutionReason.is_valid("") is False
    
    def test_is_resolved(self):
        """is_resolved doğru çalışır"""
        # Çözüm sayılanlar
        assert ResolutionReason.is_resolved("recompute_resolved") is True
        assert ResolutionReason.is_resolved("manual_resolved") is True
        assert ResolutionReason.is_resolved("auto_resolved") is True
        
        # Çözüm sayılmayanlar
        assert ResolutionReason.is_resolved("recompute_limit_exceeded") is False
        assert ResolutionReason.is_resolved("retry_exhausted") is False
        assert ResolutionReason.is_resolved("reclassified") is False
    
    def test_resolved_set_for_mttr(self):
        """RESOLVED_SET MTTR hesabı için doğru"""
        assert ResolutionReason.RECOMPUTE_RESOLVED in ResolutionReason.RESOLVED_SET
        assert ResolutionReason.MANUAL_RESOLVED in ResolutionReason.RESOLVED_SET
        assert ResolutionReason.AUTO_RESOLVED in ResolutionReason.RESOLVED_SET
        
        # Kapanış türleri dahil değil
        assert ResolutionReason.RECOMPUTE_LIMIT_EXCEEDED not in ResolutionReason.RESOLVED_SET
        assert ResolutionReason.RETRY_EXHAUSTED not in ResolutionReason.RESOLVED_SET


class TestStuckThreshold:
    """Stuck threshold testleri"""
    
    def test_stuck_threshold_is_10_minutes(self):
        """STUCK_THRESHOLD_MINUTES = 10"""
        assert STUCK_THRESHOLD_MINUTES == 10


class TestRecomputeResolutionReason:
    """Recompute'da resolution_reason kullanımı testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_recompute_resolved_sets_reason(self, mock_db):
        """Recompute resolved → resolution_reason = RECOMPUTE_RESOLVED"""
        from backend.app.recompute_service import apply_recompute_result
        
        incident = MagicMock()
        incident.id = 42
        incident.primary_flag = "MARKET_PRICE_MISSING"
        incident.recompute_count = 0
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        result = RecomputeResult(
            new_all_flags=[],
            new_primary_flag=None,
            new_category=None,
            new_severity=None,
            quality_score=100,
            is_resolved=True,
            is_reclassified=False,
        )
        
        apply_recompute_result(mock_db, 42, result)
        
        assert incident.resolution_reason == ResolutionReason.RECOMPUTE_RESOLVED
        assert incident.status == "RESOLVED"


class TestRetryExhaustedResolutionReason:
    """Retry exhausted'da resolution_reason kullanımı testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_retry_exhausted_sets_reason(self, mock_db):
        """Retry exhausted → resolution_reason = RETRY_EXHAUSTED"""
        from backend.app.retry_executor import RetryExecutor
        
        incident = MagicMock()
        incident.id = 42
        incident.retry_attempt_count = 3  # Will become 4 (exhaust)
        incident.status = "PENDING_RETRY"
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        executor = RetryExecutor(worker_id="test")
        result = RetryResult(status=RetryResultStatus.FAIL, message="Failed")
        
        executor.apply_result(mock_db, 42, result)
        
        assert incident.resolution_reason == ResolutionReason.RETRY_EXHAUSTED
        assert incident.status == "OPEN"


class TestRecomputeLimitResolutionReason:
    """Recompute limit'de resolution_reason kullanımı testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_recompute_limit_sets_reason(self, mock_db):
        """Recompute limit → resolution_reason = RECOMPUTE_LIMIT_EXCEEDED"""
        from backend.app.retry_orchestrator import RetryOrchestrator, MAX_RECOMPUTE_COUNT
        
        incident = MagicMock()
        incident.id = 42
        incident.recompute_count = MAX_RECOMPUTE_COUNT  # At limit
        incident.status = "PENDING_RETRY"
        incident.retry_exhausted_at = None
        incident.routed_payload = {}
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        mock_executor = MagicMock()
        mock_executor.execute.return_value = RetryResult(
            status=RetryResultStatus.SUCCESS,
            message="Success"
        )
        
        def apply_side_effect(db, incident_id, result, now):
            incident.status = "PENDING_RECOMPUTE"
            incident.retry_success = True
        
        mock_executor.apply_result.side_effect = apply_side_effect
        
        orchestrator = RetryOrchestrator(executor=mock_executor)
        orchestrator.process_incident(mock_db, 42)
        
        assert incident.resolution_reason == ResolutionReason.RECOMPUTE_LIMIT_EXCEEDED
        assert incident.status == "OPEN"


class TestReclassifiedNotResolved:
    """RECLASSIFIED bir çözüm değil testleri"""
    
    def test_reclassified_not_in_resolved_set(self):
        """RECLASSIFIED RESOLVED_SET'te değil"""
        assert ResolutionReason.RECLASSIFIED not in ResolutionReason.RESOLVED_SET
    
    def test_reclassified_is_resolved_returns_false(self):
        """is_resolved(RECLASSIFIED) = False"""
        assert ResolutionReason.is_resolved(ResolutionReason.RECLASSIFIED) is False
