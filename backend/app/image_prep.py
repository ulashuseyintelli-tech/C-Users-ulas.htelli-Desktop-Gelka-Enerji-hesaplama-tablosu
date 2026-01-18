"""
Image preprocessing for invoice photos.
EXIF rotation fix + quality optimization for better OCR/Vision results.
"""
import io
import logging
from PIL import Image, ImageOps, ImageEnhance, ImageFilter

logger = logging.getLogger(__name__)

# Default settings
DEFAULT_MAX_WIDTH = 2000
DEFAULT_JPEG_QUALITY = 85


def preprocess_image_bytes(
    image_bytes: bytes,
    *,
    max_width: int = DEFAULT_MAX_WIDTH,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    output_format: str = "JPEG"  # "JPEG" or "PNG"
) -> tuple[bytes, str]:
    """
    Görseli normalize eder:
    1. EXIF orientation düzeltir (iPhone/Android ters fotoğraf sorunu)
    2. RGB'e çevirir
    3. max_width'e göre küçültür (oran korur)
    4. Autocontrast + hafif contrast + sharpness + unsharp mask
    
    Args:
        image_bytes: Raw image bytes
        max_width: Maximum width (height scales proportionally)
        jpeg_quality: JPEG quality (1-100)
        output_format: "JPEG" or "PNG"
    
    Returns:
        (processed_bytes, content_type)
    """
    im = Image.open(io.BytesIO(image_bytes))
    original_size = im.size
    
    # 1) EXIF orientation fix (çok kritik - iPhone/Android fotoğrafları)
    try:
        im = ImageOps.exif_transpose(im)
        if im.size != original_size:
            logger.info(f"EXIF rotation applied: {original_size} → {im.size}")
    except Exception as e:
        logger.warning(f"EXIF transpose failed (continuing): {e}")
    
    # 2) Mode normalize (RGB veya L - grayscale)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
        logger.debug(f"Converted to RGB from {im.mode}")
    
    # 3) Resize (oran koru) - çok büyük görseller maliyet artırır
    w, h = im.size
    if w > max_width:
        new_h = int(h * (max_width / w))
        im = im.resize((max_width, new_h), resample=Image.Resampling.LANCZOS)
        logger.info(f"Resized: {w}x{h} → {max_width}x{new_h}")
    
    # 4) Autocontrast (cutoff=1 ile çok agresif olmasın)
    try:
        im = ImageOps.autocontrast(im, cutoff=1)
    except Exception as e:
        logger.warning(f"Autocontrast failed (continuing): {e}")
    
    # 5) Hafif kontrast artır (metin okunurluğu)
    im = ImageEnhance.Contrast(im).enhance(1.15)
    
    # 6) Hafif keskinlik
    im = ImageEnhance.Sharpness(im).enhance(1.2)
    
    # 7) Unsharp mask (metin netliği için harika)
    im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=140, threshold=3))
    
    # Output
    out = io.BytesIO()
    if output_format.upper() == "PNG":
        im.save(out, format="PNG", optimize=True)
        content_type = "image/png"
    else:
        # JPEG - daha küçük boyut, Vision için yeterli
        if im.mode == "RGBA":
            im = im.convert("RGB")
        im.save(out, format="JPEG", quality=jpeg_quality, optimize=True)
        content_type = "image/jpeg"
    
    result_bytes = out.getvalue()
    logger.info(f"Preprocessing complete: {len(image_bytes)} → {len(result_bytes)} bytes ({content_type})")
    
    return result_bytes, content_type


def preprocess_for_extraction(
    image_bytes: bytes,
    content_type: str
) -> tuple[bytes, str]:
    """
    Extraction için görsel hazırla.
    Fotoğraflar JPEG'e, PDF page'ler PNG kalabilir.
    
    Args:
        image_bytes: Raw image bytes
        content_type: Original content type
    
    Returns:
        (processed_bytes, new_content_type)
    """
    # PDF page render'ları zaten PNG ve optimize - sadece hafif iyileştirme
    if "png" in content_type.lower():
        return preprocess_image_bytes(
            image_bytes,
            max_width=2200,  # PDF'ler için biraz daha geniş
            output_format="PNG"
        )
    
    # Fotoğraflar (JPEG, WebP, etc.) - JPEG'e çevir
    return preprocess_image_bytes(
        image_bytes,
        max_width=2000,
        jpeg_quality=85,
        output_format="JPEG"
    )
