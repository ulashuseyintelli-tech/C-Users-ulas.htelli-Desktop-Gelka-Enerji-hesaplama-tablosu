"""
ROI crop direkt testi.
"""
from pathlib import Path
import pypdfium2 as pdfium
from PIL import Image
import io

from backend.app.region_extractor import (
    get_regions_for_vendor, 
    crop_multiple_regions,
    create_payable_total_extraction_func
)
from backend.app.extractor import get_openai_client
from backend.app.core.config import settings

# CK faturasını bul
fatura_dir = Path('Fatura örnekler')
ck_path = None
for f in fatura_dir.iterdir():
    if 'BBE' in f.name and 'CK' in f.name:
        ck_path = f
        break

if ck_path:
    print(f'Testing: {ck_path.name}')
    
    # PDF'i görsele çevir
    with open(ck_path, 'rb') as f:
        pdf_bytes = f.read()
    
    pdf = pdfium.PdfDocument(pdf_bytes)
    page = pdf[0]
    bitmap = page.render(scale=1.5)
    pil_image = bitmap.to_pil()
    
    buffer = io.BytesIO()
    pil_image.save(buffer, format='PNG')
    image_bytes = buffer.getvalue()
    
    print(f'Image: {pil_image.width}x{pil_image.height}')
    
    # ROI crop
    regions = get_regions_for_vendor('unknown')
    cropped = crop_multiple_regions(image_bytes, regions)
    
    # OpenAI ile test
    print('\nOpenAI ile ROI crop testi...')
    client = get_openai_client()
    extract_func = create_payable_total_extraction_func(client, model=settings.openai_model_accurate)
    
    for crop in cropped:
        print(f'\nTrying: {crop.name}')
        try:
            result = extract_func(crop.image_bytes)
            print(f'  Result: {result}')
            if result and result.get('payable_total'):
                print(f'  >>> FOUND: {result["payable_total"]}')
                break
        except Exception as e:
            print(f'  Error: {e}')
else:
    print('CK faturası bulunamadı!')
