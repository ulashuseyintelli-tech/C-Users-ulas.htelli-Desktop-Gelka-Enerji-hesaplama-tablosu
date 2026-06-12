"""
Seviye 2-b / C1 — Manuel akış: gerçek tüketim-ağırlıklı PTF (flag-gated).

- consumption_weighted_ptf helper (gerçek Σ kWh×PTF / Σ kWh, recon ile parite)
- GET /api/epias/prices/{period}?customer_id=... + OFFER_USE_REAL_CONSUMPTION flag
- KRİTİK: flag KAPALIYKEN production davranışı birebir aynı (profil proxy).
"""
import pytest
from fastapi.testclient import TestClient


PERIOD = "2099-01"
DATE = "2099-01-01"
# market: 10→T1(1000), 19→T2(3000), 2→T3(500)
MARKET = [(10, 1000.0), (19, 3000.0), (2, 500.0)]


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


def _seed_market(db):
    from app.pricing.schemas import HourlyMarketPrice
    for h, p in MARKET:
        db.add(HourlyMarketPrice(period=PERIOD, date=DATE, hour=h,
                                 ptf_tl_per_mwh=p, smf_tl_per_mwh=p, is_active=1))


def _seed_profile(db, customer_id="cansu", peak_kwh=100.0):
    """Tüm tüketim puant saatte (19) → gerçek ağırlıklı = puant PTF (3000)."""
    from app.pricing.schemas import ConsumptionProfile, ConsumptionHourlyData
    prof = ConsumptionProfile(customer_id=customer_id, customer_name="Cansu",
                              period=PERIOD, total_kwh=peak_kwh, is_active=1)
    db.add(prof)
    db.flush()
    kwh_map = {10: 0.0, 19: peak_kwh, 2: 0.0}
    for h, _ in MARKET:
        db.add(ConsumptionHourlyData(profile_id=prof.id, date=DATE, hour=h,
                                     consumption_kwh=kwh_map[h]))
    return prof


class TestHelper:
    def test_real_consumption_weighted(self, db):
        from app.market_prices import consumption_weighted_ptf
        _seed_market(db)
        _seed_profile(db)
        db.commit()
        # tüm tüketim 19'da (ptf 3000) → ağırlıklı = 3000
        assert consumption_weighted_ptf(db, PERIOD, "cansu") == pytest.approx(3000.0, abs=0.01)

    def test_no_profile_returns_none(self, db):
        from app.market_prices import consumption_weighted_ptf
        _seed_market(db)
        db.commit()
        assert consumption_weighted_ptf(db, PERIOD, "yok") is None


class TestEndpointFlag:
    def _url(self, customer="cansu"):
        return f"/api/epias/prices/{PERIOD}?auto_fetch=false&profile=puant_agir&customer_id={customer}"

    def test_flag_OFF_uses_profile_proxy_not_consumption(self, client, db, monkeypatch):
        """Flag kapalı → customer_id verilse bile PROXY (production birebir aynı)."""
        monkeypatch.setattr("app.market_prices.OFFER_USE_REAL_CONSUMPTION", False)
        _seed_market(db); _seed_profile(db); db.commit()
        body = client.get(self._url()).json()
        # puant_agir proxy = (1.5*1000+3*3000+0.5*500)/5 = 2150
        assert body["weighted_ptf_source"] == "hourly_weighted:puant_agir"
        assert body["weighted_ptf_tl_per_mwh"] == pytest.approx(2150.0, abs=0.01)

    def test_flag_ON_uses_real_consumption(self, client, db, monkeypatch):
        """Flag açık + firma + profil → GERÇEK tüketim-ağırlıklı (3000)."""
        monkeypatch.setattr("app.market_prices.OFFER_USE_REAL_CONSUMPTION", True)
        _seed_market(db); _seed_profile(db); db.commit()
        body = client.get(self._url()).json()
        assert body["weighted_ptf_source"] == "hourly_consumption:cansu"
        assert body["weighted_ptf_tl_per_mwh"] == pytest.approx(3000.0, abs=0.01)

    def test_flag_ON_no_profile_falls_back_to_proxy(self, client, db, monkeypatch):
        """Flag açık ama profil yok → proxy'ye düşer (fail-safe)."""
        monkeypatch.setattr("app.market_prices.OFFER_USE_REAL_CONSUMPTION", True)
        _seed_market(db); db.commit()  # profil YOK
        body = client.get(self._url(customer="yok")).json()
        assert body["weighted_ptf_source"] == "hourly_weighted:puant_agir"
        assert body["weighted_ptf_tl_per_mwh"] == pytest.approx(2150.0, abs=0.01)

    def test_flag_ON_no_customer_id_uses_proxy(self, client, db, monkeypatch):
        """Flag açık ama firma seçilmemiş → proxy (geriye-uyum)."""
        monkeypatch.setattr("app.market_prices.OFFER_USE_REAL_CONSUMPTION", True)
        _seed_market(db); _seed_profile(db); db.commit()
        body = client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=false&profile=puant_agir").json()
        assert body["weighted_ptf_source"] == "hourly_weighted:puant_agir"
