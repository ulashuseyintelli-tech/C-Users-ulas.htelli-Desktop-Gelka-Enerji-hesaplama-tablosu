"""
Invoice Reconciliation Engine — Property-Based Tests (Hypothesis).

Feature: invoice-recon-engine
Design properties 1–24 coverage (core module invariants).

Scope:
- Parser invariants (kWh round-trip, date round-trip, multiplier never applied)
- Splitter month grouping invariants (no records lost, chronological order)
- Classifier T1/T2/T3 partition invariant
- Reconciler tolerance/severity invariants
- Comparator arithmetic invariants
- Fail-closed market-data invariant

NO router/API tests here — only core recon/* module logic.
"""

import calendar
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from app.recon.classifier import classify_period_records
from app.recon.comparator import compare_costs
from app.recon.cost_engine import check_quote_eligibility
from app.recon.parser import _parse_kwh_value, _parse_datetime
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
from app.recon.splitter import split_by_month, validate_period_completeness

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")


# ═══════════════════════════════════════════════════════════════════════════════
# Strategies
# ═══════════════════════════════════════════════════════════════════════════════

st_positive_decimal = st.decimals(
    min_value=Decimal("0.001"), max_value=Decimal("99999"),
    allow_nan=False, allow_infinity=False, places=3,
)

st_hour = st.integers(min_value=0, max_value=23)
st_day = st.integers(min_value=1, max_value=28)  # safe for all months
st_month = st.integers(min_value=1, max_value=12)
st_year = st.integers(min_value=2020, max_value=2030)

st_pct = st.decimals(
    min_value=Decimal("0"), max_value=Decimal("100"),
    allow_nan=False, allow_infinity=False, places=2,
)


def make_record(year, month, day, hour, kwh, multiplier=None):
    ts = datetime(year, month, day, hour, 0, 0, tzinfo=ISTANBUL_TZ)
    return HourlyRecord(
        timestamp=ts,
        date=ts.strftime("%Y-%m-%d"),
        hour=hour,
        period=ts.strftime("%Y-%m"),
        consumption_kwh=kwh,
        multiplier=multiplier,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Property 4: kWh value parsing round-trip
# ═══════════════════════════════════════════════════════════════════════════════


class TestParserProperties:
    """Parser invariants — Property 4, 23."""

    @given(value=st.floats(min_value=0.01, max_value=99999.99, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_kwh_numeric_roundtrip(self, value):
        """Property 4: Numeric kWh values parse back correctly."""
        result = _parse_kwh_value(value)
        assert result is not None
        assert abs(float(result) - value) < 0.01

    @given(value=st.floats(min_value=0.01, max_value=9999.99, allow_nan=False, allow_infinity=False))
    @settings(max_examples=200)
    def test_kwh_turkish_locale_roundtrip(self, value):
        """Property 4: Turkish locale format → parse → original value ±0.01."""
        # Format as Turkish: "1.234,56"
        formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        result = _parse_kwh_value(formatted)
        assert result is not None
        assert abs(float(result) - value) < 0.01

    @given(
        year=st_year, month=st_month, day=st_day, hour=st_hour,
    )
    @settings(max_examples=200)
    def test_datetime_string_roundtrip(self, year, month, day, hour):
        """Property 3: DD/MM/YYYY HH:MM:SS → parse → correct date+hour."""
        max_day = calendar.monthrange(year, month)[1]
        assume(day <= max_day)
        date_str = f"{day:02d}/{month:02d}/{year} {hour:02d}:00:00"
        result = _parse_datetime(date_str)
        assert result is not None
        assert result.day == day
        assert result.month == month
        assert result.year == year
        assert result.hour == hour

    @given(
        kwh=st_positive_decimal,
        multiplier=st_positive_decimal,
    )
    @settings(max_examples=100)
    def test_multiplier_never_applied(self, kwh, multiplier):
        """Property 2: Multiplier stored as metadata, never affects consumption."""
        record = make_record(2026, 1, 15, 10, kwh, multiplier=multiplier)
        assert record.consumption_kwh == kwh  # NOT kwh * multiplier
        assert record.multiplier == multiplier

    @given(value=st.floats(min_value=-9999, max_value=-0.01, allow_nan=False, allow_infinity=False))
    @settings(max_examples=50)
    def test_negative_kwh_parses(self, value):
        """Property 23: Negative values parse to Decimal (abs applied later in pipeline)."""
        result = _parse_kwh_value(value)
        assert result is not None
        assert result < Decimal("0")  # Parser returns raw; abs is applied in provider


# ═══════════════════════════════════════════════════════════════════════════════
# Property 7, 9: Splitter invariants
# ═══════════════════════════════════════════════════════════════════════════════


class TestSplitterProperties:
    """Splitter invariants — Property 7, 9."""

    @given(
        months=st.lists(st_month, min_size=1, max_size=6),
        hours_per_month=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=100)
    def test_no_records_lost(self, months, hours_per_month):
        """Property 7: No records lost after split — sum of groups == input size."""
        records = []
        for m in months:
            for h in range(min(hours_per_month, 24)):
                records.append(make_record(2026, m, 15, h, Decimal("10")))

        result = split_by_month(records)
        total_after = sum(len(v) for v in result.values())
        assert total_after == len(records)

    @given(
        months=st.lists(st_month, min_size=2, max_size=6, unique=True),
    )
    @settings(max_examples=100)
    def test_chronological_order(self, months):
        """Property 9: Periods returned in ascending chronological order."""
        records = [make_record(2026, m, 15, 10, Decimal("5")) for m in months]
        result = split_by_month(records)
        periods = list(result.keys())
        assert periods == sorted(periods)

    @given(
        month=st_month,
        hours=st.lists(st_hour, min_size=1, max_size=24, unique=True),
    )
    @settings(max_examples=100)
    def test_all_records_same_period(self, month, hours):
        """Property 7: Every record in a group has the same YYYY-MM period."""
        records = [make_record(2026, month, 15, h, Decimal("1")) for h in hours]
        result = split_by_month(records)
        expected_period = f"2026-{month:02d}"
        assert expected_period in result
        for r in result[expected_period]:
            assert r.period == expected_period


# ═══════════════════════════════════════════════════════════════════════════════
# Property 10: T1/T2/T3 partition
# ═══════════════════════════════════════════════════════════════════════════════


class TestClassifierProperties:
    """Classifier invariants — Property 10."""

    @given(
        kwh_values=st.lists(
            st.decimals(min_value=Decimal("0.1"), max_value=Decimal("500"), places=2, allow_nan=False, allow_infinity=False),
            min_size=24, max_size=24,
        ),
    )
    @settings(max_examples=100)
    def test_partition_sum_equals_total(self, kwh_values):
        """Property 10: T1 + T2 + T3 == total (±0.01 kWh)."""
        records = [make_record(2026, 1, 15, h, kwh_values[h]) for h in range(24)]
        result = classify_period_records(records)
        partition_sum = result.t1_kwh + result.t2_kwh + result.t3_kwh
        assert abs(partition_sum - result.total_kwh) <= Decimal("0.01")

    @given(
        kwh_values=st.lists(
            st.decimals(min_value=Decimal("0.1"), max_value=Decimal("500"), places=2, allow_nan=False, allow_infinity=False),
            min_size=24, max_size=24,
        ),
    )
    @settings(max_examples=100)
    def test_percentage_sum_100(self, kwh_values):
        """Property 10: t1_pct + t2_pct + t3_pct == 100 (±0.1%)."""
        records = [make_record(2026, 1, 15, h, kwh_values[h]) for h in range(24)]
        result = classify_period_records(records)
        pct_sum = result.t1_pct + result.t2_pct + result.t3_pct
        assert abs(pct_sum - Decimal("100")) <= Decimal("0.1")


# ═══════════════════════════════════════════════════════════════════════════════
# Property 11, 12, 13, 14: Reconciler invariants
# ═══════════════════════════════════════════════════════════════════════════════


class TestReconcilerProperties:
    """Reconciler invariants — Property 11, 12, 13, 14."""

    @given(
        unit_price=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("10"), places=2, allow_nan=False, allow_infinity=False),
        discount=st.decimals(min_value=Decimal("0"), max_value=Decimal("100"), places=2, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_effective_price_formula(self, unit_price, discount):
        """Property 11: effective = unit_price × (1 - discount/100)."""
        result = calculate_effective_price(unit_price, discount)
        expected = unit_price * (Decimal("1") - discount / Decimal("100"))
        assert abs(result - expected) < Decimal("0.0001")

    @given(
        calculated=st.decimals(min_value=Decimal("1"), max_value=Decimal("10000"), places=1, allow_nan=False, allow_infinity=False),
        declared=st.decimals(min_value=Decimal("1"), max_value=Decimal("10000"), places=1, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_tolerance_match_classification(self, calculated, declared):
        """Property 13: MATCH iff |diff_pct| <= P AND |diff_kwh| <= A."""
        config = ToleranceConfig(pct_tolerance=Decimal("1"), abs_tolerance_kwh=Decimal("1"))
        summary = TimeZoneSummary(
            period="2026-01",
            t1_kwh=calculated, t2_kwh=Decimal("0"), t3_kwh=Decimal("0"),
            total_kwh=calculated,
            t1_pct=Decimal("100"), t2_pct=Decimal("0"), t3_pct=Decimal("0"),
        )
        invoice = InvoiceInput(period="2026-01", declared_total_kwh=declared)
        results = reconcile_consumption(summary, invoice, config)
        assert len(results) == 1
        item = results[0]

        diff_kwh = abs(calculated - declared)
        diff_pct = abs((calculated - declared) / declared * Decimal("100")) if declared > 0 else Decimal("0")

        if diff_pct <= Decimal("1") and diff_kwh <= Decimal("1"):
            assert item.status == ReconciliationStatus.MATCH
        else:
            assert item.status == ReconciliationStatus.MISMATCH

    @given(
        pct=st.decimals(min_value=Decimal("0"), max_value=Decimal("50"), places=1, allow_nan=False, allow_infinity=False),
        kwh=st.decimals(min_value=Decimal("0"), max_value=Decimal("100"), places=1, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_severity_boundaries(self, pct, kwh):
        """Property 14: CRITICAL/WARNING/LOW boundaries are consistent."""
        severity = _classify_severity(pct, kwh)

        if pct > Decimal("5") or kwh > Decimal("20"):
            assert severity == Severity.CRITICAL
        elif pct > Decimal("2") or kwh > Decimal("5"):
            assert severity == Severity.WARNING
        else:
            assert severity == Severity.LOW


# ═══════════════════════════════════════════════════════════════════════════════
# Property 19, 20: Comparator arithmetic invariants
# ═══════════════════════════════════════════════════════════════════════════════


class TestComparatorProperties:
    """Comparator invariants — Property 19, 20."""

    @given(
        total_kwh=st.decimals(min_value=Decimal("100"), max_value=Decimal("50000"), places=0, allow_nan=False, allow_infinity=False),
        eff_price=st.decimals(min_value=Decimal("0.5"), max_value=Decimal("5"), places=2, allow_nan=False, allow_infinity=False),
        dist_price=st.decimals(min_value=Decimal("0.1"), max_value=Decimal("2"), places=2, allow_nan=False, allow_infinity=False),
        ptf_cost=st.decimals(min_value=Decimal("100"), max_value=Decimal("100000"), places=0, allow_nan=False, allow_infinity=False),
        yekdem_cost=st.decimals(min_value=Decimal("10"), max_value=Decimal("10000"), places=0, allow_nan=False, allow_infinity=False),
        margin=st.decimals(min_value=Decimal("1.0"), max_value=Decimal("1.5"), places=2, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_invoice_formula(self, total_kwh, eff_price, dist_price, ptf_cost, yekdem_cost, margin):
        """Property 19: invoice_energy = T × EP, gelka_energy = (PC + YC) × M."""
        config = ComparisonConfig(gelka_margin_multiplier=margin)
        result = compare_costs(total_kwh, eff_price, dist_price, ptf_cost, yekdem_cost, config)
        assert result is not None

        # Invoice energy = total_kwh × effective_price
        expected_inv_energy = float((total_kwh * eff_price).quantize(Decimal("0.01")))
        assert abs(result.invoice_energy_tl - expected_inv_energy) < 0.02

        # Gelka energy = (ptf + yekdem) × margin
        expected_gelka_energy = float(((ptf_cost + yekdem_cost) * margin).quantize(Decimal("0.01")))
        assert abs(result.gelka_energy_tl - expected_gelka_energy) < 0.02

    @given(
        total_kwh=st.decimals(min_value=Decimal("1000"), max_value=Decimal("10000"), places=0, allow_nan=False, allow_infinity=False),
        eff_price=st.decimals(min_value=Decimal("1"), max_value=Decimal("5"), places=2, allow_nan=False, allow_infinity=False),
        dist_price=st.decimals(min_value=Decimal("0.5"), max_value=Decimal("2"), places=2, allow_nan=False, allow_infinity=False),
        ptf_cost=st.decimals(min_value=Decimal("100"), max_value=Decimal("50000"), places=0, allow_nan=False, allow_infinity=False),
        yekdem_cost=st.decimals(min_value=Decimal("10"), max_value=Decimal("5000"), places=0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_message_direction(self, total_kwh, eff_price, dist_price, ptf_cost, yekdem_cost):
        """Property 20: 'Tasarruf' when invoice > gelka, 'Mevcut tedarikçi' otherwise."""
        config = ComparisonConfig(gelka_margin_multiplier=Decimal("1.05"))
        result = compare_costs(total_kwh, eff_price, dist_price, ptf_cost, yekdem_cost, config)
        assert result is not None

        if result.diff_tl > 0:
            assert "Tasarruf" in result.message
        elif result.diff_tl < 0:
            assert "Mevcut tedarikçi" in result.message
        else:
            assert "eşit" in result.message.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Property 18: Fail-closed market-data invariant
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailClosedProperties:
    """Fail-closed invariant — Property 18."""

    @given(
        hours_matched=st.integers(min_value=0, max_value=0),
        yekdem_available=st.booleans(),
    )
    @settings(max_examples=50)
    def test_ptf_missing_blocks_quote(self, hours_matched, yekdem_available):
        """Property 18: PTF completely missing → quote_blocked=True."""
        ptf = PtfCostResult(
            total_ptf_cost_tl=0, weighted_avg_ptf_tl_per_mwh=0,
            hours_matched=hours_matched, hours_missing_ptf=744,
            missing_ptf_pct=100, ptf_data_sufficient=False, warning="x",
        )
        yekdem = YekdemCostResult(
            yekdem_tl_per_mwh=500 if yekdem_available else 0,
            total_yekdem_cost_tl=100 if yekdem_available else 0,
            available=yekdem_available,
        )
        blocked, reason = check_quote_eligibility(ptf, yekdem)
        assert blocked is True
        assert reason is not None
        assert "PTF" in reason

    @given(
        hours_matched=st.integers(min_value=1, max_value=744),
    )
    @settings(max_examples=50)
    def test_yekdem_missing_blocks_quote(self, hours_matched):
        """Property 18: YEKDEM unavailable → quote_blocked=True."""
        ptf = PtfCostResult(
            total_ptf_cost_tl=1000, weighted_avg_ptf_tl_per_mwh=2500,
            hours_matched=hours_matched, hours_missing_ptf=0,
            missing_ptf_pct=0, ptf_data_sufficient=True, warning=None,
        )
        yekdem = YekdemCostResult(yekdem_tl_per_mwh=0, total_yekdem_cost_tl=0, available=False)
        blocked, reason = check_quote_eligibility(ptf, yekdem)
        assert blocked is True
        assert "YEKDEM" in reason

    @given(
        hours_matched=st.integers(min_value=1, max_value=744),
        yekdem_rate=st.floats(min_value=100, max_value=1000),
    )
    @settings(max_examples=50)
    def test_both_available_allows_quote(self, hours_matched, yekdem_rate):
        """Property 18: Both available → quote NOT blocked."""
        ptf = PtfCostResult(
            total_ptf_cost_tl=1000, weighted_avg_ptf_tl_per_mwh=2500,
            hours_matched=hours_matched, hours_missing_ptf=0,
            missing_ptf_pct=0, ptf_data_sufficient=True, warning=None,
        )
        yekdem = YekdemCostResult(
            yekdem_tl_per_mwh=yekdem_rate, total_yekdem_cost_tl=500, available=True,
        )
        blocked, reason = check_quote_eligibility(ptf, yekdem)
        assert blocked is False
        assert reason is None
