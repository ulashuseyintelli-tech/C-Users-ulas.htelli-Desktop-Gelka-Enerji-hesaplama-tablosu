"""
Unit tests for recon splitter and classifier.

Tests:
- Normal month split and expected hour count
- Missing hour detection
- Duplicate hour detection
- T1/T2/T3 aggregation with Decimal precision
- Partition invariant: T1 + T2 + T3 == total
- Chronological ordering
- Empty records handling
"""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.recon.classifier import classify_period_records
from app.recon.schemas import HourlyRecord
from app.recon.splitter import (
    split_by_month,
    validate_period_completeness,
    _calculate_expected_hours,
)

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _make_record(year: int, month: int, day: int, hour: int, kwh: str = "10.5") -> HourlyRecord:
    """Create a test HourlyRecord."""
    ts = datetime(year, month, day, hour, 0, 0, tzinfo=ISTANBUL_TZ)
    return HourlyRecord(
        timestamp=ts,
        date=ts.strftime("%Y-%m-%d"),
        hour=hour,
        period=ts.strftime("%Y-%m"),
        consumption_kwh=Decimal(kwh),
        multiplier=None,
    )


def _make_full_month(year: int, month: int, kwh: str = "10.0") -> list[HourlyRecord]:
    """Generate a complete month of hourly records."""
    import calendar
    days = calendar.monthrange(year, month)[1]
    records = []
    for day in range(1, days + 1):
        for hour in range(24):
            records.append(_make_record(year, month, day, hour, kwh))
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# Splitter Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestSplitByMonth:
    """Tests for split_by_month function."""

    def test_single_month(self):
        """Single month records stay in one group."""
        records = [_make_record(2026, 1, d, h) for d in range(1, 4) for h in range(24)]
        result = split_by_month(records)
        assert list(result.keys()) == ["2026-01"]
        assert len(result["2026-01"]) == 3 * 24

    def test_multi_month_split(self):
        """Records from multiple months are split correctly."""
        records = [
            _make_record(2026, 1, 15, 10),
            _make_record(2026, 2, 5, 14),
            _make_record(2026, 1, 20, 8),
            _make_record(2026, 3, 1, 0),
        ]
        result = split_by_month(records)
        assert list(result.keys()) == ["2026-01", "2026-02", "2026-03"]
        assert len(result["2026-01"]) == 2
        assert len(result["2026-02"]) == 1
        assert len(result["2026-03"]) == 1

    def test_chronological_ordering(self):
        """Periods are returned in chronological order."""
        records = [
            _make_record(2026, 3, 1, 0),
            _make_record(2026, 1, 1, 0),
            _make_record(2026, 2, 1, 0),
        ]
        result = split_by_month(records)
        assert list(result.keys()) == ["2026-01", "2026-02", "2026-03"]

    def test_records_sorted_within_period(self):
        """Records within a period are sorted by timestamp."""
        records = [
            _make_record(2026, 1, 5, 10),
            _make_record(2026, 1, 1, 0),
            _make_record(2026, 1, 3, 15),
        ]
        result = split_by_month(records)
        timestamps = [r.timestamp for r in result["2026-01"]]
        assert timestamps == sorted(timestamps)

    def test_empty_records(self):
        """Empty input returns empty dict."""
        result = split_by_month([])
        assert result == {}

    def test_no_records_lost(self):
        """All records are preserved after split (Property 7)."""
        records = [_make_record(2026, m, 1, 0) for m in range(1, 7)]
        result = split_by_month(records)
        total = sum(len(v) for v in result.values())
        assert total == len(records)


class TestValidatePeriodCompleteness:
    """Tests for validate_period_completeness function."""

    def test_normal_month_complete(self):
        """January 2026 (31 days) complete → no gaps."""
        records = _make_full_month(2026, 1)
        stats = validate_period_completeness("2026-01", records)
        assert stats.period == "2026-01"
        assert stats.expected_hours == 31 * 24
        assert stats.record_count == 31 * 24
        assert stats.missing_hours == []
        assert stats.duplicate_hours == []
        assert stats.has_gaps is False

    def test_february_2026_complete(self):
        """February 2026 (28 days) complete."""
        records = _make_full_month(2026, 2)
        stats = validate_period_completeness("2026-02", records)
        assert stats.expected_hours == 28 * 24
        assert stats.record_count == 28 * 24
        assert stats.has_gaps is False

    def test_missing_hours_detected(self):
        """Missing hours are detected and reported."""
        # Create Jan 2026 but skip day 15 entirely
        records = [
            _make_record(2026, 1, d, h)
            for d in range(1, 32)
            for h in range(24)
            if d != 15
        ]
        stats = validate_period_completeness("2026-01", records)
        assert stats.has_gaps is True
        assert len(stats.missing_hours) == 24
        assert "2026-01-15 00:00" in stats.missing_hours
        assert "2026-01-15 23:00" in stats.missing_hours

    def test_duplicate_hours_detected(self):
        """Duplicate timestamps are detected."""
        records = _make_full_month(2026, 1)
        # Add a duplicate for Jan 1 hour 0
        records.append(_make_record(2026, 1, 1, 0, "5.0"))
        stats = validate_period_completeness("2026-01", records)
        assert len(stats.duplicate_hours) == 1
        assert "2026-01-01 00:00" in stats.duplicate_hours

    def test_expected_hours_calculation(self):
        """IC-3: Expected hours for various months."""
        # Türkiye sabit UTC+3, DST yok → her zaman gün×24
        assert _calculate_expected_hours(2026, 1, 31) == 31 * 24
        assert _calculate_expected_hours(2026, 2, 28) == 28 * 24
        assert _calculate_expected_hours(2026, 3, 31) == 31 * 24  # No DST in Turkey
        assert _calculate_expected_hours(2026, 10, 31) == 31 * 24  # No DST in Turkey


# ═══════════════════════════════════════════════════════════════════════════════
# Classifier Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestClassifyPeriodRecords:
    """Tests for classify_period_records function.

    Uses existing time_zones.py::classify_hour() — NO second T1/T2/T3 definition.
    T1: 06:00-16:59 (11 hours)
    T2: 17:00-21:59 (5 hours)
    T3: 22:00-05:59 (8 hours)
    """

    def test_t1_classification(self):
        """Hours 6-16 classified as T1."""
        records = [_make_record(2026, 1, 1, h, "1.0") for h in range(6, 17)]
        result = classify_period_records(records)
        assert result.t1_kwh == Decimal("11.0")
        assert result.t2_kwh == Decimal("0")
        assert result.t3_kwh == Decimal("0")

    def test_t2_classification(self):
        """Hours 17-21 classified as T2."""
        records = [_make_record(2026, 1, 1, h, "1.0") for h in range(17, 22)]
        result = classify_period_records(records)
        assert result.t1_kwh == Decimal("0")
        assert result.t2_kwh == Decimal("5.0")
        assert result.t3_kwh == Decimal("0")

    def test_t3_classification(self):
        """Hours 22-23, 0-5 classified as T3."""
        t3_hours = [22, 23, 0, 1, 2, 3, 4, 5]
        records = [_make_record(2026, 1, 1, h, "1.0") for h in t3_hours]
        result = classify_period_records(records)
        assert result.t1_kwh == Decimal("0")
        assert result.t2_kwh == Decimal("0")
        assert result.t3_kwh == Decimal("8.0")

    def test_full_day_partition(self):
        """Full 24-hour day: T1=11h, T2=5h, T3=8h, total=24h."""
        records = [_make_record(2026, 1, 1, h, "1.0") for h in range(24)]
        result = classify_period_records(records)
        assert result.t1_kwh == Decimal("11.0")
        assert result.t2_kwh == Decimal("5.0")
        assert result.t3_kwh == Decimal("8.0")
        assert result.total_kwh == Decimal("24.0")

    def test_partition_invariant(self):
        """Property 10: T1 + T2 + T3 == total (±0.01 kWh)."""
        records = _make_full_month(2026, 1, "3.7")
        result = classify_period_records(records)
        partition_sum = result.t1_kwh + result.t2_kwh + result.t3_kwh
        assert abs(partition_sum - result.total_kwh) <= Decimal("0.01")

    def test_percentage_sum(self):
        """Percentages sum to 100%."""
        records = _make_full_month(2026, 1, "5.0")
        result = classify_period_records(records)
        pct_sum = result.t1_pct + result.t2_pct + result.t3_pct
        assert abs(pct_sum - Decimal("100")) <= Decimal("0.1")

    def test_decimal_precision(self):
        """IC-1: All values are Decimal, not float."""
        records = [_make_record(2026, 1, 1, 10, "123.456")]
        result = classify_period_records(records)
        assert isinstance(result.t1_kwh, Decimal)
        assert isinstance(result.total_kwh, Decimal)
        assert isinstance(result.t1_pct, Decimal)

    def test_empty_records(self):
        """Empty input returns zero summary."""
        result = classify_period_records([])
        assert result.total_kwh == Decimal("0")
        assert result.t1_kwh == Decimal("0")
        assert result.t2_kwh == Decimal("0")
        assert result.t3_kwh == Decimal("0")

    def test_period_from_records(self):
        """Period is taken from first record."""
        records = [_make_record(2026, 3, 15, 10)]
        result = classify_period_records(records)
        assert result.period == "2026-03"

    def test_varying_consumption_values(self):
        """Different kWh values per hour are summed correctly."""
        records = [
            _make_record(2026, 1, 1, 8, "100.5"),   # T1
            _make_record(2026, 1, 1, 10, "200.3"),  # T1
            _make_record(2026, 1, 1, 18, "50.0"),   # T2
            _make_record(2026, 1, 1, 23, "30.2"),   # T3
        ]
        result = classify_period_records(records)
        assert result.t1_kwh == Decimal("100.5") + Decimal("200.3")
        assert result.t2_kwh == Decimal("50.0")
        assert result.t3_kwh == Decimal("30.2")
        assert result.total_kwh == Decimal("100.5") + Decimal("200.3") + Decimal("50.0") + Decimal("30.2")
