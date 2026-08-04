"""
PDF → Image rendering service.
pypdfium2 kullanır (Windows'ta sorunsuz çalışır).
"""
import io
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


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-page, bytes-tabanlı render (Sözleşme Oluşturma V1 — belge extraction'ı)
#
# render_pdf_first_page()'in aksine dosya yoluna değil bytes'a çalışır (temp
# dosya YOK, tamamen bellek-içi — pypdfium2 doğrudan bytes kabul eder,
# doğrulandı). Bu yüzden "temp cleanup" burada gerekmez: temizlenecek bir
# temp dosya hiç oluşturulmuyor.
#
# Çağrıldığı yerler:
# - app/contracts/service.py run_extraction() — application/pdf belge
#   yüklendiğinde, Vision'a ham PDF göndermek yerine sayfaları PNG'ye çevirir.
# ═══════════════════════════════════════════════════════════════════════════════

class PdfRenderError(Exception):
    """PDF açılamadı, bozuk, boş veya izin verilen sayfa sayısını aşıyor."""
    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code  # stable contract: router bunu 422'ye map eder


def render_pdf_pages_from_bytes(
    pdf_bytes: bytes,
    max_pages: int = 5,
    scale: float = DEFAULT_SCALE,
    max_width: int = MAX_WIDTH,
    max_height: int = MAX_HEIGHT,
) -> list[bytes]:
    """
    PDF bytes'ını, her sayfası ayrı bir PNG (bytes) olacak şekilde render eder.

    Guardrails:
    - Boş PDF (0 sayfa) → PdfRenderError(error_code="pdf_empty")
    - Sayfa sayısı max_pages'i aşıyorsa → PdfRenderError(error_code="pdf_too_many_pages")
      (sessizce kırpmak yerine reddediyoruz — kullanıcı eksik extraction'ın
      farkında olmayabilir)
    - Bozuk/okunamayan PDF (pypdfium2.PdfiumError) → PdfRenderError(error_code="pdf_corrupt")

    Returns:
        Sayfa sırasına göre PNG bytes listesi (1. eleman = 1. sayfa).
    """
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except pdfium.PdfiumError as e:
        raise PdfRenderError(f"PDF açılamadı veya bozuk: {e}", error_code="pdf_corrupt") from e

    try:
        page_count = len(pdf)
        if page_count < 1:
            raise PdfRenderError("PDF boş (sayfa yok)", error_code="pdf_empty")
        if page_count > max_pages:
            raise PdfRenderError(
                f"PDF {page_count} sayfa içeriyor, izin verilen üst sınır {max_pages}.",
                error_code="pdf_too_many_pages",
            )

        pages_png: list[bytes] = []
        for i in range(page_count):
            page = pdf[i]
            try:
                bitmap = page.render(scale=scale)
                pil_image = bitmap.to_pil()

                if pil_image.mode not in ("RGB", "L"):
                    pil_image = pil_image.convert("RGB")

                width, height = pil_image.size
                if width > max_width or height > max_height:
                    ratio = min(max_width / width, max_height / height)
                    new_size = (int(width * ratio), int(height * ratio))
                    pil_image = pil_image.resize(new_size, Image.Resampling.LANCZOS)

                buf = io.BytesIO()
                pil_image.save(buf, format="PNG", optimize=True)
                pages_png.append(buf.getvalue())
            finally:
                page.close()

        logger.info(f"PDF rendered: {page_count} sayfa → {len(pages_png)} PNG (bytes-mode)")
        return pages_png
    finally:
        pdf.close()
