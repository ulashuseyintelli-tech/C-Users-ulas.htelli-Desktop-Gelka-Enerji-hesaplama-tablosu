"""
scripts/post_deploy_check.py readiness/kuyruk KARARLARI — tüketici regresyon testi (R97B).

NEDEN VAR:
post_deploy_check adım 5 (check_queue_status) stuck_count > 0 veya depth > 100
ise main() -> 2 (ROLLBACK) verir. GO-R97 öncesinde readiness'in kuyruk bloğu
exception attığı için checks.queue içinde depth/stuck_count anahtarları HİÇ
oluşmuyor, .get(..., 0) ikisini de 0 yapıyor ve kapı BOŞ YERE geçiyordu.
Düzeltmeden sonra kapı gerçek. Bu dosya tüketicinin GERÇEK endpoint çıktısı
üzerindeki kararlarını ve eşiklerin DEĞİŞMEDİĞİNİ kilitler:

  - sağlıklı kuyruk      -> geçer; depth anahtarı VAR (kapı vacuous değil)
  - takılmış iş (30 dk)  -> "Queue has 1 stuck job(s)", main() -> 2
  - derinlik sınırı      -> 100 geçer; 101 "Queue backlog too high: 101", main() -> 2

Taşıma: post_deploy_check.http_get FastAPI TestClient'a yönlendirilir. Karar
mantığı (check_* fonksiyonları ve main()) ile endpoint kodu GERÇEKTİR; yalnız
HTTP soketi yoktur. Eşikler script içinden okunur, testte TANIMLANMAZ.

Çağrıldığı yerler: pytest tarafından otomatik keşfedilir
(scripts/post_deploy_check.py check_queue_status()/main() tüketici testi).
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "post_deploy_check.py"
_TABAN = "http://testserver"


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
def pdc(db, monkeypatch):
    """Gerçek post_deploy_check modülü; http_get TestClient'a bağlı."""
    from app.main import app as fastapi_app
    from app.database import get_db

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-" + "x" * 32)
    fastapi_app.dependency_overrides[get_db] = lambda: db
    client = TestClient(fastapi_app)

    spec = importlib.util.spec_from_file_location("r97b_post_deploy_check_altinda", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def http_get(url, headers=None, timeout=None):
        assert url.startswith(_TABAN), url
        r = client.get(url[len(_TABAN):], headers=headers or {})
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text}
        return r.status_code, data

    monkeypatch.setattr(mod, "BASE_URL", _TABAN)
    monkeypatch.setattr(mod, "http_get", http_get)
    yield mod
    fastapi_app.dependency_overrides.clear()


def _fatura_ekle(db):
    from app.database import Invoice

    db.add(Invoice(
        id="inv-pdc-1", tenant_id="default", source_filename="pdc.pdf",
        content_type="application/pdf", storage_original_ref="ref/pdc.pdf", file_hash="hash-pdc",
    ))
    db.flush()


def _isler_ekle(db, adet, status, *, started_at=None):
    from app.database import Job
    from app.models import JobType

    _fatura_ekle(db)
    for i in range(adet):
        db.add(Job(
            id=f"j-pdc-{status.value}-{i}", tenant_id="default", invoice_id="inv-pdc-1",
            job_type=JobType.EXTRACT, status=status, created_at=datetime.utcnow(),
            started_at=started_at,
        ))
    db.commit()


def test_saglikli_kuyruk_gecer_ve_kapi_vacuous_degil(pdc):
    status, data = pdc.http_get(f"{_TABAN}/health/ready")
    assert status == 200
    # Kapı gerçek: endpoint depth anahtarını ÜRETİYOR (eskiden hiç yoktu).
    assert "depth" in data["checks"]["queue"], data["checks"]["queue"]

    assert pdc.check_queue_status() == (True, "Queue OK: depth=0, stuck=0")
    assert pdc.check_ready()[0] is True
    assert pdc.check_database()[0] is True
    assert pdc.check_config_validation()[0] is True


def test_saglikli_ortamda_main_rollback_istemez(pdc):
    # 1 ve 2 ROLLBACK kodlarıdır; 0 = hepsi geçti, 4 = yalnız kritik-olmayan uyarı.
    assert pdc.main() in (0, 4)


def test_takilmis_is_rollback_kararina_yol_acar(pdc, db):
    from app.models import JobStatus

    _isler_ekle(db, 1, JobStatus.RUNNING, started_at=datetime.utcnow() - timedelta(minutes=30))

    assert pdc.check_queue_status() == (False, "Queue has 1 stuck job(s)")
    # Readiness'in kendisi 200/ready kalır (queue failing_checks'e girmez)...
    assert pdc.check_ready()[0] is True
    # ...rollback kararını tüketici verir.
    assert pdc.main() == 2


@pytest.mark.parametrize(
    "adet, beklenen",
    [(100, (True, "Queue OK: depth=100, stuck=0")), (101, (False, "Queue backlog too high: 101"))],
    ids=["derinlik-100-sinirda-gecer", "derinlik-101-rollback"],
)
def test_derinlik_siniri_gevsetilmedi(pdc, db, adet, beklenen):
    from app.models import JobStatus

    _isler_ekle(db, adet, JobStatus.QUEUED)

    assert pdc.check_queue_status() == beklenen
    if beklenen[0] is False:
        assert pdc.main() == 2
