#!/usr/bin/env python
"""Test HTML invoice analysis"""
import os
import sys
import requests

# Find the HTML file
html_path = os.path.join(os.path.dirname(__file__), '..', '..', 'Fatura örnekler', 'Çelik Halat Ankara.HTML')

if not os.path.exists(html_path):
    print(f"HTML file not found: {html_path}")
    sys.exit(1)

print(f"Testing with: {os.path.basename(html_path)}")

# Call API
with open(html_path, 'rb') as f:
    response = requests.post(
        'http://localhost:8000/full-process',
        files={'file': (os.path.basename(html_path), f, 'text/html')},
        params={
            'weighted_ptf_tl_per_mwh': 2844,  # Excel'deki değer
            'yekdem_tl_per_mwh': 364,
            'agreement_multiplier': 1.01
        }
    )

print(f"Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    ext = data.get('extraction', {})
    calc = data.get('calculation', {})
    
    print(f"\n=== Extraction ===")
    print(f"Vendor: {ext.get('vendor')}")
    print(f"Period: {ext.get('invoice_period')}")
    print(f"Consumption: {ext.get('consumption_kwh', {}).get('value')} kWh")
    print(f"Unit Price: {ext.get('current_active_unit_price_tl_per_kwh', {}).get('value')} TL/kWh")
    print(f"Invoice Total: {ext.get('invoice_total_with_vat_tl', {}).get('value')} TL")
    
    # Line items
    line_items = ext.get('line_items', [])
    if line_items:
        print(f"\n=== Line Items ({len(line_items)}) ===")
        for item in line_items[:5]:
            print(f"  - {item.get('label')}: {item.get('qty')} kWh x {item.get('unit_price')} = {item.get('amount_tl')} TL")
    
    if calc:
        print(f"\n=== Calculation ===")
        print(f"Current Total: {calc.get('current_total_with_vat_tl')} TL")
        print(f"Offer Total: {calc.get('offer_total_with_vat_tl')} TL")
        print(f"Savings: {calc.get('savings_ratio', 0) * 100:.1f}%")
else:
    print(f"Error: {response.text}")
