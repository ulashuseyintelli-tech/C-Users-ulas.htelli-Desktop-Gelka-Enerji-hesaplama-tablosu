"""
Liste ucu YANIT SÖZLEŞMESİ testleri (dar kapsam).

Gerçek pakette gözlenen kusur: `GET /admin/market-prices` yanıtı `ptf_value`
döndürüyordu; frontend (PriceListTable) `ptf_tl_per_mwh` okuyordu → ekranda
"NaN". `yekdem_tl_per_mwh` ise yanıtta hiç yoktu.

Bu testler şunu kilitler:
- iki alan GERÇEK kayıttan yanıta eklenir,
- `ptf_value` ve mevcut alanlar KORUNUR (geriye dönük uyumluluk),
- EKSİK fiyat SIFIRA ÇEVRİLMEZ (null kalır).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BEKLENEN_ALANLAR = {
    "period", "ptf_value", "ptf_tl_per_mwh", "yekdem_tl_per_mwh",
    "price_type", "source", "source_note", "change_reason",
    "status", "captured_at", "is_locked", "updated_by", "updated_at",
}


@pytest.fixture()
def istemci():
    from app.database import Base, MarketReferencePrice, get_db
    from app.main import app as fastapi_app

    motor = create_engine("sqlite://", connect_args={"check_same_thread": False},
                          poolclass=StaticPool)
    Base.metadata.create_all(motor, tables=[MarketReferencePrice.__table__])
    db = sessionmaker(bind=motor, autoflush=False, autocommit=False)()
    # Dolu kayıt (sentetik prova değerleri) + YEKDEM'i EKSİK olan kayıt
    db.add(MarketReferencePrice(price_type="PTF", period="2019-03",
                                ptf_tl_per_mwh=1234.56, yekdem_tl_per_mwh=78.9,
                                status="provisional", source="manual_override",
                                source_note="sentetik prova", change_reason="izole kabul"))
    db.add(MarketReferencePrice(price_type="PTF", period="2019-04",
                                ptf_tl_per_mwh=500.0, yekdem_tl_per_mwh=None,
                                status="provisional", source="manual_override"))
    db.commit()

    fastapi_app.dependency_overrides[get_db] = lambda: db
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()
    db.close()


def _satirlar(istemci):
    yanit = istemci.get("/admin/market-prices",
                        params={"page": 1, "page_size": 50, "price_type": "PTF"})
    assert yanit.status_code == 200, yanit.text
    return {s["period"]: s for s in yanit.json()["items"]}


def test_liste_yaniti_frontendin_okudugu_alanlari_icerir(istemci):
    s = _satirlar(istemci)["2019-03"]
    assert s["ptf_tl_per_mwh"] == pytest.approx(1234.56)
    assert s["yekdem_tl_per_mwh"] == pytest.approx(78.9)


def test_mevcut_alanlar_korunur_geriye_donuk(istemci):
    s = _satirlar(istemci)["2019-03"]
    assert s["ptf_value"] == pytest.approx(1234.56), "ptf_value KALDIRILMAMALI"
    for ad in ("period", "status", "captured_at", "is_locked", "updated_by", "updated_at"):
        assert ad in s, f"mevcut alan kayboldu: {ad}"


def test_yanit_alan_kumesi_beklenen_ile_ayni(istemci):
    s = _satirlar(istemci)["2019-03"]
    assert set(s.keys()) == BEKLENEN_ALANLAR


def test_yanit_katmani_deger_UYDURMAZ(istemci):
    """Yanıt katmanı kayıttaki değeri OLDUĞU GİBİ geçirir; 0'a çevirmez/uydurmaz.

    ⚠️ ŞEMA SINIRI (bu GO'nun kapsamı DIŞINDA, migration yok):
    `MarketReferencePrice.yekdem_tl_per_mwh` kolonu `nullable=False, default=0`
    olduğu için "değer yok" DB düzeyinde ZATEN 0 olarak saklanır — yani
    "eksik" ile "sıfır" veri modelinde ayrılamaz. Yanıt katmanı bu durumu
    DÜZELTMEZ ama KÖTÜLEŞTİRMEZ de: `or 0` gibi bir dönüşüm YAPMAZ.
    Arayüz tarafında null/undefined "—" gösterilir (0 ile karışmaz).
    """
    s = _satirlar(istemci)["2019-04"]
    assert s["ptf_tl_per_mwh"] == pytest.approx(500.0)
    # Kayıtta 0 duruyorsa yanıtta da 0 görünür (atlanmaz, null'a çevrilmez):
    assert s["yekdem_tl_per_mwh"] == pytest.approx(0.0)
    assert s["yekdem_tl_per_mwh"] is not None


def test_iki_alan_kayittaki_degerin_AYNISI(istemci):
    """ptf_value ile yeni ptf_tl_per_mwh AYNI kaynaktan gelir (kopya değil, aynı değer)."""
    s = _satirlar(istemci)["2019-03"]
    assert s["ptf_tl_per_mwh"] == s["ptf_value"]


def test_tablonun_okudugu_kayit_alanlari_GERCEK_degerle_doner(istemci):
    s = _satirlar(istemci)["2019-03"]
    assert s["price_type"] == "PTF"
    assert s["source"] == "manual_override"
    assert s["source_note"] == "sentetik prova"
    assert s["change_reason"] == "izole kabul"


def test_bos_kayit_alanlari_UYDURULMAZ_null_kalir(istemci):
    s = _satirlar(istemci)["2019-04"]
    assert s["source_note"] is None
    assert s["change_reason"] is None
