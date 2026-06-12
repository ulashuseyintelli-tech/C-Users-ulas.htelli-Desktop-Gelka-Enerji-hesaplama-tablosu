"""
Router-level entegrasyon testi — POST /api/pricing/upload-consumption.

Regresyon koruması: router, parse_consumption_excel'e customer_id'yi geçmeli.
Bug (önce): router `parse_consumption_excel(content, filename)` (2 arg) çağırıyordu →
TypeError: missing 'customer_id' → 500/bağlantı düşmesi. Birim testleri parser'ı
doğrudan 3 argümanla çağırdığı için yakalayamıyordu (test boşluğu).

Bu test HTTP router'ını TestClient ile çağırır: 200 + profilin DB'ye kaydını doğrular.
Parser'ın DESTEKLEDİĞİ format (Tarih datetime + Tüketim kWh) kullanılır.
"""
import calendar
import io
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook


def _make_consumption_xlsx(year=2026, month=3, days=2) -> bytes:
    """Tarih (datetime, saat gömülü) + Tüketim (kWh) formatı — parser destekliyor."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Tüketim"
    ws.append(["Tarih", "Tüketim (kWh)"])
    dmax = min(days, calendar.monthrange(year, month)[1])
    for day in range(1, dmax + 1):
        for hour in range(24):
            ws.append([datetime(year, month, day, hour, 0), 100.0 + hour])
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


class TestUploadConsumptionRouter:
    def test_upload_returns_200_and_persists_profile(self, client, db):
        xlsx = _make_consumption_xlsx(2026, 3, days=2)
        resp = client.post(
            "/api/pricing/upload-consumption",
            files={"file": ("tuketim.xlsx", xlsx,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"customer_id": "cust-router", "customer_name": "Router Test"},
        )
        # Bug öncesi burada 500/TypeError olurdu — fix sonrası 200 olmalı
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["customer_id"] == "cust-router"
        assert body["period"] == "2026-03"
        assert body["total_rows"] == 48  # 2 gün × 24 saat
        assert body["profile_id"] is not None

        # Profil gerçekten DB'ye yazıldı mı?
        from app.pricing.schemas import ConsumptionProfile
        rows = (
            db.query(ConsumptionProfile)
            .filter(ConsumptionProfile.customer_id == "cust-router")
            .all()
        )
        assert len(rows) == 1
        assert rows[0].period == "2026-03"

    def test_invalid_excel_returns_422_not_500(self, client):
        """Bozuk dosya temiz 422 döner (crash değil) — customer_id yine geçiyor."""
        resp = client.post(
            "/api/pricing/upload-consumption",
            files={"file": ("bad.xlsx", b"not a real excel", "application/octet-stream")},
            data={"customer_id": "cust-bad"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "invalid_consumption_format"
