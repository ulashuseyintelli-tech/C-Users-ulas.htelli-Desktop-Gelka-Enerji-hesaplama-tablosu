"""
R2 gross-misread guard — Katman 1 (validator hard gate) test matrisi.

İş kuralı:
- delta < %10       → sessiz
- %10 <= delta <=40 → warning
- delta > %40       → error + is_ready_for_pricing=False

İki kontrol:
- energy cross-check: consumption × unit_price vs energy_total  (temiz formül → bant sınırları burada test edilir)
- total sanity:       computed_total vs invoice_total            (gross-mismatch error yolu)

Eksik veri (energy_total yok) → crash yok, yeni warning/error üretilmez.
"""
from app.models import InvoiceExtraction, FieldValue, RawBreakdown
from app.validator import validate_extraction


def _make(consumption, unit_price, energy_total=None, invoice_total=None):
    """consumption/unit_price present (missing değil). energy_total -> cross-check; invoice_total -> total sanity."""
    return InvoiceExtraction(
        vendor="enerjisa",
        invoice_period="2026-05",
        consumption_kwh=FieldValue(value=consumption),
        current_active_unit_price_tl_per_kwh=FieldValue(value=unit_price),
        distribution_unit_price_tl_per_kwh=FieldValue(value=0.0),
        invoice_total_with_vat_tl=FieldValue(value=invoice_total),
        raw_breakdown=RawBreakdown(
            energy_total_tl=FieldValue(value=energy_total) if energy_total is not None else None,
        ),
    )


def _cross_errors(result):
    return [e for e in result.errors if isinstance(e, dict) and e.get("field") == "consumption_kwh" and "delta_ratio" in e]


def _cross_warnings(result):
    return [w for w in result.warnings if isinstance(w, dict) and w.get("field") == "consumption_kwh" and "delta_ratio" in w]


# ── Energy cross-check bant matrisi (energy_total=2000 sabit; unit_price ile delta ayarlanır) ──
# delta = |consumption*unit_price - 2000| / 2000 * 100

def test_delta_below_10_is_silent():
    # 1000 * 2.198 = 2198 -> delta = 9.9% -> sessiz
    r = _make(1000, 2.198, energy_total=2000)
    res = validate_extraction(r)
    assert not _cross_errors(res)
    assert not _cross_warnings(res)


def test_delta_exactly_10_is_warning():
    # 1000 * 2.2 = 2200 -> delta = 10.0% -> warning
    res = validate_extraction(_make(1000, 2.2, energy_total=2000))
    assert _cross_warnings(res)
    assert not _cross_errors(res)


def test_delta_exactly_40_is_warning():
    # 1000 * 2.8 = 2800 -> delta = 40.0% -> warning (sınır dahil)
    res = validate_extraction(_make(1000, 2.8, energy_total=2000))
    assert _cross_warnings(res)
    assert not _cross_errors(res)


def test_delta_over_40_is_error_and_not_ready():
    # 1000 * 2.8002 = 2800.2 -> delta = 40.01% -> error + is_ready=False
    res = validate_extraction(_make(1000, 2.8002, energy_total=2000))
    assert _cross_errors(res)
    assert res.is_ready_for_pricing is False


def test_missing_energy_total_no_crash_no_signal():
    # energy_total yok -> cross-check return None -> ne error ne warning; is_ready korunur
    res = validate_extraction(_make(1000, 2.2, energy_total=None))
    assert not _cross_errors(res)
    assert not _cross_warnings(res)
    assert res.is_ready_for_pricing is True


# ── Total sanity error yolu (gross-mismatch) ──

def test_total_sanity_gross_mismatch_is_error_and_not_ready():
    # consumption=1000, unit=2.0 -> computed_total ~2400; invoice_total=10000 -> ~%>40 -> error
    res = validate_extraction(_make(1000, 2.0, energy_total=2000, invoice_total=10000))
    total_errors = [e for e in res.errors if isinstance(e, dict) and e.get("field") == "invoice_total_with_vat_tl"]
    assert total_errors
    assert res.is_ready_for_pricing is False


def test_consistent_extraction_stays_ready():
    # energy_total = consumption*unit (cross-check 0); invoice_total yok -> total sanity yok -> ready
    res = validate_extraction(_make(1000, 2.2, energy_total=2200))
    assert not _cross_errors(res)
    assert res.is_ready_for_pricing is True
