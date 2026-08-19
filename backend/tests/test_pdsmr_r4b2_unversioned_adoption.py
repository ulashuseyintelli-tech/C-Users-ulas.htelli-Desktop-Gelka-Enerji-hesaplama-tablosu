"""
PDSMR-R4 / FAZ 4B2 — UNVERSIONED CONTROLLED ADOPTION testleri.

Kanitlanacak tek cumle: gercek canli unversioned DB'nin DISPOSABLE
byte-kopyasi, production'a ve kurulu uygulamaya DOKUNMADAN canonical head
`351d314819d5` sekline getirilebilir; her belirsizlikte fail-closed durur;
SOURCE ve ROLLBACK hicbir kosulda degismez.

Fault-injection kritik: yarida kalan bir adoption HICBIR ZAMAN ADOPTED
raporlamamali ve terminal revizyon esdegerlikten ONCE gorunmemelidir.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.legacy_adoption import alembic_runner as ar  # noqa: E402
from app.legacy_adoption.lineage import CANONICAL_HEAD  # noqa: E402
from app.legacy_adoption.unversioned_adoption import (  # noqa: E402
    ACCEPTED_DATA_VARIANT,
    FAULT_POINTS,
    REV_012,
    REV_013,
    AdoptionRefused,
    InjectedFault,
    adopt_unversioned_copy,
    assert_disposable_target,
    build_canonical_reference,
    certify_canonical_equivalence,
    check_constraints,
    is_certifiably_adopted,
    plan_rebuild_tables,
    read_audit,
    rebuild_fault_points,
)

# Gercek canli production DB — YALNIZ OKUNUR ve YALNIZ byte-kopya kaynagi
# olarak kullanilir. Testler ona ASLA baglanmaz/yazmaz.
LIVE_DB = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Programs", "Gelka", "resources", "backend",
    "gelka_enerji.db",
)
LIVE_SHA256 = "f9a3fb04a96bd167671e6d7dfa6fa77424dd27a448dba2b0cf244a4ef7653219"

pytestmark = pytest.mark.skipif(
    not ar.is_alembic_available(), reason="alembic calistirilabiliri yok"
)


def _sha(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for parca in iter(lambda: fh.read(1 << 20), b""):
            h.update(parca)
    return h.hexdigest()


def _tables(p: str) -> set[str]:
    con = sqlite3.connect("file:" + p.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        con.close()


def _revisions(p: str) -> tuple[str, ...]:
    con = sqlite3.connect("file:" + p.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        return tuple(sorted(r[0] for r in con.execute("SELECT version_num FROM alembic_version")))
    except sqlite3.OperationalError:
        return ()
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────────────────
# Fixture'lar — hepsi DISPOSABLE, hicbiri production'a dokunmaz
# ─────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def live_source(tmp_path_factory) -> str:
    """
    Gercek canli DB'nin DISPOSABLE byte-kopyasi.

    Canli DB yoksa (ör. CI) test ATLANIR — sentetik bir taklitle
    degistirilmez: bu paketin degeri GERCEK semayla calismasindadir.
    """
    if not os.path.isfile(LIVE_DB):
        pytest.skip("canli production DB bu makinede yok")
    hedef = str(tmp_path_factory.mktemp("r4b2_src") / "SOURCE.db")
    shutil.copyfile(LIVE_DB, hedef)
    if _sha(hedef) != LIVE_SHA256:
        pytest.skip("canli DB parmak izi bu testin kalibre edildigi surumden farkli")
    return hedef


@pytest.fixture(scope="session")
def scratch_refs(tmp_path_factory) -> str:
    """Canonical referanslarin uretildigi paylasilan gecici dizin."""
    return str(tmp_path_factory.mktemp("r4b2_refs"))


@pytest.fixture(scope="session")
def ref_head(scratch_refs) -> str:
    return build_canonical_reference(scratch_refs, CANONICAL_HEAD)


@pytest.fixture()
def arena(live_source, tmp_path) -> dict:
    """Her test icin taze SOURCE/ROLLBACK/WORKING/CANONICAL ucgeni."""
    yollar = {ad: str(tmp_path / (ad + ".db"))
              for ad in ("SOURCE", "ROLLBACK", "WORKING", "CANONICAL")}
    for ad in ("SOURCE", "ROLLBACK", "WORKING"):
        shutil.copyfile(live_source, yollar[ad])
    yollar["scratch"] = str(tmp_path / "refs")
    return yollar


def _adopt(arena: dict, **kw):
    return adopt_unversioned_copy(
        arena["WORKING"], source_path=arena["SOURCE"], rollback_path=arena["ROLLBACK"],
        canonical_target=arena["CANONICAL"], scratch_dir=arena["scratch"],
        expected_source_sha256=LIVE_SHA256, confirm_disposable_copy=True, **kw,
    )


def _assert_source_rollback_intact(arena: dict) -> None:
    assert _sha(arena["SOURCE"]) == LIVE_SHA256, "SOURCE DEGISTI"
    assert _sha(arena["ROLLBACK"]) == LIVE_SHA256, "ROLLBACK DEGISTI"


def _assert_production_untouched() -> None:
    assert _sha(LIVE_DB) == LIVE_SHA256, "PRODUCTION DB DEGISTI — kritik ihlal"
    for ek in ("-wal", "-shm", "-journal"):
        assert not os.path.exists(LIVE_DB + ek), "production DB'de sidecar olustu: " + ek


# ─────────────────────────────────────────────────────────────────────────
# POZITIF — uctan uca rehearsal
# ─────────────────────────────────────────────────────────────────────────
def test_end_to_end_adoption_reaches_canonical_head(arena, ref_head):
    r = _adopt(arena)
    assert r.outcome == "ADOPTED"
    assert r.terminal_revision == CANONICAL_HEAD
    assert r.heads == 1
    assert r.integrity_check == "ok"
    assert r.foreign_key_violations == 0
    assert _revisions(arena["CANONICAL"]) == (CANONICAL_HEAD,)
    assert not certify_canonical_equivalence(
        arena["CANONICAL"], ref_head, expect_terminal=True,
        source_manifest=r.row_counts_before)
    _assert_source_rollback_intact(arena)
    _assert_production_untouched()


def test_row_preservation_is_exact_for_every_pre_existing_table(arena):
    r = _adopt(arena)
    for tablo, once in r.row_counts_before.items():
        assert r.row_counts_after[tablo] == once, tablo + " satir sayisi degisti"


def test_canonical_new_tables_start_empty(arena):
    r = _adopt(arena)
    yeni = set(r.row_counts_after) - set(r.row_counts_before)
    assert yeni, "yeni canonical tablo olusmadi"
    for t in yeni - {"alembic_version"}:
        assert r.row_counts_after[t] == 0, t + " bos baslamadi"
    assert r.row_counts_after["alembic_version"] == 1


def test_missing_revision_effects_are_applied_in_order(arena):
    r = _adopt(arena)
    assert REV_012 in r.applied_effects
    assert REV_013 in r.applied_effects
    assert r.applied_effects.index(REV_012) < r.applied_effects.index(REV_013)
    assert "ptf_drift_log" in _tables(arena["CANONICAL"])


def test_013_final_check_semantics_match_canonical(arena, scratch_refs, ref_head):
    _adopt(arena)
    con = sqlite3.connect(arena["CANONICAL"])
    rcon = sqlite3.connect(ref_head)
    try:
        w = check_constraints(con.execute(
            "SELECT sql FROM sqlite_master WHERE name='ptf_drift_log'").fetchone()[0])
        c = check_constraints(rcon.execute(
            "SELECT sql FROM sqlite_master WHERE name='ptf_drift_log'").fetchone()[0])
    finally:
        con.close()
        rcon.close()
    assert w == c
    assert any("missing_legacy" in ifade for ifade in w), w


def test_dedupe_unique_index_is_enforced_by_the_database(arena):
    """`ix_incidents_dedupe_unique` sonrasi ikinci ayni uclu REDDEDILMELI."""
    _adopt(arena)
    con = sqlite3.connect(arena["CANONICAL"])
    try:
        sutun = ("trace_id, tenant_id, severity, category, message, status, "
                 "occurrence_count, dedupe_key, dedupe_bucket")
        deger = "('t1','gelka','HIGH','CALC','m','OPEN',1,'k1',20677)"
        con.execute("INSERT INTO incidents (" + sutun + ") VALUES " + deger)
        con.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO incidents (" + sutun + ") VALUES "
                        + deger.replace("'t1'", "'t2'"))
            con.commit()
    finally:
        con.close()


def test_updated_by_backfill_is_not_executed_and_is_recorded_as_variant(arena):
    """
    Owner karari: 011'in `updated_by='system_migration'` backfill'i
    CALISTIRILMAZ (sahte provenance uretir). NULL'lar KORUNUR.
    """
    con = sqlite3.connect(arena["SOURCE"])
    try:
        once = con.execute(
            "SELECT COUNT(*) FROM market_reference_prices WHERE updated_by IS NULL").fetchone()[0]
    finally:
        con.close()
    r = _adopt(arena)
    con = sqlite3.connect(arena["CANONICAL"])
    try:
        sonra = con.execute(
            "SELECT COUNT(*) FROM market_reference_prices WHERE updated_by IS NULL").fetchone()[0]
        uydurma = con.execute(
            "SELECT COUNT(*) FROM market_reference_prices "
            "WHERE updated_by = 'system_migration'").fetchone()[0]
    finally:
        con.close()
    assert sonra == once, "NULL updated_by degerleri degistirildi"
    assert uydurma == 0, "gecmise donuk system_migration yazildi"
    assert ACCEPTED_DATA_VARIANT in r.accepted_data_variants


def test_audit_is_written_outside_db_and_carries_no_pii(arena):
    r = _adopt(arena)
    audit = read_audit(arena["CANONICAL"])
    assert audit is not None, "audit imzasi dogrulanamadi"
    assert audit["terminal_revision"] == CANONICAL_HEAD
    assert ACCEPTED_DATA_VARIANT in audit["accepted_data_variants"]
    blob = json.dumps(audit, ensure_ascii=False).lower()
    for yasak in ("password", "secret", "api_key", "token", "@"):
        assert yasak not in blob, "audit'te yasakli ifade: " + yasak
    assert r.published_to == arena["CANONICAL"]


def test_second_and_third_run_are_byte_stable_no_ops(arena, tmp_path):
    r1 = _adopt(arena)
    assert r1.outcome == "ADOPTED"
    adopted = _sha(arena["CANONICAL"])
    for kosu in (2, 3):
        w = str(tmp_path / ("W" + str(kosu) + ".db"))
        t = str(tmp_path / ("C" + str(kosu) + ".db"))
        shutil.copyfile(arena["CANONICAL"], w)
        r = adopt_unversioned_copy(
            w, source_path=arena["SOURCE"], rollback_path=arena["ROLLBACK"],
            canonical_target=t, scratch_dir=arena["scratch"],
            expected_source_sha256=LIVE_SHA256, confirm_disposable_copy=True)
        assert r.outcome == "ALREADY_ADOPTED"
        assert _sha(w) == adopted, str(kosu) + ". kosu byte mutasyonu yapti"
        assert not os.path.exists(t), str(kosu) + ". kosu yeni hedef yayimladi"


def test_is_certifiably_adopted_is_independent_of_audit(arena, ref_head):
    r = _adopt(arena)
    os.remove(arena["CANONICAL"] + ".pdsmr-r4b2-adoption-audit.json")
    assert is_certifiably_adopted(arena["CANONICAL"], ref_head, r.row_counts_before)


# ─────────────────────────────────────────────────────────────────────────
# FAULT INJECTION — her noktada SOURCE/ROLLBACK/production degismemeli
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("nokta", FAULT_POINTS)
def test_fault_injection_never_reports_adopted_halfway(arena, nokta, ref_head):
    try:
        r = _adopt(arena, fault_at=nokta)
    except (InjectedFault, AdoptionRefused):
        r = None
    _assert_source_rollback_intact(arena)
    _assert_production_untouched()

    if r is not None:
        # Kesinti yayim/audit SONRASINDA ise akis tamamlanmis olabilir.
        assert nokta in ("after_atomic_publish", "after_audit_commit"), (
            nokta + " noktasinda kesinti olmasina ragmen akis tamamlandi")
        assert r.outcome == "ADOPTED"
        return

    # Yarim kalan WORKING (varsa) ASLA sertifikalanabilir olmamali.
    if os.path.exists(arena["WORKING"]):
        assert not is_certifiably_adopted(arena["WORKING"], ref_head), (
            nokta + ": yarim WORKING sertifikalanabilir gorundu")
    # Yayim yapilmadiysa canonical hedef OLUSMAMALI.
    if nokta not in ("after_atomic_publish", "before_audit_commit", "after_audit_commit"):
        assert not os.path.exists(arena["CANONICAL"]), (
            nokta + ": yarim akis canonical hedefi yayimladi")


def _rebuild_tablolari(live_source_path: str, ref_head_path: str) -> tuple[str, ...]:
    return plan_rebuild_tables(live_source_path, ref_head_path)


def test_every_rebuilt_table_has_its_own_before_and_after_fault_point(live_source, ref_head):
    """
    "HER rebuild kapsanmali" sartinin MEKANIK kaniti.

    Tek bir `mid_each_rebuild` noktasi YETMEZ — yalniz bir yinelemede
    tetiklenir. Bu test, onarim listesindeki HER tablo icin ayri
    before/after noktasi URETILDIGINI ve matrisin bunlarin TAMAMINI
    kapsadigini dogrular.
    """
    tablolar = _rebuild_tablolari(live_source, ref_head)
    assert tablolar, "onarim listesi bos — fixture beklenen surumden farkli"
    noktalar = rebuild_fault_points(tablolar)
    assert len(noktalar) == 3 * len(tablolar)
    for t in tablolar:
        assert "before_rebuild:" + t in noktalar
        assert "mid_rebuild:" + t in noktalar
        assert "after_rebuild:" + t in noktalar


def test_fault_matrix_covers_every_mandatory_point(live_source, ref_head):
    """Zorunlu 23 genel nokta + her rebuild icin 2 nokta = TAM kapsama."""
    tablolar = _rebuild_tablolari(live_source, ref_head)
    kapsanan = set(FAULT_POINTS) | set(rebuild_fault_points(tablolar))
    zorunlu = {
        "before_working_copy", "after_working_copy",
        "before_first_rebuild", "mid_each_rebuild", "after_rebuild_batch",
        "before_index_batch", "mid_index_batch", "after_index_batch",
        "before_012_effect", "after_012_effect",
        "before_013_effect", "after_013_effect",
        "before_f4_effect", "after_f4_effect",
        "before_beda_effect", "after_beda_effect",
        "before_terminal_certification", "before_terminal_record",
        "after_terminal_record",
        "before_atomic_publish", "after_atomic_publish",
        "before_audit_commit", "after_audit_commit",
    }
    assert len(zorunlu) == 23
    assert zorunlu <= kapsanan, "kapsanmayan zorunlu nokta: " + str(sorted(zorunlu - kapsanan))
    assert len(kapsanan) == 23 + 3 * len(tablolar)


@pytest.mark.parametrize("yon", ["before", "mid", "after"])
def test_per_rebuild_fault_injection_is_fail_closed(arena, ref_head, live_source, yon):
    """
    Onarim listesindeki HER tablo icin before / mid / after kesintisi AYRI
    AYRI kosulur (9 tablo x 3 = 27 nokta). Her birinde: SOURCE/ROLLBACK/
    production degismemeli, yarim WORKING sertifikalanabilir OLMAMALI,
    erken terminal kayit olmamali, canonical hedef yayimlanmamali.
    """
    tablolar = _rebuild_tablolari(live_source, ref_head)
    for t in tablolar:
        shutil.copyfile(arena["SOURCE"], arena["WORKING"])
        if os.path.exists(arena["CANONICAL"]):
            os.remove(arena["CANONICAL"])
        with pytest.raises(InjectedFault):
            _adopt(arena, fault_at=yon + "_rebuild:" + t)
        _assert_source_rollback_intact(arena)
        _assert_production_untouched()
        assert not is_certifiably_adopted(arena["WORKING"], ref_head), (
            yon + "_rebuild:" + t + " -> yarim WORKING sertifikalanabilir gorundu")
        assert _revisions(arena["WORKING"]) == (), (
            yon + "_rebuild:" + t + " -> terminal revizyon erken yazilmis")
        assert not os.path.exists(arena["CANONICAL"]), (
            yon + "_rebuild:" + t + " -> yarim akis canonical hedefi yayimladi")


@pytest.mark.parametrize("yon", ["before", "mid", "after"])
def test_before_mid_after_are_three_distinct_points_not_aliases(
    arena, ref_head, live_source, monkeypatch, yon
):
    """
    `mid` bir before/after ALIAS'I OLMADIGININ mekanik kaniti.

    Acilan her baglantiya trace callback takilir ve kesinti anina kadar
    CALISAN HER SQL kaydedilir. Beklenen imzalar (ilk rebuild tablosu icin):
      before -> gecici tablo HENUZ olusturulmadi
      mid    -> gecici tablo OLUSTU + veri kopyalandi, ama DROP/RENAME YOK
      after  -> DROP + RENAME tamamlandi
    """
    tablo = _rebuild_tablolari(live_source, ref_head)[0]
    gecici = tablo + "_pdsmr_r4b2_new"
    calisan: list[str] = []
    gercek_connect = sqlite3.connect

    def izleyen(target, *a, **kw):
        con = gercek_connect(target, *a, **kw)
        con.set_trace_callback(calisan.append)
        return con

    monkeypatch.setattr(sqlite3, "connect", izleyen)
    with pytest.raises(InjectedFault):
        _adopt(arena, fault_at=yon + "_rebuild:" + tablo)
    monkeypatch.undo()

    metin = " || ".join(calisan)
    gecici_olustu = ('CREATE TABLE "' + gecici + '"') in metin
    veri_kopyalandi = ('INSERT INTO "' + gecici + '"') in metin
    dusuruldu = ('DROP TABLE "' + tablo + '"') in metin
    yeniden_adlandirildi = ('RENAME TO "' + tablo + '"') in metin

    if yon == "before":
        assert not gecici_olustu, "before: rebuild HENUZ baslamamis olmali"
        assert not dusuruldu
    elif yon == "mid":
        assert gecici_olustu, "mid: rebuild FIILEN baslamis olmali"
        assert veri_kopyalandi, "mid: veri kopyasi tamamlanmis olmali"
        assert not dusuruldu, "mid: tablo HENUZ canonical gorunur OLMAMALI"
        assert not yeniden_adlandirildi, "mid: RENAME yapilmamis olmali"
    else:
        assert gecici_olustu and veri_kopyalandi
        assert dusuruldu and yeniden_adlandirildi, "after: rebuild TAMAMLANMIS olmali"

    _assert_source_rollback_intact(arena)
    _assert_production_untouched()


def test_mid_rebuild_fault_rolls_back_the_table_unchanged(arena, ref_head, live_source):
    """
    `mid` kesintisi transaction ICINDE oldugu icin ROLLBACK ile geri alinir:
    tablo ESKI haliyle kalir, gecici tablo ARTIK kalmaz.
    """
    tablo = _rebuild_tablolari(live_source, ref_head)[0]
    con = sqlite3.connect(arena["WORKING"])
    try:
        once_ddl = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tablo,)).fetchone()[0]
        once_satir = con.execute('SELECT COUNT(*) FROM "' + tablo + '"').fetchone()[0]
    finally:
        con.close()

    with pytest.raises(InjectedFault):
        _adopt(arena, fault_at="mid_rebuild:" + tablo)

    con = sqlite3.connect(arena["WORKING"])
    try:
        sonra_ddl = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tablo,)).fetchone()[0]
        sonra_satir = con.execute('SELECT COUNT(*) FROM "' + tablo + '"').fetchone()[0]
        kalinti = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%_pdsmr_r4b2_new'")]
    finally:
        con.close()
    assert sonra_ddl == once_ddl, "mid kesintisi tablo semasini yarim birakti"
    assert sonra_satir == once_satir
    assert kalinti == [], "gecici rebuild tablosu kalinti birakti: " + str(kalinti)
    assert _revisions(arena["WORKING"]) == ()
    _assert_source_rollback_intact(arena)


@pytest.mark.parametrize("yon", ["before", "mid", "after"])
def test_rebuild_fault_for_untouched_table_is_deterministic_refusal_all_directions(arena, yon):
    """Onarim planinda OLMAYAN tabloya her uc yonde de deterministik ret."""
    with pytest.raises(AdoptionRefused, match="onarim listesinde yok"):
        _adopt(arena, fault_at=yon + "_rebuild:incidents")
    _assert_source_rollback_intact(arena)


def test_rebuild_fault_for_untouched_table_is_deterministic_refusal(arena):
    """
    Onarim listesinde OLMAYAN bir tabloya kesinti istenirse SESSIZCE
    gecilmez — deterministik olarak REDDEDILIR (aksi halde test yanlislikla
    "gecti" sanilirdi).
    """
    with pytest.raises(AdoptionRefused, match="onarim listesinde yok"):
        _adopt(arena, fault_at="before_rebuild:incidents")
    _assert_source_rollback_intact(arena)


@pytest.mark.parametrize("nokta", [
    "before_terminal_certification", "before_terminal_record",
    "before_index_batch", "after_rebuild_batch", "before_012_effect",
])
def test_terminal_revision_never_appears_before_equivalence(arena, nokta):
    try:
        _adopt(arena, fault_at=nokta)
    except (InjectedFault, AdoptionRefused):
        pass
    if os.path.exists(arena["WORKING"]):
        assert _revisions(arena["WORKING"]) == (), (
            nokta + ": terminal revizyon esdegerlikten ONCE yazilmis")


def test_rerun_after_fault_is_safe_or_deterministic_hard_stop(arena, ref_head):
    """Kesinti sonrasi yeniden kosu: ya guvenle tamamlar ya deterministik durur."""
    try:
        _adopt(arena, fault_at="mid_each_rebuild")
    except (InjectedFault, AdoptionRefused):
        pass
    _assert_source_rollback_intact(arena)
    # WORKING'i SOURCE'tan tazeleyip yeniden dene — deterministik tamamlanmali.
    shutil.copyfile(arena["SOURCE"], arena["WORKING"])
    r = _adopt(arena)
    assert r.outcome == "ADOPTED"
    assert not certify_canonical_equivalence(
        arena["CANONICAL"], ref_head, expect_terminal=True,
        source_manifest=r.row_counts_before)


# ─────────────────────────────────────────────────────────────────────────
# NEGATIF — hepsi fail-closed, hicbiri SOURCE/ROLLBACK'i degistirmez
# ─────────────────────────────────────────────────────────────────────────
def test_missing_explicit_confirmation_is_refused(arena):
    with pytest.raises(AdoptionRefused, match="confirm_disposable_copy"):
        adopt_unversioned_copy(
            arena["WORKING"], source_path=arena["SOURCE"], rollback_path=arena["ROLLBACK"],
            canonical_target=arena["CANONICAL"], scratch_dir=arena["scratch"],
            expected_source_sha256=LIVE_SHA256)
    _assert_source_rollback_intact(arena)


def test_production_path_target_is_refused(arena, tmp_path):
    sahte = tmp_path / "AppData" / "Local" / "Programs" / "Gelka Enerji" / "resources"
    sahte.mkdir(parents=True)
    hedef = str(sahte / "gelka_enerji.db")
    with pytest.raises(AdoptionRefused, match="kurulu uygulama"):
        assert_disposable_target(hedef, etiket="canonical_target",
                                 source=arena["SOURCE"], rollback=arena["ROLLBACK"])
    _assert_production_untouched()


def test_confirmation_flag_alone_does_not_bypass_path_safety(arena, tmp_path):
    """`confirm_disposable_copy=True` TEK BASINA yeterli guvenlik SAYILMAZ."""
    sahte = tmp_path / "AppData" / "Local" / "Programs" / "Gelka Enerji"
    sahte.mkdir(parents=True)
    with pytest.raises(AdoptionRefused, match="kurulu uygulama"):
        adopt_unversioned_copy(
            arena["WORKING"], source_path=arena["SOURCE"], rollback_path=arena["ROLLBACK"],
            canonical_target=str(sahte / "out.db"), scratch_dir=arena["scratch"],
            expected_source_sha256=LIVE_SHA256, confirm_disposable_copy=True)
    _assert_source_rollback_intact(arena)


def test_source_hash_drift_is_refused(arena):
    con = sqlite3.connect(arena["SOURCE"])
    con.execute("UPDATE customers SET notes = COALESCE(notes,'') || 'x'")
    con.commit()
    con.close()
    with pytest.raises(AdoptionRefused, match="parmak izi"):
        _adopt(arena)
    assert not os.path.exists(arena["CANONICAL"])


def test_rollback_not_byte_identical_to_source_is_refused(arena):
    con = sqlite3.connect(arena["ROLLBACK"])
    con.execute("UPDATE customers SET notes = COALESCE(notes,'') || 'y'")
    con.commit()
    con.close()
    with pytest.raises(AdoptionRefused, match="byte-identik"):
        _adopt(arena)


def test_source_and_rollback_same_file_is_refused(arena):
    with pytest.raises(AdoptionRefused, match="ayni dosya"):
        adopt_unversioned_copy(
            arena["WORKING"], source_path=arena["SOURCE"], rollback_path=arena["SOURCE"],
            canonical_target=arena["CANONICAL"], scratch_dir=arena["scratch"],
            expected_source_sha256=LIVE_SHA256, confirm_disposable_copy=True)


def test_working_already_versioned_is_refused(arena):
    con = sqlite3.connect(arena["WORKING"])
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    con.execute("INSERT INTO alembic_version VALUES ('013_extend_ptf_drift_severity')")
    con.commit()
    con.close()
    with pytest.raises(AdoptionRefused, match="UNVERSIONED"):
        _adopt(arena)
    _assert_source_rollback_intact(arena)


def test_unknown_fault_point_is_refused(arena):
    with pytest.raises(AdoptionRefused, match="fault noktasi"):
        _adopt(arena, fault_at="olmayan_nokta")


def test_missing_working_file_is_refused(arena):
    os.remove(arena["WORKING"])
    with pytest.raises(AdoptionRefused, match="WORKING"):
        _adopt(arena)


def test_extra_table_not_in_canonical_is_refused(arena):
    con = sqlite3.connect(arena["WORKING"])
    con.execute("CREATE TABLE surprise_table (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    with pytest.raises(AdoptionRefused, match="tablo kumesi"):
        _adopt(arena)
    _assert_source_rollback_intact(arena)


def test_unique_duplicate_blocks_index_and_preserves_data(arena):
    """Duplicate varsa UNIQUE index kurulmaz; veri SILINMEZ/BIRLESTIRILMEZ."""
    con = sqlite3.connect(arena["WORKING"])
    try:
        sutun = ("trace_id, tenant_id, severity, category, message, status, "
                 "occurrence_count, dedupe_key, dedupe_bucket")
        for iz in ("a", "b"):
            con.execute("INSERT INTO incidents (" + sutun + ") VALUES "
                        "('" + iz + "','gelka','HIGH','CALC','m','OPEN',1,'k1',5)")
        con.commit()
        once = con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    finally:
        con.close()
    with pytest.raises(AdoptionRefused, match="UNIQUE index kurulamaz"):
        _adopt(arena)
    con = sqlite3.connect(arena["WORKING"])
    try:
        assert con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == once
    finally:
        con.close()
    _assert_source_rollback_intact(arena)


def test_ptf_drift_log_non_empty_shape_change_is_refused(arena, scratch_refs):
    """013 sekil degisimi BOS olmayan tabloda reddedilir (veri kaybi olamaz)."""
    from app.legacy_adoption.unversioned_adoption import _replace_empty_table_shape

    ref = build_canonical_reference(scratch_refs, REV_013)
    con = sqlite3.connect(arena["WORKING"])
    con.execute("CREATE TABLE ptf_drift_log (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO ptf_drift_log (id) VALUES (1)")
    con.commit()
    con.close()
    with pytest.raises(AdoptionRefused, match="sekil degisimi reddedildi"):
        _replace_empty_table_shape(arena["WORKING"], "ptf_drift_log", ref)


def test_early_terminal_record_is_caught_by_certification(arena, ref_head):
    """Esdegerlik kanitlanmadan alembic_version olusursa kapi FAIL vermeli."""
    con = sqlite3.connect(arena["WORKING"])
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    con.commit()
    con.close()
    hatalar = certify_canonical_equivalence(
        arena["WORKING"], ref_head, expect_terminal=False)
    assert any("ERKEN TERMINAL KAYIT" in h for h in hatalar), hatalar


def test_terminal_revision_present_but_schema_incomplete_is_not_adopted(arena, ref_head):
    """Terminal revizyon VAR ama sema eksikse sertifikasyon GECMEMELI."""
    con = sqlite3.connect(arena["WORKING"])
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    con.execute("INSERT INTO alembic_version VALUES ('" + CANONICAL_HEAD + "')")
    con.commit()
    con.close()
    assert not is_certifiably_adopted(arena["WORKING"], ref_head)


def test_row_loss_is_detected_by_certification(arena, ref_head):
    r = _adopt(arena)
    con = sqlite3.connect(arena["CANONICAL"])
    con.execute("DELETE FROM customers WHERE id = (SELECT MIN(id) FROM customers)")
    con.commit()
    con.close()
    hatalar = certify_canonical_equivalence(
        arena["CANONICAL"], ref_head, expect_terminal=True,
        source_manifest=r.row_counts_before)
    assert any("satir korunumu ihlali" in h for h in hatalar), hatalar


def test_index_shape_drift_is_detected_by_certification(arena, ref_head):
    r = _adopt(arena)
    con = sqlite3.connect(arena["CANONICAL"])
    con.execute("DROP INDEX ix_incidents_dedupe_unique")
    con.execute("CREATE UNIQUE INDEX ix_incidents_dedupe_unique ON incidents "
                "(dedupe_key, tenant_id, dedupe_bucket)")
    con.commit()
    con.close()
    hatalar = certify_canonical_equivalence(
        arena["CANONICAL"], ref_head, expect_terminal=True,
        source_manifest=r.row_counts_before)
    assert any("index sekli" in h for h in hatalar), hatalar


def test_foreign_key_violation_is_detected_by_certification(arena, ref_head):
    r = _adopt(arena)
    con = sqlite3.connect(arena["CANONICAL"])
    con.execute("UPDATE offers SET customer_id = 999999 "
                "WHERE id = (SELECT MIN(id) FROM offers)")
    con.commit()
    con.close()
    hatalar = certify_canonical_equivalence(
        arena["CANONICAL"], ref_head, expect_terminal=True,
        source_manifest=r.row_counts_before)
    assert any("foreign_key_check" in h for h in hatalar), hatalar


def test_wrong_check_semantics_is_detected(arena, ref_head):
    r = _adopt(arena)
    con = sqlite3.connect(arena["CANONICAL"])
    surum = con.execute("PRAGMA schema_version").fetchone()[0]
    sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='ptf_drift_log'").fetchone()[0]
    con.execute("PRAGMA writable_schema=ON")
    con.execute("UPDATE sqlite_master SET sql=? WHERE name='ptf_drift_log'",
                (sql.replace("'missing_legacy'", "'bozuk_deger'"),))
    con.execute("PRAGMA schema_version=" + str(surum + 1))
    con.execute("PRAGMA writable_schema=OFF")
    con.commit()
    con.close()
    hatalar = certify_canonical_equivalence(
        arena["CANONICAL"], ref_head, expect_terminal=True,
        source_manifest=r.row_counts_before)
    assert any("CHECK semantigi" in h for h in hatalar), hatalar


# ─────────────────────────────────────────────────────────────────────────
# WIRING — bu modul HICBIR uretim yolundan cagrilmamali
# ─────────────────────────────────────────────────────────────────────────
def test_module_is_not_wired_into_any_runtime_path():
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    izinli = os.path.join(backend, "app", "legacy_adoption")
    ihlaller = []
    for kok, _dizinler, dosyalar in os.walk(os.path.join(backend, "app")):
        if kok.startswith(izinli):
            continue
        for d in dosyalar:
            if not d.endswith(".py"):
                continue
            with open(os.path.join(kok, d), encoding="utf-8", errors="replace") as fh:
                if "unversioned_adoption" in fh.read():
                    ihlaller.append(os.path.relpath(os.path.join(kok, d), backend))
    assert ihlaller == [], "unversioned_adoption uygulama koduna baglanmis: " + str(ihlaller)


def test_module_never_uses_forbidden_sql_or_create_all():
    """
    Yasak yapilar KOD duzeyinde aranir, ham metinde DEGIL.

    Modul docstring'i `create_all` ve `SELECT *`i NEDEN YASAK olduklarini
    ANLATIR; naive bir substring taramasi bu ACIKLAMAYA takilir ve gercek
    bir ihlali gizleyecek kadar gurultulu olur. Bu yuzden AST kullanilir:
    - hicbir cagri/attribute `create_all` veya `stamp` OLMAMALI,
    - hicbir SQL STRING SABITI `SELECT *` veya kolon listesiz INSERT
      icermemeli (docstring'ler haric tutulur).
    """
    import ast

    yol = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "legacy_adoption", "unversioned_adoption.py")
    with open(yol, encoding="utf-8") as fh:
        agac = ast.parse(fh.read())

    # 1) Yasak CAGRI/ATTRIBUTE adlari
    yasak_ad = {"create_all", "stamp"}
    ihlaller = []
    for n in ast.walk(agac):
        if isinstance(n, ast.Attribute) and n.attr in yasak_ad:
            ihlaller.append("attribute:" + n.attr)
        if isinstance(n, ast.Name) and n.id in yasak_ad:
            ihlaller.append("name:" + n.id)
    assert ihlaller == [], "yasak kod yapisi: " + str(ihlaller)

    # 2) SQL string SABITLERI — docstring'ler haric
    docstringler = set()
    for n in ast.walk(agac):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(n, clean=False)
            if d:
                docstringler.add(d)
    sql_ihlal = []
    for n in ast.walk(agac):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if n.value in docstringler:
                continue
            duz = " ".join(n.value.split()).upper()
            if "SELECT *" in duz:
                sql_ihlal.append("SELECT * -> " + duz[:60])
            if "INSERT INTO" in duz and "VALUES" in duz and "(" not in duz.split("VALUES")[0].split("INSERT INTO")[1]:
                sql_ihlal.append("kolon listesiz INSERT -> " + duz[:60])
    assert sql_ihlal == [], "yasak SQL sabiti: " + str(sql_ihlal)
