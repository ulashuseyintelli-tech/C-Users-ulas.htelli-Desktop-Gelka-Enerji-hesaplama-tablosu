"""
SoT-X Seviye 1 — get_ptf_yekdem_for_period kaynak-sırası matrisi.

Kilitli davranış:
  override PTF varsa        -> "override" (hourly çağrılmaz)
  override yok + hourly var  -> "hourly_weighted:<profile>"
  hourly yok + reference>0   -> "reference_scalar" + meta_ptf_source_warning
  hourly yok + reference<=0  -> "not_found" (fail-closed)
"""
import pytest
from app.calculator import get_ptf_yekdem_for_period
from app.models import OfferParams


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    import app.pricing.schemas  # noqa: F401 — hourly_market_prices tablosunu Base'e kaydet
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _add_hourly(db, period, hour, ptf):
    from app.pricing.schemas import HourlyMarketPrice
    db.add(HourlyMarketPrice(period=period, date=f"{period}-01", hour=hour,
                             ptf_tl_per_mwh=ptf, smf_tl_per_mwh=ptf, is_active=1))


def _add_reference(db, period, ptf, yekdem=300.0):
    from app.database import MarketReferencePrice
    db.add(MarketReferencePrice(period=period, price_type="PTF",
                                ptf_tl_per_mwh=ptf, yekdem_tl_per_mwh=yekdem))


# saat 10→T1, 19→T2, 2→T3 (puant_agir weighted = 1550; duz = 1166.67)
def _seed_hourly(db, period):
    for h, p in [(10, 1000.0), (19, 2000.0), (2, 500.0)]:
        _add_hourly(db, period, h, p)


def test_override_first_hourly_not_consulted():
    db = _session()
    _seed_hourly(db, "2099-01")  # hourly olsa bile override öncelikli
    db.commit()
    params = OfferParams(use_reference_prices=False, weighted_ptf_tl_per_mwh=2222.0, yekdem_tl_per_mwh=300.0)
    ptf, yek, src, err, warn = get_ptf_yekdem_for_period(db, "2099-01", params, tariff_group="Sanayi")
    assert src == "override" and ptf == 2222.0 and err is None and warn is None


def test_hourly_weighted_sanayi_puant():
    db = _session()
    _seed_hourly(db, "2099-01")
    _add_reference(db, "2099-01", ptf=900.0, yekdem=300.0)  # YEKDEM buradan
    db.commit()
    params = OfferParams(use_reference_prices=True)
    ptf, yek, src, err, warn = get_ptf_yekdem_for_period(db, "2099-01", params, tariff_group="Sanayi")
    assert src == "hourly_weighted:puant_agir"
    assert ptf == pytest.approx(1550.0, abs=0.01)
    assert yek == 300.0  # aylık YEKDEM korunur
    assert warn is None


def test_hourly_weighted_mesken_duz():
    db = _session()
    _seed_hourly(db, "2099-01")
    db.commit()
    params = OfferParams(use_reference_prices=True)
    ptf, yek, src, err, warn = get_ptf_yekdem_for_period(db, "2099-01", params, tariff_group="Mesken")
    assert src == "hourly_weighted:duz"
    assert ptf == pytest.approx(1166.6667, abs=0.01)


def test_reference_fallback_with_warning():
    db = _session()
    _add_reference(db, "2099-02", ptf=850.0, yekdem=300.0)  # hourly YOK
    db.commit()
    params = OfferParams(use_reference_prices=True)
    ptf, yek, src, err, warn = get_ptf_yekdem_for_period(db, "2099-02", params, tariff_group="Sanayi")
    assert src == "reference_scalar"
    assert ptf == 850.0
    assert warn is not None and "aylık ortalama" in warn


def test_fail_closed_no_hourly_no_reference():
    db = _session()
    db.commit()  # ne hourly ne reference
    params = OfferParams(use_reference_prices=True)
    ptf, yek, src, err, warn = get_ptf_yekdem_for_period(db, "2099-03", params, tariff_group="Sanayi")
    assert src == "not_found" and err is not None and ptf == 0


def test_fail_closed_reference_zero():
    db = _session()
    _add_reference(db, "2099-04", ptf=0.0, yekdem=300.0)  # reference PTF <= 0
    db.commit()
    params = OfferParams(use_reference_prices=True)
    ptf, yek, src, err, warn = get_ptf_yekdem_for_period(db, "2099-04", params, tariff_group="Sanayi")
    assert src == "not_found" and err is not None


def test_fail_closed_db_none():
    params = OfferParams(use_reference_prices=True)
    ptf, yek, src, err, warn = get_ptf_yekdem_for_period(None, None, params)
    assert src == "not_found" and err is not None
