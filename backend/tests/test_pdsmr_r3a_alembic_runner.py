"""
PDSMR-R3A — env.py / alembic_runner.py hedef-DB override duzeltmesinin
ODAKLI testleri.

KOK NEDEN (duzeltilen): backend/alembic/env.py, FROZEN in-process cagrilarda
(_frozen_config) Config'e ONCEDEN set edilen hedefi (fresh/working calisma
kopyasi) KOSULSUZ settings.database_url (surecin DATABASE_URL ortam
degiskeni = paketlenmis uygulamada canonical yol) ile EZIYORDU - db_path
parametresi SESSIZCE YOK SAYILIYORDU. Canli frozen exe testinde KANITLANDI:
fresh_initialize() dogrudan canonical'i migrate ediyordu, 'fresh' calisma
kopyasi HIC YAZILMIYORDU (calisma-kopyasi/atomik-yayimlama guvenlik
modelinin TAMAMEN BOZULMASI). Detaylar icin bkz.
app/legacy_adoption/alembic_runner.py ve alembic/env.py modul dokstring'leri.

ONEMLI TEST-ORTAMI NOTU: bu dosyadaki testlerin bir kismi GERCEK frozen
in-process `import alembic` yolunu (sys.frozen=True + gercek alembic.ini)
calisma zamaninda tetikler. Bu testler TAMAMEN IZOLE bir alt-surecte
(subprocess) calistirilir - AYNI pytest sureci icinde CALISTIRILMAZ, cunku
test_pdsmr_r3_startup_gate.py (ve bu dosyanin kendisi de, `from
test_legacy_adoption_validator import ...` icin) `sys.path.insert(0,
backend_dir)` yapar; backend_dir KENDI icinde bir `alembic/` (migration
script'leri) klasoru barindirir - eger backend_dir sys.path'te
site-packages'DAN ONCE gelirse, `import alembic` GERCEK paket yerine bu
klasoru cozer (alembic_runner.py modul dokstring'indeki #1 golgeleme
SINIFI ile AYNI risk - PDSMR-R3A'nin KENDI kok-neden tanisinda AYNI hataya
dusulup duzeltildi, bkz. kapanis raporu). Izole alt-surec kullanmak (ve
backend_dir'i alt-surecte YALNIZ append etmek, insert(0,...) DEGIL) bu
riski YAPISAL olarak ORTADAN KALDIRIR.

`_validate_frozen_target_path()`/`_frozen_config()` testleri BUNUN DISINDA
- alembic import ETMEDEN ONCE calisirlar (bkz. alembic_runner.py'deki kontrol
sirasi), bu yuzden ANA pytest surecinde GUVENLE calistirilabilir.

Cagrildigi yerler:
- CI/manuel `pytest backend/tests/test_pdsmr_r3a_alembic_runner.py`
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

# ONEMLI: append, insert(0,...) DEGIL - test_pdsmr_r3_startup_gate.py
# BUNU insert(0,...) ile yapar, ama BU DOSYADA `_frozen_config()`'in bazi
# testleri ANA pytest surecinde `from alembic.config import Config`'e
# GERCEKTEN ulasir (gecerli bir yol icin) - eger backend_dir (kendi
# `alembic/` migration PAKETIYLE) site-packages'DAN ONCE gelirse, 'import
# alembic' bunu golgeler (bkz. modul dokstring'i - bu dosyanin KENDI ilk
# taslaginda AYNI hataya dusulup duzeltildi). `app`/
# `test_legacy_adoption_validator` icin sira ONEMLI DEGIL (baska hicbir
# yerde ayni adla YOK), bu yuzden append GUVENLE yeterlidir.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from test_legacy_adoption_validator import _build_golden_legacy_db  # noqa: E402

from app.legacy_adoption import alembic_runner as ar  # noqa: E402
from app.legacy_adoption.lineage import CANONICAL_HEAD  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAHTE_EXE = os.path.join(BACKEND_DIR, "sahte_gelka-backend.exe")
DECOY_DEFAULT = "sqlite:///./ASLA_KULLANILMAMALI_pdsmr_r3a_decoy.db"
RESULT_MARKER = "PDSMR_R3A_RESULT_JSON:"


def _sha256(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_isolated(body: str, *, extra_env: dict | None = None, timeout: int = 90) -> subprocess.CompletedProcess:
    """
    `body`'yi TAMAMEN izole bir Python surecinde calistirir.

    IKI AYRI, BAGIMSIZ mekanizma dogru sekilde ele alinir (ikisi de
    PDSMR-R3A'nin KENDI tanisinda GERCEKTEN yasandi - bkz. modul dokstring'i):
      1) IMPORT cozumu (sys.path, Python-seviyesi): `python -c`
         `sys.path[0]`'i BOS STRING (`''`) olarak koyar - CALISMA ZAMANINDA
         CWD'ye COZULEN DINAMIK bir giristir. CWD backend_dir ise (kendi
         `alembic/` migration PAKETINI - __init__.py ile - barindirir),
         `''` bunu site-packages'DAN ONCE golgeler; `sys.path.append(...)`
         sirasi BUNU ONLEMEZ, cunku sorun `''` girisidir, benim EKLEDIGIM
         giris DEGIL. `''` ACIKCA CIKARILIR (backend_dir ise HALA YALNIZ,
         site-packages'dan SONRA, append edilir - insert(0,...) ASLA
         kullanilmaz).
      2) script_location cozumu (Alembic-seviyesi, sys.path'TEN BAGIMSIZ
         AYRI bir mekanizma): alembic.ini'deki GORECE `script_location =
         alembic` degeri CWD'YE GORE cozulur (ampirik DOGRULANDI - bu,
         `_frozen_config()`'un ONCEKI "ini dizinine gore cozer" varsayimini
         YANLISLADI, bkz. o fonksiyonun guncellenmis yorumu). Gercek
         frozen exe'de bu, run_server.py'nin KENDI, frozen modda kosulsuz
         `os.chdir(base_dir)` cagrisiyla (satir 10-13) GUVENCE altindadir -
         base_dir = alembic.ini'nin bulundugu dizin. Bu YUZDEN CWD BURADA
         backend_dir'e (ASLA notr bir dizine DEGIL) CEKILIR - (1)'in
         COZUMU (`''` cikarma) BUNUNLA CELISMEZ, cunku ikisi TAMAMEN
         FARKLI cozumleme katmanlaridir (biri sys.path/import, digeri
         Alembic'in kendi dosya-yolu birlestirmesi).

    `body`, sonucunu `print(RESULT_MARKER + json.dumps(...))` ile TEK
    SATIRDA bildirmelidir - stdout'taki BASKA satirlar yok sayilir.
    """
    onek = (
        "import sys, os, json\n"
        "sys.path = [p for p in sys.path if p != '']\n"
        f"sys.path.append({BACKEND_DIR!r})\n"
        f"os.chdir({BACKEND_DIR!r})\n"
        f"RESULT_MARKER = {RESULT_MARKER!r}\n"
    )
    tam = onek + "\n" + textwrap.dedent(body)
    ortam = dict(os.environ)
    ortam.pop("DATABASE_URL", None)
    ortam.pop("GELKA_PACKAGED_RUNTIME", None)
    if extra_env:
        ortam.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", tam],
        capture_output=True, text=True, timeout=timeout, env=ortam,
    )


def _extract_result(sonuc: subprocess.CompletedProcess) -> dict:
    for satir in sonuc.stdout.splitlines():
        if satir.startswith(RESULT_MARKER):
            return json.loads(satir[len(RESULT_MARKER):])
    pytest.fail(
        f"alt-surecten JSON sonuc alinamadi (returncode={sonuc.returncode})\n"
        f"--- stdout ---\n{sonuc.stdout}\n--- stderr (son 4000) ---\n{sonuc.stderr[-4000:]}"
    )


# ═══════════════════════════════════════════════════════════════════════
# A/F — _validate_frozen_target_path / _frozen_config: IN-PROCESS (guvenli,
# alembic import ETMEZ - bkz. alembic_runner.py'deki kontrol sirasi).
# ═══════════════════════════════════════════════════════════════════════
def test_validate_accepts_well_formed_absolute_normalized_path(tmp_path):
    yol = str(tmp_path / "alt" / "gelka_enerji.db")
    assert ar._validate_frozen_target_path(yol) == yol


def test_validate_rejects_relative_path():
    with pytest.raises(ValueError):
        ar._validate_frozen_target_path("gelka_enerji.db")


def test_validate_rejects_empty_string():
    with pytest.raises(ValueError):
        ar._validate_frozen_target_path("")


def test_validate_rejects_nul_byte(tmp_path):
    with pytest.raises(ValueError):
        ar._validate_frozen_target_path(str(tmp_path / "x\x00.db"))


def test_validate_rejects_sqlite_uri_scheme():
    with pytest.raises(ValueError):
        ar._validate_frozen_target_path("sqlite:///C:/foo/gelka_enerji.db")


def test_validate_rejects_file_uri_scheme():
    with pytest.raises(ValueError):
        ar._validate_frozen_target_path("file:C:/foo/gelka_enerji.db")


def test_validate_rejects_query_string(tmp_path):
    with pytest.raises(ValueError):
        ar._validate_frozen_target_path(str(tmp_path / "gelka_enerji.db") + "?mode=ro")


def test_validate_rejects_dot_dot_segment_non_normalized(tmp_path):
    bozuk = str(tmp_path / "alt" / ".." / "alt" / "gelka_enerji.db")
    with pytest.raises(ValueError):
        ar._validate_frozen_target_path(bozuk)


@pytest.mark.skipif(os.sep != "\\", reason="yalniz Windows'ta anlamli")
def test_validate_rejects_forward_slash_only_path_non_normalized(tmp_path):
    # os.path.normpath Windows'ta '/' -> '\\' cevirir; bu yuzden sadece
    # '/' iceren bir yol normalize edilince DEGISIR -> REDDEDILMELI.
    bozuk = str(tmp_path).replace("\\", "/") + "/gelka_enerji.db"
    with pytest.raises(ValueError):
        ar._validate_frozen_target_path(bozuk)


def test_frozen_config_rejects_invalid_path_before_importing_alembic(monkeypatch):
    """ini VAR (BACKEND_DIR'de gercek alembic.ini) ama db_path gecersiz ->
    ValueError, `from alembic.config import Config` satirina hic ULASILMADAN
    (bkz. alembic_runner.py'deki kontrol sirasi) - bu yuzden ANA surecte
    GUVENLE test edilebilir."""
    monkeypatch.setattr(sys, "executable", SAHTE_EXE)
    with pytest.raises(ValueError):
        ar._frozen_config("relative_gelka_enerji.db")


def test_frozen_config_raises_alembic_unavailable_when_ini_missing_even_for_valid_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "yok_burada" / "sahte.exe"))
    gecerli_yol = str(tmp_path / "gelka_enerji.db")
    with pytest.raises(ar.AlembicUnavailable):
        ar._frozen_config(gecerli_yol)


def test_frozen_config_sets_attribute_not_sqlalchemy_url(tmp_path):
    """`_frozen_config` artik `sqlalchemy.url`'i DOGRUDAN set ETMEZ - tek
    kaynak `Config.attributes["pdsmr_target_db_path"]`dir (env.py bunu okur).

    IZOLE alt-surecte calisir (ANA surecte DEGIL): bu dosyanin `from
    test_legacy_adoption_validator import _build_golden_legacy_db`
    satiri, O DOSYANIN (yetki alani DISINDA, dokunulmayan) KENDI
    `sys.path.insert(0, backend_dir)` yan etkisini tetikler - `_frozen_config`
    burada GECERLI bir yol icin GERCEKTEN `from alembic.config import
    Config`e ulastigindan, ANA surecte bu golgelemeye MARUZ KALIR (bkz.
    modul dokstring'i)."""
    hedef = str(tmp_path / "gelka_enerji.db")
    body = f"""
        import sys
        sys.executable = {SAHTE_EXE!r}
        from app.legacy_adoption import alembic_runner as ar
        cfg = ar._frozen_config({hedef!r})
        print(RESULT_MARKER + json.dumps({{
            "attribute": cfg.attributes.get("pdsmr_target_db_path"),
        }}))
    """
    sonuc = _run_isolated(body, extra_env={"DATABASE_URL": DECOY_DEFAULT})
    assert sonuc.returncode == 0, sonuc.stderr[-4000:]
    veri = _extract_result(sonuc)
    assert veri["attribute"] == hedef


# ═══════════════════════════════════════════════════════════════════════
# A/B — env.py'nin iki dali (attribute yok -> settings.database_url;
# attribute var -> onu EZER): IZOLE alt-surec.
# ═══════════════════════════════════════════════════════════════════════
def test_env_py_attribute_absent_falls_back_to_settings_database_url_unchanged(tmp_path):
    """Kontrat A: attribute YOKSA mevcut CLI/dev-subprocess davranisi
    (settings.database_url -> DATABASE_URL ortam degiskeni) AYNEN korunur."""
    hedef = str(tmp_path / "settings_hedefi" / "gelka_enerji.db")
    os.makedirs(os.path.dirname(hedef), exist_ok=True)
    body = f"""
        from alembic.config import Config
        from alembic.command import upgrade
        cfg = Config({os.path.join(BACKEND_DIR, "alembic.ini")!r})
        # attribute KASITLI OLARAK set edilmiyor -> env.py'nin ELSE dalini test eder.
        upgrade(cfg, {CANONICAL_HEAD!r})
        import sqlite3
        con = sqlite3.connect({hedef!r})
        rev = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        con.close()
        print(RESULT_MARKER + json.dumps({{"terminal_revision": rev}}))
    """
    sonuc = _run_isolated(body, extra_env={"DATABASE_URL": "sqlite:///" + hedef.replace("\\", "/")})
    assert sonuc.returncode == 0, sonuc.stderr[-4000:]
    veri = _extract_result(sonuc)
    assert veri["terminal_revision"] == CANONICAL_HEAD
    assert os.path.isfile(hedef)


def test_env_py_attribute_present_overrides_settings_database_url_decoy_untouched(tmp_path):
    """Kontrat B (CEKIRDEK regresyon testi): settings.database_url KASITLI
    olarak bir DECOY'u gosterirken, migration YALNIZ attribute ile verilen
    hedefe uygulanir - decoy HIC OLUSMAZ."""
    hedef = str(tmp_path / "gercek_hedef" / "fresh.db")
    os.makedirs(os.path.dirname(hedef), exist_ok=True)
    decoy = str(tmp_path / "yanlislikla_DATABASE_URL_gosterdigi_canonical" / "gelka_enerji.db")
    # decoy dizini BILEREK olusturulmuyor.
    body = f"""
        from alembic.config import Config
        from alembic.command import upgrade
        cfg = Config({os.path.join(BACKEND_DIR, "alembic.ini")!r})
        cfg.attributes["pdsmr_target_db_path"] = {hedef!r}
        upgrade(cfg, {CANONICAL_HEAD!r})
        import sqlite3
        con = sqlite3.connect({hedef!r})
        rev = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        con.close()
        print(RESULT_MARKER + json.dumps({{"terminal_revision": rev}}))
    """
    sonuc = _run_isolated(body, extra_env={"DATABASE_URL": "sqlite:///" + decoy.replace("\\", "/")})
    assert sonuc.returncode == 0, sonuc.stderr[-4000:]
    veri = _extract_result(sonuc)
    assert veri["terminal_revision"] == CANONICAL_HEAD
    assert os.path.isfile(hedef), "hedef (attribute ile verilen) dosya OLUSMADI"
    assert not os.path.exists(decoy), (
        "DECOY (DATABASE_URL'in yanlislikla gosterdigi) dosya OLUSTU - "
        "KOK NEDEN duzelmemis: migration hala settings.database_url'e gidiyor"
    )


# ═══════════════════════════════════════════════════════════════════════
# E — tam hedef kaniti: PRAGMA database_list + bosluk/Turkce/'%' Windows yolu.
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("klasor_adi", [
    "bosluklu klasor adi",
    "türkçe şğıöçü karakterler",
    "yuzde % isaretli",
    "hepsi turkce % ve boşluk",
])
def test_env_py_special_character_windows_paths_open_exact_intended_file(tmp_path, klasor_adi):
    hedef = str(tmp_path / klasor_adi / "gelka_enerji.db")
    os.makedirs(os.path.dirname(hedef), exist_ok=True)
    body = f"""
        from alembic.config import Config
        from alembic.command import upgrade
        cfg = Config({os.path.join(BACKEND_DIR, "alembic.ini")!r})
        cfg.attributes["pdsmr_target_db_path"] = {hedef!r}
        upgrade(cfg, {CANONICAL_HEAD!r})
        import sqlite3
        con = sqlite3.connect({hedef!r})
        satirlar = con.execute("PRAGMA database_list").fetchall()
        dosya = [r[2] for r in satirlar if r[1] == "main"][0]
        con.close()
        print(RESULT_MARKER + json.dumps({{"pragma_dosya": dosya}}))
    """
    sonuc = _run_isolated(body, extra_env={"DATABASE_URL": DECOY_DEFAULT})
    assert sonuc.returncode == 0, sonuc.stderr[-4000:]
    veri = _extract_result(sonuc)
    assert os.path.normcase(veri["pragma_dosya"]) == os.path.normcase(hedef), (
        f"PRAGMA database_list ile ACILAN fiziksel dosya beklenenle ESLESMIYOR: "
        f"{veri['pragma_dosya']!r} != {hedef!r}"
    )


# ═══════════════════════════════════════════════════════════════════════
# B/F — gercek alembic_runner.alembic_upgrade() sarmalayicisi, frozen taklidi.
# ═══════════════════════════════════════════════════════════════════════
def test_frozen_alembic_upgrade_wrapper_targets_exact_path_not_environment_database_url(tmp_path):
    hedef = str(tmp_path / "fresh_calisma_kopyasi" / "gelka_enerji.db")
    os.makedirs(os.path.dirname(hedef), exist_ok=True)
    decoy = str(tmp_path / "canonical_decoy" / "gelka_enerji.db")
    body = f"""
        import sys
        sys.frozen = True
        sys.executable = {SAHTE_EXE!r}
        from app.legacy_adoption import alembic_runner as ar
        ar.alembic_upgrade({hedef!r}, {CANONICAL_HEAD!r})
        n = ar.alembic_heads_count({hedef!r})
        import sqlite3
        con = sqlite3.connect({hedef!r})
        rev = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        con.close()
        print(RESULT_MARKER + json.dumps({{"terminal_revision": rev, "heads": n}}))
    """
    sonuc = _run_isolated(body, extra_env={"DATABASE_URL": "sqlite:///" + decoy.replace("\\", "/")})
    assert sonuc.returncode == 0, sonuc.stderr[-4000:]
    veri = _extract_result(sonuc)
    assert veri["terminal_revision"] == CANONICAL_HEAD
    assert veri["heads"] == 1
    assert not os.path.exists(decoy)


def test_frozen_alembic_upgrade_wrapper_rejects_invalid_target_path_end_to_end(tmp_path):
    """Kontrat F, sarmalayici seviyesinde: gecersiz db_path GERCEK frozen
    taklidinde bile HARD_STOP'a (RuntimeError) ulasir, create_all yok."""
    body = f"""
        import sys
        sys.frozen = True
        sys.executable = {SAHTE_EXE!r}
        from app.legacy_adoption import alembic_runner as ar
        try:
            ar.alembic_upgrade("relative_gelka_enerji.db", {CANONICAL_HEAD!r})
            sonuc = {{"raised": False}}
        except RuntimeError as exc:
            sonuc = {{"raised": True, "mesaj": str(exc)}}
        print(RESULT_MARKER + json.dumps(sonuc))
    """
    sonuc = _run_isolated(body, extra_env={"DATABASE_URL": DECOY_DEFAULT})
    assert sonuc.returncode == 0, sonuc.stderr[-4000:]
    veri = _extract_result(sonuc)
    assert veri["raised"] is True, "relative bir hedef icin RuntimeError BEKLENIYORDU"


# ═══════════════════════════════════════════════════════════════════════
# C/D — startup_gate.py uctan uca (fresh_initialize / perform_controlled_
# adoption), frozen taklidi, DATABASE_URL BILEREK bir DECOY'u gosterir.
# startup_gate.py'ye HIC DOKUNULMADI - yalniz mevcut davranisi frozen
# taklidinde EXERCISE edilir.
# ═══════════════════════════════════════════════════════════════════════
def test_frozen_fresh_initialize_never_touches_environment_database_url_decoy(tmp_path):
    canonical = str(tmp_path / "userData" / "database" / "gelka_enerji.db")
    decoy = str(tmp_path / "YANLISLIKLA_DATABASE_URL" / "gelka_enerji.db")
    body = f"""
        import sys
        sys.frozen = True
        sys.executable = {SAHTE_EXE!r}
        from app.legacy_adoption import startup_gate as sg
        rapor = sg.run_startup_gate({canonical!r})
        ikinci = sg.run_startup_gate({canonical!r})
        print(RESULT_MARKER + json.dumps({{
            "action": rapor.action,
            "terminal_revision": rapor.terminal_revision,
            "heads": rapor.heads,
            "integrity_check": rapor.integrity_check,
            "foreign_key_violations": rapor.foreign_key_violations,
            "ikinci_action": ikinci.action,
        }}))
    """
    sonuc = _run_isolated(body, extra_env={"DATABASE_URL": "sqlite:///" + decoy.replace("\\", "/")})
    assert sonuc.returncode == 0, sonuc.stderr[-4000:]
    veri = _extract_result(sonuc)
    assert veri["action"] == "FRESH_INITIALIZED"
    assert veri["terminal_revision"] == CANONICAL_HEAD
    assert veri["heads"] == 1
    assert veri["integrity_check"] == "ok"
    assert veri["foreign_key_violations"] == 0
    assert veri["ikinci_action"] == "CERTIFIED_NOOP"
    assert os.path.isfile(canonical)
    assert not os.path.exists(decoy), (
        "DECOY (DATABASE_URL) dosyasi OLUSTU - fresh_initialize hala yanlis hedefe yaziyor"
    )


@pytest.fixture(scope="module")
def r3a_legacy_master(tmp_path_factory) -> str:
    return _build_golden_legacy_db(str(tmp_path_factory.mktemp("r3a") / "legacy_master.db"))


@pytest.fixture()
def r3a_rescued_rig(r3a_legacy_master, tmp_path):
    """GERCEK bir PDSMR-R2 rescue calistirir (perform_rescue) - kod tekrari
    yok, kurgu journal YOK (test_pdsmr_r3_startup_gate.py::rescued_rig ile
    AYNI ilke)."""
    resources_backend = tmp_path / "app" / "resources" / "backend"
    resources_backend.mkdir(parents=True)
    legacy_path = str(resources_backend / "gelka_enerji.db")
    shutil.copyfile(r3a_legacy_master, legacy_path)

    userdata = tmp_path / "AppData" / "Roaming" / "gelka-enerji"
    canonical_path = str(userdata / "database" / "gelka_enerji.db")
    backups_dir = str(userdata / "database" / "backups")

    from app.legacy_adoption.rescue import perform_rescue

    perform_rescue(
        legacy_path, canonical_path, backups_dir,
        version_label="1.0.6", confirm_installer_context=True,
    )
    return {"legacy": legacy_path, "canonical": canonical_path}


def test_frozen_controlled_adoption_never_touches_environment_database_url_decoy(r3a_rescued_rig, tmp_path):
    canonical = r3a_rescued_rig["canonical"]
    decoy = str(tmp_path / "YANLISLIKLA_DATABASE_URL" / "gelka_enerji.db")
    body = f"""
        import sys
        sys.frozen = True
        sys.executable = {SAHTE_EXE!r}
        from app.legacy_adoption import startup_gate as sg
        rapor = sg.run_startup_gate({canonical!r})
        ikinci = sg.run_startup_gate({canonical!r})
        print(RESULT_MARKER + json.dumps({{
            "action": rapor.action,
            "terminal_revision": rapor.terminal_revision,
            "heads": rapor.heads,
            "integrity_check": rapor.integrity_check,
            "foreign_key_violations": rapor.foreign_key_violations,
            "ikinci_action": ikinci.action,
        }}))
    """
    sonuc = _run_isolated(body, extra_env={"DATABASE_URL": "sqlite:///" + decoy.replace("\\", "/")})
    assert sonuc.returncode == 0, sonuc.stderr[-4000:]
    veri = _extract_result(sonuc)
    assert veri["action"] == "ADOPTED"
    assert veri["terminal_revision"] == CANONICAL_HEAD
    assert veri["heads"] == 1
    assert veri["integrity_check"] == "ok"
    assert veri["foreign_key_violations"] == 0
    assert veri["ikinci_action"] == "CERTIFIED_NOOP"
    assert not os.path.exists(decoy), (
        "DECOY (DATABASE_URL) dosyasi OLUSTU - adoption hala yanlis hedefe yaziyor"
    )


def test_frozen_controlled_adoption_fault_before_publish_leaves_canonical_untouched(r3a_rescued_rig):
    """Kontrat D: publish-oncesi fault injection canonical'i DEGISTIRMEZ;
    ikinci (fault'suz) calistirma BASARIYLA tamamlanir (deterministik
    kurtarma) - GERCEK frozen taklidinde."""
    canonical = r3a_rescued_rig["canonical"]
    onceki_hash = _sha256(canonical)

    body1 = f"""
        import sys
        sys.frozen = True
        sys.executable = {SAHTE_EXE!r}
        from app.legacy_adoption import startup_gate as sg
        try:
            sg.perform_controlled_adoption({canonical!r}, fault_at="before_atomic_publish")
            hata = None
        except sg.InjectedFault as exc:
            hata = str(exc)
        print(RESULT_MARKER + json.dumps({{"hata": hata}}))
    """
    sonuc1 = _run_isolated(body1, extra_env={"DATABASE_URL": DECOY_DEFAULT})
    assert sonuc1.returncode == 0, sonuc1.stderr[-4000:]
    veri1 = _extract_result(sonuc1)
    assert veri1["hata"] is not None, "InjectedFault beklendi"
    assert _sha256(canonical) == onceki_hash, (
        "publish-oncesi fault injection SONRASI canonical DEGISTI - "
        "calisma-kopyasi/atomik-yayimlama modeli hala bozuk"
    )

    body2 = f"""
        import sys
        sys.frozen = True
        sys.executable = {SAHTE_EXE!r}
        from app.legacy_adoption import startup_gate as sg
        rapor = sg.run_startup_gate({canonical!r})
        print(RESULT_MARKER + json.dumps({{
            "terminal_revision": rapor.terminal_revision, "action": rapor.action,
        }}))
    """
    sonuc2 = _run_isolated(body2, extra_env={"DATABASE_URL": DECOY_DEFAULT})
    assert sonuc2.returncode == 0, sonuc2.stderr[-4000:]
    veri2 = _extract_result(sonuc2)
    assert veri2["terminal_revision"] == CANONICAL_HEAD
    assert veri2["action"] == "ADOPTED"
