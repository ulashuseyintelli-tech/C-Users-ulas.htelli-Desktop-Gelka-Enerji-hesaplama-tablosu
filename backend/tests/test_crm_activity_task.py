"""
S2 — Activity & Task Engine — odaklı test suite (WB-2/3/4: Activity, Task,
Today).

Desen: in-memory SQLite + get_db override (bkz. test_contracts.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

_TR_TZ = ZoneInfo("Europe/Istanbul")


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    import app.pricing.schemas  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture()
def storage_tmp(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.storage import clear_storage_cache

    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    clear_storage_cache()
    yield tmp_path
    clear_storage_cache()


@pytest.fixture()
def client(db, storage_tmp):
    from app.main import app as fastapi_app
    from app.database import get_db

    fastapi_app.dependency_overrides[get_db] = lambda: db
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def _make_customer(db, name="Test Firma A.Ş."):
    from app.database import Customer
    c = Customer(name=name)
    db.add(c)
    db.flush()
    return c


def _make_offer(db, customer_id, status="draft", tenant_id="default"):
    from app.database import Offer
    o = Offer(
        tenant_id=tenant_id, customer_id=customer_id, vendor="TEST", consumption_kwh=1000.0,
        current_unit_price=2.0, weighted_ptf=2.0, yekdem=0.1, agreement_multiplier=1.01,
        current_total=2000.0, offer_total=1900.0, savings_amount=100.0, savings_ratio=0.05,
        status=status,
    )
    db.add(o)
    db.flush()
    return o


def _make_contract(db, offer_id, customer_id=None, status="DRAFT", tenant_id="default"):
    from app.database import Contract
    c = Contract(offer_id=offer_id, customer_id=customer_id, status=status, tenant_id=tenant_id)
    db.add(c)
    db.flush()
    return c


def _future_naive_utc(days: int, hour: int = 10) -> str:
    """Europe/Istanbul'a göre 'bugün+days, saat=hour' -> naive UTC ISO string (API request body için)."""
    now_tr = datetime.now(_TR_TZ)
    target_tr = (now_tr + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    target_utc = target_tr.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return target_utc.isoformat()


class TestActivityCreate:
    def test_create_note_for_customer(self, db, client):
        c = _make_customer(db)
        db.commit()
        resp = client.post("/crm/activities", json={"customer_id": c.id, "activity_type": "NOTE", "title": "Not", "body": "Aradım, fiyat çalışması konuşuldu"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["activity_type"] == "NOTE"
        assert body["source"] == "manual"

    def test_create_call_for_offer(self, db, client):
        c = _make_customer(db)
        db.flush()
        o = _make_offer(db, c.id)
        db.commit()
        resp = client.post("/crm/activities", json={"offer_id": o.id, "activity_type": "CALL", "title": "Arandı"})
        assert resp.status_code == 200
        assert resp.json()["offer_id"] == o.id

    def test_create_email_for_contract(self, db, client):
        c = _make_customer(db)
        db.flush()
        o = _make_offer(db, c.id)
        db.flush()
        ct = _make_contract(db, o.id, customer_id=c.id)
        db.commit()
        resp = client.post("/crm/activities", json={"contract_id": ct.id, "activity_type": "EMAIL", "title": "E-posta gönderildi"})
        assert resp.status_code == 200
        assert resp.json()["contract_id"] == ct.id

    def test_create_meeting(self, db, client):
        c = _make_customer(db)
        db.commit()
        resp = client.post("/crm/activities", json={"customer_id": c.id, "activity_type": "MEETING", "title": "Toplantı yapıldı"})
        assert resp.status_code == 200
        assert resp.json()["activity_type"] == "MEETING"

    def test_cannot_create_task_completed_activity_directly(self, db, client):
        """TASK_COMPLETED yalnız sistem tarafından üretilir, kullanıcı doğrudan oluşturamaz."""
        c = _make_customer(db)
        db.commit()
        resp = client.post("/crm/activities", json={"customer_id": c.id, "activity_type": "TASK_COMPLETED", "title": "x"})
        assert resp.status_code == 422

    def test_no_subject_rejected(self, db, client):
        resp = client.post("/crm/activities", json={"activity_type": "NOTE", "title": "x"})
        assert resp.status_code == 422

    def test_two_subjects_rejected(self, db, client):
        c = _make_customer(db)
        db.flush()
        o = _make_offer(db, c.id)
        db.commit()
        resp = client.post("/crm/activities", json={"customer_id": c.id, "offer_id": o.id, "activity_type": "NOTE", "title": "x"})
        assert resp.status_code == 422

    def test_invalid_subject_fails_closed(self, db, client):
        resp = client.post("/crm/activities", json={"customer_id": 99999, "activity_type": "NOTE", "title": "x"})
        assert resp.status_code == 404


class TestActivityList:
    def test_chronological_ordering_newest_first(self, db, client):
        c = _make_customer(db)
        db.commit()
        for i in range(3):
            resp = client.post("/crm/activities", json={"customer_id": c.id, "activity_type": "NOTE", "title": f"Not {i}"})
            assert resp.status_code == 200

        resp = client.get("/crm/activities", params={"customer_id": c.id})
        body = resp.json()
        assert len(body) == 3
        assert body[0]["title"] == "Not 2"
        assert body[2]["title"] == "Not 0"

    def test_pagination(self, db, client):
        c = _make_customer(db)
        db.commit()
        for i in range(5):
            client.post("/crm/activities", json={"customer_id": c.id, "activity_type": "NOTE", "title": f"N{i}"})

        resp = client.get("/crm/activities", params={"customer_id": c.id, "skip": 2, "limit": 2})
        assert len(resp.json()) == 2

    def test_customer_activity_scoped(self, db, client):
        c1 = _make_customer(db, "Firma 1")
        db.flush()
        c2 = _make_customer(db, "Firma 2")
        db.commit()
        client.post("/crm/activities", json={"customer_id": c1.id, "activity_type": "NOTE", "title": "C1 not"})
        client.post("/crm/activities", json={"customer_id": c2.id, "activity_type": "NOTE", "title": "C2 not"})

        resp = client.get("/crm/activities", params={"customer_id": c1.id})
        body = resp.json()
        assert len(body) == 1
        assert body[0]["title"] == "C1 not"

    def test_offer_activity_includes_audit_status_change(self, db, client):
        """Offer subject'inde audit_logs'taki OFFER_STATUS_CHANGED, activities ile birleşik döner (duplicate yazılmaz)."""
        c = _make_customer(db)
        db.flush()
        o = _make_offer(db, c.id, status="draft")
        db.commit()

        client.post("/crm/activities", json={"offer_id": o.id, "activity_type": "NOTE", "title": "manuel not"})
        status_resp = client.put(f"/offers/{o.id}/status", params={"status": "sent"})
        assert status_resp.status_code == 200

        resp = client.get("/crm/activities", params={"offer_id": o.id})
        body = resp.json()
        sources = {a["source"] for a in body}
        assert "manual" in sources
        assert "audit" in sources
        assert len(body) == 2  # 1 manuel + 1 audit projection — activities tablosuna DUPLICATE yazılmadı

    def test_contract_activity_scoped(self, db, client):
        c = _make_customer(db)
        db.flush()
        o = _make_offer(db, c.id)
        db.flush()
        ct = _make_contract(db, o.id, customer_id=c.id)
        db.commit()
        client.post("/crm/activities", json={"contract_id": ct.id, "activity_type": "NOTE", "title": "sözleşme notu"})

        resp = client.get("/crm/activities", params={"contract_id": ct.id})
        assert len(resp.json()) == 1


class TestTaskLifecycle:
    def test_create_task(self, db, client):
        c = _make_customer(db)
        db.commit()
        resp = client.post("/crm/tasks", json={"customer_id": c.id, "title": "Cuma ara", "due_at": _future_naive_utc(2)})
        assert resp.status_code == 200
        assert resp.json()["status"] == "OPEN"

    def test_complete_task(self, db, client):
        c = _make_customer(db)
        db.commit()
        task = client.post("/crm/tasks", json={"customer_id": c.id, "title": "T1"}).json()

        resp = client.post(f"/crm/tasks/{task['id']}/complete")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "COMPLETED"
        assert body["completed_at"] is not None

    def test_complete_twice_idempotent(self, db, client):
        c = _make_customer(db)
        db.commit()
        task = client.post("/crm/tasks", json={"customer_id": c.id, "title": "T1"}).json()

        r1 = client.post(f"/crm/tasks/{task['id']}/complete").json()
        r2 = client.post(f"/crm/tasks/{task['id']}/complete").json()
        assert r1["completed_at"] == r2["completed_at"]  # completed_at DEĞİŞMEDİ

        # duplicate TASK_COMPLETED Activity üretilmedi
        activities = client.get("/crm/activities", params={"customer_id": c.id}).json()
        completed_activities = [a for a in activities if a["activity_type"] == "TASK_COMPLETED"]
        assert len(completed_activities) == 1

    def test_cancel_task(self, db, client):
        c = _make_customer(db)
        db.commit()
        task = client.post("/crm/tasks", json={"customer_id": c.id, "title": "T1"}).json()

        resp = client.post(f"/crm/tasks/{task['id']}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "CANCELLED"

    def test_cannot_complete_cancelled_task(self, db, client):
        c = _make_customer(db)
        db.commit()
        task = client.post("/crm/tasks", json={"customer_id": c.id, "title": "T1"}).json()
        client.post(f"/crm/tasks/{task['id']}/cancel")

        resp = client.post(f"/crm/tasks/{task['id']}/complete")
        assert resp.status_code == 409

    def test_cannot_cancel_completed_task(self, db, client):
        c = _make_customer(db)
        db.commit()
        task = client.post("/crm/tasks", json={"customer_id": c.id, "title": "T1"}).json()
        client.post(f"/crm/tasks/{task['id']}/complete")

        resp = client.post(f"/crm/tasks/{task['id']}/cancel")
        assert resp.status_code == 409

    def test_status_filter(self, db, client):
        c = _make_customer(db)
        db.commit()
        t1 = client.post("/crm/tasks", json={"customer_id": c.id, "title": "Open"}).json()
        t2 = client.post("/crm/tasks", json={"customer_id": c.id, "title": "Done"}).json()
        client.post(f"/crm/tasks/{t2['id']}/complete")

        resp = client.get("/crm/tasks", params={"customer_id": c.id, "status": "OPEN"})
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == t1["id"]

    def test_subject_filter(self, db, client):
        c1 = _make_customer(db, "F1")
        db.flush()
        c2 = _make_customer(db, "F2")
        db.commit()
        client.post("/crm/tasks", json={"customer_id": c1.id, "title": "C1 task"})
        client.post("/crm/tasks", json={"customer_id": c2.id, "title": "C2 task"})

        resp = client.get("/crm/tasks", params={"customer_id": c1.id})
        assert len(resp.json()) == 1

    def test_invalid_subject_rejected(self, db, client):
        resp = client.post("/crm/tasks", json={"customer_id": 99999, "title": "x"})
        assert resp.status_code == 404

    def test_update_editable_fields(self, db, client):
        c = _make_customer(db)
        db.commit()
        task = client.post("/crm/tasks", json={"customer_id": c.id, "title": "Eski başlık"}).json()

        resp = client.patch(f"/crm/tasks/{task['id']}", json={"title": "Yeni başlık"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "Yeni başlık"

    def test_cannot_edit_completed_task(self, db, client):
        c = _make_customer(db)
        db.commit()
        task = client.post("/crm/tasks", json={"customer_id": c.id, "title": "T1"}).json()
        client.post(f"/crm/tasks/{task['id']}/complete")

        resp = client.patch(f"/crm/tasks/{task['id']}", json={"title": "Değiştirilemez"})
        assert resp.status_code == 409


class TestTaskDueDates:
    def test_due_today_projection(self, db, client):
        c = _make_customer(db)
        db.commit()
        client.post("/crm/tasks", json={"customer_id": c.id, "title": "Bugün", "due_at": _future_naive_utc(0)})
        client.post("/crm/tasks", json={"customer_id": c.id, "title": "Yarın", "due_at": _future_naive_utc(1)})

        resp = client.get("/crm/tasks", params={"customer_id": c.id, "due_today": True})
        body = resp.json()
        assert len(body) == 1
        assert body[0]["title"] == "Bugün"

    def test_overdue_projection(self, db, client):
        c = _make_customer(db)
        db.commit()
        client.post("/crm/tasks", json={"customer_id": c.id, "title": "Geçmiş", "due_at": _future_naive_utc(-2)})
        client.post("/crm/tasks", json={"customer_id": c.id, "title": "Bugün", "due_at": _future_naive_utc(0)})

        resp = client.get("/crm/tasks", params={"customer_id": c.id, "overdue": True})
        body = resp.json()
        assert len(body) == 1
        assert body[0]["title"] == "Geçmiş"

    def test_future_task_not_in_due_today_or_overdue(self, db, client):
        c = _make_customer(db)
        db.commit()
        client.post("/crm/tasks", json={"customer_id": c.id, "title": "Gelecek", "due_at": _future_naive_utc(5)})

        due_today = client.get("/crm/tasks", params={"customer_id": c.id, "due_today": True}).json()
        overdue = client.get("/crm/tasks", params={"customer_id": c.id, "overdue": True}).json()
        assert due_today == []
        assert overdue == []

    def test_completed_task_hidden_from_overdue(self, db, client):
        c = _make_customer(db)
        db.commit()
        task = client.post("/crm/tasks", json={"customer_id": c.id, "title": "Geçmiş ama tamam", "due_at": _future_naive_utc(-3)}).json()
        client.post(f"/crm/tasks/{task['id']}/complete")

        resp = client.get("/crm/tasks", params={"customer_id": c.id, "overdue": True})
        assert resp.json() == []


class TestToday:
    def test_due_today_and_overdue_counts(self, db, client):
        c = _make_customer(db)
        db.commit()
        client.post("/crm/tasks", json={"customer_id": c.id, "title": "Bugün1", "due_at": _future_naive_utc(0)})
        client.post("/crm/tasks", json={"customer_id": c.id, "title": "Bugün2", "due_at": _future_naive_utc(0, hour=18)})
        client.post("/crm/tasks", json={"customer_id": c.id, "title": "Geçmiş", "due_at": _future_naive_utc(-1)})

        resp = client.get("/crm/today")
        assert resp.status_code == 200
        body = resp.json()
        assert body["due_today_count"] == 2
        assert body["overdue_count"] == 1

    def test_completed_hidden_from_today(self, db, client):
        c = _make_customer(db)
        db.commit()
        task = client.post("/crm/tasks", json={"customer_id": c.id, "title": "Bugün", "due_at": _future_naive_utc(0)}).json()
        client.post(f"/crm/tasks/{task['id']}/complete")

        resp = client.get("/crm/today")
        assert resp.json()["due_today_count"] == 0

    def test_recent_activities_present(self, db, client):
        c = _make_customer(db)
        db.commit()
        client.post("/crm/activities", json={"customer_id": c.id, "activity_type": "NOTE", "title": "son not"})

        resp = client.get("/crm/today")
        body = resp.json()
        assert len(body["recent_activities"]) == 1

    def test_s1_summary_metrics_present(self, db, client):
        resp = client.get("/crm/today")
        body = resp.json()
        assert "total_customers" in body
        assert "total_open_offers" in body
        assert "total_finalized_contracts" in body


class TestSecurityIntegrity:
    def test_other_tenant_offer_not_valid_subject(self, db, client):
        """SINGLE GELKA TENANT: farklı tenant'ın offer'ına activity/task oluşturulamaz."""
        c = _make_customer(db)
        db.flush()
        o = _make_offer(db, c.id, tenant_id="other-tenant")
        db.commit()

        resp = client.post("/crm/activities", json={"offer_id": o.id, "activity_type": "NOTE", "title": "x"})
        assert resp.status_code == 404

    def test_no_cross_subject_leakage(self, db, client):
        c1 = _make_customer(db, "F1")
        db.flush()
        c2 = _make_customer(db, "F2")
        db.commit()
        client.post("/crm/tasks", json={"customer_id": c1.id, "title": "C1 task"})
        client.post("/crm/tasks", json={"customer_id": c2.id, "title": "C2 task"})

        resp = client.get("/crm/tasks", params={"customer_id": c2.id})
        body = resp.json()
        assert all(t["customer_id"] == c2.id for t in body)
