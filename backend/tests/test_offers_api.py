"""
S1 CRM Core — WB-6: GET /offers ve GET /offers/{id} için odaklı test
suite (agreement_multiplier, allowed_transitions, has_contract).

Desen: in-memory SQLite + get_db override (bkz. test_contracts.py).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    import app.pricing.schemas  # noqa: F401 - Base.metadata'ya kaydolsun

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


@pytest.fixture()
def client(db):
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


def _make_offer(db, customer_id, status="draft", agreement_multiplier=1.06):
    from app.database import Offer
    o = Offer(
        customer_id=customer_id,
        vendor="TEST",
        consumption_kwh=1000.0,
        current_unit_price=2.0,
        weighted_ptf=2.0,
        yekdem=0.1,
        agreement_multiplier=agreement_multiplier,
        current_total=2000.0,
        offer_total=1900.0,
        savings_amount=100.0,
        savings_ratio=0.05,
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


class TestListOffers:
    def test_agreement_multiplier_present(self, db, client):
        c = _make_customer(db)
        db.flush()
        _make_offer(db, c.id, agreement_multiplier=1.09)
        db.commit()

        resp = client.get("/offers")
        assert resp.json()[0]["agreement_multiplier"] == 1.09

    def test_allowed_transitions_matches_lifecycle(self, db, client):
        from app.services.offer_lifecycle import VALID_OFFER_TRANSITIONS

        c = _make_customer(db)
        db.flush()
        _make_offer(db, c.id, status="sent")
        db.commit()

        resp = client.get("/offers")
        assert resp.json()[0]["allowed_transitions"] == VALID_OFFER_TRANSITIONS["sent"]

    def test_terminal_status_has_empty_transitions(self, db, client):
        c = _make_customer(db)
        db.flush()
        _make_offer(db, c.id, status="completed")
        db.commit()

        resp = client.get("/offers")
        assert resp.json()[0]["allowed_transitions"] == []

    def test_has_contract_false_when_no_contract(self, db, client):
        c = _make_customer(db)
        db.flush()
        _make_offer(db, c.id)
        db.commit()

        resp = client.get("/offers")
        assert resp.json()[0]["has_contract"] is False

    def test_has_contract_true_when_contract_exists(self, db, client):
        """Owner kararı: Contract tablosu otoritedir, Offer.status değil."""
        c = _make_customer(db)
        db.flush()
        o = _make_offer(db, c.id, status="draft")  # status hala 'draft' olsa bile
        _make_contract(db, o.id, customer_id=c.id)
        db.commit()

        resp = client.get("/offers")
        assert resp.json()[0]["has_contract"] is True

    def test_has_contract_scoped_per_offer_no_cross_contamination(self, db, client):
        c = _make_customer(db)
        db.flush()
        o1 = _make_offer(db, c.id)
        o2 = _make_offer(db, c.id)
        _make_contract(db, o1.id, customer_id=c.id)
        db.commit()

        resp = client.get("/offers")
        by_id = {row["id"]: row for row in resp.json()}
        assert by_id[o1.id]["has_contract"] is True
        assert by_id[o2.id]["has_contract"] is False


class TestGetOfferDetail:
    def test_allowed_transitions_and_has_contract_present(self, db, client):
        c = _make_customer(db)
        db.flush()
        o = _make_offer(db, c.id, status="accepted")
        db.commit()

        resp = client.get(f"/offers/{o.id}")
        body = resp.json()
        assert body["allowed_transitions"] == ["contracting", "rejected"]
        assert body["has_contract"] is False

    def test_has_contract_true_in_detail(self, db, client):
        c = _make_customer(db)
        db.flush()
        o = _make_offer(db, c.id)
        _make_contract(db, o.id, customer_id=c.id)
        db.commit()

        resp = client.get(f"/offers/{o.id}")
        assert resp.json()["has_contract"] is True
