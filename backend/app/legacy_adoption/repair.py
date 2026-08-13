"""
PDSMR-R1D / 2R2 — KANONIK ONARIM SOZLESMESI (YALNIZ DISPOSABLE KOPYA).

Bu modul, owner'in ADI ADINA yetkilendirdigi TEK onarimi uygular:
  1) incidents.dedupe_bucket  -> INTEGER affinity
  2) incidents.deduction_total-> INTEGER affinity
  3) migration 004'un ALTI canonical index'ini yaratir

KARANTINA — bu modul FAZ 3 DEGILDIR:
  - Hicbir router / CLI / startup yolundan CAGRILMAZ. Bunu
    tests/test_legacy_adoption_validator.py'deki wiring testi korur.
  - Cagiranin `confirm_disposable_copy=True` demesi ZORUNLUDUR.
  - Kurulu uygulamanin dizinine denk gelen bir yol verilirse
    FIZIKSEL OLARAK reddeder (policy.FORBIDDEN_REPAIR_PATH_MARKERS).
  - Gercek production DB uzerinde CALISTIRILMAZ.

SQLite'in ALTER COLUMN'u olmadigi icin onarim bir TABLO YENIDEN INSASIDIR.
Yeni tablo DDL'i, mevcut tablonun DDL'inden TURETILIR: yalniz iki kolonun
tipi degistirilir, diger her kolon/kisit oldugu gibi korunur. Boylece
"canonical olmayan ama mevcut" kolonlar da kaybolmaz.

Idempotent: zaten kanonik olan bir DB'de hicbir sey yazmaz.

Cagrildigi yerler:
- tests/test_legacy_adoption_validator.py [PDSMR-R1D/2R2 provasi]
"""
from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from . import policy
from .fingerprint import sqlite_affinity


class RepairRefused(Exception):
    """Onarim on-kosullari saglanmadi. Hicbir sey yazilmadi."""


@dataclass
class RepairReport:
    outcome: str  # "REPAIRED" | "ALREADY_CANONICAL"
    columns_converted: tuple[str, ...] = ()
    indexes_created: tuple[str, ...] = ()
    rows_before: int = 0
    rows_after: int = 0
    preserved_columns: int = 0
    preserved_indexes: tuple[str, ...] = ()
    integrity_check: str = ""
    foreign_key_violations: int = -1
    details: dict[str, Any] = field(default_factory=dict)


def _assert_path_is_repairable(db_path: str) -> None:
    normalize = os.path.abspath(db_path).lower()
    for marker in policy.FORBIDDEN_REPAIR_PATH_MARKERS:
        if marker.lower() in normalize:
            raise RepairRefused(
                f"yol kurulu uygulama alanina denk geliyor (marker={marker!r}); "
                "onarim yalniz disposable kopyada calisir"
            )
    if not os.path.isfile(db_path):
        raise RepairRefused("dosya bulunamadi")


def _table_ddl(con: sqlite3.Connection, table: str) -> str:
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row or not row[0]:
        raise RepairRefused(f"{table} tablosunun DDL'i okunamadi")
    return row[0]


def _retype_column_in_ddl(ddl: str, column: str, new_type: str) -> str:
    """
    DDL metninde YALNIZ verilen kolonun tip bildirimini degistirir.

    Gercek uretim DDL'i HIBRITTIR: create_all() ile uretilen kolonlar
    `,\\n\\tad TIP` bicimindeyken, sonradan ALTER TABLE ADD COLUMN ile
    eklenenler ayni satira `, ad TIP` olarak eklenir. Bu yuzden kolon adi
    satir basina degil, kendisinden once gelen `,` ya da `(` ayiricisina
    gore aranir; ayirici sarti `x_dedupe_bucket` gibi bir kolona
    yanlislikla dokunulmasini da onler.

    Eslesme TEKIL degilse islem reddedilir — belirsiz bir DDL uzerinde
    tahminle degisiklik yapmak, hic yapmamaktan daha tehlikelidir.
    """
    desen = re.compile(
        r'([,(]\s*"?' + re.escape(column)
        + r'"?\s+)([A-Za-z_][A-Za-z0-9_]*(?:\s*\(\s*\d+\s*(?:,\s*\d+\s*)?\))?)'
    )
    eslesmeler = desen.findall(ddl)
    if len(eslesmeler) != 1:
        raise RepairRefused(
            f"{column} kolonunun tip bildirimi DDL'de tekil degil (adet={len(eslesmeler)})"
        )
    return desen.sub(lambda m: m.group(1) + new_type, ddl, count=1)


def _is_already_canonical(con: sqlite3.Connection) -> bool:
    tipler = {
        r[1]: sqlite_affinity(r[2])
        for r in con.execute("PRAGMA table_info(incidents)").fetchall()
    }
    for _tablo, kolon in policy.CANONICAL_INTEGER_COLUMNS:
        if tipler.get(kolon) != "INTEGER":
            return False
    mevcut = {r[1] for r in con.execute("PRAGMA index_list(incidents)").fetchall()}
    gerekli = {s["name"] for s in policy.REQUIRED_CANONICAL_INDEXES["incidents"]}
    return gerekli.issubset(mevcut)


def _canonical_index_sql(spec: dict) -> str:
    essiz = "UNIQUE " if spec["unique"] else ""
    kolonlar = ", ".join(spec["columns"])
    return f'CREATE {essiz}INDEX {spec["name"]} ON incidents ({kolonlar})'


def repair_incidents_canonical(
    db_path: str,
    *,
    confirm_disposable_copy: bool = False,
) -> RepairReport:
    """
    incidents tablosunu kanonik hale getirir. YALNIZ DISPOSABLE KOPYA.

    Args:
        db_path: Onarilacak SQLite KOPYASI. Kurulu uygulama yoluna denk
            gelirse RepairRefused firlatir.
        confirm_disposable_copy: Cagiranin acik onayi. False ise hicbir
            sey yapilmaz — kazayla calistirmaya karsi kasitli surtunme.

    Raises:
        RepairRefused: on-kosul saglanmadi; DB'ye HICBIR SEY yazilmadi.
            Ozellikle: donusum bir degeri kaybediyorsa (kesirli/ sayisal
            olmayan) islem BASLAMADAN reddedilir.

    Cagrildigi yerler:
    - tests/test_legacy_adoption_validator.py [PDSMR-R1D/2R2]
    """
    if not confirm_disposable_copy:
        raise RepairRefused("confirm_disposable_copy=True verilmedi")
    _assert_path_is_repairable(db_path)

    con = sqlite3.connect(db_path)
    con.isolation_level = None  # islem sinirlarini biz yonetiyoruz
    try:
        incidents_var = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='incidents'"
        ).fetchone()[0]
        if not incidents_var:
            raise RepairRefused("hedefte incidents tablosu YOK — bu legacy ailesi degil")

        if _is_already_canonical(con):
            return RepairReport(outcome="ALREADY_CANONICAL")

        # ── On-kosul: donusum KAYIPSIZ olmali ───────────────────────────
        # Kontrol sirasi ONEMLIDIR: en SPESIFIK teshis once verilir.
        # 'bugun' gibi bir deger her iki kontrole de takilir; hangi
        # mesajin dondugu, operatorun ne yapacagini belirler.
        for _tablo, kolon in policy.CANONICAL_INTEGER_COLUMNS:
            blob = con.execute(
                f"SELECT COUNT(*) FROM incidents WHERE typeof({kolon})='blob'"
            ).fetchone()[0]
            if blob:
                raise RepairRefused(f"incidents.{kolon}: {blob} blob deger — donusum TANIMSIZ")

            metin_bozuk = con.execute(
                f"SELECT COUNT(*) FROM incidents WHERE typeof({kolon})='text' "
                f"AND NOT (TRIM({kolon}) GLOB '[0-9]*' OR TRIM({kolon}) GLOB '-[0-9]*')"
            ).fetchone()[0]
            if metin_bozuk:
                raise RepairRefused(
                    f"incidents.{kolon}: {metin_bozuk} sayisal-olmayan metin — "
                    "donusum BELIRSIZ, onarim reddedildi"
                )

            kayipli = con.execute(
                f"SELECT COUNT(*) FROM incidents WHERE {kolon} IS NOT NULL "
                f"AND CAST({kolon} AS INTEGER) <> {kolon}"
            ).fetchone()[0]
            if kayipli:
                raise RepairRefused(
                    f"incidents.{kolon}: {kayipli} satirda INTEGER donusumu "
                    "degeri DEGISTIRIRDI — onarim reddedildi"
                )

        eski_kolonlar = [r[1] for r in con.execute("PRAGMA table_info(incidents)").fetchall()]
        satir_once = con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        eski_index_sql = [
            r[0] for r in con.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='incidents' "
                "AND sql IS NOT NULL ORDER BY name"
            ).fetchall()
        ]
        eski_index_adlari = tuple(sorted(
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='incidents' "
                "AND sql IS NOT NULL"
            ).fetchall()
        ))

        ddl = _table_ddl(con, "incidents")
        yeni_ddl = ddl
        for _tablo, kolon in policy.CANONICAL_INTEGER_COLUMNS:
            yeni_ddl = _retype_column_in_ddl(yeni_ddl, kolon, "INTEGER")
        yeni_ddl = re.sub(
            r'CREATE\s+TABLE\s+"?incidents"?', 'CREATE TABLE "incidents_pdsmr_new"',
            yeni_ddl, count=1,
        )

        kolon_listesi = ", ".join(f'"{c}"' for c in eski_kolonlar)
        secim = ", ".join(
            f'CAST("{c}" AS INTEGER)' if any(c == k for _t, k in policy.CANONICAL_INTEGER_COLUMNS)
            else f'"{c}"'
            for c in eski_kolonlar
        )

        # ── SQLite'in resmi "tablo semasini degistirme" yordami ─────────
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("BEGIN")
        try:
            con.execute(yeni_ddl)
            con.execute(
                f'INSERT INTO "incidents_pdsmr_new" ({kolon_listesi}) '
                f'SELECT {secim} FROM "incidents"'
            )
            con.execute('DROP TABLE "incidents"')
            con.execute('ALTER TABLE "incidents_pdsmr_new" RENAME TO "incidents"')

            for sql in eski_index_sql:
                con.execute(sql)

            olusturulan = []
            mevcut = {r[1] for r in con.execute("PRAGMA index_list(incidents)").fetchall()}
            for spec in policy.REQUIRED_CANONICAL_INDEXES["incidents"]:
                if spec["name"] in mevcut:
                    continue
                con.execute(_canonical_index_sql(spec))
                olusturulan.append(spec["name"])

            ihlaller = con.execute("PRAGMA foreign_key_check").fetchall()
            if ihlaller:
                raise RepairRefused(f"onarim sonrasi {len(ihlaller)} FK ihlali — geri alindi")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.execute("PRAGMA foreign_keys=ON")

        satir_sonra = con.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        yeni_kolonlar = [r[1] for r in con.execute("PRAGMA table_info(incidents)").fetchall()]
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        fk = len(con.execute("PRAGMA foreign_key_check").fetchall())

        return RepairReport(
            outcome="REPAIRED",
            columns_converted=tuple(k for _t, k in policy.CANONICAL_INTEGER_COLUMNS),
            indexes_created=tuple(olusturulan),
            rows_before=satir_once,
            rows_after=satir_sonra,
            preserved_columns=len(yeni_kolonlar),
            preserved_indexes=eski_index_adlari,
            integrity_check=integrity,
            foreign_key_violations=fk,
            details={"columns_before": len(eski_kolonlar)},
        )
    finally:
        con.close()


__all__ = ["RepairRefused", "RepairReport", "repair_incidents_canonical"]
