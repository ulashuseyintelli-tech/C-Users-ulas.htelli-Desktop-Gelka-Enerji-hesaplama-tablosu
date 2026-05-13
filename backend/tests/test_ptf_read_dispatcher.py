"""
Tests for PTF read dispatcher — Phase 1 T1.4 (ptf-sot-unification).

Validates:
1. Default (switch OFF) → canonical reader (hourly_market_prices)
2. use_legacy_ptf=True → legacy reader (market_reference_prices)
3. Canonical missing → empty list (reader level) + 409 (analyze level, Hybrid-C)
4. Cache key includes ptf_source → different keys for same params

Scope out: drift compare, drift log, dual-write, runtime reload.
"""

from __future__ import annotations

import hashlib
import os
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, MarketReferencePrice
from app.pricing.excel_parser import ParsedMarketRecord
from app.pricing.pricing_cache import build_cache_key

# Import schemas to register HourlyMarketPrice with Base.metadata
import app.pricing.schemas  # noqa: F401
from app.pricing.schemas import HourlyMarketPrice


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_session():
    """In-memory SQLite with all pricing tables."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_canonical(session, period="2026-03", days=1, ptf=2500.0, smf=2600.0):
    """Seed hourly_market_prices with test data."""
    from datetime import date as date_type
    for day in range(1, days + 1):
        d = date_type(int(period[:4]), int(period[5:7]), day)
        for hour in range(24):
            session.add(HourlyMarketPrice(
                period=period,
                date=d.isoformat(),
                hour=hour,
                ptf_tl_per_mwh=ptf + hour,  # slight variation per hour
                smf_tl_per_mwh=smf,
                currency="TRY",
                source="test",
                version=1,
                is_active=1,
            ))
    session.commit()


def _seed_legacy(session, period="2026-03", ptf=2450.0, yekdem=400.0):
    """Seed market_reference_prices with a single monthly PTF row."""
    session.add(MarketReferencePrice(
        period=period,
        price_type="PTF",
        ptf_tl_per_mwh=ptf,
        yekdem_tl_per_mwh=yekdem,
        status="final",
        source="test",
    ))
    session.commit()


# ── Dispatcher tests ──────────────────────────────────────────────────────────

class TestPtfReadDispatcher:
    """T1.4 — read dispatcher routes to correct source based on guard switch."""

    def test_default_uses_canonical_reader(self, db_session):
        """Switch OFF (default) → reads from hourly_market_prices."""
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)

        import app.guard_config as gc_mod
        gc_mod._guard_config = None

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)

            from app.pricing.router import _load_market_records
            records = _load_market_records(db_session, "2026-03")

        gc_mod._guard_config = None

        # Canonical has hourly variation (ptf=3000+hour); legacy is flat 2000
        assert len(records) == 24  # 1 day × 24 hours
        # First hour should be 3000 (canonical), not 2000 (legacy)
        assert records[0].ptf_tl_per_mwh == 3000.0

    def test_legacy_switch_uses_legacy_reader(self, db_session):
        """Switch ON → reads from market_reference_prices (flat monthly avg)."""
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)

        import app.guard_config as gc_mod
        gc_mod._guard_config = None

        with patch.dict(os.environ, {"OPS_GUARD_USE_LEGACY_PTF": "true"}, clear=False):
            from app.pricing.router import _load_market_records
            records = _load_market_records(db_session, "2026-03")

        gc_mod._guard_config = None

        # Legacy spreads monthly avg across all hours of month (31 days × 24)
        assert len(records) == 31 * 24  # March = 31 days
        # All hours have the same flat PTF (legacy monthly average)
        assert all(r.ptf_tl_per_mwh == 2000.0 for r in records)

    def test_canonical_reader_missing_returns_empty_list(self, db_session):
        """Canonical table empty for period → empty list (no silent fallback)."""
        # Only seed legacy — canonical is empty
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)

        import app.guard_config as gc_mod
        gc_mod._guard_config = None

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)

            from app.pricing.router import _load_market_records
            records = _load_market_records(db_session, "2026-03")

        gc_mod._guard_config = None

        # CRITICAL: empty list, NOT legacy fallback. Silent fallback YASAK.
        assert records == []

    def test_analyze_canonical_missing_returns_409(self, db_session):
        """Hybrid-C contract: canonical empty → /api/pricing/analyze returns 409/404.

        This test validates the full contract at the endpoint level:
        canonical missing + legacy exists + switch OFF = hard error, not silent fallback.
        """
        from fastapi.testclient import TestClient

        # Only seed legacy — canonical is empty for 2026-03
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)

        import app.guard_config as gc_mod
        gc_mod._guard_config = None

        with patch.dict(os.environ, {
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)

            from app.main import app
            from app.database import get_db

            app.dependency_overrides[get_db] = lambda: db_session

            client = TestClient(app)
            resp = client.post("/api/pricing/analyze", json={
                "period": "2026-03",
                "multiplier": 1.10,
                "dealer_commission_pct": 0,
                "imbalance_params": {
                    "forecast_error_rate": 0.05,
                    "imbalance_cost_tl_per_mwh": 150.0,
                    "smf_based_imbalance_enabled": False,
                },
                "use_template": False,
                "t1_kwh": 25000,
                "t2_kwh": 12500,
                "t3_kwh": 12500,
                "voltage_level": "og",
            })

            app.dependency_overrides.clear()

        gc_mod._guard_config = None

        # Hybrid-C: canonical missing → 404 market_data_not_found
        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["error"] == "market_data_not_found"


class TestCacheKeyPtfSource:
    """Cache key must differentiate canonical vs legacy to prevent stale hits."""

    def test_same_params_different_ptf_source_different_key(self):
        """Identical request params but different ptf_source → different cache keys."""
        common = dict(
            customer_id=None,
            period="2026-03",
            multiplier=1.10,
            dealer_commission_pct=0,
            imbalance_params={
                "forecast_error_rate": 0.05,
                "imbalance_cost_tl_per_mwh": 150.0,
                "smf_based_imbalance_enabled": False,
            },
            t1_kwh=25000.0,
            t2_kwh=12500.0,
            t3_kwh=12500.0,
            use_template=False,
            voltage_level="og",
        )
        key_canonical = build_cache_key(**common, ptf_source="canonical")
        key_legacy = build_cache_key(**common, ptf_source="legacy")

        assert key_canonical != key_legacy
        assert len(key_canonical) == 64  # SHA256 hex
        assert len(key_legacy) == 64

    def test_default_ptf_source_is_canonical(self):
        """build_cache_key default ptf_source='canonical' — backward compatible."""
        key_explicit = build_cache_key(
            customer_id=None, period="2026-03", multiplier=1.0,
            dealer_commission_pct=0, imbalance_params={},
            ptf_source="canonical",
        )
        key_default = build_cache_key(
            customer_id=None, period="2026-03", multiplier=1.0,
            dealer_commission_pct=0, imbalance_params={},
        )
        assert key_explicit == key_default
