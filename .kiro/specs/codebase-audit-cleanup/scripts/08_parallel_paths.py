"""
08_parallel_paths.py — Parallel path detection (A10, R24)

Read-only (R16). Kanıt-temelli (R1). Deterministik, idempotent.

Amaç:
  Aynı "işi" yapan ≥2 paralel yolu (kaynak / fonksiyon / endpoint / modül)
  tek tabloya koy. Her çift/grup için:
    - domain          : ptf | yekdem | validation | fe_client | extraction | pdf_jobs
    - paths[]         : paralel yolların tam referansı
    - status          : parallel_unresolved | legacy_migration | dual_active |
                        unconnected_alternative | resolved
    - severity        : P0..P3
    - converges_in_same_workflow: bool   ← R24 kritik kriteri
    - canonical_suggestion : (zaten kararlıysa) hangi yol canonical
    - orphan_paths[]       : hangi path(ler) bağlantısız
    - evidence_refs        : A2..A9 artifact referansları

Girdi:
    artifacts/phase1_db_inventory.json
    artifacts/phase2_endpoint_mapping.json
    artifacts/phase1_imports.json
    artifacts/phase2_invoice_flow_sources.json
    artifacts/phase3_duplications.json
    artifacts/phase4_sot_matrix.json

Çıktı:
    stdout: özet
    artifacts/phase3_parallel_paths.json
"""

from __future__ import annotations
import json
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

SCRIPT_DIR = Path(__file__).resolve().parent
SPEC_DIR = SCRIPT_DIR.parent
WORKSPACE_ROOT = SPEC_DIR.parent.parent.parent
ARTIFACTS_DIR = SPEC_DIR / "artifacts"

A2 = ARTIFACTS_DIR / "phase1_db_inventory.json"
A5 = ARTIFACTS_DIR / "phase2_endpoint_mapping.json"
A6 = ARTIFACTS_DIR / "phase1_imports.json"
A7 = ARTIFACTS_DIR / "phase2_invoice_flow_sources.json"
A8 = ARTIFACTS_DIR / "phase3_duplications.json"
A9 = ARTIFACTS_DIR / "phase4_sot_matrix.json"

OUT = ARTIFACTS_DIR / "phase3_parallel_paths.json"


def _posix_rel(path: Path) -> str:
    try:
        rel = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        rel = path
    return rel.as_posix()


# ------------------------------------------------------------------------------
# Parallel path kayıtları — A5+A7+A8+A9'dan konsolide
# ------------------------------------------------------------------------------
def build_parallel_paths(
    a5: dict, a6: dict, a7: dict, a8: dict, a9: dict
) -> list[dict]:
    paths: list[dict] = []

    # SoT matrisinden canonical önerilerini oku
    sot_by_domain = {row["domain"]: row for row in a9.get("sot_matrix", [])}

    # --- 1) PTF (P0) — "aynı dönem için iki farklı kaynaktan PTF okuma"
    ptf_sot = sot_by_domain.get("ptf", {})
    paths.append({
        "id": "PP-PTF",
        "domain": "ptf",
        "concept": "saatlik PTF (TL/MWh) — teklif & risk hesabı",
        "paths": [
            {
                "kind": "db_table",
                "ref": "hourly_market_prices",
                "role": "canonical (yeni)",
                "used_by": [
                    "pricing_router::analyze",
                    "pricing_router::simulate",
                    "pricing_router::compare",
                    "pricing_router::report/pdf",
                ],
                "status": "live",
            },
            {
                "kind": "db_table",
                "ref": "market_reference_prices",
                "role": "legacy manuel mod",
                "used_by": [
                    "main.py /api/epias/prices/{period} (fallback)",
                    "calculator (dolaylı)",
                ],
                "status": "live_legacy",
            },
        ],
        "status": ptf_sot.get("migration_status", "parallel_unresolved"),
        "severity": "P0",
        "converges_in_same_workflow": True,   # aynı teklif/risk akışında birleşir
        "convergence_evidence": [
            "/api/pricing/analyze (new) ve /analyze-invoice (legacy) aynı müşteriye teklif üretir",
        ],
        "canonical_suggestion": "hourly_market_prices",
        "orphan_paths": [],
        "evidence_refs": [
            "artifacts/phase1_db_inventory.json (F-PTF auto-flag)",
            "artifacts/phase2_invoice_flow_sources.json",
            "artifacts/phase4_sot_matrix.json (ptf row)",
        ],
        "delegated_to_spec": "ptf-sot-unification",
    })

    # --- 2) YEKDEM (P1)
    yk_sot = sot_by_domain.get("yekdem", {})
    paths.append({
        "id": "PP-YEKDEM",
        "domain": "yekdem",
        "concept": "aylık YEKDEM (TL/MWh)",
        "paths": [
            {
                "kind": "db_table",
                "ref": "monthly_yekdem_prices",
                "role": "canonical (yeni)",
                "used_by": ["pricing_router::yekdem endpoints", "calculator (dolaylı)"],
                "status": "live",
            },
            {
                "kind": "db_table",
                "ref": "market_reference_prices",
                "role": "legacy (eski YEKDEM rows — 39 eksik dönem)",
                "used_by": ["calculator fallback (dolaylı)"],
                "status": "live_legacy",
            },
        ],
        "status": yk_sot.get("migration_status", "legacy_rows_exist"),
        "severity": "P1",
        "converges_in_same_workflow": True,
        "convergence_evidence": [
            "aynı fatura validasyonu / teklif akışında YEKDEM bileşeni iki kaynaktan okunabilir",
        ],
        "canonical_suggestion": "monthly_yekdem_prices",
        "orphan_paths": [],
        "evidence_refs": [
            "artifacts/phase1_db_inventory.json",
            "artifacts/phase4_sot_matrix.json (yekdem row)",
        ],
        "delegated_to_spec": "yekdem-legacy-migration",
    })

    # --- 3) Invoice validation (P1) — legacy canlı + yeni stack DEAD
    val_sot = sot_by_domain.get("invoice_validation", {})
    verdict = a7.get("new_validation_stack", {}).get("verdict", "UNKNOWN")
    paths.append({
        "id": "PP-VALIDATION",
        "domain": "validation",
        "concept": "fatura ekstraksiyon doğrulaması",
        "paths": [
            {
                "kind": "function",
                "ref": "app.validator.validate_extraction",
                "role": "legacy (üretim yolu)",
                "used_by": [
                    "/analyze-invoice", "/full-process",
                    "/extraction/patch-fields", "/extraction/apply-suggested-fixes",
                    "/invoices/{id}/validate", "/invoices/{id}/extract",
                ],
                "status": "live",
            },
            {
                "kind": "function",
                "ref": "app.invoice.validation.validator.validate",
                "role": "yeni stack entry",
                "used_by": [],
                "status": "unconnected",
            },
            {
                "kind": "function",
                "ref": "app.invoice.validation.enforcement.apply_enforcement",
                "role": "yeni stack enforcement",
                "used_by": [],
                "status": "unconnected",
            },
            {
                "kind": "function",
                "ref": "app.invoice.validation.shadow.shadow_validate_hook",
                "role": "yeni stack shadow (planlanmış ama bağlanmamış)",
                "used_by": [],
                "status": "unconnected",
            },
        ],
        "status": "unconnected_alternative" if verdict == "DEAD" else "partial",
        "severity": "P1",
        "converges_in_same_workflow": True,  # bağlandığında aynı akışta yarışırlar
        "convergence_evidence": [
            "yeni stack üretime alınırsa legacy validator ile aynı handler'da çağrılır; "
            "şimdi DEAD olduğundan convergence potansiyel",
        ],
        "canonical_suggestion": "app.validator.validate_extraction",  # bugün canlı olan
        "orphan_paths": [
            "app.invoice.validation.validator.validate",
            "app.invoice.validation.enforcement.apply_enforcement",
            "app.invoice.validation.shadow.shadow_validate_hook",
        ],
        "evidence_refs": [
            "artifacts/phase2_invoice_flow_sources.json (verdict=DEAD)",
            "artifacts/phase1_imports.json",
            "artifacts/phase4_sot_matrix.json (invoice_validation row)",
        ],
        "delegated_to_spec": "invoice-validation-prod-hardening",
    })

    # --- 4) FE admin client (P2) — dual FE adapter (A5 dual_fe_client)
    dual_fe = a5.get("dual_fe_client", [])
    fe_sot = sot_by_domain.get("fe_admin_market_prices", {})
    if dual_fe:
        # Her dual kayıt için ayrı bir parallel path giriyoruz (endpoint bazlı)
        for d in dual_fe:
            endpoint_path = d["path"]
            is_admin = endpoint_path.startswith("/admin/")
            # Domain + canonical suggestion endpoint'e göre
            if is_admin:
                dom_label = "fe_admin"
                canonical = fe_sot.get("canonical_source")  # marketPricesApi.ts
            else:
                dom_label = "fe_epias"
                canonical = None  # /api/epias/prices için SoT kararı henüz yok
            # Yol başına role atama (heuristic):
            paths_for_dup: list[dict] = []
            for f in d.get("files", []):
                if is_admin:
                    role = "canonical (yeni modül)" if "marketPricesApi.ts" in f else "deprecated (api.ts)"
                else:
                    # /api/epias için App.tsx inline, api.ts axios — ikisi de canlı, SoT yok
                    role = "inline fetch (App.tsx)" if "App.tsx" in f else (
                        "axios wrapper (api.ts)" if "api.ts" in f else "unknown"
                    )
                paths_for_dup.append({
                    "kind": "fe_caller",
                    "ref": f,
                    "role": role,
                    "used_by": [],
                    "status": "live",
                })
            paths.append({
                "id": f"PP-FE-DUAL:{d['method']}:{endpoint_path}",
                "domain": dom_label,
                "concept": f"FE → {d['method']} {endpoint_path}",
                "paths": paths_for_dup,
                "status": "dual_active",
                "severity": "P2",
                "converges_in_same_workflow": True,
                "convergence_evidence": [
                    f"her iki FE yolu da {d['method']} {endpoint_path} BE endpoint'ine bağlanıyor",
                ],
                "canonical_suggestion": canonical,
                "orphan_paths": [],
                "evidence_refs": ["artifacts/phase2_endpoint_mapping.json (dual_fe_client)"],
                "delegated_to_spec": "pricing-consistency-fixes" if is_admin else None,
            })

    # --- 5) Extraction (olası, heuristic) — orphan extractor'lar varsa
    # A6 orphan listesinden extraction aile modüllerini topla
    modules = a6.get("modules", [])
    extraction_orphans = [
        m for m in modules
        if m.get("status") == "orphan" and
           any(tok in m["module"] for tok in ("extractor", "canonical_extractor", "fast_extractor"))
    ]
    if extraction_orphans:
        paths.append({
            "id": "PP-EXTRACTION",
            "domain": "extraction",
            "concept": "fatura ekstraksiyon motoru",
            "paths": [
                {
                    "kind": "module",
                    "ref": "app.extractor",
                    "role": "canlı (main.py import)",
                    "used_by": ["/analyze-invoice", "/full-process"],
                    "status": "live",
                },
                *[
                    {
                        "kind": "module",
                        "ref": m["module"],
                        "role": "orphan (fully-dead, ne main ne test import ediyor)",
                        "used_by": [],
                        "status": "orphan",
                    }
                    for m in extraction_orphans
                ],
            ],
            "status": "unconnected_alternative",
            "severity": "P2",
            "converges_in_same_workflow": False,  # orphan'lar bağlı değil → birleşim yok
            "convergence_evidence": [
                "orphan extractor'lar hiçbir handler'dan çağrılmıyor",
            ],
            "canonical_suggestion": "app.extractor",
            "orphan_paths": [m["module"] for m in extraction_orphans],
            "evidence_refs": [
                "artifacts/phase1_imports.json (orphan modules)",
                "artifacts/phase2_invoice_flow_sources.json",
            ],
            "delegated_to_spec": None,  # cleanup spec'i audit dışı; önce silme kararı
        })

    # --- 6) PDF jobs — orphan router
    pdf_sot = sot_by_domain.get("pdf_jobs", {})
    paths.append({
        "id": "PP-PDF_JOBS",
        "domain": "pdf_jobs",
        "concept": "async PDF üretim job akışı",
        "paths": [
            {
                "kind": "router",
                "ref": "app.pdf_api.router",
                "role": "tasarlanmış ama include_router çağrılmamış",
                "used_by": [],
                "status": "orphan_router",
                "endpoints": ["POST /pdf/jobs", "GET /pdf/jobs/{id}", "GET /pdf/jobs/{id}/download"],
            },
            # pdf_generator, report_pdf endpoint'i (pricing) — inline sync üretim
            {
                "kind": "endpoint",
                "ref": "POST /api/pricing/report/pdf",
                "role": "inline/sync PDF üretim (canlı)",
                "used_by": ["FE pricing report download"],
                "status": "live",
            },
            {
                "kind": "endpoint",
                "ref": "POST /offers/{id}/generate-pdf",
                "role": "offer PDF üretim (canlı, sync)",
                "used_by": ["offers panel"],
                "status": "live",
            },
        ],
        "status": pdf_sot.get("migration_status", "router_unregistered"),
        "severity": "P1",
        "converges_in_same_workflow": False,  # async stack bağlı değil → paralel yol yok aslında
        "convergence_evidence": [
            "async pdf_api stack kullanılmıyor; sync üretim üretimde — henüz paralel değil, "
            "ancak aynı 'PDF üret' hedefine iki farklı mimari aday",
        ],
        "canonical_suggestion": "sync inline (canlı) — async stack önce bağlanmalı ya da silinmeli",
        "orphan_paths": ["app.pdf_api.router"],
        "evidence_refs": [
            "artifacts/phase2_endpoint_mapping.json (BE_ONLY /pdf/*)",
            "artifacts/phase1_imports.json (orphan_router)",
            "artifacts/phase4_sot_matrix.json (pdf_jobs row)",
        ],
        "delegated_to_spec": "pdf-render-worker",
    })

    # Deterministik sıralama: severity → id
    sev_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    paths.sort(key=lambda p: (sev_order.get(p["severity"], 9), p["id"]))
    return paths


# ------------------------------------------------------------------------------
# Ana
# ------------------------------------------------------------------------------
def main() -> int:
    for p in (A2, A5, A6, A7, A8, A9):
        if not p.is_file():
            print(f"[HATA] Girdi eksik: {p}", file=sys.stderr)
            return 2

    a5 = json.loads(A5.read_text(encoding="utf-8"))
    a6 = json.loads(A6.read_text(encoding="utf-8"))
    a7 = json.loads(A7.read_text(encoding="utf-8"))
    a8 = json.loads(A8.read_text(encoding="utf-8"))
    a9 = json.loads(A9.read_text(encoding="utf-8"))

    parallel = build_parallel_paths(a5, a6, a7, a8, a9)

    # Özet sayımlar
    by_sev: dict[str, int] = {}
    by_convergence: dict[str, int] = {"convergent": 0, "non_convergent": 0}
    by_status: dict[str, int] = {}
    for p in parallel:
        by_sev[p["severity"]] = by_sev.get(p["severity"], 0) + 1
        key = "convergent" if p["converges_in_same_workflow"] else "non_convergent"
        by_convergence[key] += 1
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1

    artifact: dict[str, Any] = {
        "_meta": {
            "script": "08_parallel_paths.py",
            "inputs": [_posix_rel(p) for p in (A2, A5, A6, A7, A8, A9)],
            "counts": {
                "total": len(parallel),
                "by_severity": dict(sorted(by_sev.items())),
                "by_convergence": by_convergence,
                "by_status": dict(sorted(by_status.items())),
            },
        },
        "parallel_paths": parallel,
    }

    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    # Stdout özeti
    print("=" * 78)
    print("A10 — Parallel path detection (R24)")
    print("=" * 78)
    print(f"Toplam paralel yol : {len(parallel)}")
    print(f"Severity dağılımı  : {dict(sorted(by_sev.items()))}")
    print(f"Convergence        : {by_convergence}")
    print(f"Status             : {dict(sorted(by_status.items()))}")
    print()

    for p in parallel:
        mark = "⚠" if p["converges_in_same_workflow"] else "·"
        print(f"[{p['severity']}] {mark} {p['id']}")
        print(f"    concept  : {p['concept']}")
        print(f"    status   : {p['status']}")
        print(f"    canonical: {p.get('canonical_suggestion') or '(belirlenmedi)'}")
        print(f"    paths ({len(p['paths'])}):")
        for path in p["paths"]:
            role = path.get("role", "")
            status = path.get("status", "")
            print(f"      - [{status:<16s}] {path['ref']}  ({role})")
        if p["orphan_paths"]:
            print(f"    orphan   : {len(p['orphan_paths'])} path(s)")
        if p.get("delegated_to_spec"):
            print(f"    → spec   : {p['delegated_to_spec']}")
        print()

    print(f"Artifact: {_posix_rel(OUT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
