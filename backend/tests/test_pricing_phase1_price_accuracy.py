"""
Fiyat Doğruluğu Faz 1 — eksik ya da doğrulanmamış PTF/YEKDEM ile teklif KESİNLEŞMEZ.

Owner GO kapsamı (hedefli):
- eksik veri (PTF/YEKDEM kaydı yok) → null/bilinmiyor; varsayılan sabit YOK
- gerçek sıfır (saatlik 0 TL/MWh saatleri; açıkça girilen YEKDEM 0) eksikten AYRI
- açıkça seçilen "YEKDEM hariç" gerçek 0'dan AYRI
- API'den doğrudan kayıt/PDF denemesi (POST /offers, /offers/{id}/generate-pdf,
  /generate-pdf-simple, /generate-pdf-direct) sunucuda reddedilir
- mevcut ağırlıklı PTF öncelikleri korunur (manual_override > tüketim > saatlik > referans)
- ortak fiyat yazımında mevcut admin yetkisi; EPİAŞ otomatik çekimi kapalı
Dönem yarışı frontend testindedir: frontend/src/pricing/__tests__/usePeriodPriceFetch.test.tsx

Ağ YOK: EPİAŞ istemcisine her erişim testi patlatır (autouse). Veritabanı in-memory
SQLite. Başarılı POST /offers TestClient altında asılı kalabildiği için (bkz.
test_s5_r01_offer_pdf.py harness notu) kalıcı kayıt yolu uç fonksiyonu doğrudan
çağrılarak sınanır; HTTP sözleşmesi (query param adları) ret yollarında TestClient ile.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

PERIOD = "2099-01"
DATE = "2099-01-01"
# 10→T1 (Gündüz), 19→T2 (Puant), 2→T3 (Gece)
MARKET = [(10, 1000.0), (19, 3000.0), (2, 500.0)]
DUZ = 1500.0     # (1000 + 3000 + 500) / 3
PUANT = 2150.0   # (1.5*1000 + 3*3000 + 0.5*500) / 5

# CalculationResult zorunlu alanları — sentetik, tutarlı (test_s5_r01_offer_pdf ile aynı).
HESAP_SONUCU = {
    "current_energy_tl": 2000.0,
    "current_distribution_tl": 300.0,
    "current_demand_tl": 0.0,
    "current_btv_tl": 100.0,
    "current_vat_matrah_tl": 2400.0,
    "current_vat_tl": 480.0,
    "current_total_with_vat_tl": 2880.0,
    "offer_ptf_tl": 1800.0,
    "offer_yekdem_tl": 50.0,
    "offer_energy_tl": 1850.0,
    "offer_distribution_tl": 300.0,
    "offer_demand_tl": 0.0,
    "offer_btv_tl": 90.0,
    "offer_vat_matrah_tl": 2240.0,
    "offer_vat_tl": 448.0,
    "offer_total_with_vat_tl": 2688.0,
    "difference_excl_vat_tl": 160.0,
    "difference_incl_vat_tl": 192.0,
    "savings_ratio": 0.0667,
    "unit_price_savings_ratio": 0.075,
}

PDF_FORM = {
    "consumption_kwh": "1000",
    "current_energy_tl": "500",
    "offer_energy_tl": "400",
    "offer_total": "450",
    "savings_ratio": "0.10",
    "invoice_period": PERIOD,
}


# ═══════════════════════════════════════════════════════════════════════════
# Fikstürler
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    import app.pricing.schemas  # noqa: F401 — saatlik/YEKDEM tabloları Base.metadata'ya

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
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


@pytest.fixture(autouse=True)
def _epias_cagrisi_yasak(monkeypatch):
    """Faz 1: hiçbir yol EPİAŞ istemcisine gitmemeli — giderse test patlar."""
    def _patla(*_a, **_k):
        raise AssertionError("EPİAŞ istemcisi çağrıldı (Faz 1'de kapalı olmalıydı)")

    monkeypatch.setattr("app.market_prices.fetch_and_cache_from_epias", _patla)
    monkeypatch.setattr("app.epias_client.fetch_market_prices_from_epias", _patla)
    monkeypatch.setattr("app.epias_client.EpiasClient._get_eptr", _patla)


def _saatlik(db, kayitlar=MARKET, period=PERIOD, source="epias_excel"):
    from app.pricing.schemas import HourlyMarketPrice

    for saat, ptf in kayitlar:
        db.add(HourlyMarketPrice(period=period, date=DATE, hour=saat, ptf_tl_per_mwh=ptf,
                                 smf_tl_per_mwh=ptf, source=source, is_active=1))
    db.commit()


def _referans(db, ptf, yekdem, source="epias_manual", note=None, period=PERIOD):
    from app.database import MarketReferencePrice

    db.add(MarketReferencePrice(period=period, price_type="PTF", ptf_tl_per_mwh=ptf,
                                yekdem_tl_per_mwh=yekdem, source=source, source_note=note,
                                is_locked=0, status="final"))
    db.commit()


def _fatura(yek_tl=None, period=PERIOD):
    from app.models import ChargeBreakdown, FieldValue, InvoiceExtraction, InvoiceMeta

    return InvoiceExtraction(
        vendor="test", invoice_period=period,
        consumption_kwh=FieldValue(value=1000.0, confidence=1.0),
        current_active_unit_price_tl_per_kwh=FieldValue(value=2.0, confidence=1.0),
        distribution_unit_price_tl_per_kwh=FieldValue(value=0.5, confidence=1.0),
        demand_qty=FieldValue(value=0.0, confidence=1.0),
        demand_unit_price_tl_per_unit=FieldValue(value=0.0, confidence=1.0),
        invoice_total_with_vat_tl=FieldValue(value=3500.0, confidence=1.0),
        charges=(ChargeBreakdown(yek_amount=FieldValue(value=yek_tl, confidence=1.0))
                 if yek_tl is not None else None),
        meta=InvoiceMeta(tariff_group_guess="Sanayi"),
    )


def _teklif_cagir(db, *, ptf, yekdem, onay=False, haric=False, period=PERIOD, calc_ek=None):
    """POST /offers uç fonksiyonunu doğrudan çağırır (TestClient asılma notu, modül başlığı)."""
    from app.main import create_offer
    from app.models import CalculationResult, FieldValue, InvoiceExtraction, OfferParams

    ext = InvoiceExtraction(
        vendor="Sentetik", invoice_period=period,
        consumption_kwh=FieldValue(value=1000.0, confidence=1.0),
        current_active_unit_price_tl_per_kwh=FieldValue(value=2.0, confidence=1.0),
    )
    calc = CalculationResult(**{**HESAP_SONUCU, **(calc_ek or {})})
    params = OfferParams(weighted_ptf_tl_per_mwh=ptf, yekdem_tl_per_mwh=yekdem,
                         agreement_multiplier=1.01)
    return asyncio.run(create_offer(
        extraction=ext, calculation=calc, params=params, customer_id=None,
        invoice_total_raw="2880", operator_confirmed_warnings=False,
        price_confirmed_by_user=onay, yekdem_excluded=haric, db=db,
    ))


def _yanit(r):
    """create_offer başarıda dict, ret yolunda JSONResponse döner."""
    if isinstance(r, dict):
        return 200, r
    return r.status_code, json.loads(r.body)


def _kayitli_teklif(db, provenance=None):
    from app.database import Offer

    calc = dict(HESAP_SONUCU)
    if provenance is not None:
        calc["meta_price_provenance"] = provenance
    o = Offer(tenant_id="default", vendor="Sentetik", invoice_period=PERIOD,
              consumption_kwh=1000.0, current_unit_price=2.0, weighted_ptf=2500.0,
              yekdem=50.0, agreement_multiplier=1.01, current_total=2880.0,
              offer_total=2688.0, savings_amount=192.0, savings_ratio=0.0667,
              extraction_result={"meta": {}}, calculation_result=calc)
    db.add(o)
    db.commit()
    return o


# ═══════════════════════════════════════════════════════════════════════════
# 1) GET /api/epias/prices — eksik veri, gerçek sıfır, kaynak güveni
# ═══════════════════════════════════════════════════════════════════════════

class TestDonemFiyatiOkuma:
    def test_kayit_yoksa_null_ve_varsayilan_sabit_yok(self, client):
        r = client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=true&profile=duz")
        assert r.status_code == 200
        b = r.json()
        assert b["ptf_tl_per_mwh"] is None and b["yekdem_tl_per_mwh"] is None
        assert b["weighted_ptf_tl_per_mwh"] is None
        assert b["source"] == "not_found" and b["yekdem_status"] == "missing"
        assert b["weighted_ptf_source"] == "not_found"
        prov = b["price_provenance"]
        assert prov["verified"] is False
        assert set(prov["blocking_reasons"]) == {"ptf_missing", "yekdem_missing"}
        assert "2974" not in r.text and "364" not in r.text, "varsayılan sabit sızdı"

    def test_saatlik_var_referans_yok_yekdem_bilinmiyor_sifir_degil(self, client, db):
        _saatlik(db)
        b = client.get(f"/api/epias/prices/{PERIOD}?profile=duz").json()
        assert b["weighted_ptf_tl_per_mwh"] == pytest.approx(DUZ)
        assert b["weighted_ptf_source"] == "hourly_weighted:duz"
        assert b["ptf_source_warning"] is None
        assert b["yekdem_tl_per_mwh"] is None and b["yekdem_status"] == "missing"
        prov = b["price_provenance"]
        assert prov["ptf"]["system_verified"] is True and prov["ptf"]["epias"] is True
        assert prov["blocking_reasons"] == ["yekdem_missing"]

    def test_saatlik_gercek_sifir_saatleri_eksik_sayilmaz(self, client, db):
        _saatlik(db, [(10, 0.0), (19, 3000.0), (2, 0.0)])
        b = client.get(f"/api/epias/prices/{PERIOD}?profile=duz").json()
        assert b["weighted_ptf_tl_per_mwh"] == pytest.approx(1000.0), "0 TL/MWh saatler ortalamaya girer"

    def test_db_yekdem_sifir_deger_olarak_tasinir_ve_onay_ister(self, client, db):
        _referans(db, 2500.0, 0.0)
        b = client.get(f"/api/epias/prices/{PERIOD}?profile=duz").json()
        assert b["yekdem_tl_per_mwh"] == 0.0, "0 değeri null'a çevrilmemeli"
        assert b["yekdem_status"] == "zero_unverified"
        prov = b["price_provenance"]
        assert prov["requires_confirmation"] is True
        assert prov["blocking_reasons"] == ["confirmation_required"]

    def test_gelistirme_ornek_verisi_dogrulanmis_sayilmaz(self, client, db):
        _referans(db, 2500.0, 300.0, source="epias_manual", note="Sample data (dev)")
        prov = client.get(f"/api/epias/prices/{PERIOD}").json()["price_provenance"]
        assert prov["ptf"]["system_verified"] is False and prov["ptf"]["epias"] is False
        assert prov["requires_confirmation"] is True

    def test_guvenilir_manuel_kayit_onaysiz_dogrulanir_ama_epias_degil(self, client, db):
        _referans(db, 2508.8, 235.63, source="manual_override")
        prov = client.get(f"/api/epias/prices/{PERIOD}").json()["price_provenance"]
        assert prov["verified"] is True and prov["verified_by"] == "system"
        assert prov["epias_basis"] is False, "manual_override EPİAŞ etiketi taşımaz"

    def test_epias_etiketli_kayit_epias_temelli(self, client, db):
        _referans(db, 2508.8, 235.63, source="epias_manual")
        prov = client.get(f"/api/epias/prices/{PERIOD}").json()["price_provenance"]
        assert prov["verified"] is True and prov["epias_basis"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 2) AI yolu (calculate_offer) — sessiz YEKDEM=0 kapalı
# ═══════════════════════════════════════════════════════════════════════════

class TestAIHesapYolu:
    def test_faturada_yekdem_var_donem_yekdemi_yok_fail_closed(self, db):
        from app.calculator import CalculationError, calculate_offer
        from app.models import OfferParams

        _saatlik(db)
        with pytest.raises(CalculationError, match="YEKDEM"):
            calculate_offer(_fatura(yek_tl=120.0), OfferParams(), db=db)

    def test_faturada_yekdem_yok_haric_isaretlenir(self, db):
        from app.calculator import calculate_offer
        from app.models import OfferParams

        _saatlik(db)
        sonuc = calculate_offer(_fatura(), OfferParams(), db=db)
        assert sonuc.offer_yekdem_tl == 0
        yek = sonuc.meta_price_provenance["yekdem"]
        assert yek["mode"] == "excluded" and yek["exclusion_basis"] == "invoice"

    def test_override_yekdem_verilmezse_sessiz_sifir_yok(self, db):
        from app.calculator import CalculationError, calculate_offer
        from app.models import OfferParams

        params = OfferParams(use_reference_prices=False, weighted_ptf_tl_per_mwh=2000.0)
        with pytest.raises(CalculationError, match="YEKDEM"):
            calculate_offer(_fatura(yek_tl=120.0), params, db=db)

    def test_override_acik_sifir_yekdem_deger_ama_onay_ister(self, db):
        from app.calculator import calculate_offer
        from app.models import OfferParams

        params = OfferParams(use_reference_prices=False, weighted_ptf_tl_per_mwh=2000.0,
                             yekdem_tl_per_mwh=0.0)
        sonuc = calculate_offer(_fatura(yek_tl=120.0), params, db=db)
        yek = sonuc.meta_price_provenance["yekdem"]
        assert yek["mode"] == "included" and yek["status"] == "zero" and yek["value"] == 0.0
        assert sonuc.meta_price_provenance["requires_confirmation"] is True

    def test_dogrulanmis_db_fiyati_ai_yolunda_onay_istemez(self, db):
        from app.calculator import calculate_offer
        from app.models import OfferParams

        _referans(db, 2508.8, 235.63, source="epias_manual")  # saatlik yok → referans skaler
        sonuc = calculate_offer(_fatura(yek_tl=120.0), OfferParams(), db=db)
        prov = sonuc.meta_price_provenance
        assert sonuc.meta_pricing_source == "reference_scalar"
        assert prov["verified"] is True and prov["epias_basis"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 3) POST /offers — sunucu kapısı + snapshot'ta dönem/kaynak/kullanıcı doğrulaması
# ═══════════════════════════════════════════════════════════════════════════

class TestTeklifKaydiKapisi:
    def test_dogrulanmamis_fiyat_422_ve_kayit_yok(self, db):
        from app.database import Offer

        kod, govde = _yanit(_teklif_cagir(db, ptf=2974.1, yekdem=364.0))
        assert kod == 422 and govde["error"]["code"] == "price_unverified"
        assert govde["error"]["blocking_reasons"] == ["confirmation_required"]
        assert db.query(Offer).count() == 0

    def test_ptf_eksik_422(self, db):
        kod, govde = _yanit(_teklif_cagir(db, ptf=None, yekdem=300.0, onay=True))
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["ptf_missing"]

    def test_yekdem_eksik_ve_haric_secilmemis_422(self, db):
        kod, govde = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=None, onay=True))
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["yekdem_missing"]

    def test_yekdem_sifir_onaysiz_422(self, db):
        _referans(db, 2500.0, 300.0, source="epias_manual")  # PTF sistemce doğrulanır
        kod, govde = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=0.0))
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["confirmation_required"]
        assert govde["error"]["price_provenance"]["yekdem"]["status"] == "zero"

    def test_acik_haric_kaydedilir_ve_gercek_sifirdan_ayrilir(self, db):
        from app.database import Offer

        kod, _ = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=None, onay=True, haric=True))
        assert kod == 200
        o = db.query(Offer).one()
        assert o.yekdem == 0.0
        yek = o.calculation_result["meta_price_provenance"]["yekdem"]
        assert yek["mode"] == "excluded" and yek["exclusion_basis"] == "user"

    def test_gercek_sifir_onayla_kaydedilir_haric_degil(self, db):
        from app.database import Offer

        kod, _ = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=0.0, onay=True))
        assert kod == 200
        prov = db.query(Offer).one().calculation_result["meta_price_provenance"]
        assert prov["yekdem"]["mode"] == "included" and prov["yekdem"]["value"] == 0.0
        assert prov["yekdem"]["status"] == "zero" and prov["verified_by"] == "user"

    def test_guvenilir_db_fiyati_onaysiz_kaydedilir_donem_ve_kaynak_snapshotta(self, db):
        from app.database import Offer

        _referans(db, 2508.8, 235.63, source="manual_override")
        kod, _ = _yanit(_teklif_cagir(db, ptf=2508.8, yekdem=235.63))
        assert kod == 200
        prov = db.query(Offer).one().calculation_result["meta_price_provenance"]
        assert prov["period"] == PERIOD and prov["verified_by"] == "system"
        assert prov["ptf"]["source"] == "manual_override" and prov["user_confirmed"] is False

    def test_istemcinin_provenance_iddiasi_ezilir(self, db):
        from app.database import Offer

        sahte = {"version": 1, "verified": True, "epias_basis": True, "verified_by": "system"}
        kod, _ = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=300.0, onay=True,
                                      calc_ek={"meta_price_provenance": sahte}))
        assert kod == 200
        prov = db.query(Offer).one().calculation_result["meta_price_provenance"]
        assert prov["epias_basis"] is False and prov["verified_by"] == "user"
        assert prov["system_lookup"] == "ok"

    def test_http_sozlesmesi_query_param_adlari(self, client, db):
        """Ret yolları TestClient ile: yeni query param'lar HTTP'den gerçekten okunuyor."""
        from app.database import Offer

        govde = {
            "extraction": {
                "invoice_period": PERIOD,
                "consumption_kwh": {"value": 1000.0, "confidence": 1.0},
                "current_active_unit_price_tl_per_kwh": {"value": 2.0, "confidence": 1.0},
            },
            "calculation": HESAP_SONUCU,
            "params": {"weighted_ptf_tl_per_mwh": 2500.0, "agreement_multiplier": 1.01},
        }
        r = client.post("/offers", json=govde,
                        params={"invoice_total_raw": "2880", "price_confirmed_by_user": "true"})
        assert r.status_code == 422 and r.json()["error"]["blocking_reasons"] == ["yekdem_missing"]
        r2 = client.post("/offers", json=govde,
                         params={"invoice_total_raw": "2880", "yekdem_excluded": "true"})
        assert r2.status_code == 422
        assert r2.json()["error"]["blocking_reasons"] == ["confirmation_required"]
        assert db.query(Offer).count() == 0


# ═══════════════════════════════════════════════════════════════════════════
# 4) PDF uçları — doğrudan API denemesi sunucuda reddedilir
# ═══════════════════════════════════════════════════════════════════════════

class TestPdfKapilari:
    def test_eski_teklif_provenance_yok_409_uretici_cagrilmaz(self, client, db):
        o = _kayitli_teklif(db)
        with patch("app.pdf_generator.generate_and_store_offer_pdf") as uretici:
            r = client.post(f"/offers/{o.id}/generate-pdf")
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "price_unverified"
        uretici.assert_not_called()
        db.refresh(o)
        assert o.pdf_ref is None

    def test_dogrulanmamis_snapshot_409(self, client, db):
        o = _kayitli_teklif(db, {"version": 1, "verified": False})
        with patch("app.pdf_generator.generate_and_store_offer_pdf") as uretici:
            r = client.post(f"/offers/{o.id}/generate-pdf")
        assert r.status_code == 409
        uretici.assert_not_called()

    def test_dogrulanmis_snapshot_uretir(self, client, db):
        from app.price_provenance import build_price_provenance

        prov = build_price_provenance(None, period=PERIOD, ptf=2500.0, yekdem=50.0,
                                      yekdem_excluded=False, user_confirmed=True)
        o = _kayitli_teklif(db, prov)
        cagrilar = []

        def sahte(**kw):
            cagrilar.append(kw)
            from app.services.storage import get_storage
            return get_storage().put_bytes(f"offers/{kw['offer_id']}/offer.pdf",
                                           b"%PDF-sentetik-teklif", "application/pdf")

        with patch("app.pdf_generator.generate_and_store_offer_pdf", side_effect=sahte):
            r = client.post(f"/offers/{o.id}/generate-pdf")
        assert r.status_code == 200 and len(cagrilar) == 1
        assert cagrilar[0]["calculation"].meta_price_provenance["verified"] is True

    def test_simple_ptf_gonderilmezse_varsayilan_yok(self, client):
        r = client.post("/generate-pdf-simple", data=PDF_FORM)
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_ptf"

    def test_simple_yekdem_gonderilmezse_varsayilan_yok(self, client):
        r = client.post("/generate-pdf-simple",
                        data={**PDF_FORM, "weighted_ptf_tl_per_mwh": "2500",
                              "price_confirmed_by_user": "true"})
        assert r.status_code == 422
        assert r.json()["error"]["blocking_reasons"] == ["yekdem_missing"]

    def test_simple_onaysiz_422(self, client):
        r = client.post("/generate-pdf-simple",
                        data={**PDF_FORM, "weighted_ptf_tl_per_mwh": "2500",
                              "yekdem_tl_per_mwh": "300"})
        assert r.status_code == 422
        assert r.json()["error"]["blocking_reasons"] == ["confirmation_required"]

    def test_simple_onayli_uretir_epias_iddiasi_yok(self, client):
        from app.pdf_generator import generate_offer_pdf_bytes as gercek

        yakalanan = {}

        def sarmal(*a, **kw):
            yakalanan["calc"], yakalanan["params"] = a[1], a[2]
            return gercek(*a, **kw)

        with patch("app.main.generate_offer_pdf_bytes", side_effect=sarmal):
            r = client.post("/generate-pdf-simple",
                            data={**PDF_FORM, "weighted_ptf_tl_per_mwh": "2500",
                                  "yekdem_excluded": "true", "price_confirmed_by_user": "true"})
        assert r.status_code == 200
        prov = yakalanan["calc"].meta_price_provenance
        assert prov["epias_basis"] is False and prov["yekdem"]["mode"] == "excluded"
        assert yakalanan["params"].yekdem_tl_per_mwh == 0.0

    def test_direct_onaysiz_422_uretici_cagrilmaz(self, client):
        govde = {"extraction": {"invoice_period": PERIOD}, "calculation": HESAP_SONUCU,
                 "params": {"weighted_ptf_tl_per_mwh": 2500.0, "yekdem_tl_per_mwh": 300.0,
                            "agreement_multiplier": 1.01}}
        with patch("app.main.generate_offer_pdf") as uretici:
            r = client.post("/generate-pdf-direct", json=govde)
        assert r.status_code == 422 and r.json()["error"]["code"] == "price_unverified"
        uretici.assert_not_called()

    def test_direct_istemci_provenance_iddiasi_ezilir(self, client, tmp_path):
        sahte_prov = {"version": 1, "verified": True, "epias_basis": True}
        govde = {"extraction": {"invoice_period": PERIOD},
                 "calculation": {**HESAP_SONUCU, "meta_price_provenance": sahte_prov},
                 "params": {"weighted_ptf_tl_per_mwh": 2500.0, "yekdem_tl_per_mwh": 300.0,
                            "agreement_multiplier": 1.01}}
        yakalanan = {}

        def sahte(extraction, calculation, params, **kw):
            yakalanan["calc"] = calculation
            dosya = tmp_path / "teklif.pdf"
            dosya.write_bytes(b"%PDF-1.4 sentetik")
            return str(dosya)

        with patch("app.main.generate_offer_pdf", side_effect=sahte):
            r = client.post("/generate-pdf-direct?price_confirmed_by_user=true", json=govde)
        assert r.status_code == 200
        prov = yakalanan["calc"].meta_price_provenance
        assert prov["epias_basis"] is False and prov["system_lookup"] == "unavailable"


# ═══════════════════════════════════════════════════════════════════════════
# 5) PDF metni — doğrulanmamış fiyat "EPİAŞ verisi" diye sunulmaz
# ═══════════════════════════════════════════════════════════════════════════

class TestPdfMetni:
    def test_dogrulanmamis_fiyatta_epias_iddiasi_yok(self):
        from app.pdf_generator import enerji_bedeli_paragraflari

        metin = " ".join(enerji_bedeli_paragraflari(
            {"epias_basis": False, "yekdem_excluded": False, "ptf_reference_scalar": True}, 1.06))
        assert "EPİAŞ" not in metin and "1.06" in metin

    def test_epias_dogrulanmis_metin_eskisiyle_birebir(self):
        from app.pdf_generator import enerji_bedeli_paragraflari

        paragraflar = enerji_bedeli_paragraflari(
            {"epias_basis": True, "yekdem_excluded": False, "ptf_reference_scalar": False}, 1.01)
        assert paragraflar == [
            "Enerji bedeli, EPİAŞ verileri esas alınarak oluşturulmaktadır. İlgili fatura dönemi için "
            "EPİAŞ saatlik PTF ile abonenin tüketim değerleri kullanılarak Ağırlıklı PTF hesaplanır. "
            "Üzerine YEKDEM birim bedeli eklenerek toplam enerji birim maliyeti oluşturulur. Bu maliyet, "
            "anlaşma fiyat katsayısı (<b>1.01</b>) ile çarpılarak nihai enerji bedeline ulaşılır."
        ]

    def test_yekdem_haric_metni(self):
        from app.pdf_generator import enerji_bedeli_paragraflari, yekdem_uygulamasi_metni

        bayrak = {"epias_basis": False, "yekdem_excluded": True, "ptf_reference_scalar": False}
        assert "dahil edilmemiştir" in yekdem_uygulamasi_metni(bayrak)
        assert "YEKDEM" not in enerji_bedeli_paragraflari(bayrak, 1.0)[0]

    def test_provenance_yoksa_epias_false(self):
        from app.models import CalculationResult
        from app.pdf_generator import fiyat_kaynagi_bayraklari

        assert fiyat_kaynagi_bayraklari(CalculationResult(**HESAP_SONUCU))["epias_basis"] is False

    def test_html_sablonu_kaynaga_gore(self):
        from app.models import CalculationResult, FieldValue, InvoiceExtraction, OfferParams
        from app.pdf_generator import generate_offer_html

        ext = InvoiceExtraction(invoice_period=PERIOD,
                                consumption_kwh=FieldValue(value=1000.0, confidence=1.0))
        params = OfferParams(weighted_ptf_tl_per_mwh=2500.0, yekdem_tl_per_mwh=300.0)
        dogrulanmamis = generate_offer_html(ext, CalculationResult(**HESAP_SONUCU), params)
        assert "EPİAŞ verileri esas alınarak" not in dogrulanmamis
        epias_prov = {"verified": True, "epias_basis": True,
                      "ptf": {"source": "hourly_weighted:duz"}, "yekdem": {"mode": "included"}}
        dogrulanmis = generate_offer_html(
            ext, CalculationResult(**HESAP_SONUCU, meta_price_provenance=epias_prov), params)
        assert "EPİAŞ verileri esas alınarak" in dogrulanmis


# ═══════════════════════════════════════════════════════════════════════════
# 6) Ortak fiyat yazımı — mevcut yetki; EPİAŞ senkronu kapalı
# ═══════════════════════════════════════════════════════════════════════════

class TestOrtakFiyatYazimi:
    def test_admin_yetkisi_uygulanir(self, client, monkeypatch):
        import app.main as m

        monkeypatch.setattr(m, "ADMIN_API_KEY_ENABLED", True)
        monkeypatch.setattr(m, "ADMIN_API_KEY", "test-anahtar")
        govde = {"ptf_tl_per_mwh": 2500.0, "yekdem_tl_per_mwh": 300.0}
        assert client.post(f"/api/epias/prices/{PERIOD}", json=govde).status_code == 401
        r = client.post(f"/api/epias/prices/{PERIOD}", json=govde,
                        headers={"X-Admin-Key": "test-anahtar"})
        assert r.status_code == 200

    def test_ptf_guncellemesi_yekdemi_sifirla_ezmez(self, client, db):
        from app.database import MarketReferencePrice

        _referans(db, 2500.0, 300.0, source="manual_override")
        r = client.post(f"/api/epias/prices/{PERIOD}", json={"ptf_tl_per_mwh": 2600.0})
        assert r.status_code == 200
        kayit = db.query(MarketReferencePrice).filter_by(period=PERIOD).one()
        db.refresh(kayit)
        assert kayit.ptf_tl_per_mwh == 2600.0 and kayit.yekdem_tl_per_mwh == 300.0

    def test_yeni_donem_yekdemsiz_422_kayit_yok(self, client, db):
        from app.database import MarketReferencePrice

        r = client.post(f"/api/epias/prices/{PERIOD}", json={"ptf_tl_per_mwh": 2600.0})
        assert r.status_code == 422
        assert db.query(MarketReferencePrice).count() == 0

    def test_epias_senkronu_kapali_mock_bile_yazmaz(self, client, db):
        from app.database import MarketReferencePrice

        r = client.post(f"/api/epias/sync/{PERIOD}?use_mock=true")
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "epias_integration_disabled"
        assert db.query(MarketReferencePrice).count() == 0
        assert client.post("/admin/epias/sync-all?force_refresh=true").status_code == 503

    def test_epias_senkronu_yetkisiz_401(self, client, monkeypatch):
        import app.main as m

        monkeypatch.setattr(m, "ADMIN_API_KEY_ENABLED", True)
        monkeypatch.setattr(m, "ADMIN_API_KEY", "test-anahtar")
        assert client.post(f"/api/epias/sync/{PERIOD}").status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# 7) Mevcut ağırlıklı PTF öncelikleri — kapı zinciri birebir izler
# ═══════════════════════════════════════════════════════════════════════════

class TestOnceliklerKorunur:
    def test_saatlik_varken_referans_skaler_sistemce_dogrulanmaz(self, db):
        from app.price_provenance import build_price_provenance

        _saatlik(db)
        _referans(db, 900.0, 300.0, source="epias_manual")
        prov = build_price_provenance(db, period=PERIOD, ptf=900.0, yekdem=300.0,
                                      yekdem_excluded=False, user_confirmed=False)
        assert prov["ptf"]["status"] == "user_entered", "sistem saatliği seçerdi"
        prov2 = build_price_provenance(db, period=PERIOD, ptf=PUANT, yekdem=300.0,
                                       yekdem_excluded=False, user_confirmed=False)
        assert prov2["ptf"]["source"] == "hourly_weighted:puant_agir"
        assert prov2["verified"] is True and prov2["epias_basis"] is True

    def test_manual_override_saatligi_kaynakta_da_ezer(self, db):
        from app.price_provenance import build_price_provenance

        _saatlik(db)
        _referans(db, 1234.5, 300.0, source="manual_override")
        prov = build_price_provenance(db, period=PERIOD, ptf=PUANT, yekdem=300.0,
                                      yekdem_excluded=False, user_confirmed=False)
        assert prov["ptf"]["status"] == "user_entered", "sistem manual_override'ı seçerdi"
        prov2 = build_price_provenance(db, period=PERIOD, ptf=1234.5, yekdem=300.0,
                                       yekdem_excluded=False, user_confirmed=False)
        assert prov2["ptf"]["source"] == "manual_override" and prov2["verified"] is True

    def test_snapshot_dogrulamasi_surum_ve_bayrak_ister(self):
        from app.price_provenance import snapshot_price_verified

        assert not snapshot_price_verified({})
        assert not snapshot_price_verified({"meta_price_provenance": {"verified": True}})
        assert snapshot_price_verified({"meta_price_provenance": {"version": 1, "verified": True}})


class TestSabitTohumlamaYok:
    def test_acilista_ornek_fiyat_ekleyen_fonksiyon_yok(self):
        import app.main as m

        assert not hasattr(m, "_add_sample_market_prices")
