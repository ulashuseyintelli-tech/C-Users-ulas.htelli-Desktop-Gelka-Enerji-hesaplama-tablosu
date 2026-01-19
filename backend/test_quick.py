#!/usr/bin/env python3
"""Quick test script for invoice analysis."""
import requests
import json
import os
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"

# Health check
print("=" * 50)
print("HEALTH CHECK")
print("=" * 50)
response = requests.get(f"{BASE_URL}/health/ready")
print(f"Status: {response.status_code}")
if response.status_code in [200, 503]:
    data = response.json()
    print(f"Ready: {data.get('status')}")
    print(f"Config Hash: {data.get('config_hash')}")
    print(f"Pilot Enabled: {data.get('pilot', {}).get('enabled')}")

# Test multiple invoices
invoice_dir = Path("../Fatura örnekler")
pdf_files = list(invoice_dir.glob("*.pdf"))[:5]  # First 5 PDFs

print(f"\n{'=' * 50}")
print(f"TESTING {len(pdf_files)} INVOICES")
print("=" * 50)

results = []
for pdf_file in pdf_files:
    print(f"\n📄 {pdf_file.name}")
    print("-" * 40)
    
    with open(pdf_file, "rb") as f:
        files = {"file": (pdf_file.name, f, "application/pdf")}
        response = requests.post(f"{BASE_URL}/analyze-invoice", files=files)
        
        result = {
            "file": pdf_file.name,
            "status": response.status_code,
            "vendor": None,
            "period": None,
            "line_items": 0,
            "ready": False
        }
        
        if response.status_code == 200:
            data = response.json()
            ext = data.get("extraction", {})
            val = data.get("validation", {})
            
            result["vendor"] = ext.get("vendor")
            result["period"] = ext.get("invoice_period")
            result["line_items"] = len(ext.get("line_items", []))
            result["ready"] = val.get("is_ready_for_pricing", False)
            
            print(f"  ✅ Vendor: {result['vendor']}")
            print(f"  📅 Period: {result['period']}")
            print(f"  📊 Line Items: {result['line_items']}")
            print(f"  💰 Ready: {result['ready']}")
        else:
            error = response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text
            print(f"  ❌ Error: {str(error)[:100]}")
        
        results.append(result)

# Summary
print(f"\n{'=' * 50}")
print("SUMMARY")
print("=" * 50)
success = sum(1 for r in results if r["status"] == 200)
print(f"Total: {len(results)}")
print(f"Success: {success}")
print(f"Failed: {len(results) - success}")

vendors = set(r["vendor"] for r in results if r["vendor"])
print(f"Vendors detected: {', '.join(vendors) if vendors else 'None'}")

print("\n" + "=" * 50)
print("TEST COMPLETE")
print("=" * 50)
