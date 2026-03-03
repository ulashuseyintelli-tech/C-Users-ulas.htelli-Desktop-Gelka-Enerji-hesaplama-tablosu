"""
PDF/HTML Teklif Çıktısı Generator

Jinja2 template + PDF rendering:
- Primary: WeasyPrint (requires Cairo/Pango on system)
- Fallback: Playwright/Chromium (works everywhere, including Windows)

Auto-fallback: If WeasyPrint fails, Playwright is used automatically.
"""
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from jinja2 import Environment, FileSystemLoader

from .models import CalculationResult, InvoiceExtraction, OfferParams

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# WeasyPrint - Optional Import (may fail on Windows without Cairo/Pango)
# ═══════════════════════════════════════════════════════════════════════════════
WEASYPRINT_AVAILABLE = False
HTML = None
CSS = None

try:
    from weasyprint import HTML, CSS
    WEASYPRINT_AVAILABLE = True
    logger.info("WeasyPrint available for PDF generation")
except ImportError as e:
    logger.warning(f"WeasyPrint not available: {e}. Will use Playwright fallback.")
except OSError as e:
    # Cairo/Pango DLL issues on Windows
    logger.warning(f"WeasyPrint system dependency error: {e}. Will use Playwright fallback.")

# ═══════════════════════════════════════════════════════════════════════════════
# ReportLab - Fallback for Windows (pure Python, no system dependencies)
# ═══════════════════════════════════════════════════════════════════════════════
REPORTLAB_AVAILABLE = False
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from io import BytesIO
    REPORTLAB_AVAILABLE = True
    logger.info("ReportLab available for PDF generation (fallback)")
except ImportError as e:
    logger.warning(f"ReportLab not available: {e}")

# Template directory
TEMPLATE_DIR = Path(__file__).parent / "templates"
OUTPUT_DIR = Path(__file__).parent.parent / "outputs"

# Ensure directories exist
TEMPLATE_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Jinja2 environment - auto_reload for development
env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), auto_reload=True)


def format_currency(value: float) -> str:
    """Format number as Turkish Lira"""
    try:
        return f"{float(value):,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "0,00 TL"


def format_percent(value: float) -> str:
    """Format number as percentage"""
    try:
        return f"%{float(value) * 100:.1f}"
    except:
        return "%0,0"


def format_number(value: float, decimals: int = 2) -> str:
    """Format number with Turkish locale"""
    try:
        formatted = f"{float(value):,.{decimals}f}"
        return formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "0"


# Register filters
env.filters["currency"] = format_currency
env.filters["percent"] = format_percent
env.filters["number"] = format_number
env.filters["abs"] = lambda x: abs(float(x)) if x else 0


def _load_image_base64(filename: str) -> Optional[str]:
    """Load an image from templates dir and return base64 string."""
    import base64
    img_path = TEMPLATE_DIR / filename
    if img_path.exists():
        try:
            with open(img_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.warning(f"Failed to load image {filename}: {e}")
    return None


def generate_offer_html(
    extraction: InvoiceExtraction,
    calculation: CalculationResult,
    params: OfferParams,
    customer_name: Optional[str] = None,
    customer_company: Optional[str] = None,
    offer_id: Optional[int] = None,
    contact_person: Optional[str] = None,
    offer_date: Optional[str] = None,
    offer_validity_days: int = 15,
) -> str:
    """
    Generate HTML offer document from calculation results.
    """
    template = env.get_template("offer_template.html")
    
    # Prepare context
    consumption = extraction.consumption_kwh.value or 0
    current_unit = extraction.current_active_unit_price_tl_per_kwh.value or 0
    offer_unit = (params.weighted_ptf_tl_per_mwh / 1000 + params.yekdem_tl_per_mwh / 1000) * params.agreement_multiplier
    savings_per_kwh = current_unit - offer_unit
    yearly_savings = calculation.difference_incl_vat_tl * 12
    
    # Tarife grubu belirleme
    tariff_group = "Sanayi"  # Default
    if extraction.meta:
        tg = extraction.meta.tariff_group_guess or ""
        vg = extraction.meta.voltage_guess or ""
        if tg.lower() in ["sanayi", "industry"]:
            tariff_group = f"Sanayi {vg}" if vg and vg != "unknown" else "Sanayi"
        elif tg.lower() in ["mesken", "residential"]:
            tariff_group = "Mesken"
        elif tg.lower() in ["ticarethane", "commercial"]:
            tariff_group = "Ticarethane"
        elif tg.lower() in ["tarimsal", "agricultural"]:
            tariff_group = "Tarımsal Sulama"
        else:
            tariff_group = f"{tg} {vg}".strip() if tg != "unknown" else "Sanayi"
    
    # Teklif tarihi
    if offer_date:
        try:
            from datetime import datetime as dt
            parsed_date = dt.strptime(offer_date, "%Y-%m-%d")
            formatted_offer_date = parsed_date.strftime("%d.%m.%Y")
        except:
            formatted_offer_date = datetime.now().strftime("%d.%m.%Y")
    else:
        formatted_offer_date = datetime.now().strftime("%d.%m.%Y")
    
    # Hitap metni
    if customer_name:
        greeting = f"Sayın {customer_name} Yetkilisi,"
    elif contact_person:
        greeting = f"Sayın {contact_person},"
    else:
        greeting = "Sayın Yetkili,"
    
    # Antetli kağıt PNG'sini base64 olarak yükle
    letterhead_b64 = _load_image_base64("antetli_bg_300dpi.png") or _load_image_base64("antetli_bg.png")
    if letterhead_b64:
        logger.info(f"LETTERHEAD loaded OK, base64 length={len(letterhead_b64)}, starts={letterhead_b64[:30]}")
    else:
        logger.error("LETTERHEAD NOT LOADED! Check templates dir for antetli_bg_300dpi.png or antetli_bg.png")

    # Footer PNG'sini base64 olarak yükle
    footer_b64 = _load_image_base64("footer.png")
    if footer_b64:
        logger.info(f"FOOTER loaded OK, base64 length={len(footer_b64)}")
    else:
        logger.warning("Footer image not found, will use text fallback")

    context = {
        "offer_id": offer_id or datetime.now().strftime("%Y%m%d%H%M%S"),
        "date": formatted_offer_date,
        "customer_name": customer_name or "",
        "customer_company": customer_company,
        "contact_person": contact_person or "",
        "greeting": greeting,
        "offer_validity_days": offer_validity_days,
        
        # Antetli kağıt arka planı
        "letterhead_base64": letterhead_b64 or "",
        "footer_base64": footer_b64 or "",
        
        # Extraction data
        "vendor": extraction.vendor,
        "invoice_period": extraction.invoice_period,
        "consumption_kwh": consumption,
        "current_unit_price": current_unit,
        "distribution_unit_price": extraction.distribution_unit_price_tl_per_kwh.value or 0,
        "demand_qty": extraction.demand_qty.value or 0,
        "demand_unit_price": extraction.demand_unit_price_tl_per_unit.value or 0,
        
        # Offer params
        "weighted_ptf": params.weighted_ptf_tl_per_mwh,
        "yekdem": params.yekdem_tl_per_mwh,
        "agreement_multiplier": params.agreement_multiplier,
        
        # UI Switches (Teklif Varsayımları)
        "extra_items_apply_to_offer": params.extra_items_apply_to_offer,
        "use_offer_distribution": params.use_offer_distribution,
        "offer_distribution_unit_price": params.offer_distribution_unit_price_tl_per_kwh,
        
        # Calculation results
        "calc": calculation,
        
        # Derived values
        "offer_unit_price": offer_unit,
        
        # Yıllık projeksiyon ve kWh başı tasarruf
        "yearly_savings_tl": yearly_savings,
        "savings_per_kwh": savings_per_kwh,
        
        # Teklif mektubu için
        "tariff_group": tariff_group,
    }
    
    return template.render(**context)


def generate_offer_pdf(
    extraction: InvoiceExtraction,
    calculation: CalculationResult,
    params: OfferParams,
    customer_name: Optional[str] = None,
    customer_company: Optional[str] = None,
    offer_id: Optional[int] = None,
    output_filename: Optional[str] = None,
    contact_person: Optional[str] = None,
    offer_date: Optional[str] = None,
    offer_validity_days: int = 15,
) -> str:
    """
    Generate PDF offer document and save to file.
    Returns the path to the generated PDF file.
    
    Uses WeasyPrint if available, falls back to Playwright/Chromium.
    """
    # Generate filename
    if not output_filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = (customer_company or customer_name or "teklif").replace(" ", "_")[:30]
        output_filename = f"teklif_{safe_name}_{timestamp}.pdf"
    
    output_path = OUTPUT_DIR / output_filename
    
    # Generate PDF bytes using the fallback-enabled function
    pdf_bytes = generate_offer_pdf_bytes(
        extraction, calculation, params,
        customer_name, customer_company, offer_id,
        contact_person=contact_person,
        offer_date=offer_date,
        offer_validity_days=offer_validity_days,
    )
    
    # Write to file
    with open(output_path, "wb") as f:
        f.write(pdf_bytes)
    
    return str(output_path)


def get_pdf_styles() -> str:
    """Return CSS styles for PDF generation"""
    return """
    @page {
        size: A4;
        margin: 2cm;
    }
    
    body {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        font-size: 11pt;
        line-height: 1.5;
        color: #333;
    }
    
    .header {
        text-align: center;
        margin-bottom: 30px;
        padding-bottom: 20px;
        border-bottom: 2px solid #10B981;
    }
    
    .logo {
        font-size: 24pt;
        font-weight: bold;
        color: #10B981;
    }
    
    .offer-title {
        font-size: 18pt;
        color: #1F2937;
        margin-top: 10px;
    }
    
    .offer-meta {
        color: #6B7280;
        font-size: 10pt;
    }
    
    /* Teklif Mektubu Stilleri */
    .offer-letter {
        background-color: #FAFAFA;
        padding: 25px;
        border-radius: 8px;
        border-left: 4px solid #10B981;
        margin-bottom: 30px;
        font-size: 10.5pt;
        line-height: 1.7;
    }
    
    .offer-letter p {
        margin-bottom: 12px;
        text-align: justify;
    }
    
    .letter-subsection {
        margin: 20px 0;
        padding: 15px;
        background-color: #FFFFFF;
        border-radius: 5px;
        border: 1px solid #E5E7EB;
    }
    
    .subsection-title {
        color: #1F2937;
        margin-bottom: 10px;
        font-size: 11pt;
    }
    
    .commercial-terms {
        margin: 10px 0 10px 20px;
        padding-left: 0;
    }
    
    .commercial-terms li {
        margin-bottom: 5px;
        list-style-type: disc;
    }
    
    .savings-summary-letter {
        background-color: #ECFDF5;
        border: 1px solid #10B981;
    }
    
    .savings-list {
        margin: 10px 0 10px 20px;
        padding-left: 0;
    }
    
    .savings-list li {
        margin-bottom: 8px;
        list-style-type: none;
    }
    
    .savings-list li::before {
        content: "•";
        color: #10B981;
        font-weight: bold;
        margin-right: 8px;
    }
    
    .signature {
        margin-top: 25px;
        padding-top: 15px;
        border-top: 1px solid #E5E7EB;
    }
    
    .contact-info {
        font-size: 9pt;
        color: #6B7280;
    }
    
    .page-break {
        page-break-after: always;
    }
    
    .section {
        margin-bottom: 25px;
    }
    
    .section-title {
        font-size: 14pt;
        font-weight: bold;
        color: #1F2937;
        margin-bottom: 10px;
        padding-bottom: 5px;
        border-bottom: 1px solid #E5E7EB;
    }
    
    table {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 15px;
    }
    
    th, td {
        padding: 10px;
        text-align: left;
        border-bottom: 1px solid #E5E7EB;
    }
    
    th {
        background-color: #F9FAFB;
        font-weight: 600;
        color: #374151;
    }
    
    .highlight-row {
        background-color: #ECFDF5;
    }
    
    .highlight-row td {
        font-weight: bold;
        color: #059669;
    }
    
    .comparison-table {
        margin-top: 20px;
    }
    
    .comparison-table th {
        text-align: center;
    }
    
    .current-col {
        background-color: #FEF2F2;
        color: #DC2626;
    }
    
    .offer-col {
        background-color: #ECFDF5;
        color: #059669;
    }
    
    .savings-box {
        background-color: #10B981;
        color: white;
        padding: 20px;
        border-radius: 10px;
        text-align: center;
        margin: 30px 0;
    }
    
    .savings-amount {
        font-size: 28pt;
        font-weight: bold;
    }
    
    .savings-label {
        font-size: 12pt;
        opacity: 0.9;
    }
    
    .footer {
        margin-top: 40px;
        padding-top: 20px;
        border-top: 1px solid #E5E7EB;
        font-size: 9pt;
        color: #6B7280;
        text-align: center;
    }
    
    .notes {
        background-color: #F9FAFB;
        padding: 15px;
        border-radius: 5px;
        font-size: 10pt;
    }
    """


# Create default template if not exists
def create_default_template():
    """Create the default offer template - now reads from the actual template file"""
    # Template is now managed directly in offer_template.html
    # This function is kept for backward compatibility but does nothing
    # The template file is the source of truth
    pass


# Initialize template on module load
create_default_template()


# ═══════════════════════════════════════════════════════════════════════════════
# Storage-Integrated PDF Generation (with Playwright fallback)
# ═══════════════════════════════════════════════════════════════════════════════

def _html_to_pdf_weasyprint(html_content: str) -> bytes:
    """Convert HTML to PDF using WeasyPrint."""
    html = HTML(string=html_content)
    css = CSS(string=get_pdf_styles())
    return html.write_pdf(stylesheets=[css])


def _html_to_pdf_playwright(html_content: str) -> bytes:
    """Convert HTML to PDF using Playwright/Chromium (sync API)."""
    from .services.pdf_playwright import html_to_pdf_bytes_sync_v2
    return html_to_pdf_bytes_sync_v2(html_content)


def _generate_pdf_reportlab(
    extraction: InvoiceExtraction,
    calculation: CalculationResult,
    params: OfferParams,
    customer_name: Optional[str] = None,
    customer_company: Optional[str] = None,
    offer_id: Optional[int] = None,
    contact_person: Optional[str] = None,
    offer_date: Optional[str] = None,
    offer_validity_days: int = 15,
) -> bytes:
    """Generate PDF using ReportLab (pure Python, works everywhere)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
    import os
    
    buffer = BytesIO()
    
    # Register Turkish-compatible font ONCE
    font_name = 'Helvetica'
    try:
        font_paths = [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
        for font_path in font_paths:
            if os.path.exists(font_path):
                if 'TurkishFont' not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont('TurkishFont', font_path))
                font_name = 'TurkishFont'
                break
    except Exception as e:
        logger.warning(f"Font registration failed: {e}")
    
    styles = getSampleStyleSheet()
    
    # Offer ID and date for header
    offer_id_str = str(offer_id) if offer_id else datetime.now().strftime('%Y%m%d%H%M%S')
    if offer_date:
        try:
            parsed = datetime.strptime(offer_date, "%Y-%m-%d")
            date_str = parsed.strftime('%d.%m.%Y')
        except Exception:
            date_str = datetime.now().strftime('%d.%m.%Y')
    else:
        date_str = datetime.now().strftime('%d.%m.%Y')
    
    # Header/Footer callback - font_name'i closure'da yakala
    def add_header_footer(canvas, doc):
        canvas.saveState()
        width, height = A4
        
        # ═══════════════════════════════════════════════════════════════════
        # HEADER - Orijinal header.png görselini kullan
        # ═══════════════════════════════════════════════════════════════════
        header_path = Path(__file__).parent / "templates" / "header.png"
        if header_path.exists():
            from PIL import Image as PILImage
            with PILImage.open(str(header_path)) as img:
                img_w, img_h = img.size
            
            pdf_img_width = width
            pdf_img_height = pdf_img_width * (img_h / img_w)
            
            canvas.drawImage(
                str(header_path),
                x=0,
                y=height - pdf_img_height,
                width=pdf_img_width,
                height=pdf_img_height,
                mask='auto'
            )
            
            # Teklif No ve Tarih — header görseli ile başlık arasındaki beyaz alan
            # Header görseli height-pdf_img_height'ta bitiyor, topMargin=3.2cm
            # Beyaz alan: (height - pdf_img_height) ile (height - 3.2cm) arası
            white_zone_top = height - pdf_img_height  # header görseli alt kenarı
            canvas.setFont(font_name, 9)
            canvas.setFillColor(colors.HexColor('#374151'))
            text_x = width - 2*cm
            text_y_top = white_zone_top - 0.25*cm
            canvas.drawRightString(text_x, text_y_top, f"Teklif No: {offer_id_str}")
            canvas.drawRightString(text_x, text_y_top - 0.4*cm, f"Tarih: {date_str}")
        else:
            canvas.setFont(font_name, 18)
            canvas.setFillColor(colors.HexColor('#10B981'))
            canvas.drawString(2*cm, height - 1.5*cm, "GELKA ENERJİ")
        
        # ═══════════════════════════════════════════════════════════════════
        # FOOTER - footer.png (ikonlar) + metin satırı (referans PDF gibi)
        # ═══════════════════════════════════════════════════════════════════
        footer_path = Path(__file__).parent / "templates" / "footer.png"
        if footer_path.exists():
            from PIL import Image as PILImage
            with PILImage.open(str(footer_path)) as fimg:
                fimg_w, fimg_h = fimg.size
            img_width = width
            img_height = img_width * (fimg_h / fimg_w)
            canvas.drawImage(
                str(footer_path), 
                0, 0,
                width=img_width, 
                height=img_height,
                mask='auto'
            )
            # Metin satırı footer.png'nin hemen üstünde
            footer_text_y = img_height + 0.2*cm
        else:
            footer_text_y = 0.8*cm
        
        # Footer metin satırı (referans PDF'deki gibi)
        canvas.setFont(font_name, 7)
        canvas.setFillColor(colors.HexColor('#6B7280'))
        footer_text = f"Teklif No: {offer_id_str} | {date_str} | Geçerlilik: {offer_validity_days} Gün | www.gelkaenerji.com.tr"
        canvas.drawCentredString(width / 2, footer_text_y, footer_text)
        
        canvas.restoreState()
    
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4, 
        topMargin=3.2*cm,
        bottomMargin=3.8*cm,
        leftMargin=2*cm, 
        rightMargin=2*cm
    )
    
    # doc.width = 21cm - 2cm - 2cm = 17cm — yazılar ve tablolar aynı genişlikte
    avail_w = 17*cm
    col4 = avail_w / 4
    
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=13, textColor=colors.HexColor('#1F2937'), alignment=1, fontName=font_name, spaceAfter=0, spaceBefore=0)
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'], fontSize=10, textColor=colors.HexColor('#1F2937'), fontName=font_name, spaceBefore=2, spaceAfter=2)
    normal_style = ParagraphStyle('Normal', parent=styles['Normal'], fontSize=9, fontName=font_name)
    letter_style = ParagraphStyle('Letter', parent=styles['Normal'], fontSize=8.5, fontName=font_name, leading=11.5, alignment=4)
    
    elements = []
    
    # Helper to get value from FieldValue or string
    def get_val(field, default="-"):
        if field is None:
            return default
        if hasattr(field, 'value'):
            return field.value if field.value else default
        return str(field) if field else default
    
    # Sayıları Türkçe formatla (nokta binlik, virgül ondalık)
    def fmt_tl(val):
        return f"{val:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")
    
    # Türkçe sayı formatı
    def fmt_num(val, decimals=2):
        fmt_str = f"{{:,.{decimals}f}}"
        return fmt_str.format(val).replace(",", "X").replace(".", ",").replace("X", ".")
    
    # Tarife grubu
    tariff_group = "Sanayi"
    if hasattr(extraction, 'meta') and extraction.meta:
        tg = getattr(extraction.meta, 'tariff_group_guess', '') or ''
        if tg:
            tariff_group = tg
    
    consumption = float(get_val(extraction.consumption_kwh, 0))
    vendor = get_val(extraction.vendor)
    period = get_val(extraction.invoice_period)
    
    # Başlık - yeşil alt çizgili
    title_para = Paragraph("ENERJİ TASARRUF TEKLİFİ", title_style)
    title_table = Table([[title_para]], colWidths=[avail_w])
    title_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('LINEBELOW', (0, 0), (-1, -1), 2, colors.HexColor('#10B981')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
    ]))
    elements.append(title_table)
    elements.append(Spacer(1, 0.35*cm))  # Yeşil çizgi → Sayın arasında boşluk
    
    # Hitap metni
    if customer_name:
        greeting = f"Sayın {customer_name} Yetkilisi,"
    elif contact_person:
        greeting = f"Sayın {contact_person},"
    else:
        greeting = "Sayın Yetkili,"
    
    elements.append(Paragraph(f"<b>{greeting}</b>", letter_style))
    elements.append(Spacer(1, 0.1*cm))
    
    elements.append(Paragraph(
        f"Mevcut elektrik tüketim verileriniz ve tarafımıza iletilen fatura bilgileriniz esas alınarak yapılan analiz sonucunda, "
        f"<b>{tariff_group}</b> abone grubunuz için hazırlanan elektrik enerjisi tedarik teklifimizi bilgilerinize sunarız.",
        letter_style
    ))
    elements.append(Spacer(1, 0.1*cm))
    
    # Müşteri bilgileri tablosu
    if customer_name or contact_person:
        cell_style = ParagraphStyle('CellText', fontSize=8.5, fontName=font_name, leading=11)
        cust_data = [
            [
                Paragraph("<b>Firma Adı</b>", cell_style),
                Paragraph(customer_name or "-", cell_style),
                Paragraph("<b>Yetkili Kişi</b>", cell_style),
                Paragraph(contact_person or "-", cell_style),
            ],
        ]
        # Etiketler dar, değerler geniş
        ct = Table(cust_data, colWidths=[2.5*cm, avail_w/2 - 2.5*cm, 2.5*cm, avail_w/2 - 2.5*cm])
        ct.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F3F4F6')),
            ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#F3F4F6')),
            ('GRID', (0, 0), (-1, -1), 0.75, colors.HexColor('#CCCCCC')),
            ('FONTNAME', (0, 0), (-1, -1), font_name),
            ('PADDING', (0, 0), (-1, -1), 4),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(ct)
        elements.append(Spacer(1, 0.1*cm))
    
    elements.append(Paragraph(
        f"Çalışma, aynı tüketim miktarı (<b>{fmt_num(consumption, 2)} kWh</b>), aynı dağıtım bedelleri ve aynı vergi kalemleri esas alınarak yapılmış; "
        f"fark yalnızca enerji tedarik bedelinden kaynaklanmaktadır.",
        letter_style
    ))
    elements.append(Spacer(1, 0.1*cm))
    
    # Enerji Bedelinin Hesaplama Yapısı
    elements.append(Paragraph("<b>Enerji Bedelinin Hesaplama Yapısı</b>", letter_style))
    elements.append(Paragraph(
        f"Enerji bedeli, EPİAŞ verileri esas alınarak oluşturulmaktadır. İlgili fatura dönemi için EPİAŞ saatlik PTF ile "
        f"abonenin tüketim değerleri kullanılarak Ağırlıklı PTF hesaplanır. Üzerine YEKDEM birim bedeli eklenerek toplam enerji birim maliyeti "
        f"oluşturulur. Bu maliyet, anlaşma fiyat katsayısı (<b>{params.agreement_multiplier:.2f}</b>) ile çarpılarak nihai enerji bedeline ulaşılır.",
        letter_style
    ))
    elements.append(Spacer(1, 0.1*cm))
    
    # YEKDEM Uygulaması
    elements.append(Paragraph("<b>YEKDEM Uygulaması</b>", letter_style))
    elements.append(Paragraph(
        "YEKDEM bedeli, EPİAŞ tarafından kesinleştirilmediği durumlarda tahmini olarak faturalandırılabilir. "
        "Gerçekleşen değer açıklandığında fark izleyen dönemlerde mahsup edilir.",
        letter_style
    ))
    elements.append(Spacer(1, 0.1*cm))
    
    # Diğer Bedeller
    elements.append(Paragraph("<b>Diğer Bedeller</b>", letter_style))
    elements.append(Paragraph(
        "Dağıtım bedeli, BTV ve KDV gibi regüle edilen kalemlerde mevcut uygulama aynen korunmaktadır.",
        letter_style
    ))
    elements.append(Spacer(1, 0.05*cm))
    
    # ═══════════════════════════════════════════════════════════════════════════
    # KARŞILAŞTIRMA TABLOSU (Diğer Bedeller ile Ticari Şartlar arasında)
    # ═══════════════════════════════════════════════════════════════════════════
    elements.append(Paragraph("<b>MALİYET KARŞILAŞTIRMASI</b>", heading_style))
    
    energy_diff = calculation.current_energy_tl - calculation.offer_energy_tl
    total_diff = abs(calculation.difference_incl_vat_tl)
    savings_pct = abs(calculation.savings_ratio * 100)
    
    savings_data = [
        ["Kalem", "Mevcut Fatura", "Teklifimiz", "Tasarruf"],
        ["Enerji Bedeli", fmt_tl(calculation.current_energy_tl), fmt_tl(calculation.offer_energy_tl), fmt_tl(energy_diff)],
        ["Dağıtım Bedeli", fmt_tl(calculation.current_distribution_tl), fmt_tl(calculation.offer_distribution_tl), "-"],
        ["BTV", fmt_tl(calculation.current_btv_tl), fmt_tl(calculation.offer_btv_tl), "-"],
        ["KDV Matrahı", fmt_tl(calculation.current_vat_matrah_tl), fmt_tl(calculation.offer_vat_matrah_tl), "-"],
        [f"KDV (%{int(getattr(calculation, 'meta_vat_rate', 0.20) * 100)})", fmt_tl(calculation.current_vat_tl), fmt_tl(calculation.offer_vat_tl), "-"],
        ["TOPLAM", fmt_tl(calculation.current_total_with_vat_tl), fmt_tl(calculation.offer_total_with_vat_tl), fmt_tl(total_diff)],
    ]
    # Eşit sütun genişlikleri
    t = Table(savings_data, colWidths=[col4, col4, col4, col4])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#10B981')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#ECFDF5')),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('GRID', (0, 0), (-1, -1), 0.75, colors.HexColor('#CCCCCC')),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('PADDING', (0, 0), (-1, -1), 4),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),  # İlk sütun sola
        ('ALIGN', (1, 0), (-1, 0), 'CENTER'),  # Header ortala
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),  # Sayılar sağa
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 0.05*cm))
    
    # Tasarruf vurgusu - yeşil arka planlı kutu (HTML .save class gibi)
    savings_text = (
        f'<font size="6">Aylık Tasarruf</font><br/>'
        f'<b><font size="10">{fmt_tl(total_diff)} (%{fmt_num(savings_pct)})</font></b>'
    )
    savings_style = ParagraphStyle('SavingsBox', fontSize=10, textColor=colors.white, alignment=1, fontName=font_name, leading=14)
    # Karşılaştırma tablosuyla aynı genişlik: avail_w = 18cm
    savings_box = Table(
        [[Paragraph(savings_text, savings_style)]],
        colWidths=[avail_w],
    )
    savings_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#10B981')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(savings_box)
    elements.append(Spacer(1, 0.05*cm))
    
    # Teklif Parametreleri - Maliyet tablosunun hemen altında
    offer_unit_price = (params.weighted_ptf_tl_per_mwh / 1000 + params.yekdem_tl_per_mwh / 1000) * params.agreement_multiplier
    current_unit_price = calculation.current_energy_tl / consumption if consumption > 0 else 0
    
    param_data = [
        ["Ağırlıklı PTF", f"{fmt_num(params.weighted_ptf_tl_per_mwh)} TL/MWh", "YEKDEM", f"{fmt_num(params.yekdem_tl_per_mwh)} TL/MWh"],
        ["Anlaşma Çarpanı", fmt_num(params.agreement_multiplier), "Teklif Birim Fiyat", f"{fmt_num(offer_unit_price, 4)} TL/kWh"],
        ["Mevcut Birim Fiyat", f"{fmt_num(current_unit_price, 4)} TL/kWh", "Birim Fiyat Farkı", f"{fmt_num(current_unit_price - offer_unit_price, 4)} TL/kWh"],
    ]
    # 4 eşit sütun
    t = Table(param_data, colWidths=[col4, col4, col4, col4])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F3F4F6')),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#F3F4F6')),
        ('GRID', (0, 0), (-1, -1), 0.75, colors.HexColor('#CCCCCC')),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('PADDING', (0, 0), (-1, -1), 4),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),  # Değerler sola
        ('ALIGN', (3, 0), (3, -1), 'LEFT'),  # Değerler sola
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 0.05*cm))
    
    # Fatura Bilgileri - Teklif Parametreleri'nin hemen altında
    elements.append(Paragraph("<b>Fatura Bilgileri</b>", letter_style))
    invoice_data = [
        ["Tedarikçi", vendor, "Dönem", period],
        ["Tüketim", f"{fmt_num(consumption)} kWh", "Tarife Grubu", tariff_group],
    ]
    t = Table(invoice_data, colWidths=[col4, col4, col4, col4])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F3F4F6')),
        ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#F3F4F6')),
        ('GRID', (0, 0), (-1, -1), 0.75, colors.HexColor('#CCCCCC')),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('PADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 0.05*cm))
    
    # Sonuç paragrafı - ilk sayfada kalsın
    elements.append(Paragraph(
        f"Yapılan hesaplamalar sonucunda; mevcut durumda KDV hariç toplam bedel <b>{fmt_tl(calculation.current_vat_matrah_tl)}</b>, "
        f"teklifimiz kapsamında KDV hariç toplam bedel <b>{fmt_tl(calculation.offer_vat_matrah_tl)}</b> olmak üzere, "
        f"<b>KDV hariç %{fmt_num(savings_pct)} oranında tasarruf</b> sağlanmaktadır.",
        letter_style
    ))
    elements.append(Spacer(1, 0.05*cm))
    
    if contact_person:
        elements.append(Paragraph(f"İlgili: {contact_person}", letter_style))
    elements.append(Paragraph("Bilgilerinize sunarız. Saygılarımızla, <b>Gelka Enerji</b>", letter_style))
    
    # ═══════════════════════════════════════════════════════════════════════════
    # TİCARİ ŞARTLAR - Aynı sayfada devam et (PageBreak kaldırıldı)
    # ═══════════════════════════════════════════════════════════════════════════
    elements.append(Spacer(1, 0.05*cm))
    
    # Ticari Şartlar - yeşil sol kenarlı kutu (HTML .terms class gibi)
    terms_title_style = ParagraphStyle('TermsTitle', fontSize=9, fontName=font_name, textColor=colors.HexColor('#1F2937'), leading=13)
    terms_item_style = ParagraphStyle('TermsItem', fontSize=8, fontName=font_name, textColor=colors.HexColor('#333333'), leading=12)
    
    green = '#10B981'
    terms_content = Paragraph(
        f'<b>Ticari Şartlar</b><br/>'
        f'<font color="{green}">□</font> Fatura vadesi +10 gün&nbsp;&nbsp;&nbsp;'
        f'<font color="{green}">□</font> Teminat&nbsp;&nbsp;&nbsp;'
        f'<font color="{green}">□</font> Güvence Bedeli&nbsp;&nbsp;&nbsp;'
        f'<font color="{green}">□</font> Ön ödeme',
        terms_title_style
    )
    terms_box = Table([[terms_content]], colWidths=[avail_w])
    terms_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F9F9F9')),
        ('LINEBEFORE', (0, 0), (0, -1), 3, colors.HexColor('#10B981')),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(terms_box)
    elements.append(Spacer(1, 0.1*cm))
    
    # Ek Bilgiler
    ek_bilgi_style = ParagraphStyle('EkBilgi', parent=styles['Normal'], fontSize=7.5, fontName=font_name, leading=10, textColor=colors.HexColor('#6B7280'))
    elements.append(Paragraph(f"<b>Ek Bilgiler:</b> Bu teklif, mevcut fatura verileriniz esas alınarak hazırlanmıştır. Gerçek tasarruf tutarları, tüketim miktarı ve piyasa koşullarına göre değişiklik gösterebilir. Teklif {offer_validity_days} gün süreyle geçerlidir.", ek_bilgi_style))
    
    # Build with header/footer
    doc.build(elements, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
    return buffer.getvalue()


def generate_offer_pdf_bytes(
    extraction: InvoiceExtraction,
    calculation: CalculationResult,
    params: OfferParams,
    customer_name: Optional[str] = None,
    customer_company: Optional[str] = None,
    offer_id: Optional[int] = None,
    contact_person: Optional[str] = None,
    offer_date: Optional[str] = None,
    offer_validity_days: int = 15,
) -> bytes:
    """
    Generate PDF offer document as bytes.

    Priority: ReportLab (best layout with header/footer PNG) > Playwright > WeasyPrint

    Returns:
        PDF file bytes (ready for storage.put_bytes)
    """
    # 1) ReportLab FIRST — produces the best layout with header.png/footer.png
    if REPORTLAB_AVAILABLE:
        try:
            logger.info("Attempting ReportLab PDF generation (primary)...")
            pdf_bytes = _generate_pdf_reportlab(
                extraction, calculation, params,
                customer_name, customer_company, offer_id,
                contact_person=contact_person,
                offer_date=offer_date,
                offer_validity_days=offer_validity_days,
            )
            logger.info(f"Generated PDF with ReportLab: {len(pdf_bytes)} bytes for offer {offer_id}")
            return pdf_bytes
        except Exception as e:
            logger.warning(f"ReportLab PDF generation failed: {e}")
            import traceback
            logger.warning(traceback.format_exc())

    # 2) Fallback: Playwright (HTML template)
    try:
        html_content = generate_offer_html(
            extraction, calculation, params,
            customer_name, customer_company, offer_id,
            contact_person=contact_person,
            offer_date=offer_date,
            offer_validity_days=offer_validity_days,
        )
        logger.info(f"HTML generated successfully, length: {len(html_content)}")
        logger.info("Attempting Playwright PDF generation (fallback)...")
        pdf_bytes = _html_to_pdf_playwright(html_content)
        logger.info(f"Generated PDF with Playwright: {len(pdf_bytes)} bytes for offer {offer_id}")
        return pdf_bytes
    except Exception as e:
        import traceback
        logger.warning(f"Playwright PDF generation failed: {e}")
        logger.warning(traceback.format_exc())

    # 3) Final fallback: WeasyPrint
    if WEASYPRINT_AVAILABLE:
        try:
            html_content = generate_offer_html(
                extraction, calculation, params,
                customer_name, customer_company, offer_id,
                contact_person=contact_person,
                offer_date=offer_date,
                offer_validity_days=offer_validity_days,
            )
            pdf_bytes = _html_to_pdf_weasyprint(html_content)
            logger.info(f"Generated PDF with WeasyPrint: {len(pdf_bytes)} bytes for offer {offer_id}")
            return pdf_bytes
        except Exception as e:
            logger.warning(f"WeasyPrint failed: {e}")

    raise RuntimeError(f"PDF generation failed. ReportLab: {'available' if REPORTLAB_AVAILABLE else 'unavailable'}. WeasyPrint: {'available' if WEASYPRINT_AVAILABLE else 'unavailable'}.")



def generate_and_store_offer_pdf(
    extraction: InvoiceExtraction,
    calculation: CalculationResult,
    params: OfferParams,
    offer_id: int,
    customer_name: Optional[str] = None,
    customer_company: Optional[str] = None,
) -> str:
    """
    Generate PDF and store to storage backend.
    
    Returns:
        Storage reference (local path or s3://bucket/key)
    """
    from .services.storage import get_storage
    
    # Generate PDF bytes
    pdf_bytes = generate_offer_pdf_bytes(
        extraction, calculation, params,
        customer_name, customer_company, offer_id
    )
    
    # Store to backend
    storage = get_storage()
    pdf_ref = storage.put_bytes(
        key=f"offers/{offer_id}/offer.pdf",
        data=pdf_bytes,
        content_type="application/pdf"
    )
    
    logger.info(f"Stored PDF: {pdf_ref}")
    return pdf_ref
