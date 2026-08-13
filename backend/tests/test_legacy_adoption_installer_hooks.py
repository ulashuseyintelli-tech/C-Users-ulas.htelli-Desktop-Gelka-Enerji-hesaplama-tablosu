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


def test_rescue_executable_is_not_yet_bundled():
    """
    Bilinen/beklenen durum: rescue.exe bu turda GERCEKTEN derlenmedi.
    installer.nsh'nin gercek ExecWait cagrisi bilerek YORUM SATIRINDA.
    Bu test, birisi yanlislikla "etkinlestirdim" saniminda kalmasin diye
    mevcut durumu ACIKCA kayit altina alir.
    """
    yol = os.path.join(_ELECTRON_DIR, "build", "installer.nsh")
    with open(yol, encoding="utf-8") as fh:
        icerik = fh.read()
    aktif_satirlar = [
        s for s in icerik.splitlines()
        if "ExecWait" in s and not s.strip().startswith(";")
    ]
    assert aktif_satirlar == [], (
        "ExecWait etkinlestirilmis gorunuyor ama rescue.exe henuz derlenmedi "
        "— once packaging PR'inda gercek exe'yi uretin"
    )
