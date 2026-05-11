"""
Nominal vs Gerçek Marj Analizi — Marj Sapma Motoru.

Ana soru: "Ben bu müşteriye %4 marjla sattım; saatlik tüketim
profiline göre gerçekte kaç % marj kazandım?"

Çekirdek gerçek:
  - Sabit fiyat satıyorsun (teklif birim fiyat)
  - Değişken maliyetle alıyorsun (saatlik PTF)
  - Bu yüzden: Marj = profil fonksiyonu

Dağıtım bedeli dahil değil — regüle kalem, her iki tarafta aynı.
Bu modül sadece enerji marjını ölçer.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Modeller
# ═══════════════════════════════════════════════════════════════════════════════


class MarginVerdict(str, Enum):
    """Marj gerçekleşme kararı — ana karar dili."""
    PROFITABLE = "Kârlı"            # Gerçek ≈ Nominal (sapma ±eşik içinde)
    OVERPERFORM = "Overperform"      # Gerçek > Nominal
    MARGIN_ERODING = "Marj Eriyor"   # Gerçek < Nominal ama > 0
    LOSS = "Zararlı"                 # Gerçek < 0


class PricingDecision(str, Enum):
    """Teklif kararı — satışçıya ne yapması gerektiğini söyler."""
    ACCEPT = "TEKLİF UYGUN"
    REPRICE = "FİYAT ARTIR"
    REJECT = "TEKLİF VERME"
    OVERPRICE = "FİYAT DÜŞÜR"


class PricingAggressiveness(str, Enum):
    """Fiyat değişikliği agresiflik seviyesi — müşteri kayıp riski."""
    NONE = "YOK"        # Fiyat değişikliği gerekmiyor
    LOW = "DÜŞÜK"       # 0–0.01 fark, rahat
    MEDIUM = "ORTA"     # 0.01–0.03 fark, dikkat
    HIGH = "YÜKSEK"     # >0.03 fark, müşteri kayıp riski


class HourlyMarginDetail(BaseModel):
    """Tek saatlik marj detayı — en kötü/en iyi tablolar için."""
    hour: str = Field(description="Tarih ve saat (YYYY-MM-DD HH:00)")
    ptf_tl_per_mwh: float = Field(description="O saatteki PTF (TL/MWh)")
    consumption_kwh: float = Field(description="O saatteki tüketim (kWh)")
    cost_tl: float = Field(description="O saatteki maliyet (TL)")
    margin_tl: float = Field(description="O saatteki marj (TL)")
    time_zone: Optional[str] = Field(default=None, description="Zaman dilimi (T1/T2/T3)")


class MarginRealityResult(BaseModel):
    """Nominal vs Gerçek Marj Analizi sonucu — ana karar çıktısı."""

    # ── Karar ──────────────────────────────────────────────────────────
    verdict: MarginVerdict = Field(description="Ana karar: Kârlı / Overperform / Marj Eriyor / Zararlı")
    pricing_decision: PricingDecision = Field(description="Teklif kararı: TEKLİF UYGUN / FİYAT ARTIR / TEKLİF VERME / FİYAT DÜŞÜR")
    pricing_decision_reason: str = Field(description="Karar gerekçesi (tek cümle)")
    pricing_aggressiveness: PricingAggressiveness = Field(description="Fiyat değişikliği agresiflik seviyesi")

    # ── Katsayı bilgisi ────────────────────────────────────────────────
    multiplier: float = Field(description="Girilen katsayı (örn: 1.04)")
    effective_multiplier: float = Field(
        description="Gerçekleşen katsayı etkisi = Teklif Fiyat / Ağırlıklı Maliyet"
    )

    # ── Nominal (kağıt üzeri) hesap ───────────────────────────────────
    nominal_margin_pct: float = Field(description="Kağıt üzeri marj % = (katsayı - 1) × 100")
    nominal_margin_tl: float = Field(description="Kağıt üzeri marj TL (dönem ortalaması ile)")

    # ── Gerçek (saatlik) hesap ────────────────────────────────────────
    real_margin_pct: float = Field(description="Gerçek marj % (saatlik hesapla)")
    real_margin_tl: float = Field(description="Gerçek marj TL (saatlik hesapla)")

    # ── Sapma (EN KRİTİK METRİK) ─────────────────────────────────────
    margin_deviation_pct: float = Field(description="Marj sapması % = gerçek - nominal (+ iyi, - kötü)")
    margin_deviation_tl: float = Field(description="Marj sapması TL")

    # ── Revenue-based margin (ticari karar için) ──────────────────────
    real_margin_on_revenue_pct: float = Field(
        description="Ciro bazlı gerçek marj % = Gerçek Marj TL / Toplam Satış TL × 100"
    )

    # ── Multiplier sapması ────────────────────────────────────────────
    multiplier_delta: float = Field(
        description="Katsayı sapması = Effective Multiplier - Nominal Multiplier"
    )

    # ── Saat detayları ────────────────────────────────────────────────
    total_hours: int = Field(description="Toplam saat sayısı")
    negative_margin_hours: int = Field(description="Negatif marj saat sayısı")
    negative_margin_total_tl: float = Field(description="Negatif saatlerin toplam zararı (TL)")
    positive_margin_total_tl: float = Field(description="Pozitif saatlerin toplam kârı (TL)")

    # ── Katsayı önerileri ─────────────────────────────────────────────
    break_even_multiplier: float = Field(description="Gerçek marj = 0 olan katsayı")
    safe_multiplier: float = Field(description="Break-even + tampon")
    target_margin_pct: float = Field(
        default=0.0,
        description="Hedef marj % (kullanıcının istediği gerçek marj)",
    )
    required_multiplier_for_target: float = Field(
        default=0.0,
        description="Hedef marjı bu profilde gerçekten sağlayan katsayı",
    )

    # ── Toplam tutarlar ───────────────────────────────────────────────
    total_offer_tl: float = Field(description="Toplam teklif tutarı (TL)")
    total_cost_tl: float = Field(description="Toplam maliyet (TL)")
    total_consumption_kwh: float = Field(description="Toplam tüketim (kWh)")
    offer_unit_price_tl_per_kwh: float = Field(description="Teklif birim fiyat (TL/kWh)")
    weighted_cost_tl_per_kwh: float = Field(description="Ağırlıklı maliyet (TL/kWh)")

    # ── En kötü / en iyi saatler ──────────────────────────────────────
    worst_hours: list[HourlyMarginDetail] = Field(
        default_factory=list,
        description="En çok zarar edilen 10 saat",
    )
    best_hours: list[HourlyMarginDetail] = Field(
        default_factory=list,
        description="En çok kâr edilen 10 saat",
    )

    # ── Histogram verisi ──────────────────────────────────────────────
    hourly_margins_tl: list[float] = Field(
        default_factory=list,
        description="Tüm saatlerin marj değerleri (TL) — histogram için",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Ana Hesaplama Fonksiyonu
# ═══════════════════════════════════════════════════════════════════════════════


def calculate_margin_reality(
    offer_ptf_tl_per_mwh: float,
    yekdem_tl_per_mwh: float,
    multiplier: float,
    hourly_ptf_prices: list[float],
    hourly_consumption_kwh: list[float],
    hourly_timestamps: list[str] | None = None,
    hourly_time_zones: list[str] | None = None,
    include_yekdem: bool = True,
    margin_erosion_threshold_pct: float = 1.0,
    safe_multiplier_buffer: float = 0.01,
) -> MarginRealityResult:
    """Nominal (kağıt üzeri) marj ile gerçek (saatlik) marjı karşılaştır.

    Args:
        offer_ptf_tl_per_mwh: Teklif PTF (dönem ortalaması, TL/MWh).
        yekdem_tl_per_mwh: YEKDEM bedeli (TL/MWh).
        multiplier: Katsayı (örn: 1.04).
        hourly_ptf_prices: Saatlik PTF verileri (TL/MWh), her saat için.
        hourly_consumption_kwh: Saatlik tüketim (kWh), her saat için.
        hourly_timestamps: Opsiyonel saat etiketleri ("YYYY-MM-DD HH:00").
        hourly_time_zones: Opsiyonel zaman dilimleri ("T1"/"T2"/"T3").
        include_yekdem: YEKDEM dahil mi.
        margin_erosion_threshold_pct: Marj eriyor eşiği (varsayılan %1).
        safe_multiplier_buffer: Güvenli katsayı tamponu (varsayılan +0.01).

    Returns:
        MarginRealityResult: Tüm marj metrikleri ve karar.
    """
    assert len(hourly_ptf_prices) == len(hourly_consumption_kwh), (
        f"PTF ({len(hourly_ptf_prices)}) ve tüketim ({len(hourly_consumption_kwh)}) "
        f"dizileri aynı uzunlukta olmalı"
    )

    yekdem = yekdem_tl_per_mwh if include_yekdem else 0.0

    # ── 1. Sabit satış fiyatı ──────────────────────────────────────────
    offer_unit_price = (offer_ptf_tl_per_mwh + yekdem) / 1000.0 * multiplier

    # ── 2. Nominal (kağıt üzeri) hesap ─────────────────────────────────
    nominal_margin_pct = (multiplier - 1.0) * 100.0
    nominal_cost_per_kwh = (offer_ptf_tl_per_mwh + yekdem) / 1000.0

    # ── 3. Saatlik hesaplama ───────────────────────────────────────────
    total_hours = len(hourly_ptf_prices)
    hourly_margins: list[float] = []
    hourly_details: list[HourlyMarginDetail] = []
    total_cost = 0.0
    total_offer = 0.0
    total_kwh = 0.0
    negative_hours = 0
    negative_total = 0.0
    positive_total = 0.0

    for i in range(total_hours):
        ptf_h = hourly_ptf_prices[i]
        kwh_h = hourly_consumption_kwh[i]

        if kwh_h <= 0:
            hourly_margins.append(0.0)
            continue

        # Saatlik maliyet (TL/kWh)
        cost_per_kwh_h = (ptf_h + yekdem) / 1000.0
        cost_h = cost_per_kwh_h * kwh_h

        # Saatlik gelir
        offer_h = offer_unit_price * kwh_h

        # Saatlik marj
        margin_h = offer_h - cost_h

        hourly_margins.append(round(margin_h, 2))
        total_cost += cost_h
        total_offer += offer_h
        total_kwh += kwh_h

        if margin_h < 0:
            negative_hours += 1
            negative_total += margin_h
        else:
            positive_total += margin_h

        # Detay kaydı (en kötü/en iyi için)
        ts = hourly_timestamps[i] if hourly_timestamps and i < len(hourly_timestamps) else f"H{i:04d}"
        tz = hourly_time_zones[i] if hourly_time_zones and i < len(hourly_time_zones) else None
        hourly_details.append(HourlyMarginDetail(
            hour=ts,
            ptf_tl_per_mwh=round(ptf_h, 2),
            consumption_kwh=round(kwh_h, 2),
            cost_tl=round(cost_h, 2),
            margin_tl=round(margin_h, 2),
            time_zone=tz,
        ))

    # ── 4. Toplam gerçek marj ──────────────────────────────────────────
    real_margin_tl = total_offer - total_cost
    nominal_margin_tl = (offer_unit_price - nominal_cost_per_kwh) * total_kwh

    # ── 5. Gerçek marj oranı ───────────────────────────────────────────
    # Cost-based margin (iç analiz): marj / maliyet
    if total_cost > 0:
        real_margin_pct = (real_margin_tl / total_cost) * 100.0
    else:
        real_margin_pct = 0.0

    # Revenue-based margin (ticari karar): marj / ciro
    if total_offer > 0:
        real_margin_on_revenue_pct = (real_margin_tl / total_offer) * 100.0
    else:
        real_margin_on_revenue_pct = 0.0

    # ── 6. Marj sapması ────────────────────────────────────────────────
    margin_deviation_pct = real_margin_pct - nominal_margin_pct
    margin_deviation_tl = real_margin_tl - nominal_margin_tl

    # ── 7. Effective Multiplier + delta ──────────────────────────────
    weighted_cost_per_kwh = total_cost / total_kwh if total_kwh > 0 else 0.0
    if weighted_cost_per_kwh > 0:
        effective_multiplier = offer_unit_price / weighted_cost_per_kwh
    else:
        effective_multiplier = multiplier
    multiplier_delta = effective_multiplier - multiplier

    # ── 8. Break-even katsayı ──────────────────────────────────────────
    base_unit_price = (offer_ptf_tl_per_mwh + yekdem) / 1000.0
    if base_unit_price > 0:
        break_even_multiplier = weighted_cost_per_kwh / base_unit_price
    else:
        break_even_multiplier = 1.0

    safe_mult = break_even_multiplier + safe_multiplier_buffer

    # ── 8b. Target Multiplier ─────────────────────────────────────────
    # "Bu müşteri için %4 gerçek marj istiyorsan kaç ile satmalısın?"
    # Hedef: (offer_price × total_kwh) - total_cost = target_pct/100 × total_cost
    # offer_price = base_price × target_mult
    # target_mult = weighted_cost × (1 + target_pct/100) / base_price
    #
    # Nominal marj % kullanıcının girdiği katsayıdan türetilir.
    target_margin = nominal_margin_pct  # kullanıcının istediği marj
    if base_unit_price > 0 and total_kwh > 0:
        required_mult = weighted_cost_per_kwh * (1.0 + target_margin / 100.0) / base_unit_price
    else:
        required_mult = multiplier

    # ── 9. En kötü / en iyi 10 saat ───────────────────────────────────
    sorted_by_margin = sorted(hourly_details, key=lambda d: d.margin_tl)
    worst_10 = sorted_by_margin[:10]
    best_10 = sorted_by_margin[-10:][::-1]  # en iyiler büyükten küçüğe

    # ── 10. Karar (verdict) ────────────────────────────────────────────
    verdict = _determine_verdict(
        real_margin_tl=real_margin_tl,
        real_margin_pct=real_margin_pct,
        nominal_margin_pct=nominal_margin_pct,
        margin_deviation_pct=margin_deviation_pct,
        erosion_threshold=margin_erosion_threshold_pct,
    )

    # ── 11. Teklif kararı (pricing decision) ──────────────────────────
    decision, decision_reason = _determine_pricing_decision(
        real_margin_pct=real_margin_pct,
        nominal_margin_pct=nominal_margin_pct,
        required_multiplier=required_mult,
        multiplier=multiplier,
    )

    # ── 12. Agresiflik seviyesi ────────────────────────────────────────
    aggressiveness = _determine_aggressiveness(
        current_multiplier=multiplier,
        required_multiplier=required_mult,
        decision=decision,
    )

    logger.info(
        "margin_reality: multiplier=%.2f nominal=%.1f%% real=%.1f%% "
        "deviation=%.1f%% effective=%.3f verdict=%s",
        multiplier, nominal_margin_pct, real_margin_pct,
        margin_deviation_pct, effective_multiplier, verdict.value,
    )

    return MarginRealityResult(
        verdict=verdict,
        pricing_decision=decision,
        pricing_decision_reason=decision_reason,
        pricing_aggressiveness=aggressiveness,
        multiplier=round(multiplier, 4),
        effective_multiplier=round(effective_multiplier, 4),
        nominal_margin_pct=round(nominal_margin_pct, 2),
        nominal_margin_tl=round(nominal_margin_tl, 2),
        real_margin_pct=round(real_margin_pct, 2),
        real_margin_tl=round(real_margin_tl, 2),
        margin_deviation_pct=round(margin_deviation_pct, 2),
        margin_deviation_tl=round(margin_deviation_tl, 2),
        real_margin_on_revenue_pct=round(real_margin_on_revenue_pct, 2),
        multiplier_delta=round(multiplier_delta, 4),
        total_hours=total_hours,
        negative_margin_hours=negative_hours,
        negative_margin_total_tl=round(negative_total, 2),
        positive_margin_total_tl=round(positive_total, 2),
        break_even_multiplier=round(break_even_multiplier, 4),
        safe_multiplier=round(safe_mult, 4),
        target_margin_pct=round(target_margin, 2),
        required_multiplier_for_target=round(required_mult, 4),
        total_offer_tl=round(total_offer, 2),
        total_cost_tl=round(total_cost, 2),
        total_consumption_kwh=round(total_kwh, 2),
        offer_unit_price_tl_per_kwh=round(offer_unit_price, 6),
        weighted_cost_tl_per_kwh=round(weighted_cost_per_kwh, 6),
        worst_hours=worst_10,
        best_hours=best_10,
        hourly_margins_tl=hourly_margins,
    )


def _determine_verdict(
    real_margin_tl: float,
    real_margin_pct: float,
    nominal_margin_pct: float,
    margin_deviation_pct: float,
    erosion_threshold: float,
) -> MarginVerdict:
    """Marj gerçekleşme kararını belirle.

    Karar mantığı:
      1. Gerçek marj < 0 → ZARARLI
      2. Gerçek marj > 0 ve sapma > +eşik → OVERPERFORM
      3. Gerçek marj > 0 ve sapma < -eşik → MARJ ERİYOR
      4. Gerçek marj > 0 ve sapma ±eşik içinde → KÂRLI
    """
    if real_margin_tl < 0:
        return MarginVerdict.LOSS

    if margin_deviation_pct > erosion_threshold:
        return MarginVerdict.OVERPERFORM

    if margin_deviation_pct < -erosion_threshold:
        return MarginVerdict.MARGIN_ERODING

    return MarginVerdict.PROFITABLE


def _determine_pricing_decision(
    real_margin_pct: float,
    nominal_margin_pct: float,
    required_multiplier: float,
    multiplier: float,
) -> tuple[PricingDecision, str]:
    """Teklif kararı üret — satışçıya ne yapması gerektiğini söyle.

    Karar mantığı:
      1. Gerçek marj < 0 → REJECT (zarar — teklif verme)
      2. Gerçek marj < hedef marj → REPRICE (fiyat artır)
      3. Gerçek marj ≥ hedef marj × 1.5 → OVERPRICE (fazla kâr — fiyat düşür)
      4. Gerçek marj ≈ hedef marj → ACCEPT (teklif uygun)

    Returns:
        (PricingDecision, reason_string)
    """
    if real_margin_pct < 0:
        return (
            PricingDecision.REJECT,
            f"Gerçek marj negatif (%{real_margin_pct:.1f}). Bu müşteriye bu fiyatla teklif verme.",
        )

    if real_margin_pct < nominal_margin_pct * 0.5:
        return (
            PricingDecision.REPRICE,
            f"Gerçek marj %{real_margin_pct:.1f}, hedef %{nominal_margin_pct:.1f}. "
            f"×{required_multiplier:.3f} ile fiyatla.",
        )

    if nominal_margin_pct > 0 and real_margin_pct >= nominal_margin_pct * 1.5:
        return (
            PricingDecision.OVERPRICE,
            f"Gerçek marj %{real_margin_pct:.1f}, hedefin %50 üstünde. "
            f"Rekabet avantajı için fiyat düşürülebilir.",
        )

    return (
        PricingDecision.ACCEPT,
        f"Gerçek marj %{real_margin_pct:.1f}, hedef %{nominal_margin_pct:.1f} ile uyumlu. Teklif uygun.",
    )


def _determine_aggressiveness(
    current_multiplier: float,
    required_multiplier: float,
    decision: PricingDecision,
) -> PricingAggressiveness:
    """Fiyat değişikliği agresiflik seviyesi — müşteri kayıp riski.

    Fark = |required - current|
      0–0.01  → LOW (rahat)
      0.01–0.03 → MEDIUM (dikkat)
      >0.03   → HIGH (müşteri kayıp riski)
    """
    if decision == PricingDecision.ACCEPT:
        return PricingAggressiveness.NONE

    diff = abs(required_multiplier - current_multiplier)

    if diff <= 0.01:
        return PricingAggressiveness.LOW
    elif diff <= 0.03:
        return PricingAggressiveness.MEDIUM
    else:
        return PricingAggressiveness.HIGH
