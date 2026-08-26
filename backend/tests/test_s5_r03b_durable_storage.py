"""
S5-R03B — Durable (upgrade-safe) PDF/belge storage + offline UI + güvenlik.

Owner Bölüm 11 zorunlu testleri + mutation kapıları. Kapsam:
- Dual-root (durable base_dir + legacy_base_dir) resolve/put sözleşmesi
- Legacy absolute ref containment + kaçış reddi (mevcut R01 tekniklerinin
  dual-root'a genişletilmesi)
- Root symlink/junction fail-closed (storage kökünün KENDİSİ)
- migrate_legacy_artifact: başarı + hash-mismatch rollback + containment reddi
- Upgrade-survival rehearsal (owner'ın 8 maddesi, tamamen disposable dizinler)
- Executable/install kökünde yeni PDF sıfır (uçtan uca STORAGE_DIR ile)
- UI/API fiziksel yol sızıntısı sıfır
- Google Fonts statik taraması sıfır
- `/generate-pdf-direct` public error sanitize + mutation kapısı
- Mutation kapıları: absolute pdf_ref geri gelirse / legacy containment
  kaldırılırsa / str(e) geri gelirse / Google Fonts linki geri gelirse FAIL

Çağrıldığı yerler:
- pytest tam regresyon + S5 kabul paketi (otomatik)
"""
from __future__ import annotations

import ast
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_KOK = Path(__file__).resolve().parents[2]

# CalculationResult'un zorunlu alanları — test_s5_r03a_pdf_engine.py ile
# aynı sentetik küme (generate_pdf_for_offer endpoint'i offer.calculation_
# result'tan tam bir CalculationResult yeniden kurar; eksik alan 500 verir).
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
# Ortak fixture'lar (test_s5_r03a_pdf_engine.py ile aynı bağımsız kalıp)
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

    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "durable"))
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


def _teklif(db):
    from app.database import Offer

    o = Offer(
        tenant_id="default",
        customer_id=None,
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
    db.add(o)
    db.flush()
    db.commit()
    return o


def _fonksiyon_ast(dosya: Path, ad: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    agac = ast.parse(dosya.read_text(encoding="utf-8"))
    for dugum in ast.walk(agac):
        if isinstance(dugum, (ast.FunctionDef, ast.AsyncFunctionDef)) and dugum.name == ad:
            return dugum
    raise AssertionError(f"{dosya.name} icinde {ad} bulunamadi")


# ═══════════════════════════════════════════════════════════════════════════
# 1) Dual-root: yeni yazım durable köke, legacy okunabilir, kaçış reddi
# ═══════════════════════════════════════════════════════════════════════════

class TestDualRootSozlesmesi:
    def test_relative_ref_cwd_bagimsiz_durable_koke_cozulur(self, tmp_path, monkeypatch):
        """ÇEKİRDEK düzeltme: relative ref, hangi CWD'den çağrılırsa
        çağrılsın HER ZAMAN base_dir'e göre çözülür (eski hata: CWD'ye
        göre çözüyordu — packaged'da CWD=kurulum dizini idi)."""
        from app.services.storage_local import LocalStorage

        durable = tmp_path / "durable"
        storage = LocalStorage(base_dir=str(durable))
        ref = storage.put_bytes("offers/1/offer.pdf", b"%PDF-x", "application/pdf")
        assert ref == "offers/1/offer.pdf"

        beklenen = str((durable / "offers" / "1" / "offer.pdf").resolve())

        decoy = tmp_path / "baska_calisma_dizini"
        decoy.mkdir()
        eski_cwd = os.getcwd()
        try:
            os.chdir(str(decoy))
            assert storage.resolve_local_path(ref) == beklenen, (
                "relative ref CWD'ye göre çözülmüş — durable-root garantisi bozuk"
            )
        finally:
            os.chdir(eski_cwd)

    def test_legacy_absolute_ref_okunabilir(self, tmp_path):
        from app.services.storage_local import LocalStorage

        durable = tmp_path / "durable"
        legacy = tmp_path / "legacy"
        legacy_dosya = legacy / "offers" / "6" / "offer.pdf"
        legacy_dosya.parent.mkdir(parents=True)
        legacy_dosya.write_bytes(b"%PDF-eski-surum")

        storage = LocalStorage(base_dir=str(durable), legacy_base_dir=str(legacy))
        cozulen = storage.resolve_local_path(str(legacy_dosya))
        assert cozulen == str(legacy_dosya.resolve())
        assert Path(cozulen).read_bytes() == b"%PDF-eski-surum"

    def test_legacy_base_dir_verilmezse_legacy_okuma_devre_disi(self, tmp_path):
        """Dev/test varsayılanı: legacy_base_dir=None → yalnız base_dir
        geçerlidir (geriye dönük uyumluluk — mevcut testler bunu varsayar)."""
        from app.services.storage_local import LocalStorage

        durable = tmp_path / "durable"
        disari = tmp_path / "disarida" / "x.pdf"
        disari.parent.mkdir(parents=True)
        disari.write_bytes(b"x")

        storage = LocalStorage(base_dir=str(durable))
        assert storage.legacy_base_dir is None
        with pytest.raises(ValueError):
            storage.resolve_local_path(str(disari))

    def test_her_iki_kok_disinda_absolute_ref_reddedilir(self, tmp_path):
        from app.services.storage_local import LocalStorage

        durable = tmp_path / "durable"
        legacy = tmp_path / "legacy"
        disari = tmp_path / "ucuncu_dizin" / "gizli.pdf"
        disari.parent.mkdir(parents=True)
        disari.write_bytes(b"gizli")

        storage = LocalStorage(base_dir=str(durable), legacy_base_dir=str(legacy))
        with pytest.raises(ValueError, match="path traversal"):
            storage.resolve_local_path(str(disari))

    def test_relative_ref_ile_de_disari_kacis_reddedilir(self, tmp_path):
        from app.services.storage_local import LocalStorage

        durable = tmp_path / "durable"
        storage = LocalStorage(base_dir=str(durable))
        with pytest.raises(ValueError, match="path traversal"):
            storage.resolve_local_path("../../disarida/x.pdf")

    def test_legacy_symlink_kacisi_reddedilir(self, tmp_path):
        """R01'in symlink-kaçış tekniği legacy_base_dir'e genişletildi."""
        from app.services.storage_local import LocalStorage

        durable = tmp_path / "durable"
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        disari = tmp_path / "disarida"
        disari.mkdir()
        (disari / "gizli.pdf").write_bytes(b"%PDF-gizli")

        link = legacy / "sizinti"
        try:
            os.symlink(str(disari), str(link))
        except OSError:
            pytest.skip("bu ortamda symlink oluşturulamıyor")

        storage = LocalStorage(base_dir=str(durable), legacy_base_dir=str(legacy))
        with pytest.raises(ValueError, match="path traversal"):
            storage.resolve_local_path(str(link / "gizli.pdf"))

    @pytest.mark.skipif(os.name != "nt", reason="junction yalnız Windows'ta")
    def test_legacy_junction_kacisi_reddedilir(self, tmp_path):
        from app.services.storage_local import LocalStorage

        durable = tmp_path / "durable"
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        disari = tmp_path / "disarida"
        disari.mkdir()
        (disari / "gizli.pdf").write_bytes(b"%PDF-gizli")

        link = legacy / "junction_sizinti"
        sonuc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(disari)], capture_output=True,
        )
        if sonuc.returncode != 0 or not link.exists():
            pytest.skip("bu ortamda junction oluşturulamıyor")

        storage = LocalStorage(base_dir=str(durable), legacy_base_dir=str(legacy))
        with pytest.raises(ValueError, match="path traversal"):
            storage.resolve_local_path(str(link / "gizli.pdf"))

    def test_case_alias_kacisi_reddedilir(self, tmp_path):
        """Kök ve referans aynı normalizasyondan geçtiği için Windows'un
        case-insensitive dosya sisteminde bile kardeş-dizin kaçışı olmaz."""
        from app.services.storage_local import LocalStorage

        durable = tmp_path / "Durable"
        durable.mkdir()
        kardes = tmp_path / "Durable_Evil"
        kardes.mkdir()
        (kardes / "x.pdf").write_bytes(b"x")

        storage = LocalStorage(base_dir=str(durable))
        with pytest.raises(ValueError, match="path traversal"):
            storage.resolve_local_path(str(kardes / "x.pdf").upper())

    def test_missing_legacy_dosya_fail_closed_sessiz_regenerate_yok(self, tmp_path):
        """Legacy ref containment'tan geçer (yol yapısal olarak geçerli)
        ama fiziksel dosya yoksa: exists()=False, sessiz üretim YOK."""
        from app.services.storage_local import LocalStorage

        durable = tmp_path / "durable"
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        hic_olmayan = legacy / "offers" / "99" / "offer.pdf"

        storage = LocalStorage(base_dir=str(durable), legacy_base_dir=str(legacy))
        assert storage.exists(str(hic_olmayan)) is False
        assert storage.get_local_path(str(hic_olmayan)) is not None, (
            "containment geçerliyse yol dönmeli — yokluk ayrı bir kontrol"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 2) Storage kökünün KENDİSİ symlink/junction ise fail-closed
# ═══════════════════════════════════════════════════════════════════════════

class TestKokReparsePointFailClosed:
    def test_var_olan_symlink_kok_reddedilir(self, tmp_path):
        gercek_hedef = tmp_path / "gercek_disari"
        gercek_hedef.mkdir()
        kok_link = tmp_path / "storage_link"
        try:
            os.symlink(str(gercek_hedef), str(kok_link))
        except OSError:
            pytest.skip("bu ortamda symlink oluşturulamıyor")

        from app.services.storage_local import LocalStorage

        with pytest.raises(ValueError, match="reparse point"):
            LocalStorage(base_dir=str(kok_link))

    @pytest.mark.skipif(os.name != "nt", reason="junction yalnız Windows'ta")
    def test_var_olan_junction_kok_reddedilir(self, tmp_path):
        gercek_hedef = tmp_path / "gercek_disari"
        gercek_hedef.mkdir()
        kok_link = tmp_path / "storage_junction"
        sonuc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(kok_link), str(gercek_hedef)],
            capture_output=True,
        )
        if sonuc.returncode != 0 or not kok_link.exists():
            pytest.skip("bu ortamda junction oluşturulamıyor")

        from app.services.storage_local import LocalStorage

        with pytest.raises(ValueError, match="reparse point"):
            LocalStorage(base_dir=str(kok_link))

    def test_normal_dizin_reddedilmez(self, tmp_path):
        """Negatif kontrol: sıradan bir dizin reparse-point sanılıp
        yanlışlıkla reddedilmemeli."""
        from app.services.storage_local import LocalStorage

        normal = tmp_path / "normal_durable"
        storage = LocalStorage(base_dir=str(normal))
        assert storage.base_dir == normal.resolve()

    def test_henuz_var_olmayan_kok_reddedilmez(self, tmp_path):
        """Henüz oluşturulmamış bir dizin reparse-point OLAMAZ; `mkdir`
        normal bir dizin yaratır — fail-closed kontrolü yanlış-pozitif
        vermemeli."""
        from app.services.storage_local import LocalStorage

        henuz_yok = tmp_path / "olusturulacak" / "durable"
        storage = LocalStorage(base_dir=str(henuz_yok))
        assert storage.base_dir.is_dir()


# ═══════════════════════════════════════════════════════════════════════════
# 3) migrate_legacy_artifact — installer-fazına hazırlık primitifi
# ═══════════════════════════════════════════════════════════════════════════

class TestMigrateLegacyArtifact:
    def test_basarili_migrasyon_hash_dogrulanir_kaynak_korunur(self, tmp_path):
        from app.services.storage_local import LocalStorage

        durable = tmp_path / "durable"
        legacy = tmp_path / "legacy"
        legacy_dosya = legacy / "offers" / "6" / "offer.pdf"
        legacy_dosya.parent.mkdir(parents=True)
        icerik = b"%PDF-tasinacak-icerik" * 100
        legacy_dosya.write_bytes(icerik)
        beklenen_hash = hashlib.sha256(icerik).hexdigest()

        storage = LocalStorage(base_dir=str(durable), legacy_base_dir=str(legacy))
        yeni_ref = storage.migrate_legacy_artifact(
            str(legacy_dosya), "offers/6/offer.pdf", "application/pdf"
        )

        assert yeni_ref == "offers/6/offer.pdf", "dönüş relative anahtar olmalı"
        hedef = Path(storage.resolve_local_path(yeni_ref))
        assert hedef.exists()
        assert hashlib.sha256(hedef.read_bytes()).hexdigest() == beklenen_hash

        # KAYNAK ASLA silinmez (owner: "Kaynak silme ancak ayrı installer GO ile").
        assert legacy_dosya.exists(), "migrasyon kaynağı silmemeli"
        assert legacy_dosya.read_bytes() == icerik

    def test_hash_uyusmazliginda_rollback_orphan_birakmaz(self, tmp_path, monkeypatch):
        from app.services import storage_local as sl

        durable = tmp_path / "durable"
        legacy = tmp_path / "legacy"
        legacy_dosya = legacy / "offers" / "7" / "offer.pdf"
        legacy_dosya.parent.mkdir(parents=True)
        legacy_dosya.write_bytes(b"orijinal-icerik")

        storage = sl.LocalStorage(base_dir=str(durable), legacy_base_dir=str(legacy))

        # read_bytes'ı yalnız DOĞRULAMA okumasında bozuk veri dönecek şekilde
        # sahteleyerek hash-mismatch senaryosunu deterministik üretiyoruz.
        gercek_read_bytes = Path.read_bytes
        cagri_sayaci = {"n": 0}

        def sahte_read_bytes(self):
            cagri_sayaci["n"] += 1
            if cagri_sayaci["n"] == 2:  # 1: kaynak okuma, 2: doğrulama okuması
                return b"BOZULMUS-VERI"
            return gercek_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", sahte_read_bytes)

        with pytest.raises(RuntimeError, match="hash doğrulaması başarısız"):
            storage.migrate_legacy_artifact(
                str(legacy_dosya), "offers/7/offer.pdf", "application/pdf"
            )

        monkeypatch.setattr(Path, "read_bytes", gercek_read_bytes)
        # Rollback: yayımlanan (yanlış) dosya geri alınmış olmalı.
        hedef = durable / "offers" / "7" / "offer.pdf"
        assert not hedef.exists(), "hash-mismatch sonrası hedef dosya kalmamalı (orphan)"
        # Kaynak dokunulmamış.
        assert legacy_dosya.read_bytes() == b"orijinal-icerik"

    def test_containment_disi_kaynak_migrasyona_kabul_edilmez(self, tmp_path):
        from app.services.storage_local import LocalStorage

        durable = tmp_path / "durable"
        legacy = tmp_path / "legacy"
        disari = tmp_path / "disarida" / "x.pdf"
        disari.parent.mkdir(parents=True)
        disari.write_bytes(b"x")

        storage = LocalStorage(base_dir=str(durable), legacy_base_dir=str(legacy))
        with pytest.raises(ValueError, match="path traversal"):
            storage.migrate_legacy_artifact(str(disari), "offers/1/offer.pdf", "application/pdf")


# ═══════════════════════════════════════════════════════════════════════════
# 4) Upgrade-survival rehearsal — owner'ın 8 maddesi (tamamen disposable)
# ═══════════════════════════════════════════════════════════════════════════

class TestUpgradeSurvivalRehearsal:
    """
    Gerçek kurulu v1.0.6 veya production verisi KULLANILMAZ — hepsi
    `tmp_path` altında sahte/disposable dizinlerdir.
    """

    def test_8_maddelik_rehearsal(self, tmp_path):
        from app.services.storage_local import LocalStorage

        # 1) Sahte v1.0.6 install root.
        install_root = tmp_path / "install_root_v1_sahte"
        legacy_storage = install_root / "resources" / "backend" / "storage"

        # 2) Legacy PDF + absolute pdf_ref üret (eski sözleşme simülasyonu:
        #    doğrudan dosya yazımı, put_bytes'ın YENİ relative dönüşünü
        #    KULLANMADAN — gerçek eski davranışı taklit eder).
        legacy_pdf = legacy_storage / "offers" / "6" / "offer.pdf"
        legacy_pdf.parent.mkdir(parents=True)
        legacy_icerik = b"%PDF-v1.0.6-eski-teklif" * 50
        legacy_pdf.write_bytes(legacy_icerik)
        legacy_absolute_ref = str(legacy_pdf)

        # 3) Yeni durable data root (userData benzeri, install_root'tan AYRI).
        durable_root = tmp_path / "userData_gelka_enerji" / "storage"
        storage = LocalStorage(base_dir=str(durable_root), legacy_base_dir=str(legacy_storage))

        # 6 (mantıksal olarak upgrade ÖNCESİ çalışması gereken adım — gerçek
        # akışta rescue, uninstallOldVersion'DAN ÖNCE koşar): legacy PDF'yi
        # migration planıyla hash-identik taşı.
        migrated_ref = storage.migrate_legacy_artifact(
            legacy_absolute_ref, "offers/6/offer.pdf", "application/pdf"
        )
        assert hashlib.sha256(
            Path(storage.resolve_local_path(migrated_ref)).read_bytes()
        ).hexdigest() == hashlib.sha256(legacy_icerik).hexdigest(), (
            "madde 6: legacy PDF hash-identik taşınamadı"
        )

        # Migrasyon sonrası YENİ bir teklif için gerçek uygulama akışını taklit
        # eden bir yazım (offer id=1, durable köke).
        yeni_ref = storage.put_bytes("offers/1/offer.pdf", b"%PDF-yeni-teklif", "application/pdf")
        assert not os.path.isabs(yeni_ref)

        # 4) Install root'u upgrade gibi kaldır (installer.nsh'nin
        #    uninstallOldVersion'ı `resources\backend\storage` dahil TÜM
        #    eski kurulum ağacını böyle siler).
        import shutil

        shutil.rmtree(install_root)
        assert not install_root.exists()

        # 5) Durable root'taki YENİ PDF korunmuş olmalı.
        yeni_fiziksel = Path(storage.resolve_local_path(yeni_ref))
        assert yeni_fiziksel.exists(), "madde 5: yeni PDF install-root silinince kayboldu"
        assert yeni_fiziksel.read_bytes() == b"%PDF-yeni-teklif"

        # 6 (doğrulama tekrarı): migre edilmiş PDF de install_root silindikten
        #    SONRA hâlâ okunabilir (artık yalnız durable kökte yaşıyor).
        migrated_fiziksel = Path(storage.resolve_local_path(migrated_ref))
        assert migrated_fiziksel.exists()
        assert hashlib.sha256(migrated_fiziksel.read_bytes()).hexdigest() == hashlib.sha256(
            legacy_icerik
        ).hexdigest()

        # 7) "Uygulama binary/resources dizini silinse dahi durable PDF
        #    korunmalı" — install_root (binary/resources'ı temsil eder) ZATEN
        #    yukarıda silindi; bu madde 5/6 ile aynı kanıtla örtüşür, ayrıca
        #    AÇIKÇA doğrulanır: durable_root install_root'un İÇİNDE DEĞİLDİR.
        assert not str(durable_root.resolve()).startswith(str(tmp_path / "install_root_v1_sahte"))

        # 8) Uninstall simülasyonu (yalnız userData/durable_root'un KENDİSİNİ
        #    silmek DIŞINDA hiçbir işlem) kullanıcı verisini SİLMEMELİ —
        #    yani durable_root'a dokunan bir "uninstall" adımı bu akışta HİÇ
        #    yok; gerçek NSIS `deleteAppDataOnUninstall:false` (main.js/
        #    installer.nsh bulgusu) ile tutarlı olarak durable_root install
        #    silme işleminden TAMAMEN bağımsız yaşamaya devam eder — yukarıki
        #    `shutil.rmtree(install_root)` sonrası durable_root'un hâlâ TAM
        #    ve bozulmamış durması bunun doğrudan kanıtıdır.
        assert durable_root.exists()
        assert len(list(durable_root.rglob("*.pdf"))) == 2, "durable kökte tam olarak 2 PDF kalmalı"


# ═══════════════════════════════════════════════════════════════════════════
# 5) Executable/install kökünde yeni PDF sıfır — uçtan uca STORAGE_DIR
# ═══════════════════════════════════════════════════════════════════════════

class TestInstallKokundeSifirYeniPdf:
    def test_storage_dir_override_ile_yeni_pdf_yalniz_durable_kokte(
        self, tmp_path, monkeypatch
    ):
        """
        `get_storage()`'ın gerçek env-tabanlı davranışını (Electron'un
        STORAGE_DIR ile geçirdiği yol) uçtan uca kanıtlar: "install root"u
        temsil eden CWD-göreli varsayılan `./storage` konumunda SIFIR yeni
        dosya oluşmalı; hepsi STORAGE_DIR'e (durable) gitmeli.
        """
        from app.core.config import settings
        from app.services.storage import clear_storage_cache

        sahte_install_root = tmp_path / "sahte_kurulum_koku"
        sahte_install_root.mkdir()
        sahte_durable_root = tmp_path / "sahte_userData" / "storage"

        eski_cwd = os.getcwd()
        try:
            os.chdir(str(sahte_install_root))  # packaged'daki CWD=install_root taklidi
            monkeypatch.setattr(settings, "storage_dir", str(sahte_durable_root))
            clear_storage_cache()
            from app.services.storage import get_storage

            storage = get_storage()
            assert storage.legacy_base_dir == (sahte_install_root / "storage").resolve(), (
                "legacy kök CWD-göreli './storage' formülüyle hesaplanmalı"
            )

            ref = storage.put_bytes("offers/1/offer.pdf", b"%PDF-test", "application/pdf")
            assert not os.path.isabs(ref)

            install_root_storage = sahte_install_root / "storage"
            yeni_pdf_install_kokunde = (
                list(install_root_storage.rglob("*.pdf")) if install_root_storage.is_dir() else []
            )
            assert yeni_pdf_install_kokunde == [], (
                f"install-root-göreli konumda BEKLENMEYEN yeni PDF: {yeni_pdf_install_kokunde}"
            )

            fiziksel = Path(storage.resolve_local_path(ref))
            assert str(fiziksel).startswith(str(sahte_durable_root.resolve())), (
                "yeni PDF durable kök DIŞINDA bir yere yazılmış"
            )
        finally:
            os.chdir(eski_cwd)
            clear_storage_cache()


# ═══════════════════════════════════════════════════════════════════════════
# 6) UI/API fiziksel yol sızıntısı sıfır
# ═══════════════════════════════════════════════════════════════════════════

class TestFizikselYolSizintisiYok:
    def test_generate_pdf_response_fiziksel_yol_icermez(self, client, db, storage_tmp):
        from unittest.mock import patch

        offer = _teklif(db)

        def _sahte(**kw):
            from app.services.storage import get_storage

            return get_storage().put_bytes(
                f"offers/{kw['offer_id']}/offer.pdf", b"%PDF-sizinti-testi", "application/pdf"
            )

        with patch("app.pdf_generator.generate_and_store_offer_pdf", side_effect=_sahte):
            r = client.post(f"/offers/{offer.id}/generate-pdf")

        assert r.status_code == 200
        govde = r.text
        assert str(storage_tmp) not in govde, "response fiziksel storage_tmp yolunu içeriyor"
        assert ":\\\\" not in govde and ":/" not in govde.replace("http://", "").replace(
            "https://", ""
        ), "response bir Windows sürücü harfi/mutlak yol içeriyor olabilir"
        pdf_ref = r.json()["pdf_ref"]
        assert not os.path.isabs(pdf_ref), "pdf_ref mutlak yol olarak sızmış"


# ═══════════════════════════════════════════════════════════════════════════
# 7) Google Fonts / dış ağ — statik tarama
# ═══════════════════════════════════════════════════════════════════════════

class TestGoogleFontsStatikTarama:
    def test_index_html_google_fonts_referansi_icermez(self):
        icerik = (REPO_KOK / "frontend" / "index.html").read_text(encoding="utf-8")
        for yasakli in ("googleapis.com", "gstatic.com", "fonts.google"):
            assert yasakli not in icerik, f"frontend/index.html hâlâ '{yasakli}' içeriyor"

    def test_index_css_google_fonts_referansi_icermez(self):
        icerik = (REPO_KOK / "frontend" / "src" / "index.css").read_text(encoding="utf-8")
        for yasakli in ("googleapis.com", "gstatic.com", "fonts.google"):
            assert yasakli not in icerik, f"frontend/src/index.css hâlâ '{yasakli}' içeriyor"

    def test_frontend_src_genelinde_cdn_at_import_yok(self):
        src_kok = REPO_KOK / "frontend" / "src"
        for dosya in src_kok.rglob("*"):
            if not dosya.is_file() or dosya.suffix not in (".ts", ".tsx", ".css", ".js", ".jsx"):
                continue
            icerik = dosya.read_text(encoding="utf-8", errors="ignore")
            assert "googleapis.com" not in icerik and "gstatic.com" not in icerik, (
                f"{dosya.relative_to(REPO_KOK)} dış font CDN referansı içeriyor"
            )

    def test_mutation_google_fonts_linki_geri_gelirse_kirilir(self):
        """Mutation kanıtı: index.html'e Google Fonts linki EKLENSE bu test
        FAIL eder (yukarıdaki testlerin aynısı — burada AÇIKÇA doğrulanan
        yeniden-ekleme senaryosu ile)."""
        icerik = (REPO_KOK / "frontend" / "index.html").read_text(encoding="utf-8")
        sahte_mutasyon = icerik + '\n<link href="https://fonts.googleapis.com/x" rel="stylesheet">'
        assert "googleapis.com" in sahte_mutasyon  # mutasyonun kendisi doğru enjekte edildi
        # gerçek dosya bu diziyi içermediği için üstteki testler PASS kalır;
        # bu test yalnız mutasyonun DETECT EDİLEBİLİR olduğunu kanıtlar.
        assert "googleapis.com" not in icerik


# ═══════════════════════════════════════════════════════════════════════════
# 8) /generate-pdf-direct — public error sanitize + mutation kapısı
# ═══════════════════════════════════════════════════════════════════════════

class TestGeneratePdfDirectSanitize:
    def test_hata_yolunda_ic_detay_sizmaz(self, client):
        from unittest.mock import patch

        gecersiz_govde = {
            "extraction": {"meta": {}},
            "calculation": dict(HESAP_SONUCU),
            "params": {
                "weighted_ptf_tl_per_mwh": 100.0,
                "yekdem_tl_per_mwh": 10.0,
                "agreement_multiplier": 1.01,
            },
            "customer_name": "GİZLİ MÜŞTERİ ADI",
        }

        with patch(
            "app.main.generate_offer_pdf",
            side_effect=RuntimeError(
                "ic detay: C:/cok/gizli/yol/motor.dll GİZLİ MÜŞTERİ ADI yuklenemedi"
            ),
        ):
            r = client.post("/generate-pdf-direct", json=gecersiz_govde)

        assert r.status_code == 500
        govde = r.text
        assert "gizli" not in govde and "motor.dll" not in govde and "C:/" not in govde
        assert "GİZLİ MÜŞTERİ ADI" not in govde, "customer_name PII response'a sızmış"
        assert "beklenmeyen bir hata" in govde

    def test_mutation_str_e_geri_gelirse_kirilir(self):
        """AST kapısı: except handler'ın GÖVDESİ (docstring DIŞINDA — o salt
        metin, `ast.unparse` ile kod sanılabilir) `str(e)`/`{e}`
        interpolasyonu İÇEREMEZ."""
        fn = _fonksiyon_ast(REPO_KOK / "backend" / "app" / "main.py", "generate_pdf_direct")
        handlerlar = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
        assert handlerlar, "generate_pdf_direct içinde except handler bulunamadı"
        for handler in handlerlar:
            handler_kaynak = "\n".join(ast.unparse(s) for s in handler.body)
            assert "str(e)" not in handler_kaynak, (
                "generate_pdf_direct'in except gövdesi hâlâ str(e) döndürüyor"
            )
            for dugum in ast.walk(handler):
                if isinstance(dugum, ast.JoinedStr):
                    for deger in dugum.values:
                        if (
                            isinstance(deger, ast.FormattedValue)
                            and isinstance(deger.value, ast.Name)
                            and deger.value.id == "e"
                        ):
                            raise AssertionError("f-string içinde {e} interpolasyonu bulundu")

    def test_unreachable_ama_silinmedi(self):
        """Owner Bölüm 9: route dead/unreachable ise SİLİNMEZ — yalnız
        güvenlik düzeltmesi uygulanır. Endpoint hâlâ kayıtlı olmalı."""
        from app.main import app as fastapi_app

        yollar = {r.path for r in fastapi_app.routes if hasattr(r, "path")}
        assert "/generate-pdf-direct" in yollar


# ═══════════════════════════════════════════════════════════════════════════
# 8b) pypdf kararı — mevcut best-effort/fail-safe davranış test altında
# ═══════════════════════════════════════════════════════════════════════════

class TestSayfaNumaralamaBestEffort:
    """
    Owner Bölüm 10: sayfa numaralama ürün sözleşmesinde ZORUNLU DEĞİL
    (backend/tests altında bugüne dek hiç test edilmiyordu — yalnız elle
    çalıştırılan smoke_test_page_numbering.py vardı, pytest'e dahil değildi).
    Karar: pypdf YENİ dependency olarak EKLENMEDİ; mevcut best-effort/
    fail-safe davranış (numaralama başarısız → HAM ama GEÇERLİ PDF) artık
    otomatik test altında.
    """

    def test_pypdf_yoklugunda_ham_ama_gecerli_pdf_doner(self, monkeypatch):
        """`stamp_page_numbers` import'u/çağrısı patlarsa Playwright motoru
        HÂLÂ geçerli bir PDF döndürmeli — kullanıcıya sahte "numaralandı"
        iddiası verilmez, üretim de PATLAMAZ (fail-safe)."""
        import app.services.pdf_playwright as pp

        def _patlayan_stamp(pdf_bytes):
            raise ModuleNotFoundError("No module named 'pypdf'")

        # pdf_page_numbering modülü içindeki stamp_page_numbers'ı, henüz
        # import edilmeden ÖNCE (fonksiyon-içi lazy import) sahtelemek için
        # sys.modules'a sahte bir modül enjekte ediyoruz.
        import types
        import sys as _sys

        sahte_modul = types.ModuleType("app.services.pdf_page_numbering")
        sahte_modul.stamp_page_numbers = _patlayan_stamp
        monkeypatch.setitem(_sys.modules, "app.services.pdf_page_numbering", sahte_modul)

        html = "<html><body><h1>Test Teklif</h1></body></html>"
        pdf_bytes = pp.html_to_pdf_bytes_sync_v2(html)

        assert pdf_bytes.startswith(b"%PDF"), (
            "sayfa numaralama başarısız olsa bile geçerli/ham bir PDF dönmeli"
        )
        assert len(pdf_bytes) > 100

    def test_pypdf_kurulu_degil_requirements_txt_yeni_dependency_yok(self):
        """Owner: 'Yeni dependency ekleme.' — requirements.txt'e pypdf
        eklenmediğini statik olarak pinler (mutation kapısı)."""
        req = (REPO_KOK / "backend" / "requirements.txt").read_text(encoding="utf-8")
        assert "pypdf" not in req.lower().replace("pypdfium2", ""), (
            "requirements.txt'e pypdf eklenmiş — owner Bölüm 10 kararına aykırı "
            "(pypdfium2 farklı bir pakettir, hariç tutuldu)"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 9) Mutation kapıları — storage.py / storage_local.py sözleşme pinleri
# ═══════════════════════════════════════════════════════════════════════════

class TestMutasyonKapilari:
    def test_put_bytes_absolute_yol_donerse_kirilir(self):
        """AST kapısı: `put_bytes` `return key` yapmalı (str(path) DEĞİL)."""
        fn = _fonksiyon_ast(
            REPO_KOK / "backend" / "app" / "services" / "storage_local.py", "put_bytes"
        )
        son_return = [n for n in ast.walk(fn) if isinstance(n, ast.Return)][-1]
        assert isinstance(son_return.value, ast.Name) and son_return.value.id == "key", (
            "put_bytes artık 'key' parametresini DEĞİL başka bir ifadeyi döndürüyor "
            "(S5-R03B relative-ref sözleşmesi kırılmış olabilir)"
        )

    def test_get_storage_legacy_base_dir_gecirmezse_kirilir(self):
        """AST kapısı: `get_storage()` `LocalStorage(...)` çağrısına
        `legacy_base_dir` kwarg'ını GEÇİRMELİDİR — aksi hâlde legacy
        containment sessizce devre dışı kalır."""
        fn = _fonksiyon_ast(REPO_KOK / "backend" / "app" / "services" / "storage.py", "get_storage")
        cagrilar = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "LocalStorage"
        ]
        assert cagrilar, "get_storage içinde LocalStorage(...) çağrısı bulunamadı"
        kwarg_adlari = {kw.arg for kw in cagrilar[0].keywords}
        assert "legacy_base_dir" in kwarg_adlari, (
            "LocalStorage() çağrısı legacy_base_dir kwarg'ını kaybetmiş"
        )

    def test_mutation_main_js_storage_dir_wiring_kaldirilirsa_kirilir(self):
        """Statik kapı: `electron/main.js` STORAGE_DIR'i gerçekten
        `dbRouting.resolveDurableStorageDir(...)`'in sonucuyla, korumalı env
        bloğunda (machineLocalEnv SONRASI) geçirmeli — aksi hâlde durable
        kök hiç kullanılmaz, backend sessizce eski CWD-göreli './storage'a
        (kurulum dizinine) düşer (S5-R03 HARD STOP'un storage eşdeğeri)."""
        icerik = (REPO_KOK / "electron" / "main.js").read_text(encoding="utf-8")
        assert "resolveDurableStorageDir" in icerik, (
            "main.js dbRouting.resolveDurableStorageDir'ı hiç çağırmıyor"
        )
        assert "STORAGE_DIR: durableStorageDir" in icerik, (
            "main.js STORAGE_DIR'ı spawn env bloğuna geçirmiyor "
            "(machineLocalEnv sonrası korumalı literal olmalı)"
        )
        # Korumalı env bloğunun İÇİNDE olduğunu doğrula: STORAGE_DIR satırı,
        # DATABASE_URL/ENV/GELKA_PACKAGED_RUNTIME ile AYNI `env: {...}`
        # nesnesinde olmalı (machineLocalEnv spread'inden SONRA).
        env_blok_basi = icerik.index("...process.env, ...machineLocalEnv,")
        env_blok_sonu = icerik.index("},", env_blok_basi)
        env_blok = icerik[env_blok_basi:env_blok_sonu]
        assert "STORAGE_DIR: durableStorageDir" in env_blok, (
            "STORAGE_DIR korumalı env bloğunun DIŞINDA — machine-local.env "
            "tarafından ezilebilir"
        )

    def test_reparse_point_kontrolu_init_icinde_cagriliyor(self):
        """AST kapısı: `__init__` kök-reparse-point kontrolünü İKİ KEZ
        çağırmalı — mkdir ÖNCESİ (asıl savunma) VE mkdir SONRASI (TOCTOU
        defense-in-depth). Yalnız `>=1` istemek, TOCTOU çağrısının tek
        başına kaldırılmasını YAKALAMAZ (ampirik olarak doğrulandı)."""
        fn = _fonksiyon_ast(
            REPO_KOK / "backend" / "app" / "services" / "storage_local.py", "__init__"
        )
        cagri_sayisi = sum(
            1
            for n in ast.walk(fn)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_reparse_point_ise_fail_closed"
        )
        assert cagri_sayisi >= 2, (
            f"__init__ reparse-point kontrolünü {cagri_sayisi} kez çağırıyor "
            "(>=2 beklenir: mkdir-öncesi + TOCTOU mkdir-sonrası)"
        )
