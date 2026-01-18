"""
Region-Based Extractor - PDF'i bölgelere ayırıp her bölgeyi ayrı OCR'la.

Bu modül:
1. PDF'i görüntüye render eder
2. Tedarikçi profiline göre bölgelere ayırır
3. Her bölgeyi ayrı prompt ile OpenAI Vision'a gönderir
4. Sonuçları birleştirip kanonik formata dönüştürür

Avantajları:
- Daha az token kullanımı (sadece ilgili bölge)
- Daha yüksek doğruluk (odaklanmış prompt)
- Yanlış tablodan okuma riski düşük
"""

import base64
import json
import logging
import tempfile
import os
from typing import Optional
from PIL import Image

from .supplier_profiles import (
    CanonicalInvoice,
    InvoiceLine,
    LineCode,
    TaxBreakdown,
    VATInfo,
    Totals,
    RegionCoordinates,
    get_regions_for_supplier,
    get_region_prompt,
    detect_supplier,
    tr_money,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# PDF Rendering
# ═══════════════════════════════════════════════════════════════════════════════

def render_pdf_pages(pdf_path: str, dpi: int = 200) -> list[Image.Image]:
    """
    PDF sayfalarını görüntüye render et.
    
    pdfplumber kullanır (requirements.txt'te olmalı).
    """
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed. Run: pip install pdfplumber")
        return []
    
    pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                img = page.to_image(resolution=dpi).original
                pages.append(img)
    except Exception as e:
        logger.error(f"PDF render error: {e}")
    
    return pages


def crop_region(img: Image.Image, region: tuple[float, float, float, float]) -> Image.Image:
    """
    Görüntüden belirli bir bölgeyi kırp.
    
    Args:
        img: PIL Image
        region: (x0, y0, x1, y1) normalize koordinatlar (0..1)
    
    Returns:
        Kırpılmış görüntü
    """
    w, h = img.size
    x0, y0, x1, y1 = region
    box = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
    return img.crop(box)


def image_to_base64(img: Image.Image, format: str = "PNG") -> str:
    """PIL Image'ı base64 string'e çevir"""
    import io
    buffer = io.BytesIO()
    img.save(buffer, format=format)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# OpenAI Vision OCR
# ═══════════════════════════════════════════════════════════════════════════════

def ocr_region_with_openai(
    img: Image.Image,
    prompt: str,
    api_key: Optional[str] = None,
    model: str = "gpt-4o"
) -> dict:
    """
    Görüntüyü OpenAI Vision ile OCR yap.
    
    Args:
        img: PIL Image
        prompt: OCR prompt'u
        api_key: OpenAI API key (None ise env'den alınır)
        model: OpenAI model
    
    Returns:
        JSON dict
    """
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("openai not installed")
        return {}
    
    if api_key is None:
        from .core.config import settings
        api_key = settings.openai_api_key
    
    if not api_key:
        logger.error("OpenAI API key not configured")
        return {}
    
    client = OpenAI(api_key=api_key)
    base64_image = image_to_base64(img)
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt + "\n\nSADECE JSON döndür, başka bir şey yazma."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000,
            temperature=0.1,
        )
        
        content = response.choices[0].message.content
        
        # JSON parse
        # Markdown code block varsa temizle
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        return json.loads(content.strip())
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}, content: {content[:200]}")
        return {}
    except Exception as e:
        logger.error(f"OpenAI Vision error: {e}")
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# Line Item Parsing
# ═══════════════════════════════════════════════════════════════════════════════

def map_label_to_code(label: str) -> LineCode:
    """Etiket metninden kalem kodunu belirle"""
    t = (label or "").lower()
    
    if "dağıtım" in t:
        return LineCode.DISTRIBUTION
    if "yek" in t or "yekdem" in t:
        return LineCode.YEK
    if "vergi" in t or "btv" in t or "fon" in t:
        return LineCode.TAX_BTV
    if "yüksek" in t and "kademe" in t:
        return LineCode.ACTIVE_ENERGY_HIGH
    if "düşük" in t and "kademe" in t:
        return LineCode.ACTIVE_ENERGY_LOW
    if "gündüz" in t or "t1" in t:
        return LineCode.ACTIVE_ENERGY_T1
    if "puant" in t or "t2" in t:
        return LineCode.ACTIVE_ENERGY_T2
    if "gece" in t or "t3" in t:
        return LineCode.ACTIVE_ENERGY_T3
    if "reaktif" in t:
        return LineCode.REACTIVE
    if "demand" in t or "güç" in t:
        return LineCode.DEMAND
    
    return LineCode.ACTIVE_ENERGY


def parse_lines_from_json(lines_json: list) -> list[InvoiceLine]:
    """JSON'dan InvoiceLine listesi oluştur"""
    result = []
    
    for row in lines_json or []:
        label = (row.get("label") or "").strip()
        qty = tr_money(row.get("qty_kwh"))
        unit_price = tr_money(row.get("unit_price"))
        amount = tr_money(row.get("amount_tl"))
        
        if label and (qty or amount):
            line = InvoiceLine(
                code=map_label_to_code(label),
                label=label,
                qty_kwh=qty,
                unit_price=unit_price,
                amount=amount,
                evidence=f"{label}: {qty} kWh × {unit_price} = {amount} TL",
            )
            result.append(line)
    
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Region-Based Extraction Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def extract_invoice_by_regions(
    pdf_path: str,
    supplier_code: Optional[str] = None,
    page_index: int = 0,
    api_key: Optional[str] = None,
) -> CanonicalInvoice:
    """
    PDF'i bölgelere ayırıp her bölgeyi ayrı OCR'la.
    
    Args:
        pdf_path: PDF dosya yolu
        supplier_code: Tedarikçi kodu (None ise otomatik tespit)
        page_index: İşlenecek sayfa (varsayılan: 0)
        api_key: OpenAI API key
    
    Returns:
        CanonicalInvoice
    """
    invoice = CanonicalInvoice()
    
    # PDF'i render et
    pages = render_pdf_pages(pdf_path)
    if not pages:
        invoice.errors.append("PDF_RENDER_FAILED")
        return invoice
    
    if page_index >= len(pages):
        invoice.errors.append(f"PAGE_INDEX_OUT_OF_RANGE: {page_index} >= {len(pages)}")
        return invoice
    
    img = pages[page_index]
    
    # Tedarikçi tespiti (dosya adından)
    if not supplier_code:
        filename = os.path.basename(pdf_path)
        profile = detect_supplier("", filename)
        supplier_code = profile.code if profile else "unknown"
    
    invoice.supplier = supplier_code
    
    # Region koordinatlarını al
    regions = get_regions_for_supplier(supplier_code)
    
    # 1) Fatura Detayı bölgesi
    logger.info(f"Extracting fatura_detayi region for {supplier_code}")
    det_img = crop_region(img, regions.fatura_detayi)
    det_prompt = get_region_prompt("fatura_detayi")
    det_json = ocr_region_with_openai(det_img, det_prompt, api_key)
    
    if det_json.get("lines"):
        invoice.lines = parse_lines_from_json(det_json["lines"])
        invoice.source_anchor = "fatura_detayi_region"
    else:
        invoice.warnings.append("FATURA_DETAYI_EMPTY")
    
    # 2) Vergiler bölgesi
    logger.info(f"Extracting vergiler region for {supplier_code}")
    tax_img = crop_region(img, regions.vergiler)
    tax_prompt = get_region_prompt("vergiler")
    tax_json = ocr_region_with_openai(tax_img, tax_prompt, api_key)
    
    invoice.taxes = TaxBreakdown(
        btv=tr_money(tax_json.get("btv_tl")),
        other=tr_money(tax_json.get("other_taxes_tl")),
    )
    invoice.vat = VATInfo(
        base=tr_money(tax_json.get("vat_base_tl")),
        amount=tr_money(tax_json.get("vat_amount_tl")),
        rate=0.20,
    )
    
    # 3) Özet bölgesi
    logger.info(f"Extracting ozet region for {supplier_code}")
    ozet_img = crop_region(img, regions.ozet)
    ozet_prompt = get_region_prompt("ozet")
    ozet_json = ocr_region_with_openai(ozet_img, ozet_prompt, api_key)
    
    invoice.totals.payable = tr_money(ozet_json.get("payable_tl"))
    # due_date parse edilebilir
    
    # 4) Fatura Tutarı bölgesi
    logger.info(f"Extracting fatura_tutari region for {supplier_code}")
    total_img = crop_region(img, regions.fatura_tutari)
    total_prompt = get_region_prompt("fatura_tutari")
    total_json = ocr_region_with_openai(total_img, total_prompt, api_key)
    
    invoice.totals.total = tr_money(total_json.get("total_tl"))
    
    # Subtotal hesapla (kalem toplamı)
    if invoice.lines:
        invoice.totals.subtotal = sum(
            l.amount or 0 for l in invoice.lines if l.amount
        )
    
    # Doğrulama
    invoice.validate()
    
    logger.info(f"Region extraction complete: {invoice.to_debug_dict()}")
    
    return invoice


def extract_invoice_by_regions_from_image(
    image_bytes: bytes,
    supplier_code: Optional[str] = None,
    api_key: Optional[str] = None,
) -> CanonicalInvoice:
    """
    Görüntü bytes'ından region-based extraction.
    
    Args:
        image_bytes: Görüntü bytes
        supplier_code: Tedarikçi kodu
        api_key: OpenAI API key
    
    Returns:
        CanonicalInvoice
    """
    import io
    
    invoice = CanonicalInvoice()
    
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        invoice.errors.append(f"IMAGE_LOAD_FAILED: {e}")
        return invoice
    
    invoice.supplier = supplier_code or "unknown"
    
    # Region koordinatlarını al
    regions = get_regions_for_supplier(invoice.supplier)
    
    # Aynı extraction pipeline...
    # (Kısaltılmış - tam implementasyon yukarıdaki fonksiyonla aynı)
    
    # 1) Fatura Detayı
    det_img = crop_region(img, regions.fatura_detayi)
    det_prompt = get_region_prompt("fatura_detayi")
    det_json = ocr_region_with_openai(det_img, det_prompt, api_key)
    
    if det_json.get("lines"):
        invoice.lines = parse_lines_from_json(det_json["lines"])
    
    # 2) Vergiler
    tax_img = crop_region(img, regions.vergiler)
    tax_prompt = get_region_prompt("vergiler")
    tax_json = ocr_region_with_openai(tax_img, tax_prompt, api_key)
    
    invoice.taxes = TaxBreakdown(
        btv=tr_money(tax_json.get("btv_tl")),
    )
    invoice.vat = VATInfo(
        amount=tr_money(tax_json.get("vat_amount_tl")),
    )
    
    # 3) Özet
    ozet_img = crop_region(img, regions.ozet)
    ozet_prompt = get_region_prompt("ozet")
    ozet_json = ocr_region_with_openai(ozet_img, ozet_prompt, api_key)
    
    invoice.totals.payable = tr_money(ozet_json.get("payable_tl"))
    
    # 4) Fatura Tutarı
    total_img = crop_region(img, regions.fatura_tutari)
    total_prompt = get_region_prompt("fatura_tutari")
    total_json = ocr_region_with_openai(total_img, total_prompt, api_key)
    
    invoice.totals.total = tr_money(total_json.get("total_tl"))
    
    # Doğrulama
    invoice.validate()
    
    return invoice
