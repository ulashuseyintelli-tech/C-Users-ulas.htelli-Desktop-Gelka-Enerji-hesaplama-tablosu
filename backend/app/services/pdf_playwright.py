"""
Playwright PDF Renderer - Deterministic HTML → PDF

Uses headless Chromium for HTML → PDF conversion.
CSS controls page size/margins - Playwright just renders.

Setup (one-time):
    python -m playwright install chromium

CRITICAL RULES:
- scale = 1.0 ALWAYS (never change)
- prefer_css_page_size = True (CSS @page controls size)
- margin = 0 (CSS @page controls margins)
- emulate_media("print") before PDF generation
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_playwright_available: Optional[bool] = None


def is_playwright_available() -> bool:
    """Check if playwright is installed and usable."""
    global _playwright_available
    if _playwright_available is None:
        try:
            from playwright.sync_api import sync_playwright
            _playwright_available = True
        except ImportError:
            _playwright_available = False
            logger.warning("Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
    return _playwright_available


def html_to_pdf_bytes_sync_v2(html: str) -> bytes:
    """
    Convert HTML to PDF using headless Chromium (sync API).
    """
    if not is_playwright_available():
        raise RuntimeError("Playwright not available")
    
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.set_content(html, wait_until="networkidle")
            page.emulate_media(media="print")
            
            pdf_bytes = page.pdf(
                print_background=True,
                prefer_css_page_size=True,
                scale=1.0,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            return pdf_bytes
        finally:
            browser.close()


# Legacy function for backward compatibility
def html_to_pdf_bytes_sync(html: str) -> bytes:
    """Sync wrapper - redirects to v2."""
    return html_to_pdf_bytes_sync_v2(html)
