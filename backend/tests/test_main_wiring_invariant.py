"""
test_main_wiring_invariant.py — 3 kurallı altın kural (B10, R18)

Bu test dosyası sistemde tek bir ilkeyi zorlar:
    "main.py'ye bağlı değilse yoktur."

Üç kural:

    1) MAIN WIRING — backend/app/ altındaki her .py modülü ya main.py'den
       erişilebilir (BFS import closure) ya backend/tests/'den test ediliyor;
       ikisi de yoksa FAIL.

    2) SINGLE SoT — source-of-truth.md steering'inde kilitli canonical domainler
       için yalnızca bir yazıcı olmalı:
         - PTF    canonical = hourly_market_prices
         - YEKDEM canonical = monthly_yekdem_prices
         - validation canonical = app.validator.validate_extraction (legacy)
       Yeni kod yazımında deprecated tablo/modül SELECT/INSERT eklenirse FAIL.

    3) ORPHAN ENDPOINT — her FastAPI @app / @router decorator'lı endpoint için
       router'ı app.include_router() çağrısı ile kaydedilmiş olmalı. Tanımlı ama
       include edilmemiş router = FAIL.

Bilinen fail listesi (hard_delete_candidates.md ve wiring_gaps.md'de kanıtlı):
    - 9 orphan modül (kategori A)
    - pdf_api.router (kategori B, 3 endpoint)
    - "dormant" modüller flag OFF olduğu için alive_from_main sayılır, muaf.

Bu fail listesi @pytest.mark.xfail ile işaretlenir. Yeni orphan/unregistered router
eklenirse test FAIL vermeli. Liste küçüldükçe (cleanup spec'leri ilerledikçe)
xfail kayıtları elle güncellenir; tamamen temizlenince xfail blokları silinir.

Çalıştırma:
    pytest backend/tests/test_main_wiring_invariant.py -v
"""

from __future__ import annotations
import ast
import json
import re
import sys
from pathlib import Path
from typing import Iterable

import pytest

# ------------------------------------------------------------------------------
# Yol keşfi
# ------------------------------------------------------------------------------
THIS_FILE = Path(__file__).resolve()
BACKEND_ROOT = THIS_FILE.parent.parent
WORKSPACE_ROOT = BACKEND_ROOT.parent
APP_ROOT = BACKEND_ROOT / "app"
TESTS_ROOT = BACKEND_ROOT / "tests"
MAIN_FILE = APP_ROOT / "main.py"

ARTIFACTS_DIR = (
    WORKSPACE_ROOT / ".kiro" / "specs" / "codebase-audit-cleanup" / "artifacts"
)

# ------------------------------------------------------------------------------
# Bilinen xfail listeleri — cleanup tamamlanana kadar muaf
# ------------------------------------------------------------------------------
# Kanıt: hard_delete_candidates.md §1 (kategori A) + artifacts/phase1_imports.json
KNOWN_ORPHAN_MODULES_XFAIL: frozenset[str] = frozenset({
    "app.canonical_extractor",
    "app.fast_extractor",
    "app.pricing.excel_formatter",
    "app.rq_worker",
    "app.seed_market_prices",
    "app.services.job_claim",
    "app.services.webhook_manager",
    "app.worker",
    "app.worker_pg",
    # __init__.py paketleri — alt modüller ayrı import'larla canlı; paket
    # kendisi re-export yapmıyor. Cleanup scope'u dışı, xfail olarak muaf:
    "app.core",
    "app.guards",
    "app.invoice",
    "app.pricing",
    "app.services",
    "app.testing",
    "app",  # app/__init__.py kendisi
})

# Kanıt: artifacts/phase1_imports.json::orphan_routers + wiring_gaps.md §2
KNOWN_ORPHAN_ROUTERS_XFAIL: frozenset[str] = frozenset({
    # pdf_api.router — include_router çağrısı yok, 3 endpoint ulaşılamaz
    "backend/app/pdf_api.py::router",
})

# ------------------------------------------------------------------------------
# AST yardımcıları (scripts/04_import_closure.py'den uyarlanmış — bağımlılıksız)
# ------------------------------------------------------------------------------


def _module_of_file(path: Path) -> str | None:
    """backend/app/pricing/router.py -> 'app.pricing.router'"""
    try:
        rel = path.relative_to(BACKEND_ROOT)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def _file_of_module(module: str) -> Path | None:
    parts = module.split(".")
    p1 = BACKEND_ROOT.joinpath(*parts).with_suffix(".py")
    if p1.is_file():
        return p1
    p2 = BACKEND_ROOT.joinpath(*parts, "__init__.py")
    if p2.is_file():
        return p2
    return None


def _resolve_relative(
    current_module: str, is_package: bool, level: int, mod: str | None
) -> str | None:
    if level <= 0:
        return mod
    parts = current_module.split(".")
    pkg_parts = parts if is_package else parts[:-1]
    trim = level - 1
    if trim > 0:
        if trim >= len(pkg_parts):
            return None
        base = pkg_parts[:-trim]
    else:
        base = pkg_parts
    if not base and not mod:
        return None
    return ".".join(base + [mod]) if mod else ".".join(base)


def _normalize_mod(name: str) -> str:
    return name[len("backend."):] if name.startswith("backend.") else name


def _extract_imports(tree: ast.AST, current_module: str, is_package: bool) -> set[str]:
    out: set[str] = set()

    def _walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name.startswith("app") or alias.name.startswith("backend."):
                        out.add(_normalize_mod(alias.name))
            elif isinstance(child, ast.ImportFrom):
                level = child.level or 0
                base = child.module or ""
                if level > 0:
                    resolved = _resolve_relative(current_module, is_package, level, base)
                else:
                    resolved = base if base else None
                if not resolved:
                    for alias in child.names:
                        sub = _resolve_relative(
                            current_module, is_package, level, alias.name
                        )
                        if sub:
                            out.add(_normalize_mod(sub))
                    continue
                if resolved.startswith("app") or resolved.startswith("backend."):
                    out.add(_normalize_mod(resolved))
                    for alias in child.names:
                        sub = f"{resolved}.{alias.name}"
                        if _file_of_module(_normalize_mod(sub)) is not None:
                            out.add(_normalize_mod(sub))
            else:
                _walk(child)

    _walk(tree)
    return out


def _bfs_closure(entry_files: Iterable[Path]) -> set[str]:
    from collections import deque

    visited: set[str] = set()
    queue: deque[str] = deque()
    for f in entry_files:
        m = _module_of_file(f)
        if m:
            visited.add(m)
            queue.append(m)

    while queue:
        mod = queue.popleft()
        f = _file_of_module(mod)
        if f is None:
            continue
        try:
            src = f.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        is_pkg = f.name == "__init__.py"
        for dep in _extract_imports(tree, mod, is_pkg):
            if dep in visited:
                continue
            if _file_of_module(dep) is None:
                continue
            visited.add(dep)
            queue.append(dep)
    return visited


# ------------------------------------------------------------------------------
# Router tespiti (scripts/01_inventory_endpoints.py mantığı, test-lokal)
# ------------------------------------------------------------------------------


def _find_router_defs(py_files: list[Path]) -> dict[str, dict]:
    """Modül düzeyinde `X = APIRouter(...)` tanımlarını bul.

    Döner: {"<file>::<var_name>": {"file", "line", "prefix", "module"}}
    """
    results: dict[str, dict] = {}
    for f in py_files:
        try:
            src = f.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        file_rel = str(f.relative_to(WORKSPACE_ROOT)).replace("\\", "/")
        mod = _module_of_file(f) or ""
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            fname = None
            if isinstance(call.func, ast.Name):
                fname = call.func.id
            elif isinstance(call.func, ast.Attribute):
                fname = call.func.attr
            if fname != "APIRouter":
                continue
            prefix = ""
            for kw in call.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        prefix = kw.value.value
                    break
            for t in node.targets:
                if isinstance(t, ast.Name):
                    key = f"{file_rel}::{t.id}"
                    results[key] = {
                        "file": file_rel,
                        "line": node.lineno,
                        "prefix": prefix,
                        "module": mod,
                        "var_name": t.id,
                    }
    return results


def _find_included_routers(main_py: Path) -> set[str]:
    """main.py'de `app.include_router(<name>)` çağrılarını bul.

    Basit: <name> yerel ad; import kaynağı ayrı — bu test için ad eşleşmesi
    yeterli (aynı repo içindeyiz).
    """
    try:
        tree = ast.parse(main_py.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr != "include_router":
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Name):
            out.add(arg.id)
        elif isinstance(arg, ast.Attribute):
            out.add(arg.attr)
    return out


# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def all_app_files() -> list[Path]:
    return sorted(
        p for p in APP_ROOT.rglob("*.py") if "__pycache__" not in p.parts
    )


@pytest.fixture(scope="module")
def all_app_modules(all_app_files: list[Path]) -> set[str]:
    return {m for m in (_module_of_file(p) for p in all_app_files) if m}


@pytest.fixture(scope="module")
def main_closure() -> set[str]:
    return _bfs_closure([MAIN_FILE])


@pytest.fixture(scope="module")
def tests_closure() -> set[str]:
    if not TESTS_ROOT.is_dir():
        return set()
    entries = [p for p in TESTS_ROOT.rglob("*.py") if "__pycache__" not in p.parts]
    return _bfs_closure(entries)


# ==============================================================================
# KURAL 1 — Main wiring
# ==============================================================================


def test_rule1_every_app_module_is_reachable(
    all_app_modules: set[str],
    main_closure: set[str],
    tests_closure: set[str],
) -> None:
    """Her app.* modülü ya main.py ya tests/ closure'unda olmalı.

    Bilinen orphan'lar KNOWN_ORPHAN_MODULES_XFAIL ile muaftır. Yeni orphan
    eklenirse test FAIL verir — bu bir governance guard'ıdır (R18).
    """
    unreachable = [
        m for m in sorted(all_app_modules)
        if m not in main_closure and m not in tests_closure
    ]

    # Known orphan'ları çıkar
    new_orphans = [m for m in unreachable if m not in KNOWN_ORPHAN_MODULES_XFAIL]

    if new_orphans:
        lines = [
            "Yeni orphan modül(ler) tespit edildi — main.py veya tests/ altından erişilmiyor:",
            "",
        ]
        for m in new_orphans:
            f = _file_of_module(m)
            lines.append(f"  - {m}  ({f.relative_to(WORKSPACE_ROOT) if f else '?'})")
        lines += [
            "",
            "Çözüm: modülü main.py'ye import et veya test yaz. Kabul edilemezse sil.",
            "Eğer planlı şekilde orphan olacaksa KNOWN_ORPHAN_MODULES_XFAIL'a ekle",
            "(ama bu, steering §5 yasak kalıplarına aykırı; gerekçeyle belgeleyin).",
        ]
        pytest.fail("\n".join(lines))


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Bilinen 9 fully-dead orphan + boş paket __init__.py'leri. "
        "Kanıt: artifacts/hard_delete_candidates.md §1, artifacts/phase1_imports.json. "
        "Cleanup spec'lerinde silinecek; liste küçüldükçe "
        "KNOWN_ORPHAN_MODULES_XFAIL güncellenir."
    ),
)
def test_rule1_known_orphans_still_present(
    all_app_modules: set[str],
    main_closure: set[str],
    tests_closure: set[str],
) -> None:
    """Bilinen orphan'lar hâlâ orphan. Temizlenince bu test PASS'e döner;
    o anda xfail kaldırılır. Erken silinen orphan'lar için de uyarı verir."""
    still_orphan = {
        m for m in KNOWN_ORPHAN_MODULES_XFAIL
        if m in all_app_modules and m not in main_closure and m not in tests_closure
    }
    if still_orphan != KNOWN_ORPHAN_MODULES_XFAIL & all_app_modules:
        pytest.fail(
            "KNOWN_ORPHAN_MODULES_XFAIL listesi güncel değil. "
            f"Hâlâ orphan: {sorted(still_orphan)}"
        )
    # Listedeki tüm orphan'lar hâlâ orphan ise xfail davranışı gereği "başarısız"
    pytest.fail("Bilinen orphan'lar hâlâ silinmedi — bu beklenen durum (xfail).")


# ==============================================================================
# KURAL 2 — Single SoT (steering §5 yasak kalıplar enforce)
# ==============================================================================


# Yasak kalıplar — source-of-truth.md §5'ten kopyalanmış; bu listede hızlı
# değişiklik varsa steering ve bu test birlikte güncellenmelidir.
FORBIDDEN_SQL_PATTERNS: list[tuple[str, str]] = [
    # (regex, açıklama)
    # PTF/YEKDEM için legacy tabloya yeni yazıcı yasak
    (
        r"INSERT\s+INTO\s+market_reference_prices",
        "market_reference_prices legacy; canonical=hourly_market_prices (PTF) "
        "veya monthly_yekdem_prices (YEKDEM). Yeni INSERT yasak.",
    ),
    (
        r"UPDATE\s+market_reference_prices",
        "market_reference_prices legacy; UPDATE yeni kodda yasak. "
        "Sadece migration script'leri için muaf olabilir.",
    ),
]

# Yeni kodda bu paketten import yasak (stack DEAD, production wiring öncesi):
FORBIDDEN_IMPORTS: list[tuple[str, str]] = [
    (
        r"from\s+app\.invoice\.validation\b",
        "app.invoice.validation.* stack DEAD (A7 verdict). Production wiring "
        "invoice-validation-prod-hardening spec'inde yapılır; öncesinde import yasak.",
    ),
    (
        r"from\s+\.invoice\.validation\b",
        "Aynı: relative import ile bağlama yasak.",
    ),
]

# Paths muaf — migration script'leri ve mevcut testler (existing code'a karşı
# legacy SELECT'lere dokunmuyoruz; yalnızca INSERT/UPDATE yasaklandı).
FORBIDDEN_SCAN_EXCLUDE_DIRS = {
    "__pycache__",
    ".hypothesis",
    ".venv",
    "node_modules",
    ".git",
    "alembic",           # migration script'leri için dahil — ama aşağıda beyaz liste
}


def _scan_backend_for_forbidden(
    patterns: list[tuple[str, str]],
    extensions: tuple[str, ...] = (".py",),
) -> list[dict]:
    """backend/ altını tara. Testler ve alembic migration'ları dahil ama ayrıştırılır.

    Döner: [{"file", "line", "pattern", "reason", "is_test", "is_migration"}]
    """
    hits: list[dict] = []
    for f in BACKEND_ROOT.rglob("*"):
        if not f.is_file() or f.suffix not in extensions:
            continue
        if any(part in FORBIDDEN_SCAN_EXCLUDE_DIRS for part in f.parts):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        file_rel = str(f.relative_to(WORKSPACE_ROOT)).replace("\\", "/")
        is_test = "tests/" in file_rel or "/tests/" in file_rel
        is_migration = "/alembic/" in file_rel or file_rel.startswith("backend/alembic/")
        for i, line in enumerate(text.splitlines(), start=1):
            # Yorum satırlarını atla (Python)
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for pat, reason in patterns:
                if re.search(pat, line):
                    hits.append({
                        "file": file_rel, "line": i,
                        "pattern": pat, "reason": reason,
                        "is_test": is_test, "is_migration": is_migration,
                        "line_text": line.strip()[:120],
                    })
    return hits


def test_rule2_no_new_legacy_ptf_writers() -> None:
    """market_reference_prices'a yeni INSERT/UPDATE yasak (production kodunda).

    Muaf: alembic migration'ları + testler (legacy davranışı fix eden test'ler
    eski tabloya yazabilir — migration tamamlanıncaya dek).
    """
    hits = _scan_backend_for_forbidden(FORBIDDEN_SQL_PATTERNS)
    # Production hits (test ve migration hariç)
    prod_hits = [h for h in hits if not h["is_test"] and not h["is_migration"]]

    if prod_hits:
        lines = ["Legacy tabloya yeni INSERT/UPDATE tespit edildi (üretim kodu):", ""]
        for h in prod_hits:
            lines.append(f"  - {h['file']}:{h['line']}")
            lines.append(f"      pattern: {h['pattern']}")
            lines.append(f"      reason : {h['reason']}")
            lines.append(f"      line   : {h['line_text']}")
            lines.append("")
        lines.append(
            "Çözüm: canonical kaynağa yaz (hourly_market_prices / "
            "monthly_yekdem_prices). Steering §5: source-of-truth.md."
        )
        pytest.fail("\n".join(lines))


def test_rule2_no_new_dead_validation_imports(main_closure: set[str]) -> None:
    """app.invoice.validation.* paketinden yeni import yasak (üretim kodunda).

    "Üretim kodu" = main.py import closure'ında olan modüller.
    Muaf:
      - tests/ (zaten test-only stack)
      - invoice.validation.* paketi kendi içindeki import'lar (iç tutarlılık)
      - orphan modüller (main closure'da değil; zaten kural 1 ile yakalanıyor)
    """
    hits = _scan_backend_for_forbidden(FORBIDDEN_IMPORTS)
    prod_hits = []
    for h in hits:
        if h["is_test"]:
            continue
        # Paket iç tutarlılığı
        if "app/invoice/validation/" in h["file"] or "app\\invoice\\validation\\" in h["file"]:
            continue
        # Yalnızca main closure'ındaki modüller "üretim" sayılır
        # file → module name dönüşümü
        rel = h["file"]
        if rel.startswith("backend/"):
            mod_parts = rel[len("backend/"):].removesuffix(".py").replace("/", ".")
            if mod_parts.endswith(".__init__"):
                mod_parts = mod_parts[:-len(".__init__")]
            if mod_parts not in main_closure:
                continue  # orphan/test-only; kural 2 scope'u dışı
        prod_hits.append(h)

    if prod_hits:
        lines = ["DEAD stack'ten import tespit edildi (üretim kodu):", ""]
        for h in prod_hits:
            lines.append(f"  - {h['file']}:{h['line']}")
            lines.append(f"      line   : {h['line_text']}")
            lines.append("")
        lines.append(
            "Çözüm: invoice-validation-prod-hardening spec'ini tamamla, "
            "sonra import et."
        )
        pytest.fail("\n".join(lines))


# ==============================================================================
# KURAL 3 — Orphan endpoint (router tanımlı ama include_router yok)
# ==============================================================================


def test_rule3_every_router_is_included(all_app_files: list[Path]) -> None:
    """Her APIRouter tanımının main.py'de include_router çağrısı olmalı.

    "app" (FastAPI instance) sayılmaz — o zaten kendi üzerine decorator alıyor.
    Bilinen orphan router'lar KNOWN_ORPHAN_ROUTERS_XFAIL ile muaftır.
    """
    router_defs = _find_router_defs(all_app_files)
    included_names = _find_included_routers(MAIN_FILE)

    orphan_routers = []
    for key, info in router_defs.items():
        var_name = info["var_name"]
        if var_name == "app":
            continue  # FastAPI instance, router değil
        if var_name in included_names:
            continue  # include_router çağrısı var
        orphan_routers.append((key, info))

    # xfail listesini çıkar
    new_orphans = [
        (k, v) for k, v in orphan_routers
        if k not in KNOWN_ORPHAN_ROUTERS_XFAIL
    ]

    if new_orphans:
        lines = ["Orphan router tespit edildi (APIRouter tanımlı ama include yok):", ""]
        for key, info in new_orphans:
            lines.append(f"  - {key}")
            lines.append(f"      prefix={info['prefix']!r}  "
                         f"file={info['file']}:{info['line']}")
        lines += [
            "",
            "Çözüm: main.py'ye `app.include_router(<name>)` ekle veya router'ı sil.",
            "Kanıt: bu router hiçbir endpoint çağrısına yanıt vermez (404 döner).",
        ]
        pytest.fail("\n".join(lines))


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Bilinen orphan router: pdf_api.router (3 endpoint /pdf/jobs). "
        "Karar pdf-render-worker spec'inde. Kanıt: wiring_gaps.md §2."
    ),
)
def test_rule3_known_orphan_routers_still_present(all_app_files: list[Path]) -> None:
    """Bilinen orphan router'ların hâlâ include edilmediğini doğrular.

    Bu test PASS'a döndüğünde (ya include edildi ya silindi) xfail kaldırılır.
    """
    router_defs = _find_router_defs(all_app_files)
    included_names = _find_included_routers(MAIN_FILE)
    still_orphan = {
        k for k, info in router_defs.items()
        if info["var_name"] != "app"
        and info["var_name"] not in included_names
    }
    known_present = KNOWN_ORPHAN_ROUTERS_XFAIL & still_orphan
    if known_present != KNOWN_ORPHAN_ROUTERS_XFAIL & set(router_defs.keys()):
        pytest.fail(
            "KNOWN_ORPHAN_ROUTERS_XFAIL listesi güncel değil. "
            f"Hâlâ orphan: {sorted(known_present)}"
        )
    pytest.fail("Bilinen orphan router hâlâ include edilmedi — beklenen (xfail).")
