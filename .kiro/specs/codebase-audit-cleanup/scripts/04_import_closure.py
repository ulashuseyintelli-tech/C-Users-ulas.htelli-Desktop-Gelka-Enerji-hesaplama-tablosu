"""
04_import_closure.py — Import kapanışı + route erişilebilirlik (A6)

Read-only (R16). Kanıt-temelli (R1). Deterministik, idempotent.

Amaç:
- backend/app/main.py'den BFS ile transitive import kapanışı çıkar.
- backend/tests/**/*.py'den ayrı BFS ile test-only set çıkar.
- Her modül için sınıfla: alive_from_main | alive_from_tests_only | orphan | dormant.
- A3 router tanımları + main.py include_router çağrıları ile her endpoint'e
  `reachable` flag ata (router DEAD ise endpoint DEAD).
- Guard config'te default=False olan *_enabled flag'lerini tespit et ve ilgili
  modülleri dormant olarak işaretle (flag mevcut davranış; audit bunu raporlar).

Girdi:
    backend/app/**/*.py
    backend/tests/**/*.py (transitive closure için)
    artifacts/phase1_endpoints.json (A3)

Çıktı:
    stdout: özet
    artifacts/phase1_imports.json

Şema:
{
  "_meta": {
    "script", "root_module", "total_modules",
    "counts": {"alive_from_main": N, "alive_from_tests_only": M, "orphan": K, "dormant": L},
    "router_reachability": {"pricing_router": true, "router": false}  # her router
  },
  "modules": [
    {
      "module": "app.pdf_api",
      "file": "backend/app/pdf_api.py",
      "status": "orphan | alive_from_main | alive_from_tests_only | dormant",
      "reason": "no import chain from main.py",
      "imported_by_main": false,
      "imported_by_tests": ["backend/tests/test_pdf_api.py"],
      "has_lazy_import": false
    },
    ...
  ],
  "endpoint_reachability": [
    {
      "method": "POST",
      "path": "/pdf/jobs",
      "router": "router",
      "file": "backend/app/pdf_api.py",
      "line": 122,
      "reachable": false,
      "reason": "router 'router' defined but never included"
    }
  ],
  "orphan_routers": [
    {"router_name": "router", "prefix": "/pdf", "defined_in": "backend/app/pdf_api.py", "endpoint_count": 3}
  ],
  "dormant_flags": [
    {"flag": "adaptive_control_enabled", "default": false, "defined_in": "backend/app/guard_config.py:107"}
  ]
}
"""

from __future__ import annotations
import ast
import json
import re
import sys
from pathlib import Path
from collections import deque
from typing import Any

# UTF-8 stdout (cp1254 fix)
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
TESTS_ROOT = BACKEND_ROOT / "tests"
MAIN_FILE = APP_ROOT / "main.py"

A3_ARTIFACT = ARTIFACTS_DIR / "phase1_endpoints.json"
OUT_ARTIFACT = ARTIFACTS_DIR / "phase1_imports.json"


def _posix_rel(path: Path) -> str:
    try:
        rel = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        rel = path
    return rel.as_posix()


# ------------------------------------------------------------------------------
# Modül adı ↔ dosya yolu
# ------------------------------------------------------------------------------
def module_of_file(path: Path) -> str | None:
    """backend/app/pricing/router.py -> 'app.pricing.router'
    backend/app/__init__.py         -> 'app'
    backend/tests/test_x.py         -> 'tests.test_x'
    """
    try:
        rel = path.relative_to(BACKEND_ROOT)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def file_of_module(module: str) -> Path | None:
    """'app.pricing.router' -> .../backend/app/pricing/router.py (varsa).

    __init__.py fallback'i de dener.
    """
    parts = module.split(".")
    # Dosya doğrudan
    p1 = BACKEND_ROOT.joinpath(*parts).with_suffix(".py")
    if p1.is_file():
        return p1
    # Paket (dizin + __init__.py)
    p2 = BACKEND_ROOT.joinpath(*parts, "__init__.py")
    if p2.is_file():
        return p2
    return None


# ------------------------------------------------------------------------------
# AST: bir dosyadaki import referanslarını çıkar
# ------------------------------------------------------------------------------
def resolve_relative(current_module: str, is_package: bool, level: int, mod: str | None) -> str | None:
    """Python relative-import semantiğini uygula.

    current_module: dosyanın modül adı ('app.main' veya 'app.guards')
    is_package:     dosya __init__.py mi (paket) yoksa .py modülü mü?
    level:          from .X (1), from ..X (2), ...
    mod:            'X' (varsa) ya da None (from . import Y durumunda)

    Döner: tam mutlak modül adı ('app.X', 'app.guards.X', ...).
    Modülden (file.py): current package = current_module'ün parent'ı.
    Paketten (__init__.py): current package = current_module kendisi.
    """
    if level <= 0:
        return mod
    parts = current_module.split(".")
    # current paket yolu
    pkg_parts = parts if is_package else parts[:-1]
    # level=1 → aynı paket; level=2 → parent; ...
    trim = level - 1
    if trim > 0:
        if trim >= len(pkg_parts):
            return None  # workspace dışı
        base = pkg_parts[:-trim]
    else:
        base = pkg_parts
    if not base and not mod:
        return None
    if mod:
        return ".".join(base + [mod])
    return ".".join(base)


def extract_imports_from_ast(
    tree: ast.AST, current_module: str, is_package: bool
) -> tuple[set[str], bool]:
    """Returns (imported_modules, has_lazy_import).

    Lazy: import ifadesi bir Function/Method/Condition body'sinin içindeysa.
    Tüm import'lar 'backend'-altı ise yakalanır; third-party atlanır.
    """
    results: set[str] = set()
    has_lazy = False

    # Yardımcı: top-level mi kontrolü (modül gövdesinde mi)
    def _walk_with_scope(node: ast.AST, is_top: bool) -> None:
        nonlocal has_lazy
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # içerideki import'lar lazy
                _walk_with_scope(child, is_top=False)
            elif isinstance(child, ast.ClassDef):
                _walk_with_scope(child, is_top=is_top)
            elif isinstance(child, (ast.If, ast.Try, ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
                # koşullu/try içindekiler lazy sayılır (conservative)
                _walk_with_scope(child, is_top=False)
            elif isinstance(child, ast.Import):
                for alias in child.names:
                    name = alias.name
                    if name.startswith("app") or name.startswith("backend.") or name == "app":
                        results.add(_normalize_mod(name))
                if not is_top:
                    has_lazy = True
            elif isinstance(child, ast.ImportFrom):
                level = child.level or 0
                base = child.module or ""
                if level > 0:
                    resolved = resolve_relative(current_module, is_package, level, base)
                else:
                    resolved = base if base else None
                if not resolved:
                    # from . import x, y, z
                    for alias in child.names:
                        sub = resolve_relative(current_module, is_package, level, alias.name)
                        if sub:
                            results.add(_normalize_mod(sub))
                    if not is_top:
                        has_lazy = True
                    continue
                # 'from X import a, b' — X modülü ve muhtemelen X.a, X.b (paket+submodule)
                if resolved.startswith("app") or resolved.startswith("backend."):
                    results.add(_normalize_mod(resolved))
                    # Attribute veya submodül: her isim için olası submodül dene
                    for alias in child.names:
                        sub = f"{resolved}.{alias.name}"
                        if file_of_module(_normalize_mod(sub)) is not None:
                            results.add(_normalize_mod(sub))
                if not is_top:
                    has_lazy = True
            else:
                _walk_with_scope(child, is_top=is_top)

    _walk_with_scope(tree, is_top=True)
    return results, has_lazy


def _normalize_mod(name: str) -> str:
    """backend.app.foo → app.foo (bizim dizin kökü backend/)."""
    if name.startswith("backend."):
        return name[len("backend."):]
    return name


def parse_file_imports(path: Path) -> tuple[set[str], bool]:
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set(), False
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return set(), False
    mod = module_of_file(path) or ""
    is_package = path.name == "__init__.py"
    return extract_imports_from_ast(tree, mod, is_package)


# ------------------------------------------------------------------------------
# BFS closure
# ------------------------------------------------------------------------------
def bfs_closure(entry_files: list[Path]) -> tuple[set[str], dict[str, bool]]:
    """Giriş dosya(lar)ından başla, backend.app.* modüllerinin kapanışını döndür.

    Dönen:
        reached_modules: {modül adı}
        lazy_map: {modül adı: has_lazy_import?}  # sadece ulaşılan modüller için
    """
    visited: set[str] = set()
    lazy_map: dict[str, bool] = {}
    queue: deque[str] = deque()

    for f in entry_files:
        m = module_of_file(f)
        if m:
            visited.add(m)
            queue.append(m)

    while queue:
        mod = queue.popleft()
        f = file_of_module(mod)
        if f is None:
            continue
        imports, has_lazy = parse_file_imports(f)
        lazy_map[mod] = has_lazy
        for dep in imports:
            if dep in visited:
                continue
            if file_of_module(dep) is None:
                # backend.app dışındaki veya kayıp modüller
                continue
            visited.add(dep)
            queue.append(dep)

    return visited, lazy_map


# ------------------------------------------------------------------------------
# Dormant flag tespiti (guard_config.py default=False olan *_enabled flag'leri)
# ------------------------------------------------------------------------------
DORMANT_FLAG_FILE = APP_ROOT / "guard_config.py"

# Bilinen flag → modül etkisi haritası (elle kuruluyor; guard_config yorumlarına
# dayalı). Kod değiştirmez; sadece raporda "bu flag OFF, ilgili modüller dormant".
# Kaynak: guard_config.py yorumları ve design.md "orphan vs dormant" kuralı.
FLAG_MODULE_MAP = {
    "adaptive_control_enabled":   ["app.adaptive_control"],
    "decision_layer_enabled":     ["app.guards.guard_decision", "app.guards.guard_decision_middleware"],
    "drift_guard_enabled":        ["app.guards.drift_guard"],
}


def detect_dormant_flags() -> list[dict]:
    """guard_config.py içinde default=False olan *_enabled flag'lerini listele."""
    if not DORMANT_FLAG_FILE.is_file():
        return []
    try:
        src = DORMANT_FLAG_FILE.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return []

    flags: list[dict] = []
    for node in ast.walk(tree):
        # AnnAssign: `name: bool = False`
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id.endswith("_enabled"):
                if isinstance(node.value, ast.Constant) and node.value.value is False:
                    flags.append({
                        "flag": target.id,
                        "default": False,
                        "defined_in": f"{_posix_rel(DORMANT_FLAG_FILE)}:{node.lineno}",
                        "affected_modules": FLAG_MODULE_MAP.get(target.id, []),
                    })
        # Assign: `name = False`
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.endswith("_enabled"):
                    if isinstance(node.value, ast.Constant) and node.value.value is False:
                        flags.append({
                            "flag": target.id,
                            "default": False,
                            "defined_in": f"{_posix_rel(DORMANT_FLAG_FILE)}:{node.lineno}",
                            "affected_modules": FLAG_MODULE_MAP.get(target.id, []),
                        })
    # Dedupe (AnnAssign + Assign aynı flag'i iki kez yakalayabilir)
    seen_keys: set[str] = set()
    unique_flags: list[dict] = []
    for f in flags:
        if f["flag"] in seen_keys:
            continue
        seen_keys.add(f["flag"])
        unique_flags.append(f)
    unique_flags.sort(key=lambda x: x["flag"])
    return unique_flags


# ------------------------------------------------------------------------------
# Ana
# ------------------------------------------------------------------------------
def main() -> int:
    if not MAIN_FILE.is_file():
        print(f"[HATA] main.py yok: {MAIN_FILE}", file=sys.stderr)
        return 2
    if not A3_ARTIFACT.is_file():
        print(f"[HATA] A3 artifact eksik: {A3_ARTIFACT}", file=sys.stderr)
        return 2

    # Tüm app modüllerini listele
    all_app_files = sorted(
        [p for p in APP_ROOT.rglob("*.py") if "__pycache__" not in p.parts]
    )
    all_app_modules = [m for m in (module_of_file(p) for p in all_app_files) if m]
    all_app_modules_set = set(all_app_modules)

    # BFS1: main.py'den
    main_reached, main_lazy = bfs_closure([MAIN_FILE])

    # BFS2: test dosyalarından (test dosyalarını test modüllerinden değil,
    # onların backend.app altına inen bağlantılarından hesapla)
    test_entries: list[Path] = []
    if TESTS_ROOT.is_dir():
        test_entries = [p for p in TESTS_ROOT.rglob("*.py") if "__pycache__" not in p.parts]
    tests_reached, _ = bfs_closure(test_entries)

    # Dormant flag tespiti
    dormant_flags = detect_dormant_flags()
    dormant_module_set: set[str] = set()
    for f in dormant_flags:
        dormant_module_set.update(f["affected_modules"])

    # Sınıflandır
    modules_report: list[dict] = []
    counts = {"alive_from_main": 0, "alive_from_tests_only": 0, "orphan": 0, "dormant": 0}

    # tests'te hangi dosyalar hangi app modülünü import ediyor — ters index
    tests_import_of_module: dict[str, list[str]] = {}
    for p in test_entries:
        imports, _ = parse_file_imports(p)
        for m in imports:
            # Sadece app.* kısmı bizi ilgilendiriyor
            if m.startswith("app") and m in all_app_modules_set:
                tests_import_of_module.setdefault(m, []).append(_posix_rel(p))

    for m in sorted(all_app_modules_set):
        f = file_of_module(m)
        file_rel = _posix_rel(f) if f else None
        in_main = m in main_reached
        in_tests = m in tests_reached

        # Dormant: bir flag bu modülü veya bir üst paket'i dormant yapıyorsa
        is_dormant = any(
            m == dm or m.startswith(dm + ".") for dm in dormant_module_set
        )

        if is_dormant and not in_main:
            # Dormant VE main'den erişilemiyorsa yine de dormant etiketi kullan
            status = "dormant"
            reason = "flag-driven dormant (feature gate OFF)"
            counts["dormant"] += 1
        elif is_dormant and in_main:
            # Main'den erişiliyor ama flag OFF → dormant ama alive
            status = "dormant"
            reason = "alive via main but feature flag OFF"
            counts["dormant"] += 1
        elif in_main:
            status = "alive_from_main"
            reason = "reachable from main.py"
            counts["alive_from_main"] += 1
        elif in_tests:
            status = "alive_from_tests_only"
            reason = "imported only by tests"
            counts["alive_from_tests_only"] += 1
        else:
            status = "orphan"
            reason = "no import chain from main or tests"
            counts["orphan"] += 1

        modules_report.append({
            "module": m,
            "file": file_rel,
            "status": status,
            "reason": reason,
            "imported_by_main": in_main,
            "imported_by_tests": sorted(tests_import_of_module.get(m, [])),
            "has_lazy_import": main_lazy.get(m, False),
        })

    # Endpoint reachability (A3 → router isimleri)
    a3 = json.loads(A3_ARTIFACT.read_text(encoding="utf-8"))
    a3_meta = a3.get("_meta", {})
    a3_endpoints = a3.get("endpoints", [])
    router_prefixes: dict[str, str] = a3_meta.get("router_prefixes", {})
    include_prefixes: dict[str, str] = a3_meta.get("include_router_prefixes", {})

    # Router erişilebilirlik:
    # - "app" ismiyle tanımlı endpoint'ler her zaman reachable (FastAPI instance).
    # - Diğer router'lar için "include_router_prefixes" anahtarları = canlı.
    router_reachability: dict[str, bool] = {"app": True}
    for rname in router_prefixes.keys():
        router_reachability[rname] = rname in include_prefixes

    endpoint_reach: list[dict] = []
    for e in a3_endpoints:
        rname = e.get("router", "app")
        reachable = router_reachability.get(rname, False)
        reason = (
            "app-level endpoint" if rname == "app"
            else (f"router '{rname}' included via app.include_router"
                  if reachable
                  else f"router '{rname}' defined but never included")
        )
        endpoint_reach.append({
            "method": e["method"],
            "path": e["path"],
            "router": rname,
            "file": e["file"],
            "line": e["line"],
            "function": e["function"],
            "reachable": reachable,
            "reason": reason,
        })
    endpoint_reach.sort(key=lambda r: (r["reachable"], r["path"], r["method"]))

    # Orphan router listesi
    orphan_routers: list[dict] = []
    # Her router için endpoint sayısını bul
    router_endpoint_count: dict[str, int] = {}
    router_defined_in: dict[str, str] = {}
    for e in a3_endpoints:
        rname = e.get("router", "app")
        router_endpoint_count[rname] = router_endpoint_count.get(rname, 0) + 1
        router_defined_in.setdefault(rname, e["file"])
    for rname, included in router_reachability.items():
        if not included and rname != "app":
            orphan_routers.append({
                "router_name": rname,
                "prefix": router_prefixes.get(rname, ""),
                "defined_in": router_defined_in.get(rname, "?"),
                "endpoint_count": router_endpoint_count.get(rname, 0),
            })
    orphan_routers.sort(key=lambda r: r["router_name"])

    artifact: dict[str, Any] = {
        "_meta": {
            "script": "04_import_closure.py",
            "root_module": "app.main",
            "total_modules": len(all_app_modules_set),
            "counts": counts,
            "router_reachability": router_reachability,
            "endpoint_total": len(a3_endpoints),
            "endpoint_reachable": sum(1 for e in endpoint_reach if e["reachable"]),
            "endpoint_unreachable": sum(1 for e in endpoint_reach if not e["reachable"]),
        },
        "modules": modules_report,
        "endpoint_reachability": endpoint_reach,
        "orphan_routers": orphan_routers,
        "dormant_flags": dormant_flags,
    }

    OUT_ARTIFACT.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Stdout özeti
    print("=" * 78)
    print("A6 — Import kapanışı + route erişilebilirlik")
    print("=" * 78)
    print(f"Toplam app modülü           : {len(all_app_modules_set)}")
    for k, v in counts.items():
        print(f"  {k:<24s} {v}")
    print()
    print(f"Router erişilebilirlik:")
    for r, ok in sorted(router_reachability.items()):
        mark = "✓" if ok else "✗"
        prefix = router_prefixes.get(r, "")
        print(f"  [{mark}] {r:<20s} prefix={prefix!r}")
    print()
    print(f"Endpoint reachability       : "
          f"{artifact['_meta']['endpoint_reachable']}/{artifact['_meta']['endpoint_total']} reachable, "
          f"{artifact['_meta']['endpoint_unreachable']} unreachable")
    print()
    if orphan_routers:
        print(f"Orphan router sayısı         : {len(orphan_routers)}")
        for o in orphan_routers:
            print(f"  '{o['router_name']}' ({o['prefix']}) — {o['endpoint_count']} endpoint, "
                  f"defined in {o['defined_in']}")
        print()
    if dormant_flags:
        print(f"Dormant flag sayısı          : {len(dormant_flags)}")
        for f in dormant_flags:
            mods = ", ".join(f["affected_modules"]) or "(no mapped modules)"
            print(f"  {f['flag']:<32s} → {mods}")
        print()

    # Orphan modül örnekleri
    orphans = [m for m in modules_report if m["status"] == "orphan"]
    if orphans:
        print(f"Orphan modüller (örnek 10):")
        for m in orphans[:10]:
            print(f"  {m['module']:<40s} {m['file']}")
        print()
    tests_only = [m for m in modules_report if m["status"] == "alive_from_tests_only"]
    if tests_only:
        print(f"alive_from_tests_only (örnek 10):")
        for m in tests_only[:10]:
            imp_by = m["imported_by_tests"]
            first = imp_by[0] if imp_by else "?"
            print(f"  {m['module']:<40s} (used by {len(imp_by)}, first={first})")
        print()

    print(f"Artifact: {_posix_rel(OUT_ARTIFACT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
