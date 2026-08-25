"""
S5-R01 — LocalStorage atomik publish sözleşmesi.

Owner Bölüm 3.1 zorunlu davranışları:
 1. Geçici dosya hedefle aynı dizin/filesystem üzerinde oluşturulur.
 2. Geçici ad benzersizdir.
 3. Bytes tamamen yazılır.
 4. `flush` + `fsync` uygulanır.
 5. Dosya handle'ı kapatılır.
 6. Nihai publish `os.replace` ile atomik yapılır.
 7. Hata hâlinde geçici dosya temizlenir.
 8. Eski sağlam hedef, yeni dosya tamamen hazır olmadan silinmez.
 9. Download hiçbir anda yarım dosya görmez.
10. Mevcut caller'ların return/path sözleşmesi bozulmaz.

Ek olarak Bölüm 7 "Storage" matrisi: existing-target koruması, same-directory
replace, temp cleanup, concurrent publish, Windows handle-close davranışı ve
diğer caller'ların geriye dönük uyumluluğu.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from app.services.storage_local import LocalStorage


PDF = "application/pdf"


@pytest.fixture()
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(base_dir=str(tmp_path / "storage"))


def _temp_artiklari(dizin: Path) -> list[Path]:
    """Yayımlanmamış geçici dosya kalıntıları."""
    if not dizin.is_dir():
        return []
    return [p for p in dizin.iterdir() if p.name.endswith(".tmp")]


# ═══════════════════════════════════════════════════════════════════════════
# 10 — Return/path sözleşmesi bozulmadı
# ═══════════════════════════════════════════════════════════════════════════

def test_donus_sozlesmesi_mutlak_yol_ve_icerik_aynen_korunur(storage, tmp_path):
    ref = storage.put_bytes("offers/1/offer.pdf", b"%PDF-govde", PDF)

    assert os.path.isabs(ref), "dönüş mutlak yol olmalı (eski sözleşme)"
    assert Path(ref) == Path(storage.base_dir) / "offers" / "1" / "offer.pdf"
    assert Path(ref).read_bytes() == b"%PDF-govde"
    # Aynı ref `resolve_local_path` containment kontrolünden geçmeli.
    assert storage.resolve_local_path(ref) == str(Path(ref).resolve())


def test_ic_ice_dizinler_olusturulur(storage):
    ref = storage.put_bytes("a/b/c/d.pdf", b"x", PDF)
    assert Path(ref).is_file()


# ═══════════════════════════════════════════════════════════════════════════
# 1 + 2 + 6 — Geçici dosya aynı dizinde, benzersiz, publish `os.replace` ile
# ═══════════════════════════════════════════════════════════════════════════

def test_gecici_dosya_hedefle_ayni_dizinde_ve_replace_ile_yayimlanir(storage, monkeypatch):
    yakalanan: dict[str, str] = {}
    gercek_replace = os.replace

    def izleyen_replace(src, dst):
        yakalanan["src"] = str(src)
        yakalanan["dst"] = str(dst)
        return gercek_replace(src, dst)

    monkeypatch.setattr(os, "replace", izleyen_replace)
    ref = storage.put_bytes("offers/7/offer.pdf", b"veri", PDF)

    assert yakalanan, "publish `os.replace` ile yapılmalı"
    assert os.path.dirname(yakalanan["src"]) == os.path.dirname(yakalanan["dst"]), (
        "geçici dosya hedefle AYNI dizinde olmalı — aksi hâlde replace atomik değildir"
    )
    assert yakalanan["dst"] == ref


def test_gecici_adlar_benzersiz(storage, monkeypatch):
    adlar: list[str] = []
    gercek_replace = os.replace

    def izleyen_replace(src, dst):
        adlar.append(os.path.basename(str(src)))
        return gercek_replace(src, dst)

    monkeypatch.setattr(os, "replace", izleyen_replace)
    for i in range(5):
        storage.put_bytes(f"offers/{i}/offer.pdf", b"v", PDF)

    assert len(set(adlar)) == len(adlar), "geçici adlar benzersiz olmalı"


# ═══════════════════════════════════════════════════════════════════════════
# 4 + 5 — flush + fsync uygulanır, handle kapanır
# ═══════════════════════════════════════════════════════════════════════════

def test_fsync_uygulanir(storage, monkeypatch):
    cagrildi: list[int] = []
    gercek_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (cagrildi.append(fd), gercek_fsync(fd))[1])

    storage.put_bytes("offers/9/offer.pdf", b"kalici", PDF)
    assert cagrildi, "dayanıklılık için fsync çağrılmalı"


def test_handle_replace_oncesi_kapatilir(storage, monkeypatch):
    """
    Windows'ta açık handle `os.replace`i engeller. Publish anında geçici
    dosyanın yeniden açılabiliyor olması handle'ın kapandığını kanıtlar.
    """
    gercek_replace = os.replace

    def kontrol_eden_replace(src, dst):
        # Handle açık kalsaydı bu açış Windows'ta hata verirdi.
        with open(src, "rb") as fh:
            assert fh.read() == b"handle-testi"
        return gercek_replace(src, dst)

    monkeypatch.setattr(os, "replace", kontrol_eden_replace)
    storage.put_bytes("offers/11/offer.pdf", b"handle-testi", PDF)


# ═══════════════════════════════════════════════════════════════════════════
# 7 + 8 — Hata hâlinde temp temizlenir, mevcut sağlam hedef korunur
# ═══════════════════════════════════════════════════════════════════════════

def test_yazim_hatasinda_temp_temizlenir_ve_hedef_olusmaz(storage, monkeypatch):
    def patlayan_fsync(fd):
        raise OSError("disk hatası")

    monkeypatch.setattr(os, "fsync", patlayan_fsync)

    with pytest.raises(OSError):
        storage.put_bytes("offers/13/offer.pdf", b"yarim", PDF)

    hedef = Path(storage.base_dir) / "offers" / "13" / "offer.pdf"
    assert not hedef.exists(), "başarısız yazım hedef dosya BIRAKMAMALI"
    assert _temp_artiklari(hedef.parent) == [], "geçici dosya artığı kalmamalı"


def test_mevcut_saglam_hedef_basarisiz_yazimda_korunur(storage, monkeypatch):
    ref = storage.put_bytes("offers/17/offer.pdf", b"SURUM-1", PDF)
    assert Path(ref).read_bytes() == b"SURUM-1"

    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        storage.put_bytes("offers/17/offer.pdf", b"SURUM-2-BOZUK", PDF)

    assert Path(ref).read_bytes() == b"SURUM-1", (
        "eski sağlam hedef, yeni dosya hazır olmadan ASLA bozulmamalı"
    )
    assert _temp_artiklari(Path(ref).parent) == []


def test_replace_kalici_hata_verirse_yukari_firlatir_ve_artik_birakmaz(storage, monkeypatch):
    monkeypatch.setattr(
        os, "replace",
        lambda src, dst: (_ for _ in ()).throw(PermissionError("hedef meşgul")),
    )
    # Yeniden denemeleri hızlandır (test süresi için).
    monkeypatch.setattr("app.services.storage_local._PUBLISH_RETRY_DELAY_SECONDS", 0.0)

    with pytest.raises(OSError):
        storage.put_bytes("offers/19/offer.pdf", b"veri", PDF)

    hedef_dizin = Path(storage.base_dir) / "offers" / "19"
    assert _temp_artiklari(hedef_dizin) == [], "sessiz başarısızlık ve artık YOK"
    assert not (hedef_dizin / "offer.pdf").exists()


# ═══════════════════════════════════════════════════════════════════════════
# Windows handle-close davranışı — geçici çakışmada yeniden dener
# ═══════════════════════════════════════════════════════════════════════════

def test_gecici_permission_hatasinda_yeniden_denenir(storage, monkeypatch):
    gercek_replace = os.replace
    kalan = {"hata": 2}

    def bazen_patlayan_replace(src, dst):
        if kalan["hata"] > 0:
            kalan["hata"] -= 1
            raise PermissionError("hedef okunuyor (Windows)")
        return gercek_replace(src, dst)

    monkeypatch.setattr(os, "replace", bazen_patlayan_replace)
    monkeypatch.setattr("app.services.storage_local._PUBLISH_RETRY_DELAY_SECONDS", 0.0)

    ref = storage.put_bytes("offers/23/offer.pdf", b"sonunda-yayimlandi", PDF)
    assert Path(ref).read_bytes() == b"sonunda-yayimlandi"
    assert kalan["hata"] == 0, "geçici hatalar tüketilmeliydi"


# ═══════════════════════════════════════════════════════════════════════════
# 3 + 9 — Eşzamanlı publish ve yırtık (torn) okuma yok
# ═══════════════════════════════════════════════════════════════════════════

def test_es_zamanli_publish_tek_saglam_dosya_birakir(storage):
    """
    Aynı anahtara N eşzamanlı yazım: sonuç MUTLAKA yazılan payload'lardan
    BİRİNİN tamamı olmalı — karışım/yarım asla.
    """
    anahtar = "offers/29/offer.pdf"
    payloadlar = [bytes([65 + i]) * (256 * 1024) for i in range(6)]
    hatalar: list[BaseException] = []
    bariyer = threading.Barrier(len(payloadlar))

    def yaz(veri: bytes):
        try:
            bariyer.wait(timeout=10)
            storage.put_bytes(anahtar, veri, PDF)
        except BaseException as e:  # noqa: BLE001 — hata testte raporlanır
            hatalar.append(e)

    isler = [threading.Thread(target=yaz, args=(p,)) for p in payloadlar]
    for t in isler:
        t.start()
    for t in isler:
        t.join(timeout=30)

    assert not hatalar, f"eşzamanlı publish hata verdi: {hatalar!r}"
    hedef = Path(storage.base_dir) / "offers" / "29" / "offer.pdf"
    icerik = hedef.read_bytes()
    assert icerik in payloadlar, "dosya yazılan payload'lardan BİRİNİN tamamı olmalı (yırtık değil)"
    assert _temp_artiklari(hedef.parent) == [], "hiçbir geçici dosya artığı kalmamalı"


def test_yazim_surerken_okuma_yarim_dosya_gormez(storage):
    """
    Yazım sürerken okuyan bir tüketici HER SEFERİNDE ya eski ya yeni içeriğin
    TAMAMINI görmeli. Doğrudan hedefe yazan eski davranış yarım içerik
    gösterirdi.

    Okuyucu gerçekçi bir istemci gibi okumalar ARASINDA kısa süre bekler.
    Aralıksız (tight-loop) okuma Windows'ta hedefi sürekli açık tutar ve
    `os.replace`e hiç fırsat bırakmaz; o PATOLOJİK durum ayrıca
    `test_surekli_okunan_hedef_yayini_reddeder` ile pinlenmiştir.

    Yayının reddedilmesi (OSError) burada da MEŞRU sonuçtur — kabul edilemez
    olan tek şey YIRTIK içeriktir.
    """
    anahtar = "offers/31/offer.pdf"
    eski = b"E" * (512 * 1024)
    yeni = b"Y" * (512 * 1024)
    storage.put_bytes(anahtar, eski, PDF)
    hedef = Path(storage.base_dir) / "offers" / "31" / "offer.pdf"

    gozlemler: list[bytes] = []
    dur = threading.Event()

    def okuyucu():
        while not dur.is_set():
            try:
                gozlemler.append(hedef.read_bytes())
            except OSError:
                # Windows'ta replace anında kısa süreli erişim hatası olabilir;
                # bu "yarım içerik" DEĞİLDİR ve kabul edilir.
                pass
            time.sleep(0.002)  # gerçekçi istemci: handle'ı sürekli tutmaz

    t = threading.Thread(target=okuyucu)
    t.start()
    yayin_reddi = 0
    try:
        for _ in range(15):
            for veri in (yeni, eski):
                try:
                    storage.put_bytes(anahtar, veri, PDF)
                except OSError:
                    yayin_reddi += 1  # meşru: hedef meşgul → yırtık yazım YOK
    finally:
        dur.set()
        t.join(timeout=30)

    assert gozlemler, "okuyucu hiç okuma yapamadı — test anlamsız olurdu"
    bozuk = [g for g in gozlemler if g not in (eski, yeni)]
    assert not bozuk, (
        f"{len(bozuk)} okuma YARIM/yırtık içerik gördü "
        f"(yayın reddi: {yayin_reddi})"
    )


def test_surekli_okunan_hedef_yayini_reddeder_fakat_bozmaz(storage, monkeypatch):
    """
    PATOLOJİK durum pinlenir: hedef ARALIKSIZ okunuyorsa (Windows'ta okuyucu
    FILE_SHARE_DELETE vermez) `os.replace` fırsat bulamaz ve yayın sonunda
    REDDEDİLİR.

    Bu bilinçli bir tasarım sonucudur, gizlenmemelidir:
      - Sessiz başarısızlık YOK → OSError yükselir.
      - Mevcut hedef BOZULMAZ → eski içerik aynen durur.
      - Geçici dosya artığı KALMAZ.

    Eski (atomik olmayan) davranış bu senaryoda hedefin üzerine yazar ve
    okuyucuya yırtık içerik gösterirdi.
    """
    # Testi hızlandır: bekleme sıfır, deneme sayısı korunur.
    monkeypatch.setattr("app.services.storage_local._PUBLISH_RETRY_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        os, "replace",
        lambda src, dst: (_ for _ in ()).throw(PermissionError("hedef sürekli okunuyor")),
    )

    anahtar = "offers/41/offer.pdf"
    hedef = Path(storage.base_dir) / "offers" / "41" / "offer.pdf"
    hedef.parent.mkdir(parents=True, exist_ok=True)
    hedef.write_bytes(b"ESKI-SAGLAM-ICERIK")

    with pytest.raises(OSError):
        storage.put_bytes(anahtar, b"YENI-ICERIK", PDF)

    assert hedef.read_bytes() == b"ESKI-SAGLAM-ICERIK", "mevcut hedef BOZULMAMALI"
    assert _temp_artiklari(hedef.parent) == [], "geçici dosya artığı kalmamalı"


# ═══════════════════════════════════════════════════════════════════════════
# Diğer caller'ların geriye dönük uyumluluğu
# ═══════════════════════════════════════════════════════════════════════════

def test_pdf_artifact_store_geriye_donuk_calisir(tmp_path):
    """`put_bytes` sözleşmesine dayanan mevcut caller bozulmadı."""
    from app.services.pdf_artifact_store import PdfArtifactStore

    storage = LocalStorage(base_dir=str(tmp_path / "artifacts"))
    store = PdfArtifactStore(storage)
    ref = store.store_pdf("job-42", b"%PDF-1.4 artifact")

    assert store.exists(ref)
    assert store.get_pdf(ref) == b"%PDF-1.4 artifact"


def test_get_bytes_ve_delete_sozlesmesi_korunur(storage):
    ref = storage.put_bytes("offers/37/offer.pdf", b"silinecek", PDF)
    assert storage.get_bytes(ref) == b"silinecek"
    assert storage.delete(ref) is True
    assert storage.exists(ref) is False
    assert storage.delete(ref) is False, "olmayan dosyanın silinmesi False dönmeli"
