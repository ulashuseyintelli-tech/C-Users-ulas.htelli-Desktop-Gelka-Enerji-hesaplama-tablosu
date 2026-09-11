"""
Fiyat Doğruluğu Faz 1 — eksik, doğrulanmamış ya da kesinleşmemiş PTF/YEKDEM ile
teklif KESİNLEŞMEZ.

Owner GO + teyit kapsamı (hedefli):
- eksik veri (PTF/YEKDEM kaydı yok) → null/bilinmiyor; varsayılan sabit YOK; 0 yazılmaz
- gerçek sıfır (saatlik 0 TL/MWh saatleri; YEKDEM 0) eksikten ve hariçten AYRI. Kayıtlı
  YEKDEM 0 YALNIZ yetkili yönetim ekranından açıkça girilip kesinleşmişse (geçmişteki
  açık sıfır satırı) doğrulanır; açık giriş kaydı olmayan eski 0 otomatik gerçek ya da
  eksik sayılmaz ve kesinleşemez
- açıkça seçilen "YEKDEM hariç" gerçek 0'dan AYRI; doğrulanmış muafiyet kuralı olmadığı
  için "muaf" seçimi YOK (tanınmayan seçim → yekdem_mode_invalid)
- provisional kayıt yalnız TASLAK hesapta; POST /offers ve PDF uçları reddeder
- doğrulama YALNIZ sunucuda: istemcinin "doğrulandı" beyanı (price_confirmed_by_user)
  hiçbir kapıyı açmaz
- API'den doğrudan kayıt/belge denemesi (POST /offers, /offers/{id}/generate-pdf,
  /offers/{id}/generate-html, /generate-pdf-simple, /generate-pdf-direct,
  /generate-html-direct) sunucuda reddedilir
- mevcut ağırlıklı PTF öncelikleri korunur (manual_override > tüketim > saatlik > referans)
- ortak fiyat yazımında mevcut admin yetkisi; ana ekran kaydı yalnız TASLAK yazar ve
  kesin kaydı değiştiremez; yeni dönem YEKDEM'siz oluşturulamaz; EPİAŞ çekimi kapalı
Dönem yarışı frontend testindedir: frontend/src/pricing/__tests__/usePeriodPriceFetch.test.tsx

Ağ YOK: EPİAŞ istemcisine her erişim testi patlatır (autouse). Veritabanı in-memory
SQLite. Başarılı POST /offers TestClient altında asılı kalabildiği için (bkz.
test_s5_r01_offer_pdf.py harness notu) kalıcı kayıt yolu uç fonksiyonu doğrudan
çağrılarak sınanır; HTTP sözleşmesi (query param adları) ret yollarında TestClient ile.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

PERIOD = "2099-01"
DATE = "2099-01-01"
# Yönetim doğrulayıcısı gelecek dönemi reddeder (FUTURE_PERIOD); yönetim yolu testleri
# geçmiş bir dönem kullanır.
GECMIS_DONEM = "2024-03"
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
# Hariç seçiminde teklif hesabında YEKDEM tutarı olamaz (seçim–hesap tutarlılığı).
HARIC_HESAP = {"offer_yekdem_tl": 0.0}

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


def _referans(db, ptf, yekdem, source="epias_manual", note=None, period=PERIOD, status="final"):
    """Dönem referans kaydı. Varsayılan: güvenilir kaynak + KESİN (final)."""
    from app.database import MarketReferencePrice

    db.add(MarketReferencePrice(period=period, price_type="PTF", ptf_tl_per_mwh=ptf,
                                yekdem_tl_per_mwh=yekdem, source=source, source_note=note,
                                is_locked=0, status=status))
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


def _teklif_cagir(db, *, ptf, yekdem, mod=None, period=PERIOD, calc_ek=None):
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
        yekdem_mode=mod, db=db,
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


def _kesin_provenance(db, ptf=2500.0, yekdem=50.0, source="epias_manual"):
    """Dönemin güvenilir + KESİN kaydı yazılır; provenance GERÇEK fonksiyonla hesaplanır."""
    from app.price_provenance import build_price_provenance

    _referans(db, ptf, yekdem, source=source)
    return build_price_provenance(db, period=PERIOD, ptf=ptf, yekdem=yekdem,
                                  yekdem_mode="included")


# ═══════════════════════════════════════════════════════════════════════════
# 1) GET /api/epias/prices — eksik veri, gerçek sıfır, kaynak güveni, provisional
# ═══════════════════════════════════════════════════════════════════════════

class TestDonemFiyatiOkuma:
    def test_kayit_yoksa_null_ve_varsayilan_sabit_yok(self, client):
        r = client.get(f"/api/epias/prices/{PERIOD}?auto_fetch=true&profile=duz")
        assert r.status_code == 200
        b = r.json()
        assert b["ptf_tl_per_mwh"] is None and b["yekdem_tl_per_mwh"] is None
        assert b["weighted_ptf_tl_per_mwh"] is None
        assert b["source"] == "not_found" and b["yekdem_status"] == "missing"
        assert b["weighted_ptf_source"] == "not_found" and b["record_status"] is None
        prov = b["price_provenance"]
        assert prov["verified"] is False and prov["draft_only"] is True
        assert prov["blocking_reasons"] == ["ptf_missing", "yekdem_missing"]
        assert "2974" not in r.text and "364" not in r.text, "varsayılan sabit sızdı"

    def test_saatlik_var_referans_yok_yekdem_bilinmiyor_sifir_degil(self, client, db):
        _saatlik(db)
        b = client.get(f"/api/epias/prices/{PERIOD}?profile=duz").json()
        assert b["weighted_ptf_tl_per_mwh"] == pytest.approx(DUZ)
        assert b["weighted_ptf_source"] == "hourly_weighted:duz"
        assert b["ptf_source_warning"] is None
        assert b["yekdem_tl_per_mwh"] is None and b["yekdem_status"] == "missing"
        prov = b["price_provenance"]
        assert prov["ptf"]["system_verified"] is True and prov["ptf"]["final"] is True
        assert prov["ptf"]["epias"] is True
        assert prov["blocking_reasons"] == ["yekdem_missing"]

    def test_saatlik_gercek_sifir_saatleri_eksik_sayilmaz(self, client, db):
        _saatlik(db, [(10, 0.0), (19, 3000.0), (2, 0.0)])
        b = client.get(f"/api/epias/prices/{PERIOD}?profile=duz").json()
        assert b["weighted_ptf_tl_per_mwh"] == pytest.approx(1000.0), "0 TL/MWh saatler ortalamaya girer"

    def test_db_yekdem_sifir_deger_olarak_tasinir_ve_kesinlesemez(self, client, db):
        _referans(db, 2500.0, 0.0)  # güvenilir + final; açık giriş kaydı yok (eski sıfır)
        b = client.get(f"/api/epias/prices/{PERIOD}?profile=duz").json()
        assert b["yekdem_tl_per_mwh"] == 0.0, "0 değeri null'a çevrilmemeli (eksik sayılmaz)"
        assert b["yekdem_status"] == "zero_unverified"
        prov = b["price_provenance"]
        assert prov["ptf"]["system_verified"] is True
        assert prov["yekdem"]["status"] == "zero_unverified" and prov["yekdem"]["value"] == 0.0
        assert prov["yekdem"]["period_verified"] is False
        assert prov["yekdem"]["period_zero_audit_id"] is None
        assert prov["blocking_reasons"] == ["yekdem_zero_unverified"]

    def test_gelistirme_ornek_verisi_dogrulanmis_sayilmaz(self, client, db):
        _referans(db, 2500.0, 300.0, source="epias_manual", note="Sample data (dev)")
        prov = client.get(f"/api/epias/prices/{PERIOD}").json()["price_provenance"]
        assert prov["ptf"]["system_verified"] is False and prov["ptf"]["epias"] is False
        assert prov["blocking_reasons"] == ["ptf_unverified", "yekdem_unverified"]

    def test_kesin_manuel_kayit_dogrulanir_ama_epias_degil(self, client, db):
        _referans(db, 2508.8, 235.63, source="manual_override")
        prov = client.get(f"/api/epias/prices/{PERIOD}").json()["price_provenance"]
        assert prov["verified"] is True and prov["verified_by"] == "system"
        assert prov["draft_only"] is False
        assert prov["epias_basis"] is False, "manual_override EPİAŞ etiketi taşımaz"

    def test_epias_etiketli_kesin_kayit_epias_temelli(self, client, db):
        _referans(db, 2508.8, 235.63, source="epias_manual")
        prov = client.get(f"/api/epias/prices/{PERIOD}").json()["price_provenance"]
        assert prov["verified"] is True and prov["epias_basis"] is True

    def test_provisional_kayit_degerleri_tasir_ama_yalniz_taslak(self, client, db):
        _referans(db, 2508.8, 235.63, source="manual_override", status="provisional")
        b = client.get(f"/api/epias/prices/{PERIOD}").json()
        assert b["weighted_ptf_tl_per_mwh"] == pytest.approx(2508.8)
        assert b["yekdem_tl_per_mwh"] == pytest.approx(235.63)
        assert b["record_status"] == "provisional"
        prov = b["price_provenance"]
        assert prov["verified"] is False and prov["draft_only"] is True
        assert prov["provisional"] is True
        assert prov["blocking_reasons"] == ["ptf_provisional", "yekdem_provisional"]

    def test_admin_donem_okuma_varsayilan_uretmez(self, client):
        """GET /admin/market-prices/{period}: kayıt yoksa null + not_found (eski: 2974.1/364.0 'default')."""
        r = client.get(f"/admin/market-prices/{PERIOD}")
        assert r.status_code == 200
        b = r.json()
        assert b["ptf_tl_per_mwh"] is None and b["yekdem_tl_per_mwh"] is None
        assert b["source"] == "not_found"


# ═══════════════════════════════════════════════════════════════════════════
# 2) AI yolu (calculate_offer) — sessiz YEKDEM=0 ve tahmini "hariç" kapalı
# ═══════════════════════════════════════════════════════════════════════════

class TestAIHesapYolu:
    def test_faturada_yekdem_var_donem_yekdemi_yok_fail_closed(self, db):
        from app.calculator import CalculationError, calculate_offer
        from app.models import OfferParams

        _saatlik(db)
        with pytest.raises(CalculationError, match="YEKDEM"):
            calculate_offer(_fatura(yek_tl=120.0), OfferParams(), db=db)

    def test_faturada_yekdem_yok_secim_bos_haric_tahmin_edilmez(self, db):
        from app.calculator import calculate_offer
        from app.models import OfferParams

        _saatlik(db)
        sonuc = calculate_offer(_fatura(), OfferParams(), db=db)
        assert sonuc.offer_yekdem_tl == 0  # taslak hesap YEKDEM'siz gösterilir
        prov = sonuc.meta_price_provenance
        assert prov["yekdem"]["mode"] is None and prov["yekdem"]["status"] == "mode_required"
        assert prov["blocking_reasons"] == ["yekdem_mode_required"]
        assert prov["draft_only"] is True

    def test_override_yekdem_verilmezse_sessiz_sifir_yok(self, db):
        from app.calculator import CalculationError, calculate_offer
        from app.models import OfferParams

        params = OfferParams(use_reference_prices=False, weighted_ptf_tl_per_mwh=2000.0)
        with pytest.raises(CalculationError, match="YEKDEM"):
            calculate_offer(_fatura(yek_tl=120.0), params, db=db)

    def test_override_sifir_yekdem_deger_olarak_kalir_kayitsiz_dogrulanmaz(self, db):
        from app.calculator import calculate_offer
        from app.models import OfferParams

        params = OfferParams(use_reference_prices=False, weighted_ptf_tl_per_mwh=2000.0,
                             yekdem_tl_per_mwh=0.0)
        sonuc = calculate_offer(_fatura(yek_tl=120.0), params, db=db)
        prov = sonuc.meta_price_provenance
        yek = prov["yekdem"]
        # Kullanıcının 0'ı değer olarak korunur (null/hariç değil); dönem kaydı yoksa diğer
        # her kullanıcı değeri gibi doğrulanmaz.
        assert yek["mode"] == "included" and yek["status"] == "user_entered" and yek["value"] == 0.0
        assert "yekdem_unverified" in prov["blocking_reasons"]
        assert prov["verified"] is False

    def test_kesin_db_fiyati_ai_yolunda_dogrulanir(self, db):
        from app.calculator import calculate_offer
        from app.models import OfferParams

        _referans(db, 2508.8, 235.63, source="epias_manual")  # saatlik yok → referans skaler
        sonuc = calculate_offer(_fatura(yek_tl=120.0), OfferParams(), db=db)
        prov = sonuc.meta_price_provenance
        assert sonuc.meta_pricing_source == "reference_scalar"
        assert prov["verified"] is True and prov["epias_basis"] is True
        assert prov["yekdem"]["mode"] == "included" and prov["yekdem"]["mode_basis"] == "invoice"

    def test_provisional_db_fiyati_ai_yolunda_yalniz_taslak_hesap(self, db):
        from app.calculator import calculate_offer
        from app.models import OfferParams

        _referans(db, 2508.8, 235.63, source="epias_manual", status="provisional")
        sonuc = calculate_offer(_fatura(yek_tl=120.0), OfferParams(), db=db)
        assert sonuc.offer_yekdem_tl > 0  # taslak hesap provisional değerle YAPILIR
        prov = sonuc.meta_price_provenance
        assert prov["verified"] is False and prov["provisional"] is True
        assert prov["blocking_reasons"] == ["ptf_provisional", "yekdem_provisional"]


# ═══════════════════════════════════════════════════════════════════════════
# 3) POST /offers — sunucu kapısı + snapshot'ta dönem/kaynak/durum/YEKDEM seçimi
# ═══════════════════════════════════════════════════════════════════════════

class TestTeklifKaydiKapisi:
    def test_dogrulanmamis_fiyat_422_ve_kayit_yok(self, db):
        from app.database import Offer

        kod, govde = _yanit(_teklif_cagir(db, ptf=2974.1, yekdem=364.0))
        assert kod == 422 and govde["error"]["code"] == "price_unverified"
        assert govde["error"]["blocking_reasons"] == ["ptf_unverified", "yekdem_unverified"]
        assert db.query(Offer).count() == 0

    def test_ptf_eksik_422(self, db):
        _referans(db, 2500.0, 300.0)
        kod, govde = _yanit(_teklif_cagir(db, ptf=None, yekdem=300.0))
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["ptf_missing"]

    def test_yekdem_eksik_ve_secim_dahil_422(self, db):
        _referans(db, 2500.0, 300.0)
        kod, govde = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=None))
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["yekdem_missing"]
        assert govde["error"]["price_provenance"]["yekdem"]["mode_basis"] == "default"

    def test_kayitla_eslesmeyen_sifir_yekdem_dogrulanmaz_haric_degil(self, db):
        from app.database import Offer

        _referans(db, 2500.0, 300.0)
        kod, govde = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=0.0, mod="included"))
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["yekdem_unverified"]
        yek = govde["error"]["price_provenance"]["yekdem"]
        assert yek["mode"] == "included" and yek["value"] == 0.0  # 0 korunur, hariç'e dönmez
        assert yek["status"] == "user_entered"
        assert db.query(Offer).count() == 0

    def test_acik_giris_kaydi_olmayan_kayitli_sifir_kesinlesemez(self, db):
        _referans(db, 2500.0, 0.0)  # güvenilir + final; açık giriş kaydı yok (eski sıfır)
        kod, govde = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=0.0))
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["yekdem_zero_unverified"]

    def test_acik_haric_kaydedilir_ve_gercek_sifirdan_ayrilir(self, db):
        from app.database import Offer

        _referans(db, 2500.0, 300.0)
        kod, _ = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=None, mod="excluded",
                                      calc_ek=HARIC_HESAP))
        assert kod == 200
        o = db.query(Offer).one()
        assert o.yekdem == 0.0  # uygulanan YEKDEM; hariç ile gerçek 0 ayrımı snapshot'ta
        yek = o.calculation_result["meta_price_provenance"]["yekdem"]
        assert yek["mode"] == "excluded" and yek["mode_basis"] == "user"
        assert yek["value"] is None and yek["status"] == "excluded"

    def test_muaf_secimi_yok_gecersiz_secim_422_kayit_yok(self, db):
        """Doğrulanmış muafiyet kuralı yok: 'exempt' tanınmayan seçimdir (hariç'e çevrilmez)."""
        from app.database import Offer

        _referans(db, 2500.0, 300.0)
        kod, govde = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=None, mod="exempt",
                                          calc_ek=HARIC_HESAP))
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["yekdem_mode_invalid"]
        assert govde["error"]["price_provenance"]["yekdem"]["status"] == "mode_invalid"
        assert db.query(Offer).count() == 0

    def test_haric_secip_hesapta_yekdem_birakmak_celiski_422(self, db):
        from app.database import Offer

        _referans(db, 2500.0, 300.0)
        # HESAP_SONUCU offer_yekdem_tl=50 → "hariç" seçimiyle çelişir
        kod, govde = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=300.0, mod="excluded"))
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["yekdem_mode_conflict"]
        assert db.query(Offer).count() == 0

    def test_gecersiz_yekdem_secimi_422(self, db):
        _referans(db, 2500.0, 300.0)
        kod, govde = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=300.0, mod="belki"))
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["yekdem_mode_invalid"]

    def test_provisional_fiyat_kesin_teklif_olamaz_422(self, db):
        from app.database import Offer

        _referans(db, 2508.8, 235.63, source="manual_override", status="provisional")
        kod, govde = _yanit(_teklif_cagir(db, ptf=2508.8, yekdem=235.63))
        assert kod == 422
        assert govde["error"]["blocking_reasons"] == ["ptf_provisional", "yekdem_provisional"]
        assert govde["error"]["price_provenance"]["provisional"] is True
        assert db.query(Offer).count() == 0

    def test_provisional_ptf_haric_seciminde_de_engellenir(self, db):
        _referans(db, 2508.8, 235.63, source="manual_override", status="provisional")
        kod, govde = _yanit(_teklif_cagir(db, ptf=2508.8, yekdem=None, mod="excluded",
                                          calc_ek=HARIC_HESAP))
        assert kod == 422 and govde["error"]["blocking_reasons"] == ["ptf_provisional"]

    def test_kesin_db_fiyati_kaydedilir_donem_kaynak_durum_snapshotta(self, db):
        from app.database import Offer

        _referans(db, 2508.8, 235.63, source="manual_override")
        kod, _ = _yanit(_teklif_cagir(db, ptf=2508.8, yekdem=235.63, mod="included"))
        assert kod == 200
        o = db.query(Offer).one()
        prov = o.calculation_result["meta_price_provenance"]
        assert prov["version"] == 2 and prov["period"] == PERIOD
        assert prov["verified_by"] == "system" and prov["draft_only"] is False
        assert prov["ptf"]["source"] == "manual_override" and prov["ptf"]["final"] is True
        assert prov["yekdem"]["period_record_status"] == "final"
        assert "user_confirmed" not in prov and "requires_confirmation" not in prov
        assert o.yekdem == pytest.approx(235.63)

    def test_istemcinin_provenance_iddiasi_kapiyi_acmaz_ve_ezilir(self, db):
        from app.database import Offer

        sahte = {"version": 2, "verified": True, "epias_basis": True, "verified_by": "system"}
        kod, _ = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=300.0,
                                      calc_ek={"meta_price_provenance": sahte}))
        assert kod == 422, "istemcinin 'doğrulandı' iddiası kapıyı açmamalı"
        assert db.query(Offer).count() == 0
        _referans(db, 2500.0, 300.0, source="manual_override")
        kod, _ = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=300.0,
                                      calc_ek={"meta_price_provenance": sahte}))
        assert kod == 200
        prov = db.query(Offer).one().calculation_result["meta_price_provenance"]
        assert prov["epias_basis"] is False and prov["system_lookup"] == "ok"

    def test_http_istemci_onay_bayragi_kapiyi_acmaz(self, client, db):
        """Ret yolları TestClient ile: eski 'price_confirmed_by_user' beyanı hiçbir kapıyı açmaz."""
        from app.database import Offer

        govde = {
            "extraction": {
                "invoice_period": PERIOD,
                "consumption_kwh": {"value": 1000.0, "confidence": 1.0},
                "current_active_unit_price_tl_per_kwh": {"value": 2.0, "confidence": 1.0},
            },
            "calculation": {**HESAP_SONUCU, **HARIC_HESAP},
            "params": {"weighted_ptf_tl_per_mwh": 2500.0, "yekdem_tl_per_mwh": 300.0,
                       "agreement_multiplier": 1.01},
        }
        onay = {"invoice_total_raw": "2880", "price_confirmed_by_user": "true"}
        r = client.post("/offers", json=govde, params=onay)
        assert r.status_code == 422
        assert r.json()["error"]["blocking_reasons"] == ["ptf_unverified", "yekdem_unverified"]
        r2 = client.post("/offers", json=govde, params={**onay, "yekdem_mode": "excluded"})
        assert r2.status_code == 422
        assert r2.json()["error"]["blocking_reasons"] == ["ptf_unverified"]
        _referans(db, 2500.0, 300.0, source="manual_override", status="provisional")
        r3 = client.post("/offers", json=govde, params=onay)
        assert r3.status_code == 422
        assert r3.json()["error"]["blocking_reasons"] == ["ptf_provisional", "yekdem_provisional"]
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
        o = _kayitli_teklif(db, {"version": 2, "verified": False})
        with patch("app.pdf_generator.generate_and_store_offer_pdf") as uretici:
            r = client.post(f"/offers/{o.id}/generate-pdf")
        assert r.status_code == 409
        uretici.assert_not_called()

    def test_surum1_kullanici_onayli_snapshot_409(self, client, db):
        """Sürüm 1 snapshot'ı kullanıcı onayıyla 'doğrulanmış' olabilir → PDF yok."""
        o = _kayitli_teklif(db, {"version": 1, "verified": True, "verified_by": "user"})
        with patch("app.pdf_generator.generate_and_store_offer_pdf") as uretici:
            r = client.post(f"/offers/{o.id}/generate-pdf")
        assert r.status_code == 409
        uretici.assert_not_called()

    def test_dogrulanmis_snapshot_uretir(self, client, db):
        prov = _kesin_provenance(db)
        assert prov["verified"] is True
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

    def test_simple_yekdem_gonderilmezse_varsayilan_yok(self, client, db):
        _referans(db, 2500.0, 300.0)
        r = client.post("/generate-pdf-simple", data={**PDF_FORM, "weighted_ptf_tl_per_mwh": "2500"})
        assert r.status_code == 422
        assert r.json()["error"]["blocking_reasons"] == ["yekdem_missing"]

    def test_simple_dogrulanmamis_fiyat_onay_bayragiyla_da_422(self, client):
        with patch("app.main.generate_offer_pdf_bytes") as uretici:
            r = client.post("/generate-pdf-simple",
                            data={**PDF_FORM, "weighted_ptf_tl_per_mwh": "2500",
                                  "yekdem_tl_per_mwh": "300", "price_confirmed_by_user": "true"})
        assert r.status_code == 422
        assert r.json()["error"]["blocking_reasons"] == ["ptf_unverified", "yekdem_unverified"]
        uretici.assert_not_called()

    def test_simple_provisional_422(self, client, db):
        _referans(db, 2500.0, 300.0, status="provisional")
        with patch("app.main.generate_offer_pdf_bytes") as uretici:
            r = client.post("/generate-pdf-simple",
                            data={**PDF_FORM, "weighted_ptf_tl_per_mwh": "2500",
                                  "yekdem_tl_per_mwh": "300"})
        assert r.status_code == 422
        assert r.json()["error"]["blocking_reasons"] == ["ptf_provisional", "yekdem_provisional"]
        uretici.assert_not_called()

    def test_simple_kesin_fiyat_ve_haric_secimi_uretir_epias_iddiasi_yok(self, client, db):
        from app.pdf_generator import generate_offer_pdf_bytes as gercek

        _referans(db, 2500.0, 300.0, source="manual_override")
        yakalanan = {}

        def sarmal(*a, **kw):
            yakalanan["calc"], yakalanan["params"] = a[1], a[2]
            return gercek(*a, **kw)

        with patch("app.main.generate_offer_pdf_bytes", side_effect=sarmal):
            r = client.post("/generate-pdf-simple",
                            data={**PDF_FORM, "weighted_ptf_tl_per_mwh": "2500",
                                  "yekdem_mode": "excluded"})
        assert r.status_code == 200
        prov = yakalanan["calc"].meta_price_provenance
        assert prov["verified_by"] == "system" and prov["epias_basis"] is False
        assert prov["yekdem"]["mode"] == "excluded"
        assert yakalanan["params"].yekdem_tl_per_mwh == 0.0

    def test_simple_muaf_secimi_gecersiz_422_uretici_cagrilmaz(self, client, db):
        _referans(db, 2500.0, 300.0, source="epias_manual")
        with patch("app.main.generate_offer_pdf_bytes") as uretici:
            r = client.post("/generate-pdf-simple",
                            data={**PDF_FORM, "weighted_ptf_tl_per_mwh": "2500",
                                  "yekdem_mode": "exempt"})
        assert r.status_code == 422
        assert r.json()["error"]["blocking_reasons"] == ["yekdem_mode_invalid"]
        uretici.assert_not_called()

    def test_direct_dogrulanmamis_onay_bayragiyla_da_422_uretici_cagrilmaz(self, client):
        govde = {"extraction": {"invoice_period": PERIOD}, "calculation": HESAP_SONUCU,
                 "params": {"weighted_ptf_tl_per_mwh": 2500.0, "yekdem_tl_per_mwh": 300.0,
                            "agreement_multiplier": 1.01}}
        with patch("app.main.generate_offer_pdf") as uretici:
            r = client.post("/generate-pdf-direct?price_confirmed_by_user=true", json=govde)
        assert r.status_code == 422 and r.json()["error"]["code"] == "price_unverified"
        uretici.assert_not_called()

    def test_direct_istemci_provenance_iddiasi_ezilir(self, client, db, tmp_path):
        _referans(db, 2500.0, 300.0, source="manual_override")
        sahte_prov = {"version": 2, "verified": True, "epias_basis": True}
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
            r = client.post("/generate-pdf-direct", json=govde)
        assert r.status_code == 200
        prov = yakalanan["calc"].meta_price_provenance
        assert prov["epias_basis"] is False and prov["system_lookup"] == "ok"
        assert prov["verified_by"] == "system"

    def test_direct_haric_secip_hesapta_yekdem_birakmak_422(self, client, db):
        _referans(db, 2500.0, 300.0)
        govde = {"extraction": {"invoice_period": PERIOD}, "calculation": HESAP_SONUCU,
                 "params": {"weighted_ptf_tl_per_mwh": 2500.0, "yekdem_tl_per_mwh": 300.0,
                            "agreement_multiplier": 1.01}}
        with patch("app.main.generate_offer_pdf") as uretici:
            r = client.post("/generate-pdf-direct?yekdem_mode=excluded", json=govde)
        assert r.status_code == 422
        assert r.json()["error"]["blocking_reasons"] == ["yekdem_mode_conflict"]
        uretici.assert_not_called()


    def test_kayitli_teklif_html_dogrulanmamis_409(self, client, db):
        """HTML teklif belgesi de PDF ile AYNI snapshot kapısından geçer."""
        o = _kayitli_teklif(db)  # Faz 1 öncesi: provenance yok
        r = client.post(f"/offers/{o.id}/generate-html")
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "price_unverified"

    def test_kayitli_teklif_html_dogrulanmis_uretir(self, client, db):
        o = _kayitli_teklif(db, _kesin_provenance(db))
        r = client.post(f"/offers/{o.id}/generate-html")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]

    def test_html_direct_istemci_iddiasi_kapiyi_acmaz_epias_yazdirmaz(self, client, db):
        sahte_prov = {"version": 2, "verified": True, "epias_basis": True, "verified_by": "system"}
        govde = {"extraction": {"invoice_period": PERIOD,
                                "consumption_kwh": {"value": 1000.0, "confidence": 1.0}},
                 "calculation": {**HESAP_SONUCU, "meta_price_provenance": sahte_prov},
                 "params": {"weighted_ptf_tl_per_mwh": 2500.0, "yekdem_tl_per_mwh": 300.0,
                            "agreement_multiplier": 1.01}}
        r = client.post("/generate-html-direct?price_confirmed_by_user=true", json=govde)
        assert r.status_code == 422 and r.json()["error"]["code"] == "price_unverified"
        _referans(db, 2500.0, 300.0, source="manual_override")  # kesin ama EPİAŞ etiketli değil
        r2 = client.post("/generate-html-direct", json=govde)
        assert r2.status_code == 200
        assert "EPİAŞ verileri esas alınarak" not in r2.text, "istemcinin EPİAŞ iddiası belgeye sızdı"


# ═══════════════════════════════════════════════════════════════════════════
# 5) PDF metni — doğrulanmamış fiyat "EPİAŞ verisi" diye sunulmaz; hariç ≠ dahil; "muaf" yok
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

    def test_pdf_metninde_muaf_ifadesi_yok_haric_dahilden_ayri(self):
        """Doğrulanmış muafiyet kuralı yok: hiçbir seçimde "muaf" metni üretilmez."""
        from app.pdf_generator import enerji_bedeli_paragraflari, yekdem_uygulamasi_metni

        haric = {"epias_basis": False, "yekdem_excluded": True, "ptf_reference_scalar": False}
        dahil = {"epias_basis": False, "yekdem_excluded": False, "ptf_reference_scalar": False}
        h, d = yekdem_uygulamasi_metni(haric), yekdem_uygulamasi_metni(dahil)
        assert h != d and "dahil edilmemiştir" in h and "dahil edilmemiştir" not in d
        metinler = [h, d, *enerji_bedeli_paragraflari(haric, 1.0),
                    *enerji_bedeli_paragraflari(dahil, 1.0)]
        assert all("muaf" not in m.lower() for m in metinler)

    def test_bayraklar_secimden_turer_muaf_bayragi_yok(self):
        from app.models import CalculationResult
        from app.pdf_generator import fiyat_kaynagi_bayraklari

        for mod, haric in {"excluded": True, "included": False}.items():
            b = fiyat_kaynagi_bayraklari(CalculationResult(
                **HESAP_SONUCU, meta_price_provenance={"yekdem": {"mode": mod}}))
            assert b["yekdem_excluded"] is haric
            assert set(b) == {"epias_basis", "yekdem_excluded", "ptf_reference_scalar"}

    def test_provenance_yoksa_epias_false(self):
        from app.models import CalculationResult
        from app.pdf_generator import fiyat_kaynagi_bayraklari

        assert fiyat_kaynagi_bayraklari(CalculationResult(**HESAP_SONUCU))["epias_basis"] is False

    def test_html_sablonu_kaynaga_ve_secime_gore(self):
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

        sifir = OfferParams(weighted_ptf_tl_per_mwh=2500.0, yekdem_tl_per_mwh=0.0)
        haric = generate_offer_html(ext, CalculationResult(
            **HESAP_SONUCU, meta_price_provenance={"verified": True, "yekdem": {"mode": "excluded"}}), sifir)
        assert "dahil edilmemiştir" in haric and "<td>Dahil değil</td>" in haric
        assert "dahil edilmemiştir" not in dogrulanmis
        # (Tüm HTML'de küçük harf araması yapılmaz: gömülü antet base64'ü rastgele harf taşır.)
        for belge in (haric, dogrulanmis):
            assert "<td>Muaf</td>" not in belge and "muaf olduğu" not in belge

    def test_reportlab_pdf_haric_ve_dahil_metni_ayri_muaf_yok(self):
        pypdfium2 = pytest.importorskip("pypdfium2")
        from app.models import CalculationResult, FieldValue, InvoiceExtraction, OfferParams
        from app.pdf_generator import generate_offer_pdf_bytes

        ext = InvoiceExtraction(invoice_period=PERIOD,
                                consumption_kwh=FieldValue(value=1000.0, confidence=1.0))
        metinler = {}
        for mod, yekdem in (("excluded", 0.0), ("included", 300.0)):
            params = OfferParams(weighted_ptf_tl_per_mwh=2500.0, yekdem_tl_per_mwh=yekdem)
            calc = CalculationResult(**HESAP_SONUCU, meta_price_provenance={
                "verified": True, "epias_basis": False, "yekdem": {"mode": mod}})
            belge = pypdfium2.PdfDocument(generate_offer_pdf_bytes(ext, calc, params))
            ham = " ".join(belge[i].get_textpage().get_text_bounded() for i in range(len(belge)))
            metinler[mod] = " ".join(ham.split())
        assert "dahil edilmemiştir" in metinler["excluded"] and "Dahil değil" in metinler["excluded"]
        assert "dahil edilmemiştir" not in metinler["included"]
        assert all("muaf" not in m.lower() for m in metinler.values())


# ═══════════════════════════════════════════════════════════════════════════
# 6) Ana ekran fiyat kaydı (ortak yazım) — mevcut yetki; yalnız TASLAK; EPİAŞ kapalı
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

        _referans(db, 2500.0, 300.0, source="manual_override", status="provisional")
        r = client.post(f"/api/epias/prices/{PERIOD}", json={"ptf_tl_per_mwh": 2600.0})
        assert r.status_code == 200
        kayit = db.query(MarketReferencePrice).filter_by(period=PERIOD).one()
        db.refresh(kayit)
        assert kayit.ptf_tl_per_mwh == 2600.0 and kayit.yekdem_tl_per_mwh == 300.0
        assert kayit.status == "provisional"

    def test_ana_ekran_kaydi_yalniz_taslak_yazar(self, client, db):
        from app.database import MarketReferencePrice

        r = client.post(f"/api/epias/prices/{PERIOD}",
                        json={"ptf_tl_per_mwh": 2600.0, "yekdem_tl_per_mwh": 310.0})
        assert r.status_code == 200 and r.json()["record_status"] == "provisional"
        kayit = db.query(MarketReferencePrice).filter_by(period=PERIOD).one()
        assert kayit.status == "provisional" and kayit.source == "manual_override"
        prov = client.get(f"/api/epias/prices/{PERIOD}").json()["price_provenance"]
        assert prov["draft_only"] is True
        assert prov["blocking_reasons"] == ["ptf_provisional", "yekdem_provisional"]

    def test_ana_ekran_kaydi_kesin_kaydi_degistiremez_409(self, client, db):
        from app.database import MarketReferencePrice

        _referans(db, 2500.0, 300.0, source="epias_manual")
        r = client.post(f"/api/epias/prices/{PERIOD}",
                        json={"ptf_tl_per_mwh": 2600.0, "yekdem_tl_per_mwh": 300.0})
        assert r.status_code == 409
        r2 = client.post(f"/api/epias/prices/{PERIOD}",
                         json={"ptf_tl_per_mwh": 2500.0, "yekdem_tl_per_mwh": 310.0})
        assert r2.status_code == 409
        kayit = db.query(MarketReferencePrice).filter_by(period=PERIOD).one()
        db.refresh(kayit)
        assert (kayit.ptf_tl_per_mwh, kayit.yekdem_tl_per_mwh, kayit.status, kayit.source) == \
            (2500.0, 300.0, "final", "epias_manual")

    def test_ana_ekran_kaydi_kesin_kayitta_ayni_deger_islem_yok(self, client, db):
        from app.database import MarketReferencePrice

        _referans(db, 2500.0, 300.0, source="epias_manual")
        r = client.post(f"/api/epias/prices/{PERIOD}", json={"ptf_tl_per_mwh": 2500.0})
        assert r.status_code == 200 and r.json()["record_status"] == "final"
        kayit = db.query(MarketReferencePrice).filter_by(period=PERIOD).one()
        db.refresh(kayit)
        assert kayit.status == "final" and kayit.source == "epias_manual"

    def test_yeni_donem_yekdemsiz_422_kayit_yok(self, client, db):
        from app.database import MarketReferencePrice

        r = client.post(f"/api/epias/prices/{PERIOD}", json={"ptf_tl_per_mwh": 2600.0})
        assert r.status_code == 422
        assert db.query(MarketReferencePrice).count() == 0

    def test_ana_ekran_gercek_sifir_taslak_yazilir_acik_kayit_uretmez_negatif_422(self, client, db):
        """Owner teyidi: gerçek 0 yasak değil. Hızlı kayıt 0'ı yalnız TASLAK yazar ve açık
        sıfır satırı üretmez (kesin teyit Piyasa Fiyatları ekranında). Negatif → 422."""
        from app.database import MarketReferencePrice, PriceChangeHistory

        r = client.post(f"/api/epias/prices/{PERIOD}",
                        json={"ptf_tl_per_mwh": 2600.0, "yekdem_tl_per_mwh": 0})
        assert r.status_code == 200 and r.json()["record_status"] == "provisional"
        kayit = db.query(MarketReferencePrice).filter_by(period=PERIOD).one()
        assert (kayit.yekdem_tl_per_mwh, kayit.status) == (0.0, "provisional")
        assert db.query(PriceChangeHistory).count() == 0
        b = client.get(f"/api/epias/prices/{PERIOD}").json()
        assert b["yekdem_tl_per_mwh"] == 0.0 and b["yekdem_status"] == "zero_unverified"
        assert b["price_provenance"]["blocking_reasons"] == ["ptf_provisional", "yekdem_zero_unverified"]
        r2 = client.post(f"/api/epias/prices/{PERIOD}",
                         json={"ptf_tl_per_mwh": 2600.0, "yekdem_tl_per_mwh": -1})
        assert r2.status_code == 422
        db.refresh(kayit)
        assert kayit.yekdem_tl_per_mwh == 0.0

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
# 7) Yetkili yönetim yolu — yeni dönem YEKDEM ister; toplu içe aktarma 0 yazmaz
# ═══════════════════════════════════════════════════════════════════════════

class TestYonetimKaydiYekdem:
    def test_servis_yeni_ptf_kaydi_yekdemsiz_reddedilir_sifir_yazilmaz(self, db):
        from app.database import MarketReferencePrice
        from app.market_price_admin_service import MarketPriceAdminService, ServiceErrorCode
        from app.market_price_validator import NormalizedMarketPriceInput

        sonuc = MarketPriceAdminService().upsert_price(
            db, NormalizedMarketPriceInput(period=GECMIS_DONEM, value=Decimal("2500.00"),
                                           status="final"),
            updated_by="test", source="epias_manual")
        assert sonuc.success is False
        assert sonuc.error.error_code == ServiceErrorCode.YEKDEM_REQUIRED
        assert sonuc.error.field == "yekdem_value"
        assert db.query(MarketReferencePrice).count() == 0

    def test_servis_yekdemli_yeni_kayit_ve_ptf_only_guncelleme(self, db):
        from app.database import MarketReferencePrice
        from app.market_price_admin_service import MarketPriceAdminService
        from app.market_price_validator import NormalizedMarketPriceInput

        servis = MarketPriceAdminService()
        ilk = servis.upsert_price(
            db, NormalizedMarketPriceInput(period=GECMIS_DONEM, value=Decimal("2500.00"),
                                           status="provisional", yekdem_value=Decimal("300.00")),
            updated_by="test", source="epias_manual")
        assert ilk.success and ilk.created
        guncel = servis.upsert_price(
            db, NormalizedMarketPriceInput(period=GECMIS_DONEM, value=Decimal("2550.00"),
                                           status="final"),
            updated_by="test", source="epias_manual", change_reason="kesinleşti")
        assert guncel.success and not guncel.created
        kayit = db.query(MarketReferencePrice).one()
        assert (kayit.ptf_tl_per_mwh, kayit.yekdem_tl_per_mwh, kayit.status) == (2550.0, 300.0, "final")

    def test_yonetim_api_yekdemsiz_yeni_donem_400_yekdemli_kesin_kayit_dogrulanir(self, client, db):
        from app.database import MarketReferencePrice

        r = client.post("/admin/market-prices",
                        json={"period": GECMIS_DONEM, "value": 2500, "status": "final"})
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "YEKDEM_REQUIRED"
        assert r.json()["detail"]["field"] == "yekdem_value"
        assert db.query(MarketReferencePrice).count() == 0
        r2 = client.post("/admin/market-prices",
                         json={"period": GECMIS_DONEM, "value": 2500, "status": "final",
                               "yekdem_value": 300})
        assert r2.status_code == 200 and r2.json()["action"] == "created"
        prov = client.get(f"/api/epias/prices/{GECMIS_DONEM}").json()["price_provenance"]
        assert prov["verified"] is True and prov["epias_basis"] is True

    def test_toplu_onizleme_yeni_donemi_saymaz_uygulama_sifir_yazmaz(self, db):
        from app.bulk_importer import BulkImporter
        from app.database import MarketReferencePrice

        aktarici = BulkImporter()
        satirlar = aktarici.parse_csv(f"period,value,status\n{GECMIS_DONEM},2500.00,final\n")
        onizleme = aktarici.preview(db, satirlar)
        assert onizleme.new_records == 0 and onizleme.invalid_rows == 1
        assert onizleme.errors[0]["error_code"] == "YEKDEM_REQUIRED"
        sonuc = aktarici.apply(db, satirlar, updated_by="test")
        assert sonuc.accepted_count == 0
        assert sonuc.rejected_rows[0]["error_code"] == "YEKDEM_REQUIRED"
        assert db.query(MarketReferencePrice).count() == 0

    def test_toplu_ice_aktarma_mevcut_donemin_ptfsini_gunceller_yekdeme_dokunmaz(self, db):
        from app.bulk_importer import BulkImporter
        from app.database import MarketReferencePrice

        _referans(db, 2400.0, 300.0, period=GECMIS_DONEM, status="provisional")
        aktarici = BulkImporter()
        satirlar = aktarici.parse_csv(f"period,value,status\n{GECMIS_DONEM},2500.00,provisional\n")
        assert aktarici.preview(db, satirlar).updates == 1
        sonuc = aktarici.apply(db, satirlar, updated_by="test", change_reason="toplu")
        assert sonuc.accepted_count == 1
        kayit = db.query(MarketReferencePrice).one()
        db.refresh(kayit)
        assert (kayit.ptf_tl_per_mwh, kayit.yekdem_tl_per_mwh) == (2500.0, 300.0)


# ═══════════════════════════════════════════════════════════════════════════
# 7b) Gerçek YEKDEM=0 — yetkili ekranda açık giriş; eski sıfır otomatik sınıflanmaz
# ═══════════════════════════════════════════════════════════════════════════

def _yonetim_kaydi(client, *, yekdem=0, status="final", ptf=2500, sebep=None, force=False):
    """POST /admin/market-prices — yetkili Piyasa Fiyatları ekranının ucu (kaynak epias_manual)."""
    govde = {"period": GECMIS_DONEM, "value": ptf, "status": status}
    if yekdem is not None:
        govde["yekdem_value"] = yekdem
    if sebep is not None:
        govde["change_reason"] = sebep
    if force:
        govde["force_update"] = True
    return client.post("/admin/market-prices", json=govde)


def _gecmis_donem_prov(client):
    return client.get(f"/api/epias/prices/{GECMIS_DONEM}").json()["price_provenance"]


class TestAcikSifirYekdem:
    def test_dogrulayici_sifiri_kabul_eder_negatifi_reddeder(self):
        from app.market_price_validator import MarketPriceValidator

        dogrulayici = MarketPriceValidator()
        for deger in (0, 0.0, "0", "0.00", Decimal("0")):
            sonuc, ayrik = dogrulayici.validate_yekdem_value(deger)
            assert sonuc.is_valid and ayrik == 0, deger
        for deger in (-1, -0.01, Decimal("-5")):
            sonuc, _ = dogrulayici.validate_yekdem_value(deger)
            assert not sonuc.is_valid and "negatif" in sonuc.errors[0].message, deger

    def test_acik_kesin_sifir_dogrulanir_ve_teklif_kaydedilir(self, client, db):
        from app.database import Offer, PriceChangeHistory

        r = _yonetim_kaydi(client, yekdem=0)
        assert r.status_code == 200 and r.json()["action"] == "created"
        satirlar = db.query(PriceChangeHistory).order_by(PriceChangeHistory.id).all()
        assert [(s.price_type, s.new_value, s.new_status) for s in satirlar] == \
            [("PTF", 2500.0, "final"), ("YEKDEM", 0.0, "final")]
        b = client.get(f"/api/epias/prices/{GECMIS_DONEM}").json()
        assert b["yekdem_tl_per_mwh"] == 0.0 and b["yekdem_status"] == "known"
        prov = b["price_provenance"]
        assert prov["verified"] is True and prov["verified_by"] == "system"
        assert prov["yekdem"]["period_zero_audit_id"] == satirlar[1].id
        kod, _ = _yanit(_teklif_cagir(db, ptf=2500.0, yekdem=0.0, mod="included",
                                      period=GECMIS_DONEM))
        assert kod == 200
        o = db.query(Offer).one()
        yek = o.calculation_result["meta_price_provenance"]["yekdem"]
        assert (yek["mode"], yek["status"], yek["value"]) == ("included", "matched", 0.0)
        assert o.yekdem == 0.0

    def test_taslak_acik_sifir_yalniz_taslak_yeniden_girilmeden_kesinlesmez(self, client, db):
        assert _yonetim_kaydi(client, yekdem=0, status="provisional").status_code == 200
        assert _gecmis_donem_prov(client)["blocking_reasons"] == ["ptf_provisional", "yekdem_provisional"]
        # YEKDEM yeniden girilmeden kesinleştirme açık sıfır teyidini KAPSAMAZ.
        assert _yonetim_kaydi(client, yekdem=None, sebep="kesinleşti").status_code == 200
        prov = _gecmis_donem_prov(client)
        assert prov["ptf"]["system_verified"] is True
        assert prov["blocking_reasons"] == ["yekdem_zero_unverified"]
        # Açık 0 ile teyit: değer değişmediği için force_update gerekmez.
        assert _yonetim_kaydi(client, yekdem=0, sebep="YEKDEM 0 teyit").status_code == 200
        assert _gecmis_donem_prov(client)["verified"] is True

    def test_eski_kesin_sifir_otomatik_siniflandirilmaz_acik_teyitle_dogrulanir(self, client, db):
        from app.database import MarketReferencePrice

        _referans(db, 2500.0, 0.0, period=GECMIS_DONEM)  # eski kesin sıfır: açık giriş kaydı yok
        b = client.get(f"/api/epias/prices/{GECMIS_DONEM}").json()
        # Ne gerçek (doğrulanmış) ne eksik (null/missing): anlamı doğrulanmamış sıfır.
        assert b["yekdem_tl_per_mwh"] == 0.0 and b["yekdem_status"] == "zero_unverified"
        assert b["price_provenance"]["blocking_reasons"] == ["yekdem_zero_unverified"]
        kayit = db.query(MarketReferencePrice).one()
        assert (kayit.yekdem_tl_per_mwh, kayit.status) == (0.0, "final")  # okuma kaydı değiştirmez
        assert _yonetim_kaydi(client, yekdem=0, sebep="YEKDEM 0 teyit").status_code == 200
        assert _gecmis_donem_prov(client)["verified"] is True

    def test_teyitten_sonra_baska_yol_ya_da_yekdemsiz_guncelleme_teyidi_dusurur(self, client, db):
        from app.market_prices import upsert_market_prices

        assert _yonetim_kaydi(client, yekdem=0).status_code == 200
        assert _gecmis_donem_prov(client)["verified"] is True
        # Geçmiş yazmayan başka bir yol kaydı yeniden yazar (değer yine 0, kaynak farklı).
        ok, _ = upsert_market_prices(db, period=GECMIS_DONEM, ptf_tl_per_mwh=2500.0,
                                     yekdem_tl_per_mwh=0.0, source="epias_api")
        assert ok
        assert _gecmis_donem_prov(client)["blocking_reasons"] == ["yekdem_zero_unverified"]
        # Yeniden açık teyit → doğrulanır; ardından YEKDEM girilmeden PTF düzeltmesi teyidi düşürür.
        assert _yonetim_kaydi(client, yekdem=0, sebep="YEKDEM 0 teyit").status_code == 200
        assert _gecmis_donem_prov(client)["verified"] is True
        r = _yonetim_kaydi(client, yekdem=None, ptf=2510, sebep="PTF düzeltme", force=True)
        assert r.status_code == 200
        assert _gecmis_donem_prov(client)["blocking_reasons"] == ["yekdem_zero_unverified"]

    def test_kilit_acik_sifir_teyidini_dusurmez(self, client, db):
        from app.market_prices import lock_market_prices

        assert _yonetim_kaydi(client, yekdem=0).status_code == 200
        assert lock_market_prices(db, GECMIS_DONEM)[0] is True
        assert _gecmis_donem_prov(client)["verified"] is True

    def test_servis_sifir_satiri_yalniz_acik_sifirda_teyit_noop_degil(self, db):
        from app.database import PriceChangeHistory
        from app.market_price_admin_service import MarketPriceAdminService, ServiceErrorCode
        from app.market_price_validator import NormalizedMarketPriceInput

        servis = MarketPriceAdminService()

        def yaz(yekdem, sebep=None):
            return servis.upsert_price(
                db, NormalizedMarketPriceInput(period=GECMIS_DONEM, value=Decimal("2500.00"),
                                               status="provisional", yekdem_value=yekdem),
                updated_by="test", source="epias_manual", change_reason=sebep)

        def yekdem_satirlari():
            return [(s.old_value, s.new_value) for s in db.query(PriceChangeHistory)
                    .filter_by(price_type="YEKDEM").order_by(PriceChangeHistory.id)]

        assert yaz(Decimal("300.00")).created
        assert yekdem_satirlari() == []  # sıfır dışı değer ayrı satır yazmaz
        sonuc = yaz(Decimal("0"), sebep="YEKDEM gerçekte 0")
        assert sonuc.success and sonuc.changed
        assert yekdem_satirlari() == [(300.0, 0.0)]
        sonuc = yaz(Decimal("0"))  # kayıtlı 0'ın teyidi no-op değil → neden ister
        assert not sonuc.success
        assert sonuc.error.error_code == ServiceErrorCode.CHANGE_REASON_REQUIRED
        sonuc = yaz(Decimal("0"), sebep="teyit")
        assert sonuc.success and sonuc.changed
        assert yekdem_satirlari() == [(300.0, 0.0), (0.0, 0.0)]
        sonuc = yaz(None)  # YEKDEM verilmedi + aynı değer/durum → no-op (satır yok)
        assert sonuc.success and not sonuc.changed
        assert yekdem_satirlari() == [(300.0, 0.0), (0.0, 0.0)]

    def test_gecmis_ucu_yekdem_satirlarini_listeler_ptf_gecmisi_ayri(self, client, db):
        assert _yonetim_kaydi(client, yekdem=0).status_code == 200
        r = client.get("/admin/market-prices/history",
                       params={"period": GECMIS_DONEM, "price_type": "YEKDEM"})
        assert r.status_code == 200
        assert [(h["action"], h["new_value"], h["new_status"]) for h in r.json()["history"]] == \
            [("INSERT", 0.0, "final")]
        ptf = client.get("/admin/market-prices/history", params={"period": GECMIS_DONEM})
        assert [h["new_value"] for h in ptf.json()["history"]] == [2500.0]


# ═══════════════════════════════════════════════════════════════════════════
# 8) Mevcut ağırlıklı PTF öncelikleri — kapı zinciri birebir izler
# ═══════════════════════════════════════════════════════════════════════════

class TestOnceliklerKorunur:
    def test_saatlik_varken_referans_skaler_sistemce_dogrulanmaz(self, db):
        from app.price_provenance import build_price_provenance

        _saatlik(db)
        _referans(db, 900.0, 300.0, source="epias_manual")
        prov = build_price_provenance(db, period=PERIOD, ptf=900.0, yekdem=300.0,
                                      yekdem_mode="included")
        assert prov["ptf"]["status"] == "user_entered", "sistem saatliği seçerdi"
        prov2 = build_price_provenance(db, period=PERIOD, ptf=PUANT, yekdem=300.0,
                                       yekdem_mode="included")
        assert prov2["ptf"]["source"] == "hourly_weighted:puant_agir"
        assert prov2["verified"] is True and prov2["epias_basis"] is True

    def test_manual_override_saatligi_kaynakta_da_ezer(self, db):
        from app.price_provenance import build_price_provenance

        _saatlik(db)
        _referans(db, 1234.5, 300.0, source="manual_override")
        prov = build_price_provenance(db, period=PERIOD, ptf=PUANT, yekdem=300.0,
                                      yekdem_mode="included")
        assert prov["ptf"]["status"] == "user_entered", "sistem manual_override'ı seçerdi"
        prov2 = build_price_provenance(db, period=PERIOD, ptf=1234.5, yekdem=300.0,
                                       yekdem_mode="included")
        assert prov2["ptf"]["source"] == "manual_override" and prov2["verified"] is True

    def test_provisional_manual_override_saatligi_ezer_ama_kesinlesmez(self, db):
        from app.price_provenance import build_price_provenance

        _saatlik(db)
        _referans(db, 1234.5, 300.0, source="manual_override", status="provisional")
        prov = build_price_provenance(db, period=PERIOD, ptf=1234.5, yekdem=300.0,
                                      yekdem_mode="included")
        assert prov["ptf"]["source"] == "manual_override"
        assert prov["blocking_reasons"] == ["ptf_provisional", "yekdem_provisional"]

    def test_kesinlik_yalniz_acik_final_durumu(self):
        from types import SimpleNamespace
        from app.price_provenance import reference_is_final

        assert reference_is_final(SimpleNamespace(status="final")) is True
        for durum in ("provisional", None, "", "FINAL"):
            assert reference_is_final(SimpleNamespace(status=durum)) is False

    def test_db_hatasinda_fail_closed(self):
        from unittest.mock import MagicMock
        from sqlalchemy.exc import OperationalError
        from app.price_provenance import build_price_provenance

        db = MagicMock()
        db.query.side_effect = OperationalError("SELECT", {}, Exception("kilitli"))
        prov = build_price_provenance(db, period=PERIOD, ptf=2500.0, yekdem=300.0,
                                      yekdem_mode="included")
        assert prov["system_lookup"] == "error" and prov["verified"] is False

    def test_snapshot_dogrulamasi_surum_ve_sistem_dogrulamasi_ister(self):
        from app.price_provenance import snapshot_price_verified

        assert not snapshot_price_verified({})
        assert not snapshot_price_verified({"meta_price_provenance": {"verified": True}})
        assert not snapshot_price_verified({"meta_price_provenance": {"version": 1, "verified": True}})
        assert not snapshot_price_verified(
            {"meta_price_provenance": {"version": 2, "verified": True, "verified_by": "user"}})
        assert snapshot_price_verified(
            {"meta_price_provenance": {"version": 2, "verified": True, "verified_by": "system"}})


class TestSabitTohumlamaYok:
    def test_acilista_ornek_fiyat_ekleyen_fonksiyon_yok(self):
        import app.main as m

        assert not hasattr(m, "_add_sample_market_prices")
