"""Debug test - tam extraction sonucunu göster"""
import requests
import json

API_URL = "http://localhost:8000"

def test_invoice(filepath: str):
    """Tek fatura test et ve tam sonucu göster"""
    print(f"\nTesting: {filepath}")
    
    with open(filepath, 'rb') as f:
        files = {'file': (filepath.split('/')[-1], f, 'application/pdf')}
        response = requests.post(
            f"{API_URL}/full-process",
            files=files,
            params={'use_reference_prices': 'true'}
        )
    
    if response.status_code == 200:
        data = response.json()
        ext = data.get('extraction', {})
        
        print("\n=== INVOICE TOTAL ===")
        print(f"invoice_total_with_vat_tl: {ext.get('invoice_total_with_vat_tl')}")
        
        print("\n=== CHARGES ===")
        charges = ext.get('charges', {})
        print(json.dumps(charges, indent=2, ensure_ascii=False))
        
        print("\n=== RAW BREAKDOWN ===")
        raw = ext.get('raw_breakdown', {})
        print(json.dumps(raw, indent=2, ensure_ascii=False))
        
        print("\n=== LINE ITEMS ===")
        for item in ext.get('line_items', []):
            print(f"  {item.get('label')}: {item.get('qty')} @ {item.get('unit_price')} = {item.get('amount_tl')} TL")
        
        print("\n=== CALCULATION ===")
        calc = data.get('calculation', {})
        print(f"current_total_with_vat_tl: {calc.get('current_total_with_vat_tl')}")
        print(f"current_energy_tl: {calc.get('current_energy_tl')}")
        print(f"current_distribution_tl: {calc.get('current_distribution_tl')}")
        print(f"current_vat_tl: {calc.get('current_vat_tl')}")
    else:
        print(f"Error: {response.text}")

if __name__ == "__main__":
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fatura_dir = os.path.join(base_dir, "Fatura örnekler")
    
    ck_file = None
    for f in os.listdir(fatura_dir):
        if "BBE2025" in f and "CK" in f:
            ck_file = os.path.join(fatura_dir, f)
            break
    
    if ck_file:
        test_invoice(ck_file)
