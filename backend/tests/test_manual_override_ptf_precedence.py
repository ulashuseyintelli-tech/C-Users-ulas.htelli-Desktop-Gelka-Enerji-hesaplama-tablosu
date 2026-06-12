"""
Manuel kayıt (source="manual_override") = açık override.

Precedence: params override → MANUAL_OVERRIDE skaler → C2 gerçek tüketim →
            hourly-weighted → auto reference scalar → fail-closed.

KRİTİK regresyon guard'ları:
- epias_manual + hourly → hourly kazanır (bugünkü davranış korunur)
- seed + hourly → hourly kazanır
- manual_override + hourly → MANUAL kazanır
- manual_override + ptf<=0/None → override SAYILMAZ, fallback devam eder
"""
import pytest
from fastapi.testclient import TestClient

from app.calculator import get_ptf_yekdem_for_period
from app.models import OfferParams

PERIOD = "2099-01"
DATE = "2099-01-01"
MARKET = [(10, 1000.0), (19, 3000.0), (2, 500.0)]  # puant_agir proxy = 2150
PROXY = 2150.0
MANUAL_PTF = 1234.5


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    import app.pricing.schemas  # noqa: F401
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture()
def client(db):
    from app.main import app as fastapi_app
    from app.database import get_db
    fastapi_app.dependency_overrides[get_db] = lambda: db
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def _seed_hourly(db):
    from app.pricing.schemas import HourlyMarketPrice
    for h, p in MARKET:
        db.add(HourlyMarketPrice(period=PERIOD, date=DATE, hour=h,
                                 ptf_tl_per_mwh=p, smf_tl_per_mwh=p, is_active=1))


def _seed_ref(db, source, ptf=MANUAL_PTF, yekdem=300.0):
    from app.database import MarketReferencePrice
    db.add(MarketReferencePrice(period=PERIOD, price_type="PTF",
                                ptf_tl_per_mwh=ptf, yekdem_tl_per_mwh=yekdem,
                                source=source, is_locked=0))


def _seed_profile(db, customer_id="cansu"):
    from app.pricing.schemas import ConsumptionProfile, ConsumptionHourlyData
    prof = ConsumptionProfile(customer_id=customer_id, customer_name="C",
                              period=PERIOD, total_kwh=100.0, is_active=1)
    db.add(prof); db.flush()
    for h, _ in MARKET:
        db.add(ConsumptionHourlyData(profile_id=prof.id, date=DATE, hour=h,
                                     consumption_kwh=(100.0 if h == 19 else 0.0)))


# ── Calculator precedence ──────────────────────────────────────────────────────
class TestCalculatorPrecedence:
    def _call(self, db, **params_kw):
        params = OfferParams(use_reference_prices=True, **params_kw)
        return get_ptf_yekdem_for_period(db, PERIOD, params, tariff_group=None)

    def test_manual_override_beats_hourly(self, db):
        _seed_hourly(db); _seed_ref(db, "manual_override"); db.commit()
        ptf, _, source, _, _ = self._call(db)
        assert source == "manual_override"
        assert ptf == pytest.approx(MANUAL_PTF, abs=0.01)

    def test_epias_manual_does_NOT_override_hourly(self, db):
        """REGRESYON: epias_manual auto skaler → hourly kazanır."""
        _seed_hourly(db); _seed_ref(db, "epias_manual"); db.commit()
        ptf, _, source, _, _ = self._call(db)
        assert source == "hourly_weighted:puant_agir"
        assert ptf == pytest.approx(PROXY, abs=0.01)

    def test_seed_does_NOT_override_hourly(self, db):
        """REGRESYON: seed → hourly kazanır."""
        _seed_hourly(db); _seed_ref(db, "seed"); db.commit()
        _, _, source, _, _ = self._call(db)
        assert source == "hourly_weighted:puant_agir"

    def test_manual_override_beats_c2_real_consumption(self, db, monkeypatch):
        """Priority 2 > 3: manuel kayıt, gerçek tüketim (C2) profilinden de önce."""
        monkeypatch.setattr("app.market_prices.OFFER_USE_REAL_CONSUMPTION", True)
        _seed_hourly(db); _seed_ref(db, "manual_override"); _seed_profile(db); db.commit()
        ptf, _, source, _, _ = self._call(db, customer_id="cansu")
        assert source == "manual_override"
        assert ptf == pytest.approx(MANUAL_PTF, abs=0.01)

    def test_request_override_beats_manual_override(self, db):
        """Priority 1 > 2: use_reference_prices=False override en üstte kalır."""
        _seed_hourly(db); _seed_ref(db, "manual_override"); db.commit()
        params = OfferParams(use_reference_prices=False, weighted_ptf_tl_per_mwh=999.0, yekdem_tl_per_mwh=50.0)
        ptf, _, source, _, _ = get_ptf_yekdem_for_period(db, PERIOD, params, tariff_group=None)
        assert source == "override"
        assert ptf == pytest.approx(999.0, abs=0.01)

    def test_manual_override_ptf_zero_guard_falls_back(self, db):
        """GUARD: manual_override ama ptf<=0 → override SAYILMAZ → hourly'ye düşer."""
        _seed_hourly(db); _seed_ref(db, "manual_override", ptf=0.0); db.commit()
        _, _, source, _, _ = self._call(db)
        assert source == "hourly_weighted:puant_agir"


# ── Endpoint precedence (GET /api/epias/prices) ─────────────────────────────────
class TestEndpointPrecedence:
    def _get(self, client):
        return client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=false&profile=puant_agir").json()

    def test_manual_override_beats_hourly(self, client, db):
        _seed_hourly(db); _seed_ref(db, "manual_override"); db.commit()
        body = self._get(client)
        assert body["weighted_ptf_source"] == "manual_override"
        assert body["weighted_ptf_tl_per_mwh"] == pytest.approx(MANUAL_PTF, abs=0.01)

    def test_epias_manual_does_NOT_override_hourly(self, client, db):
        _seed_hourly(db); _seed_ref(db, "epias_manual"); db.commit()
        body = self._get(client)
        assert body["weighted_ptf_source"] == "hourly_weighted:puant_agir"

    def test_manual_override_ptf_zero_guard_falls_back(self, client, db):
        _seed_hourly(db); _seed_ref(db, "manual_override", ptf=0.0); db.commit()
        body = self._get(client)
        assert body["weighted_ptf_source"] == "hourly_weighted:puant_agir"
