"""Integration suite for pricing-cache-key-completeness (bugfix).

T15 — scaffold (fixture + helpers)
T16 — LOW vs HIGH end-to-end (B1 baseline regression replay)
T17 — cache hit determinism (aynı request 2× → 2. cache_hit=True)
T18 — cache observability (response.cache objesi + cache_hit mirror)

**Kritik kanıt (T16):** Handler gerçek bir /api/pricing/analyze isteğinde
build_cache_key'e t1/t2/t3/use_template/voltage_level parametrelerini geçiriyor
mu? PBT sadece build_cache_key fonksiyonunu test eder; bu suite handler
zincirini de doğrular.

References:
- Spec: .kiro/specs/pricing-cache-key-completeness/{bugfix.md, design.md, tasks.md}
- B1 baseline collision: baselines/2026-05-12_pre-ptf-unification_baseline.json
  2026-03 period, LOW (25k/12.5k/12.5k) vs HIGH (250k/125k/125k) → same hash.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base


# ---------------------------------------------------------------------------
# T15 — Scaffold: fixture + helpers
# ---------------------------------------------------------------------------
def _normalize(weights):
    """Normalize 24 weights to sum=1.0 (template fixture helper)."""
    total = sum(weights)
    return [w / total for w in weights]


@pytest.fixture
def e2e_client():
    """TestClient with in-memory DB seeded for period 2026-04.

    Single period is sufficient — the bug condition is across consumption
    profiles, not periods. 2026-04 chosen because it's one of the canonical
    periods in the B1 baseline (and a fresh in-memory DB means no v1 cache
    contamination).
    """
    import os

    with patch.dict(os.environ, {
        "ADMIN_API_KEY_ENABLED": "false",
        "API_KEY_ENABLED": "false",
    }):
        from app.main import app as fastapi_app
        from app.database import get_db
        from app.pricing.schemas import (
            HourlyMarketPrice,
            MonthlyYekdemPrice,
            ProfileTemplate as PTSchema,
        )

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        TestSession = sessionmaker(bind=engine)
        session = TestSession()

        # Seed template (needed even when use_template=False for AnalyzeRequest validation)
        weights = _normalize([1.0] * 24)
        session.add(PTSchema(
            name="3_vardiya_sanayi",
            display_name="3 Vardiya Sanayi",
            description="7/24 kesintisiz üretim — düz profil",
            hourly_weights=json.dumps(weights),
            is_builtin=1,
        ))

        # Seed YEKDEM for 2026-04
        session.add(MonthlyYekdemPrice(
            period="2026-04",
            yekdem_tl_per_mwh=400.0,
            source="test-seed",
        ))

        # Seed HourlyMarketPrice for 2026-04 (30 days × 24h = 720 records)
        for day in range(1, 31):
            date_str = f"2026-04-{day:02d}"
            for hour in range(24):
                session.add(HourlyMarketPrice(
                    period="2026-04",
                    date=date_str,
                    hour=hour,
                    ptf_tl_per_mwh=3000.0,
                    smf_tl_per_mwh=2800.0,
                    source="test-seed",
                    version=1,
                    is_active=1,
                ))

        session.commit()

        def _override_get_db():
            try:
                yield session
            finally:
                pass

        fastapi_app.dependency_overrides[get_db] = _override_get_db
        yield TestClient(fastapi_app, raise_server_exceptions=False)
        fastapi_app.dependency_overrides.clear()
        session.close()


def _low_payload():
    """LOW tüketim profili — B1 baseline 2026-03 senaryosu ile eşdeğer granülarite."""
    return {
        "period": "2026-04",
        "customer_id": "TEST-CUST-CACHE-KEY",
        "multiplier": 1.05,
        "dealer_commission_pct": 0.0,
        "imbalance_params": {
            "forecast_error_rate": 0.05,
            "imbalance_cost_tl_per_mwh": 150.0,
            "smf_based_imbalance_enabled": False,
        },
        "use_template": False,
        "t1_kwh": 25_000,
        "t2_kwh": 12_500,
        "t3_kwh": 12_500,
        "voltage_level": "og",
    }


def _high_payload():
    """HIGH tüketim profili — LOW'un tam 10x'i, aynı diğer alanlar."""
    return {**_low_payload(), "t1_kwh": 250_000, "t2_kwh": 125_000, "t3_kwh": 125_000}


# ---------------------------------------------------------------------------
# T16 — LOW vs HIGH end-to-end (B1 baseline regression replay)
# ---------------------------------------------------------------------------
class TestLowVsHighProfile:
    """T16: the critical end-to-end proof that the handler wires t1/t2/t3 into
    the cache key. Without T7's wiring, this test would fail even though T2's
    PBT passes.
    """

    def test_different_consumption_different_response(self, e2e_client):
        """LOW (50k total) and HIGH (500k total) with all other fields identical
        MUST produce different responses, different cache behavior, and different
        total_consumption_kwh.
        """
        resp_low = e2e_client.post("/api/pricing/analyze", json=_low_payload())
        resp_high = e2e_client.post("/api/pricing/analyze", json=_high_payload())

        assert resp_low.status_code == 200, (
            f"LOW failed: {resp_low.status_code} {resp_low.text[:500]}"
        )
        assert resp_high.status_code == 200, (
            f"HIGH failed: {resp_high.status_code} {resp_high.text[:500]}"
        )

        low = resp_low.json()
        high = resp_high.json()

        # Both must be cache misses — different keys means HIGH can't hit LOW's entry
        assert low["cache_hit"] is False, (
            "LOW unexpectedly served from cache — test fixture not isolated?"
        )
        assert high["cache_hit"] is False, (
            "HIGH unexpectedly served from cache — CACHE KEY COLLISION. "
            "Handler is not passing t1/t2/t3 to build_cache_key. T7 broken."
        )

        # Total consumption must reflect the input profile
        low_total = low["weighted_prices"]["total_consumption_kwh"]
        high_total = high["weighted_prices"]["total_consumption_kwh"]
        assert low_total == pytest.approx(50_000.0, abs=1.0), (
            f"LOW total_consumption_kwh = {low_total}, expected 50000"
        )
        assert high_total == pytest.approx(500_000.0, abs=1.0), (
            f"HIGH total_consumption_kwh = {high_total}, expected 500000. "
            "This is the B1 baseline bug: HIGH getting LOW's cached response."
        )

        # Supplier cost structure should differ (different energy totals)
        assert low["weighted_prices"]["total_cost_tl"] != high["weighted_prices"]["total_cost_tl"]


# ---------------------------------------------------------------------------
# T17 — Cache hit determinism
# ---------------------------------------------------------------------------
class TestCacheHitDeterminism:
    """T17: two identical requests → second must be cache hit with same content."""

    def test_same_request_hits_cache(self, e2e_client):
        payload = _low_payload()
        resp_1 = e2e_client.post("/api/pricing/analyze", json=payload)
        resp_2 = e2e_client.post("/api/pricing/analyze", json=payload)

        assert resp_1.status_code == 200
        assert resp_2.status_code == 200
        r1, r2 = resp_1.json(), resp_2.json()

        assert r1["cache_hit"] is False, "First call should be miss"
        assert r2["cache_hit"] is True, "Second identical call should be hit"

        # Sanity: core fields identical (weighted prices deterministic)
        assert r1["weighted_prices"]["total_consumption_kwh"] == r2["weighted_prices"]["total_consumption_kwh"]
        assert r1["weighted_prices"]["total_cost_tl"] == r2["weighted_prices"]["total_cost_tl"]


# ---------------------------------------------------------------------------
# T18 — Cache observability (response.cache objesi, Decision 9)
# ---------------------------------------------------------------------------
class TestCacheObservability:
    """T18: response.cache field populated correctly on both miss and hit."""

    def test_cache_field_on_miss(self, e2e_client):
        resp = e2e_client.post("/api/pricing/analyze", json=_low_payload())
        assert resp.status_code == 200
        data = resp.json()

        assert "cache" in data, "Response missing 'cache' observability object"
        cache = data["cache"]
        assert cache["hit"] is False
        assert cache["key_version"] == "v2"
        assert cache["cached_key_version"] is None

    def test_cache_field_on_hit(self, e2e_client):
        payload = _low_payload()
        e2e_client.post("/api/pricing/analyze", json=payload)  # warm
        resp = e2e_client.post("/api/pricing/analyze", json=payload)

        assert resp.status_code == 200
        data = resp.json()
        cache = data["cache"]
        assert cache["hit"] is True
        assert cache["key_version"] == "v2"
        assert cache["cached_key_version"] == "v2"

    def test_cache_hit_mirror_invariant(self, e2e_client):
        """Backward-compat: response.cache.hit == response.cache_hit for every request."""
        # Miss path
        resp1 = e2e_client.post("/api/pricing/analyze", json=_low_payload())
        d1 = resp1.json()
        assert d1["cache"]["hit"] == d1["cache_hit"]

        # Hit path
        resp2 = e2e_client.post("/api/pricing/analyze", json=_low_payload())
        d2 = resp2.json()
        assert d2["cache"]["hit"] == d2["cache_hit"]
