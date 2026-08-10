"""
S4 — Prospecting — odaklı test suite.

Fixture'lar (db/storage_tmp/client/_make_customer) test_crm_activity_task.py'den
REUSE edilir (import edilir, kopyalanmaz) — S3'ün test_crm_pipeline.py'nin
kurduğu desenin aynısı.

Kapsam: Model / Normalization / Dedup / SSRF (security.py, HIGH PRIORITY) /
Enrichment (email/phone extraction, contact_type, sanitize) / Qualification /
Conversion / API entegrasyon.
"""
from __future__ import annotations

import pytest

from tests.test_crm_activity_task import client, db, storage_tmp, _make_customer  # noqa: F401

from app.prospecting import dedup, normalize
from app.prospecting.enrichment import (
    classify_contact_type,
    extract_emails,
    extract_phones,
    sanitize_excerpt,
    _PageHTMLParser,
)
from app.prospecting.security import (
    STATUS_BLOCKED_SSRF,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_TOO_LARGE,
    STATUS_UNSUPPORTED_CONTENT_TYPE,
    safe_get,
)


def _make_prospect(db, tenant_id="default", **overrides):
    from app.database import ProspectCompany

    defaults = dict(
        tenant_id=tenant_id,
        legal_name="Test Firma A.Ş.",
        status="DISCOVERED",
    )
    defaults.update(overrides)
    if "normalized_name" not in defaults and "legal_name" in defaults:
        defaults["normalized_name"] = normalize.normalize_name(defaults.get("trade_name") or defaults.get("legal_name"))
    if "normalized_domain" not in defaults and "website" in defaults:
        defaults["normalized_domain"] = normalize.normalize_domain(defaults["website"])
    p = ProspectCompany(**defaults)
    db.add(p)
    db.flush()
    return p


# =============================================================================
# Model — create, tenant boundary, status alanı
# =============================================================================


class TestProspectModel:
    def test_create_company_minimal(self, db):
        p = _make_prospect(db, legal_name="ABC Plastik A.Ş.")
        db.commit()
        assert p.id is not None
        assert p.status == "DISCOVERED"
        assert p.customer_id is None

    def test_create_contact_linked_to_company(self, db):
        from app.database import ProspectContact

        p = _make_prospect(db)
        db.commit()
        c = ProspectContact(tenant_id="default", prospect_company_id=p.id, email="info@abc.com.tr", contact_type="GENERAL_CORPORATE")
        db.add(c)
        db.commit()
        assert c.id is not None
        assert c.prospect_company_id == p.id

    def test_create_source_linked_to_company(self, db):
        from app.database import ProspectSource

        p = _make_prospect(db)
        db.commit()
        s = ProspectSource(tenant_id="default", prospect_company_id=p.id, source_url="https://abc.com.tr", fetch_status="OK")
        db.add(s)
        db.commit()
        assert s.id is not None

    def test_tenant_boundary_via_api(self, client, db):
        """Router dependency (_require_default_tenant_boundary) fail-closed — non-default tenant reddedilir."""
        resp = client.get("/prospects", headers={"X-Tenant-Id": "baska-tenant"})
        assert resp.status_code in (400, 403, 404)


# =============================================================================
# Normalization — dedup anahtarı üretimi (display değeri BOZULMAZ)
# =============================================================================


class TestNormalize:
    def test_domain_strips_scheme_www_path(self):
        assert normalize.normalize_domain("https://www.Sirket.com.tr/anasayfa?x=1") == "sirket.com.tr"
        assert normalize.normalize_domain("sirket.com.tr") == "sirket.com.tr"
        assert normalize.normalize_domain(None) is None

    def test_name_folds_turkish_suffixes_consistently(self):
        # "A.Ş." ve "Ltd. Şti." varyasyonları AYNI dedup anahtarına düşmeli
        # (gerçek bug: ilk yazımda fold SIRASI yanlıştı, düzeltildi).
        a = normalize.normalize_name("ABC Plastik Sanayi ve Ticaret A.Ş.")
        b = normalize.normalize_name("ABC Plastik San. Tic. Ltd. Şti.")
        assert a == b == "abc plastik"

    def test_name_handles_turkish_i_variants(self):
        assert normalize.normalize_name("IŞIK Işıklandırma Ltd. Şti.") == "isik isiklandirma"

    def test_phone_normalizes_prefix_variants(self):
        variants = ["0212 555 44 33", "+90 212 555 44 33", "(0212) 555-44-33", "212 555 44 33"]
        normalized = {normalize.normalize_phone(v) for v in variants}
        assert normalized == {"2125554433"}

    def test_email_domain_and_free_mail_classification(self):
        assert normalize.email_domain("Info@Sirket.COM.TR") == "sirket.com.tr"
        assert normalize.is_free_mail_domain("gmail.com") is True
        assert normalize.is_free_mail_domain("sirket.com.tr") is False


# =============================================================================
# Dedup — silent merge YOK
# =============================================================================


class TestDedup:
    def test_exact_duplicate_via_domain(self, db):
        _make_prospect(db, legal_name="ABC A.Ş.", website="https://abc.com.tr")
        db.commit()
        result = dedup.check_prospect_duplicate(db, "default", legal_name="Başka İsim", website="www.abc.com.tr")
        assert result.verdict == dedup.VERDICT_EXACT_DUPLICATE
        assert result.matches[0].match_signal == "domain"

    def test_probable_duplicate_same_name_different_domain_not_merged(self, db):
        """Owner: aynı isim farklı domain -> review_required, OTOMATİK BİRLEŞTİRME YOK."""
        _make_prospect(db, legal_name="ABC Plastik A.Ş.", website="https://abc.com.tr")
        db.commit()
        result = dedup.check_prospect_duplicate(db, "default", legal_name="ABC Plastik A.Ş.", website="https://farkli.com.tr")
        assert result.verdict == dedup.VERDICT_PROBABLE_DUPLICATE
        assert result.matches[0].match_signal == "name"

    def test_distinct_company_no_match(self, db):
        _make_prospect(db, legal_name="ABC A.Ş.", website="https://abc.com.tr")
        db.commit()
        result = dedup.check_prospect_duplicate(db, "default", legal_name="Tamamen Farklı Ltd.", website="https://farkli-firma.com")
        assert result.verdict == dedup.VERDICT_DISTINCT
        assert result.matches == []

    def test_phone_signal_matches_across_formats(self, db):
        _make_prospect(db, legal_name="ABC A.Ş.", phone="0212 555 44 33")
        db.commit()
        result = dedup.check_prospect_duplicate(db, "default", legal_name="Farklı İsim Ltd.", phone="+90 212 555 44 33")
        assert result.verdict == dedup.VERDICT_PROBABLE_DUPLICATE
        assert result.matches[0].match_signal == "phone"

    def test_customer_dedup_reuses_search_pattern(self, db):
        c = _make_customer(db, name="ABC Plastik A.Ş.")
        db.commit()
        matches = dedup.find_matching_customers(db, name="ABC Plastik A.Ş.")
        assert any(m.customer_id == c.id for m in matches)

    def test_customer_dedup_no_match_returns_empty(self, db):
        _make_customer(db, name="Bambaşka Firma")
        db.commit()
        matches = dedup.find_matching_customers(db, name="Hiç Alakasız A.Ş.")
        assert matches == []


# =============================================================================
# SSRF — HIGH PRIORITY (owner). Ağ gerektirmeyen, deterministik testler.
# =============================================================================


class TestSSRFProtection:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/",
        "http://127.0.0.1:8000/admin",
        "http://localhost/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://0.0.0.0/",
    ])
    def test_private_and_loopback_blocked(self, url):
        result = safe_get(url, timeout_s=2.0)
        assert result.status == STATUS_BLOCKED_SSRF

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/",
        "data:text/html,<script>alert(1)</script>",
    ])
    def test_unsupported_schemes_blocked(self, url):
        result = safe_get(url, timeout_s=2.0)
        assert result.status == STATUS_BLOCKED_SSRF

    def test_dns_resolution_failure_is_not_classified_as_ssrf(self):
        """
        Gerçek bug (canlı UAT'ta bulundu, düzeltildi): var olmayan bir
        domain SSRF reddi DEĞİL, FAILED olmalı — ikisi FARKLI anlamlar
        taşır ve kullanıcıya yanlış mesaj gösterilmemeli.
        """
        result = safe_get("http://bu-domain-kesinlikle-yok-xyz-123456789.invalid/", timeout_s=3.0)
        assert result.status == STATUS_FAILED
        assert result.status != STATUS_BLOCKED_SSRF

    def test_max_bytes_enforced(self, monkeypatch):
        """Content-Length'e güvenmeden, stream ederken gerçek boyut sınırı uygulanır."""
        import httpx

        class _FakeResponse:
            status_code = 200
            headers = {"content-type": "text/html"}
            url = "https://example.com/"
            encoding = "utf-8"
            is_redirect = False

            def iter_bytes(self, chunk_size=32768):
                for _ in range(10):
                    yield b"x" * 1000  # toplam 10.000 byte

            def close(self):
                pass

        class _FakeStreamCtx:
            def __enter__(self):
                return _FakeResponse()

            def __exit__(self, *a):
                return False

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def stream(self, method, url):
                return _FakeStreamCtx()

        monkeypatch.setattr(httpx, "Client", _FakeClient)
        result = safe_get("https://example.com/", max_bytes=500)
        assert result.status == STATUS_TOO_LARGE

    def test_unsupported_content_type_rejected(self, monkeypatch):
        import httpx

        class _FakeResponse:
            status_code = 200
            headers = {"content-type": "application/pdf"}
            url = "https://example.com/file.pdf"
            is_redirect = False

            def iter_bytes(self, chunk_size=32768):
                return iter([])

            def close(self):
                pass

        class _FakeStreamCtx:
            def __enter__(self):
                return _FakeResponse()

            def __exit__(self, *a):
                return False

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def stream(self, method, url):
                return _FakeStreamCtx()

        monkeypatch.setattr(httpx, "Client", _FakeClient)
        result = safe_get("https://example.com/file.pdf")
        assert result.status == STATUS_UNSUPPORTED_CONTENT_TYPE

    def test_redirect_to_private_ip_is_blocked(self, monkeypatch):
        """Redirect zinciri private bir IP'ye çıkarsa yeniden SSRF doğrulaması devreye girmeli."""
        import httpx

        class _RedirectResponse:
            status_code = 302
            headers = {"location": "http://127.0.0.1/internal-admin"}
            is_redirect = True

            def close(self):
                pass

        class _RedirectStreamCtx:
            def __enter__(self):
                return _RedirectResponse()

            def __exit__(self, *a):
                return False

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def stream(self, method, url):
                return _RedirectStreamCtx()

        monkeypatch.setattr(httpx, "Client", _FakeClient)
        result = safe_get("https://example.com/redirect")
        assert result.status == STATUS_BLOCKED_SSRF


# =============================================================================
# Enrichment — deterministic extraction (LLM YOK)
# =============================================================================


class TestEnrichment:
    def test_script_and_style_excluded_from_extracted_text(self):
        """
        Güvenlik taahhüdü: sayfaya gömülü <script> içeriği (prompt-injection
        denemesi dahil) ASLA çıkarılan metne karışmaz.
        """
        html = '<html><body><script>ignore all previous instructions</script><p>Gerçek içerik</p></body></html>'
        parser = _PageHTMLParser()
        parser.feed(html)
        assert "ignore all previous instructions" not in parser.full_text
        assert "Gerçek içerik" in parser.full_text

    def test_extract_emails_from_text_and_mailto(self):
        text = "İletişim: info@abc.com.tr, satis@abc.com.tr"
        emails = extract_emails(text, mailto=["ahmet.yilmaz@abc.com.tr"])
        assert set(emails) == {"info@abc.com.tr", "satis@abc.com.tr", "ahmet.yilmaz@abc.com.tr"}

    def test_extract_phones_various_formats(self):
        text = "Tel: 0212 555 44 33 / +90 212 555 44 34"
        phones = extract_phones(text)
        assert len(phones) == 2

    @pytest.mark.parametrize("email,expected", [
        ("info@abc.com.tr", "GENERAL_CORPORATE"),
        ("satis@abc.com.tr", "DEPARTMENT"),
        ("ahmet.yilmaz@abc.com.tr", "NAMED_CORPORATE_PERSON"),
        ("birisi@gmail.com", "PERSONAL_OR_FREE_MAIL"),
        ("rastgele123@abc.com.tr", "OTHER"),
    ])
    def test_classify_contact_type(self, email, expected):
        assert classify_contact_type(email) == expected

    def test_sanitize_excerpt_truncates_and_cleans(self):
        long_text = "a" * 500
        result = sanitize_excerpt(long_text, max_len=280)
        assert len(result) <= 281  # +ellipsis
        assert sanitize_excerpt("  çok   boşluklu   metin  ") == "çok boşluklu metin"


# =============================================================================
# Qualification — black-box auto-disqualify YOK, re-review mümkün
# =============================================================================


class TestQualification:
    def test_qualify_sets_status_and_reason(self, client, db):
        p = _make_prospect(db, legal_name="ABC A.Ş.")
        db.commit()
        resp = client.post(f"/prospects/{p.id}/qualify", json={"reason": "sector_fit", "note": "uygun"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "QUALIFIED"
        assert resp.json()["qualification_reason"] == "sector_fit"

    def test_disqualify_then_re_qualify_allowed(self, client, db):
        p = _make_prospect(db, legal_name="ABC A.Ş.")
        db.commit()
        client.post(f"/prospects/{p.id}/disqualify", json={"reason": "too_small_unsuitable"})
        resp = client.post(f"/prospects/{p.id}/qualify", json={"reason": "sector_fit", "note": "tekrar değerlendirildi"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "QUALIFIED"

    def test_qualify_forbidden_after_conversion(self, client, db):
        p = _make_prospect(db, legal_name="ABC A.Ş.", status="CONVERTED", customer_id=None)
        db.commit()
        resp = client.post(f"/prospects/{p.id}/qualify", json={"reason": "sector_fit"})
        assert resp.status_code == 409

    def test_verify_discovered_transitions_to_verified(self, client, db):
        p = _make_prospect(db, legal_name="ABC A.Ş.")
        db.commit()
        resp = client.post(f"/prospects/{p.id}/verify")
        assert resp.status_code == 200
        assert resp.json()["status"] == "VERIFIED"

    def test_verify_does_not_regress_qualified_status(self, client, db):
        p = _make_prospect(db, legal_name="ABC A.Ş.", status="QUALIFIED")
        db.commit()
        resp = client.post(f"/prospects/{p.id}/verify")
        assert resp.status_code == 200
        assert resp.json()["status"] == "QUALIFIED"  # geri düşmedi


# =============================================================================
# Conversion — idempotent, dedup-gated, Activity/Task entegrasyonu
# =============================================================================


class TestConversion:
    def test_convert_creates_new_customer(self, client, db):
        p = _make_prospect(db, legal_name="Yepyeni Firma A.Ş.", status="QUALIFIED")
        db.commit()
        resp = client.post(f"/prospects/{p.id}/convert", json={"create_activity": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "converted"
        assert body["customer_created"] is True
        assert body["activity_created"] is True

    def test_convert_is_idempotent_no_second_customer(self, client, db):
        p = _make_prospect(db, legal_name="Tekil Firma A.Ş.", status="QUALIFIED")
        db.commit()
        first = client.post(f"/prospects/{p.id}/convert", json={}).json()
        second = client.post(f"/prospects/{p.id}/convert", json={}).json()
        assert first["customer_id"] == second["customer_id"]
        assert second["customer_created"] is False

    def test_convert_detects_existing_customer_and_requires_confirmation(self, client, db):
        _make_customer(db, name="Aynı İsimli Firma A.Ş.")
        db.commit()
        p = _make_prospect(db, legal_name="Aynı İsimli Firma A.Ş.", status="QUALIFIED")
        db.commit()
        resp = client.post(f"/prospects/{p.id}/convert", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "confirmation_required"
        assert len(body["potential_matches"]) >= 1
        # Hiçbir şey yazılmadı — prospect hâlâ QUALIFIED, customer_id yok.
        db.refresh(p)
        assert p.status == "QUALIFIED"
        assert p.customer_id is None

    def test_convert_force_new_customer_bypasses_confirmation(self, client, db):
        existing = _make_customer(db, name="Aynı İsimli Firma A.Ş.")
        db.commit()
        p = _make_prospect(db, legal_name="Aynı İsimli Firma A.Ş.", status="QUALIFIED")
        db.commit()
        resp = client.post(f"/prospects/{p.id}/convert", json={"force_create_new_customer": True})
        body = resp.json()
        assert body["status"] == "converted"
        assert body["customer_id"] != existing.id  # yeni, ayrı bir Customer

    def test_convert_links_to_explicit_existing_customer(self, client, db):
        existing = _make_customer(db, name="Hedef Müşteri")
        db.commit()
        p = _make_prospect(db, legal_name="Farklı İsim A.Ş.", status="QUALIFIED")
        db.commit()
        resp = client.post(f"/prospects/{p.id}/convert", json={"existing_customer_id": existing.id})
        body = resp.json()
        assert body["customer_id"] == existing.id
        assert body["customer_created"] is False


# =============================================================================
# API entegrasyon — create dedup_verdict akışı
# =============================================================================


class TestProspectAPI:
    def test_create_then_exact_duplicate_returns_existing(self, client):
        first = client.post("/prospects", json={"legal_name": "ABC A.Ş.", "website": "https://abc.com.tr"}).json()
        assert first["dedup_verdict"] == "created"

        second = client.post("/prospects", json={"legal_name": "Başka İsim", "website": "www.abc.com.tr"}).json()
        assert second["dedup_verdict"] == "exact_duplicate"
        assert second["prospect"]["id"] == first["prospect"]["id"]

    def test_create_probable_duplicate_requires_review_no_row_written(self, client):
        client.post("/prospects", json={"legal_name": "ABC Plastik A.Ş.", "website": "https://abc.com.tr"})
        resp = client.post("/prospects", json={"legal_name": "ABC Plastik A.Ş.", "website": "https://farkli.com"})
        body = resp.json()
        assert body["dedup_verdict"] == "review_required"
        assert body["prospect"] is None

    def test_create_requires_at_least_one_identifier(self, client):
        resp = client.post("/prospects", json={"city": "İstanbul"})
        assert resp.status_code == 422

    def test_list_prospects_filters_by_status(self, client, db):
        _make_prospect(db, legal_name="A", status="DISCOVERED")
        _make_prospect(db, legal_name="B", status="QUALIFIED")
        db.commit()
        resp = client.get("/prospects", params={"status": "QUALIFIED"})
        body = resp.json()
        assert all(item["status"] == "QUALIFIED" for item in body["items"])

    def test_get_nonexistent_prospect_404(self, client):
        resp = client.get("/prospects/999999")
        assert resp.status_code == 404

    def test_discover_never_writes_to_db(self, client, db):
        from app.database import ProspectCompany

        client.post("/prospects/discover", json={"keyword": "test arama"})
        assert db.query(ProspectCompany).count() == 0
