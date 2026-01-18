"""
Issue Reporter Tests - Sprint 7.1

IssueReporter idempotency, batch reporting testleri.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from backend.app.issue_reporter import (
    IssueReporter,
    IssueCreationResult,
    MockIssueTracker,
)


class TestMockIssueTracker:
    """MockIssueTracker testleri"""
    
    def test_creates_issue_with_incrementing_id(self):
        """Issue ID'ler artarak oluşturulur"""
        tracker = MockIssueTracker()
        
        result1 = tracker.create_issue("Title 1", "Body 1", ["label1"], {})
        result2 = tracker.create_issue("Title 2", "Body 2", ["label2"], {})
        
        assert result1.issue_id == "MOCK-1"
        assert result2.issue_id == "MOCK-2"
    
    def test_stores_created_issues(self):
        """Oluşturulan issue'lar saklanır"""
        tracker = MockIssueTracker()
        
        tracker.create_issue("Test Title", "Test Body", ["bug"], {"key": "value"})
        
        assert len(tracker.created_issues) == 1
        assert tracker.created_issues[0]["title"] == "Test Title"
        assert tracker.created_issues[0]["labels"] == ["bug"]
    
    def test_can_be_configured_to_fail(self):
        """Fail modunda hata döner"""
        tracker = MockIssueTracker(should_fail=True)
        
        result = tracker.create_issue("Title", "Body", [], {})
        
        assert result.success is False
        assert "configured to fail" in result.error_message


class TestIssueReporterIdempotency:
    """Idempotency testleri"""
    
    @pytest.fixture
    def mock_db(self):
        """Mock database session"""
        return MagicMock()
    
    @pytest.fixture
    def mock_tracker(self):
        """Mock issue tracker"""
        return MockIssueTracker()
    
    @pytest.fixture
    def reporter(self, mock_tracker):
        """IssueReporter instance"""
        return IssueReporter(mock_tracker)
    
    def test_skips_already_reported_incident(self, mock_db, reporter):
        """external_issue_id varsa skip"""
        incident = MagicMock()
        incident.id = 42
        incident.external_issue_id = "EXISTING-123"
        incident.external_issue_url = "https://example.com/issues/123"
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        result = reporter.report_incident(mock_db, 42)
        
        assert result.success is True
        assert result.issue_id == "EXISTING-123"
        # Tracker'a çağrı yapılmamalı
        assert len(reporter.tracker.created_issues) == 0
    
    def test_creates_issue_for_unreported_incident(self, mock_db, reporter):
        """external_issue_id yoksa issue oluşturur"""
        incident = MagicMock()
        incident.id = 42
        incident.external_issue_id = None
        incident.external_issue_url = None
        incident.primary_flag = "CALC_BUG"
        incident.category = "CALC_BUG"
        incident.severity = "S1"
        incident.provider = "ck_bogazici"
        incident.period = "2025-01"
        incident.all_flags = ["CALC_BUG"]
        incident.action_type = "BUG_REPORT"
        incident.action_owner = "calc"
        incident.action_code = "ENGINE_REGRESSION"
        incident.dedupe_key = "abc123"
        incident.dedupe_bucket = 20103
        incident.occurrence_count = 1
        incident.first_seen_at = datetime(2025, 1, 15, 10, 0, 0)
        incident.last_seen_at = datetime(2025, 1, 15, 10, 0, 0)
        incident.routed_payload = None
        incident.trace_id = "trace-001"
        incident.tenant_id = "default"
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        now = datetime(2025, 1, 15, 12, 0, 0)
        result = reporter.report_incident(mock_db, 42, now)
        
        assert result.success is True
        assert result.issue_id == "MOCK-1"
        assert incident.external_issue_id == "MOCK-1"
        assert incident.reported_at == now
        mock_db.commit.assert_called()
    
    def test_does_not_create_duplicate_issue(self, mock_db, reporter):
        """Aynı incident için tekrar issue oluşturmaz"""
        incident = MagicMock()
        incident.id = 42
        incident.external_issue_id = None
        incident.primary_flag = "CALC_BUG"
        incident.category = "CALC_BUG"
        incident.severity = "S1"
        incident.provider = "test"
        incident.period = "2025-01"
        incident.all_flags = []
        incident.action_type = "BUG_REPORT"
        incident.action_owner = "calc"
        incident.action_code = "TEST"
        incident.dedupe_key = "abc"
        incident.dedupe_bucket = 1
        incident.occurrence_count = 1
        incident.first_seen_at = datetime.now()
        incident.last_seen_at = datetime.now()
        incident.routed_payload = None
        incident.trace_id = "t1"
        incident.tenant_id = "default"
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        # İlk çağrı - issue oluşturur
        result1 = reporter.report_incident(mock_db, 42)
        assert result1.success is True
        assert result1.issue_id == "MOCK-1"
        
        # Simüle: external_issue_id set edildi
        incident.external_issue_id = "MOCK-1"
        incident.external_issue_url = "https://mock-tracker.example/issues/MOCK-1"
        
        # İkinci çağrı - skip
        result2 = reporter.report_incident(mock_db, 42)
        assert result2.success is True
        assert result2.issue_id == "MOCK-1"
        
        # Sadece 1 issue oluşturulmalı
        assert len(reporter.tracker.created_issues) == 1


class TestIssueReporterDryRun:
    """Dry run testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_dry_run_does_not_create_issue(self, mock_db):
        """Dry run modunda issue oluşturmaz"""
        tracker = MockIssueTracker()
        reporter = IssueReporter(tracker, dry_run=True)
        
        incident = MagicMock()
        incident.id = 42
        incident.external_issue_id = None
        incident.primary_flag = "TEST"
        incident.category = "TEST"
        incident.severity = "S1"
        incident.provider = "test"
        incident.period = "2025-01"
        incident.all_flags = []
        incident.action_type = "BUG_REPORT"
        incident.action_owner = "calc"
        incident.action_code = "TEST"
        incident.dedupe_key = "abc"
        incident.dedupe_bucket = 1
        incident.occurrence_count = 1
        incident.first_seen_at = datetime.now()
        incident.last_seen_at = datetime.now()
        incident.routed_payload = None
        incident.trace_id = "t1"
        incident.tenant_id = "default"
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        result = reporter.report_incident(mock_db, 42)
        
        assert result.success is True
        assert result.issue_id == "DRY-RUN"
        # Tracker'a çağrı yapılmamalı
        assert len(tracker.created_issues) == 0


class TestGetUnreportedBugs:
    """get_unreported_bugs testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_filters_by_status_and_action_type(self, mock_db):
        """REPORTED + BUG_REPORT + external_issue_id IS NULL filtresi"""
        tracker = MockIssueTracker()
        reporter = IssueReporter(tracker)
        
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        
        reporter.get_unreported_bugs(mock_db, "default", limit=10)
        
        # filter çağrıldı
        mock_db.query.return_value.filter.assert_called()


class TestIssueTitleAndBody:
    """Issue title/body oluşturma testleri"""
    
    def test_title_format(self):
        """Title formatı: [FLAG] provider - period"""
        tracker = MockIssueTracker()
        reporter = IssueReporter(tracker)
        
        incident = MagicMock()
        incident.primary_flag = "CALC_BUG"
        incident.provider = "ck_bogazici"
        incident.period = "2025-01"
        
        title = reporter._build_issue_title(incident)
        
        assert title == "[CALC_BUG] ck_bogazici - 2025-01"
    
    def test_body_contains_all_flags(self):
        """Body tüm flag'leri içerir"""
        tracker = MockIssueTracker()
        reporter = IssueReporter(tracker)
        
        incident = MagicMock()
        incident.id = 42
        incident.primary_flag = "CALC_BUG"
        incident.category = "CALC_BUG"
        incident.severity = "S1"
        incident.provider = "test"
        incident.period = "2025-01"
        incident.all_flags = ["CALC_BUG", "DISTRIBUTION_MISMATCH"]
        incident.action_type = "BUG_REPORT"
        incident.action_owner = "calc"
        incident.action_code = "ENGINE_REGRESSION"
        incident.dedupe_key = "abc"
        incident.dedupe_bucket = 1
        incident.occurrence_count = 3
        incident.first_seen_at = datetime(2025, 1, 15, 10, 0, 0)
        incident.last_seen_at = datetime(2025, 1, 15, 12, 0, 0)
        incident.routed_payload = None
        incident.trace_id = "trace-001"
        
        body = reporter._build_issue_body(incident)
        
        assert "CALC_BUG" in body
        assert "DISTRIBUTION_MISMATCH" in body
        assert "Occurrence Count" in body
        assert "3" in body
    
    def test_labels_include_category_and_severity(self):
        """Label'lar category ve severity içerir"""
        tracker = MockIssueTracker()
        reporter = IssueReporter(tracker)
        
        incident = MagicMock()
        incident.category = "CALC_BUG"
        incident.severity = "S1"
        incident.primary_flag = "CALC_BUG"
        incident.action_owner = "calc"
        
        labels = reporter._build_labels(incident)
        
        assert "incident" in labels
        assert "category:CALC_BUG" in labels
        assert "severity:S1" in labels
        assert "flag:CALC_BUG" in labels
        assert "owner:calc" in labels


class TestBatchReporting:
    """Batch reporting testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_batch_reports_all_unreported(self, mock_db):
        """Batch tüm unreported incident'ları raporlar"""
        tracker = MockIssueTracker()
        reporter = IssueReporter(tracker)
        
        # 3 unreported incident
        incidents = []
        for i in range(3):
            inc = MagicMock()
            inc.id = i + 1
            inc.external_issue_id = None
            inc.primary_flag = "TEST"
            inc.category = "TEST"
            inc.severity = "S1"
            inc.provider = "test"
            inc.period = "2025-01"
            inc.all_flags = []
            inc.action_type = "BUG_REPORT"
            inc.action_owner = "calc"
            inc.action_code = "TEST"
            inc.dedupe_key = f"key{i}"
            inc.dedupe_bucket = 1
            inc.occurrence_count = 1
            inc.first_seen_at = datetime.now()
            inc.last_seen_at = datetime.now()
            inc.routed_payload = None
            inc.trace_id = f"t{i}"
            inc.tenant_id = "default"
            incidents.append(inc)
        
        # get_unreported_bugs mock
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = incidents
        
        # report_incident için her incident'ı döndür
        def get_incident_by_id(incident_id):
            for inc in incidents:
                if inc.id == incident_id:
                    return inc
            return None
        
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            incidents[0], incidents[1], incidents[2]
        ]
        
        summary = reporter.report_batch(mock_db, "default")
        
        assert summary["total"] == 3
        assert summary["success"] == 3
        assert summary["failed"] == 0


class TestTrackerFailure:
    """Tracker failure testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_handles_tracker_failure(self, mock_db):
        """Tracker fail olursa hata döner"""
        tracker = MockIssueTracker(should_fail=True)
        reporter = IssueReporter(tracker)
        
        incident = MagicMock()
        incident.id = 42
        incident.external_issue_id = None
        incident.primary_flag = "TEST"
        incident.category = "TEST"
        incident.severity = "S1"
        incident.provider = "test"
        incident.period = "2025-01"
        incident.all_flags = []
        incident.action_type = "BUG_REPORT"
        incident.action_owner = "calc"
        incident.action_code = "TEST"
        incident.dedupe_key = "abc"
        incident.dedupe_bucket = 1
        incident.occurrence_count = 1
        incident.first_seen_at = datetime.now()
        incident.last_seen_at = datetime.now()
        incident.routed_payload = None
        incident.trace_id = "t1"
        incident.tenant_id = "default"
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        result = reporter.report_incident(mock_db, 42)
        
        assert result.success is False
        assert "configured to fail" in result.error_message
        # DB güncellenmemeli
        assert incident.external_issue_id is None


class TestIncidentNotFound:
    """Incident not found testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_returns_error_for_missing_incident(self, mock_db):
        """Incident bulunamazsa hata döner"""
        tracker = MockIssueTracker()
        reporter = IssueReporter(tracker)
        
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        result = reporter.report_incident(mock_db, 999)
        
        assert result.success is False
        assert "not found" in result.error_message
