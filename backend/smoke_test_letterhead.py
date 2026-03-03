"""
Smoke test: ReportLab PDF testi (birincil renderer).
Çalıştır: python smoke_test_letterhead.py  (backend/ dizininden)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

def main():
    from app.models import InvoiceExtraction, CalculationResult, OfferParams, FieldValue

    extraction = InvoiceExtraction(
        vendor="Manuel Giriş",
        invoice_period="01/2026",
        consumption_kwh=FieldValue(value=222222, confidence=1.0),
        current_active_unit_price_tl_per_kwh=FieldValue(value=4.0, confidence=1.0),
        distribution_unit_price_tl_per_kwh=FieldValue(value=1.878, confidence=1.0),
        invoice_total_with_vat_tl=FieldValue(value=1620641.05, confidence=1.0),
        demand_qty=FieldValue(value=0, confidence=1.0),
        demand_unit_price_tl_per_unit=FieldValue(value=0, confidence=1.0),
    )

    calculation = CalculationResult(
        current_energy_tl=888888.00,
        current_distribution_tl=417201.81,
        current_btv_tl=44444.40,
        current_vat_matrah_tl=1350534.21,
        current_vat_tl=270106.84,
        current_total_with_vat_tl=1620641.05,
        current_demand_tl=0.0,
        offer_energy_tl=749217.25,
        offer_ptf_tl=660888.12,
        offer_yekdem_tl=80888.89,
        offer_distribution_tl=417201.81,
        offer_btv_tl=37460.86,
        offer_vat_matrah_tl=1203879.92,
        offer_vat_tl=240775.98,
        offer_total_with_vat_tl=1444655.90,
        offer_demand_tl=0.0,
        difference_excl_vat_tl=146654.29,
        difference_incl_vat_tl=175985.14,
        savings_ratio=-0.1086,
        unit_price_savings_ratio=-0.1572,
        meta_vat_rate=0.20,
    )

    params = OfferParams(
        weighted_ptf_tl_per_mwh=2974.10,
        yekdem_tl_per_mwh=364.00,
        agreement_multiplier=1.01,
    )

    from app.pdf_generator import _generate_pdf_reportlab

    print("ReportLab PDF üretiliyor...")
    pdf_bytes = _generate_pdf_reportlab(
        extraction, calculation, params,
        customer_name="Akın Plastik",
        customer_company="Akın Plastik",
        offer_id=20260303,
        contact_person="Ahmet",
        offer_date="2026-03-03",
        offer_validity_days=15,
    )

    # Çıktı dosyası
    out = os.path.join(os.path.dirname(__file__), "smoke_test_reportlab10.pdf")
    with open(out, "wb") as f:
        f.write(pdf_bytes)
    print(f"PDF kaydedildi: {out} ({len(pdf_bytes)} bytes)")

if __name__ == "__main__":
    main()
