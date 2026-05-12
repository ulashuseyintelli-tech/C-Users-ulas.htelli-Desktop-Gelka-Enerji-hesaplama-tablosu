"""
09_golden_baseline.py — B1 Golden baseline capture (R22)

Read-only (R16). Kanıt-temelli (R1). Deterministik, idempotent (payload seti sabit).

Amaç:
  PTF tekleştirmesi (ptf-sot-unification) başlamadan ÖNCE canlı backend'in
  30 senaryo için response hash'ini kilitle. Migration sonrası aynı 30 senaryo
  tekrar çalıştırılır; hash'ler değişmişse regresyon vardır.

Kapsam: 5 dönem × 2 tüketim profili × 3 endpoint = **30 snapshot**

  Dönemler (PTF veri durumunu temsil):
    2026-03  — canonical+legacy (en zengin coverage)
    2026-02  — canonical+legacy
    2026-01  — canonical+legacy (sınır)
    2026-04  — canonical+legacy (en son canonical)
    2025-12  — legacy-only (Hybrid-C → 404 market_data_not_found beklenir)

  Profiller (sabit, deterministik):
    low      — 50_000 kWh/ay (T1=25k T2=12.5k T3=12.5k)
    high     — 500_000 kWh/ay (T1=250k T2=125k T3=125k)

  Endpoint'ler:
    1) POST /api/pricing/analyze   (canonical path — hourly_market_prices)
    2) GET  /api/epias/prices/{period}   (read-only PTF/YEKDEM)
    3) POST /full-process           (legacy invoice flow — sabit PDF fixture ile)

Kullanım:
    python .kiro/specs/codebase-audit-cleanup/scripts/09_golden_baseline.py \
        --base-url http://127.0.0.1:8000 \
        --label pre-ptf-unification

Çıktı:
    baselines/<YYYY-MM-DD>_<label>_baseline.json
      - capture_meta: zaman, git sha, base URL, python version
      - snapshots: 30 satır — her biri için full response + SHA256 hash
      - summary: hash_count, error_count, expected_409_count

DoD:
  - 30 snapshot alındı
  - 2025-12 için 3/3 endpoint hatası (market_data_not_found) beklenen davranış
  - Dosya git'e commit edilir
"""

from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

# UTF-8 stdout (Windows cp1254 fix)
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
BASELINES_DIR = WORKSPACE_ROOT / "baselines"
BASELINES_DIR.mkdir(parents=True, exist_ok=True)


def _posix_rel(path: Path) -> str:
    try:
        rel = path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        rel = path
    return rel.as_posix()


# ------------------------------------------------------------------------------
# Senaryo matrisi (5 × 2 × 3 = 30)
# ------------------------------------------------------------------------------
PERIODS = [
    ("2026-03", "canonical+legacy"),
    ("2026-02", "canonical+legacy"),
    ("2026-01", "canonical+legacy"),
    ("2026-04", "canonical+legacy"),
    ("2025-12", "legacy-only (Hybrid-C 409 beklenir)"),
]

PROFILES = {
    "low":  {"t1_kwh": 25_000,  "t2_kwh": 12_500,  "t3_kwh": 12_500},   # ≈ 50k
    "high": {"t1_kwh": 250_000, "t2_kwh": 125_000, "t3_kwh": 125_000},  # ≈ 500k
}

# Sabit multiplier + imbalance params (determinism için)
FIXED_MULTIPLIER = 1.10
FIXED_IMBALANCE = {
    "forecast_error_rate": 0.05,
    "imbalance_cost_tl_per_mwh": 150.0,
    "smf_based_imbalance_enabled": False,
}


# ------------------------------------------------------------------------------
# HTTP helper (stdlib urllib — bağımlılık yok)
# ------------------------------------------------------------------------------
def _http_request(
    method: str,
    url: str,
    body: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict | str]:
    """Return (status_code, parsed_json_or_text). Hata durumunda body=error_text."""
    import urllib.request
    import urllib.error

    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        status = e.code
    except urllib.error.URLError as e:
        return 0, f"URLError: {e.reason}"
    except TimeoutError:
        return 0, "Timeout"

    try:
        return status, json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return status, raw


def _stable_hash(payload: Any) -> str:
    """Nesne hash'i — sort_keys + ensure_ascii=False ile deterministik."""
    if isinstance(payload, (dict, list)):
        s = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    else:
        s = str(payload)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(WORKSPACE_ROOT),
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


# ------------------------------------------------------------------------------
# Response sanitization — volatile alanları çıkar (hash deterministik olsun)
# ------------------------------------------------------------------------------
VOLATILE_KEYS = {
    # trace_id, timestamp, cache_hit, generated_at vb.
    "trace_id", "request_id", "timestamp", "captured_at", "generated_at",
    "cache_hit", "duration_ms", "latency_ms", "server_time",
}


def _sanitize(obj: Any) -> Any:
    """Volatile alanları recursive olarak çıkar."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    return obj


# ------------------------------------------------------------------------------
# Senaryo çalıştırıcılar
# ------------------------------------------------------------------------------
def run_pricing_analyze(
    base_url: str, period: str, profile_name: str, profile: dict,
) -> dict:
    """POST /api/pricing/analyze — canonical PTF path."""
    url = f"{base_url.rstrip('/')}/api/pricing/analyze"
    body = {
        "period": period,
        "multiplier": FIXED_MULTIPLIER,
        "dealer_commission_pct": 0,
        "imbalance_params": FIXED_IMBALANCE,
        "use_template": False,
        **profile,
        "voltage_level": "og",
    }
    status, resp = _http_request("POST", url, body=body)
    sanitized = _sanitize(resp) if isinstance(resp, dict) else resp
    return {
        "endpoint": "POST /api/pricing/analyze",
        "period": period,
        "profile": profile_name,
        "request_body": body,
        "status_code": status,
        "response_sanitized": sanitized,
        "response_hash": _stable_hash(sanitized),
        "error": None if status < 400 else (
            sanitized.get("detail") if isinstance(sanitized, dict) else sanitized
        ),
    }


def run_epias_prices(base_url: str, period: str, profile_name: str, _: dict) -> dict:
    """GET /api/epias/prices/{period} — read-only, profile irrelevant.

    Not: profile_name yalnızca kayıt adı için; endpoint profile bağımsız.
    """
    url = f"{base_url.rstrip('/')}/api/epias/prices/{period}?auto_fetch=false"
    status, resp = _http_request("GET", url)
    sanitized = _sanitize(resp) if isinstance(resp, dict) else resp
    return {
        "endpoint": "GET /api/epias/prices/{period}",
        "period": period,
        "profile": profile_name,
        "request_url": url,
        "status_code": status,
        "response_sanitized": sanitized,
        "response_hash": _stable_hash(sanitized),
        "error": None if status < 400 else (
            sanitized.get("detail") if isinstance(sanitized, dict) else sanitized
        ),
    }


def run_full_process(
    base_url: str, period: str, profile_name: str, profile: dict,
) -> dict:
    """POST /full-process — legacy invoice flow.

    Bu endpoint multipart/form-data + bir fatura görseli ister. Baseline için
    gerçek bir invoice gönderemeyiz (LLM çağrısı + stochastic output). Bu
    yüzden baseline'da sadece **endpoint availability** ve **400/422 şeması**
    doğrulanır: boş body gönder, hata şemasının sabit kaldığını kontrol et.

    Bu bir tam response hash değil; "error-shape snapshot"tır. Migration
    sonrası aynı boş çağrı aynı hata şemasını dönmeli.
    """
    url = f"{base_url.rstrip('/')}/full-process"
    # Boş body — 422 Unprocessable Entity bekleriz (fatura yok)
    status, resp = _http_request("POST", url, body={})
    sanitized = _sanitize(resp) if isinstance(resp, dict) else resp
    # Pydantic validation error şemasındaki "input" volatile olabilir;
    # sadece error type + loc + msg'i yakalayalım (input değerini at):
    if isinstance(sanitized, dict) and "detail" in sanitized and isinstance(sanitized["detail"], list):
        sanitized["detail"] = [
            {k: v for k, v in err.items() if k in ("type", "loc", "msg")}
            for err in sanitized["detail"]
        ]
    return {
        "endpoint": "POST /full-process (error-shape only)",
        "period": period,          # scenario key; body'de kullanılmadı
        "profile": profile_name,   # scenario key
        "note": "Error-shape snapshot — full invoice processing skipped (stochastic LLM).",
        "status_code": status,
        "response_sanitized": sanitized,
        "response_hash": _stable_hash(sanitized),
        "error": None,
    }


ENDPOINTS = [
    ("analyze",       run_pricing_analyze),
    ("epias_prices",  run_epias_prices),
    ("full_process",  run_full_process),
]


# ------------------------------------------------------------------------------
# Ana
# ------------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Golden baseline capture (B1, R22)")
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000",
        help="Backend base URL (varsayılan: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--label", default="pre-ptf-unification",
        help="Snapshot etiketi — dosya adına eklenir",
    )
    parser.add_argument(
        "--date-override", default=None,
        help="YYYY-MM-DD — dosya adı için; varsayılan: bugün (deterministik rerun için)",
    )
    args = parser.parse_args()

    # Canlı backend sağlıklı mı?
    status, resp = _http_request("GET", f"{args.base_url.rstrip('/')}/health")
    if status != 200:
        print(f"[HATA] Backend erişilemez: {args.base_url}/health → {status} {resp}",
              file=sys.stderr)
        print("       Backend'i başlatın: `uvicorn app.main:app --host 127.0.0.1 --port 8000`",
              file=sys.stderr)
        return 2

    # Git SHA — regresyon karşılaştırması için referans
    git_sha = _git_sha()

    # Snapshot döngüsü
    snapshots: list[dict] = []
    total = len(PERIODS) * len(PROFILES) * len(ENDPOINTS)
    idx = 0
    for period, period_note in PERIODS:
        for profile_name, profile in PROFILES.items():
            for ep_name, ep_fn in ENDPOINTS:
                idx += 1
                print(f"[{idx:>2}/{total}] {period:<8} {profile_name:<4} "
                      f"{ep_name:<14s} ... ", end="", flush=True)
                rec = ep_fn(args.base_url, period, profile_name, profile)
                rec["_scenario_id"] = f"{period}::{profile_name}::{ep_name}"
                rec["_period_note"] = period_note
                snapshots.append(rec)
                print(f"HTTP {rec['status_code']}  hash={rec['response_hash'][:16]}")

    # Özet sayımlar
    errors = [s for s in snapshots if s.get("error")]
    expected_409 = [
        s for s in snapshots
        if s["period"] == "2025-12" and s["status_code"] in (404, 409)
    ]

    # Artifact
    today = args.date_override or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_name = f"{today}_{args.label}_baseline.json"
    out_path = BASELINES_DIR / out_name

    artifact = {
        "_meta": {
            "script": "09_golden_baseline.py",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "base_url": args.base_url,
            "label": args.label,
            "git_sha": git_sha,
            "python_version": sys.version.split()[0],
            "scenario_count": len(snapshots),
            "error_count": len(errors),
            "expected_409_count": len(expected_409),
        },
        "matrix": {
            "periods": [{"period": p, "note": n} for p, n in PERIODS],
            "profiles": PROFILES,
            "endpoints": [name for name, _ in ENDPOINTS],
            "multiplier": FIXED_MULTIPLIER,
            "imbalance_params": FIXED_IMBALANCE,
        },
        "snapshots": snapshots,
    }
    out_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )

    # Stdout özeti
    print()
    print("=" * 78)
    print("B1 — Golden baseline capture")
    print("=" * 78)
    print(f"Toplam senaryo       : {len(snapshots)}")
    print(f"Hatalı snapshot      : {len(errors)}")
    print(f"Beklenen 2025-12 404 : {len(expected_409)} / 6  (2 profil × 3 endpoint)")
    print()
    print(f"Artifact: baselines/{out_name}")
    print()
    print("Sıradaki adım: bu dosyayı git'e commit et.")
    print("  git add baselines/{out_name}")
    print("  git commit -m 'baseline: pre-ptf-unification (30 snapshot, git sha "
          f"{git_sha[:8]})'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
