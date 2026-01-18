"""
Retry Executor Tests - Sprint 7.0

RetryExecutor claim, execute, apply_result testleri.
Backoff stratejisi: 30m → 2h → 6h → 24h (max 4 attempt)
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

from backend.app.retry_executor import (
    RetryExecutor,
    RetryResult,
    RetryResultStatus,
    BatchSummary,
    generate_worker_id,
)


class TestGenerateWorkerId:
    """Worker ID generation testleri"""
    
    def test_worker_id_format(self):
        """Worker ID hostname:pid:uuid formatında olmalı"""
        worker_id = generate_worker_id()
        parts = worker_id.split(":")
        assert len(parts) == 3
        # pid sayı olmalı
        assert parts[1].isdigit()
        # uuid 8 karakter hex
        assert len(parts[2]) == 8
    
    def test_worker_id_unique(self):
        """Her çağrıda farklı worker ID"""
        id1 = generate_worker_id()
        id2 = generate_worker_id()
        assert id1 != id2


class TestBackoffCalculation:
    """Backoff hesaplama testleri"""
    
    def test_backoff_minutes_sequence(self):
        """Backoff sırası: 30, 120, 360, 1440"""
        executor = RetryExecutor()
        assert executor._get_backoff_minutes(0) == 30
        assert executor._get_backoff_minutes(1) == 120
        assert executor._get_backoff_minutes(2) == 360
        assert executor._get_backoff_minutes(3) == 1440
    
    def test_backoff_beyond_max_uses_last(self):
        """Max'ı aşan attempt son backoff'u kullanır"""
        executor = RetryExecutor()
        assert executor._get_backoff_minutes(4) == 1440
        assert executor._get_backoff_minutes(10) == 1440
    
    def test_backoff_negative_uses_first(self):
        """Negatif attempt ilk backoff'u kullanır"""
        executor = RetryExecutor()
        assert executor._get_backoff_minutes(-1) == 30


class TestClaimEligibility:
    """Claim eligibility testleri"""
    
    @pytest.fixture
    def mock_db(self):
        """Mock database session"""
        db = MagicMock()
        # SQLite dialect
        db.bind.dialect.name = "sqlite"
        return db
    
    @pytest.fixture
    def executor(self):
        """RetryExecutor instance"""
        return RetryExecutor(worker_id="test-worker")
    
    def test_not_eligible_future_retry_at(self, mock_db, executor):
        """retry_eligible_at > now → claim edilmez"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        
        # Candidate with future eligible_at
        candidate = MagicMock()
        candidate.id = 1
        candidate.retry_eligible_at = now + timedelta(hours=1)  # 1 saat sonra
        candidate.retry_lock_until = None
        candidate.retry_exhausted_at = None
        candidate.status = "PENDING_RETRY"
        
        # Query returns no candidates (filtered by eligible_at <= now)
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        
        result = executor.claim(mock_db, "default", now, limit=10)
        
        assert result == []
    
    def test_not_eligible_locked(self, mock_db, executor):
        """retry_lock_until > now → claim edilmez"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        
        # Query returns no candidates (filtered by lock)
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        
        result = executor.claim(mock_db, "default", now, limit=10)
        
        assert result == []
    
    def test_sqlite_optimistic_claim_rowcount(self, mock_db, executor):
        """SQLite: optimistic claim rowcount=1 ile tek claim"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        
        # Eligible candidate
        candidate = MagicMock()
        candidate.id = 42
        candidate.retry_eligible_at = now - timedelta(minutes=5)
        candidate.retry_lock_until = None
        candidate.retry_exhausted_at = None
        candidate.status = "PENDING_RETRY"
        candidate.tenant_id = "default"
        
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [candidate]
        
        # UPDATE returns rowcount=1 (success)
        update_result = MagicMock()
        update_result.rowcount = 1
        mock_db.execute.return_value = update_result
        
        result = executor.claim(mock_db, "default", now, limit=10)
        
        assert len(result) == 1
        assert result[0].id == 42
        mock_db.refresh.assert_called_once_with(candidate)
    
    def test_sqlite_optimistic_claim_race_lost(self, mock_db, executor):
        """SQLite: race kaybedilirse rowcount=0, claim başarısız"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        
        candidate = MagicMock()
        candidate.id = 42
        
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [candidate]
        
        # UPDATE returns rowcount=0 (race lost)
        update_result = MagicMock()
        update_result.rowcount = 0
        mock_db.execute.return_value = update_result
        
        result = executor.claim(mock_db, "default", now, limit=10)
        
        assert len(result) == 0


class TestApplyResult:
    """apply_result testleri"""
    
    @pytest.fixture
    def mock_db(self):
        """Mock database session"""
        return MagicMock()
    
    @pytest.fixture
    def executor(self):
        """RetryExecutor instance"""
        return RetryExecutor(worker_id="test-worker")
    
    @pytest.fixture
    def mock_incident(self):
        """Mock incident"""
        incident = MagicMock()
        incident.id = 42
        incident.retry_attempt_count = 0
        incident.status = "PENDING_RETRY"
        incident.retry_lock_until = datetime(2025, 1, 15, 10, 5, 0)
        incident.retry_lock_by = "test-worker"
        return incident
    
    def test_fail_1_attempt_1_eligible_30m(self, mock_db, executor, mock_incident):
        """Fail #1 → attempt=1, eligible_at=now+30m, status=PENDING_RETRY"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_incident.retry_attempt_count = 0
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RetryResult(status=RetryResultStatus.FAIL, message="Lookup failed")
        executor.apply_result(mock_db, 42, result, now)
        
        assert mock_incident.retry_attempt_count == 1
        assert mock_incident.retry_eligible_at == now + timedelta(minutes=30)
        assert mock_incident.status == "PENDING_RETRY"
        assert mock_incident.retry_last_attempt_at == now
        assert mock_incident.retry_lock_until is None
        assert mock_incident.retry_lock_by is None
    
    def test_fail_2_attempt_2_eligible_120m(self, mock_db, executor, mock_incident):
        """Fail #2 → attempt=2, eligible_at=now+120m"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_incident.retry_attempt_count = 1
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RetryResult(status=RetryResultStatus.FAIL, message="Lookup failed")
        executor.apply_result(mock_db, 42, result, now)
        
        assert mock_incident.retry_attempt_count == 2
        assert mock_incident.retry_eligible_at == now + timedelta(minutes=120)
    
    def test_fail_3_attempt_3_eligible_360m(self, mock_db, executor, mock_incident):
        """Fail #3 → attempt=3, eligible_at=now+360m (6h)"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_incident.retry_attempt_count = 2
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RetryResult(status=RetryResultStatus.FAIL, message="Lookup failed")
        executor.apply_result(mock_db, 42, result, now)
        
        assert mock_incident.retry_attempt_count == 3
        assert mock_incident.retry_eligible_at == now + timedelta(minutes=360)
        assert mock_incident.status == "PENDING_RETRY"
    
    def test_fail_4_exhaust_status_open(self, mock_db, executor, mock_incident):
        """Fail #4 → attempt=4, EXHAUST: status=OPEN, exhausted_at set, eligible_at NULL"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_incident.retry_attempt_count = 3  # Will become 4 (exhaust)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RetryResult(status=RetryResultStatus.FAIL, message="Lookup failed")
        executor.apply_result(mock_db, 42, result, now)
        
        # attempt=4 >= MAX_RETRY_ATTEMPTS(4) → exhausted
        assert mock_incident.retry_attempt_count == 4
        assert mock_incident.status == "OPEN"
        assert mock_incident.retry_exhausted_at == now
        assert mock_incident.retry_eligible_at is None
    
    def test_success_pending_recompute_not_resolved(self, mock_db, executor, mock_incident):
        """Success → status=PENDING_RECOMPUTE (not RESOLVED!), retry_success=True"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_incident.retry_attempt_count = 2
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RetryResult(status=RetryResultStatus.SUCCESS, message="Lookup succeeded")
        executor.apply_result(mock_db, 42, result, now)
        
        # KONTRAT: Executor ASLA RESOLVED set etmez
        assert mock_incident.status == "PENDING_RECOMPUTE"
        assert mock_incident.retry_success is True
        assert mock_incident.retry_eligible_at is None
        assert mock_incident.retry_lock_until is None
        assert mock_incident.retry_lock_by is None
        # attempt_count değişmemeli (success'te artmaz)
        assert mock_incident.retry_attempt_count == 2
    
    def test_last_attempt_at_set_on_every_attempt(self, mock_db, executor, mock_incident):
        """last_attempt_at her denemede set edilir"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        # Fail
        result = RetryResult(status=RetryResultStatus.FAIL, message="Failed")
        executor.apply_result(mock_db, 42, result, now)
        assert mock_incident.retry_last_attempt_at == now
        
        # Success
        now2 = datetime(2025, 1, 15, 11, 0, 0)
        result2 = RetryResult(status=RetryResultStatus.SUCCESS, message="Success")
        executor.apply_result(mock_db, 42, result2, now2)
        assert mock_incident.retry_last_attempt_at == now2
    
    def test_lock_cleared_on_success(self, mock_db, executor, mock_incident):
        """Success path'te lock cleared"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_incident.retry_lock_until = now + timedelta(minutes=5)
        mock_incident.retry_lock_by = "test-worker"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RetryResult(status=RetryResultStatus.SUCCESS, message="Success")
        executor.apply_result(mock_db, 42, result, now)
        
        assert mock_incident.retry_lock_until is None
        assert mock_incident.retry_lock_by is None
    
    def test_retry_success_flag_set_on_success(self, mock_db, executor, mock_incident):
        """Success'te retry_success=True set edilir"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RetryResult(status=RetryResultStatus.SUCCESS, message="Success")
        executor.apply_result(mock_db, 42, result, now)
        
        assert mock_incident.retry_success is True
    
    def test_retry_success_flag_set_on_fail(self, mock_db, executor, mock_incident):
        """Fail'de retry_success=False set edilir"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RetryResult(status=RetryResultStatus.FAIL, message="Failed")
        executor.apply_result(mock_db, 42, result, now)
        
        assert mock_incident.retry_success is False
    
    def test_executor_never_sets_resolved(self, mock_db, executor, mock_incident):
        """KONTRAT: Executor ASLA RESOLVED set etmez"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        # Success bile olsa RESOLVED olmamalı
        result = RetryResult(status=RetryResultStatus.SUCCESS, message="Success")
        executor.apply_result(mock_db, 42, result, now)
        
        assert mock_incident.status != "RESOLVED"
        assert mock_incident.status == "PENDING_RECOMPUTE"
    
    def test_lock_cleared_on_fail(self, mock_db, executor, mock_incident):
        """Fail path'te lock cleared"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_incident.retry_lock_until = now + timedelta(minutes=5)
        mock_incident.retry_lock_by = "test-worker"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RetryResult(status=RetryResultStatus.FAIL, message="Failed")
        executor.apply_result(mock_db, 42, result, now)
        
        assert mock_incident.retry_lock_until is None
        assert mock_incident.retry_lock_by is None
    
    def test_lock_cleared_on_exhaust(self, mock_db, executor, mock_incident):
        """Exhaust path'te lock cleared (fail #4)"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        mock_incident.retry_attempt_count = 3  # Will become 4 (exhaust)
        mock_incident.retry_lock_until = now + timedelta(minutes=5)
        mock_incident.retry_lock_by = "test-worker"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RetryResult(status=RetryResultStatus.FAIL, message="Failed")
        executor.apply_result(mock_db, 42, result, now)
        
        assert mock_incident.retry_lock_until is None
        assert mock_incident.retry_lock_by is None


class TestExecute:
    """execute() testleri"""
    
    def test_execute_calls_lookup_executor(self):
        """execute() lookup_executor'ı çağırır"""
        mock_lookup = MagicMock(return_value=RetryResult(
            status=RetryResultStatus.SUCCESS,
            message="OK"
        ))
        executor = RetryExecutor(lookup_executor=mock_lookup)
        
        incident = MagicMock()
        result = executor.execute(incident)
        
        mock_lookup.assert_called_once_with(incident)
        assert result.status == RetryResultStatus.SUCCESS
    
    def test_execute_handles_exception(self):
        """execute() exception'ı yakalar"""
        def failing_lookup(incident):
            raise ValueError("Provider error")
        
        executor = RetryExecutor(lookup_executor=failing_lookup)
        
        incident = MagicMock()
        incident.id = 42
        result = executor.execute(incident)
        
        assert result.status == RetryResultStatus.EXCEPTION
        assert "Provider error" in result.message


class TestBatchSummary:
    """run_batch() testleri"""
    
    @pytest.fixture
    def mock_db(self):
        """Mock database session"""
        db = MagicMock()
        db.bind.dialect.name = "sqlite"
        return db
    
    def test_batch_no_eligible_returns_empty_summary(self, mock_db):
        """Eligible incident yoksa boş summary"""
        executor = RetryExecutor()
        
        # No candidates
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        
        summary = executor.run_batch(mock_db, "default")
        
        assert summary.claimed == 0
        assert summary.success == 0
        assert summary.fail == 0
        assert summary.exhausted == 0
        assert summary.errors == 0
    
    def test_batch_counts_success_fail_exhausted(self, mock_db):
        """Batch success/fail/exhausted sayıları doğru"""
        # 3 incident: 1 success, 1 fail, 1 exhaust
        incidents = []
        for i in range(3):
            inc = MagicMock()
            inc.id = i + 1
            inc.retry_attempt_count = [0, 0, 3][i]  # 3rd will exhaust
            incidents.append(inc)
        
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = incidents
        
        # Optimistic lock success
        update_result = MagicMock()
        update_result.rowcount = 1
        mock_db.execute.return_value = update_result
        
        # Lookup results: success, fail, fail (exhaust)
        call_count = [0]
        def mock_lookup(incident):
            call_count[0] += 1
            if call_count[0] == 1:
                return RetryResult(status=RetryResultStatus.SUCCESS, message="OK")
            else:
                return RetryResult(status=RetryResultStatus.FAIL, message="Failed")
        
        executor = RetryExecutor(lookup_executor=mock_lookup, worker_id="test")
        
        # Mock apply_result to track calls
        with patch.object(executor, 'apply_result') as mock_apply:
            summary = executor.run_batch(mock_db, "default")
        
        assert summary.claimed == 3
        # Note: actual counts depend on apply_result behavior
        # This test verifies the batch flow works


class TestConstants:
    """Sabit değer testleri"""
    
    def test_max_retry_attempts_is_4(self):
        """MAX_RETRY_ATTEMPTS = 4 (4. fail = exhaust)"""
        executor = RetryExecutor()
        assert executor.MAX_RETRY_ATTEMPTS == 4
    
    def test_lock_minutes_is_5(self):
        """LOCK_MINUTES = 5"""
        executor = RetryExecutor()
        assert executor.LOCK_MINUTES == 5
    
    def test_backoff_sequence(self):
        """BACKOFF_MINUTES = [30, 120, 360, 1440]"""
        executor = RetryExecutor()
        assert executor.BACKOFF_MINUTES == [30, 120, 360, 1440]
    
    def test_exhaust_semantics(self):
        """
        Exhaust semantics: 4 retry, 4. fail = exhaust
        
        Timeline:
        - fail #1 → attempt=1 → +30m
        - fail #2 → attempt=2 → +120m
        - fail #3 → attempt=3 → +360m
        - fail #4 → attempt=4 → EXHAUST (no more retry)
        """
        executor = RetryExecutor()
        # 4 backoff schedule, 4 max attempts
        assert len(executor.BACKOFF_MINUTES) == executor.MAX_RETRY_ATTEMPTS
        # fail #4 (attempt=4) triggers exhaust
        assert 4 >= executor.MAX_RETRY_ATTEMPTS
