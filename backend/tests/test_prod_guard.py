"""
Sprint 4 P2 - Production Guard Tests

Guard logic'ini doğrudan test eder.
"""

import pytest
from backend.app.incident_service import check_production_guard, validate_environment


class TestProductionGuard:
    """Production guard testleri"""
    
    def test_production_without_api_key_enabled_fails(self):
        """ENV=production + API_KEY_ENABLED=false → Fail"""
        success, error = check_production_guard(
            env="production",
            api_key_enabled=False,
            api_key=""
        )
        assert not success
        assert "ADMIN_API_KEY_ENABLED=true" in error
    
    def test_production_with_short_api_key_fails(self):
        """ENV=production + API_KEY kısa → Fail"""
        success, error = check_production_guard(
            env="production",
            api_key_enabled=True,
            api_key="short_key"  # 9 karakter
        )
        assert not success
        assert "32 characters" in error
    
    def test_production_with_empty_api_key_fails(self):
        """ENV=production + API_KEY boş → Fail"""
        success, error = check_production_guard(
            env="production",
            api_key_enabled=True,
            api_key=""
        )
        assert not success
        assert "32 characters" in error
    
    def test_production_with_31_char_api_key_fails(self):
        """ENV=production + API_KEY 31 karakter → Fail"""
        success, error = check_production_guard(
            env="production",
            api_key_enabled=True,
            api_key="a" * 31
        )
        assert not success
        assert "32 characters" in error
    
    def test_production_with_32_char_api_key_succeeds(self):
        """ENV=production + API_KEY 32 karakter → OK"""
        success, error = check_production_guard(
            env="production",
            api_key_enabled=True,
            api_key="a" * 32
        )
        assert success
        assert error == ""
    
    def test_production_with_long_api_key_succeeds(self):
        """ENV=production + API_KEY uzun → OK"""
        success, error = check_production_guard(
            env="production",
            api_key_enabled=True,
            api_key="a" * 64
        )
        assert success
        assert error == ""
    
    def test_development_without_api_key_succeeds(self):
        """ENV=development + API_KEY_ENABLED=false → OK"""
        success, error = check_production_guard(
            env="development",
            api_key_enabled=False,
            api_key=""
        )
        assert success
        assert error == ""
    
    def test_staging_without_api_key_succeeds(self):
        """ENV=staging + API_KEY_ENABLED=false → OK"""
        success, error = check_production_guard(
            env="staging",
            api_key_enabled=False,
            api_key=""
        )
        assert success
        assert error == ""
    
    def test_empty_env_succeeds(self):
        """ENV boş → OK (development gibi davranır)"""
        success, error = check_production_guard(
            env="",
            api_key_enabled=False,
            api_key=""
        )
        assert success
        assert error == ""
    
    def test_invalid_env_fails(self):
        """Geçersiz ENV → Fail"""
        success, error = check_production_guard(
            env="prod",  # Geçersiz
            api_key_enabled=True,
            api_key="a" * 32
        )
        assert not success
        assert "Invalid ENV" in error
    
    def test_typo_env_fails(self):
        """Yazım hatası ENV → Fail"""
        success, error = check_production_guard(
            env="producton",  # Typo
            api_key_enabled=True,
            api_key="a" * 32
        )
        assert not success
        assert "Invalid ENV" in error
