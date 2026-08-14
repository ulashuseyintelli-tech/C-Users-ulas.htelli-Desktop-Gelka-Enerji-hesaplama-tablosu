"""
PDSMR-R3 — startup_gate.py testleri.

Kanitlanacak: run_startup_gate() HER durumda ya basariyla bir GateResult
doner ya GateRefused firlatir — hicbir varsayilan/bilinmeyen dal app
baslangicina GECMEZ. create_all() bu modulde hic YOKTUR (Base.metadata
import edilmez).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_legacy_adoption_validator import _build_golden_legacy_db  # noqa: E402

from app.legacy_adoption import alembic_runner as ar  # noqa: E402
from app.legacy_adoption import policy  # noqa: E402
from app.legacy_adoption import startup_gate as sg  # noqa: E402
from app.legacy_adoption.lineage import CANONICAL_HEAD, PRODUCTION_BRANCH_TIP  # noqa: E402
from app.legacy_adoption.rescue import perform_rescue, read_journal  # noqa: E402


def _sha256(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def legacy_master(tmp_path_factory) -> str:
    return _build_golden_legacy_db(str(tmp_path_factory.mktemp("r3") / "legacy_master.db"))


@pytest.fixture()
def rescued_rig(legacy_master, tmp_path):
    """
    GERCEK bir PDSMR-R2 rescue calistirir (perform_rescue) - boylece
    startup_gate testleri GERCEK bir rescue journal + rollback yedegi
    kullanir (kod tekrari yok, kurgu journal YOK).
    """
    resources_backend = tmp_path / "app" / "resources" / "backend"
    resources_backend.mkdir(parents=True)
    legacy_path = str(resources_backend / "gelka_enerji.db")
    shutil.copyfile(legacy_master, legacy_path)

    userdata = tmp_path / "AppData" / "Roaming" / "gelka-enerji"
    canonical_path = str(userdata / "database" / "gelka_enerji.db")
    backups_dir = str(userdata / "database" / "backups")

    perform_rescue(
        legacy_path, canonical_path, backups_dir,
        version_label="1.0.6", confirm_installer_context=True,
    )
    return {
        "legacy": legacy_path,
        "canonical": canonical_path,
        "backups_dir": backups_dir,
        "root": str(tmp_path),
    }


def _touch_sqlite(path: str) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.close()


# ── classify_startup_state ────────────────────────────────────────────────
def test_state_A_db_absent(tmp_path):
    canonical = str(tmp_path / "database" / "gelka_enerji.db")
    assert sg.classify_startup_state(canonical) is sg.BootstrapState.DB_ABSENT


def test_state_B_verified_legacy_013(rescued_rig):
    assert sg.classify_startup_state(rescued_rig["canonical"]) is sg.BootstrapState.VERIFIED_LEGACY_013


def test_state_C_verified_canonical_head(rescued_rig):
    sg.perform_controlled_adoption(rescued_rig["canonical"])
    assert sg.classify_startup_state(rescued_rig["canonical"]) is sg.BootstrapState.VERIFIED_CANONICAL_HEAD


def test_state_D_partial_s5_schema(rescued_rig):
    """013 revizyonunda + S5 tablolarindan biri elle eklenmis (create_all
    fail-open imzasi) -> PARTIAL_S5_SCHEMA, HARD_STOP."""
    con = sqlite3.connect(rescued_rig["canonical"])
    con.execute(
        "CREATE TABLE outreach_messages (id INTEGER PRIMARY KEY, tenant_id TEXT)"
    )
    con.commit()
    con.close()
    assert sg.classify_startup_state(rescued_rig["canonical"]) is sg.BootstrapState.PARTIAL_S5_SCHEMA


@pytest.mark.parametrize("icerik_uretici", [
    lambda p: open(p, "wb").write(b"gecerli bir sqlite dosyasi degil"),
    lambda p: _touch_sqlite(p),  # gecerli sqlite ama alembic_version YOK
])
def test_state_E_unknown_schema(tmp_path, icerik_uretici):
    canonical = tmp_path / "gelka_enerji.db"
    icerik_uretici(str(canonical))
    assert sg.classify_startup_state(str(canonical)) is sg.BootstrapState.UNKNOWN_SCHEMA


def test_state_E_unknown_revision(tmp_path):
    canonical = tmp_path / "gelka_enerji.db"
    con = sqlite3.connect(str(canonical))
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
    con.execute("INSERT INTO alembic_version VALUES ('deadbeef0000')")
    con.commit()
    con.close()
    assert sg.classify_startup_state(str(canonical)) is sg.BootstrapState.UNKNOWN_SCHEMA


def test_state_E_two_heads(tmp_path):
    canonical = tmp_path / "gelka_enerji.db"
    con = sqlite3.connect(str(canonical))
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
    con.execute(f"INSERT INTO alembic_version VALUES ('{CANONICAL_HEAD}')")
    con.execute("INSERT INTO alembic_version VALUES ('deadbeef0000')")
    con.commit()
    con.close()
    assert sg.classify_startup_state(str(canonical)) is sg.BootstrapState.UNKNOWN_SCHEMA


def test_state_G_canonical_absent_legacy_exists(tmp_path):
    legacy = tmp_path / "resources" / "backend" / "gelka_enerji.db"
    legacy.parent.mkdir(parents=True)
    _touch_sqlite(str(legacy))
    canonical = tmp_path / "userData" / "database" / "gelka_enerji.db"
    assert sg.classify_startup_state(
        str(canonical), legacy_hint_path=str(legacy)
    ) is sg.BootstrapState.CANONICAL_ABSENT_LEGACY_EXISTS


def test_stale_working_artifact_is_cleaned_before_classification(rescued_rig):
    """F) INTERRUPTED_ADOPTION_STATE'in deterministik kurtarmasi: onceki
    crash'ten kalan .pdsmr-r3-working dosyasi SESSIZCE temizlenir, canonical
    (013, hic dokunulmamis) durumuna gore siniflandirma YAPILIR."""
    kalinti = rescued_rig["canonical"] + sg.WORKING_SUFFIX
    with open(kalinti, "wb") as fh:
        fh.write(b"crash'ten kalan yarim veri")
    assert os.path.isfile(kalinti)
    durum = sg.classify_startup_state(rescued_rig["canonical"])
    assert durum is sg.BootstrapState.VERIFIED_LEGACY_013
    assert not os.path.isfile(kalinti), "kalinti temizlenmedi"


# ── run_startup_gate: uctan uca dispatch ──────────────────────────────────
@pytest.mark.skipif(not ar.is_alembic_available(), reason="alembic calistirilabiliri yok")
def test_fresh_initialize_reaches_canonical_head(tmp_path):
    canonical = str(tmp_path / "userData" / "database" / "gelka_enerji.db")
    rapor = sg.run_startup_gate(canonical)
    assert rapor.action == "FRESH_INITIALIZED"
    assert rapor.terminal_revision == CANONICAL_HEAD
    assert rapor.heads == 1
    assert rapor.integrity_check == "ok"
    assert rapor.foreign_key_violations == 0
    assert os.path.isfile(canonical)
    assert sg.classify_startup_state(canonical) is sg.BootstrapState.VERIFIED_CANONICAL_HEAD


@pytest.mark.skipif(not ar.is_alembic_available(), reason="alembic calistirilabiliri yok")
def test_fresh_initialize_second_run_is_noop_certification(tmp_path):
    canonical = str(tmp_path / "userData" / "database" / "gelka_enerji.db")
    sg.run_startup_gate(canonical)
    ikinci = sg.run_startup_gate(canonical)
    assert ikinci.action == "CERTIFIED_NOOP"
    assert ikinci.terminal_revision == CANONICAL_HEAD


def test_startup_gate_source_never_calls_create_all_or_imports_database():
    """
    startup_gate modulunun KAYNAK KODUNDA (yorum/docstring DEGIL, GERCEK
    AST dugumlerinde) `create_all` cagrisi veya `app.database` importu
    GECMEZ - modul app.database'i (dolayisiyla Base.metadata/engine'i)
    HIC BILMEZ. Bu, yorumlardaki "create_all() ASLA cagrilmaz" gibi DUZ
    METIN aciklamalarindan BAGIMSIZ, statik/kesin bir kanittir (ast
    kullanir - basit metin arama YANLIS POZITIF verir, cunku bu dosyanin
    KENDI yorumlarinda da "create_all" kelimesi GECER).
    """
    import ast
    import inspect

    kaynak = inspect.getsource(sg)
    agac = ast.parse(kaynak)

    cagrilan_isimler = set()
    for node in ast.walk(agac):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                cagrilan_isimler.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                cagrilan_isimler.add(node.func.id)
        if isinstance(node, ast.Import):
            for isim in node.names:
                assert "database" not in isim.name.split("."), (
                    f"startup_gate.py app.database'i IMPORT EDIYOR: {isim.name}"
                )
        if isinstance(node, ast.ImportFrom):
            if node.module and "database" in node.module.split("."):
                pytest.fail(f"startup_gate.py app.database'DEN import ediyor: {node.module}")

    assert "create_all" not in cagrilan_isimler, "startup_gate.py create_all() CAGIRIYOR"


@pytest.mark.skipif(not ar.is_alembic_available(), reason="alembic calistirilabiliri yok")
def test_controlled_adoption_reaches_canonical_head_with_s5_tables(rescued_rig):
    rapor = sg.run_startup_gate(rescued_rig["canonical"])
    assert rapor.action == "ADOPTED"
    assert rapor.terminal_revision == CANONICAL_HEAD
    assert rapor.heads == 1
    assert rapor.integrity_check == "ok"
    assert rapor.foreign_key_violations == 0
    for kritik, beklenen in policy.EXPECTED_ROW_COUNTS.items():
        assert rapor.row_counts.get(kritik) == beklenen, f"{kritik} satir sayisi degisti"

    con = sqlite3.connect(rescued_rig["canonical"])
    tablolar = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    con.close()
    for s5 in policy.EXPECTED_ABSENT_MODEL_TABLES:
        assert s5 in tablolar


@pytest.mark.skipif(not ar.is_alembic_available(), reason="alembic calistirilabiliri yok")
def test_controlled_adoption_second_run_is_certified_noop(rescued_rig):
    sg.run_startup_gate(rescued_rig["canonical"])
    ikinci = sg.run_startup_gate(rescued_rig["canonical"])
    assert ikinci.action == "CERTIFIED_NOOP"
    assert ikinci.terminal_revision == CANONICAL_HEAD


@pytest.mark.skipif(not ar.is_alembic_available(), reason="alembic calistirilabiliri yok")
def test_controlled_adoption_preserves_legacy_source_untouched(rescued_rig):
    onceki_hash = _sha256(rescued_rig["legacy"])
    sg.run_startup_gate(rescued_rig["canonical"])
    assert _sha256(rescued_rig["legacy"]) == onceki_hash, "legacy kaynak DEGISTI"


@pytest.mark.skipif(not ar.is_alembic_available(), reason="alembic calistirilabiliri yok")
def test_controlled_adoption_preserves_rollback_backup(rescued_rig):
    journal = read_journal(rescued_rig["canonical"])
    yedek_hash_once = _sha256(journal["backup_path"])
    sg.run_startup_gate(rescued_rig["canonical"])
    assert os.path.isfile(journal["backup_path"]), "R2 rollback yedegi silindi"
    assert _sha256(journal["backup_path"]) == yedek_hash_once, "R2 rollback yedegi DEGISTI"


def test_partial_s5_schema_hard_stops(rescued_rig):
    con = sqlite3.connect(rescued_rig["canonical"])
    con.execute("CREATE TABLE outreach_templates (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    onceki_hash = _sha256(rescued_rig["canonical"])
    with pytest.raises(sg.GateRefused) as exc_info:
        sg.run_startup_gate(rescued_rig["canonical"])
    assert exc_info.value.exit_code == sg.EXIT_HARD_STOP_PARTIAL_SCHEMA
    assert _sha256(rescued_rig["canonical"]) == onceki_hash, "HARD_STOP sirasinda DB'ye DOKUNULDU"


def test_canonical_absent_legacy_exists_hard_stops(tmp_path):
    legacy = tmp_path / "resources" / "backend" / "gelka_enerji.db"
    legacy.parent.mkdir(parents=True)
    _touch_sqlite(str(legacy))
    canonical = str(tmp_path / "userData" / "database" / "gelka_enerji.db")
    with pytest.raises(sg.GateRefused) as exc_info:
        sg.run_startup_gate(canonical, legacy_hint_path=str(legacy))
    assert exc_info.value.exit_code == sg.EXIT_HARD_STOP_CANONICAL_ABSENT_LEGACY_EXISTS
    assert not os.path.exists(canonical), "HARD_STOP sirasinda canonical OLUSTURULDU"


def test_unknown_schema_hard_stops(tmp_path):
    canonical = tmp_path / "gelka_enerji.db"
    open(str(canonical), "wb").write(b"gecersiz")
    with pytest.raises(sg.GateRefused) as exc_info:
        sg.run_startup_gate(str(canonical))
    assert exc_info.value.exit_code == sg.EXIT_HARD_STOP_UNKNOWN_SCHEMA


def test_missing_rescue_journal_hard_stops_adoption(rescued_rig):
    """Journal dosyasi silinirse (ör. bozuk kurulum) adoption ARTIK
    GUVENLI ONKOSUL olmadan calisamaz -> HARD_STOP, canonical DEGISMEZ."""
    from app.legacy_adoption.rescue import _journal_path

    os.remove(_journal_path(rescued_rig["canonical"]))
    onceki_hash = _sha256(rescued_rig["canonical"])
    with pytest.raises(sg.GateRefused) as exc_info:
        sg.run_startup_gate(rescued_rig["canonical"])
    assert exc_info.value.exit_code == sg.EXIT_HARD_STOP_AUDIT_MISSING_OR_INVALID
    assert _sha256(rescued_rig["canonical"]) == onceki_hash


def test_tampered_rollback_backup_hard_stops_adoption(rescued_rig):
    journal = read_journal(rescued_rig["canonical"])
    with open(journal["backup_path"], "r+b") as fh:
        # SQLite HEADER'inin ICINE yaz (ilk 16 bayt hep "SQLite format 3\0"
        # sabit metnidir - kesinlikle sifir DEGILDIR, bu yuzden hash
        # GARANTILI degisir; offset 200 gibi rastgele bir nokta zaten
        # sifir-doldurulmus BOS sayfa alanina denk gelip NO-OP olabilir).
        fh.seek(0)
        fh.write(b"TAMPERED_BYTES!!")
    onceki_hash = _sha256(rescued_rig["canonical"])
    with pytest.raises(sg.GateRefused) as exc_info:
        sg.run_startup_gate(rescued_rig["canonical"])
    assert exc_info.value.exit_code == sg.EXIT_HARD_STOP_AUDIT_MISSING_OR_INVALID
    assert _sha256(rescued_rig["canonical"]) == onceki_hash


def test_canonical_mutated_after_rescue_hard_stops_adoption(rescued_rig):
    """rescue'dan SONRA, adoption'dan ONCE canonical'e (ör. baska bir
    surec) yazi olursa: adoption bunu TESPIT ETMELI, korumasiz devam
    ETMEMELI."""
    con = sqlite3.connect(rescued_rig["canonical"])
    con.execute("UPDATE incidents SET dedupe_key = dedupe_key || '_x' WHERE id = (SELECT MIN(id) FROM incidents)")
    con.commit()
    con.close()
    with pytest.raises(sg.GateRefused) as exc_info:
        sg.run_startup_gate(rescued_rig["canonical"])
    assert exc_info.value.exit_code == sg.EXIT_HARD_STOP_AUDIT_MISSING_OR_INVALID


# ── STEP 6: kesinti guvenligi (fault injection) ──────────────────────────
@pytest.mark.skipif(not ar.is_alembic_available(), reason="alembic calistirilabiliri yok")
@pytest.mark.parametrize("nokta", [
    "before_working_copy_creation", "during_working_copy_creation",
    "before_repair", "after_repair",
    "before_lineage_reconciliation", "after_lineage_reconciliation",
    "during_forward_migration",
    "before_atomic_publish", "after_atomic_publish",
    "before_audit_finalize", "after_audit_finalize",
])
def test_adoption_interruption_never_leaves_canonical_in_half_state(rescued_rig, nokta):
    onceki_hash = _sha256(rescued_rig["canonical"])
    onceki_rev = sqlite3.connect(rescued_rig["canonical"]).execute(
        "SELECT version_num FROM alembic_version"
    ).fetchall()

    with pytest.raises((sg.InjectedFault, sg.GateRefused)):
        sg.perform_controlled_adoption(rescued_rig["canonical"], fault_at=nokta)

    # after_atomic_publish'ten SONRAKI kesintiler canonical'in ZATEN
    # yayimlanmis olmasina izin verir (rename tamamlandi) - o noktadan
    # ONCEKI TUM kesintiler canonical'i GIRIS haliyle DEGISMEDEN birakmali.
    if nokta in ("before_working_copy_creation", "during_working_copy_creation",
                 "before_repair", "after_repair",
                 "before_lineage_reconciliation", "after_lineage_reconciliation",
                 "during_forward_migration", "before_atomic_publish"):
        assert _sha256(rescued_rig["canonical"]) == onceki_hash, (
            f"{nokta} kesintisi canonical'i DEGISTIRDI (yayimlamadan ONCE olmamali)"
        )
        rev = sqlite3.connect(rescued_rig["canonical"]).execute(
            "SELECT version_num FROM alembic_version"
        ).fetchall()
        assert rev == onceki_rev

    # HANGI noktada kesilirse kesilsin: canonical GECERLI bir SQLite dosyasi
    # olarak kalmali (ne yarim yazilmis, ne bozuk).
    assert sg._is_valid_sqlite(rescued_rig["canonical"])


@pytest.mark.skipif(not ar.is_alembic_available(), reason="alembic calistirilabiliri yok")
@pytest.mark.parametrize("nokta", [
    "before_working_copy_creation", "before_repair", "before_atomic_publish",
    "after_atomic_publish", "after_audit_finalize",
])
def test_rerun_after_adoption_interruption_completes_successfully(rescued_rig, nokta):
    try:
        sg.perform_controlled_adoption(rescued_rig["canonical"], fault_at=nokta)
    except (sg.InjectedFault, sg.GateRefused):
        pass

    rapor = sg.run_startup_gate(rescued_rig["canonical"])
    assert rapor.terminal_revision == CANONICAL_HEAD
    assert rapor.integrity_check == "ok"
    assert rapor.foreign_key_violations == 0


@pytest.mark.skipif(not ar.is_alembic_available(), reason="alembic calistirilabiliri yok")
@pytest.mark.parametrize("nokta", ["before_alembic_run", "after_alembic_run", "before_atomic_publish"])
def test_fresh_init_interruption_never_leaves_canonical_present(tmp_path, nokta):
    canonical = str(tmp_path / "userData" / "database" / "gelka_enerji.db")
    with pytest.raises((sg.InjectedFault, sg.GateRefused)):
        sg.fresh_initialize(canonical, fault_at=nokta)
    if nokta != "after_atomic_publish":
        assert not os.path.exists(canonical), (
            f"{nokta} kesintisi canonical'i OLUSTURDU (yayimlamadan ONCE olmamali)"
        )


@pytest.mark.skipif(not ar.is_alembic_available(), reason="alembic calistirilabiliri yok")
@pytest.mark.parametrize("nokta", ["before_alembic_run", "before_atomic_publish"])
def test_rerun_after_fresh_init_interruption_completes_successfully(tmp_path, nokta):
    canonical = str(tmp_path / "userData" / "database" / "gelka_enerji.db")
    try:
        sg.fresh_initialize(canonical, fault_at=nokta)
    except (sg.InjectedFault, sg.GateRefused):
        pass
    rapor = sg.run_startup_gate(canonical)
    assert rapor.terminal_revision == CANONICAL_HEAD


# ── STEP 4 kanit: frozen'da alembic.ini beklenen yolda YOKSA create_all'a
# DUSULMEZ (PDSMR-R3A STRATEJI 2: companion exe TERK EDILDI, in-process
# calisir - bkz. alembic_runner.py). sys.frozen'i GERCEKTEN True yapamayiz
# (pytest venv'de calisir), ama is_alembic_available()'in FROZEN dalini
# monkeypatch ile TETIKLEYIP HARD_STOP'un create_all'a asla dusmedigini
# kanitlariz - bu, GERCEK frozen davranisin (PDSMR-R3A kapanis raporunda
# GERCEK gelka-backend.exe ile AYRICA kanitlandi) BIRE BIR AYNI kod yolunu
# (startup_gate.py'nin EXIT_ALEMBIC_UNAVAILABLE dalini) tetikler.
def test_frozen_missing_alembic_ini_hard_stops_fresh_init_not_create_all(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys, "executable", str(tmp_path / "sahte_frozen_exe" / "gelka-backend.exe")
    )
    os.makedirs(os.path.dirname(sys.executable), exist_ok=True)
    # alembic.ini KASITLI olarak OLUSTURULMADI -> is_alembic_available() False donmeli.
    assert ar.is_alembic_available() is False

    canonical = str(tmp_path / "userData" / "database" / "gelka_enerji.db")
    with pytest.raises(sg.GateRefused) as exc_info:
        sg.run_startup_gate(canonical)
    assert exc_info.value.exit_code == sg.EXIT_ALEMBIC_UNAVAILABLE
    assert not os.path.exists(canonical), (
        "alembic.ini yokken canonical OLUSTURULDU - create_all'a sessizce "
        "dusulmus OLABILIR"
    )


def test_unknown_fault_point_is_silently_ignored_not_a_security_hole(rescued_rig):
    """Bilinmeyen bir fault_at degeri sessizce YOK SAYILMALI (herhangi bir
    noktada eslesmedigi icin) - GateRefused/InjectedFault FIRLATMAMALI."""
    rapor = sg.perform_controlled_adoption(rescued_rig["canonical"], fault_at="var_olmayan_nokta")
    assert rapor.terminal_revision == CANONICAL_HEAD


# ── STEP 10 oncesi: process/kaynak temizligi bu testler icin gerekmez -
# (subprocess alt-surecler alembic_runner.run_alembic icinde kisa omurlu,
# hicbiri arka planda kalmaz - dogrudan pytest sureci icinde sqlite3
# baglantilari ile calisir).
