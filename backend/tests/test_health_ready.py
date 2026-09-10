"""
GET /health/ready readiness sözleşmesi — regresyon testi (R97B).

NEDEN VAR:
Endpoint Sprint 8.9.1'de yazıldı ve ona istek atan hiçbir test yoktu; readiness
KOŞULSUZ 503 döndürürken tam regresyon yeşil kaldı. GO-R96 izole provasında
yakalanan 503 gövdesi üç kusuru gösterdi; GO-R97 (PR #50) bunları düzeltti ama
test EKLEMEDİ. Bu dosya o düzeltmeyi kilitler — biri geri gelirse kırmızı olur:

  1) db.execute("SELECT 1") — SQLAlchemy 2.x ham string'i reddeder (ArgumentError)
     -> checks.database "error" -> KOŞULSUZ 503.
  2) Job.updated_at — Job modelinde böyle bir kolon YOK (AttributeError)
     -> checks.queue "Could not check queue: ...".
  3) Job.status == "processing" / "pending" — GEÇERSİZ JobStatus değerleri
     (enum: QUEUED/RUNNING/SUCCEEDED/FAILED). Hata vermeden daima 0 döner; yani
     1 ve 2 düzeltilse bile kuyruk kontrolü SESSİZCE YALAN SÖYLEYEN bir yeşile
     dönerdi.

Kuyruk semantiği (master ile birebir): takılmış iş = RUNNING ve
started_at < şimdi - 10 dk. started_at, işin RUNNING'e geçtiği an aynı geçişte
yazılır (job_claim, job_queue, rq_worker); "10 dakikadır RUNNING" tanımının
model karşılığı budur. started_at'i NULL olan RUNNING kayıt takılmış SAYILMAZ
(başlama anı bilinmiyor) — bilinçli karar, aşağıda kilitli.

Desen: in-memory SQLite + get_db override (bkz. test_contracts_list_api.py).

Çağrıldığı yerler: pytest tarafından otomatik keşfedilir
(app/main.py health_ready() endpoint'inin regresyon testi).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient


# Readiness yanıtında bulunması ZORUNLU dört alt kontrol.
BEKLENEN_KONTROLLER = {"config", "database", "openai_api", "queue"}


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
def client(db, monkeypatch):
    from app.main import app as fastapi_app
    from app.database import get_db

    # openai_api kontrolü yalnızca env'deki anahtarın BİÇİMSEL varlığına bakar
    # (dış çağrı yok). Sağlıklı dev ortamını temsil etmek için sahte bir anahtar
    # set ediliyor ki dört kontrolün DÖRDÜ de "ok" olsun.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-" + "x" * 32)

    fastapi_app.dependency_overrides[get_db] = lambda: db
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def _fatura_ekle(db, invoice_id: str = "inv-health-1"):
    """Job.invoice_id NOT NULL + FK olduğu için önce bir Invoice gerekir."""
    from app.database import Invoice

    inv = Invoice(
        id=invoice_id,
        tenant_id="default",
        source_filename="health.pdf",
        content_type="application/pdf",
        storage_original_ref="ref/health.pdf",
        file_hash="hash-health",
    )
    db.add(inv)
    db.flush()
    return inv


def _job_ekle(db, job_id, status, *, tenant_id="default", created_at=None, started_at=None):
    from app.database import Job
    from app.models import JobType

    job = Job(
        id=job_id,
        tenant_id=tenant_id,
        invoice_id="inv-health-1",
        job_type=JobType.EXTRACT,
        status=status,
        created_at=created_at or datetime.utcnow(),
        started_at=started_at,
    )
    db.add(job)
    return job


# ── ANA SÖZLEŞME ─────────────────────────────────────────────────────────────


def test_saglikli_dbde_200_ve_dort_kontrol_de_hatasiz(client):
    """
    Sağlıklı bir dev veritabanında /health/ready 200 döner ve dört alt
    kontrolün HİÇBİRİ "error" değildir. 503 regresyonunun ana kilidi.
    """
    r = client.get("/health/ready")

    assert r.status_code == 200, f"readiness 200 beklendi, gövde: {r.text}"

    body = r.json()
    assert body["status"] == "ready"
    # failing_checks yalnızca hata varsa eklenir; hiç olmamalı.
    assert "failing_checks" not in body, f"beklenmeyen failing_checks: {body.get('failing_checks')}"

    checks = body["checks"]
    assert BEKLENEN_KONTROLLER <= set(checks), (
        f"eksik kontrol(ler): {BEKLENEN_KONTROLLER - set(checks)}"
    )

    hatalilar = {ad: k for ad, k in checks.items() if k.get("status") == "error"}
    assert not hatalilar, f"hatalı alt kontrol(ler): {hatalilar}"

    # Sahte anahtar set edildiği için dördü de tam "ok" olmalı.
    for ad in BEKLENEN_KONTROLLER:
        assert checks[ad]["status"] == "ok", f"{ad} 'ok' değil: {checks[ad]}"


def test_gercek_db_hatasi_hala_503_uretir(client):
    """
    Negatif yol korunmalı: veritabanı GERÇEKTEN erişilemezse readiness 503 ve
    failing_checks=["database"] döner, hata mesajı görünür kalır. Düzeltme
    "her zaman 200" üreten bir kontrole dönüşmemeli.
    """
    from sqlalchemy.exc import OperationalError
    from app.main import app as fastapi_app
    from app.database import get_db

    class _ErisilemezOturum:
        def execute(self, *a, **k):
            raise OperationalError("SELECT 1", {}, Exception("veritabani erisilemez (test)"))

        def query(self, *a, **k):
            raise OperationalError("SELECT jobs", {}, Exception("veritabani erisilemez (test)"))

    fastapi_app.dependency_overrides[get_db] = lambda: _ErisilemezOturum()
    r = client.get("/health/ready")

    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["failing_checks"] == ["database"]
    assert body["checks"]["database"]["status"] == "error"
    assert "erisilemez" in body["checks"]["database"]["message"]
    # Kuyruk kontrolü de DB'ye ulaşamaz ama yalnız uyarı üretir, 503'e katılmaz.
    assert body["checks"]["queue"]["status"] == "warning"


# ── KUSUR 1: ham string SQL ──────────────────────────────────────────────────


def test_database_kontrolu_text_construct_kullanir(client):
    """
    Kusur 1 kilidi: db.execute("SELECT 1") geri gelirse SQLAlchemy 2.x
    ArgumentError fırlatır, database "error" olur ve endpoint 503'e düşer.
    """
    db_check = client.get("/health/ready").json()["checks"]["database"]

    assert db_check["status"] == "ok", f"database kontrolü ok değil: {db_check}"
    assert "latency_ms" in db_check
    assert "Textual SQL expression" not in str(db_check)


# ── KUSUR 2 + 3: kuyruk kontrolü ─────────────────────────────────────────────


def test_queue_kontrolu_hata_mesaji_uretmez(client):
    """
    Kusur 2 kilidi: Job.updated_at geri gelirse AttributeError yakalanır ve
    queue "Could not check queue: ..." uyarısına düşer; depth anahtarı kaybolur.
    """
    queue = client.get("/health/ready").json()["checks"]["queue"]

    assert queue["status"] == "ok", f"queue kontrolü ok değil: {queue}"
    assert "Could not check queue" not in str(queue)
    assert queue["depth"] == 0


def test_queue_depth_QUEUED_joblari_gercekten_sayar(client, db):
    """
    Kusur 3 kilidi (depth): eski kod status == "pending" arıyordu; geçersiz bir
    JobStatus değeri olduğu için depth DAİMA 0 kalıyordu. Doğru değer QUEUED.
    """
    from app.models import JobStatus

    _fatura_ekle(db)
    _job_ekle(db, "j-queued-1", JobStatus.QUEUED)
    _job_ekle(db, "j-queued-2", JobStatus.QUEUED)
    # Gürültü: bunlar depth'e girmemeli.
    _job_ekle(db, "j-done", JobStatus.SUCCEEDED)
    _job_ekle(db, "j-failed", JobStatus.FAILED)
    _job_ekle(db, "j-running", JobStatus.RUNNING, started_at=datetime.utcnow())
    db.commit()

    body = client.get("/health/ready").json()

    assert body["checks"]["queue"]["depth"] == 2, body["checks"]["queue"]
    assert body["status"] == "ready"


def test_queue_takilmis_job_tespit_eder_ve_503_uretmez(client, db):
    """
    Kusur 2+3 kilidi (stuck): 10 dakikadan uzun süredir RUNNING olan iş takılmış
    sayılır. Eski kod hem yanlış kolonu hem yanlış durumu kullandığı için bunu
    ASLA göremezdi. Takılmış iş readiness'i DÜŞÜRMEZ (queue failing_checks'e
    girmez); rollback kararı post_deploy_check tüketicisindedir.
    """
    from app.models import JobStatus

    _fatura_ekle(db)
    _job_ekle(db, "j-stuck", JobStatus.RUNNING, started_at=datetime.utcnow() - timedelta(minutes=30))
    _job_ekle(db, "j-fresh", JobStatus.RUNNING, started_at=datetime.utcnow())
    db.commit()

    r = client.get("/health/ready")
    body = r.json()
    queue = body["checks"]["queue"]

    assert queue["status"] == "warning"
    assert queue["stuck_count"] == 1, queue
    assert "stuck" in queue["message"]
    assert r.status_code == 200
    assert body["status"] == "ready"
    assert "failing_checks" not in body


def test_takilma_esigi_started_at_uzerinden_10_dakika(client, db):
    """
    Eşik semantiği: 9 dakikadır RUNNING takılmış DEĞİL, 11 dakikadır RUNNING
    takılmış. Ölçüt created_at değil started_at'tir: uzun süre kuyrukta
    beklemiş (created_at eski) ama yeni başlamış iş takılmış sayılmaz.
    """
    from app.models import JobStatus

    simdi = datetime.utcnow()
    _fatura_ekle(db)
    _job_ekle(db, "j-9dk", JobStatus.RUNNING, started_at=simdi - timedelta(minutes=9))
    _job_ekle(db, "j-11dk", JobStatus.RUNNING, started_at=simdi - timedelta(minutes=11))
    _job_ekle(
        db, "j-eski-kuyruk-yeni-baslamis", JobStatus.RUNNING,
        created_at=simdi - timedelta(hours=2), started_at=simdi - timedelta(minutes=1),
    )
    db.commit()

    queue = client.get("/health/ready").json()["checks"]["queue"]
    assert queue["stuck_count"] == 1, f"yalnız 11 dk'lık iş takılmış sayılmalı: {queue}"


def test_started_at_NULL_running_takilmis_sayilmaz(client, db):
    """
    Bilinçli karar (master semantiği): started_at'i NULL olan RUNNING kaydın
    başlama anı bilinmediği için "10 dakikadır RUNNING" iddia edilemez; SQL NULL
    karşılaştırması onu eşleştirmez ve takılmış SAYILMAZ. Modeldeki tüm RUNNING
    geçişleri started_at'i aynı anda yazar; bu durum yalnız anormal/elle
    düzenlenmiş veride oluşabilir. Davranış değişirse bu test kırılır.
    """
    from app.models import JobStatus

    _fatura_ekle(db)
    _job_ekle(
        db, "j-null-started", JobStatus.RUNNING,
        created_at=datetime.utcnow() - timedelta(minutes=45), started_at=None,
    )
    db.commit()

    queue = client.get("/health/ready").json()["checks"]["queue"]
    assert queue["status"] == "ok", queue
    assert "stuck_count" not in queue


# ── MULTITENANT SÖZLEŞMESİ ───────────────────────────────────────────────────


def test_queue_sayimi_tenanttan_bagimsizdir(client, db):
    """
    /health/ready bir ALTYAPI endpoint'idir (ops_guard_middleware ve
    guard_decision_middleware içinde _SKIP_PATHS'tedir); auth/tenant bağlamı
    yoktur ve probe'lar X-Tenant-Id göndermez. Kuyruk sayımı bu yüzden bilinçli
    olarak GLOBAL'dir: "default" dışındaki tenant'ta takılan iş de altyapı
    sinyalidir. Buraya tenant filtresi eklenirse bu test kırılır.
    """
    from app.models import JobStatus

    _fatura_ekle(db)
    _job_ekle(db, "j-t2-queued", JobStatus.QUEUED, tenant_id="tenant-2")
    _job_ekle(
        db, "j-t3-stuck", JobStatus.RUNNING, tenant_id="tenant-3",
        started_at=datetime.utcnow() - timedelta(minutes=30),
    )
    db.commit()

    queue = client.get("/health/ready").json()["checks"]["queue"]

    assert queue["depth"] == 1, f"başka tenant'ın QUEUED işi sayılmadı: {queue}"
    assert queue["stuck_count"] == 1, f"başka tenant'ın takılmış işi sayılmadı: {queue}"
