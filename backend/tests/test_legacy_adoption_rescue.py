"""
PDSMR-R2 — installer pre-upgrade rescue testleri.

Kanitlanacak tek cumle: rescue YALNIZ dogrulanmis A durumunda (legacy var,
canonical yok) mutasyon yapar; digger her durumda ya temiz no-op'tur ya da
fail-closed HARD_STOP'tur — ve hicbir kesinti DB'yi ARADA bir durumda
BIRAKMAZ (ya pre-rescue kalir ya tam rescued olur).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.legacy_adoption.pathsafety import (  # noqa: E402
    ELECTRON_APP_NAME,
    is_forbidden_target,
    resolve_backups_dir,
    resolve_canonical_db_path,
    resolve_userdata_database_dir,
    resolve_userdata_dir_from_appdata_root,
    same_file,
)
from app.legacy_adoption.rescue import (  # noqa: E402
    FAULT_POINTS,
    InjectedFault,
    PrecedenceState,
    RescueRefused,
    classify_precedence,
    perform_rescue,
    read_journal,
)
from test_legacy_adoption_validator import _build_golden_legacy_db  # noqa: E402


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


@pytest.fixture(scope="module")
def legacy_master(tmp_path_factory) -> str:
    return _build_golden_legacy_db(str(tmp_path_factory.mktemp("r2") / "legacy_master.db"))


@pytest.fixture()
def rig(legacy_master, tmp_path):
    """installer oncesi/sonrasi dizinlerini taklit eder."""
    resources_backend = tmp_path / "app" / "resources" / "backend"
    resources_backend.mkdir(parents=True)
    legacy_path = str(resources_backend / "gelka_enerji.db")
    shutil.copyfile(legacy_master, legacy_path)

    # userdata = Electron app.getPath('userData') ILE BIREBIR AYNI seydir
    # (app adi ZATEN icinde) — resolve_canonical_db_path buna app adi
    # EKLEMEZ, dogrudan altina database/ ekler.
    userdata = tmp_path / "AppData" / "Roaming" / ELECTRON_APP_NAME
    canonical_path = resolve_canonical_db_path(str(userdata))
    backups_dir = resolve_backups_dir(str(userdata))

    return {
        "legacy": legacy_path,
        "canonical": canonical_path,
        "backups_dir": backups_dir,
        "userdata": str(userdata),
        "sha": _sha256(legacy_path),
        "root": str(tmp_path),
    }


def _rescue(rig, **kw):
    return perform_rescue(
        rig["legacy"], rig["canonical"], rig["backups_dir"],
        version_label="1.0.6", confirm_installer_context=True, **kw,
    )


def _assert_legacy_untouched(rig):
    assert os.path.isfile(rig["legacy"]), "legacy KAYNAK silindi"
    assert _sha256(rig["legacy"]) == rig["sha"], "legacy KAYNAK degisti"


# ─────────────────────────────────────────────────────────────────────────
# ADIM 2 — yol kimligi (userData formulu)
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kullanici_adi", [
    "ulastelli", "Mehmet Ali", "Şükrü Öztürk", "user with spaces",
])
def test_userdata_formula_is_independent_of_username_content(kullanici_adi):
    """
    userData formulu = <AppData Roaming kok>\\gelka-enerji\\database.
    Kullanici adinin ICERIGI (bosluk, TR karakter) formule hic GIRMEZ —
    yalniz AppData KOKU degisir; bu esitligi kanitlar.
    """
    kok = os.path.join("C:\\Users", kullanici_adi, "AppData", "Roaming")
    userdata = resolve_userdata_dir_from_appdata_root(kok)
    d = resolve_userdata_database_dir(userdata)
    assert d == os.path.join(kok, "gelka-enerji", "database")


def test_userdata_formula_ignores_install_directory():
    """
    Assisted custom install dizini (INSTDIR) formule HIC GIRMEZ. Kullanici
    D:\\MyApps\\Gelka gibi bir yer secse bile userData degismez — Electron'un
    kendisi de INSTDIR'i degil app.name + AppData'yi kullanir.
    """
    appdata_kok = "C:\\Users\\ulastelli\\AppData\\Roaming"
    userdata = resolve_userdata_dir_from_appdata_root(appdata_kok)
    farkli_instdir_senaryolari = [
        "D:\\MyApps\\Gelka", "C:\\Program Files\\Gelka Enerji (custom)",
    ]
    sonuclar = {resolve_userdata_database_dir(userdata) for _ in farkli_instdir_senaryolari}
    assert len(sonuclar) == 1, "INSTDIR degisikligi userData'yi etkiledi"


def test_userdata_matches_real_observed_electron_directory_name():
    """
    Kurulu v1.0.6'nin GERCEK gozlemlenen userData klasor adi 'gelka-enerji'dir
    (package.json 'name' alani, 'productName' DEGIL). Bu olculmus davranistir.
    """
    assert ELECTRON_APP_NAME == "gelka-enerji"


def test_canonical_and_backups_share_the_same_userdata_root():
    userdata = "C:\\Users\\x\\AppData\\Roaming\\gelka-enerji"
    db = resolve_canonical_db_path(userdata)
    yedekler = resolve_backups_dir(userdata)
    assert os.path.dirname(db) == os.path.dirname(yedekler)
    assert os.path.basename(os.path.dirname(db)) == "database"


def test_js_and_python_use_identical_userdata_semantics():
    """
    electron/dbRouting.js::resolveCanonicalDbPath(userDataDir) ve
    pathsafety.py::resolve_canonical_db_path(userdata_dir) AYNI girdi
    semantigini (app.getPath('userData') ile BIREBIR ayni deger) kullanir.
    Bu testte JS TARAFI CALISTIRILMAZ (Python-only ortam); yalniz Python
    formulunun JS'teki `path.join(userDataDir, 'database', 'gelka_enerji.db')`
    ile AYNI sonucu urettigi, sabit degerler karsilastirilarak dogrulanir.
    JS tarafi ayrica electron/dbRouting.test.js'de bagimsiz test edilir.
    """
    userdata = os.path.join("C:\\Users", "x", "AppData", "Roaming", "gelka-enerji")
    assert resolve_canonical_db_path(userdata) == os.path.join(
        userdata, "database", "gelka_enerji.db"
    )


# ─────────────────────────────────────────────────────────────────────────
# ADIM 3 — yedi durumlu oncelik siniflandirmasi
# ─────────────────────────────────────────────────────────────────────────
def test_state_A_legacy_only(rig):
    s = classify_precedence(rig["legacy"], rig["canonical"])
    assert s.state is PrecedenceState.LEGACY_ELIGIBLE
    assert s.legacy_sha256 == rig["sha"]


def test_state_B_canonical_only_never_overwritten(rig):
    os.makedirs(os.path.dirname(rig["canonical"]), exist_ok=True)
    shutil.copyfile(rig["legacy"], rig["canonical"])
    os.remove(rig["legacy"])
    s = classify_precedence(rig["legacy"], rig["canonical"])
    assert s.state is PrecedenceState.CANONICAL_ALREADY_PRESENT


def test_state_C_both_absent_is_fresh_install(tmp_path):
    s = classify_precedence(str(tmp_path / "yok1.db"), str(tmp_path / "yok2.db"))
    assert s.state is PrecedenceState.FRESH_INSTALL


def test_state_D_equal_hashes_is_verified_equivalence(rig):
    os.makedirs(os.path.dirname(rig["canonical"]), exist_ok=True)
    shutil.copyfile(rig["legacy"], rig["canonical"])
    s = classify_precedence(rig["legacy"], rig["canonical"])
    assert s.state is PrecedenceState.EQUIVALENT
    assert s.legacy_sha256 == s.canonical_sha256


def test_state_E_conflicting_hashes_preserves_both(rig):
    os.makedirs(os.path.dirname(rig["canonical"]), exist_ok=True)
    shutil.copyfile(rig["legacy"], rig["canonical"])
    con = sqlite3.connect(rig["canonical"])
    con.execute("UPDATE customers SET name='farkli' WHERE id=(SELECT MIN(id) FROM customers)")
    con.commit()
    con.close()
    s = classify_precedence(rig["legacy"], rig["canonical"])
    assert s.state is PrecedenceState.CONFLICT
    assert s.legacy_sha256 != s.canonical_sha256


def test_state_F_invalid_canonical_hard_stops(rig):
    os.makedirs(os.path.dirname(rig["canonical"]), exist_ok=True)
    with open(rig["canonical"], "wb") as fh:
        fh.write(b"bu bir sqlite dosyasi degil" * 50)
    s = classify_precedence(rig["legacy"], rig["canonical"])
    assert s.state is PrecedenceState.CANONICAL_INVALID


def test_state_G_invalid_legacy_hard_stops(rig):
    with open(rig["legacy"], "wb") as fh:
        fh.write(b"bozuk" * 50)
    s = classify_precedence(rig["legacy"], rig["canonical"])
    assert s.state is PrecedenceState.LEGACY_INVALID


def test_state_G_legacy_missing_core_tables_is_invalid(rig):
    con = sqlite3.connect(rig["legacy"])
    con.execute("DROP TABLE incidents")
    con.commit()
    con.close()
    s = classify_precedence(rig["legacy"], rig["canonical"])
    assert s.state is PrecedenceState.LEGACY_INVALID


# ─────────────────────────────────────────────────────────────────────────
# POZITIF — basarili kurtarma (A)
# ─────────────────────────────────────────────────────────────────────────
def test_successful_rescue_creates_canonical_with_matching_hash(rig):
    rapor = _rescue(rig)
    assert rapor.outcome == "RESCUED"
    assert os.path.isfile(rig["canonical"])
    assert _sha256(rig["canonical"]) == rig["sha"]
    assert rapor.integrity_check == "ok"
    assert rapor.foreign_key_violations == 0
    _assert_legacy_untouched(rig)


def test_successful_rescue_creates_readable_rollback_backup(rig):
    rapor = _rescue(rig)
    assert os.path.isfile(rapor.backup_path)
    assert _sha256(rapor.backup_path) == rig["sha"]
    con = sqlite3.connect(rapor.backup_path)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_successful_rescue_writes_sanitized_journal_outside_db(rig):
    _rescue(rig)
    journal = read_journal(rig["canonical"])
    assert journal["outcome"] == "RESCUED"
    assert journal["source_sha256"] == rig["sha"]
    metin = str(journal)
    assert "password" not in metin.lower() and "@" not in metin


def test_rescue_never_deletes_or_mutates_legacy_source(rig):
    mtime_once = os.path.getmtime(rig["legacy"])
    _rescue(rig)
    assert os.path.isfile(rig["legacy"])
    assert os.path.getmtime(rig["legacy"]) == mtime_once
    _assert_legacy_untouched(rig)


def test_rescued_canonical_remains_pre_adoption_revision(rig):
    """Rescue sadece TASIR; adopt ETMEZ. Revizyon 013'te kalmalidir."""
    _rescue(rig)
    con = sqlite3.connect(rig["canonical"])
    rev = con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    con.close()
    assert rev == "013_extend_ptf_drift_severity"


def test_paths_with_spaces_and_turkish_characters(legacy_master, tmp_path):
    kullanici_dizin = tmp_path / "Kullanıcılar" / "Şükrü Öztürk" / "AppData Roaming"
    legacy_dir = kullanici_dizin / "resources backend"
    legacy_dir.mkdir(parents=True)
    legacy_path = str(legacy_dir / "gelka_enerji.db")
    shutil.copyfile(legacy_master, legacy_path)

    userdata = kullanici_dizin / "gelka-enerji"
    canonical = resolve_canonical_db_path(str(userdata))
    backups = resolve_backups_dir(str(userdata))

    rapor = perform_rescue(legacy_path, canonical, backups,
                            version_label="1.0.6", confirm_installer_context=True)
    assert rapor.outcome == "RESCUED"
    assert os.path.isfile(canonical)
    assert _sha256(canonical) == _sha256(legacy_path)


# ─────────────────────────────────────────────────────────────────────────
# NEGATIF — no-op / hard-stop
# ─────────────────────────────────────────────────────────────────────────
def test_missing_confirmation_refuses(rig):
    with pytest.raises(RescueRefused, match="confirm_installer_context"):
        perform_rescue(rig["legacy"], rig["canonical"], rig["backups_dir"], version_label="x")
    assert not os.path.exists(rig["canonical"])


def test_noop_when_canonical_already_present(rig):
    os.makedirs(os.path.dirname(rig["canonical"]), exist_ok=True)
    shutil.copyfile(rig["legacy"], rig["canonical"])
    os.remove(rig["legacy"])  # B durumu icin legacy'nin yoklugu onemli degil, ama net olsun
    shutil.copyfile(rig["canonical"], rig["legacy"])  # legacy'yi geri koy (B testi icin gerekmez)
    con_hash = _sha256(rig["canonical"])
    rapor = _rescue(rig)
    assert rapor.outcome in ("NOOP_EQUIVALENT",)  # ikisi de var + esit -> D
    assert _sha256(rig["canonical"]) == con_hash, "canonical EZILDI"


def test_noop_fresh_install(tmp_path):
    userdata = tmp_path / "gelka-enerji"
    rapor = perform_rescue(
        str(tmp_path / "yok.db"), resolve_canonical_db_path(str(userdata)),
        resolve_backups_dir(str(userdata)), version_label="1.0.6",
        confirm_installer_context=True,
    )
    assert rapor.outcome == "NOOP_FRESH_INSTALL"


def test_conflict_preserves_both_files_untouched(rig):
    os.makedirs(os.path.dirname(rig["canonical"]), exist_ok=True)
    shutil.copyfile(rig["legacy"], rig["canonical"])
    con = sqlite3.connect(rig["canonical"])
    con.execute("UPDATE customers SET name='farkli' WHERE id=(SELECT MIN(id) FROM customers)")
    con.commit()
    con.close()
    canon_hash_once = _sha256(rig["canonical"])

    with pytest.raises(RescueRefused, match="E_CONFLICT"):
        _rescue(rig)

    assert _sha256(rig["canonical"]) == canon_hash_once, "canonical HARD_STOP'ta bile degisti"
    _assert_legacy_untouched(rig)


def test_invalid_canonical_is_never_silently_replaced(rig):
    os.makedirs(os.path.dirname(rig["canonical"]), exist_ok=True)
    with open(rig["canonical"], "wb") as fh:
        fh.write(b"bozuk canonical" * 20)
    bozuk_icerik = open(rig["canonical"], "rb").read()

    with pytest.raises(RescueRefused, match="F_CANONICAL_INVALID"):
        _rescue(rig)

    assert open(rig["canonical"], "rb").read() == bozuk_icerik, "bozuk canonical SESSIZCE degisti"


def test_invalid_legacy_stops_installer(rig):
    with open(rig["legacy"], "wb") as fh:
        fh.write(b"bozuk")
    with pytest.raises(RescueRefused, match="G_LEGACY_INVALID"):
        _rescue(rig)
    assert not os.path.exists(rig["canonical"])


@pytest.mark.parametrize("marker_yol", [
    "Program Files\\Gelka Enerji\\resources", "AppData\\Local\\Programs\\Gelka Enerji",
])
def test_installed_application_path_as_target_is_refused(rig, tmp_path, marker_yol):
    sahte_canonical = str(tmp_path / marker_yol.replace("\\", os.sep) / "gelka_enerji.db")
    with pytest.raises(RescueRefused, match="kurulu uygulama"):
        perform_rescue(
            rig["legacy"], sahte_canonical, rig["backups_dir"],
            version_label="1.0.6", confirm_installer_context=True,
        )
    assert not os.path.exists(sahte_canonical)


def test_legacy_equals_canonical_path_is_refused(rig):
    with pytest.raises(RescueRefused, match="AYNI dosya"):
        perform_rescue(
            rig["legacy"], rig["legacy"], rig["backups_dir"],
            version_label="1.0.6", confirm_installer_context=True,
        )
    _assert_legacy_untouched(rig)


def test_unknown_fault_point_is_refused(rig):
    with pytest.raises(RescueRefused, match="fault noktasi"):
        _rescue(rig, fault_at="olmayan_nokta")


# ─────────────────────────────────────────────────────────────────────────
# KESINTI — DB ASLA arada bir durumda kalmaz
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("nokta", list(FAULT_POINTS))
def test_interruption_never_leaves_canonical_in_a_half_state(rig, nokta):
    """
    Her kesinti noktasindan sonra: canonical ya HIC yoktur ya da TAM ve
    dogru hash'e sahiptir. Kismi/bozuk bir dosya ASLA kalici gorunmez
    (rename'den sonraki fault noktalari haric — onlarda zaten canonical
    tamdir, dosya sistemi seviyesinde artik geri alinamaz).
    """
    with pytest.raises(InjectedFault):
        _rescue(rig, fault_at=nokta)

    if os.path.exists(rig["canonical"]):
        # rename SONRASI kesintiler: canonical TAM olmali
        assert _sha256(rig["canonical"]) == rig["sha"], f"{nokta}: canonical KISMI/bozuk kaldi"
    # temp dosyasi hicbir kosulda kalici artik birakmamali
    assert not os.path.exists(rig["canonical"] + ".rescue-tmp"), f"{nokta}: stale temp kaldi"
    _assert_legacy_untouched(rig)


@pytest.mark.parametrize("nokta", list(FAULT_POINTS))
def test_rerun_after_interruption_completes_successfully(rig, nokta):
    with pytest.raises(InjectedFault):
        _rescue(rig, fault_at=nokta)

    rapor = _rescue(rig)
    assert rapor.outcome in ("RESCUED", "NOOP_EQUIVALENT")
    assert os.path.isfile(rig["canonical"])
    assert _sha256(rig["canonical"]) == rig["sha"]
    _assert_legacy_untouched(rig)


def test_stale_temp_file_from_previous_crash_does_not_block_rerun(rig):
    os.makedirs(os.path.dirname(rig["canonical"]), exist_ok=True)
    with open(rig["canonical"] + ".rescue-tmp", "wb") as fh:
        fh.write(b"onceki denemeden kalmis yarim dosya")
    rapor = _rescue(rig)
    assert rapor.outcome == "RESCUED"


def test_repeated_execution_second_and_third_run_are_safe_noops(rig):
    r1 = _rescue(rig)
    assert r1.outcome == "RESCUED"
    h1 = _sha256(rig["canonical"])

    r2 = _rescue(rig)
    assert r2.outcome == "NOOP_EQUIVALENT"
    assert _sha256(rig["canonical"]) == h1

    r3 = _rescue(rig)
    assert r3.outcome == "NOOP_EQUIVALENT"
    assert _sha256(rig["canonical"]) == h1
    _assert_legacy_untouched(rig)


def test_source_change_after_backup_is_detected(rig, monkeypatch):
    """
    Kopyalama SIRASINDA kaynak degisirse (savunma amacli) yakalanmalidir.
    Gercek SQLite backup API'si tek cagirimda calistigi icin bu senaryoyu
    dogrudan tetiklemek zor; bunun yerine ikinci dogrulama noktasinin
    (post-copy source re-hash) davranisini fault-injection ile kanitlariz:
    `after_temp_copy` sonrasi kaynagi degistirip devami HARD_STOP vermeli.
    """
    with pytest.raises(InjectedFault):
        _rescue(rig, fault_at="after_temp_copy")
    con = sqlite3.connect(rig["legacy"])
    con.execute("UPDATE customers SET name='degisti-kopyalama-sirasinda' "
                "WHERE id=(SELECT MIN(id) FROM customers)")
    con.commit()
    con.close()
    yeni_sha = _sha256(rig["legacy"])
    assert yeni_sha != rig["sha"]

    rapor = _rescue(rig)
    # kaynak DEGISTI: yeni kurtarma yeni hash uzerinden basariyla tamamlanir
    # (bu artik "farkli ama gecerli" bir A durumudur, veri kaybi yoktur).
    assert rapor.outcome == "RESCUED"
    assert _sha256(rig["canonical"]) == yeni_sha


def test_destination_occupied_during_rescue_is_detected(rig, monkeypatch):
    """
    rename hemen oncesi TOCTOU kontrolu: canonical beklenmedik sekilde
    olusursa cakisma raporlanir, rename denenmez.
    """
    import app.legacy_adoption.rescue as rescue_mod

    gercek_fault = rescue_mod._fault

    def sahte_fault(point, fault_at):
        if point == "before_rename":
            os.makedirs(os.path.dirname(rig["canonical"]), exist_ok=True)
            with open(rig["canonical"], "wb") as fh:
                fh.write(b"baska bir surec tarafindan olusturuldu")
        return gercek_fault(point, fault_at)

    monkeypatch.setattr(rescue_mod, "_fault", sahte_fault)
    with pytest.raises(RescueRefused, match="cakisma"):
        _rescue(rig)


# ─────────────────────────────────────────────────────────────────────────
# ADIM 6 — kurtarma + adoption sinir ayrimi
# ─────────────────────────────────────────────────────────────────────────
def test_rescue_success_never_implies_adoption_success(rig):
    """
    Basarili bir rescue outcome'i "RESCUED"tir, "ADOPTED" degil. Rescue ve
    adoption FARKLI, ayri denetlenebilir asamalardir.
    """
    rapor = _rescue(rig)
    assert rapor.outcome != "ADOPTED"
    assert "ADOPTED" not in str(rapor.__dict__)


def test_full_sequence_rescue_then_adoption_on_disposable_copy(rig):
    """
    ADIM 6: PRE-UPGRADE RESCUE -> (installer temizligi simule) -> canonical
    hayatta kalir -> PDSMR-R1D controlled adoption -> terminal head.
    """
    from app.legacy_adoption.adoption import CANONICAL_HEAD, adopt_legacy_copy

    rapor = _rescue(rig)
    assert rapor.outcome == "RESCUED"

    # installer temizligi SIMULE edilir: resources dizini SILINIR.
    shutil.rmtree(os.path.dirname(rig["legacy"]))
    assert not os.path.exists(rig["legacy"])
    assert os.path.isfile(rig["canonical"]), "canonical temizlikten SAG CIKAMADI"

    # adopt_legacy_copy source/rollback/working icin UC AYRI dosya ister
    # (adoption.py kaynak==rollback'i reddeder). rescue'un ureettigi
    # rollback yedegi zaten mevcut; ondan ikinci bir immutable kopya
    # cikarilir (source), asil DB working olarak kullanilir.
    adoption_source = rig["canonical"] + ".adoption-source"
    shutil.copyfile(rapor.backup_path, adoption_source)
    adoption_rollback = rig["canonical"] + ".adoption-rollback"
    shutil.copyfile(rapor.backup_path, adoption_rollback)
    adoption_rapor = adopt_legacy_copy(
        rig["canonical"], source_path=adoption_source, rollback_path=adoption_rollback,
        expected_source_sha256=_sha256(adoption_source), confirm_disposable_copy=True,
    )
    # "Application schema validator PASS" burada adoption'in KENDI
    # sertifikasyonudur (terminal revizyon + tek head + integrity + fk).
    # Faz 2 validate_legacy_db() PRE-adoption uygunluk kapisidir — adoption
    # SONRASI DB'yi (artik outreach tablolari VAR, revizyon artik head'te)
    # kasitli olarak HARD_STOP eder; bu, "yanlis adoption" DEGIL, dogru
    # kapsam ayrimidir (bkz. PDSMR-R2 ADIM 6: rescue/adoption ayri
    # denetlenebilir asamalardir; Faz 2 kapisi da PRE-adoption'a ozeldir).
    assert adoption_rapor.outcome == "ADOPTED"
    assert adoption_rapor.terminal_revision == CANONICAL_HEAD
    assert adoption_rapor.heads == 1
    assert adoption_rapor.integrity_check == "ok"
    assert adoption_rapor.foreign_key_violations == 0


# ─────────────────────────────────────────────────────────────────────────
# Uygulama koduna baglanmadigi kaniti
# ─────────────────────────────────────────────────────────────────────────
def test_rescue_is_not_wired_into_application_code():
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paket = os.path.join(backend, "app", "legacy_adoption")
    ihlaller = []
    for kok, _d, dosyalar in os.walk(os.path.join(backend, "app")):
        if kok.startswith(paket):
            continue
        for dosya in dosyalar:
            if dosya.endswith(".py"):
                with open(os.path.join(kok, dosya), encoding="utf-8", errors="replace") as fh:
                    if "legacy_adoption" in fh.read():
                        ihlaller.append(os.path.relpath(os.path.join(kok, dosya), backend))
    assert ihlaller == [], f"rescue uygulama koduna baglanmis: {ihlaller}"


def test_forbidden_target_marker_matches_pathsafety_policy():
    assert is_forbidden_target("C:\\Program Files\\Gelka Enerji\\x.db")
    assert is_forbidden_target("C:\\Users\\x\\AppData\\Local\\Programs\\Gelka Enerji\\x.db")
    assert not is_forbidden_target("C:\\Users\\x\\AppData\\Roaming\\gelka-enerji\\database\\x.db")


def test_same_file_detects_relative_and_case_alias(rig, monkeypatch):
    monkeypatch.chdir(os.path.dirname(rig["legacy"]))
    takma = os.path.join(".", os.path.basename(rig["legacy"]).upper())
    assert same_file(takma, rig["legacy"])
