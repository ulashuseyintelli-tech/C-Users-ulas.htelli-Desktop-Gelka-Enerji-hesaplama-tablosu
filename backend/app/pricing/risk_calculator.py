"""
Pricing Risk Engine — Profil Risk Skoru ve Teklif Uyarı Sistemi.

Risk modeli (v1):
  Ana sinyal: Ağırlıklı PTF sapması (weighted vs arithmetic avg)
  Destek sinyalleri: T2 tüketim payı, peak concentration

Eşikler:
  Sapma > %5       → Yüksek
  Sapma %2–%5      → Orta
  Sapma < %2       → Düşük

Override kuralları:
  T2 tüketim payı > %40  → risk en az Orta
  T2 tüketim payı > %55  → risk Yüksek
  Peak concentration > %45 → risk en az Orta

Uyarı sistemi:
  Seçilen katsayı < güvenli katsayı → uyarı mesajı üret
  Seçilen katsayı ≥ güvenli katsayı → uyarı yok

Requirements: 12.1–12.5, 13.1–13.4
"""

from __future__ import annotations

import logging

from .models import (
    RiskLevel,
    RiskScoreResult,
    WeightedPriceResult,
    TimeZoneBreakdown,
)

logger = logging.getLogger(__name__)


def calculate_risk_score(
    weighted_result: WeightedPriceResult,
    time_zone_breakdown: dict[str, TimeZoneBreakdown],
) -> RiskScoreResult:
    """Profil risk skoru hesapla.

    Üç katmanlı model:
    1. Ana sinyal: Ağırlıklı PTF sapması
    2. T2 override: Puant dilimi tüketim yoğunluğu
    3. Peak concentration override: Yüksek PTF saatlerine yoğunlaşma

    Args:
        weighted_result: Ağırlıklı fiyat hesaplama sonucu.
        time_zone_breakdown: T1/T2/T3 zaman dilimi dağılımı.

    Returns:
        RiskScoreResult: Risk skoru sonucu (reasons dahil).
    """
    weighted_ptf = weighted_result.weighted_ptf_tl_per_mwh
    arithmetic_avg = weighted_result.arithmetic_avg_ptf
    reasons: list[str] = []

    # ─── 1. Ana sinyal: Sapma yüzdesi ───────────────────────────────────
    if arithmetic_avg != 0:
        deviation_pct = abs(weighted_ptf - arithmetic_avg) / arithmetic_avg * 100.0
    else:
        deviation_pct = 0.0

    deviation_pct = round(deviation_pct, 2)

    # Sapma bazlı risk seviyesi
    if deviation_pct > 5.0:
        risk = RiskLevel.HIGH
        direction = "üzerinde" if weighted_ptf > arithmetic_avg else "altında"
        reasons.append(
            f"Ağırlıklı PTF ortalamanın %{deviation_pct:.1f} {direction} → yüksek sapma"
        )
    elif deviation_pct >= 2.0:
        risk = RiskLevel.MEDIUM
        direction = "üzerinde" if weighted_ptf > arithmetic_avg else "altında"
        reasons.append(
            f"Ağırlıklı PTF ortalamanın %{deviation_pct:.1f} {direction}"
        )
    else:
        risk = RiskLevel.LOW
        reasons.append(
            f"Ağırlıklı PTF sapması düşük (%{deviation_pct:.1f})"
        )

    # ─── 2. T2 tüketim payı ─────────────────────────────────────────────
    t2_breakdown = time_zone_breakdown.get("T2")
    t2_consumption_pct = t2_breakdown.consumption_pct if t2_breakdown else 0.0

    # T2 override
    if t2_consumption_pct > 55.0:
        risk = RiskLevel.HIGH
        reasons.append(
            f"T2 (puant) tüketim oranı %{t2_consumption_pct:.0f} → çok yüksek puant riski"
        )
    elif t2_consumption_pct > 40.0:
        if risk == RiskLevel.LOW:
            risk = RiskLevel.MEDIUM
        reasons.append(
            f"T2 (puant) tüketim oranı %{t2_consumption_pct:.0f} → puant riski yüksek"
        )

    # ─── 3. Peak concentration ───────────────────────────────────────────
    peak_concentration = _calculate_peak_concentration(
        weighted_result, time_zone_breakdown,
    )

    # Peak concentration override
    if peak_concentration > 45.0:
        if risk == RiskLevel.LOW:
            risk = RiskLevel.MEDIUM
        reasons.append(
            f"Puant saatleri maliyet payı %{peak_concentration:.0f} → yoğunlaşma riski"
        )

    return RiskScoreResult(
        score=risk,
        weighted_ptf=weighted_ptf,
        arithmetic_avg_ptf=arithmetic_avg,
        deviation_pct=deviation_pct,
        t2_consumption_pct=round(t2_consumption_pct, 2),
        peak_concentration=round(peak_concentration, 2),
        reasons=reasons,
    )


def _calculate_peak_concentration(
    weighted_result: WeightedPriceResult,
    time_zone_breakdown: dict[str, TimeZoneBreakdown],
) -> float:
    """Yüksek PTF saatlerine yoğunlaşma oranı hesapla.

    Peak concentration = T2 maliyet payı (%)
    T2 maliyetinin toplam maliyete oranı — puant saatlerinde
    ne kadar para harcandığını gösterir.

    Returns:
        Peak concentration yüzdesi (0–100).
    """
    t2_breakdown = time_zone_breakdown.get("T2")
    if not t2_breakdown:
        return 0.0

    # Toplam maliyet
    total_cost = sum(
        tz.total_cost_tl for tz in time_zone_breakdown.values()
    )

    if total_cost <= 0:
        return 0.0

    return t2_breakdown.total_cost_tl / total_cost * 100.0


# ═══════════════════════════════════════════════════════════════════════════════
# Teklif Uyarı Sistemi
# ═══════════════════════════════════════════════════════════════════════════════


def generate_offer_warning(
    selected_multiplier: float,
    safe_multiplier: float,
    recommended_multiplier: float,
    risk_level: RiskLevel | None = None,
) -> str | None:
    """Teklif uyarı mesajı üret.

    Kurallar:
    - Seçilen katsayı < güvenli katsayı → uyarı mesajı
    - Seçilen katsayı ≥ güvenli katsayı → None (uyarı yok)

    Args:
        selected_multiplier: Seçilen katsayı.
        safe_multiplier: Güvenli katsayı (5. persentil).
        recommended_multiplier: Önerilen katsayı (bir üst 0.01 adımı).
        risk_level: Risk seviyesi (opsiyonel, mesaja eklenir).

    Returns:
        Uyarı mesajı veya None.
    """
    if selected_multiplier >= safe_multiplier:
        return None

    risk_str = f" (Risk: {risk_level.value})" if risk_level else ""

    return (
        f"Bu müşteri için ×{selected_multiplier:.2f} riskli{risk_str}. "
        f"Minimum güvenli katsayı: ×{safe_multiplier:.3f}. "
        f"Önerilen: ×{recommended_multiplier:.2f}"
    )


def check_risk_safe_multiplier_coherence(
    risk_level: RiskLevel,
    safe_multiplier: float,
) -> str | None:
    """Risk seviyesi ile güvenli katsayı arasındaki tutarlılığı kontrol et.

    Çelişki durumları:
    - Risk = Düşük ama safe_multiplier > 1.06 → uyarı log'la
    - Risk = Yüksek ama safe_multiplier < 1.02 → uyarı log'la

    Args:
        risk_level: Hesaplanan risk seviyesi.
        safe_multiplier: Hesaplanan güvenli katsayı.

    Returns:
        Çelişki uyarı mesajı veya None.
    """
    warning = None

    if risk_level == RiskLevel.LOW and safe_multiplier > 1.06:
        warning = (
            f"Tutarsızlık: Risk seviyesi Düşük ama güvenli katsayı ×{safe_multiplier:.3f} "
            f"(>×1.060). Profil dağılımı düzgün ama bazı saatlerde yüksek PTF var."
        )
        logger.warning("risk_coherence_mismatch: %s", warning)

    elif risk_level == RiskLevel.HIGH and safe_multiplier < 1.02:
        warning = (
            f"Tutarsızlık: Risk seviyesi Yüksek ama güvenli katsayı ×{safe_multiplier:.3f} "
            f"(<×1.020). Profil yoğunlaşması yüksek ama PTF yayılımı düşük."
        )
        logger.warning("risk_coherence_mismatch: %s", warning)

    return warning
