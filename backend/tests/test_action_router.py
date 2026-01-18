"""
Action Router Tests - Sprint 6.0

4 route testleri: USER_FIX, RETRY_LOOKUP, BUG_REPORT, FALLBACK_OK
"""

import pytest
from datetime import datetime, timedelta
from backend.app.action_router import ActionRouter, RoutedAction, UiAlertPayload, RetrySchedule
from backend.app.issue_payload import IssuePayloadBuilder


class TestActionRouter:
    """ActionRouter testleri"""
    
    @pytest.fixture
    def router(self):
        return ActionRouter()
    
    @pytest.fixture
    def base_params(self):
        return {
            "provider": "ck_bogazici",
            "invoice_id": "INV001",
            "period": "2025-01",
            "dedupe_key": "test_dedupe_key",
        }
    
    def test_user_fix_route(self, router, base_params):
        """USER_FIX → status OPEN + ui_alert"""
        incident = {
            "primary_flag": "TARIFF_META_MISSING",
            "category": "TARIFF_META_MISSING",
            "severity": "S1",
            "action": {
                "type": "USER_FIX",
                "owner": "user",
                "code": "MANUAL_META_ENTRY",
                "hint_text": "Sag ust CK metasi OCR ile okunamadi; manuel meta gir",
            },
            "all_flags": ["TARIFF_META_MISSING"],
        }
        
        result = router.route(incident=incident, **base_params)
        
        assert result.action_type == "USER_FIX"
        assert result.status == "OPEN"
        assert result.payload is not None
        assert "ui_alert" in result.payload
        assert result.payload["ui_alert"]["code"] == "MANUAL_META_ENTRY"
        assert "manuel meta gir" in result.payload["ui_alert"]["message"]
    
    def test_retry_lookup_route(self, router, base_params):
        """RETRY_LOOKUP → PENDING_RETRY + retry_eligible_at"""
        incident = {
            "primary_flag": "MARKET_PRICE_MISSING",
            "category": "PRICE_MISSING",
            "severity": "S1",
            "action": {
                "type": "RETRY_LOOKUP",
                "owner": "market_price",
                "code": "PTF_YEKDEM_CHECK",
                "hint_text": "PTF/YEKDEM kaynaklarini kontrol et",
            },
            "all_flags": ["MARKET_PRICE_MISSING"],
        }
        
        now = datetime(2025, 1, 15, 10, 0, 0)
        result = router.route(incident=incident, now=now, **base_params)
        
        assert result.action_type == "RETRY_LOOKUP"
        assert result.status == "PENDING_RETRY"
        assert result.payload is not None
        assert "retry" in result.payload
        assert result.payload["retry"]["reason_code"] == "PTF_YEKDEM_CHECK"
        
        # retry_eligible_at 30 dakika sonra olmali
        expected_retry = (now + timedelta(minutes=30)).isoformat()
        assert result.payload["retry"]["retry_eligible_at"] == expected_retry
    
    def test_bug_report_route(self, router, base_params):
        """BUG_REPORT → REPORTED + issue payload"""
        incident = {
            "primary_flag": "CALC_BUG",
            "category": "CALC_BUG",
            "severity": "S1",
            "action": {
                "type": "BUG_REPORT",
                "owner": "calc",
                "code": "ENGINE_REGRESSION",
                "hint_text": "computed 0; input CK var; engine regression suphesi",
            },
            "all_flags": ["CALC_BUG"],
        }
        
        result = router.route(incident=incident, **base_params)
        
        assert result.action_type == "BUG_REPORT"
        assert result.status == "REPORTED"
        assert result.payload is not None
        assert "issue" in result.payload
        
        issue = result.payload["issue"]
        assert issue["primary_flag"] == "CALC_BUG"
        assert issue["category"] == "CALC_BUG"
        assert issue["severity"] == "S1"
        assert issue["dedupe_key"] == "test_dedupe_key"
        assert "[CALC_BUG]" in issue["title"]
    
    def test_fallback_ok_route(self, router, base_params):
        """FALLBACK_OK → AUTO_RESOLVED, payload yok"""
        incident = {
            "primary_flag": "JSON_REPAIR_APPLIED",
            "category": "JSON_REPAIR",
            "severity": "S3",
            "action": {
                "type": "FALLBACK_OK",
                "owner": "extraction",
                "code": "JSON_REPAIR_REVIEW",
                "hint_text": "JSON repair uygulandi; sonuc dogru ise OK",
            },
            "all_flags": ["JSON_REPAIR_APPLIED"],
        }
        
        result = router.route(incident=incident, **base_params)
        
        assert result.action_type == "FALLBACK_OK"
        assert result.status == "AUTO_RESOLVED"
        assert result.payload is None
    
    def test_unknown_action_defaults_to_user_fix(self, router, base_params):
        """Bilinmeyen action type → USER_FIX"""
        incident = {
            "primary_flag": "UNKNOWN_FLAG",
            "category": "UNKNOWN",
            "severity": "S2",
            "action": {
                "type": "UNKNOWN_ACTION",
                "owner": "unknown",
                "code": "UNKNOWN",
            },
            "all_flags": ["UNKNOWN_FLAG"],
        }
        
        result = router.route(incident=incident, **base_params)
        
        assert result.action_type == "USER_FIX"
        assert result.status == "OPEN"
    
    def test_missing_action_defaults_to_user_fix(self, router, base_params):
        """Action yoksa → USER_FIX"""
        incident = {
            "primary_flag": "SOME_FLAG",
            "category": "SOME_CATEGORY",
            "severity": "S2",
            # action yok
        }
        
        result = router.route(incident=incident, **base_params)
        
        assert result.action_type == "USER_FIX"
        assert result.status == "OPEN"


class TestActionRouterWithContext:
    """Context ile ActionRouter testleri"""
    
    @pytest.fixture
    def router(self):
        return ActionRouter()
    
    def test_bug_report_with_calc_context(self, router):
        """BUG_REPORT calc_context ile"""
        incident = {
            "primary_flag": "CALC_BUG",
            "category": "CALC_BUG",
            "severity": "S1",
            "action": {
                "type": "BUG_REPORT",
                "owner": "calc",
                "code": "ENGINE_REGRESSION",
            },
            "all_flags": ["CALC_BUG"],
        }
        
        calc_context = {
            "consumption_kwh": 15000,
            "distribution_total_tl": 0,
            "meta_distribution_source": "epdk_tariff",
            # PII - olmamali
            "customer_name": "ACME Corp",
        }
        
        result = router.route(
            incident=incident,
            provider="ck_bogazici",
            invoice_id="INV001",
            period="2025-01",
            dedupe_key="test_key",
            calc_context=calc_context,
        )
        
        issue = result.payload["issue"]
        
        # Allowlist icindekiler olmali
        assert issue["normalized_inputs"].get("consumption_kwh") == 15000
        assert issue["normalized_inputs"].get("distribution_total_tl") == 0
        
        # PII olmamali
        assert "customer_name" not in issue["normalized_inputs"]
    
    def test_bug_report_with_lookup_evidence(self, router):
        """BUG_REPORT lookup_evidence ile"""
        incident = {
            "primary_flag": "CALC_BUG",
            "category": "CALC_BUG",
            "severity": "S1",
            "action": {
                "type": "BUG_REPORT",
                "owner": "calc",
                "code": "ENGINE_REGRESSION",
            },
            "all_flags": ["CALC_BUG"],
        }
        
        lookup_evidence = {
            "market_price_status": "success",
            "market_price_source": "epias_api",
            "tariff_status": "success",
            "tariff_source": "epdk_tariff",
        }
        
        result = router.route(
            incident=incident,
            provider="ck_bogazici",
            invoice_id="INV001",
            period="2025-01",
            dedupe_key="test_key",
            lookup_evidence=lookup_evidence,
        )
        
        issue = result.payload["issue"]
        
        assert issue["lookup_evidence"]["market_price"]["status"] == "success"
        assert issue["lookup_evidence"]["tariff"]["source"] == "epdk_tariff"


class TestCustomRetryDelay:
    """Custom retry delay testleri"""
    
    def test_custom_retry_delay(self):
        """Custom retry delay kullanilmali"""
        router = ActionRouter(retry_delay_minutes=60)
        
        incident = {
            "primary_flag": "MARKET_PRICE_MISSING",
            "category": "PRICE_MISSING",
            "severity": "S1",
            "action": {
                "type": "RETRY_LOOKUP",
                "owner": "market_price",
                "code": "PTF_YEKDEM_CHECK",
            },
            "all_flags": ["MARKET_PRICE_MISSING"],
        }
        
        now = datetime(2025, 1, 15, 10, 0, 0)
        result = router.route(
            incident=incident,
            provider="test",
            invoice_id="test",
            period="2025-01",
            dedupe_key="test",
            now=now,
        )
        
        # 60 dakika sonra olmali
        expected_retry = (now + timedelta(minutes=60)).isoformat()
        assert result.payload["retry"]["retry_eligible_at"] == expected_retry


class TestRoutedActionSerialization:
    """RoutedAction serialization testleri"""
    
    @pytest.fixture
    def router(self):
        return ActionRouter()
    
    def test_to_dict_serializable(self, router):
        """to_dict JSON serializable olmali"""
        incident = {
            "primary_flag": "TARIFF_META_MISSING",
            "category": "TARIFF_META_MISSING",
            "severity": "S1",
            "action": {
                "type": "USER_FIX",
                "owner": "user",
                "code": "MANUAL_META_ENTRY",
                "hint_text": "Test hint",
            },
            "all_flags": ["TARIFF_META_MISSING"],
        }
        
        result = router.route(
            incident=incident,
            provider="test",
            invoice_id="test",
            period="2025-01",
            dedupe_key="test",
        )
        
        d = result.to_dict()
        assert isinstance(d, dict)
        assert d["action_type"] == "USER_FIX"
        assert d["status"] == "OPEN"
