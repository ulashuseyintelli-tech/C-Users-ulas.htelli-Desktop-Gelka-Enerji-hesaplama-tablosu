from __future__ import annotations
import os, tempfile
from PIL import Image
from .models import Invoice
from .render import render_pdf_pages, crop_region
from .profiles.ck_bogazici_regions import REGIONS as CK_REGIONS
from .ocr_adapter import ocr_image_to_json
from .parsers import parse_line_items_from_json, compute_total_kwh, compute_weighted_unit_price
from .normalize import tr_to_float, parse_date_iso
from .validators import validate_invoice

def detect_profile_from_filename(pdf_path: str) -> str:
    # v2: hızlı yaklaşım. Prod'da: OCR ile başlık okutup tespit edin.
    fn = os.path.basename(pdf_path).lower()
    if fn.startswith("bbe") or "bogazici" in fn or "ck" in fn:
        return "ck_bogazici"
    if fn.startswith("es0"):
        return "enerjisa"
    if fn.startswith("pba"):
        return "uludag"
    if fn.startswith("eal"):
        return "osmangazi"
    if fn.startswith("kse"):
        return "kolen"
    return "unknown"

def save_tmp(img: Image.Image, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    img.save(path)
    return path

def parse_ck_bogazici(pdf_path: str) -> Invoice:
    inv = Invoice(supplier_profile="ck_bogazici")
    pages = render_pdf_pages(pdf_path, dpi=200)
    # CK örnekleri genelde 1. sayfada
    img = pages[0]

    # 1) Fatura Detayı
    det_img = crop_region(img, CK_REGIONS["fatura_detayi"])
    det_path = save_tmp(det_img, ".png")
    det_prompt = open(os.path.join(os.path.dirname(__file__), "..", "docs", "openai_prompt_templates.md"), "r", encoding="utf-8").read()
    # Siz prod'da burada sadece ilgili prompt parçasını verirsiniz.
    det_json = ocr_image_to_json(det_path, prompt="""Sadece Fatura Detayı tablosunu oku ve JSON döndür.""")
    inv.lines = parse_line_items_from_json(det_json.get("lines"))

    # 2) Vergiler
    tax_img = crop_region(img, CK_REGIONS["vergiler"])
    tax_path = save_tmp(tax_img, ".png")
    tax_json = ocr_image_to_json(tax_path, prompt="""Sadece Vergi ve Fonlar bölümünü oku ve JSON döndür.""")
    inv.taxes.btv_tl = tr_to_float(tax_json.get("btv_tl"))
    inv.taxes.other_taxes_tl = tr_to_float(tax_json.get("other_taxes_tl"))
    inv.vat.base_tl = tr_to_float(tax_json.get("vat_base_tl"))
    inv.vat.amount_tl = tr_to_float(tax_json.get("vat_amount_tl"))
    inv.vat.rate = 0.20  # CK örneklerinde %20; OCR ile de alınabilir.

    # 3) Özet / toplamlar
    ozet_img = crop_region(img, CK_REGIONS["ozet"])
    ozet_path = save_tmp(ozet_img, ".png")
    ozet_json = ocr_image_to_json(ozet_path, prompt="""Sadece Ödenecek Tutar ve Son Ödeme Tarihi alanlarını oku ve JSON döndür.""")
    inv.totals.payable_tl = tr_to_float(ozet_json.get("payable_tl"))
    inv.due_date = parse_date_iso(ozet_json.get("due_date"))

    # 4) Fatura tutarı (total) + subtotal
    total_img = crop_region(img, CK_REGIONS["fatura_tutari"])
    total_path = save_tmp(total_img, ".png")
    total_json = ocr_image_to_json(total_path, prompt="""Sadece Fatura Tutarı alanını oku ve JSON döndür: {\"total_tl\":\"...\"}""")
    inv.totals.total_tl = tr_to_float(total_json.get("total_tl"))

    # subtotal: kalem bedelleri + vergiler (KDV hariç) gibi.
    # v2 basit: kalem amount toplamı
    inv.totals.subtotal_tl = sum([li.amount_tl for li in inv.lines if li.amount_tl is not None]) if inv.lines else None

    # türetilenler
    inv.total_kwh = compute_total_kwh(inv.lines)
    inv.weighted_unit_price_tl_per_kwh = compute_weighted_unit_price(inv.lines)

    return validate_invoice(inv)

def parse_invoice(pdf_path: str) -> Invoice:
    profile = detect_profile_from_filename(pdf_path)
    if profile == "ck_bogazici":
        return parse_ck_bogazici(pdf_path)
    # Diğer profiller: aynı region yaklaşımı ile eklenecek.
    inv = Invoice(supplier_profile=profile)
    inv.warnings.append("PROFILE_NOT_IMPLEMENTED_YET")
    return inv
