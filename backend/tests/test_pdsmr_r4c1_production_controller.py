"""
PDSMR-R4 / FAZ 4C1 — PRODUCTION ADOPTION CONTROLLER testleri.

Kanitlanacak: controller, 4B2 motorunun etrafina fail-closed bir kapi/yetki/
yedek/geri-alma sozlesmesi kurar; gercek production DB'ye DOKUNMADAN exact
production kopyasinda cutover ve rollback provasini gecer; her kesintide
SOURCE/ROLLBACK degismez, erken basari/audit olusmaz ve hedef ya hic
dokunulmamis ya da DOGRULANMIS sekilde geri alinmis olur.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.legacy_adoption import alembic_runner as ar  # noqa: E402
from app.legacy_adoption.lineage import CANONICAL_HEAD  # noqa: E402
from app.legacy_adoption.unversioned_adoption import InjectedFault  # noqa: E402
from app.legacy_adoption import production_adoption_controller as C  # noqa: E402

LIVE_DB = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Programs", "Gelka", "resources", "backend",
    "gelka_enerji.db")
LIVE_SHA256 = "f9a3fb04a96bd167671e6d7dfa6fa77424dd27a448dba2b0cf244a4ef7653219"
LIVE_SIZE = 1556480
REPO_SHA = "31cecc7feca383b65a66be9e116cdc7d13ec63c3"
V106_EXE = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Programs", "Gelka", "resources", "backend",
    "gelka-backend.exe")

pytestmark = pytest.mark.skipif(
    not ar.is_alembic_available(), reason="alembic calistirilabiliri yok")


def _revisions(p: str) -> tuple[str, ...]:
    con = sqlite3.connect("file:" + p.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        return tuple(sorted(r[0] for r in con.execute("SELECT version_num FROM alembic_version")))
    except sqlite3.OperationalError:
        return ()
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────────────────
# Fixture'lar — hepsi DISPOSABLE
# ─────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def live_source(tmp_path_factory) -> str:
    if not os.path.isfile(LIVE_DB):
        pytest.skip("canli production DB bu makinede yok")
    hedef = str(tmp_path_factory.mktemp("r4c1_src") / "LIVE.db")
    shutil.copyfile(LIVE_DB, hedef)
    if C.sha256_of(hedef) != LIVE_SHA256:
        pytest.skip("canli DB parmak izi bu testin kalibre edildigi surumden farkli")
    return hedef


@pytest.fixture(scope="session")
def scratch_refs(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("r4c1_refs"))


@pytest.fixture()
def arena(live_source, tmp_path) -> dict:
    """SOURCE / ROLLBACK / WORKING / PUBLISH_TARGET / RESTORE_TARGET ucgeni."""
    y = {ad: str(tmp_path / (ad + ".db"))
         for ad in ("SOURCE", "ROLLBACK", "WORKING", "PUBLISH_TARGET", "RESTORE_TARGET")}
    for ad in y:
        shutil.copyfile(live_source, y[ad])
    y["recovery"] = str(tmp_path / "recovery")
    y["ledger"] = str(tmp_path / "ledger")
    y["scratch"] = str(tmp_path / "refs")
    return y


def _cutover(arena: dict, target_key: str = "PUBLISH_TARGET", **kw):
    return C.run_cutover(
        arena[target_key],
        source_path=arena["SOURCE"], rollback_path=arena["ROLLBACK"],
        working_path=arena["WORKING"], recovery_dir=arena["recovery"],
        scratch_dir=arena["scratch"], ledger_dir=arena["ledger"],
        expected_sha256=LIVE_SHA256, expected_size=LIVE_SIZE,
        repository_sha=REPO_SHA, version_label="test",
        confirm_disposable_rehearsal=True, **kw)


def _intact(arena: dict) -> None:
    assert C.sha256_of(arena["SOURCE"]) == LIVE_SHA256, "SOURCE DEGISTI"
    assert C.sha256_of(arena["ROLLBACK"]) == LIVE_SHA256, "ROLLBACK DEGISTI"


def _production_untouched() -> None:
    assert C.sha256_of(LIVE_DB) == LIVE_SHA256, "PRODUCTION DB DEGISTI — kritik ihlal"
    for ek in ("-wal", "-shm", "-journal"):
        assert not os.path.exists(LIVE_DB + ek), "production sidecar olustu: " + ek


# ─────────────────────────────────────────────────────────────────────────
# POZITIF — exact production-copy cutover
# ─────────────────────────────────────────────────────────────────────────
def test_cutover_rehearsal_reaches_canonical_head(arena, scratch_refs):
    r = _cutover(arena)
    assert r.outcome == "REHEARSED"
    assert r.mode == "REHEARSAL"
    assert r.recovery_state == C.RECOVERY_UNTOUCHED
    assert r.terminal_revision == CANONICAL_HEAD
    assert r.integrity_check == "ok"
    assert r.foreign_key_violations == 0
    assert _revisions(arena["PUBLISH_TARGET"]) == (CANONICAL_HEAD,)
    _intact(arena)
    _production_untouched()


def test_all_preflight_gates_are_recorded(arena):
    r = _cutover(arena)
    beklenen = {
        "process_absence", "exact_realpath", "fingerprint", "timestamp_units",
        "sidecar_absence", "physical_distinctness", "same_volume_atomic",
        "disk_space", "verified_immutable_backup", "fresh_delta_gate",
        "engine_certification", "atomic_publish", "post_publish_certification",
    }
    assert beklenen <= set(r.gates_passed), sorted(beklenen - set(r.gates_passed))


def test_row_preservation_and_new_tables_start_empty(arena):
    r = _cutover(arena)
    for t, n in r.row_counts_before.items():
        assert r.row_counts_after[t] == n, t + " satir korunumu ihlali"
    yeni = set(r.row_counts_after) - set(r.row_counts_before)
    assert yeni
    for t in yeni - {"alembic_version"}:
        assert r.row_counts_after[t] == 0, t + " bos baslamadi"


def test_backup_is_byte_identical_and_verified(arena):
    r = _cutover(arena)
    assert os.path.isfile(r.backup_path)
    assert r.backup_sha256 == LIVE_SHA256
    assert C.sha256_of(r.backup_path) == LIVE_SHA256
    butunluk, fk = C.health(r.backup_path)
    assert butunluk == "ok" and fk == 0


def test_backup_is_not_mutated_by_the_cutover(arena):
    r = _cutover(arena)
    assert C.sha256_of(r.backup_path) == LIVE_SHA256, "yedek MUTATE edildi"


def test_updated_by_nulls_are_preserved(arena):
    con = sqlite3.connect(arena["SOURCE"])
    try:
        once = con.execute(
            "SELECT COUNT(*) FROM market_reference_prices WHERE updated_by IS NULL").fetchone()[0]
    finally:
        con.close()
    r = _cutover(arena)
    con = sqlite3.connect(arena["PUBLISH_TARGET"])
    try:
        sonra = con.execute(
            "SELECT COUNT(*) FROM market_reference_prices WHERE updated_by IS NULL").fetchone()[0]
        uydurma = con.execute("SELECT COUNT(*) FROM market_reference_prices "
                              "WHERE updated_by='system_migration'").fetchone()[0]
    finally:
        con.close()
    assert sonra == once and uydurma == 0
    assert "ACCEPTED_LEGACY_DATA_VARIANT_UPDATED_BY_NULL" in r.accepted_data_variants


def test_audit_written_only_after_terminal_success_and_has_no_pii(arena):
    r = _cutover(arena)
    audit = C.read_audit(arena["PUBLISH_TARGET"])
    assert audit is not None
    assert audit["terminal_revision"] == CANONICAL_HEAD
    blob = json.dumps(audit, ensure_ascii=False).lower()
    for yasak in ("password", "secret", "api_key", "token", "@"):
        assert yasak not in blob, "audit'te yasakli ifade: " + yasak
    assert r.outcome == "REHEARSED"


def test_timestamp_fields_carry_explicit_units(arena):
    """
    Onceki fazda `stat -c %Y` (SANIYE) yanlislikla "mtime_ns" etiketlenmisti.
    Burada iki alan da AYRI ve birimiyle kaydedilir; ISO-8601 karsiligi da
    verilir — birim belirsizligi yapisal olarak olusamaz.
    """
    z = C.timestamp_fields(arena["SOURCE"])
    assert set(z) == {"st_mtime_seconds", "st_mtime_ns", "mtime_utc_iso8601", "size_bytes"}
    assert int(z["st_mtime_seconds"]) != z["st_mtime_ns"], "iki alan AYNI olamaz"
    assert z["st_mtime_ns"] > int(z["st_mtime_seconds"]) * 10**8
    assert z["mtime_utc_iso8601"].endswith("+00:00")


# ─────────────────────────────────────────────────────────────────────────
# AUTHORIZATION — tek kullanimlik, dort baglama bagli
# ─────────────────────────────────────────────────────────────────────────
def _manifest(arena, target_key="PUBLISH_TARGET", nonce="n" * 32, repo=REPO_SHA):
    return C.issue_authorization(
        arena[target_key], repository_sha=repo, nonce=nonce,
        issued_at_utc="2026-08-19T00:00:00+00:00",
        confirm_production_authorization=True)


def test_authorization_requires_explicit_confirmation(arena):
    with pytest.raises(C.ControllerRefused, match="confirm_production_authorization"):
        C.issue_authorization(arena["PUBLISH_TARGET"], repository_sha=REPO_SHA,
                              nonce="n" * 32, issued_at_utc="x")


def test_authorization_rejects_short_nonce(arena):
    with pytest.raises(C.ControllerRefused, match="nonce"):
        C.issue_authorization(arena["PUBLISH_TARGET"], repository_sha=REPO_SHA, nonce="kisa",
                              issued_at_utc="x", confirm_production_authorization=True)


def test_authorization_bound_to_wrong_repository_sha_is_refused(arena):
    m = _manifest(arena, repo="0" * 40)
    with pytest.raises(C.ControllerRefused, match="repository SHA"):
        C.validate_authorization(m, arena["PUBLISH_TARGET"], repository_sha=REPO_SHA,
                                 ledger_dir=arena["ledger"])


def test_authorization_bound_to_another_target_is_refused(arena):
    m = _manifest(arena, target_key="RESTORE_TARGET")
    with pytest.raises(C.ControllerRefused, match="BASKA hedefe"):
        C.validate_authorization(m, arena["PUBLISH_TARGET"], repository_sha=REPO_SHA,
                                 ledger_dir=arena["ledger"])


def test_stale_authorization_after_target_change_is_refused(arena):
    m = _manifest(arena)
    con = sqlite3.connect(arena["PUBLISH_TARGET"])
    con.execute("UPDATE customers SET notes = COALESCE(notes,'') || 'x'")
    con.commit()
    con.close()
    with pytest.raises(C.ControllerRefused, match="BAYAT"):
        C.validate_authorization(m, arena["PUBLISH_TARGET"], repository_sha=REPO_SHA,
                                 ledger_dir=arena["ledger"])


def test_replayed_nonce_is_refused(arena):
    m = _manifest(arena)
    C._consume_nonce(arena["ledger"], m.nonce)
    with pytest.raises(C.ControllerRefused, match="TUKETILMIS"):
        C.validate_authorization(m, arena["PUBLISH_TARGET"], repository_sha=REPO_SHA,
                                 ledger_dir=arena["ledger"])


def test_corrupt_nonce_ledger_is_fail_closed(arena):
    os.makedirs(arena["ledger"], exist_ok=True)
    with open(C._ledger_path(arena["ledger"]), "w", encoding="utf-8") as fh:
        fh.write("{bozuk json")
    with pytest.raises(C.ControllerRefused, match="defteri"):
        C._consumed_nonces(arena["ledger"])


def test_forbidden_production_path_without_authorization_is_refused(arena, tmp_path):
    """GENEL BYPASS YOK: kurulu uygulama alanina manifest'siz yazilamaz."""
    sahte = tmp_path / "AppData" / "Local" / "Programs" / "Gelka" / "resources" / "backend"
    sahte.mkdir(parents=True)
    hedef = str(sahte / "gelka_enerji.db")
    shutil.copyfile(arena["SOURCE"], hedef)
    with pytest.raises(C.ControllerRefused, match="YETKI YOK"):
        C.run_cutover(
            hedef, source_path=arena["SOURCE"], rollback_path=arena["ROLLBACK"],
            working_path=arena["WORKING"], recovery_dir=arena["recovery"],
            scratch_dir=arena["scratch"], ledger_dir=arena["ledger"],
            expected_sha256=LIVE_SHA256, expected_size=LIVE_SIZE,
            repository_sha=REPO_SHA, confirm_disposable_rehearsal=True)
    _intact(arena)
    _production_untouched()


# ─────────────────────────────────────────────────────────────────────────
# NEGATIF — on-kosul kapilari
# ─────────────────────────────────────────────────────────────────────────
def test_missing_disposable_confirmation_is_refused(arena):
    with pytest.raises(C.ControllerRefused, match="confirm_disposable_rehearsal"):
        C.run_cutover(
            arena["PUBLISH_TARGET"], source_path=arena["SOURCE"],
            rollback_path=arena["ROLLBACK"], working_path=arena["WORKING"],
            recovery_dir=arena["recovery"], scratch_dir=arena["scratch"],
            ledger_dir=arena["ledger"], expected_sha256=LIVE_SHA256,
            expected_size=LIVE_SIZE, repository_sha=REPO_SHA)


def test_fingerprint_drift_is_refused(arena):
    con = sqlite3.connect(arena["PUBLISH_TARGET"])
    con.execute("UPDATE customers SET notes = COALESCE(notes,'') || 'z'")
    con.commit()
    con.close()
    with pytest.raises(C.ControllerRefused, match="SHA-256 sapmasi|boyut sapmasi"):
        _cutover(arena)
    _intact(arena)


def test_size_drift_is_refused(arena):
    with pytest.raises(C.ControllerRefused, match="boyut sapmasi"):
        C.verify_production_identity(arena["SOURCE"], expected_sha256=LIVE_SHA256,
                                     expected_size=LIVE_SIZE + 1)


def test_sidecar_presence_is_refused(arena):
    with open(arena["PUBLISH_TARGET"] + "-wal", "w") as fh:
        fh.write("x")
    try:
        with pytest.raises(C.ControllerRefused, match="sidecar"):
            _cutover(arena)
    finally:
        os.remove(arena["PUBLISH_TARGET"] + "-wal")
    _intact(arena)


def test_missing_target_is_refused(arena):
    os.remove(arena["PUBLISH_TARGET"])
    with pytest.raises(C.ControllerRefused, match="hedef dosya yok"):
        _cutover(arena)


def test_same_file_paths_are_refused(arena):
    with pytest.raises(C.ControllerRefused, match="AYNI fiziksel dosya"):
        C.assert_physically_distinct(a=arena["SOURCE"], b=arena["SOURCE"])


def test_cross_volume_atomic_replace_is_refused():
    with pytest.raises(C.ControllerRefused, match="farkli volume"):
        C.assert_same_volume_and_atomic_replace_possible("C:\\x\\a.db", "D:\\y\\b.db")


def test_insufficient_disk_space_is_refused(arena):
    with pytest.raises(C.ControllerRefused, match="yetersiz disk"):
        C.assert_sufficient_disk_space(arena["recovery"] if os.path.isdir(arena["recovery"])
                                       else os.path.dirname(arena["SOURCE"]),
                                       10 ** 15, kat=1)


def test_open_gelka_process_blocks_backup(arena, monkeypatch):
    monkeypatch.setattr(C, "running_gelka_processes",
                        lambda: [{"pid": "1234", "path": "fake"}])
    with pytest.raises(C.ControllerRefused, match="surecleri ACIK|surecleri acik"):
        _cutover(arena)
    _intact(arena)


def test_process_check_never_uses_name_based_kill():
    """
    Isim bazli toplu kapatma KODDA bulunmamali (onceki olay dersi).

    Arama KOD duzeyinde yapilir: modul docstring'i bu ifadelerin NEDEN
    yasak oldugunu ANLATIR; naive substring taramasi o ACIKLAMAYA takilir
    ve gercek bir ihlali gizleyecek kadar gurultulu olur.
    """
    import ast

    yol = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "app", "legacy_adoption", "production_adoption_controller.py")
    with open(yol, encoding="utf-8") as fh:
        agac = ast.parse(fh.read())
    docs = set()
    for n in ast.walk(agac):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(n, clean=False)
            if d:
                docs.add(d)
    ihlaller = []
    for n in ast.walk(agac):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in docs:
            duz = n.value.lower()
            for yasak in ("taskkill", "stop-process -name", "-processname", "killall"):
                if yasak in duz:
                    ihlaller.append(yasak)
    assert ihlaller == [], "isim bazli kill izi (kod sabiti): " + str(ihlaller)


# ─────────────────────────────────────────────────────────────────────────
# ROLLBACK — publish sonrasi sertifikasyon basarisiz
# ─────────────────────────────────────────────────────────────────────────
def test_post_publish_failure_triggers_verified_atomic_restore(arena, monkeypatch):
    monkeypatch.setattr(C, "certify_canonical_equivalence",
                        lambda *a, **k: ["ENJEKTE EDILMIS SERTIFIKASYON HATASI"])
    with pytest.raises(C.ControllerRefused) as exc:
        _cutover(arena, target_key="RESTORE_TARGET")
    monkeypatch.undo()

    assert "recovery_state=" in str(exc.value)
    assert C.RECOVERY_RESTORED in str(exc.value)
    assert C.sha256_of(arena["RESTORE_TARGET"]) == LIVE_SHA256, "eski fingerprint'e DONMEDI"
    butunluk, fk = C.health(arena["RESTORE_TARGET"])
    assert butunluk == "ok" and fk == 0
    assert C.read_audit(arena["RESTORE_TARGET"]) is None, "basarisizlikta audit YAZILMAMALI"
    _intact(arena)
    _production_untouched()


def test_row_preservation_failure_also_triggers_restore(arena, monkeypatch):
    gercek = C.row_manifest
    cagri = {"n": 0}

    def sahte(p):
        cagri["n"] += 1
        m = gercek(p)
        if cagri["n"] > 1:  # publish SONRASI olcumu boz
            m["customers"] = m.get("customers", 0) + 99
        return m

    monkeypatch.setattr(C, "row_manifest", sahte)
    with pytest.raises(C.ControllerRefused, match="satir korunumu|sertifikasyon"):
        _cutover(arena, target_key="RESTORE_TARGET")
    monkeypatch.undo()
    assert C.sha256_of(arena["RESTORE_TARGET"]) == LIVE_SHA256
    assert C.read_audit(arena["RESTORE_TARGET"]) is None


# ─────────────────────────────────────────────────────────────────────────
# CONTROLLER FAULT MATRISI
# ─────────────────────────────────────────────────────────────────────────
def test_controller_fault_points_cover_mandatory_set():
    zorunlu = {
        "before_identity_binding", "after_identity_binding",
        "before_backup_copy", "after_backup_copy",
        "before_backup_verification", "after_backup_verification",
        "before_working_adoption", "after_working_adoption",
        "before_atomic_publish", "after_atomic_publish",
        "during_post_publish_certification",
        "before_rollback_replace", "after_rollback_replace",
        "during_rollback_certification",
        "before_audit_commit", "after_audit_commit",
        "before_authorization_consume", "after_authorization_consume",
        "lost_response_retry",
    }
    assert zorunlu <= set(C.CONTROLLER_FAULT_POINTS), sorted(
        zorunlu - set(C.CONTROLLER_FAULT_POINTS))


# Kesinti noktalari IKI SINIFA ayrilir:
#  - MUTLU YOL: her basarili cutover'da MUTLAKA gecilen noktalar.
#  - KOSULLU YOL: yalniz belirli bir senaryoda gecilen noktalar
#    (rollback -> publish sonrasi sertifikasyon BASARISIZ olunca;
#     authorization -> yalniz PRODUCTION modunda).
# Kosullu noktalar mutlu yolda TETIKLENMEZ; onlari mutlu yolda test etmek
# yanlis bir beklentidir — asagida KENDI senaryolarinda kosulurlar.
_ROLLBACK_FAULTS = (
    "before_rollback_replace", "after_rollback_replace", "during_rollback_certification",
)
_AUTHORIZATION_FAULTS = (
    "before_authorization_consume", "after_authorization_consume",
)
_CONDITIONAL_FAULTS = _ROLLBACK_FAULTS + _AUTHORIZATION_FAULTS
_HAPPY_PATH_FAULTS = tuple(
    p for p in C.CONTROLLER_FAULT_POINTS if p not in _CONDITIONAL_FAULTS)


def test_fault_point_classification_is_exhaustive():
    """Her nokta TAM OLARAK bir sinifta olmali — kapsam disi nokta kalmamali."""
    assert set(_HAPPY_PATH_FAULTS) | set(_CONDITIONAL_FAULTS) == set(C.CONTROLLER_FAULT_POINTS)
    assert not (set(_HAPPY_PATH_FAULTS) & set(_CONDITIONAL_FAULTS))
    assert len(C.CONTROLLER_FAULT_POINTS) == 19


@pytest.mark.parametrize("nokta", _HAPPY_PATH_FAULTS)
def test_controller_fault_is_fail_closed(arena, nokta):
    """
    Mutlu yoldaki her kesintide: SOURCE/ROLLBACK degismez, production
    degismez, erken basari/audit olusmaz.
    """
    try:
        r = _cutover(arena, fault_at=nokta)
    except (InjectedFault, C.ControllerRefused):
        r = None
    _intact(arena)
    _production_untouched()

    if r is not None:
        assert nokta in ("after_audit_commit", "lost_response_retry"), (
            nokta + ": kesintiye ragmen akis tamamlandi")
        return
    if nokta != "after_audit_commit":
        assert C.read_audit(arena["PUBLISH_TARGET"]) is None, (
            nokta + ": basari olmadan audit yazildi")


@pytest.mark.parametrize("nokta", _ROLLBACK_FAULTS)
def test_rollback_path_fault_is_fail_closed(arena, monkeypatch, nokta):
    """
    Rollback noktalari GERCEK yollarinda kosulur: publish sonrasi
    sertifikasyon BASARISIZ edilir, sonra kesinti enjekte edilir.
    Sonuc: basari iddiasi YOK, audit YOK, SOURCE/ROLLBACK/production
    degismemis.
    """
    monkeypatch.setattr(C, "certify_canonical_equivalence",
                        lambda *a, **k: ["ENJEKTE EDILMIS SERTIFIKASYON HATASI"])
    with pytest.raises((InjectedFault, C.ControllerRefused)):
        _cutover(arena, target_key="RESTORE_TARGET", fault_at=nokta)
    monkeypatch.undo()
    _intact(arena)
    _production_untouched()
    assert C.read_audit(arena["RESTORE_TARGET"]) is None, (
        nokta + ": basarisiz akista audit yazildi")


@pytest.mark.parametrize("nokta", _AUTHORIZATION_FAULTS)
def test_authorization_path_fault_is_fail_closed(arena, nokta):
    """
    Authorization noktalari GERCEK yollarinda kosulur: DISPOSABLE hedefe
    baglanmis gecerli bir manifest ile PRODUCTION modu calistirilir.

    `before_authorization_consume` -> nonce TUKETILMEMIS olmali.
    `after_authorization_consume`  -> nonce tuketilmis ama audit YOK.
    """
    m = _manifest(arena, nonce="a" * 40)
    with pytest.raises(InjectedFault):
        C.run_cutover(
            arena["PUBLISH_TARGET"], source_path=arena["SOURCE"],
            rollback_path=arena["ROLLBACK"], working_path=arena["WORKING"],
            recovery_dir=arena["recovery"], scratch_dir=arena["scratch"],
            ledger_dir=arena["ledger"], expected_sha256=LIVE_SHA256,
            expected_size=LIVE_SIZE, repository_sha=REPO_SHA,
            authorization=m, version_label="test", fault_at=nokta)
    _intact(arena)
    _production_untouched()
    assert C.read_audit(arena["PUBLISH_TARGET"]) is None, (
        nokta + ": basari olmadan audit yazildi")

    tuketilmis = C._consumed_nonces(arena["ledger"])
    if nokta == "before_authorization_consume":
        assert m.nonce not in tuketilmis, "kesinti ONCESINDE nonce tuketilmis"
    else:
        assert m.nonce in tuketilmis, "kesinti SONRASINDA nonce tuketilmemis"


def test_production_mode_with_valid_authorization_completes_on_disposable_target(arena):
    """
    PRODUCTION modu (manifest ile) DISPOSABLE hedefte uctan uca calisir ve
    nonce TEK KULLANIMLIK olarak tuketilir.

    NOT: Faz 4C1'de gercek production hedefi icin manifest URETILMEZ; bu
    test yalniz mod mekanigini disposable kopyada dogrular.
    """
    m = _manifest(arena, nonce="b" * 40)
    r = C.run_cutover(
        arena["PUBLISH_TARGET"], source_path=arena["SOURCE"],
        rollback_path=arena["ROLLBACK"], working_path=arena["WORKING"],
        recovery_dir=arena["recovery"], scratch_dir=arena["scratch"],
        ledger_dir=arena["ledger"], expected_sha256=LIVE_SHA256,
        expected_size=LIVE_SIZE, repository_sha=REPO_SHA,
        authorization=m, version_label="test")
    assert r.mode == "PRODUCTION"
    assert r.outcome == "ADOPTED"
    assert r.authorization_nonce == m.nonce
    assert "authorization_binding" in r.gates_passed
    assert "authorization_consumed" in r.gates_passed
    assert m.nonce in C._consumed_nonces(arena["ledger"])
    _intact(arena)
    _production_untouched()


def test_unknown_controller_fault_point_is_refused(arena):
    with pytest.raises(C.ControllerRefused, match="fault noktasi"):
        _cutover(arena, fault_at="olmayan_nokta")


def test_lost_response_retry_is_deterministic(arena):
    """
    Cevabi kaybolmus bir cagri tekrar edilirse: ya guvenle tamamlar ya
    deterministik durur — belirsiz durum BIRAKMAZ.
    """
    r1 = _cutover(arena)
    assert r1.outcome == "REHEARSED"
    once = C.sha256_of(arena["PUBLISH_TARGET"])
    shutil.copyfile(arena["SOURCE"], arena["WORKING"])
    with pytest.raises(C.ControllerRefused):
        _cutover(arena)  # hedef artik canonical: fingerprint kapisi reddeder
    assert C.sha256_of(arena["PUBLISH_TARGET"]) == once, "tekrar cagri hedefi degistirdi"
    _intact(arena)


# ─────────────────────────────────────────────────────────────────────────
# KURULU v1.0.6 POST-ADOPTION UYUMLULUK KAPISI
# ─────────────────────────────────────────────────────────────────────────
def _kill_tree(pid: int) -> None:
    """EXACT PID agacini kapatir — ISIM BAZLI kill YAPILMAZ."""
    ps = (
        "$ids=@(%d); $kids=Get-CimInstance Win32_Process | "
        "Where-Object { $_.ParentProcessId -eq %d } | ForEach-Object { $_.ProcessId }; "
        "($ids + $kids) | ForEach-Object { try { Stop-Process -Id $_ -Force -EA Stop } catch {} }"
    ) % (pid, pid)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, timeout=60)


@pytest.mark.skipif(not os.path.isfile(V106_EXE), reason="kurulu v1.0.6 binary yok")
def test_installed_v106_runs_on_canonical_schema_without_damaging_it(arena, tmp_path):
    """
    EXACT kurulu v1.0.6 binary, DEGISTIRILMEDEN, yalniz DATABASE_URL ile
    DISPOSABLE canonical kopyaya yonlendirilir. Production DB'ye DOKUNULMAZ.

    Kanitlanacak: uygulama acilir, mevcut 2 musteri / 2 teklif aggregate
    duzeyde gorunur ve runtime semayi GERIYE CEVIRMEZ / create_all ile
    BOZMAZ.
    """
    r = _cutover(arena)
    assert r.outcome == "REHEARSED"
    db = str(tmp_path / "v106_disposable.db")
    shutil.copyfile(arena["PUBLISH_TARGET"], db)

    def sema(p):
        con = sqlite3.connect("file:" + p.replace("\\", "/") + "?mode=ro", uri=True)
        try:
            return sorted((x[0], x[1], " ".join((x[2] or "").split())) for x in con.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
        finally:
            con.close()

    once_sema, once_sha, once_rev = sema(db), C.sha256_of(db), _revisions(db)
    once_satir = C.row_manifest(db)

    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + db.replace("\\", "/")
    env["ENV"] = "development"
    # Saglayici/ag izolasyonu — outreach/SMTP/OpenAI/webhook yuzeyi kapatilir.
    for k in list(env):
        if any(x in k.upper() for x in ("SMTP", "OPENAI", "OUTREACH", "WEBHOOK")):
            env.pop(k, None)
    env["OUTREACH_ENABLED"] = "false"

    port = 8137
    log = str(tmp_path / "v106.log")
    with open(log, "w", encoding="utf-8") as lf:
        p = subprocess.Popen([V106_EXE, "--host", "127.0.0.1", "--port", str(port)],
                             cwd=os.path.dirname(V106_EXE), env=env,
                             stdout=lf, stderr=subprocess.STDOUT, text=True)
    try:
        hazir = False
        for _ in range(150):
            if p.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/health" % port, timeout=2) as resp:
                    if resp.status == 200:
                        hazir = True
                        break
            except Exception:
                time.sleep(1)
        assert hazir, "kurulu v1.0.6 canonical semada ACILMADI — log: " + open(
            log, encoding="utf-8", errors="replace").read()[-800:]

        for yol, beklenen in (("/customers", 2), ("/offers", 2)):
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d%s" % (port, yol), timeout=15) as resp:
                veri = json.loads(resp.read().decode())
            assert resp.status == 200
            n = len(veri) if isinstance(veri, list) else len(veri.get("items", []))
            assert n == beklenen, yol + " aggregate=" + str(n)
    finally:
        p.terminate()
        try:
            p.wait(timeout=25)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=15)
        _kill_tree(p.pid)

    assert sema(db) == once_sema, "runtime semayi DEGISTIRDI (create_all hasari?)"
    assert _revisions(db) == once_rev == (CANONICAL_HEAD,), "terminal revizyon degisti"
    assert C.row_manifest(db) == once_satir, "satir sayilari degisti"
    assert C.sha256_of(db) == once_sha, "runtime DB'ye yazdi"
    _production_untouched()


# ─────────────────────────────────────────────────────────────────────────
# WIRING / STATIK
# ─────────────────────────────────────────────────────────────────────────
def test_controller_is_not_wired_into_any_runtime_path():
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    izinli = os.path.join(backend, "app", "legacy_adoption")
    ihlaller = []
    for kok, _d, dosyalar in os.walk(os.path.join(backend, "app")):
        if kok.startswith(izinli):
            continue
        for d in dosyalar:
            if d.endswith(".py"):
                with open(os.path.join(kok, d), encoding="utf-8", errors="replace") as fh:
                    if "production_adoption_controller" in fh.read():
                        ihlaller.append(os.path.relpath(os.path.join(kok, d), backend))
    assert ihlaller == [], "controller uygulama koduna baglanmis: " + str(ihlaller)


def test_controller_uses_no_forbidden_sql_or_migration_shortcut():
    import ast

    yol = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "app", "legacy_adoption", "production_adoption_controller.py")
    with open(yol, encoding="utf-8") as fh:
        agac = ast.parse(fh.read())
    yasak = {"create_all", "stamp", "alembic_upgrade"}
    ihlal = [n.attr for n in ast.walk(agac)
             if isinstance(n, ast.Attribute) and n.attr in yasak]
    ihlal += [n.id for n in ast.walk(agac) if isinstance(n, ast.Name) and n.id in yasak]
    assert ihlal == [], "yasak kod yapisi: " + str(ihlal)

    docs = set()
    for n in ast.walk(agac):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(n, clean=False)
            if d:
                docs.add(d)
    for n in ast.walk(agac):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in docs:
            assert "SELECT *" not in " ".join(n.value.split()).upper()
