"""
Pricing Risk Engine — Risk Skoru ve Teklif Uyarı Sistemi Testleri.

Task 16.1: calculate_risk_score() testleri
Task 16.2: generate_offer_warning() testleri

Risk modeli:
  Ana sinyal: Ağırlıklı PTF sapması
  Override: T2 tüketim payı, peak concentration
"""

import pytest
from app.pricing.models import (
    RiskLevel,
    WeightedPriceResult,
    TimeZoneBreakdown,
)
from app.pricing.risk_calculator import (
    calculate_risk_score,
    generate_offer_warning,
    check_risk_safe_multiplier_coherence,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _weighted(
    weighted_ptf: float = 2000.0,
    arithmetic_avg: float = 2000.0,
    weighted_smf: float = 2100.0,
    arithmetic_avg_smf: float = 2100.0,
    total_kwh: float = 100000.0,
    total_cost: float = 200000.0,
    hours: int = 744,
) -> WeightedPriceResult:
    return WeightedPriceResult(
        weighted_ptf_tl_per_mwh=weighted_ptf,
        weighted_smf_tl_per_mwh=weighted_smf,
        arithmetic_avg_ptf=arithmetic_avg,
        arithmetic_avg_smf=arithmetic_avg_smf,
        total_consumption_kwh=total_kwh,
        total_cost_tl=total_cost,
        hours_count=hours,
    )


def _tz_breakdown(
    t1_kwh: float = 50000.0,
    t1_pct: float = 50.0,
    t1_cost: float = 100000.0,
    t2_kwh: float = 25000.0,
    t2_pct: float = 25.0,
    t2_cost: float = 75000.0,
    t3_kwh: float = 25000.0,
    t3_pct: float = 25.0,
    t3_cost: float = 25000.0,
) -> dict[str, TimeZoneBreakdown]:
    return {
        "T1": TimeZoneBreakdown(
            label="Gündüz (06:00-16:59)",
            consumption_kwh=t1_kwh,
            consumption_pct=t1_pct,
            weighted_ptf_tl_per_mwh=2000.0,
            weighted_smf_tl_per_mwh=2100.0,
            total_cost_tl=t1_cost,
        ),
        "T2": TimeZoneBreakdown(
            label="Puant (17:00-21:59)",
            consumption_kwh=t2_kwh,
            consumption_pct=t2_pct,
            weighted_ptf_tl_per_mwh=3000.0,
            weighted_smf_tl_per_mwh=3100.0,
            total_cost_tl=t2_cost,
        ),
        "T3": TimeZoneBreakdown(
            label="Gece (22:00-05:59)",
            consumption_kwh=t3_kwh,
            consumption_pct=t3_pct,
            weighted_ptf_tl_per_mwh=1500.0,
            weighted_smf_tl_per_mwh=1600.0,
            total_cost_tl=t3_cost,
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Task 16.1: Risk Skoru Testleri
# ═══════════════════════════════════════════════════════════════════════════════

class TestRiskScore:
    """calculate_risk_score() testleri."""

    # ─── Sapma bazlı eşikler ────────────────────────────────────────────

    def test_low_risk_small_deviation(self):
        """Sapma < %2 → Düşük risk + açıklama."""
        # weighted=2000, avg=2000 → sapma=%0
        wr = _weighted(weighted_ptf=2000.0, arithmetic_avg=2000.0)
        tz = _tz_breakdown()
        result = calculate_risk_score(wr, tz)

        assert result.score == RiskLevel.LOW
        assert result.deviation_pct == pytest.approx(0.0)
        assert len(result.reasons) >= 1
        assert "düşük" in result.reasons[0].lower()

    def test_low_risk_just_under_2pct(self):
        """Sapma = %1.9 → Düşük risk."""
        # weighted=2038, avg=2000 → sapma=1.9%
        wr = _weighted(weighted_ptf=2038.0, arithmetic_avg=2000.0)
        tz = _tz_breakdown()
        result = calculate_risk_score(wr, tz)

        assert result.score == RiskLevel.LOW
        assert result.deviation_pct < 2.0

    def test_medium_risk_at_2pct(self):
        """Sapma = %2.0 → Orta risk."""
        # weighted=2040, avg=2000 → sapma=2.0%
        wr = _weighted(weighted_ptf=2040.0, arithmetic_avg=2000.0)
        tz = _tz_breakdown()
        result = calculate_risk_score(wr, tz)

        assert result.score == RiskLevel.MEDIUM
        assert result.deviation_pct >= 2.0

    def test_medium_risk_at_4pct(self):
        """Sapma = %4.0 → Orta risk."""
        wr = _weighted(weighted_ptf=2080.0, arithmetic_avg=2000.0)
        tz = _tz_breakdown()
        result = calculate_risk_score(wr, tz)

        assert result.score == RiskLevel.MEDIUM
        assert 2.0 <= result.deviation_pct <= 5.0

    def test_high_risk_above_5pct(self):
        """Sapma > %5 → Yüksek risk + açıklama."""
        # weighted=2120, avg=2000 → sapma=6.0%
        wr = _weighted(weighted_ptf=2120.0, arithmetic_avg=2000.0)
        tz = _tz_breakdown()
        result = calculate_risk_score(wr, tz)

        assert result.score == RiskLevel.HIGH
        assert result.deviation_pct > 5.0
        assert any("yüksek sapma" in r for r in result.reasons)

    def test_negative_deviation_uses_absolute(self):
        """Negatif sapma (weighted < avg) mutlak değer kullanır."""
        # weighted=1900, avg=2000 → sapma=|−5%|=5%
        wr = _weighted(weighted_ptf=1900.0, arithmetic_avg=2000.0)
        tz = _tz_breakdown()
        result = calculate_risk_score(wr, tz)

        assert result.deviation_pct == pytest.approx(5.0)
        assert result.score == RiskLevel.MEDIUM  # %5 tam sınırda → %2–%5 aralığı

    # ─── T2 override ────────────────────────────────────────────────────

    def test_t2_override_40pct_upgrades_low_to_medium(self):
        """T2 > %40 → Düşük risk Orta'ya yükseltilir."""
        wr = _weighted(weighted_ptf=2000.0, arithmetic_avg=2000.0)  # sapma=0 → Düşük
        tz = _tz_breakdown(t2_pct=42.0)
        result = calculate_risk_score(wr, tz)

        assert result.score == RiskLevel.MEDIUM

    def test_t2_override_55pct_forces_high(self):
        """T2 > %55 → risk Yüksek'e zorlanır + açıklama."""
        wr = _weighted(weighted_ptf=2000.0, arithmetic_avg=2000.0)  # sapma=0 → Düşük
        tz = _tz_breakdown(t2_pct=58.0)
        result = calculate_risk_score(wr, tz)

        assert result.score == RiskLevel.HIGH
        assert any("puant" in r.lower() for r in result.reasons)

    def test_t2_override_does_not_downgrade(self):
        """T2 override sadece yükseltir, düşürmez."""
        wr = _weighted(weighted_ptf=2120.0, arithmetic_avg=2000.0)  # sapma=6% → Yüksek
        tz = _tz_breakdown(t2_pct=10.0)  # Düşük T2
        result = calculate_risk_score(wr, tz)

        assert result.score == RiskLevel.HIGH  # Yüksek kalır

    # ─── Peak concentration override ────────────────────────────────────

    def test_peak_concentration_override_upgrades_low(self):
        """Peak concentration > %45 → Düşük risk Orta'ya yükseltilir."""
        wr = _weighted(weighted_ptf=2000.0, arithmetic_avg=2000.0)  # sapma=0 → Düşük
        # T2 maliyet payı > %45: t2_cost=50000, toplam=100000 → %50
        tz = _tz_breakdown(
            t1_cost=30000.0, t2_cost=50000.0, t3_cost=20000.0,
            t2_pct=25.0,  # T2 tüketim payı düşük (override tetiklemesin)
        )
        result = calculate_risk_score(wr, tz)

        assert result.peak_concentration > 45.0
        assert result.score == RiskLevel.MEDIUM

    def test_peak_concentration_low_no_override(self):
        """Peak concentration < %45 → override yok."""
        wr = _weighted(weighted_ptf=2000.0, arithmetic_avg=2000.0)
        tz = _tz_breakdown(
            t1_cost=60000.0, t2_cost=20000.0, t3_cost=20000.0,
            t2_pct=20.0,
        )
        result = calculate_risk_score(wr, tz)

        assert result.peak_concentration < 45.0
        assert result.score == RiskLevel.LOW

    # ─── Edge cases ─────────────────────────────────────────────────────

    def test_zero_arithmetic_avg(self):
        """Aritmetik ortalama 0 ise sapma 0."""
        wr = _weighted(weighted_ptf=0.0, arithmetic_avg=0.0)
        tz = _tz_breakdown()
        result = calculate_risk_score(wr, tz)

        assert result.deviation_pct == 0.0
        assert result.score == RiskLevel.LOW

    def test_result_fields_populated(self):
        """Tüm sonuç alanları doldurulur."""
        wr = _weighted(weighted_ptf=2100.0, arithmetic_avg=2000.0)
        tz = _tz_breakdown()
        result = calculate_risk_score(wr, tz)

        assert result.weighted_ptf == 2100.0
        assert result.arithmetic_avg_ptf == 2000.0
        assert result.deviation_pct > 0
        assert result.t2_consumption_pct >= 0
        assert result.peak_concentration >= 0

    def test_missing_t2_breakdown(self):
        """T2 verisi yoksa T2 payı 0, peak concentration 0."""
        wr = _weighted(weighted_ptf=2000.0, arithmetic_avg=2000.0)
        tz = {
            "T1": TimeZoneBreakdown(
                label="Gündüz", consumption_kwh=100000.0,
                consumption_pct=100.0, weighted_ptf_tl_per_mwh=2000.0,
                weighted_smf_tl_per_mwh=2100.0, total_cost_tl=200000.0,
            ),
        }
        result = calculate_risk_score(wr, tz)

        assert result.t2_consumption_pct == 0.0
        assert result.peak_concentration == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Task 16.2: Teklif Uyarı Sistemi Testleri
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfferWarning:
    """generate_offer_warning() testleri."""

    def test_no_warning_when_above_safe(self):
        """Seçilen katsayı ≥ güvenli katsayı → uyarı yok."""
        result = generate_offer_warning(
            selected_multiplier=1.05,
            safe_multiplier=1.042,
            recommended_multiplier=1.05,
        )
        assert result is None

    def test_no_warning_when_equal_safe(self):
        """Seçilen katsayı = güvenli katsayı → uyarı yok."""
        result = generate_offer_warning(
            selected_multiplier=1.042,
            safe_multiplier=1.042,
            recommended_multiplier=1.05,
        )
        assert result is None

    def test_warning_when_below_safe(self):
        """Seçilen katsayı < güvenli katsayı → uyarı mesajı."""
        result = generate_offer_warning(
            selected_multiplier=1.03,
            safe_multiplier=1.042,
            recommended_multiplier=1.05,
            risk_level=RiskLevel.MEDIUM,
        )
        assert result is not None
        assert "1.03" in result
        assert "1.042" in result
        assert "1.05" in result
        assert "riskli" in result.lower()
        assert "Risk: Orta" in result

    def test_warning_format(self):
        """Uyarı mesajı doğru formatta."""
        result = generate_offer_warning(
            selected_multiplier=1.02,
            safe_multiplier=1.057,
            recommended_multiplier=1.06,
            risk_level=RiskLevel.HIGH,
        )
        expected = (
            "Bu müşteri için ×1.02 riskli (Risk: Yüksek). "
            "Minimum güvenli katsayı: ×1.057. "
            "Önerilen: ×1.06"
        )
        assert result == expected

    def test_warning_without_risk_level(self):
        """Risk seviyesi verilmezse parantez eklenmez."""
        result = generate_offer_warning(
            selected_multiplier=1.03,
            safe_multiplier=1.042,
            recommended_multiplier=1.05,
        )
        assert result is not None
        assert "Risk:" not in result

    def test_warning_with_high_safe_multiplier(self):
        """Yüksek güvenli katsayı ile uyarı."""
        result = generate_offer_warning(
            selected_multiplier=1.05,
            safe_multiplier=1.100,
            recommended_multiplier=1.10,
        )
        assert result is not None
        assert "1.100" in result



# ═══════════════════════════════════════════════════════════════════════════════
# Risk / Safe Multiplier Tutarlılık Kontrolü
# ═══════════════════════════════════════════════════════════════════════════════

class TestRiskCoherence:
    """check_risk_safe_multiplier_coherence() testleri."""

    def test_low_risk_low_multiplier_no_warning(self):
        """Düşük risk + düşük katsayı → tutarlı, uyarı yok."""
        result = check_risk_safe_multiplier_coherence(RiskLevel.LOW, 1.025)
        assert result is None

    def test_low_risk_high_multiplier_warning(self):
        """Düşük risk + yüksek katsayı (>1.06) → tutarsızlık uyarısı."""
        result = check_risk_safe_multiplier_coherence(RiskLevel.LOW, 1.075)
        assert result is not None
        assert "Tutarsızlık" in result

    def test_high_risk_high_multiplier_no_warning(self):
        """Yüksek risk + yüksek katsayı → tutarlı, uyarı yok."""
        result = check_risk_safe_multiplier_coherence(RiskLevel.HIGH, 1.08)
        assert result is None

    def test_high_risk_low_multiplier_warning(self):
        """Yüksek risk + düşük katsayı (<1.02) → tutarsızlık uyarısı."""
        result = check_risk_safe_multiplier_coherence(RiskLevel.HIGH, 1.015)
        assert result is not None
        assert "Tutarsızlık" in result

    def test_medium_risk_no_coherence_check(self):
        """Orta risk → tutarlılık kontrolü tetiklenmez."""
        result = check_risk_safe_multiplier_coherence(RiskLevel.MEDIUM, 1.08)
        assert result is None
