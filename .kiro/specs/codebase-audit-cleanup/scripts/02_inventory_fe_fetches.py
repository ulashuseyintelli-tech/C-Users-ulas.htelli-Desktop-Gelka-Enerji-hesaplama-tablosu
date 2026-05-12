"""
02_inventory_fe_fetches.py — Frontend HTTP çağrı envanteri (A4)

Read-only (R16). Kanıt-temelli (R1). Deterministik, idempotent.

Amaç:
- frontend/src/**/*.{ts,tsx} altındaki TÜM HTTP çağrılarını listele.
- Axios instance çağrıları: api.get(...), api.post(...), adminApi.get(...) vb.
- Native fetch çağrıları: fetch('...', { method: 'POST', ... })
- Path'i normalize et: `${period}` → `{period}`, query string ayrıştır.
- Sınıflandır: teklif | risk | admin | invoice | telemetry | health | pricing | epias | other
- Duplikeleri (method + normalized_path) merge et, occurrences sayaç tut.

Kullanım:
    python .kiro/specs/codebase-audit-cleanup/scripts/02_inventory_fe_fetches.py

Çıktı:
    stdout: insan-okunur özet
    artifacts/phase1_fe_fetches.json

Şema:
{
  "_meta": {
    "script", "scanned_root", "file_count",
    "call_count_raw", "call_count_unique",
    "usage_distribution": {"pricing": N, ...},
    "dynamic_path_count": N,
    "spot_check": {"/api/pricing/analyze": true, ...}
  },
  "calls": [
    {
      "method": "POST",
      "path": "/api/epias/prices/{period}",
      "file": "frontend/src/App.tsx",
      "line": 1190,
      "params": {"query": [], "body": true},
      "usage": "epias",
      "client": "fetch|api|adminApi",
      "raw_path": "${API_BASE}/api/epias/prices/${period}",
      "interpolations": ["period"],
      "dynamic": true
    },
    ...
  ],
  "unique_calls": [  # method + path merge, occurrences sayısı
    {
      "method": "GET",
      "path": "/api/pricing/periods",
      "occurrences": 2,
      "files": [{"file": "...", "line": 930}, ...],
      "usage": "pricing"
    }
  ]
}

Not: TS/TSX için stdlib'te AST parser yok. Regex + context-line hibrit kullanıyoruz.
Aynı dosyadaki comment satırları ve test kodu (**/__tests__/**) opsiyonel dışlanabilir;
audit kapsamı gereği TEST DIŞI kaynaklar taranır.
"""

from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from typing import Any

# UTF-8 stdout (Windows cp1254 fix; 01_inventory_endpoints.py ile aynı kalıp)
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
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

FRONTEND_SRC = WORKSPACE_ROOT / "frontend" / "src"
ARTIFACT_PATH = ARTIFACTS_DIR / "phase1_fe_fetches.json"

# Test dosyalarını ENVANTERE almıyoruz (audit kapsamı: canlı FE→BE ilişkisi).
# Ama bir istatistik olarak sayıyoruz.
EXCLUDE_TEST_DIRS = {"__tests__", "__mocks__"}
EXCLUDE_TEST_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")

# Spot check (A4 DoD: manuel mod'un epias çağrısı + kritik 3 path)
SPOT_CHECK_PATHS = [
    "/api/pricing/analyze",
    "/api/epias/prices/{period}",
    "/full-process",
]

# ------------------------------------------------------------------------------
# Regex tanımları
# ------------------------------------------------------------------------------
# 1) axios instance method: <client>.<method>( <path-literal> [, { ... }] )
#    Client adı: api | adminApi | apiClient | axios  (axios.get gibi global kullanım da dahil)
AXIOS_CALL_RE = re.compile(
    r"""
    \b(?P<client>api|adminApi|apiClient|axios)
    \s*\.\s*
    (?P<method>get|post|put|delete|patch|head|options)
    (?:\s*<[^>]*>)?            # optional TS generic: .get<FooResponse>( — TÜM blok opsiyonel
    \s*\(
    \s*
    (?P<quote>[`'"])           # opening quote
    (?P<path>[^`'"]*)          # path content (no escape support — FE kodu böyle kullanmıyor)
    (?P=quote)                 # matching closing quote
    """,
    re.VERBOSE | re.DOTALL,    # DOTALL: '.' newline'ı da eşleştirsin (multi-line çağrı)
)

# 2) native fetch: fetch( <url-literal> [, { method: '...' ... }] )
FETCH_CALL_RE = re.compile(
    r"""
    \bfetch\s*\(\s*
    (?P<quote>[`'"])
    (?P<url>[^`'"]*)
    (?P=quote)
    """,
    re.VERBOSE | re.DOTALL,
)

# 2b) fetch(VAR_NAME, {...})  — değişken referanslı; VAR'ın tanımına bakacağız.
FETCH_CALL_VAR_RE = re.compile(
    r"""
    \bfetch\s*\(\s*
    (?P<name>[A-Z][A-Z0-9_]+)      # ALL_CAPS sabit ismi
    \s*,                            # argüman ayırıcı — fetch(VAR) tek arg olsa da genelde 2.arg var
    """,
    re.VERBOSE,
)

# const VAR_NAME = '/path' tarzı tanımlar — yalnızca '/' ile başlayan literal yakalıyoruz.
CONST_URL_RE = re.compile(
    r"""
    (?:const|let|var)\s+
    (?P<name>[A-Z][A-Z0-9_]+)
    \s*(?::\s*[^=]+)?               # optional TS type annotation
    \s*=\s*
    (?P<quote>[`'"])
    (?P<url>/[^\s`'"]*)             # '/' ile başlayan yol
    (?P=quote)
    """,
    re.VERBOSE,
)

# fetch'in ikinci argümanındaki method; aynı satır veya takip eden 1-6 satırda aranır.
FETCH_METHOD_RE = re.compile(r"""method\s*:\s*['"](?P<method>[A-Z]+)['"]""")

# 3) new URL(`${API_BASE}/path`) kalıbı — fetch(url.toString(), {method}) için
URL_CTOR_RE = re.compile(
    r"""
    \bnew\s+URL\s*\(\s*
    (?P<quote>[`'"])
    (?P<url>[^`'"]*)
    (?P=quote)
    """,
    re.VERBOSE,
)

# Path'ten API_BASE'i ayır
API_BASE_PREFIX_RE = re.compile(r"""^\$\{API_BASE\}""")

# Template literal interpolasyonları: ${foo}, ${foo.bar}, ${encodeURIComponent(x)}
INTERP_RE = re.compile(r"\$\{([^}]+)\}")

# Kurala göre path segment interpolasyonu nasıl placeholder'a çevrilir:
#   /foo/${id}/bar      -> /foo/{id}/bar
#   /foo?x=${y}         -> query kısmına dokunmaz (query params ayrı alan)
# Karmaşık expression için (ör. ${encodeURIComponent(period)}) son identifier alınır.
IDENT_TRAIL_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\)?$")


# ------------------------------------------------------------------------------
# Yardımcılar
# ------------------------------------------------------------------------------
def _posix_rel(path: Path) -> str:
    try:
        rel = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        rel = path
    return rel.as_posix()


def _is_test_path(path: Path) -> bool:
    if any(part in EXCLUDE_TEST_DIRS for part in path.parts):
        return True
    name = path.name
    return any(name.endswith(sfx) for sfx in EXCLUDE_TEST_SUFFIXES)


def _extract_interp_name(expr: str) -> str:
    """`${encodeURIComponent(period)}` → 'period'.

    Sadece alfanumerik son identifier'ı alır; aksi halde 'param'.
    """
    expr = expr.strip()
    m = IDENT_TRAIL_RE.search(expr)
    if m:
        return m.group(1)
    return "param"


def _normalize_path(raw: str) -> tuple[str, list[str], dict[str, Any], bool]:
    """Ham URL/path'i normalize et.

    Döner: (normalized_path, interpolations, params_meta, dynamic_flag)

    Kurallar:
    - `${API_BASE}` prefix'i kaldırılır.
    - Path segmentlerindeki ${...} → {<name>}
    - Query string ayrılır: params.query listesi (isim listesi; sabit değerler yok sayılır)
    - Path '/' ile başlamıyorsa başına eklenir.
    """
    # API_BASE prefix
    s = API_BASE_PREFIX_RE.sub("", raw)

    # Path ile query'yi ayır
    if "?" in s:
        path_part, query_part = s.split("?", 1)
    else:
        path_part, query_part = s, ""

    # Path'teki interpolasyonları topla ve placeholder'a çevir
    path_interps: list[str] = []

    def _path_sub(m: re.Match[str]) -> str:
        name = _extract_interp_name(m.group(1))
        path_interps.append(name)
        return "{" + name + "}"

    path_norm = INTERP_RE.sub(_path_sub, path_part)

    # Query string'deki interpolasyon isimleri (sadece isim tarafı için)
    query_params: list[str] = []
    if query_part:
        # Parça `a=${x}&b=c` ya da `${params}` (URLSearchParams) olabilir.
        # Template kısımlarını atla; "k=" formunu yakala.
        for segment in query_part.split("&"):
            if not segment:
                continue
            if "=" in segment:
                k = segment.split("=", 1)[0]
                # key kendisi de ${...} olabilir; çöz
                k_clean = INTERP_RE.sub(lambda m: _extract_interp_name(m.group(1)), k)
                if k_clean:
                    query_params.append(k_clean)
            else:
                # `${toString()}` gibi full-interp; "dynamic_query" olarak işaretle
                query_params.append("<dynamic>")

    # Normalize: '/' başla
    if not path_norm.startswith("/"):
        path_norm = "/" + path_norm

    # Trailing slash koru ama multi-slash yut (çok nadir)
    path_norm = re.sub(r"/{2,}", "/", path_norm)

    all_interps = path_interps + [q for q in query_params if q != "<dynamic>"]
    params_meta: dict[str, Any] = {"query": query_params, "body": False}
    dynamic = bool(path_interps) or bool(query_params)

    return path_norm, all_interps, params_meta, dynamic


def _classify_usage(path: str, file_rel: str) -> str:
    """Heuristic sınıflandırma — rapor için. A5 matching burayı kullanmaz."""
    p = path.lower()
    f = file_rel.lower()
    if p.startswith("/admin/market-prices") or "market-prices" in f:
        return "admin-market-prices"
    if p.startswith("/admin/telemetry") or "/telemetry" in f:
        return "telemetry"
    if p.startswith("/api/pricing"):
        return "pricing"
    if p.startswith("/api/epias"):
        return "epias"
    if p.startswith("/admin"):
        return "admin"
    if "invoice" in p or p.startswith("/offers"):
        return "invoice-offer"
    if p == "/analyze-invoice" or p == "/full-process" or p == "/calculate-offer":
        return "invoice-flow"
    if p.startswith("/health"):
        return "health"
    if p.startswith("/webhooks") or p.startswith("/audit-logs"):
        return "ops"
    if p.startswith("/customers") or p.startswith("/stats"):
        return "crm"
    if p.startswith("/pdf"):
        return "pdf"
    if p.startswith("/extraction") or p.startswith("/generate-pdf") or p.startswith("/generate-html"):
        return "pdf-generation"
    if p.startswith("/jobs") or p.startswith("/cache") or p.startswith("/metrics"):
        return "ops"
    return "other"


# ------------------------------------------------------------------------------
# Dosya tarama
# ------------------------------------------------------------------------------
def _find_nearby_method(source_lines: list[str], start_idx: int, window: int = 8) -> str | None:
    """fetch('...', { method: 'POST', ... }) için yakın satırlarda method ara.

    start_idx: fetch( bulunduğu satır indeksi (0-based).
    """
    end = min(len(source_lines), start_idx + window)
    blob = "\n".join(source_lines[start_idx:end])
    m = FETCH_METHOD_RE.search(blob)
    return m.group("method").upper() if m else None


def _has_body_payload(source_lines: list[str], start_idx: int, window: int = 8) -> bool:
    """fetch(...) ikinci argümanında body: veya formData gönderiyor mu?"""
    end = min(len(source_lines), start_idx + window)
    blob = "\n".join(source_lines[start_idx:end])
    return bool(re.search(r"\bbody\s*:", blob))


def _strip_comments_preserve_lines(text: str) -> str:
    """`//...\\n` ve `/* ... */` yorumlarını boşlukla değiştir, satır sayısını koru.

    Tek satır yorumlarda newline'ı bırakır; blok yorumlarda içerideki newline'ları
    korur — böylece match.start() ile satır numarası hesabı doğru kalır.
    """
    # Blok yorumları /* ... */
    def _block_repl(m: re.Match[str]) -> str:
        content = m.group(0)
        # İçindeki newline'ları koru, geri kalanı boşluk yap
        return "".join("\n" if ch == "\n" else " " for ch in content)

    text = re.sub(r"/\*.*?\*/", _block_repl, text, flags=re.DOTALL)
    # Tek satır yorumlar
    text = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), text)
    return text


def _line_of(text: str, pos: int) -> int:
    """Metinde 0-tabanlı pos'un 1-tabanlı satır numarası."""
    return text.count("\n", 0, pos) + 1


def scan_file(path: Path) -> list[dict]:
    try:
        text_raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    text = _strip_comments_preserve_lines(text_raw)
    file_rel = _posix_rel(path)
    records: list[dict] = []

    # Dosya içi sabit URL tanımlarını topla (fetch(VAR,...) çözümü için)
    const_url_map: dict[str, str] = {}
    for m in CONST_URL_RE.finditer(text):
        const_url_map[m.group("name")] = m.group("url")

    # 1) axios instance çağrıları (multi-line)
    for m in AXIOS_CALL_RE.finditer(text):
        raw = m.group("path")
        method = m.group("method").upper()
        client = m.group("client")
        norm_path, interps, params, dynamic = _normalize_path(raw)
        usage = _classify_usage(norm_path, file_rel)
        call_line = _line_of(text, m.start())
        records.append(
            {
                "method": method,
                "path": norm_path,
                "file": file_rel,
                "line": call_line,
                "client": client,
                "raw_path": raw,
                "interpolations": interps,
                "dynamic": dynamic,
                "params": params,
                "usage": usage,
            }
        )

    # 2) native fetch çağrıları (string literal)
    lines = text.splitlines()
    for m in FETCH_CALL_RE.finditer(text):
        raw = m.group("url")
        norm_path, interps, params, dynamic = _normalize_path(raw)
        call_line = _line_of(text, m.start())
        method = _find_nearby_method(lines, call_line - 1) or "GET"
        if _has_body_payload(lines, call_line - 1):
            params = {**params, "body": True}
        usage = _classify_usage(norm_path, file_rel)
        records.append(
            {
                "method": method,
                "path": norm_path,
                "file": file_rel,
                "line": call_line,
                "client": "fetch",
                "raw_path": raw,
                "interpolations": interps,
                "dynamic": dynamic,
                "params": params,
                "usage": usage,
            }
        )

    # 2b) fetch(VAR_NAME, ...) — sabit referansı ile çağrı
    for m in FETCH_CALL_VAR_RE.finditer(text):
        name = m.group("name")
        raw = const_url_map.get(name)
        if raw is None:
            continue
        norm_path, interps, params, dynamic = _normalize_path(raw)
        call_line = _line_of(text, m.start())
        method = _find_nearby_method(lines, call_line - 1) or "GET"
        if _has_body_payload(lines, call_line - 1):
            params = {**params, "body": True}
        usage = _classify_usage(norm_path, file_rel)
        records.append(
            {
                "method": method,
                "path": norm_path,
                "file": file_rel,
                "line": call_line,
                "client": "fetch-const",
                "raw_path": f"{name}={raw}",
                "interpolations": interps,
                "dynamic": dynamic,
                "params": params,
                "usage": usage,
            }
        )

    # 3) new URL(`${API_BASE}/path...`) kalıbı + eşlik eden fetch method
    for m in URL_CTOR_RE.finditer(text):
        raw = m.group("url")
        if "${API_BASE}" not in raw and not raw.startswith("/"):
            continue
        norm_path, interps, params, dynamic = _normalize_path(raw)
        call_line = _line_of(text, m.start())
        method = _find_nearby_method(lines, call_line - 1, window=12) or "GET"
        if _has_body_payload(lines, call_line - 1, window=12):
            params = {**params, "body": True}
        usage = _classify_usage(norm_path, file_rel)
        records.append(
            {
                "method": method,
                "path": norm_path,
                "file": file_rel,
                "line": call_line,
                "client": "fetch-url-ctor",
                "raw_path": raw,
                "interpolations": interps,
                "dynamic": dynamic,
                "params": params,
                "usage": usage,
            }
        )

    return records


# ------------------------------------------------------------------------------
# Ana çalışma
# ------------------------------------------------------------------------------
def main() -> int:
    if not FRONTEND_SRC.is_dir():
        print(f"[HATA] frontend/src bulunamadı: {FRONTEND_SRC}", file=sys.stderr)
        return 2

    all_ts = sorted(FRONTEND_SRC.rglob("*.ts"))
    all_tsx = sorted(FRONTEND_SRC.rglob("*.tsx"))
    all_files = [p for p in (all_ts + all_tsx) if "__pycache__" not in p.parts]

    prod_files = [p for p in all_files if not _is_test_path(p)]
    test_files = [p for p in all_files if _is_test_path(p)]

    all_calls: list[dict] = []
    for f in prod_files:
        all_calls.extend(scan_file(f))

    # Deterministik sıralama
    all_calls.sort(
        key=lambda r: (r["file"], r["line"], r["method"], r["path"], r["client"])
    )

    # Unique merge: (method, path) → {files: [...], occurrences}
    unique_map: dict[tuple[str, str], dict] = {}
    for c in all_calls:
        key = (c["method"], c["path"])
        if key not in unique_map:
            unique_map[key] = {
                "method": c["method"],
                "path": c["path"],
                "usage": c["usage"],
                "dynamic": c["dynamic"],
                "clients": set(),
                "files": [],
                "occurrences": 0,
            }
        u = unique_map[key]
        u["occurrences"] += 1
        u["clients"].add(c["client"])
        u["files"].append({"file": c["file"], "line": c["line"]})

    unique_list: list[dict] = []
    for (method, path), u in unique_map.items():
        u["clients"] = sorted(u["clients"])
        # files deterministik sırala
        u["files"].sort(key=lambda x: (x["file"], x["line"]))
        unique_list.append(u)
    unique_list.sort(key=lambda r: (r["path"], r["method"]))

    # Usage distribution (unique_list üzerinden)
    usage_dist: dict[str, int] = {}
    for u in unique_list:
        usage_dist[u["usage"]] = usage_dist.get(u["usage"], 0) + 1

    dynamic_count = sum(1 for u in unique_list if u["dynamic"])

    present = {u["path"] for u in unique_list}
    spot_check = {p: (p in present) for p in SPOT_CHECK_PATHS}

    artifact: dict[str, Any] = {
        "_meta": {
            "script": "02_inventory_fe_fetches.py",
            "scanned_root": _posix_rel(FRONTEND_SRC),
            "file_count": len(prod_files),
            "test_file_count_excluded": len(test_files),
            "call_count_raw": len(all_calls),
            "call_count_unique": len(unique_list),
            "usage_distribution": dict(sorted(usage_dist.items())),
            "dynamic_path_count": dynamic_count,
            "spot_check": spot_check,
        },
        "calls": all_calls,
        "unique_calls": unique_list,
    }

    ARTIFACT_PATH.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Stdout özeti
    print("=" * 78)
    print("A4 — Frontend fetch çağrı envanteri")
    print("=" * 78)
    print(f"Taranan dosya sayısı    : {len(prod_files)} (test dışı)")
    print(f"Dışlanan test dosyaları : {len(test_files)}")
    print(f"Toplam çağrı (ham)       : {len(all_calls)}")
    print(f"Benzersiz (method+path) : {len(unique_list)}")
    print(f"Dinamik path içeren     : {dynamic_count}")
    print()
    print("Usage dağılımı:")
    for u, c in sorted(usage_dist.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {u:<24s} {c}")
    print()
    print("Spot check (A4 DoD):")
    for p, ok in spot_check.items():
        mark = "✓" if ok else "✗"
        print(f"  [{mark}] {p}")
    missing = [p for p, ok in spot_check.items() if not ok]
    if missing:
        print()
        print("UYARI — FE'de çağrılmayan beklenen path'ler:")
        for p in missing:
            print(f"   - {p}")
    print()
    print("Örnek 3 benzersiz çağrı:")
    for u in unique_list[:3]:
        clients = ",".join(u["clients"])
        flag = " (dyn)" if u["dynamic"] else ""
        print(f"  {u['method']:<6s} {u['path']:<40s} [{clients}]{flag}  x{u['occurrences']}")
    print()
    print(f"Artifact: {_posix_rel(ARTIFACT_PATH)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
