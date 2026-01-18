"""
HTML to Image Renderer for e-Invoice HTML files.

Uses subprocess to run Playwright in a separate process.
This avoids asyncio conflicts on Windows.
"""
import logging
import subprocess
import sys
import tempfile
import os

logger = logging.getLogger(__name__)


def render_html_to_image(html_content: bytes, width: int = 1200) -> bytes:
    """
    Render HTML content to PNG image using Playwright.
    
    Runs Playwright in a separate subprocess to avoid asyncio conflicts.
    
    Args:
        html_content: HTML file content as bytes
        width: Viewport width in pixels
    
    Returns:
        PNG image as bytes
    """
    # Create temp files for input/output
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.html', delete=False) as html_file:
        html_file.write(html_content)
        html_path = html_file.name
    
    png_path = html_path.replace('.html', '.png')
    
    try:
        # Run Playwright in subprocess
        script = f'''
import sys
from playwright.sync_api import sync_playwright

html_path = r"{html_path}"
png_path = r"{png_path}"
width = {width}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={{"width": width, "height": 800}})
    
    with open(html_path, 'rb') as f:
        content = f.read()
    
    try:
        html_str = content.decode('utf-8')
    except UnicodeDecodeError:
        html_str = content.decode('latin-1')
    
    page.set_content(html_str, wait_until="networkidle")
    page.wait_for_timeout(500)
    
    height = page.evaluate("document.body.scrollHeight")
    page.set_viewport_size({{"width": width, "height": min(height + 100, 16000)}})
    
    page.screenshot(path=png_path, full_page=True, type="png")
    browser.close()
    
print(f"OK:{{height}}")
'''
        
        result = subprocess.run(
            [sys.executable, '-c', script],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout
            logger.error(f"Playwright subprocess error: {error_msg}")
            raise RuntimeError(f"Playwright failed: {error_msg}")
        
        # Read output image
        if not os.path.exists(png_path):
            raise RuntimeError("Screenshot file not created")
        
        with open(png_path, 'rb') as f:
            screenshot = f.read()
        
        # Parse height from output
        output = result.stdout.strip()
        if output.startswith("OK:"):
            height = int(output.split(":")[1])
            logger.info(f"HTML rendered to image: {len(screenshot)} bytes, height={height}px")
        else:
            logger.info(f"HTML rendered to image: {len(screenshot)} bytes")
        
        return screenshot
        
    finally:
        # Cleanup temp files
        try:
            os.unlink(html_path)
        except:
            pass
        try:
            os.unlink(png_path)
        except:
            pass


async def render_html_to_image_async(html_content: bytes, width: int = 1200) -> bytes:
    """
    Async wrapper - just calls sync version since it uses subprocess.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, render_html_to_image, html_content, width)
