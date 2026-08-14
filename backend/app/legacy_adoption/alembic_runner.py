"""
PDSMR-R3 / PDSMR-R3A — paylasilan Alembic calistirici.

DEV (kaynak, venv): ALT SUREC ile calisir (DEGISMEDI, kanitlandi).
`backend/` sys.path'te oldugundan `backend/alembic/` (migration dizini)
kurulu `alembic` paketini GOLGELER ve `from alembic import command`
IN-PROCESS ImportError verir; konsol script'i (venv `alembic.exe`) alt
surecte dogru paketi cozer.

FROZEN (PyInstaller, gelka-backend.exe): IN-PROCESS calisir (PDSMR-R3A
FAZ A/B ile GERCEK derlemeyle KANITLANDI — bkz. kapanis raporu). Ayri bir
"gelka-alembic.exe" companion mimarisi TERK EDILDI (owner STRATEJI 2):
gelka-backend.exe'nin KENDI, ZATEN CALISAN app.* + alembic bagimlilik
grafigini kullanir. Iki AYRI, izole edilmis kok neden bulundu:

  1) CALISMA-ZAMANI GOLGELEME: alembic.ini + alembic/ (migration
     script'leri) gelka-backend.exe ile AYNI dizine konursa, `import
     alembic` bundled paket yerine YANLISLIKLA bu klasoru bulabilir.
     DUZELTME: migration script'leri exe'nin dizinine (resources/backend/)
     KONUR AMA frozen sys.path bu dizini KENDILIGINDEN ICERMEZ (izole
     testle KANITLANDI - os.chdir() sys.path'i DEGISTIRMEZ), bu yuzden
     coğaltma OLUŞMAZ; asil risk `sys.path`e ACIKCA EKLEME yapmaktan
     kacinmaktir (bu modul BUNU YAPMAZ).

  2) DERLEME-ZAMANI GOLGELEME: PyInstaller `--paths .` bayragiyla
     backend/ dizininde CALISTIRILIRSA, analiz asamasinda backend/alembic/
     (migration dizini) GERCEK alembic paketiyle KARISIYOR — GERCEK
     derlemeyle KANITLANDI (bayrak kaldirilinca sorun ANINDA cozuldu; VE
     PDSMR-R3A kapanisinda AYRICA, `--paths .` GERI EKLENEREK KANITLANDI:
     `is_alembic_available()` False donuyor, EXIT_ALEMBIC_UNAVAILABLE(52)
     HARD_STOP).
     ⚠ ONEMLI DUZELTME (PDSMR-R3A kapanis, ONCEKI iddia YANLIŞTI): bu
     modulun ONCEKI hali "gelka-backend.exe build komutu --paths .
     KULLANMIYOR, bu yuzden risk TASIMAZ" diyordu - bu SADECE
     electron/package.json'daki BASITLESTIRILMIS `build:backend` script'i
     icin DOGRUYDU. GERCEK/eksiksiz uretim script'i (`build-desktop.bat`,
     repo KOKUNDE) `cd backend` SONRASINDA `--paths .` ILE PyInstaller
     CAGIRIYOR - yani GERCEK release build'i BU RISKI TASIR. Bu, PDSMR-R3A
     kapanisinda CANLI derlemeyle KANITLANDI ve owner'a AYRI, kritik bir
     bulgu olarak raporlandı - bu dosyada (yetki alani disinda) DUZELTILMEDI.
     build-desktop.bat DUZELTILMEDEN calistirilirsa, R3/R3A'nin frozen
     alembic bootstrap'i HER ZAMAN EXIT 52 ile HARD_STOP OLUR.

`env.py` (alembic/env.py) ALEMBIC TARAFINDAN DINAMIK yuklenir
(importlib spec_from_file_location + exec_module) - PyInstaller'in
STATIK analizi env.py'nin KENDI importlarini (logging.config, app.core.config,
app.database) GOREMEZ; bunlarin build komutunda ACIKCA hidden-import/
add-data olarak belirtilmesi GEREKIR (gelka-backend.exe icin: `app;app`
zaten add-data; `logging.config` STEP 4/5'te eklendi).

Hicbir sekilde alembic calistirilamiyorsa `AlembicUnavailable` firlatilir
— cagiran (startup_gate.py) bunu KOSULSUZ HARD_STOP'a cevirir; create_all()'a
ASLA duselmez (owner kurali, PDSMR-R3 STEP 4/7).

PDSMR-R3A TARGET-DB OVERRIDE DUZELTMESI (STEP 1 kok neden): `_frozen_config()`
ONCEDEN `cfg.set_main_option("sqlalchemy.url", "sqlite:///" + db_path...)`
CAGIRIYORDU - ama `alembic/env.py` (satir 24) KENDI, KOSULSUZ olarak
`config.set_main_option("sqlalchemy.url", settings.database_url)` CAGIRIYOR;
yani BURADA set edilen deger HER ZAMAN settings.database_url (surecin
DATABASE_URL ortam degiskeni = PAKETLENMIS UYGULAMADA canonical yol) ile
EZILIYORDU - db_path parametresi SESSIZCE YOK SAYILIYORDU. Canli frozen exe
testinde KANITLANDI: fresh_initialize() 'fresh' calisma kopyasi YERINE
DOGRUDAN canonical'i migrate ediyordu (calisma-kopyasi/atomik-yayimlama
guvenlik modelinin TAMAMEN BOZULMASI - owner: "DATABASE_URL ortam degiskeni
bu cozumun kontrol kanali OLMAYACAK").

DUZELTME: `_frozen_config()` artik hedefi `cfg.attributes["pdsmr_target_db_path"]`
UZERINDEN GECER (Alembic'in bu tam senaryo icin RESMI/belgeli mekanizmasi -
bkz. alembic.config.Config.attributes docstring'i). `env.py` bu attribute
VARSA ondan SQLAlchemy `URL.create()` ile (ham string birlestirme YOK) bir
URL insa eder; YOKSA (CLI `alembic upgrade head`, dev-subprocess
_run_alembic_subprocess) davranis DEGISMEDEN settings.database_url'e duser -
byte-semantik olarak ONCEKI ile AYNI. Hedef yol MUTLAK+normalize edilmis
olmalidir - bkz. `_validate_frozen_target_path()`; relative/URI/sorgu-dizesi/
NUL/belirsiz bir yol SESSIZCE duzeltilmez, REDDEDILIR (ValueError ->
alembic_upgrade/alembic_heads_count'un mevcut `except Exception` -> RuntimeError
-> startup_gate.py/adoption.py'nin mevcut GateRefused/AdoptionRefused
donusumu ile DOGAL olarak HARD_STOP'a ulasir - bu iki dosyaya DOKUNULMEDI).

Cagrildigi yerler:
- app/legacy_adoption/adoption.py [PDSMR-R1D/Faz3 → PDSMR-R3'te PROMOTE edildi]
- app/legacy_adoption/startup_gate.py [PDSMR-R3]
- backend/run_server.py [PDSMR-R3, frozen — dolayli, startup_gate uzerinden]
- tests/test_legacy_adoption_phase3.py
- tests/test_pdsmr_r3_startup_gate.py
- tests/test_pdsmr_r3a_alembic_runner.py [PDSMR-R3A]
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Optional


class AlembicUnavailable(Exception):
    """Calistirilabilir/calisir durumda bir alembic bulunamadi. Cagiran HARD_STOP'a cevirmelidir."""


def backend_dir() -> str:
    # __file__ = backend/app/legacy_adoption/alembic_runner.py -> uc kez yukari
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def frozen_alembic_ini_path() -> str:
    """
    FROZEN modda alembic.ini'nin bulunmasi BEKLENEN yol: gelka-backend.exe
    ile AYNI dizin (resources/backend/) — electron-builder extraResources
    ile buraya kopyalanir (bkz. package.json). Bu dizin frozen sys.path'e
    KENDILIGINDEN GIRMEZ (yukaridaki modul docstring'i, ADIM 1 kaniti) -
    bu yuzden burada durmasi GUVENLIDIR.
    """
    return os.path.join(os.path.dirname(sys.executable), "alembic.ini")


def _find_dev_alembic_exe() -> Optional[str]:
    kok = os.path.dirname(sys.executable)
    for ad in ("alembic.exe", "alembic"):
        aday = os.path.join(kok, ad)
        if os.path.isfile(aday):
            return aday
    return shutil.which("alembic")


def is_alembic_available() -> bool:
    """
    DEV: venv/PATH'te calistirilabilir bir alembic var mi VE GERCEKTEN
    calisiyor mu (--help smoke test).
    FROZEN: alembic.ini beklenen yolda mevcut mu VE alembic Python API'si
    (Config/command) bu process icinde GERCEKTEN import edilebiliyor mu.

    Dosya VARLIGI TEK BASINA YETERSIZDIR (PDSMR-R3'te ilk companion-exe
    denemesinde GERCEK derlemeyle KANITLANDI) — bu yuzden HER ZAMAN bir
    calisma/import DENEMESI yapilir.
    """
    if getattr(sys, "frozen", False):
        if not os.path.isfile(frozen_alembic_ini_path()):
            return False
        try:
            import alembic  # noqa: F401 - ONCE, TAM import (bkz. modul docstring'i #2)
            from alembic.config import Config  # noqa: F401
            from alembic.command import heads  # noqa: F401
            return True
        except Exception:
            return False

    exe = _find_dev_alembic_exe()
    if not exe:
        return False
    try:
        sonuc = subprocess.run([exe, "--help"], capture_output=True, text=True, timeout=15)
        return sonuc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def find_alembic_exe() -> str:
    """
    GERIYE DONUK UYUMLULUK icin korundu (adoption.py, mevcut testler).
    YALNIZ DEV/subprocess yolunda anlamlidir — FROZEN modda cagirilmamalidir
    (orada in-process calisilir, "exe" kavrami YOKTUR).
    """
    exe = _find_dev_alembic_exe()
    if exe:
        return exe
    raise AlembicUnavailable("calistirilabilir alembic bulunamadi (venv/PATH)")


def _run_alembic_subprocess(db_path: str, *args: str, cwd: Optional[str] = None) -> str:
    exe = find_alembic_exe()
    calisma_dizini = cwd or backend_dir()
    if not os.path.isdir(calisma_dizini):
        calisma_dizini = os.path.dirname(exe)

    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + db_path.replace("\\", "/")
    sonuc = subprocess.run(
        [exe, *args],
        cwd=calisma_dizini, env=env, capture_output=True, text=True,
    )
    if sonuc.returncode != 0:
        kuyruk = (sonuc.stderr or sonuc.stdout).strip().splitlines()[-8:]
        raise RuntimeError(
            f"alembic {' '.join(args)} basarisiz (exit={sonuc.returncode}): "
            + " | ".join(kuyruk)
        )
    return sonuc.stdout


def _validate_frozen_target_path(db_path: str) -> str:
    """
    FROZEN in-process modda alembic'in hangi FIZIKSEL dosyaya yazacagini
    KESIN ve tek anlamli belirler (PDSMR-R3A STEP 1 kontrati). Mutlak VE
    normalize edilmis (os.path.normpath ile DEGISMEYEN) yerel dosya yolu
    ZORUNLUDUR - relative yol, URI/sorgu dizesi, NUL veya belirsiz (normalize
    edilince DEGISEN) bir yol SESSIZCE duzeltilmez, REDDEDILIR (fail-closed;
    owner karari: "DATABASE_URL ortam degiskeni bu cozumun kontrol kanali
    OLMAYACAK" - string bicimlendirmede de AYNI siddet gerekir).

    KASITLI OLARAK `alembic` paketini import ETMEZ (bkz. _frozen_config'in
    cagri sirasi) - boylece BU fonksiyon, gercek alembic import'unun
    calisma-zamani `sys.path` durumuna DUYARLI olabilecegi ortamlarda bile
    (ör. test sureci) GUVENLE, izolasyon GEREKMEDEN test edilebilir.

    Cagrildigi yerler:
    - _frozen_config() [bu modul, PDSMR-R3A]
    - tests/test_pdsmr_r3a_alembic_runner.py
    """
    if not db_path or "\x00" in db_path:
        raise ValueError("gecersiz hedef DB yolu: bos veya NUL karakter iceriyor")
    if "://" in db_path or db_path.lstrip().lower().startswith("file:"):
        raise ValueError(f"gecersiz hedef DB yolu: URI biciminde olamaz: {db_path!r}")
    if "?" in db_path:
        raise ValueError(f"gecersiz hedef DB yolu: sorgu dizesi ('?') iceremez: {db_path!r}")
    if not os.path.isabs(db_path):
        raise ValueError(f"gecersiz hedef DB yolu: mutlak (absolute) olmalidir: {db_path!r}")
    normalize_edilmis = os.path.normpath(db_path)
    if normalize_edilmis != db_path:
        raise ValueError(
            "gecersiz hedef DB yolu: normalize edilmemis/belirsiz "
            f"({db_path!r} != normpath {normalize_edilmis!r})"
        )
    return db_path


def _frozen_config(db_path: str) -> "Config":  # noqa: F821
    """
    FROZEN modda hedef DB yolunu `Config.attributes["pdsmr_target_db_path"]`
    UZERINDEN `alembic/env.py`'ye GECER (PDSMR-R3A duzeltmesi - bkz. modul
    dokstring'i). `set_main_option("sqlalchemy.url", ...)` ARTIK BURADA
    CAGRILMAZ: tek gecerli kaynak env.py'nin KENDI mantigidir (attribute
    varsa ondan URL insa eder, yoksa settings.database_url'e duser) - boylece
    BURADA olusabilecek "eski/yanlis deger env.py'den ONCE bir sekilde
    okunur" belirsizligi YAPISAL olarak ORTADAN KALKAR.

    Kontroller, `alembic` paketini import ETMEDEN ONCE yapilir (ini varligi,
    sonra db_path dogrulamasi) - boylece gecersiz bir db_path icin
    `AlembicUnavailable`/`ValueError`, GERCEK bir alembic import'u hic
    TETIKLENMEDEN firlatilir.
    """
    ini_yolu = frozen_alembic_ini_path()
    if not os.path.isfile(ini_yolu):
        raise AlembicUnavailable(f"alembic.ini bulunamadi: {ini_yolu}")
    hedef = _validate_frozen_target_path(db_path)

    from alembic.config import Config

    cfg = Config(ini_yolu)
    # script_location alembic.ini'de GORECE ("alembic") - AMPIRIK DOGRULANDI
    # (PDSMR-R3A test-harness tanisi): Alembic bunu ini dosyasinin KENDI
    # dizinine GORE DEGIL, surecin CALISMA DIZININE (CWD) GORE cozer. Bu
    # YUZDEN migration klasorunun alembic.ini'nin yaninda olmasi TEK BASINA
    # YETERSIZDIR - CWD'nin de O DIZIN olmasi GEREKIR. Gercek frozen exe'de
    # bu, run_server.py'nin frozen modda KOSULSUZ `os.chdir(base_dir)`
    # cagrisiyla (satir 10-13, main() cagrilmadan ONCE, modul import
    # zamaninda calisir) GUVENCE altindadir - base_dir = alembic.ini'nin
    # bulundugu dizin (resources/backend/).
    cfg.attributes["pdsmr_target_db_path"] = hedef
    return cfg


def alembic_upgrade(db_path: str, revision: str, *, cwd: Optional[str] = None) -> None:
    if getattr(sys, "frozen", False):
        from alembic.command import upgrade

        try:
            upgrade(_frozen_config(db_path), revision)
        except AlembicUnavailable:
            raise
        except Exception as exc:
            raise RuntimeError(f"alembic upgrade {revision} basarisiz (in-process): {exc}") from exc
        return
    _run_alembic_subprocess(db_path, "upgrade", revision, cwd=cwd)


def alembic_heads_count(db_path: str, *, cwd: Optional[str] = None) -> int:
    if getattr(sys, "frozen", False):
        from alembic.script import ScriptDirectory

        try:
            cfg = _frozen_config(db_path)
            script_dir = ScriptDirectory.from_config(cfg)
            return len(script_dir.get_heads())
        except AlembicUnavailable:
            raise
        except Exception as exc:
            raise RuntimeError(f"alembic heads basarisiz (in-process): {exc}") from exc
    cikti = _run_alembic_subprocess(db_path, "heads", cwd=cwd)
    return len([s for s in cikti.splitlines() if s.strip()])


__all__ = [
    "AlembicUnavailable",
    "alembic_heads_count",
    "alembic_upgrade",
    "backend_dir",
    "find_alembic_exe",
    "frozen_alembic_ini_path",
    "is_alembic_available",
]
