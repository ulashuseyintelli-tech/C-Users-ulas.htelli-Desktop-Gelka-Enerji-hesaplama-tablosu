"""
07_sot_matrix_archaeology.py — SoT matrisi + git arkeolojisi (A9)

Read-only (R16). Kanıt-temelli (R1). Deterministik, idempotent.

İki bölümlü analiz:

(A) SoT matrisi — her canonical domain için:
    {concept, canonical_source, canonical_writer, readers, deprecated,
     migration_status}
    Domainler (karar kilit):
      - PTF        canonical = hourly_market_prices
      - YEKDEM     canonical = monthly_yekdem_prices
      - validation canonical = app.validator (legacy canlı; yeni stack DEAD)
      - pdf_jobs   canonical = (henüz yok — orphan router)
      - fe_admin   canonical = marketPricesApi.ts (yeni), api.ts deprecated

(B) Git arkeolojisi — kritik bulguların:
      * introduced_at   (ilk commit: sha, date, message)
      * last_modified   (son commit: sha, date, message)
      * used_in_production (A6/A7 verdict'ten türetilir)

    Odak set (talimat gereği):
      - Tablolar: market_reference_prices, hourly_market_prices, monthly_yekdem_prices
      - Modüller: app.validator, app.invoice.validation.*, app.pdf_api
      - Orphan modüller (ilk 9 fully-dead)

Yöntem:
  - Dosyalar için: `git log --follow --reverse --format=...` → ilk commit
                   `git log -1 --format=...` → son commit
  - Tablolar (DB schema string'leri) için: `git log -S "token" --reverse ...` (pickaxe)

Girdi:
    artifacts/phase1_db_inventory.json
    artifacts/phase1_imports.json
    artifacts/phase2_invoice_flow_sources.json
    artifacts/phase3_duplications.json

Çıktı:
    stdout: özet
    artifacts/phase4_sot_matrix.json
"""

from __future__ import annotations
import json
import subprocess
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

A2 = ARTIFACTS_DIR / "phase1_db_inventory.json"
A6 = ARTIFACTS_DIR / "phase1_imports.json"
A7 = ARTIFACTS_DIR / "phase2_invoice_flow_sources.json"
A8 = ARTIFACTS_DIR / "phase3_duplications.json"

OUT = ARTIFACTS_DIR / "phase4_sot_matrix.json"


def _posix_rel(path: Path) -> str:
    try:
        rel = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        rel = path
    return rel.as_posix()


# ------------------------------------------------------------------------------
# Git helpers
# ------------------------------------------------------------------------------
def _run_git(args: list[str]) -> str:
    """git komutunu çalıştır; boş string döner hata halinde."""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,   # pickaxe aramaları büyük repo'da yavaş olabilir
        )
        if r.returncode != 0:
            return ""
        return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def git_file_first_commit(path: str) -> dict | None:
    """Bir dosya için ilk (introduced) commit bilgisi.

    `git log --follow --reverse --format=... -- <path>` → ilk satır.
    """
    out = _run_git([
        "log", "--follow", "--reverse",
        "--format=%H%x01%ai%x01%an%x01%s",
        "--", path,
    ])
    for line in out.splitlines():
        parts = line.split("\x01", 3)
        if len(parts) == 4:
            return {
                "sha":     parts[0],
                "date":    parts[1][:10],
                "author":  parts[2],
                "message": parts[3],
            }
    return None


def git_file_last_commit(path: str) -> dict | None:
    out = _run_git([
        "log", "-1", "--follow",
        "--format=%H%x01%ai%x01%an%x01%s",
        "--", path,
    ])
    line = out.strip().splitlines()[0] if out.strip() else ""
    parts = line.split("\x01", 3)
    if len(parts) == 4:
        return {
            "sha":     parts[0],
            "date":    parts[1][:10],
            "author":  parts[2],
            "message": parts[3],
        }
    return None


def git_pickaxe_first(token: str) -> dict | None:
    """String token'ı ilk ekleyen commit (pickaxe).

    Not: `git log -S <token> --reverse` tüm tarihi gezer.
    Pathspec ile `.py` dosyalarına kısıtlanır — PDF/binary dosyalar filtrelenir.
    """
    out = _run_git([
        "log", "-S", token, "--reverse",
        "--format=%H%x01%ai%x01%an%x01%s",
        "--", "*.py", "*.sql", "*.md",
    ])
    for line in out.splitlines():
        parts = line.split("\x01", 3)
        if len(parts) == 4:
            return {
                "sha":     parts[0],
                "date":    parts[1][:10],
                "author":  parts[2],
                "message": parts[3],
            }
    return None


def git_pickaxe_last(token: str) -> dict | None:
    out = _run_git([
        "log", "-S", token, "-1",
        "--format=%H%x01%ai%x01%an%x01%s",
        "--", "*.py", "*.sql", "*.md",
    ])
    line = out.strip().splitlines()[0] if out.strip() else ""
    parts = line.split("\x01", 3)
    if len(parts) == 4:
        return {
            "sha":     parts[0],
            "date":    parts[1][:10],
            "author":  parts[2],
            "message": parts[3],
        }
    return None


# ------------------------------------------------------------------------------
# Odak set (talimat)
# ------------------------------------------------------------------------------
# Tablolar (pickaxe ile taranır)
FOCUS_TABLES = [
    "hourly_market_prices",
    "monthly_yekdem_prices",
    "market_reference_prices",
]

# Dosyalar (file-based git log)
FOCUS_FILES_CORE = [
    # Validation stack
    "backend/app/validator.py",                             # legacy
    "backend/app/invoice/validation/__init__.py",
    "backend/app/invoice/validation/validator.py",          # yeni entry
    "backend/app/invoice/validation/enforcement.py",
    "backend/app/invoice/validation/shadow.py",
    # Dead router
    "backend/app/pdf_api.py",
]


def _orphan_module_files(imports_data: dict, limit: int = 9) -> list[str]:
    """A6 imports artifact'ından fully-dead orphan modüllerin (external mention=0)
    dosya yollarını döndür. A8'deki 'fully_dead' listesi daha net ama A8 modül
    nesnesi farklı; A6'daki status='orphan' yeterli — A8 'fully_dead' kesişimini
    burada duplicate etmiyoruz, ilk N'i alıyoruz.
    """
    orphans = [
        m for m in imports_data.get("modules", [])
        if m.get("status") == "orphan" and m.get("file")
    ]
    orphans.sort(key=lambda m: m["module"])
    return [m["file"] for m in orphans[:limit]]


# ------------------------------------------------------------------------------
# SoT matrisi — elle kurulu, A8 kanıtlarına bağlı
# ------------------------------------------------------------------------------
def build_sot_matrix(a7: dict, a8: dict) -> list[dict]:
    """Her canonical domain için SoT matrisi satırı üret.

    Alanlar: concept, canonical_source, canonical_writer, readers,
             deprecated, migration_status, evidence_refs
    """
    # A8 duplikasyonlarını id'ye göre indexle
    dup_by_id = {d["id"]: d for d in a8.get("duplications", [])}
    matrix: list[dict] = []

    # --- Domain: PTF
    matrix.append({
        "domain": "ptf",
        "concept": "saatlik PTF (TL/MWh)",
        "canonical_source": "hourly_market_prices",
        "canonical_writer": "admin EPİAŞ sync / pricing_router",
        "readers": [
            "pricing_router::analyze / simulate / compare / report",
            "main.py::epias endpoints",
        ],
        "deprecated": [
            "market_reference_prices (legacy manuel mod)",
        ],
        "migration_status": "parallel_unresolved",  # F-PTF P0
        "evidence_refs": [
            "artifacts/phase1_db_inventory.json (F-PTF auto-flag)",
            "artifacts/phase2_invoice_flow_sources.json (market_reference_prices x9 ref, hourly_market_prices x2 ref)",
        ],
        "delegated_to_spec": dup_by_id.get("F-PTF", {}).get("delegated_to_spec"),
        "severity": "P0",
    })

    # --- Domain: YEKDEM
    matrix.append({
        "domain": "yekdem",
        "concept": "aylık YEKDEM (TL/MWh)",
        "canonical_source": "monthly_yekdem_prices",
        "canonical_writer": "admin sync / bulk import",
        "readers": [
            "calculator::calculate_offer (dolaylı)",
            "validator::validate_extraction (dolaylı)",
        ],
        "deprecated": [
            "market_reference_prices (legacy YEKDEM rows — 39 eksik dönem)",
        ],
        "migration_status": "legacy_rows_exist",  # F-YEKDEM-eski P1
        "evidence_refs": [
            "artifacts/phase1_db_inventory.json (cross-source YEKDEM)",
            "artifacts/phase2_invoice_flow_sources.json (monthly_yekdem_prices x4 ref)",
        ],
        "delegated_to_spec": dup_by_id.get("F-YEKDEM-eski", {}).get("delegated_to_spec"),
        "severity": "P1",
    })

    # --- Domain: invoice validation
    new_verdict = a7.get("new_validation_stack", {}).get("verdict", "UNKNOWN")
    matrix.append({
        "domain": "invoice_validation",
        "concept": "fatura ekstraksiyon doğrulaması",
        "canonical_source": "app.validator::validate_extraction",  # legacy canlı
        "canonical_writer": "main.py handler'ları (analyze-invoice, full-process, ...)",
        "readers": [
            "/analyze-invoice", "/full-process", "/extraction/patch-fields",
            "/extraction/apply-suggested-fixes", "/invoices/{id}/validate",
            "/invoices/{id}/extract",
        ],
        "deprecated": [],  # legacy hâlâ canlı; yeni stack henüz bağlanmadı
        "unconnected_alternative": [
            "app.invoice.validation.validator::validate",
            "app.invoice.validation.enforcement::apply_enforcement",
            "app.invoice.validation.shadow::shadow_validate_hook",
        ],
        "migration_status": "new_stack_dead" if new_verdict == "DEAD" else "partial",
        "evidence_refs": [
            "artifacts/phase2_invoice_flow_sources.json (verdict=" + new_verdict + ")",
            "artifacts/phase1_imports.json (invoice.validation.* alive_from_tests_only)",
        ],
        "delegated_to_spec": dup_by_id.get("F-VALIDATION", {}).get("delegated_to_spec"),
        "severity": "P1",
    })

    # --- Domain: pdf_jobs (orphan router)
    matrix.append({
        "domain": "pdf_jobs",
        "concept": "async PDF üretim job akışı",
        "canonical_source": None,  # tasarım var ama wire edilmemiş
        "canonical_writer": None,
        "readers": [],
        "deprecated": [],
        "unconnected_alternative": [
            "app.pdf_api.router (3 endpoint: POST /pdf/jobs, GET /pdf/jobs/{id}, GET /pdf/jobs/{id}/download)",
        ],
        "migration_status": "router_unregistered",
        "evidence_refs": [
            "artifacts/phase1_imports.json (orphan_routers: 'router' @ /pdf)",
            "artifacts/phase2_endpoint_mapping.json (3 BE_ONLY /pdf/*)",
        ],
        "delegated_to_spec": "pdf-render-worker",
        "severity": "P1",
    })

    # --- Domain: FE admin client
    matrix.append({
        "domain": "fe_admin_market_prices",
        "concept": "admin market-prices FE adapter",
        "canonical_source": "frontend/src/market-prices/marketPricesApi.ts",
        "canonical_writer": "admin panel components (MarketPricesTab, UpsertFormModal, BulkImportWizard)",
        "readers": [
            "hooks/useMarketPricesList.ts",
            "hooks/useUpsertMarketPrice.ts",
            "hooks/useBulkImportPreview.ts",
            "hooks/useBulkImportApply.ts",
            "hooks/useAuditHistory.ts",
        ],
        "deprecated": [
            "frontend/src/api.ts::getMarketPrices",
            "frontend/src/api.ts::getMarketPrice",
            "frontend/src/api.ts::upsertMarketPrice",
            "frontend/src/api.ts::lockMarketPrice",
        ],
        "migration_status": "dual_active",  # F-DUAL_FE P2
        "evidence_refs": [
            "artifacts/phase2_endpoint_mapping.json (dual_fe_client x3)",
        ],
        "delegated_to_spec": dup_by_id.get("F-DUAL_FE", {}).get("delegated_to_spec"),
        "severity": "P2",
    })

    return matrix


# ------------------------------------------------------------------------------
# Git arkeolojisi toplama
# ------------------------------------------------------------------------------
def gather_archaeology(imports_data: dict, a7: dict) -> list[dict]:
    """Odak setin her artifact'ı için introduced/last commit bilgisi."""
    records: list[dict] = []

    # Modüllerin "used_in_production" durumu — A6 + A7 ile türetilir
    module_status: dict[str, dict] = {m["module"]: m for m in imports_data.get("modules", [])}
    new_verdict = a7.get("new_validation_stack", {}).get("verdict", "UNKNOWN")

    def _used(file_rel: str) -> tuple[bool, str]:
        """Dosya → (used_in_production, reason)."""
        # Modül adını türet
        if file_rel.startswith("backend/"):
            mod = file_rel[len("backend/"):].replace("/", ".").removesuffix(".py")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
        else:
            mod = ""
        info = module_status.get(mod, {})
        status = info.get("status", "unknown")
        if status == "alive_from_main":
            return True, "alive_from_main (A6)"
        if status == "alive_from_tests_only":
            # Özel durum: invoice.validation.* DEAD verdict'i
            if mod.startswith("app.invoice.validation"):
                return False, f"alive_from_tests_only; A7 verdict={new_verdict}"
            return False, "alive_from_tests_only (A6)"
        if status == "orphan":
            return False, "orphan (A6)"
        if status == "dormant":
            return False, "dormant (A6, flag OFF)"
        return False, "unknown"

    # --- Tablolar (pickaxe)
    for tok in FOCUS_TABLES:
        intro = git_pickaxe_first(tok)
        last = git_pickaxe_last(tok)
        # Canonical vs legacy?
        role = {
            "hourly_market_prices":       "canonical PTF (+ SMF)",
            "monthly_yekdem_prices":      "canonical YEKDEM",
            "market_reference_prices":    "legacy (hem PTF hem YEKDEM eski rows)",
        }.get(tok, "unknown")
        # Üretimde kullanılıyor mu? Tüm üç tablo da canlı sayılır (A7 ref count>0)
        records.append({
            "artifact_type": "table",
            "artifact": tok,
            "role": role,
            "introduced_at": intro,
            "last_modified": last,
            "used_in_production": True,
            "usage_reason": "invoice flow transitive reference (A7 ref count>0)",
        })

    # --- Çekirdek dosyalar
    for fp in FOCUS_FILES_CORE:
        full = WORKSPACE_ROOT / fp
        intro = git_file_first_commit(fp)
        last = git_file_last_commit(fp)
        used, reason = _used(fp)
        # pdf_api için özel durum
        if fp == "backend/app/pdf_api.py":
            used = False
            reason = "orphan router; include_router çağrılmamış (A6)"
        records.append({
            "artifact_type": "module",
            "artifact": fp,
            "role": {
                "backend/app/validator.py": "legacy validator (canlı)",
                "backend/app/invoice/validation/__init__.py": "yeni stack paket girişi",
                "backend/app/invoice/validation/validator.py": "yeni stack validator",
                "backend/app/invoice/validation/enforcement.py": "yeni stack enforcement",
                "backend/app/invoice/validation/shadow.py": "yeni stack shadow hook",
                "backend/app/pdf_api.py": "orphan router (dead)",
            }.get(fp, ""),
            "introduced_at": intro,
            "last_modified": last,
            "used_in_production": used,
            "usage_reason": reason,
        })

    # --- Orphan modüller (ilk 9 fully-dead)
    orphan_files = _orphan_module_files(imports_data, limit=9)
    for fp in orphan_files:
        intro = git_file_first_commit(fp)
        last = git_file_last_commit(fp)
        used, reason = _used(fp)
        records.append({
            "artifact_type": "module",
            "artifact": fp,
            "role": "orphan (A6 fully-dead)",
            "introduced_at": intro,
            "last_modified": last,
            "used_in_production": used,
            "usage_reason": reason,
        })

    # Deterministik sıralama: tip → artifact adı
    records.sort(key=lambda r: (r["artifact_type"], r["artifact"]))
    return records


# ------------------------------------------------------------------------------
# Ana
# ------------------------------------------------------------------------------
def main() -> int:
    for p in (A2, A6, A7, A8):
        if not p.is_file():
            print(f"[HATA] Girdi eksik: {p}", file=sys.stderr)
            return 2

    imports_data = json.loads(A6.read_text(encoding="utf-8"))
    a7 = json.loads(A7.read_text(encoding="utf-8"))
    a8 = json.loads(A8.read_text(encoding="utf-8"))

    sot_matrix = build_sot_matrix(a7, a8)
    archaeology = gather_archaeology(imports_data, a7)

    # Özet sayımları
    tables_count = sum(1 for r in archaeology if r["artifact_type"] == "table")
    modules_count = sum(1 for r in archaeology if r["artifact_type"] == "module")
    with_intro = sum(1 for r in archaeology if r["introduced_at"])
    used_prod = sum(1 for r in archaeology if r["used_in_production"])

    artifact: dict[str, Any] = {
        "_meta": {
            "script": "07_sot_matrix_archaeology.py",
            "inputs": [_posix_rel(p) for p in (A2, A6, A7, A8)],
            "counts": {
                "sot_matrix_rows": len(sot_matrix),
                "archaeology_tables": tables_count,
                "archaeology_modules": modules_count,
                "with_introduced_at": with_intro,
                "used_in_production": used_prod,
            },
        },
        "sot_matrix": sot_matrix,
        "archaeology": archaeology,
    }

    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Stdout özeti -------------------------------------------------------
    print("=" * 78)
    print("A9 — SoT matrisi + git arkeolojisi")
    print("=" * 78)
    print()
    print("SoT matrisi (canonical kararlar):")
    for row in sot_matrix:
        sev = row.get("severity", "—")
        status = row.get("migration_status", "—")
        canonical = row.get("canonical_source") or "(yok — wire edilmemiş)"
        print(f"  [{sev}] {row['domain']:<28s} canonical={canonical}")
        print(f"         status={status}  → {row.get('delegated_to_spec')}")
    print()

    print("Git arkeolojisi:")
    print(f"  Tablolar        : {tables_count}")
    print(f"  Modüller        : {modules_count}")
    print(f"  introduced_at ✓ : {with_intro}/{len(archaeology)}")
    print(f"  üretimde        : {used_prod}/{len(archaeology)}")
    print()

    # Kritik satırları yazdır
    print("Kritik bulgular — timeline:")
    for r in archaeology:
        intro = r["introduced_at"] or {}
        intro_str = f"{intro.get('date', '????')}  {intro.get('sha', '')[:8]}" if intro else "—"
        last = r["last_modified"] or {}
        last_str = f"{last.get('date', '????')}" if last else "—"
        mark = "✓" if r["used_in_production"] else "✗"
        artifact_name = r["artifact"]
        if len(artifact_name) > 50:
            artifact_name = "..." + artifact_name[-47:]
        print(f"  [{mark}] {artifact_name:<50s} ilk={intro_str}  son={last_str}")
        print(f"      {r.get('role', '')}")
        if intro and intro.get("message"):
            msg = intro["message"]
            if len(msg) > 70:
                msg = msg[:67] + "..."
            print(f"      mesaj: {msg}")
    print()
    print(f"Artifact: {_posix_rel(OUT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
