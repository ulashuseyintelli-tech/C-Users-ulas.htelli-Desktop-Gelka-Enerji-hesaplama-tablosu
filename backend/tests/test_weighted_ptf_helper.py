"""
SoT-X Seviye 1 — profil-ağırlıklı PTF helper testleri.

Saf core (_zone_weighted_avg_ptf, default_profile_for_tariff) + db entegrasyonu
(weighted_ptf_for_profile). Bkz. docs/sot-x-offer-ptf-parity.md
"""
import pytest
from app.market_prices import (
    _zone_weighted_avg_ptf,
    default_profile_for_tariff,
    weighted_ptf_for_profile,
    PROFILE_WEIGHTS,
)

# Kontrollü kayıtlar: saat 10 → T1 (Gündüz), 19 → T2 (Puant), 2 → T3 (Gece)
RECORDS = [(10, 1000.0), (19, 2000.0), (2, 500.0)]


class TestZoneWeightedAvg:
    def test_duz_equals_simple_average(self):
        # (1000+2000+500)/3 = 1166.67
        assert _zone_weighted_avg_ptf(RECORDS, "duz") == pytest.approx(1166.6667, abs=0.01)

    def test_puant_agir_above_average(self):
        # (1.5*1000 + 3*2000 + 0.5*500) / 5 = 1550
        assert _zone_weighted_avg_ptf(RECORDS, "puant_agir") == pytest.approx(1550.0, abs=0.01)

    def test_gece_agir_below_average(self):
        # (1*1000 + 0.5*2000 + 3*500) / 4.5 = 777.78
        assert _zone_weighted_avg_ptf(RECORDS, "gece_agir") == pytest.approx(777.7778, abs=0.01)

    def test_empty_returns_none(self):
        assert _zone_weighted_avg_ptf([], "puant_agir") is None

    def test_unknown_profile_falls_back_to_puant(self):
        # Bilinmeyen profil → puant_agir katsayıları
        assert _zone_weighted_avg_ptf(RECORDS, "olmayan") == _zone_weighted_avg_ptf(RECORDS, "puant_agir")

    def test_ordering_invariant(self):
        # Sıra önemli değil (toplam ağırlıklı ortalama)
        assert _zone_weighted_avg_ptf(RECORDS, "puant_agir") == pytest.approx(
            _zone_weighted_avg_ptf(list(reversed(RECORDS)), "puant_agir"), abs=1e-9
        )


class TestDefaultProfileForTariff:
    @pytest.mark.parametrize("tariff,expected", [
        ("Sanayi OG", "puant_agir"),
        ("Ticarethane", "puant_agir"),
        ("Mesken", "duz"),
        ("mesken alçak gerilim", "duz"),
        ("", "puant_agir"),
        (None, "puant_agir"),
        ("bilinmeyen", "puant_agir"),
    ])
    def test_mapping(self, tariff, expected):
        assert default_profile_for_tariff(tariff) == expected


class TestWeightedPtfForProfileDb:
    def _session(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database import Base
        import app.pricing.schemas  # noqa: F401 — HourlyMarketPrice'ı Base.metadata'a kaydet
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine)()

    def _add(self, db, period, hour, ptf, is_active=1):
        from app.pricing.schemas import HourlyMarketPrice
        db.add(HourlyMarketPrice(period=period, date=f"{period}-01", hour=hour,
                                 ptf_tl_per_mwh=ptf, smf_tl_per_mwh=ptf, is_active=is_active))

    def test_reads_active_rows_and_weights(self):
        db = self._session()
        for h, p in RECORDS:
            self._add(db, "2099-01", h, p, is_active=1)
        self._add(db, "2099-01", 12, 99999.0, is_active=0)  # arşiv → görmezden gel
        db.commit()
        res = weighted_ptf_for_profile(db, "2099-01", "duz")
        assert res.ptf_tl_per_mwh == pytest.approx(1166.6667, abs=0.01)
        assert res.hours == 3
        assert res.profile == "duz"

    def test_no_hourly_returns_none(self):
        db = self._session()
        res = weighted_ptf_for_profile(db, "2099-12", "puant_agir")
        assert res.ptf_tl_per_mwh is None
        assert res.hours == 0
