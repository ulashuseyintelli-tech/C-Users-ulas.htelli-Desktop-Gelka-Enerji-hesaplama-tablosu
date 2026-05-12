"""
01_inventory_endpoints.py — FastAPI endpoint envanteri (A3)

Read-only (R16). Kanıt-temelli (R1). Deterministik, idempotent.

Amaç:
- backend/app/**/*.py altındaki tüm FastAPI route decorator'larını AST ile tara.
- Hem `@app.*` (FastAPI instance) hem `@<name>_router.*` / `@router.*` (APIRouter) 
  dekoratörlerini yakala.
- APIRouter'lar için `APIRouter(prefix=...)` değerini decorator path'e prepend et.
- `app.include_router(<router>, prefix=...)` çağrılarından ek prefix birleştir.
- Full path'i, dosyayı ve satır numarasını artifact'a yaz.

Kullanım:
    python .kiro/specs/codebase-audit-cleanup/scripts/01_inventory_endpoints.py

Çıktı:
    stdout          : insan-okunur özet rapor
    artifacts/phase1_endpoints.json

Şema (her kayıt):
    {
      "method": "GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|API_ROUTE|WEBSOCKET",
      "path":   "/api/pricing/analyze",   # FULL path (prefix dahil)
      "function": "analyze",
      "file":   "backend/app/pricing/router.py",   # workspace-relative POSIX
      "line":   443,
      "router": "pricing_router"          # "app" = FastAPI instance
    }
"""

from __future__ import annotations
import ast
import json
import sys
from pathlib import Path
from typing import Any

# Windows cp1254 konsolu bazı Unicode karakterleri basamıyor; UTF-8'e zorla.
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

# ------------------------------------------------------------------------------
# Yol keşfi (01_inventory_db.py ile aynı kalıp)
# ------------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
SPEC_DIR = SCRIPT_DIR.parent
WORKSPACE_ROOT = SPEC_DIR.parent.parent.parent  # .kiro/specs/<name>/scripts -> workspace
ARTIFACTS_DIR = SPEC_DIR / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

BACKEND_APP_DIR = WORKSPACE_ROOT / "backend" / "app"
ARTIFACT_PATH = ARTIFACTS_DIR / "phase1_endpoints.json"

# HTTP method attribute adları (FastAPI / APIRouter ortak)
HTTP_METHOD_ATTRS = {
    "get", "post", "put", "delete", "patch", "options", "head",
    "api_route", "websocket",
}

# Spot-check (A3 kabul kriterleri)
SPOT_CHECK_PATHS = [
    "/api/pricing/analyze",
    "/api/epias/prices/{period}",
    "/api/full-process",
]


# ------------------------------------------------------------------------------
# AST yardımcıları
# ------------------------------------------------------------------------------
def _posix_rel(path: Path) -> str:
    """Workspace'e göreli POSIX path (JSON'da OS-agnostik)."""
    try:
        rel = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        rel = path
    return rel.as_posix()


def _const_str(node: ast.AST) -> str | None:
    """ast.Constant düğümünden str değer çıkar (yoksa None)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _join_prefix(base: str, path: str) -> str:
    """FastAPI'nin prefix + path birleştirme davranışını taklit et.

    Kurallar:
    - base'in sondaki '/' temizlenir
    - path '/' ile başlamıyorsa prepend edilir
    - path == '/' özel durumu: FastAPI bunu olduğu gibi base'e ekler
      (base='/api/pricing', path='/' -> '/api/pricing/')
      Pratikte nadir; yine de korunur.
    """
    base = (base or "").rstrip("/")
    if not path:
        return base or "/"
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}" if base else path


# ------------------------------------------------------------------------------
# Dosya başına tarama
# ------------------------------------------------------------------------------
def _extract_decorator_info(
    dec: ast.AST,
) -> tuple[str, str, str] | None:
    """Decorator düğümünden (carrier_name, method, path) çıkar.

    Desteklenen şekiller:
        @app.get("/foo")                        -> ("app", "GET", "/foo")
        @pricing_router.post("/bar")            -> ("pricing_router", "POST", "/bar")
        @router.get("/baz", response_model=...) -> ("router", "GET", "/baz")
        @app.api_route("/qux", methods=["GET"]) -> ("app", "API_ROUTE", "/qux")
        @app.websocket("/ws")                   -> ("app", "WEBSOCKET", "/ws")

    Path sabit string değilse None döner (f-string, değişken vb. kapsam dışı).
    """
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not isinstance(func, ast.Attribute):
        return None
    attr = func.attr
    if attr not in HTTP_METHOD_ATTRS:
        return None
    carrier = func.value
    if not isinstance(carrier, ast.Name):
        return None
    # İlk positional argüman = path
    if not dec.args:
        return None
    path = _const_str(dec.args[0])
    if path is None:
        return None
    return carrier.id, attr.upper(), path


def _find_router_prefix_in_module(tree: ast.Module) -> dict[str, str]:
    """Modül düzeyindeki `<name> = APIRouter(prefix=...)` atamalarını topla.

    Sadece sabit string prefix yakalanır; prefix yoksa "" döner.
    """
    prefixes: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        call = node.value
        fname: str | None = None
        if isinstance(call.func, ast.Name):
            fname = call.func.id
        elif isinstance(call.func, ast.Attribute):
            fname = call.func.attr
        if fname != "APIRouter":
            continue
        prefix = ""
        for kw in call.keywords:
            if kw.arg == "prefix":
                val = _const_str(kw.value)
                if val is not None:
                    prefix = val
                break
        for target in node.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix
    return prefixes


def _find_include_router_prefixes(tree: ast.Module) -> dict[str, str]:
    """`app.include_router(<name>, prefix="/x")` çağrılarından ek prefix.

    Not: Aynı router birden fazla include edilirse ilk bulunan alınır.
    Prefix verilmemişse "" (yani APIRouter'ın kendi prefix'i geçerli).
    """
    extras: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "include_router":
            continue
        if not call.args:
            continue
        first = call.args[0]
        if not isinstance(first, ast.Name):
            continue
        router_name = first.id
        prefix = ""
        for kw in call.keywords:
            if kw.arg == "prefix":
                val = _const_str(kw.value)
                if val is not None:
                    prefix = val
                break
        extras.setdefault(router_name, prefix)
    return extras


def _enclosing_function_name(
    dec_node: ast.AST, file_tree: ast.Module
) -> str | None:
    """Bir decorator düğümünün ait olduğu function/async function adını bul."""
    # Gezilecek alanlar: modül body + class body
    stack: list[ast.AST] = [file_tree]
    while stack:
        parent = stack.pop()
        body = getattr(parent, "body", None)
        if not isinstance(body, list):
            continue
        for child in body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if dec_node in child.decorator_list:
                    return child.name
                stack.append(child)
            elif isinstance(child, ast.ClassDef):
                stack.append(child)
    return None


def scan_file(path: Path) -> tuple[list[dict], dict[str, str], dict[str, str]]:
    """Bir .py dosyasını tara.

    Döner:
        records:           endpoint kayıtları (path henüz APIRouter prefix'siz)
        router_prefixes:   bu modülde tanımlı APIRouter prefix'leri
        include_prefixes:  bu modülde yapılan include_router() prefix ek'leri
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], {}, {}
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return [], {}, {}

    router_prefixes = _find_router_prefix_in_module(tree)
    include_prefixes = _find_include_router_prefixes(tree)

    records: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            info = _extract_decorator_info(dec)
            if info is None:
                continue
            carrier, method, raw_path = info
            records.append(
                {
                    "carrier": carrier,
                    "method": method,
                    "raw_path": raw_path,
                    "function": node.name,
                    "file": _posix_rel(path),
                    "line": dec.lineno,
                }
            )
    return records, router_prefixes, include_prefixes


# ------------------------------------------------------------------------------
# Ana çalışma
# ------------------------------------------------------------------------------
def main() -> int:
    if not BACKEND_APP_DIR.is_dir():
        print(f"[HATA] backend/app bulunamadı: {BACKEND_APP_DIR}", file=sys.stderr)
        return 2

    py_files = sorted(BACKEND_APP_DIR.rglob("*.py"))
    # __pycache__ zaten glob'a takılmaz ama emin olalım
    py_files = [p for p in py_files if "__pycache__" not in p.parts]

    all_raw: list[dict] = []
    all_router_prefixes: dict[str, str] = {}     # router_name -> prefix (APIRouter(prefix=...))
    all_include_prefixes: dict[str, str] = {}    # router_name -> extra prefix (include_router)

    for f in py_files:
        recs, rp, ip = scan_file(f)
        all_raw.extend(recs)
        # Aynı isimde birden fazla tanım: ilk bulunan kazanır (deterministik: dosya sırası)
        for k, v in rp.items():
            all_router_prefixes.setdefault(k, v)
        for k, v in ip.items():
            all_include_prefixes.setdefault(k, v)

    # Full path çözümleme
    endpoints: list[dict] = []
    for rec in all_raw:
        carrier = rec["carrier"]
        raw_path = rec["raw_path"]
        if carrier == "app":
            full_path = raw_path if raw_path.startswith("/") else "/" + raw_path
            router_label = "app"
        else:
            router_prefix = all_router_prefixes.get(carrier, "")
            include_prefix = all_include_prefixes.get(carrier, "")
            # Birleşim sırası: include_prefix + router_prefix + raw_path
            combined = _join_prefix(include_prefix, _join_prefix(router_prefix, raw_path).lstrip("/"))
            # _join_prefix ikinci argümanın '/' ile başlamasını zorlar; düzeltme için
            # şu basit zincirleme yeterli:
            p1 = _join_prefix(router_prefix, raw_path)
            full_path = _join_prefix(include_prefix, p1.lstrip("/")) if include_prefix else p1
            router_label = carrier
        endpoints.append(
            {
                "method": rec["method"],
                "path": full_path,
                "function": rec["function"],
                "file": rec["file"],
                "line": rec["line"],
                "router": router_label,
            }
        )

    # Deterministik sıralama: (file, line)
    endpoints.sort(key=lambda r: (r["file"], r["line"], r["method"], r["path"]))

    # Spot check
    present_paths = {e["path"] for e in endpoints}
    spot_check = {
        p: (p in present_paths) for p in SPOT_CHECK_PATHS
    }

    # Router özeti
    router_summary: dict[str, int] = {}
    for e in endpoints:
        router_summary[e["router"]] = router_summary.get(e["router"], 0) + 1

    # Artifact yaz — deterministik: timestamp yok (idempotent DoD).
    artifact: dict[str, Any] = {
        "_meta": {
            "script": "01_inventory_endpoints.py",
            "scanned_root": _posix_rel(BACKEND_APP_DIR),
            "file_count": len(py_files),
            "endpoint_count": len(endpoints),
            "router_prefixes": all_router_prefixes,
            "include_router_prefixes": all_include_prefixes,
            "spot_check": spot_check,
            "router_endpoint_counts": router_summary,
        },
        "endpoints": endpoints,
    }
    ARTIFACT_PATH.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Stdout özeti
    print("=" * 78)
    print("A3 — FastAPI endpoint envanteri")
    print("=" * 78)
    print(f"Taranan dosya sayısı : {len(py_files)}")
    print(f"Bulunan endpoint      : {len(endpoints)}")
    print(f"Router prefix tablosu : {all_router_prefixes or '{}'}")
    if all_include_prefixes:
        print(f"include_router prefix : {all_include_prefixes}")
    print()
    print("Router başına sayı:")
    for r, c in sorted(router_summary.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {r:<24s} {c}")
    print()
    print("Spot check (A3 DoD):")
    for p, ok in spot_check.items():
        mark = "✓" if ok else "✗"
        print(f"  [{mark}] {p}")
    missing = [p for p, ok in spot_check.items() if not ok]
    if missing:
        print()
        print("UYARI — beklenen spot-check path'leri bulunamadı:")
        for p in missing:
            print(f"   - {p}")
        print("  Not: Bu bir bulgudur (FE çağrısı ile backend tanımı uyuşmuyor olabilir).")
        print("  A5 endpoint↔FE matching task'ı bu durumu değerlendirecek.")
    print()
    print(f"Artifact: {_posix_rel(ARTIFACT_PATH)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
