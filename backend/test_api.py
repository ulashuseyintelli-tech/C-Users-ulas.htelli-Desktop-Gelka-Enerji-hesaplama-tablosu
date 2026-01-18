import requests
import time

pdf_path = r'C:\Users\ulas.htelli\Desktop\Gelka Enerji hesaplama tablosu\Fatura örnekler\PBA2025000262749.pdf'

print('Testing /full-process endpoint...')
start = time.time()

with open(pdf_path, 'rb') as f:
    files = {'file': ('test.pdf', f, 'application/pdf')}
    data = {
        'weighted_ptf_tl_per_mwh': '2974.1',
        'yekdem_tl_per_mwh': '364.0',
        'agreement_multiplier': '1.01'
    }
    r = requests.post('http://localhost:8000/full-process', files=files, data=data, timeout=120)

elapsed = time.time()-start
print(f'Response in {elapsed:.2f}s')
print(f'Status: {r.status_code}')
if r.status_code == 200:
    data = r.json()
    ext = data.get('extraction', {})
    print('Vendor:', ext.get('vendor'))
    cons = ext.get('consumption_kwh', {})
    print('Consumption:', cons.get('value'), 'kWh')
else:
    print('Error:', r.text[:500])
