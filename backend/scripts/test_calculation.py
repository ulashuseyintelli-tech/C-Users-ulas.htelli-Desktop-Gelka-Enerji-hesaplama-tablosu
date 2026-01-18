#!/usr/bin/env python
"""Test calculation with CK Boğaziçi invoice"""
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

print(f"Testing with: {os.path.basename(pdf_path)}")

# Call API - use full-process endpoint
with open(pdf_path, 'rb') as f:
    response = requests.post(
        'http://localhost:8000/full-process',
        files={'file': (os.path.basename(pdf_path), f, 'application/pdf')},
        params={
            'weighted_ptf_tl_per_mwh': 2974.1,
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
    print(f"Consumption: {ext.get('consumption_kwh', {}).get('value')} kWh")
    print(f"Unit Price: {ext.get('current_active_unit_price_tl_per_kwh', {}).get('value')} TL/kWh")
    print(f"Invoice Total (from PDF): {ext.get('invoice_total_with_vat_tl', {}).get('value')} TL")
    
    rb = ext.get('raw_breakdown', {})
    print(f"\n=== Raw Breakdown (from PDF) ===")
    print(f"Energy: {rb.get('energy_total_tl', {}).get('value')} TL")
    print(f"Distribution: {rb.get('distribution_total_tl', {}).get('value')} TL")
    print(f"BTV: {rb.get('btv_tl', {}).get('value')} TL")
    print(f"VAT: {rb.get('vat_tl', {}).get('value')} TL")
    
    print(f"\n=== Calculation (Mevcut Fatura) ===")
    print(f"Energy: {calc.get('current_energy_tl')} TL")
    print(f"Distribution: {calc.get('current_distribution_tl')} TL")
    print(f"BTV: {calc.get('current_btv_tl')} TL")
    print(f"VAT Matrah: {calc.get('current_vat_matrah_tl')} TL")
    print(f"VAT: {calc.get('current_vat_tl')} TL")
    print(f"TOTAL: {calc.get('current_total_with_vat_tl')} TL")
    print(f"Extra Items: {calc.get('current_extra_items_tl')} TL")
    
    print(f"\n=== Expected vs Actual ===")
    expected = 593738.26
    actual = calc.get('current_total_with_vat_tl', 0)
    print(f"Expected: {expected} TL")
    print(f"Actual: {actual} TL")
    print(f"Difference: {actual - expected} TL")
    print(f"Match: {'✓' if abs(actual - expected) < 1 else '✗'}")
else:
    print(f"Error: {response.text}")
