"""
Retry Orchestrator Tests - Sprint 8.0

Retry + Recompute koordinasyonu testleri.
Tek otorite RESOLVED kontratı.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

from backend.app.retry_orchestrator import (
    RetryOrchestrator,
    OrchestrationResult,
    BatchOrchestrationSummary,
    MAX_RECOMPUTE_COUNT,
)
from backend.app.retry_executor import RetryResult, RetryResultStatus
from backend.app.recompute_service import RecomputeContext, RecomputeResult


class TestSingleAuthorityResolved:
    """Tek otorite RESOLVED kontrat testleri"""
    
    @pytest.fixture
    def mock_db(self):
        """Mock database session"""
        return MagicMock()
    
    @pytest.fixture
    def mock_incident(self):
        """Mock incident"""
        incident = MagicMock()
        incident.id = 42
        incident.primary_flag = "MARKET_PRICE_MISSING"
        incident.category = "PRICE_MISSING"
        incident.status = "PENDING_RETRY"
        incident.retry_attempt_count = 1
        incident.recompute_count = 0
        incident.retry_exhausted_at = None
        incident.routed_payload = {}
        return incident
    
    def test_executor_success_sets_pending_recompute(self, mock_db, mock_incident):
        """Executor success → PENDING_RECOMPUTE (not RESOLVED)"""
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        # Mock executor to return success
        mock_executor = MagicMock()
        mock_executor.execute.return_value = RetryResult(
            status=RetryResultStatus.SUCCESS,
            message="Lookup succeeded"
        )
        
        # Mock context provider
        def mock_context_provider(inc):
            return RecomputeContext(
                extraction={},
                validation={},
                calculation={"meta_pricing_source": "epias"},
                calculation_error=None,
                debug_meta=None,
            )
        
        orchestrator = RetryOrchestrator(
            executor=mock_executor,
            context_provider=mock_context_provider,
        )
        
        # After executor.apply_result, status should be PENDING_RECOMPUTE
        # (We're testing the contract, not the full flow here)
        mock_executor.apply_result.return_value = None
        
        # Simulate executor setting PENDING_RECOMPUTE
        def apply_side_effect(db, incident_id, result, now):
            mock_incident.status = "PENDING_RECOMPUTE"
            mock_incident.retry_success = True
        
        mock_executor.apply_result.side_effect = apply_side_effect
        
        result = orchestrator.process_incident(mock_db, 42)
        
        # Executor should have been called
        mock_executor.execute.assert_called_once()
        mock_executor.apply_result.assert_called_once()
    
    def test_orchestrator_success_recompute_resolved(self, mock_db, mock_incident):
        """Orchestrator: retry success + recompute(no flags) → RESOLVED"""
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        # Mock executor to return success
        mock_executor = MagicMock()
        mock_executor.execute.return_value = RetryResult(
            status=RetryResultStatus.SUCCESS,
            message="Lookup succeeded"
        )
        
        def apply_side_effect(db, incident_id, result, now):
            mock_incident.status = "PENDING_RECOMPUTE"
            mock_incident.retry_success = True
        
        mock_executor.apply_result.side_effect = apply_side_effect
        
        # Mock context provider - no flags (resolved)
        def mock_context_provider(inc):
            return RecomputeContext(
                extraction={},
                validation={},
                calculation={"meta_pricing_source": "epias", "meta_distribution_source": "tariff"},
                calculation_error=None,
                debug_meta=None,
            )
        
        orchestrator = RetryOrchestrator(
            executor=mock_executor,
            context_provider=mock_context_provider,
        )
        
        # Mock recompute to set RESOLVED
        with patch('backend.app.retry_orchestrator.recompute_quality_flags') as mock_recompute:
            mock_recompute.return_value = RecomputeResult(
                new_all_flags=[],
                new_primary_flag=None,
                new_category=None,
                new_severity=None,
                quality_score=100,
                is_resolved=True,
                is_reclassified=False,
            )
            
            with patch('backend.app.retry_orchestrator.apply_recompute_result') as mock_apply:
                def apply_recompute_side_effect(db, incident_id, result, now):
                    mock_incident.status = "RESOLVED"
                
                mock_apply.side_effect = apply_recompute_side_effect
                
                result = orchestrator.process_incident(mock_db, 42)
        
        assert result.retry_success is True
        assert result.is_resolved is True
        assert result.final_status == "RESOLVED"
    
    def test_orchestrator_success_recompute_not_resolved(self, mock_db, mock_incident):
        """Orchestrator: retry success + recompute(flags) → not RESOLVED"""
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        mock_executor = MagicMock()
        mock_executor.execute.return_value = RetryResult(
            status=RetryResultStatus.SUCCESS,
            message="Lookup succeeded"
        )
        
        def apply_side_effect(db, incident_id, result, now):
            mock_incident.status = "PENDING_RECOMPUTE"
            mock_incident.retry_success = True
        
        mock_executor.apply_result.side_effect = apply_side_effect
        
        def mock_context_provider(inc):
            return RecomputeContext(
                extraction={},
                validation={},
                calculation={"meta_pricing_source": "default"},  # Still missing
                calculation_error=None,
                debug_meta=None,
            )
        
        orchestrator = RetryOrchestrator(
            executor=mock_executor,
            context_provider=mock_context_provider,
        )
        
        with patch('backend.app.retry_orchestrator.recompute_quality_flags') as mock_recompute:
            mock_recompute.return_value = RecomputeResult(
                new_all_flags=["MARKET_PRICE_MISSING"],
                new_primary_flag="MARKET_PRICE_MISSING",
                new_category="PRICE_MISSING",
                new_severity="S1",
                quality_score=50,
                is_resolved=False,
                is_reclassified=False,
            )
            
            with patch('backend.app.retry_orchestrator.apply_recompute_result') as mock_apply:
                def apply_recompute_side_effect(db, incident_id, result, now):
                    mock_incident.status = "PENDING_RETRY"  # Back to retry
                
                mock_apply.side_effect = apply_recompute_side_effect
                
                result = orchestrator.process_incident(mock_db, 42)
        
        assert result.retry_success is True
        assert result.is_resolved is False
    
    def test_orchestrator_success_recompute_reclassify(self, mock_db, mock_incident):
        """Orchestrator: retry success + recompute(different primary) → reclassified"""
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        mock_executor = MagicMock()
        mock_executor.execute.return_value = RetryResult(
            status=RetryResultStatus.SUCCESS,
            message="Lookup succeeded"
        )
        
        def apply_side_effect(db, incident_id, result, now):
            mock_incident.status = "PENDING_RECOMPUTE"
            mock_incident.retry_success = True
        
        mock_executor.apply_result.side_effect = apply_side_effect
        
        def mock_context_provider(inc):
            return RecomputeContext(
                extraction={},
                validation={"is_ready_for_pricing": False, "missing_fields": ["consumption_kwh"]},
                calculation={"meta_pricing_source": "epias"},
                calculation_error=None,
                debug_meta=None,
            )
        
        orchestrator = RetryOrchestrator(
            executor=mock_executor,
            context_provider=mock_context_provider,
        )
        
        with patch('backend.app.retry_orchestrator.recompute_quality_flags') as mock_recompute:
            mock_recompute.return_value = RecomputeResult(
                new_all_flags=["CONSUMPTION_MISSING"],
                new_primary_flag="CONSUMPTION_MISSING",  # Different!
                new_category="CONSUMPTION_MISSING",
                new_severity="S1",
                quality_score=50,
                is_resolved=False,
                is_reclassified=True,
            )
            
            with patch('backend.app.retry_orchestrator.apply_recompute_result') as mock_apply:
                result = orchestrator.process_incident(mock_db, 42)
        
        assert result.retry_success is True
        assert result.is_reclassified is True


class TestRecomputeLimitGuard:
    """Recompute limit guard testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    @pytest.fixture
    def mock_incident(self):
        incident = MagicMock()
        incident.id = 42
        incident.primary_flag = "MARKET_PRICE_MISSING"
        incident.status = "PENDING_RETRY"
        incident.retry_attempt_count = 1
        incident.retry_exhausted_at = None
        incident.routed_payload = {}
        return incident
    
    def test_recompute_limit_exceeded_sets_open(self, mock_db, mock_incident):
        """recompute_count >= MAX → OPEN + resolution_note"""
        mock_incident.recompute_count = MAX_RECOMPUTE_COUNT  # At limit
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        mock_executor = MagicMock()
        mock_executor.execute.return_value = RetryResult(
            status=RetryResultStatus.SUCCESS,
            message="Lookup succeeded"
        )
        
        def apply_side_effect(db, incident_id, result, now):
            mock_incident.status = "PENDING_RECOMPUTE"
            mock_incident.retry_success = True
        
        mock_executor.apply_result.side_effect = apply_side_effect
        
        orchestrator = RetryOrchestrator(executor=mock_executor)
        
        result = orchestrator.process_incident(mock_db, 42)
        
        assert result.is_recompute_limited is True
        assert mock_incident.status == "OPEN"
        assert mock_incident.resolution_note == "recompute_limit_exceeded"
    
    def test_recompute_under_limit_continues(self, mock_db, mock_incident):
        """recompute_count < MAX → normal flow"""
        mock_incident.recompute_count = MAX_RECOMPUTE_COUNT - 1  # Under limit
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        mock_executor = MagicMock()
        mock_executor.execute.return_value = RetryResult(
            status=RetryResultStatus.SUCCESS,
            message="Lookup succeeded"
        )
        
        def apply_side_effect(db, incident_id, result, now):
            mock_incident.status = "PENDING_RECOMPUTE"
            mock_incident.retry_success = True
        
        mock_executor.apply_result.side_effect = apply_side_effect
        
        def mock_context_provider(inc):
            return RecomputeContext(
                extraction={},
                validation={},
                calculation={"meta_pricing_source": "epias"},
                calculation_error=None,
                debug_meta=None,
            )
        
        orchestrator = RetryOrchestrator(
            executor=mock_executor,
            context_provider=mock_context_provider,
        )
        
        with patch('backend.app.retry_orchestrator.recompute_quality_flags') as mock_recompute:
            mock_recompute.return_value = RecomputeResult(
                new_all_flags=[],
                new_primary_flag=None,
                new_category=None,
                new_severity=None,
                quality_score=100,
                is_resolved=True,
                is_reclassified=False,
            )
            
            with patch('backend.app.retry_orchestrator.apply_recompute_result'):
                result = orchestrator.process_incident(mock_db, 42)
        
        assert result.is_recompute_limited is False


class TestIdempotency:
    """Idempotency testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_pending_recompute_can_be_reprocessed(self, mock_db):
        """PENDING_RECOMPUTE zaten set ise yeniden işlenebilir"""
        incident = MagicMock()
        incident.id = 42
        incident.status = "PENDING_RECOMPUTE"  # Already set
        incident.retry_success = True
        incident.recompute_count = 1
        incident.retry_exhausted_at = None
        incident.routed_payload = {}
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        mock_executor = MagicMock()
        mock_executor.execute.return_value = RetryResult(
            status=RetryResultStatus.SUCCESS,
            message="Lookup succeeded"
        )
        
        def apply_side_effect(db, incident_id, result, now):
            incident.status = "PENDING_RECOMPUTE"
        
        mock_executor.apply_result.side_effect = apply_side_effect
        
        def mock_context_provider(inc):
            return RecomputeContext(
                extraction={},
                validation={},
                calculation={"meta_pricing_source": "epias"},
                calculation_error=None,
                debug_meta=None,
            )
        
        orchestrator = RetryOrchestrator(
            executor=mock_executor,
            context_provider=mock_context_provider,
        )
        
        with patch('backend.app.retry_orchestrator.recompute_quality_flags') as mock_recompute:
            mock_recompute.return_value = RecomputeResult(
                new_all_flags=[],
                new_primary_flag=None,
                new_category=None,
                new_severity=None,
                quality_score=100,
                is_resolved=True,
                is_reclassified=False,
            )
            
            with patch('backend.app.retry_orchestrator.apply_recompute_result') as mock_apply:
                # İki kez çalıştır
                result1 = orchestrator.process_incident(mock_db, 42)
                result2 = orchestrator.process_incident(mock_db, 42)
        
        # Her ikisi de başarılı olmalı
        assert result1.retry_success is True
        assert result2.retry_success is True


class TestRetryFail:
    """Retry fail testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_retry_fail_does_not_trigger_recompute(self, mock_db):
        """Retry fail → recompute tetiklenmez"""
        incident = MagicMock()
        incident.id = 42
        incident.status = "PENDING_RETRY"
        incident.retry_attempt_count = 1
        incident.recompute_count = 0
        incident.retry_exhausted_at = None
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        mock_executor = MagicMock()
        mock_executor.execute.return_value = RetryResult(
            status=RetryResultStatus.FAIL,
            message="Lookup failed"
        )
        
        def apply_side_effect(db, incident_id, result, now):
            incident.status = "PENDING_RETRY"
            incident.retry_success = False
        
        mock_executor.apply_result.side_effect = apply_side_effect
        
        orchestrator = RetryOrchestrator(executor=mock_executor)
        
        with patch('backend.app.retry_orchestrator.recompute_quality_flags') as mock_recompute:
            result = orchestrator.process_incident(mock_db, 42)
            
            # Recompute çağrılmamalı
            mock_recompute.assert_not_called()
        
        assert result.retry_success is False
        assert result.is_resolved is False
    
    def test_retry_exhausted_sets_flag(self, mock_db):
        """Retry exhausted → is_exhausted=True"""
        incident = MagicMock()
        incident.id = 42
        incident.status = "PENDING_RETRY"
        incident.retry_attempt_count = 3
        incident.recompute_count = 0
        incident.retry_exhausted_at = None
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        mock_executor = MagicMock()
        mock_executor.execute.return_value = RetryResult(
            status=RetryResultStatus.FAIL,
            message="Lookup failed"
        )
        
        def apply_side_effect(db, incident_id, result, now):
            incident.status = "OPEN"
            incident.retry_success = False
            incident.retry_exhausted_at = datetime.now()
        
        mock_executor.apply_result.side_effect = apply_side_effect
        
        orchestrator = RetryOrchestrator(executor=mock_executor)
        
        result = orchestrator.process_incident(mock_db, 42)
        
        assert result.retry_success is False
        assert result.is_exhausted is True


class TestStuckPendingRecompute:
    """Stuck PENDING_RECOMPUTE testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_processes_stuck_incidents(self, mock_db):
        """Stuck PENDING_RECOMPUTE incident'ları işlenir"""
        stuck_incident = MagicMock()
        stuck_incident.id = 42
        stuck_incident.status = "PENDING_RECOMPUTE"
        stuck_incident.recompute_count = 1
        stuck_incident.routed_payload = {}
        
        mock_db.query.return_value.filter.return_value.limit.return_value.all.return_value = [stuck_incident]
        
        orchestrator = RetryOrchestrator()
        
        with patch('backend.app.retry_orchestrator.recompute_quality_flags') as mock_recompute:
            mock_recompute.return_value = RecomputeResult(
                new_all_flags=[],
                new_primary_flag=None,
                new_category=None,
                new_severity=None,
                quality_score=100,
                is_resolved=True,
                is_reclassified=False,
            )
            
            with patch('backend.app.retry_orchestrator.apply_recompute_result'):
                processed = orchestrator.process_pending_recomputes(mock_db, "default")
        
        assert processed == 1


class TestConstants:
    """Sabit değer testleri"""
    
    def test_max_recompute_count_is_5(self):
        """MAX_RECOMPUTE_COUNT = 5"""
        assert MAX_RECOMPUTE_COUNT == 5
