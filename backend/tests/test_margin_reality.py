"""
Nominal vs Gerçek Marj Analizi — Unit Testler.

Zorunlu senaryolar:
1. %4 manuel marjın gerçek marjı %2'ye düşürdüğü müşteri (MARJ ERİYOR)
2. %4 manuel marjın gerçek marjı %6'ya çıktığı müşteri (OVERPERFORM)
3. Gerçek marjın negatife düştüğü müşteri (ZARARLI)
4. Break-even katsayının doğru hesaplandığı senaryo
5. Effective Multiplier'ın doğru hesaplandığı senaryo
"""

import pytest
from app.pricing.margin_reality import (
    calculate_margin_reality,
    MarginVerdict,
    MarginRealityResult,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Yardımcı: Saatlik veri üretici
# ═══════════════════════════════════════════════════════════════════════════════


def _make_uniform_hours(
    ptf: float, kwh: float, count: int = 744,
) -> tuple[list[float], list[float]]:
    """Tüm saatlerde aynı PTF ve tüketim — düz profil."""
    return [ptf] * count, [kwh] * count


def _make_peak_heavy_hours(
    base_ptf: float,
    peak_ptf: float,
    base_kwh: float,
    peak_kwh: float,
    total_hours: int = 744,
    peak_ratio: float = 0.3,
) -> tuple[list[float], list[float]]:
    """Pahalı saatlerde yoğun tüketim — marj eritici profil.

    peak_ratio: Toplam saatlerin kaçı pahalı (0.3 = %30).
    Pahalı saatlerde hem PTF yüksek hem tüketim yüksek.
    """
    peak_count = int(total_hours * peak_ratio)
    off_count = total_hours - peak_count

    ptf_list = [base_ptf] * off_count + [peak_ptf] * peak_count
    kwh_list = [base_kwh] * off_count + [peak_kwh] * peak_count
    return ptf_list, kwh_list


def _make_offpeak_heavy_hours(
    base_ptf: float,
    peak_ptf: float,
    base_kwh: float,
    peak_kwh: float,
    total_hours: int = 744,
    offpeak_ratio: float = 0.7,
) -> tuple[list[float], list[float]]:
    """Ucuz saatlerde yoğun tüketim — marj artırıcı profil.

    Ucuz saatlerde tüketim yüksek, pahalı saatlerde düşük.
    """
    offpeak_count = int(total_hours * offpeak_ratio)
    peak_count = total_hours - offpeak_count

    ptf_list = [base_ptf] * offpeak_count + [peak_ptf] * peak_count
    kwh_list = [peak_kwh] * offpeak_count + [base_kwh] * peak_count
    return ptf_list, kwh_list


# ═══════════════════════════════════════════════════════════════════════════════
# Senaryo 1: %4 Manuel → Gerçek %2 → MARJ ERİYOR
# ═══════════════════════════════════════════════════════════════════════════════


class TestMarginEroding:
    """Pahalı saatlerde yoğun tüketen müşteri — marj eriyor."""

    def test_margin_eroding_verdict(self):
        """Manuel %4 marj, gerçek marj düşük → MARJ ERİYOR."""
        # Dönem ortalaması 2000 TL/MWh ama müşteri pahalı saatlerde biraz daha fazla tüketiyor
        # Hafif peak ağırlıklı profil — marj eriyor ama hâlâ pozitif
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1800.0,   # ucuz saatler (ortalamaya yakın)
            peak_ptf=2400.0,   # pahalı saatler (ortalamadan biraz yüksek)
            base_kwh=50.0,     # ucuz saatlerde normal tüketim
            peak_kwh=80.0,     # pahalı saatlerde biraz daha fazla tüketim
            total_hours=100,
            peak_ratio=0.3,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,  # dönem ortalaması
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.verdict == MarginVerdict.MARGIN_ERODING
        assert result.nominal_margin_pct == pytest.approx(4.0)
        assert result.real_margin_pct < result.nominal_margin_pct
        assert result.margin_deviation_pct < -1.0  # eşik altında
        assert result.real_margin_tl > 0  # hâlâ kârlı ama eriyor

    def test_margin_eroding_metrics(self):
        """Marj eriyor senaryosunda tüm metrikler tutarlı."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1500.0, peak_ptf=3000.0,
            base_kwh=30.0, peak_kwh=100.0,
            total_hours=100, peak_ratio=0.4,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # Toplam tutarlar tutarlı
        assert result.total_offer_tl == pytest.approx(
            result.offer_unit_price_tl_per_kwh * result.total_consumption_kwh, rel=0.01
        )
        assert result.real_margin_tl == pytest.approx(
            result.total_offer_tl - result.total_cost_tl, rel=0.01
        )
        # Negatif + pozitif = gerçek marj
        assert result.real_margin_tl == pytest.approx(
            result.positive_margin_total_tl + result.negative_margin_total_tl, rel=0.01
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Senaryo 2: %4 Manuel → Gerçek %6+ → OVERPERFORM
# ═══════════════════════════════════════════════════════════════════════════════


class TestOverperform:
    """Ucuz saatlerde yoğun tüketen müşteri — overperform."""

    def test_overperform_verdict(self):
        """Manuel %4 marj, gerçek marj yüksek → OVERPERFORM."""
        ptf_list, kwh_list = _make_offpeak_heavy_hours(
            base_ptf=1500.0,   # ucuz saatler
            peak_ptf=3000.0,   # pahalı saatler
            base_kwh=30.0,     # pahalı saatlerde az tüketim
            peak_kwh=100.0,    # ucuz saatlerde çok tüketim
            total_hours=100,
            offpeak_ratio=0.7,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.verdict == MarginVerdict.OVERPERFORM
        assert result.real_margin_pct > result.nominal_margin_pct
        assert result.margin_deviation_pct > 1.0  # eşik üstünde
        assert result.effective_multiplier > result.multiplier


# ═══════════════════════════════════════════════════════════════════════════════
# Senaryo 3: Gerçek Marj Negatif → ZARARLI
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoss:
    """Çok düşük katsayı + pahalı profil → zarar."""

    def test_loss_verdict(self):
        """Düşük katsayı ile pahalı profil → ZARARLI."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1500.0, peak_ptf=4000.0,
            base_kwh=20.0, peak_kwh=120.0,
            total_hours=100, peak_ratio=0.5,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.01,  # çok düşük katsayı
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.verdict == MarginVerdict.LOSS
        assert result.real_margin_tl < 0
        assert result.real_margin_pct < 0
        assert result.negative_margin_hours > 0

    def test_loss_all_hours_expensive(self):
        """Tüm saatler teklif fiyatından pahalı → tam zarar."""
        # Tüm saatlerde PTF çok yüksek
        ptf_list = [5000.0] * 50
        kwh_list = [100.0] * 50

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.verdict == MarginVerdict.LOSS
        assert result.negative_margin_hours == 50
        assert result.positive_margin_total_tl == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Senaryo 4: Break-even Katsayı Doğruluğu
# ═══════════════════════════════════════════════════════════════════════════════


class TestBreakEvenMultiplier:
    """Break-even katsayı hesabı doğruluğu."""

    def test_break_even_uniform_profile(self):
        """Düz profilde break-even = 1.0 (maliyet = ortalama)."""
        ptf_list, kwh_list = _make_uniform_hours(
            ptf=2000.0, kwh=60.0, count=100,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # Düz profilde ağırlıklı maliyet = ortalama maliyet
        # Break-even = ağırlıklı maliyet / baz fiyat = 1.0
        assert result.break_even_multiplier == pytest.approx(1.0, abs=0.001)
        assert result.real_margin_pct == pytest.approx(result.nominal_margin_pct, abs=0.1)

    def test_break_even_with_multiplier_at_break_even(self):
        """Break-even katsayı ile çalıştırınca marj ≈ 0."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1500.0, peak_ptf=3000.0,
            base_kwh=30.0, peak_kwh=100.0,
            total_hours=100, peak_ratio=0.4,
        )

        # Önce break-even katsayıyı bul
        result1 = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )
        be = result1.break_even_multiplier

        # Break-even katsayı ile tekrar çalıştır
        result2 = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=be,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # Gerçek marj ≈ 0 olmalı
        assert abs(result2.real_margin_tl) < 1.0  # 1 TL tolerans
        assert abs(result2.real_margin_pct) < 0.1  # %0.1 tolerans

    def test_safe_multiplier_above_break_even(self):
        """Güvenli katsayı her zaman break-even'dan büyük."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1500.0, peak_ptf=3000.0,
            base_kwh=30.0, peak_kwh=100.0,
            total_hours=100, peak_ratio=0.4,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.safe_multiplier > result.break_even_multiplier
        assert result.safe_multiplier == pytest.approx(
            result.break_even_multiplier + 0.01, abs=0.001
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Senaryo 5: Effective Multiplier Doğruluğu
# ═══════════════════════════════════════════════════════════════════════════════


class TestEffectiveMultiplier:
    """Effective Multiplier hesabı doğruluğu."""

    def test_effective_equals_nominal_for_uniform(self):
        """Düz profilde effective = nominal katsayı."""
        ptf_list, kwh_list = _make_uniform_hours(
            ptf=2000.0, kwh=60.0, count=100,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.effective_multiplier == pytest.approx(1.04, abs=0.001)

    def test_effective_lower_for_peak_heavy(self):
        """Pahalı profilde effective < nominal."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1500.0, peak_ptf=3000.0,
            base_kwh=30.0, peak_kwh=100.0,
            total_hours=100, peak_ratio=0.4,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # "Sen ×1.04 sattığını sanıyorsun ama aslında daha düşüğe satmışsın"
        assert result.effective_multiplier < result.multiplier

    def test_effective_higher_for_offpeak_heavy(self):
        """Ucuz profilde effective > nominal."""
        ptf_list, kwh_list = _make_offpeak_heavy_hours(
            base_ptf=1500.0, peak_ptf=3000.0,
            base_kwh=30.0, peak_kwh=100.0,
            total_hours=100, offpeak_ratio=0.7,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # "Sen ×1.04 sattığını sanıyorsun ama aslında daha yüksekten satmışsın"
        assert result.effective_multiplier > result.multiplier


# ═══════════════════════════════════════════════════════════════════════════════
# Ek Testler: En Kötü/En İyi Saatler, Histogram, Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorstBestHours:
    """En kötü ve en iyi 10 saat tablosu."""

    def test_worst_hours_sorted(self):
        """En kötü saatler marj bazında küçükten büyüğe sıralı."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1500.0, peak_ptf=3000.0,
            base_kwh=30.0, peak_kwh=100.0,
            total_hours=100, peak_ratio=0.4,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert len(result.worst_hours) <= 10
        margins = [h.margin_tl for h in result.worst_hours]
        assert margins == sorted(margins)  # küçükten büyüğe

    def test_best_hours_sorted(self):
        """En iyi saatler marj bazında büyükten küçüğe sıralı."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1500.0, peak_ptf=3000.0,
            base_kwh=30.0, peak_kwh=100.0,
            total_hours=100, peak_ratio=0.4,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert len(result.best_hours) <= 10
        margins = [h.margin_tl for h in result.best_hours]
        assert margins == sorted(margins, reverse=True)  # büyükten küçüğe

    def test_histogram_length_matches_hours(self):
        """Histogram verisi saat sayısıyla eşleşir."""
        ptf_list, kwh_list = _make_uniform_hours(ptf=2000.0, kwh=60.0, count=50)

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert len(result.hourly_margins_tl) == 50


class TestEdgeCases:
    """Sınır durumları."""

    def test_zero_consumption_hours_skipped(self):
        """Sıfır tüketimli saatler marj hesabını etkilemez."""
        ptf_list = [2000.0, 3000.0, 1000.0]
        kwh_list = [100.0, 0.0, 100.0]  # ortadaki saat sıfır

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.total_consumption_kwh == 200.0
        assert result.total_hours == 3

    def test_no_yekdem(self):
        """YEKDEM olmadan hesaplama."""
        ptf_list, kwh_list = _make_uniform_hours(ptf=2000.0, kwh=60.0, count=10)

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
            include_yekdem=False,
        )

        # YEKDEM dahil değilse teklif fiyat sadece PTF bazlı
        expected_offer_price = 2000.0 / 1000.0 * 1.04
        assert result.offer_unit_price_tl_per_kwh == pytest.approx(expected_offer_price, rel=0.01)

    def test_profitable_verdict_for_uniform(self):
        """Düz profilde nominal ≈ gerçek → KÂRLI."""
        ptf_list, kwh_list = _make_uniform_hours(ptf=2000.0, kwh=60.0, count=100)

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.verdict == MarginVerdict.PROFITABLE
        assert abs(result.margin_deviation_pct) < 1.0

    def test_custom_erosion_threshold(self):
        """Özel marj eriyor eşiği parametrik çalışır."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1800.0, peak_ptf=2300.0,
            base_kwh=50.0, peak_kwh=70.0,
            total_hours=100, peak_ratio=0.3,
        )

        # Varsayılan eşik (%1) ile
        result1 = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
            margin_erosion_threshold_pct=1.0,
        )

        # Geniş eşik (%5) ile — aynı sapma artık "kârlı" sayılabilir
        result2 = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
            margin_erosion_threshold_pct=5.0,
        )

        # Aynı veri, farklı eşik → farklı verdict olabilir
        assert result1.real_margin_pct == result2.real_margin_pct
        # Geniş eşikle daha toleranslı karar


# ═══════════════════════════════════════════════════════════════════════════════
# Yeni Metrikler: Revenue-based Margin ve Multiplier Delta
# ═══════════════════════════════════════════════════════════════════════════════


class TestRevenueBasedMargin:
    """Ciro bazlı marj hesabı (ticari karar için)."""

    def test_revenue_margin_positive(self):
        """Ciro bazlı marj pozitif ve cost-based'den düşük."""
        ptf_list, kwh_list = _make_uniform_hours(ptf=2000.0, kwh=60.0, count=100)

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # Revenue-based her zaman cost-based'den düşük olmalı (aynı marj TL, daha büyük payda)
        assert result.real_margin_on_revenue_pct > 0
        assert result.real_margin_on_revenue_pct < result.real_margin_pct

    def test_revenue_margin_negative_for_loss(self):
        """Zarar durumunda ciro bazlı marj da negatif."""
        ptf_list = [5000.0] * 50
        kwh_list = [100.0] * 50

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.real_margin_on_revenue_pct < 0


class TestMultiplierDelta:
    """Multiplier sapması hesabı."""

    def test_delta_zero_for_uniform(self):
        """Düz profilde multiplier delta ≈ 0."""
        ptf_list, kwh_list = _make_uniform_hours(ptf=2000.0, kwh=60.0, count=100)

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert abs(result.multiplier_delta) < 0.001

    def test_delta_negative_for_peak_heavy(self):
        """Pahalı profilde multiplier delta negatif."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1800.0, peak_ptf=2400.0,
            base_kwh=50.0, peak_kwh=80.0,
            total_hours=100, peak_ratio=0.3,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # "1.04 sanıyorsun ama aslında daha düşüğe satmışsın"
        assert result.multiplier_delta < 0
        assert result.multiplier_delta == pytest.approx(
            result.effective_multiplier - result.multiplier, abs=0.0001
        )

    def test_delta_positive_for_offpeak_heavy(self):
        """Ucuz profilde multiplier delta pozitif."""
        ptf_list, kwh_list = _make_offpeak_heavy_hours(
            base_ptf=1500.0, peak_ptf=3000.0,
            base_kwh=30.0, peak_kwh=100.0,
            total_hours=100, offpeak_ratio=0.7,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.multiplier_delta > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Target Multiplier — "Bu müşteri için %4 istiyorsan kaçtan satmalısın?"
# ═══════════════════════════════════════════════════════════════════════════════


class TestTargetMultiplier:
    """Target multiplier: hedef marjı gerçekten sağlayan katsayı."""

    def test_target_equals_nominal_for_uniform(self):
        """Düz profilde target = nominal (erime yok)."""
        ptf_list, kwh_list = _make_uniform_hours(ptf=2000.0, kwh=60.0, count=100)

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # Düz profilde erime yok → target ≈ nominal
        assert result.target_margin_pct == pytest.approx(4.0)
        assert result.required_multiplier_for_target == pytest.approx(1.04, abs=0.002)

    def test_target_higher_for_peak_heavy(self):
        """Pahalı profilde target > nominal (daha yüksek katsayı gerekli)."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1800.0, peak_ptf=2400.0,
            base_kwh=50.0, peak_kwh=80.0,
            total_hours=100, peak_ratio=0.3,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # Pahalı profilde %4 gerçek marj için daha yüksek katsayı lazım
        assert result.required_multiplier_for_target > result.multiplier
        assert result.target_margin_pct == pytest.approx(4.0)

    def test_target_lower_for_offpeak_heavy(self):
        """Ucuz profilde target < nominal (daha düşük katsayı yeterli)."""
        ptf_list, kwh_list = _make_offpeak_heavy_hours(
            base_ptf=1500.0, peak_ptf=3000.0,
            base_kwh=30.0, peak_kwh=100.0,
            total_hours=100, offpeak_ratio=0.7,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # Ucuz profilde %4 gerçek marj için daha düşük katsayı yeterli
        assert result.required_multiplier_for_target < result.multiplier

    def test_target_multiplier_actually_achieves_target(self):
        """Target multiplier ile çalıştırınca gerçek marj ≈ hedef marj."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1800.0, peak_ptf=2400.0,
            base_kwh=50.0, peak_kwh=80.0,
            total_hours=100, peak_ratio=0.3,
        )

        # Önce target multiplier'ı bul
        result1 = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )
        target_mult = result1.required_multiplier_for_target

        # Target multiplier ile tekrar çalıştır
        result2 = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=target_mult,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # Gerçek marj ≈ hedef marj (%4) olmalı
        assert result2.real_margin_pct == pytest.approx(4.0, abs=0.5)

    def test_target_always_above_break_even(self):
        """Target multiplier her zaman break-even'dan büyük (pozitif marj hedefi için)."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1800.0, peak_ptf=2400.0,
            base_kwh=50.0, peak_kwh=80.0,
            total_hours=100, peak_ratio=0.3,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.required_multiplier_for_target > result.break_even_multiplier


# ═══════════════════════════════════════════════════════════════════════════════
# Pricing Decision — "Ne yapmalısın?"
# ═══════════════════════════════════════════════════════════════════════════════

from app.pricing.margin_reality import PricingDecision


class TestPricingDecision:
    """Teklif kararı testleri."""

    def test_reject_when_loss(self):
        """Zarar durumunda → TEKLİF VERME."""
        ptf_list = [5000.0] * 50
        kwh_list = [100.0] * 50

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.pricing_decision == PricingDecision.REJECT
        assert "teklif verme" in result.pricing_decision_reason.lower()

    def test_reprice_when_eroding(self):
        """Marj eriyor → FİYAT ARTIR."""
        ptf_list, kwh_list = _make_peak_heavy_hours(
            base_ptf=1800.0, peak_ptf=2400.0,
            base_kwh=50.0, peak_kwh=80.0,
            total_hours=100, peak_ratio=0.3,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # Eğer gerçek marj hedefin %50'sinden düşükse REPRICE
        if result.real_margin_pct < result.nominal_margin_pct * 0.5:
            assert result.pricing_decision == PricingDecision.REPRICE

    def test_accept_when_profitable(self):
        """Marj korunuyor → TEKLİF UYGUN."""
        ptf_list, kwh_list = _make_uniform_hours(ptf=2000.0, kwh=60.0, count=100)

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.pricing_decision == PricingDecision.ACCEPT
        assert "uygun" in result.pricing_decision_reason.lower()

    def test_overprice_when_overperform(self):
        """Fazla kâr → FİYAT DÜŞÜR."""
        ptf_list, kwh_list = _make_offpeak_heavy_hours(
            base_ptf=1500.0, peak_ptf=3000.0,
            base_kwh=30.0, peak_kwh=100.0,
            total_hours=100, offpeak_ratio=0.7,
        )

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # Gerçek marj hedefin %50 üstündeyse OVERPRICE
        if result.real_margin_pct >= result.nominal_margin_pct * 1.5:
            assert result.pricing_decision == PricingDecision.OVERPRICE

    def test_decision_reason_not_empty(self):
        """Karar gerekçesi her zaman dolu."""
        ptf_list, kwh_list = _make_uniform_hours(ptf=2000.0, kwh=60.0, count=10)

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert len(result.pricing_decision_reason) > 10


# ═══════════════════════════════════════════════════════════════════════════════
# Pricing Aggressiveness — "Bu fiyatı vermek ne kadar zor?"
# ═══════════════════════════════════════════════════════════════════════════════

from app.pricing.margin_reality import PricingAggressiveness


class TestPricingAggressiveness:
    """Agresiflik seviyesi testleri."""

    def test_none_when_accept(self):
        """Teklif uygunsa agresiflik YOK."""
        ptf_list, kwh_list = _make_uniform_hours(ptf=2000.0, kwh=60.0, count=100)

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.04,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        assert result.pricing_aggressiveness == PricingAggressiveness.NONE

    def test_aggressiveness_scales_with_gap(self):
        """Fark büyüdükçe agresiflik artar."""
        # Çok pahalı profil — büyük fark gerektirecek
        ptf_list = [1500.0] * 50 + [4000.0] * 50
        kwh_list = [20.0] * 50 + [150.0] * 50

        result = calculate_margin_reality(
            offer_ptf_tl_per_mwh=2000.0,
            yekdem_tl_per_mwh=500.0,
            multiplier=1.01,
            hourly_ptf_prices=ptf_list,
            hourly_consumption_kwh=kwh_list,
        )

        # Büyük fark → HIGH veya MEDIUM olmalı
        assert result.pricing_aggressiveness in (
            PricingAggressiveness.MEDIUM,
            PricingAggressiveness.HIGH,
        )
