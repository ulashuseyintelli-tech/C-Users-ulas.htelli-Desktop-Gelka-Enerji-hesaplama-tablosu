"""
Invoice Reconciliation Engine — Monthly Splitter.

IC-3: DST-aware saat sayısı doğrulaması.
- Normal ay: gün_sayısı × 24
- DST ileri geçiş ayı (Mart): gün_sayısı × 24 - 1
- DST geri geçiş ayı (Ekim/Kasım): gün_sayısı × 24 + 1

Kayıtları YYYY-MM dönemlerine göre gruplar, eksik/duplike saatleri tespit eder.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .schemas import HourlyRecord, PeriodStats

ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")


def split_by_month(
    records: list[HourlyRecord],
) -> dict[str, list[HourlyRecord]]:
    """Kayıtları YYYY-MM dönemlerine göre grupla.

    Returns:
        Kronolojik sıralı dict[period, records].
        Her grup içindeki kayıtlar timestamp'e göre sıralı.
    """
    groups: dict[str, list[HourlyRecord]] = defaultdict(list)
    for record in records:
        groups[record.period].append(record)

    # Sort periods chronologically
    sorted_periods = sorted(groups.keys())

    # Sort records within each period
    result: dict[str, list[HourlyRecord]] = {}
    for period in sorted_periods:
        result[period] = sorted(groups[period], key=lambda r: r.timestamp)

    return result


def validate_period_completeness(
    period: str, records: list[HourlyRecord]
) -> PeriodStats:
    """Dönem için beklenen saat sayısı vs mevcut kontrol.

    IC-3: DST geçişlerini hesaba katar.

    Args:
        period: "YYYY-MM" formatında dönem
        records: Bu döneme ait kayıtlar

    Returns:
        PeriodStats with missing/duplicate hour detection
    """
    year, month = int(period[:4]), int(period[5:7])
    days_in_month = calendar.monthrange(year, month)[1]

    # IC-3: DST-aware expected hours
    expected_hours = _calculate_expected_hours(year, month, days_in_month)

    # Build set of (date, hour) tuples from records
    seen_hours: dict[str, int] = {}  # "YYYY-MM-DD HH:00" → count
    for record in records:
        key = f"{record.date} {record.hour:02d}:00"
        seen_hours[key] = seen_hours.get(key, 0) + 1

    # Detect duplicates
    duplicate_hours = [k for k, v in seen_hours.items() if v > 1]

    # Detect missing hours — generate all expected (date, hour) pairs
    all_expected = _generate_expected_hours(year, month, days_in_month)
    missing_hours = [h for h in all_expected if h not in seen_hours]

    has_gaps = len(missing_hours) > 0

    return PeriodStats(
        period=period,
        record_count=len(records),
        expected_hours=expected_hours,
        missing_hours=missing_hours,
        duplicate_hours=duplicate_hours,
        has_gaps=has_gaps,
    )


def _calculate_expected_hours(year: int, month: int, days_in_month: int) -> int:
    """IC-3: DST-aware beklenen saat sayısı hesapla.

    Türkiye'de DST:
    - Mart son Pazar: saat 03:00'te ileri (02:00→03:00 atlanır, 23 saatlik gün)
    - Ekim son Pazar: saat 04:00'te geri (03:00→02:00 tekrar, 25 saatlik gün)

    Not: Türkiye 2016'dan beri kalıcı yaz saati uyguluyor (UTC+3 sabit).
    Bu nedenle DST geçişi YOKTUR. Ancak IC-3 gereği mimari olarak desteklenir.
    """
    # Türkiye 2016'dan beri DST uygulamıyor — sabit UTC+3.
    # Ancak IC-3 gereği DST-aware hesaplama altyapısı korunur.
    # Gerçek DST geçişi olup olmadığını zoneinfo'dan kontrol et.
    base_hours = days_in_month * 24

    # Check if any DST transition occurs in this month
    dst_offset = _get_dst_hour_adjustment(year, month, days_in_month)
    return base_hours + dst_offset


def _get_dst_hour_adjustment(year: int, month: int, days_in_month: int) -> int:
    """DST geçişi nedeniyle saat farkını hesapla.

    Returns:
        0: Normal ay (DST geçişi yok)
        -1: DST ileri geçiş (bir saat kayıp)
        +1: DST geri geçiş (bir saat fazla)
    """
    # Ayın ilk ve son anının UTC offset'ini karşılaştır
    first_day = datetime(year, month, 1, 0, 0, 0, tzinfo=ISTANBUL_TZ)
    last_day = datetime(year, month, days_in_month, 23, 0, 0, tzinfo=ISTANBUL_TZ)

    offset_start = first_day.utcoffset()
    offset_end = last_day.utcoffset()

    if offset_start is None or offset_end is None:
        return 0

    diff_seconds = (offset_end - offset_start).total_seconds()
    if diff_seconds > 0:
        # Offset increased → DST forward (spring) → lost 1 hour
        return -1
    elif diff_seconds < 0:
        # Offset decreased → DST backward (fall) → gained 1 hour
        return 1
    return 0


def _generate_expected_hours(year: int, month: int, days_in_month: int) -> list[str]:
    """Dönemdeki tüm beklenen (date, hour) çiftlerini üret.

    Returns:
        ["YYYY-MM-DD HH:00", ...] formatında sıralı liste
    """
    expected: list[str] = []
    for day in range(1, days_in_month + 1):
        for hour in range(24):
            d = date(year, month, day)
            expected.append(f"{d.isoformat()} {hour:02d}:00")
    return expected
