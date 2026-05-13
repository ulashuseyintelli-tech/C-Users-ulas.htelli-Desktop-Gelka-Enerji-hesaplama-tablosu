"""
Tests for YEKDEM hard-block — P0 financial safety fix.

Contract:
- YEKDEM missing for period → 409 yekdem_data_not_found (hard failure)
- YEKDEM present → analyze proceeds normally (200)
- No fallback, no approximation, no warning-only mode

Feature: P0 #7 — YEKDEM hard-block
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db

# Register pricing schemas with Base.metadata
import app.pricing.schemas  # noqa: F401
from app.pricing.schemas import HourlyMarketPrice, MonthlyYekdemPrice


@pytest.fixture()
def db_session():
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


def _seed_market_data(session, period="2026-03"):
    """Seed canonical hourly PTF data for the period."""
    from datetime import date as date_type
    for day in range(1, 4):  # 3 days enough for test
        d = date_type(int(period[:4]), int(period[5:7]), day)
        for hour in range(24):
            session.add(HourlyMarketPrice(
                period=period, date=d.isoformat(), hour=hour,
                ptf_tl_per_mwh=2500.0, smf_tl_per_mwh=2600.0,
                currency="TRY", source="test", version=1, is_active=1,
            ))
    session.commit()


def _seed_yekdem(session, period="2026-03", value=747.8):
    """Seed YEKDEM for the period."""
    session.add(MonthlyYekdemPrice(
        period=period, yekdem_tl_per_mwh=value, source="test",
    ))
    session.commit()


def _make_client(session):
    """Create TestClient with DB override."""
    with patch.dict(os.environ, {
        "ADMIN_API_KEY_ENABLED": "false",
        "API_KEY_ENABLED": "false",
    }, clear=False):
        os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
        os.environ.pop("USE_LEGACY_PTF", None)

        import app.guard_config as gc_mod
        gc_mod._guard_config = None

        from app.main import app
        app.dependency_overrides[get_db] = lambda: session
        client = TestClient(app)
        yield client
        app.dependency_overrides.clear()
        gc_mod._guard_config = None


ANALYZE_BODY = {
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
}


class TestYekdemHardBlock:
    """P0 #7: Missing YEKDEM = hard 409, not warning-only."""

    def test_missing_yekdem_returns_409(self, db_session):
        """YEKDEM missing → 409 yekdem_data_not_found. No approximate calculation."""
        _seed_market_data(db_session, "2026-03")
        # Deliberately NOT seeding YEKDEM

        for client in _make_client(db_session):
            resp = client.post("/api/pricing/analyze", json=ANALYZE_BODY)

        assert resp.status_code == 409
        body = resp.json()
        assert body["detail"]["error"] == "yekdem_data_not_found"
        assert "2026-03" in body["detail"]["message"]
        assert "period" in body["detail"]

    def test_existing_yekdem_returns_200(self, db_session):
        """YEKDEM present → analyze proceeds normally."""
        _seed_market_data(db_session, "2026-03")
        _seed_yekdem(db_session, "2026-03", 747.8)

        for client in _make_client(db_session):
            resp = client.post("/api/pricing/analyze", json=ANALYZE_BODY)

        assert resp.status_code == 200
        body = resp.json()
        # Verify YEKDEM was actually used (not zero)
        assert body.get("supplier_cost", {}).get("yekdem_tl_per_mwh", 0) > 0

    def test_warning_only_behavior_removed(self, db_session):
        """Regression: old warning-only behavior must NOT exist.

        Previously the system returned 200 with yekdem=0 and a warning.
        That behavior is now a financial safety violation.
        """
        _seed_market_data(db_session, "2026-03")
        # No YEKDEM seeded

        for client in _make_client(db_session):
            resp = client.post("/api/pricing/analyze", json=ANALYZE_BODY)

        # Must NOT be 200 with yekdem=0
        assert resp.status_code != 200, (
            "System returned 200 without YEKDEM — warning-only behavior still active. "
            "This is a financial safety violation."
        )
