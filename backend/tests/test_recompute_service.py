"""
Recompute Service Tests - Sprint 7.1.2

Retry sonrası recompute ve resolution doğrulama testleri.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from backend.app.recompute_service import (
    RecomputeContext,
    RecomputeResult,
    recompute_quality_flags,
    apply_recompute_result,
    check_resolution_by_recompute,
)


class TestRecomputeQualityFlags:
    """recompute_quality_flags testleri"""
    
    def test_no_flags_returns_resolved(self):
        """Flag yoksa is_resolved=True"""
        context = RecomputeContext(
            extraction={"consumption_kwh": {"value": 1000, "confidence": 0.95}},
            validation={"is_ready_for_pricing": True},
            calculation={"meta_pricing_source": "epias", "meta_distribution_source": "tariff"},
            calculation_error=None,
            debug_meta=None,
        )
        
        result = recompute_quality_flags(context)
        
        assert result.is_resolved is True
        assert result.new_all_flags == []
        assert result.new_primary_flag is None
    
    def test_with_flags_returns_not_resolved(self):
        """Flag varsa is_resolved=False"""
        context = RecomputeContext(
            extraction={},
            validation={"is_ready_for_pricing": False, "missing_fields": ["consumption_kwh"]},
            calculation=None,
            calculation_error=None,
            debug_meta=None,
        )
        
        result = recompute_quality_flags(context)
        
        assert result.is_resolved is False
        assert len(result.new_all_flags) > 0
        assert result.new_primary_flag is not None
    
    def test_market_price_missing_flag(self):
        """MARKET_PRICE_MISSING flag doğru tespit edilir"""
        context = RecomputeContext(
            extraction={},
            validation={},
            calculation={"meta_pricing_source": "default"},
            calculation_error=None,
            debug_meta=None,
        )
        
        result = recompute_quality_flags(context)
        
        assert "MARKET_PRICE_MISSING" in result.new_all_flags
    
    def test_quality_score_calculated(self):
        """Quality score hesaplanır"""
        context = RecomputeContext(
            extraction={},
            validation={},
            calculation={"meta_pricing_source": "epias", "meta_distribution_source": "tariff"},
            calculation_error=None,
            debug_meta=None,
        )
        
        result = recompute_quality_flags(context)
        
        assert result.quality_score >= 0
        assert result.quality_score <= 100


class TestApplyRecomputeResult:
    """apply_recompute_result testleri"""
    
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
        incident.severity = "S1"
        incident.all_flags = ["MARKET_PRICE_MISSING"]
        incident.secondary_flags = []
        incident.recompute_count = 0
        incident.status = "PENDING_RETRY"
        return incident
    
    def test_resolved_sets_status_resolved(self, mock_db, mock_incident):
        """is_resolved=True → status=RESOLVED"""
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RecomputeResult(
            new_all_flags=[],
            new_primary_flag=None,
            new_category=None,
            new_severity=None,
            quality_score=100,
            is_resolved=True,
            is_reclassified=False,
        )
        
        now = datetime(2025, 1, 15, 12, 0, 0)
        success = apply_recompute_result(mock_db, 42, result, now)
        
        assert success is True
        assert mock_incident.status == "RESOLVED"
        assert mock_incident.resolved_at == now
        assert mock_incident.recompute_count == 1
        mock_db.commit.assert_called()
    
    def test_same_primary_keeps_status(self, mock_db, mock_incident):
        """Aynı primary → status değişmez"""
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RecomputeResult(
            new_all_flags=["MARKET_PRICE_MISSING"],
            new_primary_flag="MARKET_PRICE_MISSING",  # Aynı
            new_category="PRICE_MISSING",
            new_severity="S1",
            quality_score=50,
            is_resolved=False,
            is_reclassified=False,
        )
        
        now = datetime(2025, 1, 15, 12, 0, 0)
        success = apply_recompute_result(mock_db, 42, result, now)
        
        assert success is True
        assert mock_incident.status == "PENDING_RETRY"  # Değişmedi
        assert mock_incident.recompute_count == 1
    
    def test_different_primary_reclassifies(self, mock_db, mock_incident):
        """Farklı primary → reclassify"""
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RecomputeResult(
            new_all_flags=["CALC_BUG"],
            new_primary_flag="CALC_BUG",  # Farklı
            new_category="CALC_BUG",
            new_severity="S1",
            quality_score=50,
            is_resolved=False,
            is_reclassified=False,
        )
        
        now = datetime(2025, 1, 15, 12, 0, 0)
        success = apply_recompute_result(mock_db, 42, result, now)
        
        assert success is True
        assert mock_incident.primary_flag == "CALC_BUG"
        assert mock_incident.previous_primary_flag == "MARKET_PRICE_MISSING"
        assert mock_incident.reclassified_at == now
        assert mock_incident.category == "CALC_BUG"
        assert result.is_reclassified is True
    
    def test_reclassified_at_set(self, mock_db, mock_incident):
        """Reclassify'da reclassified_at set edilir"""
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RecomputeResult(
            new_all_flags=["TARIFF_LOOKUP_FAILED"],
            new_primary_flag="TARIFF_LOOKUP_FAILED",
            new_category="TARIFF_MISSING",
            new_severity="S1",
            quality_score=60,
            is_resolved=False,
            is_reclassified=False,
        )
        
        now = datetime(2025, 1, 15, 12, 0, 0)
        apply_recompute_result(mock_db, 42, result, now)
        
        assert mock_incident.reclassified_at == now
        assert mock_incident.previous_primary_flag == "MARKET_PRICE_MISSING"
    
    def test_occurrences_preserved(self, mock_db, mock_incident):
        """Occurrence count korunur"""
        mock_incident.occurrence_count = 5
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
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
        
        # occurrence_count değişmemeli
        assert mock_incident.occurrence_count == 5
    
    def test_recompute_count_increments(self, mock_db, mock_incident):
        """Her recompute'da recompute_count artar"""
        mock_incident.recompute_count = 2
        mock_db.query.return_value.filter.return_value.first.return_value = mock_incident
        
        result = RecomputeResult(
            new_all_flags=["MARKET_PRICE_MISSING"],
            new_primary_flag="MARKET_PRICE_MISSING",
            new_category="PRICE_MISSING",
            new_severity="S1",
            quality_score=50,
            is_resolved=False,
            is_reclassified=False,
        )
        
        apply_recompute_result(mock_db, 42, result)
        
        assert mock_incident.recompute_count == 3


class TestCheckResolutionByRecompute:
    """check_resolution_by_recompute testleri"""
    
    def test_resolved_returns_true_false_none(self):
        """Çözüldüyse (True, False, None)"""
        context = RecomputeContext(
            extraction={},
            validation={},
            calculation={"meta_pricing_source": "epias", "meta_distribution_source": "tariff"},
            calculation_error=None,
            debug_meta=None,
        )
        
        is_resolved, is_reclassified, new_primary = check_resolution_by_recompute(
            context, "MARKET_PRICE_MISSING"
        )
        
        assert is_resolved is True
        assert is_reclassified is False
        assert new_primary is None
    
    def test_same_primary_returns_false_false_same(self):
        """Aynı primary → (False, False, same)"""
        context = RecomputeContext(
            extraction={},
            validation={},
            calculation={"meta_pricing_source": "default"},
            calculation_error=None,
            debug_meta=None,
        )
        
        is_resolved, is_reclassified, new_primary = check_resolution_by_recompute(
            context, "MARKET_PRICE_MISSING"
        )
        
        assert is_resolved is False
        assert is_reclassified is False
        assert new_primary == "MARKET_PRICE_MISSING"
    
    def test_different_primary_returns_false_true_new(self):
        """Farklı primary → (False, True, new)"""
        context = RecomputeContext(
            extraction={},
            validation={"is_ready_for_pricing": False, "missing_fields": ["consumption_kwh"]},
            calculation={"meta_pricing_source": "epias", "meta_distribution_source": "tariff"},
            calculation_error=None,
            debug_meta=None,
        )
        
        is_resolved, is_reclassified, new_primary = check_resolution_by_recompute(
            context, "MARKET_PRICE_MISSING"
        )
        
        # CONSUMPTION_MISSING daha yüksek priority
        assert is_resolved is False
        assert is_reclassified is True
        assert new_primary == "CONSUMPTION_MISSING"


class TestIssueIntegrationNotRetriggered:
    """Issue integration yeniden tetiklenmez testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_reclassify_does_not_clear_external_issue_id(self, mock_db):
        """Reclassify external_issue_id'yi silmez"""
        incident = MagicMock()
        incident.id = 42
        incident.primary_flag = "MARKET_PRICE_MISSING"
        incident.external_issue_id = "ISSUE-123"
        incident.external_issue_url = "https://example.com/issues/123"
        incident.recompute_count = 0
        
        mock_db.query.return_value.filter.return_value.first.return_value = incident
        
        result = RecomputeResult(
            new_all_flags=["CALC_BUG"],
            new_primary_flag="CALC_BUG",
            new_category="CALC_BUG",
            new_severity="S1",
            quality_score=50,
            is_resolved=False,
            is_reclassified=False,
        )
        
        apply_recompute_result(mock_db, 42, result)
        
        # external_issue_id korunmalı
        assert incident.external_issue_id == "ISSUE-123"
        assert incident.external_issue_url == "https://example.com/issues/123"


class TestIncidentNotFound:
    """Incident not found testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_returns_false_for_missing_incident(self, mock_db):
        """Incident bulunamazsa False döner"""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        result = RecomputeResult(
            new_all_flags=[],
            new_primary_flag=None,
            new_category=None,
            new_severity=None,
            quality_score=100,
            is_resolved=True,
            is_reclassified=False,
        )
        
        success = apply_recompute_result(mock_db, 999, result)
        
        assert success is False
