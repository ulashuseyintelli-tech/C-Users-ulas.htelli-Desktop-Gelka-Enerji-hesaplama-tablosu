"""
PDSMR-R4 / FAZ 4B2 — UNVERSIONED (alembic_version'SIZ) KONTROLLU ADOPTION.

PROBLEM: gercek canli production DB'si `Base.metadata.create_all()` ile
kurulmustur; `alembic_version` tablosu HIC OLUSMAMISTIR. Bu yuzden:

  - normal `alembic upgrade` KULLANILAMAZ: alembic base'den baslar ve
    `001_initial`'in `create_table('invoices')` cagrisi MEVCUT tabloyla
    "table already exists" ile CAKISIR (owner tespiti),
  - ara `stamp` / elle "uygulanmis sayma" YASAKTIR,
  - `create_all()` YASAKTIR.

COZUM: gerekli her etki, GERCEK migration kaynaklarindan turetilen
CANONICAL REFERANS veritabanlarindan okunur. Referanslar TAZE, BOS bir
dosyada gercek `alembic upgrade` ile uretilir (WORKING uzerinde DEGIL) —
yani DDL tahmin edilmez, elle uydurulmaz; migration'in KENDI ciktisidir.

AKIS:
  0) yol guvenligi (realpath/samefile/junction/case-alias + yasak marker)
  1) SOURCE/ROLLBACK degismezligi + beklenen parmak izi
  2) canonical referanslar (REF_HEAD / REF_012 / REF_013)
  3) FRESH delta kapisi — onceki faz kanitini DEVRALMAZ
  4) SOURCE satir manifesti
  5) A: canonical tablo onarimi (SQLite resmi table-rebuild)
  6) B: canonical index sozlesmesi
  7) C: eksik revision etkileri (012 -> 013 -> S5)
  8) TERMINAL SERTIFIKASYON KAPISI (tamami gecmeden alembic_version YOK)
  9) terminal kaydi (tek islem) + YENIDEN tam sertifikasyon
 10) atomik yayim (yalniz disposable hedefe)
 11) DB DISINDA imzali audit

YASAKLAR (kod duzeyinde zorlanir):
  - production/kurulu-uygulama yoluna fiziksel olarak esleyen hedef
  - `SELECT *` veya kolon listesiz `INSERT`
  - kayipli/belirsiz cast
  - 011'in `updated_by` backfill'i (owner karari: sahte provenance uretir)
  - erken `alembic_version` olusturma/yazma

Cagrildigi yerler:
- tests/test_pdsmr_r4b2_unversioned_adoption.py [PDSMR-R4/Faz4B2]
  (Bilincli olarak HICBIR router/CLI/startup/installer yolundan cagrilmaz.)
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from . import alembic_runner as _ar
from .lineage import CANONICAL_HEAD, FullEffectClass, classify_full_lineage
from .pathsafety import is_forbidden_target, real_path, same_file

AUDIT_SUFFIX = ".pdsmr-r4b2-adoption-audit.json"
AUDIT_VERSION = "PDSMR-R4B2/1"

# Owner karari (Faz 4A dispozisyonu): 011'in ikinci backfill'i
# (`updated_by='system_migration'`) CALISTIRILMAZ — gecmiste olusmamis bir
# aktoru sonradan uydurur. Mevcut NULL degerler KORUNUR ve audit'e bu
# etiketle yazilir.
ACCEPTED_DATA_VARIANT = "ACCEPTED_LEGACY_DATA_VARIANT_UPDATED_BY_NULL"

REV_012 = "012_add_ptf_drift_log_table"
REV_013 = "013_extend_ptf_drift_severity"

# Faz 4A/4B2 fresh olcumunde KABUL EDILMIS sapma sinifi. Bunlarin DISINDA
# bir CONFLICT/UNKNOWN gorulurse fail-closed durulur.
_ACCEPTED_PRE_ADOPTION_CLASSES = frozenset({
    FullEffectClass.PRESENT_EXACT,
    FullEffectClass.ABSENT_SAFE_TO_APPLY,
    FullEffectClass.CONFLICT,
    FullEffectClass.UNKNOWN_OR_UNPROVABLE,
})

FAULT_POINTS = (
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
)


class AdoptionRefused(Exception):
    """On-kosul saglanmadi / fail-closed durdu."""


class InjectedFault(Exception):
    """YALNIZ test — gercek bir kesintiyi taklit eder."""


@dataclass
class UnversionedAdoptionReport:
    outcome: str  # "ADOPTED" | "ALREADY_ADOPTED"
    rebuilt_tables: tuple[str, ...] = ()
    created_indexes: tuple[str, ...] = ()
    dropped_indexes: tuple[str, ...] = ()
    applied_effects: tuple[str, ...] = ()
    terminal_revision: str = ""
    heads: int = 0
    integrity_check: str = ""
    foreign_key_violations: int = -1
    row_counts_before: dict[str, int] = field(default_factory=dict)
    row_counts_after: dict[str, int] = field(default_factory=dict)
    accepted_data_variants: tuple[str, ...] = ()
    source_sha256: str = ""
    rollback_sha256: str = ""
    published_to: str = ""
    details: dict[str, Any] = field(default_factory=dict)


# ── Temel yardimcilar ───────────────────────────────────────────────────
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for parca in iter(lambda: fh.read(1 << 20), b""):
            h.update(parca)
    return h.hexdigest()


def _ro(path: str) -> str:
    return "file:" + path.replace("\\", "/").replace(" ", "%20") + "?mode=ro"


def _fault(point: str, fault_at: Optional[str]) -> None:
    if fault_at == point:
        raise InjectedFault("enjekte edilmis kesinti: " + point)


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}


def _columns(con: sqlite3.Connection, tablo: str) -> list[str]:
    return [r[1] for r in con.execute('PRAGMA table_info("' + tablo + '")')]


def _table_sql(con: sqlite3.Connection, tablo: str) -> str:
    r = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tablo,)
    ).fetchone()
    if not r or not r[0]:
        raise AdoptionRefused(tablo + ": CREATE TABLE DDL'i okunamadi")
    return r[0]


def _index_sqls(con: sqlite3.Connection, tablo: str) -> list[tuple[str, str]]:
    """(ad, sql) — otomatik (sqlite_autoindex) index'ler HARIC."""
    return [
        (r[0], r[1]) for r in con.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
            "AND sql IS NOT NULL ORDER BY name", (tablo,)
        )
    ]


def check_constraints(ddl: str) -> tuple[str, ...]:
    """
    Bir CREATE TABLE DDL'inden CHECK kisiti IFADELERINI cikarir.

    NEDEN HAM METIN KARSILASTIRMASI DEGIL: create_all() ve alembic ayni
    semayi FARKLI YAZARLAR (tirnak bicimi, kolon SIRASI, satir sonlari).
    Bunlar anlam farki DEGILDIR. Anlam tasiyan sey CHECK ifadesinin
    KENDISIDIR; burada yalniz o cikarilir ve normalize edilir
    (bosluk daraltilir, tanimlayici tirnaklari kaldirilir).

    Cagrildigi yerler:
    - certify_canonical_equivalence() [PDSMR-R4/Faz4B2]
    - adopt_unversioned_copy() (onarilacak tablo tespiti)
    - tests/test_pdsmr_r4b2_unversioned_adoption.py
    """
    ifadeler: list[str] = []
    metin = ddl
    i = 0
    while True:
        j = metin.upper().find("CHECK", i)
        if j < 0:
            break
        k = metin.find("(", j)
        if k < 0:
            break
        derinlik, son = 0, -1
        for p in range(k, len(metin)):
            if metin[p] == "(":
                derinlik += 1
            elif metin[p] == ")":
                derinlik -= 1
                if derinlik == 0:
                    son = p
                    break
        if son < 0:
            break
        ic = " ".join(metin[k + 1:son].split()).replace('"', "").replace("'", "'")
        ifadeler.append(ic)
        i = son + 1
    return tuple(sorted(ifadeler))


def plan_rebuild_tables(working: str, ref: str) -> tuple[str, ...]:
    """
    Canonical'dan AYRISTIGI olculen ve bu yuzden YENIDEN INSA edilmesi
    gereken tablolari doner.

    Ayrisma olcutu: kolon sekli (tip/notnull/default), CHECK semantigi ve
    tablo-seviyesi UNIQUE. Kolon SIRASI olcut DEGILDIR (owner karari:
    DOCUMENTED_NON_SEMANTIC_COLUMN_ORDER_VARIANT) — ancak rebuild edilen
    tablo canonical DDL'den insa edildigi icin sirayi da canonical'dan alir.

    Cagrildigi yerler:
    - adopt_unversioned_copy() [PDSMR-R4/Faz4B2]
    - tests/test_pdsmr_r4b2_unversioned_adoption.py (fault matrisi parametresi)
    """
    wcon = sqlite3.connect(_ro(working), uri=True)
    rcon = sqlite3.connect(_ro(ref), uri=True)
    try:
        out: list[str] = []
        for t in sorted(_tables(wcon) & _tables(rcon)):
            wk = {r[1]: (r[2] or "", bool(r[3]), r[4] is not None)
                  for r in wcon.execute('PRAGMA table_info("' + t + '")')}
            rk = {r[1]: (r[2] or "", bool(r[3]), r[4] is not None)
                  for r in rcon.execute('PRAGMA table_info("' + t + '")')}
            wch = check_constraints(_table_sql(wcon, t))
            rch = check_constraints(_table_sql(rcon, t))
            wu = sorted(v[:3] for v in _index_signatures(wcon, t).values() if v[3] == "u")
            ru = sorted(v[:3] for v in _index_signatures(rcon, t).values() if v[3] == "u")
            if wk != rk or wch != rch or wu != ru:
                out.append(t)
        return tuple(out)
    finally:
        wcon.close()
        rcon.close()


def rebuild_fault_points(tablolar: tuple[str, ...]) -> tuple[str, ...]:
    """
    Her rebuild icin AYRI before/after kesinti noktalari.

    Tek bir `mid_each_rebuild` noktasi "HER rebuild kapsanmali" sartini
    KARSILAMAZ (yalniz bir yinelemede tetiklenir). Bu yuzden tablo basina
    UC ayri nokta uretilir — before / mid / after — ve test matrisi
    bunlarin TAMAMINI parametrik olarak kosar. `mid`, rebuild fiilen
    basladiktan (gecici tablo + veri kopyasi) fakat tablo canonical
    gorunur hale gelmeden (DROP/RENAME/COMMIT oncesi) tetiklenir.

    Cagrildigi yerler:
    - adopt_unversioned_copy() (dogrulama)
    - tests/test_pdsmr_r4b2_unversioned_adoption.py
    """
    noktalar: list[str] = []
    for t in tablolar:
        noktalar.append("before_rebuild:" + t)
        noktalar.append("mid_rebuild:" + t)
        noktalar.append("after_rebuild:" + t)
    return tuple(noktalar)


def _row_manifest(path: str) -> dict[str, int]:
    """Tum uygulama tablolarinin satir sayisi — preservation kanitinin temeli."""
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        return {t: con.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0]
                for t in sorted(_tables(con))}
    finally:
        con.close()


def _health(path: str) -> tuple[str, int]:
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        return (con.execute("PRAGMA integrity_check").fetchone()[0],
                len(con.execute("PRAGMA foreign_key_check").fetchall()))
    finally:
        con.close()


def _revisions(path: str) -> tuple[str, ...]:
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        return tuple(sorted(r[0] for r in con.execute("SELECT version_num FROM alembic_version")))
    except sqlite3.OperationalError:
        return ()
    finally:
        con.close()


# ── Canonical referanslar (GERCEK alembic, TAZE BOS dosyada) ────────────
def build_canonical_reference(scratch_dir: str, revision: str) -> str:
    """
    Verilen revizyona kadar GERCEK `alembic upgrade` ile TAZE, BOS bir
    referans DB uretir.

    Bu, "WORKING uzerinde alembic calistirmak" DEGILDIR — yasak olan odur.
    Buradaki amac migration'in KENDI urettigi DDL'i okumaktir; boylece
    hicbir DDL elle uydurulmaz veya tahmin edilmez.

    Cagrildigi yerler:
    - adopt_unversioned_copy() [PDSMR-R4/Faz4B2]
    - tests/test_pdsmr_r4b2_unversioned_adoption.py
    """
    hedef = os.path.join(scratch_dir, "REF_" + revision + ".db")
    if not os.path.exists(hedef):
        _ar.alembic_upgrade(hedef, revision)
    return hedef


# ── Yol guvenligi ───────────────────────────────────────────────────────
def assert_disposable_target(yol: str, *, etiket: str, source: str, rollback: str) -> None:
    """
    Hedefin production/kurulu-uygulama alanina FIZIKSEL olarak denk
    gelmedigini kanitlar. `confirm_disposable_copy=True` TEK BASINA
    yeterli guvenlik SAYILMAZ (owner karari) — bu kontrol HER ZAMAN calisir
    ve symlink/junction/case-alias'i realpath uzerinden ayrica dogrular.

    Cagrildigi yerler:
    - adopt_unversioned_copy() [PDSMR-R4/Faz4B2]
    - tests/test_pdsmr_r4b2_unversioned_adoption.py
    """
    marker = is_forbidden_target(yol)
    if marker:
        raise AdoptionRefused(etiket + " kurulu uygulama alaninda (marker=" + repr(marker) + ")")
    gercek = real_path(yol)
    marker2 = is_forbidden_target(gercek)
    if marker2:
        raise AdoptionRefused(
            etiket + " cozulmus yolu kurulu uygulama alanina isaret ediyor "
            "(marker=" + repr(marker2) + ")"
        )
    if not os.path.isdir(os.path.dirname(gercek)):
        raise AdoptionRefused(etiket + ": ust dizin yok — kimlik kanitlanamiyor")
    if same_file(yol, source):
        raise AdoptionRefused(etiket + " SOURCE ile ayni dosya")
    if same_file(yol, rollback):
        raise AdoptionRefused(etiket + " ROLLBACK ile ayni dosya")


# ── A: canonical tablo onarimi (SQLite resmi table-rebuild) ─────────────
def _rebuild_table(working: str, tablo: str, ref: str,
                   fault_at: Optional[str] = None) -> None:
    """
    `tablo`yu canonical referansin DDL'i ile YENIDEN INSA eder.

    Veri aktarimi ACIK kolon listeleriyle yapilir — `SELECT *` ve kolon
    listesiz `INSERT` KULLANILMAZ: canonical kolon SIRASI farkli oldugu
    icin ordinal kopya veriyi sessizce karistirirdi.
    """
    rcon = sqlite3.connect(_ro(ref), uri=True)
    try:
        canonical_ddl = _table_sql(rcon, tablo)
        canonical_kolonlar = _columns(rcon, tablo)
        canonical_indexler = _index_sqls(rcon, tablo)
    finally:
        rcon.close()

    con = sqlite3.connect(working)
    con.isolation_level = None
    try:
        mevcut_kolonlar = _columns(con, tablo)
        kayip = sorted(set(mevcut_kolonlar) - set(canonical_kolonlar))
        if kayip:
            raise AdoptionRefused(
                tablo + ": canonical'da BULUNMAYAN kolon(lar) var, veri kaybi riski: " + str(kayip)
            )
        ortak = [k for k in canonical_kolonlar if k in mevcut_kolonlar]
        if not ortak:
            raise AdoptionRefused(tablo + ": ortak kolon yok — yabanci sema")

        satir_once = con.execute('SELECT COUNT(*) FROM "' + tablo + '"').fetchone()[0]
        gecici = tablo + "_pdsmr_r4b2_new"
        yeni_ddl = None
        for desen in ('CREATE TABLE "' + tablo + '"', "CREATE TABLE " + tablo):
            if desen in canonical_ddl:
                yeni_ddl = canonical_ddl.replace(desen, 'CREATE TABLE "' + gecici + '"', 1)
                break
        if yeni_ddl is None:
            raise AdoptionRefused(tablo + ": canonical DDL'de tablo adi bulunamadi")

        kolon_listesi = ", ".join('"' + k + '"' for k in ortak)

        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("BEGIN")
        try:
            con.execute(yeni_ddl)
            con.execute(
                'INSERT INTO "' + gecici + '" (' + kolon_listesi + ") "
                "SELECT " + kolon_listesi + ' FROM "' + tablo + '"'
            )
            # MID: rebuild FIILEN BASLADI (gecici tablo olustu + veri
            # kopyalandi) ama tablo HENUZ TAMAMLANMADI ve canonical gorunur
            # DEGIL — DROP/RENAME/COMMIT yapilmadi. Bu nokta before/after
            # ALIAS'I DEGILDIR; kesinti burada olursa islem ROLLBACK ile
            # geri alinir ve tablo eski haliyle kalir.
            _fault("mid_rebuild:" + tablo, fault_at)
            con.execute('DROP TABLE "' + tablo + '"')
            con.execute('ALTER TABLE "' + gecici + '" RENAME TO "' + tablo + '"')
            for _ad, sql in canonical_indexler:
                con.execute(sql)
            ihlal = con.execute("PRAGMA foreign_key_check").fetchall()
            if ihlal:
                raise AdoptionRefused(
                    tablo + ": rebuild sonrasi " + str(len(ihlal)) + " FK ihlali — geri alindi"
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.execute("PRAGMA foreign_keys=ON")

        satir_sonra = con.execute('SELECT COUNT(*) FROM "' + tablo + '"').fetchone()[0]
        if satir_sonra != satir_once:
            raise AdoptionRefused(
                tablo + ": satir sayisi degisti " + str(satir_once) + " -> " + str(satir_sonra)
            )
    finally:
        con.close()


# ── C: eksik revision etkisi (tablo + index'leri referanstan) ───────────
def _create_table_from_reference(working: str, tablo: str, ref: str) -> None:
    rcon = sqlite3.connect(_ro(ref), uri=True)
    try:
        ddl = _table_sql(rcon, tablo)
        indexler = _index_sqls(rcon, tablo)
    finally:
        rcon.close()
    con = sqlite3.connect(working)
    con.isolation_level = None
    try:
        if tablo in _tables(con):
            raise AdoptionRefused(tablo + ": zaten mevcut — etki tekrar uygulanmaz")
        con.execute("BEGIN")
        try:
            con.execute(ddl)
            for _ad, sql in indexler:
                con.execute(sql)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


def _replace_empty_table_shape(working: str, tablo: str, ref: str) -> None:
    """
    BOS bir tablonun seklini referanstakiyle degistirir (013'un CHECK
    genisletmesi icin). Tablo BOS DEGILSE reddeder — veri kaybi olamaz.
    """
    con = sqlite3.connect(working)
    try:
        satir = con.execute('SELECT COUNT(*) FROM "' + tablo + '"').fetchone()[0]
        eski_indexler = [ad for ad, _ in _index_sqls(con, tablo)]
    finally:
        con.close()
    if satir:
        raise AdoptionRefused(
            tablo + ": " + str(satir) + " satir var — sekil degisimi reddedildi"
        )
    con = sqlite3.connect(working)
    con.isolation_level = None
    try:
        con.execute("BEGIN")
        try:
            for ad in eski_indexler:
                con.execute('DROP INDEX "' + ad + '"')
            con.execute('DROP TABLE "' + tablo + '"')
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()
    _create_table_from_reference(working, tablo, ref)


# ── B: canonical index sozlesmesi ───────────────────────────────────────
def _index_signatures(con: sqlite3.Connection, tablo: str) -> dict[str, tuple]:
    out = {}
    for r in con.execute('PRAGMA index_list("' + tablo + '")'):
        ad, essiz, kaynak, kismi = r[1], bool(r[2]), r[3], bool(r[4])
        kols = tuple(x[2] for x in con.execute('PRAGMA index_info("' + ad + '")'))
        out[ad] = (kols, essiz, kismi, kaynak)
    return out


def _assert_no_duplicate(con: sqlite3.Connection, tablo: str, kolonlar: tuple[str, ...]) -> int:
    """UNIQUE index kurulmadan ONCE zorunlu duplicate probe."""
    liste = ", ".join('"' + k + '"' for k in kolonlar)
    kosul = " AND ".join('"' + k + '" IS NOT NULL' for k in kolonlar)
    return con.execute(
        "SELECT COUNT(*) FROM (SELECT " + liste + ' FROM "' + tablo + '" '
        "WHERE " + kosul + " GROUP BY " + liste + " HAVING COUNT(*) > 1)"
    ).fetchone()[0]


def _reconcile_indexes(working: str, ref: str, fault_at: Optional[str] = None
                       ) -> tuple[list[str], list[str]]:
    """
    WORKING'in index kumesini canonical referansla BIREBIR esitler.

    - canonical'da olup WORKING'de olmayan -> olusturulur (UNIQUE ise ONCE
      duplicate probe; duplicate varsa HARD STOP, veri SILINMEZ/BIRLESTIRILMEZ)
    - WORKING'de olup canonical'da olmayan -> dusurulur (bilincli drift
      birakilmaz; owner karari)
    - naming-only varyantlar -> dogal olarak yukaridaki iki adimla canonical
      ad/origin'e cevrilir
    """
    olusturulan: list[str] = []
    dusurulen: list[str] = []
    rcon = sqlite3.connect(_ro(ref), uri=True)
    con = sqlite3.connect(working)
    con.isolation_level = None
    try:
        ortak_tablolar = sorted(_tables(con) & _tables(rcon))
        ilk = True
        for tablo in ortak_tablolar:
            ref_idx = {ad: sql for ad, sql in _index_sqls(rcon, tablo)}
            ref_sig = _index_signatures(rcon, tablo)
            cur_sig = _index_signatures(con, tablo)

            for ad, sql in sorted(ref_idx.items()):
                if ad in cur_sig:
                    if cur_sig[ad][:3] != ref_sig[ad][:3]:
                        raise AdoptionRefused(
                            tablo + "." + ad + ": ayni ad FARKLI sekil "
                            "working=" + str(cur_sig[ad][:3]) + " canonical=" + str(ref_sig[ad][:3])
                        )
                    continue
                kols, essiz, _kismi, _o = ref_sig[ad]
                if essiz:
                    cakisan = _assert_no_duplicate(con, tablo, kols)
                    if cakisan:
                        raise AdoptionRefused(
                            tablo + "." + ad + ": UNIQUE index kurulamaz — " + str(cakisan)
                            + " cakisan grup (veri SILINMEDI/BIRLESTIRILMEDI)"
                        )
                if not ilk:
                    _fault("mid_index_batch", fault_at)
                con.execute(sql)
                olusturulan.append(tablo + "." + ad)
                ilk = False

            for ad in sorted(cur_sig):
                if ad.startswith("sqlite_"):
                    continue
                if ad not in ref_idx:
                    con.execute('DROP INDEX "' + ad + '"')
                    dusurulen.append(tablo + "." + ad)
    finally:
        con.close()
        rcon.close()
    return olusturulan, dusurulen


# ── TERMINAL SERTIFIKASYON KAPISI ───────────────────────────────────────
def certify_canonical_equivalence(
    working: str, ref: str, *, expect_terminal: bool,
    source_manifest: Optional[dict[str, int]] = None,
) -> list[str]:
    """
    WORKING'in canonical referansla TAM esdegerligini dogrular.

    `expect_terminal=False` (terminal kayittan ONCE): `alembic_version`
    WORKING'de BULUNMAMALIDIR — erken-terminal-record guard budur.
    `expect_terminal=True`: TAM olarak (CANONICAL_HEAD,) olmalidir.

    Bos liste = TAM ESDEGER. Dolu liste = kapi GECILMEDI (fail-closed).

    Cagrildigi yerler:
    - adopt_unversioned_copy() [PDSMR-R4/Faz4B2] (terminal kayit ONCESI ve SONRASI)
    - is_certifiably_adopted() [idempotentlik]
    - tests/test_pdsmr_r4b2_unversioned_adoption.py
    """
    hatalar: list[str] = []
    wcon = sqlite3.connect(_ro(working), uri=True)
    rcon = sqlite3.connect(_ro(ref), uri=True)
    try:
        wt, rt = _tables(wcon), _tables(rcon)

        # 1) alembic_version: erken-terminal-record guard
        w_rev = _revisions(working)
        if expect_terminal:
            if w_rev != (CANONICAL_HEAD,):
                hatalar.append("terminal revizyon " + CANONICAL_HEAD + " degil: " + str(w_rev))
        else:
            if "alembic_version" in wt:
                hatalar.append(
                    "ERKEN TERMINAL KAYIT: esdegerlik kanitlanmadan alembic_version olusmus"
                )
        wt_k = wt - {"alembic_version"}
        rt_k = rt - {"alembic_version"}

        # 2) tablo kumesi exact
        if wt_k != rt_k:
            hatalar.append(
                "tablo kumesi farkli: eksik=" + str(sorted(rt_k - wt_k))
                + " fazla=" + str(sorted(wt_k - rt_k))
            )

        # 3) kolon semantigi + PK/FK + index + CHECK
        for t in sorted(wt_k & rt_k):
            wk = {r[1]: (r[2] or "", bool(r[3]), r[4] is not None)
                  for r in wcon.execute('PRAGMA table_info("' + t + '")')}
            rk = {r[1]: (r[2] or "", bool(r[3]), r[4] is not None)
                  for r in rcon.execute('PRAGMA table_info("' + t + '")')}
            if set(wk) != set(rk):
                hatalar.append(
                    t + ": kolon kumesi eksik=" + str(sorted(set(rk) - set(wk)))
                    + " fazla=" + str(sorted(set(wk) - set(rk)))
                )
            for k in sorted(set(wk) & set(rk)):
                if wk[k] != rk[k]:
                    hatalar.append(
                        t + "." + k + ": sekil working=" + str(wk[k]) + " canonical=" + str(rk[k])
                    )

            wpk = tuple(r[1] for r in wcon.execute('PRAGMA table_info("' + t + '")') if r[5])
            rpk = tuple(r[1] for r in rcon.execute('PRAGMA table_info("' + t + '")') if r[5])
            if wpk != rpk:
                hatalar.append(t + ": PK working=" + str(wpk) + " canonical=" + str(rpk))

            wfk = sorted((r[2], r[3], r[4]) for r in
                         wcon.execute('PRAGMA foreign_key_list("' + t + '")'))
            rfk = sorted((r[2], r[3], r[4]) for r in
                         rcon.execute('PRAGMA foreign_key_list("' + t + '")'))
            if wfk != rfk:
                hatalar.append(t + ": FK working=" + str(wfk) + " canonical=" + str(rfk))

            wi, ri = _index_signatures(wcon, t), _index_signatures(rcon, t)
            for ad in sorted(set(wi) | set(ri)):
                if ad not in wi:
                    hatalar.append(t + "." + ad + ": index EKSIK")
                elif ad not in ri:
                    hatalar.append(t + "." + ad + ": index FAZLA")
                elif wi[ad] != ri[ad]:
                    hatalar.append(
                        t + "." + ad + ": index sekli working=" + str(wi[ad])
                        + " canonical=" + str(ri[ad])
                    )

            # CHECK constraint SEMANTIGI (ham DDL metni DEGIL — bkz.
            # check_constraints() docstring'i: tirnak bicimi ve kolon
            # sirasi anlam farki tasimaz).
            wch = check_constraints(_table_sql(wcon, t))
            rch = check_constraints(_table_sql(rcon, t))
            if wch != rch:
                hatalar.append(
                    t + ": CHECK semantigi working=" + str(wch) + " canonical=" + str(rch))

        # 4) canonical YENI tablolar 0 satirla baslamali
        for t in sorted(rt_k - (set(source_manifest or {}))):
            if t in wt_k:
                n = wcon.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0]
                if n:
                    hatalar.append(t + ": canonical YENI tablo " + str(n) + " satirla basladi (0 olmali)")

        # 5) SOURCE -> WORKING satir korunumu
        if source_manifest is not None:
            for t, beklenen in sorted(source_manifest.items()):
                if t not in wt_k:
                    hatalar.append(t + ": SOURCE'ta vardi, WORKING'de YOK")
                    continue
                n = wcon.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0]
                if n != beklenen:
                    hatalar.append(
                        t + ": satir korunumu ihlali source=" + str(beklenen) + " working=" + str(n)
                    )
    finally:
        wcon.close()
        rcon.close()

    butunluk, fk = _health(working)
    if butunluk != "ok":
        hatalar.append("integrity_check=" + butunluk)
    if fk:
        hatalar.append("foreign_key_check " + str(fk) + " ihlal")
    return hatalar


def _write_terminal_record(working: str) -> None:
    """
    Terminal revizyon kaydini TEK ATOMIK islemde yazar.

    Bu bir "genel amacli stamp" DEGILDIR: revizyon adi cagirandan
    alinmaz, sabittir ve YALNIZ tam esdegerlik kanitlandiktan SONRA
    bu modulun icinden cagrilir.
    """
    con = sqlite3.connect(working)
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            if "alembic_version" in _tables(con):
                raise AdoptionRefused("alembic_version zaten var — erken kayit belirtisi")
            con.execute(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
            con.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (CANONICAL_HEAD,))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


def is_certifiably_adopted(working: str, ref: str,
                           source_manifest: Optional[dict[str, int]] = None) -> bool:
    """
    DB'nin BAGIMSIZ olarak adopt edilmis oldugunu kanitlar (audit'e guvenmez).

    Cagrildigi yerler:
    - adopt_unversioned_copy() [idempotentlik kontrolu]
    - tests/test_pdsmr_r4b2_unversioned_adoption.py
    """
    if _revisions(working) != (CANONICAL_HEAD,):
        return False
    return not certify_canonical_equivalence(
        working, ref, expect_terminal=True, source_manifest=source_manifest
    )


# ── Audit (DB'nin DISINDA, sanitize) ────────────────────────────────────
def _audit_path(working: str) -> str:
    return working + AUDIT_SUFFIX


def _write_audit(working: str, payload: dict) -> None:
    metin = json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True)
    imza = hashlib.sha256(metin.encode("utf-8")).hexdigest()
    with open(_audit_path(working), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(
            {"audit": payload, "audit_sha256": imza, "audit_version": AUDIT_VERSION},
            indent=1, ensure_ascii=False, sort_keys=True,
        ))


def read_audit(working: str) -> Optional[dict]:
    """Audit'i okur ve imzasini dogrular; bozuksa None."""
    yol = _audit_path(working)
    if not os.path.isfile(yol):
        return None
    try:
        with open(yol, encoding="utf-8") as fh:
            paket = json.load(fh)
        beklenen = hashlib.sha256(
            json.dumps(paket["audit"], indent=1, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return paket["audit"] if beklenen == paket.get("audit_sha256") else None
    except (OSError, ValueError, KeyError):
        return None


# ── ANA AKIS ────────────────────────────────────────────────────────────
def adopt_unversioned_copy(
    working_path: str,
    *,
    source_path: str,
    rollback_path: str,
    canonical_target: str,
    scratch_dir: str,
    expected_source_sha256: str,
    confirm_disposable_copy: bool = False,
    fault_at: Optional[str] = None,
) -> UnversionedAdoptionReport:
    """
    Unversioned bir DISPOSABLE kopyayi canonical head'e adopt eder.

    Args:
        working_path: Mutasyona ugrayacak calisma kopyasi.
        source_path: Degismez kaynak. ASLA yazilmaz.
        rollback_path: Ayri geri-donus yedegi. ASLA yazilmaz.
        canonical_target: Sertifikali sonucun ATOMIK yayimlanacagi
            DISPOSABLE hedef. Production yoluna fiziksel olarak eslerse ret.
        scratch_dir: Canonical referanslarin uretilecegi gecici dizin.
        expected_source_sha256: Beklenen kaynak parmak izi; sapma -> ret.
        confirm_disposable_copy: Cagiranin acik onayi (TEK BASINA yeterli
            guvenlik SAYILMAZ; yol kontrolleri her zaman calisir).
        fault_at: YALNIZ test — belirtilen noktada kesinti enjekte eder.

    Raises:
        AdoptionRefused: on-kosul saglanmadi / fail-closed.
        InjectedFault: test kesintisi.

    Cagrildigi yerler:
    - tests/test_pdsmr_r4b2_unversioned_adoption.py [PDSMR-R4/Faz4B2]
    """
    if not confirm_disposable_copy:
        raise AdoptionRefused("confirm_disposable_copy=True verilmedi")
    _dinamik_rebuild_fault = (
        fault_at is not None
        and fault_at.split(":", 1)[0] in ("before_rebuild", "mid_rebuild", "after_rebuild")
        and ":" in fault_at
    )
    if fault_at is not None and fault_at not in FAULT_POINTS and not _dinamik_rebuild_fault:
        raise AdoptionRefused("bilinmeyen fault noktasi: " + str(fault_at))
    for yol, ad in ((source_path, "SOURCE"), (rollback_path, "ROLLBACK"),
                    (working_path, "WORKING")):
        if not os.path.isfile(yol):
            raise AdoptionRefused(ad + " dosyasi yok")

    # 0) yol guvenligi — confirm bayragindan BAGIMSIZ
    assert_disposable_target(working_path, etiket="WORKING",
                             source=source_path, rollback=rollback_path)
    assert_disposable_target(canonical_target, etiket="canonical_target",
                             source=source_path, rollback=rollback_path)
    if same_file(source_path, rollback_path):
        raise AdoptionRefused("SOURCE ile ROLLBACK ayni dosya")

    # 1) degismezlik + parmak izi
    kaynak_hash = _sha256(source_path)
    if kaynak_hash != expected_source_sha256:
        raise AdoptionRefused("SOURCE parmak izi beklenenden FARKLI — adoption reddedildi")
    rollback_hash = _sha256(rollback_path)
    if rollback_hash != kaynak_hash:
        raise AdoptionRefused("ROLLBACK, SOURCE ile byte-identik degil")
    for yol, ad in ((source_path, "SOURCE"), (rollback_path, "ROLLBACK"),
                    (working_path, "WORKING")):
        butunluk, fk = _health(yol)
        if butunluk != "ok":
            raise AdoptionRefused(ad + ": integrity_check=" + butunluk)
        if fk:
            raise AdoptionRefused(ad + ": " + str(fk) + " FK ihlali")

    # 2) canonical referanslar (GERCEK alembic, taze bos dosyalarda)
    os.makedirs(scratch_dir, exist_ok=True)
    ref_head = build_canonical_reference(scratch_dir, CANONICAL_HEAD)
    ref_012 = build_canonical_reference(scratch_dir, REV_012)
    ref_013 = build_canonical_reference(scratch_dir, REV_013)

    source_manifest = _row_manifest(source_path)

    # Idempotentlik: zaten adopt edilmis mi?
    if is_certifiably_adopted(working_path, ref_head, source_manifest):
        return UnversionedAdoptionReport(
            outcome="ALREADY_ADOPTED",
            terminal_revision=CANONICAL_HEAD,
            heads=_ar.alembic_heads_count(working_path),
            integrity_check="ok",
            foreign_key_violations=0,
            row_counts_before=source_manifest,
            row_counts_after=_row_manifest(working_path),
            accepted_data_variants=(ACCEPTED_DATA_VARIANT,),
            source_sha256=kaynak_hash,
            rollback_sha256=rollback_hash,
            details={"audit_present": read_audit(working_path) is not None},
        )

    # 3) FRESH delta kapisi — onceki faz kanitini DEVRALMAZ
    _fault("before_working_copy", fault_at)
    siniflar = classify_full_lineage(working_path)
    _fault("after_working_copy", fault_at)
    taninmayan = [s for s in siniflar if s.effect_class not in _ACCEPTED_PRE_ADOPTION_CLASSES]
    if taninmayan:
        raise AdoptionRefused(
            "fresh siniflandirmada taninmayan sinif: "
            + "; ".join(s.revision + "=" + s.effect_class.value for s in taninmayan)
        )
    if _revisions(working_path):
        raise AdoptionRefused(
            "WORKING zaten alembic_version tasiyor — bu modul UNVERSIONED DB icindir"
        )

    # ── 5) A: canonical tablo onarimi ──────────────────────────────────
    onarilacak = list(plan_rebuild_tables(working_path, ref_head))
    wcon = sqlite3.connect(_ro(working_path), uri=True)
    rcon = sqlite3.connect(_ro(ref_head), uri=True)
    try:
        eksik_tablolar = sorted(_tables(rcon) - _tables(wcon) - {"alembic_version"})
    finally:
        wcon.close()
        rcon.close()

    # Dinamik rebuild fault noktasi verildiyse hedef tablo GERCEKTEN
    # onarim listesinde olmali — aksi halde kesinti sessizce hic tetiklenmez
    # ve test yanlislikla "gecti" sanilir. Bu, deterministik bir rettir.
    if _dinamik_rebuild_fault:
        _hedef = fault_at.split(":", 1)[1]
        if _hedef not in onarilacak:
            raise AdoptionRefused(
                "rebuild fault hedefi onarim listesinde yok: " + _hedef
                + " (liste=" + str(onarilacak) + ")"
            )

    _fault("before_first_rebuild", fault_at)
    for i, t in enumerate(onarilacak):
        if i:
            _fault("mid_each_rebuild", fault_at)
        # HER rebuild icin AYRI before/mid/after noktasi (owner sarti).
        _fault("before_rebuild:" + t, fault_at)
        # `mid_rebuild:<t>` _rebuild_table'in ICINDE, transaction acikken
        # tetiklenir (bkz. oradaki yorum) — before/after alias'i DEGILDIR.
        _rebuild_table(working_path, t, ref_head, fault_at=fault_at)
        _fault("after_rebuild:" + t, fault_at)
    _fault("after_rebuild_batch", fault_at)

    # ── 7) C: eksik revision etkileri (012 -> 013 -> S5) ───────────────
    uygulanan: list[str] = []
    if "ptf_drift_log" in eksik_tablolar:
        _fault("before_012_effect", fault_at)
        _create_table_from_reference(working_path, "ptf_drift_log", ref_012)
        _fault("after_012_effect", fault_at)
        con = sqlite3.connect(_ro(working_path), uri=True)
        try:
            if con.execute("SELECT COUNT(*) FROM ptf_drift_log").fetchone()[0]:
                raise AdoptionRefused("012: ptf_drift_log BOS baslamadi")
            ddl_012 = " ".join(_table_sql(con, "ptf_drift_log").split())
        finally:
            con.close()
        if "'low', 'high'" not in ddl_012 and '"low", "high"' not in ddl_012:
            raise AdoptionRefused("012: severity CHECK 2-degerli canonical sekilde degil")
        uygulanan.append(REV_012)

        _fault("before_013_effect", fault_at)
        _replace_empty_table_shape(working_path, "ptf_drift_log", ref_013)
        _fault("after_013_effect", fault_at)
        rc = sqlite3.connect(_ro(ref_013), uri=True)
        wc = sqlite3.connect(_ro(working_path), uri=True)
        try:
            if " ".join(_table_sql(wc, "ptf_drift_log").split()) != \
               " ".join(_table_sql(rc, "ptf_drift_log").split()):
                raise AdoptionRefused("013: final CHECK semantigi canonical referansla eslesmiyor")
        finally:
            rc.close()
            wc.close()
        uygulanan.append(REV_013)

    _fault("before_f4_effect", fault_at)
    for t in eksik_tablolar:
        if t == "ptf_drift_log":
            continue
        _create_table_from_reference(working_path, t, ref_head)
        con = sqlite3.connect(_ro(working_path), uri=True)
        try:
            if con.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0]:
                raise AdoptionRefused(t + ": canonical YENI tablo BOS baslamadi")
        finally:
            con.close()
        uygulanan.append("table:" + t)
    _fault("after_f4_effect", fault_at)
    _fault("before_beda_effect", fault_at)
    _fault("after_beda_effect", fault_at)

    # ── 6) B: canonical index sozlesmesi ───────────────────────────────
    _fault("before_index_batch", fault_at)
    olusturulan, dusurulen = _reconcile_indexes(working_path, ref_head, fault_at=fault_at)
    _fault("after_index_batch", fault_at)

    # ── 8) TERMINAL SERTIFIKASYON KAPISI ───────────────────────────────
    _fault("before_terminal_certification", fault_at)
    hatalar = certify_canonical_equivalence(
        working_path, ref_head, expect_terminal=False, source_manifest=source_manifest)
    if hatalar:
        raise AdoptionRefused("terminal kapisi GECILEMEDI: " + "; ".join(hatalar[:8]))

    # ── 9) terminal kayit + YENIDEN tam sertifikasyon ──────────────────
    _fault("before_terminal_record", fault_at)
    _write_terminal_record(working_path)
    _fault("after_terminal_record", fault_at)
    hatalar2 = certify_canonical_equivalence(
        working_path, ref_head, expect_terminal=True, source_manifest=source_manifest)
    if hatalar2:
        raise AdoptionRefused(
            "terminal kayit SONRASI sertifikasyon basarisiz: " + "; ".join(hatalar2[:8]))

    # SOURCE/ROLLBACK hala degismemis mi (savunma amacli son kontrol)
    if _sha256(source_path) != kaynak_hash:
        raise AdoptionRefused("SOURCE DEGISTI — adoption gecersiz")
    if _sha256(rollback_path) != rollback_hash:
        raise AdoptionRefused("ROLLBACK DEGISTI — adoption gecersiz")

    # ── 10) atomik yayim ───────────────────────────────────────────────
    _fault("before_atomic_publish", fault_at)
    os.replace(working_path, canonical_target)
    _fault("after_atomic_publish", fault_at)

    butunluk, fk = _health(canonical_target)
    rapor = UnversionedAdoptionReport(
        outcome="ADOPTED",
        rebuilt_tables=tuple(onarilacak),
        created_indexes=tuple(olusturulan),
        dropped_indexes=tuple(dusurulen),
        applied_effects=tuple(uygulanan),
        terminal_revision=CANONICAL_HEAD,
        heads=_ar.alembic_heads_count(canonical_target),
        integrity_check=butunluk,
        foreign_key_violations=fk,
        row_counts_before=source_manifest,
        row_counts_after=_row_manifest(canonical_target),
        accepted_data_variants=(ACCEPTED_DATA_VARIANT,),
        source_sha256=kaynak_hash,
        rollback_sha256=rollback_hash,
        published_to=canonical_target,
    )

    # ── 11) audit (DB DISINDA, PII/secret YOK) ─────────────────────────
    _fault("before_audit_commit", fault_at)
    _write_audit(canonical_target, {
        "outcome": rapor.outcome,
        "terminal_revision": rapor.terminal_revision,
        "rebuilt_tables": list(rapor.rebuilt_tables),
        "created_indexes": list(rapor.created_indexes),
        "dropped_indexes": list(rapor.dropped_indexes),
        "applied_effects": list(rapor.applied_effects),
        "accepted_data_variants": list(rapor.accepted_data_variants),
        "row_counts_before": rapor.row_counts_before,
        "row_counts_after": rapor.row_counts_after,
        "source_sha256": rapor.source_sha256,
        "rollback_sha256": rapor.rollback_sha256,
        "integrity_check": rapor.integrity_check,
        "foreign_key_violations": rapor.foreign_key_violations,
    })
    _fault("after_audit_commit", fault_at)
    return rapor


__all__ = [
    "ACCEPTED_DATA_VARIANT",
    "AUDIT_SUFFIX",
    "AUDIT_VERSION",
    "FAULT_POINTS",
    "REV_012",
    "REV_013",
    "AdoptionRefused",
    "InjectedFault",
    "UnversionedAdoptionReport",
    "adopt_unversioned_copy",
    "assert_disposable_target",
    "build_canonical_reference",
    "certify_canonical_equivalence",
    "check_constraints",
    "is_certifiably_adopted",
    "plan_rebuild_tables",
    "read_audit",
    "rebuild_fault_points",
]
