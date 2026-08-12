"""
S5 — Outreach — odaklı test suite.

Fixture'lar (db/storage_tmp/client/_make_customer) test_crm_activity_task.py'den
REUSE edilir (import edilir, kopyalanmaz) — S4'ün test_prospecting.py'siyle
AYNI desen.

Kapsam: Compliance engine (contact_type vs recipient_legal_type AYRIMI,
fail-closed) / Drafting (deterministic template + AI-fallback + footer
ayrımı) / SMTP provider (header-injection/config-validation, GERÇEK ağ YOK) /
Service orkestrasyonu (idempotent send, partial-failure) / CRM entegrasyonu /
HTTP router.

Owner'ın WB8 EK talebi (10.08 mesajı) — iki senaryo ÖZELLİKLE kalıcı hale
getirildi:
  1. test_send_provider_success_but_final_commit_fails_leaves_message_stuck_in_sending
  2. test_approve_then_new_suppression_then_send_is_blocked_by_fresh_recheck
"""
from __future__ import annotations

import smtplib

import pytest
from fastapi import HTTPException

from tests.test_crm_activity_task import client, db, storage_tmp, _make_customer  # noqa: F401

from app.database import (
    ProspectCompany, ProspectContact, ProspectSource, Customer, SuppressionEntry,
    OutreachMessage, Activity, Task,
)
from app.core.config import settings
import app.outreach.service as svc
from app.outreach.compliance import evaluate_email_send_eligibility
from app.outreach.drafting import (
    build_placeholder_context, render_editable_body_from_template, create_draft,
    ensure_default_template, TemplateRenderError, DEFAULT_TEMPLATE_NAME,
)
from app.outreach.sender_profile import (
    OutreachSenderProfile, SenderProfileIncompleteError, get_sender_profile, render_mandatory_footer,
)
from app.outreach.smtp_provider import SmtpMailProvider, SendResult, OutboundMailProvider, get_outbound_mail_provider

TENANT = "default"


# ═══════════════════════════════════════════════════════════════════════════
# Yardımcılar
# ═══════════════════════════════════════════════════════════════════════════

def _make_prospect(db, tenant_id=TENANT, **overrides):
    defaults = dict(tenant_id=tenant_id, legal_name="Test Firma A.Ş.")
    defaults.update(overrides)
    c = ProspectCompany(**defaults)
    db.add(c)
    db.flush()
    return c


def _make_source(db, company_id, tenant_id=TENANT, **overrides):
    defaults = dict(tenant_id=tenant_id, prospect_company_id=company_id, source_url="https://test.example", fetch_status="OK")
    defaults.update(overrides)
    s = ProspectSource(**defaults)
    db.add(s)
    db.flush()
    return s


def _make_contact(db, company_id, tenant_id=TENANT, **overrides):
    defaults = dict(tenant_id=tenant_id, prospect_company_id=company_id, email="info@test.example", contact_type="GENERAL_CORPORATE")
    defaults.update(overrides)
    c = ProspectContact(**defaults)
    db.add(c)
    db.flush()
    db.refresh(c)
    return c


@pytest.fixture()
def sender_profile_configured(monkeypatch):
    """Sahte-TAM sender profile — testler GERÇEK MERSİS/unvan İCAT ETMEZ, yalnız test verisi."""
    monkeypatch.setattr(settings, "outreach_sender_trade_name", "Test Gönderici A.Ş.")
    monkeypatch.setattr(settings, "outreach_sender_mersis_number", "9999999999999999")
    monkeypatch.setattr(settings, "outreach_sender_email", "gonderici@test.invalid")
    monkeypatch.setattr(settings, "outreach_sender_phone", "+90 000 000 00 00")
    monkeypatch.setattr(settings, "outreach_sender_website", "https://test.invalid")
    monkeypatch.setattr(settings, "outreach_sender_privacy_notice_url", "https://test.invalid/aydinlatma")
    monkeypatch.setattr(settings, "outreach_sender_unsubscribe_instruction", "Test: YANIT verin.")
    yield


@pytest.fixture()
def iys_verified(monkeypatch):
    monkeypatch.setattr(settings, "outreach_iys_status", "IYS_VERIFIED")
    yield


class FakeProvider(OutboundMailProvider):
    """Test double — HİÇBİR gerçek ağ bağlantısı yapmaz."""

    def __init__(self, result: SendResult):
        self._result = result
        self.calls: list[dict] = []

    def send(self, *, to_email, subject, body_text, reply_to=None):
        self.calls.append({"to_email": to_email, "subject": subject, "body_text": body_text})
        return self._result


def _approve(db, message_id):
    svc.finalize_draft_message(db, TENANT, message_id, editable_body="Test gövde metni")
    return svc.approve_message(db, TENANT, message_id)


# ═══════════════════════════════════════════════════════════════════════════
# Compliance engine — contact_type vs recipient_legal_type AYRIMI
# ═══════════════════════════════════════════════════════════════════════════

class TestComplianceEngine:
    def test_invalid_email_syntax_blocks_with_exact_reason(self, db):
        r = evaluate_email_send_eligibility(db, TENANT, candidate_email="not-an-email")
        assert r.can_send is False
        assert "EMAIL_INVALID_SYNTAX" in r.reason_codes

    def test_suppressed_email_blocks_regardless_of_category(self, db):
        company = _make_prospect(db)
        _make_source(db, company.id)
        db.add(SuppressionEntry(tenant_id=TENANT, email_normalized="blocked@x.com", reason="USER_REJECTED"))
        db.commit()
        r = evaluate_email_send_eligibility(db, TENANT, candidate_email="blocked@x.com", prospect_company_id=company.id)
        assert r.can_send is False
        assert "SUPPRESSED" in r.reason_codes
        assert r.suppression_status == "SUPPRESSED"

    def test_general_corporate_contact_type_does_NOT_imply_tacir(self, db):
        """
        KRİTİK REGRESYON TESTİ — owner'ın 10.08 düzeltmesi. İlk yazımda
        GENERAL_CORPORATE -> TACIR varsayımı vardı; owner bunu REDDETTİ.
        Bu test bir daha ASLA geri gelmesin diye kalıcı hale getirildi.
        """
        company = _make_prospect(db)
        _make_source(db, company.id)
        db.commit()
        r = evaluate_email_send_eligibility(db, TENANT, candidate_email="info@x.com", prospect_company_id=company.id)
        assert r.contact_type == "GENERAL_CORPORATE"
        assert r.recipient_legal_type == "UNKNOWN"
        assert "RECIPIENT_LEGAL_TYPE_UNVERIFIED" in r.reason_codes
        assert r.kvkk_status == "OK"  # contact_type ekseni bağımsız çalışmaya devam eder

    def test_verified_legal_type_plus_iys_verified_allows_send(self, db, iys_verified):
        """Hard-code 'her zaman false' DEĞİL — DB/config durumu değişince sonuç değişir kanıtı."""
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        db.commit()
        r = evaluate_email_send_eligibility(db, TENANT, candidate_email="info@x.com", prospect_company_id=company.id)
        assert r.can_send is True
        assert r.recipient_legal_type == "TACIR"

    def test_named_corporate_person_requires_kvkk_review(self, db):
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        db.commit()
        r = evaluate_email_send_eligibility(db, TENANT, candidate_email="ahmet.yilmaz@x.com", prospect_company_id=company.id)
        assert r.contact_type == "NAMED_CORPORATE_PERSON"
        assert "KVKK_REVIEW_REQUIRED" in r.reason_codes

    def test_free_mail_requires_kvkk_opt_in(self, db):
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        db.commit()
        r = evaluate_email_send_eligibility(db, TENANT, candidate_email="ahmet@gmail.com", prospect_company_id=company.id)
        assert r.contact_type == "PERSONAL_OR_FREE_MAIL"
        assert "KVKK_OPT_IN_REQUIRED" in r.reason_codes

    def test_missing_source_evidence_blocks(self, db):
        company = _make_prospect(db, verified_legal_type="TACIR")  # kasıtlı: source YOK
        db.commit()
        r = evaluate_email_send_eligibility(db, TENANT, candidate_email="info@x.com", prospect_company_id=company.id)
        assert "SOURCE_EVIDENCE_MISSING" in r.reason_codes
        assert r.source_status == "MISSING"

    def test_wrong_tenant_access_is_fail_closed(self, db):
        company = _make_prospect(db)
        _make_source(db, company.id)
        db.commit()
        r = evaluate_email_send_eligibility(db, "baska-tenant", candidate_email="info@x.com", prospect_company_id=company.id)
        assert "PROSPECT_COMPANY_NOT_FOUND" in r.reason_codes

    def test_test_recipient_whitelist_bypasses_iys_kvkk_but_not_suppression(self, db, monkeypatch):
        monkeypatch.setattr(settings, "outreach_test_recipient_emails", "test@gelka-owner.invalid")
        r = evaluate_email_send_eligibility(db, TENANT, candidate_email="test@gelka-owner.invalid")
        assert r.recipient_category == "TEST_RECIPIENT"
        assert r.can_send is True

        db.add(SuppressionEntry(tenant_id=TENANT, email_normalized="test@gelka-owner.invalid", reason="MANUAL_BLOCK"))
        db.commit()
        r2 = evaluate_email_send_eligibility(db, TENANT, candidate_email="test@gelka-owner.invalid")
        assert r2.can_send is False, "TEST_RECIPIENT dahi suppression'ı bypass EDEMEMELİ"

    def test_iys_unknown_blocks_all_prospect_recipients_by_default(self, db):
        """V1 varsayılanı — owner: 'IYS: UNKNOWN / CREDENTIALS NOT PROVIDED'."""
        assert settings.outreach_iys_status == "IYS_UNKNOWN"
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        db.commit()
        r = evaluate_email_send_eligibility(db, TENANT, candidate_email="info@x.com", prospect_company_id=company.id)
        assert "IYS_STATUS_UNKNOWN" in r.reason_codes


# ═══════════════════════════════════════════════════════════════════════════
# Drafting — deterministic template + footer ayrımı
# ═══════════════════════════════════════════════════════════════════════════

class TestDrafting:
    def test_sender_profile_incomplete_blocks_footer_render(self):
        empty = get_sender_profile()
        assert empty.is_complete is False
        with pytest.raises(SenderProfileIncompleteError):
            render_mandatory_footer(empty)

    def test_full_sender_profile_produces_footer_with_all_fields(self):
        profile = OutreachSenderProfile(
            trade_name="X A.Ş.", mersis_number="123", sender_email="x@x.com", phone="+90 0",
            website="https://x.com", privacy_notice_url="https://x.com/aydinlatma", unsubscribe_instruction="YANIT verin.",
        )
        footer = render_mandatory_footer(profile)
        assert "123" in footer and "X A.Ş." in footer and "aydinlatma" in footer.lower() or "YANIT" in footer

    def test_unresolved_placeholder_raises(self):
        ctx = build_placeholder_context(company_name="X")
        with pytest.raises(TemplateRenderError):
            render_editable_body_from_template("Merhaba $bilinmeyen_alan", ctx)

    def test_create_draft_separates_editable_and_footer(self):
        profile = OutreachSenderProfile(
            trade_name="X A.Ş.", mersis_number="123", sender_email="x@x.com", phone=None,
            website=None, privacy_notice_url="https://x.com/p", unsubscribe_instruction="YANIT.",
        )
        draft = create_draft(company_name="ABC", sender_profile=profile, contact_full_name="Ahmet Yılmaz", use_ai=False)
        assert "123" in draft.system_footer
        assert "123" not in draft.editable_body
        assert draft.used_ai is False

    def test_ai_assist_gracefully_falls_back_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr(settings, "openai_api_key", None)
        profile = OutreachSenderProfile(
            trade_name="X", mersis_number="1", sender_email="x@x.com", phone=None,
            website=None, privacy_notice_url="https://x.com/p", unsubscribe_instruction="Y.",
        )
        draft = create_draft(company_name="ABC", sender_profile=profile, use_ai=True)
        assert draft.used_ai is False
        assert "ABC" in draft.subject

    def test_ensure_default_template_is_idempotent_and_tenant_scoped(self, db):
        t1 = ensure_default_template(db, TENANT)
        db.commit()
        t2 = ensure_default_template(db, TENANT)
        db.commit()
        assert t1.id == t2.id
        assert t1.name == DEFAULT_TEMPLATE_NAME
        t3 = ensure_default_template(db, "baska-tenant")
        db.commit()
        assert t3.id != t1.id


# ═══════════════════════════════════════════════════════════════════════════
# SMTP provider — HİÇBİR gerçek ağ bağlantısı yok
# ═══════════════════════════════════════════════════════════════════════════

class TestSmtpProvider:
    def test_not_configured_fails_closed_without_network(self):
        provider = SmtpMailProvider()
        result = provider.send(to_email="a@b.com", subject="x", body_text="y")
        assert result.success is False
        assert result.error_code == "CONFIG_INCOMPLETE"

    def test_header_injection_in_subject_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "outreach_smtp_host", "fake.invalid")
        monkeypatch.setattr(settings, "outreach_smtp_username", "u@fake.invalid")
        monkeypatch.setattr(settings, "outreach_smtp_password", "fake")
        monkeypatch.setattr(settings, "outreach_sender_email", "u@fake.invalid")
        provider = SmtpMailProvider()
        result = provider.send(to_email="ok@x.com", subject="Merhaba\r\nBcc: evil@evil.com", body_text="x")
        assert result.success is False
        assert result.error_code == "HEADER_INJECTION_REJECTED"

    def test_header_injection_in_to_email_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "outreach_smtp_host", "fake.invalid")
        monkeypatch.setattr(settings, "outreach_smtp_username", "u@fake.invalid")
        monkeypatch.setattr(settings, "outreach_smtp_password", "fake")
        monkeypatch.setattr(settings, "outreach_sender_email", "u@fake.invalid")
        provider = SmtpMailProvider()
        result = provider.send(to_email="ok@x.com\r\nBcc: evil@evil.com", subject="x", body_text="x")
        assert result.success is False
        assert result.error_code == "HEADER_INJECTION_REJECTED"

    def test_invalid_security_mode_rejected_no_silent_fallback(self, monkeypatch):
        monkeypatch.setattr(settings, "outreach_smtp_host", "fake.invalid")
        monkeypatch.setattr(settings, "outreach_smtp_username", "u@fake.invalid")
        monkeypatch.setattr(settings, "outreach_smtp_password", "fake")
        monkeypatch.setattr(settings, "outreach_sender_email", "u@fake.invalid")
        monkeypatch.setattr(settings, "outreach_smtp_security", "not_a_real_mode")
        provider = SmtpMailProvider()
        result = provider.send(to_email="ok@x.com", subject="x", body_text="x")
        assert result.success is False
        assert result.error_code == "CONFIG_INVALID"

    def test_get_outbound_mail_provider_returns_provider_instance(self):
        assert isinstance(get_outbound_mail_provider(), OutboundMailProvider)


class TestSmtpAuthOnlyTest:
    """
    Owner'ın 'S5 — FINAL PROVIDER / DELIVERY GATE' STEP 1 talebi —
    test_authentication() YALNIZ EHLO/STARTTLS/EHLO/AUTH/QUIT yapmalı,
    send_message()'a (dolayısıyla gerçek bir e-posta gönderimine) HİÇBİR
    KOŞULDA ulaşmamalı. GERÇEK ağ bağlantısı YOK — smtplib.SMTP MOCK'lanır.
    """

    def test_not_configured_returns_config_incomplete_without_network(self):
        provider = SmtpMailProvider()
        result = provider.test_authentication()
        assert result.auth_ok is False
        assert result.error_detail == "CONFIG_INCOMPLETE"

    def test_successful_auth_flow_never_touches_send_message(self, monkeypatch):
        monkeypatch.setattr(settings, "outreach_smtp_host", "fake.invalid")
        monkeypatch.setattr(settings, "outreach_smtp_port", 587)
        monkeypatch.setattr(settings, "outreach_smtp_security", "starttls")
        monkeypatch.setattr(settings, "outreach_smtp_username", "u@fake.invalid")
        monkeypatch.setattr(settings, "outreach_smtp_password", "fake-secret-not-real")
        monkeypatch.setattr(settings, "outreach_sender_email", "u@fake.invalid")

        calls = []

        class FakeSMTP:
            def __init__(self, host, port, timeout=None):
                calls.append(("connect", host, port))

            def ehlo(self):
                calls.append(("ehlo",))

            def starttls(self, context=None):
                calls.append(("starttls",))

            def login(self, user, pw):
                calls.append(("login", user))  # parola KAYDEDİLMEZ

            def send_message(self, *a, **kw):
                raise AssertionError("test_authentication() ASLA send_message ÇAĞIRMAMALI!")

            def quit(self):
                calls.append(("quit",))

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.quit()
                return False

        import app.outreach.smtp_provider as smtp_provider_module
        monkeypatch.setattr(smtp_provider_module.smtplib, "SMTP", FakeSMTP)

        provider = SmtpMailProvider()
        result = provider.test_authentication()

        assert result.tls_ok is True
        assert result.certificate_ok is True
        assert result.auth_ok is True
        assert calls == [
            ("connect", "fake.invalid", 587), ("ehlo",), ("starttls",), ("ehlo",),
            ("login", "u@fake.invalid"), ("quit",),
        ], "Sıra tam olarak EHLO->STARTTLS->EHLO->AUTH->QUIT olmalı, MAIL FROM/RCPT TO/DATA YOK"

    def test_auth_failure_reported_without_leaking_password_value(self, monkeypatch):
        monkeypatch.setattr(settings, "outreach_smtp_host", "fake.invalid")
        monkeypatch.setattr(settings, "outreach_smtp_security", "starttls")
        monkeypatch.setattr(settings, "outreach_smtp_username", "u@fake.invalid")
        monkeypatch.setattr(settings, "outreach_smtp_password", "super-secret-value-12345")
        monkeypatch.setattr(settings, "outreach_sender_email", "u@fake.invalid")

        class FakeSMTPAuthFail:
            def __init__(self, host, port, timeout=None):
                pass

            def ehlo(self):
                pass

            def starttls(self, context=None):
                pass

            def login(self, user, pw):
                raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import app.outreach.smtp_provider as smtp_provider_module
        monkeypatch.setattr(smtp_provider_module.smtplib, "SMTP", FakeSMTPAuthFail)

        provider = SmtpMailProvider()
        result = provider.test_authentication()

        assert result.auth_ok is False
        assert result.tls_ok is True  # STARTTLS AUTH'dan önce başarıyla tamamlandı
        assert "super-secret-value-12345" not in (result.error_detail or ""), "Parola ASLA hata mesajında yer almamalı"


# ═══════════════════════════════════════════════════════════════════════════
# Service orkestrasyonu — durum makinesi + idempotent send
# ═══════════════════════════════════════════════════════════════════════════

class TestServiceOrchestration:
    def test_full_draft_edit_approve_send_flow(self, db, sender_profile_configured, iys_verified, monkeypatch):
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        db.commit()

        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        assert draft.status == "DRAFT"

        ready = svc.finalize_draft_message(db, TENANT, draft.id, editable_body="Merhaba, görüşelim.")
        assert ready.status == "READY_FOR_REVIEW"

        approved = svc.approve_message(db, TENANT, draft.id)
        assert approved.status == "APPROVED"
        assert approved.approved_at is not None

        fake_provider = FakeProvider(SendResult(success=True, provider_message_id="<ok@test>"))
        monkeypatch.setattr(svc, "get_outbound_mail_provider", lambda: fake_provider)
        sent = svc.send_message(db, TENANT, draft.id)
        assert sent.status == "SENT"
        assert sent.provider_message_id == "<ok@test>"
        assert len(fake_provider.calls) == 1

    def test_double_click_send_does_not_send_twice(self, db, sender_profile_configured, iys_verified, monkeypatch):
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        _approve(db, draft.id)

        fake_provider = FakeProvider(SendResult(success=True, provider_message_id="<x>"))
        monkeypatch.setattr(svc, "get_outbound_mail_provider", lambda: fake_provider)
        svc.send_message(db, TENANT, draft.id)

        with pytest.raises(HTTPException) as exc:
            svc.send_message(db, TENANT, draft.id)
        assert exc.value.status_code == 409
        assert len(fake_provider.calls) == 1, "İkinci deneme provider.send()'e ASLA ulaşmamalı"

    def test_concurrent_claim_only_one_wins(self, db, sender_profile_configured, iys_verified):
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        _approve(db, draft.id)

        claim1 = svc.claim_message_for_sending(db, TENANT, draft.id)
        claim2 = svc.claim_message_for_sending(db, TENANT, draft.id)
        assert claim1 is not None and claim1.status == "SENDING"
        assert claim2 is None, "İkinci claim None dönmeli — aynı mesaj iki kez SENDING'e çekilemez"

    def test_provider_failure_marks_failed_not_stuck(self, db, sender_profile_configured, iys_verified, monkeypatch):
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        _approve(db, draft.id)

        fake_provider = FakeProvider(SendResult(success=False, error_code="RECIPIENT_REFUSED", error_detail="550"))
        monkeypatch.setattr(svc, "get_outbound_mail_provider", lambda: fake_provider)
        with pytest.raises(HTTPException) as exc:
            svc.send_message(db, TENANT, draft.id)
        assert exc.value.status_code == 502

        reloaded = db.query(OutreachMessage).filter_by(id=draft.id).first()
        assert reloaded.status == "FAILED"
        assert reloaded.failure_code == "RECIPIENT_REFUSED"

    def test_send_provider_success_but_final_commit_fails_leaves_message_stuck_in_sending(
        self, db, sender_profile_configured, iys_verified, monkeypatch
    ):
        """
        Owner'ın WB8 EK talebi #1 — EN TEHLİKELİ duplicate-mail penceresi:
        provider KABUL EDER, ama SONRAKİ DB commit (SENT'e geçiş)
        BAŞARISIZ olur -> mesaj SENDING'de KALIR -> force-olmayan bir
        retry TEKRAR GÖNDERMEZ -> manuel reconciliation gerekir.
        """
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        _approve(db, draft.id)

        fake_provider = FakeProvider(SendResult(success=True, provider_message_id="<crash-after-accept@test>"))
        monkeypatch.setattr(svc, "get_outbound_mail_provider", lambda: fake_provider)

        # db.commit()'i yalnız 2. çağrıda (final SENT persist) patlat —
        # 1. çağrı claim_message_for_sending()'in APPROVED->SENDING commit'idir,
        # o BAŞARILI olmalı (gerçek senaryo: claim zaten kalıcı olmuş olurdu).
        real_commit = db.commit
        call_count = {"n": 0}

        def flaky_commit():
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated DB failure right after provider accepted the message")
            return real_commit()

        monkeypatch.setattr(db, "commit", flaky_commit)

        with pytest.raises(RuntimeError):
            svc.send_message(db, TENANT, draft.id)

        assert len(fake_provider.calls) == 1, "Provider GERÇEKTEN kabul etti (senaryo budur)"

        # Session'daki dirty/uncommitted in-memory durumu at, DB'de GERÇEKTEN
        # kalıcı olan neyse onu oku (flaky_commit hiçbir zaman gerçek commit'i
        # çağırmadığı için SENT'e geçiş DB'ye hiç yazılmamış olmalı).
        db.expire_all()
        monkeypatch.setattr(db, "commit", real_commit)  # normale döndür — sonraki assert'ler için

        reloaded = db.query(OutreachMessage).filter_by(id=draft.id).first()
        assert reloaded.status == "SENDING", (
            "Mesaj ne APPROVED'a geri dönmeli ne SENT'e geçmeli — SENDING'de "
            "GÜVENLİ ŞEKİLDE takılı kalmalı (bilinçli tasarım, owner onayladı)."
        )

        # "force olmayan" bir retry: send_message() tekrar çağrılır — status
        # artık APPROVED OLMADIĞI için 409 ile reddedilmeli, provider'a
        # İKİNCİ KEZ ASLA ulaşılmamalı (duplicate-mail önlenir).
        with pytest.raises(HTTPException) as exc:
            svc.send_message(db, TENANT, draft.id)
        assert exc.value.status_code == 409
        assert len(fake_provider.calls) == 1, "Retry provider'ı İKİNCİ KEZ ÇAĞIRMAMALI — bu tam da duplicate-mail riskidir"

    def test_approve_then_new_suppression_then_send_is_blocked_by_fresh_recheck(
        self, db, sender_profile_configured, iys_verified, monkeypatch
    ):
        """
        Owner'ın WB8 EK talebi #2 — APPROVED anındaki izin, SEND anında
        KALICI bir hak sayılmamalı. approve() ile send() arasında yeni bir
        suppression eklenirse, send() TAZE yeniden değerlendirme ile
        BLOCKED olmalı; mesaj SENDING'e hiç geçmemeli.
        """
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        contact = _make_contact(db, company.id, email="info@approved-then-suppressed.example")
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        approved = _approve(db, draft.id)
        assert approved.status == "APPROVED"

        # approve SONRASI, send'DEN ÖNCE suppression eklenir.
        db.add(SuppressionEntry(tenant_id=TENANT, email_normalized="info@approved-then-suppressed.example", reason="USER_REJECTED"))
        db.commit()

        fake_provider = FakeProvider(SendResult(success=True, provider_message_id="<should-not-be-called@test>"))
        monkeypatch.setattr(svc, "get_outbound_mail_provider", lambda: fake_provider)

        with pytest.raises(HTTPException) as exc:
            svc.send_message(db, TENANT, draft.id)
        assert exc.value.status_code == 409
        assert exc.value.detail.get("error") == "compliance_blocked"
        assert "SUPPRESSED" in exc.value.detail.get("reason_codes", [])
        assert len(fake_provider.calls) == 0, "Provider'a HİÇ ulaşılmamalı — SENDING'e hiç geçilmemeli"

        reloaded = db.query(OutreachMessage).filter_by(id=draft.id).first()
        assert reloaded.status == "APPROVED", "SENDING'e hiç geçmemeli"

    def test_approve_itself_blocked_when_compliance_fails(self, db, sender_profile_configured):
        """approve() da TAZE değerlendirir — yalnız send() değil."""
        company = _make_prospect(db)  # verified_legal_type YOK — bilerek
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        svc.finalize_draft_message(db, TENANT, draft.id, editable_body="x")
        with pytest.raises(HTTPException) as exc:
            svc.approve_message(db, TENANT, draft.id)
        assert exc.value.status_code == 409
        reloaded = db.query(OutreachMessage).filter_by(id=draft.id).first()
        assert reloaded.status == "READY_FOR_REVIEW", "Reddedilen approve status'u İLERLETMEMELİ"

    def test_create_draft_message_no_longer_accepts_direct_email(self):
        """
        Owner'ın 'S5 PRE-DELIVERY HARDENING' düzeltmesi — genel production
        fonksiyonuna gömülü bir 'direct_email' kaçış yolu KALICI olarak
        KALDIRILDI. Bir daha ASLA geri gelmesin diye imza kontrolü.
        """
        import inspect
        sig = inspect.signature(svc.create_draft_message)
        assert "direct_email" not in sig.parameters


class TestOwnerControlledTestDraft:
    """
    Owner'ın 'S5 PRE-DELIVERY HARDENING' talimatı — create_draft_message()'dan
    TAMAMEN AYRI, açık bir test-yolu. Owner madde madde:
      - yalnız allowlist'teki adres kabul edilir
      - recipient_category HER ZAMAN TEST_RECIPIENT (çağıran override edemez)
      - Prospect/Customer kaydı OLUŞTURULMAZ/DEĞİŞTİRİLMEZ
      - suppression YİNE uygulanır
      - router/UI'dan erişilemez (bu test dosyası zaten yalnız service.py'yi
        doğrudan çağırıyor — router.py'de bu fonksiyona karşılık gelen
        HİÇBİR endpoint YOK, ayrıca bkz. test_no_router_endpoint_exists).
    """

    def test_whitelisted_address_succeeds_as_test_recipient(self, db, sender_profile_configured, monkeypatch):
        monkeypatch.setattr(settings, "outreach_test_recipient_emails", "owner-test@gelka-owner.invalid")
        draft = svc.create_owner_controlled_test_draft(db, TENANT, test_recipient_email="owner-test@gelka-owner.invalid")
        assert draft.recipient_category == "TEST_RECIPIENT"
        assert draft.recipient_email_snapshot == "owner-test@gelka-owner.invalid"
        assert draft.status == "DRAFT"

    def test_non_whitelisted_address_rejected_early(self, db, sender_profile_configured, monkeypatch):
        monkeypatch.setattr(settings, "outreach_test_recipient_emails", "owner-test@gelka-owner.invalid")
        with pytest.raises(HTTPException) as exc:
            svc.create_owner_controlled_test_draft(db, TENANT, test_recipient_email="rastgele@baska-adres.example")
        assert exc.value.status_code == 422
        assert exc.value.detail.get("error") == "not_an_owner_controlled_test_recipient"

    def test_never_creates_or_touches_prospect_or_customer_rows(self, db, sender_profile_configured, monkeypatch):
        monkeypatch.setattr(settings, "outreach_test_recipient_emails", "owner-test@gelka-owner.invalid")
        before_companies = db.query(ProspectCompany).count()
        before_customers = db.query(Customer).count()
        draft = svc.create_owner_controlled_test_draft(db, TENANT, test_recipient_email="owner-test@gelka-owner.invalid")
        assert draft.prospect_company_id is None
        assert draft.customer_id is None
        assert draft.contact_id is None
        assert db.query(ProspectCompany).count() == before_companies
        assert db.query(Customer).count() == before_customers

    def test_suppression_still_enforced_for_owner_controlled_test(self, db, sender_profile_configured, monkeypatch):
        """Owner madde: 'still enforce suppression' — TEST_RECIPIENT dahi suppression'dan MUAF DEĞİL."""
        monkeypatch.setattr(settings, "outreach_test_recipient_emails", "owner-test@gelka-owner.invalid")
        db.add(SuppressionEntry(tenant_id=TENANT, email_normalized="owner-test@gelka-owner.invalid", reason="MANUAL_BLOCK"))
        db.commit()
        draft = svc.create_owner_controlled_test_draft(db, TENANT, test_recipient_email="owner-test@gelka-owner.invalid")
        # Draft OLUŞUR (owner'ın normal akışıyla tutarlı — gate approve/send'de),
        # ama compliance snapshot'ı SUPPRESSED'i AÇIKÇA göstermeli.
        assert draft.compliance_snapshot_json["can_send"] is False
        assert "SUPPRESSED" in draft.compliance_snapshot_json["reason_codes"]

        # approve() de bunu tekrar TAZE değerlendirip reddetmeli — bu
        # zaten create_draft_message ile PAYLAŞILAN approve_message()'ın
        # test edilmiş davranışı, burada owner-controlled test icin de
        # AYNI korumanın geçerli olduğunu doğruluyoruz.
        svc.finalize_draft_message(db, TENANT, draft.id, editable_body="x")
        with pytest.raises(HTTPException) as exc:
            svc.approve_message(db, TENANT, draft.id)
        assert exc.value.status_code == 409

    def test_router_exposes_no_owner_controlled_test_endpoint(self):
        """Owner madde: 'remain unavailable from public router/UI'."""
        from app.outreach.router import outreach_router
        paths = [route.path for route in outreach_router.routes]
        assert not any("test" in p.lower() or "owner" in p.lower() for p in paths), (
            f"Router'da owner-controlled-test'e işaret eden bir path bulundu: {paths}"
        )


class TestFinalizeDraftMessage:
    def test_finalize_never_touches_system_footer(self, db, sender_profile_configured):
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        original_footer = draft.system_footer_snapshot
        svc.finalize_draft_message(db, TENANT, draft.id, editable_body="Tamamen farklı bir metin, footer'la ilgisi yok.")
        reloaded = db.query(OutreachMessage).filter_by(id=draft.id).first()
        assert reloaded.system_footer_snapshot == original_footer, "finalize_draft_message ASLA footer'a dokunmamalı"


# ═══════════════════════════════════════════════════════════════════════════
# CRM Activity/Task entegrasyonu (WB6)
# ═══════════════════════════════════════════════════════════════════════════

class TestCrmIntegration:
    def test_converted_prospect_send_creates_email_activity(self, db, sender_profile_configured, iys_verified, monkeypatch):
        customer = Customer(name="Dönüştürülmüş Müşteri")
        db.add(customer)
        db.flush()
        company = _make_prospect(db, verified_legal_type="TACIR", status="CONVERTED", customer_id=customer.id)
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        _approve(db, draft.id)

        fake_provider = FakeProvider(SendResult(success=True, provider_message_id="<x>"))
        monkeypatch.setattr(svc, "get_outbound_mail_provider", lambda: fake_provider)
        svc.send_message(db, TENANT, draft.id)

        activities = db.query(Activity).filter(Activity.customer_id == customer.id).all()
        assert len(activities) == 1
        assert activities[0].activity_type == "EMAIL"

    def test_unconverted_prospect_send_creates_no_activity_but_still_succeeds(
        self, db, sender_profile_configured, iys_verified, monkeypatch
    ):
        company = _make_prospect(db, verified_legal_type="TACIR")  # customer_id YOK
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        _approve(db, draft.id)

        fake_provider = FakeProvider(SendResult(success=True, provider_message_id="<x>"))
        monkeypatch.setattr(svc, "get_outbound_mail_provider", lambda: fake_provider)
        sent = svc.send_message(db, TENANT, draft.id)

        assert sent.status == "SENT", "Activity oluşturulamaması gönderimi ENGELLEMEMELİ"
        assert db.query(Activity).count() == 0

    def test_activity_creation_failure_does_not_break_send(
        self, db, sender_profile_configured, iys_verified, monkeypatch
    ):
        customer = Customer(name="Test Müşteri E")
        db.add(customer)
        db.flush()
        company = _make_prospect(db, verified_legal_type="TACIR", status="CONVERTED", customer_id=customer.id)
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        _approve(db, draft.id)

        fake_provider = FakeProvider(SendResult(success=True, provider_message_id="<x>"))
        monkeypatch.setattr(svc, "get_outbound_mail_provider", lambda: fake_provider)

        def _boom(*a, **kw):
            raise RuntimeError("simulated CRM failure")
        monkeypatch.setattr(svc.crm_service, "create_activity", _boom)

        sent = svc.send_message(db, TENANT, draft.id)
        assert sent.status == "SENT", "CRM Activity hatası gönderimin KENDİSİNİ etkilememeli"

    def test_follow_up_task_requires_customer_context(self, db, sender_profile_configured):
        company = _make_prospect(db, verified_legal_type="TACIR")  # customer_id YOK
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        with pytest.raises(HTTPException) as exc:
            svc.create_follow_up_task(db, TENANT, draft.id)
        assert exc.value.status_code == 422
        assert exc.value.detail.get("error") == "follow_up_task_requires_customer"

    def test_follow_up_task_is_opt_in_not_automatic(self, db, sender_profile_configured, iys_verified, monkeypatch):
        """send_message() KENDİLİĞİNDEN bir Task oluşturmamalı — owner: 'user-opt-in'."""
        customer = Customer(name="Test Müşteri F")
        db.add(customer)
        db.flush()
        company = _make_prospect(db, verified_legal_type="TACIR", status="CONVERTED", customer_id=customer.id)
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        draft = svc.create_draft_message(db, TENANT, contact_id=contact.id)
        _approve(db, draft.id)
        fake_provider = FakeProvider(SendResult(success=True, provider_message_id="<x>"))
        monkeypatch.setattr(svc, "get_outbound_mail_provider", lambda: fake_provider)
        svc.send_message(db, TENANT, draft.id)

        assert db.query(Task).filter(Task.customer_id == customer.id).count() == 0, "send_message otomatik Task OLUŞTURMAMALI"

        task = svc.create_follow_up_task(db, TENANT, draft.id, days_from_now=3)
        assert task.customer_id == customer.id


# ═══════════════════════════════════════════════════════════════════════════
# HTTP router entegrasyonu
# ═══════════════════════════════════════════════════════════════════════════

class TestOutreachRouter:
    def test_full_http_flow_up_to_send_config_incomplete(self, client, db, sender_profile_configured, iys_verified):
        """
        Gerçek HTTP katmanı üzerinden draft->finalize->approve->send.
        SMTP configured OLMADIĞI için send() 502/CONFIG_INCOMPLETE ile
        aga hic cikmadan biter — GERÇEK bir gönderim ASLA denenmez.
        """
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        db.commit()

        r = client.post("/outreach/messages", json={"contact_id": contact.id})
        assert r.status_code == 200
        msg_id = r.json()["id"]

        r = client.get(f"/outreach/messages/{msg_id}/compliance")
        assert r.status_code == 200
        assert r.json()["compliance_snapshot_json"]["can_send"] is True

        r = client.patch(f"/outreach/messages/{msg_id}", json={"editable_body": "Test"})
        assert r.status_code == 200 and r.json()["status"] == "READY_FOR_REVIEW"

        r = client.post(f"/outreach/messages/{msg_id}/approve")
        assert r.status_code == 200 and r.json()["status"] == "APPROVED"

        r = client.post(f"/outreach/messages/{msg_id}/send")
        assert r.status_code == 502
        assert r.json()["detail"]["error_code"] == "CONFIG_INCOMPLETE"

    def test_suppression_http_endpoints(self, client):
        r = client.post("/outreach/suppressions", json={"email": "http-block@x.com", "reason": "MANUAL_BLOCK"})
        assert r.status_code == 200
        r = client.get("/outreach/suppressions")
        assert r.status_code == 200
        assert any(s["email_normalized"] == "http-block@x.com" for s in r.json())

    def test_list_messages_filters_by_status(self, client, db, sender_profile_configured):
        company = _make_prospect(db, verified_legal_type="TACIR")
        _make_source(db, company.id)
        contact = _make_contact(db, company.id)
        db.commit()
        r = client.post("/outreach/messages", json={"contact_id": contact.id})
        msg_id = r.json()["id"]

        r = client.get("/outreach/messages", params={"status": "DRAFT"})
        assert r.status_code == 200 and len(r.json()) == 1
        r = client.get("/outreach/messages", params={"status": "SENT"})
        assert r.status_code == 200 and len(r.json()) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Güvenlik — S1-S4 regresyon (bu dosyanın kendi ekleri S1-S4'ü BOZMAMALI)
# ═══════════════════════════════════════════════════════════════════════════

class TestSecurityAndRegression:
    def test_no_secrets_hardcoded_in_source(self):
        """
        outreach/ altındaki hiçbir .py dosyası gerçek bir parola/anahtar
        İÇERMEMELİ — yalnız settings.* üzerinden okunmalı.
        """
        import pathlib
        outreach_dir = pathlib.Path(__file__).parent.parent / "app" / "outreach"
        suspicious_patterns = ("sk-", "AKIA", "-----BEGIN")
        for py_file in outreach_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for pattern in suspicious_patterns:
                assert pattern not in content, f"{py_file.name} şüpheli bir secret deseni içeriyor: {pattern}"

    def test_prospecting_regression_untouched(self, db):
        """S4'ün temel akışı S5 tarafından bozulmadı — hızlı duman testi."""
        from app.prospecting import service as prospecting_service
        from app.prospecting.schemas import ProspectCompanyCreateRequest
        result = prospecting_service.create_prospect(
            db, TENANT, ProspectCompanyCreateRequest(legal_name="Regresyon Test A.Ş.")
        )
        assert result.prospect is not None
        assert result.prospect.status == "DISCOVERED"
