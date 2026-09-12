"""
GERÇEK EpiasReadOnlyClient — ağsız protokol testleri (httpx.MockTransport).

FİKSTÜR DAYANAĞI: EPİAŞ Şeffaflık Elektrik Servisleri teknik dokümanı v1.15.15
(kamuya açık; seffaflik.epias.com.tr/electricity-service/technical). Fikstürler
üretim kodundan değil, aşağıda bölüm numarasıyla alıntılanan maddelerden
türetilmiştir. Belgenin çevrimdışı kopyası geliştiricinin yerel kanıt
klasöründedir; repoya EKLENMEZ.

- §2 "İsteklere Zorunlu Güvenlik Bilgisinin Eklenmesi":
  POST https://giris.epias.com.tr/cas/v1/tickets
  -H "Content-Type: application/x-www-form-urlencoded" -H "Accept: text/plain"
  -d 'username=...&password=...'
  "Başarılı Sonuç: HTTP 201 Created ile TGT değeri: TGT-..."
  Örnek Python/JS kodları bileti YANIT GÖVDESİNDEN okur (response.text).
  "Bu şekilde alınan TGT bilgisinin 2 saat geçerliliği vardır."
- Servisler: Kök dizin /electricity-service; 5.128 "POST /v1/markets/dam/data/mcp";
  5.256 "POST /v1/renewables/data/unit-cost"; her ikisinde "Header TGT gerekli".
- DTO'lar: 6.382 PtfResponseDto → items, page, **statistic** (TEKİL);
  6.383 PtfResponseStatisticsDto → priceAvg, ptfWeightedAvg;
  6.415 RenewableSmUnitCostDataDto → period, version, supplierUnitCost, unitCost, ptf;
  RenewableSmUnitCostResponseDto → items.
- Tarih biçimi: ISO-8601 "yyyy-MM-dd'T'HH:mm:ssXXX", Türkiye saati (+03:00).

Testler üretim kodunun varsayımını tekrarlamaz; fikstürler yukarıdaki belgelenen
biçimden üretilir. Belgede kanıtlanmayan noktalar testte SABİTLENMEZ.
"""

import logging
import os
from unittest.mock import patch

import httpx
import pytest

from app.epias_public_client import (
    BASE_URL, MCP_PATH, TGT_URL, UNIT_COST_PATH,
    EpiasIstemciHatasi, EpiasReadOnlyClient,
)

BELGE_TGT = "TGT-1234567890ABCDEF-cas01.example"
SIR = "S3cretP@ss"
KIMLIK = {"EPIAS_USERNAME": "kullanici@example.com", "EPIAS_PASSWORD": SIR}


def _istemci(handler):
    return EpiasReadOnlyClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


def _tgt_yaniti(istek):
    """Belgelenen başarılı TGT yanıtı: HTTP 201 + gövdede TGT-..."""
    return httpx.Response(201, text=BELGE_TGT)


def test_tgt_istegi_belgelenen_bicimde_gonderilir_ve_govdeden_okunur():
    kayit = {}

    def handler(istek):
        kayit["url"] = str(istek.url)
        kayit["method"] = istek.method
        kayit["content_type"] = istek.headers.get("content-type")
        kayit["accept"] = istek.headers.get("accept")
        kayit["govde"] = istek.content.decode()
        return _tgt_yaniti(istek)

    with patch.dict(os.environ, KIMLIK):
        c = _istemci(handler)
        bilet = c.get_tgt()
        c.kapat()

    assert bilet == BELGE_TGT
    assert kayit["method"] == "POST" and kayit["url"] == TGT_URL
    assert kayit["content_type"].startswith("application/x-www-form-urlencoded")
    assert kayit["accept"] == "text/plain"
    assert "username=" in kayit["govde"] and "password=" in kayit["govde"]


def test_tgt_govdesi_belgelenen_onekte_degilse_reddedilir():
    def handler(istek):
        return httpx.Response(201, text="<html>giris sayfasi</html>")

    with patch.dict(os.environ, KIMLIK):
        c = _istemci(handler)
        with pytest.raises(EpiasIstemciHatasi) as hata:
            c.get_tgt()
        c.kapat()
    assert "belgelenen" in str(hata.value)


def test_kimlik_dogrulama_hatasi_sir_sizdirmaz(caplog):
    def handler(istek):
        return httpx.Response(401, text="Unauthorized")

    with patch.dict(os.environ, KIMLIK), caplog.at_level(logging.DEBUG):
        c = _istemci(handler)
        with pytest.raises(EpiasIstemciHatasi) as hata:
            c.get_tgt()
        c.kapat()
    assert "401" in str(hata.value)
    assert SIR not in str(hata.value) and SIR not in caplog.text
    assert KIMLIK["EPIAS_USERNAME"] not in caplog.text


def test_kimlik_bilgisi_yoksa_ag_istegi_yapilmaz():
    cagri = {"sayi": 0}

    def handler(istek):
        cagri["sayi"] += 1
        return _tgt_yaniti(istek)

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("EPIAS_USERNAME", None)
        os.environ.pop("EPIAS_PASSWORD", None)
        c = _istemci(handler)
        with pytest.raises(EpiasIstemciHatasi):
            c.get_tgt()
        c.kapat()
    assert cagri["sayi"] == 0


# ── SERVİS İSTEKLERİ (TGT taşınması, ayrıştırma, bozuk yanıt, zaman aşımı) ────

# BELGE §5.128/5.256: iki servis de POST + "Header TGT gerekli".
# BELGE §6.382/6.383: PtfResponseDto.statistic (TEKİL) → priceAvg, ptfWeightedAvg.
PTF_YANITI = {
    "items": [{"date": "2026-05-01T00:00:00+03:00", "price": 2400.0}],
    "page": None,
    "statistic": {"priceAvg": 590.90, "ptfWeightedAvg": 795.70,
                  "priceMin": 0.0, "priceMax": 3400.0, "priceSum": 1.0},
}
# BELGE §6.415: RenewableSmUnitCostDataDto → period, version, supplierUnitCost,
# unitCost, ptf. Tarihler ISO-8601 "yyyy-MM-dd'T'HH:mm:ssXXX" (+03:00).
YEKDEM_YANITI = {
    "items": [
        {"period": "2026-05-01T00:00:00+03:00", "version": "2026-05-01T00:00:00+03:00",
         "supplierUnitCost": 1306.10, "unitCost": 1306.10, "ptf": 2214.0},
        {"period": "2026-05-01T00:00:00+03:00", "version": "2026-08-01T00:00:00+03:00",
         "supplierUnitCost": 1317.24, "unitCost": 1317.24, "ptf": 2214.0},
    ]
}


def _servis_handleri(yanitlar, kayit):
    """TGT'yi verir; servis isteklerini kaydedip verilen yanıtı döndürür."""

    def handler(istek):
        if str(istek.url) == TGT_URL:
            return httpx.Response(201, text=BELGE_TGT)
        kayit.append({
            "url": str(istek.url), "method": istek.method,
            "tgt": istek.headers.get("tgt"),
            "accept": istek.headers.get("accept"),
            "govde": istek.content.decode(),
            "authorization": istek.headers.get("authorization"),
            "cookie": istek.headers.get("cookie"),
        })
        return yanitlar(istek)

    return handler


def test_ptf_istegi_bileti_tgt_basliginda_tasir_ve_statistic_ayristirir():
    kayit = []
    c = _istemci(_servis_handleri(lambda i: httpx.Response(200, json=PTF_YANITI), kayit))
    with patch.dict(os.environ, KIMLIK):
        ozet = c.fetch_mcp("2026-05")
    c.kapat()

    assert len(kayit) == 1
    assert kayit[0]["method"] == "POST"
    assert kayit[0]["url"] == BASE_URL + MCP_PATH
    assert kayit[0]["tgt"] == BELGE_TGT          # bilet servis isteğine taşındı
    assert kayit[0]["authorization"] is None     # şifre başka yolla gitmiyor
    assert "2026-05-01T00:00:00+03:00" in kayit[0]["govde"]
    assert "2026-05-31T23:00:00+03:00" in kayit[0]["govde"]
    assert KIMLIK["EPIAS_PASSWORD"] not in kayit[0]["govde"]
    assert ozet.aritmetik == pytest.approx(590.90)
    assert ozet.agirlikli == pytest.approx(795.70)
    assert ozet.birim == "TL/MWh"


def test_ptf_yaniti_belgelenen_statistic_alani_yoksa_hata_verir():
    """'statistics' (ÇOĞUL) belgede yok; sessizce None dönmemeli."""
    kayit = []
    bozuk = {"items": [], "statistics": {"priceAvg": 590.90}}
    c = _istemci(_servis_handleri(lambda i: httpx.Response(200, json=bozuk), kayit))
    with patch.dict(os.environ, KIMLIK):
        with pytest.raises(EpiasIstemciHatasi) as hata:
            c.fetch_mcp("2026-05")
    c.kapat()
    assert "statistic" in str(hata.value)


def test_ptf_yaniti_json_degilse_hata_verir():
    kayit = []
    c = _istemci(_servis_handleri(lambda i: httpx.Response(200, text="<html>bakim</html>"), kayit))
    with patch.dict(os.environ, KIMLIK):
        with pytest.raises(EpiasIstemciHatasi) as hata:
            c.fetch_mcp("2026-05")
    c.kapat()
    assert "çözümlenemedi" in str(hata.value)


def test_servis_http_hatasi_kodu_bildirir_sir_sizdirmaz():
    kayit = []
    c = _istemci(_servis_handleri(lambda i: httpx.Response(500, text="internal"), kayit))
    with patch.dict(os.environ, KIMLIK):
        with pytest.raises(EpiasIstemciHatasi) as hata:
            c.fetch_mcp("2026-05")
    c.kapat()
    assert "500" in str(hata.value)
    assert KIMLIK["EPIAS_PASSWORD"] not in str(hata.value)
    assert BELGE_TGT not in str(hata.value)


def test_yekdem_ayristirma_donem_versiyon_ve_iki_segment():
    kayit = []
    c = _istemci(_servis_handleri(lambda i: httpx.Response(200, json=YEKDEM_YANITI), kayit))
    with patch.dict(os.environ, KIMLIK):
        satirlar = c.fetch_unit_cost("2026-05", "2026-05")
    c.kapat()

    assert kayit[0]["url"] == BASE_URL + UNIT_COST_PATH
    assert kayit[0]["tgt"] == BELGE_TGT
    assert [(s.donem, s.versiyon) for s in satirlar] == [("2026-05", "2026-05"), ("2026-05", "2026-08")]
    assert satirlar[0].serbest_tuketici == pytest.approx(1306.10)   # supplierUnitCost
    assert satirlar[0].gts_k1 == pytest.approx(1306.10)             # unitCost
    assert satirlar[1].serbest_tuketici == pytest.approx(1317.24)


def test_yekdem_bozuk_satir_sayisal_olmayan_degeri_none_yapar():
    kayit = []
    bozuk = {"items": [{"period": "2026-05-01T00:00:00+03:00", "version": None,
                        "supplierUnitCost": "yok", "unitCost": None}]}
    c = _istemci(_servis_handleri(lambda i: httpx.Response(200, json=bozuk), kayit))
    with patch.dict(os.environ, KIMLIK):
        satirlar = c.fetch_unit_cost("2026-05", "2026-05")
    c.kapat()
    assert satirlar[0].versiyon is None
    assert satirlar[0].serbest_tuketici is None and satirlar[0].gts_k1 is None


def test_zaman_asimi_istemci_hatasina_donusur_ve_sir_sizdirmaz(caplog):
    def handler(istek):
        if str(istek.url) == TGT_URL:
            return httpx.Response(201, text=BELGE_TGT)
        raise httpx.ReadTimeout("timed out", request=istek)

    c = _istemci(handler)
    with patch.dict(os.environ, KIMLIK), caplog.at_level(logging.DEBUG):
        with pytest.raises(EpiasIstemciHatasi) as hata:
            c.fetch_mcp("2026-05")
    c.kapat()
    assert "başarısız" in str(hata.value)
    assert KIMLIK["EPIAS_PASSWORD"] not in str(hata.value)
    assert KIMLIK["EPIAS_PASSWORD"] not in caplog.text
    assert BELGE_TGT not in caplog.text


def test_varsayilan_istemci_sonlu_zaman_asimi_ile_kurulur():
    """http verilmediğinde gerçek httpx.Client SONLU timeout ile oluşturulmalı."""
    from app.epias_public_client import VARSAYILAN_ZAMAN_ASIMI

    c = EpiasReadOnlyClient()
    http = c._client()
    try:
        assert isinstance(http, httpx.Client)
        assert http.timeout.connect == VARSAYILAN_ZAMAN_ASIMI
        assert http.timeout.read == VARSAYILAN_ZAMAN_ASIMI
        assert None not in (http.timeout.connect, http.timeout.read,
                            http.timeout.write, http.timeout.pool)
    finally:
        c.kapat()
    assert http.is_closed


def test_bilet_bir_kez_alinir_ve_kapatinca_unutulur():
    tgt_sayaci = {"n": 0}

    def handler(istek):
        if str(istek.url) == TGT_URL:
            tgt_sayaci["n"] += 1
            return httpx.Response(201, text=BELGE_TGT)
        return httpx.Response(200, json=PTF_YANITI)

    c = _istemci(handler)
    with patch.dict(os.environ, KIMLIK):
        c.fetch_mcp("2026-05")
        c.fetch_mcp("2026-06")
        assert tgt_sayaci["n"] == 1
        c.kapat()
        assert c._tgt is None


# ── UÇ: servis/ayrıştırma hatası KULLANICI ARALIK HATASI (422) DEĞİLDİR ──────

@pytest.fixture()
def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base, MarketReferencePrice, PriceChangeHistory

    motor = create_engine("sqlite://", connect_args={"check_same_thread": False},
                          poolclass=StaticPool)
    Base.metadata.create_all(motor, tables=[MarketReferencePrice.__table__,
                                            PriceChangeHistory.__table__])
    db = sessionmaker(bind=motor, autoflush=False, autocommit=False)()
    db.add(MarketReferencePrice(price_type="PTF", period="2026-05", ptf_tl_per_mwh=590.90,
                                yekdem_tl_per_mwh=1306.10, status="provisional",
                                source="epias_api.mcp_avg"))
    db.commit()
    yield db
    db.close()


def _uc(db, handler):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[get_db] = lambda: db
    yapilan = {}

    def fabrika(*a, **k):
        yapilan["istemci"] = _istemci(handler)
        return yapilan["istemci"]

    return TestClient(fastapi_app), fabrika, yapilan


def _temizle():
    from app.main import app as fastapi_app
    fastapi_app.dependency_overrides.clear()


def test_uc_servis_hatasi_422_uretmez_ve_sir_sizdirmaz(_db):
    """EPİAŞ 500 + bozuk yanıt: 200 + uyarı; ASLA 422 (kullanıcı aralık hatası)."""
    def handler(istek):
        if str(istek.url) == TGT_URL:
            return httpx.Response(201, text=BELGE_TGT)
        return httpx.Response(500, text="EPIAS internal error " + SIR)

    istemci, fabrika, yapilan = _uc(_db, handler)
    try:
        with patch.dict(os.environ, dict(KIMLIK, EPIAS_COMPARE_ENABLED="true")), \
             patch("app.epias_public_client.EpiasReadOnlyClient", fabrika):
            yanit = istemci.get("/admin/market-prices/epias-compare",
                                params={"from_period": "2026-05", "to_period": "2026-05"})
    finally:
        _temizle()

    assert yanit.status_code == 200, yanit.text
    assert yanit.status_code != 422
    kodlar = {u["kod"] for u in yanit.json()["uyarilar"]}
    assert "ptf_api_hatasi" in kodlar and "yekdem_api_hatasi" in kodlar
    assert SIR not in yanit.text and BELGE_TGT not in yanit.text
    assert yapilan["istemci"]._http.is_closed   # hata yolunda da kapatıldı


def test_uc_tgt_reddi_422_uretmez(_db):
    """Kimlik doğrulama hatası kullanıcı girdisi hatası DEĞİLDİR."""
    def handler(istek):
        return httpx.Response(401, text="Unauthorized")

    istemci, fabrika, yapilan = _uc(_db, handler)
    try:
        with patch.dict(os.environ, dict(KIMLIK, EPIAS_COMPARE_ENABLED="true")), \
             patch("app.epias_public_client.EpiasReadOnlyClient", fabrika):
            yanit = istemci.get("/admin/market-prices/epias-compare",
                                params={"from_period": "2026-05", "to_period": "2026-05"})
    finally:
        _temizle()

    assert yanit.status_code == 200 and yanit.status_code != 422
    kodlar = {u["kod"] for u in yanit.json()["uyarilar"]}
    assert "ptf_api_hatasi" in kodlar
    assert SIR not in yanit.text
    assert yapilan["istemci"]._http.is_closed


def test_uc_basarili_gercek_istemci_zinciri_ve_yazma_yok(_db):
    """Gerçek istemci + MockTransport ile uçtan uca: 200, DB'ye yazma yok."""
    from app.database import MarketReferencePrice

    def handler(istek):
        if str(istek.url) == TGT_URL:
            return httpx.Response(201, text=BELGE_TGT)
        if str(istek.url).endswith(MCP_PATH):
            return httpx.Response(200, json=PTF_YANITI)
        return httpx.Response(200, json=YEKDEM_YANITI)

    once = [(k.id, k.ptf_tl_per_mwh, k.yekdem_tl_per_mwh, k.status, k.source)
            for k in _db.query(MarketReferencePrice).all()]
    istemci, fabrika, yapilan = _uc(_db, handler)
    try:
        with patch.dict(os.environ, dict(KIMLIK, EPIAS_COMPARE_ENABLED="true")), \
             patch("app.epias_public_client.EpiasReadOnlyClient", fabrika):
            yanit = istemci.get("/admin/market-prices/epias-compare",
                                params={"from_period": "2026-05", "to_period": "2026-05",
                                        "evaluated_at": "2026-06-30"})
    finally:
        _temizle()

    assert yanit.status_code == 200, yanit.text
    govde = yanit.json()
    assert govde["kayit"] == "EPIAS-KARSILASTIRMA"
    assert BELGE_TGT not in yanit.text and SIR not in yanit.text
    sonra = [(k.id, k.ptf_tl_per_mwh, k.yekdem_tl_per_mwh, k.status, k.source)
             for k in _db.query(MarketReferencePrice).all()]
    assert once == sonra
    assert yapilan["istemci"]._http.is_closed


def test_maskeleme_gomulu_bilet_ve_parolayi_temizler():
    """Vakum test değil: hata metni GERÇEKTEN sırrı taşıyor, maskelenmeli."""
    from app.epias_public_client import _maskele

    def handler(istek):
        if str(istek.url) == TGT_URL:
            return httpx.Response(201, text=BELGE_TGT)
        raise httpx.ConnectError(
            "proxy reddetti: ticket=%s&password=%s" % (BELGE_TGT, SIR), request=istek)

    c = _istemci(handler)
    with patch.dict(os.environ, KIMLIK):
        with pytest.raises(EpiasIstemciHatasi) as hata:
            c.fetch_mcp("2026-05")
        mesaj = str(hata.value)
        # Maskeleme kapalıyken sızacağını da göster (test kendini kanıtlar):
        assert SIR in "proxy reddetti: ticket=%s&password=%s" % (BELGE_TGT, SIR)
        assert _maskele(SIR) == "***"
    c.kapat()
    assert SIR not in mesaj
    assert BELGE_TGT not in mesaj and "TGT-" not in mesaj


# ── ÇALIŞMA MODELİ: senkron uç olay döngüsünde ÇALIŞMAZ ─────────────────────

def test_uc_govdesi_olay_dongusunde_calismaz(_db):
    """Senkron (def) uç, FastAPI tarafından iş parçacığı havuzunda çalışmalı.

    Kanıt: gövde içinde çalışan bir olay döngüsü YOKTUR (get_running_loop
    RuntimeError verir) ve iş parçacığı ana iş parçacığı değildir.
    Bu iddiayı kod yorumuna değil, gözleme bağlar.
    """
    import asyncio
    import inspect
    import threading

    import app.main as m

    gozlem = {}
    assert not inspect.iscoroutinefunction(m.epias_compare_endpoint), "uç async tanımlanmış"

    def handler(istek):
        if str(istek.url) == TGT_URL:
            return httpx.Response(201, text=BELGE_TGT)
        if str(istek.url).endswith(MCP_PATH):
            return httpx.Response(200, json=PTF_YANITI)
        return httpx.Response(200, json=YEKDEM_YANITI)

    import app.epias_compare as ec
    gercek_build = ec.build_comparison   # yama ONCESI bagla (ozyineleme olmasin)

    def izleyen_build(*a, **k):
        try:
            asyncio.get_running_loop()
            gozlem["dongu"] = True
        except RuntimeError:
            gozlem["dongu"] = False
        gozlem["ana_iplik"] = threading.current_thread() is threading.main_thread()
        return gercek_build(*a, **k)

    istemci, fabrika, _yapilan = _uc(_db, handler)
    try:
        with patch.dict(os.environ, dict(KIMLIK, EPIAS_COMPARE_ENABLED="true")), \
             patch("app.epias_public_client.EpiasReadOnlyClient", fabrika), \
             patch("app.epias_compare.build_comparison", izleyen_build):
            yanit = istemci.get("/admin/market-prices/epias-compare",
                                params={"from_period": "2026-05", "to_period": "2026-05"})
    finally:
        _temizle()

    assert yanit.status_code == 200, yanit.text
    assert gozlem["dongu"] is False, "uç gövdesi olay döngüsünde çalıştı (bloklama riski)"
    assert gozlem["ana_iplik"] is False, "uç gövdesi ana iş parçacığında çalıştı"


def test_rota_sirasi_period_rotasindan_once(_db):
    """'epias-compare' bir dönem sanılmamalı: literal rota önce kayıtlı olmalı."""
    from app.main import app as fastapi_app

    yollar = [getattr(r, "path", "") for r in fastapi_app.routes]
    assert "/admin/market-prices/epias-compare" in yollar
    assert "/admin/market-prices/{period}" in yollar
    assert yollar.index("/admin/market-prices/epias-compare") < yollar.index("/admin/market-prices/{period}")
