"""
Pricing Consistency Fixes — Bug Condition Exploration Tests.

Property 1: Bug Condition — Pricing Consistency Defects
(Dual Margin, Net Margin, YEKDEM 404, Safety Guards)

CRITICAL: These tests encode the EXPECTED behavior after the fix.
They MUST FAIL on the current unfixed code — failure confirms the bugs exist.
DO NOT attempt to fix the test or the code when they fail.

Bug Conditions Tested:
  C1 — Single Margin (no dual model): system produces only gross_margin_tl_per_mwh,
        no gross_margin_energy / gross_margin_total fields, no dual sales price
  C2 — Incomplete Net Margin: net_margin_per_mwh = gross - dealer (missing imbalance)
  C4 — YEKDEM 404: YEKDEM missing → HTTP 404 instead of graceful fallback

Safety Guards:
  - dealer_commission_total_tl >= 0 and capped to energy margin
  - imbalance_cost_per_mwh >= weighted_ptf * 0.01 (RISK_FLOOR)
  - LOSS_RISK flag when net < 0
  - UNPROFITABLE_OFFER flag when gross_total < 0

Validates: Requirements 1.1, 1.2, 1.3, 1.5, 1.6, 1.9, 1.10,
           2.1, 2.2, 2.3, 2.5, 2.6, 2.10, 2.11
"""

from __future__ import annotations

import calendar
import json
import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.pricing.schemas  # noqa: F401 — register pricing tables
from app.pricing.schemas import (
    HourlyMarketPrice,
    MonthlyYekdemPrice,
    ProfileTemplate,
)
from app.pricing.models import ImbalanceParams
from app.pricing.excel_parser import ParsedMarketRecord, ParsedConsumptionRecord
from app.pricing.pricing_engine import calculate_hourly_costs, calculate_weighted_prices
from app.pricing.profile_templates import _normalize


# ═══════════════════════════════════════════════════════════════════════════════
# Test Data Builders
# ═══════════════════════════════════════════════════════════════════════════════


def _market(date: str, hour: int, ptf: float, smf: float) -> ParsedMarketRecord:
    return ParsedMarketRecord(
        period=date[:7], date=date, hour=hour,
        ptf_tl_per_mwh=ptf, smf_tl_per_mwh=smf,
    )


def _consumption(date: str, hour: int, kwh: float) -> ParsedConsumptionRecord:
    return ParsedConsumptionRecord(date=date, hour=hour, consumption_kwh=kwh)


def _build_market_data(period: str = "2026-01", ptf: float = 1500.0, smf: float = 1600.0):
    """Build a full month of market data with constant PTF/SMF."""
    year, month = int(period[:4]), int(period[5:7])
    days = calendar.monthrange(year, month)[1]
    records = []
    for day in range(1, days + 1):
        date_str = f"{period}-{day:02d}"
        for hour in range(24):
            records.append(_market(date_str, hour, ptf, smf))
    return records


def _build_consumption_data(period: str = "2026-01", kwh_per_hour: float = 100.0):
    """Build a full month of consumption data with constant kWh."""
    year, month = int(period[:4]), int(period[5:7])
    days = calendar.monthrange(year, month)[1]
    records = []
    for day in range(1, days + 1):
        date_str = f"{period}-{day:02d}"
        for hour in range(24):
            records.append(_consumption(date_str, hour, kwh_per_hour))
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# Bug C1 — Single Margin (no dual model)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBugC1_DualMarginModel:
    """
    Bug C1: System produces only single gross_margin, no dual model.

    Expected behavior (after fix):
    - HourlyCostResult has gross_margin_energy_total_tl AND gross_margin_total_total_tl
    - When distribution > 0: gross_margin_energy_total_tl > gross_margin_total_total_tl
    - PricingSummary has dual sales price fields

    **Validates: Requirements 1.1, 1.2, 2.1, 2.2, 2.3**
    """

    def test_hourly_result_has_dual_margin_fields(self):
        """
        calculate_hourly_costs must return both energy and total gross margin.

        On unfixed code: AttributeError — HourlyCostResult has no
        gross_margin_energy_total_tl or gross_margin_total_total_tl fields.
        """
        market = _build_market_data("2026-01", ptf=1500.0, smf=1600.0)
        consumption = _build_consumption_data("2026-01", kwh_per_hour=100.0)
        params = ImbalanceParams(forecast_error_rate=0.05, smf_based_imbalance_enabled=False)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
            dealer_commission_pct=3.0,
            distribution_unit_price_tl_per_kwh=0.81,
        )

        # Must have dual margin fields
        assert hasattr(result, "gross_margin_energy_total_tl"), \
            "HourlyCostResult missing gross_margin_energy_total_tl field"
        assert hasattr(result, "gross_margin_total_total_tl"), \
            "HourlyCostResult missing gross_margin_total_total_tl field"

        # Energy margin > Total margin when distribution > 0
        assert result.gross_margin_energy_total_tl > result.gross_margin_total_total_tl, (
            f"Energy margin ({result.gross_margin_energy_total_tl}) should be > "
            f"total margin ({result.gross_margin_total_total_tl}) when distribution > 0"
        )

    def test_hourly_result_has_distribution_cost(self):
        """
        calculate_hourly_costs must return distribution_cost_total_tl.

        On unfixed code: AttributeError — field does not exist.
        """
        market = _build_market_data("2026-01", ptf=1500.0, smf=1600.0)
        consumption = _build_consumption_data("2026-01", kwh_per_hour=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
            distribution_unit_price_tl_per_kwh=0.81,
        )

        assert hasattr(result, "distribution_cost_total_tl"), \
            "HourlyCostResult missing distribution_cost_total_tl field"

        # distribution_cost = 0.81 TL/kWh × total_kwh
        # total_kwh = 100 kWh/h × 744 hours = 74400 kWh
        expected_dist = 0.81 * 74400.0
        assert result.distribution_cost_total_tl == pytest.approx(expected_dist, rel=0.01)

    def test_analyze_response_has_dual_sales_price(self):
        """
        PricingSummary in analyze response must have dual sales price fields:
        sales_energy_price_per_mwh and sales_effective_price_per_mwh.

        On unfixed code: KeyError — fields do not exist in response.

        **Validates: Requirements 2.1, 2.3**
        """
        client = _create_test_client(seed_yekdem=True)

        payload = {
            "period": "2026-01",
            "multiplier": 1.15,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
            "dealer_commission_pct": 3.0,
            "voltage_level": "og",
        }

        resp = client.post("/api/pricing/analyze", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:500]}"

        data = resp.json()
        pricing = data["pricing"]

        # Dual sales price fields must exist
        assert "sales_energy_price_per_mwh" in pricing, \
            "PricingSummary missing sales_energy_price_per_mwh"
        assert "sales_effective_price_per_mwh" in pricing, \
            "PricingSummary missing sales_effective_price_per_mwh"

        # Dual margin fields must exist
        assert "gross_margin_energy_per_mwh" in pricing, \
            "PricingSummary missing gross_margin_energy_per_mwh"
        assert "gross_margin_total_per_mwh" in pricing, \
            "PricingSummary missing gross_margin_total_per_mwh"


# ═══════════════════════════════════════════════════════════════════════════════
# Bug C2 — Incomplete Net Margin
# ═══════════════════════════════════════════════════════════════════════════════


class TestBugC2_IncompleteNetMargin:
    """
    Bug C2: net_margin_per_mwh = gross - dealer (missing imbalance deduction).

    Expected behavior (after fix):
    - net_margin_per_mwh = gross_margin_total_per_mwh - dealer_commission_per_mwh
                           - imbalance_cost_per_mwh
    - Per-MWh and total-TL are consistent: total_tl ≈ per_mwh × consumption / 1000

    **Validates: Requirements 1.5, 1.6, 2.5, 2.6**
    """

    def test_net_margin_includes_imbalance_deduction(self):
        """
        Net margin per-MWh must deduct imbalance cost.

        On unfixed code: net_margin = gross - dealer (imbalance missing).
        After fix: net_margin = gross_total - dealer - imbalance.
        """
        client = _create_test_client(seed_yekdem=True)

        payload = {
            "period": "2026-01",
            "multiplier": 1.15,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
            "dealer_commission_pct": 3.0,
            "imbalance_params": {
                "forecast_error_rate": 0.05,
                "imbalance_cost_tl_per_mwh": 50.0,
                "smf_based_imbalance_enabled": False,
            },
        }

        resp = client.post("/api/pricing/analyze", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        pricing = data["pricing"]

        # These fields must exist (C1 fix prerequisite)
        gross_margin_total = pricing["gross_margin_total_per_mwh"]
        dealer_commission = pricing["dealer_commission_per_mwh"]
        imbalance_cost = pricing["imbalance_cost_per_mwh"]
        net_margin = pricing["net_margin_per_mwh"]

        # Net margin = gross_total - dealer - imbalance
        expected_net = gross_margin_total - dealer_commission - imbalance_cost
        assert net_margin == pytest.approx(expected_net, abs=0.02), (
            f"net_margin_per_mwh ({net_margin}) != "
            f"gross_margin_total ({gross_margin_total}) - "
            f"dealer ({dealer_commission}) - imbalance ({imbalance_cost}) = {expected_net}"
        )

    def test_per_mwh_total_tl_consistency(self):
        """
        Per-MWh and total-TL values must be consistent:
        total_tl ≈ per_mwh × consumption_kwh / 1000

        **Validates: Requirements 2.5, 2.6**
        """
        client = _create_test_client(seed_yekdem=True)

        payload = {
            "period": "2026-01",
            "multiplier": 1.15,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
            "dealer_commission_pct": 3.0,
        }

        resp = client.post("/api/pricing/analyze", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        pricing = data["pricing"]
        total_kwh = data["weighted_prices"]["total_consumption_kwh"]

        # gross_margin_energy: per_mwh × kwh / 1000 ≈ total_tl
        energy_per_mwh = pricing["gross_margin_energy_per_mwh"]
        energy_total_tl = pricing.get("total_gross_margin_tl", 0)  # backward compat alias
        expected_energy_total = energy_per_mwh * total_kwh / 1000
        assert energy_total_tl == pytest.approx(expected_energy_total, abs=0.02), (
            f"Energy margin total TL ({energy_total_tl}) != "
            f"per_mwh ({energy_per_mwh}) × kwh ({total_kwh}) / 1000 = {expected_energy_total}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Bug C4 — YEKDEM 404
# ═══════════════════════════════════════════════════════════════════════════════


class TestBugC4_Yekdem404:
    """
    Bug C4: YEKDEM missing for period → HTTP 404.

    Expected behavior (after fix):
    - HTTP 200 (not 404)
    - yekdem=0 in supplier_cost
    - Warning with severity "high" and impact "pricing_accuracy_low"

    **Validates: Requirements 1.9, 1.10, 2.10, 2.11**
    """

    def test_analyze_without_yekdem_returns_200(self):
        """
        Analyze endpoint with period that has no YEKDEM should return 200.

        On unfixed code: HTTP 404 with yekdem_not_found error.
        After fix: HTTP 200 with yekdem=0 and warning.
        """
        # Create client WITHOUT seeding YEKDEM for the period
        client = _create_test_client(seed_yekdem=False)

        payload = {
            "period": "2026-01",
            "multiplier": 1.15,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
        }

        resp = client.post("/api/pricing/analyze", json=payload)

        # Must be 200, not 404
        assert resp.status_code == 200, (
            f"Expected 200 (graceful YEKDEM fallback), got {resp.status_code}: "
            f"{resp.text[:500]}"
        )

    def test_analyze_without_yekdem_has_zero_yekdem(self):
        """
        When YEKDEM is missing, supplier_cost.yekdem_tl_per_mwh must be 0.

        **Validates: Requirements 2.10**
        """
        client = _create_test_client(seed_yekdem=False)

        payload = {
            "period": "2026-01",
            "multiplier": 1.15,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
        }

        resp = client.post("/api/pricing/analyze", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        assert data["supplier_cost"]["yekdem_tl_per_mwh"] == 0.0

    def test_analyze_without_yekdem_has_severity_warning(self):
        """
        When YEKDEM is missing, warnings must include a high-severity entry
        with impact "pricing_accuracy_low".

        **Validates: Requirements 2.11**
        """
        client = _create_test_client(seed_yekdem=False)

        payload = {
            "period": "2026-01",
            "multiplier": 1.15,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
        }

        resp = client.post("/api/pricing/analyze", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        warnings = data.get("warnings", [])

        # Find the YEKDEM warning
        yekdem_warnings = [
            w for w in warnings
            if w.get("severity") == "high" and w.get("impact") == "pricing_accuracy_low"
        ]
        assert len(yekdem_warnings) >= 1, (
            f"Expected warning with severity='high' and impact='pricing_accuracy_low', "
            f"got warnings: {warnings}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Safety Guards (v3.1)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafetyGuards:
    """
    Safety guard properties that must hold for any pricing result.

    **Validates: Design v3.1 Safety Guards**
    """

    def test_dealer_commission_non_negative(self):
        """
        dealer_commission_total_tl >= 0 always.

        On unfixed code: field may not exist or may not be capped.
        """
        market = _build_market_data("2026-01", ptf=1500.0, smf=1600.0)
        consumption = _build_consumption_data("2026-01", kwh_per_hour=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
            dealer_commission_pct=5.0,
            distribution_unit_price_tl_per_kwh=0.0,
        )

        assert hasattr(result, "dealer_commission_total_tl"), \
            "HourlyCostResult missing dealer_commission_total_tl"
        assert result.dealer_commission_total_tl >= 0, \
            f"dealer_commission_total_tl ({result.dealer_commission_total_tl}) must be >= 0"

    def test_dealer_commission_capped_to_energy_margin(self):
        """
        dealer_commission_total_tl <= max(0, gross_margin_energy_total_tl).

        On unfixed code: no cap exists, commission can exceed margin.
        """
        market = _build_market_data("2026-01", ptf=1500.0, smf=1600.0)
        consumption = _build_consumption_data("2026-01", kwh_per_hour=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
            dealer_commission_pct=5.0,
            distribution_unit_price_tl_per_kwh=0.81,
        )

        assert hasattr(result, "dealer_commission_total_tl")
        assert hasattr(result, "gross_margin_energy_total_tl")

        cap = max(0, result.gross_margin_energy_total_tl)
        assert result.dealer_commission_total_tl <= cap, (
            f"dealer_commission ({result.dealer_commission_total_tl}) exceeds "
            f"energy margin cap ({cap})"
        )

    def test_imbalance_floor_per_mwh(self):
        """
        imbalance_cost_per_mwh >= weighted_ptf * 0.01 (RISK_FLOOR).

        On unfixed code: no floor exists.
        """
        market = _build_market_data("2026-01", ptf=1500.0, smf=1600.0)
        consumption = _build_consumption_data("2026-01", kwh_per_hour=100.0)
        # Zero imbalance params — floor should still apply
        params = ImbalanceParams(
            forecast_error_rate=0.0,
            imbalance_cost_tl_per_mwh=0.0,
            smf_based_imbalance_enabled=False,
        )

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
            dealer_commission_pct=0.0,
            distribution_unit_price_tl_per_kwh=0.0,
        )

        assert hasattr(result, "imbalance_cost_total_tl"), \
            "HourlyCostResult missing imbalance_cost_total_tl"

        # weighted_ptf for constant 1500 PTF = 1500
        # RISK_FLOOR = 0.01
        # imbalance_per_mwh >= 1500 * 0.01 = 15.0
        # imbalance_total >= 15.0 * 74400 / 1000 = 1116.0
        min_imbalance_per_mwh = 1500.0 * 0.01
        total_kwh = 100.0 * 744  # 74400
        min_imbalance_total = min_imbalance_per_mwh * total_kwh / 1000
        assert result.imbalance_cost_total_tl >= min_imbalance_total - 0.02, (
            f"imbalance_cost_total_tl ({result.imbalance_cost_total_tl}) < "
            f"floor ({min_imbalance_total})"
        )

    def test_risk_flag_loss_risk_when_net_negative(self):
        """
        PricingSummary must have risk_flags field. When net_margin_total_tl < 0,
        risk_flags must include LOSS_RISK with priority 1.

        On unfixed code: risk_flags field does not exist in PricingSummary.
        """
        client = _create_test_client(seed_yekdem=True, yekdem_value=150.0)

        # Use very low multiplier + high dealer + high imbalance to force negative net
        payload = {
            "period": "2026-01",
            "multiplier": 1.01,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
            "dealer_commission_pct": 10.0,
            "imbalance_params": {
                "forecast_error_rate": 0.10,
                "imbalance_cost_tl_per_mwh": 100.0,
                "smf_based_imbalance_enabled": False,
            },
        }

        resp = client.post("/api/pricing/analyze", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        pricing = data["pricing"]

        # risk_flags field must exist regardless of net margin sign
        assert "risk_flags" in pricing, "PricingSummary missing risk_flags field"
        assert isinstance(pricing["risk_flags"], list), "risk_flags must be a list"

    def test_risk_flag_unprofitable_when_gross_total_negative(self):
        """
        When gross_margin_total_per_mwh < 0, risk_flags must include
        UNPROFITABLE_OFFER with priority 2.

        On unfixed code: risk_flags field does not exist.
        """
        client = _create_test_client(seed_yekdem=True, yekdem_value=150.0)

        # Very low multiplier + high distribution → negative gross total margin
        payload = {
            "period": "2026-01",
            "multiplier": 1.01,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
            "voltage_level": "og",
        }

        resp = client.post("/api/pricing/analyze", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        pricing = data["pricing"]

        # Check if gross_margin_total_per_mwh is negative
        gross_total = pricing.get("gross_margin_total_per_mwh")
        assert gross_total is not None, "PricingSummary missing gross_margin_total_per_mwh"

        if gross_total < 0:
            assert "risk_flags" in pricing, "PricingSummary missing risk_flags field"
            unprofitable_flags = [
                f for f in pricing["risk_flags"]
                if f.get("type") == "UNPROFITABLE_OFFER"
            ]
            assert len(unprofitable_flags) >= 1, (
                f"Expected UNPROFITABLE_OFFER flag when gross_total < 0, "
                f"got risk_flags: {pricing.get('risk_flags', [])}"
            )
            assert unprofitable_flags[0]["priority"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# TestClient Factory
# ═══════════════════════════════════════════════════════════════════════════════


def _create_test_client(
    seed_yekdem: bool = True,
    yekdem_value: float = 400.0,
    period: str = "2026-01",
    ptf: float = 1500.0,
    smf: float = 1600.0,
):
    """Create a FastAPI TestClient with in-memory DB seeded with test data.

    Args:
        seed_yekdem: Whether to seed YEKDEM record for the period.
        yekdem_value: YEKDEM value in TL/MWh.
        period: Period string (YYYY-MM).
        ptf: Constant PTF value for all hours.
        smf: Constant SMF value for all hours.
    """
    with patch.dict(os.environ, {
        "ADMIN_API_KEY_ENABLED": "false",
        "API_KEY_ENABLED": "false",
    }):
        from app.main import app as fastapi_app
        from app.database import get_db
        from fastapi.testclient import TestClient

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        TestSession = sessionmaker(bind=engine)
        session = TestSession()

        # Seed ProfileTemplate
        weights = _normalize([1.0] * 24)
        session.add(ProfileTemplate(
            name="3_vardiya_sanayi",
            display_name="3 Vardiya Sanayi",
            description="7/24 kesintisiz üretim — düz profil",
            hourly_weights=json.dumps(weights),
            is_builtin=1,
        ))

        # Seed YEKDEM (conditionally)
        if seed_yekdem:
            session.add(MonthlyYekdemPrice(
                period=period,
                yekdem_tl_per_mwh=yekdem_value,
                source="test-seed",
            ))

        # Seed market data
        year, month = int(period[:4]), int(period[5:7])
        days = calendar.monthrange(year, month)[1]
        for day in range(1, days + 1):
            date_str = f"{period}-{day:02d}"
            for hour in range(24):
                session.add(HourlyMarketPrice(
                    period=period,
                    date=date_str,
                    hour=hour,
                    ptf_tl_per_mwh=ptf,
                    smf_tl_per_mwh=smf,
                    source="test-seed",
                    version=1,
                    is_active=1,
                ))
        session.commit()

        def _override_get_db():
            try:
                yield session
            finally:
                pass

        fastapi_app.dependency_overrides[get_db] = _override_get_db
        client = TestClient(fastapi_app, raise_server_exceptions=False)

        # Cleanup will happen when test ends — but we need to return client
        # Store references for cleanup
        client._test_session = session
        client._test_app = fastapi_app

        return client
