"""
generate_run_summary() kuyruk metrikleri — regresyon testleri.

Kusur (GO-R97'de main.py::health_ready için düzeltilenin AYNISI,
app/incident_metrics.py::generate_run_summary kuyruk bloğunda):
  - Job.status == "pending" / "processing": GEÇERSİZ JobStatus değerleri
    (enum: QUEUED / RUNNING / SUCCEEDED / FAILED). SQLAlchemy bilinmeyen
    string'i olduğu gibi SQL'e geçirdiği için hata VERMEDEN 0 dönüyordu.
  - Job.updated_at: ALAN YOK -> AttributeError -> bare except yutuyordu.
  Sonuç: queue_depth daima 0, queue_stuck daima False.

Bu testler izole, bellek-içi bir SQLite üzerinde koşar; modül seviyesindeki
engine'e (./gelka_enerji.db) ve gerçek/production DB'ye DOKUNMAZ.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, Invoice, Job
from backend.app.incident_metrics import generate_run_summary
from backend.app.models import JobStatus, JobType


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _utc_naive_simdi() -> datetime:
    # Uygulama started_at'i naive UTC yazıyor (datetime.utcnow():
    # job_queue.mark_running/claim_job, services/job_claim, rq_worker).
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fatura(db, tenant_id: str = "default") -> Invoice:
    invoice = Invoice(
        tenant_id=tenant_id,
        source_filename="kuyruk-testi.pdf",
        content_type="application/pdf",
        storage_original_ref="test://kuyruk-testi",
    )
    db.add(invoice)
    db.commit()
    return invoice


def _is(db, invoice: Invoice, status: JobStatus, started_at=None) -> Job:
    job = Job(
        tenant_id=invoice.tenant_id,
        invoice_id=invoice.id,
        job_type=JobType.EXTRACT_AND_VALIDATE,
        status=status,
        started_at=started_at,
    )
    db.add(job)
    db.commit()
    return job


def _ozet(db):
    return generate_run_summary(db=db, tenant_id="default")


class TestRunSummaryKuyrukSayimi:
    """Sayılar gerçek kuyruğu yansıtmalı (eskiden daima 0/False)."""

    def test_queued_isler_kuyruk_derinligine_sayilir(self, db):
        fatura = _fatura(db)
        _is(db, fatura, JobStatus.QUEUED)
        _is(db, fatura, JobStatus.QUEUED)

        ozet = _ozet(db)

        assert ozet.queue_depth == 2
        assert ozet.queue_stuck is False

    def test_esikten_eski_running_is_takilmis_sayilir(self, db):
        fatura = _fatura(db)
        _is(db, fatura, JobStatus.RUNNING, started_at=_utc_naive_simdi() - timedelta(minutes=30))

        ozet = _ozet(db)

        assert ozet.queue_stuck is True
        assert ozet.queue_depth == 0  # RUNNING kuyruk derinliğine sayılmaz

    def test_esik_icindeki_running_is_takilmis_sayilmaz(self, db):
        fatura = _fatura(db)
        _is(db, fatura, JobStatus.RUNNING, started_at=_utc_naive_simdi() - timedelta(minutes=2))

        assert _ozet(db).queue_stuck is False

    def test_started_at_null_running_is_takilmis_sayilmaz(self, db):
        # main.py::health_ready ile AYNI NULL semantiği: started_at'i NULL
        # olan RUNNING kaydı SQL'de "< eşik" ile EŞLEŞMEZ.
        fatura = _fatura(db)
        _is(db, fatura, JobStatus.RUNNING, started_at=None)

        assert _ozet(db).queue_stuck is False

    def test_bitmis_isler_yok_sayilir(self, db):
        fatura = _fatura(db)
        eski = _utc_naive_simdi() - timedelta(hours=2)
        _is(db, fatura, JobStatus.SUCCEEDED, started_at=eski)
        _is(db, fatura, JobStatus.FAILED, started_at=eski)

        ozet = _ozet(db)

        assert ozet.queue_depth == 0
        assert ozet.queue_stuck is False

    def test_kuyruk_sayimi_tenant_filtresiz_sistem_genelidir(self, db):
        # Multitenant kararı: worker işleri tenant'a bakmadan FIFO alıyor
        # (job_queue.get_next_queued_job, services/job_claim) — başka
        # tenant'ın takılmış işi de TÜM kuyruğu bekletir.
        baska = _fatura(db, tenant_id="baska-tenant")
        _is(db, baska, JobStatus.QUEUED)
        _is(db, baska, JobStatus.RUNNING, started_at=_utc_naive_simdi() - timedelta(minutes=30))

        ozet = _ozet(db)  # tenant_id="default"

        assert ozet.queue_depth == 1
        assert ozet.queue_stuck is True


class TestRunSummaryKuyrukHataGorunurlugu:
    """Okuma hatası sessiz 0/False yerine görünür durum olmalı."""

    def test_to_dict_basarili_okumada_ok_durumu_verir(self, db):
        _is(db, _fatura(db), JobStatus.QUEUED)

        assert _ozet(db).to_dict()["queue"] == {
            "status": "ok",
            "current_depth": 1,
            "stuck_detected": False,
            "error": None,
        }

    def test_db_hatasi_sessiz_sifir_yerine_gorunur_durum_olur(self, db, caplog):
        # jobs tablosu yok (ör. yarım kalmış şema) -> gerçek bir DB hatası.
        Job.__table__.drop(db.get_bind())

        with caplog.at_level(logging.WARNING):
            ozet = _ozet(db)

        assert ozet.queue_depth is None
        assert ozet.queue_stuck is None
        assert ozet.queue_error.startswith("OperationalError")
        assert "jobs" in ozet.queue_error

        kuyruk = ozet.to_dict()["queue"]
        assert kuyruk["status"] == "error"
        assert kuyruk["current_depth"] is None
        assert kuyruk["stuck_detected"] is None
        assert kuyruk["error"] == ozet.queue_error

        assert any(
            r.name.endswith("incident_metrics") and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_programlama_hatasi_yutulmaz(self, db):
        # Bu olaydaki kusurun sınıfı (Job.updated_at -> AttributeError) bir
        # PROGRAMLAMA hatasıydı ve bare except onu sessizce yuttu. Artık
        # yalnız DB hataları (SQLAlchemyError) yakalanır.
        class _JobSorgusundaProgramlamaHatasi:
            def __init__(self, gercek):
                self._gercek = gercek

            def query(self, *entities, **kwargs):
                if entities and entities[0] is Job:
                    raise AttributeError("enjekte edilmiş programlama hatası")
                return self._gercek.query(*entities, **kwargs)

        with pytest.raises(AttributeError, match="enjekte"):
            generate_run_summary(db=_JobSorgusundaProgramlamaHatasi(db), tenant_id="default")
