"""
Seviye 2 — Offer (profil-ağırlıklı) vs Recon/Pricing (gerçek tüketim-ağırlıklı)
PTF parite/sapma ölçümü.

İki ağırlıklandırma:
- GERÇEK (recon/pricing): Σ(kWh_h × PTF_h) / Σ(kWh_h)  — pricing_engine.calculate_weighted_prices
- OFFER PROXY:          Σ(w_zone × PTF_h) / Σ(w_zone) — market_prices._zone_weighted_avg_ptf
  (w_zone = profil sabit katsayısı; gerçek tüketim KULLANMAZ)

Sapma% = (proxy / gerçek − 1) × 100. Büyükse offer maliyet-altı/üstü fiyatlıyor
demektir → Seviye 2 (b): offer gerçek tüketim profili varken onu kullanmalı.

TEST-ONLY — production kod değişmez. Sadece iki mevcut fonksiyonu karşılaştırır.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pricing.pricing_engine import calculate_weighted_prices
from app.pricing.excel_parser import ParsedMarketRecord, ParsedConsumptionRecord
from app.market_prices import _zone_weighted_avg_ptf, default_profile_for_tariff

_FIXTURE = Path(__file__).parent / "fixtures" / "cansu_parity_2026_01.json"


def _true_consumption_weighted_ptf(records: list[dict]) -> float:
    """Gerçek tüketim-ağırlıklı PTF — production pricing fonksiyonu ile."""
    mkt = [ParsedMarketRecord(period="X", date=r["date"], hour=r["hour"],
                              ptf_tl_per_mwh=r["ptf"], smf_tl_per_mwh=r["ptf"]) for r in records]
    cons = [ParsedConsumptionRecord(date=r["date"], hour=r["hour"],
                                    consumption_kwh=r["kwh"]) for r in records]
    return calculate_weighted_prices(mkt, cons).weighted_ptf_tl_per_mwh


def _offer_profile_weighted_ptf(records: list[dict], profile: str) -> float:
    """Offer proxy — production market_prices fonksiyonu ile."""
    return _zone_weighted_avg_ptf([(r["hour"], r["ptf"]) for r in records], profile)


def _deviation_pct(proxy: float, true_val: float) -> float:
    return (proxy / true_val - 1.0) * 100.0


# ═══════════════════════════════════════════════════════════════════════════════
# Sentetik — deterministik, elle hesaplanabilir
# ═══════════════════════════════════════════════════════════════════════════════

class TestSyntheticParity:
    # Saat sınıfları: 10→T1(Gündüz), 19→T2(Puant), 2→T3(Gece)
    def test_peak_concentrated_consumption_offer_proxy_underestimates(self):
        """Tüketim %100 puant saatte → gerçek = puant PTF; profil proxy düşük kalır."""
        recs = [
            {"date": "2099-01-01", "hour": 10, "ptf": 1000.0, "kwh": 0.0},   # T1
            {"date": "2099-01-01", "hour": 19, "ptf": 3000.0, "kwh": 100.0},  # T2 (tüm tüketim)
            {"date": "2099-01-01", "hour": 2,  "ptf": 500.0,  "kwh": 0.0},   # T3
        ]
        true_val = _true_consumption_weighted_ptf(recs)
        proxy = _offer_profile_weighted_ptf(recs, "puant_agir")
        # gerçek: tüm tüketim 19'da → 3000
        assert true_val == pytest.approx(3000.0, abs=0.01)
        # proxy: (1.5*1000 + 3*3000 + 0.5*500) / 5 = 2150
        assert proxy == pytest.approx(2150.0, abs=0.01)
        # proxy puant-yoğun gerçeği ~%28 ALTINDA tahmin ediyor
        assert _deviation_pct(proxy, true_val) == pytest.approx(-28.333, abs=0.01)

    def test_flat_consumption_duz_profile_parity(self):
        """Düz tüketim + düz profil → proxy ≈ gerçek (sapma ~0)."""
        recs = [
            {"date": "2099-01-01", "hour": 10, "ptf": 1000.0, "kwh": 50.0},
            {"date": "2099-01-01", "hour": 19, "ptf": 3000.0, "kwh": 50.0},
            {"date": "2099-01-01", "hour": 2,  "ptf": 500.0,  "kwh": 50.0},
        ]
        true_val = _true_consumption_weighted_ptf(recs)   # (1000+3000+500)/3 = 1500
        proxy = _offer_profile_weighted_ptf(recs, "duz")  # düz {1,1,1} → 1500
        assert true_val == pytest.approx(1500.0, abs=0.01)
        assert proxy == pytest.approx(1500.0, abs=0.01)
        assert _deviation_pct(proxy, true_val) == pytest.approx(0.0, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# Gerçek Cansu — ölçüm/rapor (fixture: canlı DB'den, 2026-01, 744 saat)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCansuRealParity:
    @pytest.fixture(scope="class")
    def records(self):
        data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        assert data["hours"] == len(data["records"]) > 0
        return data["records"]

    def test_report_deviation_all_profiles(self, records, capsys):
        true_val = _true_consumption_weighted_ptf(records)
        # NOT: print ASCII-safe — Windows konsolu (cp1254) -> / em-dash yerine ASCII
        lines = [
            "",
            "=== CANSU 2026-01 - Offer proxy vs Gercek tuketim-agirlikli PTF ===",
            f"GERCEK (tuketim-agirlikli): {true_val:,.2f} TL/MWh",
        ]
        devs = {}
        for profile in ("duz", "puant_agir", "gece_agir"):
            proxy = _offer_profile_weighted_ptf(records, profile)
            dev = _deviation_pct(proxy, true_val)
            devs[profile] = dev
            lines.append(f"  proxy[{profile:<10}] = {proxy:,.2f}  -> sapma {dev:+.2f}%")
        auto = default_profile_for_tariff(None)  # Cansu tarife bilinmiyor -> puant_agir
        lines.append(f"Offer otomatik profil (tarife bilinmiyor): {auto} -> sapma {devs[auto]:+.2f}%")
        with capsys.disabled():
            print("\n".join(lines))

        # Sağlamlık: değerler makul aralıkta (motor doğru çalışıyor)
        assert true_val > 0
        # Offer otomatik profil sapması raporlanır; |sapma| < %20 bekleniyor
        # (büyürse Seviye 2 (b) tetiklenir — bu sınır kasıtlı geniş, ölçüm amaçlı)
        assert abs(devs[auto]) < 20.0, f"Offer proxy sapması beklenenden büyük: {devs[auto]:+.2f}%"
