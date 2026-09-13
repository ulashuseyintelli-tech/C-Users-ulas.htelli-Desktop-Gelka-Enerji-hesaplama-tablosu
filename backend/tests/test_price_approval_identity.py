"""
Fiyat kimliği ve yetkili onay akışı — hedefli kabul (OWNER-KARARI-01/02, 2026-09-13).

Kapsam (GO "FİYAT KİMLİĞİ VE YETKİLİ ONAY AKIŞINI UYGULA", madde 3):
- aritmetik yöntemin onay öncesi/sonrası korunması (saatlik veri varken bile)
- iki segmentin farklı değerleri (segment başına onay; eşitlik otomatik seçim değil)
- eksik YEKDEM ile açıkça işaretlenmiş taslak teklif + taslak PDF
- açık kullanıcı işlemi + yeniden doğrulama ile kesinleşme (otomatik değil)
- aday değişimi → yeniden onay (hiçbir şey yazılmaz)
- eşzamanlı onay → yalnız biri başarılı
- geçmiş yazımı hatasında tam geri alma
- yetkisiz onayın reddi (admin bayrağı kapalıyken de)
- eski snapshot'ların değişmeden okunması; yeni tekliflerin kimlik kapısını aşamaması
- belge yöntem etiketinin gerçek hesapla eşleşmesi

AĞ YOK: EPİAŞ istemcisi sahte sınıfla değiştirilir; gerçek istemcinin HTTP katmanına
her erişim testi patlatır (autouse). Veritabanı sentetik SQLite (bellek içi ya da geçici
dosya). Başarılı POST /offers TestClient altında asılı kalabildiği için (bkz.
test_pricing_phase1_price_accuracy.py) kalıcı yol uç fonksiyonları doğrudan çağrılır.

Çağrıldığı yerler:
- pytest (hedefli kabul)
"""
from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

DONEM = "2026-07"
ARITMETIK = 2699.61
AGIRLIKLI = 2753.01
ST = 486.314
GTS = 470.125
# Saatlik PTF'ler aritmetikten FARKLI bir profil ağırlıklı değer üretir (10→T1, 19→T2, 2→T3).
SAATLIK = [(10, 1000.0), (19, 3000.0), (2, 500.0)]
ONAY_ANAHTARI = "sentetik-onay-anahtari"
YETKI = "paylasilan_yonetici_anahtari"  # sunucunun doğruladığı yetki türü (kişi değil)
# Resmî yanıttaki TAM versiyon metinleri (aynı ay içinde iki farklı yayım — sentetik).
VERSIYON_1 = "2026-07-01T00:00:00+03:00"
VERSIYON_2 = "2026-07-20T00:00:00+03:00"

from app.epias_public_client import EpiasReadOnlyClient as _GERCEK_ISTEMCI  # noqa: E402  (autouse sahteden önce)


def _saatlik_kalemler(period, ortalama, *, sapma=100.0):
    """Dönemin her saati için bir kalem (+03:00); ± sapma dönüşümlü → aritmetik ortalama = ortalama."""
    from app.price_approval import _ay_saatleri

    return tuple((t.isoformat(), round(ortalama + (sapma if i % 2 == 0 else -sapma), 6))
                 for i, t in enumerate(_ay_saatleri(period)))

HESAP_SONUCU = {
    "current_energy_tl": 2000.0, "current_distribution_tl": 300.0, "current_demand_tl": 0.0,
    "current_btv_tl": 100.0, "current_vat_matrah_tl": 2400.0, "current_vat_tl": 480.0,
    "current_total_with_vat_tl": 2880.0, "offer_ptf_tl": 1800.0, "offer_yekdem_tl": 50.0,
    "offer_energy_tl": 1850.0, "offer_distribution_tl": 300.0, "offer_demand_tl": 0.0,
    "offer_btv_tl": 90.0, "offer_vat_matrah_tl": 2240.0, "offer_vat_tl": 448.0,
    "offer_total_with_vat_tl": 2688.0, "difference_excl_vat_tl": 160.0,
    "difference_incl_vat_tl": 192.0, "savings_ratio": 0.0667, "unit_price_savings_ratio": 0.075,
}


# ═══════════════════════════════════════════════════════════════════════════
# Sahte EPİAŞ istemcisi + ağ yasağı
# ═══════════════════════════════════════════════════════════════════════════

class SahteEpias:
    """EpiasReadOnlyClient arayüzü; değerler test içinden değiştirilebilir."""
    ptf = ARITMETIK
    agirlikli = AGIRLIKLI
    st = ST
    gts = GTS
    versiyon = VERSIYON_1
    # None → dönemin tam ve tutarlı saatlik kalemleri (ptf'den) üretilir; test açıkça verebilir.
    saatlik = None
    sayfa = None
    # None → tek satır (versiyon, st, gts); test aynı ay için birden çok satır verebilir.
    yekdem_satirlari = None
    hata = None
    cagri = 0

    def __init__(self, *_a, **_k):
        pass

    def fetch_mcp(self, donem):
        from app.epias_public_client import PtfOzet

        type(self).cagri += 1
        if type(self).hata is not None:
            raise type(self).hata
        saatlik = type(self).saatlik
        if saatlik is None:
            saatlik = _saatlik_kalemler(donem, type(self).ptf) if type(self).ptf is not None else ()
        return PtfOzet(donem=donem, aritmetik=type(self).ptf, agirlikli=type(self).agirlikli,
                       saatlik=tuple(saatlik), sayfa=type(self).sayfa)

    def fetch_unit_cost(self, bas, bit):
        from app.epias_public_client import YekdemSatiri

        type(self).cagri += 1
        if type(self).hata is not None:
            raise type(self).hata
        satirlar = type(self).yekdem_satirlari or [(type(self).versiyon, type(self).st, type(self).gts)]
        return [YekdemSatiri(donem=bas, versiyon=(v or "")[:7] or None, serbest_tuketici=st, gts_k1=gts,
                             versiyon_tam=v) for v, st, gts in satirlar]

    def kapat(self):
        pass


@pytest.fixture(autouse=True)
def _ag_yasak_ve_sahte_istemci(monkeypatch):
    import app.epias_public_client as epc

    def _patla(*_a, **_k):
        raise AssertionError("gerçek EPİAŞ HTTP katmanına erişildi")

    monkeypatch.setattr(epc.EpiasReadOnlyClient, "_client", _patla)
    monkeypatch.setattr("app.market_prices.fetch_and_cache_from_epias", _patla)
    monkeypatch.setattr("app.epias_client.fetch_market_prices_from_epias", _patla)
    SahteEpias.ptf, SahteEpias.agirlikli = ARITMETIK, AGIRLIKLI
    SahteEpias.st, SahteEpias.gts, SahteEpias.versiyon = ST, GTS, VERSIYON_1
    SahteEpias.saatlik = SahteEpias.sayfa = SahteEpias.yekdem_satirlari = SahteEpias.hata = None
    SahteEpias.cagri = 0
    monkeypatch.setattr(epc, "EpiasReadOnlyClient", SahteEpias)
    monkeypatch.setenv("EPIAS_COMPARE_ENABLED", "true")


# ═══════════════════════════════════════════════════════════════════════════
# Fikstürler ve yardımcılar
# ═══════════════════════════════════════════════════════════════════════════

def _motor(url="sqlite:///:memory:"):
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    from app.price_approval import onay_metadata
    import app.pricing.schemas  # noqa: F401

    kw = {"connect_args": {"check_same_thread": False}}
    if url.endswith(":memory:"):
        kw["poolclass"] = StaticPool
    motor = sa.create_engine(url, **kw)
    Base.metadata.create_all(motor)
    onay_metadata.create_all(motor)
    return motor


@pytest.fixture()
def db():
    from sqlalchemy.orm import sessionmaker

    s = sessionmaker(bind=_motor())()
    yield s
    s.close()


@pytest.fixture()
def storage_tmp(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.storage import clear_storage_cache

    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    clear_storage_cache()
    yield tmp_path
    clear_storage_cache()


@pytest.fixture()
def client(db, storage_tmp):
    from app.main import app as fastapi_app
    from app.database import get_db

    fastapi_app.dependency_overrides[get_db] = lambda: db
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def _referans(db, ptf=ARITMETIK, yekdem=ST, source="manual_override", status="provisional", period=DONEM):
    from app.database import MarketReferencePrice

    kayit = MarketReferencePrice(period=period, price_type="PTF", ptf_tl_per_mwh=ptf,
                                 yekdem_tl_per_mwh=yekdem, source=source, is_locked=0, status=status)
    db.add(kayit)
    db.commit()
    return kayit


def _saatlik(db, period=DONEM):
    from app.pricing.schemas import HourlyMarketPrice

    for saat, ptf in SAATLIK:
        db.add(HourlyMarketPrice(period=period, date=period + "-01", hour=saat, ptf_tl_per_mwh=ptf,
                                 smf_tl_per_mwh=ptf, source="epias_excel", is_active=1))
    db.commit()


def _ptf_onayla(db, client_sinifi=SahteEpias, **ek):
    from app.price_approval import ptf_adayi_hazirla, ptf_onayla

    aday = ptf_adayi_hazirla(db, client_sinifi(), DONEM)
    return ptf_onayla(db, client_sinifi(), period=DONEM, aday_parmak_izi=aday["aday_parmak_izi"],
                      beklenen_revision=aday["beklenen_revision"],
                      beklenen_kayit_parmak_izi=aday["beklenen_kayit_parmak_izi"],
                      onaylayan_beyan="Yetkili Test", dogrulanan_yetki=YETKI, change_reason="sentetik onay", **ek)


def _yekdem_onayla(db, segment):
    from app.price_approval import yekdem_adayi_hazirla, yekdem_onayla

    aday = yekdem_adayi_hazirla(db, SahteEpias(), DONEM, segment)
    return yekdem_onayla(db, SahteEpias(), period=DONEM, segment=segment,
                         aday_parmak_izi=aday["aday_parmak_izi"], beklenen_revision=aday["beklenen_revision"],
                         onaylayan_beyan="Yetkili Test", dogrulanan_yetki=YETKI, change_reason="sentetik onay")


def _ozet(db) -> str:
    """Dört fiyat/onay tablosunun TAM içerik özeti (satır sayısı yetmez)."""
    icerik = {}
    for tablo in ("market_reference_prices", "price_change_history", "ptf_onay_revizyonlari",
                  "yekdem_onay_revizyonlari"):
        satirlar = db.execute(sa.text(f"SELECT * FROM {tablo} ORDER BY id")).fetchall()
        icerik[tablo] = [[str(v) for v in s] for s in satirlar]
    return hashlib.sha256(json.dumps(icerik, sort_keys=True).encode()).hexdigest()


def _sayim(db, tablo):
    return db.execute(sa.text(f"SELECT COUNT(*) FROM {tablo}")).scalar()


def _teklif(db, *, ptf=ARITMETIK, yekdem=ST, mod="included", segment=None, taslak=False, calc_ek=None):
    from app.main import create_offer
    from app.models import CalculationResult, FieldValue, InvoiceExtraction, OfferParams

    ext = InvoiceExtraction(
        vendor="Sentetik", invoice_period=DONEM,
        consumption_kwh=FieldValue(value=1000.0, confidence=1.0),
        current_active_unit_price_tl_per_kwh=FieldValue(value=2.0, confidence=1.0),
    )
    calc = CalculationResult(**{**HESAP_SONUCU, **(calc_ek or {})})
    params = OfferParams(weighted_ptf_tl_per_mwh=ptf, yekdem_tl_per_mwh=yekdem, agreement_multiplier=1.01)
    r = asyncio.run(create_offer(
        extraction=ext, calculation=calc, params=params, customer_id=None,
        invoice_total_raw="2880", operator_confirmed_warnings=False,
        yekdem_mode=mod, yekdem_segment=segment, fiyat_taslak=taslak, db=db))
    if isinstance(r, dict):
        return 200, r
    return r.status_code, json.loads(r.body)


def _pdf_metni(pdf_bytes: bytes) -> str:
    import io
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join((sayfa.extract_text() or "") for sayfa in pdf.pages)


# ═══════════════════════════════════════════════════════════════════════════
# 1) Aritmetik yöntem onay öncesi/sonrası korunur; sonraki değişiklik onayı düşürür
# ═══════════════════════════════════════════════════════════════════════════

class TestAritmetikYontemKorunur:
    def test_manual_override_aritmetik_onaydan_sonra_saatlige_kaymaz(self, db, client):
        from app.calculator import get_ptf_yekdem_for_period
        from app.models import OfferParams
        from app.price_provenance import effective_ptf_candidates

        _saatlik(db)
        _referans(db, source="manual_override", status="provisional")
        once = effective_ptf_candidates(db, DONEM)
        assert [(c.source, c.value) for c in once] == [("manual_override", ARITMETIK)]

        sonuc = _ptf_onayla(db)
        assert sonuc["revision"] == 1 and sonuc["basis"] == "mcp_avg"

        sonra = effective_ptf_candidates(db, DONEM)
        assert [(c.source, c.value) for c in sonra] == [("monthly_arithmetic:mcp_avg", ARITMETIK)]
        assert sonra[0].approval["revision"] == 1
        ptf, _y, kaynak, hata, _u = get_ptf_yekdem_for_period(
            db, DONEM, OfferParams(use_reference_prices=True), tariff_group="Sanayi")
        assert (ptf, kaynak, hata) == (ARITMETIK, "monthly_arithmetic:mcp_avg", None)
        b = client.get(f"/api/epias/prices/{DONEM}?profile=puant_agir").json()
        assert b["weighted_ptf_tl_per_mwh"] == ARITMETIK
        assert b["weighted_ptf_source"] == "monthly_arithmetic:mcp_avg"
        assert b["ptf_yontem_etiketi"] == "Aylık aritmetik PTF (EPİAŞ)"

    def test_epias_manual_kayit_onaydan_once_saatlik_sonra_onayli_aritmetik(self, db):
        from app.price_provenance import effective_ptf_candidates

        _saatlik(db)
        _referans(db, ptf=ARITMETIK, source="epias_manual", status="final")
        assert all(c.source.startswith("hourly_weighted:") for c in effective_ptf_candidates(db, DONEM))
        _ptf_onayla(db)
        assert [c.source for c in effective_ptf_candidates(db, DONEM)] == ["monthly_arithmetic:mcp_avg"]

    def test_onay_degeri_ve_yontemi_yalniz_acik_adaydir_baska_alan_degismez(self, db):
        from app.database import MarketReferencePrice

        _referans(db, ptf=ARITMETIK, yekdem=123.0, source="manual_override", status="provisional")
        _ptf_onayla(db)
        db.expire_all()
        k = db.query(MarketReferencePrice).filter_by(period=DONEM).one()
        assert (k.ptf_tl_per_mwh, k.yekdem_tl_per_mwh, k.status, k.source) == (ARITMETIK, 123.0, "final", "epias_api")
        assert k.updated_by == "Yetkili Test"

    def test_sonraki_fiyat_degisikligi_eski_onayi_gecersiz_kilar(self, db):
        from app.price_approval import gecerli_ptf_onayi
        from app.price_provenance import effective_ptf_candidates, build_price_provenance

        _saatlik(db)
        _referans(db)
        _ptf_onayla(db)
        assert gecerli_ptf_onayi(db, DONEM) is not None
        # Onay DIŞI bir yoldan (ör. hızlı kayıt) değer/kaynak değişir.
        db.execute(sa.text("UPDATE market_reference_prices SET ptf_tl_per_mwh = 2700.0, "
                           "source = 'manual_override' WHERE period = :p"), {"p": DONEM})
        db.commit()
        assert gecerli_ptf_onayi(db, DONEM) is None
        assert effective_ptf_candidates(db, DONEM)[0].source == "manual_override"
        prov = build_price_provenance(db, period=DONEM, ptf=2700.0, yekdem=None, yekdem_mode="excluded")
        assert prov["verified"] is False
        assert "ptf_provisional" in prov["blocking_reasons"] or "ptf_identity_missing" in prov["blocking_reasons"]
        # Yalnız durum değişikliği de (final→provisional) onayı düşürür.
        _ptf_onayla(db)
        assert gecerli_ptf_onayi(db, DONEM).revision == 2
        db.execute(sa.text("UPDATE market_reference_prices SET status='provisional' WHERE period=:p"), {"p": DONEM})
        db.commit()
        assert gecerli_ptf_onayi(db, DONEM) is None


# ═══════════════════════════════════════════════════════════════════════════
# 2) İki segmentin farklı değerleri
# ═══════════════════════════════════════════════════════════════════════════

class TestIkiSegment:
    def test_segment_basina_onay_ve_teklif_kapisi(self, db):
        from app.price_provenance import build_price_provenance

        _referans(db)
        _ptf_onayla(db)
        _yekdem_onayla(db, "st")

        def prov(yek, seg):
            return build_price_provenance(db, period=DONEM, ptf=ARITMETIK, yekdem=yek,
                                          yekdem_mode="included", yekdem_segment=seg)

        p_st = prov(ST, "st")
        assert p_st["verified"] is True
        assert p_st["yekdem"]["segment"] == "st" and p_st["yekdem"]["segment_basis"] == "user"
        assert p_st["yekdem"]["approval"]["revision"] == 1 and p_st["yekdem"]["approval"]["version"] == VERSIYON_1
        assert p_st["ptf"]["approval"]["basis"] == "mcp_avg"
        # GTŞ-K1 henüz onaylı değil → taslak; hariç'e ÇEVRİLMEZ.
        p_gts = prov(GTS, "gts")
        assert p_gts["verified"] is False and p_gts["blocking_reasons"] == ["yekdem_approval_missing"]
        assert p_gts["yekdem"]["mode"] == "included"
        _yekdem_onayla(db, "gts")
        assert prov(GTS, "gts")["verified"] is True
        # Segment ile değer karışırsa doğrulanmaz.
        assert prov(GTS, "st")["blocking_reasons"] == ["yekdem_unverified"]
        assert prov(ST, "gts")["blocking_reasons"] == ["yekdem_unverified"]

    def test_esit_degerlerde_bile_segment_otomatik_secilmez(self, db):
        from app.price_provenance import build_price_provenance

        SahteEpias.gts = ST  # iki segment eşit
        _referans(db)
        _ptf_onayla(db)
        _yekdem_onayla(db, "st")
        _yekdem_onayla(db, "gts")
        p = build_price_provenance(db, period=DONEM, ptf=ARITMETIK, yekdem=ST, yekdem_mode="included")
        assert p["verified"] is False and p["blocking_reasons"] == ["yekdem_segment_missing"]
        assert p["yekdem"]["segment"] is None
        assert build_price_provenance(db, period=DONEM, ptf=ARITMETIK, yekdem=ST, yekdem_mode="included",
                                      yekdem_segment="xx")["blocking_reasons"] == ["yekdem_segment_invalid"]

    def test_yekdem_onayi_segmentsiz_reddedilir(self, db):
        from app.price_approval import OnayHatasi, yekdem_onayla

        with pytest.raises(OnayHatasi) as e:
            yekdem_onayla(db, SahteEpias(), period=DONEM, segment=None, aday_parmak_izi="x",
                          beklenen_revision=0, onaylayan_beyan="Y", dogrulanan_yetki=YETKI, change_reason="g")
        assert e.value.kod == "segment_zorunlu" and e.value.http == 422
        assert _sayim(db, "yekdem_onay_revizyonlari") == 0


# ═══════════════════════════════════════════════════════════════════════════
# 3) Eksik YEKDEM → açıkça işaretlenmiş taslak teklif + taslak PDF
# 4) Açık işlemle kesinleşme (otomatik değil, yeniden doğrulamalı)
# ═══════════════════════════════════════════════════════════════════════════

class TestTaslakVeKesinlesme:
    def test_eksik_yekdem_taslak_teklif_ve_taslak_pdf(self, db, storage_tmp):
        from app.database import Offer
        from app.main import generate_draft_pdf_for_offer, generate_pdf_for_offer
        from fastapi import HTTPException

        _referans(db)
        _ptf_onayla(db)
        # Açık taslak isteği yoksa eski davranış: 422, kayıt yok.
        kod, govde = _teklif(db, segment="st")
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["yekdem_approval_missing"]
        assert db.query(Offer).count() == 0

        kod, govde = _teklif(db, segment="st", taslak=True)
        assert kod == 200 and govde["fiyat_durumu"] == "taslak"
        assert govde["blocking_reasons"] == ["yekdem_approval_missing"]
        teklif = db.query(Offer).get(govde["id"])
        prov = teklif.calculation_result["meta_price_provenance"]
        assert prov["version"] == 3 and prov["verified"] is False and prov["draft_only"] is True
        assert prov["yekdem"]["mode"] == "included", "eksik YEKDEM hariç'e çevrilmedi"

        with pytest.raises(HTTPException) as e:
            asyncio.run(generate_pdf_for_offer(teklif.id, db=db, _=None))
        assert e.value.status_code == 409
        yanit = asyncio.run(generate_draft_pdf_for_offer(teklif.id, db=db, _=None))
        assert yanit.headers["X-Fiyat-Durumu"] == "taslak"
        assert yanit.body.startswith(b"%PDF")
        assert "TASLAK" in _pdf_metni(yanit.body)
        db.refresh(teklif)
        assert teklif.pdf_ref is None, "taslak PDF saklanmaz / pdf_ref yazılmaz"

    def test_acik_islemle_kesinlesme_otomatik_degil_ve_degerler_degismez(self, db, storage_tmp):
        from app.database import Offer
        from fastapi import HTTPException
        from app.main import finalize_offer_price, generate_draft_pdf_for_offer, generate_pdf_for_offer

        _referans(db)
        _ptf_onayla(db)
        _, govde = _teklif(db, segment="gts", yekdem=GTS, taslak=True)
        teklif = db.query(Offer).get(govde["id"])
        oncesi = json.dumps(teklif.calculation_result, sort_keys=True)

        # Onay yokken kesinleştirme reddedilir, teklif DEĞİŞMEZ.
        r = finalize_offer_price(teklif.id, yekdem_segment=None, kesinlestiren="Kullanıcı", db=db, _=None)
        assert r.status_code == 422
        db.refresh(teklif)
        assert json.dumps(teklif.calculation_result, sort_keys=True) == oncesi

        _yekdem_onayla(db, "gts")
        db.refresh(teklif)
        assert teklif.calculation_result["meta_fiyat_durumu"] == "taslak", "onay teklifi kendiliğinden kesinleştirmez"

        sonuc = finalize_offer_price(teklif.id, yekdem_segment=None, kesinlestiren="Kullanıcı", db=db, _=None)
        assert sonuc["fiyat_durumu"] == "kesin"
        db.refresh(teklif)
        cr = teklif.calculation_result
        assert cr["meta_price_provenance"]["verified"] is True and cr["meta_price_provenance"]["version"] == 3
        assert cr["meta_price_provenance"]["yekdem"]["approval"]["segment"] == "gts"
        assert cr["meta_price_provenance_gecmisi"][0]["provenance"]["verified"] is False
        assert (teklif.weighted_ptf, teklif.yekdem, teklif.offer_total) == (ARITMETIK, GTS, 2688.0)
        # Kesinleşmiş teklifin normal PDF'i üretilir.
        pdf = asyncio.run(generate_pdf_for_offer(teklif.id, db=db, _=None))
        assert pdf["regenerated"] is True
        with pytest.raises(HTTPException) as e:
            asyncio.run(generate_draft_pdf_for_offer(teklif.id, db=db, _=None))
        assert e.value.detail["error"] == "zaten_kesin"

    def test_kesinlestirmede_segment_celiskisi_reddedilir(self, db):
        from app.database import Offer
        from app.main import finalize_offer_price

        _referans(db)
        _ptf_onayla(db)
        _, govde = _teklif(db, segment="st", taslak=True)
        _yekdem_onayla(db, "gts")
        r = finalize_offer_price(govde["id"], yekdem_segment="gts", kesinlestiren="K", db=db, _=None)
        assert r.status_code == 422 and json.loads(r.body)["error"]["code"] == "segment_celiskisi"
        assert db.query(Offer).get(govde["id"]).calculation_result["meta_fiyat_durumu"] == "taslak"


# ═══════════════════════════════════════════════════════════════════════════
# 5) Aday değişimi   6) Eşzamanlı onay   7) Geçmiş yazımı hatasında tam rollback
# ═══════════════════════════════════════════════════════════════════════════

class TestOnayButunlugu:
    def test_aday_degisirse_hicbir_sey_yazilmaz_yeni_aday_doner(self, db):
        from app.price_approval import OnayHatasi, ptf_adayi_hazirla, ptf_onayla, yekdem_adayi_hazirla, yekdem_onayla

        _referans(db)
        aday = ptf_adayi_hazirla(db, SahteEpias(), DONEM)
        once = _ozet(db)
        SahteEpias.ptf = 2701.00  # resmî değer ekranda gösterildikten sonra değişti
        with pytest.raises(OnayHatasi) as e:
            ptf_onayla(db, SahteEpias(), period=DONEM, aday_parmak_izi=aday["aday_parmak_izi"],
                       beklenen_revision=aday["beklenen_revision"],
                       beklenen_kayit_parmak_izi=aday["beklenen_kayit_parmak_izi"],
                       onaylayan_beyan="Y", dogrulanan_yetki=YETKI, change_reason="g")
        assert e.value.kod == "aday_degisti" and e.value.http == 409
        assert e.value.ek["yeni_aday"]["value"] == 2701.00
        assert _ozet(db) == once

        y_aday = yekdem_adayi_hazirla(db, SahteEpias(), DONEM, "st")
        SahteEpias.versiyon = VERSIYON_2  # AYNI ay içinde yeni tam versiyon yayımlandı (değer aynı)
        with pytest.raises(OnayHatasi) as e2:
            yekdem_onayla(db, SahteEpias(), period=DONEM, segment="st", aday_parmak_izi=y_aday["aday_parmak_izi"],
                          beklenen_revision=0, onaylayan_beyan="Y", dogrulanan_yetki=YETKI, change_reason="g")
        assert e2.value.kod == "aday_degisti"
        assert _ozet(db) == once

    def test_esz_amanli_iki_onaydan_yalniz_biri_basarili(self, tmp_path):
        from sqlalchemy.orm import sessionmaker
        from app.price_approval import OnayHatasi, ptf_adayi_hazirla, ptf_onayla

        motor = _motor(f"sqlite:///{(tmp_path / 'yaris.db').as_posix()}")
        Oturum = sessionmaker(bind=motor)
        a, b = Oturum(), Oturum()
        _referans(a)
        aday_a = ptf_adayi_hazirla(a, SahteEpias(), DONEM)
        aday_b = ptf_adayi_hazirla(b, SahteEpias(), DONEM)

        def b_once_tamamlar():
            ptf_onayla(b, SahteEpias(), period=DONEM, aday_parmak_izi=aday_b["aday_parmak_izi"],
                       beklenen_revision=aday_b["beklenen_revision"],
                       beklenen_kayit_parmak_izi=aday_b["beklenen_kayit_parmak_izi"],
                       onaylayan_beyan="B", dogrulanan_yetki=YETKI, change_reason="b")

        with pytest.raises(OnayHatasi) as e:
            ptf_onayla(a, SahteEpias(), period=DONEM, aday_parmak_izi=aday_a["aday_parmak_izi"],
                       beklenen_revision=aday_a["beklenen_revision"],
                       beklenen_kayit_parmak_izi=aday_a["beklenen_kayit_parmak_izi"],
                       onaylayan_beyan="A", dogrulanan_yetki=YETKI, change_reason="a", yaris_kancasi=b_once_tamamlar)
        assert e.value.http == 409
        c = Oturum()
        assert _sayim(c, "ptf_onay_revizyonlari") == 1
        assert c.execute(sa.text("SELECT onaylayan_beyan FROM ptf_onay_revizyonlari")).scalar() == "B"
        assert _sayim(c, "price_change_history") == 1

        # Aynı kayıt durumunda (yeniden onay) yarış: benzersizlik kısıtı ikinciyi reddeder.
        aday_a2 = ptf_adayi_hazirla(a, SahteEpias(), DONEM)
        aday_b2 = ptf_adayi_hazirla(b, SahteEpias(), DONEM)

        def b_yine_once():
            ptf_onayla(b, SahteEpias(), period=DONEM, aday_parmak_izi=aday_b2["aday_parmak_izi"],
                       beklenen_revision=aday_b2["beklenen_revision"],
                       beklenen_kayit_parmak_izi=aday_b2["beklenen_kayit_parmak_izi"],
                       onaylayan_beyan="B2", dogrulanan_yetki=YETKI, change_reason="b2")

        with pytest.raises(OnayHatasi) as e3:
            ptf_onayla(a, SahteEpias(), period=DONEM, aday_parmak_izi=aday_a2["aday_parmak_izi"],
                       beklenen_revision=aday_a2["beklenen_revision"],
                       beklenen_kayit_parmak_izi=aday_a2["beklenen_kayit_parmak_izi"],
                       onaylayan_beyan="A2", dogrulanan_yetki=YETKI, change_reason="a2", yaris_kancasi=b_yine_once)
        assert e3.value.kod == "onay_yarisi"
        c.close()
        c = Oturum()
        assert _sayim(c, "ptf_onay_revizyonlari") == 2
        assert _sayim(c, "price_change_history") == 2, "kaybedenin geçmiş satırı da geri alındı"
        for s in (a, b, c):
            s.close()

    def test_yekdem_esz_amanli_onay_benzersizlikle_tek(self, tmp_path):
        from sqlalchemy.orm import sessionmaker
        from app.price_approval import OnayHatasi, yekdem_adayi_hazirla, yekdem_onayla

        motor = _motor(f"sqlite:///{(tmp_path / 'yaris2.db').as_posix()}")
        Oturum = sessionmaker(bind=motor)
        a, b = Oturum(), Oturum()
        aa = yekdem_adayi_hazirla(a, SahteEpias(), DONEM, "st")
        bb = yekdem_adayi_hazirla(b, SahteEpias(), DONEM, "st")

        def b_once():
            yekdem_onayla(b, SahteEpias(), period=DONEM, segment="st", aday_parmak_izi=bb["aday_parmak_izi"],
                          beklenen_revision=bb["beklenen_revision"], onaylayan_beyan="B", dogrulanan_yetki=YETKI, change_reason="b")

        with pytest.raises(OnayHatasi) as e:
            yekdem_onayla(a, SahteEpias(), period=DONEM, segment="st", aday_parmak_izi=aa["aday_parmak_izi"],
                          beklenen_revision=aa["beklenen_revision"], onaylayan_beyan="A", dogrulanan_yetki=YETKI, change_reason="a",
                          yaris_kancasi=b_once)
        assert e.value.kod == "onay_yarisi"
        assert _sayim(Oturum(), "yekdem_onay_revizyonlari") == 1

    def test_gecmis_yazimi_hatasinda_tam_geri_alma(self, db, monkeypatch):
        import app.price_approval as pa

        _referans(db)
        once = _ozet(db)

        def _bozuk(*_a, **_k):
            raise RuntimeError("geçmiş yazımı başarısız (enjekte)")

        monkeypatch.setattr(pa, "_gecmis_satiri_ekle", _bozuk)
        with pytest.raises(RuntimeError):
            _ptf_onayla(db)
        db.expire_all()
        assert _ozet(db) == once, "kayıt, geçmiş ve onay tablosu değişmedi"
        assert pa.gecerli_ptf_onayi(db, DONEM) is None


# ═══════════════════════════════════════════════════════════════════════════
# 8) Yetkisiz onay reddi (admin bayrağı kapalı olsa da)
# ═══════════════════════════════════════════════════════════════════════════

class TestYetki:
    def _govde(self, client, db):
        from app.price_approval import ptf_adayi_hazirla

        aday = ptf_adayi_hazirla(db, SahteEpias(), DONEM)
        return {"period": DONEM, "kalem": "PTF", "aday_parmak_izi": aday["aday_parmak_izi"],
                "beklenen_revision": aday["beklenen_revision"],
                "beklenen_kayit_parmak_izi": aday["beklenen_kayit_parmak_izi"],
                "onaylayan_beyan": "Yetkili", "change_reason": "onay"}

    def test_bayrak_kapali_anahtar_tanimsiz_onay_reddedilir(self, client, db, monkeypatch):
        import app.main as m

        monkeypatch.setattr(m, "ADMIN_API_KEY_ENABLED", False)
        monkeypatch.setattr(m, "ADMIN_API_KEY", "")
        _referans(db)
        once = _ozet(db)
        r = client.post("/admin/market-prices/approve", json=self._govde(client, db))
        assert r.status_code == 403 and r.json()["detail"]["error"] == "approval_not_configured"
        assert _ozet(db) == once

    def test_anahtar_eksik_ya_da_yanlis_reddedilir_dogruysa_yazar(self, client, db, monkeypatch):
        import app.main as m

        monkeypatch.setattr(m, "ADMIN_API_KEY_ENABLED", False)  # mevcut admin bayrağı KAPALI
        monkeypatch.setattr(m, "ADMIN_API_KEY", ONAY_ANAHTARI)
        _referans(db)
        once = _ozet(db)
        govde = self._govde(client, db)
        r = client.post("/admin/market-prices/approve", json=govde)
        assert r.status_code == 401
        r = client.post("/admin/market-prices/approve", json=govde, headers={"X-Admin-Key": "yanlis"})
        assert r.status_code == 403 and r.json()["detail"]["error"] == "approval_forbidden"
        assert _ozet(db) == once
        r = client.post("/admin/market-prices/approve", json=govde, headers={"X-Admin-Key": ONAY_ANAHTARI})
        assert r.status_code == 200, r.text
        assert r.json()["revision"] == 1 and r.json()["yontem_etiketi"] == "Aylık aritmetik PTF (EPİAŞ)"

    def test_aday_ucu_salt_okunur_ve_yekdemde_segment_zorunlu(self, client, db):
        _referans(db)
        once = _ozet(db)
        r = client.get(f"/admin/market-prices/approval-candidate?period={DONEM}&kalem=PTF")
        assert r.status_code == 200 and r.json()["value"] == ARITMETIK and r.json()["basis"] == "mcp_avg"
        r = client.get(f"/admin/market-prices/approval-candidate?period={DONEM}&kalem=YEKDEM")
        assert r.status_code == 422
        r = client.get(f"/admin/market-prices/approval-candidate?period={DONEM}&kalem=YEKDEM&segment=gts")
        assert r.status_code == 200 and r.json()["value"] == GTS and r.json()["version"] == VERSIYON_1
        assert _ozet(db) == once

    def test_ozellik_kapaliyken_onay_ucu_epiasa_gitmez(self, client, db, monkeypatch):
        import app.main as m

        monkeypatch.setattr(m, "ADMIN_API_KEY", ONAY_ANAHTARI)
        monkeypatch.setenv("EPIAS_COMPARE_ENABLED", "false")
        _referans(db)
        r = client.post("/admin/market-prices/approve", headers={"X-Admin-Key": ONAY_ANAHTARI},
                        json={"period": DONEM, "kalem": "PTF", "aday_parmak_izi": "x", "beklenen_revision": 0,
                              "onaylayan_beyan": "Y", "change_reason": "g"})
        assert r.status_code == 503 and SahteEpias.cagri == 0


# ═══════════════════════════════════════════════════════════════════════════
# 9) Eski snapshot'lar değişmeden okunur   10) Yeni teklifler kimlik kapısını aşamaz
# ═══════════════════════════════════════════════════════════════════════════

def _eski_teklif(db, provenance):
    from app.database import Offer

    calc = dict(HESAP_SONUCU)
    if provenance is not None:
        calc["meta_price_provenance"] = provenance
    o = Offer(tenant_id="default", vendor="Sentetik", invoice_period=DONEM, consumption_kwh=1000.0,
              current_unit_price=2.0, weighted_ptf=2500.0, yekdem=50.0, agreement_multiplier=1.01,
              current_total=2880.0, offer_total=2688.0, savings_amount=192.0, savings_ratio=0.0667,
              extraction_result={"meta": {}, "vendor": "Sentetik", "invoice_period": DONEM},
              calculation_result=calc)
    db.add(o)
    db.commit()
    return o


SURUM2_DOGRULANMIS = {
    "version": 2, "period": DONEM, "system_lookup": "ok",
    "ptf": {"value": 2500.0, "status": "matched", "source": "manual_override", "trusted": True,
            "final": True, "system_verified": True, "epias": False},
    "yekdem": {"mode": "included", "value": 50.0, "status": "matched", "trusted": True, "final": True},
    "verified": True, "verified_by": "system", "draft_only": False, "provisional": False,
    "epias_basis": False, "blocking_reasons": [], "messages": [],
}


class TestEskiVeYeniTeklifler:
    def test_surum2_snapshot_degismeden_okunur_ve_belgesi_eski_etiketi_tasir(self, client, db):
        from app.price_provenance import snapshot_price_verified

        o = _eski_teklif(db, SURUM2_DOGRULANMIS)
        once = json.dumps(o.calculation_result, sort_keys=True)
        assert snapshot_price_verified(o.calculation_result) is True
        r = client.post(f"/offers/{o.id}/generate-html")
        assert r.status_code == 200 and "Ağırlıklı PTF" in r.text
        assert "Aylık aritmetik PTF" not in r.text and "TASLAK" not in r.text
        assert client.get(f"/offers/{o.id}").json()["calculation_result"]["meta_price_provenance"]["version"] == 2
        db.refresh(o)
        assert json.dumps(o.calculation_result, sort_keys=True) == once

    def test_eski_kayitlar_kesinlestirilemez_ve_taslak_pdfe_acilmaz(self, db):
        from fastapi import HTTPException
        from app.main import finalize_offer_price, generate_draft_pdf_for_offer

        faz1_oncesi = _eski_teklif(db, None)
        surum2 = _eski_teklif(db, SURUM2_DOGRULANMIS)
        for teklif, beklenen in ((faz1_oncesi, "taslak_degil"), (surum2, "zaten_kesin")):
            with pytest.raises(HTTPException) as e:
                finalize_offer_price(teklif.id, db=db, _=None)
            assert e.value.status_code == 409 and e.value.detail["error"] == beklenen
        with pytest.raises(HTTPException) as e:
            asyncio.run(generate_draft_pdf_for_offer(faz1_oncesi.id, db=db, _=None))
        assert e.value.status_code == 409

    @pytest.mark.parametrize("kurulum", ["final_manual_override", "final_epias_manual", "saatlik_epias_excel"])
    def test_kimliksiz_kesin_kaynak_kesin_teklif_uretemez(self, db, kurulum):
        if kurulum == "saatlik_epias_excel":
            _saatlik(db)
            ptf = 1500.0  # 'duz' profil ağırlıklı değer (eski Faz 1'de doğrulanırdı)
        else:
            _referans(db, source=kurulum.replace("final_", ""), status="final")
            ptf = ARITMETIK
        kod, govde = _teklif(db, ptf=ptf, mod="excluded", calc_ek={"offer_yekdem_tl": 0.0})
        assert kod == 422
        assert govde["error"]["blocking_reasons"] == ["ptf_identity_missing"]

    def test_istemci_provenance_enjeksiyonu_ezilir_ve_taslak_kesin_sayilmaz(self, db):
        from app.database import Offer

        _referans(db, source="manual_override", status="final")
        sahte = {**SURUM2_DOGRULANMIS, "version": 3}
        kod, govde = _teklif(db, mod="excluded", taslak=True,
                             calc_ek={"offer_yekdem_tl": 0.0, "meta_price_provenance": sahte})
        assert kod == 200 and govde["fiyat_durumu"] == "taslak"
        prov = db.query(Offer).get(govde["id"]).calculation_result["meta_price_provenance"]
        assert prov["verified"] is False and prov["blocking_reasons"] == ["ptf_identity_missing"]

    def test_onayli_kesin_teklif_belgesi_gercek_yontemi_yazar(self, client, db, storage_tmp):
        from app.database import Offer

        _saatlik(db)
        _referans(db)
        _ptf_onayla(db)
        _yekdem_onayla(db, "st")
        kod, govde = _teklif(db, segment="st")
        assert kod == 200 and govde["fiyat_durumu"] == "kesin", govde
        teklif = db.query(Offer).get(govde["id"])
        prov = teklif.calculation_result["meta_price_provenance"]
        assert prov["version"] == 3 and prov["ptf"]["source"] == "monthly_arithmetic:mcp_avg"
        assert prov["ptf"]["approval"]["revision"] == 1 and prov["yekdem"]["approval"]["revision"] == 1
        html = client.post(f"/offers/{teklif.id}/generate-html").text
        assert "Aylık aritmetik PTF (EPİAŞ)" in html and "Ağırlıklı PTF" not in html
        assert "Serbest Tüketici" in html and "TASLAK" not in html
        # Sonraki fiyat değişikliği kaydedilmiş teklifi değiştirmez (yeniden hesaplanmaz).
        once = json.dumps(teklif.calculation_result, sort_keys=True)
        db.execute(sa.text("UPDATE market_reference_prices SET ptf_tl_per_mwh = 2800 WHERE period=:p"), {"p": DONEM})
        db.commit()
        db.refresh(teklif)
        assert json.dumps(teklif.calculation_result, sort_keys=True) == once
        assert client.post(f"/offers/{teklif.id}/generate-html").status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# 11) Kaynak kimliği: tam versiyon, saatlik dayanak, gerçek sıfır, incelenebilir kanıt
# ═══════════════════════════════════════════════════════════════════════════

class TestTamVersiyonKimligi:
    def test_ayni_ay_farkli_tam_versiyonlar_esitlenmez_aday_onay_snapshot_ayni_kimlik(self, db):
        from app.price_approval import (
            OnayHatasi, gecerli_yekdem_onayi, yekdem_adayi_hazirla, yekdem_onayla,
        )
        from app.price_provenance import build_price_provenance

        # Ekranda yalnız 1. versiyon görüldü; aynı değerle aynı ayda 2. versiyon yayımlandı.
        eski_aday = yekdem_adayi_hazirla(db, SahteEpias(), DONEM, "st")
        assert eski_aday["version"] == VERSIYON_1
        SahteEpias.yekdem_satirlari = [(VERSIYON_1, ST, GTS), (VERSIYON_2, ST, GTS)]
        yeni_aday = yekdem_adayi_hazirla(db, SahteEpias(), DONEM, "st")
        assert yeni_aday["version"] == VERSIYON_2 and yeni_aday["value"] == eski_aday["value"]
        assert yeni_aday["aday_parmak_izi"] != eski_aday["aday_parmak_izi"], "ay düzeyinde eşitlendi"
        assert yeni_aday["kaynak_kanit"]["ayni_donem_versiyonlari"] == [VERSIYON_1, VERSIYON_2]

        with pytest.raises(OnayHatasi) as e:
            yekdem_onayla(db, SahteEpias(), period=DONEM, segment="st", aday_parmak_izi=eski_aday["aday_parmak_izi"],
                          beklenen_revision=0, onaylayan_beyan="Y", dogrulanan_yetki=YETKI, change_reason="g")
        assert e.value.kod == "aday_degisti" and e.value.ek["yeni_aday"]["version"] == VERSIYON_2
        assert _sayim(db, "yekdem_onay_revizyonlari") == 0

        yekdem_onayla(db, SahteEpias(), period=DONEM, segment="st", aday_parmak_izi=yeni_aday["aday_parmak_izi"],
                      beklenen_revision=0, onaylayan_beyan="Y", dogrulanan_yetki=YETKI, change_reason="g")
        satir = db.execute(sa.text("SELECT version, kaynak_kanit_sha256, kaynak_kanit_json "
                                   "FROM yekdem_onay_revizyonlari")).one()
        assert satir.version == VERSIYON_2
        assert satir.kaynak_kanit_sha256 == yeni_aday["kaynak_kanit_sha256"]
        assert hashlib.sha256(satir.kaynak_kanit_json.encode("utf-8")).hexdigest() == satir.kaynak_kanit_sha256
        assert json.loads(satir.kaynak_kanit_json) == yeni_aday["kaynak_kanit"]
        assert gecerli_yekdem_onayi(db, DONEM, "st").version == VERSIYON_2

        _referans(db)
        _ptf_onayla(db)
        prov = build_price_provenance(db, period=DONEM, ptf=ARITMETIK, yekdem=ST, yekdem_mode="included",
                                      yekdem_segment="st")
        assert prov["verified"] is True
        assert prov["yekdem"]["approval"]["version"] == VERSIYON_2
        assert prov["yekdem"]["approval"]["kaynak_kanit_sha256"] == yeni_aday["kaynak_kanit_sha256"]
        assert prov["ptf"]["approval"]["kaynak_kanit_sha256"] == \
            db.execute(sa.text("SELECT kaynak_kanit_sha256 FROM ptf_onay_revizyonlari")).scalar()

    def test_ayni_tam_versiyon_farkli_degerle_belirsiz(self, db):
        from app.price_approval import OnayHatasi, yekdem_adayi_hazirla

        SahteEpias.yekdem_satirlari = [(VERSIYON_1, ST, GTS), (VERSIYON_1, ST + 1, GTS)]
        with pytest.raises(OnayHatasi) as e:
            yekdem_adayi_hazirla(db, SahteEpias(), DONEM, "st")
        assert e.value.kod == "resmi_versiyon_belirsiz" and e.value.http == 422

    def test_tam_versiyon_yoksa_aday_olusmaz(self, db):
        from app.price_approval import OnayHatasi, yekdem_adayi_hazirla

        SahteEpias.yekdem_satirlari = [(None, ST, GTS)]
        with pytest.raises(OnayHatasi) as e:
            yekdem_adayi_hazirla(db, SahteEpias(), DONEM, "st")
        assert e.value.kod == "resmi_versiyon_yok"

    def test_gercek_istemci_tam_versiyonu_ve_saatlik_kalemleri_kisaltmadan_tasir(self):
        # Gerçek sınıf (modül yüklenirken yakalandı); HTTP yerine sahte yanıt gövdesi.
        istemci = _GERCEK_ISTEMCI()
        yanitlar = {
            "unit": {"items": [{"period": "2026-07-01T00:00:00+03:00", "version": VERSIYON_1,
                                "supplierUnitCost": 0, "unitCost": None},
                               {"period": "2026-07-01T00:00:00+03:00", "version": VERSIYON_2,
                                "supplierUnitCost": ST, "unitCost": GTS}]},
            "mcp": {"items": [{"date": "2026-07-01T00:00:00+03:00", "hour": "00:00", "price": 2500.5}],
                    "statistic": {"priceAvg": ARITMETIK, "ptfWeightedAvg": AGIRLIKLI},
                    "page": {"number": 1, "size": 1, "total": 1}},
        }
        istemci._istek = lambda yol, govde: yanitlar["unit" if "unit" in yol.lower() else "mcp"]
        satirlar = istemci.fetch_unit_cost(DONEM, DONEM)
        assert [(s.versiyon, s.versiyon_tam) for s in satirlar] == [("2026-07", VERSIYON_1), ("2026-07", VERSIYON_2)]
        assert (satirlar[0].serbest_tuketici, satirlar[0].gts_k1) == (0.0, None), "0 korunur, eksik None kalır"
        ozet = istemci.fetch_mcp(DONEM)
        assert ozet.saatlik == (("2026-07-01T00:00:00+03:00", 2500.5),)
        assert ozet.sayfa == {"number": 1, "size": 1, "total": 1} and ozet.aritmetik == ARITMETIK


class TestSaatlikPtfDayanagi:
    def test_tam_ve_tutarli_saatlik_veri_onaylanabilir(self, db):
        from app.price_approval import ptf_adayi_hazirla

        aday = ptf_adayi_hazirla(db, SahteEpias(), DONEM)
        d = aday["kaynak_kanit"]["saatlik_dogrulama"]
        assert aday["onaylanabilir"] is True and aday["onaylanamama_nedenleri"] == []
        assert (d["beklenen_saat"], d["donen_kalem"], d["tekil_gecerli_saat"], d["eksik_saat"]) == (744, 744, 744, 0)
        assert d["ilk_saat"] == "2026-07-01T00:00:00+03:00" and d["son_saat"] == "2026-07-31T23:00:00+03:00"
        assert aday["kaynak_kanit"]["alan"] == "statistic.priceAvg"
        assert aday["kaynak_kanit"]["priceAvg"] == repr(ARITMETIK)

    def test_subat_artik_yil_saat_sayisi(self):
        from app.price_approval import _ay_saatleri

        assert len(_ay_saatleri("2028-02")) == 696 and len(_ay_saatleri("2027-02")) == 672
        assert len(_ay_saatleri("2026-12")) == 744

    @pytest.mark.parametrize("bozulma,neden", [
        ("eksik", "eksik_saat"),
        ("tekrar", "tekrarlanan_saat"),
        ("donem_disi", "donem_disi_saat"),
        ("gecersiz_damga", "gecersiz_saat_damgasi"),
        ("saat_dilimsiz", "gecersiz_saat_damgasi"),
        ("gecersiz_fiyat", "gecersiz_fiyat"),
        ("sayfalama", "sayfalama_kirpilmasi"),
        ("yuvarlama", "ortalama_yuvarlama_tutarsiz"),
        ("bos", "saatlik_veri_yok"),
    ])
    def test_eksik_ya_da_tutarsiz_aday_onaylanamaz_hicbir_sey_yazilmaz(self, db, bozulma, neden):
        from app.price_approval import OnayHatasi, ptf_adayi_hazirla, ptf_onayla

        kalemler = list(_saatlik_kalemler(DONEM, ARITMETIK))
        if bozulma == "eksik":
            kalemler.pop(100)
        elif bozulma == "tekrar":
            kalemler[101] = kalemler[100]
        elif bozulma == "donem_disi":
            kalemler.append(("2026-08-01T00:00:00+03:00", ARITMETIK))
        elif bozulma == "gecersiz_damga":
            kalemler[5] = ("tarih-degil", ARITMETIK)
        elif bozulma == "saat_dilimsiz":
            kalemler[5] = ("2026-07-01T05:00:00", kalemler[5][1])
        elif bozulma == "gecersiz_fiyat":
            kalemler[7] = (kalemler[7][0], None)
        elif bozulma == "sayfalama":
            SahteEpias.sayfa = {"number": 1, "size": 744, "total": 1488}
        elif bozulma == "yuvarlama":
            kalemler = list(_saatlik_kalemler(DONEM, ARITMETIK + 0.02))
        elif bozulma == "bos":
            kalemler = []
        SahteEpias.saatlik = kalemler
        _referans(db)
        once = _ozet(db)

        aday = ptf_adayi_hazirla(db, SahteEpias(), DONEM)
        assert aday["onaylanabilir"] is False and neden in aday["onaylanamama_nedenleri"]
        with pytest.raises(OnayHatasi) as e:
            ptf_onayla(db, SahteEpias(), period=DONEM, aday_parmak_izi=aday["aday_parmak_izi"],
                       beklenen_revision=aday["beklenen_revision"],
                       beklenen_kayit_parmak_izi=aday["beklenen_kayit_parmak_izi"],
                       onaylayan_beyan="Y", dogrulanan_yetki=YETKI, change_reason="g")
        assert e.value.kod == "aday_dogrulanamadi" and e.value.http == 422
        assert _ozet(db) == once

    @pytest.mark.parametrize("fark,onaylanabilir", [(0.0049, True), (-0.0049, True), (0.0051, False), (-0.0051, False)])
    def test_yuvarlama_toleransi_siniri(self, db, fark, onaylanabilir):
        from app.price_approval import saatlik_ptf_dogrula

        rapor = saatlik_ptf_dogrula(DONEM, _saatlik_kalemler(DONEM, ARITMETIK + fark), ARITMETIK)
        assert rapor["onaylanabilir"] is onaylanabilir

    def test_aday_ucu_onaylanamaz_adayi_nedenleriyle_gosterir(self, client, db):
        SahteEpias.saatlik = _saatlik_kalemler(DONEM, ARITMETIK, sapma=0.0)[:-1]  # ortalama bozulmaz
        r = client.get(f"/admin/market-prices/approval-candidate?period={DONEM}&kalem=PTF")
        assert r.status_code == 200
        govde = r.json()
        assert govde["onaylanabilir"] is False and govde["onaylanamama_nedenleri"] == ["eksik_saat"]
        assert govde["kaynak_kanit"]["saatlik_dogrulama"]["eksik_ornek"] == ["2026-07-31T23:00:00+03:00"]
        assert "kaynak_kanit_json" not in govde


class TestGercekSifirYekdem:
    def test_gercek_sifir_kanitla_onaylanir_ve_kesin_teklifte_kullanilir(self, db):
        from app.database import Offer
        from app.price_provenance import build_price_provenance

        SahteEpias.st = 0.0
        _referans(db, yekdem=0.0)
        _ptf_onayla(db)
        sonuc = _yekdem_onayla(db, "st")
        assert sonuc["value"] == 0.0
        kanit = json.loads(db.execute(sa.text("SELECT kaynak_kanit_json FROM yekdem_onay_revizyonlari")).scalar())
        assert kanit["supplierUnitCost"] == "0.0" and kanit["segment_alani"] == "supplierUnitCost"
        prov = build_price_provenance(db, period=DONEM, ptf=ARITMETIK, yekdem=0.0, yekdem_mode="included",
                                      yekdem_segment="st")
        assert prov["verified"] is True and prov["yekdem"]["mode"] == "included"
        kod, govde = _teklif(db, yekdem=0.0, segment="st", calc_ek={"offer_yekdem_tl": 0.0})
        assert kod == 200 and govde["fiyat_durumu"] == "kesin", govde
        assert db.query(Offer).get(govde["id"]).yekdem == 0.0

    def test_eksik_deger_sifir_sayilmaz(self, db, client):
        from app.price_approval import OnayHatasi, yekdem_adayi_hazirla
        from app.price_provenance import build_price_provenance

        SahteEpias.st = None
        with pytest.raises(OnayHatasi) as e:
            yekdem_adayi_hazirla(db, SahteEpias(), DONEM, "st")
        assert e.value.kod == "resmi_segment_degeri_yok"
        r = client.get(f"/admin/market-prices/approval-candidate?period={DONEM}&kalem=YEKDEM&segment=st")
        assert r.status_code == 422 and r.json()["detail"]["error"] == "resmi_segment_degeri_yok"
        assert _sayim(db, "yekdem_onay_revizyonlari") == 0
        # Onaylı gerçek 0 varken bile teklifte değer YOKSA (None) doğrulanmaz.
        SahteEpias.st = 0.0
        _referans(db, yekdem=0.0)
        _ptf_onayla(db)
        _yekdem_onayla(db, "st")
        prov = build_price_provenance(db, period=DONEM, ptf=ARITMETIK, yekdem=None, yekdem_mode="included",
                                      yekdem_segment="st")
        assert prov["verified"] is False


class TestKanitVeKimlikBilgisi:
    def test_onay_gecmisi_ucu_incelenebilir_kaniti_dondurur(self, client, db, monkeypatch):
        import app.main as m

        monkeypatch.setattr(m, "ADMIN_API_KEY_ENABLED", True)
        monkeypatch.setattr(m, "ADMIN_API_KEY", ONAY_ANAHTARI)
        _referans(db)
        _ptf_onayla(db)
        _yekdem_onayla(db, "gts")
        once, cagri_once = _ozet(db), SahteEpias.cagri
        assert client.get(f"/admin/market-prices/approvals?period={DONEM}").status_code == 401
        r = client.get(f"/admin/market-prices/approvals?period={DONEM}", headers={"X-Admin-Key": ONAY_ANAHTARI})
        assert r.status_code == 200, r.text
        govde = r.json()
        assert govde["altyapi"] is True and govde["resmi_kesinlesme"] == "BELIRSIZ"
        ptf = govde["ptf"][0]
        assert ptf["gecerli"] is True and ptf["basis"] == "mcp_avg"
        assert ptf["kaynak_kanit"]["saatlik_dogrulama"]["beklenen_saat"] == 744
        assert ptf["kaynak_kanit"]["priceAvg"] == repr(ARITMETIK)
        yek = govde["yekdem"][0]
        assert (yek["segment"], yek["version"], yek["guncel"]) == ("gts", VERSIYON_1, True)
        assert yek["kaynak_kanit"]["segment_alani"] == "unitCost"
        for satir in (ptf, yek):
            metin = json.dumps(satir["kaynak_kanit"], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            assert hashlib.sha256(metin.encode("utf-8")).hexdigest() == satir["kaynak_kanit_sha256"]
        assert _ozet(db) == once and SahteEpias.cagri == cagri_once, "geçmiş ucu EPİAŞ'a gitmez"

    def test_kimlik_bilgisi_eksik_acik_hata_ve_sir_sizmaz(self, client, db, monkeypatch):
        from app.epias_public_client import EpiasKimlikBilgisiEksik

        monkeypatch.setenv("EPIAS_PASSWORD", "")
        SahteEpias.hata = EpiasKimlikBilgisiEksik("EPIAS_USERNAME/EPIAS_PASSWORD tanımlı değil")
        r = client.get(f"/admin/market-prices/approval-candidate?period={DONEM}&kalem=PTF")
        assert r.status_code == 503 and r.json()["detail"]["error"] == "kimlik_bilgisi_eksik"
        assert "tarayıcıdan girilmez" in r.json()["detail"]["message"]

    def test_gercek_istemci_kimlik_yoksa_ozel_hata_firlatir_ag_yok(self, monkeypatch):
        from app.epias_public_client import EpiasKimlikBilgisiEksik

        monkeypatch.delenv("EPIAS_USERNAME", raising=False)
        monkeypatch.delenv("EPIAS_PASSWORD", raising=False)
        istemci = _GERCEK_ISTEMCI()
        with pytest.raises(EpiasKimlikBilgisiEksik):
            istemci.get_tgt()  # _client autouse ile patlatılır; kimlik denetimi ağdan ÖNCE
        istemci.kapat()


# ═══════════════════════════════════════════════════════════════════════════
# 12) Onay tabloları YOKKEN ölçülen davranış (iddia değil, ölçüm)
# ═══════════════════════════════════════════════════════════════════════════

class TestOnayTablolariYok:
    @pytest.fixture()
    def tablosuz_db(self):
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app.database import Base
        import app.pricing.schemas  # noqa: F401

        motor = sa.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                                 poolclass=StaticPool)
        Base.metadata.create_all(motor)
        s = sessionmaker(bind=motor)()
        yield s
        s.close()

    def test_tablolar_yokken_olculen_davranis(self, tablosuz_db, storage_tmp, monkeypatch):
        from app.database import Offer, get_db
        from app.main import app as fastapi_app
        from app.price_approval import onay_tablolari_var
        from app.price_provenance import snapshot_price_verified
        import app.main as m

        db = tablosuz_db
        assert onay_tablolari_var(db) is False
        _referans(db, source="epias_api", status="final")
        eski = _eski_teklif(db, SURUM2_DOGRULANMIS)
        eski_once = json.dumps(eski.calculation_result, sort_keys=True)

        # a) kesin teklif isteği: reddedilir, kayıt yok (ölçülen nedenler: PTF kimliği + YEKDEM onayı yok)
        kod, govde = _teklif(db, segment="st")
        assert kod == 422
        assert govde["error"]["blocking_reasons"] == ["ptf_identity_missing", "yekdem_approval_missing"]
        assert db.query(Offer).count() == 1
        # b) açık taslak isteği: taslak olarak kaydedilir
        kod, govde = _teklif(db, segment="st", taslak=True)
        assert kod == 200 and govde["fiyat_durumu"] == "taslak"
        # c) eski sürüm 2 teklif değişmeden kesin okunur
        db.refresh(eski)
        assert snapshot_price_verified(eski.calculation_result) is True
        assert json.dumps(eski.calculation_result, sort_keys=True) == eski_once

        monkeypatch.setattr(m, "ADMIN_API_KEY_ENABLED", False)
        monkeypatch.setattr(m, "ADMIN_API_KEY", ONAY_ANAHTARI)
        fastapi_app.dependency_overrides[get_db] = lambda: db
        try:
            c = TestClient(fastapi_app)
            # d) aday salt okunur: resmî aday gösterilir, geçerli onay yok
            r = c.get(f"/admin/market-prices/approval-candidate?period={DONEM}&kalem=PTF")
            assert r.status_code == 200 and r.json()["gecerli_onay"] is None
            # e) onay yazımı: 503 onay_altyapisi_yok, hiçbir şey yazılmaz
            aday = r.json()
            r = c.post("/admin/market-prices/approve", headers={"X-Admin-Key": ONAY_ANAHTARI}, json={
                "period": DONEM, "kalem": "PTF", "aday_parmak_izi": aday["aday_parmak_izi"],
                "beklenen_revision": aday["beklenen_revision"],
                "beklenen_kayit_parmak_izi": aday["beklenen_kayit_parmak_izi"],
                "onaylayan_beyan": "Y", "change_reason": "g"})
            assert r.status_code == 503 and r.json()["detail"]["error"] == "onay_altyapisi_yok"
            assert onay_tablolari_var(db) is False
            # f) geçmiş ucu: altyapı yok bilgisi
            r = c.get(f"/admin/market-prices/approvals?period={DONEM}")
            assert r.status_code == 200 and r.json()["altyapi"] is False
        finally:
            fastapi_app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════
# 13) Tarihsel sürüm 2 snapshot (master 38245e9'dan YAKALANMIŞ) değişmeden okunur
# ═══════════════════════════════════════════════════════════════════════════

def test_master_yakalanmis_surum2_snapshot_okunur_ve_degismez(client, db):
    import os
    from app.price_provenance import snapshot_price_verified

    yol = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "tarihsel_provenance_v2_38245e9.json")
    with open(yol, encoding="utf-8") as fh:
        fikstur = json.load(fh)
    for anahtar in ("s5_2026_01", "faz1_2099_01"):
        prov = fikstur[anahtar]["provenance"]
        assert fikstur[anahtar]["yakalama"]["commit"].startswith("38245e9")
        assert prov["version"] == 2 and prov["verified"] is True
        o = _eski_teklif(db, prov)
        once = json.dumps(o.calculation_result, sort_keys=True)
        assert snapshot_price_verified(o.calculation_result) is True
        r = client.post(f"/offers/{o.id}/generate-html")
        assert r.status_code == 200 and "TASLAK" not in r.text and "Aylık aritmetik PTF" not in r.text
        db.refresh(o)
        assert json.dumps(o.calculation_result, sort_keys=True) == once


# ═══════════════════════════════════════════════════════════════════════════
# 14) Kimlik güven sınırı: beyan edilen ad ≠ doğrulanmış yetkili
# ═══════════════════════════════════════════════════════════════════════════

class TestOnaylayanGuvenSiniri:
    def test_onay_satiri_beyan_ve_dogrulanan_yetkiyi_ayri_tutar_istemci_yetki_yazamaz(self, client, db, monkeypatch):
        import app.main as m
        from app.price_approval import ptf_adayi_hazirla
        from app.price_provenance import build_price_provenance

        monkeypatch.setattr(m, "ADMIN_API_KEY", ONAY_ANAHTARI)
        _referans(db)
        aday = ptf_adayi_hazirla(db, SahteEpias(), DONEM)
        r = client.post("/admin/market-prices/approve", headers={"X-Admin-Key": ONAY_ANAHTARI}, json={
            "period": DONEM, "kalem": "PTF", "aday_parmak_izi": aday["aday_parmak_izi"],
            "beklenen_revision": aday["beklenen_revision"],
            "beklenen_kayit_parmak_izi": aday["beklenen_kayit_parmak_izi"],
            "onaylayan_beyan": "Genel Müdür (beyan)", "change_reason": "onay",
            # İstemcinin kendi yazdığı "doğrulanmış kimlik" alanları YOK SAYILIR.
            "dogrulanan_yetki": "e_imza_dogrulandi", "onaylayan_dogrulandi": True, "approved_by": "Sahte"})
        assert r.status_code == 200, r.text
        assert r.json()["onaylayan_beyan"] == "Genel Müdür (beyan)"
        assert r.json()["onaylayan_dogrulandi"] is False
        assert r.json()["dogrulanan_yetki"] == "paylasilan_yonetici_anahtari"
        satir = db.execute(sa.text("SELECT onaylayan_beyan, dogrulanan_yetki FROM ptf_onay_revizyonlari")).one()
        assert tuple(satir) == ("Genel Müdür (beyan)", "paylasilan_yonetici_anahtari")
        gecmis = db.execute(sa.text("SELECT change_reason FROM price_change_history")).scalar()
        assert "yetki: paylasilan_yonetici_anahtari" in gecmis and "beyandır" in gecmis
        onay = build_price_provenance(db, period=DONEM, ptf=ARITMETIK, yekdem=None,
                                      yekdem_mode="excluded")["ptf"]["approval"]
        assert onay["onaylayan_beyan"] == "Genel Müdür (beyan)" and onay["onaylayan_dogrulandi"] is False
        assert "kişi doğrulanmadı" in onay["dogrulanan_yetki_etiketi"]
        assert "approved_by" not in onay

    def test_dogrulanmamis_yetki_turu_hicbir_sey_yazmaz(self, db):
        from app.price_approval import OnayHatasi, ptf_adayi_hazirla, ptf_onayla

        _referans(db)
        aday = ptf_adayi_hazirla(db, SahteEpias(), DONEM)
        once = _ozet(db)
        for yetki in ("", None, "kullanici_beyani"):
            with pytest.raises(OnayHatasi) as e:
                ptf_onayla(db, SahteEpias(), period=DONEM, aday_parmak_izi=aday["aday_parmak_izi"],
                           beklenen_revision=0, beklenen_kayit_parmak_izi=aday["beklenen_kayit_parmak_izi"],
                           onaylayan_beyan="Y", dogrulanan_yetki=yetki, change_reason="g")
            assert e.value.kod == "yetki_dogrulanmadi" and e.value.http == 403
        assert _ozet(db) == once

    def test_kesinlestiren_beyan_olarak_ve_yetki_ayri_kaydedilir(self, db):
        from app.database import Offer
        from app.main import finalize_offer_price

        _referans(db)
        _ptf_onayla(db)
        _yekdem_onayla(db, "st")
        _, govde = _teklif(db, segment=None, taslak=True)
        sonuc = finalize_offer_price(govde["id"], yekdem_segment="st", kesinlestiren="Satış Uzmanı", db=db, _=None)
        assert sonuc["fiyat_durumu"] == "kesin"
        kes = db.query(Offer).get(govde["id"]).calculation_result["meta_fiyat_kesinlesme"]
        assert kes["kesinlestiren_beyan"] == "Satış Uzmanı" and kes["kesinlestiren_dogrulandi"] is False
        assert kes["dogrulanan_yetki"] in ("api_anahtari", "dogrulama_kapali")
        assert "kesinlestiren" not in kes


# ═══════════════════════════════════════════════════════════════════════════
# 15) YEKDEM: en son yayımlanan tam versiyon (tarih sıralaması, metin tahmini yok)
# ═══════════════════════════════════════════════════════════════════════════

class TestVersiyonSiralamasi:
    def test_en_son_versiyon_tarih_ile_secilir_metin_sirasi_ile_degil(self, db):
        from app.price_approval import yekdem_adayi_hazirla

        # Metin olarak küçük olan "…T23:00:00+00:00" (UTC) aslında +03:00 ile 05T02:00 → daha yeni.
        eski, yeni = "2026-07-05T00:00:00+03:00", "2026-07-04T23:00:00+00:00"
        assert yeni < eski  # metin sıralaması yanıltıcı
        SahteEpias.yekdem_satirlari = [(eski, ST, GTS), (yeni, ST + 1, GTS)]
        aday = yekdem_adayi_hazirla(db, SahteEpias(), DONEM, "st")
        assert aday["version"] == yeni and aday["value"] == ST + 1
        assert aday["kaynak_kanit"]["ayni_donem_versiyonlari"] == [eski, yeni]
        assert aday["kaynak_kanit"]["resmi_kesinlesme"] == "BELIRSIZ"

    @pytest.mark.parametrize("satirlar", [
        [("v2", ST, GTS), (VERSIYON_1, ST, GTS)],
        [("2026-07-20T00:00:00", ST, GTS)],  # saat dilimsiz
        [("2026-07-20T00:00:00+03:00", ST, GTS), ("2026-07-19T21:00:00+00:00", ST, GTS)],  # aynı an, iki metin
    ])
    def test_siralanamayan_versiyonla_aday_olusmaz(self, db, satirlar):
        from app.price_approval import OnayHatasi, yekdem_adayi_hazirla

        SahteEpias.yekdem_satirlari = satirlar
        with pytest.raises(OnayHatasi) as e:
            yekdem_adayi_hazirla(db, SahteEpias(), DONEM, "st")
        assert e.value.kod == "resmi_versiyon_siralanamaz" and e.value.http == 422


# ═══════════════════════════════════════════════════════════════════════════
# 16) Segment istek → snapshot; hata halinde taslak korunur; eski snapshot kapıyı aşamaz
# ═══════════════════════════════════════════════════════════════════════════

class TestSegmentVeSnapshotBaglantisi:
    def test_segment_istekten_taslak_ve_kesin_snapshota_tasinir_hata_taslagi_korur(self, db, storage_tmp):
        from app.database import Offer
        from app.main import finalize_offer_price, generate_draft_pdf_for_offer

        _referans(db)
        _ptf_onayla(db)
        kod, govde = _teklif(db, segment="gts", yekdem=GTS, taslak=True)
        assert kod == 200 and govde["fiyat_durumu"] == "taslak"
        teklif = db.query(Offer).get(govde["id"])
        cr = teklif.calculation_result
        assert cr["meta_teklif_fiyat_girdisi"]["yekdem_segment"] == "gts"
        assert cr["meta_price_provenance"]["yekdem"]["segment"] == "gts"
        oncesi = json.dumps(cr, sort_keys=True)

        # Hata 1: segment çelişkisi; Hata 2: onay yok → taslak DEĞİŞMEZ, taslak PDF hâlâ üretilir.
        r1 = finalize_offer_price(teklif.id, yekdem_segment="st", kesinlestiren="K", db=db, _=None)
        r2 = finalize_offer_price(teklif.id, yekdem_segment=None, kesinlestiren="K", db=db, _=None)
        assert (r1.status_code, r2.status_code) == (422, 422)
        db.refresh(teklif)
        assert json.dumps(teklif.calculation_result, sort_keys=True) == oncesi
        pdf = asyncio.run(generate_draft_pdf_for_offer(teklif.id, db=db, _=None))
        assert pdf.headers["X-Fiyat-Durumu"] == "taslak"

        _yekdem_onayla(db, "gts")
        sonuc = finalize_offer_price(teklif.id, yekdem_segment=None, kesinlestiren="K", db=db, _=None)
        assert sonuc["fiyat_durumu"] == "kesin"
        db.refresh(teklif)
        cr = teklif.calculation_result
        assert cr["meta_teklif_fiyat_girdisi"]["yekdem_segment"] == "gts"
        assert cr["meta_price_provenance"]["yekdem"]["segment"] == "gts"
        assert cr["meta_price_provenance"]["yekdem"]["approval"]["segment"] == "gts"
        assert cr["meta_price_provenance"]["yekdem"]["approval"]["version"] == VERSIYON_1

    def test_eski_kesin_snapshot_yeni_istekte_kimlik_kapisini_asamaz(self, db):
        from app.database import Offer

        _referans(db)
        _ptf_onayla(db)
        _yekdem_onayla(db, "st")
        kod, govde = _teklif(db, segment="st")
        assert kod == 200 and govde["fiyat_durumu"] == "kesin"
        gercek_eski = db.query(Offer).get(govde["id"]).calculation_result["meta_price_provenance"]
        assert gercek_eski["verified"] is True and gercek_eski["version"] == 3

        # Onay sonradan geçersizleşir (kayıt onay dışı yoldan değişir).
        db.execute(sa.text("UPDATE market_reference_prices SET source='manual_override' WHERE period=:p"),
                   {"p": DONEM})
        db.commit()
        # Aynı ekran değerleri + GERÇEK eski kesin snapshot istemci gövdesinde → yine reddedilir.
        ek = {"meta_price_provenance": gercek_eski, "meta_fiyat_durumu": "kesin"}
        kod, govde = _teklif(db, segment="st", calc_ek=ek)
        assert kod == 422 and govde["error"]["blocking_reasons"][0] == "ptf_identity_missing"
        assert db.query(Offer).count() == 1
        # Taslak istenirse sunucu kendi snapshot'ını yazar; istemcinin "kesin" iddiası taşınmaz.
        kod, govde = _teklif(db, segment="st", taslak=True, calc_ek=ek)
        cr = db.query(Offer).get(govde["id"]).calculation_result
        assert cr["meta_fiyat_durumu"] == "taslak" and cr["meta_price_provenance"]["verified"] is False
