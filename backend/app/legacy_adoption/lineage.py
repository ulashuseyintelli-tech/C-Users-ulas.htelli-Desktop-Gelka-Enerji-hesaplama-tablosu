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


__all__ = [
    "CANONICAL_BRANCH",
    "CANONICAL_HEAD",
    "EffectClass",
    "PRODUCTION_BRANCH_TIP",
    "RevisionClassification",
    "classify_canonical_branch",
]
