"""
Invoice Reconciliation Engine — T1/T2/T3 Classifier.

İKİNCİ BİR T1/T2/T3 TANIMI YOKTUR.
Mevcut `app.pricing.time_zones.classify_hour()` ve `app.pricing.models.TimeZone`
doğrudan kullanılır. Bu modül sadece recon pipeline'a uygun bir wrapper sağlar.

IC-1: Tüm toplamlar Decimal ile hesaplanır.
"""

from __future__ import annotations

from decimal import Decimal

from ..pricing.models import TimeZone
from ..pricing.time_zones import classify_hour
from .schemas import HourlyRecord, TimeZoneSummary


def classify_period_records(records: list[HourlyRecord]) -> TimeZoneSummary:
    """Dönem kayıtlarını T1/T2/T3'e sınıflandır ve topla.

    Mevcut classify_hour() fonksiyonunu kullanır — ikinci bir T1/T2/T3
    tanımı YAPILMAZ.

    IC-1: Tüm toplamlar Decimal aritmetik ile hesaplanır.

    Args:
        records: Tek bir döneme ait HourlyRecord listesi (splitter çıktısı)

    Returns:
        TimeZoneSummary with T1/T2/T3 kWh totals and percentages

    Invariant:
        t1_kwh + t2_kwh + t3_kwh == total_kwh (±0.01 kWh)
    """
    if not records:
        return TimeZoneSummary(
            period="",
            t1_kwh=Decimal("0"),
            t2_kwh=Decimal("0"),
            t3_kwh=Decimal("0"),
            total_kwh=Decimal("0"),
            t1_pct=Decimal("0"),
            t2_pct=Decimal("0"),
            t3_pct=Decimal("0"),
        )

    t1_sum = Decimal("0")
    t2_sum = Decimal("0")
    t3_sum = Decimal("0")

    for record in records:
        # Reuse existing classify_hour — NO second T1/T2/T3 definition
        zone = classify_hour(record.hour)

        if zone == TimeZone.T1:
            t1_sum += record.consumption_kwh
        elif zone == TimeZone.T2:
            t2_sum += record.consumption_kwh
        else:  # TimeZone.T3
            t3_sum += record.consumption_kwh

    total = t1_sum + t2_sum + t3_sum

    # Calculate percentages (avoid division by zero)
    if total > Decimal("0"):
        t1_pct = (t1_sum / total * Decimal("100")).quantize(Decimal("0.01"))
        t2_pct = (t2_sum / total * Decimal("100")).quantize(Decimal("0.01"))
        t3_pct = Decimal("100") - t1_pct - t2_pct  # Ensure sum = 100%
    else:
        t1_pct = Decimal("0")
        t2_pct = Decimal("0")
        t3_pct = Decimal("0")

    period = records[0].period if records else ""

    return TimeZoneSummary(
        period=period,
        t1_kwh=t1_sum,
        t2_kwh=t2_sum,
        t3_kwh=t3_sum,
        total_kwh=total,
        t1_pct=t1_pct,
        t2_pct=t2_pct,
        t3_pct=t3_pct,
    )
