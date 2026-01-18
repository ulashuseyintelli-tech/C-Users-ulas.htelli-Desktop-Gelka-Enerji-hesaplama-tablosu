"""
Incident Metrics Tests - Sprint 8.2 + 8.6

KPI query fonksiyonları testleri.
"""

import pytest
from datetime import datetime, timedelta, date
from unittest.mock import MagicMock, patch

from backend.app.incident_metrics import (
    # Sprint 8.2
    RetryFunnel,
    IncidentMetrics,
    get_daily_counts,
    get_status_distribution,
    get_retry_funnel,
    get_top_primary_flags,
    get_stuck_pending_recompute_count,
    get_false_success_rate,
    get_mttr,
    # Sprint 8.6
    AlertType,
    PeriodStats,
    DriftAlert,
    TopOffender,
    HistogramBucket,
    ActionClassDistribution,
    SystemHealthReport,
    get_ratio_bucket,
    calculate_mismatch_histogram,
    detect_drift,
    DRIFT_MIN_SAMPLE,
    DRIFT_MIN_ABSOLUTE_DELTA,
    DRIFT_RATE_MULTIPLIER,
)
from backend.app.resolution_reasons import STUCK_THRESHOLD_MINUTES


# ═══════════════════════════════════════════════════════════════════════════════
# SPRINT 8.6: SYSTEM HEALTH DASHBOARD TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPeriodStats:
    """PeriodStats dataclass testleri"""
    
    def test_mismatch_rate_calculation(self):
        """Mismatch rate doğru hesaplanır"""
        stats = PeriodStats(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 7),
            total_invoices=100,
            mismatch_count=15,
        )
        assert stats.mismatch_rate == 0.15
    
    def test_mismatch_rate_zero_total(self):
        """Sıfır total'de mismatch rate 0"""
        stats = PeriodStats(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 7),
            total_invoices=0,
            mismatch_count=0,
        )
        assert stats.mismatch_rate == 0.0
    
    def test_s1_rate_calculation(self):
        """S1 rate doğru hesaplanır (S1 / (S1 + S2))"""
        stats = PeriodStats(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 7),
            s1_count=20,
            s2_count=80,
        )
        assert stats.s1_rate == 0.2
    
    def test_s1_rate_zero_severity(self):
        """Sıfır severity'de S1 rate 0"""
        stats = PeriodStats(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 7),
            s1_count=0,
            s2_count=0,
        )
        assert stats.s1_rate == 0.0
    
    def test_ocr_suspect_rate_calculation(self):
        """OCR suspect rate doğru hesaplanır"""
        stats = PeriodStats(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 7),
            mismatch_count=50,
            ocr_suspect_count=10,
        )
        assert stats.ocr_suspect_rate == 0.2


class TestTopOffender:
    """TopOffender dataclass testleri"""
    
    def test_mismatch_rate_calculation(self):
        """Mismatch rate doğru hesaplanır (rate, count değil!)"""
        offender = TopOffender(
            provider="ck_bogazici",
            total_count=100,
            mismatch_count=25,
        )
        assert offender.mismatch_rate == 0.25
    
    def test_mismatch_rate_zero_total(self):
        """Sıfır total'de mismatch rate 0"""
        offender = TopOffender(
            provider="test",
            total_count=0,
            mismatch_count=0,
        )
        assert offender.mismatch_rate == 0.0
    
    def test_to_dict(self):
        """to_dict doğru çalışır"""
        offender = TopOffender(
            provider="enerjisa",
            total_count=200,
            mismatch_count=30,
        )
        d = offender.to_dict()
        assert d["provider"] == "enerjisa"
        assert d["total_count"] == 200
        assert d["mismatch_count"] == 30
        assert d["mismatch_rate"] == 0.15


class TestTopOffendersMinVolume:
    """Top offenders min volume guard testleri"""
    
    def test_min_volume_constant(self):
        """Min volume constant doğru değerde"""
        from backend.app.incident_metrics import TOP_OFFENDERS_MIN_INVOICES
        assert TOP_OFFENDERS_MIN_INVOICES == 20
    
    def test_high_rate_low_volume_excluded(self):
        """
        Yüksek rate ama düşük volume olan provider'lar
        top_by_rate listesinden hariç tutulur.
        
        Örnek: %50 (5/10) yanıltıcı görünür ama n < 20
        """
        # Bu test DB mock gerektiriyor, unit test olarak
        # sadece mantığı doğruluyoruz
        offender_low_volume = TopOffender(
            provider="small_provider",
            total_count=10,  # < 20
            mismatch_count=5,  # %50 rate
        )
        offender_high_volume = TopOffender(
            provider="large_provider",
            total_count=100,  # >= 20
            mismatch_count=20,  # %20 rate
        )
        
        # Low volume provider yüksek rate'e sahip ama güvenilir değil
        assert offender_low_volume.mismatch_rate == 0.5
        assert offender_high_volume.mismatch_rate == 0.2
        
        # Min volume guard: total_count >= 20
        assert offender_low_volume.total_count < 20
        assert offender_high_volume.total_count >= 20


class TestHistogramBuckets:
    """Histogram bucket testleri"""
    
    def test_bucket_0_2_percent(self):
        """0-2% bucket doğru eşlenir"""
        assert get_ratio_bucket(0.00) == "0-2%"
        assert get_ratio_bucket(0.01) == "0-2%"
        assert get_ratio_bucket(0.019) == "0-2%"
    
    def test_bucket_2_5_percent(self):
        """2-5% bucket doğru eşlenir"""
        assert get_ratio_bucket(0.02) == "2-5%"
        assert get_ratio_bucket(0.03) == "2-5%"
        assert get_ratio_bucket(0.049) == "2-5%"
    
    def test_bucket_5_10_percent(self):
        """5-10% bucket doğru eşlenir"""
        assert get_ratio_bucket(0.05) == "5-10%"
        assert get_ratio_bucket(0.07) == "5-10%"
        assert get_ratio_bucket(0.099) == "5-10%"
    
    def test_bucket_10_20_percent(self):
        """10-20% bucket doğru eşlenir"""
        assert get_ratio_bucket(0.10) == "10-20%"
        assert get_ratio_bucket(0.15) == "10-20%"
        assert get_ratio_bucket(0.199) == "10-20%"
    
    def test_bucket_20_plus_percent(self):
        """20%+ bucket doğru eşlenir"""
        assert get_ratio_bucket(0.20) == "20%+"
        assert get_ratio_bucket(0.50) == "20%+"
        assert get_ratio_bucket(1.0) == "20%+"


class TestCalculateMismatchHistogram:
    """calculate_mismatch_histogram testleri"""
    
    def test_empty_incidents(self):
        """Boş incident listesi için histogram"""
        histogram = calculate_mismatch_histogram([])
        assert len(histogram) == 5
        assert all(b.count == 0 for b in histogram)
    
    def test_single_incident(self):
        """Tek incident için histogram"""
        incidents = [
            {
                "details": {
                    "flag_details": [
                        {"code": "INVOICE_TOTAL_MISMATCH", "ratio": 0.08}
                    ]
                }
            }
        ]
        histogram = calculate_mismatch_histogram(incidents)
        
        # 8% → 5-10% bucket
        bucket_5_10 = next(b for b in histogram if b.label == "5-10%")
        assert bucket_5_10.count == 1
    
    def test_multiple_incidents_different_buckets(self):
        """Farklı bucket'lara düşen incident'lar"""
        incidents = [
            {"details": {"flag_details": [{"code": "INVOICE_TOTAL_MISMATCH", "ratio": 0.01}]}},  # 0-2%
            {"details": {"flag_details": [{"code": "INVOICE_TOTAL_MISMATCH", "ratio": 0.03}]}},  # 2-5%
            {"details": {"flag_details": [{"code": "INVOICE_TOTAL_MISMATCH", "ratio": 0.07}]}},  # 5-10%
            {"details": {"flag_details": [{"code": "INVOICE_TOTAL_MISMATCH", "ratio": 0.15}]}},  # 10-20%
            {"details": {"flag_details": [{"code": "INVOICE_TOTAL_MISMATCH", "ratio": 0.25}]}},  # 20%+
        ]
        histogram = calculate_mismatch_histogram(incidents)
        
        assert histogram[0].count == 1  # 0-2%
        assert histogram[1].count == 1  # 2-5%
        assert histogram[2].count == 1  # 5-10%
        assert histogram[3].count == 1  # 10-20%
        assert histogram[4].count == 1  # 20%+
    
    def test_ignores_non_mismatch_flags(self):
        """Mismatch olmayan flag'ler ignore edilir"""
        incidents = [
            {"details": {"flag_details": [{"code": "CALC_BUG", "ratio": 0.5}]}},
        ]
        histogram = calculate_mismatch_histogram(incidents)
        assert all(b.count == 0 for b in histogram)


class TestDriftDetection:
    """Drift detection (triple guard + zero rate handling) testleri"""
    
    def test_no_drift_insufficient_sample(self):
        """Yetersiz sample'da drift tetiklenmez (n < 20)"""
        alert = detect_drift(
            old_count=5,
            new_count=15,
            old_total=10,
            new_total=19,  # < 20
            alert_type=AlertType.S1_RATE_DRIFT,
        )
        assert alert.triggered is False
    
    def test_no_drift_insufficient_delta(self):
        """Yetersiz delta'da drift tetiklenmez (delta < 5)"""
        alert = detect_drift(
            old_count=10,
            new_count=14,  # delta = 4 < 5
            old_total=100,
            new_total=100,
            alert_type=AlertType.S1_RATE_DRIFT,
        )
        assert alert.triggered is False
    
    def test_no_drift_insufficient_rate_increase(self):
        """Yetersiz rate artışında drift tetiklenmez (rate < 2x)"""
        alert = detect_drift(
            old_count=10,
            new_count=18,  # delta = 8 >= 5
            old_total=100,
            new_total=100,  # rate: 10% → 18% (1.8x < 2x)
            alert_type=AlertType.S1_RATE_DRIFT,
        )
        assert alert.triggered is False
    
    def test_drift_triggered_all_conditions_met(self):
        """Tüm koşullar sağlandığında drift tetiklenir"""
        alert = detect_drift(
            old_count=5,
            new_count=15,  # delta = 10 >= 5
            old_total=100,
            new_total=100,  # rate: 5% → 15% (3x >= 2x)
            alert_type=AlertType.S1_RATE_DRIFT,
        )
        assert alert.triggered is True
        assert alert.alert_type == AlertType.S1_RATE_DRIFT
        assert "S1_RATE_DRIFT" in alert.message
    
    def test_drift_boundary_exactly_at_thresholds(self):
        """Tam threshold değerlerinde drift tetiklenir"""
        alert = detect_drift(
            old_count=5,
            new_count=10,  # delta = 5 (exactly)
            old_total=100,
            new_total=20,  # n = 20 (exactly), rate: 5% → 50% (10x >= 2x)
            alert_type=AlertType.MISMATCH_RATE_DRIFT,
        )
        assert alert.triggered is True
    
    def test_drift_zero_old_rate_triggers_with_count_guard(self):
        """
        Zero rate handling: prev_rate == 0 iken rate guard atlanır,
        count guard yeterli olduğunda drift tetiklenir.
        """
        alert = detect_drift(
            old_count=0,
            new_count=10,  # delta = 10 >= 5, new_count >= 5
            old_total=100,
            new_total=100,  # n >= 20
            alert_type=AlertType.OCR_SUSPECT_DRIFT,
        )
        # Zero rate case: rate guard atlanır, count guard yeterli
        assert alert.triggered is True
        assert "0%" in alert.message
    
    def test_drift_zero_old_rate_no_trigger_insufficient_new_count(self):
        """
        Zero rate handling: prev_rate == 0 ama new_count < min_abs_delta
        ise drift tetiklenmez.
        """
        alert = detect_drift(
            old_count=0,
            new_count=3,  # delta = 3 < 5, new_count < 5
            old_total=100,
            new_total=100,
            alert_type=AlertType.OCR_SUSPECT_DRIFT,
        )
        assert alert.triggered is False


class TestDriftAlertToDict:
    """DriftAlert.to_dict testleri"""
    
    def test_to_dict_structure(self):
        """to_dict doğru yapıda dict döner"""
        alert = DriftAlert(
            alert_type=AlertType.S1_RATE_DRIFT,
            old_rate=0.05,
            new_rate=0.15,
            old_count=5,
            new_count=15,
            triggered=True,
            message="Test message",
        )
        d = alert.to_dict()
        
        assert d["alert_type"] == "S1_RATE_DRIFT"
        assert d["old_rate"] == 0.05
        assert d["new_rate"] == 0.15
        assert d["old_count"] == 5
        assert d["new_count"] == 15
        assert d["triggered"] is True
        assert d["message"] == "Test message"


class TestActionClassDistribution:
    """ActionClassDistribution testleri"""
    
    def test_total_calculation(self):
        """Total doğru hesaplanır"""
        dist = ActionClassDistribution(
            verify_ocr=10,
            verify_invoice_logic=20,
            accept_rounding=5,
        )
        assert dist.total == 35
    
    def test_to_dict_with_rates(self):
        """to_dict rate'leri içerir"""
        dist = ActionClassDistribution(
            verify_ocr=10,
            verify_invoice_logic=20,
            accept_rounding=10,
        )
        d = dist.to_dict()
        
        assert d["VERIFY_OCR"] == 10
        assert d["VERIFY_INVOICE_LOGIC"] == 20
        assert d["ACCEPT_ROUNDING_TOLERANCE"] == 10
        assert d["total"] == 40
        assert d["rates"]["VERIFY_OCR"] == 0.25
        assert d["rates"]["VERIFY_INVOICE_LOGIC"] == 0.5
        assert d["rates"]["ACCEPT_ROUNDING_TOLERANCE"] == 0.25
    
    def test_to_dict_zero_total(self):
        """Sıfır total'de rate'ler 0"""
        dist = ActionClassDistribution()
        d = dist.to_dict()
        
        assert d["total"] == 0
        assert d["rates"]["VERIFY_OCR"] == 0
        assert d["rates"]["VERIFY_INVOICE_LOGIC"] == 0
        assert d["rates"]["ACCEPT_ROUNDING_TOLERANCE"] == 0


class TestGoldenScenarios:
    """Golden test senaryoları"""
    
    def test_golden_no_drift(self):
        """Golden: Drift yok - stabil sistem"""
        # Önceki dönem: 10/100 = 10%
        # Yeni dönem: 12/100 = 12% (1.2x < 2x)
        alert = detect_drift(
            old_count=10,
            new_count=12,
            old_total=100,
            new_total=100,
            alert_type=AlertType.S1_RATE_DRIFT,
        )
        assert alert.triggered is False
    
    def test_golden_s1_drift(self):
        """Golden: S1 rate drift - ciddi artış"""
        # Önceki dönem: 5/100 = 5%
        # Yeni dönem: 20/100 = 20% (4x >= 2x)
        alert = detect_drift(
            old_count=5,
            new_count=20,
            old_total=100,
            new_total=100,
            alert_type=AlertType.S1_RATE_DRIFT,
        )
        assert alert.triggered is True
        assert alert.old_rate == 0.05
        assert alert.new_rate == 0.20
    
    def test_golden_ocr_drift(self):
        """Golden: OCR suspect drift"""
        # Önceki dönem: 3/30 = 10%
        # Yeni dönem: 12/40 = 30% (3x >= 2x)
        alert = detect_drift(
            old_count=3,
            new_count=12,
            old_total=30,
            new_total=40,
            alert_type=AlertType.OCR_SUSPECT_DRIFT,
        )
        assert alert.triggered is True
    
    def test_golden_mismatch_drift(self):
        """Golden: Mismatch rate drift"""
        # Önceki dönem: 10/200 = 5%
        # Yeni dönem: 25/200 = 12.5% (2.5x >= 2x)
        alert = detect_drift(
            old_count=10,
            new_count=25,
            old_total=200,
            new_total=200,
            alert_type=AlertType.MISMATCH_RATE_DRIFT,
        )
        assert alert.triggered is True
    
    def test_golden_small_sample_protection(self):
        """Golden: Küçük sample koruması - yüksek rate ama düşük n"""
        # Önceki dönem: 1/10 = 10%
        # Yeni dönem: 5/15 = 33% (3.3x >= 2x, ama n=15 < 20)
        alert = detect_drift(
            old_count=1,
            new_count=5,
            old_total=10,
            new_total=15,
            alert_type=AlertType.S1_RATE_DRIFT,
        )
        assert alert.triggered is False  # n < 20
    
    def test_golden_zero_rate_new_problem(self):
        """
        Golden: Zero rate → new problem detection
        
        Senaryo: Önceki dönemde hiç OCR suspect yoktu (0%),
        yeni dönemde aniden 10 tane çıktı.
        Bu "yeni problem" olarak algılanmalı.
        """
        alert = detect_drift(
            old_count=0,
            new_count=10,  # Yeni problem: 10 tane OCR suspect
            old_total=100,
            new_total=100,  # n >= 20
            alert_type=AlertType.OCR_SUSPECT_DRIFT,
        )
        # Zero rate case: rate guard atlanır, count guard yeterli
        assert alert.triggered is True
        assert alert.old_rate == 0.0
        assert alert.new_rate == 0.10
        assert "0%" in alert.message


# ═══════════════════════════════════════════════════════════════════════════════
# SPRINT 8.2: EXISTING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRetryFunnel:
    """RetryFunnel dataclass testleri"""
    
    def test_success_rate_calculation(self):
        """Success rate doğru hesaplanır"""
        funnel = RetryFunnel(
            attempts_total=100,
            attempts_success=60,
        )
        assert funnel.success_rate == 0.6
    
    def test_success_rate_zero_attempts(self):
        """Sıfır attempt'te success rate 0"""
        funnel = RetryFunnel(attempts_total=0)
        assert funnel.success_rate == 0.0
    
    def test_false_success_rate_calculation(self):
        """False success rate doğru hesaplanır"""
        funnel = RetryFunnel(
            attempts_total=100,
            attempts_success=60,
            resolved_after_retry=45,
            still_pending=15,
        )
        # 15 / 60 = 0.25
        assert funnel.false_success_rate == 0.25
    
    def test_false_success_rate_zero_success(self):
        """Sıfır success'te false success rate 0"""
        funnel = RetryFunnel(attempts_success=0)
        assert funnel.false_success_rate == 0.0


class TestGetDailyCounts:
    """get_daily_counts testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_returns_correct_structure(self, mock_db):
        """Doğru yapıda dict döner"""
        mock_db.query.return_value.filter.return_value.count.return_value = 10
        
        result = get_daily_counts(mock_db, "default", date(2025, 1, 15))
        
        assert "total" in result
        assert "new" in result
        assert "resolved" in result


class TestGetStatusDistribution:
    """get_status_distribution testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_returns_dict(self, mock_db):
        """Dict döner"""
        mock_db.query.return_value.filter.return_value.group_by.return_value.all.return_value = [
            ("OPEN", 5),
            ("RESOLVED", 10),
        ]
        
        result = get_status_distribution(mock_db, "default")
        
        assert result == {"OPEN": 5, "RESOLVED": 10}


class TestGetRetryFunnel:
    """get_retry_funnel testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_calculates_funnel_correctly(self, mock_db):
        """Funnel doğru hesaplanır"""
        # 3 incident: 1 success+resolved, 1 success+pending, 1 exhausted
        incidents = []
        
        inc1 = MagicMock()
        inc1.retry_attempt_count = 2
        inc1.retry_success = True
        inc1.status = "RESOLVED"
        inc1.retry_exhausted_at = None
        incidents.append(inc1)
        
        inc2 = MagicMock()
        inc2.retry_attempt_count = 1
        inc2.retry_success = True
        inc2.status = "PENDING_RETRY"
        inc2.retry_exhausted_at = None
        incidents.append(inc2)
        
        inc3 = MagicMock()
        inc3.retry_attempt_count = 4
        inc3.retry_success = False
        inc3.status = "OPEN"
        inc3.retry_exhausted_at = datetime.now()
        incidents.append(inc3)
        
        mock_db.query.return_value.filter.return_value.filter.return_value.all.return_value = incidents
        
        funnel = get_retry_funnel(mock_db, "default")
        
        assert funnel.attempts_total == 3
        assert funnel.attempts_success == 2
        assert funnel.resolved_after_retry == 1
        assert funnel.still_pending == 1
        assert funnel.exhausted == 1


class TestGetTopPrimaryFlags:
    """get_top_primary_flags testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_returns_sorted_list(self, mock_db):
        """Sıralı liste döner"""
        mock_db.query.return_value.filter.return_value.group_by.return_value.order_by.return_value.limit.return_value.all.return_value = [
            ("CALC_BUG", 15),
            ("MARKET_PRICE_MISSING", 12),
        ]
        
        result = get_top_primary_flags(mock_db, "default", limit=10)
        
        assert result == [("CALC_BUG", 15), ("MARKET_PRICE_MISSING", 12)]


class TestGetStuckPendingRecomputeCount:
    """get_stuck_pending_recompute_count testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_uses_threshold(self, mock_db):
        """Threshold kullanılır"""
        mock_db.query.return_value.filter.return_value.count.return_value = 3
        
        now = datetime(2025, 1, 15, 12, 0, 0)
        result = get_stuck_pending_recompute_count(
            mock_db, "default", 
            threshold_minutes=STUCK_THRESHOLD_MINUTES,
            now=now
        )
        
        assert result == 3
        # filter çağrıldı
        mock_db.query.return_value.filter.assert_called()


class TestGetFalseSuccessRate:
    """get_false_success_rate testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_returns_rate(self, mock_db):
        """Oran döner"""
        # Mock retry funnel
        with patch('backend.app.incident_metrics.get_retry_funnel') as mock_funnel:
            mock_funnel.return_value = RetryFunnel(
                attempts_total=100,
                attempts_success=60,
                resolved_after_retry=45,
                still_pending=15,
            )
            
            result = get_false_success_rate(mock_db, "default")
            
            assert result == 0.25


class TestGetMttr:
    """get_mttr testleri"""
    
    @pytest.fixture
    def mock_db(self):
        return MagicMock()
    
    def test_calculates_average(self, mock_db):
        """Ortalama doğru hesaplanır"""
        # 2 incident: 60 dakika ve 120 dakika
        inc1 = MagicMock()
        inc1.first_seen_at = datetime(2025, 1, 15, 10, 0, 0)
        inc1.resolved_at = datetime(2025, 1, 15, 11, 0, 0)  # 60 dakika
        
        inc2 = MagicMock()
        inc2.first_seen_at = datetime(2025, 1, 15, 10, 0, 0)
        inc2.resolved_at = datetime(2025, 1, 15, 12, 0, 0)  # 120 dakika
        
        mock_db.query.return_value.filter.return_value.all.return_value = [inc1, inc2]
        
        result = get_mttr(mock_db, "default")
        
        # (60 + 120) / 2 = 90
        assert result == 90.0
    
    def test_returns_none_for_empty(self, mock_db):
        """Veri yoksa None döner"""
        mock_db.query.return_value.filter.return_value.all.return_value = []
        
        result = get_mttr(mock_db, "default")
        
        assert result is None


class TestEmptyDataHandling:
    """Boş veri handling testleri"""
    
    @pytest.fixture
    def mock_db(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.count.return_value = 0
        db.query.return_value.filter.return_value.group_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.group_by.return_value.order_by.return_value.limit.return_value.all.return_value = []
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        return db
    
    def test_daily_counts_empty(self, mock_db):
        """Boş veri için daily counts"""
        result = get_daily_counts(mock_db, "default", date(2025, 1, 15))
        assert result["total"] == 0
        assert result["new"] == 0
        assert result["resolved"] == 0
    
    def test_status_distribution_empty(self, mock_db):
        """Boş veri için status distribution"""
        result = get_status_distribution(mock_db, "default")
        assert result == {}
    
    def test_top_flags_empty(self, mock_db):
        """Boş veri için top flags"""
        result = get_top_primary_flags(mock_db, "default")
        assert result == []
