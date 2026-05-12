"""
05_invoice_flow_sources.py — Fatura akış kaynak haritası (A7)

Read-only (R16). Kanıt-temelli (R1). Deterministik, idempotent.

Amaç (iki eksenli):
  (a) tasks.md A7: Fatura akışı zinciri (endpoint → extractor → validator →
      calculator) + PTF/YEKDEM kaynak tablosu referansları
  (b) A6 bulgusundan gelen soru: Yeni invoice.validation.* stack'i üretim
      yolundan çağrılıyor mu, yoksa legacy validator.py mi kullanımda?

Girdi:
    artifacts/phase1_endpoints.json   (A3)
    artifacts/phase1_imports.json     (A6)
    backend/app/main.py, validator.py, invoice/**
    backend/app/calculator.py, extractor.py (dosya varsa)

Çıktı:
    stdout: akış özeti + validation wiring verdict
    artifacts/phase2_invoice_flow_sources.json

Şema:
{
  "_meta": {...},
  "invoice_endpoints": [
    {
      "method": "POST",
      "path": "/analyze-invoice",
      "function": "analyze_invoice",
      "file": "backend/app/main.py",
      "line": 776,
      "calls_detected": ["extract_invoice_data", "validate_extraction"],
      "imports_extractor": "app.extractor",
      "imports_validator": "app.validator",
      "imports_calculator": null
    },
    ...
  ],
  "legacy_validator": {
    "module": "app.validator",
    "entry": "validate_extraction",
    "used_by_handlers": [...],
    "reachable_from_main": true
  },
  "new_validation_stack": {
    "package": "app.invoice.validation",
    "entry_candidates": ["validate", "apply_enforcement", "shadow_validate_hook"],
    "reachable_from_main": false,
    "reachable_callers_in_production": [],
    "verdict": "DEAD | BROKEN | OK"
  },
  "source_tables": {
    "ptf": {
      "hourly_market_prices":      [{"file", "line", "context"}],
      "market_reference_prices":   [...]
    },
    "yekdem": {
      "monthly_yekdem_prices":     [...],
      "market_reference_prices":   [...]   # eski YEKDEM kaynağı
    },
    "referenced_from_handlers": {...}     # endpoint → tablo referansı
  },
  "flow_diagram": [
    "POST /analyze-invoice",
    "  → app.extractor::extract_invoice_data",
    "  → app.validator::validate_extraction",
    "POST /full-process",
    "  → app.extractor::extract_invoice_data",
    "  → app.validator::validate_extraction",
    "POST /calculate-offer",
    "  → app.calculator::calculate_offer",
    ...
  ]
}
"""

from __future__ import annotations
import ast
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

BACKEND_ROOT = WORKSPACE_ROOT / "backend"
APP_ROOT = BACKEND_ROOT / "app"
MAIN_FILE = APP_ROOT / "main.py"

A3_ARTIFACT = ARTIFACTS_DIR / "phase1_endpoints.json"
A6_ARTIFACT = ARTIFACTS_DIR / "phase1_imports.json"
OUT_ARTIFACT = ARTIFACTS_DIR / "phase2_invoice_flow_sources.json"


def _posix_rel(path: Path) -> str:
    try:
        rel = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        rel = path
    return rel.as_posix()


# ------------------------------------------------------------------------------
# Invoice flow endpoint path filtresi
# ------------------------------------------------------------------------------
INVOICE_FLOW_PATH_PREFIXES = [
    "/analyze-invoice",
    "/full-process",
    "/calculate-offer",
    "/invoices",       # /invoices, /invoices/{id}, /invoices/{id}/validate, /invoices/{id}/extract, ...
    "/extraction",     # /extraction/patch-fields, /extraction/apply-suggested-fixes
    "/offers",         # /offers, /offers/{id}, /offers/{id}/generate-pdf, ...
]

# Fatura akışıyla ilgili BE fonksiyon adları (handler body taraması için yaygın
# çağrı isimleri; handler → underlying module resolution'a rehber)
INVOICE_FLOW_CALL_NAMES = {
    # Extractor aileleri
    "extract_invoice_data",
    "extract_invoice",
    "extract_from_pdf",
    "canonical_extract",
    "fast_extract",
    "preprocess_image_bytes",
    "extract_text_from_pdf",
    # Validator (legacy)
    "validate_extraction",
    # Validator (yeni stack)
    "validate",
    "apply_enforcement",
    "shadow_validate_hook",
    "compare_validators",
    # Calculator
    "calculate_offer",
    # PDF generator (teklif)
    "generate_offer_pdf",
    "generate_offer_html",
    "generate_offer_pdf_bytes",
}

# Yeni invoice validation stack'ın giriş noktası adayları
NEW_STACK_ENTRY_POINTS = {
    "app.invoice.validation.validator": ["validate"],
    "app.invoice.validation.enforcement": ["apply_enforcement"],
    "app.invoice.validation.shadow": ["shadow_validate_hook", "compare_validators"],
}


# ------------------------------------------------------------------------------
# AST: bir fonksiyon tanımının içinden çağrı isimlerini topla
# ------------------------------------------------------------------------------
def _collect_call_names_in_fn(fn_node: ast.AST) -> set[str]:
    """Function body içinde yapılan Call'ların func isimlerini döndür.

    Yakalananlar:
      - foo(...)              → "foo"
      - mod.foo(...)          → "foo" (attr adı; modül adı ayrı incelenir)
      - obj.method(...)       → "method"
    """
    names: set[str] = set()
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _collect_imports_top_and_inline(
    file_tree: ast.Module, fn_node: ast.AST | None = None
) -> dict[str, str]:
    """Dosya top-level ve (opsiyonel) fonksiyon body'sindeki import'lardan
    {local_name: module} map'i kur. Relative import'lar "app.X" formuna çevrilir.

    Örnek:
      `from .validator import validate_extraction` →
        { "validate_extraction": "app.validator" }
      `from .invoice.validation import validate`   →
        { "validate": "app.invoice.validation" }
    """
    results: dict[str, str] = {}

    def _process(node: ast.AST) -> None:
        if isinstance(node, ast.ImportFrom):
            level = node.level or 0
            base = node.module or ""
            # Bu script'te relative import'ları 'app.*' olarak kısaltıyoruz;
            # main.py için `from .X import Y` → module "app.X"
            if level == 1 and base:
                fq = f"app.{base}"
            elif level == 1 and not base:
                # 'from . import X' → module app.X
                for alias in node.names:
                    results[alias.asname or alias.name] = f"app.{alias.name}"
                return
            elif level == 2 and base:
                fq = base  # nadir
            elif level == 0:
                fq = base
            else:
                return
            for alias in node.names:
                results[alias.asname or alias.name] = fq
        elif isinstance(node, ast.Import):
            for alias in node.names:
                results[alias.asname or alias.name] = alias.name

    # Top-level
    for child in file_tree.body:
        _process(child)

    # Function body (lazy import'lar dahil)
    if fn_node is not None:
        for child in ast.walk(fn_node):
            _process(child)

    return results


def _find_function(tree: ast.Module, name: str) -> ast.AST | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    return None


# ------------------------------------------------------------------------------
# Source table string tarama
# ------------------------------------------------------------------------------
SOURCE_TABLE_TOKENS = {
    "ptf_tables": [
        "hourly_market_prices",      # canonical PTF
        "market_reference_prices",   # legacy PTF (+ legacy YEKDEM)
    ],
    "yekdem_tables": [
        "monthly_yekdem_prices",     # canonical YEKDEM
        "market_reference_prices",   # legacy YEKDEM (aynı tablo, farklı rol)
    ],
}


def _grep_table_references(root: Path) -> dict[str, list[dict]]:
    """App ağacında tablo adlarının geçtiği yerleri topla.

    Döner: {token: [{file, line, snippet}, ...], ...}
    Deterministik: token sırası alfabetik, dosya+satır sıralı.
    """
    # Token listesi — alfabetik sıralı dict ile determinism
    unique_tokens = sorted({t for toks in SOURCE_TABLE_TOKENS.values() for t in toks})
    results: dict[str, list[dict]] = {t: [] for t in unique_tokens}
    if not root.is_dir():
        return results
    for py in sorted(root.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            for tok in unique_tokens:
                if tok in line:
                    snippet = line.strip()[:160]
                    results[tok].append({
                        "file": _posix_rel(py),
                        "line": i,
                        "snippet": snippet,
                    })
    # Son emniyet: her token içindeki liste dosya+satıra göre sırala
    for tok in results:
        results[tok].sort(key=lambda r: (r["file"], r["line"]))
    return results


# ------------------------------------------------------------------------------
# Ana
# ------------------------------------------------------------------------------
def main() -> int:
    for p in (A3_ARTIFACT, A6_ARTIFACT, MAIN_FILE):
        if not p.is_file():
            print(f"[HATA] Girdi eksik: {p}", file=sys.stderr)
            return 2

    a3 = json.loads(A3_ARTIFACT.read_text(encoding="utf-8"))
    a6 = json.loads(A6_ARTIFACT.read_text(encoding="utf-8"))

    a3_endpoints: list[dict] = a3.get("endpoints", [])
    a6_modules: list[dict] = a6.get("modules", [])
    a6_module_by_name: dict[str, dict] = {m["module"]: m for m in a6_modules}

    # Invoice flow endpoint'lerini filtrele (path-prefix bazlı)
    invoice_endpoints: list[dict] = []
    for e in a3_endpoints:
        p = e["path"]
        # Sadece main.py'deki handler'ları tarayacağız (router handler'ları scope dışı)
        if not e["file"].endswith("app/main.py"):
            continue
        if any(p == pfx or p.startswith(pfx + "/") or p.startswith(pfx + "{") for pfx in INVOICE_FLOW_PATH_PREFIXES):
            invoice_endpoints.append(dict(e))
        elif p in {"/analyze-invoice", "/full-process", "/calculate-offer"}:
            invoice_endpoints.append(dict(e))

    # main.py AST bir kez
    main_src = MAIN_FILE.read_text(encoding="utf-8")
    main_tree = ast.parse(main_src, filename=str(MAIN_FILE))
    main_top_imports = _collect_imports_top_and_inline(main_tree, fn_node=None)

    flow_diagram: list[str] = []
    for e in invoice_endpoints:
        fn = _find_function(main_tree, e["function"])
        if fn is None:
            e["calls_detected"] = []
            e["imports_extractor"] = None
            e["imports_validator"] = None
            e["imports_calculator"] = None
            continue

        # Handler içindeki çağrı isimleri (inline import'lar dahil)
        all_calls = _collect_call_names_in_fn(fn)
        relevant_calls = sorted(all_calls & INVOICE_FLOW_CALL_NAMES)
        e["calls_detected"] = relevant_calls

        # Handler içinde lazy import edilen isimler
        handler_local_imports = _collect_imports_top_and_inline(main_tree, fn_node=fn)
        # Öncelikle handler-local; yoksa top-level import'a düş
        def _resolve(name: str) -> str | None:
            return handler_local_imports.get(name) or main_top_imports.get(name)

        # Rollere göre haritala
        e["imports_extractor"] = None
        e["imports_validator"] = None
        e["imports_calculator"] = None
        for n in relevant_calls:
            mod = _resolve(n)
            if mod is None:
                continue
            if n in {"extract_invoice_data", "extract_invoice", "extract_from_pdf",
                     "canonical_extract", "fast_extract"}:
                e["imports_extractor"] = mod
            elif n in {"validate_extraction", "validate", "apply_enforcement",
                       "shadow_validate_hook", "compare_validators"}:
                # legacy vs yeni ayrımı
                e["imports_validator"] = mod
            elif n == "calculate_offer":
                e["imports_calculator"] = mod

        # Flow diagram satırları
        flow_diagram.append(f"{e['method']} {e['path']}  [{e['function']}@{_posix_rel(MAIN_FILE)}:{e['line']}]")
        for role in ("imports_extractor", "imports_validator", "imports_calculator"):
            mod = e.get(role)
            if mod:
                flow_diagram.append(f"  → [{role[8:]}] {mod}")

    # Legacy validator analizi
    legacy_users = [
        {"method": e["method"], "path": e["path"], "function": e["function"]}
        for e in invoice_endpoints
        if (e.get("imports_validator") == "app.validator"
            or "validate_extraction" in (e.get("calls_detected") or []))
    ]
    legacy_reachable = a6_module_by_name.get("app.validator", {}).get("imported_by_main", False)
    legacy_validator_info = {
        "module": "app.validator",
        "entry": "validate_extraction",
        "used_by_handlers": legacy_users,
        "reachable_from_main": legacy_reachable,
    }

    # Yeni stack analizi — üretim yolu var mı?
    new_stack_entries: list[dict] = []
    for mod, fns in NEW_STACK_ENTRY_POINTS.items():
        status = a6_module_by_name.get(mod, {})
        new_stack_entries.append({
            "module": mod,
            "functions": fns,
            "status": status.get("status", "unknown"),
            "imported_by_main": status.get("imported_by_main", False),
            "imported_by_tests": status.get("imported_by_tests", []),
        })

    # invoice.validation paketinin herhangi bir alt modülü main'den erişiliyor mu?
    new_stack_reachable = any(
        m["module"].startswith("app.invoice.validation")
        and m.get("imported_by_main") is True
        for m in a6_modules
    )

    # Üretim-yolu çağıran (handler'lar içinde)
    reachable_callers = [
        {"method": e["method"], "path": e["path"], "function": e["function"]}
        for e in invoice_endpoints
        if (e.get("imports_validator") or "").startswith("app.invoice.validation")
    ]

    # Verdict
    if reachable_callers:
        verdict = "OK"
        verdict_reason = "New stack reached by at least one production handler"
    elif new_stack_reachable:
        verdict = "BROKEN"
        verdict_reason = ("Package imported somewhere from main closure but no handler calls "
                          "its entry points — partial wiring")
    else:
        verdict = "DEAD"
        verdict_reason = ("New validation stack is fully test-only; no production handler imports "
                          "or calls it")

    new_stack_info = {
        "package": "app.invoice.validation",
        "entry_points": new_stack_entries,
        "reachable_from_main": new_stack_reachable,
        "reachable_callers_in_production": reachable_callers,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
    }

    # Tablo referansları
    table_refs = _grep_table_references(APP_ROOT)

    # Handler -> tablo referansı map (dolaylı)
    # Yaklaşım: her handler'ın import zincirinde (extractor/validator/calculator + transitive)
    # bu tablo adlarını içeren dosyalar var mı?
    # Pratik: handler → imports_* modüllerinden başla, A6'nın modül listesinde transitive olmak
    # yerine basit: her tablo referansının dosyası hangi endpoint'lerin import closure'unda.
    # Burada dar tutup handler seviyesinde direct ref arıyoruz: handler body'sinde literal tablo
    # adı geçiyor mu?
    handler_table_refs: list[dict] = []
    for e in invoice_endpoints:
        fn = _find_function(main_tree, e["function"])
        if fn is None:
            continue
        fn_src_start = fn.lineno
        fn_src_end = getattr(fn, "end_lineno", None) or fn_src_start + 50
        lines = main_src.splitlines()[fn_src_start - 1:fn_src_end]
        blob = "\n".join(lines)
        refs = {tok for tok in (t for toks in SOURCE_TABLE_TOKENS.values() for t in toks) if tok in blob}
        if refs:
            handler_table_refs.append({
                "method": e["method"],
                "path": e["path"],
                "function": e["function"],
                "tables": sorted(refs),
            })

    artifact: dict[str, Any] = {
        "_meta": {
            "script": "05_invoice_flow_sources.py",
            "invoice_endpoint_count": len(invoice_endpoints),
        },
        "invoice_endpoints": invoice_endpoints,
        "legacy_validator": legacy_validator_info,
        "new_validation_stack": new_stack_info,
        "source_tables": {
            "definitions": SOURCE_TABLE_TOKENS,
            "references_per_token": table_refs,
            "referenced_from_handlers": handler_table_refs,
        },
        "flow_diagram": flow_diagram,
    }

    OUT_ARTIFACT.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Stdout özet
    print("=" * 78)
    print("A7 — Fatura kontrol akışı kaynak haritası")
    print("=" * 78)
    print(f"Invoice flow endpoint sayısı : {len(invoice_endpoints)}")
    print()

    print("Akış diyagramı (ilk 20 satır):")
    for line in flow_diagram[:20]:
        print(f"  {line}")
    if len(flow_diagram) > 20:
        print(f"  ... ({len(flow_diagram)-20} satır daha)")
    print()

    # Legacy validator
    print("Legacy validator (app.validator::validate_extraction)")
    print(f"  reachable_from_main : {legacy_validator_info['reachable_from_main']}")
    print(f"  handler kullanıcı sayısı : {len(legacy_users)}")
    if legacy_users[:5]:
        for h in legacy_users[:5]:
            print(f"    - {h['method']:<6s} {h['path']:<30s} [{h['function']}]")
    print()

    # Yeni stack
    print("Yeni validation stack (app.invoice.validation.*)")
    print(f"  reachable_from_main : {new_stack_reachable}")
    print(f"  üretim çağıranı     : {len(reachable_callers)}")
    print(f"  VERDICT             : {verdict}")
    print(f"  Sebep               : {verdict_reason}")
    print()
    print("  Giriş noktaları:")
    for ne in new_stack_entries:
        by_tests_n = len(ne.get("imported_by_tests", []))
        print(f"    {ne['module']:<40s} status={ne['status']:<24s} imported_by_main={ne['imported_by_main']} tests={by_tests_n}")
    print()

    # Tablo referansları özet
    print("PTF/YEKDEM tablo referansları:")
    for tok, refs in sorted(table_refs.items()):
        print(f"  {tok:<30s} {len(refs)} kez")
    print()

    if handler_table_refs:
        print("Handler body'lerinde direkt tablo adı geçen:")
        for h in handler_table_refs:
            tables = ", ".join(h["tables"])
            print(f"  {h['method']:<6s} {h['path']:<30s} [{h['function']}]  → {tables}")
        print()
    else:
        print("Handler body'lerinde direkt tablo adı geçen YOK")
        print("(Tablo erişimi handler→extractor/calculator zinciri üzerinden)")
        print()

    print(f"Artifact: {_posix_rel(OUT_ARTIFACT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
