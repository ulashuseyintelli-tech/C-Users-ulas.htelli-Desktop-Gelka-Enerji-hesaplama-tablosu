"""
Property-Based Tests for Invoice Reconciliation Engine.

Hypothesis ile core recon modüllerindeki invariant'lar zorlanır.
Tüm property'ler design.md'deki Properties 1-24'e referans verir.

Test scope:
- Parser invariants (multiplier, kWh, hour range, parse stats)
- Splitter month grouping invariants
- Classifier T1/T2/T3 partition invariants
- Reconciler tolerance/severity invariants
- Comparator arithmetic invariants
- Fail-closed market-data invariant

NOT: API/router test edilmez — sadece pure domain logic.
"""

from __future__ import annotations

import calendar
from datetime import datetime
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.recon.classifier import classify_period_records
from app.recon.comparator import compare_costs
from app.recon.cost_engine import check_quote_eligibility
from app.recon.parser import _parse_datetime, _parse_kwh_value
from app.recon.reconciler import (
    _classify_severity,
    calculate_effective_price,
    reconcile_consumption,
)
from app.recon.schemas import (
    ComparisonConfig,
    HourlyRecord,
    InvoiceInput,
    PtfCostResult,
    ReconciliationStatus,
    Severity,
    TimeZoneSummary,
    ToleranceConfig,
    YekdemCostResult,
)
from app.recon.splitter import (
    _calculate_expected_hours,
    split_by_month,
    validate_period_completeness,
)

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")

# Settings for PBT — keep examples low for fast CI
PBT_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    deadline=2000,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Strategies
# ═══════════════════════════════════════════════════════════════════════════════


@st.composite
def hourly_record_strategy(draw, year=2026, month=None) -> HourlyRecord:
    """Generate a valid HourlyRecord."""
    if month is None:
        month = draw(st.integers(min_value=1, max_value=12))
    days_in_month = calendar.monthrange(year, month)[1]
    day = draw(st.integers(min_value=1, max_value=days_in_month))
    hour = draw(st.integers(min_value=0, max_value=23))
    kwh = draw(st.decimals(
        min_value=Decimal("0"), max_value=Decimal("10000"),
        allow_nan=False, allow_infinity=False, places=3,
    ))
    has_multiplier = draw(st.booleans())
    multiplier = None
    if has_multiplier:
        multiplier = draw(st.decimals(
            min_value=Decimal("1"), max_value=Decimal("1000"),
            allow_nan=False, allow_infinity=False, places=2,
        ))

    ts = datetime(year, month, day, hour, 0, 0, tzinfo=ISTANBUL_TZ)
    return HourlyRecord(
        timestamp=ts,
        date=ts.strftime("%Y-%m-%d"),
        hour=hour,
        period=ts.strftime("%Y-%m"),
        consumption_kwh=kwh,
        multiplier=multiplier,
    )


@st.composite
def kwh_string_strategy(draw) -> tuple[Decimal, str]:
    """Generate (value, formatted_string) pairs in Turkish locale."""
    integer_part = draw(st.integers(min_value=0, max_value=999_999))
    decimal_part = draw(st.integers(min_value=0, max_value=999))
    value = Decimal(f"{integer_part}.{decimal_part:03d}")

    fmt = draw(st.sampled_from(["plain_dot", "tr_comma", "tr_full"]))
    if fmt == "plain_dot":
        text = f"{integer_part}.{decimal_part:03d}"
    elif fmt == "tr_comma":
        text = f"{integer_part},{decimal_part:03d}"
    else:  # tr_full: thousands dot, comma decimal
        if integer_part >= 1000:
            int_str = f"{integer_part:,}".replace(",", ".")
            text = f"{int_str},{decimal_part:03d}"
        else:
            text = f"{integer_part},{decimal_part:03d}"
    return value, text


# ═══════════════════════════════════════════════════════════════════════════════
# Parser PBT — Properties 2, 3, 4, 5, 6
# ═══════════════════════════════════════════════════════════════════════════════


class TestParserProperties:
    """Parser invariant'ları."""

    @PBT_SETTINGS
    @given(
        year=st.integers(min_value=2020, max_value=2030),
        month=st.integers(min_value=1, max_value=12),
        day=st.integers(min_value=1, max_value=28),  # safe for all months
        hour=st.integers(min_value=0, max_value=23),
        minute=st.integers(min_value=0, max_value=59),
        second=st.integers(min_value=0, max_value=59),
    )
    def test_property_3_date_parsing_round_trip(self, year, month, day, hour, minute, second):
        """Property 3: DD/MM/YYYY HH:MM:SS round-trip preserves datetime."""
        formatted = f"{day:02d}/{month:02d}/{year} {hour:02d}:{minute:02d}:{second:02d}"
        parsed = _parse_datetime(formatted)
        assert parsed is not None
        assert parsed.year == year
        assert parsed.month == month
        assert parsed.day == day
        assert parsed.hour == hour
        assert parsed.minute == minute
        assert parsed.second == second
        # IC-2: result is timezone-aware Europe/Istanbul
        assert parsed.tzinfo is not None

    @PBT_SETTINGS
    @given(value_text=kwh_string_strategy())
    def test_property_4_kwh_parsing_round_trip(self, value_text):
        """Property 4: Turkish locale kWh format → parse → original ±0.01."""
        value, text = value_text
        parsed = _parse_kwh_value(text)
        assert parsed is not None
        assert abs(parsed - value) <= Decimal("0.01"), (
            f"Round-trip failed: text={text!r}, expected={value}, got={parsed}"
        )

    @PBT_SETTINGS
    @given(hour=st.integers(min_value=0, max_value=23))
    def test_property_6_hour_range_invariant(self, hour):
        """Property 6: All parsed hours are in [0, 23]."""
        # Build a date string with this hour
        text = f"15/01/2026 {hour:02d}:00:00"
        parsed = _parse_datetime(text)
        assert parsed is not None
        assert 0 <= parsed.hour <= 23

    @PBT_SETTINGS
    @given(invalid_hour=st.integers(min_value=24, max_value=99))
    def test_invalid_hour_returns_none(self, invalid_hour):
        """Hour > 23 → datetime construction fails → None."""
        text = f"15/01/2026 {invalid_hour:02d}:00:00"
        parsed = _parse_datetime(text)
        assert parsed is None

    @PBT_SETTINGS
    @given(
        record=hourly_record_strategy(),
        multiplier_meta=st.decimals(
            min_value=Decimal("1"), max_value=Decimal("10000"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
    )
    def test_property_2_multiplier_never_applied(self, record, multiplier_meta):
        """Property 2: Multiplier is metadata-only, never applied to kWh.

        Even if multiplier is set, consumption_kwh equals raw value (not raw × multiplier).
        """
        # Replace multiplier metadata
        record_with_mult = record.model_copy(update={"multiplier": multiplier_meta})

        # Classify — multiplier should not affect kWh totals
        summary = classify_period_records([record_with_mult])
        # Total kWh equals the record's consumption_kwh (unchanged by multiplier)
        assert summary.total_kwh == record_with_mult.consumption_kwh


# ═══════════════════════════════════════════════════════════════════════════════
# Splitter PBT — Properties 7, 8, 9, 24
# ═══════════════════════════════════════════════════════════════════════════════


class TestSplitterProperties:
    """Splitter invariant'ları."""

    @PBT_SETTINGS
    @given(records=st.lists(hourly_record_strategy(year=2026), min_size=0, max_size=100))
    def test_property_7_no_records_lost(self, records):
        """Property 7: split → all records preserved (sum of group sizes == input)."""
        result = split_by_month(records)
        total = sum(len(v) for v in result.values())
        assert total == len(records)

    @PBT_SETTINGS
    @given(records=st.lists(hourly_record_strategy(year=2026), min_size=1, max_size=50))
    def test_property_7_same_period_in_group(self, records):
        """Property 7: every record in a group has the same YYYY-MM period."""
        result = split_by_month(records)
        for period, group in result.items():
            for rec in group:
                assert rec.period == period

    @PBT_SETTINGS
    @given(records=st.lists(hourly_record_strategy(year=2026), min_size=2, max_size=50))
    def test_property_9_chronological_period_ordering(self, records):
        """Property 9: periods returned in ascending chronological order."""
        result = split_by_month(records)
        periods = list(result.keys())
        assert periods == sorted(periods)

    @PBT_SETTINGS
    @given(
        year=st.integers(min_value=2020, max_value=2030),
        month=st.integers(min_value=1, max_value=12),
    )
    def test_property_8_expected_hours_calculation(self, year, month):
        """Property 8: expected_hours = days_in_month × 24 (DST-aware)."""
        days_in_month = calendar.monthrange(year, month)[1]
        expected = _calculate_expected_hours(year, month, days_in_month)
        # Türkiye sabit UTC+3 → expected == days × 24
        # Eğer DST varsa ±1 (IC-3 desteklenmeli)
        base = days_in_month * 24
        assert expected in (base - 1, base, base + 1)

    @PBT_SETTINGS
    @given(records=st.lists(hourly_record_strategy(year=2026), min_size=1, max_size=30))
    def test_property_24_order_independence(self, records):
        """Property 24: Results identical regardless of input order."""
        import random
        result_a = split_by_month(records)
        shuffled = list(records)
        random.shuffle(shuffled)
        result_b = split_by_month(shuffled)
        # Same periods
        assert set(result_a.keys()) == set(result_b.keys())
        # Same kWh totals per period
        for period in result_a:
            sum_a = sum(r.consumption_kwh for r in result_a[period])
            sum_b = sum(r.consumption_kwh for r in result_b[period])
            assert sum_a == sum_b


# ═══════════════════════════════════════════════════════════════════════════════
# Classifier PBT — Property 10 (T1/T2/T3 partition)
# ═══════════════════════════════════════════════════════════════════════════════


class TestClassifierProperties:
    """Classifier invariant'ları."""

    @PBT_SETTINGS
    @given(records=st.lists(
        hourly_record_strategy(year=2026, month=1),
        min_size=1, max_size=100,
    ))
    def test_property_10_partition_invariant(self, records):
        """Property 10: T1 + T2 + T3 == total_kwh (±0.01 kWh tolerance)."""
        summary = classify_period_records(records)
        partition_sum = summary.t1_kwh + summary.t2_kwh + summary.t3_kwh
        # IC-1: Decimal precision — should be exact
        assert abs(partition_sum - summary.total_kwh) <= Decimal("0.01")

    @PBT_SETTINGS
    @given(records=st.lists(
        hourly_record_strategy(year=2026, month=1),
        min_size=1, max_size=100,
    ))
    def test_property_10_percentages_sum_to_100(self, records):
        """Property 10: t1_pct + t2_pct + t3_pct == 100 (±0.1)."""
        summary = classify_period_records(records)
        if summary.total_kwh > Decimal("0"):
            pct_sum = summary.t1_pct + summary.t2_pct + summary.t3_pct
            assert abs(pct_sum - Decimal("100")) <= Decimal("0.1")

    @PBT_SETTINGS
    @given(records=st.lists(
        hourly_record_strategy(year=2026, month=1),
        min_size=1, max_size=50,
    ))
    def test_property_24_classifier_order_independence(self, records):
        """Classifier output identical regardless of input order."""
        import random
        result_a = classify_period_records(records)
        shuffled = list(records)
        random.shuffle(shuffled)
        result_b = classify_period_records(shuffled)
        assert result_a.t1_kwh == result_b.t1_kwh
        assert result_a.t2_kwh == result_b.t2_kwh
        assert result_a.t3_kwh == result_b.t3_kwh
        assert result_a.total_kwh == result_b.total_kwh

    @PBT_SETTINGS
    @given(
        positive_kwh=st.decimals(
            min_value=Decimal("0.001"), max_value=Decimal("100000"),
            allow_nan=False, allow_infinity=False, places=3,
        ),
    )
    def test_classifier_non_negative_outputs(self, positive_kwh):
        """All output kWh values must be non-negative (records are non-negative)."""
        ts = datetime(2026, 1, 15, 10, 0, 0, tzinfo=ISTANBUL_TZ)
        record = HourlyRecord(
            timestamp=ts, date="2026-01-15", hour=10, period="2026-01",
            consumption_kwh=positive_kwh, multiplier=None,
        )
        summary = classify_period_records([record])
        assert summary.t1_kwh >= Decimal("0")
        assert summary.t2_kwh >= Decimal("0")
        assert summary.t3_kwh >= Decimal("0")
        assert summary.total_kwh >= Decimal("0")


# ═══════════════════════════════════════════════════════════════════════════════
# Reconciler PBT — Properties 11, 12, 13, 14
# ═══════════════════════════════════════════════════════════════════════════════


class TestReconcilerProperties:
    """Reconciler invariant'ları."""

    @PBT_SETTINGS
    @given(
        unit_price=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("100"),
            allow_nan=False, allow_infinity=False, places=4,
        ),
        discount_pct=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("100"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
    )
    def test_property_11_effective_price_formula(self, unit_price, discount_pct):
        """Property 11: effective = unit_price × (1 - discount_pct / 100)."""
        result = calculate_effective_price(unit_price, discount_pct)
        expected = unit_price * (Decimal("1") - discount_pct / Decimal("100"))
        # Allow tiny rounding differences
        assert abs(result - expected) < Decimal("0.0001")

    @PBT_SETTINGS
    @given(
        unit_price=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("100"),
            allow_nan=False, allow_infinity=False, places=4,
        ),
    )
    def test_property_11_no_discount_returns_original(self, unit_price):
        """When discount is None or 0, effective_price == unit_price."""
        assert calculate_effective_price(unit_price, None) == unit_price
        assert calculate_effective_price(unit_price, Decimal("0")) == unit_price

    @PBT_SETTINGS
    @given(
        calculated=st.decimals(
            min_value=Decimal("0.01"), max_value=Decimal("100000"),
            allow_nan=False, allow_infinity=False, places=3,
        ),
        declared=st.decimals(
            min_value=Decimal("0.01"), max_value=Decimal("100000"),
            allow_nan=False, allow_infinity=False, places=3,
        ),
    )
    def test_property_12_delta_correctness(self, calculated, declared):
        """Property 12: delta_kwh = calculated - declared, delta_pct = delta / declared * 100."""
        summary = TimeZoneSummary(
            period="2026-01",
            t1_kwh=calculated, t2_kwh=Decimal("0"), t3_kwh=Decimal("0"),
            total_kwh=calculated,
            t1_pct=Decimal("100"), t2_pct=Decimal("0"), t3_pct=Decimal("0"),
        )
        invoice = InvoiceInput(period="2026-01", declared_total_kwh=declared)
        config = ToleranceConfig()
        items = reconcile_consumption(summary, invoice, config)
        assert len(items) == 1
        item = items[0]
        # delta_kwh = excel - invoice
        expected_delta = float(calculated - declared)
        assert abs(item.delta_kwh - expected_delta) < 0.001
        # delta_pct
        if declared > Decimal("0"):
            expected_pct = float((calculated - declared) / declared * Decimal("100"))
            assert abs(item.delta_pct - expected_pct) < 0.01

    @PBT_SETTINGS
    @given(
        calculated=st.decimals(
            min_value=Decimal("100"), max_value=Decimal("10000"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
        delta=st.decimals(
            min_value=Decimal("-1000"), max_value=Decimal("1000"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
        pct_tol=st.decimals(
            min_value=Decimal("0.5"), max_value=Decimal("10"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
        abs_tol=st.decimals(
            min_value=Decimal("0.5"), max_value=Decimal("50"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
    )
    def test_property_13_match_iff_both_tolerances(self, calculated, delta, pct_tol, abs_tol):
        """Property 13: MATCH iff |delta_pct| <= P AND |delta_kwh| <= A."""
        declared = calculated + delta
        assume(declared > Decimal("10"))  # avoid edge cases

        summary = TimeZoneSummary(
            period="2026-01",
            t1_kwh=calculated, t2_kwh=Decimal("0"), t3_kwh=Decimal("0"),
            total_kwh=calculated,
            t1_pct=Decimal("100"), t2_pct=Decimal("0"), t3_pct=Decimal("0"),
        )
        invoice = InvoiceInput(period="2026-01", declared_total_kwh=declared)
        config = ToleranceConfig(pct_tolerance=pct_tol, abs_tolerance_kwh=abs_tol)
        items = reconcile_consumption(summary, invoice, config)
        item = items[0]

        abs_delta_kwh = abs(Decimal(str(item.delta_kwh)))
        abs_delta_pct = abs(Decimal(str(item.delta_pct)))

        if abs_delta_pct <= pct_tol and abs_delta_kwh <= abs_tol:
            assert item.status == ReconciliationStatus.MATCH
            assert item.severity is None
        else:
            assert item.status == ReconciliationStatus.MISMATCH
            assert item.severity is not None

    @PBT_SETTINGS
    @given(
        abs_pct=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("100"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
        abs_kwh=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("1000"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
    )
    def test_property_14_severity_thresholds(self, abs_pct, abs_kwh):
        """Property 14:
        - CRITICAL if pct > 5 OR kwh > 20
        - WARNING if pct > 2 OR kwh > 5 (and not CRITICAL)
        - LOW otherwise
        """
        result = _classify_severity(abs_pct, abs_kwh)
        if abs_pct > Decimal("5") or abs_kwh > Decimal("20"):
            assert result == Severity.CRITICAL
        elif abs_pct > Decimal("2") or abs_kwh > Decimal("5"):
            assert result == Severity.WARNING
        else:
            assert result == Severity.LOW


# ═══════════════════════════════════════════════════════════════════════════════
# Comparator PBT — Properties 19, 20
# ═══════════════════════════════════════════════════════════════════════════════


class TestComparatorProperties:
    """Comparator arithmetic invariant'ları."""

    @PBT_SETTINGS
    @given(
        total_kwh=st.decimals(
            min_value=Decimal("1"), max_value=Decimal("100000"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
        unit_price=st.decimals(
            min_value=Decimal("0.01"), max_value=Decimal("100"),
            allow_nan=False, allow_infinity=False, places=4,
        ),
        dist_price=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("10"),
            allow_nan=False, allow_infinity=False, places=4,
        ),
        ptf_cost=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("1000000"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
        yekdem_cost=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("100000"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
        margin=st.decimals(
            min_value=Decimal("1"), max_value=Decimal("2"),
            allow_nan=False, allow_infinity=False, places=4,
        ),
    )
    def test_property_19_cost_formulas(self, total_kwh, unit_price, dist_price, ptf_cost, yekdem_cost, margin):
        """Property 19: invoice_energy = T × EP, gelka_energy = (PC + YC) × M."""
        config = ComparisonConfig(gelka_margin_multiplier=margin)
        result = compare_costs(
            total_kwh=total_kwh,
            effective_unit_price=unit_price,
            distribution_unit_price=dist_price,
            ptf_cost_tl=ptf_cost,
            yekdem_cost_tl=yekdem_cost,
            config=config,
        )
        assert result is not None
        # invoice_energy = T × EP (allow rounding)
        expected_invoice_energy = float((total_kwh * unit_price).quantize(Decimal("0.01")))
        assert abs(result.invoice_energy_tl - expected_invoice_energy) < 0.02
        # gelka_energy = (PC + YC) × M
        expected_gelka_energy = float(((ptf_cost + yekdem_cost) * margin).quantize(Decimal("0.01")))
        assert abs(result.gelka_energy_tl - expected_gelka_energy) < 0.02

    @PBT_SETTINGS
    @given(
        total_kwh=st.decimals(
            min_value=Decimal("100"), max_value=Decimal("10000"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
        unit_price=st.decimals(
            min_value=Decimal("1"), max_value=Decimal("10"),
            allow_nan=False, allow_infinity=False, places=4,
        ),
        ptf_cost=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("100000"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
        yekdem_cost=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("10000"),
            allow_nan=False, allow_infinity=False, places=2,
        ),
    )
    def test_property_20_message_direction(self, total_kwh, unit_price, ptf_cost, yekdem_cost):
        """Property 20: 'Tasarruf' if invoice > gelka, 'Mevcut tedarikçi avantajlı' otherwise."""
        config = ComparisonConfig(gelka_margin_multiplier=Decimal("1.05"))
        result = compare_costs(
            total_kwh=total_kwh,
            effective_unit_price=unit_price,
            distribution_unit_price=Decimal("1"),  # constant
            ptf_cost_tl=ptf_cost,
            yekdem_cost_tl=yekdem_cost,
            config=config,
        )
        assert result is not None
        if result.diff_tl > 0.01:
            assert "Tasarruf" in result.message
        elif result.diff_tl < -0.01:
            assert "Mevcut tedarikçi avantajlı" in result.message

    @PBT_SETTINGS
    @given(
        total_kwh=st.decimals(
            min_value=Decimal("0"), max_value=Decimal("0"),
            allow_nan=False, allow_infinity=False,
        ),
    )
    def test_zero_kwh_returns_none(self, total_kwh):
        """When total_kwh is 0, comparator returns None (no comparison possible)."""
        result = compare_costs(
            total_kwh=total_kwh,
            effective_unit_price=Decimal("1.95"),
            distribution_unit_price=Decimal("1.21"),
            ptf_cost_tl=Decimal("100"),
            yekdem_cost_tl=Decimal("10"),
            config=ComparisonConfig(),
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# Fail-Closed Market Data PBT — Property 18
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailClosedProperties:
    """Property 18: Fail-closed quote blocking."""

    @PBT_SETTINGS
    @given(
        ptf_sufficient=st.booleans(),
        yekdem_available=st.booleans(),
    )
    def test_property_18_fail_closed_logic(self, ptf_sufficient, yekdem_available):
        """quote_blocked iff PTF insufficient OR YEKDEM unavailable."""
        ptf = PtfCostResult(
            total_ptf_cost_tl=1000.0 if ptf_sufficient else 0.0,
            weighted_avg_ptf_tl_per_mwh=2500.0 if ptf_sufficient else 0.0,
            hours_matched=744 if ptf_sufficient else 0,
            hours_missing_ptf=0 if ptf_sufficient else 744,
            missing_ptf_pct=0.0 if ptf_sufficient else 100.0,
            ptf_data_sufficient=ptf_sufficient,
            warning=None if ptf_sufficient else "missing",
        )
        yekdem = YekdemCostResult(
            yekdem_tl_per_mwh=500.0 if yekdem_available else 0.0,
            total_yekdem_cost_tl=100.0 if yekdem_available else 0.0,
            available=yekdem_available,
        )
        blocked, reason = check_quote_eligibility(ptf, yekdem)

        if ptf_sufficient and yekdem_available:
            assert blocked is False
            assert reason is None
        else:
            assert blocked is True
            assert reason is not None
            if not ptf_sufficient:
                assert "PTF" in reason
            if not yekdem_available:
                assert "YEKDEM" in reason

    @PBT_SETTINGS
    @given(
        hours_total=st.integers(min_value=24, max_value=744),
        hours_missing=st.integers(min_value=0, max_value=744),
    )
    def test_property_18_no_quote_when_all_ptf_missing(self, hours_total, hours_missing):
        """If all hours missing PTF, quote must be blocked."""
        assume(hours_missing <= hours_total)
        hours_matched = hours_total - hours_missing
        # Can only generate quote if at least 1 hour matched
        ptf_sufficient = hours_matched > 0
        ptf = PtfCostResult(
            total_ptf_cost_tl=100.0 if ptf_sufficient else 0.0,
            weighted_avg_ptf_tl_per_mwh=2500.0 if ptf_sufficient else 0.0,
            hours_matched=hours_matched,
            hours_missing_ptf=hours_missing,
            missing_ptf_pct=hours_missing / hours_total * 100.0,
            ptf_data_sufficient=ptf_sufficient,
            warning=None,
        )
        yekdem = YekdemCostResult(
            yekdem_tl_per_mwh=500.0,
            total_yekdem_cost_tl=10.0,
            available=True,
        )
        blocked, reason = check_quote_eligibility(ptf, yekdem)
        if not ptf_sufficient:
            # Completely missing → must block
            assert blocked is True
            assert "PTF" in reason
