"""Test PTF/YEKDEM otomatik çekme"""
import requests
import os

# Enerjisa faturası ile test (daha basit dosya adı)
invoice_path = r'C:\Users\ulas.htelli\Desktop\Gelka Enerji hesaplama tablosu\Fatura örnekler\ES02025001847342.pdf'
with open(invoice_path, 'rb') as f:
    r = requests.post(
        'http://localhost:8000/full-process',
        files={'file': ('ck.pdf', f, 'application/pdf')},
        params={'use_reference_prices': True}
    )

data = r.json()
print('Status:', r.status_code)

if 'calculation' in data and data['calculation']:
    calc = data['calculation']
    print('\n=== HESAPLAMA SONUCU ===')
    print(f"Dönem: {calc.get('meta_pricing_period')}")
    print(f"PTF: {calc.get('meta_ptf_tl_per_mwh')} TL/MWh")
    print(f"YEKDEM: {calc.get('meta_yekdem_tl_per_mwh')} TL/MWh")
    print(f"Fiyat Kaynağı: {calc.get('meta_pricing_source')}")
    print(f"Dağıtım Kaynağı: {calc.get('meta_distribution_source')}")
    print(f"Dağıtım Tarife: {calc.get('meta_distribution_tariff_key')}")
    print(f"Teklif Toplam: {calc.get('offer_total_with_vat_tl')} TL")
    savings_pct = calc.get('savings_ratio', 0) * 100
    print(f"Tasarruf: {calc.get('difference_incl_vat_tl')} TL ({savings_pct:.1f}%)")
elif 'calculation_error' in data and data['calculation_error']:
    print('\nHESAPLAMA HATASI:', data['calculation_error'])
else:
    print('\nHesaplama yapılamadı')
    if 'validation' in data:
        print('Validation errors:', data['validation'].get('errors'))
