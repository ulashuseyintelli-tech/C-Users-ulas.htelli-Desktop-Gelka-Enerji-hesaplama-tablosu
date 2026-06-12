"""
Seviye 2-b / C2 — AI/full-process: get_ptf_yekdem_for_period gerçek-tüketim dalı.

Aynı flag (OFFER_USE_REAL_CONSUMPTION). KRİTİK kontroller:
- override önceliği BOZULMAZ (flag+profil olsa bile override en üstte)
- customer_id yok / flag kapalı → davranış birebir aynı (profil proxy)
"""
import pytest

from app.calculator import get_ptf_yekdem_for_period
from app.models import OfferParams

PERIOD = "2099-01"
DATE = "2099-01-01"
MARKET = [(10, 1000.0), (19, 3000.0), (2, 500.0)]  # 10→T1, 19→T2(puant), 2→T3


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    import app.pricing.schemas  # noqa: F401
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _seed_market(db):
    from app.pricing.schemas import HourlyMarketPrice
    for h, p in MARKET:
        db.add(HourlyMarketPrice(period=PERIOD, date=DATE, hour=h,
                                 ptf_tl_per_mwh=p, smf_tl_per_mwh=p, is_active=1))


def _seed_profile(db, customer_id="cansu"):
    """Tüm tüketim puant saatte (19) → gerçek ağırlıklı = 3000."""
    from app.pricing.schemas import ConsumptionProfile, ConsumptionHourlyData
    prof = ConsumptionProfile(customer_id=customer_id, customer_name="Cansu",
                              period=PERIOD, total_kwh=100.0, is_active=1)
    db.add(prof); db.flush()
    for h, _ in MARKET:
        db.add(ConsumptionHourlyData(profile_id=prof.id, date=DATE, hour=h,
                                     consumption_kwh=(100.0 if h == 19 else 0.0)))


class TestGetPtfRealConsumptionBranch:
    def test_flag_ON_customer_with_profile_uses_real(self, db, monkeypatch):
        monkeypatch.setattr("app.market_prices.OFFER_USE_REAL_CONSUMPTION", True)
        _seed_market(db); _seed_profile(db); db.commit()
        params = OfferParams(customer_id="cansu", use_reference_prices=True)
        ptf, yek, source, err, warn = get_ptf_yekdem_for_period(db, PERIOD, params, tariff_group=None)
        assert source == "hourly_consumption:cansu"
        assert ptf == pytest.approx(3000.0, abs=0.01)
        assert err is None

    def test_flag_OFF_uses_profile_proxy(self, db, monkeypatch):
        """Flag kapalı → customer_id olsa bile proxy (production birebir aynı)."""
        monkeypatch.setattr("app.market_prices.OFFER_USE_REAL_CONSUMPTION", False)
        _seed_market(db); _seed_profile(db); db.commit()
        params = OfferParams(customer_id="cansu", use_reference_prices=True)
        ptf, yek, source, err, warn = get_ptf_yekdem_for_period(db, PERIOD, params, tariff_group=None)
        assert source == "hourly_weighted:puant_agir"
        assert ptf == pytest.approx(2150.0, abs=0.01)  # (1.5*1000+3*3000+0.5*500)/5

    def test_flag_ON_no_profile_falls_back_to_proxy(self, db, monkeypatch):
        monkeypatch.setattr("app.market_prices.OFFER_USE_REAL_CONSUMPTION", True)
        _seed_market(db); db.commit()  # profil YOK
        params = OfferParams(customer_id="yok", use_reference_prices=True)
        _, _, source, _, _ = get_ptf_yekdem_for_period(db, PERIOD, params, tariff_group=None)
        assert source == "hourly_weighted:puant_agir"

    def test_flag_ON_no_customer_id_uses_proxy(self, db, monkeypatch):
        monkeypatch.setattr("app.market_prices.OFFER_USE_REAL_CONSUMPTION", True)
        _seed_market(db); _seed_profile(db); db.commit()
        params = OfferParams(customer_id=None, use_reference_prices=True)
        _, _, source, _, _ = get_ptf_yekdem_for_period(db, PERIOD, params, tariff_group=None)
        assert source == "hourly_weighted:puant_agir"

    def test_override_priority_preserved(self, db, monkeypatch):
        """KRİTİK: flag açık + firma + profil olsa BİLE override en üstte kalır."""
        monkeypatch.setattr("app.market_prices.OFFER_USE_REAL_CONSUMPTION", True)
        _seed_market(db); _seed_profile(db); db.commit()
        params = OfferParams(customer_id="cansu", use_reference_prices=False,
                             weighted_ptf_tl_per_mwh=999.0, yekdem_tl_per_mwh=50.0)
        ptf, yek, source, err, warn = get_ptf_yekdem_for_period(db, PERIOD, params, tariff_group=None)
        assert source == "override"
        assert ptf == pytest.approx(999.0, abs=0.01)
