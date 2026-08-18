"""
PDSMR-R1D / FAZ 3 — REVIZYON ETKI ESDEGERLIGI (SALT-OKUNUR SINIFLANDIRMA).

Uretim `013_extend_ptf_drift_severity` dalindadir. Canonical head
`351d314819d5` iki dalin birlesimidir:

    011_market_prices_ptf_admin
      ├─ (A dali / uretimin dali) 012 -> 013 ────────────┐
      └─ (B dali / canonical)  a93beeaddf82 -> ... -> 9d4a2f6b18ce ─┤
                                                        └─> 351d314819d5

Head'e ulasmak icin alembic'in IKI ebeveyni de "uygulanmis" gormesi gerekir.
B dalinin her revizyonu, ETKISININ hedef DB'de gercekten bulunup
bulunmadigina gore siniflandirilir:

    A) EXACT_EFFECT_ALREADY_PRESENT   -> stamp edilebilir
    B) ACCEPTED_VERIFIED_VARIANT      -> stamp edilebilir
    C) EFFECT_MISSING_AND_SAFE_TO_RUN -> NORMAL CALISTIRILIR, ASLA stamp EDILMEZ
    D) CONFLICT_OR_UNKNOWN            -> HARD_STOP

Siniflandirma TABLO ADI VARLIGINA bakarak yapilmaz: her revizyonun
urettigi somut tablo/kolon/index/kisit tek tek probe edilir.

Cagrildigi yerler:
- app/legacy_adoption/adoption.py [PDSMR-R1D/Faz3]
- tests/test_legacy_adoption_phase3.py
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

PRODUCTION_BRANCH_TIP = "013_extend_ptf_drift_severity"
CANONICAL_HEAD = "351d314819d5"


class EffectClass(str, Enum):
    EXACT_EFFECT_ALREADY_PRESENT = "A_EXACT_EFFECT_ALREADY_PRESENT"
    ACCEPTED_VERIFIED_VARIANT = "B_ACCEPTED_VERIFIED_VARIANT"
    EFFECT_MISSING_AND_SAFE_TO_RUN = "C_EFFECT_MISSING_AND_SAFE_TO_RUN"
    CONFLICT_OR_UNKNOWN = "D_CONFLICT_OR_UNKNOWN"


# ── Probe yardimcilari (hepsi salt-okunur) ──────────────────────────────
def _tables(con: sqlite3.Connection) -> set[str]:
    return {
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _index_names(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA index_list({table})").fetchall()}


def _index_columns(con: sqlite3.Connection, table: str) -> list[tuple[str, ...]]:
    rows = con.execute(f"PRAGMA index_list({table})").fetchall()
    out = []
    for r in rows:
        out.append(tuple(x[2] for x in con.execute(f"PRAGMA index_info({r[1]})").fetchall()))
    return out


@dataclass(frozen=True)
class RevisionEffect:
    """Bir revizyonun somut, olculebilir semantik etkisi."""

    revision: str
    summary: str
    present: Callable[[sqlite3.Connection], bool]
    evidence: Callable[[sqlite3.Connection], str]
    conflicts_if_run: Callable[[sqlite3.Connection], Optional[str]]


def _contract_v1_present(con):
    gerekli = {
        "contracts", "customer_legal_profiles", "customer_authorized_representatives",
        "uploaded_reference_documents", "document_extraction_runs",
        "document_field_candidates",
    }
    return gerekli.issubset(_tables(con))


def _contract_v1_evidence(con):
    gerekli = {
        "contracts", "customer_legal_profiles", "customer_authorized_representatives",
        "uploaded_reference_documents", "document_extraction_runs",
        "document_field_candidates",
    }
    var = sorted(gerekli & _tables(con))
    return f"tablolar mevcut={var} ({len(var)}/{len(gerekli)})"


def _dedup_widen_present(con):
    if "uploaded_reference_documents" not in _tables(con):
        return False
    hedef = ("tenant_id", "customer_id", "sha256", "document_type")
    return hedef in _index_columns(con, "uploaded_reference_documents")


def _dedup_widen_evidence(con):
    if "uploaded_reference_documents" not in _tables(con):
        return "tablo YOK"
    return f"uploaded_reference_documents index imzalari={_index_columns(con, 'uploaded_reference_documents')}"


def _s2_present(con):
    return {"activities", "tasks"}.issubset(_tables(con))


def _s4_present(con):
    return {"prospect_companies", "prospect_sources", "prospect_contacts"}.issubset(_tables(con))


_OUTREACH = ("outreach_messages", "outreach_templates", "suppression_entries")


def _outreach_present(con):
    return set(_OUTREACH).issubset(_tables(con))


_LEGAL_TYPE_COLS = (
    "verified_legal_type", "verified_legal_type_note", "verified_legal_type_set_at",
)


def _legal_type_present(con):
    if "prospect_companies" not in _tables(con):
        return False
    return set(_LEGAL_TYPE_COLS).issubset(_columns(con, "prospect_companies"))


_PRICING_TABLES = (
    "analysis_cache", "consumption_hourly_data", "consumption_profiles",
    "data_versions", "hourly_market_prices", "monthly_yekdem_prices",
    "price_change_history", "profile_templates",
)


def _pricing_present(con):
    return set(_PRICING_TABLES).issubset(_tables(con))


def _pricing_conflict(con):
    """
    Bu revizyon KOSULSUZ create_table kullanir. Tablolar zaten varsa
    normal calistirmak "table already exists" ile duser.
    """
    mevcut = sorted(set(_PRICING_TABLES) & _tables(con))
    if mevcut:
        return f"kosulsuz create_table; zaten var olan {len(mevcut)} tablo: {mevcut}"
    return None


def _period_index_present(con):
    if "market_reference_prices" not in _tables(con):
        return False
    return "ix_market_reference_prices_period" in _index_names(con, "market_reference_prices")


def _conflict_if_table_exists(tables):
    def _f(con):
        mevcut = sorted(set(tables) & _tables(con))
        return f"kosulsuz create_table; zaten var: {mevcut}" if mevcut else None
    return _f


def _no_conflict(_con):
    return None


# ── B dalinin revizyonlari, GRAF SIRASINDA ──────────────────────────────
CANONICAL_BRANCH: tuple[RevisionEffect, ...] = (
    RevisionEffect(
        "a93beeaddf82", "contract generation v1 tablolari",
        _contract_v1_present, _contract_v1_evidence,
        _conflict_if_table_exists((
            "contracts", "customer_legal_profiles",
            "customer_authorized_representatives", "uploaded_reference_documents",
            "document_extraction_runs", "document_field_candidates",
        )),
    ),
    RevisionEffect(
        "dc8343278cfa", "uploaded_reference_documents dedup'ini document_type ile genislet",
        _dedup_widen_present, _dedup_widen_evidence, _no_conflict,
    ),
    RevisionEffect(
        "8b9a332a3680", "activities + tasks tablolari (S2)",
        _s2_present,
        lambda con: f"activities/tasks mevcut={sorted({'activities','tasks'} & _tables(con))}",
        _conflict_if_table_exists(("activities", "tasks")),
    ),
    RevisionEffect(
        "e340ce40c05c", "prospect_companies/sources/contacts (S4)",
        _s4_present,
        lambda con: f"prospect tablolari mevcut="
                    f"{sorted({'prospect_companies','prospect_sources','prospect_contacts'} & _tables(con))}",
        _conflict_if_table_exists(("prospect_companies", "prospect_sources", "prospect_contacts")),
    ),
    RevisionEffect(
        "f4e7efc70c80", "outreach_messages / outreach_templates / suppression_entries (S5)",
        _outreach_present,
        lambda con: f"outreach tablolari mevcut={sorted(set(_OUTREACH) & _tables(con))}",
        _conflict_if_table_exists(_OUTREACH),
    ),
    RevisionEffect(
        "beda29569b0d", "prospect_companies.verified_legal_type* kolonlari (S5)",
        _legal_type_present,
        lambda con: "prospect_companies kolonlari mevcut=" + str(sorted(
            set(_LEGAL_TYPE_COLS) & (_columns(con, "prospect_companies")
                                     if "prospect_companies" in _tables(con) else set())
        )),
        _no_conflict,
    ),
    RevisionEffect(
        "7b3e1c8a52df", "pricing modulu 8 tablosu",
        _pricing_present,
        lambda con: f"pricing tablolari mevcut={sorted(set(_PRICING_TABLES) & _tables(con))}",
        _pricing_conflict,
    ),
    RevisionEffect(
        "9d4a2f6b18ce", "ix_market_reference_prices_period",
        _period_index_present,
        lambda con: "market_reference_prices index'leri=" + str(sorted(
            _index_names(con, "market_reference_prices")
            if "market_reference_prices" in _tables(con) else set()
        )),
        _no_conflict,
    ),
)


@dataclass(frozen=True)
class RevisionClassification:
    revision: str
    summary: str
    effect_class: EffectClass
    evidence: str
    conflict: Optional[str] = None


def classify_canonical_branch(db_path: str) -> tuple[RevisionClassification, ...]:
    """
    B dalinin her revizyonunu SALT-OKUNUR probe ile siniflandirir.

    Tablo adinin var olmasi tek basina esdegerlik SAYILMAZ; her revizyon
    icin uretmesi gereken somut nesneler aranir.

    Cagrildigi yerler:
    - app/legacy_adoption/adoption.py::plan_adoption [PDSMR-R1D/Faz3]
    - tests/test_legacy_adoption_phase3.py
    """
    uri = "file:" + db_path.replace("\\", "/").replace(" ", "%20") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        sonuc = []
        for eff in CANONICAL_BRANCH:
            var = eff.present(con)
            kanit = eff.evidence(con)
            if var:
                sonuc.append(RevisionClassification(
                    eff.revision, eff.summary,
                    EffectClass.EXACT_EFFECT_ALREADY_PRESENT, kanit,
                ))
                continue
            catisma = eff.conflicts_if_run(con)
            if catisma:
                # Etki EKSIK ama calistirmak da catisir -> bilinmeyen durum.
                sonuc.append(RevisionClassification(
                    eff.revision, eff.summary,
                    EffectClass.CONFLICT_OR_UNKNOWN, kanit, catisma,
                ))
                continue
            sonuc.append(RevisionClassification(
                eff.revision, eff.summary,
                EffectClass.EFFECT_MISSING_AND_SAFE_TO_RUN, kanit,
            ))
        return tuple(sonuc)
    finally:
        con.close()



# ═══════════════════════════════════════════════════════════════════════
# PDSMR-R4 / FAZ 2 — TAM MIGRATION ETKI DEFTERI (graf-farkinda, coklu-atom)
# ═══════════════════════════════════════════════════════════════════════
"""
Yukaridaki `classify_canonical_branch` YALNIZ B dalini (a93beeaddf82 ->
... -> 9d4a2f6b18ce, 8 revizyon) kapsar ve uretimin ZATEN `013`'te
damgali oldugunu VARSAYAR. PDSMR-R4 Faz 0, gercek canli DB'nin
`alembic_version` HIC TASIMADIGINI kanitladi (bkz. evidence-archive/
PDSMR-R4/faz0-live-db-identity.json) — yani bu varsayim gercek canli DB
icin GECERSIZ.

Bu bolum, base'ten canonical head'e (351d314819d5) kadar TUM grafi
(govde + A dali + B dali + merge, 24 revizyon) AYNI somut-etki
disiplinini kullanarak siniflandirir:

    001_initial ... 011_market_prices_ptf_admin   (GOVDE, 13 revizyon)
      |-- (A dali) 012 -> 013                      (2 revizyon)
      +-- (B dali) a93beeaddf82 -> ... -> 9d4a2f6b18ce (8 revizyon)
                                    -> 351d314819d5 (MERGE, no-op)

Owner'in PDSMR-R4 Faz 2 sozlesmesi (aynen uygulanir):
  1. Segment kimligi ACIKCA tasinir (GraphSegment) — dogrusal degildir.
  2. Bir revizyonun atomlarindan (tablo/kolon/index/kisit) YALNIZ bir
     kismi mevcutsa sonuc CONFLICT'tir, ABSENT_SAFE_TO_APPLY DEGIL.
  3. ABSENT_SAFE_TO_APPLY yalniz TUM atomlar yok VE on kosullar
     dogrulanmis VE catisan baska nesne YOKSA verilir; aksi halde
     UNKNOWN_OR_UNPROVABLE.
  4. Sonraki bir revizyon tarafindan DEGISTIRILMIS etkiler (ör. 012'nin
     CHECK'i 013 tarafindan) ACIKCA, iki bilinen literal metinle
     modellenir — orntulu tolerans/gevsek karsilastirma YOK.
  5. Probe'lar affinity/nullability/default/PK/FK/index-kolon-sirasi/
     uniqueness/partial durumunu kontrol eder (fingerprint.py'nin
     DatabaseFingerprint'i uzerinden — kod tekrari YOK).
  6. Data-migration etkisi (ör. 005/011'in backfill UPDATE'leri)
     bugunku semadan/aggregate sayimdan KANITLANAMAZ; boyle bir atom
     HER ZAMAN UNPROVABLE doner, tahmin edilmez.
  7. Baglanti HER YERDE mode=ro'dur. Runtime model/metadata importu,
     create_all, alembic stamp/upgrade, yazan SQL YOKTUR.
  8. `classify_full_lineage()` bu asamada HICBIR production/startup/
     adoption cagrisina baglanmaz (yalniz testler cagirir).

Mevcut `classify_canonical_branch`/`CANONICAL_BRANCH` bu bolumden
ETKILENMEDEN, oldugu gibi kalir (adoption.py + test_legacy_adoption_
phase3.py hala onu kullanir).

Cagrildigi yerler:
- tests/test_pdsmr_r4_full_lineage.py [PDSMR-R4/Faz2]
  (Bilincli olarak HICBIR router/CLI/adoption/startup yolundan
   cagrilmaz — bkz. madde 8.)
"""
from .fingerprint import DatabaseFingerprint, collect_fingerprint


class GraphSegment(str, Enum):
    TRUNK = "TRUNK"            # 001_initial -> 011 (A ve B'nin ortak govdesi)
    BRANCH_A = "BRANCH_A"      # 012 -> 013 (uretim kuyrugu)
    BRANCH_B = "BRANCH_B"      # a93beeaddf82 -> ... -> 9d4a2f6b18ce (canonical)
    MERGE = "MERGE"            # 351d314819d5


class FullEffectClass(str, Enum):
    PRESENT_EXACT = "PRESENT_EXACT"
    ABSENT_SAFE_TO_APPLY = "ABSENT_SAFE_TO_APPLY"
    CONFLICT = "CONFLICT"
    UNKNOWN_OR_UNPROVABLE = "UNKNOWN_OR_UNPROVABLE"


# Bir atomun somut durumu. "PRESENT_SUPERSEDED" yalniz madde-4 icin
# vardir: atom KENDI degil, GRAF'ta bilinen bir SONRAKI revizyonun
# ureteceği literal haliyle eslesiyor.
_ATOM_PRESENT_EXACT = "PRESENT_EXACT"
_ATOM_PRESENT_SUPERSEDED = "PRESENT_SUPERSEDED"
_ATOM_ABSENT = "ABSENT"
_ATOM_WRONG_SHAPE = "PRESENT_WRONG_SHAPE"
_ATOM_UNPROVABLE = "UNPROVABLE"


@dataclass(frozen=True)
class AtomResult:
    """Bir revizyonun TEK somut nesnesinin (tablo/kolon/index/kisit/data-
    etkisi) salt-okunur probe sonucu. `detail` yalniz sema ozeti/sayim
    icerir — PII/musteri verisi YOK."""

    atom_id: str
    state: str
    detail: str
    superseded_by: Optional[str] = None


@dataclass(frozen=True)
class FullRevisionClassification:
    revision: str
    segment: GraphSegment
    summary: str
    effect_class: FullEffectClass
    atoms: tuple[AtomResult, ...]
    reason: str


# ── kucuk spec tipleri (yalniz veri, davranis yok) ──────────────────────
@dataclass(frozen=True)
class _Col:
    name: str
    affinity: str
    notnull: bool
    has_default: bool


@dataclass(frozen=True)
class _Idx:
    name: str
    columns: tuple[str, ...]
    unique: bool
    partial: bool = False


# ── atom uretimi: jenerik, spec-driven, DatabaseFingerprint uzerinden ───
def _col_atom(fp: DatabaseFingerprint, table: str, col: _Col) -> AtomResult:
    aid = f"column:{table}.{col.name}"
    tfp = fp.tables.get(table)
    if tfp is None or col.name not in tfp.columns:
        return AtomResult(aid, _ATOM_ABSENT, f"{table}.{col.name} yok")
    c = tfp.columns[col.name]
    if c.affinity != col.affinity:
        return AtomResult(
            aid, _ATOM_WRONG_SHAPE,
            f"affinity={c.affinity} beklenen={col.affinity} (declared={c.declared_type})",
        )
    if c.notnull != col.notnull:
        return AtomResult(aid, _ATOM_WRONG_SHAPE, f"notnull={c.notnull} beklenen={col.notnull}")
    if c.has_default != col.has_default:
        return AtomResult(
            aid, _ATOM_WRONG_SHAPE, f"has_default={c.has_default} beklenen={col.has_default}"
        )
    return AtomResult(
        aid, _ATOM_PRESENT_EXACT,
        f"affinity={c.affinity} notnull={c.notnull} has_default={c.has_default}",
    )


def _idx_atom(fp: DatabaseFingerprint, table: str, idx: _Idx) -> AtomResult:
    aid = f"index:{table}.{idx.name}"
    tfp = fp.tables.get(table)
    if tfp is None or idx.name not in tfp.indexes:
        return AtomResult(aid, _ATOM_ABSENT, f"{table}.{idx.name} yok")
    i = tfp.indexes[idx.name]
    if i.columns != idx.columns:
        return AtomResult(aid, _ATOM_WRONG_SHAPE, f"kolonlar={i.columns} beklenen={idx.columns}")
    if i.unique != idx.unique:
        return AtomResult(aid, _ATOM_WRONG_SHAPE, f"unique={i.unique} beklenen={idx.unique}")
    if i.partial != idx.partial:
        return AtomResult(aid, _ATOM_WRONG_SHAPE, f"partial={i.partial} beklenen={idx.partial}")
    return AtomResult(aid, _ATOM_PRESENT_EXACT, f"kolonlar={i.columns} unique={i.unique}")


def _idx_dropped_atom(fp: DatabaseFingerprint, table: str, idx_name: str) -> AtomResult:
    """Bir migration'in KALDIRDIGI index icin: index YOKSA etki VAR
    (PRESENT), index HALA ORADAYSA etki YOK (ABSENT).

    DIKKAT: tablonun KENDISI yoksa bu "drop basarili" DEGIL, "bu revizyon
    (ve onu iceren tablo) hic olusmamis" demektir — bu durumda ABSENT
    donulur, boylece ayni revizyonun DIGER atomlariyla (kolonlar/diger
    index'ler, hepsi tabloya bagli oldugundan hepsi ABSENT) TUTARLI kalir
    ve yanlislikla "kismi etki" (CONFLICT) uretilmez.
    """
    aid = f"index_dropped:{table}.{idx_name}"
    tfp = fp.tables.get(table)
    if tfp is None:
        return AtomResult(aid, _ATOM_ABSENT, f"{table} tablosu yok")
    if idx_name not in tfp.indexes:
        return AtomResult(aid, _ATOM_PRESENT_EXACT, f"{table}.{idx_name} kaldirilmis (beklenen)")
    return AtomResult(aid, _ATOM_ABSENT, f"{table}.{idx_name} hala mevcut (kaldirilmis olmali)")


def _idx_atom_ex(
    fp: DatabaseFingerprint, table: str, primary: _Idx,
    superseded_by: tuple[tuple[_Idx, str], ...] = (),
    *, known_removed_by: tuple[str, ...] = (), precursor_shapes: tuple[_Idx, ...] = (),
) -> AtomResult:
    """
    `_idx_atom` ile AYNI, ama madde-4 (supersession) icin: `primary`
    eslesmezse, ACIKCA listelenmis alternatif sekillerden (her biri
    HANGI GRAF revizyonunun urettigi bilinen literal sekil oldugu
    belirtilerek) biriyle eslesiyorsa PRESENT_SUPERSEDED doner — orntulu
    tolerans degil, kapali/bilinen bir alternatif kumesi.

    `known_removed_by`: bu index'i TAMAMEN KALDIRDIGI (yeniden sekillen-
    dirmedigi) BILINEN, ADI GECEN revizyon(lar). Index HIC YOKSA ve bu
    liste BOS DEGILSE, "18100a648086 hic calismadi" ile "18100a648086
    calisti AMA 011 onun index'ini SONRADAN kaldirdi (9d4a2f6b18ce henuz
    calismadi)" ayirt edilemez GIBI GORUNSE de — asil ayrimi tablonun
    KENDI ATOMU (diger kolonlar/PK/diger index'ler mevcut mu) yapar; bu
    parametre yalniz "index yoklugu TEK BASINA bu revizyonu CONFLICT'e
    dusurmesin, ACIKCA bilinen bir kaldirma nedeni varsa" der.

    `precursor_shapes`: bu index'in, BU revizyondan ONCEKI (graf'ta daha
    erken) BASKA bir revizyonun urettigi BILINEN sekli. Boyle bir sekille
    eslesirse WRONG_SHAPE DEGIL, ABSENT donulur — cunku bu, "bozuk bir
    sema" degil, "bu revizyon henuz calismadi, nesne hala onceki halinde"
    demektir (ör. dc8343278cfa'nin 4-kolonlu genisletmesi henuz olmamis,
    index hala a93beeaddf82'nin 3-kolonlu ORIJINAL halinde).
    """
    aid = f"index:{table}.{primary.name}"
    tfp = fp.tables.get(table)
    if tfp is None:
        # Tablonun KENDISI yok -> bu revizyon (tablosuyla birlikte) hic
        # olusmamis; "bilerek kaldirilmis" istisnasi burada UYGULANMAZ,
        # aksi halde ayni revizyonun DIGER (dogru sekilde ABSENT donen)
        # atomlariyla TUTARSIZ, yanlis bir PRESENT_SUPERSEDED uretilir.
        return AtomResult(aid, _ATOM_ABSENT, f"{table} tablosu yok")
    if primary.name not in tfp.indexes:
        if known_removed_by:
            return AtomResult(
                aid, _ATOM_PRESENT_SUPERSEDED,
                f"{'/'.join(known_removed_by)} tarafindan bilerek kaldirilmis (henuz yeniden "
                "olusturulmamis olabilir) — bu, bu revizyonun etkisini GECERSIZ kilmaz",
                superseded_by=known_removed_by[0],
            )
        return AtomResult(aid, _ATOM_ABSENT, f"{table}.{primary.name} yok")
    i = tfp.indexes[primary.name]
    if i.columns == primary.columns and i.unique == primary.unique and i.partial == primary.partial:
        return AtomResult(aid, _ATOM_PRESENT_EXACT, f"kolonlar={i.columns} unique={i.unique}")
    for alt, rev in superseded_by:
        if i.columns == alt.columns and i.unique == alt.unique and i.partial == alt.partial:
            return AtomResult(
                aid, _ATOM_PRESENT_SUPERSEDED,
                f"{rev} tarafindan degistirilmis hali: kolonlar={i.columns} unique={i.unique}",
                superseded_by=rev,
            )
    for pre in precursor_shapes:
        if i.columns == pre.columns and i.unique == pre.unique and i.partial == pre.partial:
            return AtomResult(
                aid, _ATOM_ABSENT,
                f"hala onceki (bu revizyondan ONCEKI) hali: kolonlar={i.columns} unique={i.unique}",
            )
    return AtomResult(
        aid, _ATOM_WRONG_SHAPE,
        f"kolonlar={i.columns} unique={i.unique} partial={i.partial} — bilinen hicbir sekille eslesmiyor",
    )


def _idx_dropped_atom_ex(
    fp: DatabaseFingerprint, table: str, idx_name: str,
    *, superseding_shapes: tuple[tuple[_Idx, str], ...] = (),
) -> AtomResult:
    """
    `_idx_dropped_atom` ile AYNI, ama madde-4 icin: kaldirilan index'in
    adi, DAHA SONRAKI bir revizyon tarafindan FARKLI bir sekille yeniden
    kullanilmis olabilir. Boyle bir sekille eslesirse "kaldirma etkisi"
    yine de GERCEKLESMIS sayilir (orijinal nesne kesinlikle yok artik) —
    PRESENT_SUPERSEDED. Eski (kaldirilmamis) sekil hala oradaysa ABSENT.
    """
    aid = f"index_dropped:{table}.{idx_name}"
    tfp = fp.tables.get(table)
    if tfp is None:
        return AtomResult(aid, _ATOM_ABSENT, f"{table} tablosu yok")
    if idx_name not in tfp.indexes:
        return AtomResult(aid, _ATOM_PRESENT_EXACT, f"{table}.{idx_name} kaldirilmis (beklenen)")
    i = tfp.indexes[idx_name]
    for alt, rev in superseding_shapes:
        if i.columns == alt.columns and i.unique == alt.unique and i.partial == alt.partial:
            return AtomResult(
                aid, _ATOM_PRESENT_SUPERSEDED,
                f"orijinal kaldirilmis, {rev} ayni adla farkli sekilde yeniden olusturmus: "
                f"kolonlar={i.columns} unique={i.unique}",
                superseded_by=rev,
            )
    return AtomResult(aid, _ATOM_ABSENT, f"{table}.{idx_name} hala eski sekliyle mevcut (kaldirilmamis)")


def _table_atom(fp: DatabaseFingerprint, table: str) -> AtomResult:
    aid = f"table:{table}"
    if table in fp.tables:
        return AtomResult(aid, _ATOM_PRESENT_EXACT, f"{table} mevcut")
    return AtomResult(aid, _ATOM_ABSENT, f"{table} yok")


def _pk_atom(fp: DatabaseFingerprint, table: str, pk: tuple[str, ...]) -> AtomResult:
    aid = f"pk:{table}"
    tfp = fp.tables.get(table)
    if tfp is None:
        return AtomResult(aid, _ATOM_ABSENT, f"{table} yok")
    if tfp.primary_key != pk:
        return AtomResult(aid, _ATOM_WRONG_SHAPE, f"pk={tfp.primary_key} beklenen={pk}")
    return AtomResult(aid, _ATOM_PRESENT_EXACT, f"pk={tfp.primary_key}")


def _fk_atom(fp: DatabaseFingerprint, table: str, fks: tuple[str, ...]) -> AtomResult:
    aid = f"fk:{table}"
    tfp = fp.tables.get(table)
    if tfp is None:
        return AtomResult(aid, _ATOM_ABSENT, f"{table} yok")
    if set(tfp.foreign_keys) != set(fks):
        return AtomResult(aid, _ATOM_WRONG_SHAPE, f"fk={tfp.foreign_keys} beklenen={fks}")
    return AtomResult(aid, _ATOM_PRESENT_EXACT, f"fk={tfp.foreign_keys}")


def _unprovable_data_atom(fp: DatabaseFingerprint, table: str, column: str, tag: str) -> AtomResult:
    """
    Data-migration (backfill UPDATE) etkisi icin (madde 6).

    DDL (kolon) YOKSA: bu revizyonun TAMAMI (DDL + backfill) HENUZ
    calismamis demektir — ileri-uygulama (normal alembic upgrade, backfill
    dahil) iyi tanimlidir -> ABSENT (CONFLICT/UNKNOWN'a DUSMEZ).

    DDL VARSA: kolonun deger tasimasi/tasimamasi, backfill'in FIILEN
    calisip calismadigini KANITLAMAZ (yeni satirlar backfill'DEN BAGIMSIZ
    olarak da ayni sekle sahip olabilir) — bu yuzden HER ZAMAN UNPROVABLE.
    Aggregate satir sayimi bile burada YETERSIZDIR (nedensellik kurulamaz);
    bu fonksiyon bilerek hicbir COUNT(*) calistirmaz.
    """
    aid = f"data:{table}.{column}[{tag}]"
    tfp = fp.tables.get(table)
    if tfp is None or column not in tfp.columns:
        return AtomResult(
            aid, _ATOM_ABSENT,
            f"{table}.{column} kolonu yok — ileri-uygulama (backfill dahil) iyi tanimli",
        )
    return AtomResult(
        aid, _ATOM_UNPROVABLE,
        f"{table}.{column} kolonu mevcut ama backfill'in fiilen calistigi "
        "bugunku semadan kanitlanamaz (requirement 6)",
    )


def _table_create_atoms(
    fp: DatabaseFingerprint, table: str, cols: tuple[_Col, ...],
    pk: tuple[str, ...], fks: tuple[str, ...], idxs: tuple[_Idx, ...],
) -> tuple[AtomResult, ...]:
    out = [_table_atom(fp, table)]
    out.extend(_col_atom(fp, table, c) for c in cols)
    out.append(_pk_atom(fp, table, pk))
    out.append(_fk_atom(fp, table, fks))
    out.extend(_idx_atom(fp, table, i) for i in idxs)
    return tuple(out)


def _column_add_atoms(
    fp: DatabaseFingerprint, table: str, cols: tuple[_Col, ...],
    idxs: tuple[_Idx, ...] = (), dropped_idx_names: tuple[str, ...] = (),
) -> tuple[AtomResult, ...]:
    out = [_col_atom(fp, table, c) for c in cols]
    out.extend(_idx_atom(fp, table, i) for i in idxs)
    out.extend(_idx_dropped_atom(fp, table, n) for n in dropped_idx_names)
    return tuple(out)


def _tables_present(fp: DatabaseFingerprint, tables: tuple[str, ...]) -> bool:
    """Bir revizyonun on kosulu: bagimli oldugu tablolar ONCEDEN var mi."""
    return all(t in fp.tables for t in tables)


# ── ptf_drift_log CHECK kisitlari: madde 4 (supersession) EXPLICIT ──────
_PTF_SEVERITY_012_TEXT = "severity IN ('low', 'high')"
_PTF_SEVERITY_013_TEXT = "severity IN ('low', 'high', 'missing_legacy')"
_PTF_REQUEST_HASH_LEN_TEXT = "length(request_hash) = 64"


def _table_ddl_text(con: sqlite3.Connection, table: str) -> str:
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return (row[0] if row and row[0] else "")


def _norm_sql(text: str) -> str:
    return " ".join(text.split())


def _ptf_severity_atom_012(con: sqlite3.Connection) -> AtomResult:
    aid = "constraint:ptf_drift_log.ck_ptf_drift_log_severity[012]"
    ddl = _norm_sql(_table_ddl_text(con, "ptf_drift_log"))
    if not ddl:
        return AtomResult(aid, _ATOM_ABSENT, "ptf_drift_log tablosu yok")
    if _PTF_SEVERITY_012_TEXT in ddl:
        return AtomResult(aid, _ATOM_PRESENT_EXACT, "012'nin kendi hali (2 deger: low/high)")
    if _PTF_SEVERITY_013_TEXT in ddl:
        return AtomResult(
            aid, _ATOM_PRESENT_SUPERSEDED,
            "013 tarafindan degistirilmis hali (3 deger) — 012'nin etkisi bunun icinde kapsanir",
            superseded_by="013_extend_ptf_drift_severity",
        )
    return AtomResult(aid, _ATOM_WRONG_SHAPE, "bilinmeyen severity CHECK metni")


def _ptf_severity_atom_013(con: sqlite3.Connection) -> AtomResult:
    aid = "constraint:ptf_drift_log.ck_ptf_drift_log_severity[013]"
    ddl = _norm_sql(_table_ddl_text(con, "ptf_drift_log"))
    if not ddl:
        return AtomResult(aid, _ATOM_ABSENT, "ptf_drift_log tablosu yok")
    if _PTF_SEVERITY_013_TEXT in ddl:
        return AtomResult(aid, _ATOM_PRESENT_EXACT, "013'un kendi hali (3 deger)")
    if _PTF_SEVERITY_012_TEXT in ddl:
        return AtomResult(aid, _ATOM_ABSENT, "hala 012'nin hali (2 deger) — 013 henuz calismamis")
    return AtomResult(aid, _ATOM_WRONG_SHAPE, "bilinmeyen severity CHECK metni")


def _ptf_request_hash_len_atom(con: sqlite3.Connection) -> AtomResult:
    aid = "constraint:ptf_drift_log.ck_ptf_drift_log_request_hash_len"
    ddl = _norm_sql(_table_ddl_text(con, "ptf_drift_log"))
    if not ddl:
        return AtomResult(aid, _ATOM_ABSENT, "ptf_drift_log tablosu yok")
    if _PTF_REQUEST_HASH_LEN_TEXT in ddl:
        return AtomResult(aid, _ATOM_PRESENT_EXACT, "request_hash_len CHECK mevcut")
    return AtomResult(aid, _ATOM_WRONG_SHAPE, "request_hash_len CHECK bulunamadi/degisik")


# ── siniflandirma: atom listesinden FullEffectClass'a (madde 2/3/6) ─────
def _classify(
    revision: str, segment: GraphSegment, summary: str, atoms: tuple[AtomResult, ...],
    *, preconditions_ok: bool = True, extra_conflict: Optional[str] = None,
) -> FullRevisionClassification:
    wrong = [a for a in atoms if a.state == _ATOM_WRONG_SHAPE]
    present = [a for a in atoms if a.state in (_ATOM_PRESENT_EXACT, _ATOM_PRESENT_SUPERSEDED)]
    absent = [a for a in atoms if a.state == _ATOM_ABSENT]
    unprovable = [a for a in atoms if a.state == _ATOM_UNPROVABLE]
    n = len(atoms)

    if wrong:
        return FullRevisionClassification(
            revision, segment, summary, FullEffectClass.CONFLICT, atoms,
            f"{len(wrong)}/{n} nesne yanlis sekilde mevcut (ilk: {wrong[0].atom_id} — {wrong[0].detail})",
        )
    if absent and (present or unprovable):
        return FullRevisionClassification(
            revision, segment, summary, FullEffectClass.CONFLICT, atoms,
            f"kismi etki: mevcut/kanitlanamaz={len(present) + len(unprovable)} eksik={len(absent)} "
            f"toplam={n} (ilk eksik: {absent[0].atom_id})",
        )
    if unprovable:
        return FullRevisionClassification(
            revision, segment, summary, FullEffectClass.UNKNOWN_OR_UNPROVABLE, atoms,
            f"{len(unprovable)}/{n} nesne bugunku semadan kanitlanamaz (data-etkisi) — tahmin edilmedi",
        )
    if present and len(present) == n:
        superseded = [a for a in atoms if a.state == _ATOM_PRESENT_SUPERSEDED]
        ek = (
            f" ({len(superseded)} nesne sonraki revizyon tarafindan degistirilmis halde: "
            f"{superseded[0].superseded_by})" if superseded else ""
        )
        return FullRevisionClassification(
            revision, segment, summary, FullEffectClass.PRESENT_EXACT, atoms,
            f"{n}/{n} nesne tam eslesiyor{ek}",
        )
    # Buraya ulasildiysa: present=0, wrong=0, unprovable=0 -> hepsi ABSENT
    # (n==0 -> present=len([])==0==n de bir onceki dalda TRUE olurdu, yani
    # bos atom listesi bu satira hic dusmez; MERGE ayrica ele alinir).
    if extra_conflict:
        return FullRevisionClassification(
            revision, segment, summary, FullEffectClass.CONFLICT, atoms, extra_conflict,
        )
    if not preconditions_ok:
        return FullRevisionClassification(
            revision, segment, summary, FullEffectClass.UNKNOWN_OR_UNPROVABLE, atoms,
            "on kosullar (bagimli tablo/kolon) dogrulanamadi",
        )
    return FullRevisionClassification(
        revision, segment, summary, FullEffectClass.ABSENT_SAFE_TO_APPLY, atoms,
        f"{n}/{n} nesne yok, on kosullar saglaniyor, guvenle uygulanabilir",
    )


# ── GOVDE (TRUNK): 001_initial -> 011, 13 revizyon, graf sirasinda ──────
def _rev_001_initial(fp, con):
    atoms = (
        _table_create_atoms(fp, "invoices", (
            _Col("id", "TEXT", True, False), _Col("source_filename", "TEXT", True, False),
            _Col("content_type", "TEXT", True, False), _Col("storage_original_ref", "TEXT", True, False),
            _Col("storage_page1_ref", "TEXT", False, False), _Col("file_hash", "TEXT", False, False),
            _Col("vendor_guess", "TEXT", False, False), _Col("invoice_period", "TEXT", False, False),
            _Col("extraction_json", "NUMERIC", False, False), _Col("validation_json", "NUMERIC", False, False),
            _Col("status", "TEXT", True, True), _Col("error_message", "TEXT", False, False),
            _Col("created_at", "NUMERIC", True, True), _Col("updated_at", "NUMERIC", True, True),
        ), ("id",), (), (_Idx("ix_invoices_file_hash", ("file_hash",), False),))
        + _table_create_atoms(fp, "customers", (
            _Col("id", "INTEGER", True, False), _Col("name", "TEXT", True, False),
            _Col("company", "TEXT", False, False), _Col("email", "TEXT", False, False),
            _Col("phone", "TEXT", False, False), _Col("address", "TEXT", False, False),
            _Col("notes", "TEXT", False, False), _Col("created_at", "NUMERIC", True, True),
            _Col("updated_at", "NUMERIC", True, True),
        ), ("id",), (), (_Idx("ix_customers_name", ("name",), False),))
        + _table_create_atoms(fp, "offers", (
            _Col("id", "INTEGER", True, False), _Col("customer_id", "INTEGER", False, False),
            _Col("vendor", "TEXT", False, False), _Col("invoice_period", "TEXT", False, False),
            _Col("consumption_kwh", "REAL", True, False), _Col("current_unit_price", "REAL", True, False),
            _Col("distribution_unit_price", "REAL", False, False), _Col("demand_qty", "REAL", False, False),
            _Col("demand_unit_price", "REAL", False, False), _Col("weighted_ptf", "REAL", True, False),
            _Col("yekdem", "REAL", True, False), _Col("agreement_multiplier", "REAL", True, False),
            _Col("current_total", "REAL", True, False), _Col("offer_total", "REAL", True, False),
            _Col("savings_amount", "REAL", True, False), _Col("savings_ratio", "REAL", True, False),
            _Col("calculation_result", "NUMERIC", False, False), _Col("extraction_result", "NUMERIC", False, False),
            _Col("created_at", "NUMERIC", True, True), _Col("pdf_ref", "TEXT", False, False),
            _Col("status", "TEXT", True, True),
        ), ("id",), ("customer_id->customers.id",), ())
        + _table_create_atoms(fp, "jobs", (
            _Col("id", "TEXT", True, False), _Col("invoice_id", "TEXT", True, False),
            _Col("job_type", "TEXT", True, False), _Col("status", "TEXT", True, True),
            _Col("payload_json", "NUMERIC", False, False), _Col("result_json", "NUMERIC", False, False),
            _Col("error", "TEXT", False, False), _Col("created_at", "NUMERIC", True, True),
            _Col("started_at", "NUMERIC", False, False), _Col("finished_at", "NUMERIC", False, False),
        ), ("id",), ("invoice_id->invoices.id",), (
            _Idx("ix_jobs_invoice_id", ("invoice_id",), False),
            _Idx("ix_jobs_status_created", ("status", "created_at"), False),
        ))
    )
    return _classify("001_initial", GraphSegment.TRUNK, "temel tablolar (invoices/customers/offers/jobs)", atoms)


def _rev_002(fp, con):
    atoms = (
        _column_add_atoms(fp, "invoices", (_Col("tenant_id", "TEXT", True, True),),
                           (_Idx("ix_invoices_tenant_id", ("tenant_id",), False),))
        + _column_add_atoms(fp, "jobs", (_Col("tenant_id", "TEXT", True, True),),
                             (_Idx("ix_jobs_tenant_id", ("tenant_id",), False),))
        + _column_add_atoms(fp, "offers", (
            _Col("tenant_id", "TEXT", True, True), _Col("extra_items_json", "NUMERIC", False, False),
            _Col("extra_items_total_tl", "REAL", False, True),
        ), (_Idx("ix_offers_tenant_id", ("tenant_id",), False),))
    )
    return _classify(
        "002", GraphSegment.TRUNK, "tenant_id + extra_items kolonlari", atoms,
        preconditions_ok=_tables_present(fp, ("invoices", "jobs", "offers")),
    )


def _rev_003(fp, con):
    atoms = (
        _table_create_atoms(fp, "audit_logs", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("actor_type", "TEXT", True, True), _Col("actor_id", "TEXT", False, False),
            _Col("action", "TEXT", True, False), _Col("target_type", "TEXT", False, False),
            _Col("target_id", "TEXT", False, False), _Col("details_json", "NUMERIC", False, False),
            _Col("ip_address", "TEXT", False, False), _Col("user_agent", "TEXT", False, False),
            _Col("created_at", "NUMERIC", False, False),
        ), ("id",), (), (
            _Idx("ix_audit_logs_tenant_id", ("tenant_id",), False),
            _Idx("ix_audit_logs_created_at", ("created_at",), False),
            _Idx("ix_audit_logs_tenant_action", ("tenant_id", "action"), False),
            _Idx("ix_audit_logs_target", ("target_type", "target_id"), False),
        ))
        + _table_create_atoms(fp, "webhook_configs", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("url", "TEXT", True, False), _Col("events", "NUMERIC", True, False),
            _Col("secret", "TEXT", False, False), _Col("headers_json", "NUMERIC", False, False),
            _Col("is_active", "INTEGER", False, True), _Col("last_triggered_at", "NUMERIC", False, False),
            _Col("success_count", "INTEGER", False, True), _Col("failure_count", "INTEGER", False, True),
            _Col("created_at", "NUMERIC", False, False), _Col("updated_at", "NUMERIC", False, False),
        ), ("id",), (), (_Idx("ix_webhook_configs_tenant_id", ("tenant_id",), False),))
        + _table_create_atoms(fp, "webhook_deliveries", (
            _Col("id", "INTEGER", True, False), _Col("webhook_config_id", "INTEGER", False, False),
            _Col("event_type", "TEXT", True, False), _Col("payload_json", "NUMERIC", True, False),
            _Col("status", "TEXT", True, True), _Col("response_status_code", "INTEGER", False, False),
            _Col("response_body", "TEXT", False, False), _Col("error_message", "TEXT", False, False),
            _Col("attempt_count", "INTEGER", False, True), _Col("next_retry_at", "NUMERIC", False, False),
            _Col("created_at", "NUMERIC", False, False), _Col("delivered_at", "NUMERIC", False, False),
        ), ("id",), ("webhook_config_id->webhook_configs.id",), (
            _Idx("ix_webhook_deliveries_config", ("webhook_config_id",), False),
            _Idx("ix_webhook_deliveries_status", ("status",), False),
        ))
    )
    return _classify("003", GraphSegment.TRUNK, "audit_logs + webhook_configs + webhook_deliveries", atoms)


def _rev_18100a648086(fp, con):
    atoms = (
        _table_create_atoms(fp, "market_reference_prices", (
            _Col("id", "INTEGER", True, False), _Col("period", "TEXT", True, False),
            _Col("ptf_tl_per_mwh", "REAL", True, False), _Col("yekdem_tl_per_mwh", "REAL", True, False),
            _Col("source_note", "TEXT", False, False), _Col("is_locked", "INTEGER", False, False),
            _Col("updated_by", "TEXT", False, False), _Col("created_at", "NUMERIC", False, False),
            _Col("updated_at", "NUMERIC", False, False),
        ), ("id",), (), (
            _Idx("ix_market_reference_prices_id", ("id",), False),
        ))
        + (
            # ix_market_reference_prices_period: BU revizyonun kendi hali
            # unique=True'dur. 011 bunu DUSURUR, 9d4a2f6b18ce AYNI adla
            # unique=False olarak YENIDEN OLUSTURUR — madde 4, ACIKCA
            # modellenmis supersession (orntulu tolerans DEGIL).
            _idx_atom_ex(
                fp, "market_reference_prices",
                _Idx("ix_market_reference_prices_period", ("period",), True),
                superseded_by=((_Idx("ix_market_reference_prices_period", ("period",), False), "9d4a2f6b18ce"),),
                known_removed_by=("011_market_prices_ptf_admin",),
            ),
        )
        + _table_create_atoms(fp, "distribution_tariffs", (
            _Col("id", "INTEGER", True, False), _Col("valid_from", "TEXT", True, False),
            _Col("valid_to", "TEXT", False, False), _Col("tariff_group", "TEXT", True, False),
            _Col("voltage_level", "TEXT", True, False), _Col("term_type", "TEXT", True, False),
            _Col("unit_price_tl_per_kwh", "REAL", True, False), _Col("source_note", "TEXT", False, False),
            _Col("created_at", "NUMERIC", False, False), _Col("updated_at", "NUMERIC", False, False),
        ), ("id",), (), (
            _Idx("ix_distribution_tariffs_id", ("id",), False),
            _Idx("ix_distribution_tariffs_valid_from", ("valid_from",), False),
        ))
    )
    return _classify(
        "18100a648086", GraphSegment.TRUNK, "market_reference_prices + distribution_tariffs", atoms,
    )


def _rev_c1a7f0e94d52(fp, con):
    atoms = _table_create_atoms(fp, "incidents", (
        _Col("id", "INTEGER", True, False), _Col("trace_id", "TEXT", True, False),
        _Col("tenant_id", "TEXT", True, False), _Col("invoice_id", "TEXT", False, False),
        _Col("offer_id", "INTEGER", False, False), _Col("severity", "TEXT", True, False),
        _Col("category", "TEXT", True, False), _Col("message", "TEXT", True, False),
        _Col("details_json", "NUMERIC", False, False), _Col("dedupe_key", "TEXT", False, False),
        _Col("occurrence_count", "INTEGER", True, False), _Col("first_seen_at", "NUMERIC", False, False),
        _Col("last_seen_at", "NUMERIC", False, False), _Col("status", "TEXT", True, False),
        _Col("resolution_note", "TEXT", False, False), _Col("resolved_by", "TEXT", False, False),
        _Col("resolved_at", "NUMERIC", False, False), _Col("created_at", "NUMERIC", False, False),
        _Col("updated_at", "NUMERIC", False, False),
    ), ("id",), (), (
        _Idx("ix_incidents_id", ("id",), False), _Idx("ix_incidents_trace_id", ("trace_id",), False),
        _Idx("ix_incidents_tenant_id", ("tenant_id",), False), _Idx("ix_incidents_invoice_id", ("invoice_id",), False),
        _Idx("ix_incidents_severity", ("severity",), False), _Idx("ix_incidents_category", ("category",), False),
        _Idx("ix_incidents_dedupe_key", ("dedupe_key",), False), _Idx("ix_incidents_status", ("status",), False),
        _Idx("ix_incidents_created_at", ("created_at",), False),
    ))
    return _classify("c1a7f0e94d52", GraphSegment.TRUNK, "incidents tablosu (eksik ata onarimi)", atoms)


def _rev_004(fp, con):
    atoms = _column_add_atoms(fp, "incidents", (
        _Col("provider", "TEXT", False, False), _Col("period", "TEXT", False, False),
        _Col("dedupe_bucket", "INTEGER", False, False), _Col("primary_flag", "TEXT", False, False),
        _Col("action_type", "TEXT", False, False), _Col("action_owner", "TEXT", False, False),
        _Col("action_code", "TEXT", False, False), _Col("all_flags", "NUMERIC", False, False),
        _Col("secondary_flags", "NUMERIC", False, False), _Col("deduction_total", "INTEGER", False, False),
        _Col("routed_payload", "NUMERIC", False, False),
    ), (
        _Idx("ix_incidents_provider", ("provider",), False), _Idx("ix_incidents_period", ("period",), False),
        _Idx("ix_incidents_primary_flag", ("primary_flag",), False),
        _Idx("ix_incidents_action_type", ("action_type",), False),
        _Idx("ix_incidents_dedupe_bucket", ("dedupe_bucket",), False),
        _Idx("ix_incidents_dedupe_unique", ("tenant_id", "dedupe_key", "dedupe_bucket"), True),
    ))
    return _classify(
        "004", GraphSegment.TRUNK, "incidents: action-router alanlari (Sprint 6.1)", atoms,
        preconditions_ok=_tables_present(fp, ("incidents",)),
    )


def _rev_005(fp, con):
    atoms = _column_add_atoms(fp, "incidents", (
        _Col("retry_attempt_count", "INTEGER", False, False), _Col("retry_eligible_at", "NUMERIC", False, False),
        _Col("retry_last_attempt_at", "NUMERIC", False, False), _Col("retry_lock_until", "NUMERIC", False, False),
        _Col("retry_lock_by", "TEXT", False, False), _Col("retry_exhausted_at", "NUMERIC", False, False),
    ), (_Idx("ix_incidents_retry_eligible_at", ("retry_eligible_at",), False),)) + (
        _unprovable_data_atom(fp, "incidents", "retry_attempt_count", "005-backfill"),
    )
    return _classify(
        "005_retry_executor", GraphSegment.TRUNK, "incidents: retry execution alanlari", atoms,
        preconditions_ok=_tables_present(fp, ("incidents",)),
    )


def _rev_006(fp, con):
    atoms = _column_add_atoms(fp, "incidents", (
        _Col("external_issue_id", "TEXT", False, False), _Col("external_issue_url", "TEXT", False, False),
        _Col("reported_at", "NUMERIC", False, False),
    ), (_Idx("ix_incidents_unreported_bugs", ("status", "action_type", "external_issue_id"), False),))
    return _classify(
        "006_issue_integration", GraphSegment.TRUNK, "incidents: issue tracking alanlari", atoms,
        preconditions_ok=_tables_present(fp, ("incidents",)),
    )


def _rev_007(fp, con):
    atoms = _column_add_atoms(fp, "incidents", (
        _Col("reclassified_at", "NUMERIC", False, False), _Col("previous_primary_flag", "TEXT", False, False),
        _Col("recompute_count", "INTEGER", False, False),
    ))
    return _classify(
        "007_reclassification", GraphSegment.TRUNK, "incidents: reclassification alanlari", atoms,
        preconditions_ok=_tables_present(fp, ("incidents",)),
    )


def _rev_008(fp, con):
    atoms = _column_add_atoms(fp, "incidents", (_Col("retry_success", "NUMERIC", False, False),),
                               (_Idx("ix_incidents_pending_recompute", ("tenant_id", "status", "updated_at"), False),))
    return _classify(
        "008_retry_orchestrator", GraphSegment.TRUNK, "incidents: retry_success + pending-recompute index", atoms,
        preconditions_ok=_tables_present(fp, ("incidents",)),
    )


def _rev_009(fp, con):
    atoms = _column_add_atoms(fp, "incidents", (_Col("resolution_reason", "TEXT", False, False),),
                               (_Idx("ix_incidents_resolution_reason", ("tenant_id", "resolution_reason"), False),))
    return _classify(
        "009_resolution_reasons", GraphSegment.TRUNK, "incidents: resolution_reason", atoms,
        preconditions_ok=_tables_present(fp, ("incidents",)),
    )


def _rev_010(fp, con):
    atoms = _column_add_atoms(fp, "incidents", (_Col("feedback_json", "NUMERIC", False, False),))
    return _classify(
        "010_feedback_loop", GraphSegment.TRUNK, "incidents: feedback_json", atoms,
        preconditions_ok=_tables_present(fp, ("incidents",)),
    )


def _rev_011(fp, con):
    atoms = _column_add_atoms(fp, "market_reference_prices", (
        _Col("price_type", "TEXT", True, True), _Col("status", "TEXT", True, True),
        _Col("captured_at", "NUMERIC", False, False), _Col("change_reason", "TEXT", False, False),
        _Col("source", "TEXT", True, True),
    ), (
        _Idx("ix_market_reference_prices_price_type_period", ("price_type", "period"), True),
        _Idx("ix_market_reference_prices_status", ("status",), False),
    )) + (
        # ix_market_reference_prices_period DUSURULMESI: 9d4a2f6b18ce AYNI
        # adla unique=False olarak yeniden olusturabilir — madde 4,
        # ACIKCA modellenmis supersession (bkz. 18100a648086'nin AYNI
        # index icin simetrik kontrolu).
        _idx_dropped_atom_ex(
            fp, "market_reference_prices", "ix_market_reference_prices_period",
            superseding_shapes=(
                (_Idx("ix_market_reference_prices_period", ("period",), False), "9d4a2f6b18ce"),
            ),
        ),
        # NOT: updated_by icin BENZER bir backfill atomu BILEREK YOKTUR —
        # updated_by kolonu 18100a648086'dan BERI zaten var (011'in kendi
        # eklemedigi bir kolon); varligi 011'in calisip calismadigi
        # hakkinda HICBIR ayirt edici bilgi tasimaz (18100a648086 calisir
        # calismaz HER ZAMAN var olur) — boyle bir atom yalniz gurultu
        # ekler, kanit eklemez.
        _unprovable_data_atom(fp, "market_reference_prices", "captured_at", "011-backfill"),
    )
    return _classify(
        "011_market_prices_ptf_admin", GraphSegment.TRUNK, "market_reference_prices: PTF admin alanlari", atoms,
        preconditions_ok=_tables_present(fp, ("market_reference_prices",)),
    )


# ── A DALI (uretim kuyrugu): 012 -> 013 ─────────────────────────────────
def _rev_012(fp, con):
    atoms = _table_create_atoms(fp, "ptf_drift_log", (
        _Col("id", "INTEGER", True, False), _Col("created_at", "NUMERIC", True, True),
        _Col("period", "TEXT", True, False), _Col("canonical_price", "REAL", True, False),
        _Col("legacy_price", "REAL", False, False), _Col("delta_abs", "REAL", False, False),
        _Col("delta_pct", "REAL", False, False), _Col("severity", "TEXT", True, False),
        _Col("request_hash", "TEXT", True, False), _Col("customer_id", "INTEGER", False, False),
    ), ("id",), (), (
        _Idx("ix_ptf_drift_log_created_at", ("created_at",), False),
        _Idx("ix_ptf_drift_log_period", ("period",), False),
        _Idx("ix_ptf_drift_log_request_hash", ("request_hash",), False),
    )) + (_ptf_severity_atom_012(con), _ptf_request_hash_len_atom(con))
    return _classify("012_add_ptf_drift_log_table", GraphSegment.BRANCH_A, "ptf_drift_log tablosu", atoms)


def _rev_013(fp, con):
    atoms = (_ptf_severity_atom_013(con),)
    return _classify(
        "013_extend_ptf_drift_severity", GraphSegment.BRANCH_A,
        "ptf_drift_log.severity CHECK'ini 3 degere genislet", atoms,
        preconditions_ok=_tables_present(fp, ("ptf_drift_log",)),
    )


# ── B DALI (canonical): a93beeaddf82 -> ... -> 9d4a2f6b18ce ─────────────
def _rev_a93beeaddf82(fp, con):
    atoms = (
        _table_create_atoms(fp, "contracts", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("customer_id", "INTEGER", False, False), _Col("offer_id", "INTEGER", True, False),
            _Col("legal_profile_id", "INTEGER", False, False),
            _Col("authorized_representative_id", "INTEGER", False, False),
            _Col("contract_number", "TEXT", False, False), _Col("status", "TEXT", True, False),
            _Col("template_version", "TEXT", False, False), _Col("start_date", "NUMERIC", False, False),
            _Col("end_date", "NUMERIC", False, False), _Col("contract_snapshot_json", "NUMERIC", False, False),
            _Col("extraction_snapshot_json", "NUMERIC", False, False), _Col("pdf_storage_ref", "TEXT", False, False),
            _Col("pdf_sha256", "TEXT", False, False), _Col("finalized_at", "NUMERIC", False, False),
            _Col("created_by", "TEXT", False, False), _Col("created_at", "NUMERIC", False, False),
        ), ("id",), (
            "authorized_representative_id->customer_authorized_representatives.id",
            "customer_id->customers.id", "legal_profile_id->customer_legal_profiles.id",
            "offer_id->offers.id",
        ), (
            _Idx("ix_contracts_contract_number", ("contract_number",), True),
            _Idx("ix_contracts_customer_id", ("customer_id",), False),
            _Idx("ix_contracts_id", ("id",), False), _Idx("ix_contracts_offer_id", ("offer_id",), False),
            _Idx("ix_contracts_status", ("status",), False), _Idx("ix_contracts_tenant_id", ("tenant_id",), False),
        ))
        + _table_create_atoms(fp, "customer_authorized_representatives", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("customer_id", "INTEGER", False, False), _Col("legal_profile_id", "INTEGER", False, False),
            _Col("full_name", "TEXT", True, False), _Col("national_id", "TEXT", True, False),
            _Col("authority_type", "TEXT", False, False), _Col("authority_scope", "TEXT", False, False),
            _Col("authority_start_date", "NUMERIC", False, False), _Col("authority_end_date", "NUMERIC", False, False),
            _Col("is_indefinite", "NUMERIC", True, False), _Col("source_document_id", "INTEGER", False, False),
            _Col("verification_status", "TEXT", True, False), _Col("created_at", "NUMERIC", False, False),
            _Col("updated_at", "NUMERIC", False, False),
        ), ("id",), (
            "customer_id->customers.id", "legal_profile_id->customer_legal_profiles.id",
            "source_document_id->uploaded_reference_documents.id",
        ), (
            _Idx("ix_customer_authorized_representatives_customer_id", ("customer_id",), False),
            _Idx("ix_customer_authorized_representatives_id", ("id",), False),
            _Idx("ix_customer_authorized_representatives_legal_profile_id", ("legal_profile_id",), False),
            _Idx("ix_customer_authorized_representatives_tenant_id", ("tenant_id",), False),
            _Idx("ix_customer_authorized_representatives_verification_status", ("verification_status",), False),
        ))
        + _table_create_atoms(fp, "customer_legal_profiles", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("customer_id", "INTEGER", False, False), _Col("legal_name", "TEXT", True, False),
            _Col("tax_number", "TEXT", True, False), _Col("tax_office", "TEXT", True, False),
            _Col("mersis_number", "TEXT", False, False), _Col("trade_registry_number", "TEXT", False, False),
            _Col("registered_address", "TEXT", True, False), _Col("facility_address", "TEXT", False, False),
            _Col("notification_address", "TEXT", False, False), _Col("verification_status", "TEXT", True, False),
            _Col("created_at", "NUMERIC", False, False), _Col("updated_at", "NUMERIC", False, False),
        ), ("id",), ("customer_id->customers.id",), (
            _Idx("ix_customer_legal_profiles_customer_id", ("customer_id",), False),
            _Idx("ix_customer_legal_profiles_id", ("id",), False),
            _Idx("ix_customer_legal_profiles_tenant_id", ("tenant_id",), False),
            _Idx("ix_customer_legal_profiles_verification_status", ("verification_status",), False),
        ))
        + _table_create_atoms(fp, "document_extraction_runs", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("document_id", "INTEGER", True, False), _Col("extractor_type", "TEXT", True, False),
            _Col("extractor_version", "TEXT", True, False), _Col("model_name", "TEXT", True, False),
            _Col("prompt_version", "TEXT", True, False), _Col("status", "TEXT", True, False),
            _Col("raw_response_ref", "TEXT", False, False), _Col("error_code", "TEXT", False, False),
            _Col("started_at", "NUMERIC", False, False), _Col("completed_at", "NUMERIC", False, False),
        ), ("id",), ("document_id->uploaded_reference_documents.id",), (
            _Idx("ix_document_extraction_runs_document_id", ("document_id",), False),
            _Idx("ix_document_extraction_runs_id", ("id",), False),
            _Idx("ix_document_extraction_runs_status", ("status",), False),
            _Idx("ix_document_extraction_runs_tenant_id", ("tenant_id",), False),
        ))
        + _table_create_atoms(fp, "document_field_candidates", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("document_id", "INTEGER", True, False), _Col("extraction_run_id", "INTEGER", True, False),
            _Col("field_name", "TEXT", True, False), _Col("raw_value", "TEXT", False, False),
            _Col("normalized_value", "TEXT", False, False), _Col("source_page", "INTEGER", True, False),
            _Col("source_text", "TEXT", False, False), _Col("confidence", "REAL", True, False),
            _Col("validation_status", "TEXT", True, False), _Col("conflict_status", "TEXT", True, False),
            _Col("user_decision", "TEXT", False, False), _Col("corrected_value", "TEXT", False, False),
            _Col("decided_by", "TEXT", False, False), _Col("decided_at", "NUMERIC", False, False),
            _Col("created_at", "NUMERIC", False, False),
        ), ("id",), (
            "document_id->uploaded_reference_documents.id", "extraction_run_id->document_extraction_runs.id",
        ), (
            _Idx("ix_document_field_candidates_conflict_status", ("conflict_status",), False),
            _Idx("ix_document_field_candidates_document_id", ("document_id",), False),
            _Idx("ix_document_field_candidates_extraction_run_id", ("extraction_run_id",), False),
            _Idx("ix_document_field_candidates_field_name", ("field_name",), False),
            _Idx("ix_document_field_candidates_id", ("id",), False),
            _Idx("ix_document_field_candidates_tenant_id", ("tenant_id",), False),
            _Idx("ix_document_field_candidates_validation_status", ("validation_status",), False),
        ))
        + _table_create_atoms(fp, "uploaded_reference_documents", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("customer_id", "INTEGER", False, False), _Col("document_type", "TEXT", True, False),
            _Col("original_filename", "TEXT", True, False), _Col("mime_type", "TEXT", True, False),
            _Col("file_size", "INTEGER", True, False), _Col("sha256", "TEXT", True, False),
            _Col("storage_ref", "TEXT", True, False), _Col("processing_status", "TEXT", True, False),
            _Col("uploaded_at", "NUMERIC", False, False),
        ), ("id",), ("customer_id->customers.id",), (
            _Idx("ix_uploaded_reference_documents_customer_id", ("customer_id",), False),
            _Idx("ix_uploaded_reference_documents_document_type", ("document_type",), False),
            _Idx("ix_uploaded_reference_documents_id", ("id",), False),
            _Idx("ix_uploaded_reference_documents_processing_status", ("processing_status",), False),
            _Idx("ix_uploaded_reference_documents_sha256", ("sha256",), False),
            _Idx("ix_uploaded_reference_documents_tenant_id", ("tenant_id",), False),
        ))
        + (
            # sqlite_autoindex_...1 (dedup unique kisit): BU revizyonun
            # kendi hali 3 kolonludur (tenant_id,customer_id,sha256).
            # dc8343278cfa bunu document_type ile 4 koluna GENISLETIR —
            # madde 4, ACIKCA modellenmis supersession.
            _idx_atom_ex(
                fp, "uploaded_reference_documents",
                _Idx("sqlite_autoindex_uploaded_reference_documents_1",
                     ("tenant_id", "customer_id", "sha256"), True),
                superseded_by=((
                    _Idx("sqlite_autoindex_uploaded_reference_documents_1",
                         ("tenant_id", "customer_id", "sha256", "document_type"), True),
                    "dc8343278cfa",
                ),),
            ),
        )
    )
    return _classify(
        "a93beeaddf82", GraphSegment.BRANCH_B, "contract generation v1 tablolari", atoms,
        preconditions_ok=_tables_present(fp, ("customers", "offers")),
    )


def _rev_dc8343278cfa(fp, con):
    atoms = (_idx_atom_ex(
        fp, "uploaded_reference_documents",
        _Idx("sqlite_autoindex_uploaded_reference_documents_1",
             ("tenant_id", "customer_id", "sha256", "document_type"), True),
        # a93beeaddf82'nin KENDI (henuz genisletilmemis, 3 kolonlu) hali
        # bir "bozukluk" degil, "dc8343278cfa henuz calismadi" demektir.
        precursor_shapes=(
            _Idx("sqlite_autoindex_uploaded_reference_documents_1",
                 ("tenant_id", "customer_id", "sha256"), True),
        ),
    ),)
    return _classify(
        "dc8343278cfa", GraphSegment.BRANCH_B,
        "uploaded_reference_documents dedup'ini document_type ile genislet", atoms,
        preconditions_ok=_tables_present(fp, ("uploaded_reference_documents",)),
    )


def _rev_8b9a332a3680(fp, con):
    atoms = (
        _table_create_atoms(fp, "activities", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("customer_id", "INTEGER", False, False), _Col("offer_id", "INTEGER", False, False),
            _Col("contract_id", "INTEGER", False, False), _Col("activity_type", "TEXT", True, False),
            _Col("title", "TEXT", False, False), _Col("body", "TEXT", False, False),
            _Col("occurred_at", "NUMERIC", True, False), _Col("created_at", "NUMERIC", False, False),
        ), ("id",), ("contract_id->contracts.id", "customer_id->customers.id", "offer_id->offers.id"), (
            _Idx("ix_activities_activity_type", ("activity_type",), False),
            _Idx("ix_activities_contract_id", ("contract_id",), False),
            _Idx("ix_activities_customer_id", ("customer_id",), False),
            _Idx("ix_activities_id", ("id",), False), _Idx("ix_activities_offer_id", ("offer_id",), False),
            _Idx("ix_activities_tenant_id", ("tenant_id",), False),
        ))
        + _table_create_atoms(fp, "tasks", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("customer_id", "INTEGER", False, False), _Col("offer_id", "INTEGER", False, False),
            _Col("contract_id", "INTEGER", False, False), _Col("title", "TEXT", True, False),
            _Col("description", "TEXT", False, False), _Col("status", "TEXT", True, False),
            _Col("due_at", "NUMERIC", False, False), _Col("completed_at", "NUMERIC", False, False),
            _Col("created_at", "NUMERIC", False, False), _Col("updated_at", "NUMERIC", False, False),
        ), ("id",), ("contract_id->contracts.id", "customer_id->customers.id", "offer_id->offers.id"), (
            _Idx("ix_tasks_contract_id", ("contract_id",), False),
            _Idx("ix_tasks_customer_id", ("customer_id",), False),
            _Idx("ix_tasks_due_at", ("due_at",), False), _Idx("ix_tasks_id", ("id",), False),
            _Idx("ix_tasks_offer_id", ("offer_id",), False), _Idx("ix_tasks_status", ("status",), False),
            _Idx("ix_tasks_tenant_id", ("tenant_id",), False),
        ))
    )
    return _classify(
        "8b9a332a3680", GraphSegment.BRANCH_B, "activities + tasks tablolari (S2)", atoms,
        preconditions_ok=_tables_present(fp, ("customers", "offers", "contracts")),
    )


def _rev_e340ce40c05c(fp, con):
    atoms = (
        _table_create_atoms(fp, "prospect_companies", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("legal_name", "TEXT", False, False), _Col("trade_name", "TEXT", False, False),
            _Col("normalized_name", "TEXT", False, False), _Col("website", "TEXT", False, False),
            _Col("normalized_domain", "TEXT", False, False), _Col("phone", "TEXT", False, False),
            _Col("address", "TEXT", False, False), _Col("city", "TEXT", False, False),
            _Col("district", "TEXT", False, False), _Col("sector", "TEXT", False, False),
            _Col("industrial_zone", "TEXT", False, False), _Col("status", "TEXT", True, False),
            _Col("qualification_reason", "TEXT", False, False), _Col("qualification_note", "TEXT", False, False),
            _Col("customer_id", "INTEGER", False, False), _Col("duplicate_of_id", "INTEGER", False, False),
            _Col("discovered_at", "NUMERIC", False, False), _Col("last_verified_at", "NUMERIC", False, False),
            _Col("created_at", "NUMERIC", False, False), _Col("updated_at", "NUMERIC", False, False),
        ), ("id",), ("customer_id->customers.id", "duplicate_of_id->prospect_companies.id"), (
            _Idx("ix_prospect_companies_city", ("city",), False),
            _Idx("ix_prospect_companies_customer_id", ("customer_id",), False),
            _Idx("ix_prospect_companies_id", ("id",), False),
            _Idx("ix_prospect_companies_normalized_domain", ("normalized_domain",), False),
            _Idx("ix_prospect_companies_normalized_name", ("normalized_name",), False),
            _Idx("ix_prospect_companies_status", ("status",), False),
            _Idx("ix_prospect_companies_tenant_id", ("tenant_id",), False),
        ))
        + _table_create_atoms(fp, "prospect_contacts", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("prospect_company_id", "INTEGER", True, False), _Col("full_name", "TEXT", False, False),
            _Col("job_title", "TEXT", False, False), _Col("email", "TEXT", False, False),
            _Col("phone", "TEXT", False, False), _Col("contact_type", "TEXT", True, False),
            _Col("verification_status", "TEXT", True, False), _Col("source_id", "INTEGER", False, False),
            _Col("created_at", "NUMERIC", False, False), _Col("updated_at", "NUMERIC", False, False),
        ), ("id",), ("prospect_company_id->prospect_companies.id", "source_id->prospect_sources.id"), (
            _Idx("ix_prospect_contacts_email", ("email",), False),
            _Idx("ix_prospect_contacts_id", ("id",), False),
            _Idx("ix_prospect_contacts_prospect_company_id", ("prospect_company_id",), False),
            _Idx("ix_prospect_contacts_tenant_id", ("tenant_id",), False),
        ))
        + _table_create_atoms(fp, "prospect_sources", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("prospect_company_id", "INTEGER", True, False), _Col("source_type", "TEXT", True, False),
            _Col("source_url", "TEXT", True, False), _Col("source_title", "TEXT", False, False),
            _Col("content_hash", "TEXT", False, False), _Col("evidence_text", "TEXT", False, False),
            _Col("fetch_status", "TEXT", True, False), _Col("discovered_at", "NUMERIC", False, False),
            _Col("last_checked_at", "NUMERIC", False, False),
        ), ("id",), ("prospect_company_id->prospect_companies.id",), (
            _Idx("ix_prospect_sources_content_hash", ("content_hash",), False),
            _Idx("ix_prospect_sources_id", ("id",), False),
            _Idx("ix_prospect_sources_prospect_company_id", ("prospect_company_id",), False),
            _Idx("ix_prospect_sources_tenant_id", ("tenant_id",), False),
        ))
    )
    return _classify(
        "e340ce40c05c", GraphSegment.BRANCH_B, "prospect_companies/sources/contacts (S4)", atoms,
        preconditions_ok=_tables_present(fp, ("customers",)),
    )


def _rev_f4e7efc70c80(fp, con):
    atoms = (
        _table_create_atoms(fp, "outreach_messages", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("prospect_company_id", "INTEGER", False, False), _Col("contact_id", "INTEGER", False, False),
            _Col("customer_id", "INTEGER", False, False), _Col("channel", "TEXT", True, False),
            _Col("recipient_category", "TEXT", True, False), _Col("recipient_email_snapshot", "TEXT", True, False),
            _Col("recipient_legal_type", "TEXT", False, False), _Col("subject", "TEXT", True, False),
            _Col("body_snapshot", "TEXT", True, False), _Col("system_footer_snapshot", "TEXT", True, False),
            _Col("compliance_snapshot_json", "NUMERIC", False, False),
            _Col("source_snapshot_json", "NUMERIC", False, False), _Col("status", "TEXT", True, False),
            _Col("provider", "TEXT", False, False), _Col("provider_message_id", "TEXT", False, False),
            _Col("approved_at", "NUMERIC", False, False), _Col("sent_at", "NUMERIC", False, False),
            _Col("delivered_at", "NUMERIC", False, False), _Col("bounced_at", "NUMERIC", False, False),
            _Col("failed_at", "NUMERIC", False, False), _Col("failure_code", "TEXT", False, False),
            _Col("replied_at", "NUMERIC", False, False), _Col("created_at", "NUMERIC", False, False),
            _Col("updated_at", "NUMERIC", False, False),
        ), ("id",), (
            "contact_id->prospect_contacts.id", "customer_id->customers.id",
            "prospect_company_id->prospect_companies.id",
        ), (
            _Idx("ix_outreach_messages_contact_id", ("contact_id",), False),
            _Idx("ix_outreach_messages_customer_id", ("customer_id",), False),
            _Idx("ix_outreach_messages_id", ("id",), False),
            _Idx("ix_outreach_messages_prospect_company_id", ("prospect_company_id",), False),
            _Idx("ix_outreach_messages_provider_message_id", ("provider_message_id",), False),
            _Idx("ix_outreach_messages_recipient_email_snapshot", ("recipient_email_snapshot",), False),
            _Idx("ix_outreach_messages_status", ("status",), False),
            _Idx("ix_outreach_messages_tenant_id", ("tenant_id",), False),
        ))
        + _table_create_atoms(fp, "outreach_templates", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("name", "TEXT", True, False), _Col("subject_template", "TEXT", True, False),
            _Col("body_template", "TEXT", True, False), _Col("version", "INTEGER", True, False),
            _Col("active", "NUMERIC", True, False), _Col("created_at", "NUMERIC", False, False),
            _Col("updated_at", "NUMERIC", False, False),
        ), ("id",), (), (
            _Idx("ix_outreach_templates_id", ("id",), False),
            _Idx("ix_outreach_templates_tenant_id", ("tenant_id",), False),
        ))
        + _table_create_atoms(fp, "suppression_entries", (
            _Col("id", "INTEGER", True, False), _Col("tenant_id", "TEXT", True, False),
            _Col("email_normalized", "TEXT", True, False), _Col("reason", "TEXT", True, False),
            _Col("source", "TEXT", False, False), _Col("note", "TEXT", False, False),
            _Col("effective_at", "NUMERIC", False, False), _Col("created_at", "NUMERIC", False, False),
        ), ("id",), (), (
            _Idx("ix_suppression_entries_email_normalized", ("email_normalized",), False),
            _Idx("ix_suppression_entries_id", ("id",), False),
            _Idx("ix_suppression_entries_tenant_id", ("tenant_id",), False),
        ))
    )
    return _classify(
        "f4e7efc70c80", GraphSegment.BRANCH_B,
        "outreach_messages / outreach_templates / suppression_entries (S5)", atoms,
        preconditions_ok=_tables_present(fp, ("customers", "prospect_companies", "prospect_contacts")),
    )


def _rev_beda29569b0d(fp, con):
    atoms = _column_add_atoms(fp, "prospect_companies", (
        _Col("verified_legal_type", "TEXT", False, False),
        _Col("verified_legal_type_note", "TEXT", False, False),
        _Col("verified_legal_type_set_at", "NUMERIC", False, False),
    ))
    return _classify(
        "beda29569b0d", GraphSegment.BRANCH_B, "prospect_companies.verified_legal_type* kolonlari (S5)", atoms,
        preconditions_ok=_tables_present(fp, ("prospect_companies",)),
    )


def _rev_7b3e1c8a52df(fp, con):
    atoms = (
        _table_create_atoms(fp, "analysis_cache", (
            _Col("id", "INTEGER", True, False), _Col("cache_key", "TEXT", True, False),
            _Col("customer_id", "TEXT", True, False), _Col("period", "TEXT", True, False),
            _Col("params_hash", "TEXT", True, False), _Col("result_json", "TEXT", True, False),
            _Col("created_at", "NUMERIC", True, False), _Col("expires_at", "NUMERIC", True, False),
            _Col("hit_count", "INTEGER", True, False),
        ), ("id",), (), (
            _Idx("idx_cache_customer_period", ("customer_id", "period"), False),
            _Idx("idx_cache_expires", ("expires_at",), False), _Idx("ix_analysis_cache_id", ("id",), False),
            _Idx("sqlite_autoindex_analysis_cache_1", ("cache_key",), True),
        ))
        + _table_create_atoms(fp, "consumption_hourly_data", (
            _Col("id", "INTEGER", True, False), _Col("profile_id", "INTEGER", True, False),
            _Col("date", "TEXT", True, False), _Col("hour", "INTEGER", True, False),
            _Col("consumption_kwh", "REAL", True, False),
        ), ("id",), ("profile_id->consumption_profiles.id",), (
            _Idx("ix_consumption_hourly_data_id", ("id",), False),
            _Idx("ix_consumption_hourly_data_profile_id", ("profile_id",), False),
            _Idx("sqlite_autoindex_consumption_hourly_data_1", ("profile_id", "date", "hour"), True),
        ))
        + _table_create_atoms(fp, "consumption_profiles", (
            _Col("id", "INTEGER", True, False), _Col("customer_id", "TEXT", True, False),
            _Col("customer_name", "TEXT", False, False), _Col("period", "TEXT", True, False),
            _Col("version", "INTEGER", True, False), _Col("source", "TEXT", True, False),
            _Col("profile_type", "TEXT", True, False), _Col("template_name", "TEXT", False, False),
            _Col("total_kwh", "REAL", True, False), _Col("is_active", "INTEGER", True, False),
            _Col("created_at", "NUMERIC", True, False), _Col("updated_at", "NUMERIC", True, False),
        ), ("id",), (), (
            _Idx("idx_consumption_active", ("customer_id", "period", "is_active"), False),
            _Idx("ix_consumption_profiles_customer_id", ("customer_id",), False),
            _Idx("ix_consumption_profiles_id", ("id",), False),
            _Idx("ix_consumption_profiles_period", ("period",), False),
            _Idx("sqlite_autoindex_consumption_profiles_1", ("customer_id", "period", "version"), True),
        ))
        + _table_create_atoms(fp, "data_versions", (
            _Col("id", "INTEGER", True, False), _Col("customer_id", "TEXT", False, False),
            _Col("data_type", "TEXT", True, False), _Col("period", "TEXT", True, False),
            _Col("version", "INTEGER", True, False), _Col("row_count", "INTEGER", True, False),
            _Col("quality_score", "INTEGER", False, False), _Col("upload_filename", "TEXT", False, False),
            _Col("uploaded_by", "TEXT", False, False), _Col("is_active", "INTEGER", True, False),
            _Col("created_at", "NUMERIC", True, False),
        ), ("id",), (), (
            _Idx("idx_data_versions_lookup", ("data_type", "period", "customer_id"), False),
            _Idx("ix_data_versions_id", ("id",), False),
            _Idx("sqlite_autoindex_data_versions_1", ("data_type", "period", "customer_id", "version"), True),
        ))
        + _table_create_atoms(fp, "hourly_market_prices", (
            _Col("id", "INTEGER", True, False), _Col("period", "TEXT", True, False),
            _Col("date", "TEXT", True, False), _Col("hour", "INTEGER", True, False),
            _Col("ptf_tl_per_mwh", "REAL", True, False), _Col("smf_tl_per_mwh", "REAL", True, False),
            _Col("version", "INTEGER", True, False), _Col("is_active", "INTEGER", True, False),
            _Col("source", "TEXT", True, False), _Col("currency", "TEXT", True, False),
            _Col("created_at", "NUMERIC", True, False), _Col("updated_at", "NUMERIC", True, False),
        ), ("id",), (), (
            _Idx("idx_hourly_market_date_hour", ("date", "hour"), False),
            _Idx("idx_hourly_market_period_active", ("period", "is_active"), False),
            _Idx("ix_hourly_market_prices_id", ("id",), False),
            _Idx("ix_hourly_market_prices_period", ("period",), False),
            _Idx("sqlite_autoindex_hourly_market_prices_1", ("period", "date", "hour", "version"), True),
        ))
        + _table_create_atoms(fp, "monthly_yekdem_prices", (
            _Col("id", "INTEGER", True, False), _Col("period", "TEXT", True, False),
            _Col("yekdem_tl_per_mwh", "REAL", True, False), _Col("source", "TEXT", True, False),
            _Col("created_at", "NUMERIC", True, False), _Col("updated_at", "NUMERIC", True, False),
        ), ("id",), (), (
            _Idx("ix_monthly_yekdem_prices_id", ("id",), False),
            _Idx("ix_monthly_yekdem_prices_period", ("period",), True),
        ))
        + _table_create_atoms(fp, "price_change_history", (
            _Col("id", "INTEGER", True, False), _Col("price_record_id", "INTEGER", True, False),
            _Col("price_type", "TEXT", True, False), _Col("period", "TEXT", True, False),
            _Col("action", "TEXT", True, False), _Col("old_value", "REAL", False, False),
            _Col("new_value", "REAL", True, False), _Col("old_status", "TEXT", False, False),
            _Col("new_status", "TEXT", True, False), _Col("change_reason", "TEXT", False, False),
            _Col("updated_by", "TEXT", False, False), _Col("source", "TEXT", False, False),
            _Col("created_at", "NUMERIC", False, False),
        ), ("id",), ("price_record_id->market_reference_prices.id",), (
            _Idx("ix_price_change_history_created_at", ("created_at",), False),
            _Idx("ix_price_change_history_id", ("id",), False),
            _Idx("ix_price_change_history_price_record_id", ("price_record_id",), False),
        ))
        + _table_create_atoms(fp, "profile_templates", (
            _Col("id", "INTEGER", True, False), _Col("name", "TEXT", True, False),
            _Col("display_name", "TEXT", True, False), _Col("description", "TEXT", False, False),
            _Col("hourly_weights", "TEXT", True, False), _Col("is_builtin", "INTEGER", True, False),
            _Col("created_at", "NUMERIC", True, False), _Col("updated_at", "NUMERIC", True, False),
        ), ("id",), (), (
            _Idx("ix_profile_templates_id", ("id",), False),
            _Idx("sqlite_autoindex_profile_templates_1", ("name",), True),
        ))
    )
    return _classify(
        "7b3e1c8a52df", GraphSegment.BRANCH_B, "pricing modulu 8 tablosu", atoms,
        preconditions_ok=_tables_present(fp, ("market_reference_prices",)),
    )


def _rev_9d4a2f6b18ce(fp, con):
    atoms = (_idx_atom_ex(
        fp, "market_reference_prices",
        _Idx("ix_market_reference_prices_period", ("period",), False),
        # 18100a648086'nin KENDI (011 tarafindan henuz dusurulmemis,
        # unique=True) hali bir "bozukluk" degil, "9d4a2f6b18ce (ve
        # muhtemelen 011) henuz calismadi" demektir.
        precursor_shapes=(_Idx("ix_market_reference_prices_period", ("period",), True),),
    ),)
    return _classify(
        "9d4a2f6b18ce", GraphSegment.BRANCH_B, "ix_market_reference_prices_period (non-unique)", atoms,
        preconditions_ok=_tables_present(fp, ("market_reference_prices",)),
    )


# ── MERGE: 351d314819d5 (no-op — bkz. migration docstring'i) ────────────
def _rev_merge(a_tip: FullRevisionClassification, b_tip: FullRevisionClassification) -> FullRevisionClassification:
    """
    351d314819d5'in upgrade()/downgrade()'i BOSTUR (alembic dosyasinda
    dogrulandi) — kendi somut atomu YOKTUR. Bu noktanin "guvenligi"
    TAMAMEN iki ebeveyninin (013 ve 9d4a2f6b18ce) durumuna baglidir.
    """
    revision, segment, summary = "351d314819d5", GraphSegment.MERGE, "iki dalin birlesim noktasi (no-op)"
    bad = {FullEffectClass.CONFLICT, FullEffectClass.UNKNOWN_OR_UNPROVABLE}
    if a_tip.effect_class in bad or b_tip.effect_class in bad:
        return FullRevisionClassification(
            revision, segment, summary, FullEffectClass.UNKNOWN_OR_UNPROVABLE, (),
            f"ebeveynlerden en az biri cozumlenemedi: 013={a_tip.effect_class.value} "
            f"9d4a2f6b18ce={b_tip.effect_class.value}",
        )
    if (a_tip.effect_class is FullEffectClass.PRESENT_EXACT
            and b_tip.effect_class is FullEffectClass.PRESENT_EXACT):
        return FullRevisionClassification(
            revision, segment, summary, FullEffectClass.PRESENT_EXACT, (),
            "iki ebeveyn de PRESENT_EXACT; no-op merge'in kendi DDL etkisi yok",
        )
    return FullRevisionClassification(
        revision, segment, summary, FullEffectClass.ABSENT_SAFE_TO_APPLY, (),
        f"ebeveynler cozumlenebilir durumda (013={a_tip.effect_class.value} "
        f"9d4a2f6b18ce={b_tip.effect_class.value}); normal alembic upgrade ile guvenle ulasilir",
    )


# Graf sirasinda TUM revizyon siniflandiricilari (segment etiketiyle).
_TRUNK_REVISIONS = (
    _rev_001_initial, _rev_002, _rev_003, _rev_18100a648086, _rev_c1a7f0e94d52,
    _rev_004, _rev_005, _rev_006, _rev_007, _rev_008, _rev_009, _rev_010, _rev_011,
)
_BRANCH_A_REVISIONS = (_rev_012, _rev_013)
_BRANCH_B_REVISIONS = (
    _rev_a93beeaddf82, _rev_dc8343278cfa, _rev_8b9a332a3680, _rev_e340ce40c05c,
    _rev_f4e7efc70c80, _rev_beda29569b0d, _rev_7b3e1c8a52df, _rev_9d4a2f6b18ce,
)


def classify_full_lineage(db_path: str) -> tuple[FullRevisionClassification, ...]:
    """
    Base'ten canonical head'e (351d314819d5) TUM grafi (govde+A+B+merge,
    24 revizyon) SALT-OKUNUR probe ile, graf sirasinda siniflandirir.

    `alembic_version` tablosunun VARLIGINA guvenmez/bakmaz — sinif tamamen
    somut sema/kisit kanitindan turetilir. Bu, PDSMR-R4 Faz 0'in
    kanitladigi "gercek canli DB'de alembic_version hic yok" durumunu
    dogrudan ele almak icin tasarlandi.

    HICBIR yazma yapmaz (mode=ro), alembic/create_all cagirmaz, model
    metadata import etmez.

    Cagrildigi yerler:
    - tests/test_pdsmr_r4_full_lineage.py [PDSMR-R4/Faz2]
      (Faz 2 kapsaminda BASKA HICBIR cagiran YOK — bkz. modul dokstring'i
       madde 8. Faz 3/4 onaylanmadan production/startup/adoption'a
       BAGLANMAZ.)
    """
    fp = collect_fingerprint(db_path)
    uri = "file:" + db_path.replace("\\", "/").replace(" ", "%20") + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        trunk = tuple(f(fp, con) for f in _TRUNK_REVISIONS)
        branch_a = tuple(f(fp, con) for f in _BRANCH_A_REVISIONS)
        branch_b = tuple(f(fp, con) for f in _BRANCH_B_REVISIONS)
        merge = (_rev_merge(branch_a[-1], branch_b[-1]),)
        return trunk + branch_a + branch_b + merge
    finally:
        con.close()


__all__ = [
    "AtomResult",
    "CANONICAL_BRANCH",
    "CANONICAL_HEAD",
    "EffectClass",
    "FullEffectClass",
    "FullRevisionClassification",
    "GraphSegment",
    "PRODUCTION_BRANCH_TIP",
    "RevisionClassification",
    "classify_canonical_branch",
    "classify_full_lineage",
]
