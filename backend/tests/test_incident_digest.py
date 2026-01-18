"""
Incident Digest Tests - Sprint 8.2

Daily digest ve alert testleri.
"""

import pytest
from datetime import datetime, date, timezone
from unittest.mock import MagicMock, patch

from backend.app.incident_digest import (
    DailyDigest,
    AlertConfig,
    DEFAULT_ALERT_CONFIG,
    generate_alerts,
    generate_daily_digest,
)
from backend.app.incident_metrics import IncidentMetrics, RetryFunnel


class TestDailyDigest:
    """DailyDigest dataclass testleri"""
    
    def test_to_dict(self):
        """to_dict doğru yapıda dict döner"""
        metrics = IncidentMetrics(
            total_incidents=100,
            new_today=10,
            resolved_today=5,
            by_status={"OPEN": 50, "RESOLVED": 50},
            retry_funnel=RetryFunnel(
                attempts_total=20,
                attempts_success=15,
                resolved_after_retry=10,
                still_pending=5,
                exhausted=3,
            ),
            top_primary_flags=[("CALC_BUG", 15)],
            top_action_codes=[("ENGINE_REGRESSION", 10)],
            top_providers=[("ck_bogazici", 20)],
            mttr_minutes=90.5,
        )
        
        digest = DailyDigest(
            date=date(2025, 1, 15),
            tenant_id="default",
            metrics=metrics,
        )
        
        result = digest.to_dict()
        
        assert result["date"] == "2025-01-15"
        assert result["tenant_id"] == "default"
        assert result["summary"]["total_incidents"] == 100
        assert result["retry_funnel"]["attempts_total"] == 20
        assert result["top_primary_flags"][0]["flag"] == "CALC_BUG"
    
    def test_to_markdown(self):
        """to_markdown markdown formatında string döner"""
        metrics = IncidentMetrics(
            total_incidents=100,
            new_today=10,
            resolved_today=5,
            by_status={"OPEN": 50},
            retry_funnel=RetryFunnel(),
            top_primary_flags=[("CALC_BUG", 15)],
        )
        
        digest = DailyDigest(
            date=date(2025, 1, 15),
            tenant_id="default",
            metrics=metrics,
        )
        
        result = digest.to_markdown()
        
        assert "# Daily Incident Digest" in result
        assert "2025-01-15" in result
        assert "Total Incidents" in result
        assert "CALC_BUG" in result


class TestGenerateAlerts:
    """generate_alerts testleri"""
    
    def test_stuck_pending_alert(self):
        """Stuck pending recompute alert"""
        metrics = IncidentMetrics(
            stuck_pending_recompute_count=3,
            retry_funnel=RetryFunnel(),
        )
        
        config = AlertConfig(enabled=True, stuck_count_threshold=1)
        alerts = generate_alerts(metrics, config)
        
        assert len(alerts) == 1
        assert "stuck" in alerts[0].lower()
    
    def test_recompute_limit_alert(self):
        """Recompute limit exceeded alert"""
        metrics = IncidentMetrics(
            recompute_limit_exceeded_count=2,
            retry_funnel=RetryFunnel(),
        )
        
        config = AlertConfig(enabled=True, recompute_limit_threshold=1)
        alerts = generate_alerts(metrics, config)
        
        assert len(alerts) == 1
        assert "recompute limit" in alerts[0].lower()
    
    def test_alerts_disabled_by_default(self):
        """Default config'te alert'ler kapalı"""
        metrics = IncidentMetrics(
            stuck_pending_recompute_count=10,
            recompute_limit_exceeded_count=5,
            retry_funnel=RetryFunnel(attempts_total=100, exhausted=50),
        )
        
        # Default config: enabled=False
        alerts = generate_alerts(metrics, DEFAULT_ALERT_CONFIG)
        
        # Alert üretilmez (sadece log)
        assert len(alerts) == 0
    
    def test_high_exhausted_rate_alert(self):
        """High exhausted rate alert"""
        metrics = IncidentMetrics(
            retry_funnel=RetryFunnel(
                attempts_total=100,
                exhausted=30,  # %30 > %20 threshold
            ),
        )
        
        config = AlertConfig(enabled=True, exhausted_rate_threshold=0.20)
        alerts = generate_alerts(metrics, config)
        
        assert len(alerts) == 1
        assert "exhausted" in alerts[0].lower()


class TestGenerateDailyDigest:
    """generate_daily_digest testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_generates_complete_digest(self, mock_db):
        """Tam digest üretir"""
        with patch('backend.app.incident_digest.get_daily_counts') as mock_daily:
            mock_daily.return_value = {"total": 100, "new": 10, "resolved": 5}
            
            with patch('backend.app.incident_digest.get_status_distribution') as mock_status:
                mock_status.return_value = {"OPEN": 50, "RESOLVED": 50}
                
                with patch('backend.app.incident_digest.get_retry_funnel') as mock_funnel:
                    mock_funnel.return_value = RetryFunnel()
                    
                    with patch('backend.app.incident_digest.get_recompute_limit_exceeded_count') as mock_limit:
                        mock_limit.return_value = 0
                        
                        with patch('backend.app.incident_digest.get_stuck_pending_recompute_count') as mock_stuck:
                            mock_stuck.return_value = 0
                            
                            with patch('backend.app.incident_digest.get_reclassified_count') as mock_reclass:
                                mock_reclass.return_value = 0
                                
                                with patch('backend.app.incident_digest.get_top_primary_flags') as mock_flags:
                                    mock_flags.return_value = [("CALC_BUG", 15)]
                                    
                                    with patch('backend.app.incident_digest.get_top_action_codes') as mock_codes:
                                        mock_codes.return_value = []
                                        
                                        with patch('backend.app.incident_digest.get_top_providers') as mock_providers:
                                            mock_providers.return_value = []
                                            
                                            with patch('backend.app.incident_digest.get_mttr') as mock_mttr:
                                                mock_mttr.return_value = 90.0
                                                
                                                digest = generate_daily_digest(
                                                    mock_db,
                                                    "default",
                                                    date(2025, 1, 15),
                                                )
        
        assert digest.date == date(2025, 1, 15)
        assert digest.tenant_id == "default"
        assert digest.metrics.total_incidents == 100
        assert digest.metrics.new_today == 10
        assert digest.metrics.mttr_minutes == 90.0
    
    def test_includes_alerts(self, mock_db):
        """Alert'leri içerir"""
        with patch('backend.app.incident_digest.get_daily_counts') as mock_daily:
            mock_daily.return_value = {"total": 100, "new": 10, "resolved": 5}
            
            with patch('backend.app.incident_digest.get_status_distribution') as mock_status:
                mock_status.return_value = {}
                
                with patch('backend.app.incident_digest.get_retry_funnel') as mock_funnel:
                    mock_funnel.return_value = RetryFunnel()
                    
                    with patch('backend.app.incident_digest.get_recompute_limit_exceeded_count') as mock_limit:
                        mock_limit.return_value = 0
                        
                        with patch('backend.app.incident_digest.get_stuck_pending_recompute_count') as mock_stuck:
                            mock_stuck.return_value = 5  # Stuck var
                            
                            with patch('backend.app.incident_digest.get_reclassified_count') as mock_reclass:
                                mock_reclass.return_value = 0
                                
                                with patch('backend.app.incident_digest.get_top_primary_flags') as mock_flags:
                                    mock_flags.return_value = []
                                    
                                    with patch('backend.app.incident_digest.get_top_action_codes') as mock_codes:
                                        mock_codes.return_value = []
                                        
                                        with patch('backend.app.incident_digest.get_top_providers') as mock_providers:
                                            mock_providers.return_value = []
                                            
                                            with patch('backend.app.incident_digest.get_mttr') as mock_mttr:
                                                mock_mttr.return_value = None
                                                
                                                # Alert'ler açık
                                                config = AlertConfig(enabled=True)
                                                digest = generate_daily_digest(
                                                    mock_db,
                                                    "default",
                                                    date(2025, 1, 15),
                                                    alert_config=config,
                                                )
        
        assert digest.metrics.stuck_pending_recompute_count == 5
        assert len(digest.metrics.alerts) > 0


class TestAlertConfig:
    """AlertConfig testleri"""
    
    def test_default_config_disabled(self):
        """Default config disabled"""
        assert DEFAULT_ALERT_CONFIG.enabled is False
    
    def test_default_thresholds(self):
        """Default threshold'lar"""
        assert DEFAULT_ALERT_CONFIG.bug_report_rate_threshold == 0.10
        assert DEFAULT_ALERT_CONFIG.exhausted_rate_threshold == 0.20
        assert DEFAULT_ALERT_CONFIG.stuck_count_threshold == 1
        assert DEFAULT_ALERT_CONFIG.recompute_limit_threshold == 1
