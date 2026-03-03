"""
Smoke test: Footer ikonlari ANTETLI KAGIT OLMADAN duzgun gorunuyor mu?
Eger bu testte ikonlar tam gorunuyorsa, sorun antetli kagidin footer'i ortmesi.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

def main():
    # Mevcut template'i oku
    tpl_path = os.path.join(os.path.dirname(__file__), "app", "templates", "offer_template.html")
    with open(tpl_path, "r", encoding="utf-8") as f:
        html = f.read()
    
    # Jinja tag'lerini basit degerlerle degistir (letterhead OLMADAN)
    html = html.replace("{{ offer_id }}", "TEST-001")
    html = html.replace("{{ date }}", "03.03.2026")
    html = html.replace("{{ greeting }}", "Sayin Test Yetkili,")
    html = html.replace("{{ tariff_group }}", "Sanayi")
    html = html.replace("{% if letterhead_base64 %}", "{% if false %}")
    
    # Diger Jinja tag'lerini temizle
    import re
    html = re.sub(r'\{\{.*?\}\}', '-', html)
    html = re.sub(r'\{%.*?%\}', '', html)
    
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        page.set_content(html, wait_until="load")
        page.emulate_media(media="print")
        
        import time
        time.sleep(1)
        
        ss = os.path.join(os.path.dirname(__file__), "smoke_footer_NO_letterhead.png")
        page.screenshot(path=ss, full_page=True)
        print(f"Screenshot (NO letterhead): {ss}")
        
        pdf_bytes = page.pdf(print_background=True, prefer_css_page_size=True, scale=1.0)
        browser.close()

    out = os.path.join(os.path.dirname(__file__), "smoke_footer_NO_letterhead.pdf")
    with open(out, "wb") as f:
        f.write(pdf_bytes)
    print(f"PDF (NO letterhead): {out} ({len(pdf_bytes)} bytes)")
    print("Ac ve ikonlari kontrol et - antetli kagit YOK!")

if __name__ == "__main__":
    main()
