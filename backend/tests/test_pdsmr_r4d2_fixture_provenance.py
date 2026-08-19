"""
PDSMR-R4 / FAZ 4D2 — FIXTURE PROVENANCE + PRODUCTION GUVENLIK AYRIMI.

Iki seyi birden kanitlar:

  1) Sentetik legacy fixture DETERMINISTIK, SIFIRDAN uretilmis ve
     PII/gercek-veri ICERMEZ — yani 4B2/4C1 paketlerinin canli production
     parmak izine bagimliligi KALICI olarak kalkti.

  2) Bu kolaylik URETIM GUVENLIGINI GEVSETMEDI: sentetik fixture bir
     production hedefi olarak KABUL EDILMEZ, test kimligi production
     yetkisinin yerine GECMEZ ve test-only yardimcilar runtime import
     grafiginde BULUNMAZ.

Cagrildigi yerler:
- pytest (CI/yerel regresyon)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # kardes test modulleri

import pdsmr_r4d2_legacy_fixture as LF  # noqa: E402
from app.legacy_adoption import production_adoption_controller as C  # noqa: E402
from app.legacy_adoption.unversioned_adoption import (  # noqa: E402
    FAULT_POINTS,
    plan_rebuild_tables,
    rebuild_fault_points,
)

# Tarihsel kimlikler — YALNIZ rehearsal/tarih kaydi olarak anilir, aktif
# production kabul kimligi DEGILDIR (owner Faz 4D2 madde 5).
TARIHSEL_PRE_ADOPTION = "f9a3fb04a96bd167671e6d7dfa6fa77424dd27a448dba2b0cf244a4ef7653219"
CANONICAL_POST_CUTOVER = "338ab934343b660bf2473a13964a16d6612a179615779f4d95222634a549f08d"


@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory) -> str:
    return LF.build_legacy_fixture(str(tmp_path_factory.mktemp("prov") / "legacy.db"))


# ─────────────────────────────────────────────────────────────────────────
# 1) DETERMINIZM + PROVENANCE
# ─────────────────────────────────────────────────────────────────────────
def test_fixture_creation_succeeds(fixture_db):
    assert os.path.isfile(fixture_db)
    assert os.path.getsize(fixture_db) > 0
    butunluk, fk = C.health(fixture_db)
    assert butunluk == "ok" and fk == 0


def test_three_builds_produce_identical_structural_fingerprint(tmp_path):
    izler = {
        LF.structural_fingerprint(
            LF.build_legacy_fixture(str(tmp_path / ("b%d.db" % i))))
        for i in range(3)
    }
    assert len(izler) == 1, "fixture DETERMINISTIK degil: " + str(izler)


def test_fixture_is_not_derived_from_production_or_recovery(fixture_db):
    """
    Fixture'in parmak izi ne production ne de recovery kimligiyle esit
    OLMAMALIDIR — sifirdan uretildiginin dogrudan kaniti.
    """
    h = LF.sha256_of(fixture_db)
    assert h != TARIHSEL_PRE_ADOPTION, "fixture recovery kopyasindan turetilmis!"
    assert h != CANONICAL_POST_CUTOVER, "fixture production'dan turetilmis!"


def test_fixture_contains_only_synthetic_data(fixture_db):
    """
    Butun metin sutunlari taranir: gercek musteri/teklif degeri, gercek
    e-posta alan adi veya secret BULUNMAMALIDIR.
    """
    con = sqlite3.connect("file:" + fixture_db.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        tablolar = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        degerler: list[str] = []
        for t in tablolar:
            kolonlar = [r[1] for r in con.execute('PRAGMA table_info("%s")' % t)]
            for k in kolonlar:
                for (v,) in con.execute(
                        'SELECT "%s" FROM "%s" WHERE typeof("%s")=\'text\'' % (k, t, k)):
                    degerler.append(v)
    finally:
        con.close()
    assert degerler, "fixture'da hic metin verisi yok — sentetik satirlar eksik"
    blob = " ".join(degerler).lower()
    for yasak in ("password", "secret", "api_key", "token", "bearer"):
        assert yasak not in blob, "fixture'da yasakli ifade: " + yasak
    # E-posta varsa YALNIZ gecersiz sentetik alan adi olabilir.
    for v in degerler:
        if "@" in v:
            assert v.endswith("@ornek.gecersiz") or ".gecersiz" in v, (
                "fixture'da gercek gorunumlu e-posta: " + v)


def test_fixture_has_no_residual_free_page_data(fixture_db):
    """
    Fixture sifirdan CREATE+INSERT ile uretildigi icin silinmis satirdan
    kalan free-page kalintisi OLAMAZ. `PRAGMA freelist_count` bunu olcer.
    """
    con = sqlite3.connect("file:" + fixture_db.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        assert con.execute("PRAGMA freelist_count").fetchone()[0] == 0
    finally:
        con.close()


# ─────────────────────────────────────────────────────────────────────────
# 2) LEGACY GERCEGINI TEMSIL ETME
# ─────────────────────────────────────────────────────────────────────────
def test_fixture_is_unversioned(fixture_db):
    con = sqlite3.connect("file:" + fixture_db.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        t = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        con.close()
    assert "alembic_version" not in t, "fixture UNVERSIONED olmali"


@pytest.mark.parametrize("tablo", ["ptf_drift_log", "outreach_messages",
                                   "outreach_templates", "suppression_entries"])
def test_fixture_lacks_post_legacy_tables(fixture_db, tablo):
    """012 ve f4e7efc70c80 ONCESI durum."""
    con = sqlite3.connect("file:" + fixture_db.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        t = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert tablo not in t


@pytest.mark.parametrize("kolon", ["verified_legal_type", "verified_legal_type_note",
                                   "verified_legal_type_set_at"])
def test_fixture_lacks_beda_columns(fixture_db, kolon):
    """beda29569b0d ONCESI durum."""
    con = sqlite3.connect("file:" + fixture_db.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        k = {r[1] for r in con.execute("PRAGMA table_info(prospect_companies)")}
    finally:
        con.close()
    assert kolon not in k


def test_fixture_preserves_accepted_updated_by_null_variant(fixture_db):
    con = sqlite3.connect("file:" + fixture_db.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        toplam = con.execute("SELECT COUNT(*) FROM market_reference_prices").fetchone()[0]
        nul = con.execute(
            "SELECT COUNT(*) FROM market_reference_prices WHERE updated_by IS NULL").fetchone()[0]
        sahte = con.execute(
            "SELECT COUNT(*) FROM market_reference_prices "
            "WHERE updated_by = 'system_migration'").fetchone()[0]
    finally:
        con.close()
    assert toplam == LF.BEKLENEN_SATIRLAR["market_reference_prices"]
    assert nul == toplam, "accepted legacy data variant (updated_by NULL) temsil edilmiyor"
    assert sahte == 0


def test_fixture_rows_exercise_pk_fk_relationships(fixture_db):
    con = sqlite3.connect("file:" + fixture_db.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        m = con.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        o = con.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
        bagli = con.execute(
            "SELECT COUNT(*) FROM offers o JOIN customers c ON o.customer_id = c.id").fetchone()[0]
        yetim = len(con.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        con.close()
    assert m == LF.BEKLENEN_SATIRLAR["customers"]
    assert o == LF.BEKLENEN_SATIRLAR["offers"]
    assert bagli == o, "offers satirlari customers'a GERCEKTEN bagli degil"
    assert yetim == 0


def test_fixture_triggers_expected_rebuild_and_fault_coverage(fixture_db, tmp_path):
    """
    Fixture, 4B2'nin dokuz-tablo rebuild gercegini ve 50 fault noktasini
    URETMELIDIR — aksi halde kapsam sessizce daralirdi.
    """
    from app.legacy_adoption.unversioned_adoption import build_canonical_reference
    from app.legacy_adoption.lineage import CANONICAL_HEAD

    ref = build_canonical_reference(str(tmp_path), CANONICAL_HEAD)
    tablolar = plan_rebuild_tables(fixture_db, ref)
    assert len(tablolar) == 9, "beklenen 9 rebuild tablosu, bulunan: " + str(tablolar)
    assert len(FAULT_POINTS) + len(rebuild_fault_points(tablolar)) == 50


# ─────────────────────────────────────────────────────────────────────────
# 3) PRODUCTION GUVENLIGI GEVSEMEDI
# ─────────────────────────────────────────────────────────────────────────
def test_synthetic_fixture_is_rejected_as_production_target(fixture_db, tmp_path):
    """
    Sentetik fixture, production kimlik kapisindan GECEMEZ: beklenen
    production parmak izi ile eslesmedigi icin reddedilir.
    """
    with pytest.raises(C.ControllerRefused, match="SHA-256 sapmasi|boyut sapmasi"):
        C.verify_production_identity(
            fixture_db, expected_sha256=CANONICAL_POST_CUTOVER, expected_size=1675264)


def test_fixture_identity_cannot_substitute_for_production_authorization(fixture_db, tmp_path):
    """
    Fixture'a baglanmis bir manifest, BASKA (production) bir hedef icin
    GECERLI SAYILMAZ.
    """
    m = C.issue_authorization(fixture_db, repository_sha="0" * 40, nonce="n" * 32,
                              issued_at_utc="2026-01-01T00:00:00+00:00",
                              confirm_production_authorization=True)
    baska = str(tmp_path / "baska.db")
    LF.build_legacy_fixture(baska)
    with pytest.raises(C.ControllerRefused, match="BASKA hedefe|BAYAT"):
        C.validate_authorization(m, baska, repository_sha="0" * 40,
                                 ledger_dir=str(tmp_path / "ledger"))


def test_environment_variable_cannot_bypass_production_fingerprint(fixture_db, monkeypatch):
    """
    Ortam degiskeniyle beklenen parmak izi EZILEMEZ — kapi parametreden
    gelir, environment'tan DEGIL.
    """
    monkeypatch.setenv("PDSMR_EXPECTED_SHA256", LF.sha256_of(fixture_db))
    monkeypatch.setenv("GELKA_SKIP_FINGERPRINT_CHECK", "1")
    with pytest.raises(C.ControllerRefused, match="SHA-256 sapmasi|boyut sapmasi"):
        C.verify_production_identity(
            fixture_db, expected_sha256=CANONICAL_POST_CUTOVER, expected_size=1675264)


def test_test_only_fixture_module_is_not_in_runtime_import_graph():
    """Test-only yardimci URETIM kodundan cagrilamaz."""
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ihlaller = []
    for kok, _d, dosyalar in os.walk(os.path.join(backend, "app")):
        for d in dosyalar:
            if d.endswith(".py"):
                with open(os.path.join(kok, d), encoding="utf-8", errors="replace") as fh:
                    if "pdsmr_r4d2_legacy_fixture" in fh.read():
                        ihlaller.append(os.path.relpath(os.path.join(kok, d), backend))
    assert ihlaller == [], "test fixture uygulama koduna baglanmis: " + str(ihlaller)


def test_runtime_source_has_no_test_hooks():
    """
    Uretim kaynak dosyalarinda test-only kanca/bypass BULUNMAMALI.
    """
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yasak = ("GELKA_SKIP_FINGERPRINT", "PDSMR_EXPECTED_SHA256", "SKIP_PRODUCTION_CHECK",
             "PYTEST_CURRENT_TEST")
    ihlaller = []
    for kok, _d, dosyalar in os.walk(os.path.join(backend, "app")):
        for d in dosyalar:
            if not d.endswith(".py"):
                continue
            with open(os.path.join(kok, d), encoding="utf-8", errors="replace") as fh:
                icerik = fh.read()
            for y in yasak:
                if y in icerik:
                    ihlaller.append(os.path.relpath(os.path.join(kok, d), backend) + ":" + y)
    assert ihlaller == [], "uretim kodunda test kancasi: " + str(ihlaller)


def test_canonical_production_db_is_not_usable_as_unversioned_fixture(tmp_path):
    """
    Canonical (post-cutover) production DB'si unversioned fixture YERINE
    KULLANILAMAZ — `alembic_version` tasidigi icin motor reddeder.
    """
    from app.legacy_adoption.unversioned_adoption import AdoptionRefused, adopt_unversioned_copy

    kaynak = LF.build_legacy_fixture(str(tmp_path / "src.db"))
    # Fixture'a terminal revizyon ekleyerek "canonical gibi" hale getir.
    calisma = str(tmp_path / "w.db")
    import shutil
    shutil.copyfile(kaynak, calisma)
    con = sqlite3.connect(calisma)
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    con.execute("INSERT INTO alembic_version VALUES ('351d314819d5')")
    con.commit()
    con.close()
    rollback = str(tmp_path / "rb.db")
    shutil.copyfile(kaynak, rollback)
    with pytest.raises(AdoptionRefused, match="UNVERSIONED"):
        adopt_unversioned_copy(
            calisma, source_path=kaynak, rollback_path=rollback,
            canonical_target=str(tmp_path / "out.db"), scratch_dir=str(tmp_path / "refs"),
            expected_source_sha256=LF.sha256_of(kaynak), confirm_disposable_copy=True)


def test_historical_fingerprints_are_documentation_only():
    """
    `f9a3fb04...` ve `338ab934...` bu test paketinde YALNIZ tarihsel/negatif
    referans olarak bulunur; hicbir fixture'in aktif kimligi DEGILDIR.
    """
    yol = os.path.abspath(__file__)
    with open(yol, encoding="utf-8") as fh:
        kaynak = fh.read()
    assert "TARIHSEL_PRE_ADOPTION" in kaynak
    assert "CANONICAL_POST_CUTOVER" in kaynak
    # Fixture modulu bu sabitleri HIC icermemeli.
    fmod = os.path.join(os.path.dirname(yol), "pdsmr_r4d2_legacy_fixture.py")
    with open(fmod, encoding="utf-8") as fh:
        fkaynak = fh.read()
    assert TARIHSEL_PRE_ADOPTION not in fkaynak
    assert CANONICAL_POST_CUTOVER not in fkaynak
