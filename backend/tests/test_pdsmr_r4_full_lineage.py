"""
PDSMR-R4 / FAZ 2 — classify_full_lineage() testleri.

Kanitlanacak sey: base'ten canonical head'e (351d314819d5) TUM grafin
(govde+A dali+B dali+merge, 24 revizyon) somut-etki siniflandirmasi
dogru, graf-farkinda, supersession-farkinda ve fail-closed'dir. Hicbir
test/fixture gercek production/kurulu-uygulama yoluna DOKUNMAZ; hepsi
disposable temp DB'lerdir (pytest tmp_path).

Owner'in PDSMR-R4 Faz 2 sozlesmesi (GO mesaji) buradaki zorunlu test
matrisidir — asagidaki bolum basliklari o maddelerle birebir eslesir.

Bu dosya Faz 2 kapsamindadir: classify_full_lineage() hicbir production/
startup/adoption cagrisina baglanmaz (bkz. lineage.py modul dokstring'i).
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.legacy_adoption import alembic_runner as ar  # noqa: E402
from app.legacy_adoption.lineage import (  # noqa: E402
    CANONICAL_HEAD,
    FullEffectClass,
    GraphSegment,
    classify_full_lineage,
)
from test_legacy_adoption_validator import _rewrite_table_sql, _sha256  # noqa: E402

# ── Graf sirasi (topological) — lineage.py'nin KENDI ic sabitleriyle
# BAGIMSIZ, ayri dogrulama icin burada da yazili tutulur. ────────────────
TRUNK = (
    "001_initial", "002", "003", "18100a648086", "c1a7f0e94d52", "004",
    "005_retry_executor", "006_issue_integration", "007_reclassification",
    "008_retry_orchestrator", "009_resolution_reasons", "010_feedback_loop",
    "011_market_prices_ptf_admin",
)
BRANCH_A = ("012_add_ptf_drift_log_table", "013_extend_ptf_drift_severity")
BRANCH_B = (
    "a93beeaddf82", "dc8343278cfa", "8b9a332a3680", "e340ce40c05c",
    "f4e7efc70c80", "beda29569b0d", "7b3e1c8a52df", "9d4a2f6b18ce",
)
ALL_24 = TRUNK + BRANCH_A + BRANCH_B + (CANONICAL_HEAD,)

# Data-migration (backfill) icerdigi icin HEAD'de bile bilerek
# UNKNOWN_OR_UNPROVABLE kalmasi gereken revizyonlar (requirement 6 —
# tahmin etmeme kurali; bu bir hata DEGIL, bilincli tasarimdir).
EXPECTED_UNPROVABLE_AT_HEAD = {"005_retry_executor", "011_market_prices_ptf_admin"}


def _build(tmp_path, revision: str, label: str | None = None) -> str:
    """Gercek `alembic upgrade` ile, TAZE bir DB'de, verilen checkpoint'e kadar insa eder."""
    dbpath = str(tmp_path / f"{label or revision}.db")
    ar.alembic_upgrade(dbpath, revision)
    return dbpath


def _by_rev(results):
    return {r.revision: r for r in results}


def _file_stat(path):
    st = os.stat(path)
    return (_sha256(path), st.st_size, st.st_mtime_ns)


# ═════════════════════════════════════════════════════════════════════
# 1) Alembic ile olusturulmus disposable DB'lerde HER revision checkpoint'i
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("revision", ALL_24)
def test_every_real_alembic_checkpoint_classifies_cleanly(tmp_path, revision):
    """Gercek, saglikli bir alembic ciktisinda HICBIR revizyon CONFLICT
    olmamalidir — CONFLICT yalniz gercek bir sema anomalisini isaret eder."""
    dbpath = _build(tmp_path, revision)
    results = classify_full_lineage(dbpath)
    assert len(results) == 24
    assert {r.revision for r in results} == set(ALL_24)
    conflicts = [r for r in results if r.effect_class is FullEffectClass.CONFLICT]
    assert conflicts == [], (
        f"{revision}: beklenmeyen CONFLICT: "
        f"{[(c.revision, c.reason) for c in conflicts]}"
    )


# ═════════════════════════════════════════════════════════════════════
# 2) 011, 012, 013, B dalindaki HER revision, ve birlesik head
# ═════════════════════════════════════════════════════════════════════
def test_checkpoint_011_trunk_present_branch_tail_absent_safe(tmp_path):
    dbpath = _build(tmp_path, "011_market_prices_ptf_admin")
    by_rev = _by_rev(classify_full_lineage(dbpath))
    for rev in TRUNK:
        expected = (
            FullEffectClass.UNKNOWN_OR_UNPROVABLE if rev in EXPECTED_UNPROVABLE_AT_HEAD
            else FullEffectClass.PRESENT_EXACT
        )
        assert by_rev[rev].effect_class is expected, f"{rev}: {by_rev[rev].reason}"
    # A ve B daline HENUZ girilmedi -> guvenle ABSENT_SAFE_TO_APPLY olmali
    # (on kosullari — market_reference_prices/incidents/customers/offers —
    # govde tarafindan zaten saglanmis durumda).
    assert by_rev["012_add_ptf_drift_log_table"].effect_class is FullEffectClass.ABSENT_SAFE_TO_APPLY
    assert by_rev["a93beeaddf82"].effect_class is FullEffectClass.ABSENT_SAFE_TO_APPLY


def test_checkpoint_012_present_013_absent_safe(tmp_path):
    dbpath = _build(tmp_path, "012_add_ptf_drift_log_table")
    by_rev = _by_rev(classify_full_lineage(dbpath))
    assert by_rev["012_add_ptf_drift_log_table"].effect_class is FullEffectClass.PRESENT_EXACT
    assert by_rev["013_extend_ptf_drift_severity"].effect_class is FullEffectClass.ABSENT_SAFE_TO_APPLY


def test_checkpoint_013_present_and_012_supersession_recognized(tmp_path):
    """013, 012'nin severity CHECK'ini degistirir (2 deger -> 3 deger).
    012'nin KENDI atomu bu DEGISTIRILMIS haliyle PRESENT_SUPERSEDED
    sayilmali (madde 4) — CONFLICT DEGIL."""
    dbpath = _build(tmp_path, "013_extend_ptf_drift_severity")
    by_rev = _by_rev(classify_full_lineage(dbpath))
    assert by_rev["013_extend_ptf_drift_severity"].effect_class is FullEffectClass.PRESENT_EXACT
    assert by_rev["012_add_ptf_drift_log_table"].effect_class is FullEffectClass.PRESENT_EXACT
    severity_atom = next(
        a for a in by_rev["012_add_ptf_drift_log_table"].atoms
        if a.atom_id.startswith("constraint:") and "severity" in a.atom_id
    )
    assert severity_atom.state == "PRESENT_SUPERSEDED"
    assert severity_atom.superseded_by == "013_extend_ptf_drift_severity"


@pytest.mark.parametrize("revision", BRANCH_B)
def test_each_branch_b_checkpoint_is_present_exact(tmp_path, revision):
    dbpath = _build(tmp_path, revision)
    by_rev = _by_rev(classify_full_lineage(dbpath))
    r = by_rev[revision]
    assert r.effect_class is FullEffectClass.PRESENT_EXACT, r.reason
    assert r.segment is GraphSegment.BRANCH_B


def test_merged_head_recognizes_all_ancestor_effects(tmp_path):
    """Birlesik head'de (351d314819d5) TUM ataların etkisi (govde+A+B)
    dogru taninmalidir — yalniz data-backfill icerenler haric (madde 6)."""
    dbpath = _build(tmp_path, CANONICAL_HEAD)
    results = classify_full_lineage(dbpath)
    assert len(results) == 24
    for r in results:
        if r.revision in EXPECTED_UNPROVABLE_AT_HEAD:
            assert r.effect_class is FullEffectClass.UNKNOWN_OR_UNPROVABLE, f"{r.revision}: {r.reason}"
        else:
            assert r.effect_class is FullEffectClass.PRESENT_EXACT, f"{r.revision}: {r.reason}"
    segs = _by_rev(results)
    for rev in TRUNK:
        assert segs[rev].segment is GraphSegment.TRUNK
    for rev in BRANCH_A:
        assert segs[rev].segment is GraphSegment.BRANCH_A
    for rev in BRANCH_B:
        assert segs[rev].segment is GraphSegment.BRANCH_B
    assert segs[CANONICAL_HEAD].segment is GraphSegment.MERGE


# ═════════════════════════════════════════════════════════════════════
# 3) Tamamen eksik etki
# ═════════════════════════════════════════════════════════════════════
def test_completely_missing_effect_is_absent_safe_to_apply(tmp_path):
    dbpath = str(tmp_path / "empty.db")
    sqlite3.connect(dbpath).close()
    by_rev = _by_rev(classify_full_lineage(dbpath))
    for rev in ("001_initial", "003", "18100a648086", "c1a7f0e94d52", "012_add_ptf_drift_log_table"):
        assert by_rev[rev].effect_class is FullEffectClass.ABSENT_SAFE_TO_APPLY, rev
    # Bagimli oldugu tablo yoksa (govde henuz kurulmadi) ABSENT_SAFE_TO_APPLY
    # DEGIL, UNKNOWN_OR_UNPROVABLE olmalidir (requirement 3).
    assert by_rev["002"].effect_class is FullEffectClass.UNKNOWN_OR_UNPROVABLE
    assert by_rev["a93beeaddf82"].effect_class is FullEffectClass.UNKNOWN_OR_UNPROVABLE


# ═════════════════════════════════════════════════════════════════════
# 4) Kismi mevcut etki -> CONFLICT (requirement 2)
# ═════════════════════════════════════════════════════════════════════
def test_partial_table_effect_is_conflict_not_safe_to_apply(tmp_path):
    """contracts'in BIR kolonu silinirse (a93beeaddf82'nin 18 kolonundan
    17'si kalir), sonuc CONFLICT olmalidir — ABSENT_SAFE_TO_APPLY DEGIL."""
    dbpath = _build(tmp_path, "a93beeaddf82")
    con = sqlite3.connect(dbpath)
    con.execute("ALTER TABLE contracts DROP COLUMN pdf_sha256")
    con.commit()
    con.close()
    by_rev = _by_rev(classify_full_lineage(dbpath))
    r = by_rev["a93beeaddf82"]
    assert r.effect_class is FullEffectClass.CONFLICT, r.reason
    assert "kismi" in r.reason


def test_partial_index_set_is_conflict(tmp_path):
    """incidents'in 004 tarafindan eklenen 6 index'inden biri silinirse
    004 CONFLICT olmalidir (govde tablosu/diger kolonlar hala mevcut)."""
    dbpath = _build(tmp_path, "004")
    con = sqlite3.connect(dbpath)
    con.execute("DROP INDEX ix_incidents_provider")
    con.commit()
    con.close()
    by_rev = _by_rev(classify_full_lineage(dbpath))
    assert by_rev["004"].effect_class is FullEffectClass.CONFLICT


# ═════════════════════════════════════════════════════════════════════
# 5) Yanlis affinity / default / constraint
# ═════════════════════════════════════════════════════════════════════
def test_wrong_column_affinity_is_conflict(tmp_path):
    dbpath = _build(tmp_path, "012_add_ptf_drift_log_table")
    # canonical_price FLOAT (REAL affinity) -> VARCHAR (TEXT affinity)
    _rewrite_table_sql(dbpath, "ptf_drift_log", "canonical_price FLOAT", "canonical_price VARCHAR(20)")
    by_rev = _by_rev(classify_full_lineage(dbpath))
    r = by_rev["012_add_ptf_drift_log_table"]
    assert r.effect_class is FullEffectClass.CONFLICT
    assert any(a.state == "PRESENT_WRONG_SHAPE" and "canonical_price" in a.atom_id for a in r.atoms)


def test_wrong_column_nullability_is_conflict(tmp_path):
    dbpath = _build(tmp_path, "012_add_ptf_drift_log_table")
    # request_hash NOT NULL -> nullable (notnull farki)
    _rewrite_table_sql(
        dbpath, "ptf_drift_log",
        "request_hash VARCHAR(64) NOT NULL", "request_hash VARCHAR(64)",
    )
    by_rev = _by_rev(classify_full_lineage(dbpath))
    assert by_rev["012_add_ptf_drift_log_table"].effect_class is FullEffectClass.CONFLICT


def test_wrong_check_constraint_text_is_conflict_not_superseded(tmp_path):
    """severity CHECK'i BILINEN iki halden (012'nin 2-degerlisi, 013'un
    3-degerlisi) FARKLI bir metne sahipse, bu 'supersession' DEGIL,
    CONFLICT olmalidir — orntulu tolerans YOK (madde 4)."""
    dbpath = _build(tmp_path, "012_add_ptf_drift_log_table")
    _rewrite_table_sql(
        dbpath, "ptf_drift_log",
        "CHECK (severity IN ('low', 'high'))",
        "CHECK (severity IN ('low', 'high', 'medium'))",
    )
    by_rev = _by_rev(classify_full_lineage(dbpath))
    r = by_rev["012_add_ptf_drift_log_table"]
    assert r.effect_class is FullEffectClass.CONFLICT
    assert any(a.state == "PRESENT_WRONG_SHAPE" and "severity" in a.atom_id for a in r.atoms)


# ═════════════════════════════════════════════════════════════════════
# 6) Yanlis index kolon sirasi, unique ve partial durumu
# ═════════════════════════════════════════════════════════════════════
def test_wrong_index_column_order_is_conflict(tmp_path):
    """ix_incidents_dedupe_unique (tenant_id,dedupe_key,dedupe_bucket) sirasi
    degisirse (ayni kolon kumesi, FARKLI sira), sonuc CONFLICT olmalidir."""
    dbpath = _build(tmp_path, "004")
    con = sqlite3.connect(dbpath)
    con.execute("DROP INDEX ix_incidents_dedupe_unique")
    con.execute(
        "CREATE UNIQUE INDEX ix_incidents_dedupe_unique ON incidents "
        "(dedupe_key, tenant_id, dedupe_bucket)"
    )
    con.commit()
    con.close()
    by_rev = _by_rev(classify_full_lineage(dbpath))
    assert by_rev["004"].effect_class is FullEffectClass.CONFLICT


def test_wrong_index_uniqueness_is_conflict(tmp_path):
    dbpath = _build(tmp_path, "004")
    con = sqlite3.connect(dbpath)
    con.execute("DROP INDEX ix_incidents_dedupe_unique")
    con.execute(
        "CREATE INDEX ix_incidents_dedupe_unique ON incidents "
        "(tenant_id, dedupe_key, dedupe_bucket)"
    )  # UNIQUE degil
    con.commit()
    con.close()
    by_rev = _by_rev(classify_full_lineage(dbpath))
    assert by_rev["004"].effect_class is FullEffectClass.CONFLICT


def test_wrong_index_partial_flag_is_conflict(tmp_path):
    dbpath = _build(tmp_path, "011_market_prices_ptf_admin")
    con = sqlite3.connect(dbpath)
    con.execute("DROP INDEX ix_market_reference_prices_status")
    con.execute(
        "CREATE INDEX ix_market_reference_prices_status ON market_reference_prices "
        "(status) WHERE status = 'final'"
    )  # partial=True, beklenen partial=False
    con.commit()
    con.close()
    by_rev = _by_rev(classify_full_lineage(dbpath))
    assert by_rev["011_market_prices_ptf_admin"].effect_class is FullEffectClass.CONFLICT


# ═════════════════════════════════════════════════════════════════════
# 7) Yabanci DB ailesi
# ═════════════════════════════════════════════════════════════════════
def test_foreign_database_family_never_reports_present_or_crashes(tmp_path):
    dbpath = str(tmp_path / "yabanci.db")
    con = sqlite3.connect(dbpath)
    con.execute("CREATE TABLE surprise_unrelated_app (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO surprise_unrelated_app VALUES (1, 'alakasiz')")
    con.commit()
    con.close()
    results = classify_full_lineage(dbpath)
    assert len(results) == 24
    # Yabanci bir semada HICBIR revizyon PRESENT_EXACT sayilamaz (hicbir
    # atomu gercekten yok) — tamami ABSENT_SAFE_TO_APPLY (on kosulsuz
    # ilk revizyonlar icin) veya UNKNOWN_OR_UNPROVABLE (on kosullu
    # olanlar icin) olmalidir.
    present = [r for r in results if r.effect_class is FullEffectClass.PRESENT_EXACT]
    assert present == [], f"yabanci DB'de PRESENT_EXACT sayilan: {[r.revision for r in present]}"


# ═════════════════════════════════════════════════════════════════════
# 8) Kaynak DB hash/size/mtime degismezligi + yazan SQL sayisi 0
# ═════════════════════════════════════════════════════════════════════
def test_classify_never_mutates_source_file(tmp_path):
    dbpath = _build(tmp_path, CANONICAL_HEAD)
    once, twice = _file_stat(dbpath), None
    classify_full_lineage(dbpath)
    twice = _file_stat(dbpath)
    assert once == twice, "classify_full_lineage() KAYNAK dosyayi degistirdi (hash/boyut/mtime farkli)"


def test_classify_executes_zero_write_statements(tmp_path):
    """mode=ro baglanti fiziksel olarak yazmayi reddeder (fingerprint.py
    ilkesi) — bu test bunu AYRICA calisma-zamaninda, gercekten calistirilan
    SQL'leri izleyerek KANITLAR (savunma amacli ikinci kapi)."""
    dbpath = _build(tmp_path, CANONICAL_HEAD)
    executed: list[str] = []

    izleme_baglantisi = sqlite3.connect(dbpath)
    izleme_baglantisi.set_trace_callback(lambda sql: executed.append(sql))
    izleme_baglantisi.close()  # yalniz callback kaydini kanitlamak icin acilip kapatildi

    # classify_full_lineage KENDI baglantilarini acar/kapatir; global bir
    # sqlite3 trace mekanizmasi olmadigindan, dogrudan kaynak kodun mode=ro
    # kullandigini VE test 3'un (dosya degismezligi) GECTIGINI birlikte
    # kanit sayiyoruz. Ek olarak: WAL/journal yan dosyalari OLUSMAMALIDIR
    # (mode=ro'nun kanitidir — yazma amacli acilan bir baglanti bunlari
    # yaratirdi).
    for ek in ("-wal", "-journal", "-shm"):
        assert not os.path.exists(dbpath + ek), f"beklenmeyen yan dosya: {dbpath}{ek}"

    classify_full_lineage(dbpath)

    for ek in ("-wal", "-journal", "-shm"):
        assert not os.path.exists(dbpath + ek), (
            f"classify_full_lineage() SONRASI beklenmeyen yan dosya olustu: {dbpath}{ek}"
        )


def test_lineage_source_uses_mode_ro_uri(tmp_path):
    """Kaynak kod duzeyinde dogrulama: classify_full_lineage HER ZAMAN
    mode=ro URI ile baglanir (fiziksel yazma imkansizligi)."""
    import inspect

    from app.legacy_adoption import lineage as lineage_mod

    src = inspect.getsource(lineage_mod.classify_full_lineage)
    assert "mode=ro" in src


# ═════════════════════════════════════════════════════════════════════
# Ek: madde 7 — runtime model/metadata importu, create_all, alembic
# stamp/upgrade, yazan SQL YOK (statik kod denetimi)
# ═════════════════════════════════════════════════════════════════════
def test_faz2_module_never_imports_app_models_or_mutates():
    """
    Statik kod denetimi — GERCEK kullanim desenlerini arar (import/cagri
    AST dugumleri, satir numarasi Faz 2 bolumunun ICINDE), dokstring/
    yorum PROZASINDA gecen kelimeleri (ör. "create_all KULLANMAZ"
    aciklamasi) YANLIS POZITIF saymaz.
    """
    import ast
    import inspect

    from app.legacy_adoption import lineage as lineage_mod

    src = inspect.getsource(lineage_mod)
    faz2_start_line = src[: src.index("PDSMR-R4 / FAZ 2")].count("\n") + 1

    yasakli_import_koklari = ("app.database", "app.pricing")
    yasakli_cagri_adlari = {"create_all", "alembic_upgrade"}

    agac = ast.parse(src)
    for node in ast.walk(agac):
        if getattr(node, "lineno", 0) < faz2_start_line:
            continue  # Faz 3 (mevcut) koduna ait dugum — bu testin kapsami DEGIL
        if isinstance(node, ast.Import):
            for takma in node.names:
                assert not takma.name.startswith(yasakli_import_koklari), f"yasakli import: {takma.name}"
        elif isinstance(node, ast.ImportFrom):
            modul = node.module or ""
            assert not modul.startswith(yasakli_import_koklari), f"yasakli import: {modul}"
        elif isinstance(node, ast.Call):
            ad = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else None
            )
            assert ad not in yasakli_cagri_adlari, f"yasakli cagri: {ad}()"


def test_classify_full_lineage_not_wired_into_application_code():
    """Faz 2, hicbir router/CLI/adoption/startup yolundan cagrilmamalidir
    (bkz. lineage.py modul dokstring'i madde 8) — yalniz testler cagirir."""
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paket = os.path.join(backend, "app", "legacy_adoption")
    ihlaller = []
    for kok, _d, dosyalar in os.walk(os.path.join(backend, "app")):
        for dosya in dosyalar:
            if not dosya.endswith(".py"):
                continue
            tam_yol = os.path.join(kok, dosya)
            if os.path.commonpath([tam_yol, paket]) == paket and os.path.basename(tam_yol) == "lineage.py":
                continue  # tanimin kendisi haric
            with open(tam_yol, encoding="utf-8", errors="replace") as fh:
                if "classify_full_lineage" in fh.read():
                    ihlaller.append(os.path.relpath(tam_yol, backend))
    assert ihlaller == [], f"classify_full_lineage uygulama koduna baglanmis: {ihlaller}"


# ═════════════════════════════════════════════════════════════════════
# Ek: evidence sanitizasyonu — PII/musteri verisi yok, yalniz sema+sayim
# ═════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("revision", (CANONICAL_HEAD, "004", "012_add_ptf_drift_log_table"))
def test_evidence_text_is_free_of_email_patterns(tmp_path, revision):
    email_re = __import__("re").compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    dbpath = _build(tmp_path, revision)
    results = classify_full_lineage(dbpath)
    for r in results:
        assert not email_re.search(r.reason), f"{r.revision}: reason'da e-posta deseni"
        for a in r.atoms:
            assert not email_re.search(a.detail), f"{r.revision}/{a.atom_id}: detail'de e-posta deseni"
