"""
PDF → Image rendering service.
pypdfium2 kullanır (Windows'ta sorunsuz çalışır).
"""
import logging
from pathlib import Path
from typing import Optional

import pypdfium2 as pdfium
from PIL import Image

logger = logging.getLogger(__name__)

# Render settings
DEFAULT_SCALE = 2.5  # Daha yüksek = daha iyi OCR kalitesi
MAX_WIDTH = 2200  # Pixel - maliyet/kalite dengesi
MAX_HEIGHT = 3000  # Pixel


def render_pdf_first_page(
    pdf_path: str,
    output_path: str,
    scale: float = DEFAULT_SCALE,
    max_width: int = MAX_WIDTH,
    max_height: int = MAX_HEIGHT
) -> str:
    """
    PDF'in 1. sayfasını PNG olarak render et.
    
    Args:
        pdf_path: PDF dosya yolu
        output_path: Çıktı PNG yolu
        scale: Render ölçeği (2.5 önerilen)
        max_width: Maksimum genişlik (resize için)
        max_height: Maksimum yükseklik (resize için)
    
    Returns:
        Kaydedilen PNG dosya yolu
    
    Raises:
        ValueError: PDF boş veya okunamıyor
        FileNotFoundError: PDF dosyası bulunamadı
    """
    # Output klasörünü oluştur
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    # PDF'i aç
    pdf = pdfium.PdfDocument(pdf_path)
    
    if len(pdf) < 1:
        pdf.close()
        raise ValueError("PDF boş (sayfa yok)")
    
    try:
        # İlk sayfayı al
        page = pdf[0]
        
        # Render et
        bitmap = page.render(scale=scale)
        pil_image = bitmap.to_pil()
        
        # RGB'ye çevir (alpha/CMYK sorunlarını önle)
        if pil_image.mode not in ("RGB", "L"):
            pil_image = pil_image.convert("RGB")
        
        # Boyut optimizasyonu - çok büyükse küçült
        width, height = pil_image.size
        if width > max_width or height > max_height:
            ratio = min(max_width / width, max_height / height)
            new_size = (int(width * ratio), int(height * ratio))
            pil_image = pil_image.resize(new_size, Image.Resampling.LANCZOS)
            logger.info(f"Image resized: {width}x{height} → {new_size[0]}x{new_size[1]}")
        
        # PNG olarak kaydet (optimize)
        pil_image.save(output_path, format="PNG", optimize=True)
        
        logger.info(f"PDF page 1 rendered: {pdf_path} → {output_path}")
        
        return output_path
        
    finally:
        page.close()
        pdf.close()


def get_page1_path(original_path: str) -> str:
    """
    Original dosya yolundan page1 PNG yolunu türet.
    
    Örnek: ./storage/abc123.pdf → ./storage/abc123_p1.png
    """
    base = original_path.rsplit(".", 1)[0]
    return f"{base}_p1.png"
