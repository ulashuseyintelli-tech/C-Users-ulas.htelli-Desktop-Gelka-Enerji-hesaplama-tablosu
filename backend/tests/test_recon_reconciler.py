"""
Unit tests for recon reconciler and cost engine.

Tests:
- Effective price calculation with discount
- Tolerance-based match/mismatch classification
- Severity levels: LOW, WARNING, CRITICAL
- NOT_CHECKED when declared value is None
- Fail-closed quote blocking
- IC-4 output fields validation
"""

from decimal import Decimal

import pytest

from app.recon.reconciler import (
    _classify_severity,
    calculate_effective_price,
    get_overall_severity,
    get_overall_status,
    reconcile_consumption,
)
from app.recon.schemas import (
    InvoiceInput,
    ReconciliationStatus,
    Severity,
    TimeZoneSummary,
    ToleranceConfig,
)
from app.recon.cost_engine import check_quote_eligibility
from app.recon.schemas import PtfCostResult, YekdemCostResult


# ═══════════════════════════════════════════════════════════════════════════════
# Effective Price Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestEffectivePrice:
    """Property 11: effective_price = unit_price × (1 - discount_pct / 100)."""

    def test_no_discount(self):
        result = calculate_effective_price(Decimal("3.08"), None)
        assert result == Decimal("3.08")

    def test_zero_discount(self):
        result = calculate_effective_price(Decimal("3.08"), Decimal("0"))
        assert result == Decimal("3.08")

    def test_cansu_su_discount(self):
        """Şubat fatura: 2.242 TL/kWh = 3.08 × (1 - 4.77/100) ≈ 2.933."""
        # Not: Cansu Su faturasında Şubat birim fiyat 2.242 TL/kWh (%4.77 iskonto)
        # Ama bu doğrudan girilmiş fiyat, iskonto ayrı hesaplanmaz
        result = calculate_effective_price(Decimal("3.08"), Decimal("4.77"))
        expected = Decimal("3.08") * (Decimal("1") - Decimal("4.77") / Decimal("100"))
        assert abs(result - expected) < Decimal("0.0001")

    def test_full_discount(self):
        result = calculate_effective_price(Decimal("5.0"), Decimal("100"))
        assert result == Decimal("0")


# ═══════════════════════════════════════════════════════════════════════════════
# Severity Classification Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestSeverityClassification:
    """Property 14: CRITICAL/WARNING/LOW boundaries."""

    def test_low_small_pct_small_kwh(self):
        assert _classify_severity(Decimal("1.5"), Decimal("3")) == Severity.LOW

    def test_warning_pct_above_2(self):
        assert _classify_severity(Decimal("3"), Decimal("4")) == Severity.WARNING

    def test_warning_kwh_above_5(self):
        assert _classify_severity(Decimal("1"), Decimal("6")) == Severity.WARNING

    def test_critical_pct_above_5(self):
        assert _classify_severity(Decimal("6"), Decimal("1")) == Severity.CRITICAL

    def test_critical_kwh_above_20(self):
        assert _classify_severity(Decimal("1"), Decimal("21")) == Severity.CRITICAL

    def test_boundary_pct_exactly_5(self):
        """Exactly 5% is WARNING, not CRITICAL (> 5 required)."""
        assert _classify_severity(Decimal("5"), Decimal("1")) == Severity.WARNING

    def test_boundary_kwh_exactly_20(self):
        """Exactly 20 kWh is WARNING, not CRITICAL (> 20 required)."""
        assert _classify_severity(Decimal("1"), Decimal("20")) == Severity.WARNING


# ═══════════════════════════════════════════════════════════════════════════════
# Reconciliation Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestReconcileConsumption:
    """Tests for reconcile_consumption function."""

    def _make_summary(self, t1=100, t2=50, t3=80):
        total = t1 + t2 + t3
        return TimeZoneSummary(
            period="2026-01",
            t1_kwh=Decimal(str(t1)),
            t2_kwh=Decimal(str(t2)),
            t3_kwh=Decimal(str(t3)),
            total_kwh=Decimal(str(total)),
            t1_pct=Decimal(str(round(t1/total*100, 2))),
            t2_pct=Decimal(str(round(t2/total*100, 2))),
            t3_pct=Decimal(str(round(t3/total*100, 2))),
        )

    def test_match_within_tolerance(self):
        """Property 13: MATCH when |diff_pct| <= P AND |diff_kwh| <= A."""
        summary = self._make_summary(100, 50, 80)
        invoice = InvoiceInput(
            period="2026-01",
            declared_total_kwh=Decimal("230.5"),  # diff = 230 - 230.5 = -0.5 kWh
        )
        config = ToleranceConfig(pct_tolerance=Decimal("1"), abs_tolerance_kwh=Decimal("1"))
        results = reconcile_consumption(summary, invoice, config)
        assert len(results) == 1
        assert results[0].status == ReconciliationStatus.MATCH
        assert results[0].severity is None

    def test_mismatch_exceeds_tolerance(self):
        """MISMATCH when diff exceeds tolerance."""
        summary = self._make_summary(100, 50, 80)  # total = 230
        invoice = InvoiceInput(
            period="2026-01",
            declared_total_kwh=Decimal("200"),  # diff = 30 kWh, 15%
        )
        config = ToleranceConfig()
        results = reconcile_consumption(summary, invoice, config)
        assert results[0].status == ReconciliationStatus.MISMATCH
        assert results[0].severity == Severity.CRITICAL  # >5% and >20 kWh

    def test_not_checked_when_none(self):
        """NOT_CHECKED when declared value is None (no item generated)."""
        summary = self._make_summary(100, 50, 80)
        invoice = InvoiceInput(period="2026-01")  # No declared values
        config = ToleranceConfig()
        results = reconcile_consumption(summary, invoice, config)
        assert len(results) == 0  # No items when nothing declared

    def test_ic4_output_fields(self):
        """IC-4: All required fields present in output."""
        summary = self._make_summary(100, 50, 80)
        invoice = InvoiceInput(
            period="2026-01",
            declared_t1_kwh=Decimal("99"),
        )
        config = ToleranceConfig()
        results = reconcile_consumption(summary, invoice, config)
        item = results[0]
        # IC-4 zorunlu alanlar
        assert hasattr(item, "excel_total_kwh")
        assert hasattr(item, "invoice_total_kwh")
        assert hasattr(item, "delta_kwh")
        assert hasattr(item, "delta_pct")
        assert hasattr(item, "severity")
        assert item.excel_total_kwh == 100.0
        assert item.invoice_total_kwh == 99.0
        assert item.delta_kwh == 1.0  # 100 - 99

    def test_multiple_fields_compared(self):
        """All declared fields are compared."""
        summary = self._make_summary(100, 50, 80)
        invoice = InvoiceInput(
            period="2026-01",
            declared_t1_kwh=Decimal("100"),
            declared_t2_kwh=Decimal("50"),
            declared_t3_kwh=Decimal("80"),
            declared_total_kwh=Decimal("230"),
        )
        config = ToleranceConfig()
        results = reconcile_consumption(summary, invoice, config)
        assert len(results) == 4
        assert all(r.status == ReconciliationStatus.MATCH for r in results)


# ═══════════════════════════════════════════════════════════════════════════════
# Fail-Closed Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFailClosed:
    """Property 18: Fail-closed quote blocking."""

    def test_blocked_when_ptf_missing(self):
        ptf = PtfCostResult(
            total_ptf_cost_tl=0, weighted_avg_ptf_tl_per_mwh=0,
            hours_matched=0, hours_missing_ptf=744,
            missing_ptf_pct=100, ptf_data_sufficient=False, warning="x"
        )
        yekdem = YekdemCostResult(yekdem_tl_per_mwh=500, total_yekdem_cost_tl=100, available=True)
        blocked, reason = check_quote_eligibility(ptf, yekdem)
        assert blocked is True
        assert "PTF" in reason

    def test_blocked_when_yekdem_missing(self):
        ptf = PtfCostResult(
            total_ptf_cost_tl=1000, weighted_avg_ptf_tl_per_mwh=2500,
            hours_matched=744, hours_missing_ptf=0,
            missing_ptf_pct=0, ptf_data_sufficient=True, warning=None
        )
        yekdem = YekdemCostResult(yekdem_tl_per_mwh=0, total_yekdem_cost_tl=0, available=False)
        blocked, reason = check_quote_eligibility(ptf, yekdem)
        assert blocked is True
        assert "YEKDEM" in reason

    def test_not_blocked_when_both_available(self):
        ptf = PtfCostResult(
            total_ptf_cost_tl=1000, weighted_avg_ptf_tl_per_mwh=2500,
            hours_matched=744, hours_missing_ptf=0,
            missing_ptf_pct=0, ptf_data_sufficient=True, warning=None
        )
        yekdem = YekdemCostResult(yekdem_tl_per_mwh=500, total_yekdem_cost_tl=100, available=True)
        blocked, reason = check_quote_eligibility(ptf, yekdem)
        assert blocked is False
        assert reason is None

    def test_blocked_when_both_missing(self):
        ptf = PtfCostResult(
            total_ptf_cost_tl=0, weighted_avg_ptf_tl_per_mwh=0,
            hours_matched=0, hours_missing_ptf=744,
            missing_ptf_pct=100, ptf_data_sufficient=False, warning="x"
        )
        yekdem = YekdemCostResult(yekdem_tl_per_mwh=0, total_yekdem_cost_tl=0, available=False)
        blocked, reason = check_quote_eligibility(ptf, yekdem)
        assert blocked is True
        assert "PTF" in reason and "YEKDEM" in reason
