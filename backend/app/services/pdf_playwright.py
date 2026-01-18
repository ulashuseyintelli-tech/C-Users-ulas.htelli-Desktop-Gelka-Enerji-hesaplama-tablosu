"""
Playwright PDF Renderer - Fallback for WeasyPrint.

Uses headless Chromium for HTML → PDF conversion.
Works on Windows without Cairo/Pango dependencies.

Setup (one-time):
    python -m playwright install chromium
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import - playwright may not be installed
_playwright_available: Optional[bool] = None


def is_playwright_available() -> bool:
    """Check if playwright is installed and usable."""
    global _playwright_available
    if _playwright_available is None:
        try:
            from playwright.async_api import async_playwright
            _playwright_available = True
        except ImportError:
            _playwright_available = False
            logger.warning("Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
    return _playwright_available


async def html_to_pdf_bytes_async(html: str) -> bytes:
    """
    Convert HTML string to PDF bytes using headless Chromium.
    
    Args:
        html: Complete HTML document string
    
    Returns:
        PDF file bytes
    
    Raises:
        RuntimeError: If playwright/chromium not available
    """
    if not is_playwright_available():
        raise RuntimeError("Playwright not available. Install: pip install playwright && python -m playwright install chromium")
    
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={
                    "top": "15mm",
                    "right": "15mm",
                    "bottom": "15mm",
                    "left": "15mm"
                },
            )
            return pdf_bytes
        finally:
            await browser.close()


def html_to_pdf_bytes_sync(html: str) -> bytes:
    """
    Sync wrapper for FastAPI endpoints.
    
    Args:
        html: Complete HTML document string
    
    Returns:
        PDF file bytes
    """
    import nest_asyncio
    
    # Allow nested event loops (for FastAPI async context)
    try:
        nest_asyncio.apply()
    except:
        pass
    
    # Check if we're already in an event loop
    try:
        loop = asyncio.get_running_loop()
        # We're in an async context - create new loop in thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(_run_async_in_new_loop, html)
            return future.result(timeout=60)
    except RuntimeError:
        # No event loop - safe to use asyncio.run
        return asyncio.run(html_to_pdf_bytes_async(html))


def _run_async_in_new_loop(html: str) -> bytes:
    """Run async PDF generation in a new event loop (for thread)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(html_to_pdf_bytes_async(html))
    finally:
        loop.close()


def html_to_pdf_bytes_sync_v2(html: str) -> bytes:
    """
    Alternative sync wrapper using sync Playwright API.
    """
    if not is_playwright_available():
        raise RuntimeError("Playwright not available")
    
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="networkidle")
            pdf_bytes = page.pdf(
                format="A4",
                print_background=True,
                margin={
                    "top": "15mm",
                    "right": "15mm",
                    "bottom": "15mm",
                    "left": "15mm"
                },
            )
            return pdf_bytes
        finally:
            browser.close()
