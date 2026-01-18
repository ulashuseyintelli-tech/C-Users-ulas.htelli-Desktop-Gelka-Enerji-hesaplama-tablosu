import requests
import os

# Dosyayı bul
pdf_path = None
for f in os.listdir('Fatura örnekler'):
    if 'BBE' in f and 'CK' in f:
        pdf_path = os.path.join('Fatura örnekler', f)
        break

print(f'PDF: {pdf_path}')

with open(pdf_path, 'rb') as f:
    files = {'file': (os.path.basename(pdf_path), f, 'application/pdf')}
    data = {
        'weighted_ptf_tl_per_mwh': 2974.1,
        'yekdem_tl_per_mwh': 364.0,
        'agreement_multiplier': 1.01
    }
    
    print('İstek gönderiliyor...')
    response = requests.post('http://localhost:8000/full-process', files=files, data=data, timeout=120)
    
    print(f'Status: {response.status_code}')
    if response.status_code == 200:
        result = response.json()
        ext = result.get('extraction', {})
        print(f"Consumption: {ext.get('consumption_kwh', {}).get('value')} kWh")
        print(f"Total: {ext.get('invoice_total_with_vat_tl', {}).get('value')} TL")
    else:
        print(f'Error: {response.text[:1000]}')
