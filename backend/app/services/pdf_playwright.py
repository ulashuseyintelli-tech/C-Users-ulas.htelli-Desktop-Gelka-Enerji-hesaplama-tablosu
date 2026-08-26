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

S5-R03A MOTOR SOZLESMESI:
- Bu modul SYNC Playwright API'si kullanir ve calisan bir asyncio
  event-loop'un ICINDEN CAGRILAMAZ (fail-fast guard asagida). Async
  endpoint'ler cagriyi bir worker thread'e (run_in_executor/to_thread)
  tasimak ZORUNDADIR — bkz. app/main.py PDF endpoint'leri.
- Browser bulunabilirligi launch hatasinin FAIL-FAST siniflandirmasiyla
  dogrulanir; bulunamazsa PlaywrightBrowserUnavailable yukselir (fiziksel
  yol LOGLANMAZ ve mesaja YAZILMAZ — S5-R03A sizinti sozlesmesi).

Çağrıldığı yerler:
- app/pdf_generator.py::_html_to_pdf_playwright() → teklif PDF fallback'i
  (app/main.py PDF endpoint'leri uzerinden, executor thread'inde)
- app/contracts/pdf_service.py → html_to_pdf_bytes_sync (sozlesme PDF'i;
  sync `def` endpoint'ler, FastAPI threadpool'unda kosar)
- app/pricing/pricing_report.py::_html_to_pdf() → fiyat raporu PDF'i
  (sync `def` endpoint, FastAPI threadpool'unda kosar)
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_playwright_available: Optional[bool] = None


class PlaywrightBrowserUnavailable(RuntimeError):
    """Playwright paketi var ama calistirabilecegi Chromium binary'si yok.

    S5-R03A: paketli/frozen ortamda browser gomulmemisse fallback 'mevcut'
    SAYILMAZ — bu tip, ust katmanin (pdf_generator motor zinciri) durumu
    fiziksel yol sizdirmadan ayirt edebilmesi icindir.
    """


def is_playwright_available() -> bool:
    """Check if playwright is installed and usable."""
    global _playwright_available
    if _playwright_available is None:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
            _playwright_available = True
        except ImportError:
            _playwright_available = False
            logger.warning("Playwright not installed. Run: pip install playwright && python -m playwright install chromium")
    return _playwright_available


def _calisan_asyncio_loop_var() -> bool:
    """Bu thread'de calisan bir asyncio event-loop var mi?"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def html_to_pdf_bytes_sync_v2(html: str) -> bytes:
    """
    Convert HTML to PDF using headless Chromium (sync API).

    S5-R03A fail-fast kapilari (siralamayla):
    1. Calisan asyncio loop icinden cagri → RuntimeError (sync Playwright
       loop icinde YASAK; cagriyi executor/thread'e tasiyin).
    2. Playwright paketi yok → RuntimeError.
    3. Chromium binary'si bu ortamda yok → launch hatasi fail-fast
       PlaywrightBrowserUnavailable olarak siniflandirilir (yol bilgisi
       loglanmaz/sizdirilmaz; `from None` orijinal zinciri keser).
    """
    if _calisan_asyncio_loop_var():
        raise RuntimeError(
            "sync Playwright calisan asyncio event-loop icinde cagrilamaz; "
            "cagriyi bir worker thread'e (run_in_executor/asyncio.to_thread) tasiyin."
        )
    if not is_playwright_available():
        raise RuntimeError("Playwright not available")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # Browser bulunabilirligi launch hatasinin FAIL-FAST siniflandirmasiyla
        # dogrulanir (S5-R03A): frozen/paketli ortamda binary gomulmemis
        # olabilir. NOT: `p.chromium.executable_path` uzerinden exists-kontrolu
        # BILEREK kullanilmiyor — normal chromium ile headless_shell ayri
        # binary'lerdir ve exists-probe tam-suite kosusunda yanlis-negatif
        # uretti (gercek Chromium E2E testlerini sessizce skip'e dusurdu).
        # Yol bilgisi kasitli olarak NE loglanir NE exception mesajina yazilir
        # (`from None` zinciri keser; orijinal Playwright mesaji yol icerir).
        try:
            browser = p.chromium.launch()
        except Exception as e:
            if "doesn't exist" in str(e).lower() or "executable" in str(e).lower():
                raise PlaywrightBrowserUnavailable(
                    "Playwright Chromium binary'si bu calisma ortaminda bulunamadi; "
                    "Playwright fallback kullanilamaz."
                ) from None
            raise
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            page.set_content(html, wait_until="load")
            page.emulate_media(media="print")

            # Wait for all images to fully load/decode
            page.wait_for_function(
                "() => Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)",
                timeout=15000,
            )

            pdf_bytes = page.pdf(
                print_background=True,
                prefer_css_page_size=True,
                scale=1.0,
            )

            # Post-process: sayfa numarası damgala
            try:
                from .pdf_page_numbering import stamp_page_numbers
                pdf_bytes = stamp_page_numbers(pdf_bytes)
            except Exception as e:
                logger.warning(f"Page numbering failed, returning raw PDF: {e}")

            return pdf_bytes
        finally:
            browser.close()


# Legacy function for backward compatibility
def html_to_pdf_bytes_sync(html: str) -> bytes:
    """Sync wrapper - redirects to v2."""
    return html_to_pdf_bytes_sync_v2(html)
