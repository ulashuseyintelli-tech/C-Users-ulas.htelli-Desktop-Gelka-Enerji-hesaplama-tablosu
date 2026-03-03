"""
PDF Post-Process: Sayfa Numarası Damgalama

Letterhead PNG zaten iletişim footer'ını (globe + phone + mail ikonları)
içerdiğinden, bu modül yalnızca sayfa numarası (Sayfa X / N) damgalar.

Bağımlılık: pypdf + reportlab
"""
import logging
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.colors import HexColor

logger = logging.getLogger(__name__)

_WIDTH, _HEIGHT = A4  # 595.27, 841.89 points

# Sayfa numarası konumu (A4 altından)
_PAGE_NUM_Y = 28 * mm   # yeşil bandın hemen üstü
_MARGIN_RIGHT = 15 * mm


def _draw_page_number(c, page_num, total_pages):
    """Tek sayfaya sayfa numarası damgala."""
    c.saveState()
    c.setFont("Helvetica", 7)
    c.setFillColor(HexColor('#6B7280'))
    page_text = f"Sayfa {page_num} / {total_pages}"
    c.drawRightString(_WIDTH - _MARGIN_RIGHT, _PAGE_NUM_Y, page_text)
    c.restoreState()


def stamp_page_numbers(pdf_bytes: bytes) -> bytes:
    """Her sayfaya sayfa numarası damgası bas. Tek sayfalık PDF'lerde numara basmaz."""
    reader = PdfReader(BytesIO(pdf_bytes))
    writer = PdfWriter()
    total = len(reader.pages)

    # Tek sayfa ise numara basma, doğrudan döndür
    if total <= 1:
        logger.info("Single page PDF — skipping page numbers")
        return pdf_bytes

    for idx, page in enumerate(reader.pages, start=1):
        overlay_buf = BytesIO()
        c = rl_canvas.Canvas(overlay_buf, pagesize=A4)
        _draw_page_number(c, idx, total)
        c.save()

        overlay_buf.seek(0)
        overlay_page = PdfReader(overlay_buf).pages[0]
        page.merge_page(overlay_page)
        writer.add_page(page)

    out = BytesIO()
    writer.write(out)
    logger.info(f"Page numbers stamped: {total} pages")
    return out.getvalue()
