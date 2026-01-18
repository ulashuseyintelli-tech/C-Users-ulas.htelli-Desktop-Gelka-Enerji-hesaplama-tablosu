"""
Tests for Pilot Guard - Sprint 8.9.1

Tests:
- PILOT_ENABLED toggle
- PILOT_TENANT_ID matching
- Rate limiting
- Startup logging
"""

import pytest
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone

# Import from app module (relative to backend/)
from app.pilot_guard import (
    is_pilot_enabled,
    is_pilot_tenant,
    get_pilot_tenant_id,
    check_pilot_rate_limit,
    get_pilot_rate_status,
    reset_pilot_rate_limit,
    log_pilot_config,
    pilot_guard,
    PilotRateLimitExceeded,
)
import app.pilot_guard as pg


class TestPilotEnabled:
    """Test PILOT_ENABLED kill switch."""
    
    def test_pilot_enabled_default_true(self):
        """Default should be enabled."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PILOT_ENABLED", None)
            assert is_pilot_enabled() is True
    
    def test_pilot_enabled_explicit_true(self):
        """PILOT_ENABLED=true should enable."""
        with patch.dict(os.environ, {"PILOT_ENABLED": "true"}):
            assert is_pilot_enabled() is True
    
    def test_pilot_enabled_explicit_false(self):
        """PILOT_ENABLED=false should disable."""
        with patch.dict(os.environ, {"PILOT_ENABLED": "false"}):
            assert is_pilot_enabled() is False
    
    def test_pilot_enabled_case_insensitive(self):
        """PILOT_ENABLED should be case insensitive."""
        with patch.dict(os.environ, {"PILOT_ENABLED": "FALSE"}):
            assert is_pilot_enabled() is False
        
        with patch.dict(os.environ, {"PILOT_ENABLED": "True"}):
            assert is_pilot_enabled() is True


class TestPilotTenant:
    """Test pilot tenant identification."""
    
    def test_is_pilot_tenant_default(self):
        """Default pilot tenant should be 'pilot'."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PILOT_TENANT_ID", None)
            
            assert get_pilot_tenant_id() == "pilot"
            assert is_pilot_tenant("pilot") is True
            assert is_pilot_tenant("other") is False
    
    def test_is_pilot_tenant_custom(self):
        """Custom PILOT_TENANT_ID should work."""
        with patch.dict(os.environ, {"PILOT_TENANT_ID": "test-tenant"}):
            assert get_pilot_tenant_id() == "test-tenant"
            assert is_pilot_tenant("test-tenant") is True
            assert is_pilot_tenant("pilot") is False
    
    def test_is_pilot_tenant_none(self):
        """None tenant_id should return False."""
        assert is_pilot_tenant(None) is False


class TestPilotRateLimit:
    """Test pilot rate limiting."""
    
    def setup_method(self):
        """Reset rate limit before each test."""
        reset_pilot_rate_limit()
    
    def test_rate_limit_under_limit(self):
        """Should not raise when under limit."""
        with patch.dict(os.environ, {"PILOT_MAX_INVOICES_PER_HOUR": "10"}):
            # 5 requests should be fine
            for _ in range(5):
                check_pilot_rate_limit()
            
            status = get_pilot_rate_status()
            assert status["current"] == 5
            assert status["remaining"] == 5
    
    def test_rate_limit_at_limit(self):
        """Should raise when at limit."""
        reset_pilot_rate_limit()
        
        with patch.dict(os.environ, {"PILOT_MAX_INVOICES_PER_HOUR": "3"}):
            # 3 requests OK
            check_pilot_rate_limit()
            check_pilot_rate_limit()
            check_pilot_rate_limit()
            
            # 4th should fail
            with pytest.raises(PilotRateLimitExceeded) as exc_info:
                check_pilot_rate_limit()
            
            assert exc_info.value.limit == 3
    
    def test_rate_limit_window_expiry(self):
        """Old timestamps should be cleaned."""
        reset_pilot_rate_limit()
        
        with patch.dict(os.environ, {"PILOT_MAX_INVOICES_PER_HOUR": "2"}):
            # Add old timestamp (2 hours ago)
            old_time = datetime.now(timezone.utc) - timedelta(hours=2)
            pg._pilot_invoice_timestamps.append(old_time)
            
            # Should clean old and allow new
            check_pilot_rate_limit()
            
            status = get_pilot_rate_status()
            assert status["current"] == 1  # Old one cleaned
    
    def test_rate_status_structure(self):
        """Rate status should have expected structure."""
        reset_pilot_rate_limit()
        
        status = get_pilot_rate_status()
        
        assert "current" in status
        assert "limit" in status
        assert "remaining" in status
        assert "window_seconds" in status
        assert status["window_seconds"] == 3600


class TestPilotGuardDecorator:
    """Test @pilot_guard decorator."""
    
    def test_decorator_enabled(self):
        """Decorated function should run when enabled."""
        @pilot_guard
        def my_func():
            return "executed"
        
        with patch.dict(os.environ, {"PILOT_ENABLED": "true"}):
            result = my_func()
            assert result == "executed"
    
    def test_decorator_disabled(self):
        """Decorated function should skip when disabled."""
        @pilot_guard
        def my_func():
            return "executed"
        
        with patch.dict(os.environ, {"PILOT_ENABLED": "false"}):
            result = my_func()
            assert result is None


class TestPilotStartupLog:
    """Test startup logging."""
    
    def test_log_pilot_config_enabled(self, caplog):
        """Should log enabled config."""
        import logging
        
        with patch.dict(os.environ, {
            "PILOT_ENABLED": "true",
            "PILOT_TENANT_ID": "test-pilot",
            "PILOT_MAX_INVOICES_PER_HOUR": "100"
        }):
            with caplog.at_level(logging.INFO):
                log_pilot_config()
            
            assert "ENABLED" in caplog.text
            assert "test-pilot" in caplog.text
            assert "100" in caplog.text
    
    def test_log_pilot_config_disabled(self, caplog):
        """Should log disabled warning."""
        import logging
        
        with patch.dict(os.environ, {"PILOT_ENABLED": "false"}):
            with caplog.at_level(logging.WARNING):
                log_pilot_config()
            
            assert "DISABLED" in caplog.text
