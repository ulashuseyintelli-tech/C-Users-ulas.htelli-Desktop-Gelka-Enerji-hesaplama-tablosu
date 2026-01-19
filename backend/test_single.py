"""Tek fatura test scripti - debug için"""
import requests
import json
import sys

API_URL = "http://localhost:8000"

def test_invoice(filepath: str):
    """Tek fatura test et ve sonucu göster"""
    print(f"\n{'='*60}")
    print(f"Testing: {filepath}")
    print('='*60)
    
    try:
        with open(filepath, 'rb') as f:
            files = {'file': (filepath.split('/')[-1], f, 'application/pdf')}
            response = requests.post(
                f"{API_URL}/full-process",
                files=files,
                params={'use_reference_prices': 'true'}
            )
        
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            # Extraction sonuçları
            ext = data.get('extraction', {})
            print(f"\n--- EXTRACTION ---")
            print(f"Vendor: {ext.get('vendor')}")
            print(f"Period: {ext.get('invoice_period')}")
            print(f"Consumption: {ext.get('consumption_kwh', {}).get('value')} kWh")
            print(f"Unit Price: {ext.get('current_active_unit_price_tl_per_kwh', {}).get('value')} TL/kWh")
            print(f"Distribution: {ext.get('distribution_unit_price_tl_per_kwh', {}).get('value')} TL/kWh")
            print(f"Invoice Total: {ext.get('invoice_total_with_vat_tl', {}).get('value')} TL")
            
            # Charges kontrolü
            charges = ext.get('charges', {})
            if charges:
                print(f"\n--- CHARGES ---")
                print(f"Total Amount: {charges.get('total_amount', {}).get('value')} TL")
                print(f"VAT Amount: {charges.get('vat_amount', {}).get('value')} TL")
            
            # Raw breakdown kontrolü
            raw = ext.get('raw_breakdown', {})
            if raw:
                print(f"\n--- RAW BREAKDOWN ---")
                print(f"Energy Total: {raw.get('energy_total_tl', {}).get('value')} TL")
                print(f"Distribution Total: {raw.get('distribution_total_tl', {}).get('value')} TL")
                print(f"BTV: {raw.get('btv_tl', {}).get('value')} TL")
                print(f"VAT: {raw.get('vat_tl', {}).get('value')} TL")
            
            # Line items
            line_items = ext.get('line_items', [])
            if line_items:
                print(f"\n--- LINE ITEMS ({len(line_items)}) ---")
                for item in line_items[:5]:
                    print(f"  {item.get('label')}: {item.get('qty')} {item.get('unit')} @ {item.get('unit_price')} = {item.get('amount_tl')} TL")
            
            # Calculation sonuçları
            calc = data.get('calculation')
            if calc:
                print(f"\n--- CALCULATION ---")
                print(f"Current Total: {calc.get('current_total_with_vat_tl')} TL")
                print(f"Offer Total: {calc.get('offer_total_with_vat_tl')} TL")
                print(f"Savings: {calc.get('savings_ratio', 0)*100:.1f}%")
            else:
                print(f"\n--- CALCULATION ERROR ---")
                print(data.get('calculation_error', 'Unknown error'))
            
            # Validation
            val = data.get('validation', {})
            if val.get('errors'):
                print(f"\n--- ERRORS ---")
                for err in val['errors']:
                    print(f"  ❌ {err}")
            if val.get('warnings'):
                print(f"\n--- WARNINGS ---")
                for warn in val['warnings'][:3]:  # İlk 3 uyarı
                    print(f"  ⚠️ {warn}")
                    
        else:
            print(f"Error: {response.text}")
            
    except Exception as e:
        print(f"Exception: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import os
    # Test dosyası - absolute path kullan
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Fatura klasöründeki dosyaları listele
    fatura_dir = os.path.join(base_dir, "Fatura örnekler")
    print(f"Fatura klasörü: {fatura_dir}")
    print(f"Dosyalar:")
    for f in os.listdir(fatura_dir):
        print(f"  - {f}")
    
    # CK Boğaziçi faturasını bul
    ck_file = None
    for f in os.listdir(fatura_dir):
        if "BBE2025" in f and "CK" in f:
            ck_file = os.path.join(fatura_dir, f)
            break
    
    if ck_file:
        print(f"\nCK Boğaziçi faturası bulundu: {ck_file}")
        test_invoice(ck_file)
    else:
        print("CK Boğaziçi faturası bulunamadı!")
