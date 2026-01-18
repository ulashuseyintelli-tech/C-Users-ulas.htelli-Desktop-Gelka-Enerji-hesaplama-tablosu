import pytest
from hypothesis import given, strategies as st, settings
from datetime import datetime, timedelta, UTC
from unittest.mock import patch
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.webhook import (
    generate_signature,
    verify_signature,
    calculate_next_retry,
    build_invoice_uploaded_payload,
    build_invoice_extracted_payload,
    build_offer_status_changed_payload,
    build_invoice_validated_payload,
    build_offer_created_payload,
    WEBHOOK_EVENTS,
    WEBHOOK_MAX_RETRIES,
)
from app.services.webhook_manager import (
    WebhookManager,
    get_webhook_manager,
    trigger_webhook,
    register_webhook,
)


class TestHMACSignature:
    def test_generate_signature_returns_sha256_prefix(self):
        payload = {"event": "test", "data": {"id": 1}}
        secret = "test-secret"
        signature = generate_signature(payload, secret)
        assert signature.startswith("sha256=")
    
    def test_generate_signature_returns_hex_string(self):
        payload = {"event": "test", "data": {"id": 1}}
        secret = "test-secret"
        signature = generate_signature(payload, secret)
        hex_part = signature.replace("sha256=", "")
        assert len(hex_part) == 64
        assert all(c in "0123456789abcdef" for c in hex_part)
    
    def test_same_payload_same_secret_same_signature(self):
        payload = {"event": "invoice.uploaded", "data": {"id": "123"}}
        secret = "my-secret"
        sig1 = generate_signature(payload, secret)
        sig2 = generate_signature(payload, secret)
        assert sig1 == sig2
    
    def test_different_payload_different_signature(self):
        secret = "my-secret"
        payload1 = {"event": "test", "data": {"id": 1}}
        payload2 = {"event": "test", "data": {"id": 2}}
        sig1 = generate_signature(payload1, secret)
        sig2 = generate_signature(payload2, secret)
        assert sig1 != sig2
    
    def test_verify_signature_valid(self):
        payload = {"event": "test", "data": {"id": 1}}
        secret = "test-secret"
        signature = generate_signature(payload, secret)
        assert verify_signature(payload, signature, secret) is True
    
    def test_verify_signature_invalid(self):
        payload = {"event": "test", "data": {"id": 1}}
        secret = "test-secret"
        invalid_sig = "sha256=0000000000000000000000000000000000000000000000000000000000000000"
        assert verify_signature(payload, invalid_sig, secret) is False


class TestWebhookConfigCRUD:
    def test_add_config_returns_config_with_id(self):
        manager = WebhookManager()
        config = manager.add_config(
            url="https://example.com/webhook",
            events=["invoice.uploaded"],
            secret="test-secret"
        )
        assert "id" in config
        assert config["id"] > 0
    
    def test_add_config_stores_url(self):
        manager = WebhookManager()
        url = "https://example.com/webhook"
        config = manager.add_config(url=url, events=["invoice.uploaded"])
        assert config["url"] == url
    
    def test_add_config_stores_events(self):
        manager = WebhookManager()
        events = ["invoice.uploaded", "invoice.extracted"]
        config = manager.add_config(url="https://example.com", events=events)
        assert config["events"] == events
    
    def test_add_config_default_active(self):
        manager = WebhookManager()
        config = manager.add_config(url="https://example.com", events=["invoice.uploaded"])
        assert config["is_active"] is True
    
    def test_list_configs_returns_all_for_tenant(self):
        manager = WebhookManager()
        manager.add_config(url="https://a.com", events=["invoice.uploaded"], tenant_id="tenant1")
        manager.add_config(url="https://b.com", events=["invoice.extracted"], tenant_id="tenant1")
        manager.add_config(url="https://c.com", events=["offer.created"], tenant_id="tenant2")
        configs = manager.list_configs(tenant_id="tenant1")
        assert len(configs) == 2
    
    def test_remove_config_removes_by_id(self):
        manager = WebhookManager()
        config = manager.add_config(url="https://example.com", events=["invoice.uploaded"])
        config_id = config["id"]
        result = manager.remove_config(config_id)
        assert result is True
        assert len(manager.list_configs()) == 0
    
    def test_remove_config_returns_false_for_nonexistent(self):
        manager = WebhookManager()
        result = manager.remove_config(999)
        assert result is False
    
    def test_get_stats_returns_correct_counts(self):
        manager = WebhookManager()
        manager.add_config(url="https://a.com", events=["invoice.uploaded"])
        manager.add_config(url="https://b.com", events=["invoice.extracted"])
        stats = manager.get_stats()
        assert stats["total_configs"] == 2
        assert stats["active_configs"] == 2


class TestEventFiltering:
    def test_get_configs_for_event_returns_matching(self):
        manager = WebhookManager()
        manager.add_config(url="https://a.com", events=["invoice.uploaded"])
        manager.add_config(url="https://b.com", events=["invoice.extracted"])
        manager.add_config(url="https://c.com", events=["invoice.uploaded", "invoice.extracted"])
        configs = manager.get_configs_for_event("invoice.uploaded")
        assert len(configs) == 2
    
    def test_get_configs_for_event_excludes_inactive(self):
        manager = WebhookManager()
        config = manager.add_config(url="https://a.com", events=["invoice.uploaded"])
        config["is_active"] = False
        configs = manager.get_configs_for_event("invoice.uploaded")
        assert len(configs) == 0
    
    def test_get_configs_for_event_filters_by_tenant(self):
        manager = WebhookManager()
        manager.add_config(url="https://a.com", events=["invoice.uploaded"], tenant_id="tenant1")
        manager.add_config(url="https://b.com", events=["invoice.uploaded"], tenant_id="tenant2")
        configs = manager.get_configs_for_event("invoice.uploaded", tenant_id="tenant1")
        assert len(configs) == 1
        assert configs[0]["url"] == "https://a.com"
    
    def test_get_configs_for_event_returns_empty_for_no_match(self):
        manager = WebhookManager()
        manager.add_config(url="https://a.com", events=["invoice.uploaded"])
        configs = manager.get_configs_for_event("offer.created")
        assert len(configs) == 0


class TestDeliveryTracking:
    def test_trigger_event_updates_success_count(self):
        manager = WebhookManager()
        config = manager.add_config(url="https://httpbin.org/post", events=["invoice.uploaded"])
        with patch('app.services.webhook_manager.send_webhook_sync') as mock_send:
            mock_send.return_value = (True, 200, '{"success": true}')
            manager.trigger_event("invoice.uploaded", {"id": "123"})
        assert config["success_count"] == 1
    
    def test_trigger_event_updates_failure_count(self):
        manager = WebhookManager()
        config = manager.add_config(url="https://example.com/webhook", events=["invoice.uploaded"])
        with patch('app.services.webhook_manager.send_webhook_sync') as mock_send:
            mock_send.return_value = (False, 500, 'Internal Server Error')
            manager.trigger_event("invoice.uploaded", {"id": "123"})
        assert config["failure_count"] == 1
    
    def test_trigger_event_updates_last_triggered_at(self):
        manager = WebhookManager()
        config = manager.add_config(url="https://example.com/webhook", events=["invoice.uploaded"])
        with patch('app.services.webhook_manager.send_webhook_sync') as mock_send:
            mock_send.return_value = (True, 200, '{}')
            manager.trigger_event("invoice.uploaded", {"id": "123"})
        assert "last_triggered_at" in config
        assert config["last_triggered_at"] is not None
    
    def test_trigger_event_returns_results(self):
        manager = WebhookManager()
        manager.add_config(url="https://a.com", events=["invoice.uploaded"])
        manager.add_config(url="https://b.com", events=["invoice.uploaded"])
        with patch('app.services.webhook_manager.send_webhook_sync') as mock_send:
            mock_send.return_value = (True, 200, '{}')
            results = manager.trigger_event("invoice.uploaded", {"id": "123"})
        assert len(results) == 2
        assert all("success" in r for r in results)


class TestRetryMechanism:
    def test_calculate_next_retry_first_attempt(self):
        next_retry = calculate_next_retry(0)
        assert next_retry is not None
        expected = datetime.now(UTC) + timedelta(seconds=60)
        assert abs((next_retry - expected).total_seconds()) < 2
    
    def test_calculate_next_retry_second_attempt(self):
        next_retry = calculate_next_retry(1)
        assert next_retry is not None
        expected = datetime.now(UTC) + timedelta(seconds=300)
        assert abs((next_retry - expected).total_seconds()) < 2
    
    def test_calculate_next_retry_third_attempt(self):
        next_retry = calculate_next_retry(2)
        assert next_retry is not None
        expected = datetime.now(UTC) + timedelta(seconds=900)
        assert abs((next_retry - expected).total_seconds()) < 2
    
    def test_calculate_next_retry_max_exceeded(self):
        next_retry = calculate_next_retry(WEBHOOK_MAX_RETRIES)
        assert next_retry is None


class TestPayloadBuilders:
    def test_build_invoice_uploaded_payload(self):
        payload = build_invoice_uploaded_payload(invoice_id="inv-123", filename="fatura.pdf", file_size=1024)
        assert payload["invoice_id"] == "inv-123"
        assert payload["filename"] == "fatura.pdf"
        assert payload["file_size"] == 1024
    
    def test_build_invoice_extracted_payload(self):
        payload = build_invoice_extracted_payload(
            invoice_id="inv-123", vendor="enerjisa", period="2025-01",
            consumption_kwh=1500.0, total_amount=2500.0
        )
        assert payload["invoice_id"] == "inv-123"
        assert payload["vendor"] == "enerjisa"
    
    def test_build_offer_status_changed_payload(self):
        payload = build_offer_status_changed_payload(
            offer_id="off-123", old_status="sent", new_status="accepted", changed_by="user@example.com"
        )
        assert payload["offer_id"] == "off-123"
        assert payload["old_status"] == "sent"
        assert payload["new_status"] == "accepted"


class TestProperty19HMACRoundTrip:
    @given(
        st.dictionaries(
            keys=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('L', 'N'))),
            values=st.one_of(st.text(max_size=100), st.integers(), st.floats(allow_nan=False, allow_infinity=False), st.booleans(), st.none()),
            min_size=1, max_size=10
        ),
        st.text(min_size=1, max_size=100)
    )
    @settings(max_examples=50)
    def test_signature_round_trip(self, payload, secret):
        signature = generate_signature(payload, secret)
        assert verify_signature(payload, signature, secret) is True
    
    @given(
        st.text(min_size=1, max_size=50),
        st.text(min_size=1, max_size=50),
        st.integers(min_value=0, max_value=1000000),
        st.text(min_size=1, max_size=100)
    )
    @settings(max_examples=30)
    def test_invoice_payload_round_trip(self, invoice_id, filename, file_size, secret):
        payload = build_invoice_uploaded_payload(invoice_id, filename, file_size)
        signature = generate_signature(payload, secret)
        assert verify_signature(payload, signature, secret) is True


class TestProperty20EventFilteringConsistency:
    @given(
        st.lists(st.sampled_from(WEBHOOK_EVENTS), min_size=1, max_size=5, unique=True),
        st.sampled_from(WEBHOOK_EVENTS)
    )
    @settings(max_examples=50)
    def test_event_filtering_consistency(self, registered_events, query_event):
        manager = WebhookManager()
        manager.add_config(url="https://example.com/webhook", events=registered_events)
        configs = manager.get_configs_for_event(query_event)
        if query_event in registered_events:
            assert len(configs) == 1
        else:
            assert len(configs) == 0
    
    @given(
        st.lists(st.sampled_from(WEBHOOK_EVENTS), min_size=1, max_size=5, unique=True),
        st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('L', 'N')))
    )
    @settings(max_examples=30)
    def test_tenant_isolation(self, events, tenant_suffix):
        manager = WebhookManager()
        tenant1 = f"tenant1_{tenant_suffix}"
        tenant2 = f"tenant2_{tenant_suffix}"
        manager.add_config(url="https://a.com", events=events, tenant_id=tenant1)
        manager.add_config(url="https://b.com", events=events, tenant_id=tenant2)
        query_event = events[0]
        configs_t1 = manager.get_configs_for_event(query_event, tenant_id=tenant1)
        configs_t2 = manager.get_configs_for_event(query_event, tenant_id=tenant2)
        assert len(configs_t1) == 1
        assert len(configs_t2) == 1
        assert configs_t1[0]["url"] != configs_t2[0]["url"]


class TestGlobalFunctions:
    def test_get_webhook_manager_returns_singleton(self):
        import app.services.webhook_manager as wm
        wm._webhook_manager = None
        manager1 = get_webhook_manager()
        manager2 = get_webhook_manager()
        assert manager1 is manager2
    
    def test_register_webhook_adds_config(self):
        import app.services.webhook_manager as wm
        wm._webhook_manager = None
        config = register_webhook(url="https://example.com/webhook", events=["invoice.uploaded"], secret="test-secret")
        assert config["url"] == "https://example.com/webhook"
        assert config["events"] == ["invoice.uploaded"]
    
    def test_trigger_webhook_triggers_event(self):
        import app.services.webhook_manager as wm
        wm._webhook_manager = None
        register_webhook(url="https://example.com/webhook", events=["invoice.uploaded"])
        with patch('app.services.webhook_manager.send_webhook_sync') as mock_send:
            mock_send.return_value = (True, 200, '{}')
            results = trigger_webhook("invoice.uploaded", {"id": "123"})
        assert len(results) == 1
        assert results[0]["success"] is True


