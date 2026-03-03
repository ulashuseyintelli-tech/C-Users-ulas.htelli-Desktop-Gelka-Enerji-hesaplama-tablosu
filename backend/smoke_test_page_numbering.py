"""
Smoke test: pdf_page_numbering footer ikonlarının kırpılıp kırpılmadığını test eder.
Boş bir A4 PDF oluşturup stamp_page_numbers ile footer damgalar.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as rl_canvas


def main():
    # Boş A4 PDF oluştur
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 14)
    c.drawString(100, 700, "Footer Icon Test - Sayfa 1")
    c.showPage()
    c.drawString(100, 700, "Footer Icon Test - Sayfa 2")
    c.showPage()
    c.save()
    raw_pdf = buf.getvalue()

    # stamp_page_numbers ile footer ekle
    from app.services.pdf_page_numbering import stamp_page_numbers
    stamped = stamp_page_numbers(raw_pdf)

    out = os.path.join(os.path.dirname(__file__), "smoke_page_numbering_test.pdf")
    with open(out, "wb") as f:
        f.write(stamped)
    print(f"PDF: {out} ({len(stamped)} bytes)")
    print("Ac ve footer ikonlarini kontrol et!")


if __name__ == "__main__":
    main()
