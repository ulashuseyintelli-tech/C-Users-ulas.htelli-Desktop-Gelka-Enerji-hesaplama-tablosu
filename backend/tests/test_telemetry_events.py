"""
Tests for POST /admin/telemetry/events endpoint and EventStore.

Feature: telemetry-unification, Task 5
Requirements: 6.1–6.12

Known failures (pre-existing, telemetry-dışı):
- test_market_price_admin_service::TestUpsertInsert::test_insert_new_record
- test_market_price_admin_service::TestUpsertUpdate::test_update_provisional_to_final
"""

import pytest
from unittest.mock import MagicMock, patch

from app.ptf_metrics import get_ptf_metrics
from app.event_store import EventStore, get_event_store


@pytest.fixture(autouse=True)
def fresh_state():
    """Reset metrics and event store before each test."""
    m = get_ptf_metrics()
    m.reset()
    s = get_event_store()
    s.reset()
    yield m, s


@pytest.fixture()
def client():
    """TestClient with DB and admin-key dependencies overridden."""
    with patch.dict("os.environ", {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app, _rate_limit_buckets
        from app.database import get_db
        from fastapi.testclient import TestClient

        # Clear rate limit state
        _rate_limit_buckets.clear()

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db
        yield TestClient(fastapi_app)
        fastapi_app.dependency_overrides.clear()


# ── EventStore unit tests ─────────────────────────────────────────────────────

class TestEventStore:
    """EventStore counter-only design tests."""

    def test_increment_and_get_counters(self):
        store = EventStore()
        store.increment("ptf_admin.upsert_submit")
        store.increment("ptf_admin.upsert_submit")
        store.increment("ptf_admin.filter_change")

        counters = store.get_counters()
        assert counters == {"ptf_admin.upsert_submit": 2, "ptf_admin.filter_change": 1}

    def test_increment_rejected(self):
        store = EventStore()
        store.increment_rejected()
        store.increment_rejected()
        totals = store.get_totals()
        assert totals["rejected"] == 2
        assert totals["accepted"] == 0

    def test_get_totals(self):
        store = EventStore()
        store.increment("ptf_admin.a")
        store.increment("ptf_admin.b")
        store.increment_rejected()
        totals = store.get_totals()
        assert totals == {"accepted": 2, "rejected": 1}

    def test_reset(self):
        store = EventStore()
        store.increment("ptf_admin.x")
        store.increment_rejected()
        store.reset()
        assert store.get_counters() == {}
        assert store.get_totals() == {"accepted": 0, "rejected": 0}


# ── Endpoint integration tests ────────────────────────────────────────────────

class TestTelemetryEndpoint:
    """POST /admin/telemetry/events integration tests."""

    def test_valid_batch_accepted(self, client, fresh_state):
        """Valid events with ptf_admin. prefix are accepted."""
        resp = client.post("/admin/telemetry/events", json={
            "events": [
                {"event": "ptf_admin.upsert_submit", "properties": {"period": "2025-01"}, "timestamp": "2025-01-15T10:00:00Z"},
                {"event": "ptf_admin.filter_change", "properties": {}, "timestamp": "2025-01-15T10:00:01Z"},
            ]
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["accepted_count"] == 2
        assert body["rejected_count"] == 0

    def test_empty_batch_returns_200(self, client, fresh_state):
        """Empty events array returns 200 with accepted_count: 0."""
        resp = client.post("/admin/telemetry/events", json={"events": []})
        assert resp.status_code == 200
        assert resp.json()["accepted_count"] == 0

    def test_unknown_prefix_rejected(self, client, fresh_state):
        """Events without ptf_admin. prefix are rejected."""
        resp = client.post("/admin/telemetry/events", json={
            "events": [
                {"event": "unknown.click", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
                {"event": "ptf_admin.valid", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
            ]
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted_count"] == 1
        assert body["rejected_count"] == 1

    def test_batch_over_100_rejected(self, client, fresh_state):
        """Batch exceeding 100 events returns 400."""
        events = [
            {"event": "ptf_admin.x", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"}
            for _ in range(101)
        ]
        resp = client.post("/admin/telemetry/events", json={"events": events})
        assert resp.status_code == 400

    def test_no_auth_required(self, client, fresh_state):
        """Endpoint works without any auth headers."""
        resp = client.post("/admin/telemetry/events", json={"events": []})
        assert resp.status_code == 200

    def test_prometheus_counter_incremented(self, client, fresh_state):
        """Accepted events increment ptf_admin_frontend_events_total."""
        metrics, _ = fresh_state
        client.post("/admin/telemetry/events", json={
            "events": [
                {"event": "ptf_admin.upsert_submit", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
                {"event": "ptf_admin.upsert_submit", "properties": {}, "timestamp": "2025-01-15T10:00:01Z"},
                {"event": "ptf_admin.filter_change", "properties": {}, "timestamp": "2025-01-15T10:00:02Z"},
            ]
        })
        output = metrics.generate_metrics().decode()
        assert 'ptf_admin_frontend_events_total{event_name="ptf_admin.upsert_submit"} 2.0' in output
        assert 'ptf_admin_frontend_events_total{event_name="ptf_admin.filter_change"} 1.0' in output

    def test_event_store_counters_updated(self, client, fresh_state):
        """EventStore counters reflect accepted/rejected counts."""
        _, store = fresh_state
        client.post("/admin/telemetry/events", json={
            "events": [
                {"event": "ptf_admin.a", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
                {"event": "bad.prefix", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
            ]
        })
        assert store.get_counters() == {"ptf_admin.a": 1}
        totals = store.get_totals()
        assert totals["accepted"] == 1
        assert totals["rejected"] == 1

    def test_rate_limit_429(self, client, fresh_state):
        """Exceeding 60 req/min returns 429."""
        from app.main import _rate_limit_buckets
        _rate_limit_buckets.clear()

        # Send 60 requests (should all pass)
        for _ in range(60):
            resp = client.post("/admin/telemetry/events", json={"events": []})
            assert resp.status_code == 200

        # 61st should be rate-limited
        resp = client.post("/admin/telemetry/events", json={"events": []})
        assert resp.status_code == 429

    def test_partial_batch_mixed_events(self, client, fresh_state):
        """Mix of valid and invalid events: valid accepted, invalid rejected."""
        resp = client.post("/admin/telemetry/events", json={
            "events": [
                {"event": "ptf_admin.a", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
                {"event": "ptf_admin.b", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
                {"event": "invalid.c", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
                {"event": "ptf_admin.a", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
                {"event": "nope.d", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
            ]
        })
        body = resp.json()
        assert body["accepted_count"] == 3
        assert body["rejected_count"] == 2
        assert body["accepted_count"] + body["rejected_count"] == 5


# ── Abuse hardening tests ─────────────────────────────────────────────────────

class TestEventNameValidation:
    """Event name validation: max length, charset, allowlist."""

    def test_name_too_long_rejected(self, client, fresh_state):
        """Event name exceeding 100 chars is rejected."""
        long_name = "ptf_admin." + "x" * 95  # 105 chars total
        resp = client.post("/admin/telemetry/events", json={
            "events": [
                {"event": long_name, "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
            ]
        })
        body = resp.json()
        assert body["accepted_count"] == 0
        assert body["rejected_count"] == 1

    def test_name_exactly_100_chars_accepted(self, client, fresh_state):
        """Event name at exactly 100 chars is accepted."""
        name = "ptf_admin." + "a" * 90  # exactly 100 chars
        resp = client.post("/admin/telemetry/events", json={
            "events": [
                {"event": name, "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
            ]
        })
        body = resp.json()
        assert body["accepted_count"] == 1
        assert body["rejected_count"] == 0

    def test_uppercase_rejected(self, client, fresh_state):
        """Uppercase chars in event name are rejected (ASCII slug only)."""
        resp = client.post("/admin/telemetry/events", json={
            "events": [
                {"event": "ptf_admin.Upsert_Submit", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
            ]
        })
        body = resp.json()
        assert body["accepted_count"] == 0
        assert body["rejected_count"] == 1

    def test_special_chars_rejected(self, client, fresh_state):
        """Special characters (spaces, dashes, unicode) in event name are rejected."""
        bad_names = [
            "ptf_admin.upsert submit",   # space
            "ptf_admin.upsert-submit",   # dash
            "ptf_admin.upsert@submit",   # @
            "ptf_admin.üpsert",          # unicode
        ]
        resp = client.post("/admin/telemetry/events", json={
            "events": [
                {"event": name, "properties": {}, "timestamp": "2025-01-15T10:00:00Z"}
                for name in bad_names
            ]
        })
        body = resp.json()
        assert body["accepted_count"] == 0
        assert body["rejected_count"] == len(bad_names)

    def test_valid_slug_chars_accepted(self, client, fresh_state):
        """Valid ASCII slug chars (lowercase, digits, dot, underscore) are accepted."""
        valid_names = [
            "ptf_admin.upsert_submit",
            "ptf_admin.filter_change",
            "ptf_admin.bulk_import_start",
            "ptf_admin.v2.new_event",
            "ptf_admin.event123",
        ]
        resp = client.post("/admin/telemetry/events", json={
            "events": [
                {"event": name, "properties": {}, "timestamp": "2025-01-15T10:00:00Z"}
                for name in valid_names
            ]
        })
        body = resp.json()
        assert body["accepted_count"] == len(valid_names)
        assert body["rejected_count"] == 0


class TestPropertiesValidation:
    """Properties dict abuse prevention."""

    def test_too_many_properties_rejected(self, client, fresh_state):
        """Event with >20 properties keys is rejected."""
        bloated_props = {f"key_{i}": f"val_{i}" for i in range(25)}
        resp = client.post("/admin/telemetry/events", json={
            "events": [
                {"event": "ptf_admin.test", "properties": bloated_props, "timestamp": "2025-01-15T10:00:00Z"},
            ]
        })
        body = resp.json()
        assert body["accepted_count"] == 0
        assert body["rejected_count"] == 1

    def test_20_properties_accepted(self, client, fresh_state):
        """Event with exactly 20 properties keys is accepted."""
        props = {f"key_{i}": f"val_{i}" for i in range(20)}
        resp = client.post("/admin/telemetry/events", json={
            "events": [
                {"event": "ptf_admin.test", "properties": props, "timestamp": "2025-01-15T10:00:00Z"},
            ]
        })
        body = resp.json()
        assert body["accepted_count"] == 1
        assert body["rejected_count"] == 0

    def test_empty_properties_accepted(self, client, fresh_state):
        """Event with empty properties is accepted."""
        resp = client.post("/admin/telemetry/events", json={
            "events": [
                {"event": "ptf_admin.test", "properties": {}, "timestamp": "2025-01-15T10:00:00Z"},
            ]
        })
        body = resp.json()
        assert body["accepted_count"] == 1
