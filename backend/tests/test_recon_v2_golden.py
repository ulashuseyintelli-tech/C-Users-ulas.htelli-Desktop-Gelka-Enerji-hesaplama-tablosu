"""
Task 9: v2 Cost Headline Golden Snapshot.

DETERMINISTIC v2 regression — separate from the v1 parser/classifier
golden snapshot (`cansu_golden_snapshot.json`). Both tests run side-by-side;
neither overwrites the other.

This snapshot exercises the full `/api/recon/analyze` pipeline end-to-end
(parse → split → classify → cost_engine_v2 → comparator_v2 → report_builder)
under controlled, deterministic PTF/YEKDEM seed:
- PTF: 3000.00 TL/MWh for every (period, date, hour) in the Excel
- YEKDEM: 400.00 TL/MWh for every period
- gelka_margin_multiplier: 1.05
- invoice_markup_factor: 1.20
   (declared_total_tl = round(reference_energy_cost_tl × 1.20, 2))

Closed-form verification (independent of code):
   reference_energy_cost = sum(consumption_kwh × PTF / 1000)
                         + total_kwh × YEKDEM / 1000
   With PTF=3000, YEKDEM=400 → ref = total_kwh × 3.4
   With invoice = ref × 1.20:
     supplier_markup_tl  = ref × 0.20
     supplier_markup_pct = 20.0
     gelka_estimate      = ref × 1.05
     potential_savings   = ref × 0.15

Regeneration:
   $env:RECON_V2_GOLDEN_REGEN = "1"
   python -m pytest tests/test_recon_v2_golden.py -k regenerate
   The flagged test will overwrite cansu_v2_golden_snapshot.json from the
   live response and fail with a regen notice. Inspect the diff, commit,
   then unset the flag and rerun normally.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
import app.pricing.schemas  # noqa: F401  (register pricing tables on Base)
from app.pricing.schemas import HourlyMarketPrice, MonthlyYekdemPrice

from app.recon.parser import parse_excel
from app.recon.router import _run_pipeline
from app.recon.schemas import ComparisonConfig, InvoiceInput, ReconRequest
from app.recon.splitter import split_by_month


# ═══════════════════════════════════════════════════════════════════════════════
# Constants — MUST match `_fixture_metadata` block in cansu_v2_golden_snapshot.json
# ═══════════════════════════════════════════════════════════════════════════════

PTF_TL_PER_MWH = 3000.0
YEKDEM_TL_PER_MWH = 400.0
GELKA_MULTIPLIER = Decimal("1.05")
INVOICE_MARKUP_FACTOR = Decimal("1.20")

# S5-R02A: kisi-adli EXCEL_PATH kaldirildi — kaynak sentetik fixture'dir.
GOLDEN_PATH = Path(__file__).parent / "fixtures" / "cansu_v2_golden_snapshot.json"

REGENERATE_FLAG = "RECON_V2_GOLDEN_REGEN"


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


def _round_currency_tl_half_up(d: Decimal) -> Decimal:
    """Local helper — same rounding rule as the production boundary."""
    from decimal import ROUND_HALF_UP
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _seed_deterministic(records, periods, db) -> None:
    """Seed PTF for every (period, date, hour) and YEKDEM per period.

    All values come from the constants above — no real EPİAŞ data, fully
    reproducible across machines.
    """
    for record in records:
        db.add(HourlyMarketPrice(
            period=record.period,
            date=record.date,
            hour=record.hour,
            ptf_tl_per_mwh=PTF_TL_PER_MWH,
            smf_tl_per_mwh=PTF_TL_PER_MWH + 100.0,
            currency="TRY",
            source="test",
            version=1,
            is_active=1,
        ))
    for period in periods:
        db.add(MonthlyYekdemPrice(
            period=period,
            yekdem_tl_per_mwh=YEKDEM_TL_PER_MWH,
            source="test",
        ))
    db.commit()


def _compute_invoice_total(reference_cost_tl: Decimal) -> Decimal:
    """declared_total_tl = round(ref × INVOICE_MARKUP_FACTOR, 2)."""
    return _round_currency_tl_half_up(reference_cost_tl * INVOICE_MARKUP_FACTOR)


# S5-R02A: kaynak artik SENTETIK (owner Bolum 2/6) — kisi adli gercek
# workbook bagimliligi ve "dosya yoksa skip" yolu kaldirildi. Golden
# snapshot resmi regen mekanizmasiyla (RECON_V2_GOLDEN_REGEN=1) sentetik
# kaynaktan yeniden uretildi.
from tests.sentetik_tuketim_fixture import build_sentetik_workbook_bytes


@pytest.fixture(scope="module")
def excel_bytes():
    return build_sentetik_workbook_bytes()


@pytest.fixture(scope="module")
def parse_result(excel_bytes):
    return parse_excel(excel_bytes)


def _reference_costs(parse_result):
    """Pre-compute reference costs by closed form so we can build invoices."""
    period_groups = split_by_month(parse_result.records)
    refs: dict[str, Decimal] = {}
    for period, records in period_groups.items():
        total_kwh = sum((r.consumption_kwh for r in records), Decimal("0"))
        ptf_part = sum(
            (r.consumption_kwh * Decimal(str(PTF_TL_PER_MWH)) / Decimal("1000")
             for r in records),
            Decimal("0"),
        )
        yekdem_part = total_kwh * Decimal(str(YEKDEM_TL_PER_MWH)) / Decimal("1000")
        refs[period] = ptf_part + yekdem_part
    return refs


@pytest.fixture(scope="module")
def reference_costs_by_period(parse_result):
    """Pre-compute reference costs by closed form so we can build invoices."""
    return _reference_costs(parse_result)


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def seeded_db(db_session, parse_result):
    """db_session with deterministic PTF + YEKDEM seeded for every Excel period."""
    period_groups = split_by_month(parse_result.records)
    _seed_deterministic(
        records=parse_result.records,
        periods=sorted(period_groups.keys()),
        db=db_session,
    )
    return db_session


@pytest.fixture()
def happy_path_response(excel_bytes, seeded_db, reference_costs_by_period):
    """Run the full v2 pipeline against deterministic DB → ReconReport JSON dict."""
    invoices = [
        InvoiceInput(
            period=period,
            declared_total_tl=_compute_invoice_total(ref),
        )
        for period, ref in reference_costs_by_period.items()
    ]
    request = ReconRequest(
        invoices=invoices,
        comparison=ComparisonConfig(gelka_margin_multiplier=GELKA_MULTIPLIER),
    )
    report = _run_pipeline(excel_bytes, request, seeded_db)
    return report.model_dump(mode="json")


@pytest.fixture(scope="module")
def golden_v2():
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _build_v2_snapshot(payload: dict) -> dict:
    """Strip the live response down to the v2-only contract subset."""
    periods_block: dict = {}
    for p in payload["periods"]:
        periods_block[p["period"]] = {
            "reference_energy_cost_tl": p["reference_energy_cost_tl"],
            "supplier_markup_tl": p["supplier_markup_tl"],
            "supplier_markup_pct": p["supplier_markup_pct"],
            "gelka_estimate_tl": p["gelka_estimate_tl"],
            "potential_savings_tl": p["potential_savings_tl"],
            "quote_blocked": p["quote_blocked"],
            "quote_block_reason": p["quote_block_reason"],
            "cost_inputs": p["cost_inputs"],
        }
    summary = payload.get("summary") or {}
    return {
        "_fixture_metadata": {
            "ptf_tl_per_mwh": PTF_TL_PER_MWH,
            "yekdem_tl_per_mwh": YEKDEM_TL_PER_MWH,
            "gelka_margin_multiplier": float(GELKA_MULTIPLIER),
            "invoice_markup_factor": float(INVOICE_MARKUP_FACTOR),
            "excel_source": "tests/sentetik_tuketim_fixture.py (sentetik, deterministik)",
            "notes": (
                "Deterministic synthetic PTF/YEKDEM seed. Numbers are reproducible: "
                "reference_energy_cost = sum(consumption × PTF / 1000) "
                "+ total_kwh × YEKDEM / 1000. "
                "declared_total_tl = round(ref × invoice_markup_factor, 2). "
                "gelka_estimate = round(ref × gelka_margin_multiplier, 2)."
            ),
        },
        "api_version": payload["api_version"],
        "status": payload["status"],
        "periods": periods_block,
        "summary": {
            "valid_period_count": summary.get("valid_period_count"),
            "partial_period_count": summary.get("partial_period_count"),
            "total_period_count": summary.get("total_period_count"),
            "total_reference_energy_cost_tl": summary.get("total_reference_energy_cost_tl"),
            "total_supplier_markup_tl": summary.get("total_supplier_markup_tl"),
            "total_gelka_estimate_tl": summary.get("total_gelka_estimate_tl"),
            "total_potential_savings_tl": summary.get("total_potential_savings_tl"),
            "annual_projection": summary.get("annual_projection"),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Snapshot regeneration (opt-in via env var — safe by default)
# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# S5-R02B: REGEN ARTIK BIR TEST DEGIL (owner Bolum 8).
#
# Onceki `test_regenerate_v2_golden_snapshot` varsayilanda skipif'ti ve her
# tam kosuya 1 kalici skip ekliyordu; regen bir DOGRULAMA degil, bir YAZMA
# islemidir. Artik acik opt-in yardimci komuttur:
#
#     python -m tests.regen_v2_golden
#
# Golden ESITLIGI ise asagidaki normal testlerle her kosuda dogrulanir.
# `regenerate_v2_golden_snapshot()` fonksiyonunu o komut kullanir.
# ═══════════════════════════════════════════════════════════════════════════
def regenerate_v2_golden_snapshot() -> str:
    """Golden snapshot'i sentetik kaynaktan yeniden uretir (TEST DEGIL).

    Fixture zincirinin (excel/seed/referans maliyet) birebir esdegeri burada
    acikca kurulur — pipeline ve deterministik seed AYNIDIR.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    import app.pricing.schemas  # noqa: F401

    veri = build_sentetik_workbook_bytes()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    try:
        parse_result = parse_excel(veri)
        gruplar = split_by_month(parse_result.records)
        _seed_deterministic(records=parse_result.records,
                            periods=sorted(gruplar.keys()), db=db)
        refler = _reference_costs(parse_result)
        invoices = [
            InvoiceInput(period=p_, declared_total_tl=_compute_invoice_total(r_))
            for p_, r_ in refler.items()
        ]
        request = ReconRequest(
            invoices=invoices,
            comparison=ComparisonConfig(gelka_margin_multiplier=GELKA_MULTIPLIER),
        )
        yanit = _run_pipeline(veri, request, db).model_dump(mode="json")
    finally:
        db.close()
        engine.dispose()
    new_snapshot = _build_v2_snapshot(yanit)
    GOLDEN_PATH.write_text(
        json.dumps(new_snapshot, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return str(GOLDEN_PATH)


# ═══════════════════════════════════════════════════════════════════════════════
# Happy-path snapshot regression
# ═══════════════════════════════════════════════════════════════════════════════


class TestApiVersionAndStatus:
    """Top-level contract: api_version=2 and status=ok under happy path."""

    def test_api_version_is_two(self, happy_path_response, golden_v2):
        assert happy_path_response["api_version"] == 2
        assert golden_v2["api_version"] == 2

    def test_status_is_ok(self, happy_path_response, golden_v2):
        assert happy_path_response["status"] == "ok"
        assert golden_v2["status"] == "ok"


class TestV2PeriodFields:
    """Per-period v2 cost-headline fields match the golden snapshot byte-for-byte."""

    def test_all_periods_present_in_golden(self, happy_path_response, golden_v2):
        live_periods = {p["period"] for p in happy_path_response["periods"]}
        assert live_periods == set(golden_v2["periods"].keys())

    @pytest.mark.parametrize(
        "field",
        [
            "reference_energy_cost_tl",
            "supplier_markup_tl",
            "supplier_markup_pct",
            "gelka_estimate_tl",
            "potential_savings_tl",
            "quote_blocked",
            "quote_block_reason",
        ],
    )
    def test_period_field_matches(self, happy_path_response, golden_v2, field):
        for p in happy_path_response["periods"]:
            expected = golden_v2["periods"][p["period"]][field]
            actual = p[field]
            assert actual == expected, (
                f"Period {p['period']} field '{field}': "
                f"live={actual} expected={expected}"
            )

    def test_cost_inputs_block_matches(self, happy_path_response, golden_v2):
        for p in happy_path_response["periods"]:
            expected = golden_v2["periods"][p["period"]]["cost_inputs"]
            actual = p["cost_inputs"]
            assert actual == expected, (
                f"Period {p['period']} cost_inputs mismatch: "
                f"live={actual} expected={expected}"
            )

    def test_cost_inputs_complete_true_for_all(self, happy_path_response):
        """Every period must report complete=True under happy-path seed."""
        for p in happy_path_response["periods"]:
            assert p["cost_inputs"]["complete"] is True, (
                f"Period {p['period']}: complete must be True under deterministic "
                f"happy-path seed"
            )

    def test_supplier_markup_pct_is_twenty_for_every_period(self, happy_path_response):
        """Closed-form: invoice = ref × 1.20 → markup_pct = 20.00 always."""
        for p in happy_path_response["periods"]:
            assert p["supplier_markup_pct"] == 20.0, (
                f"Period {p['period']}: expected 20.0, got {p['supplier_markup_pct']}"
            )


class TestV2Summary:
    """Multi-period summary v2 fields — counts, sums, and annual projection."""

    @pytest.mark.parametrize(
        "field",
        [
            "valid_period_count",
            "partial_period_count",
            "total_period_count",
            "total_reference_energy_cost_tl",
            "total_supplier_markup_tl",
            "total_gelka_estimate_tl",
            "total_potential_savings_tl",
        ],
    )
    def test_summary_field_matches(self, happy_path_response, golden_v2, field):
        live_summary = happy_path_response["summary"]
        expected = golden_v2["summary"][field]
        assert live_summary[field] == expected, (
            f"summary.{field}: live={live_summary[field]} expected={expected}"
        )

    def test_annual_projection_block_matches(self, happy_path_response, golden_v2):
        live = happy_path_response["summary"]["annual_projection"]
        expected = golden_v2["summary"]["annual_projection"]
        assert live == expected, (
            f"annual_projection mismatch: live={live} expected={expected}"
        )

    def test_annual_projection_label_exact_string(self, happy_path_response):
        """Disclaimer label is hukuki/positioning sabit — must be exactly this string."""
        live = happy_path_response["summary"]["annual_projection"]
        assert live is not None
        assert live["label"] == "tahmini yıllık projeksiyon"

    def test_annual_projection_based_on_periods_matches_valid_count(
        self, happy_path_response
    ):
        summary = happy_path_response["summary"]
        assert (
            summary["annual_projection"]["based_on_periods"]
            == summary["valid_period_count"]
        )


class TestFixtureMetadataIntegrity:
    """Snapshot must declare its own constants — self-describing test data."""

    def test_metadata_block_present(self, golden_v2):
        assert "_fixture_metadata" in golden_v2
        meta = golden_v2["_fixture_metadata"]
        assert meta["ptf_tl_per_mwh"] == PTF_TL_PER_MWH
        assert meta["yekdem_tl_per_mwh"] == YEKDEM_TL_PER_MWH
        assert meta["gelka_margin_multiplier"] == float(GELKA_MULTIPLIER)
        assert meta["invoice_markup_factor"] == float(INVOICE_MARKUP_FACTOR)


# ═══════════════════════════════════════════════════════════════════════════════
# v1 backward compatibility — assert v1 fields still emitted with v1 semantics
# ═══════════════════════════════════════════════════════════════════════════════


class TestV1FieldsUnchanged:
    """v1 contract preservation — every v1 field still emitted with the same shape."""

    def test_v1_top_level_fields_present(self, happy_path_response):
        for field in [
            "format_detected",
            "parse_stats",
            "periods",
            "warnings",
            "multiplier_metadata",
        ]:
            assert field in happy_path_response, f"v1 field {field} missing"

    def test_v1_parse_stats_shape(self, happy_path_response):
        ps = happy_path_response["parse_stats"]
        assert {"total_rows", "successful_rows", "failed_rows"}.issubset(ps.keys())

    def test_v1_period_fields_present(self, happy_path_response):
        v1_period_fields = {
            "period", "total_kwh", "t1_kwh", "t2_kwh", "t3_kwh",
            "t1_pct", "t2_pct", "t3_pct",
            "missing_hours", "duplicate_hours",
            "reconciliation", "overall_status", "overall_severity",
            "ptf_cost", "yekdem_cost", "cost_comparison",
            "quote_blocked", "quote_block_reason", "warnings",
        }
        for p in happy_path_response["periods"]:
            missing = v1_period_fields - p.keys()
            assert not missing, f"Period {p['period']} missing v1 fields: {missing}"

    def test_v1_classifier_outputs_match_v1_golden(self, parse_result):
        """The v1 classifier output for these periods is already pinned in
        cansu_golden_snapshot.json. Read that and confirm v2 pipeline still
        reproduces those numbers (regression guard for v1 alignment).
        """
        # S5-R02A: v1 beklentisi de SENTETIK manifestten gelir (eski donmus
        # JSON gercek dosyanin degerlerini iceriyordu). v1<->v2 hiza kapisi
        # AYNEN korunur: v2 pipeline'inin parse ettigi kayitlar, v1
        # classifier'dan gecince analitik beklentiyle birebir uyusmali.
        from tests.sentetik_tuketim_fixture import beklenen_manifest
        v1_golden = beklenen_manifest()

        from app.recon.classifier import classify_period_records
        groups = split_by_month(parse_result.records)
        for period, records in groups.items():
            tz = classify_period_records(records)
            exp = v1_golden["periods"][period]
            assert abs(float(tz.total_kwh) - exp["total_kwh"]) < 0.01
            assert abs(float(tz.t1_kwh) - exp["t1_kwh"]) < 0.01
            assert abs(float(tz.t2_kwh) - exp["t2_kwh"]) < 0.01
            assert abs(float(tz.t3_kwh) - exp["t3_kwh"]) < 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# Null cascade — separate test, NOT folded into the happy-path snapshot
# ═══════════════════════════════════════════════════════════════════════════════


class TestNullCascadeFailurePath:
    """Failure path lives in its own sub-suite for diff clarity.

    Same Excel, but with EMPTY PTF/YEKDEM tables — the engine must:
      - emit reference_energy_cost_tl = None for every period
      - cascade markup-side fields to None
      - set quote_blocked=True with a deterministic reason
      - set status='partial' at the report level
      - keep cost_inputs populated with complete=False
    """

    @pytest.fixture()
    def empty_db(self, db_session):
        """Returns the bare in-memory DB without any PTF/YEKDEM seed."""
        return db_session

    @pytest.fixture()
    def null_cascade_response(self, excel_bytes, empty_db):
        request = ReconRequest()  # no invoices either — purest null cascade
        report = _run_pipeline(excel_bytes, request, empty_db)
        return report.model_dump(mode="json")

    def test_status_is_partial(self, null_cascade_response):
        assert null_cascade_response["status"] == "partial"

    def test_api_version_still_two(self, null_cascade_response):
        """api_version is independent of failure path."""
        assert null_cascade_response["api_version"] == 2

    def test_every_period_reference_cost_is_none(self, null_cascade_response):
        for p in null_cascade_response["periods"]:
            assert p["reference_energy_cost_tl"] is None, (
                f"Period {p['period']} should have None reference cost"
            )

    def test_markup_side_fields_cascade_to_none(self, null_cascade_response):
        for p in null_cascade_response["periods"]:
            assert p["supplier_markup_tl"] is None
            assert p["supplier_markup_pct"] is None
            assert p["gelka_estimate_tl"] is None
            assert p["potential_savings_tl"] is None

    def test_quote_blocked_true_for_every_period(self, null_cascade_response):
        for p in null_cascade_response["periods"]:
            assert p["quote_blocked"] is True
            assert p["quote_block_reason"] is not None
            # The reason text identifies the missing data category.
            # v1 reason wins when v1 also blocks (current case — empty DB).
            # v2 deterministic strings only surface when v2 blocks but v1 did
            # not (e.g., partial PTF coverage). For the all-empty case here,
            # we assert that the reason mentions at least one of PTF/YEKDEM.
            reason = p["quote_block_reason"]
            assert "PTF" in reason or "YEKDEM" in reason, (
                f"Period {p['period']} block reason should mention PTF or YEKDEM, "
                f"got: {reason!r}"
            )

    def test_cost_inputs_complete_false(self, null_cascade_response):
        for p in null_cascade_response["periods"]:
            assert p["cost_inputs"]["complete"] is False
            # Other fields still meaningful — cost_inputs always populated
            assert p["cost_inputs"]["ptf_source"] == "hourly_market_prices"
            assert p["cost_inputs"]["yekdem_source"] == "monthly_yekdem_prices"

    def test_summary_v2_sums_are_none_when_no_valid_periods(self, null_cascade_response):
        summary = null_cascade_response.get("summary")
        if summary is None:
            # Single-period case may skip summary — but Cansu has 4 periods,
            # so summary must exist and v2 sums must be None.
            pytest.skip("Single-period response — no summary block to assert.")
        assert summary["valid_period_count"] == 0
        assert summary["partial_period_count"] == summary["total_period_count"]
        assert summary["total_reference_energy_cost_tl"] is None
        assert summary["total_supplier_markup_tl"] is None
        assert summary["total_gelka_estimate_tl"] is None
        assert summary["total_potential_savings_tl"] is None
        assert summary["annual_projection"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# v2 deterministic block reasons — surface only when v2 blocks but v1 didn't
# ═══════════════════════════════════════════════════════════════════════════════


class TestV2DeterministicBlockReason:
    """v2 quote_block_reason format is deterministic when v2 (stricter) wins.

    Setup: seed YEKDEM and *almost-complete* PTF (one missing hour). v1
    accepts partial coverage; v2 fails closed on the single gap. The merge
    rule in router.py says v2 reason takes priority in this case.
    """

    @pytest.fixture()
    def partial_ptf_response(self, excel_bytes, db_session, parse_result):
        """Seed YEKDEM for every period and PTF for every record EXCEPT one."""
        period_groups = split_by_month(parse_result.records)

        records = parse_result.records
        # Skip the very first record's (date, hour) for PTF — leaves a 1-hour gap
        # in exactly one period.
        skipped = (records[0].date, records[0].hour)
        skipped_period = records[0].period
        for record in records:
            if (record.date, record.hour) == skipped:
                continue
            db_session.add(HourlyMarketPrice(
                period=record.period,
                date=record.date,
                hour=record.hour,
                ptf_tl_per_mwh=PTF_TL_PER_MWH,
                smf_tl_per_mwh=PTF_TL_PER_MWH + 100.0,
                currency="TRY",
                source="test",
                version=1,
                is_active=1,
            ))
        for period in sorted(period_groups.keys()):
            db_session.add(MonthlyYekdemPrice(
                period=period,
                yekdem_tl_per_mwh=YEKDEM_TL_PER_MWH,
                source="test",
            ))
        db_session.commit()

        request = ReconRequest(
            comparison=ComparisonConfig(gelka_margin_multiplier=GELKA_MULTIPLIER),
        )
        report = _run_pipeline(excel_bytes, request, db_session)
        return report.model_dump(mode="json"), skipped_period

    def test_v2_deterministic_string_surfaces_for_partial_period(
        self, partial_ptf_response
    ):
        """The period with the 1-hour PTF gap shows the v2 string."""
        payload, partial_period = partial_ptf_response
        target = next(
            p for p in payload["periods"] if p["period"] == partial_period
        )
        assert target["quote_blocked"] is True
        # v2 wins because v1 considered the period sufficient (1 / N hours
        # missing < threshold), but v2 fails closed on any gap.
        assert target["quote_block_reason"] == "PTF data missing for 1 hours"
        assert target["reference_energy_cost_tl"] is None
        assert target["cost_inputs"]["complete"] is False

    def test_other_periods_succeed_when_only_one_period_has_gap(
        self, partial_ptf_response
    ):
        """Periods without the gap still produce non-null reference cost."""
        payload, partial_period = partial_ptf_response
        for p in payload["periods"]:
            if p["period"] == partial_period:
                continue
            assert p["reference_energy_cost_tl"] is not None, (
                f"Period {p['period']} should have non-null ref cost"
            )
            assert p["cost_inputs"]["complete"] is True
            assert p["quote_blocked"] is False
