"""
S5-R01 — Kayıtlı teklif PDF zinciri (offer-bound).

Owner Bölüm 1/3/4 sözleşmesi:
- Persisted teklif PDF'i YALNIZ `POST /offers/{id}/generate-pdf` +
  `GET /offers/{id}/download` ile üretilir/indirilir.
- `/generate-pdf-simple` persisted-offer akışında YASAKTIR.
- Endpoint request gövdesi almaz; PDF yalnız saklanan snapshot'tan üretilir.
- Gerçek idempotency: geçerli `pdf_ref` varsa generator ikinci kez çağrılmaz.
- Eşzamanlı iki istek tek fiziksel üretim/publish yapar.
- Dosya atomik yayımlanmadan `pdf_ref` commit edilmez; DB commit başarısızsa
  orphan dosya temizlenir.
- `pdf_ref` var / dosya yok → sessiz yeniden üretim YOK, fail-closed.
- Lock/temp residual her yolda 0.
- Download containment: `..`, absolute escape, symlink escape reddedilir.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

# CalculationResult'un zorunlu 20 alanı — sentetik, tutarlı değerler.
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


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    import app.pricing.schemas  # noqa: F401

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
def disari_kok(tmp_path_factory):
    """
    Storage kokunun DISINDA bir dizin.

    DIKKAT: `storage_tmp` fixture'i `tmp_path`i storage koku yapar; bu yuzden
    `tmp_path` "disarisi" DEGILDIR. Kacis testleri gercekten ayri bir kok
    kullanmalidir, aksi halde test yanlislikla yesil gorunur.
    """
    return tmp_path_factory.mktemp("s5r01_disari")

@pytest.fixture()
def client(db, storage_tmp):
    from app.main import app as fastapi_app
    from app.database import get_db

    fastapi_app.dependency_overrides[get_db] = lambda: db
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def _teklif(db, customer=None, **ezilen):
    from app.database import Offer
    alanlar = dict(
        tenant_id="default",
        customer_id=customer.id if customer else None,
        vendor="Sentetik Tedarikci",
        invoice_period="2026-01",
        consumption_kwh=1000.0,
        current_unit_price=2.5,
        weighted_ptf=2500.0,
        yekdem=50.0,
        agreement_multiplier=1.01,
        current_total=2880.0,
        offer_total=2688.0,
        savings_amount=192.0,
        savings_ratio=0.0667,
        extraction_result={"meta": {}},
        calculation_result=dict(HESAP_SONUCU),
    )
    alanlar.update(ezilen)
    o = Offer(**alanlar)
    db.add(o)
    db.flush()
    db.commit()
    return o


def _musteri(db, ad="Sentetik Musteri", sirket="Sentetik Sirket A.S."):
    from app.database import Customer
    c = Customer(name=ad, company=sirket)
    db.add(c)
    db.flush()
    return c


def _sahte_uretici_fabrikasi(cagri_kaydi: list, icerik: bytes = b"%PDF-sentetik-teklif"):
    """
    Gerçek `generate_and_store_offer_pdf` yerine geçer: aynı deterministik
    anahtara GERÇEK storage üzerinden yazar (atomik publish yolu fiilen
    çalışır), fakat reportlab render'ı yapmaz.
    """
    def sahte(**kw):
        cagri_kaydi.append(kw)
        from app.services.storage import get_storage
        return get_storage().put_bytes(
            f"offers/{kw['offer_id']}/offer.pdf", icerik, "application/pdf"
        )
    return sahte



def _kod_satirlari(metin: str) -> str:
    """
    TS/TSX kaynagindan yorum satirlarini ayiklar.

    Naif substring taramasi (`"x" not in dosya`) aciklama yorumlarina takilir
    ve yanlis pozitif uretir; bu yuzden yalniz KOD satirlari denetlenir.
    Blok yorumlari (/* ... */) ve satir yorumlari (//) atilir.
    """
    cikti = []
    blokta = False
    for satir in metin.splitlines():
        t = satir.strip()
        if blokta:
            if "*/" in t:
                blokta = False
                t = t.split("*/", 1)[1]
            else:
                continue
        if t.startswith("//"):
            continue
        if "/*" in t:
            once, _, sonra = t.partition("/*")
            if "*/" in sonra:
                t = once + sonra.split("*/", 1)[1]
            else:
                t = once
                blokta = True
        # JSX icindeki {/* ... */} tek satirlik yorumlar yukarida temizlendi.
        if t:
            cikti.append(t)
    return chr(10).join(cikti)


def _artiklar(kok: Path) -> list[Path]:
    """
    Yayımlanmamış geçici dosya kalıntıları.

    `.lock` dosyaları KALINTI SAYILMAZ: S5-R01 tamamlamasında kilit sahipliği
    dosyanın varlığından değil OS byte-range kilidinden gelir; dosya bilerek
    silinmez (silmek yarış yaratır, fayda sağlamaz). Asıl ölçüt aktif OS
    kilidinin kalmamasıdır — bkz. `_aktif_kilit_var_mi`.
    """
    return [p for p in kok.rglob("*") if p.is_file() and p.name.endswith(".tmp")]


def _aktif_kilit_var_mi(kilit_yolu: Path) -> bool:
    """Kilit dosyası üzerinde HÂLÂ tutulan bir OS kilidi var mı?"""
    if not kilit_yolu.exists():
        return False
    from app.main import _os_kilidi_dene, _os_kilidi_birak

    fd = os.open(str(kilit_yolu), os.O_CREAT | os.O_RDWR)
    try:
        _os_kilidi_dene(fd)
        _os_kilidi_birak(fd)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def _kilit_yolu(kok: Path, offer_id: int) -> Path:
    return kok / "offers" / str(offer_id) / ".generate.lock"


# ═══════════════════════════════════════════════════════════════════════════
# Sözleşme: request gövdesi yok, client hesap değeri gönderemez
# ═══════════════════════════════════════════════════════════════════════════

class TestRequestGovdesiYok:
    def test_endpoint_hicbir_hesap_alani_kabul_etmiyor(self):
        """
        Endpoint imzasında yalnız `offer_id` + DI bağımlılıkları olmalı.
        Form/Body/Query ile gelen bir hesap alanı OLMAMALI.
        """
        from fastapi import params as fastapi_params
        from app.main import generate_pdf_for_offer

        imza = inspect.signature(generate_pdf_for_offer)
        assert set(imza.parameters) == {"offer_id", "db", "_"}, (
            f"beklenmeyen parametre: {set(imza.parameters)}"
        )
        for ad, p in imza.parameters.items():
            if ad == "offer_id":
                continue
            assert isinstance(p.default, fastapi_params.Depends), (
                f"{ad} bir Depends olmalı — client girdisi kabul edilemez"
            )

    def test_govdeli_istek_de_hesap_degerini_kullanmaz(self, client, db):
        offer = _teklif(db)
        cagrilar: list = []

        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_sahte_uretici_fabrikasi(cagrilar)):
            r = client.post(
                f"/offers/{offer.id}/generate-pdf",
                json={"offer_total": 1.0, "savings_ratio": 0.99, "weighted_ptf_tl_per_mwh": 1.0},
            )

        assert r.status_code == 200, r.text
        params = cagrilar[0]["params"]
        assert params.weighted_ptf_tl_per_mwh == 2500.0, "client değeri KULLANILMAMALI"
        assert cagrilar[0]["calculation"].savings_ratio == HESAP_SONUCU["savings_ratio"]


# ═══════════════════════════════════════════════════════════════════════════
# Yalnız saklanan snapshot kullanılır
# ═══════════════════════════════════════════════════════════════════════════

class TestYalnizSnapshot:
    def test_uretim_kayitli_snapshot_degerlerini_kullanir(self, client, db):
        musteri = _musteri(db)
        offer = _teklif(db, customer=musteri)
        cagrilar: list = []

        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_sahte_uretici_fabrikasi(cagrilar)):
            assert client.post(f"/offers/{offer.id}/generate-pdf").status_code == 200

        kw = cagrilar[0]
        assert kw["offer_id"] == offer.id
        assert kw["customer_name"] == "Sentetik Musteri"
        assert kw["customer_company"] == "Sentetik Sirket A.S."
        assert kw["params"].yekdem_tl_per_mwh == 50.0
        assert kw["params"].agreement_multiplier == 1.01

    def test_snapshot_degisirse_uretim_de_degisir(self, client, db):
        """Kaynak GERÇEKTEN DB satırıdır — sabit/kod içi değer değil."""
        offer = _teklif(db, weighted_ptf=1111.0)
        cagrilar: list = []

        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_sahte_uretici_fabrikasi(cagrilar)):
            client.post(f"/offers/{offer.id}/generate-pdf")

        assert cagrilar[0]["params"].weighted_ptf_tl_per_mwh == 1111.0

    def test_snapshot_eksikse_400(self, client, db):
        offer = _teklif(db, calculation_result=None)
        r = client.post(f"/offers/{offer.id}/generate-pdf")
        assert r.status_code == 400

    def test_olmayan_teklif_404(self, client, db):
        assert client.post("/offers/999999/generate-pdf").status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Gerçek idempotency
# ═══════════════════════════════════════════════════════════════════════════

class TestIdempotency:
    def test_pdf_ref_varsa_generator_ikinci_kez_cagrilmaz(self, client, db):
        offer = _teklif(db)
        cagrilar: list = []
        sahte = _sahte_uretici_fabrikasi(cagrilar)

        with patch("app.pdf_generator.generate_and_store_offer_pdf", side_effect=sahte):
            ilk = client.post(f"/offers/{offer.id}/generate-pdf")
            ikinci = client.post(f"/offers/{offer.id}/generate-pdf")

        assert ilk.status_code == 200 and ikinci.status_code == 200
        assert ilk.json()["regenerated"] is True
        assert ikinci.json()["regenerated"] is False
        assert len(cagrilar) == 1, "generator YALNIZ BİR KEZ çağrılmalı"
        assert ilk.json()["pdf_ref"] == ikinci.json()["pdf_ref"]

    def test_kayip_yanit_sonrasi_retry_yeniden_uretmez(self, client, db, storage_tmp):
        """
        Lost-response senaryosu: istemci yanıtı alamadı ve aynı isteği
        tekrarladı. Duplicate artifact veya yeniden üretim OLMAMALI.
        """
        offer = _teklif(db)
        cagrilar: list = []
        sahte = _sahte_uretici_fabrikasi(cagrilar)

        with patch("app.pdf_generator.generate_and_store_offer_pdf", side_effect=sahte):
            ilk = client.post(f"/offers/{offer.id}/generate-pdf")
            for _ in range(4):
                tekrar = client.post(f"/offers/{offer.id}/generate-pdf")
                assert tekrar.status_code == 200
                assert tekrar.json()["pdf_ref"] == ilk.json()["pdf_ref"]

        assert len(cagrilar) == 1
        pdfler = list(storage_tmp.rglob("*.pdf"))
        assert len(pdfler) == 1, f"duplicate artifact oluştu: {pdfler}"

    def test_pdf_ref_var_dosya_yok_ise_fail_closed(self, client, db):
        """
        Veri/storage tutarsızlığı: SESSİZ yeniden üretim YASAK.
        """
        offer = _teklif(db)
        cagrilar: list = []

        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_sahte_uretici_fabrikasi(cagrilar)):
            ilk = client.post(f"/offers/{offer.id}/generate-pdf")
            assert ilk.status_code == 200
            os.remove(ilk.json()["pdf_ref"])  # dosya kayboldu

            sonra = client.post(f"/offers/{offer.id}/generate-pdf")

        assert sonra.status_code == 409
        assert sonra.json()["detail"]["error"] == "pdf_artifact_missing"
        assert len(cagrilar) == 1, "tutarsızlıkta yeniden üretim YAPILMAMALI"

    def test_bos_dosya_gecerli_sayilmaz(self, client, db):
        offer = _teklif(db)
        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_sahte_uretici_fabrikasi([])):
            ref = client.post(f"/offers/{offer.id}/generate-pdf").json()["pdf_ref"]
        Path(ref).write_bytes(b"")

        r = client.post(f"/offers/{offer.id}/generate-pdf")
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "pdf_artifact_missing"


# ═══════════════════════════════════════════════════════════════════════════
# Eşzamanlılık — cross-process güvenli kilit
# ═══════════════════════════════════════════════════════════════════════════

class TestEszamanlilik:
    def test_kilit_ikinci_giriseni_reddeder(self, storage_tmp):
        from app.main import _teklif_pdf_uretim_kilidi, _TeklifPdfUretimiSuruyor

        with _teklif_pdf_uretim_kilidi(42):
            with pytest.raises(_TeklifPdfUretimiSuruyor):
                with _teklif_pdf_uretim_kilidi(42):
                    pytest.fail("ikinci kilit ALINMAMALIYDI")

    def test_farkli_teklifler_birbirini_engellemez(self, storage_tmp):
        from app.main import _teklif_pdf_uretim_kilidi

        with _teklif_pdf_uretim_kilidi(1):
            with _teklif_pdf_uretim_kilidi(2):
                pass  # farklı teklif → engel yok

    def test_kilit_her_yolda_serbest_birakilir(self, storage_tmp):
        from app.main import _teklif_pdf_uretim_kilidi

        with pytest.raises(RuntimeError):
            with _teklif_pdf_uretim_kilidi(7):
                raise RuntimeError("üretim patladı")

        assert _artiklar(storage_tmp) == [], "hata yolunda .tmp artığı kalmamalı"
        assert not _aktif_kilit_var_mi(_kilit_yolu(storage_tmp, 7)), (
            "hata yolunda AKTİF OS kilidi kalmamalı"
        )
        # Kilit serbest kaldığı için yeniden alınabilmeli.
        with _teklif_pdf_uretim_kilidi(7):
            pass

    def test_sahiplik_dosya_varligindan_gelmez(self, storage_tmp):
        """
        Artakalan kilit DOSYASI tek başına sahiplik anlamına GELMEZ.

        Eski `O_CREAT|O_EXCL` tasarımında bu dosyanın varlığı kalıcı 409
        üretiyordu. Artık aktif OS kilidi yoksa çağrı ilerleyebilmelidir.
        """
        from app.main import _teklif_pdf_uretim_kilidi

        yol = _kilit_yolu(storage_tmp, 55)
        yol.parent.mkdir(parents=True, exist_ok=True)
        yol.write_bytes(b"")  # sahipsiz artık dosya
        assert yol.exists()

        with _teklif_pdf_uretim_kilidi(55):
            pass  # kilit ALINABİLMELİ

    def test_hard_kill_sonrasi_kilit_os_tarafindan_birakilir(self, storage_tmp):
        """
        ZORUNLU TEST (owner Bölüm 2): ayrı bir subprocess kilidi alır, sonra
        ZORLA öldürülür (TerminateProcess/SIGKILL — temizlik şansı YOK).
        Aynı teklif için sonraki çağrı başarıyla üretim yapabilmelidir.

        Kalıcı 409, `.tmp` artığı veya etkin kilit KALMAMALIDIR.
        """
        import subprocess
        import textwrap
        import time
        from app.main import _teklif_pdf_uretim_kilidi, _TeklifPdfUretimiSuruyor

        yol = _kilit_yolu(storage_tmp, 77)
        yol.parent.mkdir(parents=True, exist_ok=True)

        cocuk_kodu = textwrap.dedent(
            """
            import os, sys, time
            yol = sys.argv[1]
            fd = os.open(yol, os.O_CREAT | os.O_RDWR)
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print("KILIT_ALINDI", flush=True)
            time.sleep(120)
            """
        )
        cocuk = subprocess.Popen(
            [sys.executable, "-c", cocuk_kodu, str(yol)],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            assert cocuk.stdout.readline().strip() == "KILIT_ALINDI"

            # Çocuk yaşarken kilit ALINAMAMALI.
            with pytest.raises(_TeklifPdfUretimiSuruyor):
                with _teklif_pdf_uretim_kilidi(77):
                    pytest.fail("çocuk kilidi tutarken kilit ALINMAMALIYDI")

            cocuk.kill()  # hard kill — finally/atexit ÇALIŞMAZ
            cocuk.wait(timeout=30)
        finally:
            if cocuk.poll() is None:
                cocuk.kill()
                cocuk.wait(timeout=30)

        time.sleep(0.3)  # çekirdeğin handle'ı kapatması için kısa pay

        assert yol.exists(), "artık kilit dosyası bekleniyordu (silinmez)"
        assert not _aktif_kilit_var_mi(yol), "hard-kill sonrası OS kilidi bırakılmalı"

        # Ve asıl kanıt: aynı teklif için üretim yeniden mümkün.
        with _teklif_pdf_uretim_kilidi(77):
            pass
        assert _artiklar(storage_tmp) == []

    def test_hard_kill_sonrasi_endpoint_uretebilir(self, client, db, storage_tmp):
        """Uçtan uca: hard-kill artığı kalıcı 409 üretmemeli."""
        offer = _teklif(db)
        yol = _kilit_yolu(storage_tmp, offer.id)
        yol.parent.mkdir(parents=True, exist_ok=True)
        yol.write_bytes(b"12345")  # ölmüş process'ten kalan artık

        cagrilar: list = []
        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_sahte_uretici_fabrikasi(cagrilar)):
            r = client.post(f"/offers/{offer.id}/generate-pdf")

        assert r.status_code == 200, r.text
        assert len(cagrilar) == 1
        assert _artiklar(storage_tmp) == []

    def test_packaged_backend_tek_process_mekanik_dogrulama(self):
        """
        Owner Bölüm 2: "Windows paketli backend process modeli mekanik olarak
        doğrulanacak." `run_server.py` uvicorn'u `workers=1` ile başlatmalı;
        aksi hâlde kilidin process sınırı varsayımları yeniden gözden
        geçirilmelidir (kilit zaten cross-process, fakat bu gerçek pinlenir).
        """
        import ast

        kaynak = (Path(__file__).resolve().parents[1] / "run_server.py").read_text(
            encoding="utf-8"
        )
        agac = ast.parse(kaynak)
        workers_degerleri = [
            kw.value.value
            for dugum in ast.walk(agac)
            if isinstance(dugum, ast.Call)
            for kw in dugum.keywords
            if kw.arg == "workers" and isinstance(kw.value, ast.Constant)
        ]
        assert workers_degerleri == [1], (
            f"packaged backend tek process olmalı; bulunan workers={workers_degerleri}"
        )

    def test_kilit_tutulurken_endpoint_deterministik_409_doner(self, client, db, storage_tmp):
        from app.main import _teklif_pdf_uretim_kilidi

        offer = _teklif(db)
        cagrilar: list = []
        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_sahte_uretici_fabrikasi(cagrilar)):
            with _teklif_pdf_uretim_kilidi(offer.id):
                r = client.post(f"/offers/{offer.id}/generate-pdf")

        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "generation_in_progress"
        assert cagrilar == [], "kilit alınamadıysa üretim BAŞLAMAMALI"

    def test_es_zamanli_iki_giris_yalniz_biri_uretir(self, storage_tmp):
        """
        Kilit thread'ler arasında da tek kazanan bırakır (packaged backend
        `workers=1` fakat PDF render'ı ThreadPoolExecutor ile çok thread'li).
        """
        from app.main import _teklif_pdf_uretim_kilidi, _TeklifPdfUretimiSuruyor

        kazanan, kaybeden = [], []
        bariyer = threading.Barrier(8)

        def dene():
            bariyer.wait(timeout=10)
            try:
                with _teklif_pdf_uretim_kilidi(99):
                    kazanan.append(1)
                    threading.Event().wait(0.05)
            except _TeklifPdfUretimiSuruyor:
                kaybeden.append(1)

        isler = [threading.Thread(target=dene) for _ in range(8)]
        for t in isler:
            t.start()
        for t in isler:
            t.join(timeout=30)

        assert len(kazanan) == 1, f"tek üretim beklenirdi, {len(kazanan)} oldu"
        assert len(kaybeden) == 7
        assert _artiklar(storage_tmp) == []


# ═══════════════════════════════════════════════════════════════════════════
# File ↔ DB transaction sırası
# ═══════════════════════════════════════════════════════════════════════════

class TestDosyaVeritabaniSirasi:
    def test_db_commit_basarisizsa_orphan_dosya_temizlenir(self, client, db, storage_tmp):
        offer = _teklif(db)
        cagrilar: list = []
        gercek_commit = db.commit
        patladi = {"oldu": False}

        def patlayan_commit():
            if not patladi["oldu"]:
                patladi["oldu"] = True
                raise RuntimeError("DB commit basarisiz")
            return gercek_commit()

        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_sahte_uretici_fabrikasi(cagrilar)):
            with patch.object(db, "commit", side_effect=patlayan_commit):
                r = client.post(f"/offers/{offer.id}/generate-pdf")

        assert r.status_code == 500
        assert len(cagrilar) == 1, "dosya yayımlanmış olmalıydı"
        assert list(storage_tmp.rglob("*.pdf")) == [], "orphan dosya TEMİZLENMELİ"
        assert _artiklar(storage_tmp) == []

        db.expire_all()
        from app.database import Offer
        assert db.query(Offer).filter(Offer.id == offer.id).first().pdf_ref is None

    def test_dosya_uretimi_patlarsa_pdf_ref_yazilmaz(self, client, db, storage_tmp):
        offer = _teklif(db)

        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=OSError("disk dolu")):
            r = client.post(f"/offers/{offer.id}/generate-pdf")

        assert r.status_code == 500
        db.expire_all()
        from app.database import Offer
        assert db.query(Offer).filter(Offer.id == offer.id).first().pdf_ref is None
        assert _artiklar(storage_tmp) == [], "temp/lock artığı kalmamalı"

    def test_basarili_akista_artik_kalmaz(self, client, db, storage_tmp):
        offer = _teklif(db)
        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_sahte_uretici_fabrikasi([])):
            assert client.post(f"/offers/{offer.id}/generate-pdf").status_code == 200
        assert _artiklar(storage_tmp) == []


# ═══════════════════════════════════════════════════════════════════════════
# Download — containment + bütünlük
# ═══════════════════════════════════════════════════════════════════════════

class TestDownload:
    def test_pdf_yokken_404(self, client, db):
        offer = _teklif(db)
        assert client.get(f"/offers/{offer.id}/download").status_code == 404

    def test_uretilen_pdf_tam_olarak_indirilir(self, client, db):
        offer = _teklif(db)
        icerik = b"%PDF-" + b"G" * (256 * 1024)
        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_sahte_uretici_fabrikasi([], icerik=icerik)):
            client.post(f"/offers/{offer.id}/generate-pdf")

        r = client.get(f"/offers/{offer.id}/download")
        assert r.status_code == 200
        assert hashlib.sha256(r.content).hexdigest() == hashlib.sha256(icerik).hexdigest()
        assert f"teklif_{offer.id}.pdf" in r.headers.get("content-disposition", "")

    @pytest.mark.parametrize("kotu_ref", [
        "../../../etc/passwd",
        "..\\..\\..\\Windows\\win.ini",
        "C:\\Windows\\win.ini",
        "/etc/shadow",
    ])
    def test_containment_disi_ref_reddedilir(self, client, db, kotu_ref):
        from app.database import Offer

        offer = _teklif(db)
        db.query(Offer).filter(Offer.id == offer.id).update({"pdf_ref": kotu_ref})
        db.commit()

        r = client.get(f"/offers/{offer.id}/download")
        assert r.status_code in (400, 404), r.text
        if r.status_code == 400:
            assert r.json()["detail"] == "Geçersiz PDF referansı"

    def test_hata_mesaji_fiziksel_yolu_sizdirmaz(self, client, db):
        from app.database import Offer

        offer = _teklif(db)
        kotu = str(Path(os.getcwd()).resolve() / "gizli" / "dosya.pdf")
        db.query(Offer).filter(Offer.id == offer.id).update({"pdf_ref": kotu})
        db.commit()

        r = client.get(f"/offers/{offer.id}/download")
        assert r.status_code in (400, 404)
        assert "gizli" not in r.text, "fiziksel yol client'a SIZMAMALI"

    def test_symlink_ile_disari_kacis_reddedilir(self, client, db, storage_tmp, disari_kok):
        """Windows'ta symlink ayrıcalık ister; yoksa test atlanır."""
        from app.database import Offer

        disari = disari_kok / "disarida.pdf"
        disari.write_bytes(b"%PDF-gizli")
        link = Path(storage_tmp) / "offers" / "link.pdf"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(str(disari), str(link))
        except (OSError, NotImplementedError, AttributeError):
            pytest.skip("bu ortamda symlink oluşturulamıyor")

        offer = _teklif(db)
        db.query(Offer).filter(Offer.id == offer.id).update({"pdf_ref": str(link)})
        db.commit()

        r = client.get(f"/offers/{offer.id}/download")
        assert r.status_code in (400, 404), "symlink escape KABUL EDİLMEMELİ"

    @pytest.mark.skipif(os.name != "nt", reason="junction yalnız Windows'ta")
    def test_junction_ile_disari_kacis_reddedilir(self, client, db, storage_tmp, disari_kok):
        """
        Windows'ta junction YÖNETİCİ YETKİSİ İSTEMEZ — symlink'ten farklı
        olarak gerçek bir saldırı yüzeyidir. `Path.resolve()` reparse
        point'i çözdüğü için containment kontrolü bunu da reddetmelidir.
        """
        import subprocess
        from app.database import Offer

        disari = disari_kok / "disarida"
        disari.mkdir()
        (disari / "gizli.pdf").write_bytes(b"%PDF-gizli")

        link = Path(storage_tmp) / "offers" / "junction"
        link.parent.mkdir(parents=True, exist_ok=True)
        sonuc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(disari)],
            capture_output=True,
        )
        if sonuc.returncode != 0 or not link.exists():
            pytest.skip("bu ortamda junction oluşturulamıyor")

        offer = _teklif(db)
        db.query(Offer).filter(Offer.id == offer.id).update(
            {"pdf_ref": str(link / "gizli.pdf")}
        )
        db.commit()

        r = client.get(f"/offers/{offer.id}/download")
        assert r.status_code in (400, 404), "junction escape KABUL EDİLMEMELİ"
        assert b"%PDF-gizli" not in r.content


# ═══════════════════════════════════════════════════════════════════════════
# Liste / detay / PDF aynı immutable snapshot'tan
# ═══════════════════════════════════════════════════════════════════════════

class TestSnapshotTutarliligi:
    def test_liste_detay_ve_pdf_ayni_ticari_degerleri_kullanir(self, client, db):
        musteri = _musteri(db)
        offer = _teklif(db, customer=musteri)
        cagrilar: list = []

        with patch("app.pdf_generator.generate_and_store_offer_pdf",
                   side_effect=_sahte_uretici_fabrikasi(cagrilar)):
            client.post(f"/offers/{offer.id}/generate-pdf")

        liste = [o for o in client.get("/offers").json() if o["id"] == offer.id][0]
        detay = client.get(f"/offers/{offer.id}").json()

        for alan in ("consumption_kwh", "current_total", "offer_total",
                     "savings_amount", "savings_ratio", "agreement_multiplier",
                     "vendor", "invoice_period"):
            assert liste[alan] == detay[alan], f"{alan}: liste ≠ detay"

        # PDF üretimi de aynı snapshot'ı kullandı.
        assert cagrilar[0]["params"].agreement_multiplier == detay["agreement_multiplier"]
        assert cagrilar[0]["calculation"].savings_ratio == detay["calculation_result"]["savings_ratio"]

    def test_detay_ticari_alanlarin_tamamini_dondurur(self, client, db):
        offer = _teklif(db, customer=_musteri(db))
        detay = client.get(f"/offers/{offer.id}").json()
        for alan in ("customer", "vendor", "invoice_period", "consumption_kwh",
                     "current_total", "offer_total", "savings_amount",
                     "savings_ratio", "agreement_multiplier", "weighted_ptf",
                     "yekdem", "status", "pdf_ref"):
            assert alan in detay, f"detay yanıtında eksik alan: {alan}"


# ═══════════════════════════════════════════════════════════════════════════
# Frontend statik sözleşmesi
# ═══════════════════════════════════════════════════════════════════════════

# S5-R01 kapsamındaki persisted-offer yüzeyleri.
_OFFER_YUZEYLERI = (
    "crm-core/OffersScreen.tsx",
    "crm-core/OfferDetailModal.tsx",
    # S5-R01 tamamlama: teklif olusturma akisi da temizlendi (owner 1.1).
    "App.tsx",
)


class TestFrontendSozlesmesi:
    @pytest.mark.parametrize("dosya", _OFFER_YUZEYLERI)
    def test_teklif_yuzeyleri_generate_pdf_simple_cagirmiyor(self, dosya):
        """
        YORUM SATIRLARI SAYILMAZ: bu bilesenler yasak endpoint'i aciklayan
        aciklama yorumlari tasiyor. Naif substring taramasi bu yorumlara
        takilir ve testi anlamsiz kilardi; yalniz KOD satirlari denetlenir.
        """
        kod = _kod_satirlari((FRONTEND / dosya).read_text(encoding="utf-8"))
        assert "generate-pdf-simple" not in kod, (
            f"{dosya} persisted-offer akışında yasak endpoint'i çağırıyor"
        )

    def test_yeni_pdf_fonksiyonlari_offer_bound_endpointleri_kullanir(self):
        metin = (FRONTEND / "api.ts").read_text(encoding="utf-8")
        i = metin.index("export async function generateOfferPdf")
        j = metin.index("export async function downloadOfferPdf")
        son = metin.index("\n}", j)

        uretim = metin[i:j]
        indirme = metin[j:son]
        assert "/generate-pdf" in uretim and "generate-pdf-simple" not in uretim
        assert "/download" in indirme and "generate-pdf-simple" not in indirme
        # Fiziksel yol ile URL kurulmamalı.
        assert "pdf_ref" not in indirme, "indirme adresi `pdf_ref` ile kurulamaz"

    @pytest.mark.parametrize("dosya", _OFFER_YUZEYLERI)
    def test_pdf_ref_degeri_render_veya_log_edilmiyor(self, dosya):
        """
        `pdf_ref` YALNIZ varlık kontrolünde (Boolean) kullanılabilir; değeri
        ekrana yazılamaz, loglanamaz, URL'ye birleştirilemez.
        """
        kod = _kod_satirlari((FRONTEND / dosya).read_text(encoding="utf-8"))
        for satir in kod.splitlines():
            # Satır SONU yorumları da ayıklanır (`kod; // aciklama`), aksi
            # hâlde yalnız yorumda geçen `pdf_ref` yanlış pozitif üretir.
            temiz = satir.split("//", 1)[0].strip()
            if "pdf_ref" not in temiz:
                continue
            assert "Boolean(" in temiz, f"`pdf_ref` yalnız Boolean ile kullanılmalı: {temiz}"
            assert "console." not in temiz, f"`pdf_ref` loglanamaz: {temiz}"
            assert "${" not in temiz, f"`pdf_ref` URL'ye birleştirilemez: {temiz}"

    def test_teklif_olusturma_akisi_offer_bound_zinciri_kullanir(self):
        """
        Owner Bölüm 1.1: teklif oluşturma akışı teklifi bir kez persist eder,
        dönen GERÇEK `offer.id` ile offer-bound zinciri çağırır.
        """
        kod = _kod_satirlari((FRONTEND / "App.tsx").read_text(encoding="utf-8"))
        assert "createOffer(" in kod, "teklif oluşturma akışı App.tsx'te beklenirdi"
        assert "generateOfferPdf(" in kod, "offer-bound üretim çağrısı yok"
        assert "downloadOfferPdf(" in kod, "offer-bound indirme çağrısı yok"
        # Eski, teklife bağsız istemci fonksiyonu artık çağrılmamalı.
        assert "downloadPdf(" not in kod, (
            "`/generate-pdf-simple` sarmalayıcısı teklif akışında hâlâ çağrılıyor"
        )

    def test_generate_pdf_simple_endpointi_silinmedi(self):
        """
        Owner Bölüm 1.1 madde 7: endpoint geriye dönük uyumluluk için
        SİLİNMEZ — yalnız persist edilmiş teklif akışında çağrılmaz.
        """
        backend_main = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(
            encoding="utf-8"
        )
        assert '@app.post("/generate-pdf-simple")' in backend_main, (
            "endpoint bu fazda silinmemeliydi"
        )
        api_metni = (FRONTEND / "api.ts").read_text(encoding="utf-8")
        assert "generate-pdf-simple" in api_metni, (
            "istemci sarmalayıcısı da korunmalı (yalnız teklif akışında kullanılmaz)"
        )


# ═══════════════════════════════════════════════════════════════════════════
# R2 gross-misread guard — persist kapısı ve iki uygulamanın paritesi
# ═══════════════════════════════════════════════════════════════════════════

class TestMismatchGuardPersistKapisi:
    """
    Teklif PDF akışı `/generate-pdf-simple` çağırmayı bıraktığı için R2
    sapma kapısı PERSIST anına taşındı. Bu testler kapının GERÇEKTEN
    çalıştığını ve sapmalı teklifin HİÇ KAYDEDİLMEDİĞİNİ kanıtlar.
    """

    def _govde(self, **ezilen):
        hesap = dict(HESAP_SONUCU)
        hesap.update(ezilen)
        return {
            "extraction": {
                "consumption_kwh": {"value": 1000.0, "confidence": 1.0},
                "current_active_unit_price_tl_per_kwh": {"value": 2.0, "confidence": 1.0},
            },
            "calculation": hesap,
            "params": {"agreement_multiplier": 1.01},
        }

    def test_buyuk_sapma_bloke_eder_ve_teklif_kaydedilmez(self, client, db):
        from app.database import Offer

        # computed_total = 2400 + 480 = 2880; ham toplam 1000 → ~%188 sapma
        r = client.post("/offers", json=self._govde(), params={"invoice_total_raw": 1000.0})
        assert r.status_code == 422
        hata = r.json()["error"]
        assert hata["code"] == "extraction_mismatch"
        assert hata["requires_operator_confirmation"] is False, ">%40 onayla bile geçmemeli"
        assert db.query(Offer).count() == 0, "sapmalı teklif KAYDEDİLMEMELİ"

    def test_buyuk_sapma_operator_onayiyla_da_gecmez(self, client, db):
        from app.database import Offer

        r = client.post("/offers", json=self._govde(),
                        params={"invoice_total_raw": 1000.0,
                                "operator_confirmed_warnings": True})
        assert r.status_code == 422
        assert r.json()["error"]["requires_operator_confirmation"] is False
        assert db.query(Offer).count() == 0

    def test_orta_sapma_onay_ister(self, client, db):
        from app.database import Offer

        # 2880 vs 2400 → %20 sapma (WARNING bandı)
        r = client.post("/offers", json=self._govde(), params={"invoice_total_raw": 2400.0})
        assert r.status_code == 422
        assert r.json()["error"]["requires_operator_confirmation"] is True
        assert db.query(Offer).count() == 0

    # ── GEÇİŞ vakaları neden endpoint üzerinden test EDİLMİYOR ────────────
    # BAŞARILI bir `POST /offers` TestClient altında ASILIYOR: `create_offer`
    # `async def` olup senkron SQLAlchemy işini event-loop thread'inde yapıyor
    # ve paylaşılan session + StaticPool ile TestClient portal'ı kilitleniyor.
    # Bu ÖNCEDEN MEVCUT bir harness kısıtıdır — pristine HEAD worktree'sinde
    # birebir aynı probe ile doğrulandı (repoda başarılı POST /offers testi
    # olmamasının sebebi de bu). S5-R01 kapsamı dışında olduğu için
    # düzeltilmedi; bunun yerine geçiş vakaları SAF yardımcı üzerinde, kapının
    # gerçekten bağlı olduğu ise AST ile kanıtlanır.

    def test_gecis_vakalari_saf_yardimcida_dogrulanir(self):
        from app.main import extraction_mismatch_contract

        ortak = dict(
            consumption_kwh=1000.0, current_unit_price=2.0,
            current_energy_tl=HESAP_SONUCU["current_energy_tl"],
            current_vat_matrah_tl=HESAP_SONUCU["current_vat_matrah_tl"],
            current_vat_tl=HESAP_SONUCU["current_vat_tl"],
        )
        # Sapma yok (2880 vs 2880) → geçer
        assert extraction_mismatch_contract(
            invoice_total_raw=2880.0, operator_confirmed_warnings=False, **ortak) is None
        # %20 sapma + operatör onayı → geçer
        assert extraction_mismatch_contract(
            invoice_total_raw=2400.0, operator_confirmed_warnings=True, **ortak) is None
        # Ham toplam gönderilmedi → kıyas atlanır (geriye dönük uyumlu)
        assert extraction_mismatch_contract(
            invoice_total_raw=0, operator_confirmed_warnings=False, **ortak) is None

    def test_endpoint_kapiyi_snapshot_degerleriyle_cagiriyor(self):
        """
        WIRING KANITI: `create_offer` guard'ı GERÇEKTEN çağırıyor ve argümanları
        istemcinin serbest gönderdiği gövdeden değil, `extraction`/`calculation`
        alanlarından türetiyor.
        """
        import ast

        kaynak = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(
            encoding="utf-8"
        )
        agac = ast.parse(kaynak)
        fn = next(
            d for d in ast.walk(agac)
            if isinstance(d, (ast.AsyncFunctionDef, ast.FunctionDef)) and d.name == "create_offer"
        )
        cagrilar = [
            c for c in ast.walk(fn)
            if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "extraction_mismatch_contract"
        ]
        assert len(cagrilar) == 1, "create_offer guard'ı tam bir kez çağırmalı"

        kwargs = {kw.arg: ast.unparse(kw.value) for kw in cagrilar[0].keywords}
        assert kwargs["current_energy_tl"] == "calculation.current_energy_tl"
        assert kwargs["current_vat_matrah_tl"] == "calculation.current_vat_matrah_tl"
        assert kwargs["current_vat_tl"] == "calculation.current_vat_tl"
        assert kwargs["consumption_kwh"].startswith("extraction.consumption_kwh")
        assert kwargs["invoice_total_raw"] == "invoice_total_raw"
        assert kwargs["operator_confirmed_warnings"] == "operator_confirmed_warnings"

    def test_guard_girdileri_pdf_endpointine_gitmiyor(self):
        """
        Guard girdileri PERSIST kapısına aittir; PDF üretimi %100
        snapshot-bound kalmalıdır.
        """
        from app.main import generate_pdf_for_offer

        imza = inspect.signature(generate_pdf_for_offer)
        assert "invoice_total_raw" not in imza.parameters
        assert "operator_confirmed_warnings" not in imza.parameters

    def test_iki_uygulama_ayni_verdikti_verir(self, client, db):
        """
        PARİTE: kural hem `extraction_mismatch_contract` (POST /offers) hem de
        `/generate-pdf-simple` içinde satır içi duruyor. İkinci endpoint owner
        tarafından donduruldu, bu yüzden ortak yardımcıya çekilemedi — bu test
        iki kopyanın AYRIŞMASINI yakalar.
        """
        from app.main import extraction_mismatch_contract

        def _yardimci(ham, onay):
            return extraction_mismatch_contract(
                consumption_kwh=1000.0, current_unit_price=2.0,
                current_energy_tl=HESAP_SONUCU["current_energy_tl"],
                current_vat_matrah_tl=HESAP_SONUCU["current_vat_matrah_tl"],
                current_vat_tl=HESAP_SONUCU["current_vat_tl"],
                invoice_total_raw=ham, operator_confirmed_warnings=onay,
            )

        def _simple(ham, onay):
            return client.post("/generate-pdf-simple", data={
                "consumption_kwh": 1000.0, "current_unit_price": 2.0,
                "current_energy_tl": HESAP_SONUCU["current_energy_tl"],
                "current_vat_matrah_tl": HESAP_SONUCU["current_vat_matrah_tl"],
                "current_vat_tl": HESAP_SONUCU["current_vat_tl"],
                "offer_energy_tl": HESAP_SONUCU["offer_energy_tl"],
                "offer_total": HESAP_SONUCU["offer_total_with_vat_tl"],
                "savings_ratio": HESAP_SONUCU["savings_ratio"],
                "invoice_total_raw": ham,
                "operator_confirmed_warnings": onay,
            })

        # YALNIZ REDDEDİLEN vakalar endpoint üzerinden karşılaştırılır: bunlar
        # 422 ile render'dan ÖNCE döner. Sapma OLMAYAN vaka `/generate-pdf-simple`
        # içinde gerçek reportlab render'ı tetikler (yavaş/askıda kalabilir) ve
        # bu testin konusu değildir — o vaka yalnız yardımcı üzerinde doğrulanır.
        for ham, onay in ((1000.0, False), (1000.0, True), (2400.0, False)):
            beklenen = _yardimci(ham, onay)
            assert beklenen is not None, f"yardımcı reddetmeliydi (ham={ham}, onay={onay})"

            yanit = _simple(ham, onay)
            assert yanit.status_code == 422, f"parite bozuk (ham={ham}, onay={onay})"
            govde = yanit.json().get("error", {})
            assert govde.get("code") == "extraction_mismatch", (
                f"parite bozuk (ham={ham}, onay={onay})"
            )
            assert (
                beklenen["error"]["requires_operator_confirmation"]
                == govde["requires_operator_confirmation"]
            ), f"onay şartı ayrışmış (ham={ham}, onay={onay})"

        # Sapma yoksa yardımcı GEÇİRMELİ (endpoint'e gidilmez).
        assert _yardimci(2880.0, False) is None
        assert _yardimci(2400.0, True) is None, "onaylanan warning geçmeli"
