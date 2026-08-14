"""
PDSMR-R2 — NSIS hook-sirasi kaniti.

Gercek installer DERLENMEZ/CALISTIRILMAZ (yetkisiz). Bunun yerine kurulu
`app-builder-lib` paketinin (electron/node_modules — ucuncu parti, versiyon
sabit) KAYNAK sablonlari metin duzeyinde incelenir ve `customInit`
macro'sunun, resources dizinini SILEBILECEK ilk adimdan (uninstallOldVersion)
ONCE cagrildigi kanitlanir.

Node_modules kurulu degilse (temiz checkout, `npm install` calismamis)
testler SKIP edilir — bu bir HATA degil, ortam eksikligidir.
"""
from __future__ import annotations

import os
import re

import pytest

_ELECTRON_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "electron")
)
_NSIS_TEMPLATES = os.path.join(
    _ELECTRON_DIR, "node_modules", "app-builder-lib", "templates", "nsis"
)

pytestmark = pytest.mark.skipif(
    not os.path.isdir(_NSIS_TEMPLATES),
    reason="app-builder-lib NSIS sablonlari kurulu degil (npm install calismamis)",
)


def _read(*parcalar: str) -> str:
    with open(os.path.join(_NSIS_TEMPLATES, *parcalar), encoding="utf-8") as fh:
        return fh.read()


def test_our_installer_nsh_defines_customInit_hook():
    yol = os.path.join(_ELECTRON_DIR, "build", "installer.nsh")
    assert os.path.isfile(yol), "electron/build/installer.nsh eksik"
    with open(yol, encoding="utf-8") as fh:
        icerik = fh.read()
    assert re.search(r"!macro\s+customInit\b", icerik), \
        "installer.nsh bir 'customInit' macro'su tanimlamiyor"
    assert re.search(r"!macroend", icerik)


def test_customInit_is_a_recognized_app_builder_lib_hook():
    """app-builder-lib GERCEKTEN 'customInit' adinda bir hook'u tanir mi?"""
    onInit = _read("installer.nsi")
    assert re.search(r"!ifmacrodef\s+customInit", onInit), \
        "app-builder-lib surumunde customInit hook'u KALDIRILMIS/YENIDEN ADLANDIRILMIS olabilir"


def test_customInit_runs_before_uninstallOldVersion_which_can_delete_resources():
    """
    KRITIK SIRA KANITI: .onInit (customInit icerir) her zaman Section
    "install"'DAN (uninstallOldVersion'i cagiran) ONCE calisir — bu NSIS'in
    KENDI yurutme modelidir (Function .onInit, Section'lardan once tetiklenir).
    Ayrica installer.nsi'nin Section blogundan ONCE .onInit'i tanimladigini
    metin sirasiyla da dogrularız.
    """
    installer_nsi = _read("installer.nsi")
    onInit_idx = installer_nsi.find("Function .onInit")
    section_idx = installer_nsi.find('Section "install"')
    assert onInit_idx != -1 and section_idx != -1
    assert onInit_idx < section_idx, ".onInit, install Section'undan sonra tanimlanmis"

    customInit_idx = installer_nsi.find("customInit")
    assert onInit_idx < customInit_idx < section_idx, \
        "customInit .onInit disinda veya install section'undan sonra"

    install_section = _read("installSection.nsh")
    uninstall_idx = install_section.find("uninstallOldVersion")
    apply_files_idx = install_section.find("installApplicationFiles")
    custom_install_idx = install_section.find("customInstall")
    assert uninstall_idx != -1 and apply_files_idx != -1 and custom_install_idx != -1
    assert uninstall_idx < apply_files_idx < custom_install_idx, \
        "installSection.nsh'nin beklenen ic sirasi (silme -> yazma -> customInstall) degismis"


def test_customInstall_hook_is_too_late_for_rescue():
    """
    Negatif kanit: customInstall'un GEC kaldigi — installApplicationFiles'tan
    SONRA gelmesi — dogrulanir. Rescue mantigi bilerek customInit'e
    baglandi, customInstall'a DEGIL.
    """
    install_section = _read("installSection.nsh")
    apply_files_idx = install_section.find("installApplicationFiles")
    custom_install_idx = install_section.find("customInstall")
    uninstall_idx = install_section.find("uninstallOldVersion")
    assert uninstall_idx < apply_files_idx < custom_install_idx


def test_rescue_executable_is_bundled_and_wired():
    """
    PDSMR-R2I ile durum degisti: rescue.exe GERCEKTEN derlendi, GERCEK bir
    NSIS derlemesiyle (electron-builder --win nsis) gomulup, GERCEK bir
    installer calistirilarak customInit -> ExecWait -> rescue.exe zinciri
    uctan uca kanitlandi (bkz. PDSMR-R2I kapanis raporu — disposable
    upgrade/rescue/adoption provasi). Bu test artik TERSINI dogrular:
    ExecWait satiri AKTIF (yorum satirinda DEGIL) ve gelka-rescue.exe'yi
    beklenen argumanlarla cagiriyor.

    NOT: gelka-rescue.exe'nin KENDISI git'e commit EDILMEZ (.gitignore -
    "Do not commit generated installer/binary unless repository release
    policy explicitly requires it", owner PDSMR-R2I karari) - yalniz onu
    URETEN build-rescue-helper.bat ve onu CAGIRAN installer.nsh commit
    edilir. Bu yuzden bu test dosyanin VARLIGINI DEGIL, installer.nsh
    METNINI kontrol eder.
    """
    yol = os.path.join(_ELECTRON_DIR, "build", "installer.nsh")
    with open(yol, encoding="utf-8") as fh:
        icerik = fh.read()
    aktif_satirlar = [
        s for s in icerik.splitlines()
        if "ExecWait" in s and not s.strip().startswith(";")
    ]
    assert len(aktif_satirlar) == 1, (
        "installer.nsh'de tam olarak 1 aktif ExecWait satiri bekleniyordu "
        f"(rescue.exe cagrisi) - bulunan: {len(aktif_satirlar)}"
    )
    satir = aktif_satirlar[0]
    assert "gelka-rescue.exe" in satir
    assert "--legacy" in satir and "--canonical" in satir and "--backups-dir" in satir
    assert "--version-label" in satir

    # DUZELTME KANITI (PDSMR-R2I kapanis): $0 tek basina yeterli DEGIL -
    # ExecWait'in tampered/non-executable helper durumunda $0'i "0"
    # birakabildigi GERCEK derlemeyle olculdu. Bu yuzden pozitif kanit
    # (canonical dosyanin VARLIGI) da ARANMALI - NSIS'in kendi $0 != 0
    # kontrolune EK olarak ${orIfNot} ${FileExists} ile.
    # (str.split("ExecWait") KULLANILMAZ - yorumlarda da "ExecWait" kelimesi
    # gectigi icin belirsiz olur; dogrudan desen aranir.)
    assert "${orIfNot} ${FileExists}" in icerik, (
        "ExecWait sonrasi $0 != 0 tek basina yeterli DEGIL - canonical "
        "dosyanin varligini da dogrulayan ${orIfNot} ${FileExists} kontrolu "
        "OLMALI (bkz. PDSMR-R2I tampered-helper bulgusu)"
    )
