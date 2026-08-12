"""
PDSMR-R1D / Faz 2 — legacy adoption VALIDATOR (salt-okunur, fail-closed).

NE YAPAR:
  Bir legacy SQLite DB'sinin, bu build tarafindan guvenle "adopt"
  edilebilmesi icin gereken on-kosullari dogrular ve typed bir sonuc doner.

NE YAPMAZ (owner tarafindan Faz 2'de ACIKCA YASAKLANMISTIR):
  - migration CALISTIRMAZ
  - alembic stamp ATMAZ / alembic_version'a DOKUNMAZ
  - kaynak DB'ye HICBIR yazma yapmaz (baglanti ?mode=ro)
  - DB icine audit/log SATIRI yazmaz
  - adoption'i KENDILIGINDEN baslatmaz (Faz 3 yetkilendirilmemistir)

Sonuc yalnizca PASS veya HARD_STOP(reason_codes[]) olabilir. Ara durum,
"uyari", "muhtemelen uygun" YOKTUR. Taninmayan her sapma HARD_STOP'tur.

Cagrildigi yerler:
- tests/test_legacy_adoption_validator.py [PDSMR-R1D/Faz2]
- (Faz 3 adoption akisi HENUZ YOK — bilincli olarak hicbir router/CLI
   bu fonksiyonu cagirmaz.)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
from typing import Any, Optional

from . import policy
from .fingerprint import DatabaseFingerprint, collect_fingerprint, sqlite_affinity
from .result import (
    Finding,
    Outcome,
    R_ALEMBIC_REVISION_NOT_ALLOWLISTED,
    R_ALEMBIC_VERSION_MISSING,
    R_BACKUP_INSUFFICIENT_FREE_SPACE,
    R_BACKUP_TARGET_NOT_WRITABLE,
    R_COLUMN_AFFINITY_DRIFT,
    R_COLUMN_NULLABILITY_DRIFT,
    R_DB_FILE_MISSING,
    R_DB_FILE_UNREADABLE,
    R_DB_NOT_SQLITE,
    R_EVIDENCE_SANITIZATION_FAILED,
    R_EXPECTED_COLUMN_MISSING,
    R_EXPECTED_TABLE_MISSING,
    R_FORBIDDEN_TABLE_PRESENT,
    R_FOREIGN_KEY_DRIFT,
    R_FOREIGN_KEY_VIOLATIONS,
    R_INTEGRITY_CHECK_FAILED,
    R_LEGACY_TABLE_MISSING,
    R_LEGACY_TABLE_NOT_EMPTY,
    R_PRIMARY_KEY_DRIFT,
    R_REQUIRED_INDEX_MISSING,
    R_ROW_COUNT_BASELINE_MISMATCH,
    R_TABLE_COUNT_MISMATCH,
    R_UNEXPECTED_CHECK_CONSTRAINT,
    R_UNEXPECTED_TRIGGER_PRESENT,
    R_UNEXPECTED_VIEW_PRESENT,
    R_UNKNOWN_COLUMN_PRESENT,
    R_UNKNOWN_INDEX_PRESENT,
    R_UNKNOWN_TABLE_PRESENT,
    ValidationReport,
    build_report,
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# ── Model tarafi yardimcilari ────────────────────────────────────────────
def _load_model_metadata():
    """
    Beklenen semanin kaynagi: uygulamanin kendi SQLAlchemy modeli.

    Model IN-PROCESS okunur; hicbir DDL calistirilmaz, hicbir dosya
    olusturulmaz. Boylece "beklenen sema" elle yazilmis ikinci bir
    kopya olmaz ve model degistiginde validator otomatik guncel kalir.

    DIKKAT — model TAM YUKLENMELIDIR: tablolar Base.metadata'ya ancak
    kendilerini tanimlayan modul import edildiginde kaydolur. Yalniz
    app.database import edilirse app/pricing/schemas.py'deki 7 tablo
    metadata'da GORUNMEZ ve validator onlari sessizce "beklenmiyor"
    sayar — yani eksik semayi UYGUN diye onaylar. Bu, fail-closed
    ilkesinin tam tersi yonde bir hata olurdu; bu yuzden model
    modulleri burada ACIKCA import edilir.
    """
    from app import database as _database  # noqa: F401  (25 tablo)
    from app.pricing import schemas as _pricing_schemas  # noqa: F401  (7 tablo)

    return _database.Base.metadata


def _model_declared_type(column, dialect) -> str:
    try:
        return column.type.compile(dialect=dialect).upper()
    except Exception:  # noqa: BLE001 - derlenemeyen tip = bilinmeyen tip
        return type(column.type).__name__.upper()


def _types_compatible(model_declared: str, db_affinity: str) -> bool:
    """
    Model tipi ile DB affinity'si semantik olarak uyumlu mu?

    Once evrensel SQLAlchemy/SQLite esdegerlikleri (JSON->TEXT,
    BOOLEAN->INTEGER, DATETIME->NUMERIC ...), sonra ham affinity esitligi
    denenir. Ikisi de tutmuyorsa GERCEK drift vardir.
    """
    base = model_declared.split("(")[0].strip()
    allowed = policy.TYPE_EQUIVALENCES.get(base)
    if allowed and db_affinity in allowed:
        return True
    return sqlite_affinity(model_declared) == db_affinity


# Index imzasi = (kolonlar, unique, partial).
#
# `partial` imzanin PARCASIDIR, ek bilgi degil: PARTIAL bir unique index
# yalnizca WHERE kosulunu saglayan satirlar icin benzersizlik garanti eder.
# Modeldeki tam bir unique kisitin yerine gecemez — gecebilseydi validator
# olmayan bir veri butunlugu garantisini "var" sayardi (fail-open).
IndexSignature = tuple[tuple[str, ...], bool, bool]


def _model_unique_signatures(table) -> set[IndexSignature]:
    """Modeldeki VERI BUTUNLUGU tasiyan (unique) index/kisit imzalari."""
    sigs: set[IndexSignature] = set()
    for idx in table.indexes:
        if idx.unique:
            sigs.add((tuple(c.name for c in idx.columns), True, False))
    for con in table.constraints:
        if con.__class__.__name__ == "UniqueConstraint":
            sigs.add((tuple(c.name for c in con.columns), True, False))
    return sigs


def _db_index_signatures(tfp) -> dict[IndexSignature, str]:
    """DB'deki index imzalari. PK'nin otomatik index'i haric tutulur."""
    out: dict[IndexSignature, str] = {}
    for name, idx in tfp.indexes.items():
        if idx.origin == "pk":
            continue
        out[(idx.columns, idx.unique, idx.partial)] = name
    return out


def _sig_metni(sig: IndexSignature) -> str:
    return f"{list(sig[0])}{' PARTIAL' if sig[2] else ''}"


# ── Kontrol bloklari ─────────────────────────────────────────────────────
def _check_identity_and_integrity(fp: DatabaseFingerprint, findings: list[Finding]) -> None:
    if fp.alembic_version is None:
        findings.append(Finding(R_ALEMBIC_VERSION_MISSING, "alembic_version okunamadi veya bos"))
    elif fp.alembic_version != policy.ALLOWED_ALEMBIC_REVISION:
        findings.append(Finding(
            R_ALEMBIC_REVISION_NOT_ALLOWLISTED,
            f"revision={fp.alembic_version} beklenen={policy.ALLOWED_ALEMBIC_REVISION}",
        ))

    if fp.integrity_check != "ok":
        findings.append(Finding(R_INTEGRITY_CHECK_FAILED, f"integrity_check={fp.integrity_check}"))

    if fp.foreign_key_violations:
        findings.append(Finding(
            R_FOREIGN_KEY_VIOLATIONS, f"ihlal_sayisi={fp.foreign_key_violations}"
        ))

    for v in fp.views:
        findings.append(Finding(R_UNEXPECTED_VIEW_PRESENT, f"view={v}"))
    for t in fp.triggers:
        findings.append(Finding(R_UNEXPECTED_TRIGGER_PRESENT, f"trigger={t}"))


def _check_tables(fp: DatabaseFingerprint, metadata, findings: list[Finding]) -> set[str]:
    """Tablo kumesini dogrular; model ile karsilastirilacak ortak kumeyi doner."""
    db_tables = set(fp.tables)
    model_tables = set(metadata.tables)
    expected_present = (model_tables - policy.EXPECTED_ABSENT_MODEL_TABLES) | policy.KNOWN_LEGACY_ONLY_TABLES

    for t in sorted(expected_present - db_tables):
        if t == policy.REQUIRED_LEGACY_TABLE:
            continue  # ayri ve daha acik bir reason code ile raporlanir
        findings.append(Finding(R_EXPECTED_TABLE_MISSING, f"tablo={t}"))

    for t in sorted(db_tables - expected_present):
        if t in policy.EXPECTED_ABSENT_MODEL_TABLES:
            findings.append(Finding(
                R_FORBIDDEN_TABLE_PRESENT,
                f"tablo={t} (adoption oncesi bulunmamali — yarim sema belirtisi)",
            ))
        else:
            findings.append(Finding(R_UNKNOWN_TABLE_PRESENT, f"tablo={t}"))

    if len(db_tables) != policy.EXPECTED_TABLE_COUNT:
        findings.append(Finding(
            R_TABLE_COUNT_MISMATCH,
            f"gercek={len(db_tables)} beklenen={policy.EXPECTED_TABLE_COUNT}",
        ))

    if policy.REQUIRED_LEGACY_TABLE not in db_tables:
        findings.append(Finding(
            R_LEGACY_TABLE_MISSING, f"tablo={policy.REQUIRED_LEGACY_TABLE}"
        ))

    for t in sorted(db_tables):
        if t in policy.KNOWN_CHECK_CONSTRAINT_TABLES:
            continue
        if fp.tables[t].has_check_constraint:
            findings.append(Finding(R_UNEXPECTED_CHECK_CONSTRAINT, f"tablo={t}"))

    return db_tables & model_tables


def _check_columns_and_keys(
    fp: DatabaseFingerprint,
    metadata,
    common: set[str],
    findings: list[Finding],
    accepted: list[str],
) -> None:
    from sqlalchemy.dialects import sqlite as sqlite_dialect

    dialect = sqlite_dialect.dialect()

    for tname in sorted(common):
        mtable = metadata.tables[tname]
        dtable = fp.tables[tname]
        mcols = {c.name: c for c in mtable.columns}
        dcols = dtable.columns

        for cname in sorted(set(mcols) - set(dcols)):
            if (tname, cname) in policy.EXPECTED_ABSENT_MODEL_COLUMNS:
                accepted.append(f"EXPECTED_ABSENT_COLUMN {tname}.{cname}")
                continue
            findings.append(Finding(R_EXPECTED_COLUMN_MISSING, f"{tname}.{cname}"))

        for cname in sorted(set(dcols) - set(mcols)):
            findings.append(Finding(R_UNKNOWN_COLUMN_PRESENT, f"{tname}.{cname}"))

        for cname in sorted(set(mcols) & set(dcols)):
            mcol, dcol = mcols[cname], dcols[cname]
            declared = _model_declared_type(mcol, dialect)

            if not _types_compatible(declared, dcol.affinity):
                findings.append(Finding(
                    R_COLUMN_AFFINITY_DRIFT,
                    f"{tname}.{cname} model={declared} db={dcol.declared_type or '<bos>'}"
                    f" (db_affinity={dcol.affinity})",
                ))
            elif (tname, cname) in policy.ACCEPTED_AFFINITY_VARIANTS:
                accepted.append(f"SQLITE_AFFINITY_ONLY {tname}.{cname}")

            # SQLite'te "INTEGER PRIMARY KEY" rowid'dir ve table_info notnull=0
            # bildirir. Bu bir drift degil, motorun davranisidir -> PK'lar haric.
            if mcol.primary_key or cname in dtable.primary_key:
                continue
            if (not mcol.nullable) != dcol.notnull:
                variant = policy.ACCEPTED_NULLABILITY_VARIANTS.get((tname, cname))
                if variant:
                    accepted.append(f"{variant} {tname}.{cname}")
                else:
                    findings.append(Finding(
                        R_COLUMN_NULLABILITY_DRIFT,
                        f"{tname}.{cname} model_notnull={not mcol.nullable}"
                        f" db_notnull={dcol.notnull}",
                    ))

        m_pk = tuple(c.name for c in mtable.primary_key.columns)
        if m_pk != dtable.primary_key:
            findings.append(Finding(
                R_PRIMARY_KEY_DRIFT, f"{tname} model={m_pk} db={dtable.primary_key}"
            ))

        m_fk = tuple(sorted(
            f"{fk.parent.name}->{fk.column.table.name}.{fk.column.name}"
            for fk in mtable.foreign_keys
        ))
        if m_fk != dtable.foreign_keys:
            missing = sorted(set(m_fk) - set(dtable.foreign_keys))
            extra = sorted(set(dtable.foreign_keys) - set(m_fk))
            findings.append(Finding(
                R_FOREIGN_KEY_DRIFT, f"{tname} eksik={missing} fazla={extra}"
            ))


def _check_indexes(
    fp: DatabaseFingerprint, metadata, common: set[str], findings: list[Finding], evidence: dict
) -> None:
    non_unique_missing: list[str] = []
    non_unique_extra: list[str] = []

    for tname in sorted(common):
        mtable = metadata.tables[tname]
        m_unique = _model_unique_signatures(mtable)
        db_sigs = _db_index_signatures(fp.tables[tname])
        db_unique = {sig for sig in db_sigs if sig[1]}

        for sig in sorted(m_unique - db_unique):
            findings.append(Finding(
                R_REQUIRED_INDEX_MISSING, f"{tname} unique{_sig_metni(sig)}"
            ))
        for sig in sorted(db_unique - m_unique):
            findings.append(Finding(
                R_UNKNOWN_INDEX_PRESENT,
                f"{tname} unique{_sig_metni(sig)} name={db_sigs[sig]}",
            ))

        m_non_unique = {
            (tuple(c.name for c in idx.columns), False, False)
            for idx in mtable.indexes
            if not idx.unique
        }
        db_non_unique = {sig for sig in db_sigs if not sig[1]}
        for sig in sorted(m_non_unique - db_non_unique):
            entry = f"{tname}{_sig_metni(sig)}"
            non_unique_missing.append(entry)
            if policy.ENFORCE_NON_UNIQUE_INDEX_PARITY:
                findings.append(Finding(R_REQUIRED_INDEX_MISSING, entry))
        for sig in sorted(db_non_unique - m_non_unique):
            entry = f"{tname}{_sig_metni(sig)} name={db_sigs[sig]}"
            non_unique_extra.append(entry)
            if policy.ENFORCE_NON_UNIQUE_INDEX_PARITY:
                findings.append(Finding(R_UNKNOWN_INDEX_PRESENT, entry))

    # Performans-only farklar: kanita yazilir, HARD_STOP uretmez.
    evidence["non_unique_index_missing"] = non_unique_missing
    evidence["non_unique_index_extra"] = non_unique_extra
    evidence["non_unique_index_parity_enforced"] = policy.ENFORCE_NON_UNIQUE_INDEX_PARITY


def _check_row_counts(fp: DatabaseFingerprint, findings: list[Finding]) -> None:
    for table, expected in sorted(policy.EXPECTED_ROW_COUNTS.items()):
        actual = fp.row_counts.get(table)
        if actual is None:
            continue  # tablo yoklugu zaten tablo kontrolunde raporlandi
        if actual != expected:
            if table == policy.REQUIRED_LEGACY_TABLE:
                findings.append(Finding(
                    R_LEGACY_TABLE_NOT_EMPTY, f"{table} satir={actual} beklenen=0"
                ))
            else:
                findings.append(Finding(
                    R_ROW_COUNT_BASELINE_MISMATCH,
                    f"{table} gercek={actual} baseline={expected}",
                ))


def _check_backup_capability(
    db_path: str, file_size: int, target_dir: Optional[str], findings: list[Finding], evidence: dict
) -> None:
    """
    Yedeklenebilirlik sondasi — TAHRIPSIZ.

    Kaynak DB'ye DOKUNMAZ. Yalniz hedef dizinde gecici bir sonda dosyasi
    olusturur, yazar, siler; ayrica serbest alani olcer.
    """
    directory = target_dir or os.path.dirname(os.path.abspath(db_path))
    evidence["backup_target_dir_exists"] = os.path.isdir(directory)

    if not os.path.isdir(directory):
        findings.append(Finding(R_BACKUP_TARGET_NOT_WRITABLE, "hedef dizin yok"))
        return

    probe_path = None
    try:
        fd, probe_path = tempfile.mkstemp(prefix=".gelka_backup_probe_", dir=directory)
        with os.fdopen(fd, "wb") as fh:
            fh.write(b"gelka-backup-probe")
            fh.flush()
            os.fsync(fh.fileno())
        evidence["backup_probe_written"] = True
    except OSError as exc:
        evidence["backup_probe_written"] = False
        findings.append(Finding(R_BACKUP_TARGET_NOT_WRITABLE, f"errno={exc.errno}"))
        return
    finally:
        if probe_path and os.path.exists(probe_path):
            try:
                os.remove(probe_path)
            except OSError:
                pass

    try:
        free = shutil.disk_usage(directory).free
    except OSError as exc:
        findings.append(Finding(R_BACKUP_TARGET_NOT_WRITABLE, f"disk_usage errno={exc.errno}"))
        return

    required = file_size * policy.BACKUP_FREE_SPACE_FACTOR
    evidence["backup_required_bytes"] = required
    if free < required:
        findings.append(Finding(
            R_BACKUP_INSUFFICIENT_FREE_SPACE, f"serbest={free} gerekli={required}"
        ))


def assert_evidence_sanitized(report_dict: dict[str, Any]) -> list[str]:
    """
    Kanit ciktisinda sir / kisisel veri kalmadigini dogrular.

    Rapor yapisi geregi yalniz sema adi ve sayim icerir; bu fonksiyon o
    invariant'i CALISMA ANINDA da kanitlar (savunma amacli ikinci kapi).
    Ihlal listesi doner; bos liste = temiz.
    """
    blob = json.dumps(report_dict, ensure_ascii=False)
    violations: list[str] = []

    lowered = blob.lower()
    for needle in policy.FORBIDDEN_EVIDENCE_SUBSTRINGS:
        if needle in lowered:
            violations.append(f"yasakli_ifade={needle}")

    if _EMAIL_RE.search(blob):
        violations.append("eposta_adresi_tespit_edildi")

    return violations


def validate_legacy_db(
    db_path: str,
    *,
    backup_target_dir: Optional[str] = None,
    metadata: Any = None,
) -> ValidationReport:
    """
    Legacy DB adoption on-kosullarini dogrular. SALT-OKUNUR, fail-closed.

    Args:
        db_path: Dogrulanacak SQLite dosyasi. ASLA degistirilmez.
        backup_target_dir: Yedegin yazilacagi dizin. None ise DB'nin dizini.
        metadata: Beklenen semanin kaynagi (test enjeksiyonu icin).
            None ise app.database.Base.metadata kullanilir.

    Returns:
        ValidationReport — outcome PASS ya da HARD_STOP.
        Hicbir kosulda exception ile dismez: her hata bir reason_code'a
        cevrilir, cunku sessiz bir istisna "dogrulama yapilmadi"yi
        "sorun yok" gibi gosterebilir.

    Cagrildigi yerler:
    - tests/test_legacy_adoption_validator.py [PDSMR-R1D/Faz2]
    """
    findings: list[Finding] = []
    evidence: dict[str, Any] = {}
    accepted: list[str] = []

    if not os.path.exists(db_path):
        return build_report([Finding(R_DB_FILE_MISSING, "dosya bulunamadi")])
    if not os.path.isfile(db_path):
        return build_report([Finding(R_DB_FILE_MISSING, "yol bir dosya degil")])

    try:
        fp = collect_fingerprint(db_path)
    except sqlite3.DatabaseError as exc:
        return build_report([Finding(R_DB_NOT_SQLITE, f"sqlite hatasi: {type(exc).__name__}")])
    except OSError as exc:
        return build_report([Finding(R_DB_FILE_UNREADABLE, f"errno={exc.errno}")])

    evidence.update(fp.as_evidence())

    if metadata is None:
        metadata = _load_model_metadata()

    _check_identity_and_integrity(fp, findings)
    common = _check_tables(fp, metadata, findings)
    _check_columns_and_keys(fp, metadata, common, findings, accepted)
    _check_indexes(fp, metadata, common, findings, evidence)
    _check_row_counts(fp, findings)
    _check_backup_capability(db_path, fp.file_size, backup_target_dir, findings, evidence)

    report = build_report(findings, evidence, accepted)

    violations = assert_evidence_sanitized(report.to_dict())
    if violations:
        # Kanit kirlenmisse rapor YAYINLANMAZ: evidence tamamen dusurulur ve
        # sonuc HARD_STOP olur. Sizinti riski, dogrulama sonucundan onceliklidir.
        return build_report(
            [Finding(R_EVIDENCE_SANITIZATION_FAILED, v) for v in violations]
            + list(report.findings),
            evidence={"sanitized": False},
            accepted_variants=[],
        )

    return report


__all__ = [
    "Outcome",
    "ValidationReport",
    "assert_evidence_sanitized",
    "validate_legacy_db",
]
