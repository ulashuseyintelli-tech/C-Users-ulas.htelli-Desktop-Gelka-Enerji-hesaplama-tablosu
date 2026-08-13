"""
PDSMR-R1D / FAZ 3 — kontrollu legacy adoption testleri.

Kanitlanacak sey tek cumleyle: adoption YALNIZ dogrulanmis bir disposable
kopyada, YALNIZ etkisi kanitlanmis revizyonlari isaretleyerek calisir; her
belirsizlikte fail-closed durur ve kaynak/yedek dosyalara ASLA dokunmaz.

Kesinti (fault) senaryolari kritik: yarida kalan bir adoption, DB'yi
"adopt edilmis" gibi gosteren bir durumda BIRAKMAMALIDIR.
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.legacy_adoption import policy  # noqa: E402
from app.legacy_adoption.adoption import (  # noqa: E402
    AUDIT_SUFFIX,
    FAULT_POINTS,
    AdoptionRefused,
    InjectedFault,
    adopt_legacy_copy,
    read_audit,
)
from app.legacy_adoption.lineage import (  # noqa: E402
    CANONICAL_HEAD,
    PRODUCTION_BRANCH_TIP,
    EffectClass,
    classify_canonical_branch,
)
from test_legacy_adoption_validator import (  # noqa: E402
    _build_golden_legacy_db,
    _rewrite_table_sql,
    _sha256,
)

_CANONICAL_INDEXES = policy.REQUIRED_CANONICAL_INDEXES["incidents"]


# ─────────────────────────────────────────────────────────────────────────
# Fixture: uretim ailesinin pre-adoption esdegeri
# ─────────────────────────────────────────────────────────────────────────
def _make_pre_adoption_family(path: str) -> str:
    """
    Uretimin olculmus halini birebir taklit eder:
      - pre-S5 (outreach tablolari ve verified_legal_type* YOK)
      - incidents'in iki kolonu sapmis (REAL / TEXT)
      - 004'un alti index'i YOK
      - ix_market_reference_prices_period YOK
      - alembic_version = 013_extend_ptf_drift_severity
    """
    _build_golden_legacy_db(path)
    con = sqlite3.connect(path)
    try:
        for spec in _CANONICAL_INDEXES:
            con.execute(f"DROP INDEX IF EXISTS {spec['name']}")
        con.execute("DROP INDEX IF EXISTS ix_market_reference_prices_period")
        con.commit()
    finally:
        con.close()
    _rewrite_table_sql(path, "incidents", "dedupe_bucket INTEGER", "dedupe_bucket TEXT")
    _rewrite_table_sql(path, "incidents", "deduction_total INTEGER", "deduction_total REAL")
    # Sema degistikten SONRA degerleri uretimdeki olculmus hale getir:
    # dedupe_bucket tamamen NULL, deduction_total tam-degerli real.
    # Aksi halde eski INTEGER degerler yeni TEXT bildirimiyle celisir ve
    # integrity_check "NUMERIC value in incidents.dedupe_bucket" der.
    con = sqlite3.connect(path)
    try:
        con.execute("UPDATE incidents SET dedupe_bucket = NULL, deduction_total = 7.0")
        con.commit()
    finally:
        con.close()
    return path


@pytest.fixture(scope="module")
def pre_adoption_master(tmp_path_factory) -> str:
    return _make_pre_adoption_family(
        str(tmp_path_factory.mktemp("p3") / "pre_adoption.db")
    )


@pytest.fixture()
def arena(pre_adoption_master, tmp_path):
    """source / rollback / working uclusunu kurar."""
    source = str(tmp_path / "immutable_source.db")
    rollback = str(tmp_path / "rollback_backup.db")
    working = str(tmp_path / "working_copy.db")
    for hedef in (source, rollback, working):
        shutil.copyfile(pre_adoption_master, hedef)
    return {
        "source": source, "rollback": rollback, "working": working,
        "sha": _sha256(source), "dir": str(tmp_path),
    }


def _adopt(arena, **kw):
    return adopt_legacy_copy(
        arena["working"],
        source_path=arena["source"],
        rollback_path=arena["rollback"],
        expected_source_sha256=arena["sha"],
        confirm_disposable_copy=True,
        **kw,
    )


def _revisions(path):
    con = sqlite3.connect(path)
    try:
        return tuple(sorted(
            r[0] for r in con.execute("SELECT version_num FROM alembic_version").fetchall()
        ))
    finally:
        con.close()


def _tables(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    finally:
        con.close()


def _assert_untouched(arena):
    assert _sha256(arena["source"]) == arena["sha"], "KAYNAK degisti"
    assert _sha256(arena["rollback"]) == arena["sha"], "ROLLBACK degisti"


# ─────────────────────────────────────────────────────────────────────────
# ADIM 3 — siniflandirma
# ─────────────────────────────────────────────────────────────────────────
def test_classification_matches_measured_production_profile(arena):
    siniflar = {c.revision: c.effect_class for c in classify_canonical_branch(arena["working"])}
    assert siniflar["a93beeaddf82"] is EffectClass.EXACT_EFFECT_ALREADY_PRESENT
    assert siniflar["dc8343278cfa"] is EffectClass.EXACT_EFFECT_ALREADY_PRESENT
    assert siniflar["8b9a332a3680"] is EffectClass.EXACT_EFFECT_ALREADY_PRESENT
    assert siniflar["e340ce40c05c"] is EffectClass.EXACT_EFFECT_ALREADY_PRESENT
    assert siniflar["7b3e1c8a52df"] is EffectClass.EXACT_EFFECT_ALREADY_PRESENT
    assert siniflar["f4e7efc70c80"] is EffectClass.EFFECT_MISSING_AND_SAFE_TO_RUN
    assert siniflar["beda29569b0d"] is EffectClass.EFFECT_MISSING_AND_SAFE_TO_RUN
    assert siniflar["9d4a2f6b18ce"] is EffectClass.EFFECT_MISSING_AND_SAFE_TO_RUN


def test_table_name_alone_is_not_treated_as_equivalence(arena):
    """
    prospect_companies TABLOSU var ama beda29569b0d'nin KOLONLARI yok.
    Tablo adina bakan bir siniflandirma bunu yanlislikla A sayardi.
    """
    siniflar = {c.revision: c for c in classify_canonical_branch(arena["working"])}
    assert "prospect_companies" in _tables(arena["working"])
    assert siniflar["beda29569b0d"].effect_class is EffectClass.EFFECT_MISSING_AND_SAFE_TO_RUN


def test_partially_present_effect_is_conflict_not_safe_to_run(arena):
    """Pricing tablolarinin BIR KISMI silinirse -> D (CONFLICT), C degil."""
    con = sqlite3.connect(arena["working"])
    con.execute("DROP TABLE analysis_cache")
    con.commit()
    con.close()
    siniflar = {c.revision: c for c in classify_canonical_branch(arena["working"])}
    hedef = siniflar["7b3e1c8a52df"]
    assert hedef.effect_class is EffectClass.CONFLICT_OR_UNKNOWN
    assert "zaten var" in (hedef.conflict or "")


# ─────────────────────────────────────────────────────────────────────────
# POZITIF — tam adoption
# ─────────────────────────────────────────────────────────────────────────
def test_full_adoption_reaches_canonical_head(arena):
    rapor = _adopt(arena)
    assert rapor.outcome == "ADOPTED"
    assert rapor.terminal_revision == CANONICAL_HEAD
    assert rapor.heads == 1
    assert _revisions(arena["working"]) == (CANONICAL_HEAD,)
    assert rapor.integrity_check == "ok"
    assert rapor.foreign_key_violations == 0
    _assert_untouched(arena)


def test_only_A_revisions_are_stamped_and_only_C_revisions_are_run(arena):
    rapor = _adopt(arena)
    assert set(rapor.stamped) == {
        "a93beeaddf82", "dc8343278cfa", "8b9a332a3680", "e340ce40c05c", "7b3e1c8a52df",
    }
    assert set(rapor.migrated) == {
        "f4e7efc70c80", "beda29569b0d", "9d4a2f6b18ce", CANONICAL_HEAD,
    }
    # C revizyonlari ASLA stamp edilmemeli
    assert not (set(rapor.stamped) & set(rapor.migrated))


def test_adoption_creates_s5_objects_through_alembic_not_create_all(arena):
    _adopt(arena)
    assert policy.EXPECTED_ABSENT_MODEL_TABLES.issubset(_tables(arena["working"]))
    con = sqlite3.connect(arena["working"])
    kolonlar = {r[1] for r in con.execute("PRAGMA table_info(prospect_companies)").fetchall()}
    con.close()
    assert {c for _t, c in policy.EXPECTED_ABSENT_MODEL_COLUMNS}.issubset(kolonlar)


def test_adoption_installs_six_canonical_indexes_and_canonical_affinity(arena):
    _adopt(arena)
    con = sqlite3.connect(arena["working"])
    idx = {r[1] for r in con.execute("PRAGMA index_list(incidents)").fetchall()}
    tipler = {r[1]: r[2] for r in con.execute("PRAGMA table_info(incidents)").fetchall()
              if r[1] in ("dedupe_bucket", "deduction_total")}
    con.close()
    assert {s["name"] for s in _CANONICAL_INDEXES}.issubset(idx)
    assert tipler == {"dedupe_bucket": "INTEGER", "deduction_total": "INTEGER"}


def test_adoption_preserves_row_counts_and_legacy_artifact(arena):
    rapor = _adopt(arena)
    for tablo, beklenen in policy.EXPECTED_ROW_COUNTS.items():
        assert rapor.row_counts.get(tablo) == beklenen, tablo
    con = sqlite3.connect(arena["working"])
    assert con.execute("SELECT COUNT(*) FROM ptf_drift_log").fetchone()[0] == 0
    con.close()


def test_rollback_copy_still_reports_pre_adoption_state(arena):
    _adopt(arena)
    assert _revisions(arena["rollback"]) == (PRODUCTION_BRANCH_TIP,)
    assert not (policy.EXPECTED_ABSENT_MODEL_TABLES & _tables(arena["rollback"]))


def test_audit_record_is_written_outside_the_database(arena):
    _adopt(arena)
    yol = arena["working"] + AUDIT_SUFFIX
    assert os.path.isfile(yol)
    kayit = read_audit(arena["working"])
    assert kayit["terminal_revision"] == CANONICAL_HEAD
    metin = json.dumps(kayit)
    assert "password" not in metin.lower() and "@" not in metin


# ─────────────────────────────────────────────────────────────────────────
# NEGATIF — on-kosullar
# ─────────────────────────────────────────────────────────────────────────
def test_missing_confirmation_refuses(arena):
    with pytest.raises(AdoptionRefused, match="confirm_disposable_copy"):
        adopt_legacy_copy(
            arena["working"], source_path=arena["source"], rollback_path=arena["rollback"],
            expected_source_sha256=arena["sha"],
        )
    _assert_untouched(arena)


def test_source_fingerprint_drift_refuses(arena):
    with pytest.raises(AdoptionRefused, match="parmak izi"):
        _adopt({**arena, "sha": "0" * 64})
    assert _revisions(arena["working"]) == (PRODUCTION_BRANCH_TIP,)
    _assert_untouched(arena)


def test_rollback_not_matching_source_refuses(arena):
    con = sqlite3.connect(arena["rollback"])
    con.execute("CREATE TABLE bozucu (id INTEGER)")
    con.commit()
    con.close()
    with pytest.raises(AdoptionRefused, match="rollback"):
        _adopt(arena)


@pytest.mark.parametrize("parca", ["Programs\\Gelka Enerji\\resources", "Program Files\\X"])
def test_installed_application_path_is_refused(arena, tmp_path, parca):
    sahte = tmp_path / parca.replace("\\", os.sep)
    sahte.mkdir(parents=True)
    hedef = str(sahte / "gelka_enerji.db")
    shutil.copyfile(arena["working"], hedef)
    with pytest.raises(AdoptionRefused, match="kurulu uygulama"):
        adopt_legacy_copy(
            hedef, source_path=arena["source"], rollback_path=arena["rollback"],
            expected_source_sha256=arena["sha"], confirm_disposable_copy=True,
        )


def test_source_equals_working_target_is_refused(arena):
    with pytest.raises(AdoptionRefused, match="KAYNAK ile ayni"):
        adopt_legacy_copy(
            arena["source"], source_path=arena["source"], rollback_path=arena["rollback"],
            expected_source_sha256=arena["sha"], confirm_disposable_copy=True,
        )
    _assert_untouched(arena)


def test_backup_equals_working_target_is_refused(arena):
    with pytest.raises(AdoptionRefused, match="ROLLBACK"):
        adopt_legacy_copy(
            arena["rollback"], source_path=arena["source"], rollback_path=arena["rollback"],
            expected_source_sha256=arena["sha"], confirm_disposable_copy=True,
        )
    _assert_untouched(arena)


def test_relative_and_case_alias_of_source_is_refused(arena, monkeypatch):
    """Ayni dosyaya farkli METINLE isaret etmek korumayi atlatmamali."""
    monkeypatch.chdir(arena["dir"])
    takma = os.path.join(".", os.path.basename(arena["source"]).upper())
    with pytest.raises(AdoptionRefused, match="ayni dosya"):
        adopt_legacy_copy(
            takma, source_path=arena["source"], rollback_path=arena["rollback"],
            expected_source_sha256=arena["sha"], confirm_disposable_copy=True,
        )
    _assert_untouched(arena)


def test_symlink_alias_of_source_is_refused(arena, tmp_path):
    bag = str(tmp_path / "source_link.db")
    try:
        os.symlink(arena["source"], bag)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("bu ortamda sembolik bag olusturulamiyor")
    with pytest.raises(AdoptionRefused, match="ayni dosya"):
        adopt_legacy_copy(
            bag, source_path=arena["source"], rollback_path=arena["rollback"],
            expected_source_sha256=arena["sha"], confirm_disposable_copy=True,
        )
    _assert_untouched(arena)


def test_unresolvable_parent_directory_is_refused(arena, tmp_path):
    hedef = str(tmp_path / "olmayan" / "alt" / "x.db")
    with pytest.raises(AdoptionRefused):
        adopt_legacy_copy(
            hedef, source_path=arena["source"], rollback_path=arena["rollback"],
            expected_source_sha256=arena["sha"], confirm_disposable_copy=True,
        )


def test_missing_rollback_file_is_refused(arena):
    os.remove(arena["rollback"])
    with pytest.raises(AdoptionRefused, match="rollback dosyasi yok"):
        _adopt(arena)


def test_foreign_key_violation_in_working_copy_is_refused(arena):
    con = sqlite3.connect(arena["working"])
    con.execute("UPDATE offers SET customer_id=999999 WHERE id=(SELECT MIN(id) FROM offers)")
    con.commit()
    con.close()
    with pytest.raises(AdoptionRefused, match="FK ihlali"):
        _adopt(arena)


def test_wrong_pre_adoption_revision_is_refused(arena):
    con = sqlite3.connect(arena["working"])
    con.execute("UPDATE alembic_version SET version_num='000_bilinmeyen'")
    con.commit()
    con.close()
    with pytest.raises(AdoptionRefused, match="pre-adoption revizyonu"):
        _adopt(arena)


def test_lossy_incidents_conversion_is_refused_before_any_mutation(arena):
    con = sqlite3.connect(arena["working"])
    con.execute("UPDATE incidents SET deduction_total = 7.5")
    con.commit()
    con.close()
    once = _sha256(arena["working"])
    with pytest.raises(Exception) as hata:
        _adopt(arena)
    assert "DEGISTIRIRDI" in str(hata.value)
    assert _sha256(arena["working"]) == once, "reddedilen adoption DB'yi degistirdi"
    assert _revisions(arena["working"]) == (PRODUCTION_BRANCH_TIP,)


def test_tampered_schema_hard_stops_before_any_lineage_change(arena):
    """
    Beklenen tablo kumesinden sapan bir hedef, lineage'e DOKUNULMADAN
    reddedilir. (Aile kapisi etki-catismasi kapisindan ONCE calisir;
    ikisi de fail-closed'dir, sonuc ayni: mutasyon yok.)
    """
    con = sqlite3.connect(arena["working"])
    con.execute("DROP TABLE analysis_cache")
    con.commit()
    con.close()
    with pytest.raises(AdoptionRefused):
        _adopt(arena)
    assert _revisions(arena["working"]) == (PRODUCTION_BRANCH_TIP,), \
        "sapmaya ragmen lineage degistirildi"


def test_revision_effect_conflict_hard_stops_before_lineage_change(arena, monkeypatch):
    """
    D (CONFLICT_OR_UNKNOWN) siniflandirmasi adoption'i durdurmalidir.

    Aile kapisi tablo kumesini zaten kilitledigi icin bu durum gercek bir
    DB uzerinde uretilemez; siniflandirici dogrudan degistirilerek D
    dalinin BAGLI oldugu kanitlanir.
    """
    from app.legacy_adoption import adoption as adoption_mod
    from app.legacy_adoption.lineage import RevisionClassification

    gercek = adoption_mod.classify_canonical_branch

    def sahte(db_path):
        siniflar = list(gercek(db_path))
        siniflar[6] = RevisionClassification(
            siniflar[6].revision, siniflar[6].summary,
            EffectClass.CONFLICT_OR_UNKNOWN, siniflar[6].evidence,
            "sentetik catisma",
        )
        return tuple(siniflar)

    monkeypatch.setattr(adoption_mod, "classify_canonical_branch", sahte)
    with pytest.raises(AdoptionRefused, match="etki catismasi"):
        _adopt(arena)
    assert _revisions(arena["working"]) == (PRODUCTION_BRANCH_TIP,), \
        "catismaya ragmen lineage degistirildi"
    _assert_untouched(arena)


def test_genuinely_missing_revision_is_never_marked_applied(arena):
    """
    C revizyonlari (outreach / legal type / period index) yalnizca
    CALISTIRILARAK uygulanir; isaretlenerek DEGIL.
    """
    rapor = _adopt(arena)
    for eksik in ("f4e7efc70c80", "beda29569b0d", "9d4a2f6b18ce"):
        assert eksik not in rapor.stamped
        assert eksik in rapor.migrated


def test_no_general_purpose_stamp_or_arbitrary_revision_api_is_exposed():
    import inspect

    from app.legacy_adoption import adoption

    for ad in adoption.__all__:
        nesne = getattr(adoption, ad)
        if not inspect.isfunction(nesne):
            continue
        parametreler = set(inspect.signature(nesne).parameters)
        assert not (parametreler & {"revision", "rev", "stamp", "target_revision"}), \
            f"{ad} serbest revizyon argumani aliyor"


# ─────────────────────────────────────────────────────────────────────────
# KESINTI (fault injection)
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("nokta", list(FAULT_POINTS))
def test_interruption_never_leaves_a_falsely_adopted_database(arena, nokta):
    """
    En tehlikeli hata bicimi: yarida kalan adoption'in DB'yi "adopt
    edilmis" gibi gostermesi. Hangi noktada kesilirse kesilsin, DB ya
    pre-adoption'da kalir ya da tam canonical olur — arada ASLA.
    """
    with pytest.raises(InjectedFault):
        _adopt(arena, fault_at=nokta)

    revizyonlar = _revisions(arena["working"])
    assert revizyonlar != (CANONICAL_HEAD,), f"{nokta}: DB yanlislikla adopt gorunuyor"
    assert not os.path.isfile(arena["working"] + AUDIT_SUFFIX), \
        f"{nokta}: yarim adoption audit yazdi"
    _assert_untouched(arena)


@pytest.mark.parametrize("nokta", list(FAULT_POINTS))
def test_rerun_after_interruption_either_resumes_or_fails_closed(arena, nokta):
    with pytest.raises(InjectedFault):
        _adopt(arena, fault_at=nokta)
    try:
        rapor = _adopt(arena)
    except AdoptionRefused:
        # fail-closed: kabul edilebilir sonuc
        assert _revisions(arena["working"]) != (CANONICAL_HEAD,)
        return
    assert rapor.outcome in ("ADOPTED", "ALREADY_ADOPTED")
    assert _revisions(arena["working"]) == (CANONICAL_HEAD,)
    _assert_untouched(arena)


def test_unknown_fault_point_is_refused(arena):
    with pytest.raises(AdoptionRefused, match="fault noktasi"):
        _adopt(arena, fault_at="olmayan_nokta")


# ─────────────────────────────────────────────────────────────────────────
# IDEMPOTENTLIK
# ─────────────────────────────────────────────────────────────────────────
def test_second_and_third_run_are_exact_no_ops(arena):
    assert _adopt(arena).outcome == "ADOPTED"
    h1 = _sha256(arena["working"])
    a1 = _sha256(arena["working"] + AUDIT_SUFFIX)

    r2 = _adopt(arena)
    assert r2.outcome == "ALREADY_ADOPTED"
    assert _sha256(arena["working"]) == h1
    assert _sha256(arena["working"] + AUDIT_SUFFIX) == a1

    r3 = _adopt(arena)
    assert r3.outcome == "ALREADY_ADOPTED"
    assert _sha256(arena["working"]) == h1
    assert _revisions(arena["working"]) == (CANONICAL_HEAD,)
    assert sorted(os.listdir(arena["dir"])) == sorted([
        os.path.basename(arena["source"]),
        os.path.basename(arena["rollback"]),
        os.path.basename(arena["working"]),
        os.path.basename(arena["working"]) + AUDIT_SUFFIX,
    ]), "yedek/audit cogalmasi"


def test_tampered_audit_does_not_trigger_reprocessing_when_db_is_certifiable(arena):
    """
    Audit bozulsa bile DB bagimsiz olarak canonical sertifikalanabiliyorsa
    ikinci kosu yine no-op olmalidir — audit'e KORU KORUNE guvenilmez,
    ama DB'yi ikinci kez donusturmek de dogru degildir.
    """
    _adopt(arena)
    h1 = _sha256(arena["working"])
    with open(arena["working"] + AUDIT_SUFFIX, "w", encoding="utf-8") as fh:
        fh.write('{"audit": {"outcome": "SAHTE"}, "audit_sha256": "bozuk"}')
    assert read_audit(arena["working"]) is None
    rapor = _adopt(arena)
    assert rapor.outcome == "ALREADY_ADOPTED"
    assert _sha256(arena["working"]) == h1


def test_unrelated_canonical_database_is_not_treated_as_this_legacy_family(arena, tmp_path):
    """Bos/yabanci bir DB bu legacy ailesi sanilmamalidir."""
    yabanci = str(tmp_path / "yabanci.db")
    con = sqlite3.connect(yabanci)
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    con.execute("INSERT INTO alembic_version VALUES ('013_extend_ptf_drift_severity')")
    con.execute("CREATE TABLE surprise (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    with pytest.raises(AdoptionRefused):
        adopt_legacy_copy(
            yabanci, source_path=arena["source"], rollback_path=arena["rollback"],
            expected_source_sha256=arena["sha"], confirm_disposable_copy=True,
        )


def test_adoption_is_not_wired_into_application_code():
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
    assert ihlaller == [], f"Faz 3 uygulama koduna baglanmis: {ihlaller}"
