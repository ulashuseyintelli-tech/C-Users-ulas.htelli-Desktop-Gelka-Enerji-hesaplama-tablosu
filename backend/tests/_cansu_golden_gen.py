"""Generate golden snapshot from real Cansu Excel file."""
import json
import sys
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.recon.parser import parse_excel
from app.recon.splitter import split_by_month, validate_period_completeness
from app.recon.classifier import classify_period_records

EXCEL_PATH = Path(__file__).parent.parent.parent / "Cansu Saatlik Tuketim Ocak-Nisan 2026.xlsx"

data = EXCEL_PATH.read_bytes()
result = parse_excel(data)

groups = split_by_month(result.records)

print("=" * 90)
print(f"Format: {result.format_detected.value}")
print(f"Total rows: {result.total_rows}, Success: {result.successful_rows}, Failed: {result.failed_rows}")
print(f"Multiplier metadata: {result.multiplier_metadata}")
print(f"Warnings: {result.warnings}")
print("=" * 90)
print(f"{'Period':<10} {'Total kWh':>12} {'T1 kWh':>10} {'T2 kWh':>10} {'T3 kWh':>10} {'T1%':>6} {'T2%':>6} {'T3%':>6} {'Miss':>5} {'Dup':>4}")
print("-" * 90)

golden = {
    "format_detected": result.format_detected.value,
    "total_rows": result.total_rows,
    "successful_rows": result.successful_rows,
    "failed_rows": result.failed_rows,
    "multiplier_metadata": None,
    "periods": {},
}

for period, records in groups.items():
    tz = classify_period_records(records)
    stats = validate_period_completeness(period, records)
    
    print(f"{period:<10} {float(tz.total_kwh):>12.3f} {float(tz.t1_kwh):>10.3f} {float(tz.t2_kwh):>10.3f} {float(tz.t3_kwh):>10.3f} {float(tz.t1_pct):>6.2f} {float(tz.t2_pct):>6.2f} {float(tz.t3_pct):>6.2f} {len(stats.missing_hours):>5} {len(stats.duplicate_hours):>4}")
    
    golden["periods"][period] = {
        "record_count": len(records),
        "total_kwh": float(tz.total_kwh),
        "t1_kwh": float(tz.t1_kwh),
        "t2_kwh": float(tz.t2_kwh),
        "t3_kwh": float(tz.t3_kwh),
        "t1_pct": float(tz.t1_pct),
        "t2_pct": float(tz.t2_pct),
        "t3_pct": float(tz.t3_pct),
        "missing_hours": len(stats.missing_hours),
        "duplicate_hours": len(stats.duplicate_hours),
        "has_gaps": stats.has_gaps,
    }

# Save golden snapshot
snapshot_path = Path(__file__).parent / "fixtures" / "cansu_golden_snapshot.json"
snapshot_path.parent.mkdir(exist_ok=True)
snapshot_path.write_text(json.dumps(golden, indent=2, ensure_ascii=False))
print(f"\nGolden snapshot saved: {snapshot_path}")
