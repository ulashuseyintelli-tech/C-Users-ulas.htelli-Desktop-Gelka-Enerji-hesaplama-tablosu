"""
06_usage_and_duplications.py — A8a + A8b birleşik rapor

Read-only (R16). Kanıt-temelli (R1). Deterministik, idempotent.

İki eksenli analiz:

A8a — Sessiz duplikasyon konsolidasyonu
  Domain bazında "aynı kavram, farklı kaynak/yol":
    - F-PTF    (P0): hourly_market_prices vs market_reference_prices (canonical vs legacy)
    - F-YEKDEM (P1): monthly_yekdem_prices vs market_reference_prices (canonical vs legacy)
    - F-VALIDATION: app.validator vs app.invoice.validation.* (legacy vs yeni stack, A7 DEAD)
    - F-DEAD_ROUTER: pdf_api.router (A3+A5+A6 kanıtlı)
    - F-DUAL_FE: api.ts deprecated fns vs market-prices/marketPricesApi.ts (A5 kanıtlı)
    - F-ORPHAN_MODULE: 15 orphan modül (A6 kanıtlı)

  Her bulgu için: domain, kaynaklar, birleştiği akış, severity (P0..P3),
  devredilecek spec (roadmap_input).

A8b — Usage signal (gerçek kullanım kanıtı)
  Her BE endpoint için 5 kaynak taraması:
    (1) Non-FE kod referansı (backend CLI scripts, smoke_test_*.py, rq_worker)
    (2) Test referansı (backend/tests/)
    (3) Shell/batch (*.sh, *.bat, *.ps1)
    (4) Load test (k6/*.js)
    (5) Docs/runbook (*.md) — curl örnekleri
  Ayrıca FE referansı (A5 matched'ten direkt gelir).

  Sınıflandırma:
    ACTIVE       — FE veya non-FE kaynaktan çağrı var
    INTERNAL     — Sadece non-FE (CLI/job/docs) kaynaktan çağrı var
    TEST_ONLY    — Yalnızca testler referans veriyor
    DEAD         — Hiçbir kaynakta yok (FE, non-FE, test, docs, load-test)
    UNREACHABLE  — Route wire değil (A6 reachable=false) — özel durum

Girdi:
    artifacts/phase1_endpoints.json
    artifacts/phase1_fe_fetches.json
    artifacts/phase2_endpoint_mapping.json
    artifacts/phase1_imports.json
    artifacts/phase2_invoice_flow_sources.json
    artifacts/phase1_db_inventory.json

Çıktı:
    stdout: özet
    artifacts/phase3_duplications.json   (tek birleşik rapor)

Şema (top-level):
{
  "_meta": {...},
  "duplications":        [...],   # A8a — domain bazlı
  "endpoint_usage":      [...],   # A8b — endpoint bazlı
  "module_usage":        [...],   # A6 orphan/dormant modüller için çapraz
  "usage_distribution":  {...},   # özet sayımlar
  "cleanup_list":        {...},   # silinebilir / migrasyon / bağlanacak
  "roadmap_input":       [...]    # C fazı için kanıt-tabanlı maddeler
}
"""

from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from typing import Any

# UTF-8 stdout
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

# ------------------------------------------------------------------------------
# Yol keşfi
# ------------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
SPEC_DIR = SCRIPT_DIR.parent
WORKSPACE_ROOT = SPEC_DIR.parent.parent.parent
ARTIFACTS_DIR = SPEC_DIR / "artifacts"

A3 = ARTIFACTS_DIR / "phase1_endpoints.json"
A4 = ARTIFACTS_DIR / "phase1_fe_fetches.json"
A5 = ARTIFACTS_DIR / "phase2_endpoint_mapping.json"
A6 = ARTIFACTS_DIR / "phase1_imports.json"
A7 = ARTIFACTS_DIR / "phase2_invoice_flow_sources.json"
A2 = ARTIFACTS_DIR / "phase1_db_inventory.json"

OUT = ARTIFACTS_DIR / "phase3_duplications.json"


def _posix_rel(path: Path) -> str:
    try:
        rel = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        rel = path
    return rel.as_posix()


# ------------------------------------------------------------------------------
# Usage signal taraması
# ------------------------------------------------------------------------------
# Tarama kümeleri (deterministik sıralama için sorted kullanılır)
NON_FE_CODE_GLOBS = [
    ("backend/**/*.py",     "non_fe_code"),   # backend/app kapsamı ayrı; burası diğer .py'ler
    ("scripts/**/*.py",     "non_fe_code"),
]
# Tests ayrı
TEST_GLOBS = [("backend/tests/**/*.py", "tests")]
SHELL_GLOBS = [
    ("**/*.sh",  "shell"),
    ("**/*.bat", "shell"),
    ("**/*.ps1", "shell"),
]
LOAD_TEST_GLOBS = [("k6/**/*.js", "load_test")]
DOCS_GLOBS = [
    ("docs/**/*.md",                "docs"),
    ("monitoring/runbooks/**/*.md", "docs"),
]

# Dışlanan ağaçlar
EXCLUDE_PARTS = {
    "node_modules", ".git", ".hypothesis", ".venv", "__pycache__",
    "dist", "build", ".kiro",   # kiro spec'leri içinde endpoint örnekleri var, false positive yapar
}


def _is_excluded(path: Path) -> bool:
    return any(p in EXCLUDE_PARTS for p in path.parts)


def _collect_files(globs: list[tuple[str, str]]) -> list[tuple[Path, str]]:
    """(glob, category) listesinden (file_path, category) döndür."""
    out: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for pattern, cat in globs:
        for f in WORKSPACE_ROOT.glob(pattern):
            if not f.is_file():
                continue
            if _is_excluded(f):
                continue
            if f in seen:
                continue
            seen.add(f)
            out.append((f, cat))
    out.sort(key=lambda x: (x[1], str(x[0])))
    return out


def _make_endpoint_patterns(path: str) -> list[re.Pattern[str]]:
    """Endpoint path'inden referans aramak için regex pattern listesi üret.

    Örnekler:
      /api/pricing/analyze              → '/api/pricing/analyze'
      /api/epias/prices/{period}        → '/api/epias/prices/' ile başlayan literal
                                           +  template: /api/epias/prices/{period}
      /admin/market-prices/{period}/lock → '/admin/market-prices/' + ...

    Stratejiler:
      1) Tam path literal (dinamik yoksa)
      2) Prefix + '/' (dinamik varsa) — ilk {param}'a kadar
    """
    patterns: list[re.Pattern[str]] = []
    has_param = "{" in path
    escaped = re.escape(path).replace(r"\{", "{").replace(r"\}", "}")
    # Literal match (her iki durumda da — non-dynamic path'ler için tam eşleşme, dynamic için
    # template olarak dokümantasyonda geçebilir)
    patterns.append(re.compile(escaped.replace(r"\/", "/")))
    if has_param:
        # Prefix'i al: ilk { öncesi
        prefix = path.split("{", 1)[0]
        if prefix and len(prefix) >= 4:  # çok kısa prefix'ler (/) false positive yapar
            # Sonda '/' olmalı ki rastgele substring'e takılmasın
            prefix_escaped = re.escape(prefix)
            patterns.append(re.compile(prefix_escaped))
    return patterns


def _scan_endpoint_usage(
    endpoints: list[dict],
    files_by_category: dict[str, list[Path]],
) -> dict[str, dict[str, list[dict]]]:
    """Her endpoint için kategori bazlı referans listesi.

    Döner: {endpoint_path: {category: [{"file", "line", "snippet"}, ...]}}
    """
    result: dict[str, dict[str, list[dict]]] = {}

    # Benzersiz path kümesi (method bazlı değil; path bazlı usage sinyal)
    unique_paths = sorted({e["path"] for e in endpoints})

    # Her path için pattern listesi — önceden hesapla
    path_patterns: dict[str, list[re.Pattern[str]]] = {
        p: _make_endpoint_patterns(p) for p in unique_paths
    }
    for p in unique_paths:
        result[p] = {cat: [] for cat in files_by_category.keys()}

    # Dosyaları kategorilere göre tara
    for category, files in files_by_category.items():
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # Her satırda her path pattern'ı dene
            lines = text.splitlines()
            file_rel = _posix_rel(f)
            for path, patterns in path_patterns.items():
                # İlk geçen pattern için kayıt ekle; sayı şişmesin diye 3 örnekle sınırla
                hits: list[dict] = []
                for i, line in enumerate(lines, start=1):
                    stripped = line.strip()
                    for pat in patterns:
                        if pat.search(line):
                            hits.append({
                                "file": file_rel,
                                "line": i,
                                "snippet": stripped[:140],
                            })
                            break
                    if len(hits) >= 3:
                        break
                if hits:
                    result[path][category].extend(hits)

    # Deterministik sıralama
    for path, cats in result.items():
        for cat in cats:
            cats[cat].sort(key=lambda r: (r["file"], r["line"]))
    return result


# ------------------------------------------------------------------------------
# A8a: Duplikasyon kayıtlarını kur
# ------------------------------------------------------------------------------
def build_duplications(a2: dict, a5: dict, a6: dict, a7: dict) -> list[dict]:
    dups: list[dict] = []

    # --- F-PTF (P0) — A2 smoke'dan kilitli
    dups.append({
        "id": "F-PTF",
        "domain": "ptf",
        "severity": "P0",
        "type": "parallel_path",
        "canonical": "hourly_market_prices",
        "sources": [
            {"kind": "canonical", "table": "hourly_market_prices", "role": "risk-engine / pricing_router"},
            {"kind": "legacy",    "table": "market_reference_prices", "role": "manual mode / legacy lookup"},
        ],
        "converges_at": "teklif & risk hesabı (aynı dönem, farklı kaynaktan PTF)",
        "evidence": ["artifacts/phase1_db_inventory.json (smoke F-PTF auto-flag)"],
        "delegated_to_spec": "ptf-sot-unification",
    })

    # --- F-YEKDEM-eski (P1) — A2'den
    dups.append({
        "id": "F-YEKDEM-eski",
        "domain": "yekdem",
        "severity": "P1",
        "type": "legacy_migration",
        "canonical": "monthly_yekdem_prices",
        "sources": [
            {"kind": "canonical", "table": "monthly_yekdem_prices", "role": "new pipeline"},
            {"kind": "legacy",    "table": "market_reference_prices", "role": "legacy YEKDEM rows (39 eksik dönem)"},
        ],
        "converges_at": "fatura doğrulama & teklif (yekdem bileşeni)",
        "evidence": ["artifacts/phase1_db_inventory.json (YEKDEM cross-source)"],
        "delegated_to_spec": "yekdem-legacy-migration",
    })

    # --- F-VALIDATION (P1) — A7 DEAD verdict'ten
    verdict = a7.get("new_validation_stack", {}).get("verdict", "UNKNOWN")
    if verdict == "DEAD":
        dups.append({
            "id": "F-VALIDATION",
            "domain": "invoice_validation",
            "severity": "P1",
            "type": "unconnected_new_stack",
            "canonical": None,  # karar eksik: yeni stack mi bağlanacak, legacy mi sürecek?
            "sources": [
                {"kind": "legacy",
                 "module": "app.validator",
                 "entry": "validate_extraction",
                 "role": "üretim yolu (6 handler, 9 çağrı)"},
                {"kind": "unconnected_new",
                 "module": "app.invoice.validation",
                 "entry": "validate / apply_enforcement / shadow_validate_hook",
                 "role": "tam test kapsamı, 0 üretim çağrısı"},
            ],
            "converges_at": "fatura validasyon kararı",
            "evidence": [
                "artifacts/phase1_imports.json (alive_from_tests_only: tüm invoice.validation.*)",
                "artifacts/phase2_invoice_flow_sources.json (verdict=DEAD)",
            ],
            "delegated_to_spec": "invoice-validation-prod-hardening",
        })

    # --- F-DEAD_ROUTER (P1) — A5+A6'dan
    orphan_routers = a6.get("orphan_routers", [])
    if orphan_routers:
        for orph in orphan_routers:
            dups.append({
                "id": f"F-DEAD_ROUTER:{orph['router_name']}",
                "domain": "route_wiring",
                "severity": "P1",
                "type": "orphan_router",
                "canonical": None,
                "sources": [
                    {"kind": "orphan_router",
                     "router": orph["router_name"],
                     "prefix": orph["prefix"],
                     "defined_in": orph["defined_in"],
                     "endpoint_count": orph["endpoint_count"]},
                ],
                "converges_at": "pdf job akışı (tasarlanmış ama bağlanmamış)",
                "evidence": [
                    "artifacts/phase1_endpoints.json (router tanımı)",
                    "artifacts/phase2_endpoint_mapping.json (3 BE_ONLY kaydı)",
                    "artifacts/phase1_imports.json (router_reachability=false)",
                ],
                "delegated_to_spec": "pdf-render-worker",
            })

    # --- F-DUAL_FE (P2) — A5'ten
    dual_fe = a5.get("dual_fe_client", [])
    if dual_fe:
        dups.append({
            "id": "F-DUAL_FE",
            "domain": "fe_adapter",
            "severity": "P2",
            "type": "dual_client",
            "canonical": "frontend/src/market-prices/marketPricesApi.ts",
            "sources": [
                {
                    "kind": "entry",
                    "method": d["method"],
                    "path": d["path"],
                    "files": d.get("files", []),
                    "clients": d.get("clients", []),
                }
                for d in dual_fe
            ],
            "converges_at": "admin market-prices aynı endpoint'e 2 FE çağıran",
            "evidence": ["artifacts/phase2_endpoint_mapping.json (dual_fe_client)"],
            "delegated_to_spec": "pricing-consistency-fixes",  # veya yeni bir ptf-admin spec'ine
        })

    return dups


# ------------------------------------------------------------------------------
# A8b: Endpoint usage classification
# ------------------------------------------------------------------------------
def classify_endpoint(
    endpoint: dict,
    fe_refs: list[dict],
    non_fe_refs: dict[str, list[dict]],
    reachable: bool,
) -> tuple[str, str]:
    """Sınıflandır ve kısa bir gerekçe döndür."""
    if not reachable:
        return "UNREACHABLE", "router not wired (orphan router)"
    has_fe = bool(fe_refs)
    has_non_fe_code = bool(non_fe_refs.get("non_fe_code"))
    has_shell = bool(non_fe_refs.get("shell"))
    has_load = bool(non_fe_refs.get("load_test"))
    has_docs = bool(non_fe_refs.get("docs"))
    has_test = bool(non_fe_refs.get("tests"))
    non_fe_signal = has_non_fe_code or has_shell or has_load or has_docs

    if has_fe and non_fe_signal:
        return "ACTIVE", "FE + non-FE kullanım"
    if has_fe:
        return "ACTIVE", "FE kullanıyor"
    if non_fe_signal:
        parts = []
        if has_non_fe_code: parts.append("code")
        if has_shell:       parts.append("shell")
        if has_load:        parts.append("load")
        if has_docs:        parts.append("docs")
        return "INTERNAL", f"FE yok; non-FE sinyal: {','.join(parts)}"
    if has_test:
        return "TEST_ONLY", "yalnızca test referansı"
    return "DEAD", "hiçbir kaynakta referans yok"


# ------------------------------------------------------------------------------
# Ana
# ------------------------------------------------------------------------------
def main() -> int:
    for p in (A3, A4, A5, A6, A7, A2):
        if not p.is_file():
            print(f"[HATA] Girdi eksik: {p}", file=sys.stderr)
            return 2

    be = json.loads(A3.read_text(encoding="utf-8"))
    fe = json.loads(A4.read_text(encoding="utf-8"))
    mapping = json.loads(A5.read_text(encoding="utf-8"))
    imports = json.loads(A6.read_text(encoding="utf-8"))
    invoice = json.loads(A7.read_text(encoding="utf-8"))
    db = json.loads(A2.read_text(encoding="utf-8"))

    endpoints: list[dict] = be.get("endpoints", [])
    fe_unique: list[dict] = fe.get("unique_calls", [])
    fe_raw: list[dict] = fe.get("calls", [])
    endpoint_reach: list[dict] = imports.get("endpoint_reachability", [])
    reach_by_key: dict[tuple[str, str], bool] = {
        (e["method"], e["path"]): e["reachable"] for e in endpoint_reach
    }

    # --- A8a: duplikasyonlar ------------------------------------------------
    duplications = build_duplications(db, mapping, imports, invoice)

    # --- A8b: usage signal --------------------------------------------------
    # Dosya kümelerini hazırla
    cat_files: dict[str, list[Path]] = {
        "non_fe_code": [],
        "tests":       [],
        "shell":       [],
        "load_test":   [],
        "docs":        [],
    }
    for f, cat in _collect_files(NON_FE_CODE_GLOBS):
        # backend/app zaten üretim kodu sayılır, buradan ayrı bir sinyal değiliz:
        # endpoint string'inin backend/app içinde geçmesi doğaldır (tanım satırı).
        # Bu yüzden backend/app/ altını non_fe_code SAYMIYORUZ; sadece backend/
        # köküne ait ama app/ dışı + scripts/.
        fr = _posix_rel(f)
        if fr.startswith("backend/app/"):
            continue
        if fr.startswith("backend/tests/"):
            continue
        cat_files["non_fe_code"].append(f)
    for f, cat in _collect_files(TEST_GLOBS):
        cat_files["tests"].append(f)
    for f, cat in _collect_files(SHELL_GLOBS):
        cat_files["shell"].append(f)
    for f, cat in _collect_files(LOAD_TEST_GLOBS):
        cat_files["load_test"].append(f)
    for f, cat in _collect_files(DOCS_GLOBS):
        cat_files["docs"].append(f)

    # Endpoint referans taraması
    usage_refs = _scan_endpoint_usage(endpoints, cat_files)

    # FE referansları: A4 raw üzerinden (method + path bazlı gruplama, path bazlı topla)
    fe_by_path: dict[str, list[dict]] = {}
    for c in fe_raw:
        fe_by_path.setdefault(c["path"], []).append({
            "file": c["file"], "line": c["line"], "method": c["method"], "client": c["client"],
        })
    for p in fe_by_path:
        fe_by_path[p].sort(key=lambda r: (r["file"], r["line"]))

    # Endpoint başına sınıflandırma (method + path birlikte ama A5 kategorisine göre sayımı endpoint bazlı tutuyoruz)
    endpoint_usage: list[dict] = []
    # Özetler için
    class_counts: dict[str, int] = {
        "ACTIVE": 0, "INTERNAL": 0, "TEST_ONLY": 0, "DEAD": 0, "UNREACHABLE": 0,
    }
    for e in endpoints:
        path = e["path"]
        method = e["method"]
        fe_refs = fe_by_path.get(path, [])
        non_fe_refs = usage_refs.get(path, {})
        reachable = reach_by_key.get((method, path), True)
        usage_class, reason = classify_endpoint(e, fe_refs, non_fe_refs, reachable)
        class_counts[usage_class] = class_counts.get(usage_class, 0) + 1

        endpoint_usage.append({
            "method": method,
            "path": path,
            "function": e["function"],
            "file": e["file"],
            "line": e["line"],
            "router": e["router"],
            "reachable": reachable,
            "fe_used": bool(fe_refs),
            "fe_refs": fe_refs,
            "non_fe_refs": {
                cat: non_fe_refs.get(cat, [])
                for cat in ("non_fe_code", "shell", "load_test", "docs", "tests")
            },
            "usage_class": usage_class,
            "reason": reason,
        })
    endpoint_usage.sort(key=lambda r: (r["usage_class"], r["path"], r["method"]))

    # --- Module usage (A6 orphan/dormant için çapraz) -----------------------
    modules: list[dict] = imports.get("modules", [])
    module_usage: list[dict] = []
    for m in modules:
        if m["status"] in ("orphan", "dormant"):
            # Orphan modüller için: ismi doküman/script/shell'de geçiyor mu?
            name_token = m["module"]
            mentions: list[dict] = []
            for cat_name, files in cat_files.items():
                if cat_name == "tests":  # test zaten A6'da ayrı sinyaldi
                    continue
                for f in files:
                    try:
                        text = f.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    for i, line in enumerate(text.splitlines(), start=1):
                        if name_token in line:
                            mentions.append({
                                "file": _posix_rel(f),
                                "line": i,
                                "category": cat_name,
                                "snippet": line.strip()[:140],
                            })
                            break  # dosya başına tek satır
            module_usage.append({
                "module": m["module"],
                "file": m.get("file"),
                "status": m["status"],
                "reason": m.get("reason"),
                "imported_by_tests_count": len(m.get("imported_by_tests", [])),
                "external_mentions": mentions,
                "external_mention_count": len(mentions),
            })
    module_usage.sort(key=lambda r: (r["status"], r["module"]))

    # --- Cleanup listesi ----------------------------------------------------
    cleanup_list = {
        "silinebilir_aday": [],     # DEAD + UNREACHABLE endpoints + 0-mention orphan modules
        "migrasyon_aday":   [],     # LEGACY duplication / eski kaynaklar
        "baglanacak":       [],     # F-VALIDATION + dormant with roadmap
        "fe_bağımlı_ama_BE_ulaşılamaz": [],  # UNREACHABLE ama FE çağırıyor (kritik bug)
    }
    for u in endpoint_usage:
        if u["usage_class"] == "UNREACHABLE":
            if u["fe_used"]:
                cleanup_list["fe_bağımlı_ama_BE_ulaşılamaz"].append(
                    {"method": u["method"], "path": u["path"], "file": u["file"], "reason": u["reason"]}
                )
            else:
                cleanup_list["silinebilir_aday"].append(
                    {"method": u["method"], "path": u["path"], "file": u["file"], "why": "orphan router"}
                )
        elif u["usage_class"] == "DEAD":
            cleanup_list["silinebilir_aday"].append(
                {"method": u["method"], "path": u["path"], "file": u["file"], "why": "no references"}
            )
    for m in module_usage:
        if m["status"] == "orphan" and m["external_mention_count"] == 0:
            cleanup_list["silinebilir_aday"].append({
                "kind": "module", "module": m["module"], "file": m["file"],
                "why": "orphan + no external mentions",
            })
    # Duplikasyon → migrasyon/baglanacak
    for d in duplications:
        if d["id"] == "F-VALIDATION":
            cleanup_list["baglanacak"].append(d)
        elif d["type"] in ("legacy_migration", "parallel_path"):
            cleanup_list["migrasyon_aday"].append(d)
        elif d["id"].startswith("F-DEAD_ROUTER"):
            cleanup_list["silinebilir_aday"].append({
                "kind": "router", "router": d["sources"][0]["router"],
                "prefix": d["sources"][0]["prefix"],
                "why": "orphan router (not included)",
            })

    # --- Roadmap input (C fazı için) ----------------------------------------
    roadmap_input = [
        {
            "id": d["id"], "severity": d["severity"],
            "delegated_to_spec": d["delegated_to_spec"],
            "summary": f"{d['domain']} — {d['type']}",
        }
        for d in duplications
    ]

    usage_distribution = {
        "endpoints_total": len(endpoints),
        "by_class": class_counts,
        "orphan_modules_with_external_mention":
            sum(1 for m in module_usage if m["status"] == "orphan" and m["external_mention_count"] > 0),
        "orphan_modules_fully_dead":
            sum(1 for m in module_usage if m["status"] == "orphan" and m["external_mention_count"] == 0),
        "dormant_modules": sum(1 for m in module_usage if m["status"] == "dormant"),
    }

    artifact: dict[str, Any] = {
        "_meta": {
            "script": "06_usage_and_duplications.py",
            "inputs": [_posix_rel(p) for p in (A2, A3, A4, A5, A6, A7)],
            "scan_counts": {cat: len(files) for cat, files in cat_files.items()},
        },
        "duplications": duplications,
        "endpoint_usage": endpoint_usage,
        "module_usage": module_usage,
        "usage_distribution": usage_distribution,
        "cleanup_list": cleanup_list,
        "roadmap_input": roadmap_input,
    }

    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Stdout özeti -------------------------------------------------------
    print("=" * 78)
    print("A8 — Duplikasyon konsolidasyonu + Usage signal (birleşik)")
    print("=" * 78)
    print()
    print(f"[A8a] Duplikasyon bulguları: {len(duplications)}")
    for d in duplications:
        print(f"  {d['severity']:<3s} {d['id']:<24s} {d['type']:<22s} → {d['delegated_to_spec']}")
    print()
    print(f"[A8b] Endpoint usage distribution (n={usage_distribution['endpoints_total']}):")
    for cls, n in class_counts.items():
        print(f"  {cls:<12s} {n}")
    print()

    # UNREACHABLE + DEAD örnekleri
    unreach = [u for u in endpoint_usage if u["usage_class"] == "UNREACHABLE"]
    dead = [u for u in endpoint_usage if u["usage_class"] == "DEAD"]
    internal = [u for u in endpoint_usage if u["usage_class"] == "INTERNAL"]
    test_only_eps = [u for u in endpoint_usage if u["usage_class"] == "TEST_ONLY"]

    if unreach:
        print(f"UNREACHABLE ({len(unreach)}):")
        for u in unreach[:5]:
            print(f"  {u['method']:<6s} {u['path']:<40s} {u['file']}:{u['line']}")
        print()
    if dead:
        print(f"DEAD ({len(dead)}) — hiçbir sinyal yok; ilk 10:")
        for u in dead[:10]:
            print(f"  {u['method']:<6s} {u['path']:<40s} {u['file']}:{u['line']}")
        if len(dead) > 10:
            print(f"  ... ({len(dead)-10} tane daha)")
        print()
    if internal:
        print(f"INTERNAL ({len(internal)}) — FE yok ama sistem kullanıyor; ilk 10:")
        for u in internal[:10]:
            refs = ", ".join(
                f"{cat}({len(u['non_fe_refs'].get(cat, []))})"
                for cat in ("non_fe_code", "shell", "load_test", "docs")
                if u['non_fe_refs'].get(cat)
            )
            print(f"  {u['method']:<6s} {u['path']:<40s} [{refs}]")
        if len(internal) > 10:
            print(f"  ... ({len(internal)-10} tane daha)")
        print()
    if test_only_eps:
        print(f"TEST_ONLY ({len(test_only_eps)}) — yalnızca test referansı; ilk 5:")
        for u in test_only_eps[:5]:
            print(f"  {u['method']:<6s} {u['path']:<40s}")
        print()

    # Module özeti
    print(f"Orphan modül durumu ({len([m for m in module_usage if m['status']=='orphan'])}):")
    fully_dead = [m for m in module_usage if m['status']=='orphan' and m['external_mention_count']==0]
    with_mention = [m for m in module_usage if m['status']=='orphan' and m['external_mention_count']>0]
    print(f"  Tamamen ölü (0 external mention): {len(fully_dead)}")
    for m in fully_dead[:8]:
        print(f"    - {m['module']:<40s} {m['file']}")
    if with_mention:
        print(f"  Dışarıda mention var (dikkat): {len(with_mention)}")
        for m in with_mention:
            mentions_str = ", ".join(
                f"{ref['category']}:{ref['file']}" for ref in m['external_mentions'][:2]
            )
            print(f"    - {m['module']:<40s} ({mentions_str})")
    print()

    # Cleanup özet
    print("Cleanup özet:")
    print(f"  silinebilir_aday          : {len(cleanup_list['silinebilir_aday'])}")
    print(f"  migrasyon_aday            : {len(cleanup_list['migrasyon_aday'])}")
    print(f"  baglanacak                : {len(cleanup_list['baglanacak'])}")
    print(f"  fe_bağımlı_ama_ulaşılamaz : {len(cleanup_list['fe_bağımlı_ama_BE_ulaşılamaz'])}")
    print()
    print(f"Artifact: {_posix_rel(OUT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
