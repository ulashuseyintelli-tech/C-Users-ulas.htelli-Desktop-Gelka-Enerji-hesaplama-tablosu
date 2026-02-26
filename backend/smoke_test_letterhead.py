"""
Smoke test: Antetli kağıt PNG'nin Playwright PDF'e basılıp basılmadığını test eder.
Çalıştır: python -m smoke_test_letterhead  (backend/ dizininden)
veya:     python backend/smoke_test_letterhead.py
"""
import base64
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

def main():
    # 1) PNG'yi yükle
    png_path = os.path.join(os.path.dirname(__file__), "app", "templates", "antetli_bg_300dpi.png")
    if not os.path.exists(png_path):
        png_path = os.path.join(os.path.dirname(__file__), "app", "templates", "antetli_bg.png")
    
    if not os.path.exists(png_path):
        print("HATA: Antetli PNG bulunamadı!")
        return
    
    with open(png_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    
    print(f"PNG loaded: {png_path}")
    print(f"Base64 length: {len(b64)}")
    print(f"Base64 starts: {b64[:40]}")
    
    # 2) Minimal HTML - sadece antet
    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
@page {{ size: A4; margin: 0; }}
html, body {{ margin:0; padding:0; width:210mm; height:297mm; }}
.letterhead {{
  position: fixed;
  top: 0; left: 0;
  width: 210mm; height: 297mm;
  z-index: 0;
}}
.letterhead img {{
  width: 100%; height: 100%;
  object-fit: fill; display: block;
}}
.content {{
  position: relative; z-index: 1;
  padding: 60mm 20mm 35mm 20mm;
  background: transparent;
}}
h1 {{ color: red; text-align: center; }}
</style>
</head>
<body>
<div class="letterhead">
  <img src="data:image/png;base64,{b64}" alt="antet" />
</div>
<div class="content">
  <h1>SMOKE TEST - ANTET GORUNUYOR MU?</h1>
  <p>Bu metin antetli kağıdın üstünde görünmeli.</p>
</div>
</body>
</html>"""
    
    print(f"HTML length: {len(html)}")
    print(f"Contains base64 image: {'data:image/png;base64,' in html}")
    
    # 3) Playwright ile PDF oluştur
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("HATA: playwright yüklü değil! pip install playwright && python -m playwright install chromium")
        return
    
    print("Playwright başlatılıyor...")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        
        page.set_content(html, wait_until="load")
        page.emulate_media(media="print")
        
        # Image yüklenmesini bekle
        page.wait_for_function(
            "() => Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)",
            timeout=15000,
        )
        
        img_count = page.evaluate("document.images.length")
        img_complete = page.evaluate("Array.from(document.images).every(img => img.complete)")
        img_width = page.evaluate("document.images[0] ? document.images[0].naturalWidth : 0")
        print(f"Images in DOM: {img_count}, all complete: {img_complete}, first naturalWidth: {img_width}")
        
        # Screenshot al (debug)
        screenshot_path = os.path.join(os.path.dirname(__file__), "smoke_screenshot.png")
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"Screenshot saved: {screenshot_path}")
        
        # PDF al
        pdf_bytes = page.pdf(
            print_background=True,
            prefer_css_page_size=True,
            scale=1.0,
        )
        
        browser.close()
    
    pdf_path = os.path.join(os.path.dirname(__file__), "smoke_test_output3.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    
    print(f"PDF saved: {pdf_path} ({len(pdf_bytes)} bytes)")
    print("Şimdi smoke_test_output.pdf dosyasını aç ve anteti kontrol et!")

if __name__ == "__main__":
    main()
