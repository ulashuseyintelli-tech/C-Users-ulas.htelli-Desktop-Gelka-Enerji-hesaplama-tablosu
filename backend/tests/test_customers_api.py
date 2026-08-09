"""
S1 CRM Core — WB-1: GET /customers aggregate alanları (open_offer_count,
last_offer_at) için odaklı test suite.

Desen: in-memory SQLite + get_db override (bkz. test_contracts.py).

Çağrıldığı yerler: pytest tarafından otomatik keşfedilir (app/main.py
list_customers() endpoint'inin regresyon testi).
"""
from __future__ import annotations

from datetime import datetime, timedelta

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


def _make_offer(db, customer_id, status, created_at=None, **overrides):
    from app.database import Offer
    o = Offer(
        customer_id=customer_id,
        vendor="TEST",
        consumption_kwh=1000.0,
        current_unit_price=2.0,
        weighted_ptf=2.0,
        yekdem=0.1,
        agreement_multiplier=1.01,
        current_total=2000.0,
        offer_total=1900.0,
        savings_amount=100.0,
        savings_ratio=0.05,
        status=status,
        **overrides,
    )
    if created_at is not None:
        o.created_at = created_at
    db.add(o)
    db.flush()
    return o


class TestListCustomersAggregates:
    def test_no_offers_returns_zero_and_none(self, db, client):
        _make_customer(db, "Boş Firma")
        db.commit()

        resp = client.get("/customers")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["open_offer_count"] == 0
        assert body[0]["last_offer_at"] is None
        assert "offer_count" not in body[0]  # eski alan kaldırıldı

    def test_open_statuses_are_counted(self, db, client):
        """draft/sent/viewed/accepted/contracting -> açık (VALID_OFFER_TRANSITIONS'tan türetilmiş)."""
        c = _make_customer(db, "Açık Teklifli Firma")
        db.flush()
        for status in ("draft", "sent", "viewed", "accepted", "contracting"):
            _make_offer(db, c.id, status)
        db.commit()

        resp = client.get("/customers")
        body = resp.json()
        assert body[0]["open_offer_count"] == 5

    def test_terminal_statuses_are_not_counted(self, db, client):
        """completed/rejected/expired -> kapalı, open_offer_count'a dahil değil."""
        c = _make_customer(db, "Kapalı Teklifli Firma")
        db.flush()
        for status in ("completed", "rejected", "expired"):
            _make_offer(db, c.id, status)
        db.commit()

        resp = client.get("/customers")
        body = resp.json()
        assert body[0]["open_offer_count"] == 0

    def test_mixed_statuses_only_open_counted(self, db, client):
        c = _make_customer(db, "Karışık Firma")
        db.flush()
        _make_offer(db, c.id, "draft")
        _make_offer(db, c.id, "sent")
        _make_offer(db, c.id, "completed")
        _make_offer(db, c.id, "rejected")
        db.commit()

        resp = client.get("/customers")
        body = resp.json()
        assert body[0]["open_offer_count"] == 2

    def test_last_offer_at_is_most_recent(self, db, client):
        c = _make_customer(db, "Tarih Firma")
        db.flush()
        older = datetime(2026, 1, 1, 10, 0, 0)
        newer = datetime(2026, 6, 1, 10, 0, 0)
        _make_offer(db, c.id, "draft", created_at=older)
        _make_offer(db, c.id, "completed", created_at=newer)  # kapalı ama yine de en yeni
        db.commit()

        resp = client.get("/customers")
        body = resp.json()
        assert body[0]["last_offer_at"].startswith("2026-06-01")

    def test_aggregate_scoped_per_customer_no_cross_contamination(self, db, client):
        """Bir müşterinin teklifleri diğerinin open_offer_count'unu etkilemez."""
        c1 = _make_customer(db, "Firma 1")
        db.flush()
        c2 = _make_customer(db, "Firma 2")
        db.flush()
        _make_offer(db, c1.id, "draft")
        _make_offer(db, c1.id, "sent")
        _make_offer(db, c2.id, "draft")
        db.commit()

        resp = client.get("/customers")
        body = {row["name"]: row for row in resp.json()}
        assert body["Firma 1"]["open_offer_count"] == 2
        assert body["Firma 2"]["open_offer_count"] == 1

    def test_search_filter_still_works(self, db, client):
        """Regresyon: search parametresi aggregate eklemeden önceki davranışla aynı."""
        _make_customer(db, "Alfa Enerji")
        _make_customer(db, "Beta Sanayi")
        db.commit()

        resp = client.get("/customers", params={"search": "Alfa"})
        body = resp.json()
        assert len(body) == 1
        assert body[0]["name"] == "Alfa Enerji"

    def test_unassigned_offer_customer_id_null_not_counted_anywhere(self, db, client):
        """customer_id=NULL teklif hiçbir müşterinin open_offer_count'una sızmaz (WB-2/6 için ön-koşul doğrulaması)."""
        c = _make_customer(db, "Sahibi Olan Firma")
        db.flush()
        _make_offer(db, c.id, "draft")
        _make_offer(db, None, "draft")  # customer_id=NULL
        db.commit()

        resp = client.get("/customers")
        body = resp.json()
        assert body[0]["open_offer_count"] == 1  # yalnız kendi teklifi
