"""Cache temizleyip yeniden test et"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Cache'i temizle
from app.extractor import clear_extraction_cache
cleared = clear_extraction_cache()
print(f"Cache temizlendi: {cleared} kayıt silindi")

# Test et
import requests
import json

API_URL = "http://localhost:8000"

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fatura_dir = os.path.join(base_dir, "Fatura örnekler")

ck_file = None
for f in os.listdir(fatura_dir):
    if "BBE2025" in f and "CK" in f:
        ck_file = os.path.join(fatura_dir, f)
        break

if ck_file:
    print(f"\nTesting: {ck_file}")
    
    with open(ck_file, 'rb') as f:
        files = {'file': (os.path.basename(ck_file), f, 'application/pdf')}
        response = requests.post(
            f"{API_URL}/full-process",
            files=files,
            params={'use_reference_prices': 'true', 'debug': 'true'}
        )
    
    if response.status_code == 200:
        data = response.json()
        ext = data.get('extraction', {})
        
        print(f"\n=== INVOICE TOTAL ===")
        print(f"invoice_total_with_vat_tl: {ext.get('invoice_total_with_vat_tl')}")
        
        print(f"\n=== RAW BREAKDOWN ===")
        raw = ext.get('raw_breakdown', {})
        if raw:
            print(f"energy_total_tl: {raw.get('energy_total_tl')}")
            print(f"distribution_total_tl: {raw.get('distribution_total_tl')}")
            print(f"btv_tl: {raw.get('btv_tl')}")
            print(f"vat_tl: {raw.get('vat_tl')}")
        else:
            print("null")
        
        print(f"\n=== CHARGES ===")
        charges = ext.get('charges', {})
        if charges:
            print(f"total_amount: {charges.get('total_amount')}")
            print(f"vat_amount: {charges.get('vat_amount')}")
        else:
            print("null")
        
        print(f"\n=== LINE ITEMS ===")
        for item in ext.get('line_items', []):
            print(f"  {item.get('label')}: {item.get('qty')} @ {item.get('unit_price')} = {item.get('amount_tl')} TL")
        
        print(f"\n=== CALCULATION ===")
        calc = data.get('calculation', {})
        if calc:
            print(f"current_total_with_vat_tl: {calc.get('current_total_with_vat_tl')}")
            print(f"current_energy_tl: {calc.get('current_energy_tl')}")
            print(f"current_distribution_tl: {calc.get('current_distribution_tl')}")
            print(f"current_vat_tl: {calc.get('current_vat_tl')}")
            print(f"offer_total_with_vat_tl: {calc.get('offer_total_with_vat_tl')}")
            print(f"savings_ratio: {calc.get('savings_ratio', 0)*100:.1f}%")
        
        print(f"\n=== DEBUG META ===")
        debug = data.get('debug_meta', {})
        if debug:
            print(f"warnings: {debug.get('warnings', [])}")
            print(f"errors: {debug.get('errors', [])}")
    else:
        print(f"Error: {response.text}")
else:
    print("CK Boğaziçi faturası bulunamadı!")
