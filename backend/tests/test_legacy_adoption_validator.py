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
from app.legacy_adoption.fingerprint import collect_fingerprint  # noqa: E402
from app.legacy_adoption.result import (  # noqa: E402
    R_ALEMBIC_REVISION_NOT_ALLOWLISTED,
    R_ALEMBIC_VERSION_MISSING,
    R_BACKUP_TARGET_NOT_WRITABLE,
    R_CANONICAL_INDEX_DEFINITION_MISMATCH,
    R_CANONICAL_INDEX_MISSING,
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
    R_TABLE_COUNT_MISMATCH,
    R_UNEXPECTED_CHECK_CONSTRAINT,
    R_UNEXPECTED_TRIGGER_PRESENT,
    R_UNEXPECTED_VIEW_PRESENT,
    R_UNKNOWN_COLUMN_PRESENT,
    R_UNKNOWN_INDEX_PRESENT,
    R_UNKNOWN_TABLE_PRESENT,
    Outcome,
)
from app.legacy_adoption.repair import (  # noqa: E402
    RepairRefused,
    repair_incidents_canonical,
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

# PDSMR-R4 / Faz 3: bu satir sayilari SADECE bu fixture'i insa etmek
# icindir — policy.py'de KARSILIGI YOKTUR (EXPECTED_ROW_COUNTS kaldirildi,
# validator artik hicbir is verisi satir sayisini kabul/red kriteri
# olarak KULLANMAZ). Degerler tamamen KEYFIDIR; production'in gercek
# olculmus rakamlariyla (Faz 0 bulgusu) KASITLI olarak ILISKISIZDIR.
_GOLDEN_FIXTURE_ROW_COUNTS = {
    "customers": 3, "offers": 6, "contracts": 3, "activities": 7, "tasks": 7,
    "prospect_companies": 1, "prospect_contacts": 0, "prospect_sources": 3,
    "incidents": 1, "invoices": 0, "ptf_drift_log": 0, "market_reference_prices": 60,
}

# Ayni sekilde GECERLI ama TAMAMEN FARKLI hacimli ikinci bir fixture seti —
# "jenerik mi, yoksa gizlice bu rakamlara mi baglandi" sorusunu kanitlamak
# icin (bkz. test_small_and_large_valid_data_volume_produce_same_outcome).
_ALT_FIXTURE_ROW_COUNTS = {
    "customers": 41, "offers": 130, "contracts": 17, "activities": 2, "tasks": 0,
    "prospect_companies": 9, "prospect_contacts": 5, "prospect_sources": 12,
    "incidents": 0, "invoices": 0, "ptf_drift_log": 0, "market_reference_prices": 4,
}


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


def _build_golden_legacy_db(path: str, row_counts: dict[str, int] | None = None) -> str:
    """
    Production ailesinin sentetik esdegerini uretir:
      - TAM model semasi, ANCAK outreach tablolari YOK (pre-S5)
      - prospect_companies.verified_legal_type* kolonlari YOK (pre-S5)
      - legacy-only tablolar (alembic_version, ptf_drift_log) VAR
      - alembic_version = izinli revision
      - satir sayilari `row_counts` PARAMETRESINDEN gelir (verilmezse
        _GOLDEN_FIXTURE_ROW_COUNTS) — PDSMR-R4/Faz3: bu artik policy.py'nin
        degil, YALNIZ bu test dosyasinin sorumlulugudur.

    Sema kaynagi validator ile AYNI fonksiyondur; boylece fixture'in
    modelin bir kismini kacirmasi (ornegin app/pricing/schemas.py)
    mumkun olmaz.
    """
    if row_counts is None:
        row_counts = _GOLDEN_FIXTURE_ROW_COUNTS
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
            count = row_counts.get(table, 0)
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
    """
    PDSMR-R4B1 SONRASI GUNCELLENDI: model artik canonical migration
    sekliyle hizali (customers.created_at NOT NULL). Legacy bir DB'de bu
    kolon NULLABLE'dir — ACCEPTED_NULLABILITY_VARIANTS'taki
    "LEGACY_COMPATIBILITY_REQUIRED" girdisi tam da bunun icindir ve
    HARD_STOP uretmemelidir.

    (Eski hali `market_reference_prices.captured_at`i kullaniyordu; o
    kolon 4B1'de canonical'a uyarak MODELDE de nullable oldu, dolayisiyla
    artik bir sapma URETMIYOR ve varyanti test EDEMIYORDU.)
    """
    assert ("customers", "created_at") in policy.ACCEPTED_NULLABILITY_VARIANTS
    _rewrite_table_sql(
        mutable_db, "customers", "created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL",
        "created_at DATETIME DEFAULT (CURRENT_TIMESTAMP)",
    )
    report = _run(mutable_db)
    assert R_COLUMN_NULLABILITY_DRIFT not in report.reason_codes, report.reason_codes
    assert any("created_at" in v for v in report.accepted_variants)


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


def test_partial_unique_index_does_not_satisfy_required_uniqueness(mutable_db):
    """
    PARTIAL bir UNIQUE index GLOBAL benzersizlik SAGLAMAZ: WHERE kosulunun
    disinda kalan satirlar icin kisit yoktur. Modeldeki tam unique kisitin
    yerine gecmis gibi kabul edilirse validator FAIL-OPEN olur — eksik bir
    veri butunlugu garantisini "var" sayar.
    """
    con = sqlite3.connect(mutable_db)
    con.execute("DROP INDEX ix_contracts_contract_number")
    con.execute(
        "CREATE UNIQUE INDEX ix_contracts_contract_number "
        "ON contracts (contract_number) WHERE contract_number IS NOT NULL"
    )
    con.commit()
    con.close()
    assert R_REQUIRED_INDEX_MISSING in _run(mutable_db).reason_codes


def test_partial_index_flag_is_collected(mutable_db):
    """Fingerprint partial bayragini kaydetmelidir; kaydetmezse ayrim yapilamaz."""
    con = sqlite3.connect(mutable_db)
    con.execute("CREATE INDEX ix_probe_partial ON customers (email) WHERE email IS NOT NULL")
    con.commit()
    con.close()
    fp = collect_fingerprint(mutable_db)
    assert fp.tables["customers"].indexes["ix_probe_partial"].partial is True
    assert fp.tables["customers"].indexes["ix_customers_name"].partial is False


def test_all_indexes_of_a_table_are_collected_not_just_the_first(mutable_db):
    """
    PRAGMA index_list satirlari ayni cursor'da ic ice execute() ile
    gezilirse tablo basina yalniz ILK index okunur. Faz 1 kaniti bu yuzden
    123 index'in 31'ini kaydetmisti. Regresyonu kalici olarak kapatir.
    """
    con = sqlite3.connect(mutable_db)
    for i in range(5):
        con.execute(f"CREATE INDEX ix_probe_multi_{i} ON customers (phone) WHERE id > {i}")
    con.commit()
    con.close()
    toplanan = set(collect_fingerprint(mutable_db).tables["customers"].indexes)
    for i in range(5):
        assert f"ix_probe_multi_{i}" in toplanan, f"{i}. index kacirildi: {sorted(toplanan)}"


def test_autoindex_from_pk_is_excluded_but_unique_constraint_autoindex_is_not(golden_db):
    """
    origin='pk' otomatik index'i PK'nin kendisidir, ayri bir kisit degildir.
    origin='u' ise gercek bir UNIQUE constraint tasir ve karsilastirmaya girer.

    PDSMR-R4B1 SONRASI GUNCELLENDI: ornek tablo
    `market_reference_prices` -> `uploaded_reference_documents` oldu.
    market_reference_prices'in (price_type, period) benzersizligi 4B1'de
    tablo-seviyesi UniqueConstraint'ten (origin='u') canonical'in
    ADLANDIRILMIS unique index'ine (origin='c') cevrildi; dolayisiyla o
    tablo artik origin='u' bir autoindex TASIMIYOR. Test edilen DAVRANIS
    (origin='u' ayirt edilmesi) DEGISMEDI.
    """
    fp = collect_fingerprint(golden_db)
    urd = fp.tables["uploaded_reference_documents"].indexes
    u_kaynakli = [i for i in urd.values() if i.origin == "u"]
    assert u_kaynakli, "UNIQUE constraint autoindex'i bulunamadi"
    assert all(i.unique for i in u_kaynakli)
    # market_reference_prices artik canonical adlandirilmis index kullanir.
    mrp = fp.tables["market_reference_prices"].indexes
    assert "ix_market_reference_prices_price_type_period" in mrp
    assert mrp["ix_market_reference_prices_price_type_period"].origin == "c"
    assert mrp["ix_market_reference_prices_price_type_period"].unique is True
    assert _run(golden_db).outcome is Outcome.PASS


@pytest.mark.parametrize(
    "kolon,yeni_tip",
    [("deduction_total", "REAL"), ("dedupe_bucket", "TEXT")],
)
def test_incidents_production_drift_is_fail_closed(mutable_db, kolon, yeni_tip):
    """
    Uretimdeki iki incidents sapmasi (PDSMR-R1D/2R1 ADIM 4) HARD_STOP
    uretmelidir. Model, migration 004 ve write path'in ucu de Integer der;
    uretim semasi sapmadir. Bu test allowlist'e sessizce eklenmesini onler.
    """
    _rewrite_table_sql(mutable_db, "incidents", f"{kolon} INTEGER", f"{kolon} {yeni_tip}")
    report = _run(mutable_db)
    assert R_COLUMN_AFFINITY_DRIFT in report.reason_codes
    assert any(f"incidents.{kolon}" in f.detail for f in report.findings)


def test_package_has_no_outreach_or_smtp_surface():
    """provider invocation = 0: pakette e-posta/SMTP yuzeyi bulunmamalidir."""
    paket = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "legacy_adoption"
    )
    ihlaller = []
    for dosya in sorted(os.listdir(paket)):
        if not dosya.endswith(".py"):
            continue
        with open(os.path.join(paket, dosya), encoding="utf-8") as fh:
            icerik = fh.read().lower()
        for yasak in ("smtplib", "outreach.smtp", "sendmail", "send_message", "starttls"):
            if yasak in icerik:
                ihlaller.append(f"{dosya}:{yasak}")
    assert ihlaller == [], ihlaller


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


# ─────────────────────────────────────────────────────────────────────────
# PDSMR-R4 / Faz 3 — row-count ARTIK kabul/red kriteri degil, yalniz kanit
# ─────────────────────────────────────────────────────────────────────────
def test_variable_table_row_count_change_does_not_hard_stop(mutable_db):
    """
    `incidents` cocuksuz (hicbir tablo FK ile ona bakmiyor) bir is verisi
    tablosudur ve MUST_BE_EMPTY_LEGACY_TABLES'ta DEGILDIR. Satirini SIFIRA
    indirmek (1 -> 0) — eskiden R_ROW_COUNT_BASELINE_MISMATCH ile
    HARD_STOP olurdu — artik sonucu HIC DEGISTIRMEMELIDIR: row-count bir
    politika girdisi degil, yalniz kanittir.
    """
    before = _run(mutable_db)
    assert before.outcome is Outcome.PASS, before.reason_codes

    con = sqlite3.connect(mutable_db)
    con.execute("DELETE FROM incidents")
    con.commit()
    con.close()

    after = _run(mutable_db)
    assert after.outcome is Outcome.PASS, after.reason_codes
    assert after.evidence["row_counts"]["incidents"] == 0


def test_row_count_evidence_reflects_actual_measured_value(mutable_db):
    """Kanit, POLITIKA degerini degil, GERCEKTE OLCULEN degeri gostermelidir."""
    con = sqlite3.connect(mutable_db)
    con.execute("DELETE FROM market_reference_prices WHERE id NOT IN (SELECT MIN(id) FROM market_reference_prices)")
    con.commit()
    con.close()
    report = _run(mutable_db)
    assert report.outcome is Outcome.PASS, report.reason_codes
    assert report.evidence["row_counts"]["market_reference_prices"] == 1


def test_row_count_evidence_is_sorted_by_table_name(golden_db):
    report = _run(golden_db)
    tablolar = list(report.evidence["row_counts"].keys())
    assert tablolar == sorted(tablolar), tablolar
    assert len(tablolar) > 1, "sira testi anlamli olmasi icin en az 2 tablo gerekir"


def test_small_and_large_valid_data_volume_produce_same_outcome(tmp_path):
    """
    Jeneriklik kaniti: AYNI sema, TAMAMEN FARKLI is verisi hacmi tasiyan
    iki fixture, AYNI validation kararini uretmelidir. Kanit, HER birinin
    KENDI gercek sayilarini gostermelidir — birbirine veya bir policy
    sabitine BAGLI degil.
    """
    kucuk = _build_golden_legacy_db(str(tmp_path / "kucuk.db"), _GOLDEN_FIXTURE_ROW_COUNTS)
    buyuk = _build_golden_legacy_db(str(tmp_path / "buyuk.db"), _ALT_FIXTURE_ROW_COUNTS)

    rapor_kucuk = _run(kucuk)
    rapor_buyuk = _run(buyuk)

    assert rapor_kucuk.outcome is Outcome.PASS, rapor_kucuk.reason_codes
    assert rapor_buyuk.outcome is Outcome.PASS, rapor_buyuk.reason_codes
    assert rapor_kucuk.outcome == rapor_buyuk.outcome

    for tablo, beklenen in _GOLDEN_FIXTURE_ROW_COUNTS.items():
        assert rapor_kucuk.evidence["row_counts"][tablo] == beklenen
    for tablo, beklenen in _ALT_FIXTURE_ROW_COUNTS.items():
        assert rapor_buyuk.evidence["row_counts"][tablo] == beklenen
    assert rapor_kucuk.evidence["row_counts"] != rapor_buyuk.evidence["row_counts"]


def test_alembic_version_single_row_is_not_subject_to_emptiness_rule(golden_db):
    """
    `alembic_version` KNOWN_LEGACY_ONLY_TABLES'in uyesidir ama
    MUST_BE_EMPTY_LEGACY_TABLES'in DEGILDIR — DOGAL olarak TAM 1 satir
    tasir ve bu HICBIR bulguya yol acmamalidir (madde 8).
    """
    assert "alembic_version" not in policy.MUST_BE_EMPTY_LEGACY_TABLES
    con = sqlite3.connect(golden_db)
    satir = con.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0]
    con.close()
    assert satir == 1
    report = _run(golden_db)
    assert report.outcome is Outcome.PASS, report.reason_codes
    assert R_LEGACY_TABLE_NOT_EMPTY not in report.reason_codes


def test_must_be_empty_legacy_table_zero_rows_passes_that_gate(golden_db):
    """ptf_drift_log=0 -> bu ozel kapi PASS eder (madde 6)."""
    con = sqlite3.connect(golden_db)
    satir = con.execute("SELECT COUNT(*) FROM ptf_drift_log").fetchone()[0]
    con.close()
    assert satir == 0
    report = _run(golden_db)
    assert R_LEGACY_TABLE_NOT_EMPTY not in report.reason_codes


def test_row_count_measurement_missing_is_fail_closed_not_silent_pass():
    """
    `_check_row_counts()` DOGRUDAN, sentetik bir DatabaseFingerprint ile
    cagrilir: `ptf_drift_log` tabloda VAR ama `row_counts`'ta KARSILIGI
    YOK (fingerprint kapsam disi kaldigi senaryosu). Sessiz PASS YASAK —
    deterministik R_LEGACY_TABLE_NOT_EMPTY HARD_STOP uretmelidir (madde 4).
    """
    from app.legacy_adoption.fingerprint import ColumnFingerprint, DatabaseFingerprint, TableFingerprint
    from app.legacy_adoption.result import Finding
    from app.legacy_adoption.validator import _check_row_counts

    fp = DatabaseFingerprint(
        file_path="<sentetik>", file_size=0, file_sha256="0" * 64,
        sqlite_version="3.0.0", integrity_check="ok", foreign_key_violations=0,
        alembic_version=policy.ALLOWED_ALEMBIC_REVISION,
        tables={
            "ptf_drift_log": TableFingerprint(
                columns={"id": ColumnFingerprint("INTEGER", "INTEGER", True, False)},
                primary_key=("id",), foreign_keys=(), indexes={}, has_check_constraint=False,
            ),
        },
        row_counts={},  # <-- ptf_drift_log KASITLI OLARAK burada YOK
    )
    findings: list[Finding] = []
    _check_row_counts(fp, findings)
    assert any(f.reason_code == R_LEGACY_TABLE_NOT_EMPTY for f in findings), findings
    assert any("olculemedi" in f.detail for f in findings), findings


# ─────────────────────────────────────────────────────────────────────────
# PDSMR-R4 / Faz 3 — statik/AST kontrolleri
# ─────────────────────────────────────────────────────────────────────────
def test_expected_row_counts_symbol_does_not_exist_in_policy_or_validator():
    import ast

    for modul in ("app/legacy_adoption/policy.py", "app/legacy_adoption/validator.py"):
        yol = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), modul.replace("/", os.sep)
        )
        with open(yol, encoding="utf-8") as fh:
            kaynak = fh.read()
        agac = ast.parse(kaynak)
        isimler = {n.id for n in ast.walk(agac) if isinstance(n, ast.Name)}
        isimler |= {n.attr for n in ast.walk(agac) if isinstance(n, ast.Attribute)}
        assert "EXPECTED_ROW_COUNTS" not in isimler, f"{modul}: EXPECTED_ROW_COUNTS hala mevcut"


def test_no_production_specific_business_data_numbers_in_policy():
    """
    Faz 0'in olctugu GERCEK canli DB sayilari (customers=2, offers=2,
    contracts=0) policy.py'de HICBIR sabite YAZILMAMALIDIR — aksi halde
    ayni hata (bir anlik goruntuyu kalici politika sanmak) TEKRARLANIR.
    """
    yol = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "app", "legacy_adoption", "policy.py",
    )
    with open(yol, encoding="utf-8") as fh:
        kaynak = fh.read().replace(" ", "")
    for yasakli in ('"customers":2', "'customers':2", '"offers":2', "'offers':2"):
        assert yasakli not in kaynak, f"policy.py'de yasakli desen: {yasakli}"


def test_check_row_counts_makes_no_new_sql_calls():
    """
    `_check_row_counts()` YALNIZ `fp.row_counts`/`fp.tables`'i OKUMALIDIR —
    hicbir sqlite3.connect/execute cagrisi ICERMEMELIDIR (madde 3: yeni
    SQL/tekrar olcum YOK).
    """
    import ast
    import inspect

    from app.legacy_adoption.validator import _check_row_counts

    kaynak = inspect.getsource(_check_row_counts)
    agac = ast.parse(kaynak)
    for node in ast.walk(agac):
        if isinstance(node, ast.Call):
            ad = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else None
            )
            assert ad not in ("connect", "execute", "executemany", "create_all"), (
                f"_check_row_counts() yasakli cagri icermeli DEGIL: {ad}()"
            )


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
        # PDSMR-R4/Faz3: is verisi satir sayisi ARTIK kabul/red kriteri
        # degil — `incidents` (cocuksuz, MUST_BE_EMPTY_LEGACY_TABLES'ta
        # DEGIL) satirini silmek beklenen sonucu artik PASS'e cevirir
        # (asagidaki `_BEKLENEN` haritasina bkz.). Amac hala AYNI: PASS
        # yolunda da HARD_STOP yolunda da SIFIR yazan SQL.
        con.execute("DELETE FROM incidents")
    con.commit()
    revizyon_oncesi = con.execute("SELECT version_num FROM alembic_version").fetchone()
    con.close()

    monkeypatch.setattr(sqlite3, "connect", izleyen_connect)
    report = _run(mutable_db)
    monkeypatch.undo()

    _BEKLENEN = {
        "revision": Outcome.HARD_STOP, "tablo": Outcome.HARD_STOP,
        "satir_sayisi": Outcome.PASS, "yok": Outcome.PASS,
    }
    assert report.outcome is _BEKLENEN[bozulma], report.reason_codes

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


# ─────────────────────────────────────────────────────────────────────────
# PDSMR-R1D / 2R2 — canonical index sozlesmesi + onarim provasi
# ─────────────────────────────────────────────────────────────────────────
_CANONICAL_INDEX_SPECS = policy.REQUIRED_CANONICAL_INDEXES["incidents"]


def _drop_index(path: str, name: str) -> None:
    con = sqlite3.connect(path)
    con.execute(f"DROP INDEX {name}")
    con.commit()
    con.close()


def _recreate_index(path: str, sql: str, drop: str) -> None:
    con = sqlite3.connect(path)
    con.execute(f"DROP INDEX {drop}")
    con.execute(sql)
    con.commit()
    con.close()


def test_golden_db_contains_all_six_canonical_indexes(golden_db):
    """Golden fixture canonical semayi tasir; altisi da bulunmalidir."""
    idx = collect_fingerprint(golden_db).tables["incidents"].indexes
    for spec in _CANONICAL_INDEX_SPECS:
        assert spec["name"] in idx, f"{spec['name']} golden DB'de yok"
        d = idx[spec["name"]]
        assert d.columns == tuple(spec["columns"])
        assert d.unique == spec["unique"]
        assert d.partial == spec["partial"]


@pytest.mark.parametrize("spec", _CANONICAL_INDEX_SPECS, ids=lambda s: s["name"])
def test_each_canonical_index_missing_independently_hard_stops(mutable_db, spec):
    """Alti index'in HER BIRI tek basina eksildiginde HARD_STOP olmalidir."""
    _drop_index(mutable_db, spec["name"])
    report = _run(mutable_db)
    assert R_CANONICAL_INDEX_MISSING in report.reason_codes
    assert any(spec["name"] in f.detail for f in report.findings)


def test_canonical_index_with_wrong_uniqueness_hard_stops(mutable_db):
    # UNIQUE kaybolursa dedupe benzersizligi DB seviyesinde YOK olur.
    _recreate_index(
        mutable_db,
        "CREATE INDEX ix_incidents_dedupe_unique ON incidents "
        "(tenant_id, dedupe_key, dedupe_bucket)",
        "ix_incidents_dedupe_unique",
    )
    report = _run(mutable_db)
    assert R_CANONICAL_INDEX_DEFINITION_MISMATCH in report.reason_codes
    assert any("unique gercek=False" in f.detail for f in report.findings)


def test_canonical_index_with_wrong_column_order_hard_stops(mutable_db):
    # Kolon sirasi index'in kullanilabilirligini degistirir; ayni kume degil.
    _recreate_index(
        mutable_db,
        "CREATE UNIQUE INDEX ix_incidents_dedupe_unique ON incidents "
        "(dedupe_key, tenant_id, dedupe_bucket)",
        "ix_incidents_dedupe_unique",
    )
    report = _run(mutable_db)
    assert R_CANONICAL_INDEX_DEFINITION_MISMATCH in report.reason_codes
    assert any("kolonlar gercek=" in f.detail for f in report.findings)


def test_canonical_index_with_unexpected_partial_predicate_hard_stops(mutable_db):
    # Partial yuklem benzersizligi kosullu hale getirir -> canonical DEGIL.
    _recreate_index(
        mutable_db,
        "CREATE UNIQUE INDEX ix_incidents_dedupe_unique ON incidents "
        "(tenant_id, dedupe_key, dedupe_bucket) WHERE dedupe_key IS NOT NULL",
        "ix_incidents_dedupe_unique",
    )
    report = _run(mutable_db)
    assert R_CANONICAL_INDEX_DEFINITION_MISMATCH in report.reason_codes
    assert any("partial gercek=True" in f.detail for f in report.findings)


def test_same_name_wrong_definition_index_hard_stops(mutable_db):
    """Ad dogru ama tanim yanlis: 'var' gorunup korumayan en sinsi hal."""
    _recreate_index(
        mutable_db,
        "CREATE INDEX ix_incidents_dedupe_bucket ON incidents (dedupe_key)",
        "ix_incidents_dedupe_bucket",
    )
    report = _run(mutable_db)
    assert R_CANONICAL_INDEX_DEFINITION_MISMATCH in report.reason_codes
    assert R_CANONICAL_INDEX_MISSING not in report.reason_codes


def test_model_now_declares_the_canonical_dedupe_unique_index():
    """
    Kor noktanin koku: bu unique kisit migration 004'te vardi ama modelde
    yoktu; create_all() ile kurulan taze DB'ler korumasiz kaliyordu.
    """
    from app.legacy_adoption.validator import _load_model_metadata

    idx = {
        i.name: (tuple(c.name for c in i.columns), bool(i.unique))
        for i in _load_model_metadata().tables["incidents"].indexes
    }
    assert idx.get("ix_incidents_dedupe_unique") == (
        ("tenant_id", "dedupe_key", "dedupe_bucket"), True
    )


# ── Onarim provasi (YALNIZ disposable kopya) ────────────────────────────
def _make_pre_repair_copy(golden_db, tmp_path) -> str:
    """Uretim ailesini taklit eder: iki kolon sapmis + alti index yok."""
    hedef = str(tmp_path / "pre_repair.db")
    shutil.copyfile(golden_db, hedef)
    for spec in _CANONICAL_INDEX_SPECS:
        _drop_index(hedef, spec["name"])
    _rewrite_table_sql(hedef, "incidents", "dedupe_bucket INTEGER", "dedupe_bucket TEXT")
    _rewrite_table_sql(hedef, "incidents", "deduction_total INTEGER", "deduction_total REAL")
    return hedef


def test_repair_refuses_without_explicit_disposable_confirmation(golden_db, tmp_path):
    hedef = str(tmp_path / "c.db")
    shutil.copyfile(golden_db, hedef)
    before = _sha256(hedef)
    with pytest.raises(RepairRefused):
        repair_incidents_canonical(hedef)
    assert _sha256(hedef) == before


def test_repair_refuses_installed_application_path(tmp_path):
    """Kurulu uygulama yoluna benzeyen bir hedef FIZIKSEL olarak reddedilir."""
    sahte = tmp_path / "AppData" / "Local" / "Programs" / "Gelka Enerji" / "resources"
    sahte.mkdir(parents=True)
    db = sahte / "gelka_enerji.db"
    sqlite3.connect(str(db)).close()
    with pytest.raises(RepairRefused, match="disposable"):
        repair_incidents_canonical(str(db), confirm_disposable_copy=True)


def test_repair_refuses_fractional_value_conversion(golden_db, tmp_path):
    hedef = _make_pre_repair_copy(golden_db, tmp_path)
    con = sqlite3.connect(hedef)
    con.execute("UPDATE incidents SET deduction_total = 7.5")
    con.commit()
    con.close()
    before = _sha256(hedef)
    with pytest.raises(RepairRefused, match="DEGISTIRIRDI"):
        repair_incidents_canonical(hedef, confirm_disposable_copy=True)
    assert _sha256(hedef) == before, "reddedilen onarim DB'yi degistirdi"


def test_repair_refuses_nonnumeric_text_conversion(golden_db, tmp_path):
    hedef = _make_pre_repair_copy(golden_db, tmp_path)
    con = sqlite3.connect(hedef)
    con.execute("UPDATE incidents SET dedupe_bucket = 'bugun'")
    con.commit()
    con.close()
    before = _sha256(hedef)
    with pytest.raises(RepairRefused, match="BELIRSIZ"):
        repair_incidents_canonical(hedef, confirm_disposable_copy=True)
    assert _sha256(hedef) == before


def test_repair_handles_all_null_dedupe_bucket(golden_db, tmp_path):
    """Uretimin gercek hali: dedupe_bucket tamamen NULL."""
    hedef = _make_pre_repair_copy(golden_db, tmp_path)
    con = sqlite3.connect(hedef)
    con.execute("UPDATE incidents SET dedupe_bucket = NULL")
    con.commit()
    con.close()
    rapor = repair_incidents_canonical(hedef, confirm_disposable_copy=True)
    assert rapor.outcome == "REPAIRED"
    con = sqlite3.connect(hedef)
    assert con.execute(
        "SELECT COUNT(*) FROM incidents WHERE dedupe_bucket IS NOT NULL"
    ).fetchone()[0] == 0
    con.close()


def test_repair_converts_populated_safe_values(golden_db, tmp_path):
    hedef = _make_pre_repair_copy(golden_db, tmp_path)
    con = sqlite3.connect(hedef)
    con.execute("UPDATE incidents SET dedupe_bucket = '20677', deduction_total = 7.0")
    con.commit()
    con.close()
    rapor = repair_incidents_canonical(hedef, confirm_disposable_copy=True)
    assert rapor.outcome == "REPAIRED"
    con = sqlite3.connect(hedef)
    tip, deger = con.execute(
        "SELECT typeof(dedupe_bucket), dedupe_bucket FROM incidents"
    ).fetchone()
    assert (tip, deger) == ("integer", 20677)
    tip2, deger2 = con.execute(
        "SELECT typeof(deduction_total), deduction_total FROM incidents"
    ).fetchone()
    assert (tip2, deger2) == ("integer", 7)
    con.close()


def test_repair_preserves_rows_columns_fks_and_unrelated_indexes(golden_db, tmp_path):
    hedef = _make_pre_repair_copy(golden_db, tmp_path)
    con = sqlite3.connect(hedef)
    once_kolon = [r[1] for r in con.execute("PRAGMA table_info(incidents)").fetchall()]
    once_satir = con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    once_index = {r[1] for r in con.execute("PRAGMA index_list(incidents)").fetchall()}
    once_diger = {
        t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("customers", "offers", "contracts", "market_reference_prices")
    }
    con.close()

    rapor = repair_incidents_canonical(hedef, confirm_disposable_copy=True)
    assert rapor.outcome == "REPAIRED"

    con = sqlite3.connect(hedef)
    assert [r[1] for r in con.execute("PRAGMA table_info(incidents)").fetchall()] == once_kolon
    assert con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == once_satir
    sonra_index = {r[1] for r in con.execute("PRAGMA index_list(incidents)").fetchall()}
    assert once_index.issubset(sonra_index), f"kaybolan index: {once_index - sonra_index}"
    for t, n in once_diger.items():
        assert con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == n
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()
    assert rapor.integrity_check == "ok"
    assert rapor.foreign_key_violations == 0


def test_post_repair_validator_passes_and_source_copy_untouched(golden_db, tmp_path):
    """Onarim provasinin asil kaniti: onarilan kopya PASS eder."""
    kaynak = _make_pre_repair_copy(golden_db, tmp_path)
    kaynak_hash = _sha256(kaynak)

    on_rapor = _run(kaynak)
    assert on_rapor.outcome is Outcome.HARD_STOP
    assert R_CANONICAL_INDEX_MISSING in on_rapor.reason_codes
    assert R_COLUMN_AFFINITY_DRIFT in on_rapor.reason_codes

    calisma = str(tmp_path / "calisma.db")
    shutil.copyfile(kaynak, calisma)
    repair_incidents_canonical(calisma, confirm_disposable_copy=True)

    sonra = _run(calisma)
    assert sonra.outcome is Outcome.PASS, sonra.reason_codes

    # Kaynak kopya BIREBIR ayni kalmali; HARD_STOP hukmu PASS'e cevrilmez.
    assert _sha256(kaynak) == kaynak_hash
    assert _run(kaynak).outcome is Outcome.HARD_STOP


def test_repair_is_idempotent_no_op_on_second_run(golden_db, tmp_path):
    hedef = _make_pre_repair_copy(golden_db, tmp_path)
    assert repair_incidents_canonical(hedef, confirm_disposable_copy=True).outcome == "REPAIRED"
    hash_1 = _sha256(hedef)
    ikinci = repair_incidents_canonical(hedef, confirm_disposable_copy=True)
    assert ikinci.outcome == "ALREADY_CANONICAL"
    assert _sha256(hedef) == hash_1, "ikinci kosu DB'yi degistirdi — no-op degil"


def test_post_repair_dedupe_uniqueness_is_enforced_by_the_database(golden_db, tmp_path):
    """
    Semantik dogrulama: onarim sonrasi ayni (tenant_id, dedupe_key,
    dedupe_bucket) uclusu ICIN IKINCI satir DB tarafindan REDDEDILIR.
    """
    hedef = _make_pre_repair_copy(golden_db, tmp_path)
    repair_incidents_canonical(hedef, confirm_disposable_copy=True)

    con = sqlite3.connect(hedef)
    con.execute(
        "UPDATE incidents SET tenant_id='gelka', dedupe_key='k1', dedupe_bucket=20677"
    )
    con.commit()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO incidents (trace_id, tenant_id, severity, category, message, "
            "status, occurrence_count, dedupe_key, dedupe_bucket) "
            "VALUES ('t2','gelka','HIGH','CALC','m','OPEN',1,'k1',20677)"
        )
        con.commit()
    con.close()


def test_sanitization_gate_catches_leaked_email():
    leaked = {"evidence": {"note": "ornek@example.com"}}
    assert "eposta_adresi_tespit_edildi" in assert_evidence_sanitized(leaked)


def test_sanitization_gate_catches_secret_keyword():
    leaked = {"evidence": {"note": "SMTP_PASSWORD=abc"}}
    violations = assert_evidence_sanitized(leaked)
    assert any("password" in v for v in violations)
