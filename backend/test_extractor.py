import os
import time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from app.extractor import extract_invoice_data, clear_extraction_cache

# Clear cache first
clear_extraction_cache()

# Read test image
img_path = Path('test_page1.png')
with open(img_path, 'rb') as f:
    img_bytes = f.read()

print(f'Image size: {len(img_bytes)} bytes')
print('Extracting...')
start = time.time()
result = extract_invoice_data(img_bytes, 'image/png', fast_mode=False)
elapsed = time.time() - start
print(f'Done in {elapsed:.2f}s')
print(f'Vendor: {result.vendor}')
print(f'Consumption: {result.consumption_kwh.value} kWh')
print(f'Total: {result.invoice_total_with_vat_tl.value} TL')
