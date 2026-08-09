"""
S1 CRM Core — WB-8: GET /stats total_finalized_contracts için odaklı test.

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
    import app.pricing.schemas  # noqa: F401

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


def _make_offer(db, customer_id, status="draft"):
    from app.database import Offer
    o = Offer(
        customer_id=customer_id, vendor="TEST", consumption_kwh=1000.0, current_unit_price=2.0,
        weighted_ptf=2.0, yekdem=0.1, agreement_multiplier=1.01, current_total=2000.0,
        offer_total=1900.0, savings_amount=100.0, savings_ratio=0.05, status=status,
    )
    db.add(o)
    db.flush()
    return o


def _make_contract(db, offer_id, customer_id=None, status="DRAFT"):
    from app.database import Contract
    c = Contract(offer_id=offer_id, customer_id=customer_id, status=status, tenant_id="default")
    db.add(c)
    db.flush()
    return c


class TestStatsFinalizedContracts:
    def test_zero_when_no_contracts(self, db, client):
        resp = client.get("/stats")
        assert resp.json()["total_finalized_contracts"] == 0

    def test_counts_only_finalized(self, db, client):
        c = _make_customer(db)
        db.flush()
        o1 = _make_offer(db, c.id)
        o2 = _make_offer(db, c.id)
        o3 = _make_offer(db, c.id)
        _make_contract(db, o1.id, customer_id=c.id, status="FINALIZED")
        _make_contract(db, o2.id, customer_id=c.id, status="DRAFT")
        _make_contract(db, o3.id, customer_id=c.id, status="FINALIZED")
        db.commit()

        resp = client.get("/stats")
        assert resp.json()["total_finalized_contracts"] == 2

    def test_existing_fields_unaffected(self, db, client):
        """Regresyon: mevcut alanlar (total_customers, offers_by_status) bozulmadı."""
        c = _make_customer(db)
        db.flush()
        _make_offer(db, c.id, status="accepted")
        db.commit()

        resp = client.get("/stats")
        body = resp.json()
        assert body["total_customers"] == 1
        assert body["total_offers"] == 1
        assert body["offers_by_status"] == {"accepted": 1}

    def test_total_open_offers_derived_from_lifecycle(self, db, client):
        """total_open_offers OPEN_OFFER_STATUSES'tan türetilir, hard-code değil."""
        c = _make_customer(db)
        db.flush()
        _make_offer(db, c.id, status="draft")
        _make_offer(db, c.id, status="sent")
        _make_offer(db, c.id, status="completed")  # terminal, acik degil
        _make_offer(db, c.id, status="rejected")  # terminal, acik degil
        db.commit()

        resp = client.get("/stats")
        assert resp.json()["total_open_offers"] == 2
