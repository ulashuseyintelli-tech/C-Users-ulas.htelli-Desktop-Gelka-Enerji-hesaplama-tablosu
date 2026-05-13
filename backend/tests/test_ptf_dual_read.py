"""
Tests for PTF dual-read scaffold — Phase 2 T2.1 (ptf-sot-unification).

Validates:
1. Default (drift log disabled) → dual path NOT taken; canonical-only.
2. Dual mode ON → response is canonical authoritative; legacy IS actually called.
3. Dual mode + legacy reader fails → request still returns 200 (observe-only).
4. Dual mode + canonical empty → 404 (Hybrid-C kontratı dual modda da geçerli).
5. Dual mode + drift helper fails → request still returns 200.
6. Dual mode + legacy empty + canonical exists → 200 (observe-only model).
7. Kill switch precedence — use_legacy_ptf=True overrides drift log flag.

Scope out: drift compute, drift severity classification, DB write
(those land in T2.2). T2.1 is scaffold only — observe-only debug log.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, MarketReferencePrice

# Import schemas to register HourlyMarketPrice with Base.metadata
import app.pricing.schemas  # noqa: F401
from app.pricing.schemas import HourlyMarketPrice, MonthlyYekdemPrice
from app.ptf_drift_log import DRIFT_HIGH_PCT


# ── Fixtures ──────────────────────────────────────────────────────────────────

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


def _seed_canonical(session, period="2026-03", days=1, ptf=3000.0, smf=3100.0):
    from datetime import date as date_type
    for day in range(1, days + 1):
        d = date_type(int(period[:4]), int(period[5:7]), day)
        for hour in range(24):
            session.add(HourlyMarketPrice(
                period=period,
                date=d.isoformat(),
                hour=hour,
                ptf_tl_per_mwh=ptf + hour,
                smf_tl_per_mwh=smf,
                currency="TRY",
                source="test",
                version=1,
                is_active=1,
            ))
    session.commit()


def _seed_legacy(session, period="2026-03", ptf=2000.0, yekdem=400.0):
    session.add(MarketReferencePrice(
        period=period,
        price_type="PTF",
        ptf_tl_per_mwh=ptf,
        yekdem_tl_per_mwh=yekdem,
        status="final",
        source="test",
    ))
    session.commit()


def _seed_yekdem(session, period="2026-03", value=400.0):
    session.add(MonthlyYekdemPrice(
        period=period,
        yekdem_tl_per_mwh=value,
        source="test",
    ))
    session.commit()


def _reset_guard_config():
    import app.guard_config as gc_mod
    gc_mod._guard_config = None


def _analyze_request_body(period="2026-03"):
    return {
        "period": period,
        "multiplier": 1.10,
        "dealer_commission_pct": 0,
        "imbalance_params": {
            "forecast_error_rate": 0.05,
            "imbalance_cost_tl_per_mwh": 150.0,
            "smf_based_imbalance_enabled": False,
        },
        "use_template": False,
        "t1_kwh": 25000,
        "t2_kwh": 12500,
        "t3_kwh": 12500,
        "voltage_level": "og",
    }


# ── Dual-read scaffold tests ──────────────────────────────────────────────────


class TestDualReadDispatcher:
    """T2.1 — dispatcher routes to dual path only when drift log enabled."""

    def test_default_off_dual_path_not_taken(self, db_session):
        """Default (drift log disabled) → dual fonksiyonu çağrılmaz; canonical-only."""
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)

        _reset_guard_config()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            os.environ.pop("OPS_GUARD_PTF_DRIFT_LOG_ENABLED", None)
            os.environ.pop("PTF_DRIFT_LOG_ENABLED", None)

            with patch("app.pricing.router._load_market_records_dual") as dual_spy:
                from app.pricing.router import _load_market_records
                records = _load_market_records(db_session, "2026-03")

                assert dual_spy.called is False, (
                    "Default path must NOT invoke _load_market_records_dual"
                )

        _reset_guard_config()
        # Canonical reader returned 24 hourly rows — Phase 1 default behavior intact.
        assert len(records) == 24
        assert records[0].ptf_tl_per_mwh == 3000.0

    def test_dual_mode_returns_canonical_authoritative(self, db_session):
        """Dual ON → response reflects canonical values AND legacy is actually called.

        Without the legacy-was-called assertion, this test could pass even when
        dual-read silently degraded to canonical-only — defeating its purpose.
        """
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)
        _seed_yekdem(db_session, period="2026-03")

        _reset_guard_config()
        with patch.dict(os.environ, {
            "OPS_GUARD_PTF_DRIFT_LOG_ENABLED": "true",
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            _reset_guard_config()

            # Spy on legacy reader — wraps real implementation, records calls.
            from app.pricing import router as router_mod

            original_legacy = router_mod._load_market_records_legacy
            call_count = {"n": 0}

            def spying_legacy(db, period):
                call_count["n"] += 1
                return original_legacy(db, period)

            with patch.object(router_mod, "_load_market_records_legacy",
                              side_effect=spying_legacy) as legacy_spy:
                from fastapi.testclient import TestClient
                from app.main import app
                from app.database import get_db

                app.dependency_overrides[get_db] = lambda: db_session
                client = TestClient(app)
                resp = client.post("/api/pricing/analyze", json=_analyze_request_body())
                app.dependency_overrides.clear()

        _reset_guard_config()

        # Critical 1: legacy MUST have been called (otherwise dual-read didn't run).
        assert call_count["n"] >= 1, (
            "Dual mode must invoke legacy reader at least once. "
            "If legacy was not called, dual-read silently degraded to canonical-only."
        )

        # Critical 2: response reflects canonical values (3000+hour), NOT legacy (2000).
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        weighted_ptf = body["weighted_prices"]["weighted_ptf_tl_per_mwh"]
        # Canonical seeded at 3000+hour → weighted average is well above 3000.
        # Legacy seeded at 2000 → if legacy leaked into response, weighted < 2500.
        assert weighted_ptf >= 3000.0, (
            f"Response weighted_ptf={weighted_ptf} suggests legacy leaked into response. "
            f"Canonical authoritative kontratı bozulmuş."
        )

    def test_dual_mode_legacy_failure_does_not_fail_request(self, db_session):
        """Dual ON + legacy reader patlar → response yine 200, canonical değerleri."""
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_yekdem(db_session, period="2026-03")
        # NOT seeding legacy — but we'll also force legacy reader to raise to be sure.

        _reset_guard_config()
        with patch.dict(os.environ, {
            "OPS_GUARD_PTF_DRIFT_LOG_ENABLED": "true",
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            _reset_guard_config()

            from app.pricing import router as router_mod
            from fastapi.testclient import TestClient
            from app.main import app
            from app.database import get_db

            with patch.object(router_mod, "_load_market_records_legacy",
                              side_effect=RuntimeError("simulated legacy failure")):
                app.dependency_overrides[get_db] = lambda: db_session
                client = TestClient(app)
                resp = client.post("/api/pricing/analyze", json=_analyze_request_body())
                app.dependency_overrides.clear()

        _reset_guard_config()

        # Legacy exception MUST NOT propagate; canonical authoritative.
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["weighted_prices"]["weighted_ptf_tl_per_mwh"] >= 3000.0

    def test_dual_mode_canonical_empty_still_returns_404(self, db_session):
        """Dual ON + canonical empty + legacy populated → still 404 (Hybrid-C)."""
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)
        _seed_yekdem(db_session, period="2026-03")
        # Canonical NOT seeded.

        _reset_guard_config()
        with patch.dict(os.environ, {
            "OPS_GUARD_PTF_DRIFT_LOG_ENABLED": "true",
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            _reset_guard_config()

            from fastapi.testclient import TestClient
            from app.main import app
            from app.database import get_db

            app.dependency_overrides[get_db] = lambda: db_session
            client = TestClient(app)
            resp = client.post("/api/pricing/analyze", json=_analyze_request_body())
            app.dependency_overrides.clear()

        _reset_guard_config()

        # Critical: legacy presence does NOT mask canonical absence.
        assert resp.status_code == 404
        body = resp.json()
        assert body["detail"]["error"] == "market_data_not_found"

    def test_dual_mode_drift_helper_failure_does_not_fail_request(self, db_session):
        """Dual ON + _maybe_record_drift patlar → response yine 200."""
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)
        _seed_yekdem(db_session, period="2026-03")

        _reset_guard_config()
        with patch.dict(os.environ, {
            "OPS_GUARD_PTF_DRIFT_LOG_ENABLED": "true",
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            _reset_guard_config()

            from app.pricing import router as router_mod
            from fastapi.testclient import TestClient
            from app.main import app
            from app.database import get_db

            with patch.object(router_mod, "_maybe_record_drift",
                              side_effect=RuntimeError("simulated telemetry failure")):
                app.dependency_overrides[get_db] = lambda: db_session
                client = TestClient(app)
                resp = client.post("/api/pricing/analyze", json=_analyze_request_body())
                app.dependency_overrides.clear()

        _reset_guard_config()

        # Drift helper exception MUST NOT propagate to user.
        # Both _maybe_record_drift's inner try/except AND the dispatcher's
        # outer try/except (defense in depth) must keep the response clean.
        assert resp.status_code == 200, (
            f"Drift helper failure leaked into pricing response: {resp.status_code}. "
            f"Either _maybe_record_drift's inner guard or the dispatcher's outer "
            f"guard must catch all telemetry exceptions."
        )
        body = resp.json()
        assert body["weighted_prices"]["weighted_ptf_tl_per_mwh"] >= 3000.0

    def test_dual_mode_legacy_empty_still_returns_200(self, db_session):
        """Dual ON + legacy empty + canonical exists → 200.

        Critical observe-only invariant: legacy may be empty for valid reasons
        (period not yet imported into legacy, legacy table being phased out).
        Canonical is authoritative; empty legacy MUST NOT fail or downgrade the
        response. Only canonical presence matters for the 200/404 decision.
        """
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_yekdem(db_session, period="2026-03")
        # Legacy NOT seeded — but canonical exists.

        _reset_guard_config()
        with patch.dict(os.environ, {
            "OPS_GUARD_PTF_DRIFT_LOG_ENABLED": "true",
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            _reset_guard_config()

            from fastapi.testclient import TestClient
            from app.main import app
            from app.database import get_db

            app.dependency_overrides[get_db] = lambda: db_session
            client = TestClient(app)
            resp = client.post("/api/pricing/analyze", json=_analyze_request_body())
            app.dependency_overrides.clear()

        _reset_guard_config()

        # Empty legacy MUST NOT degrade canonical-authoritative response.
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["weighted_prices"]["weighted_ptf_tl_per_mwh"] >= 3000.0

    def test_kill_switch_overrides_dual(self, db_session):
        """use_legacy_ptf=True + ptf_drift_log_enabled=True → legacy okur, dual çağrılmaz.

        Kill switch precedence: emergency rollback wins over telemetry.
        """
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)

        _reset_guard_config()
        with patch.dict(os.environ, {
            "OPS_GUARD_USE_LEGACY_PTF": "true",
            "OPS_GUARD_PTF_DRIFT_LOG_ENABLED": "true",
        }, clear=False):
            _reset_guard_config()

            with patch("app.pricing.router._load_market_records_dual") as dual_spy:
                from app.pricing.router import _load_market_records
                records = _load_market_records(db_session, "2026-03")

                assert dual_spy.called is False, (
                    "Kill switch must override drift log flag — dual path forbidden"
                )

        _reset_guard_config()

        # Legacy spreads monthly avg flat across the month (March = 31 days × 24).
        assert len(records) == 31 * 24
        assert all(r.ptf_tl_per_mwh == 2000.0 for r in records)



# ── T2.2 — drift compute + write integration tests ───────────────────────────


def _query_drift_log(db_session, period: str = "2026-03"):
    """Helper: read all drift log rows for a period, sorted by id."""
    from app.ptf_drift_log import PtfDriftLog
    return (
        db_session.query(PtfDriftLog)
        .filter(PtfDriftLog.period == period)
        .order_by(PtfDriftLog.id)
        .all()
    )


def _sanitize_response(body: dict) -> dict:
    """Remove volatile fields for response equality comparison.

    cache_hit, cache.hit, cache.cached_key_version vary between calls because
    the second request may hit cache. We only want to assert that the
    *substantive* response (prices, margins, breakdowns) is identical.
    """
    if not isinstance(body, dict):
        return body
    out = {k: v for k, v in body.items() if k != "cache_hit"}
    if "cache" in out and isinstance(out["cache"], dict):
        # Keep only key_version (canonical-only marker), drop hit/cached_key_version
        out["cache"] = {"key_version": out["cache"].get("key_version")}
    return out


class TestDualReadDriftWrites:
    """T2.2 — drift computation + persistence integration."""

    def test_drift_log_written_when_canonical_and_legacy_differ(self, db_session):
        """Canonical 3000+, legacy 2000 → severity='high', delta_abs/pct populated."""
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)
        _seed_yekdem(db_session, period="2026-03")

        _reset_guard_config()
        with patch.dict(os.environ, {
            "OPS_GUARD_PTF_DRIFT_LOG_ENABLED": "true",
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            _reset_guard_config()

            from fastapi.testclient import TestClient
            from app.main import app
            from app.database import get_db

            app.dependency_overrides[get_db] = lambda: db_session
            client = TestClient(app)
            resp = client.post("/api/pricing/analyze", json=_analyze_request_body())
            app.dependency_overrides.clear()

        _reset_guard_config()

        assert resp.status_code == 200
        rows = _query_drift_log(db_session)
        assert len(rows) == 1, f"Expected 1 drift row, got {len(rows)}"
        row = rows[0]
        assert row.severity == "high"
        # Canonical hourly variation 3000+hour averages to ~3011.5
        assert row.canonical_price >= 3000.0
        # Legacy is flat 2000 spread across the month
        assert row.legacy_price == 2000.0
        # Delta is large enough to trigger high
        assert row.delta_abs is not None and row.delta_abs > 100.0
        assert row.delta_pct is not None and row.delta_pct > DRIFT_HIGH_PCT

    def test_drift_severity_low_when_values_equal(self, db_session):
        """Canonical and legacy averages equal → severity='low', deltas=0."""
        # Both seeded at flat 2000 — canonical hour 0 = 2000, hour 23 = 2023
        # Avg canonical = (2000+2001+...+2023)/24 = 2011.5
        # Legacy flat 2011.5 to match
        _seed_canonical(db_session, period="2026-03", days=1, ptf=2000.0)
        _seed_legacy(db_session, period="2026-03", ptf=2011.5)
        _seed_yekdem(db_session, period="2026-03")

        _reset_guard_config()
        with patch.dict(os.environ, {
            "OPS_GUARD_PTF_DRIFT_LOG_ENABLED": "true",
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            _reset_guard_config()

            from fastapi.testclient import TestClient
            from app.main import app
            from app.database import get_db

            app.dependency_overrides[get_db] = lambda: db_session
            client = TestClient(app)
            resp = client.post("/api/pricing/analyze", json=_analyze_request_body())
            app.dependency_overrides.clear()

        _reset_guard_config()

        assert resp.status_code == 200
        rows = _query_drift_log(db_session)
        assert len(rows) == 1
        assert rows[0].severity == "low"
        # Deltas are exactly 0 (averages match by construction)
        assert rows[0].delta_abs == 0.0
        assert rows[0].delta_pct == 0.0

    def test_legacy_missing_writes_missing_legacy_severity(self, db_session):
        """Canonical only + legacy empty → severity='missing_legacy', response 200."""
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_yekdem(db_session, period="2026-03")
        # Legacy NOT seeded — legacy reader returns empty list

        _reset_guard_config()
        with patch.dict(os.environ, {
            "OPS_GUARD_PTF_DRIFT_LOG_ENABLED": "true",
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            _reset_guard_config()

            from fastapi.testclient import TestClient
            from app.main import app
            from app.database import get_db

            app.dependency_overrides[get_db] = lambda: db_session
            client = TestClient(app)
            resp = client.post("/api/pricing/analyze", json=_analyze_request_body())
            app.dependency_overrides.clear()

        _reset_guard_config()

        # Response is 200 — observe-only invariant from T2.1.
        assert resp.status_code == 200
        rows = _query_drift_log(db_session)
        assert len(rows) == 1
        row = rows[0]
        assert row.severity == "missing_legacy"
        assert row.legacy_price is None
        assert row.delta_abs is None
        assert row.delta_pct is None
        # Canonical price still recorded so we can correlate later
        assert row.canonical_price >= 3000.0

    def test_drift_write_failure_does_not_fail_response(self, db_session):
        """write_drift_record returns False / raises → response 200."""
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)
        _seed_yekdem(db_session, period="2026-03")

        _reset_guard_config()
        with patch.dict(os.environ, {
            "OPS_GUARD_PTF_DRIFT_LOG_ENABLED": "true",
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            _reset_guard_config()

            # Force write_drift_record to raise — simulates DB connection death.
            from app import ptf_drift_log as drift_mod

            with patch.object(drift_mod, "write_drift_record",
                              side_effect=RuntimeError("simulated DB outage")):
                from fastapi.testclient import TestClient
                from app.main import app
                from app.database import get_db

                app.dependency_overrides[get_db] = lambda: db_session
                client = TestClient(app)
                resp = client.post("/api/pricing/analyze", json=_analyze_request_body())
                app.dependency_overrides.clear()

        _reset_guard_config()

        # Even with telemetry layer screaming, the user gets their price.
        assert resp.status_code == 200
        body = resp.json()
        assert body["weighted_prices"]["weighted_ptf_tl_per_mwh"] >= 3000.0

    def test_response_body_unchanged_with_drift_writes(self, db_session):
        """Drift-on response (sanitized) equals canonical-only response.

        Deep equality on substantive fields. If drift writing somehow leaked
        into the response (e.g., extra keys, mutated values), this fails.
        """
        _seed_canonical(db_session, period="2026-03", days=1, ptf=3000.0)
        _seed_legacy(db_session, period="2026-03", ptf=2000.0)
        _seed_yekdem(db_session, period="2026-03")

        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import get_db
        from app.pricing.pricing_cache import cleanup_expired_cache

        # ── Phase A: canonical-only mode (drift log disabled) ──
        _reset_guard_config()
        with patch.dict(os.environ, {
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            os.environ.pop("OPS_GUARD_PTF_DRIFT_LOG_ENABLED", None)
            os.environ.pop("PTF_DRIFT_LOG_ENABLED", None)
            _reset_guard_config()

            app.dependency_overrides[get_db] = lambda: db_session
            client = TestClient(app)
            resp_baseline = client.post("/api/pricing/analyze",
                                        json=_analyze_request_body())
            app.dependency_overrides.clear()

        assert resp_baseline.status_code == 200
        baseline_body = _sanitize_response(resp_baseline.json())

        # Wipe analyze cache so the next call re-computes (cache key includes
        # ptf_source which is the same for both modes, so cache would hit).
        from app.pricing.schemas import AnalysisCache
        db_session.query(AnalysisCache).delete()
        db_session.commit()

        # ── Phase B: drift log enabled — same request ──
        _reset_guard_config()
        with patch.dict(os.environ, {
            "OPS_GUARD_PTF_DRIFT_LOG_ENABLED": "true",
            "ADMIN_API_KEY_ENABLED": "false",
            "API_KEY_ENABLED": "false",
        }, clear=False):
            os.environ.pop("OPS_GUARD_USE_LEGACY_PTF", None)
            os.environ.pop("USE_LEGACY_PTF", None)
            _reset_guard_config()

            app.dependency_overrides[get_db] = lambda: db_session
            client = TestClient(app)
            resp_drift = client.post("/api/pricing/analyze",
                                     json=_analyze_request_body())
            app.dependency_overrides.clear()

        _reset_guard_config()

        assert resp_drift.status_code == 200
        drift_body = _sanitize_response(resp_drift.json())

        # Substantive response IDENTICAL — drift telemetry is invisible to user.
        assert drift_body == baseline_body, (
            "Drift logging leaked into response body. observe-only contract broken."
        )

        # AND the drift row was actually written in phase B (not just silently skipped)
        rows = _query_drift_log(db_session)
        assert len(rows) >= 1
