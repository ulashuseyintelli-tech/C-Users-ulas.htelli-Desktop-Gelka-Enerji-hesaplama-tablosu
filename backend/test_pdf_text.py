"""PDF metin çıkarma testi"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.pdf_text_extractor import extract_text_from_pdf, create_extraction_hint

# CK Boğaziçi faturasını bul
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fatura_dir = os.path.join(base_dir, "Fatura örnekler")

ck_file = None
for f in os.listdir(fatura_dir):
    if "BBE2025" in f and "CK" in f:
        ck_file = os.path.join(fatura_dir, f)
        break

if ck_file:
    print(f"Testing: {ck_file}")
    
    with open(ck_file, 'rb') as f:
        pdf_bytes = f.read()
    
    result = extract_text_from_pdf(pdf_bytes)
    
    print(f"\n=== PDF TEXT EXTRACTION ===")
    print(f"Page count: {result.page_count}")
    print(f"Is digital: {result.is_digital}")
    print(f"Quality: {result.extraction_quality}")
    print(f"Text length: {len(result.raw_text)} chars")
    
    print(f"\n=== EXTRACTED VALUES ===")
    print(f"Ödenecek Tutar: {result.odenecek_tutar}")
    print(f"KDV Tutarı: {result.kdv_tutari}")
    print(f"KDV Matrahı: {result.kdv_matrahi}")
    print(f"Toplam Tüketim: {result.toplam_tuketim_kwh}")
    
    print(f"\n=== HINT FOR OPENAI ===")
    hint = create_extraction_hint(result)
    print(hint if hint else "(no hint)")
    
    print(f"\n=== RAW TEXT (first 2000 chars) ===")
    print(result.raw_text[:2000])
else:
    print("CK Boğaziçi faturası bulunamadı!")
