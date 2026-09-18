"""
Incidents stats route regresyon testi.

Kök neden (RC 1.0.17'den beri 422): FastAPI route sıralaması. Statik
`/admin/incidents/stats`, dinamik `/admin/incidents/{incident_id}` (int)
route'undan SONRA kayıtlıydı; bu yüzden GET /admin/incidents/stats önce
{incident_id} route'una düşüyor, "stats" → int dönüşümü başarısız olup 422
üretiyordu. Düzeltme: {incident_id:int} path convertor — "stats" bu route'a
artık eşleşmez, doğru şekilde stats route'una düşer.

Desen: in-memory SQLite + get_db override (bkz. test_stats_api.py).
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


def _make_incident(db, *, severity="S2", category="PARSE_FAIL", status="OPEN"):
    from app.database import Incident
    inc = Incident(
        trace_id="trace-test",
        tenant_id="default",
        severity=severity,
        category=category,
        message="test incident",
        status=status,
    )
    db.add(inc)
    db.flush()
    return inc


class TestIncidentStatsRoute:
    def test_stats_route_not_422(self, db, client):
        """Regresyon: statik /stats yolu dinamik {incident_id} tarafından gölgelenmemeli."""
        resp = client.get("/admin/incidents/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert "by_status" in body
        assert "by_severity" in body
        assert "by_category" in body

    def test_stats_empty_totals_zero(self, db, client):
        resp = client.get("/admin/incidents/stats")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_stats_aggregations(self, db, client):
        _make_incident(db, severity="S1", category="PARSE_FAIL", status="OPEN")
        _make_incident(db, severity="S2", category="PARSE_FAIL", status="OPEN")
        _make_incident(db, severity="S2", category="TARIFF_MISSING", status="RESOLVED")
        db.flush()

        resp = client.get("/admin/incidents/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert body["by_status"] == {"OPEN": 2, "RESOLVED": 1}
        assert body["by_severity"] == {"S1": 1, "S2": 2}
        assert body["by_category"] == {"PARSE_FAIL": 2, "TARIFF_MISSING": 1}

    def test_single_incident_by_int_id_still_works(self, db, client):
        inc = _make_incident(db)
        db.flush()
        resp = client.get(f"/admin/incidents/{inc.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == inc.id

    def test_non_int_get_id_no_longer_422(self, db, client):
        """Regresyon: sözcüksel GET id artık path/incident_id 422'si üretmez.

        Yama GET route'unu `{incident_id:int}` yapar → "not-a-number" bu route'a
        eşleşmez. Yol yine de kardeş PATCH `{incident_id}` (str) route'una eşleştiği
        için yöntem uyuşmazlığı → 405 (yamasız halde 422 idi). Kritik değişmez:
        artık int-parse 422'si YOK ve statik /stats gölgelenmiyor.
        """
        resp = client.get("/admin/incidents/not-a-number")
        assert resp.status_code != 422
        assert resp.status_code == 405
