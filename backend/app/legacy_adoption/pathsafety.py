"""
PDSMR-R2 — dosya kimlik ve yol guvenligi yardimcilari (SALT-OKUNUR, YAN ETKISIZ).

adoption.py'deki (PDSMR-R1D/Faz3) esdeger private yardimcilarla AYNI ilke:
Windows'ta salt METIN karsilastirmasi YETMEZ — junction, gorece yol,
buyuk/kucuk harf farki ayni fiziksel dosyaya farkli metinle isaret
edebilir. Bu modul BILEREK adoption.py'yi import ETMEZ: rescue, adoption'dan
onceki bir yasam evresinde (installer) calisir; iki modulun birbirine
bagimli olmasi, birini degistirirken digerini kirmama garantisini
zayiflatirdi.

Cagrildigi yerler:
- app/legacy_adoption/rescue.py [PDSMR-R2]
- tests/test_legacy_adoption_rescue.py
"""
from __future__ import annotations

import os

# Kurulu uygulama alanina isaret eden, mutasyon hedefi olarak KESINLIKLE
# kabul edilmeyecek yol parcalari. adoption.py'deki
# FORBIDDEN_REPAIR_PATH_MARKERS ile bilerek AYNI degerler — iki modul
# BAGIMSIZ calissa da politika kararinin kendisi TEKTIR.
FORBIDDEN_TARGET_MARKERS = (
    "programs\\gelka enerji",
    "programs/gelka enerji",
    "program files",
    "appdata\\local\\programs",
    "appdata/local/programs",
)

# userData altindaki canonical yerlesim. Owner sozlesmesi (PDSMR-R2):
#   <userData>/database/gelka_enerji.db
#   <userData>/database/backups/<versioned immutable name>
DATABASE_SUBDIR = "database"
BACKUPS_SUBDIR = "backups"
CANONICAL_DB_FILENAME = "gelka_enerji.db"

# Electron `app.name`, paketlenmis uygulamada package.json'daki "name"
# alanindan gelir — "productName" DEGIL. Gercek makinede GOZLEMLENMIS
# deger: %APPDATA%\gelka-enerji (bkz. PDSMR-R2 ADIM 1 kaniti). Bu bir
# tahmin degil, kurulu v1.0.6'nin fiili calisma zamani davranisidir.
ELECTRON_APP_NAME = "gelka-enerji"


def real_path(path: str) -> str:
    """Sembolik bag / junction / gorece yol / buyuk-kucuk harf normalize edilmis yol."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def same_file(a: str, b: str) -> bool:
    """
    Ayni dosya mi? Once isletim sistemi kimligi (inode/file-id), o
    calismazsa (ör. dosyalardan biri henuz yok) cozulmus yol karsilastirmasi.
    """
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return real_path(a) == real_path(b)


def is_forbidden_target(path: str) -> str | None:
    """Yol kurulu uygulama alaninda mi? Oyleyse eslesen marker'i doner."""
    normalize = real_path(path)
    for marker in FORBIDDEN_TARGET_MARKERS:
        if os.path.normcase(marker) in normalize:
            return marker
    return None


def resolve_userdata_database_dir(userdata_dir: str) -> str:
    """
    userData/database dizinini hesaplar.

    DIKKAT — `userdata_dir` Electron `app.getPath('userData')` ILE BIREBIR
    AYNI seydir (app adini ZATEN ICERIR, ör. `...\\Roaming\\gelka-enerji`).
    Bu fonksiyon app adini KENDI EKLEMEZ — eklerse cagiranin zaten app-adi-
    dahil bir yol vermesi durumunda `...\\gelka-enerji\\gelka-enerji\\...`
    gibi CIFT bir dizin uretilir (bu hata bir provada YAKALANDI, bkz.
    PDSMR-R2 ADIM 8 kaniti). electron/dbRouting.js::resolveCanonicalDbPath
    ile AYNI semantik — iki dilde AYNI formul, farkli parametre semantigi
    OLAMAZ.

    NSIS tarafi `$APPDATA\\gelka-enerji\\database` kullanir; `$APPDATA`
    (NSIS) ve `app.getPath('appData')` (Electron) ikisi de isletim
    sisteminin per-user Roaming AppData kokunu verir. Sabit "gelka-enerji"
    parcasi kullanici adi, dil, TR karakter, silent/assisted kurulum modu
    veya INSTDIR'dan ETKILENMEZ (formulde bu girdiler hic yer almaz).
    """
    return os.path.join(userdata_dir, DATABASE_SUBDIR)


def resolve_canonical_db_path(userdata_dir: str) -> str:
    return os.path.join(resolve_userdata_database_dir(userdata_dir), CANONICAL_DB_FILENAME)


def resolve_backups_dir(userdata_dir: str) -> str:
    return os.path.join(resolve_userdata_database_dir(userdata_dir), BACKUPS_SUBDIR)


def resolve_userdata_dir_from_appdata_root(app_data_root: str, app_name: str = ELECTRON_APP_NAME) -> str:
    """
    `app_data_root` (AppData/Roaming KOKU, app adi HARIC) verildiginde
    Electron'un `app.getPath('userData')` ile URETECEGI degeri hesaplar.

    Bu, yukaridaki `resolve_userdata_database_dir(userdata_dir)`'den
    KASITLI olarak AYRI bir fonksiyondur: biri "elimde zaten userData var"
    (gercek main.js/rescue.exe cagirisi), digeri "elimde yalniz AppData
    koku var, userData'yi BEN turetmem gerekiyor" (test/dogrulama senaryosu)
    durumunu modeller. Karistirilmasin diye ADLARI farklidir.
    """
    return os.path.join(app_data_root, app_name)


__all__ = [
    "BACKUPS_SUBDIR",
    "CANONICAL_DB_FILENAME",
    "DATABASE_SUBDIR",
    "ELECTRON_APP_NAME",
    "FORBIDDEN_TARGET_MARKERS",
    "is_forbidden_target",
    "real_path",
    "resolve_backups_dir",
    "resolve_canonical_db_path",
    "resolve_userdata_database_dir",
    "resolve_userdata_dir_from_appdata_root",
    "same_file",
]
