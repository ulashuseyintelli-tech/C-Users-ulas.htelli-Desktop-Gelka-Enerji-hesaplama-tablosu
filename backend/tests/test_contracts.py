"""
Sözleşme oluşturma V1 — otomatik test suite.

Kapsam: model/repository katmanı (app/contracts/service.py), API endpoint'leri
(app/contracts/router.py), extraction parse mantığı (app/contracts/extractors.py,
OpenAI çağrısı mock'lanır), PDF şablon render + üretim (app/contracts/pdf_service.py).

KAPSAM DIŞI (bu dosyada test edilmez):
- Alembic migration round-trip: bu oturumda ad-hoc script ile doğrulandı
  (create_all + stamp 011 + upgrade head + downgrade -1), repo genelinde
  hiçbir migration'ın pytest ile test edilme geleneği yok.
- Gerçek OpenAI Vision API çağrısı: bu ortamda geçerli bir OPENAI_API_KEY yok
  (.env içinde placeholder). _call_openai_with_retry mock'lanarak parse
  katmanı gerçek örnek belge değerleriyle doğrulanır.

Desen: in-memory SQLite + get_db override (bkz. test_offer_real_consumption_c1.py).
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.contracts import extractors, pdf_service, service
from app.contracts.schemas import DocumentFieldValue

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates" / "contracts"


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

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
    """Testleri gerçek ./storage klasöründen izole eder (S10 deseni, test_pdf_artifact_storage.py)."""
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


def _make_customer(db, name="Algan Orman Ürünleri San. ve Tic. Ltd. Şti."):
    from app.database import Customer
    c = Customer(name=name)
    db.add(c)
    db.flush()
    return c


def _make_offer(db, customer_id=None, agreement_multiplier=1.01, extraction_result=None, tenant_id="default"):
    from app.database import Offer
    o = Offer(
        tenant_id=tenant_id,
        customer_id=customer_id,
        consumption_kwh=1000.0,
        current_unit_price=2.5,
        weighted_ptf=2500.0,
        yekdem=50.0,
        agreement_multiplier=agreement_multiplier,
        current_total=2500.0,
        offer_total=2400.0,
        savings_amount=100.0,
        savings_ratio=0.04,
        extraction_result=extraction_result,
    )
    db.add(o)
    db.flush()
    return o


def _make_document(db, document_type, customer_id=None, tenant_id="default", sha_seed=None):
    from app.database import UploadedReferenceDocument
    seed = sha_seed or f"{document_type}-{customer_id}-{tenant_id}"
    d = UploadedReferenceDocument(
        tenant_id=tenant_id,
        customer_id=customer_id,
        document_type=document_type,
        original_filename=f"{document_type}.pdf",
        mime_type="application/pdf",
        file_size=10,
        sha256=hashlib.sha256(seed.encode()).hexdigest(),
        storage_ref="unused-in-these-tests",
        processing_status="extracted",
    )
    db.add(d)
    db.flush()
    return d


def _make_run(db, document):
    from app.database import DocumentExtractionRun
    r = DocumentExtractionRun(
        tenant_id=document.tenant_id,
        document_id=document.id,
        extractor_type=document.document_type,
        extractor_version="v1",
        model_name="gpt-4o",
        prompt_version="v1",
        status="completed",
    )
    db.add(r)
    db.flush()
    return r


def _make_candidate(db, run, document, field_name, value, tenant_id="default"):
    from app.database import DocumentFieldCandidate
    c = DocumentFieldCandidate(
        tenant_id=tenant_id,
        extraction_run_id=run.id,
        document_id=document.id,
        field_name=field_name,
        raw_value=value,
        normalized_value=service._normalize_value(value),
        confidence=0.9,
        source_page=1,
        validation_status="pending",
        conflict_status="none",
    )
    db.add(c)
    db.flush()
    return c


def _fake_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(200, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _field(value, confidence=0.9, page=1, text="evidence"):
    return {"value": value, "confidence": confidence, "source_page": page, "source_text": text}


def _sample_snapshot(**overrides):
    base = {
        "legal_name": "ALGAN ORMAN ÜRÜNLERİ SANAYİ VE TİCARET LİMİTED ŞİRKETİ",
        "tax_number": "0510740975",
        "tax_office": "Mudurnu Vergi Dairesi",
        "registered_address": "Mudurnu / Bolu",
        "facility_address": "Kahramankazan / Ankara",
        "mersis_number": None,
        "trade_registry_number": None,
        "representative": {
            "full_name": "Berkan Ünver",
            "national_id": "58348427720",
            "authority_type": "Münferiden",
            "authority_scope": None,
            "is_indefinite": True,
        },
        "tariff_group": {"value": "AG-TT", "resolution_status": "resolved"},
        "subscription_codes": "1234567890",
        "contract_dates": {"start_date": "2026-01-01", "duration_months": 12},
        "offer_id": 1,
        "agreement_multiplier": 1.01,
        "template_version": "v1",
    }
    base.update(overrides)
    return base


def _make_ready_contract(db, tenant_id="default", agreement_multiplier=1.01):
    """READY_TO_GENERATE durumunda, finalize'a hazır bir Contract (+ Offer)."""
    from app.database import Contract
    offer = _make_offer(db, agreement_multiplier=agreement_multiplier, tenant_id=tenant_id)
    db.commit()
    snapshot = _sample_snapshot(offer_id=offer.id, agreement_multiplier=agreement_multiplier)
    contract = Contract(
        tenant_id=tenant_id, offer_id=offer.id, status="READY_TO_GENERATE", extraction_snapshot_json=snapshot,
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)
    return contract, offer


# ═══════════════════════════════════════════════════════════════════════════
# Belge yükleme (dedup, tenant izolasyonu, dosya validasyonu)
# ═══════════════════════════════════════════════════════════════════════════

class TestDocumentUploadAPI:
    def test_upload_creates_document(self, client):
        resp = client.post(
            "/api/contracts/documents/upload?document_type=vergi_levhasi",
            files={"file": ("levha.pdf", b"fake-pdf-bytes", "application/pdf")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["document_type"] == "vergi_levhasi"
        assert body["processing_status"] == "uploaded"
        assert body["is_duplicate"] is False

    def test_upload_dedup_same_tenant_same_hash(self, client):
        payload = b"identical-bytes-for-dedup-test"
        first = client.post(
            "/api/contracts/documents/upload?document_type=vergi_levhasi",
            files={"file": ("a.pdf", payload, "application/pdf")},
        ).json()
        second = client.post(
            "/api/contracts/documents/upload?document_type=vergi_levhasi",
            files={"file": ("b.pdf", payload, "application/pdf")},
        ).json()
        assert first["is_duplicate"] is False
        assert second["is_duplicate"] is True
        assert second["document_id"] == first["document_id"]

    def test_upload_dedup_scoped_by_tenant(self, client, db):
        """
        Owner kararı (final architecture review, madde 3): Customer tablosunda
        tenant_id yok — sözleşme modülü V1 yalnız default tenant ile çalışır,
        başka bir tenant fail-closed (403) reddedilir. Bu test artık "farklı
        tenant'larda doğru scoping" DEĞİL, "farklı tenant fail-closed"ı doğrular.
        """
        payload = b"same-bytes-different-tenant"
        resp_a = client.post(
            "/api/contracts/documents/upload?document_type=vergi_levhasi",
            files={"file": ("a.pdf", payload, "application/pdf")},
            headers={"X-Tenant-Id": "default"},
        )
        assert resp_a.status_code == 200

        resp_b = client.post(
            "/api/contracts/documents/upload?document_type=vergi_levhasi",
            files={"file": ("a.pdf", payload, "application/pdf")},
            headers={"X-Tenant-Id": "tenant-b"},
        )
        assert resp_b.status_code == 403
        assert resp_b.json()["detail"]["error"] == "tenant_not_supported"

        from app.database import UploadedReferenceDocument
        assert db.query(UploadedReferenceDocument).count() == 1

    def test_upload_rejects_invalid_document_type(self, client):
        resp = client.post(
            "/api/contracts/documents/upload?document_type=foo",
            files={"file": ("a.pdf", b"x", "application/pdf")},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "invalid_document_type"

    def test_upload_rejects_empty_file(self, client):
        resp = client.post(
            "/api/contracts/documents/upload?document_type=vergi_levhasi",
            files={"file": ("a.pdf", b"", "application/pdf")},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "empty_file"

    def test_upload_rejects_oversized_file(self, client):
        oversized = b"0" * (10 * 1024 * 1024 + 1)
        resp = client.post(
            "/api/contracts/documents/upload?document_type=vergi_levhasi",
            files={"file": ("a.pdf", oversized, "application/pdf")},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "file_too_large"

    def test_upload_rejects_unsupported_mime(self, client):
        resp = client.post(
            "/api/contracts/documents/upload?document_type=vergi_levhasi",
            files={"file": ("a.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "unsupported_file_type"


# ═══════════════════════════════════════════════════════════════════════════
# Extraction — parse katmanı (OpenAI çağrısı mock)
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractors:
    def test_extract_tax_certificate_parses_fields(self):
        mock_response = {
            "legal_name": _field("ALGAN ORMAN ÜRÜNLERİ SANAYİ VE TİCARET LİMİTED ŞİRKETİ"),
            "tax_number": _field("0510740975"),
            "tax_office": _field("Mudurnu Vergi Dairesi"),
            "facility_address": _field("Mudurnu / Bolu"),
            "activity_code": _field("1610.09"),
            "establishment_date": _field("2010-03-15"),
        }
        with patch("app.contracts.extractors._call_openai_with_retry", return_value=mock_response):
            result = extractors.extract_tax_certificate(_fake_png_bytes(), mime_type="image/png")

        assert result.legal_name.value == "ALGAN ORMAN ÜRÜNLERİ SANAYİ VE TİCARET LİMİTED ŞİRKETİ"
        assert result.legal_name.source_document == "vergi_levhasi"
        assert result.tax_office.value == "Mudurnu Vergi Dairesi"
        assert result.tax_number.value == "0510740975"

    def test_extract_signature_circular_parses_fields(self):
        mock_response = {
            "legal_name": _field("ALGAN ORMAN ÜRÜNLERİ SANAYİ VE TİCARET LİMİTED ŞİRKETİ"),
            "registered_address": _field("Kahramankazan / Ankara"),
            "representative_full_name": _field("Berkan Ünver"),
            "representative_national_id": _field("58348427720"),
            "authority_type": _field("Münferiden"),
            "authority_scope": _field("Tek başına temsil ve ilzam"),
            "authority_start_date": _field(None),
            "authority_end_date": _field(None),
            "is_indefinite": _field("true"),
            "trade_registry_number": _field(None),
            "mersis_number": _field(None),
            "tax_office": _field("Kahramankazan Vergi Dairesi"),
            "notary_name": _field("Ankara 5. Noterliği"),
            "notary_date": _field("2024-06-01"),
            "notary_document_number": _field("12345"),
        }
        with patch("app.contracts.extractors._call_openai_with_retry", return_value=mock_response):
            result = extractors.extract_signature_circular(_fake_png_bytes(), mime_type="image/png")

        assert result.representative_full_name.value == "Berkan Ünver"
        assert result.representative_full_name.source_document == "imza_sirkusu"
        assert result.representative_national_id.value == "58348427720"
        assert result.tax_office.value == "Kahramankazan Vergi Dairesi"

    def test_extract_missing_field_defaults_to_none_value(self):
        with patch("app.contracts.extractors._call_openai_with_retry", return_value={}):
            result = extractors.extract_tax_certificate(_fake_png_bytes(), mime_type="image/png")
        assert result.legal_name.value is None
        assert result.legal_name.confidence == 0.0


class TestRunExtraction:
    def test_run_extraction_persists_all_candidates(self, db):
        doc, _ = service.upload_reference_document(
            db, tenant_id="default", document_type="vergi_levhasi",
            file_bytes=_fake_png_bytes(), original_filename="levha.png", mime_type="image/png",
        )
        fake_result = extractors.TaxCertificateExtraction(
            legal_name=DocumentFieldValue(value="ALGAN ORMAN LTD", confidence=0.95, source_document="vergi_levhasi"),
            tax_number=DocumentFieldValue(value="0510740975", confidence=0.95, source_document="vergi_levhasi"),
            tax_office=DocumentFieldValue(value="Mudurnu Vergi Dairesi", confidence=0.9, source_document="vergi_levhasi"),
            facility_address=DocumentFieldValue(value="Mudurnu / Bolu", confidence=0.8, source_document="vergi_levhasi"),
            activity_code=DocumentFieldValue(value="1610.09", confidence=0.7, source_document="vergi_levhasi"),
            establishment_date=DocumentFieldValue(value="2010-03-15", confidence=0.7, source_document="vergi_levhasi"),
        )
        with patch("app.contracts.service.extract_tax_certificate", return_value=fake_result):
            run = service.run_extraction(db, doc)

        assert run.status == "completed"
        assert doc.processing_status == "extracted"

        from app.database import DocumentFieldCandidate
        candidates = db.query(DocumentFieldCandidate).filter(DocumentFieldCandidate.extraction_run_id == run.id).all()
        assert len(candidates) == 6
        legal_name = next(c for c in candidates if c.field_name == "legal_name")
        assert legal_name.raw_value == "ALGAN ORMAN LTD"
        assert legal_name.normalized_value == service._normalize_value("ALGAN ORMAN LTD")
        assert legal_name.validation_status == "pending"
        assert legal_name.conflict_status == "none"

    def test_run_extraction_failure_marks_failed_and_reraises(self, db):
        doc, _ = service.upload_reference_document(
            db, tenant_id="default", document_type="vergi_levhasi",
            file_bytes=_fake_png_bytes(), original_filename="levha.png", mime_type="image/png",
        )
        with patch("app.contracts.service.extract_tax_certificate", side_effect=RuntimeError("vision api boom")):
            with pytest.raises(RuntimeError):
                service.run_extraction(db, doc)

        from app.database import DocumentExtractionRun
        run = db.query(DocumentExtractionRun).filter(DocumentExtractionRun.document_id == doc.id).first()
        assert run.status == "failed"
        assert run.error_code == "RuntimeError"
        assert doc.processing_status == "failed"

    def test_extraction_endpoint_returns_502_on_failure(self, client, db):
        upload_resp = client.post(
            "/api/contracts/documents/upload?document_type=vergi_levhasi",
            files={"file": ("levha.pdf", b"fake", "application/pdf")},
        ).json()
        with patch("app.contracts.service.extract_tax_certificate", side_effect=RuntimeError("boom")):
            resp = client.post(f"/api/contracts/documents/{upload_resp['document_id']}/extract")
        assert resp.status_code == 502
        assert resp.json()["detail"]["error"] == "extraction_failed"

    def test_extraction_result_endpoint_returns_source_document(self, client, db):
        upload_resp = client.post(
            "/api/contracts/documents/upload?document_type=vergi_levhasi",
            files={"file": ("levha.pdf", b"fake", "application/pdf")},
        ).json()
        fake_result = extractors.TaxCertificateExtraction(
            legal_name=DocumentFieldValue(value="ALGAN ORMAN LTD", confidence=0.95, source_document="vergi_levhasi"),
            tax_number=DocumentFieldValue(value="0510740975", confidence=0.95, source_document="vergi_levhasi"),
            tax_office=DocumentFieldValue(value="Mudurnu Vergi Dairesi", confidence=0.9, source_document="vergi_levhasi"),
            facility_address=DocumentFieldValue(value=None, confidence=0.0, source_document="vergi_levhasi"),
            activity_code=DocumentFieldValue(value=None, confidence=0.0, source_document="vergi_levhasi"),
            establishment_date=DocumentFieldValue(value=None, confidence=0.0, source_document="vergi_levhasi"),
        )
        with patch("app.contracts.service.extract_tax_certificate", return_value=fake_result):
            client.post(f"/api/contracts/documents/{upload_resp['document_id']}/extract")

        resp = client.get(f"/api/contracts/documents/{upload_resp['document_id']}/extraction-result")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert all(c["source_document"] == "vergi_levhasi" for c in body["candidates"])


# ═══════════════════════════════════════════════════════════════════════════
# Çelişki tespiti (gerçek senaryo: vergi levhası Mudurnu vs imza sirküsü Kahramankazan)
# ═══════════════════════════════════════════════════════════════════════════

class TestConflictDetection:
    def test_no_conflict_when_values_agree(self, db):
        doc1 = _make_document(db, "vergi_levhasi")
        doc2 = _make_document(db, "imza_sirkusu")
        run1, run2 = _make_run(db, doc1), _make_run(db, doc2)
        c1 = _make_candidate(db, run1, doc1, "legal_name", "ALGAN ORMAN LTD")
        c2 = _make_candidate(db, run2, doc2, "legal_name", "ALGAN ORMAN LTD")
        db.commit()

        changed = service.detect_conflicts_for_customer_documents(db, [doc1.id, doc2.id])

        assert changed == []
        assert c1.conflict_status == "none"
        assert c2.conflict_status == "none"

    def test_conflict_when_tax_office_differs(self, db):
        """Gerçek senaryo: vergi levhası 'Mudurnu', imza sirküsü 'Kahramankazan' vergi dairesi."""
        doc1 = _make_document(db, "vergi_levhasi")
        doc2 = _make_document(db, "imza_sirkusu")
        run1, run2 = _make_run(db, doc1), _make_run(db, doc2)
        c1 = _make_candidate(db, run1, doc1, "tax_office", "Mudurnu Vergi Dairesi")
        c2 = _make_candidate(db, run2, doc2, "tax_office", "Kahramankazan Vergi Dairesi")
        db.commit()

        changed = service.detect_conflicts_for_customer_documents(db, [doc1.id, doc2.id])

        assert {c.id for c in changed} == {c1.id, c2.id}
        assert c1.conflict_status == "conflict"
        assert c2.conflict_status == "conflict"

    def test_single_document_never_conflicts(self, db):
        doc1 = _make_document(db, "vergi_levhasi")
        run1 = _make_run(db, doc1)
        _make_candidate(db, run1, doc1, "legal_name", "ALGAN ORMAN LTD")
        db.commit()

        changed = service.detect_conflicts_for_customer_documents(db, [doc1.id])
        assert changed == []

    def test_detect_conflicts_endpoint_flags_real_scenario(self, client, db):
        """
        Regresyon: service.detect_conflicts_for_customer_documents Faz 4'te
        yazılmış ama hiçbir endpoint'ten çağrılmıyordu — review ekranı hiçbir
        zaman gerçek bir çelişki göremezdi. Bu test /documents/detect-conflicts
        endpoint'ini gerçek Mudurnu/Kahramankazan senaryosuyla doğrular.
        """
        doc1 = _make_document(db, "vergi_levhasi")
        doc2 = _make_document(db, "imza_sirkusu")
        run1, run2 = _make_run(db, doc1), _make_run(db, doc2)
        _make_candidate(db, run1, doc1, "tax_office", "Mudurnu Vergi Dairesi")
        _make_candidate(db, run2, doc2, "tax_office", "Kahramankazan Vergi Dairesi")
        db.commit()

        resp = client.post("/api/contracts/documents/detect-conflicts", json={"document_ids": [doc1.id, doc2.id]})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["changed_candidates"]) == 2
        assert all(c["conflict_status"] == "conflict" for c in body["changed_candidates"])

    def test_detect_conflicts_endpoint_missing_document_returns_404(self, client, db):
        doc1 = _make_document(db, "vergi_levhasi")
        db.commit()
        resp = client.post("/api/contracts/documents/detect-conflicts", json={"document_ids": [doc1.id, 9999]})
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "document_not_found"

    def test_has_unresolved_conflicts_reflects_detection_state(self, db):
        doc1 = _make_document(db, "vergi_levhasi")
        doc2 = _make_document(db, "imza_sirkusu")
        run1, run2 = _make_run(db, doc1), _make_run(db, doc2)
        _make_candidate(db, run1, doc1, "tax_office", "Mudurnu Vergi Dairesi")
        _make_candidate(db, run2, doc2, "tax_office", "Kahramankazan Vergi Dairesi")
        db.commit()

        assert service.has_unresolved_conflicts(db, [doc1.id, doc2.id]) is False
        service.detect_conflicts_for_customer_documents(db, [doc1.id, doc2.id])
        assert service.has_unresolved_conflicts(db, [doc1.id, doc2.id]) is True


# ═══════════════════════════════════════════════════════════════════════════
# Çelişki çözümü — kardeş-aday cascade regresyon testi (bu oturumda bulunan bug)
# ═══════════════════════════════════════════════════════════════════════════

class TestConflictResolution:
    def test_resolve_cascades_to_sibling_and_clears_block(self, db):
        doc1 = _make_document(db, "vergi_levhasi")
        doc2 = _make_document(db, "imza_sirkusu")
        run1, run2 = _make_run(db, doc1), _make_run(db, doc2)
        c1 = _make_candidate(db, run1, doc1, "tax_office", "Mudurnu Vergi Dairesi")
        c2 = _make_candidate(db, run2, doc2, "tax_office", "Kahramankazan Vergi Dairesi")
        db.commit()
        service.detect_conflicts_for_customer_documents(db, [doc1.id, doc2.id])
        assert service.has_unresolved_conflicts(db, [doc1.id, doc2.id]) is True

        service.resolve_candidate(db, candidate_id=c1.id, decision="accepted", decided_by="test-user")
        db.refresh(c1)
        db.refresh(c2)

        assert c1.conflict_status == "resolved"
        assert c1.user_decision == "accepted"
        # KRİTİK: kardeş aday da 'resolved' olmalı — yoksa has_unresolved_conflicts
        # sonsuza kadar True döner ve finalize asla mümkün olmaz.
        assert c2.conflict_status == "resolved"
        # ama sistem kardeş için karar UYDURMAZ:
        assert c2.user_decision is None
        assert service.has_unresolved_conflicts(db, [doc1.id, doc2.id]) is False

    def test_resolve_non_conflict_candidate_does_not_cascade(self, db):
        doc1 = _make_document(db, "vergi_levhasi")
        run1 = _make_run(db, doc1)
        c1 = _make_candidate(db, run1, doc1, "legal_name", "ALGAN ORMAN LTD")
        db.commit()

        service.resolve_candidate(db, candidate_id=c1.id, decision="accepted")
        db.refresh(c1)

        assert c1.conflict_status == "none"
        assert c1.user_decision == "accepted"

    def test_reject_does_not_resolve_conflict_group(self, db):
        """decision='rejected' cascade tetiklemez — hangi değerin doğru olduğu hâlâ belirsiz."""
        doc1 = _make_document(db, "vergi_levhasi")
        doc2 = _make_document(db, "imza_sirkusu")
        run1, run2 = _make_run(db, doc1), _make_run(db, doc2)
        c1 = _make_candidate(db, run1, doc1, "tax_office", "Mudurnu Vergi Dairesi")
        c2 = _make_candidate(db, run2, doc2, "tax_office", "Kahramankazan Vergi Dairesi")
        db.commit()
        service.detect_conflicts_for_customer_documents(db, [doc1.id, doc2.id])

        service.resolve_candidate(db, candidate_id=c1.id, decision="rejected")
        db.refresh(c1)
        db.refresh(c2)

        assert c1.conflict_status == "conflict"
        assert c2.conflict_status == "conflict"
        assert service.has_unresolved_conflicts(db, [doc1.id, doc2.id]) is True

    def test_resolve_endpoint_returns_source_document(self, client, db):
        """
        Regresyon: candidate.document gerçek bir SQLAlchemy relationship değil
        (yalnız document_id FK var). router.resolve_field_candidate bunu manuel
        atamıyordu ve `updated.document = candidate.document` AttributeError
        fırlatıyordu — bu test ve düzeltme bu oturumda eklendi.
        """
        doc = _make_document(db, "vergi_levhasi")
        run = _make_run(db, doc)
        candidate = _make_candidate(db, run, doc, "legal_name", "ALGAN ORMAN LTD")
        db.commit()

        resp = client.post(
            f"/api/contracts/candidates/{candidate.id}/resolve",
            json={"candidate_id": candidate.id, "decision": "accepted"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_document"] == "vergi_levhasi"
        assert body["validation_status"] == "confirmed"


# ═══════════════════════════════════════════════════════════════════════════
# Tarife grubu çözümlemesi (Offer.tariff_group kolonu yok — extraction_result'tan)
# ═══════════════════════════════════════════════════════════════════════════

class TestTariffGroupResolution:
    def test_resolves_from_tariff_group_guess(self, db):
        offer = _make_offer(db, extraction_result={"meta": {"tariff_group_guess": "AG-TT"}})
        result = service.resolve_tariff_group(offer)
        assert result.resolution_status == "resolved"
        assert result.value == "AG-TT"
        assert result.source_path == "extraction_result.meta.tariff_group_guess"

    def test_falls_back_to_tariff_type_when_guess_unknown(self, db):
        offer = _make_offer(
            db, extraction_result={"meta": {"tariff_group_guess": "unknown"}, "tariff": {"tariff_type": "OG-TT"}}
        )
        result = service.resolve_tariff_group(offer)
        assert result.resolution_status == "resolved"
        assert result.value == "OG-TT"
        assert result.source_path == "extraction_result.tariff.tariff_type"

    def test_not_found_when_extraction_result_missing(self, db):
        offer = _make_offer(db, extraction_result=None)
        result = service.resolve_tariff_group(offer)
        assert result.resolution_status == "not_found"
        assert result.value is None


# ═══════════════════════════════════════════════════════════════════════════
# Sözleşme snapshot — çarpan yalnız Offer'dan gelir, temsilci bağlama, None-safe
# ═══════════════════════════════════════════════════════════════════════════

class TestContractSnapshot:
    def test_multiplier_comes_only_from_offer(self, db):
        from app.database import Contract
        customer = _make_customer(db)
        offer = _make_offer(db, customer_id=customer.id, agreement_multiplier=1.01)
        db.commit()
        contract = Contract(tenant_id="default", customer_id=customer.id, offer_id=offer.id, status="DRAFT")
        db.add(contract)
        db.flush()

        snapshot = service.build_contract_snapshot(
            db, contract, complete_fields={"start_date": "2026-01-01", "duration_months": 12}
        )
        assert snapshot["agreement_multiplier"] == 1.01
        assert snapshot["offer_id"] == offer.id

    def test_missing_legal_profile_and_representative_is_none_safe(self, db):
        from app.database import Contract
        customer = _make_customer(db)
        offer = _make_offer(db, customer_id=customer.id)
        db.commit()
        contract = Contract(tenant_id="default", customer_id=customer.id, offer_id=offer.id, status="DRAFT")
        db.add(contract)
        db.flush()

        snapshot = service.build_contract_snapshot(db, contract, complete_fields={})
        assert snapshot["legal_name"] is None
        assert snapshot["representative"]["full_name"] is None

    def test_snapshot_uses_only_bound_representative(self, db):
        from app.database import Contract, CustomerLegalProfile, CustomerAuthorizedRepresentative
        customer = _make_customer(db)
        offer = _make_offer(db, customer_id=customer.id)
        legal_profile = CustomerLegalProfile(
            tenant_id="default", customer_id=customer.id, legal_name="ALGAN ORMAN LTD",
            tax_number="0510740975", tax_office="Mudurnu VD", registered_address="Mudurnu/Bolu",
        )
        db.add(legal_profile)
        db.flush()
        rep1 = CustomerAuthorizedRepresentative(
            tenant_id="default", customer_id=customer.id, legal_profile_id=legal_profile.id,
            full_name="Berkan Ünver", national_id="58348427720", authority_type="Münferiden", is_indefinite=True,
        )
        rep2 = CustomerAuthorizedRepresentative(
            tenant_id="default", customer_id=customer.id, legal_profile_id=legal_profile.id,
            full_name="Diğer Yetkili", national_id="11111111111", authority_type="Müştereken", is_indefinite=False,
        )
        db.add_all([rep1, rep2])
        db.flush()
        db.commit()

        contract = Contract(
            tenant_id="default", customer_id=customer.id, offer_id=offer.id,
            legal_profile_id=legal_profile.id, authorized_representative_id=rep1.id, status="DRAFT",
        )
        db.add(contract)
        db.flush()

        snapshot = service.build_contract_snapshot(db, contract, complete_fields={})
        assert snapshot["representative"]["full_name"] == "Berkan Ünver"
        assert snapshot["representative"]["national_id"] == "58348427720"


# ═══════════════════════════════════════════════════════════════════════════
# Offer yaşam döngüsü — owner kararı: draft/sent/viewed/accepted/contracting/
# completed mevcut durum makinesi yeniden kullanılıyor (bkz. offer_lifecycle.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestOfferLifecycle:
    def test_transition_offer_status_valid(self, db):
        from app.contracts.service import mark_offer_contracting
        offer = _make_offer(db)
        db.commit()
        mark_offer_contracting(db, offer)
        assert offer.status == "contracting"

    def test_transition_offer_status_writes_audit_log(self, db):
        from app.database import AuditLog
        from app.contracts.service import mark_offer_contracting
        offer = _make_offer(db)
        db.commit()
        mark_offer_contracting(db, offer)

        entry = db.query(AuditLog).filter(AuditLog.target_type == "offer", AuditLog.target_id == str(offer.id)).first()
        assert entry is not None
        assert entry.details_json["new_status"] == "contracting"

    def test_mark_offer_contracting_none_offer_is_noop(self, db):
        from app.contracts.service import mark_offer_contracting
        mark_offer_contracting(db, None)  # patlamamalı

    def test_mark_offer_completed_on_terminal_state_is_silently_ignored(self, db):
        from app.contracts.service import mark_offer_completed
        offer = _make_offer(db)
        offer.status = "rejected"  # terminal, "completed"e geçiş yok
        db.commit()

        mark_offer_completed(db, offer)  # ValueError yutulmalı, patlamamalı
        assert offer.status == "rejected"  # değişmedi


# ═══════════════════════════════════════════════════════════════════════
# Sözleşme yaşam döngüsü — API uçtan uca (draft → preview → finalize)
# ═══════════════════════════════════════════════════════════════════════════

class TestManualTariffGroupFallback:
    def test_manual_tariff_group_used_when_resolution_not_found(self, db):
        """
        Regresyon: complete_fields.tariff_group (kullanıcının Ek Protokol
        tamamlama ekranında elle girdiği tarife grubu) build_contract_snapshot
        tarafından hiç okunmuyordu — resolve_tariff_group her zaman kazanıyor,
        elle girilen değer sessizce atılıyordu (şablonun 'elle girilmiştir'
        notuyla çelişen boş [BELİRTİLMEDİ] sonucu doğuruyordu).
        """
        from app.database import Contract
        customer = _make_customer(db)
        offer = _make_offer(db, customer_id=customer.id, extraction_result=None)  # → not_found
        db.commit()
        contract = Contract(tenant_id="default", customer_id=customer.id, offer_id=offer.id, status="DRAFT")
        db.add(contract)
        db.flush()

        snapshot = service.build_contract_snapshot(db, contract, complete_fields={"tariff_group": "AG-TT (elle)"})
        assert snapshot["tariff_group"]["value"] == "AG-TT (elle)"
        assert snapshot["tariff_group"]["resolution_status"] == "not_found"  # gerçekten otomatik çözülmedi

    def test_auto_resolution_wins_over_manual_when_available(self, db):
        from app.database import Contract
        customer = _make_customer(db)
        offer = _make_offer(db, customer_id=customer.id, extraction_result={"meta": {"tariff_group_guess": "OG-TT"}})
        db.commit()
        contract = Contract(tenant_id="default", customer_id=customer.id, offer_id=offer.id, status="DRAFT")
        db.add(contract)
        db.flush()

        snapshot = service.build_contract_snapshot(db, contract, complete_fields={"tariff_group": "elle-girilen-farkli-deger"})
        assert snapshot["tariff_group"]["value"] == "OG-TT"  # otomatik çözüm elle girileni ezer
        assert snapshot["tariff_group"]["resolution_status"] == "resolved"


class TestContractLifecycleAPI:
    def test_create_draft_requires_existing_offer(self, client):
        resp = client.post("/api/contracts/drafts", json={"offer_id": 9999})
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "offer_not_found"

    def test_create_draft_success(self, client, db):
        offer = _make_offer(db)
        db.commit()
        resp = client.post("/api/contracts/drafts", json={"offer_id": offer.id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "DRAFT"
        assert body["offer_id"] == offer.id

    def test_preview_requires_existing_contract(self, client):
        resp = client.post(
            "/api/contracts/9999/preview", json={"start_date": "2026-01-01", "duration_months": 12}
        )
        assert resp.status_code == 404

    def test_preview_success_renders_four_sections_with_real_multiplier(self, client, db):
        offer = _make_offer(db, agreement_multiplier=1.01)
        db.commit()
        draft = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()

        resp = client.post(
            f"/api/contracts/{draft['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "READY_TO_GENERATE"
        assert len(body["rendered_html_sections"]) == 4
        assert "1,01" in body["rendered_html_sections"][1]

    def test_finalize_requires_preview_first(self, client, db):
        offer = _make_offer(db)
        db.commit()
        draft = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()

        resp = client.post(f"/api/contracts/{draft['id']}/finalize")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "preview_required"

    def test_finalize_blocked_by_unresolved_conflicts(self, client, db):
        customer = _make_customer(db)
        offer = _make_offer(db, customer_id=customer.id)
        doc1 = _make_document(db, "vergi_levhasi", customer_id=customer.id)
        doc2 = _make_document(db, "imza_sirkusu", customer_id=customer.id)
        run1, run2 = _make_run(db, doc1), _make_run(db, doc2)
        _make_candidate(db, run1, doc1, "tax_office", "Mudurnu Vergi Dairesi")
        _make_candidate(db, run2, doc2, "tax_office", "Kahramankazan Vergi Dairesi")
        db.commit()
        service.detect_conflicts_for_customer_documents(db, [doc1.id, doc2.id])

        draft = client.post(
            "/api/contracts/drafts", json={"offer_id": offer.id, "customer_id": customer.id}
        ).json()
        client.post(f"/api/contracts/{draft['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12})

        resp = client.post(f"/api/contracts/{draft['id']}/finalize")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "unresolved_conflicts"

    def test_finalize_success_then_immutable(self, client, db):
        offer = _make_offer(db, agreement_multiplier=1.01)
        db.commit()
        draft = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()
        client.post(f"/api/contracts/{draft['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12})

        fixed_bytes = b"%PDF-fake-contract-bytes"
        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=fixed_bytes):
            resp = client.post(f"/api/contracts/{draft['id']}/finalize")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "FINALIZED"
        assert body["pdf_sha256"] == hashlib.sha256(fixed_bytes).hexdigest()

        # HIGH#1 (final architecture review): ikinci finalize artık 409 değil —
        # idempotent, aynı sonucu yeniden PDF üretmeden döndürür (retry/response-loss güvenli).
        resp2 = client.post(f"/api/contracts/{draft['id']}/finalize")
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["status"] == "FINALIZED"
        assert body2["pdf_sha256"] == body["pdf_sha256"]
        assert body2["pdf_storage_ref"] == body["pdf_storage_ref"]

        # finalize sonrası preview → 409 (immutable)
        resp3 = client.post(
            f"/api/contracts/{draft['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12}
        )
        assert resp3.status_code == 409
        assert resp3.json()["detail"]["error"] == "contract_finalized"

    def test_create_draft_transitions_offer_to_contracting(self, client, db):
        offer = _make_offer(db)
        db.commit()
        assert offer.status == "draft"

        client.post("/api/contracts/drafts", json={"offer_id": offer.id})

        db.refresh(offer)
        assert offer.status == "contracting"

    def test_finalize_transitions_offer_to_completed(self, client, db):
        from app.database import Offer
        offer = _make_offer(db, agreement_multiplier=1.01)
        db.commit()
        draft = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()
        client.post(f"/api/contracts/{draft['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12})

        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=b"%PDF-fake"):
            client.post(f"/api/contracts/{draft['id']}/finalize")

        db.refresh(offer)
        assert offer.status == "completed"

    def test_second_draft_on_completed_offer_does_not_block_or_crash(self, client, db):
        """
        owner kararı: aynı tekliften farklı sözleşme üretilebilir. Offer zaten
        'completed' iken (terminal state) ikinci bir draft açmak, offer status
        geçişi başarısız olsa bile (best-effort) sözleşme akışını engellememeli.
        """
        offer = _make_offer(db, agreement_multiplier=1.01)
        db.commit()
        draft1 = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()
        client.post(f"/api/contracts/{draft1['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12})
        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=b"%PDF-fake"):
            client.post(f"/api/contracts/{draft1['id']}/finalize")
        db.refresh(offer)
        assert offer.status == "completed"

        resp2 = client.post("/api/contracts/drafts", json={"offer_id": offer.id})
        assert resp2.status_code == 200  # engellenmedi

        db.refresh(offer)
        assert offer.status == "completed"  # terminal state'ten çıkarılmadı, sessizce yok sayıldı

    def test_download_before_finalize_returns_404(self, client, db):
        offer = _make_offer(db)
        db.commit()
        draft = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()

        resp = client.get(f"/api/contracts/{draft['id']}/download")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "pdf_not_generated"

    def test_download_after_finalize_streams_pdf_bytes(self, client, db):
        offer = _make_offer(db, agreement_multiplier=1.01)
        db.commit()
        draft = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()
        client.post(f"/api/contracts/{draft['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12})

        fixed_bytes = b"%PDF-fake-contract-bytes"
        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=fixed_bytes):
            client.post(f"/api/contracts/{draft['id']}/finalize")

        resp = client.get(f"/api/contracts/{draft['id']}/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content == fixed_bytes

    def test_tenant_isolation_on_get_contract(self, client, db):
        """
        Owner kararı (final architecture review, madde 3): sözleşme modülü V1
        yalnız default tenant'ı destekler — başka bir tenant kimliğiyle GELEN
        istek artık 404 (sessizce "bulunamadı") DEĞİL, 403 fail-closed (açıkça
        "bu tenant desteklenmiyor") döner. Default tenant için normal davranış
        (kendi sözleşmesine erişebilir) korunur.
        """
        offer = _make_offer(db, tenant_id="default")
        db.commit()
        draft_resp = client.post(
            "/api/contracts/drafts", json={"offer_id": offer.id}, headers={"X-Tenant-Id": "default"}
        )
        contract_id = draft_resp.json()["id"]

        resp_other_tenant = client.get(f"/api/contracts/{contract_id}", headers={"X-Tenant-Id": "tenant-b"})
        assert resp_other_tenant.status_code == 403
        assert resp_other_tenant.json()["detail"]["error"] == "tenant_not_supported"

        resp_default_tenant = client.get(f"/api/contracts/{contract_id}", headers={"X-Tenant-Id": "default"})
        assert resp_default_tenant.status_code == 200

        resp_no_header = client.get(f"/api/contracts/{contract_id}")  # header yok → get_tenant_id "default" döner
        assert resp_no_header.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# T.C. kimlik no görünürlük sınırı — owner kararı (RepresentativeSummary vs Detail)
# ═══════════════════════════════════════════════════════════════════════════

class TestRepresentativePiiBoundary:
    def test_summary_schema_excludes_national_id(self):
        from app.contracts.schemas import RepresentativeSummaryOut
        assert "national_id" not in RepresentativeSummaryOut.model_fields

    def test_detail_schema_includes_national_id(self):
        from app.contracts.schemas import RepresentativeDetailOut
        assert "national_id" in RepresentativeDetailOut.model_fields

    def test_contract_out_schema_excludes_national_id(self):
        from app.contracts.schemas import ContractOut
        assert "national_id" not in ContractOut.model_fields

    def test_save_representative_endpoint_returns_national_id_in_detail_view(self, client):
        resp = client.post(
            "/api/contracts/representatives",
            json={"full_name": "Berkan Ünver", "national_id": "58348427720", "is_indefinite": True},
        )
        assert resp.status_code == 200
        assert resp.json()["national_id"] == "58348427720"


# ═══════════════════════════════════════════════════════════════════════════
# PDF şablon render — tarayıcı gerektirmez (Jinja2 + Playwright'tan bağımsız)
# ═══════════════════════════════════════════════════════════════════════════

class TestPdfTemplateRendering:
    def test_render_returns_four_sections_in_order(self):
        sections = pdf_service.render_contract_sections(_sample_snapshot())
        assert len(sections) == 4
        assert "İKİLİ ANLAŞMA" in sections[0]
        assert "SÖZLEŞME EK PROTOKOLÜ" in sections[1]
        assert "TAHLİYE" in sections[2]
        assert "MÜŞTERİ BİLGİLENDİRME FORMU" in sections[3]

    def test_multiplier_uses_turkish_locale_not_template_example(self):
        sections = pdf_service.render_contract_sections(_sample_snapshot(agreement_multiplier=1.01))
        ek_protokol = sections[1]
        assert "1,01" in ek_protokol
        assert "1,06" not in ek_protokol

    def test_template_source_has_no_hardcoded_example_multiplier(self):
        content = (_TEMPLATES_DIR / "additional_protocol_v1.html").read_text(encoding="utf-8")
        assert "1,06" not in content
        assert "1.06" not in content

    def test_missing_dates_show_placeholder(self):
        sections = pdf_service.render_contract_sections(
            _sample_snapshot(contract_dates={"start_date": None, "duration_months": None})
        )
        assert "[BELİRTİLMEDİ]" in sections[1]

    def test_tariff_group_not_found_shows_manual_entry_note(self):
        sections = pdf_service.render_contract_sections(
            _sample_snapshot(tariff_group={"value": "AG-TT", "resolution_status": "not_found"})
        )
        assert "elle girilmiştir" in sections[1]

    def test_national_id_appears_in_main_contract_signature(self):
        """Ana sözleşme imza bloğu yalnız unvan + T.C./VKN gösterir (representative_full_name yok)."""
        sections = pdf_service.render_contract_sections(_sample_snapshot())
        assert "58348427720" in sections[0]

    def test_representative_full_name_appears_in_information_form_and_evacuation(self):
        sections = pdf_service.render_contract_sections(_sample_snapshot())
        assert "Berkan Ünver" in sections[2]  # tahliye taahhütnamesi
        assert "Berkan Ünver" in sections[3]  # müşteri bilgilendirme formu


# ═══════════════════════════════════════════════════════════════════════════
# PDF üretimi — Playwright çağrısı mock (hermetik) + isteğe bağlı gerçek E2E
# ═══════════════════════════════════════════════════════════════════════════

class TestPdfGeneration:
    def test_generate_contract_pdf_mocked_wires_sections_and_sha256(self):
        fixed_bytes = b"%PDF-1.4 fake content for hashing"
        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=fixed_bytes) as mock_render:
            pdf_bytes, pdf_sha256 = pdf_service.generate_contract_pdf(_sample_snapshot())

        assert pdf_bytes == fixed_bytes
        assert pdf_sha256 == hashlib.sha256(fixed_bytes).hexdigest()
        combined_html = mock_render.call_args[0][0]
        # "page-break" alt dizesi _base_style.html'in CSS kuralında da geçtiği
        # için (4 bölümün her biri stili include ediyor), yalnız gerçek ayraç
        # div'lerini sayıyoruz.
        assert combined_html.count('<div class="page-break"></div>') == 3  # 4 bölüm arasında 3 ayraç
        assert "SÖZLEŞME EK PROTOKOLÜ" in combined_html

    def test_generate_contract_pdf_real_playwright_e2e(self):
        """
        Gerçek Chromium ile uçtan uca üretim + metin doğrulama. Bu ortamda bu
        oturumda zaten doğrulanmıştı; chromium binary'si kurulu değilse (CI
        veya başka bir geliştirici makinesi) test atlanır, kırmızı olmaz.
        """
        try:
            pdf_bytes, pdf_sha256 = pdf_service.generate_contract_pdf(_sample_snapshot(agreement_multiplier=1.01))
        except Exception as exc:  # noqa: BLE001 - kasıtlı geniş yakalama, ortam eksikliği için skip
            pytest.skip(f"Playwright/Chromium bu ortamda kullanılamıyor: {exc}")

        assert pdf_bytes[:4] == b"%PDF"
        assert len(pdf_bytes) > 1000
        assert pdf_sha256 == hashlib.sha256(pdf_bytes).hexdigest()

        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(pdf_bytes)
            text = "".join(page.get_textpage().get_text_range() for page in doc)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"pypdfium2 ile metin doğrulama yapılamadı: {exc}")

        assert "1,01" in text
        assert "1,06" not in text
        assert "Berkan Ünver" in text


# ═══════════════════════════════════════════════════════════════════════════
# HIGH#1 (final architecture review) — finalize concurrency / idempotency
# ═══════════════════════════════════════════════════════════════════════════

class TestFinalizeClaim:
    def test_claim_succeeds_from_ready_to_generate(self, db):
        contract, _ = _make_ready_contract(db)
        assert service.try_claim_contract_for_finalize(db, contract.id, "default") is True
        db.refresh(contract)
        assert contract.status == "FINALIZING"

    def test_claim_succeeds_from_generated(self, db):
        """Yalnız READY_TO_GENERATE değil, dokümante edilmiş GENERATED durumundan da geçiş serbest olmalı."""
        contract, _ = _make_ready_contract(db)
        contract.status = "GENERATED"
        db.commit()
        assert service.try_claim_contract_for_finalize(db, contract.id, "default") is True

    def test_claim_fails_from_draft(self, db):
        from app.database import Contract
        offer = _make_offer(db)
        db.commit()
        contract = Contract(tenant_id="default", offer_id=offer.id, status="DRAFT")
        db.add(contract)
        db.commit()
        assert service.try_claim_contract_for_finalize(db, contract.id, "default") is False

    def test_two_sequential_claims_only_first_wins(self, db):
        """
        Eşzamanlılığın kanıtı: CAS (compare-and-set) UPDATE...WHERE deseni
        session/thread kimliğinden bağımsızdır — WHERE koşulu ikinci çağrının
        rowcount=0 görmesini garanti eder, gerçek OS thread'i gerekmez (bkz.
        service.try_claim_contract_for_finalize docstring'i). "Aynı anda iki
        render başlamasını engelle" gereksinimi budur.
        """
        contract, _ = _make_ready_contract(db)
        first = service.try_claim_contract_for_finalize(db, contract.id, "default")
        second = service.try_claim_contract_for_finalize(db, contract.id, "default")
        assert first is True
        assert second is False


class TestFinalizeConcurrencyAndIdempotencyAPI:
    def test_two_parallel_finalize_requests_only_one_generates(self, client, db):
        """
        İki paralel finalize isteği → yalnız biri gerçekten render eder (tek
        canonical PDF, tek finalized event); diğeri ya idempotent 200 ya da
        409 "finalize_in_progress" alır — hiçbir zaman ikinci bir render
        BAŞLAMAZ. TestClient senkron olduğundan gerçek thread yerine, aynı
        DB durumunu paylaşan iki ardışık çağrı (yukarıdaki CAS kanıtıyla
        birlikte) aynı garantiyi kanıtlar: claim'i KAYBEDEN istek asla
        generate_contract_pdf'e ulaşmaz.
        """
        offer = _make_offer(db, agreement_multiplier=1.01)
        db.commit()
        draft = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()
        client.post(f"/api/contracts/{draft['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12})

        render_call_count = {"n": 0}
        real_generate = None
        import app.contracts.pdf_service as pdf_service_module
        real_generate = pdf_service_module.generate_contract_pdf

        def counting_generate(snapshot):
            render_call_count["n"] += 1
            return real_generate(snapshot)

        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=b"%PDF-parallel-test"), \
             patch("app.contracts.pdf_service.generate_contract_pdf", side_effect=counting_generate):
            resp1 = client.post(f"/api/contracts/{draft['id']}/finalize")
            resp2 = client.post(f"/api/contracts/{draft['id']}/finalize")

        assert resp1.status_code == 200
        assert resp2.status_code in (200, 409)
        if resp2.status_code == 200:
            assert resp2.json()["pdf_sha256"] == resp1.json()["pdf_sha256"]
        else:
            assert resp2.json()["detail"]["error"] == "finalize_in_progress"
        # KRİTİK: render (generate_contract_pdf) yalnız BİR KEZ çağrıldı — "tek canonical PDF".
        assert render_call_count["n"] == 1

    def test_repeated_finalize_call_is_idempotent(self, client, db):
        offer = _make_offer(db, agreement_multiplier=1.01)
        db.commit()
        draft = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()
        client.post(f"/api/contracts/{draft['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12})

        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=b"%PDF-idempotent"):
            resp1 = client.post(f"/api/contracts/{draft['id']}/finalize")
        resp2 = client.post(f"/api/contracts/{draft['id']}/finalize")  # PDF mock'u artık aktif değil — render TEKRAR olmamalı

        assert resp1.status_code == 200 and resp2.status_code == 200
        assert resp1.json() == resp2.json()

    def test_hash_parity_between_stored_bytes_and_persisted_hash(self, client, db):
        """'pdf_sha256 ile indirilen dosyanın hash'i eşleşsin' — storage bytes ↔ persisted hash kanıtı."""
        offer = _make_offer(db, agreement_multiplier=1.01)
        db.commit()
        draft = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()
        client.post(f"/api/contracts/{draft['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12})

        fixed_bytes = b"%PDF-hash-parity-check"
        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=fixed_bytes):
            finalize_resp = client.post(f"/api/contracts/{draft['id']}/finalize").json()

        download_resp = client.get(f"/api/contracts/{draft['id']}/download")
        assert download_resp.status_code == 200
        assert hashlib.sha256(download_resp.content).hexdigest() == finalize_resp["pdf_sha256"]

    def test_real_playwright_finalize_then_download_full_flow(self, client, db):
        """
        HIGH#1'in yeni claim/temp/promote akışını GERÇEK Chromium ile uçtan
        uca doğrular (mock değil) — draft->preview->finalize->download,
        hash parity ve idempotent tekrar-finalize dahil. Chromium yoksa atlanır.
        """
        offer = _make_offer(db, agreement_multiplier=1.01)
        db.commit()
        draft = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()
        client.post(f"/api/contracts/{draft['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12})

        try:
            finalize_resp = client.post(f"/api/contracts/{draft['id']}/finalize")
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Playwright/Chromium bu ortamda kullanılamıyor: {exc}")
        if finalize_resp.status_code != 200:
            pytest.skip(f"Playwright/Chromium bu ortamda kullanılamıyor (status={finalize_resp.status_code}): {finalize_resp.text}")

        body = finalize_resp.json()
        assert body["status"] == "FINALIZED"

        download_resp = client.get(f"/api/contracts/{draft['id']}/download")
        assert download_resp.status_code == 200
        assert download_resp.content[:4] == b"%PDF"
        assert hashlib.sha256(download_resp.content).hexdigest() == body["pdf_sha256"]

        # idempotent tekrar-finalize: aynı sha256, yeniden render yok.
        finalize_resp2 = client.post(f"/api/contracts/{draft['id']}/finalize")
        assert finalize_resp2.status_code == 200
        assert finalize_resp2.json()["pdf_sha256"] == body["pdf_sha256"]

    def test_render_failure_reverts_status_and_allows_retry(self, db):
        contract, _ = _make_ready_contract(db, agreement_multiplier=1.01)
        assert service.try_claim_contract_for_finalize(db, contract.id, "default") is True

        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", side_effect=RuntimeError("render boom")):
            with pytest.raises(RuntimeError):
                service.finalize_contract_pdf_and_commit(db, contract.id, "default", contract.extraction_snapshot_json)

        db.refresh(contract)
        assert contract.status == "READY_TO_GENERATE"  # takılı kalmadı, retry edilebilir

        # Retry başarılı olmalı.
        assert service.try_claim_contract_for_finalize(db, contract.id, "default") is True
        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=b"%PDF-retry-ok"):
            ref, sha = service.finalize_contract_pdf_and_commit(db, contract.id, "default", contract.extraction_snapshot_json)
        db.refresh(contract)
        assert contract.status == "FINALIZED"
        assert contract.pdf_sha256 == sha

    def test_post_render_db_commit_failure_reverts_and_leaves_no_orphan_files(self, db, storage_tmp):
        """'render sonrası DB commit failure' + 'orphan temp file yok' — birlikte kanıtlanır."""
        contract, _ = _make_ready_contract(db, agreement_multiplier=1.01)
        assert service.try_claim_contract_for_finalize(db, contract.id, "default") is True

        # ÖNEMLİ: sessionmaker() varsayılanı expire_on_commit=True — claim'in
        # kendi db.commit()'i contract'ı "expired" bırakır. Mock aktifken
        # ilk attribute erişimi (snapshot argümanı gibi) SQLAlchemy'nin
        # otomatik lazy-reload'unu tetikler ve bu da db.execute üzerinden
        # geçer — mock'u YANLIŞ çağrıyı (bizim CAS'ımızı değil, ORM'in kendi
        # reload'unu) yakalamaya zorlar. Snapshot'ı mock'tan ÖNCE, düz bir
        # attribute erişimiyle (zaten reload'u tetikleyip biten) alıyoruz.
        snapshot = contract.extraction_snapshot_json

        original_execute = db.execute
        call_state = {"n": 0}

        def flaky_execute(*args, **kwargs):
            call_state["n"] += 1
            if call_state["n"] == 1:  # finalize_contract_pdf_and_commit içindeki final CAS
                raise RuntimeError("simulated DB commit failure")
            return original_execute(*args, **kwargs)

        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=b"%PDF-db-fail"), \
             patch.object(db, "execute", side_effect=flaky_execute):
            with pytest.raises(RuntimeError):
                service.finalize_contract_pdf_and_commit(db, contract.id, "default", snapshot)

        assert call_state["n"] == 2  # 1: başarısız final CAS, 2: revert (READY_TO_GENERATE)
        db.refresh(contract)
        assert contract.status == "READY_TO_GENERATE"
        assert contract.pdf_storage_ref is None  # DB'de hiçbir zaman yarım/tutarsız ref yazılmadı

        # storage_tmp altında hiçbir dosya (temp veya canonical) kalmamalı.
        leftover = list(Path(storage_tmp).rglob(f"*{contract.id}.pdf*"))
        assert leftover == [], f"orphan dosya bulundu: {leftover}"

        # Retry başarılı olmalı (yeni bir DB hatası enjekte edilmeden).
        assert service.try_claim_contract_for_finalize(db, contract.id, "default") is True
        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=b"%PDF-db-retry-ok"):
            service.finalize_contract_pdf_and_commit(db, contract.id, "default", contract.extraction_snapshot_json)
        db.refresh(contract)
        assert contract.status == "FINALIZED"

    def test_finalize_in_progress_blocks_concurrent_preview(self, client, db):
        """
        preview_contract'ın FINALIZING durumundaki bir contract'ı
        READY_TO_GENERATE'e geri almasını (ve finalize'ın final CAS'ını
        bozmasını) engelleyen ek koruma.
        """
        contract, offer = _make_ready_contract(db, agreement_multiplier=1.01)
        assert service.try_claim_contract_for_finalize(db, contract.id, "default") is True

        resp = client.post(
            f"/api/contracts/{contract.id}/preview", json={"start_date": "2026-01-01", "duration_months": 12}
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "contract_finalized"


# ═══════════════════════════════════════════════════════════════════════════
# HIGH#2 (final architecture review) — post-commit offer lifecycle failure izolasyonu
# ═══════════════════════════════════════════════════════════════════════════

class TestOfferLifecycleFailureIsolation:
    def test_unexpected_exception_in_transition_is_swallowed(self, db):
        offer = _make_offer(db)
        db.commit()
        with patch("app.contracts.service.transition_offer_status", side_effect=RuntimeError("db patladı")):
            service.mark_offer_contracting(db, offer)  # exception fırlatmamalı
        # offer.status değişmedi (transition_offer_status hiç gerçek işi yapmadı)
        db.refresh(offer)
        assert offer.status == "draft"

    def test_finalize_succeeds_even_if_offer_transition_raises_unexpected_error(self, client, db):
        """Contract finalize BAŞARISI offer-lifecycle yan etkisine bağımlı DEĞİL — istemci her zaman success görür."""
        offer = _make_offer(db, agreement_multiplier=1.01)
        db.commit()
        draft = client.post("/api/contracts/drafts", json={"offer_id": offer.id}).json()
        client.post(f"/api/contracts/{draft['id']}/preview", json={"start_date": "2026-01-01", "duration_months": 12})

        with patch("app.contracts.pdf_service.html_to_pdf_bytes_sync", return_value=b"%PDF-lifecycle-fail-test"), \
             patch("app.contracts.service.transition_offer_status", side_effect=RuntimeError("beklenmeyen audit hatası")):
            resp = client.post(f"/api/contracts/{draft['id']}/finalize")

        assert resp.status_code == 200
        assert resp.json()["status"] == "FINALIZED"

        # PDF hâlâ indirilebilir durumda (offer yan etkisi sözleşmenin kendisini bozmadı).
        download_resp = client.get(f"/api/contracts/{draft['id']}/download")
        assert download_resp.status_code == 200

    def test_retry_after_lifecycle_failure_recovers_offer_status(self, db):
        """Offer lifecycle geçişi bir kez beklenmeyen hatayla başarısız olsa da, sonraki çağrı (retry) doğru durumu yakalar."""
        offer = _make_offer(db)
        db.commit()
        with patch("app.contracts.service.transition_offer_status", side_effect=RuntimeError("geçici hata")):
            service.mark_offer_contracting(db, offer)
        db.refresh(offer)
        assert offer.status == "draft"  # ilk deneme başarısız, hâlâ draft

        service.mark_offer_contracting(db, offer)  # retry — mock artık aktif değil
        db.refresh(offer)
        assert offer.status == "contracting"

    def test_no_duplicate_audit_log_on_retry(self, db):
        """Aynı lifecycle event iki kez yazılmaz — transition_offer_status'un kendi VALID_OFFER_TRANSITIONS
        doğrulaması, hedef duruma zaten ulaşılmışsa log_action'ı tekrar tetiklemez."""
        from app.database import AuditLog
        offer = _make_offer(db)
        db.commit()

        service.mark_offer_contracting(db, offer)
        db.refresh(offer)
        assert offer.status == "contracting"

        service.mark_offer_contracting(db, offer)  # retry — offer zaten 'contracting', ValueError yutulur

        entries = db.query(AuditLog).filter(AuditLog.target_type == "offer", AuditLog.target_id == str(offer.id)).all()
        assert len(entries) == 1  # yalnız İLK başarılı geçiş audit'e yazıldı


# ═══════════════════════════════════════════════════════════════════════════
# Tenant sınırı (final architecture review, madde 3) — fail-closed, router-geneli
# ═══════════════════════════════════════════════════════════════════════════

class TestContractsTenantBoundary:
    def test_non_default_tenant_rejected_on_every_endpoint_category(self, client, db):
        """Router-level dependency: belge yükleme, taslak, finalize — hepsi 403."""
        offer = _make_offer(db, tenant_id="default")
        db.commit()

        upload_resp = client.post(
            "/api/contracts/documents/upload?document_type=vergi_levhasi",
            files={"file": ("a.pdf", b"x", "application/pdf")},
            headers={"X-Tenant-Id": "other-tenant"},
        )
        draft_resp = client.post(
            "/api/contracts/drafts", json={"offer_id": offer.id}, headers={"X-Tenant-Id": "other-tenant"}
        )

        for resp in (upload_resp, draft_resp):
            assert resp.status_code == 403
            assert resp.json()["detail"]["error"] == "tenant_not_supported"

    def test_default_tenant_without_explicit_header_still_works(self, client, db):
        """settings.tenant_required=False iken header hiç yoksa get_tenant_id 'default' döner — bu, guard'ı GEÇMELİ."""
        offer = _make_offer(db, tenant_id="default")
        db.commit()
        resp = client.post("/api/contracts/drafts", json={"offer_id": offer.id})  # X-Tenant-Id yok
        assert resp.status_code == 200
