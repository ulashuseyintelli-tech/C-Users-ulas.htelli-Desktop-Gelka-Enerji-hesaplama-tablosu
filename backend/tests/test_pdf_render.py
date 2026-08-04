"""
render_pdf_pages_from_bytes() / PdfRenderError testleri (Task #52).

Kapsam: yalnız YENİ eklenen, bytes-tabanlı multi-page render fonksiyonu.
Mevcut render_pdf_first_page()'e dokunulmadı, bu yüzden onun testi burada yok.
"""
from __future__ import annotations

import io

import pytest
from reportlab.pdfgen import canvas

from app.pdf_render import render_pdf_pages_from_bytes, PdfRenderError


def _make_pdf_bytes(num_pages: int = 1, text_prefix: str = "Sayfa") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(num_pages):
        c.drawString(100, 750, f"{text_prefix} {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


class TestRenderPdfPagesFromBytes:
    def test_single_page_pdf_returns_one_png(self):
        pdf_bytes = _make_pdf_bytes(num_pages=1)
        pages = render_pdf_pages_from_bytes(pdf_bytes, max_pages=5)
        assert len(pages) == 1
        assert pages[0].startswith(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes

    def test_multi_page_pdf_returns_one_png_per_page(self):
        pdf_bytes = _make_pdf_bytes(num_pages=3)
        pages = render_pdf_pages_from_bytes(pdf_bytes, max_pages=5)
        assert len(pages) == 3
        for page_png in pages:
            assert page_png.startswith(b"\x89PNG\r\n\x1a\n")

    def test_page_order_preserved(self):
        """1. eleman = 1. sayfa olmalı — source_page eşlemesi buna dayanıyor."""
        pdf_bytes = _make_pdf_bytes(num_pages=2)
        pages = render_pdf_pages_from_bytes(pdf_bytes, max_pages=5)
        # Farklı sayfalar farklı render çıktısı üretmeli (aynı bayt dizisi olmamalı)
        assert pages[0] != pages[1]

    def test_no_temp_files_created(self, tmp_path, monkeypatch):
        """Bytes-tabanlı render — hiçbir temp dosya oluşturulmamalı (cleanup gereksiz)."""
        import tempfile
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        pdf_bytes = _make_pdf_bytes(num_pages=2)
        render_pdf_pages_from_bytes(pdf_bytes, max_pages=5)
        assert list(tmp_path.iterdir()) == []


class TestPdfRenderErrorGuards:
    def test_corrupt_pdf_raises_pdf_corrupt(self):
        with pytest.raises(PdfRenderError) as exc_info:
            render_pdf_pages_from_bytes(b"this is definitely not a pdf file", max_pages=5)
        assert exc_info.value.error_code == "pdf_corrupt"

    def test_empty_bytes_raises_pdf_corrupt(self):
        with pytest.raises(PdfRenderError) as exc_info:
            render_pdf_pages_from_bytes(b"", max_pages=5)
        assert exc_info.value.error_code == "pdf_corrupt"

    def test_too_many_pages_rejected_not_truncated(self):
        """Sessizce kırpma YOK — sınırı aşan PDF reddedilir (kullanıcı eksik
        veri aldığının farkında olmayabilir)."""
        pdf_bytes = _make_pdf_bytes(num_pages=3)
        with pytest.raises(PdfRenderError) as exc_info:
            render_pdf_pages_from_bytes(pdf_bytes, max_pages=2)
        assert exc_info.value.error_code == "pdf_too_many_pages"

    def test_page_count_exactly_at_limit_is_allowed(self):
        """Sınırda (== max_pages) reddedilmemeli, yalnız AŞANLAR reddedilir."""
        pdf_bytes = _make_pdf_bytes(num_pages=2)
        pages = render_pdf_pages_from_bytes(pdf_bytes, max_pages=2)
        assert len(pages) == 2
