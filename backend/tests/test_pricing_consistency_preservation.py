"""
Pricing Consistency Fixes — Preservation Property Tests.

Property 2: Preservation — Existing Pricing Behavior Unchanged for Non-Bug Inputs.

METHODOLOGY: Observation-first
  1. These tests run on UNFIXED code and MUST PASS.
  2. They encode the current correct behavior that must be preserved after the fix.
  3. After the fix is applied, re-run these tests to confirm no regressions.

Preservation Properties Tested:
  P3.1 — Weighted PTF = Σ(kWh_h × PTF_h) / Σ(kWh_h) (ağırlıklı ortalama korunur)
  P3.2 — base_cost_tl = kWh × (PTF + YEKDEM) / 1000 per hour (saatlik hesaplama korunur)
  P3.3 — Dealer commission model unchanged (puan paylaşımı, maliyet tabanı)
  P3.4 — calculator.py untouched (scope dışı) — structural, not tested here
  P3.5 — Admin endpoints unchanged — structural, not tested here
  P3.6 — YEKDEM existing periods → same calculation logic
  P3.7 — Frontend BTV, KDV, tasarruf calculations unchanged — frontend, not tested here
  P3.8 — Cache mechanism unchanged

PBT Strategy:
  Random valid PTF (100–5000), YEKDEM (50–500), consumption (1–1000 kWh),
  multiplier (1.01–2.0), dealer_pct (0–10).

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8
"""

from __future__ import annotations

import calendar
import json
import os
from unittest.mock import patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from app.pricing.models import ImbalanceParams
from app.pricing.excel_parser import ParsedMarketRecord, ParsedConsumptionRecord
from app.pricing.pricing_engine import calculate_hourly_costs, calculate_weighted_prices
from app.pricing.imbalance import calculate_imbalance_cost


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


def _build_single_day_data(
    date: str = "2026-01-15",
    hours: int = 24,
    ptf: float = 1500.0,
    smf: float = 1600.0,
    kwh: float = 100.0,
):
    """Build a single day of market + consumption data (constant values)."""
    market = [_market(date, h, ptf, smf) for h in range(hours)]
    consumption = [_consumption(date, h, kwh) for h in range(hours)]
    return market, consumption


def _build_varied_day_data(
    date: str = "2026-01-15",
    ptf_values: list[float] | None = None,
    smf_values: list[float] | None = None,
    kwh_values: list[float] | None = None,
):
    """Build a single day with varied hourly values."""
    ptf_values = ptf_values or [1000 + h * 50 for h in range(24)]
    smf_values = smf_values or [1100 + h * 50 for h in range(24)]
    kwh_values = kwh_values or [50 + h * 10 for h in range(24)]

    market = [_market(date, h, ptf_values[h], smf_values[h]) for h in range(24)]
    consumption = [_consumption(date, h, kwh_values[h]) for h in range(24)]
    return market, consumption


# ═══════════════════════════════════════════════════════════════════════════════
# P3.1 — Weighted PTF Preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestWeightedPTFPreservation:
    """
    Preservation Property 3.1: Weighted PTF formula must be preserved.

    weighted_ptf = Σ(kWh_h × PTF_h) / Σ(kWh_h)

    This is the kWh-weighted average PTF, NOT arithmetic average.
    """

    def test_constant_ptf_weighted_equals_ptf(self):
        """When PTF is constant across all hours, weighted PTF = PTF."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        result = calculate_weighted_prices(market, consumption)

        assert result.weighted_ptf_tl_per_mwh == pytest.approx(1500.0, abs=0.01)
        assert result.total_consumption_kwh == pytest.approx(24 * 100.0, abs=0.01)

    def test_varied_ptf_weighted_formula(self):
        """Weighted PTF with varied hourly PTF and consumption."""
        ptf_values = [1000.0, 2000.0, 1500.0]
        kwh_values = [100.0, 200.0, 300.0]
        # weighted = (100*1000 + 200*2000 + 300*1500) / (100+200+300)
        # = (100000 + 400000 + 450000) / 600 = 950000 / 600 = 1583.33
        expected_weighted = (100*1000 + 200*2000 + 300*1500) / (100+200+300)

        market = [_market("2026-01-15", h, ptf_values[h], 1600.0) for h in range(3)]
        consumption = [_consumption("2026-01-15", h, kwh_values[h]) for h in range(3)]

        result = calculate_weighted_prices(market, consumption)
        assert result.weighted_ptf_tl_per_mwh == pytest.approx(expected_weighted, abs=0.01)

    @given(
        ptf=st.floats(min_value=100, max_value=5000, allow_nan=False, allow_infinity=False),
        kwh=st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_pbt_constant_ptf_weighted_equals_ptf(self, ptf, kwh):
        """PBT: For constant PTF, weighted PTF always equals PTF."""
        market, consumption = _build_single_day_data(ptf=ptf, kwh=kwh)
        result = calculate_weighted_prices(market, consumption)
        assert result.weighted_ptf_tl_per_mwh == pytest.approx(ptf, abs=0.01)

    @given(
        ptf1=st.floats(min_value=100, max_value=5000, allow_nan=False, allow_infinity=False),
        ptf2=st.floats(min_value=100, max_value=5000, allow_nan=False, allow_infinity=False),
        kwh1=st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False),
        kwh2=st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_pbt_weighted_ptf_formula_invariant(self, ptf1, ptf2, kwh1, kwh2):
        """PBT: Weighted PTF = Σ(kWh × PTF) / Σ(kWh) for any valid inputs."""
        market = [
            _market("2026-01-15", 0, ptf1, 1600.0),
            _market("2026-01-15", 1, ptf2, 1600.0),
        ]
        consumption = [
            _consumption("2026-01-15", 0, kwh1),
            _consumption("2026-01-15", 1, kwh2),
        ]

        result = calculate_weighted_prices(market, consumption)
        expected = (kwh1 * ptf1 + kwh2 * ptf2) / (kwh1 + kwh2)
        assert result.weighted_ptf_tl_per_mwh == pytest.approx(expected, abs=0.01)

    def test_total_consumption_kwh_preserved(self):
        """total_consumption_kwh = Σ(kWh_h)."""
        market, consumption = _build_single_day_data(kwh=150.0)
        result = calculate_weighted_prices(market, consumption)
        assert result.total_consumption_kwh == pytest.approx(24 * 150.0, abs=0.01)

    def test_total_cost_tl_preserved(self):
        """total_cost_tl = Σ(kWh × PTF / 1000)."""
        market, consumption = _build_single_day_data(ptf=2000.0, kwh=100.0)
        result = calculate_weighted_prices(market, consumption)
        # 24 hours × 100 kWh × 2000 TL/MWh / 1000 = 4800 TL
        expected = 24 * 100.0 * 2000.0 / 1000.0
        assert result.total_cost_tl == pytest.approx(expected, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# P3.2 — Hourly Base Cost Preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestHourlyBaseCostPreservation:
    """
    Preservation Property 3.2: base_cost_tl = kWh × (PTF + YEKDEM) / 1000 per hour.

    The hourly base cost formula must remain unchanged.
    """

    def test_hourly_base_cost_formula(self):
        """Each hour: base_cost_tl = kWh × (PTF + YEKDEM) / 1000."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
        )

        for entry in result.hour_costs:
            expected = entry.consumption_kwh * (entry.ptf_tl_per_mwh + 150.0) / 1000.0
            assert entry.base_cost_tl == pytest.approx(expected, abs=0.01), (
                f"Hour {entry.hour}: base_cost_tl ({entry.base_cost_tl}) != "
                f"kWh ({entry.consumption_kwh}) × (PTF ({entry.ptf_tl_per_mwh}) + "
                f"YEKDEM (150)) / 1000 = {expected}"
            )

    def test_total_base_cost_is_sum_of_hourly(self):
        """total_base_cost_tl = Σ(hourly base_cost_tl)."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
        )

        sum_hourly = sum(e.base_cost_tl for e in result.hour_costs)
        assert result.total_base_cost_tl == pytest.approx(sum_hourly, abs=0.02)

    @given(
        ptf=st.floats(min_value=100, max_value=5000, allow_nan=False, allow_infinity=False),
        yekdem=st.floats(min_value=50, max_value=500, allow_nan=False, allow_infinity=False),
        kwh=st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False),
        multiplier=st.floats(min_value=1.01, max_value=2.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_pbt_base_cost_formula(self, ptf, yekdem, kwh, multiplier):
        """PBT: For all valid inputs, base_cost = kWh × (PTF + YEKDEM) / 1000."""
        market = [_market("2026-01-15", 0, ptf, ptf + 100)]
        consumption = [_consumption("2026-01-15", 0, kwh)]
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=yekdem,
            multiplier=multiplier,
            imbalance_params=params,
        )

        assert len(result.hour_costs) == 1
        entry = result.hour_costs[0]
        expected = kwh * (ptf + yekdem) / 1000.0
        assert entry.base_cost_tl == pytest.approx(expected, abs=0.02)

    @given(
        ptf=st.floats(min_value=100, max_value=5000, allow_nan=False, allow_infinity=False),
        yekdem=st.floats(min_value=50, max_value=500, allow_nan=False, allow_infinity=False),
        kwh=st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False),
        multiplier=st.floats(min_value=1.01, max_value=2.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_pbt_total_base_cost_formula(self, ptf, yekdem, kwh, multiplier):
        """PBT: total_base_cost ≈ Σ(kWh × (PTF + YEKDEM) / 1000) within ±0.02 TL."""
        market, consumption = _build_single_day_data(ptf=ptf, smf=ptf + 100, kwh=kwh)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=yekdem,
            multiplier=multiplier,
            imbalance_params=params,
        )

        expected_total = 24 * kwh * (ptf + yekdem) / 1000.0
        assert result.total_base_cost_tl == pytest.approx(expected_total, abs=0.02)


# ═══════════════════════════════════════════════════════════════════════════════
# P3.3 — Dealer Commission Model Preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestDealerCommissionPreservation:
    """
    Preservation Property 3.3: Dealer commission model unchanged.

    Current model: dealer_commission = gross_margin × dealer_pct / 100
    (puan paylaşımı, maliyet tabanı — bilinçli tasarım kararı)

    NOTE: After the fix, dealer commission is capped to energy margin (safety guard).
    For normal cases (multiplier >= 1.01), commission < energy margin, so cap doesn't bite.
    The net margin formula changes (uses gross_margin_total instead of gross_margin_energy),
    but the dealer commission calculation base (gross_margin_energy) is preserved.
    """

    def test_dealer_commission_based_on_energy_margin(self):
        """dealer_commission is based on gross_margin_energy (sales - base_cost)."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
            dealer_commission_pct=5.0,
        )

        # gross_margin_energy = total_sales - total_base_cost (preserved)
        gross_margin = result.total_sales_revenue_tl - result.total_base_cost_tl
        expected_commission = gross_margin * 5.0 / 100.0

        # dealer_commission_total_tl should match
        assert result.dealer_commission_total_tl == pytest.approx(expected_commission, abs=0.02)

    def test_zero_dealer_commission(self):
        """dealer_commission_pct=0 → no commission deducted."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
            dealer_commission_pct=0.0,
        )

        assert result.dealer_commission_total_tl == pytest.approx(0.0, abs=0.02)

    @given(
        dealer_pct=st.floats(min_value=0, max_value=10, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=30)
    def test_pbt_dealer_commission_proportional(self, dealer_pct):
        """PBT: Dealer commission is proportional to energy gross margin."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
            dealer_commission_pct=dealer_pct,
        )

        gross = result.total_sales_revenue_tl - result.total_base_cost_tl
        raw_commission = gross * dealer_pct / 100.0
        # Safety guard: capped to max(0, energy_margin)
        expected_commission = max(0.0, min(raw_commission, max(0.0, gross)))
        assert result.dealer_commission_total_tl == pytest.approx(expected_commission, abs=0.02)


# ═══════════════════════════════════════════════════════════════════════════════
# P3.6 — YEKDEM Existing Periods Preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestYekdemExistingPreservation:
    """
    Preservation Property 3.6: When YEKDEM exists, same calculation logic applies.

    YEKDEM is added to PTF in base cost: base_cost = kWh × (PTF + YEKDEM) / 1000.
    """

    def test_yekdem_included_in_base_cost(self):
        """YEKDEM is added to PTF in hourly base cost calculation."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=200.0,
            multiplier=1.15,
            imbalance_params=params,
        )

        # Each hour: base_cost = 100 × (1500 + 200) / 1000 = 170.0
        for entry in result.hour_costs:
            assert entry.base_cost_tl == pytest.approx(170.0, abs=0.01)
            assert entry.yekdem_tl_per_mwh == 200.0

    def test_yekdem_zero_same_as_ptf_only(self):
        """YEKDEM=0 means base cost = kWh × PTF / 1000."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=0.0,
            multiplier=1.15,
            imbalance_params=params,
        )

        for entry in result.hour_costs:
            expected = 100.0 * 1500.0 / 1000.0  # 150.0
            assert entry.base_cost_tl == pytest.approx(expected, abs=0.01)

    @given(
        yekdem=st.floats(min_value=50, max_value=500, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=30)
    def test_pbt_yekdem_in_base_cost(self, yekdem):
        """PBT: YEKDEM always included in base cost formula."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=yekdem,
            multiplier=1.15,
            imbalance_params=params,
        )

        for entry in result.hour_costs:
            expected = 100.0 * (1500.0 + yekdem) / 1000.0
            assert entry.base_cost_tl == pytest.approx(expected, abs=0.02)


# ═══════════════════════════════════════════════════════════════════════════════
# P3.8 — Cache Mechanism Preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestCacheMechanismPreservation:
    """
    Preservation Property 3.8: Cache mechanism unchanged.

    Same parameters → cache_hit=True on second call.
    """

    def test_cache_hit_on_second_call(self):
        """Second call with same params returns cache_hit=True."""
        client = _create_test_client(seed_yekdem=True)

        payload = {
            "period": "2026-01",
            "multiplier": 1.15,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
            "dealer_commission_pct": 3.0,
        }

        # First call — cache miss
        resp1 = client.post("/api/pricing/analyze", json=payload)
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["cache_hit"] is False

        # Second call — cache hit
        resp2 = client.post("/api/pricing/analyze", json=payload)
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["cache_hit"] is True

    def test_different_params_no_cache_hit(self):
        """Different multiplier → no cache hit."""
        client = _create_test_client(seed_yekdem=True)

        payload1 = {
            "period": "2026-01",
            "multiplier": 1.15,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
        }
        payload2 = {**payload1, "multiplier": 1.20}

        resp1 = client.post("/api/pricing/analyze", json=payload1)
        assert resp1.status_code == 200
        assert resp1.json()["cache_hit"] is False

        resp2 = client.post("/api/pricing/analyze", json=payload2)
        assert resp2.status_code == 200
        assert resp2.json()["cache_hit"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# Gross Margin Backward Compat — total_gross_margin_tl alias preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestGrossMarginBackwardCompat:
    """
    Preservation: total_gross_margin_tl = total_sales - total_base_cost.

    After the fix, total_gross_margin_tl becomes a backward-compat alias
    for gross_margin_energy_total_tl. The VALUE must remain the same:
    sales - (PTF + YEKDEM) cost (energy gross margin, NOT total commercial margin).
    """

    def test_gross_margin_equals_sales_minus_base_cost(self):
        """total_gross_margin_tl = total_sales_revenue_tl - total_base_cost_tl."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
        )

        expected = result.total_sales_revenue_tl - result.total_base_cost_tl
        assert result.total_gross_margin_tl == pytest.approx(expected, abs=0.02)

    @given(
        ptf=st.floats(min_value=100, max_value=5000, allow_nan=False, allow_infinity=False),
        yekdem=st.floats(min_value=50, max_value=500, allow_nan=False, allow_infinity=False),
        kwh=st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False),
        multiplier=st.floats(min_value=1.01, max_value=2.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_pbt_gross_margin_invariant(self, ptf, yekdem, kwh, multiplier):
        """PBT: total_gross_margin_tl = sales - base_cost for all valid inputs."""
        market, consumption = _build_single_day_data(ptf=ptf, smf=ptf + 100, kwh=kwh)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=yekdem,
            multiplier=multiplier,
            imbalance_params=params,
        )

        expected = result.total_sales_revenue_tl - result.total_base_cost_tl
        assert result.total_gross_margin_tl == pytest.approx(expected, abs=0.02)


# ═══════════════════════════════════════════════════════════════════════════════
# Sales Revenue Preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestSalesRevenuePreservation:
    """
    Preservation: Sales revenue formula unchanged.

    sales_price_tl = kWh × (weighted_PTF + YEKDEM) × multiplier / 1000
    total_sales = Σ(hourly sales)
    """

    def test_hourly_sales_formula(self):
        """Each hour: sales = kWh × (weighted_PTF + YEKDEM) × multiplier / 1000."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
        )

        # weighted_ptf = 1500 (constant), energy_cost = 1500 + 150 = 1650
        # sales per hour = 100 × 1650 × 1.15 / 1000 = 189.75
        for entry in result.hour_costs:
            expected = 100.0 * (1500.0 + 150.0) * 1.15 / 1000.0
            assert entry.sales_price_tl == pytest.approx(expected, abs=0.01)

    def test_total_sales_is_sum_of_hourly(self):
        """total_sales_revenue_tl = Σ(hourly sales_price_tl)."""
        market, consumption = _build_single_day_data(ptf=1500.0, kwh=100.0)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
        )

        sum_hourly = sum(e.sales_price_tl for e in result.hour_costs)
        assert result.total_sales_revenue_tl == pytest.approx(sum_hourly, abs=0.02)

    @given(
        ptf=st.floats(min_value=100, max_value=5000, allow_nan=False, allow_infinity=False),
        yekdem=st.floats(min_value=50, max_value=500, allow_nan=False, allow_infinity=False),
        kwh=st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False),
        multiplier=st.floats(min_value=1.01, max_value=2.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_pbt_sales_formula(self, ptf, yekdem, kwh, multiplier):
        """PBT: total_sales ≈ 24 × kWh × (PTF + YEKDEM) × multiplier / 1000.

        Tolerance: ±0.50 TL to account for per-hour rounding (round to 2dp per hour,
        accumulated over 24 hours → up to 24 × 0.005 = 0.12 TL rounding error).
        """
        market, consumption = _build_single_day_data(ptf=ptf, smf=ptf + 100, kwh=kwh)
        params = ImbalanceParams(forecast_error_rate=0.0)

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=yekdem,
            multiplier=multiplier,
            imbalance_params=params,
        )

        expected = 24 * kwh * (ptf + yekdem) * multiplier / 1000.0
        assert result.total_sales_revenue_tl == pytest.approx(expected, abs=0.50)


# ═══════════════════════════════════════════════════════════════════════════════
# Imbalance Cost Preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestImbalanceCostPreservation:
    """
    Preservation: Imbalance calculation model (imbalance.py) unchanged.

    SMF mode: |weighted_SMF - weighted_PTF| × forecast_error_rate
    Flat mode: imbalance_cost_tl_per_mwh × forecast_error_rate
    """

    def test_flat_mode_imbalance(self):
        """Flat mode: imbalance = cost × error_rate."""
        result = calculate_imbalance_cost(
            weighted_ptf=1500.0,
            weighted_smf=1600.0,
            params=ImbalanceParams(
                forecast_error_rate=0.05,
                imbalance_cost_tl_per_mwh=50.0,
                smf_based_imbalance_enabled=False,
            ),
        )
        assert result == pytest.approx(2.5)  # 50 × 0.05

    def test_smf_mode_imbalance(self):
        """SMF mode: imbalance = |SMF - PTF| × error_rate."""
        result = calculate_imbalance_cost(
            weighted_ptf=1500.0,
            weighted_smf=1600.0,
            params=ImbalanceParams(
                forecast_error_rate=0.05,
                smf_based_imbalance_enabled=True,
            ),
        )
        assert result == pytest.approx(5.0)  # |1600-1500| × 0.05

    def test_imbalance_deducted_from_net_margin(self):
        """Imbalance share is deducted from net margin in engine.

        NOTE: After the fix, imbalance has a floor of weighted_ptf * 0.01.
        With PTF=1500, floor = 15.0 TL/MWh. The flat mode gives 50*0.05=2.5,
        so the floor (15.0) applies instead.
        """
        market, consumption = _build_single_day_data(ptf=1500.0, smf=1600.0, kwh=100.0)
        params = ImbalanceParams(
            forecast_error_rate=0.05,
            imbalance_cost_tl_per_mwh=50.0,
            smf_based_imbalance_enabled=False,
        )

        result = calculate_hourly_costs(
            market, consumption,
            yekdem_tl_per_mwh=150.0,
            multiplier=1.15,
            imbalance_params=params,
            dealer_commission_pct=0.0,
        )

        # Imbalance floor: max(2.5, 1500*0.01) = max(2.5, 15.0) = 15.0 TL/MWh
        # total_kwh = 24 × 100 = 2400
        # imbalance_share = 15.0 × 2400 / 1000 = 36.0 TL
        assert result.imbalance_cost_total_tl == pytest.approx(36.0, abs=0.02)

        # Net margin = gross_margin_total (= energy since dist=0) - dealer(0) - imbalance(36)
        gross = result.total_gross_margin_tl
        expected_net = gross - 0.0 - 36.0
        assert result.total_net_margin_tl == pytest.approx(expected_net, abs=0.02)


# ═══════════════════════════════════════════════════════════════════════════════
# Analyze Endpoint Integration Preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnalyzeEndpointPreservation:
    """
    Preservation: Analyze endpoint returns correct structure and values
    when YEKDEM exists and inputs are valid.
    """

    def test_analyze_returns_expected_structure(self):
        """Analyze response has all required top-level fields."""
        client = _create_test_client(seed_yekdem=True)

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
        # Required top-level fields
        assert "status" in data
        assert "period" in data
        assert "weighted_prices" in data
        assert "supplier_cost" in data
        assert "pricing" in data
        assert "time_zone_breakdown" in data
        assert "loss_map" in data
        assert "risk_score" in data
        assert "safe_multiplier" in data
        assert "warnings" in data
        assert "data_quality" in data
        assert "cache_hit" in data

    def test_analyze_pricing_backward_compat_fields(self):
        """PricingSummary has backward-compatible field names."""
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

        pricing = resp.json()["pricing"]

        # These backward-compat fields must exist
        assert "multiplier" in pricing
        assert "sales_price_tl_per_mwh" in pricing
        assert "gross_margin_tl_per_mwh" in pricing
        assert "dealer_commission_tl_per_mwh" in pricing
        assert "net_margin_tl_per_mwh" in pricing
        assert "total_sales_tl" in pricing
        assert "total_cost_tl" in pricing
        assert "total_gross_margin_tl" in pricing
        assert "total_dealer_commission_tl" in pricing
        assert "total_net_margin_tl" in pricing

    def test_analyze_supplier_cost_structure(self):
        """SupplierCostSummary has correct fields and values."""
        client = _create_test_client(seed_yekdem=True, yekdem_value=150.0)

        payload = {
            "period": "2026-01",
            "multiplier": 1.15,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
        }

        resp = client.post("/api/pricing/analyze", json=payload)
        assert resp.status_code == 200

        sc = resp.json()["supplier_cost"]
        assert sc["yekdem_tl_per_mwh"] == 150.0
        assert sc["total_cost_tl_per_mwh"] == pytest.approx(
            sc["weighted_ptf_tl_per_mwh"] + sc["yekdem_tl_per_mwh"] + sc["imbalance_tl_per_mwh"],
            abs=0.01,
        )

    def test_analyze_multiplier_in_pricing(self):
        """Multiplier in pricing matches request multiplier."""
        client = _create_test_client(seed_yekdem=True)

        payload = {
            "period": "2026-01",
            "multiplier": 1.20,
            "use_template": True,
            "template_name": "3_vardiya_sanayi",
            "template_monthly_kwh": 100000,
        }

        resp = client.post("/api/pricing/analyze", json=payload)
        assert resp.status_code == 200
        assert resp.json()["pricing"]["multiplier"] == 1.20


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
    """Create a FastAPI TestClient with in-memory DB seeded with test data."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    with patch.dict(os.environ, {
        "ADMIN_API_KEY_ENABLED": "false",
        "API_KEY_ENABLED": "false",
    }):
        from app.main import app as fastapi_app
        from app.database import Base, get_db
        from app.pricing.schemas import (
            HourlyMarketPrice,
            MonthlyYekdemPrice,
            ProfileTemplate,
        )
        from app.pricing.profile_templates import _normalize
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
        client._test_session = session
        client._test_app = fastapi_app

        return client
