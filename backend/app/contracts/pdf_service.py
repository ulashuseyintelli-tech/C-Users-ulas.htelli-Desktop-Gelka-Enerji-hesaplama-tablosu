"""
Sözleşme oluşturma V1 — Jinja2 şablon render + PDF üretimi.

Mevcut Playwright render servisini (app/services/pdf_playwright.py) ve
Türkçe sayı biçimlendirme filtresini (app/pdf_generator.py format_number)
yeniden kullanır. Mevcut teklif PDF üretim yolu (ReportLab birincil,
offer_template.html ikincil) HİÇ değiştirilmedi — bu tamamen ayrı,
bağımsız bir üretim yolu.

Çağrıldığı yerler: app/contracts/router.py (preview_contract, finalize_contract)
"""
import hashlib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..services.pdf_playwright import html_to_pdf_bytes_sync
from ..pdf_generator import format_number

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "contracts"

_env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))
_env.filters["number"] = format_number

_SECTION_TEMPLATES = [
    "supply_contract_v1.html",
    "additional_protocol_v1.html",
    "evacuation_commitment_v1.html",
    "customer_information_form_v1.html",
]


def _build_template_context(snapshot: dict) -> dict:
    """Contract snapshot JSON'unu (nested) düz template değişkenlerine çevirir."""
    representative = snapshot.get("representative") or {}
    tariff = snapshot.get("tariff_group") or {}
    contract_dates = snapshot.get("contract_dates") or {}

    agreement_multiplier = snapshot.get("agreement_multiplier")
    duration_months = contract_dates.get("duration_months")

    return {
        "legal_name": snapshot.get("legal_name"),
        "tax_number": snapshot.get("tax_number"),
        "tax_office": snapshot.get("tax_office"),
        "registered_address": snapshot.get("registered_address"),
        "facility_address": snapshot.get("facility_address"),
        "mersis_number": snapshot.get("mersis_number"),
        "representative_full_name": representative.get("full_name"),
        "representative_national_id": representative.get("national_id"),
        "tariff_group": tariff.get("value"),
        "tariff_group_resolution_status": tariff.get("resolution_status"),
        "subscription_codes": snapshot.get("subscription_codes"),
        "start_date": contract_dates.get("start_date"),
        "duration_months": duration_months,
        "duration_months_label": f"{duration_months} AY" if duration_months else None,
        # Katsayı Türkçe locale (virgül ondalık) — mevcut format_number filtresiyle
        # aynı biçim. Kaynak: yalnız Contract.offer_id -> Offer.agreement_multiplier
        # -> snapshot -> burada. Şablonda ASLA serbest metin/örnek değer yok.
        "agreement_multiplier": format_number(agreement_multiplier, 2) if agreement_multiplier is not None else None,
    }


def render_contract_sections(snapshot: dict) -> list[str]:
    """4 bölümü ayrı ayrı render eder (önizleme ekranı için, sırayla)."""
    context = _build_template_context(snapshot)
    return [_env.get_template(name).render(**context) for name in _SECTION_TEMPLATES]


def generate_contract_pdf(snapshot: dict) -> tuple[bytes, str]:
    """4 bölümü tek HTML'de birleştirip (bölümler arası page-break) tek PDF üretir."""
    sections = render_contract_sections(snapshot)
    combined_html = "<html><body>"
    for i, section_html in enumerate(sections):
        if i > 0:
            combined_html += '<div class="page-break"></div>'
        combined_html += section_html
    combined_html += "</body></html>"

    pdf_bytes = html_to_pdf_bytes_sync(combined_html)
    pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    return pdf_bytes, pdf_sha256
