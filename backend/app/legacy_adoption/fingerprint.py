"""
PDSMR-R1D — legacy DB fingerprint toplayici (SALT-OKUNUR).

Tek sorumluluk: bir SQLite dosyasindan deterministik, sanitize edilmis bir
sema/durum parmak izi cikarmak. HICBIR politika karari VERMEZ (o
policy.py'nin isi) ve HICBIR yazma yapmaz.

Salt-okunurluk nasil garanti edilir:
  sqlite3.connect("file:...?mode=ro", uri=True)
SQLite bu modda yazma denemesini surucü seviyesinde reddeder — yani
"dikkat ettik" degil, fiziksel olarak imkansiz.

SANITIZE: cikti YALNIZ sema/sayim/invariant bilgisi icerir. Satir icerigi,
musteri verisi, e-posta govdesi, kimlik bilgisi ASLA okunmaz/dondurulmez;
tek istisna COUNT(*) toplamlaridir.

Cagrildigi yerler:
- app/legacy_adoption/validator.py (validate_legacy_db) [PDSMR-R1D/Faz2]
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

# Sayimi alinacak tablolar — icerik DEGIL, yalniz COUNT(*).
CRITICAL_COUNT_TABLES = (
    "customers", "offers", "contracts", "activities", "tasks",
    "prospect_companies", "prospect_contacts", "prospect_sources",
    "incidents", "invoices", "ptf_drift_log", "market_reference_prices",
)

OUTREACH_TABLES = ("outreach_messages", "outreach_templates", "suppression_entries")


def sqlite_affinity(declared_type: Optional[str]) -> str:
    """
    SQLite'in resmi tip-affinity kurallari (SQLite dokumantasyonu, "Determination
    Of Column Affinity" sirasi). VARCHAR(50) ile VARCHAR(20) AYNI affinity'ye
    (TEXT) duser — SQLite uzunlugu zorlamaz. Bu, kabul edilmis dort
    "type-yazim" varyantinin neden semantik fark OLMADIGININ temelidir.
    """
    t = (declared_type or "").upper()
    if "INT" in t:
        return "INTEGER"
    if any(k in t for k in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in t or t == "":
        return "BLOB"
    if any(k in t for k in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


@dataclass(frozen=True)
class ColumnFingerprint:
    affinity: str
    declared_type: str
    notnull: bool
    has_default: bool


@dataclass(frozen=True)
class IndexFingerprint:
    columns: tuple[str, ...]
    unique: bool
    origin: str  # 'c' = CREATE INDEX, 'pk' = primary key, 'u' = UNIQUE constraint
    # PARTIAL (WHERE kosullu) index GLOBAL bir garanti tasimaz: kosulun
    # disinda kalan satirlar icin kisit YOKTUR. Bu bayrak olmadan bir
    # partial unique index, tam bir unique kisit sanilir.
    partial: bool = False


@dataclass(frozen=True)
class TableFingerprint:
    columns: dict[str, ColumnFingerprint]
    primary_key: tuple[str, ...]
    foreign_keys: tuple[str, ...]
    indexes: dict[str, IndexFingerprint]
    has_check_constraint: bool


@dataclass
class DatabaseFingerprint:
    """Sanitize edilmis, deterministik DB parmak izi."""

    file_path: str
    file_size: int
    file_sha256: str
    sqlite_version: str
    integrity_check: str
    foreign_key_violations: int
    alembic_version: Optional[str]
    tables: dict[str, TableFingerprint] = field(default_factory=dict)
    views: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    row_counts: dict[str, int] = field(default_factory=dict)
    present_outreach_tables: tuple[str, ...] = ()

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.tables))

    def as_evidence(self) -> dict[str, Any]:
        """Rapora/loga yazilabilir sanitize ozet (satir icerigi ICERMEZ)."""
        return {
            "file_size": self.file_size,
            "file_sha256": self.file_sha256,
            "sqlite_version": self.sqlite_version,
            "integrity_check": self.integrity_check,
            "foreign_key_violations": self.foreign_key_violations,
            "alembic_version": self.alembic_version,
            "table_count": len(self.tables),
            "views": list(self.views),
            "triggers": list(self.triggers),
            "present_outreach_tables": list(self.present_outreach_tables),
            "row_counts": dict(sorted(self.row_counts.items())),
        }


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _readonly_uri(path: str) -> str:
    return "file:" + path.replace("\\", "/").replace(" ", "%20") + "?mode=ro"


def collect_fingerprint(db_path: str) -> DatabaseFingerprint:
    """
    Verilen SQLite dosyasindan salt-okunur parmak izi cikarir.

    Raises:
        FileNotFoundError: dosya yoksa.
        sqlite3.DatabaseError: dosya SQLite degilse / bozuksa (cagiran
            bunu HARD_STOP'a cevirir — bkz. validator.py).

    Cagrildigi yerler:
    - app/legacy_adoption/validator.py::validate_legacy_db [PDSMR-R1D/Faz2]
    """
    if not os.path.isfile(db_path):
        raise FileNotFoundError(db_path)

    size = os.path.getsize(db_path)
    sha = _file_sha256(db_path)

    con = sqlite3.connect(_readonly_uri(db_path), uri=True)
    try:
        cur = con.cursor()
        integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
        fk_violations = len(cur.execute("PRAGMA foreign_key_check").fetchall())

        try:
            row = cur.execute("SELECT version_num FROM alembic_version").fetchone()
            alembic_version = row[0] if row else None
        except sqlite3.Error:
            alembic_version = None

        objects = cur.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table','view','trigger') AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_names = sorted(n for n, t in objects if t == "table")
        views = tuple(sorted(n for n, t in objects if t == "view"))
        triggers = tuple(sorted(n for n, t in objects if t == "trigger"))

        tables: dict[str, TableFingerprint] = {}
        for name in table_names:
            columns: dict[str, ColumnFingerprint] = {}
            pk_parts: list[tuple[int, str]] = []
            for _cid, col, ctype, notnull, dflt, pk_pos in cur.execute(
                f"PRAGMA table_info({name})"
            ).fetchall():
                columns[col] = ColumnFingerprint(
                    affinity=sqlite_affinity(ctype),
                    declared_type=(ctype or "").upper(),
                    notnull=bool(notnull),
                    has_default=dflt is not None,
                )
                if pk_pos:
                    pk_parts.append((pk_pos, col))

            fks = tuple(sorted(
                f"{r[3]}->{r[2]}.{r[4]}"
                for r in cur.execute(f"PRAGMA foreign_key_list({name})").fetchall()
            ))

            # DIKKAT: index_list satirlari once fetchall() ile MADDILESTIRILIR.
            # Ayni cursor uzerinde ic ice execute() calistirmak disdaki
            # iterasyonu sifirlar ve tablo basina yalniz ILK index okunur —
            # bu, sessizce eksik kanit uretir.
            index_rows = cur.execute(f"PRAGMA index_list({name})").fetchall()
            indexes: dict[str, IndexFingerprint] = {}
            for r in index_rows:
                idx_name, unique, origin, partial = r[1], bool(r[2]), r[3], bool(r[4])
                cols = tuple(
                    x[2] for x in cur.execute(f"PRAGMA index_info({idx_name})").fetchall()
                )
                indexes[idx_name] = IndexFingerprint(
                    columns=cols, unique=unique, origin=origin, partial=partial
                )

            sql_row = cur.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            ddl = (sql_row[0] if sql_row else "") or ""

            tables[name] = TableFingerprint(
                columns=columns,
                primary_key=tuple(c for _, c in sorted(pk_parts)),
                foreign_keys=fks,
                indexes=indexes,
                has_check_constraint="CHECK" in ddl.upper(),
            )

        row_counts: dict[str, int] = {}
        for t in CRITICAL_COUNT_TABLES:
            if t in tables:
                row_counts[t] = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

        present_outreach = tuple(t for t in OUTREACH_TABLES if t in tables)
    finally:
        con.close()

    return DatabaseFingerprint(
        file_path=db_path,
        file_size=size,
        file_sha256=sha,
        sqlite_version=sqlite3.sqlite_version,
        integrity_check=integrity,
        foreign_key_violations=fk_violations,
        alembic_version=alembic_version,
        tables=tables,
        views=views,
        triggers=triggers,
        row_counts=row_counts,
        present_outreach_tables=present_outreach,
    )
