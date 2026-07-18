"""
PATCH 2B testleri — POST /api/pricing/calculate-manual ve app.pricing.manual_calculator.

Kapsam:
- 10/10 golden parity (backend/tests/fixtures/manual_calculation/golden_fixtures.json)
- Pure-function birim testleri (Decimal, ara yuvarlama yok, edge-case'ler)
- Discriminated request validation (indexed/fixed cross-field, fail-closed)
- API contract testi (TestClient — gerçek endpoint, DB gerektirmez)
- Mevcut calculate_offer() regresyonu: bu dosyada YOK — ayrı, dokunulmamış
  test dosyaları (örn. test_pricing_core.py) zaten kapsıyor; PATCH 2B bu
  dosyalara dokunmadı, `pytest tests/test_pricing_core.py` ile ayrıca
  doğrulanmalı (README'de not düşüldü).

Karakterizasyon: docs/manual-calculation-characterization.md
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.pricing.manual_calculator import (
    ManualCalculationRequest,
    calculate_manual_offer,
)

FIXTURES_PATH = (
    Path(__file__).parent / "fixtures" / "manual_calculation" / "golden_fixtures.json"
)


def _load_fixtures() -> list[dict]:
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        return json.load(f)["fixtures"]


GOLDEN_FIXTURES = _load_fixtures()

# response alanı -> fixture 'expected' alanı eşlemesi (aynı isimler, sadece netlik için)
_RESPONSE_FIELDS = [
    "current_energy_amount_tl", "current_distribution_amount_tl", "current_btv_amount_tl",
    "current_vat_base_tl", "current_vat_amount_tl", "current_total_tl",
    "offer_energy_unit_price_tl_per_kwh", "offer_energy_amount_tl",
    "offer_distribution_amount_tl", "offer_btv_amount_tl", "offer_vat_base_tl",
    "offer_vat_amount_tl", "offer_total_tl", "difference_incl_vat_tl", "saving_rate_percent",
]


def _build_request(fixture: dict) -> ManualCalculationRequest:
    payload = dict(fixture["inputs"])
    payload["offer_type"] = fixture["offer_type"]
    return ManualCalculationRequest(**payload)


# ═══════════════════════════════════════════════════════════════════════════════
# 10/10 Golden Parity
# ═══════════════════════════════════════════════════════════════════════════════


class TestGoldenParity:
    @pytest.mark.parametrize(
        "fixture", GOLDEN_FIXTURES, ids=[f["label"] for f in GOLDEN_FIXTURES]
    )
    def test_matches_desktop_captured_output(self, fixture):
        """Her fixture, gerçek çalıştırılmış masaüstü uygulamasından (ya da —
        yalnız sabit modun difference/saving_rate alanları için — indeksli
        moddan ayrıca doğrulanmış tam-hassasiyetli formülden) okunmuştur."""
        req = _build_request(fixture)
        resp = calculate_manual_offer(req)
        got = resp.model_dump()
        expected = fixture["expected"]

        for field in _RESPONSE_FIELDS:
            got_value = float(got[field])
            expected_value = expected[field]
            assert got_value == pytest.approx(expected_value, abs=0.01), (
                f"{fixture['label']} → {field}: hesaplanan={got_value} "
                f"beklenen={expected_value}"
            )

    def test_all_ten_fixtures_present(self):
        assert len(GOLDEN_FIXTURES) == 10
        assert sum(1 for f in GOLDEN_FIXTURES if f["offer_type"] == "indexed") == 5
        assert sum(1 for f in GOLDEN_FIXTURES if f["offer_type"] == "fixed") == 5


# ═══════════════════════════════════════════════════════════════════════════════
# Pure-function birim testleri — karakterizasyon dokümanındaki edge-case'ler
# ═══════════════════════════════════════════════════════════════════════════════


class TestPureFunctionBehavior:
    def _base_indexed_payload(self, **overrides):
        payload = dict(
            consumption_kwh=Decimal("1000"),
            current_energy_unit_price_tl_per_kwh=Decimal("3.00"),
            current_distribution_unit_price_tl_per_kwh=Decimal("2.00"),
            offer_distribution_unit_price_tl_per_kwh=Decimal("2.00"),
            btv_rate=Decimal("0.01"),
            vat_rate=Decimal("0.20"),
            offer_type="indexed",
            ptf_tl_per_mwh=Decimal("2000"),
            yekdem_tl_per_mwh=Decimal("200"),
            multiplier=Decimal("1.10"),
        )
        payload.update(overrides)
        return payload

    def test_fixed_mode_ignores_multiplier_field_entirely(self):
        """Sabit modda multiplier gönderilemez (discriminator), ve zaten
        offer_energy hesaplamasında hiç kullanılmaz — sadece fixed_energy_unit_price × kwh."""
        payload = dict(
            consumption_kwh=Decimal("1000"),
            current_energy_unit_price_tl_per_kwh=Decimal("3.00"),
            current_distribution_unit_price_tl_per_kwh=Decimal("2.00"),
            offer_distribution_unit_price_tl_per_kwh=Decimal("2.00"),
            btv_rate=Decimal("0.01"),
            vat_rate=Decimal("0.20"),
            offer_type="fixed",
            fixed_energy_unit_price_tl_per_kwh=Decimal("5.00"),
        )
        resp = calculate_manual_offer(ManualCalculationRequest(**payload))
        assert resp.offer_energy_amount_tl == Decimal("5000.00")  # 1000 × 5.00, çarpan yok

    def test_yekdem_zero_excluded_not_added(self):
        """yekdem_tl_per_mwh=0 iken teklife HİÇ eklenmez (0 eklemekle sayısal
        olarak aynı sonucu verir ama kod dalı ayrı — karakterizasyon §2)."""
        req_with_zero_yekdem = ManualCalculationRequest(
            **self._base_indexed_payload(yekdem_tl_per_mwh=Decimal("0"))
        )
        resp = calculate_manual_offer(req_with_zero_yekdem)
        # (2000/1000) × 1.10 = 2.2000 TL/kWh — YEKDEM hiç katkı vermiyor
        assert resp.offer_energy_unit_price_tl_per_kwh == Decimal("2.2000")

    def test_multiplier_applies_to_ptf_plus_yekdem_sum_not_ptf_alone(self):
        """KRİTİK regresyon testi — kontrat onayı sırasında yakalanan formül
        hatasının bir daha geri gelmemesi için. Doğru: (ptf/1000+yekdem/1000)×çarpan.
        Yanlış: (ptf/1000×çarpan)+yekdem/1000."""
        req = ManualCalculationRequest(**self._base_indexed_payload())
        resp = calculate_manual_offer(req)
        correct = (Decimal("2000") / 1000 + Decimal("200") / 1000) * Decimal("1.10")
        wrong = (Decimal("2000") / 1000 * Decimal("1.10")) + Decimal("200") / 1000
        assert correct != wrong, "test senaryosu YEKDEM'i yeterince büyük seçmeli"
        assert resp.offer_energy_unit_price_tl_per_kwh == correct.quantize(Decimal("0.0001"))

    def test_negative_saving_allowed_not_clamped(self):
        """Teklif mevcuttan pahalı olabilir — hata/clamp YOK."""
        payload = self._base_indexed_payload(
            current_energy_unit_price_tl_per_kwh=Decimal("1.00"),  # ucuz mevcut fatura
            multiplier=Decimal("2.00"),  # pahalı teklif
        )
        resp = calculate_manual_offer(ManualCalculationRequest(**payload))
        assert resp.difference_incl_vat_tl < 0
        assert resp.saving_rate_percent < 0

    def test_saving_rate_percent_scale_is_0_to_100(self):
        """saving_rate_percent 0-1 değil 0-100 ölçekte (kontrat kararı)."""
        req = ManualCalculationRequest(**self._base_indexed_payload())
        resp = calculate_manual_offer(req)
        # Mantıklı bir teklif için tasarruf oranı büyüklük mertebesi 0-100 arası olmalı,
        # 0-1 arası KÜÇÜK bir kesir olmamalı (örn 0.05 değil 5.xx gibi bir şey bekleriz)
        assert abs(resp.saving_rate_percent) > 1  # 0-1 ölçekte olsaydı bu fixture'da <1 çıkardı

    def test_zero_btv_rate_produces_zero_btv_amount(self):
        payload = self._base_indexed_payload(btv_rate=Decimal("0"))
        resp = calculate_manual_offer(ManualCalculationRequest(**payload))
        assert resp.current_btv_amount_tl == Decimal("0.00")
        assert resp.offer_btv_amount_tl == Decimal("0.00")


# ═══════════════════════════════════════════════════════════════════════════════
# Discriminated request validation — fail-closed
# ═══════════════════════════════════════════════════════════════════════════════


class TestDiscriminatedValidation:
    def _valid_indexed(self, **overrides):
        base = dict(
            consumption_kwh=1000, current_energy_unit_price_tl_per_kwh=3.0,
            current_distribution_unit_price_tl_per_kwh=2.0,
            offer_distribution_unit_price_tl_per_kwh=2.0,
            btv_rate=0.01, vat_rate=0.20, offer_type="indexed",
            ptf_tl_per_mwh=2000, yekdem_tl_per_mwh=200, multiplier=1.10,
        )
        base.update(overrides)
        return base

    def _valid_fixed(self, **overrides):
        base = dict(
            consumption_kwh=1000, current_energy_unit_price_tl_per_kwh=3.0,
            current_distribution_unit_price_tl_per_kwh=2.0,
            offer_distribution_unit_price_tl_per_kwh=2.0,
            btv_rate=0.01, vat_rate=0.20, offer_type="fixed",
            fixed_energy_unit_price_tl_per_kwh=5.0,
        )
        base.update(overrides)
        return base

    def test_indexed_rejects_fixed_field(self):
        with pytest.raises(ValidationError, match="indexed"):
            ManualCalculationRequest(
                **self._valid_indexed(fixed_energy_unit_price_tl_per_kwh=5.0)
            )

    def test_fixed_rejects_indexed_fields(self):
        with pytest.raises(ValidationError, match="fixed"):
            ManualCalculationRequest(**self._valid_fixed(ptf_tl_per_mwh=2000))

    def test_indexed_missing_ptf_rejected(self):
        payload = self._valid_indexed()
        payload["ptf_tl_per_mwh"] = None
        with pytest.raises(ValidationError):
            ManualCalculationRequest(**payload)

    def test_fixed_missing_price_rejected(self):
        payload = self._valid_fixed()
        payload["fixed_energy_unit_price_tl_per_kwh"] = None
        with pytest.raises(ValidationError):
            ManualCalculationRequest(**payload)

    def test_zero_consumption_rejected(self):
        with pytest.raises(ValidationError):
            ManualCalculationRequest(**self._valid_indexed(consumption_kwh=0))

    def test_negative_consumption_rejected(self):
        with pytest.raises(ValidationError):
            ManualCalculationRequest(**self._valid_indexed(consumption_kwh=-100))

    def test_negative_price_rejected(self):
        with pytest.raises(ValidationError):
            ManualCalculationRequest(
                **self._valid_indexed(current_energy_unit_price_tl_per_kwh=-1)
            )

    def test_vat_rate_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            ManualCalculationRequest(**self._valid_indexed(vat_rate=1.5))

    def test_nan_rejected(self):
        with pytest.raises(ValidationError):
            ManualCalculationRequest(
                **self._valid_indexed(current_energy_unit_price_tl_per_kwh=Decimal("NaN"))
            )

    def test_infinity_rejected(self):
        with pytest.raises(ValidationError):
            ManualCalculationRequest(
                **self._valid_indexed(ptf_tl_per_mwh=Decimal("Infinity"))
            )

    def test_empty_string_rejected(self):
        with pytest.raises(ValidationError):
            ManualCalculationRequest(**self._valid_indexed(consumption_kwh=""))

    def test_unparseable_string_rejected(self):
        with pytest.raises(ValidationError):
            ManualCalculationRequest(**self._valid_indexed(consumption_kwh="abc"))


# ═══════════════════════════════════════════════════════════════════════════════
# API-level contract testi — gerçek endpoint, DB gerektirmez (pure function)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def client():
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


class TestCalculateManualEndpoint:
    def test_indexed_returns_200_with_expected_shape(self, client):
        fixture = next(f for f in GOLDEN_FIXTURES if f["offer_type"] == "indexed")
        payload = dict(fixture["inputs"])
        payload["offer_type"] = "indexed"
        resp = client.post("/api/pricing/calculate-manual", json=payload)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for field in _RESPONSE_FIELDS + ["calculation_version", "rounding_version"]:
            assert field in body, f"response'ta eksik alan: {field}"

    def test_fixed_returns_200_with_expected_shape(self, client):
        fixture = next(f for f in GOLDEN_FIXTURES if f["offer_type"] == "fixed")
        payload = dict(fixture["inputs"])
        payload["offer_type"] = "fixed"
        resp = client.post("/api/pricing/calculate-manual", json=payload)
        assert resp.status_code == 200, resp.text

    def test_cross_field_violation_returns_422(self, client):
        payload = dict(
            consumption_kwh=1000, current_energy_unit_price_tl_per_kwh=3.0,
            current_distribution_unit_price_tl_per_kwh=2.0,
            offer_distribution_unit_price_tl_per_kwh=2.0,
            btv_rate=0.01, vat_rate=0.20, offer_type="indexed",
            ptf_tl_per_mwh=2000, yekdem_tl_per_mwh=200, multiplier=1.10,
            fixed_energy_unit_price_tl_per_kwh=5.0,  # yanlış varyant alanı
        )
        resp = client.post("/api/pricing/calculate-manual", json=payload)
        assert resp.status_code == 422

    def test_zero_consumption_returns_422(self, client):
        payload = dict(
            consumption_kwh=0, current_energy_unit_price_tl_per_kwh=3.0,
            current_distribution_unit_price_tl_per_kwh=2.0,
            offer_distribution_unit_price_tl_per_kwh=2.0,
            btv_rate=0.01, vat_rate=0.20, offer_type="indexed",
            ptf_tl_per_mwh=2000, yekdem_tl_per_mwh=200, multiplier=1.10,
        )
        resp = client.post("/api/pricing/calculate-manual", json=payload)
        assert resp.status_code == 422

    def test_missing_offer_type_returns_422(self, client):
        resp = client.post("/api/pricing/calculate-manual", json={"consumption_kwh": 1000})
        assert resp.status_code == 422
