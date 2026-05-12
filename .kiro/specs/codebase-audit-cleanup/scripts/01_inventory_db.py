"""
01_inventory_db.py v2 — DB envanteri (R2, R7, R19, R20, R23, R24)

v2 düzeltmeleri (design revizyonu):
- Regex suffix tabanlı (ptf_tl_per_, yekdem_tl_per_ vb.)
- Snapshot/history tablolar kategorisi (offers, invoices, price_change_history...)
- Type-aware domain matching (fiyat domain'leri sadece numeric kolonlarda)
- Granüler invoice domain ayrımı (master/fk/period_bucket)
- Cross-source period coverage matrisi
- F-PTF otomatik P0 auto-flag
- Tablo rol etiketleri (canonical/snapshot/audit/legacy/config/cache)

Read-only (R16). Kanıt-temelli (R1).

Kullanım:
    python .kiro/specs/codebase-audit-cleanup/scripts/01_inventory_db.py

Çıktı:
    stdout: insan-okunur rapor
    .kiro/specs/codebase-audit-cleanup/artifacts/phase1_db_inventory.json
"""

from __future__ import annotations
import json
import re
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

# ------------------------------------------------------------------------------
# Yol keşfi
# ------------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
SPEC_DIR = SCRIPT_DIR.parent
WORKSPACE_ROOT = SPEC_DIR.parent.parent.parent
ARTIFACTS_DIR = SPEC_DIR / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

DB_CANDIDATES = [
    WORKSPACE_ROOT / "backend" / "gelka_enerji.db",
    WORKSPACE_ROOT / "gelka_enerji.db",
]

# ------------------------------------------------------------------------------
# Domain tanımları — suffix/pattern tabanlı (v2 fix #1)
# ------------------------------------------------------------------------------
# PRICE_DOMAIN: sadece numeric kolonlarda eşleşir (v2 fix #3 — type-aware)
PRICE_DOMAINS = {
    "ptf":                  [r"^ptf$", r"ptf_tl_per_", r"^weighted_ptf$", r"weighted_ptf_tl_per_"],
    "yekdem":               [r"^yekdem$", r"yekdem_tl_per_"],
    "smf":                  [r"^smf$", r"smf_tl_per_"],
    "dist_tariff_price":    [r"distribution_unit_price", r"dist_.*_tl_per_kwh"],
    "retail_tariff_price":  [r"unit_price_tl_per_kwh", r"retail_.*_tl_per_", r"current_unit_price", r"demand_unit_price"],
    "btv":                  [r"^btv", r"_btv_"],
    "kdv":                  [r"^kdv", r"^vat_", r"_vat_"],
    "commission":           [r"bayi_pay", r"_commission", r"margin_rate"],
    "total_cost":           [r"total_cost_tl", r"_cost_tl$"],
    "sales_price":          [r"sales_price", r"offer_total", r"current_total"],
    "savings":              [r"savings_(amount|ratio)"],
}

# NON_PRICE_DOMAIN: herhangi bir tipte eşleşebilir
NON_PRICE_DOMAINS = {
    "consumption_kwh":      [r"consumption_kwh", r"total_kwh", r"_kwh$"],
    "period":               [r"^period$"],
    "version":              [r"^version$", r"_hash$", r"version_num"],
    "status":               [r"^status$", r"^is_active$", r"^is_locked$"],
    "cache_key":            [r"cache_key", r"params_hash"],
    "invoice_master":       [r"^invoices$"],  # tablo adına match (özel kullanım)
    "invoice_reference_fk": [r"^invoice_id$"],
    "invoice_period_bucket":[r"^invoice_period$"],
    "change_reason":        [r"^change_reason$"],
    "tariff_group":         [r"tariff_group"],  # enum/string — fiyat değil
    "price_type":           [r"^price_type$"],  # enum — PTF/YEKDEM etiketi
}

# Numeric SQLite tipleri (v2 fix #3)
NUMERIC_TYPES = {"FLOAT", "NUMERIC", "DECIMAL", "REAL", "DOUBLE"}


def is_numeric_type(col_type: str) -> bool:
    t = (col_type or "").upper()
    return any(nt in t for nt in NUMERIC_TYPES) or t.startswith("INT")


def match_domains(column_name: str, column_type: str) -> list[str]:
    """Type-aware domain eşleşmesi."""
    col_lower = column_name.lower()
    hits: list[str] = []

    # Fiyat domain'leri — sadece numeric kolonlar
    if is_numeric_type(column_type):
        for domain, patterns in PRICE_DOMAINS.items():
            for pat in patterns:
                if re.search(pat, col_lower):
                    hits.append(domain)
                    break

    # Non-price domain'ler — tip bağımsız
    for domain, patterns in NON_PRICE_DOMAINS.items():
        for pat in patterns:
            if re.search(pat, col_lower):
                hits.append(domain)
                break

    return hits


# ------------------------------------------------------------------------------
# Tablo rol etiketleri (v2 fix #6)
# ------------------------------------------------------------------------------
TABLE_ROLES = {
    # Canonical sources (SoT adayları)
    "hourly_market_prices":  "canonical_source",
    "monthly_yekdem_prices": "canonical_source",
    "distribution_tariffs":  "config",
    "consumption_profiles":  "canonical_source",
    "profile_templates":     "config",
    "customers":             "canonical_source",
    "data_versions":         "canonical_source",

    # Snapshot / audit — duplikasyon taramasından muaf
    "offers":                "snapshot",
    "invoices":              "snapshot",
    "consumption_hourly_data": "snapshot",
    "price_change_history":  "audit_trail",
    "audit_logs":             "audit_trail",
    "incidents":             "audit_trail",
    "jobs":                  "snapshot",
    "webhook_deliveries":    "audit_trail",

    # Legacy — migrasyon gerekli
    "market_reference_prices": "legacy_deprecated",

    # Cache
    "analysis_cache":        "cache",

    # Infra
    "alembic_version":       "infra",
    "webhook_configs":       "config",
}


def get_role(table_name: str) -> str:
    return TABLE_ROLES.get(table_name, "unknown")


# Duplikasyon taramasında dikkate alınacak roller (v2 fix #2)
DUPLICATION_ELIGIBLE_ROLES = {"canonical_source", "legacy_deprecated", "derived_view"}


# ------------------------------------------------------------------------------
# SQL guard
# ------------------------------------------------------------------------------
FORBIDDEN_SQL = re.compile(
    r"\b(DROP|DELETE|TRUNCATE|ALTER|INSERT|UPDATE|REPLACE|CREATE|ATTACH|DETACH)\b",
    re.IGNORECASE,
)


def safe_execute(cursor: sqlite3.Cursor, sql: str, params: tuple = ()) -> list:
    if FORBIDDEN_SQL.search(sql):
        raise RuntimeError(f"GUARD: write-SQL rejected: {sql[:80]}")
    return cursor.execute(sql, params).fetchall()


# ------------------------------------------------------------------------------
# Envanter
# ------------------------------------------------------------------------------
def find_db() -> Path:
    for cand in DB_CANDIDATES:
        if cand.exists() and cand.stat().st_size > 0:
            return cand
    raise FileNotFoundError(f"Hiçbir DB adayı bulunamadı: {DB_CANDIDATES}")


def inspect_db(db_path: Path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = conn.cursor()

    alembic_current = None
    try:
        rows = safe_execute(cur, "SELECT version_num FROM alembic_version LIMIT 1")
        if rows:
            alembic_current = rows[0][0]
    except sqlite3.OperationalError:
        pass

    tables_raw = safe_execute(
        cur,
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name",
    )
    table_names = [r[0] for r in tables_raw]

    tables: list[dict] = []
    for tname in table_names:
        try:
            row_count = safe_execute(cur, f'SELECT COUNT(*) FROM "{tname}"')[0][0]
        except sqlite3.OperationalError as e:
            row_count = f"ERR: {e}"

        col_info = safe_execute(cur, f'PRAGMA table_info("{tname}")')
        columns = []
        domain_hits_for_table: set[str] = set()
        for cid, name, ctype, notnull, dflt, pk in col_info:
            domains = match_domains(name, ctype)
            domain_hits_for_table.update(domains)
            columns.append({
                "name": name,
                "type": ctype,
                "pk": bool(pk),
                "notnull": bool(notnull),
                "default": dflt,
                "domains": domains,
                "is_numeric": is_numeric_type(ctype),
            })

        idx_info = safe_execute(cur, f'PRAGMA index_list("{tname}")')
        indexes = [{"name": i[1], "unique": bool(i[2]), "origin": i[3]} for i in idx_info]

        sample_rows: list = []
        if isinstance(row_count, int) and 0 < row_count <= 5000:
            try:
                rows = safe_execute(cur, f'SELECT * FROM "{tname}" LIMIT 3')
                sample_rows = [list(r) for r in rows]
            except sqlite3.OperationalError:
                pass

        period_info = None
        if any(c["name"].lower() == "period" for c in columns) and isinstance(row_count, int):
            try:
                periods = safe_execute(
                    cur,
                    f'SELECT period, COUNT(*) FROM "{tname}" '
                    f'GROUP BY period ORDER BY period',
                )
                period_info = [{"period": p, "count": c} for p, c in periods]
            except sqlite3.OperationalError:
                pass

        tables.append({
            "name": tname,
            "role": get_role(tname),
            "row_count": row_count,
            "columns": columns,
            "indexes": indexes,
            "domains_hit": sorted(domain_hits_for_table),
            "sample_rows": sample_rows,
            "period_coverage": period_info,
        })

    conn.close()

    return {
        "db_path": str(db_path),
        "db_size_bytes": db_path.stat().st_size,
        "alembic_current": alembic_current,
        "table_count": len(tables),
        "tables": tables,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------------------
# Analiz
# ------------------------------------------------------------------------------
def analyze(inv: dict) -> dict:
    # Domain → kaynak haritası (sadece duplikasyon-uygun roller)
    domain_map: dict[str, list[dict]] = {}
    for t in inv["tables"]:
        dup_eligible = t["role"] in DUPLICATION_ELIGIBLE_ROLES
        for c in t["columns"]:
            for d in c["domains"]:
                domain_map.setdefault(d, []).append({
                    "table": t["name"],
                    "role": t["role"],
                    "column": c["name"],
                    "row_count": t["row_count"],
                    "type": c["type"],
                    "eligible_for_duplication": dup_eligible,
                })

    # Duplikasyon adayları — sadece duplication-eligible rollerde, ≥2 tabloda
    duplications: list[dict] = []
    dup_domains = set(PRICE_DOMAINS.keys())
    for d, locs in domain_map.items():
        if d not in dup_domains:
            continue
        eligible_locs = [l for l in locs if l["eligible_for_duplication"]]
        tables_for_domain = sorted({l["table"] for l in eligible_locs})
        if len(tables_for_domain) > 1:
            def _rc(tn: str) -> int:
                for l in eligible_locs:
                    if l["table"] == tn and isinstance(l["row_count"], int):
                        return l["row_count"]
                return 0
            duplications.append({
                "domain": d,
                "tables": tables_for_domain,
                "locations": eligible_locs,
                "snapshot_references": sorted({
                    l["table"] for l in locs if l["role"] in ("snapshot", "audit_trail")
                }),
                "suspected_sot_candidates": sorted(tables_for_domain, key=_rc, reverse=True),
            })

    # Cross-source period diff (v2 fix #5)
    cross_diffs: list[dict] = []
    for d in ("ptf", "yekdem"):
        sources = [
            t for t in inv["tables"]
            if d in t["domains_hit"] and t["role"] in DUPLICATION_ELIGIBLE_ROLES
        ]
        if len(sources) < 2:
            continue
        per_source = {}
        all_periods: set = set()
        for s in sources:
            periods = {p["period"] for p in (s["period_coverage"] or [])}
            per_source[s["name"]] = periods
            all_periods |= periods
        for name, periods in per_source.items():
            missing = sorted(all_periods - periods)
            if missing:
                cross_diffs.append({
                    "domain": d,
                    "source": name,
                    "has_periods": len(periods),
                    "missing_periods": missing,
                    "missing_count": len(missing),
                    "other_sources_have": sorted(set(per_source) - {name}),
                })

    # F-PTF auto-flag (v2 fix #6 + R24)
    f_ptf_flag = None
    ptf_duplications = [d for d in duplications if d["domain"] == "ptf"]
    if ptf_duplications:
        dup = ptf_duplications[0]
        f_ptf_flag = {
            "finding_id": "F-PTF",
            "severity": "P0",
            "requirement": "R24 (Paralel Hesap Yolu)",
            "title": "PTF canonical kaynak çatışması",
            "sources": dup["tables"],
            "suspected_sot": dup["suspected_sot_candidates"][0] if dup["suspected_sot_candidates"] else None,
            "evidence_chain": [
                "backend/app/pricing/router.py:175-188 — _load_market_records() sadece hourly_market_prices okuyor",
                "backend/app/market_prices.py:74-82 — get_market_prices() market_reference_prices okuyor",
                "Canlı API: POST /api/pricing/analyze period=2025-12 → 404 market_data_not_found",
                "Canlı API: GET /api/epias/prices/2025-12 → MarketReferencePrice satırı döner (aylık PTF)",
            ],
            "business_impact": (
                "Manuel mod → teklif üretimi zincirinde aylık PTF kullanılıyor; "
                "risk analizi saatlik ağırlıklı PTF kullanıyor. Aynı müşteriye farklı "
                "matematik modelle fiyat gösteriliyor."
            ),
            "fix_plan": "R26 Hybrid-C policy (P0-M1/M2/M3)",
        }

    # Uyarılar
    warnings: list[str] = []

    if inv["alembic_current"] is None:
        warnings.append("alembic_version boş → schema drift kontrolü imkansız")

    empty_tables = [t["name"] for t in inv["tables"] if t["row_count"] == 0]
    if empty_tables:
        warnings.append(
            f"{len(empty_tables)} boş tablo: {', '.join(empty_tables[:10])}"
            f"{' …' if len(empty_tables) > 10 else ''}"
        )

    # Cache versioning check (R19)
    for t in inv["tables"]:
        if t["role"] != "cache":
            continue
        col_names = [c["name"].lower() for c in t["columns"]]
        has_version_col = any(
            re.search(p, cn)
            for cn in col_names
            for p in NON_PRICE_DOMAINS["version"] + NON_PRICE_DOMAINS["cache_key"]
        )
        if not has_version_col:
            warnings.append(
                f"Cache tablosu '{t['name']}': source version/hash yok (R19 bulgusu)"
            )

    # Dönem bütünlüğü (sadece duplication-eligible roller)
    period_integrity: list[dict] = []
    for t in inv["tables"]:
        if t["role"] not in DUPLICATION_ELIGIBLE_ROLES:
            continue
        if not t["period_coverage"]:
            continue
        if any(d in t["domains_hit"] for d in ("ptf", "yekdem")):
            periods = sorted({p["period"] for p in t["period_coverage"]})
            period_integrity.append({
                "table": t["name"],
                "role": t["role"],
                "domains": [d for d in ("ptf", "yekdem") if d in t["domains_hit"]],
                "period_count": len(periods),
                "first_period": periods[0] if periods else None,
                "last_period": periods[-1] if periods else None,
                "periods": periods,
            })

    return {
        "domain_map": domain_map,
        "suspected_duplications": duplications,
        "cross_source_period_diff": cross_diffs,
        "f_ptf_flag": f_ptf_flag,
        "warnings": warnings,
        "period_integrity": period_integrity,
        "empty_tables": empty_tables,
    }


# ------------------------------------------------------------------------------
# Rapor
# ------------------------------------------------------------------------------
def print_report(inv: dict, an: dict) -> None:
    p = print
    p("=" * 78)
    p("DB ENVANTER v2 — Gelka Enerji")
    p("=" * 78)
    p(f"DB: {inv['db_path']}")
    p(f"Boyut: {inv['db_size_bytes']:,} byte")
    p(f"Alembic current: {inv['alembic_current']}")
    p(f"Tablo sayısı: {inv['table_count']}")
    p(f"Zaman: {inv['captured_at']}")
    p("")

    # F-PTF öne çek
    if an["f_ptf_flag"]:
        f = an["f_ptf_flag"]
        p("▓" * 78)
        p(f"🚨 {f['finding_id']} [{f['severity']}] — {f['title']}")
        p("▓" * 78)
        p(f"Requirement: {f['requirement']}")
        p(f"Kaynaklar: {', '.join(f['sources'])}")
        p(f"Önerilen SoT: {f['suspected_sot']}")
        p("\nKanıt zinciri:")
        for ev in f["evidence_chain"]:
            p(f"  • {ev}")
        p(f"\nTicari etki:\n  {f['business_impact']}")
        p(f"\nDüzeltme planı: {f['fix_plan']}")
        p("")

    # Tablolar
    p("-" * 78)
    p("TABLOLAR (rol etiketli)")
    p("-" * 78)
    p(f"{'Tablo':<30} {'Rol':<20} {'Satır':>8}  {'Kavramlar'}")
    p(f"{'-'*30} {'-'*20} {'-'*8}  {'-'*30}")
    for t in sorted(inv["tables"], key=lambda x: (x["role"], x["name"])):
        rc = t["row_count"]
        rc_s = f"{rc:,}" if isinstance(rc, int) else str(rc)
        domains = ",".join(t["domains_hit"][:4])
        if len(t["domains_hit"]) > 4:
            domains += f" +{len(t['domains_hit'])-4}"
        p(f"{t['name']:<30} {t['role']:<20} {rc_s:>8}  {domains or '-'}")
    p("")

    # Duplikasyonlar (rol filtreli)
    p("-" * 78)
    p("SESSIZ DUPLIKASYON ADAYLARI (snapshot/audit muaf, type-aware)")
    p("-" * 78)
    if not an["suspected_duplications"]:
        p("(fiyat domain'inde duplikasyon adayı yok)")
    for dup in an["suspected_duplications"]:
        p(f"\n[{dup['domain']}] → {len(dup['tables'])} canonical-uygun kaynak: {', '.join(dup['tables'])}")
        p(f"  SoT adayı (satır sayısına göre): {', '.join(dup['suspected_sot_candidates'])}")
        for loc in dup["locations"]:
            rc = loc["row_count"]
            rc_s = f"{rc:,}" if isinstance(rc, int) else str(rc)
            p(f"    - {loc['table']}.{loc['column']} ({loc['role']}, {rc_s} satır)")
        if dup["snapshot_references"]:
            p(f"  Snapshot/audit referansları (muaf): {', '.join(dup['snapshot_references'])}")
    p("")

    # Cross-source dönem farkı
    p("-" * 78)
    p("CROSS-SOURCE DÖNEM FARKI (aynı domain, farklı tablolar)")
    p("-" * 78)
    if not an["cross_source_period_diff"]:
        p("(fark yok)")
    for diff in an["cross_source_period_diff"]:
        p(f"\n[{diff['domain']}] {diff['source']} eksik ({diff['missing_count']} dönem):")
        p(f"  Diğer kaynaklar ({', '.join(diff['other_sources_have'])}) bu dönemleri tutuyor:")
        miss = diff["missing_periods"]
        p(f"    İlk 5: {miss[:5]}  Son 5: {miss[-5:]}")
    p("")

    # Dönem bütünlüğü
    p("-" * 78)
    p("DÖNEM BÜTÜNLÜĞÜ (canonical/legacy kaynaklar)")
    p("-" * 78)
    for pi in an["period_integrity"]:
        p(f"\n{pi['table']} [{pi['role']}] [{','.join(pi['domains'])}]")
        p(f"  Dönem sayısı: {pi['period_count']}")
        p(f"  İlk: {pi['first_period']}  Son: {pi['last_period']}")
    p("")

    # Uyarılar
    p("-" * 78)
    p("UYARILAR")
    p("-" * 78)
    if not an["warnings"]:
        p("(uyarı yok)")
    for w in an["warnings"]:
        p(f"  ⚠  {w}")
    p("")

    p("=" * 78)
    p(f"✓ Artifact: {ARTIFACTS_DIR / 'phase1_db_inventory.json'}")
    p("=" * 78)


def main() -> int:
    try:
        db = find_db()
    except FileNotFoundError as e:
        print(f"HATA: {e}", file=sys.stderr)
        return 2

    inv = inspect_db(db)
    an = analyze(inv)

    artifact_path = ARTIFACTS_DIR / "phase1_db_inventory.json"
    with open(artifact_path, "w", encoding="utf-8") as f:
        json.dump({"inventory": inv, "analysis": an}, f, ensure_ascii=False, indent=2, default=str)

    print_report(inv, an)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
