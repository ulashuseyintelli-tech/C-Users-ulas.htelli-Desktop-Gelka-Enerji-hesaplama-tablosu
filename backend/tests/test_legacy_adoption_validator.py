"""
PDSMR-R1D / Faz 2 — legacy adoption validator testleri.

Iki soruyu kanitlar:
  1) Validator, adoption'a UYGUN bir DB'yi PASS eder (yanlis alarm yok).
  2) Validator, her bozulma turunde HARD_STOP eder ve bunu YAPARKEN
     kaynak DB'yi DEGISTIRMEZ, migration CALISTIRMAZ, stamp ATMAZ.

(2)'nin ikinci yarisi kritik: "dogruladim" diyip sessizce yazan bir arac,
hic dogrulamayan bir araçtan daha tehlikelidir. Bu yuzden negatif
senaryolarin HEPSI, dosya SHA-256'sinin degismedigini de dogrular.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.legacy_adoption import policy  # noqa: E402
from app.legacy_adoption.result import (  # noqa: E402
    R_ALEMBIC_REVISION_NOT_ALLOWLISTED,
    R_ALEMBIC_VERSION_MISSING,
    R_BACKUP_TARGET_NOT_WRITABLE,
    R_COLUMN_AFFINITY_DRIFT,
    R_COLUMN_NULLABILITY_DRIFT,
    R_DB_FILE_MISSING,
    R_DB_NOT_SQLITE,
    R_EXPECTED_COLUMN_MISSING,
    R_EXPECTED_TABLE_MISSING,
    R_FORBIDDEN_TABLE_PRESENT,
    R_FOREIGN_KEY_VIOLATIONS,
    R_LEGACY_TABLE_MISSING,
    R_LEGACY_TABLE_NOT_EMPTY,
    R_REQUIRED_INDEX_MISSING,
    R_ROW_COUNT_BASELINE_MISMATCH,
    R_TABLE_COUNT_MISMATCH,
    R_UNEXPECTED_CHECK_CONSTRAINT,
    R_UNEXPECTED_TRIGGER_PRESENT,
    R_UNEXPECTED_VIEW_PRESENT,
    R_UNKNOWN_COLUMN_PRESENT,
    R_UNKNOWN_INDEX_PRESENT,
    R_UNKNOWN_TABLE_PRESENT,
    Outcome,
)
from app.legacy_adoption.validator import (  # noqa: E402
    assert_evidence_sanitized,
    validate_legacy_db,
)


# ─────────────────────────────────────────────────────────────────────────
# Golden fixture: adoption on-kosullarini SAGLAYAN sentetik legacy DB
# ─────────────────────────────────────────────────────────────────────────
def _affinity(t: str) -> str:
    t = (t or "").upper()
    if "INT" in t:
        return "INTEGER"
    if any(k in t for k in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in t or t == "":
        return "BLOB"
    if any(k in t for k in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _dummy_value(table: str, column: str, declared: str, row: int):
    base = (declared or "").upper().split("(")[0].strip()
    if base in ("DATETIME", "TIMESTAMP", "DATE"):
        return f"2026-01-{(row % 28) + 1:02d} 00:00:00"
    if base == "TIME":
        return "00:00:00"
    if base == "BOOLEAN":
        return row % 2
    if base == "JSON":
        return "{}"
    aff = _affinity(declared)
    if aff == "INTEGER":
        return row + 1
    if aff == "REAL":
        return float(row + 1)
    return f"{table}.{column}.{row + 1}"


# Ekleme sirasi: ebeveyn tablolar once (FK butunlugu icin).
_INSERT_ORDER = (
    "customers", "offers", "contracts", "activities", "tasks",
    "prospect_companies", "prospect_sources", "prospect_contacts",
    "incidents", "invoices", "market_reference_prices", "ptf_drift_log",
)


def _fill_table(con: sqlite3.Connection, table: str, count: int, parent_ids: dict) -> list:
    if count == 0:
        return []
    info = con.execute(f"PRAGMA table_info({table})").fetchall()
    fks = {r[3]: (r[2], r[4]) for r in con.execute(f"PRAGMA foreign_key_list({table})").fetchall()}
    ids = []
    for row in range(count):
        cols, vals = [], []
        for _cid, name, ctype, notnull, dflt, pk in info:
            if pk and "INT" in (ctype or "").upper():
                continue  # rowid — SQLite kendisi atar
            if name in fks:
                parent_table, _parent_col = fks[name]
                available = parent_ids.get(parent_table) or []
                if not notnull or not available:
                    continue  # NULL birakilir; foreign_key_check bunu ihlal saymaz
                cols.append(name)
                vals.append(available[row % len(available)])
                continue
            if not notnull and dflt is not None:
                continue
            cols.append(name)
            vals.append(_dummy_value(table, name, ctype, row))
        placeholders = ",".join("?" * len(cols))
        collist = ",".join(f'"{c}"' for c in cols)
        cur = con.execute(f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders})', vals)
        ids.append(cur.lastrowid)
    return ids


def _build_golden_legacy_db(path: str) -> str:
    """
    Production ailesinin sentetik esdegerini uretir:
      - TAM model semasi, ANCAK outreach tablolari YOK (pre-S5)
      - prospect_companies.verified_legal_type* kolonlari YOK (pre-S5)
      - legacy-only tablolar (alembic_version, ptf_drift_log) VAR
      - alembic_version = izinli revision
      - baseline row count'lari birebir

    Sema kaynagi validator ile AYNI fonksiyondur; boylece fixture'in
    modelin bir kismini kacirmasi (ornegin app/pricing/schemas.py)
    mumkun olmaz.
    """
    from sqlalchemy import create_engine

    from app.legacy_adoption.validator import _load_model_metadata

    metadata = _load_model_metadata()
    engine = create_engine(f"sqlite:///{path}")
    tables = [
        t for name, t in metadata.tables.items()
        if name not in policy.EXPECTED_ABSENT_MODEL_TABLES
    ]
    metadata.create_all(engine, tables=tables)
    engine.dispose()

    con = sqlite3.connect(path)
    try:
        for _table, column in sorted(policy.EXPECTED_ABSENT_MODEL_COLUMNS):
            con.execute(f'ALTER TABLE prospect_companies DROP COLUMN "{column}"')

        # Legacy-only tablolar modelde yoktur; minimal stub yeterlidir.
        for legacy in sorted(policy.KNOWN_LEGACY_ONLY_TABLES - {"alembic_version"}):
            con.execute(f'CREATE TABLE IF NOT EXISTS "{legacy}" (id INTEGER PRIMARY KEY)')

        con.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
        con.execute("INSERT INTO alembic_version (version_num) VALUES (?)",
                    (policy.ALLOWED_ALEMBIC_REVISION,))

        parent_ids: dict[str, list] = {}
        for table in _INSERT_ORDER:
            count = policy.EXPECTED_ROW_COUNTS.get(table, 0)
            parent_ids[table] = _fill_table(con, table, count, parent_ids)

        con.commit()
    finally:
        con.close()
    return path


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rewrite_table_sql(path: str, table: str, old: str, new: str) -> None:
    """
    Test amacli sema metni duzenlemesi (SQLite'in ALTER COLUMN eksigini asar).

    schema_version'in artirilmasi ZORUNLUDUR: aksi halde SQLite onbellekteki
    eski semayi gecerli sayar ve dosya "malformed" gorunur.
    """
    con = sqlite3.connect(path)
    try:
        sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        assert sql.count(old) == 1, f"sema parcasi tekil degil ({sql.count(old)} adet): {old}"
        version = con.execute("PRAGMA schema_version").fetchone()[0]
        con.execute("PRAGMA writable_schema=ON")
        con.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?",
            (sql.replace(old, new, 1), table),
        )
        con.execute(f"PRAGMA schema_version={version + 1}")
        con.execute("PRAGMA writable_schema=OFF")
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="module")
def golden_db(tmp_path_factory) -> str:
    return _build_golden_legacy_db(str(tmp_path_factory.mktemp("golden") / "gelka_legacy.db"))


@pytest.fixture()
def mutable_db(golden_db, tmp_path) -> str:
    """Golden DB'nin bozulabilir kopyasi. Orijinal ASLA degistirilmez."""
    target = str(tmp_path / "copy.db")
    shutil.copyfile(golden_db, target)
    return target


def _assert_untouched(path: str, before: str) -> None:
    assert _sha256(path) == before, "validator kaynak DB'yi DEGISTIRDI — salt-okunurluk ihlali"


def _run(path: str, **kw):
    """Dogrulamayi calistirir ve dosyanin degismedigini de kanitlar."""
    before = _sha256(path) if os.path.isfile(path) else None
    report = validate_legacy_db(path, **kw)
    if before is not None:
        _assert_untouched(path, before)
    return report


# ─────────────────────────────────────────────────────────────────────────
# POZITIF senaryolar
# ─────────────────────────────────────────────────────────────────────────
def test_golden_legacy_db_passes(golden_db):
    report = _run(golden_db)
    assert report.outcome is Outcome.PASS, report.reason_codes
    assert report.reason_codes == ()


def test_pass_report_records_expected_absent_s5_columns(golden_db):
    report = _run(golden_db)
    for _table, column in policy.EXPECTED_ABSENT_MODEL_COLUMNS:
        assert any(column in v for v in report.accepted_variants)


def test_validation_is_deterministic_across_runs(golden_db):
    assert _run(golden_db).to_dict() == _run(golden_db).to_dict()


def test_validation_never_writes_to_source_db(golden_db):
    before = _sha256(golden_db)
    mtime = os.path.getmtime(golden_db)
    validate_legacy_db(golden_db)
    assert _sha256(golden_db) == before
    assert os.path.getmtime(golden_db) == mtime


def test_every_connection_is_opened_read_only(golden_db, monkeypatch):
    seen: list[str] = []
    real_connect = sqlite3.connect

    def spy(target, *args, **kwargs):
        seen.append(str(target))
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    _run(golden_db)
    assert seen, "hic baglanti acilmadi"
    assert all("mode=ro" in s for s in seen), seen


def test_evidence_contains_no_secret_or_personal_data(golden_db):
    report = _run(golden_db)
    assert assert_evidence_sanitized(report.to_dict()) == []


def test_non_unique_index_difference_does_not_hard_stop(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("CREATE INDEX ix_probe_customers_email ON customers (email)")
    con.commit()
    con.close()
    report = _run(mutable_db)
    assert report.outcome is Outcome.PASS, report.reason_codes
    assert any("customers" in e for e in report.evidence["non_unique_index_extra"])


# ─────────────────────────────────────────────────────────────────────────
# NEGATIF senaryolar — her biri HARD_STOP, hicbiri kaynagi degistirmez
# ─────────────────────────────────────────────────────────────────────────
def test_missing_db_file_hard_stops(tmp_path):
    report = _run(str(tmp_path / "yok.db"))
    assert report.outcome is Outcome.HARD_STOP
    assert report.reason_codes == (R_DB_FILE_MISSING,)


def test_directory_instead_of_file_hard_stops(tmp_path):
    report = _run(str(tmp_path))
    assert report.reason_codes == (R_DB_FILE_MISSING,)


def test_non_sqlite_file_hard_stops(tmp_path):
    bogus = tmp_path / "bogus.db"
    bogus.write_bytes(b"bu bir sqlite dosyasi degil" * 100)
    report = _run(str(bogus))
    assert report.reason_codes == (R_DB_NOT_SQLITE,)


def test_empty_alembic_version_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("DELETE FROM alembic_version")
    con.commit()
    con.close()
    assert R_ALEMBIC_VERSION_MISSING in _run(mutable_db).reason_codes


def test_wrong_alembic_revision_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("UPDATE alembic_version SET version_num='deadbeefcafe'")
    con.commit()
    con.close()
    report = _run(mutable_db)
    assert R_ALEMBIC_REVISION_NOT_ALLOWLISTED in report.reason_codes
    # Revision DUZELTILMEZ: validator hicbir kosulda stamp atmaz.
    con = sqlite3.connect(mutable_db)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "deadbeefcafe"
    con.close()


def test_foreign_key_violation_hard_stops(mutable_db):
    # sqlite3'te FK zorlamasi varsayilan KAPALI oldugu icin yetim satir
    # uretilebilir; foreign_key_check bunu yakalamalidir.
    con = sqlite3.connect(mutable_db)
    con.execute("UPDATE offers SET customer_id=999999 WHERE id=(SELECT MIN(id) FROM offers)")
    con.commit()
    con.close()
    assert R_FOREIGN_KEY_VIOLATIONS in _run(mutable_db).reason_codes


def test_unknown_extra_table_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("CREATE TABLE surprise_table (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    codes = _run(mutable_db).reason_codes
    assert R_UNKNOWN_TABLE_PRESENT in codes
    assert R_TABLE_COUNT_MISMATCH in codes


def test_outreach_table_present_before_adoption_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("CREATE TABLE outreach_messages (id INTEGER PRIMARY KEY)")
    con.commit()
    con.close()
    assert R_FORBIDDEN_TABLE_PRESENT in _run(mutable_db).reason_codes


def test_expected_table_missing_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("DROP TABLE tasks")
    con.commit()
    con.close()
    assert R_EXPECTED_TABLE_MISSING in _run(mutable_db).reason_codes


def test_legacy_ptf_drift_log_missing_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("DROP TABLE ptf_drift_log")
    con.commit()
    con.close()
    assert R_LEGACY_TABLE_MISSING in _run(mutable_db).reason_codes


def test_legacy_ptf_drift_log_not_empty_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("INSERT INTO ptf_drift_log (id) VALUES (1)")
    con.commit()
    con.close()
    assert R_LEGACY_TABLE_NOT_EMPTY in _run(mutable_db).reason_codes


def test_unknown_extra_column_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("ALTER TABLE customers ADD COLUMN surprise_col TEXT")
    con.commit()
    con.close()
    report = _run(mutable_db)
    assert R_UNKNOWN_COLUMN_PRESENT in report.reason_codes


def test_expected_column_missing_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("ALTER TABLE customers DROP COLUMN email")
    con.commit()
    con.close()
    assert R_EXPECTED_COLUMN_MISSING in _run(mutable_db).reason_codes


def test_column_affinity_drift_hard_stops(mutable_db):
    # Modelde metin olan bir kolon DB'de sayisal olursa karsilastirmalar
    # sessizce yanlis sonuc verir; bu drift gorulmelidir.
    _rewrite_table_sql(mutable_db, "customers", "name VARCHAR(255)", "name REAL")
    assert R_COLUMN_AFFINITY_DRIFT in _run(mutable_db).reason_codes


def test_non_allowlisted_nullability_drift_hard_stops(mutable_db):
    # customers.email modelde nullable; DB'de NOT NULL olursa mevcut akislar
    # kirilir. Bu allowlist'te OLMAYAN bir sapmadir.
    _rewrite_table_sql(mutable_db, "customers", "email VARCHAR(255)", "email VARCHAR(255) NOT NULL")
    codes = _run(mutable_db).reason_codes
    assert R_COLUMN_NULLABILITY_DRIFT in codes


def test_allowlisted_nullability_variant_does_not_hard_stop(mutable_db):
    # market_reference_prices.captured_at owner tarafindan KABUL EDILMIS varyant.
    _rewrite_table_sql(
        mutable_db, "market_reference_prices", "captured_at DATETIME NOT NULL", "captured_at DATETIME"
    )
    report = _run(mutable_db)
    assert R_COLUMN_NULLABILITY_DRIFT not in report.reason_codes
    assert any("captured_at" in v for v in report.accepted_variants)


def test_required_unique_index_missing_hard_stops(mutable_db):
    # contracts.contract_number benzersizligi VERI BUTUNLUGU tasir:
    # kaybolursa ayni sozlesme numarasi iki kez uretilebilir.
    con = sqlite3.connect(mutable_db)
    con.execute("DROP INDEX ix_contracts_contract_number")
    con.commit()
    con.close()
    assert R_REQUIRED_INDEX_MISSING in _run(mutable_db).reason_codes


def test_unknown_unique_index_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("CREATE UNIQUE INDEX ix_surprise_unique ON customers (name)")
    con.commit()
    con.close()
    assert R_UNKNOWN_INDEX_PRESENT in _run(mutable_db).reason_codes


def test_unexpected_view_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("CREATE VIEW v_surprise AS SELECT id FROM customers")
    con.commit()
    con.close()
    assert R_UNEXPECTED_VIEW_PRESENT in _run(mutable_db).reason_codes


def test_unexpected_trigger_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute(
        "CREATE TRIGGER trg_surprise AFTER INSERT ON customers BEGIN "
        "UPDATE customers SET name=name WHERE id=NEW.id; END"
    )
    con.commit()
    con.close()
    assert R_UNEXPECTED_TRIGGER_PRESENT in _run(mutable_db).reason_codes


def test_row_count_baseline_mismatch_hard_stops(mutable_db):
    con = sqlite3.connect(mutable_db)
    con.execute("DELETE FROM customers WHERE id=(SELECT MIN(id) FROM customers)")
    con.commit()
    con.close()
    assert R_ROW_COUNT_BASELINE_MISMATCH in _run(mutable_db).reason_codes


def test_unexpected_check_constraint_hard_stops(mutable_db):
    _rewrite_table_sql(
        mutable_db, "customers", "notes TEXT", "notes TEXT CHECK (length(notes) < 500)"
    )
    assert R_UNEXPECTED_CHECK_CONSTRAINT in _run(mutable_db).reason_codes


def test_unwritable_backup_target_hard_stops(golden_db, tmp_path):
    missing_dir = str(tmp_path / "olmayan-dizin")
    report = _run(golden_db, backup_target_dir=missing_dir)
    assert R_BACKUP_TARGET_NOT_WRITABLE in report.reason_codes


# Validator'in calistirmasina IZIN VERILEN tek SQL kumesi.
_IZINLI_PRAGMALAR = {
    "integrity_check", "foreign_key_check", "table_info",
    "foreign_key_list", "index_list", "index_info",
}


def _sql_izinli_mi(sql: str) -> bool:
    s = sql.strip().rstrip(";")
    yukari = s.upper()
    if yukari.startswith("SELECT "):
        return True
    if yukari.startswith("PRAGMA "):
        ad = s[len("PRAGMA "):].split("(")[0].split("=")[0].strip().lower()
        return ad in _IZINLI_PRAGMALAR and "=" not in s
    return False


@pytest.mark.parametrize("bozulma", ["revision", "tablo", "satir_sayisi", "yok"])
def test_no_write_migration_or_stamp_statement_is_ever_executed(mutable_db, monkeypatch, bozulma):
    """
    Owner sarti: dogrulama sirasinda mutation/migration/stamp SIFIR olmalidir —
    hem PASS hem de HARD_STOP yolunda. Validator'in "duzeltmeye calismasi" en
    tehlikeli basarisizlik bicimidir.

    Kanit dolayli degil DOGRUDAN: acilan her baglantiya trace callback
    takilir ve calisan HER SQL ifadesi kaydedilir. Tek bir UPDATE/CREATE/
    DELETE/ALTER veya yazan bir PRAGMA yeterlidir — test duser.
    """
    calisan_sql: list[str] = []
    gercek_connect = sqlite3.connect

    def izleyen_connect(target, *args, **kwargs):
        con = gercek_connect(target, *args, **kwargs)
        con.set_trace_callback(calisan_sql.append)
        return con

    con = sqlite3.connect(mutable_db)
    if bozulma == "revision":
        con.execute("UPDATE alembic_version SET version_num='000_bozuk'")
    elif bozulma == "tablo":
        con.execute("CREATE TABLE surprise (id INTEGER PRIMARY KEY)")
    elif bozulma == "satir_sayisi":
        con.execute("DELETE FROM offers WHERE id=(SELECT MIN(id) FROM offers)")
    con.commit()
    revizyon_oncesi = con.execute("SELECT version_num FROM alembic_version").fetchone()
    con.close()

    monkeypatch.setattr(sqlite3, "connect", izleyen_connect)
    report = _run(mutable_db)
    monkeypatch.undo()

    beklenen = Outcome.PASS if bozulma == "yok" else Outcome.HARD_STOP
    assert report.outcome is beklenen, report.reason_codes

    yasakli = [s for s in calisan_sql if not _sql_izinli_mi(s)]
    assert yasakli == [], f"validator yazan/degistiren SQL calistirdi: {yasakli}"

    con = sqlite3.connect(mutable_db)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone() == revizyon_oncesi
    con.close()


def test_phase_three_adoption_is_not_wired_anywhere():
    """
    Faz 3 (controlled adoption) YETKILENDIRILMEMISTIR. Validator'in bir
    router/CLI/startup yolundan cagrilabilir olmasi, yanlislikla calistirilma
    riski yaratir. Bu test o yolun acilmadigini kanitlar.
    """
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    izinli_kokler = {
        os.path.join(backend, "app", "legacy_adoption"),
        os.path.join(backend, "tests"),
    }
    ihlaller = []
    for kok, _dirs, dosyalar in os.walk(os.path.join(backend, "app")):
        if any(kok.startswith(izin) for izin in izinli_kokler):
            continue
        for dosya in dosyalar:
            if not dosya.endswith(".py"):
                continue
            yol = os.path.join(kok, dosya)
            with open(yol, encoding="utf-8", errors="replace") as fh:
                if "legacy_adoption" in fh.read():
                    ihlaller.append(os.path.relpath(yol, backend))
    assert ihlaller == [], f"legacy_adoption uygulama koduna baglanmis: {ihlaller}"


def test_sanitization_gate_catches_leaked_email():
    leaked = {"evidence": {"note": "ornek@example.com"}}
    assert "eposta_adresi_tespit_edildi" in assert_evidence_sanitized(leaked)


def test_sanitization_gate_catches_secret_keyword():
    leaked = {"evidence": {"note": "SMTP_PASSWORD=abc"}}
    violations = assert_evidence_sanitized(leaked)
    assert any("password" in v for v in violations)
