"""
SALT OKUNUR EPİAŞ karşılaştırması — sentetik, AĞSIZ testler.

Kapsam (READ_ONLY_EPIAS_COMPARISON):
- Yetkisiz erişim, özellik kapalı, API hatası, eksik/uyuşmayan kimlik,
  doğru değer farkı, sır maskeleme ve HİÇBİR DB YAZIMI olmaması.
- Gerçek EPİAŞ çağrısı YOK: istemci sahte nesneyle değiştirilir.
"""

import hashlib
import os
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.epias_compare import (
    BILINMIYOR, ESLESTI, UYGULANAMAZ, UYUSMUYOR,
    TIP_AYNI, TIP_DEGER_FARKI, TIP_DONEM, TIP_SEGMENT, TIP_VERSIYON, TIP_YOK, TIP_YONTEM,
    classify_row, ptf_kimligi, yekdem_kimligi,
)
from app.epias_public_client import BIRIM, EpiasIstemciHatasi, PtfOzet, YekdemSatiri, _maskele

YAZMA_ANAHTARLARI = ("INSERT", "UPDATE", "DELETE", "REPLACE", "DROP", "ALTER", "CREATE")


class SahteIstemci:
    """Ağ kullanmayan sahte EPİAŞ istemcisi."""

    def __init__(self, ptf=None, yekdem=None, ptf_hata=None, yekdem_hata=None):
        self._ptf = ptf or {}
        self._yekdem = yekdem or []
        self._ptf_hata = ptf_hata
        self._yekdem_hata = yekdem_hata
        self.cagrilar = []

    def fetch_mcp(self, donem):
        self.cagrilar.append(("mcp", donem))
        if self._ptf_hata:
            raise EpiasIstemciHatasi(self._ptf_hata)
        return self._ptf.get(donem) or PtfOzet(donem=donem, aritmetik=None, agirlikli=None)

    def kapat(self):
        self.cagrilar.append(("kapat",))

    def fetch_unit_cost(self, bas, bit):
        self.cagrilar.append(("unit-cost", bas, bit))
        if self._yekdem_hata:
            raise EpiasIstemciHatasi(self._yekdem_hata)
        return list(self._yekdem)


@pytest.fixture()
def db_ortami():
    """Bellek içi SQLite + yalnız iki tablo; ifade dinleyicisi ile yazma denetimi."""
    from app.database import Base, MarketReferencePrice, PriceChangeHistory

    motor = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(motor, tables=[MarketReferencePrice.__table__, PriceChangeHistory.__table__])
    Oturum = sessionmaker(bind=motor, autoflush=False, autocommit=False)
    db = Oturum()

    db.add(MarketReferencePrice(price_type="PTF", period="2026-05", ptf_tl_per_mwh=590.90,
                                yekdem_tl_per_mwh=1306.10, status="provisional",
                                source="epias_api.mcp_avg"))
    db.add(MarketReferencePrice(price_type="PTF", period="2026-06", ptf_tl_per_mwh=1240.16,
                                yekdem_tl_per_mwh=1083.63, status="provisional",
                                source="manual_override"))
    db.commit()
    kayit = db.query(MarketReferencePrice).filter_by(period="2026-05").one()
    db.add(PriceChangeHistory(price_record_id=kayit.id, price_type="YEKDEM", period="2026-05",
                              action="INSERT", new_value=1306.10, new_status="provisional",
                              source="epias_api.uc.st.2026-05"))
    db.commit()

    ifadeler = []

    @event.listens_for(motor, "before_cursor_execute")
    def _dinle(conn, cursor, statement, parameters, context, executemany):
        ifadeler.append(statement)

    yield {"db": db, "motor": motor, "ifadeler": ifadeler}
    db.close()


def icerik_ozeti(db):
    """Tabloların TAM İÇERİĞİNİN özeti (satır sayısı tek başına yeterli değildir)."""
    from app.database import MarketReferencePrice, PriceChangeHistory

    parcalar = []
    for kayit in db.query(MarketReferencePrice).order_by(MarketReferencePrice.id).all():
        parcalar.append("|".join(str(x) for x in (
            kayit.id, kayit.price_type, kayit.period, kayit.ptf_tl_per_mwh, kayit.yekdem_tl_per_mwh,
            kayit.status, kayit.source, kayit.source_note, kayit.change_reason, kayit.is_locked,
            kayit.updated_by, kayit.captured_at, kayit.updated_at)))
    for satir in db.query(PriceChangeHistory).order_by(PriceChangeHistory.id).all():
        parcalar.append("|".join(str(x) for x in (
            satir.id, satir.price_record_id, satir.price_type, satir.period, satir.action,
            satir.old_value, satir.new_value, satir.old_status, satir.new_status, satir.source)))
    ham = "\n".join(parcalar)
    return hashlib.sha256(ham.encode("utf-8")).hexdigest(), len(parcalar), ham


# ── SAF SINIFLANDIRMA ────────────────────────────────────────────────────────

def _ptf_kimlik(yontem="mcp_avg", donem="2026-05"):
    return {"donem": donem, "birim": BIRIM, "yontem": yontem,
            "versiyon": UYGULANAMAZ, "segment": UYGULANAMAZ}


def _yekdem_kimlik(versiyon="2026-05", segment="st", donem="2026-05"):
    return {"donem": donem, "birim": BIRIM, "yontem": UYGULANAMAZ,
            "versiyon": versiyon, "segment": segment}


def test_ptf_uygulanamaz_alanlar_karsilastirmayi_engellemez():
    s = classify_row("PTF", _ptf_kimlik(), _ptf_kimlik(), 590.90, 590.90)
    assert s["tip"] == TIP_AYNI
    assert s["alanlar"]["versiyon"] == UYGULANAMAZ and s["alanlar"]["segment"] == UYGULANAMAZ


def test_ptf_deger_farki_dogru_hesaplanir():
    s = classify_row("PTF", _ptf_kimlik(), _ptf_kimlik(), 590.90, 795.70)
    assert s["tip"] == TIP_DEGER_FARKI
    assert s["fark"] == pytest.approx(-204.80, abs=1e-6)


def test_ptf_yontem_bilinmiyorsa_fark_uretilmez():
    s = classify_row("PTF", _ptf_kimlik(yontem=None), _ptf_kimlik(), 590.90, 795.70)
    assert s["tip"] == TIP_YOK and s["fark"] is None
    assert "yontem_bilinmiyor" in s["nedenler"]


def test_ptf_yontem_uyusmazligi_fark_uretmez():
    s = classify_row("PTF", _ptf_kimlik(yontem="abone_agirlikli"), _ptf_kimlik(yontem="mcp_avg"), 590.90, 795.70)
    assert s["tip"] == TIP_YONTEM and s["fark"] is None


def test_yekdem_versiyon_ve_segment_uyusmazligi_ayri_gosterilir():
    v = classify_row("YEKDEM", _yekdem_kimlik(versiyon="2026-05"), _yekdem_kimlik(versiyon="2026-07"), 1306.10, 1306.10)
    assert v["tip"] == TIP_VERSIYON and v["fark"] is None
    sg = classify_row("YEKDEM", _yekdem_kimlik(segment="st"), _yekdem_kimlik(segment="gts"), 1306.10, 900.0)
    assert sg["tip"] == TIP_SEGMENT and sg["fark"] is None


def test_yekdem_eksik_kimlik_tahmin_edilmez():
    for eksik in ({"versiyon": None}, {"segment": None}):
        kimlik = _yekdem_kimlik(**eksik)
        s = classify_row("YEKDEM", kimlik, _yekdem_kimlik(), 1306.10, 1306.103)
        assert s["tip"] == TIP_YOK and s["fark"] is None
        assert any(n.endswith("_bilinmiyor") for n in s["nedenler"])


def test_donem_uyusmazligi_ayri_tip():
    s = classify_row("PTF", _ptf_kimlik(donem="2026-05"), _ptf_kimlik(donem="2026-06"), 1.0, 2.0)
    assert s["tip"] == TIP_DONEM and s["fark"] is None


def test_kimlik_ayristirma_jetonsuz_kaynakta_bilinmiyor():
    class SahteKayit:
        period = "2026-06"
        source = "manual_override"

    k = ptf_kimligi(SahteKayit())
    assert k["yontem"] is None and k["versiyon"] == UYGULANAMAZ

    class SahteGecmis:
        source = "epias_api.uc.st.2026-05"

    y = yekdem_kimligi(SahteKayit(), SahteGecmis())
    assert y["segment"] == "st" and y["versiyon"] == "2026-05" and y["yontem"] == UYGULANAMAZ
    assert yekdem_kimligi(SahteKayit(), None)["versiyon"] is None


# ── RAPOR ÜRETİMİ (DB okur, ağ yok) ──────────────────────────────────────────

def _sahte_veri():
    return SahteIstemci(
        ptf={"2026-05": PtfOzet("2026-05", 590.90, 795.70),
             "2026-06": PtfOzet("2026-06", 1240.16, 1341.78)},
        yekdem=[YekdemSatiri("2026-05", "2026-05", 1306.103, 1306.103),
                YekdemSatiri("2026-05", "2026-07", 1317.236, 1317.236),
                YekdemSatiri("2026-06", "2026-06", 1083.629, 1083.629)],
    )


def _rapor(db, istemci=None, **kw):
    from app.epias_compare import build_comparison

    varsayilan = dict(evaluated_at=date(2026, 6, 30), bugun=date(2026, 6, 30))
    varsayilan.update(kw)
    return build_comparison(db, "2026-05", "2026-06", istemci or _sahte_veri(), **varsayilan)


def _satir(rapor, donem, kalem):
    return [s for s in rapor["satirlar"] if s["donem"] == donem and s["kalem"] == kalem][0]


def test_rapor_kimlikli_kayitta_ayni_kimliksizde_karsilastirilamaz(db_ortami):
    r = _rapor(db_ortami["db"])
    assert _satir(r, "2026-05", "PTF")["tip"] == TIP_AYNI
    assert _satir(r, "2026-05", "YEKDEM")["tip"] == TIP_AYNI
    ptf_06 = _satir(r, "2026-06", "PTF")
    assert ptf_06["tip"] == TIP_YOK and "yontem_bilinmiyor" in ptf_06["nedenler"]
    yekdem_06 = _satir(r, "2026-06", "YEKDEM")
    assert yekdem_06["tip"] == TIP_YOK and yekdem_06["fark"] is None
    assert all(s["epias"] is None or s["epias"]["kesinlik"] == "BELIRSIZ" for s in r["satirlar"])


def test_rapor_deger_farki(db_ortami):
    from app.database import MarketReferencePrice

    db = db_ortami["db"]
    kayit = db.query(MarketReferencePrice).filter_by(period="2026-05").one()
    kayit.ptf_tl_per_mwh = 570.00
    db.commit()
    satir = _satir(_rapor(db), "2026-05", "PTF")
    assert satir["tip"] == TIP_DEGER_FARKI
    assert satir["fark"] == pytest.approx(-20.90, abs=1e-6)


def test_rapor_api_hatasinda_satirlar_karsilastirilamaz(db_ortami):
    istemci = SahteIstemci(ptf_hata="PTF servisi 500", yekdem_hata="YEKDEM servisi 500")
    r = _rapor(db_ortami["db"], istemci)
    assert all(s["tip"] == TIP_YOK for s in r["satirlar"])
    assert any("api_hatasi" in s["nedenler"] for s in r["satirlar"])
    assert {u["kod"] for u in r["uyarilar"]} >= {"ptf_api_hatasi", "yekdem_api_hatasi"}


def test_gecmis_degerlendirme_tarihi_acikca_bildirilir(db_ortami):
    r = _rapor(db_ortami["db"], evaluated_at=date(2026, 6, 30), bugun=date(2026, 9, 12))
    assert r["gecmis_tarih_kaniti"] == "yok"
    assert any(u["kod"] == "gecmis_tarih_kaniti_yok" for u in r["uyarilar"])
    assert "gecmis_tarih_kaniti_yok" in _satir(r, "2026-05", "YEKDEM")["nedenler"]


def test_rapor_hicbir_db_yazimi_yapmaz(db_ortami):
    db, ifadeler = db_ortami["db"], db_ortami["ifadeler"]
    once_ozet, once_satir, once_ham = icerik_ozeti(db)
    ifadeler.clear()
    _rapor(db)
    yazmalar = [i for i in ifadeler if i.strip().upper().startswith(YAZMA_ANAHTARLARI)]
    sonra_ozet, sonra_satir, sonra_ham = icerik_ozeti(db)
    assert yazmalar == []
    assert (once_ozet, once_satir) == (sonra_ozet, sonra_satir)
    assert once_ham == sonra_ham


def test_maskeleme_sirri_sizdirmaz(db_ortami):
    sir = "S3cretP@ss"
    with patch.dict(os.environ, {"EPIAS_PASSWORD": sir, "EPIAS_USERNAME": "kullanici@example.com"}):
        assert sir not in _maskele("hata: parola " + sir + " TGT-12345 ile")
        assert "TGT-12345" not in _maskele("TGT-12345 reddedildi")
        istemci = SahteIstemci(ptf_hata="baglanti hatasi: " + sir, yekdem_hata="baglanti hatasi: " + sir)
        r = _rapor(db_ortami["db"], istemci)
    import json
    assert sir not in json.dumps(r, ensure_ascii=False, default=str)


# ── UÇ (endpoint) ────────────────────────────────────────────────────────────

@pytest.fixture()
def uc_istemcisi(db_ortami):
    from app.database import get_db
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[get_db] = lambda: db_ortami["db"]
    with patch("app.epias_public_client.EpiasReadOnlyClient", lambda *a, **k: _sahte_veri()):
        yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def _sorgu(istemci, basliklar=None):
    return istemci.get("/admin/market-prices/epias-compare",
                       params={"from_period": "2026-05", "to_period": "2026-06",
                               "evaluated_at": "2026-06-30"},
                       headers=basliklar or {})


def test_uc_ozellik_varsayilan_kapali(uc_istemcisi):
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("EPIAS_COMPARE_ENABLED", None)
        yanit = _sorgu(uc_istemcisi)
    assert yanit.status_code == 503
    assert yanit.json()["detail"]["error"] == "feature_disabled"


def test_uc_yetkisiz_erisim(uc_istemcisi):
    import app.main as m

    with patch.dict(os.environ, {"EPIAS_COMPARE_ENABLED": "true"}), \
         patch.object(m, "ADMIN_API_KEY_ENABLED", True), patch.object(m, "ADMIN_API_KEY", "dogru-anahtar"):
        assert _sorgu(uc_istemcisi).status_code == 401
        assert _sorgu(uc_istemcisi, {"X-Admin-Key": "yanlis"}).status_code == 403
        assert _sorgu(uc_istemcisi, {"X-Admin-Key": "dogru-anahtar"}).status_code == 200


def test_uc_basarili_yanit_ve_db_yazimi_yok(uc_istemcisi, db_ortami):
    once_ozet, once_satir, once_ham = icerik_ozeti(db_ortami["db"])
    db_ortami["ifadeler"].clear()
    with patch.dict(os.environ, {"EPIAS_COMPARE_ENABLED": "true"}):
        yanit = _sorgu(uc_istemcisi)
    assert yanit.status_code == 200
    govde = yanit.json()
    assert govde["kayit"] == "EPIAS-KARSILASTIRMA" and len(govde["satirlar"]) == 4
    yazmalar = [i for i in db_ortami["ifadeler"] if i.strip().upper().startswith(YAZMA_ANAHTARLARI)]
    sonra_ozet, sonra_satir, sonra_ham = icerik_ozeti(db_ortami["db"])
    assert yazmalar == []
    assert (once_ozet, once_satir, once_ham) == (sonra_ozet, sonra_satir, sonra_ham)


def test_uc_gecersiz_aralik_ve_tarih(uc_istemcisi):
    with patch.dict(os.environ, {"EPIAS_COMPARE_ENABLED": "true"}):
        kotu_tarih = uc_istemcisi.get("/admin/market-prices/epias-compare",
                                      params={"from_period": "2026-05", "to_period": "2026-06",
                                              "evaluated_at": "30.06.2026"})
        assert kotu_tarih.status_code == 422
        genis = uc_istemcisi.get("/admin/market-prices/epias-compare",
                                 params={"from_period": "2024-01", "to_period": "2026-06"})
        assert genis.status_code == 422
