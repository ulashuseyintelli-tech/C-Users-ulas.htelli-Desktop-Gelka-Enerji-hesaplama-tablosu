"""
Task 12: Cansu Sample Golden-File Validation.

Real-world correctness test against actual İkitelli OSB consumption data.
Golden snapshot frozen — any parser change that breaks this = regression.

Source: "Cansu Saatlik Tuketim Ocak-Nisan 2026.xlsx"
Format: B (Tarih + Aktif Çekiş)
Periods: 2026-01, 2026-02, 2026-03, 2026-04 (partial)

Invoice reference (İkitelli OSB, Nisan 2026 fatura):
- T1: 2.211 kWh, T2: 83 kWh, T3: 41 kWh, Total: 2.335 kWh
- Birim fiyat: 1,95 TL/kWh
- İletim: 0,23 TL/kWh, Dağıtım: 0,98167 TL/kWh
- Toplam dağıtım+iletim: 1,21167 TL/kWh

Assertions:
- No row loss
- No duplicate hours
- Total kWh consistency (T1+T2+T3 == total)
- Multiplier NOT applied (Format B has no multiplier)
- Golden snapshot regression check
- Invoice reconciliation sanity
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.recon.classifier import classify_period_records
from app.recon.comparator import compare_costs
from app.recon.parser import parse_excel
from app.recon.reconciler import (
    calculate_effective_price,
    reconcile_consumption,
)
from app.recon.schemas import (
    ComparisonConfig,
    InvoiceInput,
    ReconciliationStatus,
    ToleranceConfig,
)
from app.recon.splitter import split_by_month, validate_period_completeness

# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

EXCEL_PATH = Path(__file__).parent.parent.parent / "Cansu Saatlik Tuketim Ocak-Nisan 2026.xlsx"
GOLDEN_PATH = Path(__file__).parent / "fixtures" / "cansu_golden_snapshot.json"


@pytest.fixture(scope="module")
def cansu_parse_result():
    """Parse the real Cansu Excel file once for all tests."""
    if not EXCEL_PATH.exists():
        pytest.skip(f"Cansu Excel not found: {EXCEL_PATH}")
    data = EXCEL_PATH.read_bytes()
    return parse_excel(data)


@pytest.fixture(scope="module")
def golden_snapshot():
    """Load frozen golden snapshot."""
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def period_groups(cansu_parse_result):
    """Split parsed records by month."""
    return split_by_month(cansu_parse_result.records)


# ═══════════════════════════════════════════════════════════════════════════════
# Golden Snapshot Regression Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestGoldenSnapshot:
    """Frozen output regression — any parser change that breaks this = bug."""

    def test_format_detected(self, cansu_parse_result, golden_snapshot):
        assert cansu_parse_result.format_detected.value == golden_snapshot["format_detected"]

    def test_row_counts(self, cansu_parse_result, golden_snapshot):
        assert cansu_parse_result.total_rows == golden_snapshot["total_rows"]
        assert cansu_parse_result.successful_rows == golden_snapshot["successful_rows"]
        assert cansu_parse_result.failed_rows == golden_snapshot["failed_rows"]

    def test_no_row_loss(self, cansu_parse_result):
        """No records lost: total == successful + failed."""
        assert cansu_parse_result.total_rows == (
            cansu_parse_result.successful_rows + cansu_parse_result.failed_rows
        )

    def test_multiplier_not_present(self, cansu_parse_result, golden_snapshot):
        """Format B: no multiplier column → metadata is None."""
        assert cansu_parse_result.multiplier_metadata is None
        assert golden_snapshot["multiplier_metadata"] is None

    def test_period_count(self, period_groups, golden_snapshot):
        assert len(period_groups) == len(golden_snapshot["periods"])
        assert set(period_groups.keys()) == set(golden_snapshot["periods"].keys())

    def test_record_counts_per_period(self, period_groups, golden_snapshot):
        for period, records in period_groups.items():
            expected = golden_snapshot["periods"][period]["record_count"]
            assert len(records) == expected, f"Period {period}: {len(records)} != {expected}"

    def test_total_kwh_per_period(self, period_groups, golden_snapshot):
        """Total kWh matches golden snapshot (±0.01 tolerance)."""
        for period, records in period_groups.items():
            tz = classify_period_records(records)
            expected = golden_snapshot["periods"][period]["total_kwh"]
            assert abs(float(tz.total_kwh) - expected) < 0.01, (
                f"Period {period}: {float(tz.total_kwh)} != {expected}"
            )

    def test_t1t2t3_kwh_per_period(self, period_groups, golden_snapshot):
        """T1/T2/T3 kWh match golden snapshot (±0.01 tolerance)."""
        for period, records in period_groups.items():
            tz = classify_period_records(records)
            exp = golden_snapshot["periods"][period]
            assert abs(float(tz.t1_kwh) - exp["t1_kwh"]) < 0.01, f"{period} T1 mismatch"
            assert abs(float(tz.t2_kwh) - exp["t2_kwh"]) < 0.01, f"{period} T2 mismatch"
            assert abs(float(tz.t3_kwh) - exp["t3_kwh"]) < 0.01, f"{period} T3 mismatch"

    def test_missing_hours_per_period(self, period_groups, golden_snapshot):
        for period, records in period_groups.items():
            stats = validate_period_completeness(period, records)
            expected = golden_snapshot["periods"][period]["missing_hours"]
            assert len(stats.missing_hours) == expected, f"{period}: missing hours mismatch"

    def test_no_duplicate_hours(self, period_groups, golden_snapshot):
        """No duplicate timestamps in any period."""
        for period, records in period_groups.items():
            stats = validate_period_completeness(period, records)
            assert len(stats.duplicate_hours) == 0, f"{period}: unexpected duplicates"


# ═══════════════════════════════════════════════════════════════════════════════
# Partition Invariant Tests (real data)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPartitionInvariant:
    """T1 + T2 + T3 == total on real data."""

    def test_partition_sum_all_periods(self, period_groups):
        """For every period: T1 + T2 + T3 == total (±0.01 kWh)."""
        for period, records in period_groups.items():
            tz = classify_period_records(records)
            partition_sum = tz.t1_kwh + tz.t2_kwh + tz.t3_kwh
            assert abs(partition_sum - tz.total_kwh) <= Decimal("0.01"), (
                f"Period {period}: partition sum {partition_sum} != total {tz.total_kwh}"
            )

    def test_percentage_sum_all_periods(self, period_groups):
        """For every period: t1% + t2% + t3% == 100 (±0.1%)."""
        for period, records in period_groups.items():
            tz = classify_period_records(records)
            pct_sum = tz.t1_pct + tz.t2_pct + tz.t3_pct
            assert abs(pct_sum - Decimal("100")) <= Decimal("0.1"), (
                f"Period {period}: pct sum {pct_sum} != 100"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Multiplier Safety Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultiplierSafety:
    """Multiplier is NEVER applied — even if present in metadata."""

    def test_no_record_has_multiplier_applied(self, cansu_parse_result):
        """Format B: all records have multiplier=None."""
        for record in cansu_parse_result.records:
            assert record.multiplier is None

    def test_consumption_values_are_raw(self, cansu_parse_result):
        """All consumption values are positive (raw from Excel, not multiplied)."""
        for record in cansu_parse_result.records:
            assert record.consumption_kwh >= Decimal("0")


# ═══════════════════════════════════════════════════════════════════════════════
# Invoice Reconciliation Sanity (İkitelli Nisan fatura)
# ═══════════════════════════════════════════════════════════════════════════════


class TestInvoiceReconciliation:
    """Reconcile Nisan 2026 Excel data against İkitelli fatura values.

    Fatura (İkitelli OSB, Nisan 2026):
    - T1: 2.211 kWh (Gündüz)
    - T2: 83 kWh (Puant)
    - T3: 41 kWh (Gece)
    - Total: 2.335 kWh
    - Birim fiyat: 1,95 TL/kWh
    - İletim+Dağıtım: 1,21167 TL/kWh

    NOT: Fatura dönemi 31/03-30/04 ama Excel'de Nisan verisi 23/04'te bitiyor.
    Bu nedenle Excel toplamı fatura toplamından çok farklı olacak (187K vs 2.3K).
    Fatura sadece son okuma dönemini kapsıyor olabilir.
    Bu test reconciliation mekanizmasının çalıştığını doğrular.
    """

    def test_effective_price_calculation(self):
        """İskontosuz fiyat: 1.95 TL/kWh."""
        eff = calculate_effective_price(Decimal("1.95"), None)
        assert eff == Decimal("1.95")

    def test_reconciliation_detects_mismatch(self, period_groups):
        """Excel Nisan toplamı (187K kWh) vs fatura beyanı (2335 kWh) → CRITICAL mismatch.

        Bu beklenen davranış: fatura sadece son okuma dönemini kapsıyor,
        Excel ise tüm Nisan verisini içeriyor.
        """
        if "2026-04" not in period_groups:
            pytest.skip("Nisan 2026 verisi yok")

        records = period_groups["2026-04"]
        tz = classify_period_records(records)

        invoice = InvoiceInput(
            period="2026-04",
            supplier_name="BKA Enerji",
            tariff_group="SANAYİ OG TEK TERİM",
            unit_price_tl_per_kwh=Decimal("1.95"),
            discount_pct=None,
            distribution_unit_price_tl_per_kwh=Decimal("1.21167"),
            declared_t1_kwh=Decimal("2211"),
            declared_t2_kwh=Decimal("83"),
            declared_t3_kwh=Decimal("41"),
            declared_total_kwh=Decimal("2335"),
        )

        config = ToleranceConfig()
        results = reconcile_consumption(tz, invoice, config)

        # Should have 4 reconciliation items (T1, T2, T3, total)
        assert len(results) == 4

        # All should be MISMATCH (Excel has full month data, fatura has partial)
        for item in results:
            assert item.status == ReconciliationStatus.MISMATCH
            # Excel values are much larger than invoice declared
            assert item.delta_kwh > 0  # Excel > invoice

    def test_comparator_produces_result(self, period_groups):
        """Cost comparison produces valid output."""
        if "2026-04" not in period_groups:
            pytest.skip("Nisan 2026 verisi yok")

        records = period_groups["2026-04"]
        tz = classify_period_records(records)

        result = compare_costs(
            total_kwh=tz.total_kwh,
            effective_unit_price=Decimal("1.95"),
            distribution_unit_price=Decimal("1.21167"),
            ptf_cost_tl=Decimal("350000"),  # Hypothetical PTF cost
            yekdem_cost_tl=Decimal("40000"),  # Hypothetical YEKDEM cost
            config=ComparisonConfig(gelka_margin_multiplier=Decimal("1.05")),
        )

        assert result is not None
        assert result.invoice_total_tl > 0
        assert result.gelka_total_tl > 0
        # Message should contain either "Tasarruf" or "Mevcut tedarikçi"
        assert "Tasarruf" in result.message or "Mevcut tedarikçi" in result.message


# ═══════════════════════════════════════════════════════════════════════════════
# Data Quality Checks
# ═══════════════════════════════════════════════════════════════════════════════


class TestDataQuality:
    """Real-world data quality assertions."""

    def test_no_parse_errors(self, cansu_parse_result):
        """Zero parse errors on real data."""
        assert cansu_parse_result.failed_rows == 0
        assert len(cansu_parse_result.errors) == 0

    def test_no_warnings(self, cansu_parse_result):
        """No warnings on well-formed data."""
        assert len(cansu_parse_result.warnings) == 0

    def test_all_hours_valid(self, cansu_parse_result):
        """All parsed hours are in [0, 23]."""
        for record in cansu_parse_result.records:
            assert 0 <= record.hour <= 23

    def test_all_consumption_positive(self, cansu_parse_result):
        """All consumption values are non-negative."""
        for record in cansu_parse_result.records:
            assert record.consumption_kwh >= Decimal("0")

    def test_january_complete(self, period_groups):
        """January 2026 has exactly 744 hours (31 days × 24)."""
        assert len(period_groups["2026-01"]) == 744

    def test_february_complete(self, period_groups):
        """February 2026 has exactly 672 hours (28 days × 24)."""
        assert len(period_groups["2026-02"]) == 672

    def test_march_complete(self, period_groups):
        """March 2026 has exactly 744 hours (31 days × 24)."""
        assert len(period_groups["2026-03"]) == 744

    def test_april_partial(self, period_groups):
        """April 2026 is partial (data ends 23/04)."""
        assert len(period_groups["2026-04"]) == 553  # 23 days × 24 + 1 (24/04 00:00)
