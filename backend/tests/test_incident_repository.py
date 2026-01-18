"""
Incident Repository Tests - Sprint 6.1

Upsert + dedupe + status transition testleri.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from backend.app.incident_repository import (
    get_epoch_day,
    can_transition_status,
    upsert_incident,
    STATUS_PRIORITY,
)
from backend.app.action_router import RoutedAction


class TestEpochDay:
    """Epoch-day hesaplama testleri"""
    
    def test_epoch_day_calculation(self):
        """Epoch-day doğru hesaplanmalı"""
        # 2025-01-15 00:00:00 UTC
        dt = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        epoch_day = get_epoch_day(dt)
        
        # 2025-01-15 = 20103 days since epoch (approximately)
        assert epoch_day > 20000
        assert epoch_day < 21000
    
    def test_same_day_same_bucket(self):
        """Aynı gün içinde aynı bucket"""
        dt1 = datetime(2025, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        dt2 = datetime(2025, 1, 15, 23, 59, 59, tzinfo=timezone.utc)
        
        assert get_epoch_day(dt1) == get_epoch_day(dt2)
    
    def test_different_day_different_bucket(self):
        """Farklı gün farklı bucket"""
        dt1 = datetime(2025, 1, 15, 23, 59, 59, tzinfo=timezone.utc)
        dt2 = datetime(2025, 1, 16, 0, 0, 0, tzinfo=timezone.utc)
        
        assert get_epoch_day(dt1) != get_epoch_day(dt2)
        assert get_epoch_day(dt2) == get_epoch_day(dt1) + 1


class TestStatusTransition:
    """Status transition testleri"""
    
    def test_open_to_any_allowed(self):
        """OPEN'dan her yere geçiş OK"""
        assert can_transition_status("OPEN", "ACK") is True
        assert can_transition_status("OPEN", "RESOLVED") is True
        assert can_transition_status("OPEN", "AUTO_RESOLVED") is True
        assert can_transition_status("OPEN", "PENDING_RETRY") is True
        assert can_transition_status("OPEN", "REPORTED") is True
    
    def test_ack_to_resolved_allowed(self):
        """ACK → RESOLVED OK"""
        assert can_transition_status("ACK", "RESOLVED") is True
    
    def test_resolved_to_open_not_allowed(self):
        """RESOLVED → OPEN engellenmeli"""
        assert can_transition_status("RESOLVED", "OPEN") is False
    
    def test_ack_to_open_not_allowed(self):
        """ACK → OPEN engellenmeli"""
        assert can_transition_status("ACK", "OPEN") is False
    
    def test_auto_resolved_to_open_allowed(self):
        """AUTO_RESOLVED → OPEN OK (en düşük priority)"""
        assert can_transition_status("AUTO_RESOLVED", "OPEN") is True
    
    def test_reported_to_resolved_allowed(self):
        """REPORTED → RESOLVED OK"""
        assert can_transition_status("REPORTED", "RESOLVED") is True
    
    def test_pending_retry_to_open_not_allowed(self):
        """PENDING_RETRY → OPEN engellenmeli"""
        assert can_transition_status("PENDING_RETRY", "OPEN") is False
    
    def test_same_status_allowed(self):
        """Aynı status'a geçiş OK"""
        for status in STATUS_PRIORITY.keys():
            assert can_transition_status(status, status) is True


class TestUpsertIncident:
    """Upsert incident testleri"""
    
    @pytest.fixture
    def mock_db(self):
        """Mock database session"""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        return db
    
    @pytest.fixture
    def base_params(self):
        """Base upsert parameters"""
        return {
            "trace_id": "test-trace-001",
            "tenant_id": "default",
            "provider": "ck_bogazici",
            "invoice_id": "INV001",
            "period": "2025-01",
            "primary_flag": "CALC_BUG",
            "category": "CALC_BUG",
            "severity": "S1",
            "message": "Test incident",
            "action_type": "BUG_REPORT",
            "action_owner": "calc",
            "action_code": "ENGINE_REGRESSION",
            "all_flags": ["CALC_BUG"],
            "secondary_flags": [],
            "deduction_total": 50,
            "routed_action": RoutedAction(
                action_type="BUG_REPORT",
                status="REPORTED",
                payload={"issue": {"title": "Test"}},
            ),
        }
    
    def test_insert_new_incident(self, mock_db, base_params):
        """Yeni incident INSERT edilmeli"""
        now = datetime(2025, 1, 15, 10, 0, 0)
        
        incident_id, is_new = upsert_incident(db=mock_db, now=now, **base_params)
        
        # db.add çağrılmalı
        assert mock_db.add.called
        assert mock_db.commit.called
        assert is_new is True
    
    def test_dedupe_hit_updates_occurrence(self, mock_db, base_params):
        """Dedupe hit → occurrence_count artmalı"""
        # Mevcut incident simüle et
        existing = MagicMock()
        existing.id = 42
        existing.occurrence_count = 3
        existing.status = "REPORTED"
        existing.details_json = {}
        mock_db.query.return_value.filter.return_value.first.return_value = existing
        
        now = datetime(2025, 1, 15, 10, 0, 0)
        incident_id, is_new = upsert_incident(db=mock_db, now=now, **base_params)
        
        assert incident_id == 42
        assert is_new is False
        assert existing.occurrence_count == 4
        assert existing.last_seen_at == now
    
    def test_dedupe_hit_does_not_downgrade_status(self, mock_db, base_params):
        """Dedupe hit → status downgrade engellenmeli"""
        # ACK status'unda mevcut incident
        existing = MagicMock()
        existing.id = 42
        existing.occurrence_count = 1
        existing.status = "ACK"
        existing.details_json = {}
        mock_db.query.return_value.filter.return_value.first.return_value = existing
        
        # OPEN status ile gelen yeni incident
        params = {**base_params}
        params["routed_action"] = RoutedAction(
            action_type="USER_FIX",
            status="OPEN",
            payload=None,
        )
        
        now = datetime(2025, 1, 15, 10, 0, 0)
        upsert_incident(db=mock_db, now=now, **params)
        
        # Status ACK kalmalı (OPEN'a düşmemeli)
        assert existing.status == "ACK"
    
    def test_dedupe_hit_upgrades_status(self, mock_db, base_params):
        """Dedupe hit → status upgrade OK"""
        # OPEN status'unda mevcut incident
        existing = MagicMock()
        existing.id = 42
        existing.occurrence_count = 1
        existing.status = "OPEN"
        existing.details_json = {}
        mock_db.query.return_value.filter.return_value.first.return_value = existing
        
        # REPORTED status ile gelen yeni incident
        now = datetime(2025, 1, 15, 10, 0, 0)
        upsert_incident(db=mock_db, now=now, **base_params)
        
        # Status REPORTED olmalı
        assert existing.status == "REPORTED"
    
    def test_routed_payload_always_overwritten(self, mock_db, base_params):
        """USER_FIX/RETRY_LOOKUP payload overwrite edilmeli"""
        existing = MagicMock()
        existing.id = 42
        existing.occurrence_count = 1
        existing.status = "OPEN"
        existing.routed_payload = {"old": "payload"}
        existing.details_json = {}
        mock_db.query.return_value.filter.return_value.first.return_value = existing
        
        # USER_FIX ile gelen yeni incident
        params = {**base_params}
        params["action_type"] = "USER_FIX"
        params["routed_action"] = RoutedAction(
            action_type="USER_FIX",
            status="OPEN",
            payload={"ui_alert": {"message": "New message", "code": "TEST"}},
        )
        
        now = datetime(2025, 1, 15, 10, 0, 0)
        upsert_incident(db=mock_db, now=now, **params)
        
        # Yeni payload set edilmeli (USER_FIX overwrite OK)
        assert existing.routed_payload is not None
        assert "ui_alert" in str(existing.routed_payload)
    
    def test_bug_report_payload_not_overwritten(self, mock_db, base_params):
        """BUG_REPORT payload ASLA overwrite edilmemeli"""
        # Mevcut BUG_REPORT incident with rich payload
        existing = MagicMock()
        existing.id = 42
        existing.occurrence_count = 1
        existing.status = "REPORTED"
        existing.routed_payload = {
            "issue": {
                "title": "[CALC_BUG] Original",
                "normalized_inputs": {"consumption_kwh": 15000, "distribution_total_tl": 0},
            }
        }
        existing.details_json = {}
        mock_db.query.return_value.filter.return_value.first.return_value = existing
        
        # Yeni BUG_REPORT conflict - daha az bilgi ile
        params = {**base_params}
        params["routed_action"] = RoutedAction(
            action_type="BUG_REPORT",
            status="REPORTED",
            payload={"issue": {"title": "[CALC_BUG] New - less info"}},
        )
        
        now = datetime(2025, 1, 15, 10, 0, 0)
        upsert_incident(db=mock_db, now=now, **params)
        
        # Orijinal payload korunmalı
        assert "Original" in str(existing.routed_payload)
        assert "consumption_kwh" in str(existing.routed_payload)
    
    def test_bug_report_payload_set_if_empty(self, mock_db, base_params):
        """BUG_REPORT payload boşsa set edilmeli"""
        existing = MagicMock()
        existing.id = 42
        existing.occurrence_count = 1
        existing.status = "REPORTED"
        existing.routed_payload = None  # Boş
        existing.details_json = {}
        mock_db.query.return_value.filter.return_value.first.return_value = existing
        
        now = datetime(2025, 1, 15, 10, 0, 0)
        upsert_incident(db=mock_db, now=now, **base_params)
        
        # Payload set edilmeli (ilk kez)
        assert existing.routed_payload is not None
    
    def test_different_bucket_creates_new_incident(self, mock_db, base_params):
        """Farklı bucket → yeni incident"""
        # İlk gün
        now1 = datetime(2025, 1, 15, 10, 0, 0)
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        incident_id1, is_new1 = upsert_incident(db=mock_db, now=now1, **base_params)
        assert is_new1 is True
        
        # Ertesi gün - farklı bucket
        now2 = datetime(2025, 1, 16, 10, 0, 0)
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        incident_id2, is_new2 = upsert_incident(db=mock_db, now=now2, **base_params)
        assert is_new2 is True
    
    def test_auto_resolved_stays_auto_resolved_on_conflict(self, mock_db, base_params):
        """AUTO_RESOLVED conflict'te AUTO_RESOLVED kalmalı"""
        existing = MagicMock()
        existing.id = 42
        existing.occurrence_count = 1
        existing.status = "AUTO_RESOLVED"
        existing.details_json = {}
        mock_db.query.return_value.filter.return_value.first.return_value = existing
        
        # AUTO_RESOLVED ile gelen yeni incident
        params = {**base_params}
        params["routed_action"] = RoutedAction(
            action_type="FALLBACK_OK",
            status="AUTO_RESOLVED",
            payload=None,
        )
        
        now = datetime(2025, 1, 15, 10, 0, 0)
        upsert_incident(db=mock_db, now=now, **params)
        
        # Status AUTO_RESOLVED kalmalı
        assert existing.status == "AUTO_RESOLVED"


class TestStatusPriority:
    """Status priority testleri"""
    
    def test_resolved_highest_priority(self):
        """RESOLVED en yüksek priority"""
        assert STATUS_PRIORITY["RESOLVED"] > STATUS_PRIORITY["ACK"]
        assert STATUS_PRIORITY["RESOLVED"] > STATUS_PRIORITY["REPORTED"]
        assert STATUS_PRIORITY["RESOLVED"] > STATUS_PRIORITY["OPEN"]
    
    def test_auto_resolved_lowest_priority(self):
        """AUTO_RESOLVED en düşük priority"""
        assert STATUS_PRIORITY["AUTO_RESOLVED"] < STATUS_PRIORITY["OPEN"]
        assert STATUS_PRIORITY["AUTO_RESOLVED"] < STATUS_PRIORITY["PENDING_RETRY"]
    
    def test_ack_higher_than_reported(self):
        """ACK > REPORTED"""
        assert STATUS_PRIORITY["ACK"] > STATUS_PRIORITY["REPORTED"]
