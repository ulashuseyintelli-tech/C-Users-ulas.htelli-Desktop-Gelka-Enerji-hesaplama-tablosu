"""
Recon iç tutarlılık — comparator.py + classifier.py yuvarlama = ROUND_HALF_UP.

Tam yarım-kuruş sınırında (x.xx5) HALF_UP yukarı yuvarlar; eski context-default
HALF_EVEN (banker's) farklı sonuç verirdi. Bu test konvansiyonu kilitler.
(Gerçek veride bu sınıra neredeyse hiç düşülmez → golden testler değişmedi.)
"""
from datetime import datetime
from decimal import Decimal

from app.recon.comparator import compare_costs
from app.recon.classifier import classify_period_records
from app.recon.schemas import ComparisonConfig, HourlyRecord


def test_compare_costs_tl_rounds_half_up():
    # invoice_energy = 1 × 2.125 = 2.125 → HALF_UP=2.13 (HALF_EVEN olsaydı 2.12)
    cc = compare_costs(
        total_kwh=Decimal("1"),
        effective_unit_price=Decimal("2.125"),
        distribution_unit_price=Decimal("0"),
        ptf_cost_tl=Decimal("0"),
        yekdem_cost_tl=Decimal("0"),
        config=ComparisonConfig(gelka_margin_multiplier=Decimal("1.0")),
    )
    assert cc.invoice_energy_tl == 2.13   # HALF_UP ↑ (banker's: 2.12)
    assert cc.invoice_total_tl == 2.13


def _rec(hour: int, kwh: str) -> HourlyRecord:
    return HourlyRecord(
        timestamp=datetime(2099, 1, 1, hour), date="2099-01-01",
        hour=hour, period="2099-01", consumption_kwh=Decimal(kwh),
    )


def test_classify_pct_rounds_half_up():
    # t1=11.245 / total=100 × 100 = 11.245 → HALF_UP=11.25 (HALF_EVEN olsaydı 11.24)
    summ = classify_period_records([_rec(10, "11.245"), _rec(19, "88.755")])  # 10→T1, 19→T2
    assert summ.t1_pct == Decimal("11.25")  # HALF_UP ↑ (banker's: 11.24)
