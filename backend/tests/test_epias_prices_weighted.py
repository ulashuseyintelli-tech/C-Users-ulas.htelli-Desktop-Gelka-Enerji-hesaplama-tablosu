"""
SoT-X Seviye 1 — GET /api/epias/prices/{period} profil-ağırlıklı PTF testleri.

Commit 3 (manuel akış): endpoint response genişlemesi (additive) + profil seçimi
+ reference_scalar fallback uyarısı. calculator.get_ptf_yekdem_for_period ile parite.

Gerçek in-memory SQLite + get_db override; ağ (EPİAŞ) çağrısı yok (auto_fetch=false).
"""
import pytest
from fastapi.testclient import TestClient


# Kontrollü saatlik kayıtlar: 10→T1(Gündüz), 19→T2(Puant), 2→T3(Gece)
RECORDS = [(10, 1000.0), (19, 2000.0), (2, 500.0)]
PERIOD = "2099-01"


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    import app.pricing.schemas  # noqa: F401 — HourlyMarketPrice'ı Base.metadata'a kaydet
    # TestClient endpoint'i ayrı thread'de çalıştırır → in-memory SQLite'ı
    # thread'ler arası paylaşmak için check_same_thread=False + StaticPool.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def client(db):
    from app.main import app as fastapi_app
    from app.database import get_db
    fastapi_app.dependency_overrides[get_db] = lambda: db
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def _add_hourly(db, period, hour, ptf, is_active=1):
    from app.pricing.schemas import HourlyMarketPrice
    db.add(HourlyMarketPrice(period=period, date=f"{period}-01", hour=hour,
                             ptf_tl_per_mwh=ptf, smf_tl_per_mwh=ptf, is_active=is_active))


def _seed_reference(db, period, ptf, yekdem=300.0):
    from app.market_prices import upsert_market_prices
    ok, msg = upsert_market_prices(db, period, ptf, yekdem)
    assert ok, msg


class TestWeightedFromHourly:
    def test_hourly_present_puant_profile_returns_weighted(self, client, db):
        for h, p in RECORDS:
            _add_hourly(db, PERIOD, h, p)
        _seed_reference(db, PERIOD, 1234.0, yekdem=300.0)  # yekdem kaynağı
        db.commit()

        r = client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=false&profile=puant_agir")
        assert r.status_code == 200
        body = r.json()
        # (1.5*1000 + 3*2000 + 0.5*500) / 5 = 1550
        assert body["weighted_ptf_tl_per_mwh"] == pytest.approx(1550.0, abs=0.01)
        assert body["weighted_ptf_source"] == "hourly_weighted:puant_agir"
        assert body["weighted_ptf_profile"] == "puant_agir"
        assert body["ptf_source_warning"] is None

    def test_additive_scalar_field_preserved(self, client, db):
        for h, p in RECORDS:
            _add_hourly(db, PERIOD, h, p)
        _seed_reference(db, PERIOD, 1234.0)
        db.commit()
        body = client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=false").json()
        # Mevcut skaler alan bozulmadı (DB referansı = 1234)
        assert body["ptf_tl_per_mwh"] == pytest.approx(1234.0, abs=0.01)

    def test_profile_change_changes_weighted(self, client, db):
        for h, p in RECORDS:
            _add_hourly(db, PERIOD, h, p)
        db.commit()
        duz = client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=false&profile=duz").json()
        gece = client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=false&profile=gece_agir").json()
        # duz: 1166.67, gece: 777.78 → profil refetch'i farklı değer üretir
        assert duz["weighted_ptf_tl_per_mwh"] == pytest.approx(1166.6667, abs=0.01)
        assert gece["weighted_ptf_tl_per_mwh"] == pytest.approx(777.7778, abs=0.01)


class TestReferenceFallbackWarning:
    def test_no_hourly_falls_back_to_scalar_with_warning(self, client, db):
        _seed_reference(db, PERIOD, 2500.0)  # saatlik yok, yalnız aylık skaler
        db.commit()
        body = client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=false&profile=puant_agir").json()
        assert body["weighted_ptf_tl_per_mwh"] == pytest.approx(2500.0, abs=0.01)
        assert body["weighted_ptf_source"] == "reference_scalar"
        assert body["ptf_source_warning"] is not None
        assert "aylık" in body["ptf_source_warning"]


class TestProfileDefaultParity:
    """profile boşsa: tariff_group default → puant_agir fail-safe (calculator parite)."""

    def test_tariff_group_mesken_defaults_duz(self, client, db):
        for h, p in RECORDS:
            _add_hourly(db, PERIOD, h, p)
        db.commit()
        body = client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=false&tariff_group=Mesken").json()
        assert body["weighted_ptf_profile"] == "duz"

    def test_tariff_group_sanayi_defaults_puant(self, client, db):
        for h, p in RECORDS:
            _add_hourly(db, PERIOD, h, p)
        db.commit()
        body = client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=false&tariff_group=Sanayi OG").json()
        assert body["weighted_ptf_profile"] == "puant_agir"

    def test_no_profile_no_tariff_defaults_puant(self, client, db):
        for h, p in RECORDS:
            _add_hourly(db, PERIOD, h, p)
        db.commit()
        body = client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=false").json()
        assert body["weighted_ptf_profile"] == "puant_agir"

    def test_explicit_profile_overrides_tariff_default(self, client, db):
        for h, p in RECORDS:
            _add_hourly(db, PERIOD, h, p)
        db.commit()
        # Mesken default duz olurdu; explicit gece_agir kazanır
        body = client.get(
            f"/api/epias/prices/{PERIOD}?auto_fetch=false&profile=gece_agir&tariff_group=Mesken"
        ).json()
        assert body["weighted_ptf_profile"] == "gece_agir"
