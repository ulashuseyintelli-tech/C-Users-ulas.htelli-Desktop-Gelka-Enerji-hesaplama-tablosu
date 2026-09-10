"""
generate_run_summary() paydası (total_invoices) ve mismatch_rate — regresyon testleri.

Kusur (app/incident_metrics.py::generate_run_summary):
  - total_invoices, Job.job_type == "full_process" sayımıydı. JobType'ta böyle
    bir değer HİÇ OLMADI (EXTRACT / VALIDATE / EXTRACT_AND_VALIDATE).
    SQLAlchemy bilinmeyen string'i olduğu gibi SQL'e geçirdiği için sorgu hata
    VERMEDEN daima 0 dönüyordu -> mismatch_rate = incident_count / max(0, 1)
    = incident_count (2 incident -> 2.0 = %200).
  - Sorgu hata verirse bare except uydurma bir tahmin (incident_count * 5)
    dönüyordu.

Owner kararı (C, 2026-09-10): doğru payda bugün ÖLÇÜLEMİYOR. Incident'ların
tek üreticisi POST /full-process'tir ve bu endpoint Invoice/Job kaydı yazmaz.
Bu yüzden total_invoices=None, total_invoices_status="not_measured",
mismatch_rate=None. Ölçülemeyen değer 0 GÖSTERİLMEZ; Invoice/Job sayıları
paydaya İKAME EDİLMEZ.

Bu testler izole, bellek-içi bir SQLite üzerinde koşar; modül seviyesindeki
engine'e (./gelka_enerji.db) ve gerçek/production DB'ye DOKUNMAZ.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, Incident, Invoice, Job
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


def _incidentler(db, adet: int, tenant_id: str = "default") -> None:
    for i in range(adet):
        db.add(Incident(
            trace_id=f"payda-{i}",
            tenant_id=tenant_id,
            severity="S2",
            category="payda-testi",
            message="payda testi",
        ))
    db.commit()


def _fatura_ve_is(db, status: JobStatus) -> Job:
    invoice = Invoice(
        tenant_id="default",
        source_filename="payda-testi.pdf",
        content_type="application/pdf",
        storage_original_ref="test://payda-testi",
    )
    db.add(invoice)
    db.commit()
    job = Job(
        tenant_id=invoice.tenant_id,
        invoice_id=invoice.id,
        job_type=JobType.EXTRACT_AND_VALIDATE,
        status=status,
    )
    db.add(job)
    db.commit()
    return job


def _ozet(db):
    return generate_run_summary(db=db, tenant_id="default")


class TestRunSummaryPaydaOlculmuyor:
    """Payda ölçülemiyor: sayı uydurulmaz, None + "not_measured" döner."""

    @pytest.mark.parametrize("incident_adedi", [0, 1, 2, 5])
    def test_payda_ve_oran_incident_sayisindan_bagimsiz_null(self, db, incident_adedi):
        # Eski kod: payda daima 0 -> oran = incident_adedi (2 -> 2.0 = %200).
        _incidentler(db, incident_adedi)

        ozet = _ozet(db)

        assert ozet.incident_count == incident_adedi  # pay gerçekten sayıldı
        assert (ozet.total_invoices, ozet.mismatch_rate) == (None, None)
        assert ozet.total_invoices_status == "not_measured"

    def test_fatura_ve_job_kayitlari_paydaya_ikame_edilmez(self, db):
        # Invoice ve EXTRACT_AND_VALIDATE job'ları, incident'larla AYNI
        # faturaları saymaz: incident'lar /full-process'ten gelir ve o akış
        # Invoice/Job yazmaz. Rastgele alan ikamesi YAPILMAMALI.
        _fatura_ve_is(db, JobStatus.SUCCEEDED)
        _fatura_ve_is(db, JobStatus.SUCCEEDED)
        _incidentler(db, 2)

        ozet = _ozet(db)

        assert (ozet.total_invoices, ozet.mismatch_rate) == (None, None)
        assert ozet.total_invoices_status == "not_measured"

    def test_db_hatasinda_uydurma_tahmin_uretilmez(self, db):
        # Eski kod: sorgu hata verince bare except payda = incident_count * 5
        # uyduruyordu (2 incident -> 10 "fatura" -> %20 "makul" görünen oran).
        _incidentler(db, 2)
        Job.__table__.drop(db.get_bind())  # jobs tablosu yok -> gerçek DB hatası

        ozet = _ozet(db)

        assert (ozet.total_invoices, ozet.mismatch_rate) == (None, None)
        assert ozet.total_invoices_status == "not_measured"
        # Kuyruk düzeltmesinin DB hatası semantiği aynen korunur.
        assert ozet.queue_depth is None
        assert ozet.queue_error.startswith("OperationalError")

    def test_kuyruk_duzeltmesi_korunur_ayni_is_paydaya_sayilmaz(self, db):
        # Aynı QUEUED iş kuyruk derinliğine sayılır (kuyruk düzeltmesi) ama
        # paydaya SAYILMAZ.
        _fatura_ve_is(db, JobStatus.QUEUED)

        ozet = _ozet(db)

        assert ozet.queue_depth == 1
        assert ozet.to_dict()["queue"]["status"] == "ok"
        assert ozet.total_invoices is None


class TestRunSummaryPaydaSerilestirme:
    """to_dict() null'ı korur, round(None) çağırmaz, alan adları korunur."""

    def test_to_dict_json_null_ve_not_measured_verir(self, db):
        _incidentler(db, 2)

        metin = json.dumps(_ozet(db).to_dict())
        sozluk = json.loads(metin)

        assert sozluk["counts"]["total_invoices"] is None
        assert sozluk["counts"]["total_invoices_status"] == "not_measured"
        assert sozluk["rates"]["mismatch_rate"] is None
        assert '"total_invoices": null' in metin
        assert '"mismatch_rate": null' in metin
        # Diğer oranlar ölçülmüş sayılar olarak kalır.
        assert isinstance(sozluk["rates"]["s1_rate"], float)

    def test_mevcut_alan_adlari_korunur_not_measured_eklenir(self, db):
        sozluk = _ozet(db).to_dict()

        assert set(sozluk["counts"]) == {
            "total_invoices", "total_invoices_status", "incident_count",
            "s1_count", "s2_count", "ocr_suspect_count", "resolved_count",
            "feedback_count",
        }
        assert set(sozluk["rates"]) == {
            "mismatch_rate", "s1_rate", "ocr_suspect_rate",
            "feedback_coverage", "hint_accuracy_rate",
        }
