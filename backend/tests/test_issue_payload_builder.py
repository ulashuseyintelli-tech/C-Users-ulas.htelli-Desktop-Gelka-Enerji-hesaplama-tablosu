"""
Issue Payload Builder Tests - Sprint 6.0

PII-safe issue payload testleri.
"""

import pytest
from backend.app.issue_payload import IssuePayloadBuilder, IssuePayload


class TestIssuePayloadBuilder:
    """IssuePayloadBuilder testleri"""
    
    @pytest.fixture
    def builder(self):
        return IssuePayloadBuilder()
    
    @pytest.fixture
    def sample_incident(self):
        return {
            "primary_flag": "CALC_BUG",
            "category": "CALC_BUG",
            "severity": "S1",
            "action": {
                "type": "BUG_REPORT",
                "owner": "calc",
                "code": "ENGINE_REGRESSION",
                "hint_text": "computed 0; input CK var; engine regression suphesi",
            },
            "all_flags": ["CALC_BUG", "DISTRIBUTION_MISMATCH"],
        }
    
    def test_pii_allowlist_enforced(self, builder, sample_incident):
        """PII allowlist disi alanlar payload'a girmemeli"""
        calc_context = {
            # Allowlist icinde
            "consumption_kwh": 15000,
            "invoice_period": "2025-01",
            "meta_distribution_source": "epdk_tariff",
            # PII - allowlist disinda
            "customer_name": "ACME Corp",
            "customer_address": "Istanbul, Turkey",
            "tax_id": "1234567890",
            "meter_no": "ABC123456",
            "subscriber_no": "SUB789",
        }
        
        payload = builder.build(
            incident=sample_incident,
            dedupe_key="test_key",
            provider="ck_bogazici",
            invoice_id="INV001",
            period="2025-01",
            calc_context=calc_context,
        )
        
        # Allowlist icindekiler olmali
        assert payload.normalized_inputs.get("consumption_kwh") == 15000
        assert payload.normalized_inputs.get("invoice_period") == "2025-01"
        assert payload.normalized_inputs.get("meta_distribution_source") == "epdk_tariff"
        
        # PII olmamali
        assert "customer_name" not in payload.normalized_inputs
        assert "customer_address" not in payload.normalized_inputs
        assert "tax_id" not in payload.normalized_inputs
        assert "meter_no" not in payload.normalized_inputs
        assert "subscriber_no" not in payload.normalized_inputs
    
    def test_action_code_in_payload_hint_text_not_in_snapshot(self, builder, sample_incident):
        """action.code payload'da var, hint_text snapshot'a girmemeli"""
        payload = builder.build(
            incident=sample_incident,
            dedupe_key="test_key",
            provider="ck_bogazici",
            invoice_id="INV001",
            period="2025-01",
        )
        
        # action.code olmali
        assert payload.action["code"] == "ENGINE_REGRESSION"
        assert payload.action["type"] == "BUG_REPORT"
        assert payload.action["owner"] == "calc"
        
        # hint_text action dict'te olmamali (sadece type, owner, code)
        assert "hint_text" not in payload.action
    
    def test_repro_hint_no_real_data(self, builder, sample_incident):
        """repro_hint icinde gercek veri olmamali"""
        payload = builder.build(
            incident=sample_incident,
            dedupe_key="test_key",
            provider="ck_bogazici",
            invoice_id="INV001",
            period="2025-01",
        )
        
        # repro_hint "synthetic" olmali
        assert "synthetic" in payload.repro_hint.lower()
        # Gercek invoice_id olmamali
        assert "INV001" not in payload.repro_hint
        assert "ck_bogazici" not in payload.repro_hint
    
    def test_title_format(self, builder, sample_incident):
        """Title formati dogru olmali"""
        payload = builder.build(
            incident=sample_incident,
            dedupe_key="test_key",
            provider="ck_bogazici",
            invoice_id="INV001",
            period="2025-01",
        )
        
        assert payload.title == "[CALC_BUG] provider=ck_bogazici invoice=INV001 period=2025-01"
    
    def test_labels_include_required_fields(self, builder, sample_incident):
        """Labels gerekli alanlari icermeli"""
        payload = builder.build(
            incident=sample_incident,
            dedupe_key="test_key",
            provider="ck_bogazici",
            invoice_id="INV001",
            period="2025-01",
        )
        
        assert "incident" in payload.labels
        assert "CALC_BUG" in payload.labels  # category
        assert "CALC_BUG" in payload.labels  # primary_flag
        assert "calc" in payload.labels  # owner
    
    def test_lookup_evidence_safe_format(self, builder, sample_incident):
        """Lookup evidence sadece status/source icermeli"""
        lookup_evidence = {
            "market_price_status": "not_found",
            "market_price_source": "epias_api",
            "tariff_status": "success",
            "tariff_source": "epdk_tariff",
            # Raw response - olmamali
            "raw_response": {"data": "sensitive"},
        }
        
        payload = builder.build(
            incident=sample_incident,
            dedupe_key="test_key",
            provider="ck_bogazici",
            invoice_id="INV001",
            period="2025-01",
            lookup_evidence=lookup_evidence,
        )
        
        # Safe fields olmali
        assert payload.lookup_evidence["market_price"]["status"] == "not_found"
        assert payload.lookup_evidence["market_price"]["source"] == "epias_api"
        assert payload.lookup_evidence["tariff"]["status"] == "success"
        assert payload.lookup_evidence["tariff"]["source"] == "epdk_tariff"
        
        # Raw response olmamali
        assert "raw_response" not in payload.lookup_evidence
    
    def test_invoice_info_included(self, builder, sample_incident):
        """Invoice bilgisi payload'da olmali"""
        payload = builder.build(
            incident=sample_incident,
            dedupe_key="test_key",
            provider="ck_bogazici",
            invoice_id="INV001",
            period="2025-01",
        )
        
        assert payload.invoice["provider"] == "ck_bogazici"
        assert payload.invoice["invoice_id"] == "INV001"
        assert payload.invoice["period"] == "2025-01"
    
    def test_to_dict_serializable(self, builder, sample_incident):
        """to_dict JSON serializable olmali"""
        payload = builder.build(
            incident=sample_incident,
            dedupe_key="test_key",
            provider="ck_bogazici",
            invoice_id="INV001",
            period="2025-01",
        )
        
        d = payload.to_dict()
        assert isinstance(d, dict)
        assert d["primary_flag"] == "CALC_BUG"
        assert d["dedupe_key"] == "test_key"


class TestReproHints:
    """Repro hint testleri - her flag icin"""
    
    @pytest.fixture
    def builder(self):
        return IssuePayloadBuilder()
    
    @pytest.mark.parametrize("primary_flag,expected_keyword", [
        ("CALC_BUG", "distribution"),
        ("MARKET_PRICE_MISSING", "market price"),
        ("TARIFF_LOOKUP_FAILED", "tariff"),
        ("TARIFF_META_MISSING", "meta"),
        ("CONSUMPTION_MISSING", "consumption"),
        ("DISTRIBUTION_MISSING", "distribution"),
        ("DISTRIBUTION_MISMATCH", "distribution"),
        ("MISSING_FIELDS", "fields"),
    ])
    def test_repro_hint_relevant_to_flag(self, builder, primary_flag, expected_keyword):
        """Her flag icin repro hint ilgili keyword icermeli"""
        incident = {
            "primary_flag": primary_flag,
            "category": "TEST",
            "severity": "S1",
            "action": {"type": "BUG_REPORT", "owner": "test", "code": "TEST"},
            "all_flags": [primary_flag],
        }
        
        payload = builder.build(
            incident=incident,
            dedupe_key="test",
            provider="test",
            invoice_id="test",
            period="2025-01",
        )
        
        assert expected_keyword.lower() in payload.repro_hint.lower()
