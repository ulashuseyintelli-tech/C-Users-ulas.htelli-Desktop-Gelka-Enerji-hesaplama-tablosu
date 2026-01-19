# OCR Entegrasyon Planı

## Mevcut Durum (v2)
- KATMAN 1: pdfplumber → dijital PDF'lerde çalışıyor
- KATMAN 2: pypdfium2 → PDF'i görsele çeviriyor
- KATMAN 2.5: ROI Crop → kritik bölgeleri kırpıyor
- KATMAN 3: Vision (OpenAI) → görsel analiz
- KATMAN 4: Cross-validation → pdfplumber > ROI > Vision

**Problem:** Taranmış PDF'lerde pdfplumber boş dönüyor, Vision tek başına "tahmin" yapıyor.

## Hedef Mimari (v3) - OCR Entegrasyonu

```
┌─────────────────────────────────────────────────────────────────┐
│                    KATMAN MİMARİSİ v3                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  KATMAN 1: pdfplumber (dijital PDF)                            │
│     ↓ metin varsa → regex ile çıkar                            │
│     ↓ metin yoksa ↓                                            │
│                                                                 │
│  KATMAN 2: PDF → Görsel (pypdfium2)                            │
│     ↓                                                          │
│  KATMAN 2.5: ROI Crop (kritik bölgeler)                        │
│     ↓                                                          │
│  KATMAN 3: OCR (Tesseract/PaddleOCR) ← YENİ                    │
│     ↓ metin çıkar → regex ile değerleri yakala                 │
│     ↓                                                          │
│  KATMAN 4: Vision (OpenAI) - DOĞRULAYICI                       │
│     ↓ OCR sonuçlarını doğrula/düzelt                           │
│     ↓                                                          │
│  KATMAN 5: Cross-validation                                    │
│     pdfplumber > OCR > Vision öncelik sırası                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Kurulum Adımları

### 1. Tesseract Kurulumu (Windows)

```powershell
# Chocolatey ile (önerilen)
choco install tesseract

# Veya manuel:
# https://github.com/UB-Mannheim/tesseract/wiki
# tesseract-ocr-w64-setup-5.3.3.20231005.exe indir ve kur
# Kurulum sırasında "Turkish" dil paketini seç
```

### 2. Python Paketi

```bash
pip install pytesseract
```

### 3. Türkçe Dil Paketi

```powershell
# Tesseract kurulumunda seçilmediyse:
# tessdata klasörüne tur.traineddata ekle
# https://github.com/tesseract-ocr/tessdata/blob/main/tur.traineddata
```

### 4. PATH Ayarı (Windows)

```powershell
# Tesseract'ın kurulu olduğu klasörü PATH'e ekle
# Genelde: C:\Program Files\Tesseract-OCR
$env:PATH += ";C:\Program Files\Tesseract-OCR"
```

## OCR Modülü Tasarımı

### Dosya: `backend/app/ocr_extractor.py`

```python
"""
OCR Extraction Module - Tesseract/PaddleOCR

Taranmış PDF'lerden metin çıkarma.
ROI crop ile birlikte kullanıldığında en etkili.
"""

import pytesseract
from PIL import Image
import re
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class OCRResult:
    raw_text: str
    payable_total: Optional[float] = None
    vat_amount: Optional[float] = None
    energy_total: Optional[float] = None
    distribution_total: Optional[float] = None
    consumption_kwh: Optional[float] = None
    confidence: float = 0.0
    
def extract_text_from_image(image_bytes: bytes, lang: str = "tur") -> str:
    """Görseldan OCR ile metin çıkar."""
    img = Image.open(io.BytesIO(image_bytes))
    
    # Preprocessing (opsiyonel ama önerilen)
    # - Grayscale
    # - Contrast artırma
    # - Threshold
    
    text = pytesseract.image_to_string(img, lang=lang)
    return text

def parse_invoice_values(text: str) -> OCRResult:
    """OCR metninden fatura değerlerini regex ile çıkar."""
    result = OCRResult(raw_text=text)
    
    # Ödenecek Tutar pattern'leri
    patterns = [
        r"[ÖO]denecek\s*[Tt]utar[:\s]*([0-9.,]+)",
        r"[Gg]enel\s*[Tt]oplam[:\s]*([0-9.,]+)",
        r"TOPLAM[:\s]*([0-9.,]+)\s*TL",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result.payable_total = parse_tr_float(match.group(1))
            break
    
    # KDV pattern
    kdv_match = re.search(r"KDV[:\s]*([0-9.,]+)", text)
    if kdv_match:
        result.vat_amount = parse_tr_float(kdv_match.group(1))
    
    # Tüketim pattern
    kwh_match = re.search(r"([0-9.,]+)\s*kWh", text, re.IGNORECASE)
    if kwh_match:
        result.consumption_kwh = parse_tr_float(kwh_match.group(1))
    
    return result
```

## Entegrasyon Noktası

`main.py` içinde KATMAN 2.5'ten sonra:

```python
# KATMAN 3: OCR (pdfplumber başarısız olduysa)
ocr_result = None
if pdf_extracted and pdf_extracted.extraction_quality == "poor":
    try:
        from .ocr_extractor import extract_text_from_image, parse_invoice_values
        
        # ROI crop'lardan OCR yap
        for crop in cropped_images:
            ocr_text = extract_text_from_image(crop.image_bytes)
            ocr_result = parse_invoice_values(ocr_text)
            
            if ocr_result.payable_total:
                logger.info(f"OCR found payable_total: {ocr_result.payable_total}")
                break
    except Exception as e:
        logger.warning(f"OCR failed: {e}")
```

## Test Planı

1. CK Boğaziçi faturası ile test
2. Beklenen: payable_total, vat, consumption_kwh OCR'dan okunmalı
3. Vision sadece doğrulama yapmalı

## Gelecek: PaddleOCR

Tesseract yetersiz kalırsa:

```bash
pip install paddlepaddle paddleocr
```

PaddleOCR avantajları:
- Tablo tanıma daha iyi
- Düşük kaliteli görsellerde daha başarılı
- Layout analizi var

## Metrikler

Başarı kriterleri:
- Taranmış PDF'lerde doğruluk: %80 → %95+
- OCR işlem süresi: <2 saniye/sayfa
- Vision API çağrısı azalması: %50
