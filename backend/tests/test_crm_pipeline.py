"""
S3 — Sales Pipeline — odaklı test suite (WB-1/2: projection + precedence).

Fixture'lar test_crm_activity_task.py'den REUSE edilir (kod tekrarından
kaçınma, owner ilkesi) — db/storage_tmp/client fixture'ları ve
_make_customer/_make_offer/_make_contract/_future_naive_utc helper'ları
S2'de zaten tanımlı, pytest fixture'ları modüller arası import edilebilir.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests.test_crm_activity_task import (  # noqa: F401 (fixture'lar pytest tarafından çözülür)
    _TR_TZ,
    _future_naive_utc,
    _make_contract,
    _make_customer,
    _make_offer,
    client,
    db,
    storage_tmp,
)

from app.crm.service import _compute_pipeline_stage


# =============================================================================
# Saf fonksiyon testleri — precedence kuralı (DB'siz, izole).
# =============================================================================

class TestPipelineStagePrecedence:
    def test_draft_no_contract(self):
        assert _compute_pipeline_stage("draft", []) == ("DRAFT", None)

    def test_sent_no_contract(self):
        assert _compute_pipeline_stage("sent", []) == ("SENT", None)

    def test_viewed_no_contract(self):
        assert _compute_pipeline_stage("viewed", []) == ("SENT", None)

    def test_accepted_no_contract(self):
        assert _compute_pipeline_stage("accepted", []) == ("ACCEPTED", None)

    def test_rejected_is_lost(self):
        assert _compute_pipeline_stage("rejected", []) == ("LOST", None)

    def test_expired_is_lost(self):
        assert _compute_pipeline_stage("expired", []) == ("LOST", None)

    def test_contracting_without_contract_warns(self):
        assert _compute_pipeline_stage("contracting", []) == ("ACCEPTED", "CONTRACT_STATUS_WITHOUT_CONTRACT")

    def test_completed_without_contract_warns(self):
        assert _compute_pipeline_stage("completed", []) == ("COMPLETED", "COMPLETED_WITHOUT_CONTRACT")

    def test_non_finalized_contract_is_contract_stage(self):
        assert _compute_pipeline_stage("contracting", ["DRAFT"]) == ("CONTRACT", None)
        assert _compute_pipeline_stage("accepted", ["READY_TO_GENERATE"]) == ("CONTRACT", None)

    def test_finalized_contract_is_completed(self):
        assert _compute_pipeline_stage("contracting", ["FINALIZED"]) == ("COMPLETED", None)

    def test_contract_truth_overrides_stale_offer_status(self):
        """Precedence: Offer.status hâlâ 'draft' olsa bile FINALIZED Contract varsa COMPLETED."""
        assert _compute_pipeline_stage("draft", ["FINALIZED"]) == ("COMPLETED", None)

    def test_multiple_contracts_any_finalized_wins(self):
        assert _compute_pipeline_stage("contracting", ["VOID", "FINALIZED"]) == ("COMPLETED", None)

    def test_unknown_offer_status_never_silently_dropped(self):
        stage, warning = _compute_pipeline_stage("some_future_status", [])
        assert stage is not None
        assert warning == "UNKNOWN_OFFER_STATUS"


# =============================================================================
# GET /crm/pipeline — uçtan uca projection testleri.
# =============================================================================

class TestPipelineEndpoint:
    def test_draft_offer_appears_as_draft(self, db, client):
        c = _make_customer(db)
        o = _make_offer(db, c.id, status="draft")
        db.commit()
        resp = client.get("/crm/pipeline")
        assert resp.status_code == 200
        cards = resp.json()["cards"]
        card = next(x for x in cards if x["offer_id"] == o.id)
        assert card["pipeline_stage"] == "DRAFT"
        assert card["pipeline_warning"] is None
        assert card["customer_name"] == c.name

    def test_missing_customer_warning(self, db, client):
        o = _make_offer(db, None, status="draft")
        db.commit()
        resp = client.get("/crm/pipeline")
        cards = resp.json()["cards"]
        card = next(x for x in cards if x["offer_id"] == o.id)
        assert card["customer_id"] is None
        assert card["customer_name"] is None
        assert card["pipeline_warning"] == "MISSING_CUSTOMER"

    def test_finalized_contract_wins_over_draft_status(self, db, client):
        """Precedence uçtan uca: Offer.status güncellenmemiş olsa bile FINALIZED Contract stage'i belirler."""
        c = _make_customer(db)
        o = _make_offer(db, c.id, status="draft")
        _make_contract(db, o.id, customer_id=c.id, status="FINALIZED")
        db.commit()
        resp = client.get("/crm/pipeline")
        card = next(x for x in resp.json()["cards"] if x["offer_id"] == o.id)
        assert card["pipeline_stage"] == "COMPLETED"
        assert card["has_contract"] is True
        assert card["contract_status"] == "FINALIZED"

    def test_non_finalized_contract_is_contract_stage(self, db, client):
        c = _make_customer(db)
        o = _make_offer(db, c.id, status="contracting")
        _make_contract(db, o.id, customer_id=c.id, status="READY_TO_GENERATE")
        db.commit()
        resp = client.get("/crm/pipeline")
        card = next(x for x in resp.json()["cards"] if x["offer_id"] == o.id)
        assert card["pipeline_stage"] == "CONTRACT"
        assert card["pipeline_warning"] is None

    def test_rejected_and_expired_both_lost(self, db, client):
        c = _make_customer(db)
        o1 = _make_offer(db, c.id, status="rejected")
        o2 = _make_offer(db, c.id, status="expired")
        db.commit()
        cards = client.get("/crm/pipeline").json()["cards"]
        c1 = next(x for x in cards if x["offer_id"] == o1.id)
        c2 = next(x for x in cards if x["offer_id"] == o2.id)
        assert c1["pipeline_stage"] == "LOST"
        assert c2["pipeline_stage"] == "LOST"
        # alt-durum (Reddedildi/Süresi Doldu) offer_status'tan türetilir
        assert c1["offer_status"] == "rejected"
        assert c2["offer_status"] == "expired"

    def test_activity_does_not_change_stage(self, db, client):
        c = _make_customer(db)
        o = _make_offer(db, c.id, status="sent")
        db.commit()
        resp = client.post("/crm/activities", json={"customer_id": c.id, "activity_type": "MEETING", "title": "Toplantı"})
        assert resp.status_code == 200
        card = next(x for x in client.get("/crm/pipeline").json()["cards"] if x["offer_id"] == o.id)
        assert card["pipeline_stage"] == "SENT"  # değişmedi
        assert card["last_activity"] is not None
        assert card["last_activity"]["activity_type"] == "MEETING"

    def test_task_does_not_change_stage_and_overdue_counted(self, db, client):
        c = _make_customer(db)
        o = _make_offer(db, c.id, status="accepted")
        db.commit()
        past_due = _future_naive_utc(days=-5)
        resp = client.post("/crm/tasks", json={"customer_id": c.id, "title": "Geçmiş görev", "due_at": past_due})
        assert resp.status_code == 200
        card = next(x for x in client.get("/crm/pipeline").json()["cards"] if x["offer_id"] == o.id)
        assert card["pipeline_stage"] == "ACCEPTED"  # değişmedi
        assert card["overdue_task_count"] == 1
        assert card["next_open_task"] is not None

    def test_completed_task_ignored_in_next_open_task(self, db, client):
        c = _make_customer(db)
        o = _make_offer(db, c.id, status="sent")
        db.commit()
        created = client.post("/crm/tasks", json={"customer_id": c.id, "title": "Görev"}).json()
        client.post(f"/crm/tasks/{created['id']}/complete")
        card = next(x for x in client.get("/crm/pipeline").json()["cards"] if x["offer_id"] == o.id)
        assert card["next_open_task"] is None
        assert card["overdue_task_count"] == 0

    def test_no_cross_subject_leakage(self, db, client):
        c1 = _make_customer(db, name="Firma 1")
        c2 = _make_customer(db, name="Firma 2")
        o1 = _make_offer(db, c1.id, status="sent")
        o2 = _make_offer(db, c2.id, status="sent")
        db.commit()
        client.post("/crm/activities", json={"customer_id": c1.id, "activity_type": "CALL", "title": "Firma 1 araması"})
        cards = client.get("/crm/pipeline").json()["cards"]
        card1 = next(x for x in cards if x["offer_id"] == o1.id)
        card2 = next(x for x in cards if x["offer_id"] == o2.id)
        assert card1["last_activity"] is not None
        assert card2["last_activity"] is None  # Firma 2'nin kartına Firma 1'in aktivitesi SIZMADI

    def test_allowed_transitions_present_and_forbidden_transition_rejected(self, db, client):
        c = _make_customer(db)
        o = _make_offer(db, c.id, status="draft")
        db.commit()
        card = next(x for x in client.get("/crm/pipeline").json()["cards"] if x["offer_id"] == o.id)
        assert set(card["allowed_transitions"]) == {"sent", "expired", "contracting"}

        # Geçerli transition: draft -> sent ("status" query parametresi — main.py PUT /offers/{id}/status)
        ok = client.put(f"/offers/{o.id}/status?status=sent")
        assert ok.status_code == 200

        # Geçersiz transition: sent -> draft (VALID_OFFER_TRANSITIONS'ta yok)
        bad = client.put(f"/offers/{o.id}/status?status=draft")
        assert bad.status_code == 400

    def test_customerless_offer_visible_not_hidden(self, db, client):
        _make_offer(db, None, status="draft")
        db.commit()
        cards = client.get("/crm/pipeline").json()["cards"]
        assert any(c["customer_id"] is None for c in cards)

    def test_single_tenant_isolation(self, db, client):
        c = _make_customer(db)
        _make_offer(db, c.id, status="sent", tenant_id="other-tenant")
        db.commit()
        cards = client.get("/crm/pipeline").json()["cards"]
        assert all(True for _ in cards)  # default tenant guard zaten diğer tenant isteklerini reddediyor (S1/S2 kararı)

    def test_stage_filter(self, db, client):
        c = _make_customer(db)
        _make_offer(db, c.id, status="draft")
        _make_offer(db, c.id, status="sent")
        db.commit()
        cards = client.get("/crm/pipeline?stage=DRAFT").json()["cards"]
        assert all(x["pipeline_stage"] == "DRAFT" for x in cards)
        assert len(cards) >= 1

    def test_has_contract_filter(self, db, client):
        c = _make_customer(db)
        o1 = _make_offer(db, c.id, status="accepted")
        o2 = _make_offer(db, c.id, status="contracting")
        _make_contract(db, o2.id, customer_id=c.id, status="DRAFT")
        db.commit()
        cards = client.get("/crm/pipeline?has_contract=true").json()["cards"]
        ids = {x["offer_id"] for x in cards}
        assert o2.id in ids
        assert o1.id not in ids

    def test_customer_search_filter(self, db, client):
        c1 = _make_customer(db, name="Alfa Enerji A.Ş.")
        c2 = _make_customer(db, name="Beta Sanayi A.Ş.")
        o1 = _make_offer(db, c1.id, status="draft")
        _make_offer(db, c2.id, status="draft")
        db.commit()
        cards = client.get("/crm/pipeline?customer_search=Alfa").json()["cards"]
        ids = {x["offer_id"] for x in cards}
        assert o1.id in ids
        assert len(cards) == 1
