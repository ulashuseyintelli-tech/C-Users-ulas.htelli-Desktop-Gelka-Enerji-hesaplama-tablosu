"""
S1 CRM Core — WB-2: GET /api/contracts (liste) için odaklı test suite.

Desen: in-memory SQLite + get_db override (bkz. test_contracts.py).

Çağrıldığı yerler: pytest tarafından otomatik keşfedilir (app/contracts/
router.py list_contracts() endpoint'inin regresyon testi).
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


def _make_offer(db, customer_id, status="draft"):
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


class TestListContracts:
    def test_empty_list(self, db, client):
        resp = client.get("/api/contracts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_all_contracts_without_filter(self, db, client):
        cust = _make_customer(db)
        db.flush()
        o1 = _make_offer(db, cust.id)
        o2 = _make_offer(db, cust.id)
        _make_contract(db, o1.id, customer_id=cust.id)
        _make_contract(db, o2.id, customer_id=cust.id)
        db.commit()

        resp = client.get("/api/contracts")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_customer_id_filter(self, db, client):
        c1 = _make_customer(db, "Firma 1")
        db.flush()
        c2 = _make_customer(db, "Firma 2")
        db.flush()
        o1 = _make_offer(db, c1.id)
        o2 = _make_offer(db, c2.id)
        _make_contract(db, o1.id, customer_id=c1.id)
        _make_contract(db, o2.id, customer_id=c2.id)
        db.commit()

        resp = client.get("/api/contracts", params={"customer_id": c1.id})
        body = resp.json()
        assert len(body) == 1
        assert body[0]["customer_id"] == c1.id

    def test_offer_id_filter(self, db, client):
        cust = _make_customer(db)
        db.flush()
        o1 = _make_offer(db, cust.id)
        o2 = _make_offer(db, cust.id)
        contract1 = _make_contract(db, o1.id, customer_id=cust.id)
        _make_contract(db, o2.id, customer_id=cust.id)
        db.commit()

        resp = client.get("/api/contracts", params={"offer_id": o1.id})
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == contract1.id

    def test_status_filter(self, db, client):
        cust = _make_customer(db)
        db.flush()
        o1 = _make_offer(db, cust.id)
        o2 = _make_offer(db, cust.id)
        _make_contract(db, o1.id, customer_id=cust.id, status="DRAFT")
        _make_contract(db, o2.id, customer_id=cust.id, status="FINALIZED")
        db.commit()

        resp = client.get("/api/contracts", params={"status": "FINALIZED"})
        body = resp.json()
        assert len(body) == 1
        assert body[0]["status"] == "FINALIZED"

    def test_unassigned_customer_not_hidden_by_default(self, db, client):
        """Owner kararı: customer_id=NULL sözleşmeler global listede GİZLENMEZ."""
        cust = _make_customer(db)
        db.flush()
        o1 = _make_offer(db, cust.id)
        o2 = _make_offer(db, None)
        _make_contract(db, o1.id, customer_id=cust.id)
        _make_contract(db, o2.id, customer_id=None)  # sahipsiz sözleşme
        db.commit()

        resp = client.get("/api/contracts")
        body = resp.json()
        assert len(body) == 2
        customer_ids = {row["customer_id"] for row in body}
        assert None in customer_ids

    def test_pagination_skip_limit(self, db, client):
        cust = _make_customer(db)
        db.flush()
        for _ in range(5):
            o = _make_offer(db, cust.id)
            _make_contract(db, o.id, customer_id=cust.id)
        db.commit()

        resp = client.get("/api/contracts", params={"skip": 2, "limit": 2})
        assert len(resp.json()) == 2

    def test_ordered_by_created_at_desc(self, db, client):
        cust = _make_customer(db)
        db.flush()
        o1 = _make_offer(db, cust.id)
        o2 = _make_offer(db, cust.id)
        c1 = _make_contract(db, o1.id, customer_id=cust.id)
        c2 = _make_contract(db, o2.id, customer_id=cust.id)
        db.commit()

        resp = client.get("/api/contracts")
        body = resp.json()
        # en yeni (c2) en once gelmeli
        assert body[0]["id"] == c2.id
        assert body[1]["id"] == c1.id

    def test_other_tenant_contracts_not_visible(self, db, client):
        """tenant_id filtresi: default olmayan tenant'ın sözleşmesi listede görünmez."""
        cust = _make_customer(db)
        db.flush()
        o1 = _make_offer(db, cust.id)
        _make_contract(db, o1.id, customer_id=cust.id, tenant_id="other-tenant")
        db.commit()

        resp = client.get("/api/contracts")
        assert resp.json() == []

    def test_response_shape_matches_contract_out(self, db, client):
        cust = _make_customer(db)
        db.flush()
        o1 = _make_offer(db, cust.id)
        _make_contract(db, o1.id, customer_id=cust.id, status="READY_TO_GENERATE")
        db.commit()

        resp = client.get("/api/contracts")
        row = resp.json()[0]
        assert set(row.keys()) == {
            "id", "customer_id", "offer_id", "contract_number", "status",
            "start_date", "end_date", "created_at",
            "customer_name", "agreement_multiplier",
            # S5-R01: taslak eksik hukuki alanlarini gosterir (yalniz alan
            # ADLARI, PII yok). Listede de GERCEK deger doner -- varsayilan
            # bos liste dondurmek "eksik yok" yalani olurdu.
            "missing_required_fields",
        }

    def test_customer_name_and_agreement_multiplier_populated(self, db, client):
        """WB-7: N+1'siz JOIN ile customer_name + agreement_multiplier dolduruluyor."""
        cust = _make_customer(db, "Test Firma Sözleşme A.Ş.")
        db.flush()
        o1 = _make_offer(db, cust.id)
        _make_contract(db, o1.id, customer_id=cust.id)
        db.commit()

        resp = client.get("/api/contracts")
        row = resp.json()[0]
        assert row["customer_name"] == "Test Firma Sözleşme A.Ş."
        assert row["agreement_multiplier"] == 1.01

    def test_unassigned_customer_has_null_customer_name(self, db, client):
        cust = _make_customer(db)
        db.flush()
        o1 = _make_offer(db, None)  # customer_id=NULL offer
        _make_contract(db, o1.id, customer_id=None)  # sahipsiz sözleşme
        db.commit()

        resp = client.get("/api/contracts")
        row = [r for r in resp.json() if r["customer_id"] is None][0]
        assert row["customer_name"] is None
        assert row["agreement_multiplier"] == 1.01  # offer_id her zaman var, multiplier da geliyor
