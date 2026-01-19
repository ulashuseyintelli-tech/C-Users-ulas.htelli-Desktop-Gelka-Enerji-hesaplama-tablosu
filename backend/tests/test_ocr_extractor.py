"""
OCR Extractor Tests

Tesseract kurulu olmasa bile calisacak testler.
parse_tr_float ve regex pattern'leri test eder.
"""

import pytest

from backend.app.ocr_extractor import (
    parse_tr_float,
    parse_invoice_values,
    OCRResult,
    merge_ocr_results,
    create_ocr_hint,
    TESSERACT_AVAILABLE,
)


class TestParseTrFloat:
    """Turkce sayi parse testleri"""
    
    def test_simple_number(self):
        """Basit sayi"""
        assert parse_tr_float("1234") == 1234.0
    
    def test_with_decimal(self):
        """Ondalikli sayi"""
        assert parse_tr_float("1234,56") == 1234.56
    
    def test_with_thousands(self):
        """Binlik ayracli"""
        assert parse_tr_float("1.234,56") == 1234.56
    
    def test_large_number(self):
        """Buyuk sayi"""
        assert parse_tr_float("593.740,00") == 593740.00
    
    def test_very_large_number(self):
        """Cok buyuk sayi"""
        assert parse_tr_float("1.234.567,89") == 1234567.89
    
    def test_with_tl_suffix(self):
        """TL soneki ile"""
        assert parse_tr_float("593.740,00 TL") == 593740.00
    
    def test_with_lira_symbol(self):
        """Lira sembolu ile"""
        assert parse_tr_float("593.740,00₺") == 593740.00
    
    def test_with_spaces(self):
        """Bosluklu"""
        assert parse_tr_float(" 593.740,00 ") == 593740.00
    
    def test_empty_string(self):
        """Bos string"""
        assert parse_tr_float("") is None
    
    def test_none(self):
        """None input"""
        assert parse_tr_float(None) is None
    
    def test_invalid_string(self):
        """Gecersiz string"""
        assert parse_tr_float("abc") is None


class TestParseInvoiceValues:
    """Fatura degeri cikarma testleri"""
    
    def test_odenecek_tutar_basic(self):
        """Odenecek Tutar basit pattern"""
        text = "Odenecek Tutar: 593.740,00 TL"
        result = parse_invoice_values(text)
        assert result.payable_total == 593740.00
    
    def test_odenecek_tutar_uppercase(self):
        """ODENECEK TUTAR buyuk harf"""
        text = "ODENECEK TUTAR: 593.740,00"
        result = parse_invoice_values(text)
        assert result.payable_total == 593740.00
    
    def test_genel_toplam(self):
        """Genel Toplam pattern"""
        text = "Genel Toplam: 593.740,00 TL"
        result = parse_invoice_values(text)
        assert result.payable_total == 593740.00
    
    def test_kdv_with_matrah(self):
        """KDV (Matrah X) Y pattern"""
        text = "KDV (Matrah 494.781,19) 98.956,24"
        result = parse_invoice_values(text)
        assert result.vat_amount == 98956.24
        assert result.vat_base == 494781.19
    
    def test_kdv_simple(self):
        """KDV basit pattern"""
        text = "KDV: 98.956,24 TL"
        result = parse_invoice_values(text)
        assert result.vat_amount == 98956.24
    
    def test_consumption_kwh(self):
        """Tuketim kWh pattern"""
        text = "Toplam Tuketim: 116.145,630 kWh"
        result = parse_invoice_values(text)
        assert result.consumption_kwh == 116145.63
    
    def test_aktif_toplam(self):
        """AKTIF TOPLAM pattern"""
        text = "AKTIF TOPLAM 116.145,630"
        result = parse_invoice_values(text)
        assert result.consumption_kwh == 116145.63
    
    def test_enerji_bedeli(self):
        """Enerji Bedeli pattern"""
        text = "Enerji Bedeli: 250.000,00 TL"
        result = parse_invoice_values(text)
        assert result.energy_total == 250000.00
    
    def test_dagitim_bedeli(self):
        """Dagitim Bedeli pattern"""
        text = "Dagitim Bedeli: 75.000,00 TL"
        result = parse_invoice_values(text)
        assert result.distribution_total == 75000.00
    
    def test_full_invoice_text(self):
        """Tam fatura metni"""
        text = """
        CK BOGAZICI ELEKTRIK
        
        Fatura No: BBE2025000297356
        
        AKTIF TOPLAM 116.145,630
        
        Enerji Bedeli: 350.000,00 TL
        Dagitim Bedeli: 85.000,00 TL
        
        KDV (Matrah 494.781,19) 98.956,24
        
        ODENECEK TUTAR: 593.740,00 TL
        """
        result = parse_invoice_values(text)
        
        assert result.payable_total == 593740.00
        assert result.vat_amount == 98956.24
        assert result.vat_base == 494781.19
        assert result.consumption_kwh == 116145.63
        assert result.energy_total == 350000.00
        assert result.distribution_total == 85000.00
    
    def test_evidence_tracking(self):
        """Evidence kaydi"""
        text = "Odenecek Tutar: 593.740,00"
        result = parse_invoice_values(text)
        
        assert "payable_total" in result.evidence
        assert result.evidence["payable_total"]["pattern"] == "odenecek_tutar"
        assert result.evidence["payable_total"]["raw"] == "593.740,00"


class TestOCRResult:
    """OCRResult dataclass testleri"""
    
    def test_field_count_empty(self):
        """Bos result"""
        result = OCRResult()
        assert result.field_count() == 0
    
    def test_field_count_partial(self):
        """Kismi result"""
        result = OCRResult(payable_total=100.0, vat_amount=20.0)
        assert result.field_count() == 2
    
    def test_field_count_full(self):
        """Tam result"""
        result = OCRResult(
            payable_total=100.0,
            vat_amount=20.0,
            energy_total=50.0,
            distribution_total=30.0,
            consumption_kwh=1000.0
        )
        assert result.field_count() == 5
    
    def test_to_dict(self):
        """Dict donusumu"""
        result = OCRResult(payable_total=100.0, confidence=0.9)
        d = result.to_dict()
        
        assert d["payable_total"] == 100.0
        assert d["confidence"] == 0.9
        assert d["vat_amount"] is None


class TestMergeOCRResults:
    """OCR sonuc birlestirme testleri"""
    
    def test_merge_empty(self):
        """Bos liste"""
        result = merge_ocr_results([])
        assert result.field_count() == 0
    
    def test_merge_single(self):
        """Tek sonuc"""
        r1 = OCRResult(payable_total=100.0, confidence=0.9)
        result = merge_ocr_results([r1])
        assert result.payable_total == 100.0
    
    def test_merge_multiple_best_confidence(self):
        """Birden fazla sonuc - en iyi confidence"""
        r1 = OCRResult(payable_total=100.0, confidence=0.7)
        r2 = OCRResult(payable_total=200.0, confidence=0.9)
        
        result = merge_ocr_results([r1, r2])
        assert result.payable_total == 200.0  # Yuksek confidence
    
    def test_merge_complementary(self):
        """Tamamlayici sonuclar"""
        r1 = OCRResult(payable_total=100.0, confidence=0.9)
        r2 = OCRResult(vat_amount=20.0, confidence=0.8)
        
        result = merge_ocr_results([r1, r2])
        assert result.payable_total == 100.0
        assert result.vat_amount == 20.0


class TestCreateOCRHint:
    """OCR hint olusturma testleri"""
    
    def test_hint_poor_quality(self):
        """Dusuk kalite - hint yok"""
        result = OCRResult(extraction_quality="poor")
        hint = create_ocr_hint(result)
        assert hint == ""
    
    def test_hint_with_values(self):
        """Degerli result - hint var"""
        result = OCRResult(
            payable_total=593740.00,
            vat_amount=98956.24,
            extraction_quality="good"
        )
        hint = create_ocr_hint(result)
        
        assert "593740.00" in hint
        assert "98956.24" in hint
        assert "OCR" in hint
    
    def test_hint_format(self):
        """Hint formati"""
        result = OCRResult(
            payable_total=100.0,
            extraction_quality="medium"
        )
        hint = create_ocr_hint(result)
        
        assert "[OCR CROSS-CHECK]" in hint
        assert "dogrula" in hint.lower()


class TestTesseractAvailability:
    """Tesseract kullanilabilirlik testi"""
    
    def test_tesseract_flag_exists(self):
        """TESSERACT_AVAILABLE flag'i var"""
        assert isinstance(TESSERACT_AVAILABLE, bool)
    
    @pytest.mark.skipif(not TESSERACT_AVAILABLE, reason="Tesseract not installed")
    def test_tesseract_works(self):
        """Tesseract calisiyorsa basit test"""
        from app.ocr_extractor import extract_text_from_image
        from PIL import Image
        import io
        
        # Basit beyaz gorsel olustur
        img = Image.new('RGB', (100, 100), color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        
        # OCR calistir (bos metin donmeli)
        text = extract_text_from_image(buf.getvalue())
        assert isinstance(text, str)
