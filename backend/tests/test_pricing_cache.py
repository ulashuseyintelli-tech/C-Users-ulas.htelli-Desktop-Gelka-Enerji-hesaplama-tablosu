"""
Pricing Risk Engine — Cache ve Versiyonlama Testleri.

Task 19.1: Cache yönetimi testleri
Task 19.2: Veri versiyonlama testleri
"""

import pytest
import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
import app.pricing.schemas  # noqa: F401 — tabloları kaydet

from app.pricing.pricing_cache import (
    build_cache_key,
    get_cached_result,
    set_cached_result,
    invalidate_cache_for_customer,
    invalidate_cache_for_period,
    cleanup_expired_cache,
)
from app.pricing.version_manager import (
    archive_and_create_version,
    list_versions,
    get_active_version,
)
from app.pricing.schemas import AnalysisCache


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def db_session():
    """In-memory SQLite session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Task 19.1: Cache Testleri
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildCacheKey:
    """build_cache_key() testleri."""

    def test_deterministic(self):
        """Aynı parametreler → aynı key."""
        params = dict(
            customer_id="CUST-001", period="2025-01",
            multiplier=1.05, dealer_commission_pct=2.0,
            imbalance_params={"forecast_error_rate": 0.05, "smf_based_imbalance_enabled": False},
        )
        key1 = build_cache_key(**params)
        key2 = build_cache_key(**params)
        assert key1 == key2

    def test_different_multiplier_different_key(self):
        """Farklı katsayı → farklı key."""
        base = dict(
            customer_id="CUST-001", period="2025-01",
            dealer_commission_pct=2.0,
            imbalance_params={"forecast_error_rate": 0.05},
        )
        key1 = build_cache_key(multiplier=1.05, **base)
        key2 = build_cache_key(multiplier=1.06, **base)
        assert key1 != key2

    def test_different_customer_different_key(self):
        """Farklı müşteri → farklı key."""
        base = dict(
            period="2025-01", multiplier=1.05,
            dealer_commission_pct=2.0,
            imbalance_params={"forecast_error_rate": 0.05},
        )
        key1 = build_cache_key(customer_id="CUST-001", **base)
        key2 = build_cache_key(customer_id="CUST-002", **base)
        assert key1 != key2

    def test_different_period_different_key(self):
        """Farklı dönem → farklı key."""
        base = dict(
            customer_id="CUST-001", multiplier=1.05,
            dealer_commission_pct=2.0,
            imbalance_params={"forecast_error_rate": 0.05},
        )
        key1 = build_cache_key(period="2025-01", **base)
        key2 = build_cache_key(period="2025-02", **base)
        assert key1 != key2

    def test_different_dealer_commission_different_key(self):
        """Farklı bayi komisyonu → farklı key."""
        base = dict(
            customer_id="CUST-001", period="2025-01", multiplier=1.05,
            imbalance_params={"forecast_error_rate": 0.05},
        )
        key1 = build_cache_key(dealer_commission_pct=0.0, **base)
        key2 = build_cache_key(dealer_commission_pct=5.0, **base)
        assert key1 != key2

    def test_template_vs_customer_different_key(self):
        """Şablon vs gerçek profil → farklı key."""
        base = dict(
            period="2025-01", multiplier=1.05,
            dealer_commission_pct=0.0,
            imbalance_params={"forecast_error_rate": 0.05},
        )
        key1 = build_cache_key(customer_id="CUST-001", **base)
        key2 = build_cache_key(customer_id=None, template_name="ofis",
                               template_monthly_kwh=50000.0, **base)
        assert key1 != key2

    def test_sha256_format(self):
        """Key 64 karakter SHA256 hash."""
        key = build_cache_key(
            customer_id="X", period="2025-01", multiplier=1.0,
            dealer_commission_pct=0.0, imbalance_params={},
        )
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


class TestCacheOperations:
    """get/set/invalidate cache testleri."""

    def test_miss_returns_none(self, db_session):
        """Cache miss → None."""
        result = get_cached_result(db_session, "nonexistent_key")
        assert result is None

    def test_set_then_get(self, db_session):
        """Set → Get → aynı veri."""
        key = "test_key_001"
        data = {"period": "2025-01", "weighted_ptf": 2500.0}

        set_cached_result(db_session, key, "CUST-001", "2025-01", key, data)
        result = get_cached_result(db_session, key)

        assert result is not None
        assert result["period"] == "2025-01"
        assert result["weighted_ptf"] == 2500.0

    def test_hit_count_increments(self, db_session):
        """Her get → hit_count artır."""
        key = "test_key_002"
        set_cached_result(db_session, key, "CUST-001", "2025-01", key, {"x": 1})

        get_cached_result(db_session, key)
        get_cached_result(db_session, key)
        get_cached_result(db_session, key)

        record = db_session.query(AnalysisCache).filter_by(cache_key=key).first()
        assert record.hit_count == 3

    def test_expired_returns_none(self, db_session):
        """TTL süresi dolmuş → None + kayıt silinir."""
        key = "test_key_expired"
        set_cached_result(db_session, key, "CUST-001", "2025-01", key, {"x": 1})

        # Expire time'ı geçmişe çek
        record = db_session.query(AnalysisCache).filter_by(cache_key=key).first()
        record.expires_at = datetime.utcnow() - timedelta(hours=1)
        db_session.commit()

        result = get_cached_result(db_session, key)
        assert result is None

        # Kayıt silinmiş olmalı
        remaining = db_session.query(AnalysisCache).filter_by(cache_key=key).first()
        assert remaining is None

    def test_overwrite_existing(self, db_session):
        """Aynı key ile tekrar set → güncelle."""
        key = "test_key_overwrite"
        set_cached_result(db_session, key, "CUST-001", "2025-01", key, {"v": 1})
        set_cached_result(db_session, key, "CUST-001", "2025-01", key, {"v": 2})

        result = get_cached_result(db_session, key)
        assert result["v"] == 2

    def test_invalidate_customer(self, db_session):
        """Müşteri cache invalidation."""
        set_cached_result(db_session, "k1", "CUST-001", "2025-01", "k1", {"a": 1})
        set_cached_result(db_session, "k2", "CUST-001", "2025-02", "k2", {"b": 2})
        set_cached_result(db_session, "k3", "CUST-002", "2025-01", "k3", {"c": 3})

        deleted = invalidate_cache_for_customer(db_session, "CUST-001")
        assert deleted == 2

        # CUST-001 cache silinmiş
        assert get_cached_result(db_session, "k1") is None
        assert get_cached_result(db_session, "k2") is None
        # CUST-002 cache duruyor
        assert get_cached_result(db_session, "k3") is not None

    def test_invalidate_period(self, db_session):
        """Dönem cache invalidation."""
        set_cached_result(db_session, "k1", "CUST-001", "2025-01", "k1", {"a": 1})
        set_cached_result(db_session, "k2", "CUST-002", "2025-01", "k2", {"b": 2})
        set_cached_result(db_session, "k3", "CUST-001", "2025-02", "k3", {"c": 3})

        deleted = invalidate_cache_for_period(db_session, "2025-01")
        assert deleted == 2

        assert get_cached_result(db_session, "k1") is None
        assert get_cached_result(db_session, "k2") is None
        assert get_cached_result(db_session, "k3") is not None

    def test_cleanup_expired(self, db_session):
        """Süresi dolmuş kayıtları temizle."""
        set_cached_result(db_session, "fresh", "C1", "2025-01", "fresh", {"x": 1})
        set_cached_result(db_session, "stale", "C2", "2025-01", "stale", {"x": 2})

        # stale'i expire et
        record = db_session.query(AnalysisCache).filter_by(cache_key="stale").first()
        record.expires_at = datetime.utcnow() - timedelta(hours=1)
        db_session.commit()

        deleted = cleanup_expired_cache(db_session)
        assert deleted == 1

        assert get_cached_result(db_session, "fresh") is not None
        assert get_cached_result(db_session, "stale") is None


# ═══════════════════════════════════════════════════════════════════════════════
# Task 19.2: Versiyonlama Testleri
# ═══════════════════════════════════════════════════════════════════════════════

class TestVersionManager:
    """archive_and_create_version() testleri."""

    def test_first_version(self, db_session):
        """İlk yükleme → versiyon 1."""
        dv = archive_and_create_version(
            db_session, "market_data", "2025-01", None, 744,
        )
        assert dv.version == 1
        assert dv.is_active == 1

    def test_second_version_archives_first(self, db_session):
        """İkinci yükleme → v1 arşivlenir, v2 aktif."""
        dv1 = archive_and_create_version(
            db_session, "market_data", "2025-01", None, 744,
        )
        dv2 = archive_and_create_version(
            db_session, "market_data", "2025-01", None, 744,
        )

        assert dv2.version == 2
        assert dv2.is_active == 1

        db_session.refresh(dv1)
        assert dv1.is_active == 0

    def test_different_periods_independent(self, db_session):
        """Farklı dönemler bağımsız versiyonlanır."""
        dv_jan = archive_and_create_version(
            db_session, "market_data", "2025-01", None, 744,
        )
        dv_feb = archive_and_create_version(
            db_session, "market_data", "2025-02", None, 672,
        )

        assert dv_jan.version == 1
        assert dv_feb.version == 1
        assert dv_jan.is_active == 1
        assert dv_feb.is_active == 1

    def test_list_versions(self, db_session):
        """Versiyon geçmişi listele."""
        archive_and_create_version(db_session, "market_data", "2025-01", None, 744)
        archive_and_create_version(db_session, "market_data", "2025-01", None, 744)
        archive_and_create_version(db_session, "market_data", "2025-01", None, 744)

        versions = list_versions(db_session, "market_data", "2025-01")
        assert len(versions) == 3
        # Azalan sırada
        assert versions[0].version == 3
        assert versions[2].version == 1

    def test_get_active_version(self, db_session):
        """Aktif versiyon sorgula."""
        archive_and_create_version(db_session, "market_data", "2025-01", None, 744)
        archive_and_create_version(db_session, "market_data", "2025-01", None, 744)

        active = get_active_version(db_session, "market_data", "2025-01")
        assert active is not None
        assert active.version == 2
        assert active.is_active == 1

    def test_no_active_version(self, db_session):
        """Veri yoksa None."""
        active = get_active_version(db_session, "market_data", "2099-01")
        assert active is None

    def test_quality_score_and_filename(self, db_session):
        """Kalite skoru ve dosya adı kaydedilir."""
        dv = archive_and_create_version(
            db_session, "market_data", "2025-01", None, 744,
            quality_score=95, filename="epias_ocak.xlsx",
        )
        assert dv.quality_score == 95
        assert dv.upload_filename == "epias_ocak.xlsx"
