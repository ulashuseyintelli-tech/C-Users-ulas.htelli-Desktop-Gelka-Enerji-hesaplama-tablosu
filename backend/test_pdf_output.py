"""Test PDF generation with sample data"""
import sys
sys.path.insert(0, '.')

from app.pdf_generator import generate_offer_pdf_bytes
from app.models import InvoiceExtraction, CalculationResult, OfferParams, FieldValue, InvoiceMeta

# Test data - kullanıcının verdiği değerler
extraction = InvoiceExtraction(
    vendor="Manuel Giriş",
    invoice_period="-",
    consumption_kwh=FieldValue(value=32114, confidence=1.0),
    current_active_unit_price_tl_per_kwh=FieldValue(value=3.3386, confidence=1.0),
    distribution_unit_price_tl_per_kwh=FieldValue(value=1.3851, confidence=1.0),
    invoice_total_with_vat_tl=FieldValue(value=183330.37, confidence=1.0),
    demand_qty=FieldValue(value=0, confidence=1.0),
    demand_unit_price_tl_per_unit=FieldValue(value=0, confidence=1.0),
    meta=InvoiceMeta(tariff_group_guess="Sanayi"),
)

# PTF=2973, YEKDEM=113
params = OfferParams(
    weighted_ptf_tl_per_mwh=2973.0,
    yekdem_tl_per_mwh=113.0,
    agreement_multiplier=1.01,
)

# Hesaplama sonuçları (kullanıcının ekran görüntüsünden)
calculation = CalculationResult(
    current_energy_tl=107214.87,
    current_distribution_tl=44488.29,
    current_demand_tl=0,
    current_btv_tl=1072.15,
    current_vat_matrah_tl=152775.31,
    current_vat_tl=30555.06,
    current_total_with_vat_tl=183330.37,
    current_energy_unit_tl_per_kwh=3.3386,
    current_distribution_unit_tl_per_kwh=1.3851,
    offer_ptf_tl=95500,
    offer_yekdem_tl=3630,
    offer_energy_tl=100094.84,
    offer_distribution_tl=44488.29,
    offer_demand_tl=0,
    offer_btv_tl=1000.95,
    offer_vat_matrah_tl=145584.08,
    offer_vat_tl=29116.82,
    offer_total_with_vat_tl=174700.90,
    offer_energy_unit_tl_per_kwh=3.1169,
    offer_distribution_unit_tl_per_kwh=1.3851,
    difference_excl_vat_tl=7191.23,
    difference_incl_vat_tl=8629.48,
    savings_ratio=0.0471,
    unit_price_savings_ratio=0.0664,
    current_total_tl_per_kwh=5.709,
    offer_total_tl_per_kwh=5.440,
    saving_tl_per_kwh=0.269,
    annual_saving_tl=103553.76,
    meta_consumption_kwh=32114,
)

# PDF oluştur - Playwright veya ReportLab kullanacak
pdf_bytes = generate_offer_pdf_bytes(
    extraction, calculation, params,
    customer_name="Test Müşteri",
    customer_company="Test Şirketi A.Ş.",
    offer_id=None
)

# Kaydet
with open("test_teklif_output.pdf", "wb") as f:
    f.write(pdf_bytes)

print(f"PDF oluşturuldu: test_teklif_output.pdf ({len(pdf_bytes)} bytes)")
print(f"Teklif birim fiyat: {(params.weighted_ptf_tl_per_mwh / 1000 + params.yekdem_tl_per_mwh / 1000) * params.agreement_multiplier:.4f} TL/kWh")
print(f"Mevcut birim fiyat: {extraction.current_active_unit_price_tl_per_kwh.value:.4f} TL/kWh")
