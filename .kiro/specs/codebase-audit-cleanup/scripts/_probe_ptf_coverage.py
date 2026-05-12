"""Smoke probe — PTF kaynak karşılaştırması + canlı API çağrısı (R4, R7).

hourly_market_prices vs market_reference_prices dönem kapsamını çıkarır,
backend çalışıyorsa /api/pricing/analyze endpoint'ini 3 farklı dönemle
çağırıp davranışı kanıtlar.
"""
from __future__ import annotations
import json
import sqlite3
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
DB = ROOT / "backend" / "gelka_enerji.db"
API_BASE = "http://127.0.0.1:8000"

print("=" * 78)
print("PROBE — PTF KAYNAK KAPSAMI")
print("=" * 78)

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = conn.cursor()

hourly = [r[0] for r in cur.execute(
    "SELECT DISTINCT period FROM hourly_market_prices ORDER BY period"
)]
monthly_ref = [r[0] for r in cur.execute(
    "SELECT DISTINCT period FROM market_reference_prices ORDER BY period"
)]
mon_yek = [r[0] for r in cur.execute(
    "SELECT DISTINCT period FROM monthly_yekdem_prices ORDER BY period"
)]

print(f"\nhourly_market_prices (saatlik PTF/SMF):    {len(hourly)} dönem")
print(f"  → {hourly}")
print(f"\nmarket_reference_prices (aylık PTF/YEKDEM): {len(monthly_ref)} dönem")
print(f"  → İlk 5: {monthly_ref[:5]}  Son 5: {monthly_ref[-5:]}")
print(f"\nmonthly_yekdem_prices (YEKDEM canonical):   {len(mon_yek)} dönem")
print(f"  → {mon_yek}")

# Üç küme karşılaştırması
print("\n--- Küme analizi ---")
set_hourly = set(hourly)
set_monthly = set(monthly_ref)
set_yek = set(mon_yek)

only_monthly = sorted(set_monthly - set_hourly)
only_hourly  = sorted(set_hourly - set_monthly)
both = sorted(set_hourly & set_monthly)

print(f"hourly ∩ monthly (saatlik + aylık PTF iki kaynakta):   {both}")
print(f"monthly − hourly (sadece aylık PTF var, saatlik YOK): {len(only_monthly)} dönem")
if only_monthly:
    print(f"  → Örnek: {only_monthly[:10]}{' …' if len(only_monthly) > 10 else ''}")
print(f"hourly − monthly (sadece saatlik PTF var):             {only_hourly}")

conn.close()

# Canlı API — 3 dönem
print("\n" + "=" * 78)
print("CANLI API — /api/pricing/analyze")
print("=" * 78)

def call(period: str) -> dict:
    """POST /api/pricing/analyze — template tüketim profiliyle."""
    body = json.dumps({
        "period": period,
        "multiplier": 1.05,
        "use_template": True,
        "template_name": "düz_ortalama",
        "template_monthly_kwh": 100000,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}/api/pricing/analyze",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"status": resp.status, "body": json.loads(resp.read())}
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
        except Exception:
            body = {"raw": "<unparseable>"}
        return {"status": e.code, "body": body}
    except urllib.error.URLError as e:
        return {"status": -1, "error": f"{type(e).__name__}: {e}"}
    except Exception as e:
        return {"status": -2, "error": f"{type(e).__name__}: {e}"}


for p in ["2026-01", "2025-12", "2024-06"]:
    print(f"\n--- POST /api/pricing/analyze period={p} ---")
    r = call(p)
    print(f"status: {r.get('status')}")
    if r.get("status") == -1:
        print(f"  (backend erişilemiyor: {r.get('error')})")
        continue
    body = r.get("body", {})
    if r.get("status") == 200:
        # Özet
        wp = body.get("weighted_prices", {})
        sc = body.get("supplier_cost_summary", {})
        warns = body.get("warnings", [])
        print(f"  weighted_ptf_tl_per_mwh: {wp.get('weighted_ptf_tl_per_mwh')}")
        print(f"  arithmetic_avg_ptf:      {wp.get('arithmetic_avg_ptf')}")
        print(f"  matched_hours:           {wp.get('matched_hours')}")
        print(f"  yekdem_tl_per_mwh:       {sc.get('yekdem_tl_per_mwh')}")
        print(f"  warnings ({len(warns)}): {[w.get('type') for w in warns]}")
    else:
        print(f"  body: {json.dumps(body, ensure_ascii=False)[:300]}")
