"""
ROI crop'ları kaydet ve incele.
"""
from pathlib import Path
import pypdfium2 as pdfium
from PIL import Image
import io

from backend.app.region_extractor import (
    get_regions_for_vendor, 
    crop_multiple_regions,
    CK_BOGAZICI_REGIONS,
    GENERIC_REGIONS,
    CropRegion
)

# CK faturasını bul
fatura_dir = Path('Fatura örnekler')
ck_path = None
for f in fatura_dir.iterdir():
    if 'BBE' in f.name and 'CK' in f.name:
        ck_path = f
        break

if ck_path:
    print(f'Testing: {ck_path.name}')
    
    # PDF'i görsele çevir (SADECE SAYFA 1)
    with open(ck_path, 'rb') as f:
        pdf_bytes = f.read()
    
    pdf = pdfium.PdfDocument(pdf_bytes)
    page = pdf[0]
    bitmap = page.render(scale=2.0)  # Daha yüksek çözünürlük
    pil_image = bitmap.to_pil()
    
    # Orijinal görseli kaydet
    pil_image.save('debug_ck_page1.png')
    print(f'Saved: debug_ck_page1.png ({pil_image.width}x{pil_image.height})')
    
    buffer = io.BytesIO()
    pil_image.save(buffer, format='PNG')
    image_bytes = buffer.getvalue()
    
    # Daha geniş bölgeler tanımla
    test_regions = [
        # Sağ üst - daha geniş
        CropRegion(
            name="sag_ust_genis",
            x_percent=40,
            y_percent=0,
            width_percent=60,
            height_percent=50
        ),
        # Orta sağ
        CropRegion(
            name="orta_sag",
            x_percent=50,
            y_percent=20,
            width_percent=50,
            height_percent=40
        ),
        # Alt yarı
        CropRegion(
            name="alt_yari",
            x_percent=0,
            y_percent=50,
            width_percent=100,
            height_percent=50
        ),
        # Tam sağ şerit
        CropRegion(
            name="sag_serit_tam",
            x_percent=60,
            y_percent=0,
            width_percent=40,
            height_percent=100
        ),
    ]
    
    # Crop'ları kaydet
    cropped = crop_multiple_regions(image_bytes, test_regions)
    
    for crop in cropped:
        filename = f'debug_crop_{crop.name}.png'
        with open(filename, 'wb') as f:
            f.write(crop.image_bytes)
        print(f'Saved: {filename} ({crop.width}x{crop.height})')
    
    print('\nCrop dosyalarını inceleyip "Fatura Özeti" kutusunun hangi bölgede olduğunu belirleyin.')
else:
    print('CK faturası bulunamadı!')
