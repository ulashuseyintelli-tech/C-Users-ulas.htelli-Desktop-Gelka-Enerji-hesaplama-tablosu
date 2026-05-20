"""
Unit tests for app.recon.cost_engine_v2.compute_period_reference_cost.

Scope (Task 6):
- full PTF + YEKDEM → reference_energy_cost_tl computed
- 1 missing PTF hour → reference_energy_cost_tl=None
- multiple missing PTF → ptf_hours_missing total count
- YEKDEM missing → reference_energy_cost_tl=None
- empty records → reason="empty_records"
- Decimal precision preserved
- multiplier metadata never applied
- market_reference_prices table is NOT read
- structured INFO log on success and failure paths

Out of scope:
- router wiring (Task 8/9)
- comparator_v2 (Task 7)
- property-based (Task 8)
- frontend (Task 11/12)
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, MarketReferencePrice

# Register pricing models with Base.metadata
import app.pricing.schemas  # noqa: F401
from app.pricing.schemas import HourlyMarketPrice, MonthlyYekdemPrice

from app.recon.cost_engine_v2 import (
    ReferenceEnergyCostResult,
    compute_period_reference_cost,
)
from app.recon.schemas import HourlyRecord


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def db_session():
    """In-memory SQLite with all pricing tables."""
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


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _seed_ptf(
    session,
    period: str = "2026-01",
    *,
    days: int = 1,
    ptf_tl_per_mwh: float = 3000.0,
    skip: set[tuple[str, int]] | None = None,
) -> None:
    """Seed hourly_market_prices for `days` days × 24 hours.

    `skip`: set of (date, hour) tuples to omit (simulate missing PTF hours).
    """
    skip = skip or set()
    year, month = int(period[:4]), int(period[5:7])
    for day in range(1, days + 1):
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        for hour in range(24):
            if (date_str, hour) in skip:
                continue
            session.add(
                HourlyMarketPrice(
                    period=period,
                    date=date_str,
                    hour=hour,
                    ptf_tl_per_mwh=ptf_tl_per_mwh,
                    smf_tl_per_mwh=ptf_tl_per_mwh + 100.0,
                    currency="TRY",
                    source="test",
                    version=1,
                    is_active=1,
                )
            )
    session.commit()


def _seed_yekdem(
    session,
    period: str = "2026-01",
    *,
    yekdem_tl_per_mwh: float = 400.0,
) -> None:
    session.add(
        MonthlyYekdemPrice(
            period=period,
            yekdem_tl_per_mwh=yekdem_tl_per_mwh,
            source="test",
        )
    )
    session.commit()


def _seed_legacy_market_reference(
    session,
    period: str = "2026-01",
    *,
    ptf: float = 9999.0,
    yekdem: float = 9999.0,
) -> None:
    """Seed market_reference_prices (legacy table) with deliberately wrong values.

    cost_engine_v2 MUST NOT read this; if it ever did, the wrong numbers
    would surface in the result.
    """
    session.add(
        MarketReferencePrice(
            period=period,
            price_type="PTF",
            ptf_tl_per_mwh=ptf,
            yekdem_tl_per_mwh=yekdem,
            status="final",
            source="test",
        )
    )
    session.commit()


def _make_record(
    date: str,
    hour: int,
    kwh: str | Decimal,
    *,
    multiplier: Decimal | None = None,
) -> HourlyRecord:
    """Build an HourlyRecord with derived period from date."""
    period = date[:7]
    return HourlyRecord(
        timestamp=datetime(int(date[:4]), int(date[5:7]), int(date[8:10]), hour),
        date=date,
        hour=hour,
        period=period,
        consumption_kwh=Decimal(str(kwh)),
        multiplier=multiplier,
    )


def _full_day_records(date: str, kwh_per_hour: str = "100") -> list[HourlyRecord]:
    """Build 24 records (one per hour) for `date` with constant consumption."""
    return [_make_record(date, h, kwh_per_hour) for h in range(24)]


# ═══════════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputePeriodReferenceCost:
    """Unit tests for compute_period_reference_cost."""

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_full_data_returns_decimal_cost(self, db_session):
        """Full PTF + YEKDEM coverage → reference_energy_cost_tl computed."""
        period = "2026-01"
        _seed_ptf(db_session, period, days=1, ptf_tl_per_mwh=3000.0)
        _seed_yekdem(db_session, period, yekdem_tl_per_mwh=400.0)

        records = _full_day_records("2026-01-01", kwh_per_hour="100")

        result = compute_period_reference_cost(records, period, db_session)

        assert isinstance(result, ReferenceEnergyCostResult)
        assert result.reference_energy_cost_tl is not None
        # 24h × 100 kWh = 2400 kWh total
        # PTF: 24 × (100 × 3000 / 1000) = 24 × 300 = 7200
        # YEKDEM: 2400 × 400 / 1000 = 960
        # Total: 8160
        assert result.reference_energy_cost_tl == Decimal("8160.000")
        assert result.ptf_hours_missing == 0
        assert result.yekdem_missing is False
        assert result.total_kwh == Decimal("2400")
        assert result.computed_hours == 24

    # ── PTF missing — single hour ─────────────────────────────────────────────

    def test_single_missing_ptf_hour_returns_none(self, db_session):
        """One missing PTF hour → fail-closed, result None, count=1."""
        period = "2026-01"
        _seed_ptf(db_session, period, days=1, skip={("2026-01-01", 13)})
        _seed_yekdem(db_session, period)

        records = _full_day_records("2026-01-01")

        result = compute_period_reference_cost(records, period, db_session)

        assert result.reference_energy_cost_tl is None
        assert result.ptf_hours_missing == 1
        assert result.yekdem_missing is False  # YEKDEM is present
        assert result.computed_hours == 23

    # ── PTF missing — multiple hours, total count ─────────────────────────────

    def test_multiple_missing_ptf_hours_total_count(self, db_session):
        """Multiple missing PTF hours → ptf_hours_missing = total count."""
        period = "2026-01"
        skipped = {("2026-01-01", 0), ("2026-01-01", 5), ("2026-01-01", 18)}
        _seed_ptf(db_session, period, days=1, skip=skipped)
        _seed_yekdem(db_session, period)

        records = _full_day_records("2026-01-01")

        result = compute_period_reference_cost(records, period, db_session)

        assert result.reference_energy_cost_tl is None
        assert result.ptf_hours_missing == 3  # NOT 1 (first), NOT len(records)
        assert result.computed_hours == 21

    # ── YEKDEM missing ────────────────────────────────────────────────────────

    def test_yekdem_missing_returns_none(self, db_session):
        """PTF complete but YEKDEM missing → fail-closed, result None."""
        period = "2026-01"
        _seed_ptf(db_session, period, days=1)
        # No YEKDEM seeded.

        records = _full_day_records("2026-01-01")

        result = compute_period_reference_cost(records, period, db_session)

        assert result.reference_energy_cost_tl is None
        assert result.ptf_hours_missing == 0
        assert result.yekdem_missing is True
        assert result.computed_hours == 24
        assert result.total_kwh == Decimal("2400")

    # ── Empty records ─────────────────────────────────────────────────────────

    def test_empty_records_returns_none_with_empty_records_reason(
        self, db_session, caplog
    ):
        """Empty records → result None, structured log reason='empty_records'."""
        period = "2026-01"
        _seed_ptf(db_session, period, days=1)
        _seed_yekdem(db_session, period)

        with caplog.at_level(logging.INFO, logger="app.recon.cost_engine_v2"):
            result = compute_period_reference_cost([], period, db_session)

        assert result.reference_energy_cost_tl is None
        assert result.total_kwh == Decimal("0")
        assert result.ptf_hours_missing == 0
        assert result.yekdem_missing is False  # Not evaluated; early exit
        assert result.computed_hours == 0

        log_entries = [
            r for r in caplog.records
            if getattr(r, "event", None) == "reference_cost_compute"
        ]
        assert len(log_entries) == 1
        assert log_entries[0].reason == "empty_records"
        assert log_entries[0].success is False
        assert log_entries[0].records == 0

    # ── Decimal precision ─────────────────────────────────────────────────────

    def test_decimal_precision_preserved(self, db_session):
        """Decimal arithmetic — no float rounding contamination."""
        period = "2026-01"
        # Use values that would lose precision in float arithmetic
        _seed_ptf(db_session, period, days=1, ptf_tl_per_mwh=3000.123)
        _seed_yekdem(db_session, period, yekdem_tl_per_mwh=400.567)

        # Single-hour record with non-trivial fractional kWh
        records = [_make_record("2026-01-01", 0, "1234.567")]

        result = compute_period_reference_cost(records, period, db_session)

        # Expected: ptf_component = 1234.567 × 3000.123 / 1000
        #           yekdem_component = 1234.567 × 400.567 / 1000
        ptf_expected = Decimal("1234.567") * Decimal("3000.123") / Decimal("1000")
        yekdem_expected = Decimal("1234.567") * Decimal("400.567") / Decimal("1000")
        expected = ptf_expected + yekdem_expected

        assert result.reference_energy_cost_tl == expected
        # Result is a raw Decimal (no rounding applied at engine level)
        assert isinstance(result.reference_energy_cost_tl, Decimal)

    # ── Multiplier metadata-only ──────────────────────────────────────────────

    def test_multiplier_metadata_not_applied(self, db_session):
        """Format A multiplier is metadata-only; consumption_kwh used as-is."""
        period = "2026-01"
        _seed_ptf(db_session, period, days=1, ptf_tl_per_mwh=3000.0)
        _seed_yekdem(db_session, period, yekdem_tl_per_mwh=400.0)

        # Build records with multiplier=100 — if it leaked, costs would be ×100
        records = [
            _make_record("2026-01-01", h, "100", multiplier=Decimal("100"))
            for h in range(24)
        ]

        result = compute_period_reference_cost(records, period, db_session)

        # Same expected as the plain happy-path (multiplier IGNORED):
        # 24 × 100 = 2400 kWh, cost = 8160 TL
        assert result.reference_energy_cost_tl == Decimal("8160.000")
        assert result.total_kwh == Decimal("2400")

    # ── SoT: market_reference_prices NOT read ─────────────────────────────────

    def test_market_reference_prices_table_not_read(self, db_session):
        """Legacy market_reference_prices populated; canonical empty → None.

        If cost_engine_v2 ever fell back to legacy, the deliberately wrong
        seed values (9999) would surface OR the result would be non-None.
        Both are caught by this assertion.
        """
        period = "2026-01"
        # Seed ONLY the legacy table — canonical tables empty
        _seed_legacy_market_reference(db_session, period, ptf=9999.0, yekdem=9999.0)

        records = _full_day_records("2026-01-01")

        result = compute_period_reference_cost(records, period, db_session)

        # Canonical (hourly_market_prices + monthly_yekdem_prices) is empty,
        # so fail-closed must trigger on PTF. Legacy data must not leak in.
        assert result.reference_energy_cost_tl is None
        assert result.ptf_hours_missing == 24  # all hours unmatched
        assert result.yekdem_missing is True

    def test_market_reference_prices_does_not_leak_into_result(self, db_session):
        """Canonical PTF+YEKDEM seeded with 3000/400; legacy with 9999/9999.

        Result must reflect canonical values (8160), never the 9999 values.
        """
        period = "2026-01"
        _seed_ptf(db_session, period, days=1, ptf_tl_per_mwh=3000.0)
        _seed_yekdem(db_session, period, yekdem_tl_per_mwh=400.0)
        _seed_legacy_market_reference(db_session, period, ptf=9999.0, yekdem=9999.0)

        records = _full_day_records("2026-01-01", kwh_per_hour="100")

        result = compute_period_reference_cost(records, period, db_session)

        # Must equal the canonical-only happy-path: 8160
        assert result.reference_energy_cost_tl == Decimal("8160.000")

    # ── Observability: success log path ───────────────────────────────────────

    def test_structured_log_success_path(self, db_session, caplog):
        """Success path emits structured INFO log with all required fields, no PII."""
        period = "2026-01"
        _seed_ptf(db_session, period, days=1)
        _seed_yekdem(db_session, period)

        records = _full_day_records("2026-01-01")

        with caplog.at_level(logging.INFO, logger="app.recon.cost_engine_v2"):
            compute_period_reference_cost(records, period, db_session)

        entries = [
            r for r in caplog.records
            if getattr(r, "event", None) == "reference_cost_compute"
        ]
        assert len(entries) == 1
        rec = entries[0]
        # Required fields
        assert rec.event == "reference_cost_compute"
        assert rec.period == period
        assert rec.records == 24
        assert rec.computed_hours == 24
        assert rec.ptf_hours_missing == 0
        assert rec.yekdem_missing is False
        assert rec.success is True
        assert rec.reason is None
        assert rec.total_kwh == 2400.0
        # Terminology lock — no forbidden strings
        for forbidden in ("gerçek maliyet", "actual cost", "true cost"):
            assert forbidden not in rec.getMessage().lower()
        # PII absence — log entry must not carry these keys
        for pii_key in ("customer_name", "supplier_name", "tariff_group"):
            assert not hasattr(rec, pii_key)

    # ── Observability: failure log path ───────────────────────────────────────

    def test_structured_log_failure_path_ptf_missing(self, db_session, caplog):
        """Failure path emits log with success=False, reason='ptf_hours_missing'."""
        period = "2026-01"
        _seed_ptf(db_session, period, days=1, skip={("2026-01-01", 7)})
        _seed_yekdem(db_session, period)

        records = _full_day_records("2026-01-01")

        with caplog.at_level(logging.INFO, logger="app.recon.cost_engine_v2"):
            compute_period_reference_cost(records, period, db_session)

        entries = [
            r for r in caplog.records
            if getattr(r, "event", None) == "reference_cost_compute"
        ]
        assert len(entries) == 1
        rec = entries[0]
        assert rec.success is False
        assert rec.reason == "ptf_hours_missing"
        assert rec.ptf_hours_missing == 1
        assert rec.yekdem_missing is False  # YEKDEM was present

    def test_structured_log_failure_path_yekdem_missing(self, db_session, caplog):
        """Failure path emits log with reason='yekdem_missing' when only YEKDEM absent."""
        period = "2026-01"
        _seed_ptf(db_session, period, days=1)
        # No YEKDEM seeded.

        records = _full_day_records("2026-01-01")

        with caplog.at_level(logging.INFO, logger="app.recon.cost_engine_v2"):
            compute_period_reference_cost(records, period, db_session)

        entries = [
            r for r in caplog.records
            if getattr(r, "event", None) == "reference_cost_compute"
        ]
        assert len(entries) == 1
        rec = entries[0]
        assert rec.success is False
        assert rec.reason == "yekdem_missing"
        assert rec.ptf_hours_missing == 0
        assert rec.yekdem_missing is True
