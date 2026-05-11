"""
Risk Analysis DB Parameter Fix — Bug Condition Exploration & Regression Tests.

Bug: `_get_or_generate_consumption()` in router.py calls
     `generate_hourly_consumption(template_name, template_monthly_kwh, period)`
     with only 3 args — the callee requires 4: (template_name, total_monthly_kwh, period, db).
     Missing `db: Session` causes TypeError at runtime.

Validates: Requirements 1.1, 1.2, 2.1, 2.2
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
import app.pricing.schemas  # noqa: F401  — register pricing tables with Base
from app.pricing.schemas import ProfileTemplate, ConsumptionProfile, ConsumptionHourlyData
from app.pricing.profile_templates import _normalize
from app.pricing.router import _get_or_generate_consumption
from app.pricing.excel_parser import ParsedConsumptionRecord
from fastapi import HTTPException


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def db_session():
    """In-memory SQLite session with profile_templates table seeded."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Seed the "3_vardiya_sanayi" template — flat 24h profile (all weights equal)
    weights = _normalize([1.0] * 24)
    template = ProfileTemplate(
        name="3_vardiya_sanayi",
        display_name="3 Vardiya Sanayi",
        description="7/24 kesintisiz üretim — düz profil",
        hourly_weights=json.dumps(weights),
        is_builtin=1,
    )
    session.add(template)
    session.commit()

    yield session
    session.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Task 1 — Bug Condition Exploration Test
# ═══════════════════════════════════════════════════════════════════════════════


class TestBugConditionExploration:
    """
    Property 1: Bug Condition — Template consumption generation receives all
    required arguments.

    Scoped to the concrete failing case:
      use_template=True, template_name="3_vardiya_sanayi",
      template_monthly_kwh=100000, period="2026-01"

    **Validates: Requirements 1.1, 1.2, 2.1, 2.2**

    On UNFIXED code this test MUST FAIL with TypeError (missing `db` argument).
    After the fix it should PASS.
    """

    def test_template_consumption_returns_correct_records(self, db_session):
        """
        Call _get_or_generate_consumption with template params.

        Expected (after fix):
          - Returns list[ParsedConsumptionRecord] with 744 items (31 days × 24h)
          - Total consumption ≈ 100000 kWh

        On unfixed code:
          - Raises TypeError: generate_hourly_consumption() missing 1 required
            positional argument: 'db'
        """
        result = _get_or_generate_consumption(
            db=db_session,
            period="2026-01",
            customer_id=None,
            use_template=True,
            template_name="3_vardiya_sanayi",
            template_monthly_kwh=100000.0,
        )

        # Must be a list of ParsedConsumptionRecord
        assert isinstance(result, list)
        assert all(isinstance(r, ParsedConsumptionRecord) for r in result)

        # January 2026 has 31 days → 31 × 24 = 744 hourly records
        assert len(result) == 744

        # Total consumption should approximate the requested 100 000 kWh
        total_kwh = sum(r.consumption_kwh for r in result)
        assert total_kwh == pytest.approx(100000.0, abs=1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 2 — Preservation Property Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPreservation:
    """
    Property 2: Preservation — Non-template code paths unchanged.

    These tests verify behavior that must NOT change after the fix.
    They MUST PASS on the current unfixed code.

    **Validates: Requirements 3.1, 3.2**
    """

    def test_no_customer_records_raises_422(self, db_session):
        """
        When use_template=False and no customer records exist in the DB,
        _get_or_generate_consumption raises HTTPException(422) with
        "missing_consumption_data" error.

        **Validates: Requirements 3.2**
        """
        with pytest.raises(HTTPException) as exc_info:
            _get_or_generate_consumption(
                db=db_session,
                period="2026-01",
                customer_id=None,
                use_template=False,
                template_name=None,
                template_monthly_kwh=None,
            )

        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["error"] == "missing_consumption_data"

    def test_customer_id_without_records_raises_422(self, db_session):
        """
        When customer_id is provided but no matching consumption records
        exist in the DB, _get_or_generate_consumption raises HTTPException(422).

        **Validates: Requirements 3.2**
        """
        with pytest.raises(HTTPException) as exc_info:
            _get_or_generate_consumption(
                db=db_session,
                period="2026-01",
                customer_id="NONEXISTENT-CUSTOMER",
                use_template=False,
                template_name=None,
                template_monthly_kwh=None,
            )

        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["error"] == "missing_consumption_data"

    def test_customer_id_with_existing_records_returns_them(self, db_session):
        """
        When customer_id is provided and matching consumption records exist
        in the DB, _get_or_generate_consumption returns those records.

        **Validates: Requirements 3.1**
        """
        # Seed a consumption profile with hourly data for customer "C-TEST"
        profile = ConsumptionProfile(
            customer_id="C-TEST",
            customer_name="Test Customer",
            period="2026-01",
            profile_type="actual",
            total_kwh=5000.0,
            source="excel",
            version=1,
            is_active=1,
        )
        db_session.add(profile)
        db_session.flush()  # get profile.id

        # Add 3 hourly records as a minimal set
        hourly_records = [
            ConsumptionHourlyData(
                profile_id=profile.id,
                date="2026-01-01",
                hour=0,
                consumption_kwh=100.0,
            ),
            ConsumptionHourlyData(
                profile_id=profile.id,
                date="2026-01-01",
                hour=1,
                consumption_kwh=150.0,
            ),
            ConsumptionHourlyData(
                profile_id=profile.id,
                date="2026-01-01",
                hour=2,
                consumption_kwh=200.0,
            ),
        ]
        db_session.add_all(hourly_records)
        db_session.commit()

        result = _get_or_generate_consumption(
            db=db_session,
            period="2026-01",
            customer_id="C-TEST",
            use_template=False,
            template_name=None,
            template_monthly_kwh=None,
        )

        # Must return a list of ParsedConsumptionRecord
        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(r, ParsedConsumptionRecord) for r in result)

        # Verify the records match what we seeded
        assert result[0].date == "2026-01-01"
        assert result[0].hour == 0
        assert result[0].consumption_kwh == 100.0

        assert result[1].date == "2026-01-01"
        assert result[1].hour == 1
        assert result[1].consumption_kwh == 150.0

        assert result[2].date == "2026-01-01"
        assert result[2].hour == 2
        assert result[2].consumption_kwh == 200.0


# ═══════════════════════════════════════════════════════════════════════════════
# Task 3.4 — Regression Integration Test for /analyze Endpoint
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnalyzeEndpointRegression:
    """
    Integration test: POST /api/pricing/analyze with template-based consumption.

    Verifies the full endpoint works end-to-end after the db parameter fix:
      - use_template=True, template_name="3_vardiya_sanayi"
      - template_monthly_kwh=100000, period="2026-01"

    **Validates: Requirements 2.1, 2.2**
    """

    @pytest.fixture
    def analyze_client(self):
        """TestClient with in-memory DB seeded with market data, YEKDEM, and template."""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }):
            from app.main import app as fastapi_app
            from app.database import get_db
            from app.pricing.schemas import (
                HourlyMarketPrice,
                MonthlyYekdemPrice,
                ProfileTemplate as PTSchema,
            )
            from fastapi.testclient import TestClient
            from sqlalchemy.pool import StaticPool

            # In-memory SQLite with shared connection across threads
            engine = create_engine(
                "sqlite:///:memory:",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            Base.metadata.create_all(bind=engine)
            TestSession = sessionmaker(bind=engine)
            session = TestSession()

            # ── Seed ProfileTemplate ──────────────────────────────────────
            weights = _normalize([1.0] * 24)
            session.add(PTSchema(
                name="3_vardiya_sanayi",
                display_name="3 Vardiya Sanayi",
                description="7/24 kesintisiz üretim — düz profil",
                hourly_weights=json.dumps(weights),
                is_builtin=1,
            ))

            # ── Seed YEKDEM for 2026-01 ──────────────────────────────────
            session.add(MonthlyYekdemPrice(
                period="2026-01",
                yekdem_tl_per_mwh=400.0,
                source="test-seed",
            ))

            # ── Seed HourlyMarketPrice for 2026-01 (31 days × 24h = 744) ─
            import calendar
            year, month = 2026, 1
            days_in_month = calendar.monthrange(year, month)[1]
            market_records = []
            for day in range(1, days_in_month + 1):
                date_str = f"{year:04d}-{month:02d}-{day:02d}"
                for hour in range(24):
                    market_records.append(HourlyMarketPrice(
                        period="2026-01",
                        date=date_str,
                        hour=hour,
                        ptf_tl_per_mwh=3000.0,
                        smf_tl_per_mwh=2800.0,
                        source="test-seed",
                        version=1,
                        is_active=1,
                    ))
            session.add_all(market_records)
            session.commit()

            # Override get_db to yield our in-memory session
            def _override_get_db():
                try:
                    yield session
                finally:
                    pass  # keep session open for the test

            fastapi_app.dependency_overrides[get_db] = _override_get_db
            yield TestClient(fastapi_app, raise_server_exceptions=False)
            fastapi_app.dependency_overrides.clear()
            session.close()

    def test_analyze_with_template_returns_200(self, analyze_client):
        """
        POST /api/pricing/analyze with template params returns 200 and
        contains valid consumption/risk data.

        **Validates: Requirements 2.1, 2.2**
        """
        payload = {
            "period": "2026-01",
            "multiplier": 1.05,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
        }

        resp = analyze_client.post("/api/pricing/analyze", json=payload)

        # ── Status code ───────────────────────────────────────────────
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:500]}"
        )

        data = resp.json()

        # ── Response contains period ──────────────────────────────────
        assert data["period"] == "2026-01"

        # ── Weighted prices present with consumption data ─────────────
        wp = data["weighted_prices"]
        assert wp["total_consumption_kwh"] == pytest.approx(100000.0, abs=1.0)
        assert wp["hours_count"] == 744  # 31 days × 24h

        # ── Risk score present ────────────────────────────────────────
        assert "risk_score" in data
        assert "score" in data["risk_score"]

        # ── Pricing summary present ───────────────────────────────────
        assert "pricing" in data
        assert data["pricing"]["multiplier"] == 1.05
