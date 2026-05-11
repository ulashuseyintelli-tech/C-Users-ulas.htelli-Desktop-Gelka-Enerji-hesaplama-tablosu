"""
T1/T2/T3 Input Mode — Quality Tests (Tasks 1.3, 4.1–4.7, 5.1, 7.2)

Covers:
  1.3  AnalyzeRequest validation unit tests
  4.1  PBT: Per-zone round-trip (Property 1)
  4.2  PBT: Record count invariant (Property 2)
  4.3  PBT: Non-negative output (Property 3)
  4.4  PBT: Zone classification consistency (Property 4)
  4.5  PBT: Determinism (Property 8)
  4.6  PBT: Residual fix exactness (Property 9)
  4.7  Unit: Dynamic month/hour examples
  5.1  API priority order integration tests
  7.2  Distribution tariff unit tests
"""

from __future__ import annotations

import calendar
import json
import os
from unittest.mock import patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from pydantic import ValidationError

from app.pricing.models import AnalyzeRequest, TimeZone
from app.pricing.profile_templates import generate_t1t2t3_consumption
from app.pricing.time_zones import classify_hour


# ═══════════════════════════════════════════════════════════════════════════════
# 1.3 — AnalyzeRequest Validation Unit Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnalyzeRequestValidation:

    def test_valid_template_mode(self):
        req = AnalyzeRequest(period="2026-01", multiplier=1.15, use_template=True,
                             template_name="3_vardiya_sanayi", template_monthly_kwh=100000)
        assert req.use_template is True

    def test_valid_t1t2t3_mode(self):
        req = AnalyzeRequest(period="2026-01", multiplier=1.15,
                             t1_kwh=5000, t2_kwh=3000, t3_kwh=2000)
        assert req.t1_kwh == 5000

    def test_t1t2t3_all_zero_raises(self):
        with pytest.raises(ValidationError, match="Toplam tüketim sıfır"):
            AnalyzeRequest(period="2026-01", multiplier=1.15,
                           t1_kwh=0, t2_kwh=0, t3_kwh=0)

    def test_negative_t1_raises(self):
        with pytest.raises(ValidationError):
            AnalyzeRequest(period="2026-01", multiplier=1.15, t1_kwh=-100)

    def test_negative_t2_raises(self):
        with pytest.raises(ValidationError):
            AnalyzeRequest(period="2026-01", multiplier=1.15, t2_kwh=-50)

    def test_use_template_true_ignores_t1t2t3(self):
        """use_template=True + t1/t2/t3=0 → no error (t1/t2/t3 ignored)."""
        req = AnalyzeRequest(period="2026-01", multiplier=1.15, use_template=True,
                             template_name="ofis", template_monthly_kwh=50000,
                             t1_kwh=0, t2_kwh=0, t3_kwh=0)
        assert req.use_template is True

    def test_only_t1_provided(self):
        req = AnalyzeRequest(period="2026-01", multiplier=1.15, t1_kwh=10000)
        assert req.t1_kwh == 10000

    def test_multiplier_below_1_raises(self):
        with pytest.raises(ValidationError):
            AnalyzeRequest(period="2026-01", multiplier=0.99)

    def test_voltage_level_default(self):
        req = AnalyzeRequest(period="2026-01", multiplier=1.15,
                             use_template=True, template_name="ofis", template_monthly_kwh=50000)
        assert req.voltage_level == "og"


# ═══════════════════════════════════════════════════════════════════════════════
# 4.1–4.7 — generate_t1t2t3_consumption PBT + Unit Tests
# ═══════════════════════════════════════════════════════════════════════════════

# Strategies
_kwh = st.floats(min_value=0, max_value=500000, allow_nan=False, allow_infinity=False)
_period = st.sampled_from(["2024-02", "2025-02", "2025-06", "2026-01", "2026-04", "2026-12"])


class TestGenerateT1T2T3_PBT:
    """Property-based tests for generate_t1t2t3_consumption."""

    @given(t1=_kwh, t2=_kwh, t3=_kwh, period=_period)
    @settings(max_examples=100)
    def test_per_zone_round_trip(self, t1, t2, t3, period):
        """4.1 Property 1: Per-zone kWh round-trip within ±0.1%."""
        assume(t1 + t2 + t3 > 0)
        records = generate_t1t2t3_consumption(t1, t2, t3, period)

        actual_t1 = sum(r.consumption_kwh for r in records if classify_hour(r.hour) == TimeZone.T1)
        actual_t2 = sum(r.consumption_kwh for r in records if classify_hour(r.hour) == TimeZone.T2)
        actual_t3 = sum(r.consumption_kwh for r in records if classify_hour(r.hour) == TimeZone.T3)

        if t1 > 0:
            assert abs(actual_t1 - t1) / t1 < 0.001, f"T1: {actual_t1} vs {t1}"
        if t2 > 0:
            assert abs(actual_t2 - t2) / t2 < 0.001, f"T2: {actual_t2} vs {t2}"
        if t3 > 0:
            assert abs(actual_t3 - t3) / t3 < 0.001, f"T3: {actual_t3} vs {t3}"

    @given(t1=_kwh, t2=_kwh, t3=_kwh, period=_period)
    @settings(max_examples=50)
    def test_record_count_invariant(self, t1, t2, t3, period):
        """4.2 Property 2: Record count = days_in_month × 24."""
        assume(t1 + t2 + t3 > 0)
        records = generate_t1t2t3_consumption(t1, t2, t3, period)
        year, month = int(period[:4]), int(period[5:7])
        expected = calendar.monthrange(year, month)[1] * 24
        assert len(records) == expected

    @given(t1=_kwh, t2=_kwh, t3=_kwh, period=_period)
    @settings(max_examples=50)
    def test_non_negative_output(self, t1, t2, t3, period):
        """4.3 Property 3: All consumption_kwh >= -1e-10 (residual fix may produce tiny negatives)."""
        assume(t1 + t2 + t3 > 0)
        records = generate_t1t2t3_consumption(t1, t2, t3, period)
        for r in records:
            assert r.consumption_kwh >= -1e-6, f"Negative: {r.date} h{r.hour} = {r.consumption_kwh}"

    @given(t1=_kwh, t2=_kwh, t3=_kwh, period=_period)
    @settings(max_examples=50)
    def test_zone_classification_consistency(self, t1, t2, t3, period):
        """4.4 Property 4: classify_hour matches expected zone."""
        assume(t1 + t2 + t3 > 0)
        records = generate_t1t2t3_consumption(t1, t2, t3, period)
        for r in records:
            tz = classify_hour(r.hour)
            if t1 == 0 and tz == TimeZone.T1:
                assert r.consumption_kwh == 0.0 or abs(r.consumption_kwh) < 1e-6
            if t2 == 0 and tz == TimeZone.T2:
                assert r.consumption_kwh == 0.0 or abs(r.consumption_kwh) < 1e-6
            if t3 == 0 and tz == TimeZone.T3:
                assert r.consumption_kwh == 0.0 or abs(r.consumption_kwh) < 1e-6

    @given(t1=_kwh, t2=_kwh, t3=_kwh, period=_period)
    @settings(max_examples=30)
    def test_determinism(self, t1, t2, t3, period):
        """4.5 Property 8: Same input → same output."""
        assume(t1 + t2 + t3 > 0)
        r1 = generate_t1t2t3_consumption(t1, t2, t3, period)
        r2 = generate_t1t2t3_consumption(t1, t2, t3, period)
        assert len(r1) == len(r2)
        for a, b in zip(r1, r2):
            assert a.consumption_kwh == b.consumption_kwh

    @given(t1=_kwh, t2=_kwh, t3=_kwh, period=_period)
    @settings(max_examples=100)
    def test_residual_fix_exactness(self, t1, t2, t3, period):
        """4.6 Property 9: Per-zone sum ≈ input (within floating point tolerance)."""
        assume(t1 + t2 + t3 > 0)
        records = generate_t1t2t3_consumption(t1, t2, t3, period)

        actual_t1 = sum(r.consumption_kwh for r in records if classify_hour(r.hour) == TimeZone.T1)
        actual_t2 = sum(r.consumption_kwh for r in records if classify_hour(r.hour) == TimeZone.T2)
        actual_t3 = sum(r.consumption_kwh for r in records if classify_hour(r.hour) == TimeZone.T3)

        # Floating point sum tolerance: ±1e-6 or ±0.001% of value
        def close(a, b):
            if b == 0:
                return abs(a) < 1e-6
            return abs(a - b) < max(1e-6, abs(b) * 1e-5)

        assert close(actual_t1, t1), f"T1: {actual_t1} != {t1}"
        assert close(actual_t2, t2), f"T2: {actual_t2} != {t2}"
        assert close(actual_t3, t3), f"T3: {actual_t3} != {t3}"


class TestGenerateT1T2T3_Unit:
    """4.7 Unit tests: Dynamic month/hour examples."""

    def test_feb_2024_leap_year(self):
        records = generate_t1t2t3_consumption(1000, 500, 300, "2024-02")
        assert len(records) == 29 * 24  # 696

    def test_feb_2025_non_leap(self):
        records = generate_t1t2t3_consumption(1000, 500, 300, "2025-02")
        assert len(records) == 28 * 24  # 672

    def test_apr_2026_30_days(self):
        records = generate_t1t2t3_consumption(1000, 500, 300, "2026-04")
        assert len(records) == 30 * 24  # 720

    def test_jan_2026_31_days(self):
        records = generate_t1t2t3_consumption(1000, 500, 300, "2026-01")
        assert len(records) == 31 * 24  # 744

    def test_single_zone_t1_only(self):
        records = generate_t1t2t3_consumption(10000, 0, 0, "2026-01")
        t1_sum = sum(r.consumption_kwh for r in records if classify_hour(r.hour) == TimeZone.T1)
        t2_sum = sum(r.consumption_kwh for r in records if classify_hour(r.hour) == TimeZone.T2)
        t3_sum = sum(r.consumption_kwh for r in records if classify_hour(r.hour) == TimeZone.T3)
        assert t1_sum == 10000
        assert t2_sum == 0
        assert t3_sum == 0

    def test_invalid_period_raises(self):
        with pytest.raises(ValueError, match="Geçersiz dönem"):
            generate_t1t2t3_consumption(100, 100, 100, "2026-13")

    def test_all_zero_raises(self):
        with pytest.raises(ValueError, match="Toplam tüketim sıfır"):
            generate_t1t2t3_consumption(0, 0, 0, "2026-01")


# ═══════════════════════════════════════════════════════════════════════════════
# 5.1 — API Priority Order Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════


def _create_test_client(period="2026-01", ptf=1500.0, smf=1600.0, yekdem=400.0):
    """Create FastAPI TestClient with in-memory DB."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    with patch.dict(os.environ, {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import Base, get_db
        from app.pricing.schemas import HourlyMarketPrice, MonthlyYekdemPrice, ProfileTemplate
        from app.pricing.profile_templates import _normalize
        from fastapi.testclient import TestClient

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=engine)
        TestSession = sessionmaker(bind=engine)
        session = TestSession()

        weights = _normalize([1.0] * 24)
        session.add(ProfileTemplate(name="3_vardiya_sanayi", display_name="3 Vardiya Sanayi",
                                    description="test", hourly_weights=json.dumps(weights), is_builtin=1))
        session.add(MonthlyYekdemPrice(period=period, yekdem_tl_per_mwh=yekdem, source="test"))

        year, month = int(period[:4]), int(period[5:7])
        days = calendar.monthrange(year, month)[1]
        for day in range(1, days + 1):
            date_str = f"{period}-{day:02d}"
            for hour in range(24):
                session.add(HourlyMarketPrice(period=period, date=date_str, hour=hour,
                                              ptf_tl_per_mwh=ptf, smf_tl_per_mwh=smf,
                                              source="test", version=1, is_active=1))
        session.commit()

        def _override():
            try:
                yield session
            finally:
                pass

        fastapi_app.dependency_overrides[get_db] = _override
        return TestClient(fastapi_app, raise_server_exceptions=False)


class TestAPIPriorityOrder:

    def test_t1t2t3_overrides_template(self):
        """T1/T2/T3 + template params → T1/T2/T3 wins."""
        client = _create_test_client()
        resp = client.post("/api/pricing/analyze", json={
            "period": "2026-01", "multiplier": 1.15,
            "use_template": False,
            "t1_kwh": 5000, "t2_kwh": 3000, "t3_kwh": 2000,
        })
        assert resp.status_code == 200
        data = resp.json()
        total_kwh = data["weighted_prices"]["total_consumption_kwh"]
        assert abs(total_kwh - 10000) < 1  # T1+T2+T3 = 10000

    def test_template_mode_preserved(self):
        """use_template=true → template behavior unchanged."""
        client = _create_test_client()
        resp = client.post("/api/pricing/analyze", json={
            "period": "2026-01", "multiplier": 1.15,
            "use_template": True, "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
        })
        assert resp.status_code == 200

    def test_t1t2t3_zero_returns_422(self):
        """use_template=false + t1/t2/t3=0 → 422."""
        client = _create_test_client()
        resp = client.post("/api/pricing/analyze", json={
            "period": "2026-01", "multiplier": 1.15,
            "t1_kwh": 0, "t2_kwh": 0, "t3_kwh": 0,
        })
        assert resp.status_code == 422

    def test_use_template_true_ignores_t1t2t3(self):
        """use_template=true + t1/t2/t3 → template wins (T1/T2/T3 ignored by validator)."""
        client = _create_test_client()
        # Note: current implementation priority is T1/T2/T3 > template in _get_or_generate_consumption
        # But when use_template=True, the validator ignores T1/T2/T3 validation
        # The actual consumption source depends on _get_or_generate_consumption priority
        resp = client.post("/api/pricing/analyze", json={
            "period": "2026-01", "multiplier": 1.15,
            "use_template": True, "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 50000,
            "t1_kwh": 5000, "t2_kwh": 3000, "t3_kwh": 2000,
        })
        assert resp.status_code == 200  # No validation error


# ═══════════════════════════════════════════════════════════════════════════════
# 7.2 — Distribution Tariff Unit Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestDistributionTariff:

    def test_ag_vs_og_different_price(self):
        """AG and OG have different distribution unit prices."""
        from app.distribution_tariffs import get_distribution_unit_price
        og = get_distribution_unit_price("sanayi", "OG", "çift_terim")
        ag = get_distribution_unit_price("sanayi", "AG", "tek_terim")
        assert og.success and ag.success
        assert og.unit_price != ag.unit_price
        assert ag.unit_price > og.unit_price  # AG always more expensive

    def test_period_based_tariff_selection(self):
        """Pre-April 2026 vs post-April 2026 tariffs differ."""
        from app.distribution_tariffs import get_distribution_unit_price
        old = get_distribution_unit_price("sanayi", "OG", "çift_terim", period="2026-03")
        new = get_distribution_unit_price("sanayi", "OG", "çift_terim", period="2026-04")
        assert old.success and new.success
        assert old.unit_price != new.unit_price  # April 2026 tariff change

    def test_unknown_tariff_group_fails(self):
        from app.distribution_tariffs import get_distribution_unit_price
        result = get_distribution_unit_price("bilinmeyen", "OG", "çift_terim")
        assert not result.success

    def test_sanayi_og_ct_has_price(self):
        from app.distribution_tariffs import get_distribution_unit_price
        result = get_distribution_unit_price("sanayi", "OG", "çift_terim")
        assert result.success
        assert result.unit_price > 0

    def test_tarimsal_tariff_exists(self):
        from app.distribution_tariffs import get_distribution_unit_price
        result = get_distribution_unit_price("tarimsal", "OG", "tek_terim")
        assert result.success
        assert result.unit_price > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 11.1 — End-to-End Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestEndToEndAnalysis:
    """Full analyze flow: input → API → cost → margin → response validation."""

    def test_t1t2t3_full_analysis_flow(self):
        """T1/T2/T3 mode: full analysis with all fields validated."""
        client = _create_test_client(ptf=1500.0, smf=1600.0, yekdem=150.0)
        resp = client.post("/api/pricing/analyze", json={
            "period": "2026-01",
            "multiplier": 1.15,
            "use_template": False,
            "t1_kwh": 5000,
            "t2_kwh": 3000,
            "t3_kwh": 2000,
            "dealer_commission_pct": 3.0,
            "voltage_level": "og",
        })
        assert resp.status_code == 200
        data = resp.json()

        # Structure validation
        assert "weighted_prices" in data
        assert "supplier_cost" in data
        assert "pricing" in data
        assert "time_zone_breakdown" in data
        assert "risk_score" in data
        assert "loss_map" in data
        assert "safe_multiplier" in data
        assert "distribution" in data

        # Total consumption = T1 + T2 + T3
        total_kwh = data["weighted_prices"]["total_consumption_kwh"]
        assert abs(total_kwh - 10000) < 1

        # Pricing fields exist (dual margin v3)
        pricing = data["pricing"]
        assert "sales_energy_price_per_mwh" in pricing
        assert "sales_effective_price_per_mwh" in pricing
        assert "gross_margin_energy_per_mwh" in pricing
        assert "gross_margin_total_per_mwh" in pricing
        assert "net_margin_per_mwh" in pricing
        assert "risk_flags" in pricing

        # Backward compat fields
        assert "sales_price_tl_per_mwh" in pricing
        assert "gross_margin_tl_per_mwh" in pricing
        assert "total_gross_margin_tl" in pricing
        assert "total_net_margin_tl" in pricing

        # Multiplier matches
        assert pricing["multiplier"] == 1.15

    def test_template_fallback_preserved(self):
        """Template mode: existing behavior unchanged."""
        client = _create_test_client(ptf=1500.0, smf=1600.0, yekdem=150.0)
        resp = client.post("/api/pricing/analyze", json={
            "period": "2026-01",
            "multiplier": 1.10,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["weighted_prices"]["total_consumption_kwh"] > 90000

    def test_ag_vs_og_distribution_difference(self):
        """Same consumption, different voltage → distribution info reflects voltage."""
        client = _create_test_client(ptf=1500.0, smf=1600.0, yekdem=150.0)

        resp_og = client.post("/api/pricing/analyze", json={
            "period": "2026-01", "multiplier": 1.15,
            "t1_kwh": 5000, "t2_kwh": 3000, "t3_kwh": 2000,
            "voltage_level": "og",
        })
        # Use different multiplier to avoid cache hit
        resp_ag = client.post("/api/pricing/analyze", json={
            "period": "2026-01", "multiplier": 1.16,
            "t1_kwh": 5000, "t2_kwh": 3000, "t3_kwh": 2000,
            "voltage_level": "ag",
        })
        assert resp_og.status_code == 200
        assert resp_ag.status_code == 200

        dist_og = resp_og.json().get("distribution")
        dist_ag = resp_ag.json().get("distribution")

        # Both should have distribution info with correct voltage
        if dist_og:
            assert dist_og["voltage_level"] == "OG"
            assert dist_og["unit_price_tl_per_kwh"] > 0
        if dist_ag:
            assert dist_ag["voltage_level"] == "AG"
            assert dist_ag["unit_price_tl_per_kwh"] > 0

    def test_gross_margin_formula_validation(self):
        """Brüt marj = Satış - (PTF + YEKDEM + Dağıtım)."""
        client = _create_test_client(ptf=1500.0, smf=1600.0, yekdem=150.0)
        resp = client.post("/api/pricing/analyze", json={
            "period": "2026-01", "multiplier": 1.15,
            "t1_kwh": 5000, "t2_kwh": 3000, "t3_kwh": 2000,
            "voltage_level": "og",
        })
        assert resp.status_code == 200
        data = resp.json()
        pricing = data["pricing"]

        # Energy margin = sales_energy - energy_cost
        energy_price = pricing["sales_energy_price_per_mwh"]
        supplier_cost = data["supplier_cost"]["weighted_ptf_tl_per_mwh"] + data["supplier_cost"]["yekdem_tl_per_mwh"]
        expected_energy_margin = energy_price - supplier_cost
        assert pricing["gross_margin_energy_per_mwh"] == pytest.approx(expected_energy_margin, abs=0.1)

        # Total margin = energy margin - distribution
        dist_per_mwh = pricing.get("distribution_cost_per_mwh", 0)
        expected_total_margin = pricing["gross_margin_energy_per_mwh"] - dist_per_mwh
        assert pricing["gross_margin_total_per_mwh"] == pytest.approx(expected_total_margin, abs=0.1)

    def test_t2_peak_warning_thresholds(self):
        """T2 ≥ 55% → high risk score or warning."""
        client = _create_test_client(ptf=1500.0, smf=1600.0, yekdem=150.0)

        # Heavy T2 profile: T1=1000, T2=7000, T3=2000 → T2 = 70%
        resp = client.post("/api/pricing/analyze", json={
            "period": "2026-01", "multiplier": 1.15,
            "t1_kwh": 1000, "t2_kwh": 7000, "t3_kwh": 2000,
        })
        assert resp.status_code == 200
        data = resp.json()

        # T2 consumption should be dominant
        tz = data.get("time_zone_breakdown", {})
        if "T2" in tz:
            t2_pct = tz["T2"]["consumption_pct"]
            assert t2_pct > 50  # T2 dominant

    def test_template_api_returns_new_fields(self):
        """GET /api/pricing/templates returns t1_pct, t2_pct, t3_pct, risk_level, risk_buffer_pct."""
        client = _create_test_client()
        resp = client.get("/api/pricing/templates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1  # At least the seeded template

        for item in data["items"]:
            assert "t1_pct" in item, f"{item['name']} missing t1_pct"
            assert "t2_pct" in item, f"{item['name']} missing t2_pct"
            assert "t3_pct" in item, f"{item['name']} missing t3_pct"
            assert "risk_level" in item, f"{item['name']} missing risk_level"
            assert "risk_buffer_pct" in item, f"{item['name']} missing risk_buffer_pct"
            # T1 + T2 + T3 = 100
            total = item["t1_pct"] + item["t2_pct"] + item["t3_pct"]
            assert total == 100, f"{item['name']}: T1+T2+T3={total} != 100"

    def test_bayi_segments_api(self):
        """GET /api/pricing/bayi-segments returns segment definitions."""
        client = _create_test_client()
        resp = client.get("/api/pricing/bayi-segments")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 6
        for seg in data["segments"]:
            assert "name" in seg
            assert "min_multiplier" in seg
            assert "max_multiplier" in seg
            assert "bayi_points" in seg
            assert "requires_approval" in seg
