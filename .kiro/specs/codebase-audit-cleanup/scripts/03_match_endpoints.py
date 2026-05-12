"""
03_match_endpoints.py — BE endpoint ↔ FE fetch eşleştirmesi (A5)

Read-only (R16). Kanıt-temelli (R1). Deterministik, idempotent.

Girdi:
    artifacts/phase1_endpoints.json    (A3)
    artifacts/phase1_fe_fetches.json   (A4)

Çıktı:
    stdout: insan-okunur kategori özeti
    artifacts/phase2_endpoint_mapping.json

Şema:
{
  "_meta": {
    "script", "be_endpoint_count", "fe_unique_count",
    "categories": {"MATCHED": N, "FE_ONLY": M, "BE_ONLY": K, "METHOD_MISMATCH": L, "DUAL_FE_CLIENT": D}
  },
  "matched":          [{fe_method, fe_path, be_method, be_path, be_file, be_line, fe_refs:[{file,line,client}]}],
  "method_mismatch":  [{path, fe_methods:[...], be_methods:[...], fe_refs, be_refs}],
  "fe_only":          [{method, path, fe_refs}],       # FE çağırıyor, BE yok
  "be_only":          [{method, path, function, file, line, router}],  # BE var, FE çağırmıyor
  "dual_fe_client":   [{method, path, clients:[...], fe_refs, be_match}]  # 1 endpoint ↔ >1 FE client
}

Kategori kuralları:
    1. Normalize: her iki tarafta da path'i `/{param}` placeholder'ına çevir; trailing '/' sil.
       FE zaten `/{param}` formatında (A4 çıktısı). BE FastAPI `/{period}` doğal.
       Ekstra adım: query string yok (A4 normalize etti), BE path'i de zaten query-free.
    2. Bir FE çağrısı için aynı path+method BE'de varsa: MATCHED.
    3. Aynı path'te method uyuşmuyorsa (FE GET, BE sadece POST): METHOD_MISMATCH.
    4. Path hiç yoksa: FE_ONLY (ölü FE çağrısı adayı).
    5. BE endpoint'e hiçbir FE çağrısı yoksa: BE_ONLY (orphan/dead endpoint adayı).
    6. Bir MATCHED endpoint'e 2+ farklı FE client çağrı yapıyorsa: DUAL_FE_CLIENT
       (ör. deprecated api.ts fonksiyonu + yeni marketPricesApi.ts aynı path'i çağırıyor).

Path-param eşleşme:
    FE:  /api/epias/prices/{period}
    BE:  /api/epias/prices/{period}
    Parametre adı farklılığı toleranslı: segment bazında "literal vs {x}" karşılaştırması.
    Örnek: FE /users/{customerId} ↔ BE /users/{customer_id} → eşleşir (isim değil konum önemli).
"""

from __future__ import annotations
import json
import re
import sys
from pathlib import Path
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

BE_ARTIFACT = ARTIFACTS_DIR / "phase1_endpoints.json"
FE_ARTIFACT = ARTIFACTS_DIR / "phase1_fe_fetches.json"
OUT_ARTIFACT = ARTIFACTS_DIR / "phase2_endpoint_mapping.json"


def _posix_rel(path: Path) -> str:
    try:
        rel = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        rel = path
    return rel.as_posix()


# ------------------------------------------------------------------------------
# Path normalize
# ------------------------------------------------------------------------------
PLACEHOLDER_RE = re.compile(r"\{[^/}]+\}")


def normalize_path(path: str) -> str:
    """Trailing slash sil, multi-slash yut, path-param'ı segment konumuna indirge.

    Örnek:
        /api/epias/prices/{period}/     -> /api/epias/prices/{_}
        /users/{customerId}             -> /users/{_}
    """
    if not path:
        return "/"
    p = re.sub(r"/{2,}", "/", path)
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    # Parametre isimlerini segment konumuna indirge (isim-duyarsız eşleşme)
    p = PLACEHOLDER_RE.sub("{_}", p)
    return p


# ------------------------------------------------------------------------------
# Ana
# ------------------------------------------------------------------------------
def main() -> int:
    # Girdiler
    for p in (BE_ARTIFACT, FE_ARTIFACT):
        if not p.is_file():
            print(f"[HATA] Girdi eksik: {p}", file=sys.stderr)
            return 2

    be_data = json.loads(BE_ARTIFACT.read_text(encoding="utf-8"))
    fe_data = json.loads(FE_ARTIFACT.read_text(encoding="utf-8"))

    # Endpointler: list of dict (A3 şeması)
    be_endpoints: list[dict] = be_data.get("endpoints", [])
    # FE: unique_calls tarafını kullanırız (merge edilmiş); ayrıca raw calls'a da başvururuz
    fe_unique: list[dict] = fe_data.get("unique_calls", [])
    fe_raw: list[dict] = fe_data.get("calls", [])

    # Ham çağrıları (method, normalized_path) -> [raw_rec, ...] haritala — DUAL_FE_CLIENT için
    fe_raw_by_key: dict[tuple[str, str], list[dict]] = {}
    for c in fe_raw:
        key = (c["method"], normalize_path(c["path"]))
        fe_raw_by_key.setdefault(key, []).append(c)

    # BE endpoint indexleri
    be_by_method_path: dict[tuple[str, str], list[dict]] = {}   # (method, normpath) → [be_rec]
    be_by_path_any_method: dict[str, set[str]] = {}             # normpath → {methods}
    for e in be_endpoints:
        np = normalize_path(e["path"])
        # API_ROUTE'u tek bir BE kaydı olarak tutarız; FE ile eşleşmesi için yaygın method'lara
        # genişletmiyoruz (audit: olduğu gibi raporlayacağız).
        be_by_method_path.setdefault((e["method"], np), []).append(e)
        be_by_path_any_method.setdefault(np, set()).add(e["method"])

    # FE unique kümesi
    fe_set: set[tuple[str, str]] = set()
    for u in fe_unique:
        fe_set.add((u["method"], normalize_path(u["path"])))

    matched: list[dict] = []
    method_mismatch_map: dict[str, dict] = {}  # normpath -> record
    fe_only: list[dict] = []
    be_only: list[dict] = []
    dual_fe: list[dict] = []

    # ---- FE açısından (MATCHED / METHOD_MISMATCH / FE_ONLY)
    for u in fe_unique:
        fe_m = u["method"]
        fe_p_raw = u["path"]
        fe_p = normalize_path(fe_p_raw)

        be_hits = be_by_method_path.get((fe_m, fe_p))
        if be_hits:
            # MATCHED — tüm BE tanımlarını rapora ekleyelim (1'den fazla olursa da)
            fe_refs_raw = fe_raw_by_key.get((fe_m, fe_p), [])
            for be in be_hits:
                matched.append(
                    {
                        "fe_method": fe_m,
                        "fe_path": fe_p_raw,
                        "normalized_path": fe_p,
                        "be_method": be["method"],
                        "be_path": be["path"],
                        "be_file": be["file"],
                        "be_line": be["line"],
                        "be_function": be["function"],
                        "be_router": be["router"],
                        "fe_refs": [
                            {"file": r["file"], "line": r["line"], "client": r["client"]}
                            for r in fe_refs_raw
                        ],
                    }
                )
            # DUAL_FE_CLIENT: aynı endpoint'e farklı KAYNAK DOSYADAN veya farklı
            # client-adından çağrı varsa → paralel FE adapter paterni.
            distinct_clients = sorted({r["client"] for r in fe_refs_raw})
            distinct_files = sorted({r["file"] for r in fe_refs_raw})
            if len(distinct_files) >= 2 or len(distinct_clients) >= 2:
                dual_fe.append(
                    {
                        "method": fe_m,
                        "path": fe_p_raw,
                        "normalized_path": fe_p,
                        "clients": distinct_clients,
                        "files": distinct_files,
                        "fe_refs": [
                            {"file": r["file"], "line": r["line"], "client": r["client"]}
                            for r in fe_refs_raw
                        ],
                        "be_match": True,
                    }
                )
            continue

        # Aynı path'te başka method var mı?
        be_methods = be_by_path_any_method.get(fe_p)
        if be_methods:
            # METHOD_MISMATCH
            rec = method_mismatch_map.get(fe_p)
            if rec is None:
                rec = {
                    "path": fe_p_raw,
                    "normalized_path": fe_p,
                    "fe_methods": set(),
                    "be_methods": sorted(be_methods),
                    "fe_refs": [],
                    "be_refs": [],
                }
                # BE referanslarını topla
                for be_m in be_methods:
                    for be in be_by_method_path.get((be_m, fe_p), []):
                        rec["be_refs"].append(
                            {
                                "method": be["method"],
                                "file": be["file"],
                                "line": be["line"],
                                "function": be["function"],
                                "router": be["router"],
                            }
                        )
                method_mismatch_map[fe_p] = rec
            rec["fe_methods"].add(fe_m)
            for r in fe_raw_by_key.get((fe_m, fe_p), []):
                rec["fe_refs"].append(
                    {
                        "method": fe_m,
                        "file": r["file"],
                        "line": r["line"],
                        "client": r["client"],
                    }
                )
        else:
            # FE_ONLY
            fe_refs_raw = fe_raw_by_key.get((fe_m, fe_p), [])
            fe_only.append(
                {
                    "method": fe_m,
                    "path": fe_p_raw,
                    "normalized_path": fe_p,
                    "fe_refs": [
                        {"file": r["file"], "line": r["line"], "client": r["client"]}
                        for r in fe_refs_raw
                    ],
                }
            )

    # method_mismatch'i listele, fe_methods'u sorted list'e çevir
    method_mismatch: list[dict] = []
    for rec in method_mismatch_map.values():
        rec["fe_methods"] = sorted(rec["fe_methods"])
        method_mismatch.append(rec)

    # ---- BE açısından (BE_ONLY)
    for e in be_endpoints:
        np = normalize_path(e["path"])
        if (e["method"], np) in fe_set:
            continue
        # Aynı path farklı method'da FE çağrısı var mı?
        fe_methods_on_path = {m for (m, p) in fe_set if p == np}
        if fe_methods_on_path:
            # Bu endpoint METHOD_MISMATCH kaydında zaten BE_ONLY olarak geçer; ayrıca ekleme
            # Yine de işaret: path'teki BE method'u FE'de yok → method_mismatch kapsamında
            continue
        be_only.append(
            {
                "method": e["method"],
                "path": e["path"],
                "normalized_path": np,
                "function": e["function"],
                "file": e["file"],
                "line": e["line"],
                "router": e["router"],
            }
        )

    # Deterministik sıralama
    matched.sort(key=lambda r: (r["normalized_path"], r["fe_method"]))
    method_mismatch.sort(key=lambda r: r["normalized_path"])
    fe_only.sort(key=lambda r: (r["normalized_path"], r["method"]))
    be_only.sort(key=lambda r: (r["normalized_path"], r["method"]))
    dual_fe.sort(key=lambda r: (r["normalized_path"], r["method"]))

    categories = {
        "MATCHED": len(matched),
        "METHOD_MISMATCH": len(method_mismatch),
        "FE_ONLY": len(fe_only),
        "BE_ONLY": len(be_only),
        "DUAL_FE_CLIENT": len(dual_fe),
    }

    artifact: dict[str, Any] = {
        "_meta": {
            "script": "03_match_endpoints.py",
            "be_endpoint_count": len(be_endpoints),
            "fe_unique_count": len(fe_unique),
            "fe_raw_count": len(fe_raw),
            "categories": categories,
        },
        "matched": matched,
        "method_mismatch": method_mismatch,
        "fe_only": fe_only,
        "be_only": be_only,
        "dual_fe_client": dual_fe,
    }

    OUT_ARTIFACT.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Stdout özeti
    print("=" * 78)
    print("A5 — Endpoint ↔ FE fetch matching")
    print("=" * 78)
    print(f"BE endpoint sayısı       : {len(be_endpoints)}")
    print(f"FE benzersiz çağrı       : {len(fe_unique)}")
    print(f"FE ham çağrı             : {len(fe_raw)}")
    print()
    print("Kategori dağılımı:")
    for cat, n in categories.items():
        print(f"  {cat:<18s} {n}")
    print()

    # FE coverage hesabı
    fe_total = len(fe_unique)
    fe_classified = len(matched) + sum(len(r["fe_methods"]) for r in method_mismatch) + len(fe_only)
    # Not: matched'te bir FE birden çok BE ile eşleşebilir; biz FE-unique bazlı DoD istiyoruz.
    fe_matched_keys = {(r["fe_method"], r["normalized_path"]) for r in matched}
    fe_mismatch_keys = {(m, r["normalized_path"]) for r in method_mismatch for m in r["fe_methods"]}
    fe_only_keys = {(r["method"], r["normalized_path"]) for r in fe_only}
    fe_covered = len(fe_matched_keys | fe_mismatch_keys | fe_only_keys)
    print(f"FE coverage              : {fe_covered}/{fe_total}"
          f"  (matched={len(fe_matched_keys)}, mismatch={len(fe_mismatch_keys)}, fe_only={len(fe_only_keys)})")
    print()

    # Örnekler
    if method_mismatch:
        print("Method mismatch örnekleri:")
        for r in method_mismatch[:5]:
            print(f"  {r['path']:<40s} FE={r['fe_methods']} BE={r['be_methods']}")
        print()
    if fe_only:
        print("FE_ONLY (BE'de karşılığı yok) — ilk 5:")
        for r in fe_only[:5]:
            loc = r["fe_refs"][0] if r["fe_refs"] else {}
            print(f"  {r['method']:<6s} {r['path']:<40s}  {loc.get('file')}:{loc.get('line')}")
        print()
    if be_only:
        print(f"BE_ONLY (FE çağırmıyor) — {len(be_only)} adet, ilk 10:")
        for r in be_only[:10]:
            print(f"  {r['method']:<6s} {r['path']:<40s}  [{r['router']}]  {r['file']}:{r['line']}")
        print()
    if dual_fe:
        print(f"DUAL_FE_CLIENT ({len(dual_fe)}):")
        for r in dual_fe:
            files_str = ", ".join(r.get("files", []))
            print(f"  {r['method']:<6s} {r['path']:<40s}  clients={r['clients']}  files=[{files_str}]")
        print()

    # A5 DoD: "/full-process" matched, pdf endpoint'leri BE_ONLY, dual FE client tespit
    print("A5 DoD kontrolleri:")
    full_process_matched = any(
        r["normalized_path"] == "/full-process" for r in matched
    )
    pdf_be_only = [r for r in be_only if r["normalized_path"].startswith("/pdf/")]
    print(f"  [{'✓' if full_process_matched else '✗'}] /full-process MATCHED (false alarm kapandı)")
    print(f"  [{'✓' if pdf_be_only else '✗'}] /pdf/* BE_ONLY (dead router teyidi) — {len(pdf_be_only)} adet")
    print(f"  [{'✓' if dual_fe else '—'}] DUAL_FE_CLIENT tespit edildi ({len(dual_fe)} endpoint)")

    print()
    print(f"Artifact: {_posix_rel(OUT_ARTIFACT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
