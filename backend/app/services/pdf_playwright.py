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
    
    # Debug: dump rendered HTML to file for inspection
    try:
        from pathlib import Path
        debug_path = Path(__file__).parent.parent.parent / "debug_rendered.html"
        debug_path.write_text(html[:5000], encoding="utf-8")  # first 5k chars
        logger.info(f"DEBUG: HTML dumped to {debug_path} (first 5000 chars)")
        logger.info(f"DEBUG: HTML total length = {len(html)}")
        # Check if letterhead_base64 is in the HTML
        if "data:image/png;base64," in html:
            idx = html.index("data:image/png;base64,")
            # Find the end of the base64 string (next quote)
            end_idx = html.index('"', idx + 22)
            b64_len = end_idx - idx - 22
            logger.info(f"DEBUG: Found base64 image in HTML, length={b64_len}")
        else:
            logger.warning("DEBUG: NO base64 image found in HTML!")
    except Exception as e:
        logger.warning(f"DEBUG dump failed: {e}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.set_content(html, wait_until="load")
            page.emulate_media(media="print")
            
            # Wait for all images to fully load/decode
            page.wait_for_function(
                "() => Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)",
                timeout=15000,
            )
            logger.info("DEBUG: All images loaded and decoded")
            
            pdf_bytes = page.pdf(
                print_background=True,
                prefer_css_page_size=True,
                scale=1.0,
            )
            return pdf_bytes
        finally:
            browser.close()


# Legacy function for backward compatibility
def html_to_pdf_bytes_sync(html: str) -> bytes:
    """Sync wrapper - redirects to v2."""
    return html_to_pdf_bytes_sync_v2(html)
