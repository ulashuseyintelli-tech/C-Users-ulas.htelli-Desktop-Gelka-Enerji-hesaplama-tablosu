"""
Sprint 4 P1 - Primary Cause Selection Tests

Test senaryoları:
1. select_primary_flag() doğru öncelik sıralaması
2. Tek incident stratejisi - birden fazla flag → tek incident
3. CK kategori kuralları (TARIFF_META_MISSING vs TARIFF_LOOKUP_FAILED)
4. Secondary flags doğru kaydediliyor
5. Deterministik ordering (sorted + unique)
6. CALC_BUG 3 koşul kontrolü
7. Action recommendations
"""

import pytest
from unittest.mock import MagicMock, patch
from backend.app.incident_service import (
    select_primary_flag,
    get_secondary_flags,
    flag_to_category,
    normalize_flags,
    check_calc_bug_conditions,
    get_action_recommendation,
    create_incidents_from_quality,
    calculate_quality_score,
    QualityScore,
    Severity,
    Category,
    FLAG_PRIORITY,
    QUALITY_FLAGS,
    ACTION_MAP,
    IncidentAction,
    IncidentOwner,
    HintCode,
    check_production_guard,
    validate_environment,
)


class TestSelectPrimaryFlag:
    """select_primary_flag() fonksiyonu testleri"""
    
    def test_empty_list_returns_none(self):
        """Boş liste → None"""
        assert select_primary_flag([]) is None
    
    def test_single_flag_returns_itself(self):
        """Tek flag → kendisi"""
        assert select_primary_flag(["MARKET_PRICE_MISSING"]) == "MARKET_PRICE_MISSING"
    
    def test_s1_beats_s2(self):
        """S1 flag, S2'den önce gelir"""
        flags = ["MISSING_FIELDS", "MARKET_PRICE_MISSING"]  # S2, S1
        assert select_primary_flag(flags) == "MARKET_PRICE_MISSING"
    
    def test_calc_bug_highest_priority(self):
        """CALC_BUG en yüksek öncelik"""
        flags = ["MARKET_PRICE_MISSING", "CALC_BUG", "TARIFF_LOOKUP_FAILED"]
        assert select_primary_flag(flags) == "CALC_BUG"
    
    def test_market_price_beats_tariff(self):
        """MARKET_PRICE_MISSING > TARIFF_LOOKUP_FAILED"""
        flags = ["TARIFF_LOOKUP_FAILED", "MARKET_PRICE_MISSING"]
        assert select_primary_flag(flags) == "MARKET_PRICE_MISSING"
    
    def test_consumption_missing_high_priority(self):
        """CONSUMPTION_MISSING yüksek öncelik (tüketim yoksa hiçbir şey yapılamaz)"""
        flags = ["DISTRIBUTION_MISSING", "CONSUMPTION_MISSING"]
        assert select_primary_flag(flags) == "CONSUMPTION_MISSING"
    
    def test_s2_ordering(self):
        """S2 içinde doğru sıralama: MISSING_FIELDS > TOTAL_AVG > MISMATCH"""
        flags = ["DISTRIBUTION_MISMATCH", "MISSING_FIELDS", "TOTAL_AVG_UNIT_PRICE_USED"]
        assert select_primary_flag(flags) == "MISSING_FIELDS"
    
    def test_unknown_flag_lowest_priority(self):
        """Bilinmeyen flag en düşük öncelik"""
        flags = ["UNKNOWN_FLAG", "VALIDATION_WARNINGS"]
        assert select_primary_flag(flags) == "VALIDATION_WARNINGS"


class TestGetSecondaryFlags:
    """get_secondary_flags() fonksiyonu testleri"""
    
    def test_removes_primary(self):
        """Primary flag listeden çıkarılır"""
        flags = ["A", "B", "C"]
        assert get_secondary_flags(flags, "B") == ["A", "C"]
    
    def test_empty_when_single_flag(self):
        """Tek flag varsa secondary boş"""
        assert get_secondary_flags(["A"], "A") == []
    
    def test_preserves_order(self):
        """Sıra korunur"""
        flags = ["X", "Y", "Z"]
        assert get_secondary_flags(flags, "Y") == ["X", "Z"]


class TestFlagToCategory:
    """flag_to_category() fonksiyonu testleri"""
    
    def test_tariff_meta_missing(self):
        """TARIFF_META_MISSING → TARIFF_META_MISSING"""
        assert flag_to_category("TARIFF_META_MISSING") == Category.TARIFF_META_MISSING
    
    def test_tariff_lookup_failed(self):
        """TARIFF_LOOKUP_FAILED → TARIFF_MISSING"""
        assert flag_to_category("TARIFF_LOOKUP_FAILED") == Category.TARIFF_MISSING
    
    def test_distribution_missing(self):
        """DISTRIBUTION_MISSING → TARIFF_MISSING"""
        assert flag_to_category("DISTRIBUTION_MISSING") == Category.TARIFF_MISSING
    
    def test_market_price_missing(self):
        """MARKET_PRICE_MISSING → PRICE_MISSING"""
        assert flag_to_category("MARKET_PRICE_MISSING") == Category.PRICE_MISSING
    
    def test_consumption_missing(self):
        """CONSUMPTION_MISSING → CONSUMPTION_MISSING"""
        assert flag_to_category("CONSUMPTION_MISSING") == Category.CONSUMPTION_MISSING
    
    def test_calc_bug(self):
        """CALC_BUG → CALC_BUG"""
        assert flag_to_category("CALC_BUG") == Category.CALC_BUG
    
    def test_mismatch_flags(self):
        """MISMATCH içeren → MISMATCH"""
        assert flag_to_category("DISTRIBUTION_MISMATCH") == Category.MISMATCH
    
    def test_outlier_flags(self):
        """OUTLIER içeren → OUTLIER"""
        assert flag_to_category("OUTLIER_PTF") == Category.OUTLIER
        assert flag_to_category("OUTLIER_CONSUMPTION") == Category.OUTLIER


class TestSingleIncidentStrategy:
    """Tek incident stratejisi testleri"""
    
    @patch('backend.app.incident_service.create_incident')
    def test_multiple_flags_single_incident(self, mock_create):
        """Birden fazla flag → tek incident"""
        mock_create.return_value = 1
        mock_db = MagicMock()
        
        quality = QualityScore(
            score=30,
            grade="BAD",
            flags=["MARKET_PRICE_MISSING", "TARIFF_LOOKUP_FAILED", "MISSING_FIELDS"],
            flag_details=[
                {"code": "MARKET_PRICE_MISSING", "severity": Severity.S1, "message": "PTF yok", "deduction": 50},
                {"code": "TARIFF_LOOKUP_FAILED", "severity": Severity.S1, "message": "Tarife yok", "deduction": 40},
                {"code": "MISSING_FIELDS", "severity": Severity.S2, "message": "Eksik", "deduction": 20},
            ]
        )
        
        result = create_incidents_from_quality(
            db=mock_db,
            trace_id="test-123",
            quality=quality,
            tenant_id="tenant1"
        )
        
        # Tek incident oluşturulmalı
        assert mock_create.call_count == 1
        assert len(result) == 1
        
        # create_incident çağrısını kontrol et
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["category"] == Category.PRICE_MISSING  # MARKET_PRICE_MISSING → PRICE_MISSING
        assert call_kwargs["severity"] == Severity.S1
        
        # Details içinde tüm bilgiler olmalı
        details = call_kwargs["details"]
        assert details["primary_flag"] == "MARKET_PRICE_MISSING"
        assert "TARIFF_LOOKUP_FAILED" in details["secondary_flags"]
        assert "MISSING_FIELDS" in details["secondary_flags"]
        assert details["quality_score"] == 30
        assert details["quality_grade"] == "BAD"
    
    @patch('backend.app.incident_service.create_incident')
    def test_no_critical_flags_no_incident(self, mock_create):
        """S3/S4 flag'ler → incident yok"""
        mock_db = MagicMock()
        
        quality = QualityScore(
            score=85,
            grade="OK",
            flags=["JSON_REPAIR_APPLIED", "VALIDATION_WARNINGS"],
            flag_details=[
                {"code": "JSON_REPAIR_APPLIED", "severity": Severity.S3, "message": "JSON repair", "deduction": 10},
                {"code": "VALIDATION_WARNINGS", "severity": Severity.S3, "message": "Uyarılar", "deduction": 5},
            ]
        )
        
        result = create_incidents_from_quality(
            db=mock_db,
            trace_id="test-456",
            quality=quality
        )
        
        # Incident oluşturulmamalı
        assert mock_create.call_count == 0
        assert result == []
    
    @patch('backend.app.incident_service.create_incident')
    def test_message_includes_secondary_count(self, mock_create):
        """Mesaj secondary flag sayısını içermeli"""
        mock_create.return_value = 1
        mock_db = MagicMock()
        
        quality = QualityScore(
            score=40,
            grade="CHECK",
            flags=["CALC_BUG", "DISTRIBUTION_MISSING"],
            flag_details=[
                {"code": "CALC_BUG", "severity": Severity.S1, "message": "Bug", "deduction": 50},
                {"code": "DISTRIBUTION_MISSING", "severity": Severity.S1, "message": "Dağıtım yok", "deduction": 50},
            ]
        )
        
        create_incidents_from_quality(
            db=mock_db,
            trace_id="test-789",
            quality=quality
        )
        
        call_kwargs = mock_create.call_args[1]
        assert "+1 ek sorun" in call_kwargs["message"]


class TestCKCategoryRules:
    """CK dağıtım kategori kuralları testleri"""
    
    def test_tariff_meta_missing_flag(self):
        """Validation'dan TARIFF_META_MISSING flag'i"""
        validation = {
            "is_ready_for_pricing": True,
            "distribution_tariff_meta_missing": True,
            "distribution_tariff_lookup_failed": False,
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=None,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "TARIFF_META_MISSING" in quality.flags
    
    def test_tariff_lookup_failed_flag(self):
        """Validation'dan TARIFF_LOOKUP_FAILED flag'i"""
        validation = {
            "is_ready_for_pricing": True,
            "distribution_tariff_meta_missing": False,
            "distribution_tariff_lookup_failed": True,
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=None,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "TARIFF_LOOKUP_FAILED" in quality.flags
    
    def test_calc_bug_zero_distribution(self):
        """Tarife bulundu ama dağıtım 0 = CALC_BUG"""
        calculation = {
            "meta_distribution_source": "epdk_tariff",
            "distribution_total_tl": 0,
            "consumption_kwh": 10000,
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation={},
            calculation=calculation,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "CALC_BUG" in quality.flags
    
    def test_consumption_missing_from_validation(self):
        """Validation'dan CONSUMPTION_MISSING flag'i"""
        validation = {
            "is_ready_for_pricing": False,
            "missing_fields": ["consumption_kwh"],
        }
        
        quality = calculate_quality_score(
            extraction={},
            validation=validation,
            calculation=None,
            calculation_error=None,
            debug_meta=None
        )
        
        assert "CONSUMPTION_MISSING" in quality.flags


class TestPriorityMapCompleteness:
    """FLAG_PRIORITY map'in tamamlanmışlık testi"""
    
    def test_all_quality_flags_have_priority(self):
        """Tüm QUALITY_FLAGS'in priority'si olmalı"""
        for flag_code in QUALITY_FLAGS.keys():
            assert flag_code in FLAG_PRIORITY, f"{flag_code} FLAG_PRIORITY'de yok"
    
    def test_s1_flags_lower_priority_than_s2(self):
        """S1 flag'lerin priority değeri S2'den düşük olmalı"""
        s1_flags = [k for k, v in QUALITY_FLAGS.items() if v.severity == Severity.S1]
        s2_flags = [k for k, v in QUALITY_FLAGS.items() if v.severity == Severity.S2]
        
        max_s1_priority = max(FLAG_PRIORITY.get(f, 999) for f in s1_flags)
        min_s2_priority = min(FLAG_PRIORITY.get(f, 0) for f in s2_flags)
        
        assert max_s1_priority < min_s2_priority, "S1 flag'ler S2'den önce gelmeli"


class TestDeterministicOrdering:
    """Deterministik ordering testleri"""
    
    def test_normalize_flags_sorted_by_priority(self):
        """normalize_flags priority'ye göre sıralar"""
        flags = ["VALIDATION_WARNINGS", "MARKET_PRICE_MISSING", "MISSING_FIELDS"]
        result = normalize_flags(flags)
        assert result == ["MARKET_PRICE_MISSING", "MISSING_FIELDS", "VALIDATION_WARNINGS"]
    
    def test_normalize_flags_removes_duplicates(self):
        """normalize_flags duplicate'leri kaldırır"""
        flags = ["MARKET_PRICE_MISSING", "MISSING_FIELDS", "MARKET_PRICE_MISSING"]
        result = normalize_flags(flags)
        assert result == ["MARKET_PRICE_MISSING", "MISSING_FIELDS"]
    
    def test_normalize_flags_idempotent(self):
        """normalize_flags idempotent - tekrar çağrılınca aynı sonuç"""
        flags = ["VALIDATION_WARNINGS", "MARKET_PRICE_MISSING"]
        result1 = normalize_flags(flags)
        result2 = normalize_flags(result1)
        assert result1 == result2
    
    def test_secondary_flags_preserves_normalized_order(self):
        """secondary_flags normalized sırayı korur"""
        flags = ["VALIDATION_WARNINGS", "MARKET_PRICE_MISSING", "MISSING_FIELDS"]
        primary = select_primary_flag(flags)
        secondary = get_secondary_flags(flags, primary)
        # MARKET_PRICE_MISSING primary, geri kalanlar sıralı
        assert secondary == ["MISSING_FIELDS", "VALIDATION_WARNINGS"]


class TestCalcBugConditions:
    """CALC_BUG 3 koşul kontrolü testleri"""
    
    def test_calc_bug_all_conditions_met(self):
        """3 koşul sağlandığında CALC_BUG True"""
        validation = {"distribution_computed_from_tariff": True}
        calculation = {
            "meta_distribution_source": "epdk_tariff",
            "distribution_total_tl": 0,
            "consumption_kwh": 10000
        }
        is_bug, reason = check_calc_bug_conditions(validation, calculation)
        assert is_bug
        assert "0 TL" in reason
    
    def test_calc_bug_no_ck_input(self):
        """CK input yoksa CALC_BUG False"""
        validation = {}
        calculation = {
            "meta_distribution_source": "not_found",
            "distribution_total_tl": 0,
            "consumption_kwh": 10000
        }
        is_bug, _ = check_calc_bug_conditions(validation, calculation)
        assert not is_bug
    
    def test_calc_bug_lookup_not_done(self):
        """Lookup yapılmadıysa CALC_BUG False"""
        validation = {"distribution_tariff_key": "SANAYI_OG_TEK"}
        calculation = {
            "meta_distribution_source": "not_found",
            "distribution_total_tl": 0,
            "consumption_kwh": 10000
        }
        is_bug, _ = check_calc_bug_conditions(validation, calculation)
        assert not is_bug
    
    def test_calc_bug_normal_result(self):
        """Normal sonuç varsa CALC_BUG False"""
        validation = {"distribution_computed_from_tariff": True}
        calculation = {
            "meta_distribution_source": "epdk_tariff",
            "distribution_total_tl": 5000,
            "consumption_kwh": 10000
        }
        is_bug, _ = check_calc_bug_conditions(validation, calculation)
        assert not is_bug
    
    def test_calc_bug_negative_result(self):
        """Negatif sonuç CALC_BUG True"""
        validation = {"distribution_computed_from_tariff": True}
        calculation = {
            "meta_distribution_source": "epdk_tariff",
            "distribution_total_tl": -100,
            "consumption_kwh": 10000
        }
        is_bug, reason = check_calc_bug_conditions(validation, calculation)
        assert is_bug
        assert "negatif" in reason


class TestActionRecommendations:
    """Action recommendation testleri"""
    
    def test_all_flags_have_action(self):
        """Tüm flag'lerin action'ı olmalı"""
        for flag_code in QUALITY_FLAGS.keys():
            action = get_action_recommendation(flag_code)
            assert action is not None, f"{flag_code} için action yok"
            assert "type" in action
            assert "owner" in action
            assert "code" in action
    
    def test_market_price_missing_action(self):
        """MARKET_PRICE_MISSING → RETRY_LOOKUP + PTF_YEKDEM_CHECK"""
        action = get_action_recommendation("MARKET_PRICE_MISSING")
        assert action["type"] == IncidentAction.RETRY_LOOKUP
        assert action["owner"] == IncidentOwner.MARKET_PRICE
        assert action["code"] == HintCode.PTF_YEKDEM_CHECK
    
    def test_calc_bug_action(self):
        """CALC_BUG → BUG_REPORT + ENGINE_REGRESSION"""
        action = get_action_recommendation("CALC_BUG")
        assert action["type"] == IncidentAction.BUG_REPORT
        assert action["owner"] == IncidentOwner.CALC
        assert action["code"] == HintCode.ENGINE_REGRESSION
    
    def test_tariff_meta_missing_action(self):
        """TARIFF_META_MISSING → USER_FIX + MANUAL_META_ENTRY"""
        action = get_action_recommendation("TARIFF_META_MISSING")
        assert action["type"] == IncidentAction.USER_FIX
        assert action["owner"] == IncidentOwner.USER
        assert action["code"] == HintCode.MANUAL_META_ENTRY
    
    def test_unknown_flag_returns_default(self):
        """Bilinmeyen flag için default action"""
        action = get_action_recommendation("UNKNOWN_FLAG_XYZ")
        assert action["type"] == IncidentAction.USER_FIX
        assert action["owner"] == IncidentOwner.USER
        assert action["code"] == HintCode.UNKNOWN


class TestEnvironmentValidation:
    """ENV whitelist testleri"""
    
    def test_valid_environments(self):
        """Geçerli environment'lar"""
        for env in ["development", "staging", "production"]:
            valid, _ = validate_environment(env)
            assert valid, f"{env} geçerli olmalı"
    
    def test_invalid_environment(self):
        """Geçersiz environment"""
        valid, error = validate_environment("prod")
        assert not valid
        assert "Invalid ENV" in error
    
    def test_empty_environment_valid(self):
        """Boş environment geçerli (default)"""
        valid, _ = validate_environment("")
        assert valid
    
    def test_production_guard_with_invalid_env(self):
        """Geçersiz ENV ile production guard"""
        valid, error = check_production_guard("prod", True, "a" * 32)
        assert not valid
        assert "Invalid ENV" in error
