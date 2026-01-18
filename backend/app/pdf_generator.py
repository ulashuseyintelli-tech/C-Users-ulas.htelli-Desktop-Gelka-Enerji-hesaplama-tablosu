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

# Template directory
TEMPLATE_DIR = Path(__file__).parent / "templates"
OUTPUT_DIR = Path(__file__).parent.parent / "outputs"

# Ensure directories exist
TEMPLATE_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Jinja2 environment
env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))


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


def generate_offer_html(
    extraction: InvoiceExtraction,
    calculation: CalculationResult,
    params: OfferParams,
    customer_name: Optional[str] = None,
    customer_company: Optional[str] = None,
    offer_id: Optional[int] = None,
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
    
    context = {
        "offer_id": offer_id or datetime.now().strftime("%Y%m%d%H%M%S"),
        "date": datetime.now().strftime("%d.%m.%Y"),
        "customer_name": customer_name or "Sayın Müşterimiz",
        "customer_company": customer_company,
        
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
        customer_name, customer_company, offer_id
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
    """Create the default offer template"""
    template_path = TEMPLATE_DIR / "offer_template.html"
    
    # Always recreate template to ensure latest version
    template_content = '''<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <title>Enerji Teklifi - {{ offer_id }}</title>
</head>
<body>
    <div class="header">
        <div class="logo">GELKA ENERJİ</div>
        <div class="offer-title">Enerji Tasarruf Teklifi</div>
        <div class="offer-meta">
            Teklif No: {{ offer_id }} | Tarih: {{ date }}
        </div>
    </div>

    <div class="section">
        <div class="section-title">Müşteri Bilgileri</div>
        <p><strong>{{ customer_name }}</strong></p>
        {% if customer_company %}<p>{{ customer_company }}</p>{% endif %}
    </div>

    <div class="section">
        <div class="section-title">Mevcut Fatura Analizi</div>
        <table>
            <tr>
                <th>Tedarikçi</th>
                <td>{{ vendor | title }}</td>
                <th>Fatura Dönemi</th>
                <td>{{ invoice_period }}</td>
            </tr>
            <tr>
                <th>Tüketim</th>
                <td>{{ consumption_kwh | number }} kWh</td>
                <th>Aktif Enerji Birim Fiyat</th>
                <td>{{ current_unit_price | number(4) }} TL/kWh</td>
            </tr>
            <tr>
                <th>Dağıtım Birim Fiyat</th>
                <td>{{ distribution_unit_price | number(4) }} TL/kWh</td>
                <th>Demand</th>
                <td>{{ demand_qty | number }} × {{ demand_unit_price | number(2) }} TL</td>
            </tr>
        </table>
    </div>

    <div class="savings-box">
        <div class="savings-label">Aylık Tahmini Tasarruf</div>
        <div class="savings-amount">{{ calc.difference_incl_vat_tl | currency }}</div>
        <div class="savings-label">{{ calc.savings_ratio | percent }} tasarruf oranı</div>
    </div>

    <!-- Yıllık Projeksiyon ve Tasarruf Detayı -->
    <div class="section">
        <div class="section-title">Tasarruf Projeksiyonu</div>
        <table>
            <thead>
                <tr>
                    <th>Metrik</th>
                    <th>Değer</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>kWh başı toplam maliyet (Mevcut)</td>
                    <td>{{ calc.current_total_tl_per_kwh | number(4) }} TL/kWh</td>
                </tr>
                <tr>
                    <td>kWh başı toplam maliyet (Teklif)</td>
                    <td>{{ calc.offer_total_tl_per_kwh | number(4) }} TL/kWh</td>
                </tr>
                <tr style="background-color: #ECFDF5;">
                    <td><strong>kWh başı tasarruf</strong></td>
                    <td style="color: #059669; font-weight: bold;">{{ calc.saving_tl_per_kwh | number(4) }} TL/kWh</td>
                </tr>
                <tr>
                    <td>Aylık tasarruf</td>
                    <td>{{ calc.difference_incl_vat_tl | currency }}</td>
                </tr>
                <tr style="background-color: #ECFDF5;">
                    <td><strong>Yıllık tahmini tasarruf (12 ay)</strong></td>
                    <td style="color: #059669; font-size: 14pt; font-weight: bold;">{{ calc.annual_saving_tl | currency }}</td>
                </tr>
            </tbody>
        </table>
        <div class="notes" style="margin-top: 10px; font-size: 9pt;">
            <p>* Yıllık projeksiyon, mevcut ayın tüketim ve fiyat varsayımlarının 12 ay boyunca benzer kaldığı senaryo üzerinden hesaplanır.</p>
        </div>
    </div>

    <!-- Enerji Birim Fiyat Kıyası -->
    <div class="section">
        <div class="section-title">Enerji Birim Fiyat Karşılaştırması</div>
        <table>
            <tr>
                <th>Mevcut Enerji Birim Fiyat</th>
                <td class="current-col">{{ calc.current_energy_unit_tl_per_kwh | number(4) }} TL/kWh</td>
                <th>Teklif Enerji Birim Fiyat</th>
                <td class="offer-col">{{ calc.offer_energy_unit_tl_per_kwh | number(4) }} TL/kWh</td>
            </tr>
            <tr>
                <th>Birim Fiyat Tasarrufu</th>
                <td colspan="3" style="color: #059669; font-weight: bold;">
                    {{ calc.unit_price_savings_ratio | percent }} 
                    ({{ (calc.current_energy_unit_tl_per_kwh - calc.offer_energy_unit_tl_per_kwh) | number(4) }} TL/kWh)
                </td>
            </tr>
        </table>
    </div>

    <div class="section">
        <div class="section-title">Karşılaştırmalı Analiz</div>
        <table class="comparison-table">
            <thead>
                <tr>
                    <th>Kalem</th>
                    <th class="current-col">Mevcut Fatura</th>
                    <th class="offer-col">Teklifimiz</th>
                    <th>Fark</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>Aktif Enerji Bedeli</td>
                    <td class="current-col">{{ calc.current_energy_tl | currency }}</td>
                    <td class="offer-col">{{ calc.offer_energy_tl | currency }}</td>
                    <td>{{ (calc.current_energy_tl - calc.offer_energy_tl) | currency }}</td>
                </tr>
                <tr>
                    <td>Dağıtım Bedeli</td>
                    <td class="current-col">{{ calc.current_distribution_tl | currency }}</td>
                    <td class="offer-col">{{ calc.offer_distribution_tl | currency }}</td>
                    <td>{{ (calc.current_distribution_tl - calc.offer_distribution_tl) | currency }}</td>
                </tr>
                <tr>
                    <td>Demand Bedeli</td>
                    <td class="current-col">{{ calc.current_demand_tl | currency }}</td>
                    <td class="offer-col">{{ calc.offer_demand_tl | currency }}</td>
                    <td>-</td>
                </tr>
                <tr>
                    <td>BTV (%1)</td>
                    <td class="current-col">{{ calc.current_btv_tl | currency }}</td>
                    <td class="offer-col">{{ calc.offer_btv_tl | currency }}</td>
                    <td>{{ (calc.current_btv_tl - calc.offer_btv_tl) | currency }}</td>
                </tr>
                {% if calc.current_extra_items_tl != 0 %}
                <tr>
                    <td>Ek Kalemler</td>
                    <td class="current-col">{{ calc.current_extra_items_tl | currency }}</td>
                    <td class="offer-col">{{ calc.offer_extra_items_tl | currency }}</td>
                    <td>{{ (calc.current_extra_items_tl - calc.offer_extra_items_tl) | currency }}</td>
                </tr>
                {% endif %}
                <tr>
                    <td>KDV Matrahı</td>
                    <td class="current-col">{{ calc.current_vat_matrah_tl | currency }}</td>
                    <td class="offer-col">{{ calc.offer_vat_matrah_tl | currency }}</td>
                    <td>{{ calc.difference_excl_vat_tl | currency }}</td>
                </tr>
                <tr>
                    <td>KDV (%20)</td>
                    <td class="current-col">{{ calc.current_vat_tl | currency }}</td>
                    <td class="offer-col">{{ calc.offer_vat_tl | currency }}</td>
                    <td>{{ (calc.current_vat_tl - calc.offer_vat_tl) | currency }}</td>
                </tr>
                <tr class="highlight-row">
                    <td><strong>TOPLAM</strong></td>
                    <td class="current-col"><strong>{{ calc.current_total_with_vat_tl | currency }}</strong></td>
                    <td class="offer-col"><strong>{{ calc.offer_total_with_vat_tl | currency }}</strong></td>
                    <td><strong>{{ calc.difference_incl_vat_tl | currency }}</strong></td>
                </tr>
            </tbody>
        </table>
    </div>

    <div class="section">
        <div class="section-title">Teklif Parametreleri</div>
        <table>
            <tr>
                <th>Ağırlıklı PTF</th>
                <td>{{ weighted_ptf | number(2) }} TL/MWh</td>
                <th>YEKDEM</th>
                <td>{{ yekdem | number(2) }} TL/MWh</td>
            </tr>
            <tr>
                <th>Anlaşma Çarpanı</th>
                <td>{{ agreement_multiplier | number(2) }}</td>
                <th>Teklif Birim Fiyat</th>
                <td>{{ offer_unit_price | number(4) }} TL/kWh</td>
            </tr>
        </table>
    </div>

    <!-- Teklif Varsayımları ve Kapsam -->
    <div class="section">
        <div class="section-title">Teklif Varsayımları ve Kapsam</div>
        <table>
            <thead>
                <tr>
                    <th>Başlık</th>
                    <th>Durum</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>Ek kalemler (reaktif/mahsup/düzeltme vb.) teklife dahil</td>
                    <td>
                        {% if extra_items_apply_to_offer %}
                            <span style="color: #059669; font-weight: bold;">Evet (dahil)</span>
                        {% else %}
                            <span style="color: #6B7280;">Hayır (kapsam dışı)</span>
                        {% endif %}
                    </td>
                </tr>
                <tr>
                    <td>Dağıtım bedeli teklifte ayrı birim fiyatla hesaplandı</td>
                    <td>
                        {% if use_offer_distribution %}
                            <span style="color: #059669; font-weight: bold;">Evet ({{ offer_distribution_unit_price | number(4) }} TL/kWh)</span>
                        {% else %}
                            <span style="color: #6B7280;">Hayır (mevcut dağıtım birimi kullanıldı)</span>
                        {% endif %}
                    </td>
                </tr>
            </tbody>
        </table>
        <div class="notes" style="margin-top: 10px; font-size: 9pt;">
            <p><strong>Not:</strong> Ek kalemler; reaktif bedel, hizmet bedelleri, mahsuplaşma/iadeler veya düzeltme kalemleri gibi fatura kalemlerini ifade eder.</p>
            {% if calc.extra_items_note %}
            <p style="margin-top: 5px;">{{ calc.extra_items_note }}</p>
            {% endif %}
        </div>
    </div>

    <div class="section">
        <div class="section-title">Notlar</div>
        <div class="notes">
            <ul>
                <li>Bu teklif, mevcut fatura verilerinize dayanılarak hazırlanmıştır.</li>
                <li>Gerçek tasarruf miktarı, tüketim değişikliklerine göre farklılık gösterebilir.</li>
                <li>Teklif geçerlilik süresi: 15 gün</li>
                <li>Dağıtım bedeli ve vergiler mevcut tarifeler üzerinden hesaplanmıştır.</li>
            </ul>
        </div>
    </div>

    <div class="footer">
        <p>Bu teklif Gelka Enerji tarafından otomatik olarak oluşturulmuştur.</p>
        <p>İletişim: info@gelkaenerji.com | Tel: 0850 XXX XX XX</p>
    </div>
</body>
</html>'''
    
    template_path.write_text(template_content, encoding="utf-8")


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
    
    # Playwright needs full HTML with embedded styles
    full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>{get_pdf_styles()}</style>
</head>
<body>
{html_content}
</body>
</html>"""
    return html_to_pdf_bytes_sync_v2(full_html)


def generate_offer_pdf_bytes(
    extraction: InvoiceExtraction,
    calculation: CalculationResult,
    params: OfferParams,
    customer_name: Optional[str] = None,
    customer_company: Optional[str] = None,
    offer_id: Optional[int] = None,
) -> bytes:
    """
    Generate PDF offer document as bytes.
    
    Uses WeasyPrint if available, falls back to Playwright/Chromium.
    
    Returns:
        PDF file bytes (ready for storage.put_bytes)
    """
    # Generate HTML first
    try:
        html_content = generate_offer_html(
            extraction, calculation, params,
            customer_name, customer_company, offer_id
        )
        logger.info(f"HTML generated successfully, length: {len(html_content)}")
    except Exception as e:
        logger.error(f"HTML generation failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise RuntimeError(f"HTML generation failed: {e}")
    
    # 1) Try WeasyPrint first (faster, better quality)
    if WEASYPRINT_AVAILABLE:
        try:
            pdf_bytes = _html_to_pdf_weasyprint(html_content)
            logger.info(f"Generated PDF with WeasyPrint: {len(pdf_bytes)} bytes for offer {offer_id}")
            return pdf_bytes
        except Exception as e:
            logger.warning(f"WeasyPrint failed, falling back to Playwright: {e}")
    
    # 2) Fallback to Playwright (works on Windows without Cairo/Pango)
    try:
        logger.info("Attempting Playwright PDF generation...")
        pdf_bytes = _html_to_pdf_playwright(html_content)
        logger.info(f"Generated PDF with Playwright: {len(pdf_bytes)} bytes for offer {offer_id}")
        return pdf_bytes
    except Exception as e:
        logger.error(f"Playwright PDF generation failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise RuntimeError(f"PDF generation failed. WeasyPrint: {'available' if WEASYPRINT_AVAILABLE else 'unavailable'}. Playwright error: {e}")


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
