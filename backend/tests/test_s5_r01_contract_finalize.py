"""
S5-R01 — Sözleşme finalize fail-closed kapısı.

Owner Bölüm 2 kuralları:
 1. Taslak oluşturma zorunlu alanlar eksikken SERBESTTİR.
 2. Taslak/UI eksik alanları gösterir.
 3. Finalize sırasında backend aynı alanları YENİDEN doğrular.
 4. Alanlardan biri eksikse finalize reddedilir.
 5. `document_ids` boş olması kontrolü ATLATAMAZ.
 6. Belgeler mevcutsa unresolved-conflict kontrolü AYRICA uygulanır.
 7. Belge yüklenmiş olması tek başına mutlak final şartı değildir.
 8. Client'ın "hazır" iddiasına GÜVENİLMEZ.
 9. Hata response'u yalnız eksik alan ADLARINI verir; PII yok.
10. Finalize başarısızlığında state/document/audit KISMEN İLERLEMEZ.

Test verisi tamamen SENTETİKTİR; gerçek TCKN / vergi numarası içermez.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.contracts import service


ZORUNLU_ALANLAR = {
    "legal_name",
    "tax_number",
    "tax_office",
    "registered_address",
    "representative_full_name",
    "representative_national_id",
}


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — mevcut sözleşme test deseni (bkz. tests/test_contracts.py)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    import app.pricing.schemas  # noqa: F401 — Base.metadata'ya kaydolsun

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


# ═══════════════════════════════════════════════════════════════════════════
# Yardımcılar (sentetik veri)
# ═══════════════════════════════════════════════════════════════════════════

def _musteri(db, ad="Sentetik Musteri"):
    from app.database import Customer
    c = Customer(name=ad)
    db.add(c)
    db.flush()
    return c


def _teklif(db, customer_id=None, tenant_id="default"):
    from app.database import Offer
    o = Offer(
        tenant_id=tenant_id,
        customer_id=customer_id,
        consumption_kwh=1000.0,
        current_unit_price=2.5,
        weighted_ptf=2500.0,
        yekdem=50.0,
        agreement_multiplier=1.01,
        current_total=2500.0,
        offer_total=2400.0,
        savings_amount=100.0,
        savings_ratio=0.04,
    )
    db.add(o)
    db.flush()
    return o


def _hukuki_kayitlar(db, customer_id=None, tenant_id="default", **ezilen):
    """Zorunlu alanları DOLU üretir; `ezilen` ile tek tek boşaltılabilir."""
    from app.database import CustomerLegalProfile, CustomerAuthorizedRepresentative

    alanlar = dict(
        legal_name="Sentetik Enerji Sanayi Ltd. Sti.",
        tax_number="0000000000",
        tax_office="Sentetik VD",
        registered_address="Sentetik Mah. Test Cad. No:1",
    )
    temsilci_alanlari = dict(full_name="Sentetik Yetkili", national_id="00000000000")
    for k, v in ezilen.items():
        if k in alanlar:
            alanlar[k] = v
        elif k in temsilci_alanlari:
            temsilci_alanlari[k] = v
        else:
            raise AssertionError(f"bilinmeyen alan: {k}")

    profil = CustomerLegalProfile(tenant_id=tenant_id, customer_id=customer_id, **alanlar)
    db.add(profil)
    db.flush()
    temsilci = CustomerAuthorizedRepresentative(
        tenant_id=tenant_id, customer_id=customer_id, legal_profile_id=profil.id,
        **temsilci_alanlari,
    )
    db.add(temsilci)
    db.flush()
    return profil, temsilci


def _belge(db, document_type, customer_id=None, tenant_id="default", tohum=None):
    from app.database import UploadedReferenceDocument
    t = tohum or f"{document_type}-{customer_id}-{tenant_id}"
    d = UploadedReferenceDocument(
        tenant_id=tenant_id,
        customer_id=customer_id,
        document_type=document_type,
        original_filename=f"{document_type}.pdf",
        mime_type="application/pdf",
        file_size=10,
        sha256=hashlib.sha256(t.encode()).hexdigest(),
        storage_ref="bu-testlerde-kullanilmiyor",
        processing_status="extracted",
    )
    db.add(d)
    db.flush()
    return d


def _catisan_aday(db, belge, alan="legal_name"):
    """Cozulmemis conflict uretir (extraction run + candidate)."""
    from app.database import DocumentExtractionRun, DocumentFieldCandidate

    kosu = DocumentExtractionRun(
        tenant_id=belge.tenant_id,
        document_id=belge.id,
        extractor_type=belge.document_type,
        extractor_version="v1",
        model_name="sentetik",
        prompt_version="v1",
        status="completed",
    )
    db.add(kosu)
    db.flush()

    aday = DocumentFieldCandidate(
        tenant_id=belge.tenant_id,
        extraction_run_id=kosu.id,
        document_id=belge.id,
        field_name=alan,
        raw_value="Catisan Deger",
        normalized_value=service._normalize_value("Catisan Deger"),
        confidence=0.9,
        source_page=1,
        validation_status="pending",
        conflict_status="conflict",
    )
    db.add(aday)
    db.flush()
    return aday


def _taslak(client, offer_id, profil=None, temsilci=None, customer_id=None, tenant="default"):
    govde = {"offer_id": offer_id}
    if profil is not None:
        govde["legal_profile_id"] = profil.id
    if temsilci is not None:
        govde["authorized_representative_id"] = temsilci.id
    if customer_id is not None:
        govde["customer_id"] = customer_id
    r = client.post("/api/contracts/drafts", json=govde, headers={"X-Tenant-Id": tenant})
    assert r.status_code == 200, r.text
    return r.json()


def _onizleme(client, contract_id, tenant="default"):
    r = client.post(
        f"/api/contracts/{contract_id}/preview",
        json={"start_date": "2026-01-01", "duration_months": 12},
        headers={"X-Tenant-Id": tenant},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _finalize(client, contract_id, tenant="default"):
    return client.post(
        f"/api/contracts/{contract_id}/finalize", headers={"X-Tenant-Id": tenant}
    )


# ═══════════════════════════════════════════════════════════════════════════
# Kural 1 — Taslak eksik bilgiyle SERBEST
# ═══════════════════════════════════════════════════════════════════════════

_WIZARD = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "contracts" / "ContractWizardModal.tsx"
)


def _kod(metin: str) -> str:
    """TS/TSX yorum satırlarını ayıklar (yanlış pozitif önlemek için)."""
    cikti, blokta = [], False
    for satir in metin.splitlines():
        t = satir.strip()
        if blokta:
            if "*/" in t:
                blokta, t = False, t.split("*/", 1)[1]
            else:
                continue
        if t.startswith("//") or t.startswith("*"):
            continue
        if "/*" in t:
            once, _, sonra = t.partition("/*")
            if "*/" in sonra:
                t = once + sonra.split("*/", 1)[1]
            else:
                t, blokta = once, True
        t = t.split("//", 1)[0].strip()
        if t:
            cikti.append(t)
    return chr(10).join(cikti)


class TestSihirbazBelgeKapisiKaldirildi:
    """
    UAT'te bulundu: inceleme adımındaki blokajı kaldırmak YETMİYORDU —
    sihirbazın BİRİNCİ adımı da en az bir belge şart koşuyordu ve belgesi
    olmayan kullanıcı sözleşme hazırlamaya HİÇ başlayamıyordu.
    """

    def test_yukleme_adimi_belge_sart_kosmuyor(self):
        kod = _kod(_WIZARD.read_text(encoding="utf-8"))
        assert "disabled={(!taxCertFile && !signatureCircularFile) || loading}" not in kod, (
            "adım 1 hâlâ belge şart koşuyor — belgesiz taslak engellenir"
        )

    def test_belgesiz_akis_dogrudan_incelemeye_gecer(self):
        kod = _kod(_WIZARD.read_text(encoding="utf-8"))
        assert "En az bir belge" not in kod, "belgesiz devam hâlâ hata ile reddediliyor"
        assert "setStep('review')" in kod, "belgesiz yol inceleme adımına geçmeli"

    def test_inceleme_adimi_eksik_alanla_bloke_etmiyor(self):
        kod = _kod(_WIZARD.read_text(encoding="utf-8"))
        assert "disabled={hasUnresolvedConflicts || !requiredFieldsReady || loading}" not in kod, (
            "inceleme adımı hâlâ eksik zorunlu alanla bloke ediyor"
        )
        # Çözülmemiş ÇELİŞKİ engel olarak KALMALI.
        assert "disabled={hasUnresolvedConflicts || loading}" in kod

    def test_eksik_alan_uyarisi_gosteriliyor(self):
        kod = _kod(_WIZARD.read_text(encoding="utf-8"))
        assert "missingRequiredFieldNames" in kod
        assert "finalize" in kod.lower(), "uyarı finalize şartını anlatmalı"


class TestTaslakEksikBilgiyleSerbest:
    def test_hicbir_hukuki_kayit_yokken_taslak_olusturulabilir(self, client, db):
        """Vergi levhası / imza sirküleri hiç yüklenmemişken bile taslak açılır."""
        offer = _teklif(db)
        db.commit()

        taslak = _taslak(client, offer.id)
        assert taslak["status"] == "DRAFT"

    def test_belge_olmadan_onizleme_de_yapilabilir(self, client, db):
        offer = _teklif(db)
        db.commit()
        taslak = _taslak(client, offer.id)

        onizleme = _onizleme(client, taslak["id"])
        assert onizleme["status"] == "READY_TO_GENERATE"


# ═══════════════════════════════════════════════════════════════════════════
# Kural 2 — Taslak response'u EKSİK ALANLARI bildirir
# ═══════════════════════════════════════════════════════════════════════════

class TestTaslakEksikAlanBildirimi:
    def test_taslak_olusturma_yaniti_exact_eksik_listeyi_doner(self, client, db):
        offer = _teklif(db)
        db.commit()

        taslak = _taslak(client, offer.id)
        assert set(taslak["missing_required_fields"]) == ZORUNLU_ALANLAR

    def test_taslak_okuma_yaniti_ayni_listeyi_doner(self, client, db):
        """Oluşturma ve okuma response'ları TUTARLI olmalı."""
        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(db, tax_office="", national_id="")
        db.commit()

        taslak = _taslak(client, offer.id, profil, temsilci)
        okunan = client.get(f"/api/contracts/{taslak['id']}").json()

        assert taslak["missing_required_fields"] == okunan["missing_required_fields"]
        assert set(okunan["missing_required_fields"]) == {
            "tax_office", "representative_national_id",
        }

    def test_liste_yaniti_da_gercek_degeri_tasir(self, client, db):
        """Listede varsayılan boş liste dönmek 'eksik yok' YALANI olurdu."""
        offer = _teklif(db)
        db.commit()
        taslak = _taslak(client, offer.id)

        satir = [c for c in client.get("/api/contracts").json() if c["id"] == taslak["id"]][0]
        assert set(satir["missing_required_fields"]) == ZORUNLU_ALANLAR

    def test_alanlar_tamamlaninca_bos_liste_doner(self, client, db):
        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(db)
        db.commit()

        taslak = _taslak(client, offer.id, profil, temsilci)
        assert taslak["missing_required_fields"] == []
        okunan = client.get(f"/api/contracts/{taslak['id']}").json()
        assert okunan["missing_required_fields"] == []

    def test_bildirim_pii_icermez(self, client, db):
        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(
            db, legal_name="", tax_number="9999999999", national_id="12345678901",
        )
        db.commit()

        taslak = _taslak(client, offer.id, profil, temsilci)
        govde = str(taslak)
        assert "12345678901" not in govde, "TCKN taslak yanıtına SIZMAMALI"
        assert "9999999999" not in govde, "vergi numarası SIZMAMALI"
        assert taslak["missing_required_fields"] == ["legal_name"]

    def test_bildirim_ile_finalize_kapisi_ayni_kaynagi_kullanir(self, client, db):
        """
        Response'un bildirdiği eksik liste ile finalize'ın reddettiği liste
        BİREBİR aynı olmalı — iki ayrı doğrulama kaynağı olamaz.
        """
        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(db, registered_address="   ", full_name="")
        db.commit()

        taslak = _taslak(client, offer.id, profil, temsilci)
        _onizleme(client, taslak["id"])
        red = _finalize(client, taslak["id"])

        assert red.status_code == 422
        assert red.json()["detail"]["missing_fields"] == taslak["missing_required_fields"]


# ═══════════════════════════════════════════════════════════════════════════
# Kural 3/4/5 — Finalize fail-closed
# ═══════════════════════════════════════════════════════════════════════════

class TestFinalizeFailClosed:
    def test_belge_yokken_ve_alanlar_eksikken_finalize_reddedilir(self, client, db):
        """Kural 5: `document_ids` BOŞ olması kapıyı atlatamaz."""
        offer = _teklif(db)
        db.commit()
        taslak = _taslak(client, offer.id)
        _onizleme(client, taslak["id"])

        r = _finalize(client, taslak["id"])
        assert r.status_code == 422
        detay = r.json()["detail"]
        assert detay["error"] == "missing_required_fields"
        assert set(detay["missing_fields"]) == ZORUNLU_ALANLAR

    def test_belgeler_varken_alanlar_eksikse_finalize_reddedilir(self, client, db):
        """Kural 7: belge YÜKLENMİŞ olması tek başına finalize'ı açmaz."""
        musteri = _musteri(db)
        offer = _teklif(db, customer_id=musteri.id)
        _belge(db, "vergi_levhasi", customer_id=musteri.id)
        _belge(db, "imza_sirkusu", customer_id=musteri.id)
        db.commit()
        taslak = _taslak(client, offer.id, customer_id=musteri.id)
        _onizleme(client, taslak["id"])

        r = _finalize(client, taslak["id"])
        assert r.status_code == 422
        assert r.json()["detail"]["error"] == "missing_required_fields"

    @pytest.mark.parametrize(
        "bos_alan,beklenen",
        [
            ("legal_name", "legal_name"),
            ("tax_number", "tax_number"),
            ("tax_office", "tax_office"),
            ("registered_address", "registered_address"),
            ("full_name", "representative_full_name"),
            ("national_id", "representative_national_id"),
        ],
    )
    def test_tek_alan_eksikse_bile_finalize_reddedilir(self, client, db, bos_alan, beklenen):
        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(db, **{bos_alan: ""})
        db.commit()
        taslak = _taslak(client, offer.id, profil, temsilci)
        _onizleme(client, taslak["id"])

        r = _finalize(client, taslak["id"])
        assert r.status_code == 422
        assert r.json()["detail"]["missing_fields"] == [beklenen]

    def test_yalniz_bosluk_iceren_deger_dolu_sayilmaz(self, client, db):
        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(db, tax_number="   ")
        db.commit()
        taslak = _taslak(client, offer.id, profil, temsilci)
        _onizleme(client, taslak["id"])

        r = _finalize(client, taslak["id"])
        assert r.status_code == 422
        assert r.json()["detail"]["missing_fields"] == ["tax_number"]


# ═══════════════════════════════════════════════════════════════════════════
# Kural 8 — Client'ın "hazır" iddiasına güvenilmez
# ═══════════════════════════════════════════════════════════════════════════

class TestClientHazirIddiasi:
    def test_client_bos_string_gondererek_kapiyi_atlatamaz(self, client, db):
        """
        Gerçek istismar yolu: frontend `saveLegalProfile`e `get(name) || ''`
        gönderir; şema `min_length` uygulamadığı için boş string KAYDEDİLİR.
        Kapı bu kaydı DOLU saymamalıdır.
        """
        offer = _teklif(db)
        db.commit()

        profil = client.post("/api/contracts/legal-profiles", json={
            "legal_name": "", "tax_number": "", "tax_office": "", "registered_address": "",
        })
        assert profil.status_code == 200, profil.text
        temsilci = client.post("/api/contracts/representatives", json={
            "legal_profile_id": profil.json()["id"], "full_name": "", "national_id": "",
        })
        assert temsilci.status_code == 200, temsilci.text

        r = client.post("/api/contracts/drafts", json={
            "offer_id": offer.id,
            "legal_profile_id": profil.json()["id"],
            "authorized_representative_id": temsilci.json()["id"],
        })
        taslak = r.json()
        _onizleme(client, taslak["id"])

        son = _finalize(client, taslak["id"])
        assert son.status_code == 422
        assert set(son.json()["detail"]["missing_fields"]) == ZORUNLU_ALANLAR

    def test_complete_fields_ile_hazir_iddiasi_kapiyi_acmaz(self, client, db):
        """
        Preview'a gönderilen `complete_fields` client kaynaklıdır ve zorunlu
        hukuki alanları TAŞIMAZ; bunları doldurmak finalize'ı açmamalıdır.
        """
        offer = _teklif(db)
        db.commit()
        taslak = _taslak(client, offer.id)

        r = client.post(f"/api/contracts/{taslak['id']}/preview", json={
            "start_date": "2026-01-01",
            "duration_months": 24,
            "tariff_group": "Sanayi",
            "subscription_codes": "1234567890",
        })
        assert r.status_code == 200

        son = _finalize(client, taslak["id"])
        assert son.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# Kural 9 — Hata response'unda PII yok
# ═══════════════════════════════════════════════════════════════════════════

class TestPiiSizintisiYok:
    def test_hata_yaniti_yalniz_alan_adlari_icerir(self, client, db):
        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(
            db, legal_name="", full_name="Gizli Kisi Adi", national_id="12345678901",
        )
        db.commit()
        taslak = _taslak(client, offer.id, profil, temsilci)
        _onizleme(client, taslak["id"])

        r = _finalize(client, taslak["id"])
        assert r.status_code == 422
        govde = r.text
        assert "12345678901" not in govde, "TCKN response'a SIZMAMALI"
        assert "Gizli Kisi Adi" not in govde, "kişi adı response'a SIZMAMALI"
        assert r.json()["detail"]["missing_fields"] == ["legal_name"]

    def test_servis_fonksiyonu_yalniz_alan_adi_dondurur(self, db):
        """`missing_required_legal_fields` hiçbir DEĞER döndürmez."""
        from app.database import Contract

        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(db, tax_number="")
        sozlesme = Contract(
            tenant_id="default", offer_id=offer.id, status="DRAFT",
            legal_profile_id=profil.id, authorized_representative_id=temsilci.id,
        )
        db.add(sozlesme)
        db.flush()

        eksik = service.missing_required_legal_fields(db, sozlesme)
        assert eksik == ["tax_number"]
        assert all(isinstance(x, str) and x in ZORUNLU_ALANLAR for x in eksik)


# ═══════════════════════════════════════════════════════════════════════════
# Kural 6 — Conflict kontrolü AYRICA uygulanır
# ═══════════════════════════════════════════════════════════════════════════

class TestConflictKontrolu:
    def test_alanlar_tamam_fakat_cozulmemis_catisma_varsa_reddedilir(self, client, db):
        musteri = _musteri(db)
        offer = _teklif(db, customer_id=musteri.id)
        profil, temsilci = _hukuki_kayitlar(db, customer_id=musteri.id)
        belge = _belge(db, "vergi_levhasi", customer_id=musteri.id)
        _catisan_aday(db, belge)
        db.commit()
        taslak = _taslak(client, offer.id, profil, temsilci, customer_id=musteri.id)
        _onizleme(client, taslak["id"])

        r = _finalize(client, taslak["id"])
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "unresolved_conflicts"


# ═══════════════════════════════════════════════════════════════════════════
# Kural 7 — Alanlar tamam + conflict yok → finalize MÜMKÜN
# ═══════════════════════════════════════════════════════════════════════════

class TestBasariliFinalize:
    def test_alanlar_tamam_ve_catisma_yoksa_finalize_edilir(self, client, db):
        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(db)
        db.commit()
        taslak = _taslak(client, offer.id, profil, temsilci)
        _onizleme(client, taslak["id"])

        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=b"%PDF-sentetik"):
            r = _finalize(client, taslak["id"])

        assert r.status_code == 200, r.text
        assert r.json()["status"] == "FINALIZED"

    def test_belge_hic_yuklenmeden_elle_girilen_veriyle_finalize_edilebilir(self, client, db):
        """
        Kural 7: belgelerin yüklenmiş olması MUTLAK şart değildir — zorunlu
        veriler güvenilir biçimde tamamlandıysa finalize mümkündür.
        """
        from app.database import UploadedReferenceDocument

        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(db)
        db.commit()
        assert db.query(UploadedReferenceDocument).count() == 0

        taslak = _taslak(client, offer.id, profil, temsilci)
        _onizleme(client, taslak["id"])
        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=b"%PDF-sentetik"):
            r = _finalize(client, taslak["id"])

        assert r.status_code == 200, r.text


# ═══════════════════════════════════════════════════════════════════════════
# Kural 10 — Reddedilen finalize KISMİ mutasyon bırakmaz
# ═══════════════════════════════════════════════════════════════════════════

class TestKismiMutasyonYok:
    def test_reddedilen_finalize_state_document_audit_ilerletmez(self, client, db, storage_tmp):
        from app.database import Contract, Offer, UploadedReferenceDocument

        musteri = _musteri(db)
        offer = _teklif(db, customer_id=musteri.id)
        belge = _belge(db, "vergi_levhasi", customer_id=musteri.id)
        db.commit()
        taslak = _taslak(client, offer.id, customer_id=musteri.id)
        _onizleme(client, taslak["id"])

        onceki_sozlesme = db.query(Contract).filter(Contract.id == taslak["id"]).first()
        onceki_durum = onceki_sozlesme.status
        onceki_teklif_durumu = db.query(Offer).filter(Offer.id == offer.id).first().status
        onceki_belge_durumu = (
            db.query(UploadedReferenceDocument)
            .filter(UploadedReferenceDocument.id == belge.id)
            .first()
            .processing_status
        )

        r = _finalize(client, taslak["id"])
        assert r.status_code == 422

        db.expire_all()
        sonraki = db.query(Contract).filter(Contract.id == taslak["id"]).first()
        assert sonraki.status == onceki_durum, "reddedilen finalize durumu İLERLETMEMELİ"
        assert sonraki.status != "FINALIZING", "CAS claim ALINMAMALI"
        assert sonraki.pdf_storage_ref is None
        assert sonraki.pdf_sha256 is None
        assert sonraki.contract_number is None
        assert db.query(Offer).filter(Offer.id == offer.id).first().status == onceki_teklif_durumu
        assert (
            db.query(UploadedReferenceDocument)
            .filter(UploadedReferenceDocument.id == belge.id)
            .first()
            .processing_status == onceki_belge_durumu
        )
        # Hiçbir sözleşme PDF'i yayımlanmamış olmalı.
        assert list(storage_tmp.rglob("*.pdf")) == []

    def test_reddedilen_finalize_sonrasi_alanlar_tamamlaninca_finalize_edilebilir(self, client, db):
        """Kapı kalıcı olarak kilitlemez — eksik giderilince akış devam eder."""
        from app.database import Contract

        offer = _teklif(db)
        db.commit()
        taslak = _taslak(client, offer.id)
        _onizleme(client, taslak["id"])
        assert _finalize(client, taslak["id"]).status_code == 422

        profil, temsilci = _hukuki_kayitlar(db)
        sozlesme = db.query(Contract).filter(Contract.id == taslak["id"]).first()
        sozlesme.legal_profile_id = profil.id
        sozlesme.authorized_representative_id = temsilci.id
        db.commit()

        # Snapshot bayat: önizleme yenilenmeden finalize edilemez.
        bayat = _finalize(client, taslak["id"])
        assert bayat.status_code == 409
        assert bayat.json()["detail"]["error"] == "stale_preview"

        _onizleme(client, taslak["id"])
        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=b"%PDF-sentetik"):
            son = _finalize(client, taslak["id"])
        assert son.status_code == 200, son.text


# ═══════════════════════════════════════════════════════════════════════════
# Tenant sınırı
# ═══════════════════════════════════════════════════════════════════════════

class TestTenantSiniri:
    def test_farkli_tenant_finalize_edemez(self, client, db):
        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(db)
        db.commit()
        taslak = _taslak(client, offer.id, profil, temsilci, tenant="default")
        _onizleme(client, taslak["id"], tenant="default")

        r = _finalize(client, taslak["id"], tenant="baska-tenant")
        assert r.status_code in (403, 404), r.text
        if r.status_code == 404:
            assert r.json()["detail"]["error"] == "contract_not_found"

    def test_farkli_tenant_reddi_sozlesmeyi_degistirmez(self, client, db):
        from app.database import Contract

        offer = _teklif(db)
        profil, temsilci = _hukuki_kayitlar(db)
        db.commit()
        taslak = _taslak(client, offer.id, profil, temsilci)
        _onizleme(client, taslak["id"])
        onceki = db.query(Contract).filter(Contract.id == taslak["id"]).first().status

        _finalize(client, taslak["id"], tenant="baska-tenant")

        db.expire_all()
        assert db.query(Contract).filter(Contract.id == taslak["id"]).first().status == onceki
