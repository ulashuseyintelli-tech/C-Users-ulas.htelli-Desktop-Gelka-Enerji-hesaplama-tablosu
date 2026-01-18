#!/usr/bin/env python
"""Test API endpoint with CK Boğaziçi invoice"""
import os
import sys
import requests

# Find the PDF
pdf_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'Fatura örnekler')
pdf_path = None
for f in os.listdir(pdf_dir):
    if 'BBE' in f and 'CK' in f:
        pdf_path = os.path.join(pdf_dir, f)
        break

if not pdf_path:
    print("PDF not found!")
    sys.exit(1)

print(f"Testing with: {os.path.basename(pdf_path)}")

# Call API
with open(pdf_path, 'rb') as f:
    response = requests.post(
        'http://localhost:8000/analyze-invoice',
        files={'file': (os.path.basename(pdf_path), f, 'application/pdf')}
    )

print(f"Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    ext = data.get('extraction', {})
    print(f"\n=== Extraction Results ===")
    print(f"Vendor: {ext.get('vendor')}")
    print(f"Consumption: {ext.get('consumption_kwh', {}).get('value')} kWh")
    print(f"Unit Price: {ext.get('current_active_unit_price_tl_per_kwh', {}).get('value')} TL/kWh")
    print(f"Total: {ext.get('invoice_total_with_vat_tl', {}).get('value')} TL")
    
    # Validation
    val = data.get('validation', {})
    print(f"\n=== Validation ===")
    print(f"Is Ready: {val.get('is_ready_for_pricing')}")
    print(f"Missing Fields: {val.get('missing_fields')}")
    if val.get('errors'):
        print(f"Errors: {val.get('errors')}")
    if val.get('warnings'):
        print(f"Warnings: {val.get('warnings')}")
    
    sanity = val.get('sanity_check', {})
    print(f"\n=== Sanity Check ===")
    print(f"Energy Est: {sanity.get('energy_est_tl')} TL")
    print(f"Dist Est: {sanity.get('dist_est_tl')} TL")
    print(f"BTV Est: {sanity.get('btv_est_tl')} TL")
    print(f"VAT Est: {sanity.get('vat_est_tl')} TL")
    print(f"Total Est: {sanity.get('total_est_tl')} TL")
    print(f"Invoice Total: {sanity.get('invoice_total_with_vat_tl')} TL")
    print(f"Delta Ratio: {sanity.get('delta_ratio')}%")
else:
    print(f"Error: {response.text}")
