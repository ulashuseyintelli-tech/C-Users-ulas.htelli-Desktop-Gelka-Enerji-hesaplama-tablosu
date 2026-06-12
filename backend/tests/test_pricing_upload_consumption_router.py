"""
Router-level entegrasyon testi — POST /api/pricing/upload-consumption.

Kapsam:
- Regresyon: router, parse_consumption_excel'e customer_id'yi geçmeli (eski bug:
  TypeError missing 'customer_id'). Birim testleri parser'ı doğrudan çağırdığı
  için bu wiring'i yakalayamıyordu.
- A2: çok dönemli tek dosya → ay ay bölünüp dönem başına ConsumptionProfile;
  response otoritesi `profiles` listesi.

TestClient + in-memory SQLite (thread paylaşımı için StaticPool).
"""
import calendar
import io
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook


def _make_consumption_xlsx(months, year=2026, days_per_month=2, header="Tüketim (kWh)",
                           date_as_string=False) -> bytes:
    """Tarih + tüketim Excel üret.

    months: ay listesi (örn [1,2] → çok dönemli).
    header: tüketim sütunu başlığı (Cansu için "Aktif Çekiş").
    date_as_string: True → 'dd/mm/YYYY HH:MM:SS' metin (Cansu portal formatı).
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Rapor"
    ws.append(["Tarih", header])
    for month in months:
        dmax = min(days_per_month, calendar.monthrange(year, month)[1])
        for day in range(1, dmax + 1):
            for hour in range(24):
                dt = datetime(year, month, day, hour, 0)
                tarih = dt.strftime("%d/%m/%Y %H:%M:%S") if date_as_string else dt
                ws.append([tarih, 100.0 + hour])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.database import Base
    import app.pricing.schemas  # noqa: F401 — ConsumptionProfile'ı Base.metadata'a kaydet
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def client(db):
    from app.main import app as fastapi_app
    from app.database import get_db
    fastapi_app.dependency_overrides[get_db] = lambda: db
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def _upload(client, xlsx, customer_id="cust", name="Test"):
    return client.post(
        "/api/pricing/upload-consumption",
        files={"file": ("tuketim.xlsx", xlsx,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"customer_id": customer_id, "customer_name": name},
    )


class TestUploadConsumptionRouter:
    def test_single_month_returns_success_and_persists(self, client, db):
        # Bug öncesi burada TypeError/500 olurdu — fix sonrası 200/success olmalı
        resp = _upload(client, _make_consumption_xlsx([3], days_per_month=2),
                       customer_id="cust-single")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "success"
        assert body["customer_id"] == "cust-single"
        assert len(body["profiles"]) == 1
        p = body["profiles"][0]
        assert p["period"] == "2026-03"
        assert p["total_rows"] == 48  # 2 gün × 24 saat
        assert p["profile_id"] is not None

        from app.pricing.schemas import ConsumptionProfile
        rows = db.query(ConsumptionProfile).filter(
            ConsumptionProfile.customer_id == "cust-single").all()
        assert len(rows) == 1
        assert rows[0].period == "2026-03"

    def test_multi_period_splits_into_profiles_per_month(self, client, db):
        """A2: Oca–Nis tek dosya → 4 profil (her ay ayrı period)."""
        xlsx = _make_consumption_xlsx([1, 2, 3, 4], days_per_month=2)
        resp = _upload(client, xlsx, customer_id="cust-multi")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "success"
        periods = [p["period"] for p in body["profiles"]]
        assert periods == ["2026-01", "2026-02", "2026-03", "2026-04"]
        assert all(p["total_rows"] == 48 for p in body["profiles"])

        from app.pricing.schemas import ConsumptionProfile
        rows = db.query(ConsumptionProfile).filter(
            ConsumptionProfile.customer_id == "cust-multi").all()
        assert {r.period for r in rows} == {"2026-01", "2026-02", "2026-03", "2026-04"}

    def test_cansu_format_aktif_cekis_string_date(self, client, db):
        """Cansu portal formatı: header 'Aktif Çekiş' + saat-gömülü metin tarih."""
        xlsx = _make_consumption_xlsx([1, 2], days_per_month=2,
                                      header="Aktif Çekiş", date_as_string=True)
        resp = _upload(client, xlsx, customer_id="cansu-fmt")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [p["period"] for p in body["profiles"]] == ["2026-01", "2026-02"]

    def test_invalid_excel_returns_422_not_500(self, client):
        """Bozuk dosya temiz 422 döner (crash değil) — customer_id yine geçiyor."""
        resp = _upload(client, b"not a real excel", customer_id="cust-bad")
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "invalid_consumption_format"
