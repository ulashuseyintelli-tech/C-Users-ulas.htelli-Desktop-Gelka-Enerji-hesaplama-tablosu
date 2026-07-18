"""
Manuel Hesaplama — Canonical Pure Calculator (PATCH 2B)

Masaüstü liveCalculation (manuel mod, frontend/src/App.tsx) davranışının backend'e
canonical pure function olarak taşınmış hâli. Karakterizasyon ve golden fixture:
  - docs/manual-calculation-characterization.md
  - backend/tests/fixtures/manual_calculation/golden_fixtures.json

KAPSAM DIŞI (bu modül DEĞİŞTİRMEZ): OCR/InvoiceExtraction, EPDK otomatik tarife
lookup, dönem-bazlı PTF/YEKDEM otomatik çekimi — bunlar için calculate_offer()
(app/calculator.py) kullanılır. Bu modül ayrı, additive bir akıştır.

Çağrıldığı yerler:
- app/pricing/router.py.calculate_manual_offer_endpoint() → POST /api/pricing/calculate-manual
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

CALCULATION_VERSION = "manual-v1"
ROUNDING_VERSION = "round-half-up-2dp-v1"

_TWO_PLACES = Decimal("0.01")
_FOUR_PLACES = Decimal("0.0001")
_THOUSAND = Decimal("1000")
_HUNDRED = Decimal("100")


def _q2(value: Decimal) -> Decimal:
    """Yalnız response sınırında çağrılır — ara işlemlerde ASLA (bkz. karakterizasyon §4)."""
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _q4(value: Decimal) -> Decimal:
    return value.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)


class ManualCalculationRequest(BaseModel):
    """POST /api/pricing/calculate-manual request body.

    offer_type'a göre discriminated: "indexed" alanları (ptf/yekdem/multiplier) ile
    "fixed" alanı (fixed_energy_unit_price_tl_per_kwh) BİRLİKTE gönderilemez —
    fail-closed (yanlış varyant alanı → 422).
    """

    consumption_kwh: Decimal = Field(gt=Decimal("0"))
    current_energy_unit_price_tl_per_kwh: Decimal = Field(ge=Decimal("0"))
    current_distribution_unit_price_tl_per_kwh: Decimal = Field(ge=Decimal("0"))
    offer_distribution_unit_price_tl_per_kwh: Decimal = Field(ge=Decimal("0"))
    btv_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    vat_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    offer_type: Literal["indexed", "fixed"]

    # yalnız offer_type == "indexed"
    ptf_tl_per_mwh: Optional[Decimal] = Field(default=None, gt=Decimal("0"))
    yekdem_tl_per_mwh: Optional[Decimal] = Field(default=None, ge=Decimal("0"))
    multiplier: Optional[Decimal] = Field(default=None, gt=Decimal("0"))

    # yalnız offer_type == "fixed"
    fixed_energy_unit_price_tl_per_kwh: Optional[Decimal] = Field(default=None, gt=Decimal("0"))

    @model_validator(mode="after")
    def _check_finite(self) -> "ManualCalculationRequest":
        """NaN/Infinity/-Infinity reddedilir — Decimal("NaN") Field(gt=...) kontrolünü
        sessizce atlatabildiği için (decimal modülü NaN karşılaştırmalarında context'e
        göre davranır), her alan ayrıca is_finite() ile doğrulanır."""
        for name in (
            "consumption_kwh", "current_energy_unit_price_tl_per_kwh",
            "current_distribution_unit_price_tl_per_kwh", "offer_distribution_unit_price_tl_per_kwh",
            "btv_rate", "vat_rate", "ptf_tl_per_mwh", "yekdem_tl_per_mwh",
            "multiplier", "fixed_energy_unit_price_tl_per_kwh",
        ):
            value = getattr(self, name)
            if value is not None and not value.is_finite():
                raise ValueError(f"{name} sonlu (finite) bir sayı olmalı — NaN/Infinity kabul edilmez.")
        return self

    @model_validator(mode="after")
    def _check_discriminated_fields(self) -> "ManualCalculationRequest":
        indexed_fields = {
            "ptf_tl_per_mwh": self.ptf_tl_per_mwh,
            "yekdem_tl_per_mwh": self.yekdem_tl_per_mwh,
            "multiplier": self.multiplier,
        }
        fixed_fields = {"fixed_energy_unit_price_tl_per_kwh": self.fixed_energy_unit_price_tl_per_kwh}

        if self.offer_type == "indexed":
            missing = [k for k, v in indexed_fields.items() if v is None]
            if missing:
                raise ValueError(f"offer_type=indexed için zorunlu alanlar eksik: {', '.join(missing)}")
            extra = [k for k, v in fixed_fields.items() if v is not None]
            if extra:
                raise ValueError(f"offer_type=indexed iken gönderilmemeli: {', '.join(extra)}")
        else:  # fixed
            missing = [k for k, v in fixed_fields.items() if v is None]
            if missing:
                raise ValueError(f"offer_type=fixed için zorunlu alanlar eksik: {', '.join(missing)}")
            extra = [k for k, v in indexed_fields.items() if v is not None]
            if extra:
                raise ValueError(f"offer_type=fixed iken gönderilmemeli: {', '.join(extra)}")
        return self


class ManualCalculationResponse(BaseModel):
    """POST /api/pricing/calculate-manual response body."""

    current_energy_amount_tl: Decimal
    current_distribution_amount_tl: Decimal
    current_btv_amount_tl: Decimal
    current_vat_base_tl: Decimal
    current_vat_amount_tl: Decimal
    current_total_tl: Decimal

    offer_energy_unit_price_tl_per_kwh: Decimal
    offer_energy_amount_tl: Decimal
    offer_distribution_amount_tl: Decimal
    offer_btv_amount_tl: Decimal
    offer_vat_base_tl: Decimal
    offer_vat_amount_tl: Decimal
    offer_total_tl: Decimal

    difference_incl_vat_tl: Decimal
    saving_rate_percent: Decimal  # 0-100 ölçek (0-1 DEĞİL)

    calculation_version: str = CALCULATION_VERSION
    rounding_version: str = ROUNDING_VERSION


def calculate_manual_offer(req: ManualCalculationRequest) -> ManualCalculationResponse:
    """Pure function — side-effect yok, DB/OCR/EPDK/network erişimi yok.

    Karakterizasyon: docs/manual-calculation-characterization.md §3.
    Tüm ara işlemler TAM HASSASİYETTE (Decimal, yuvarlama yok); yalnız response
    oluşturulurken 2 ondalığa (tutar) / 4 ondalığa (birim fiyat) yuvarlanır (§4).
    """
    kwh = req.consumption_kwh

    # ── Mevcut (current) taraf ──
    current_energy = req.current_energy_unit_price_tl_per_kwh * kwh
    current_distribution = req.current_distribution_unit_price_tl_per_kwh * kwh
    current_btv = current_energy * req.btv_rate
    current_vat_base = current_energy + current_distribution + current_btv
    current_vat = current_vat_base * req.vat_rate
    current_total = current_vat_base + current_vat

    # ── Teklif (offer) taraf — offer_energy_unit_price_tl_per_kwh ──
    # ⚠️ Endeksli formülde çarpan PTF+YEKDEM TOPLAMINA uygulanır, yalnız PTF'ye DEĞİL.
    # Doğrulama: docs/manual-calculation-characterization.md §7.3 (5 fixture'ın 3'ünde
    # alternatif sıralama yanlış sonuç verdiği kanıtlandı).
    if req.offer_type == "indexed":
        assert req.ptf_tl_per_mwh is not None and req.multiplier is not None  # discriminator garantisi
        yekdem_component = (
            req.yekdem_tl_per_mwh / _THOUSAND
            if req.yekdem_tl_per_mwh and req.yekdem_tl_per_mwh > 0
            else Decimal("0")
        )
        offer_unit_price = ((req.ptf_tl_per_mwh / _THOUSAND) + yekdem_component) * req.multiplier
    else:
        assert req.fixed_energy_unit_price_tl_per_kwh is not None  # discriminator garantisi
        offer_unit_price = req.fixed_energy_unit_price_tl_per_kwh

    offer_energy = offer_unit_price * kwh
    offer_distribution = req.offer_distribution_unit_price_tl_per_kwh * kwh
    offer_btv = offer_energy * req.btv_rate
    offer_vat_base = offer_energy + offer_distribution + offer_btv
    offer_vat = offer_vat_base * req.vat_rate
    offer_total = offer_vat_base + offer_vat

    # ── Fark / tasarruf — TAM HASSASİYETLİ toplamlardan, yuvarlanmış toplamlardan DEĞİL ──
    difference = current_total - offer_total
    saving_rate_percent = (
        (difference / current_total) * _HUNDRED if current_total > 0 else Decimal("0")
    )

    return ManualCalculationResponse(
        current_energy_amount_tl=_q2(current_energy),
        current_distribution_amount_tl=_q2(current_distribution),
        current_btv_amount_tl=_q2(current_btv),
        current_vat_base_tl=_q2(current_vat_base),
        current_vat_amount_tl=_q2(current_vat),
        current_total_tl=_q2(current_total),
        offer_energy_unit_price_tl_per_kwh=_q4(offer_unit_price),
        offer_energy_amount_tl=_q2(offer_energy),
        offer_distribution_amount_tl=_q2(offer_distribution),
        offer_btv_amount_tl=_q2(offer_btv),
        offer_vat_base_tl=_q2(offer_vat_base),
        offer_vat_amount_tl=_q2(offer_vat),
        offer_total_tl=_q2(offer_total),
        difference_incl_vat_tl=_q2(difference),
        saving_rate_percent=_q2(saving_rate_percent),
    )
