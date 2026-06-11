"""
R2 Katman 2 — /generate-pdf-simple gross-misread guard test matrisi.

Karar ağacı:
- delta > 40                    → 422, blocking, override yok (onay olsa bile)
- 10 <= delta <= 40 + no confirm → 422, requires_operator_confirmation=True
- 10 <= delta <= 40 + confirm    → 200 (PDF)
- delta < 10                    → 200
- invoice_total_raw <= 0        → kıyas atlanır → 200 (crash yok)

Asıl operatif kapı: hesaplanan toplam (current_vat_matrah_tl + current_vat_tl) vs invoice_total_raw.
Cross-check (consumption×unit_price vs current_energy_tl) frontend'de türetildiği için ≈0 (belgelenir).
"""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

# computed_total = current_vat_matrah_tl + current_vat_tl = 2000 + 400 = 2400
# cross: consumption*unit_price (1000*2.0=2000) == current_energy_tl (2000) -> d_cross = 0
BASE = {
    "consumption_kwh": "1000",
    "current_unit_price": "2.0",
    "current_energy_tl": "2000",
    "current_vat_matrah_tl": "2000",
    "current_vat_tl": "400",
    "weighted_ptf_tl_per_mwh": "590.9",
    "yekdem_tl_per_mwh": "563.78",
    "agreement_multiplier": "1.01",
    "offer_energy_tl": "1000",
    "offer_total": "2000",
    "savings_ratio": "0.2",
    "invoice_period": "2026-05",
}


def _post(**overrides):
    return client.post("/generate-pdf-simple", data={**BASE, **overrides})


def test_hard_block_over_40():
    # invoice_total_raw=1500 -> |2400-1500|/1500 = 60% > 40 -> blocking
    r = _post(invoice_total_raw="1500")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "extraction_mismatch"
    assert err["blocking_errors"]
    assert err["requires_operator_confirmation"] is False
    # asıl tetikleyici total; cross ≈0 (blocking'de total olmalı)
    assert any(b["kind"] == "total" for b in err["blocking_errors"])


def test_hard_block_not_overridable_by_confirm():
    # >40 + confirm=true -> hala 422
    r = _post(invoice_total_raw="1500", operator_confirmed_warnings="true")
    assert r.status_code == 422
    assert r.json()["error"]["blocking_errors"]


def test_confirmable_warning_without_confirm_blocks():
    # invoice_total_raw=2000 -> |2400-2000|/2000 = 20% -> confirmable
    r = _post(invoice_total_raw="2000")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["confirmable_warnings"]
    assert not err["blocking_errors"]
    assert err["requires_operator_confirmation"] is True


def test_confirmable_warning_with_confirm_passes():
    # %20 + confirm=true -> 200 (PDF)
    r = _post(invoice_total_raw="2000", operator_confirmed_warnings="true")
    assert r.status_code == 200
    assert len(r.content) > 5000  # PDF bytes


def test_clean_payload_passes():
    # invoice_total_raw=2350 -> |2400-2350|/2350 = ~2.1% < 10 -> 200
    r = _post(invoice_total_raw="2350")
    assert r.status_code == 200
    assert len(r.content) > 5000


def test_missing_raw_total_skips_comparison():
    # invoice_total_raw=0 (gonderilmemis) -> total kıyası atlanır; cross ≈0 -> 200
    r = _post(invoice_total_raw="0")
    assert r.status_code == 200
    assert len(r.content) > 5000


def test_cross_check_is_zero_on_derived_energy():
    # consumption*unit_price == current_energy_tl -> cross delta 0; sadece total tetiklenir.
    # Gross total ile blokla, blocking yalnızca 'total' icermeli (cross degil).
    r = _post(invoice_total_raw="1500")
    assert r.status_code == 422
    kinds = {b["kind"] for b in r.json()["error"]["blocking_errors"]}
    assert "total" in kinds
    assert "cross" not in kinds  # current_energy_tl türetilmiş -> cross 0
