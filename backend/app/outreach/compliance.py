"""
S5 — Outreach — compliance.py

TEK yetkili karar noktası: evaluate_email_send_eligibility(). Owner'ın S5
GO spec'i madde D (HARD GATE): "Hiçbir gönderim endpoint'i bunu bypass
edemez — frontend'in can_send=true beyanına asla güvenilmez." Bu ilke
app/database.py'nin S5 bölüm başlığında da (satır ~1038) tekrarlanır.

Bu fonksiyon DURUM'a (config + DB) göre karar verir — "her zaman false"
hard-code EDİLMEZ; owner İYS durumunu netleştirdiğinde (settings.
outreach_iys_status) veya bir suppression kaydı silindiğinde/eklendiğinde,
fonksiyon DOĞAL olarak farklı sonuç üretir.

═══════════════════════════════════════════════════════════════════════════
DÜZELTME (owner, 10.08 — ilk yazımın ELEŞTİRİSİ, kabul edildi ve uygulandı):
contact_type ≠ recipient_legal_type. Bunlar BAĞIMSIZ iki eksendir:

  contact_type (app/prospecting/enrichment.py::classify_contact_type):
    E-POSTA ÖRÜNTÜSÜNDEN çıkarılan bir KANAL sinyali —
    GENERAL_CORPORATE | DEPARTMENT | NAMED_CORPORATE_PERSON |
    PERSONAL_OR_FREE_MAIL | OTHER. Yalnız KVKK-risk seviyesini
    (kvkk_status) belirler — "bu adres muhtemelen kurumsal bir kanal mı
    yoksa muhtemelen bir gerçek kişinin verisi mi" sorusuna heuristik
    bir yanıttır.

  recipient_legal_type (BU modülde, ProspectCompany.verified_legal_type'tan):
    ALICININ HUKUKİ statüsü — TACIR | ESNAF | BIREYSEL | UNKNOWN. Ticaret
    Bakanlığı/İYS bakımından tacir/esnaf ayrımı alıcının hukuki statüsüdür,
    e-posta adresinin BİÇİMİ DEĞİL (info@ kullanan bir şirket hukuken
    tacir de olabilir esnaf da). Bu yüzden BU alan E-POSTA ÖRÜNTÜSÜNDEN
    ASLA otomatik türetilmez ("sessiz inference yapma" — owner) — yalnız
    ProspectCompany.verified_legal_type doldurulmuşsa (insan tarafından,
    güvenilir kaynaktan doğrulanmış) TACIR/ESNAF/BIREYSEL olur; aksi halde
    UNKNOWN'dur ve V1 fail-closed kuralı gereği gerçek prospect gönderimi
    BLOKE edilir (REASON_RECIPIENT_LEGAL_TYPE_UNVERIFIED) — contact_type
    ne olursa olsun, KVKK gate'inden BAĞIMSIZ, AYRI bir gate'tir.

  İlk yazımdaki hata: "GENERAL_CORPORATE/DEPARTMENT → TACIR" inference'ı
  vardı — bu KALDIRILDI. V1'de hiçbir ProspectCompany.verified_legal_type
  henüz doldurulmadığı için pratikte TÜM PROSPECT_RECIPIENT gönderimleri
  şu an İKİ bağımsız nedenle bloke: RECIPIENT_LEGAL_TYPE_UNVERIFIED +
  IYS_STATUS_UNKNOWN (owner'ın İYS kararı zaten değişmiyor).
═══════════════════════════════════════════════════════════════════════════

Taze doğrulanmış hukuki temel (owner'ın S5 GO'sunda ve 10.08 düzeltmesinde
atıfta bulunulan Ticaret Bakanlığı/İYS/KVKK kaynakları):
- tacir/esnaf → OPT-OUT rejimi (ön onay gerekmez, ret bildirimi 3 iş günü
  içinde uygulanmalı) — AMA statü e-postadan değil, doğrulanmış kayıttan gelir
- gerçek kişi/bireysel → OPT-IN rejimi (ön yazılı onay şart) — DAHA SIKI
- İYS'ye kayıt HER işletme için zorunlu (tacir/esnaf'ın onay gerekmeyen
  adresleri dahi İYS'ye önceden yüklenmiş + ret kontrolü yapılmış olmalı)
- KVKK'nın 2026 duyurusu: üçüncü kişilerden elde edilen iletişim bilgisinin
  pazarlama amacıyla kullanılması kendiliğinden hukuki dayanak yaratmaz;
  KVKK m.5 şartı somut olay bazında değerlendirilir → NAMED_CORPORATE_PERSON
  için muhafazakar KVKK_REVIEW_REQUIRED bloğu bu nedenle korunuyor.

Sıra (fail-CLOSED, "ilk hatada dur" DEĞİL — TÜM reason_code'lar toplanır;
owner'ın "compliance state UI'da tam görünür olmalı" ilkesiyle tutarlı):
  1. E-posta sözdizimi (app/prospecting/normalize.py::is_valid_email_syntax)
  2. Suppression tablosu — KATEGORİDEN BAĞIMSIZ, TEST_RECIPIENT bile bunu
     bypass edemez (owner madde D, açık istisna: "not a suppression bypass")
  3. recipient_category çözümlemesi — YALNIZ settings whitelist'inden
     türetilir, hiçbir zaman çağıranın isteğinden GÜVENİLMEZ
  4. TEST_RECIPIENT ise → İYS/KVKK/recipient_legal_type/source-evidence
     adımları hiç değerlendirilmez, yalnız suppression geçerli
  5. PROSPECT_RECIPIENT ise → source-evidence + contact_type→kvkk_status +
     verified_legal_type→recipient_legal_type (BAĞIMSIZ) + İYS durumu,
     HEPSİ değerlendirilir (AND — hepsi temiz olmalı)

Çağrıldığı yerler:
- (henüz yok) app/outreach/service.py draft/approve/send akışları [S5-WB4+]
  — bu modül bu turda YALNIZ tanımlanıyor; hiçbir router/endpoint henüz
  onu çağırmıyor, bu yüzden mevcut hiçbir davranışı ETKİLEMEZ (S1-S4 dahil).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .. import database as db_models
from ..core.config import settings
from ..prospecting.enrichment import (
    CONTACT_TYPE_DEPARTMENT,
    CONTACT_TYPE_FREE_MAIL,
    CONTACT_TYPE_GENERAL,
    CONTACT_TYPE_NAMED_PERSON,
    classify_contact_type,
)
from ..prospecting.normalize import is_valid_email_syntax, normalize_email

RECIPIENT_CATEGORY_PROSPECT = "PROSPECT_RECIPIENT"
RECIPIENT_CATEGORY_TEST = "TEST_RECIPIENT"

RECIPIENT_LEGAL_TYPE_TACIR = "TACIR"
RECIPIENT_LEGAL_TYPE_ESNAF = "ESNAF"
RECIPIENT_LEGAL_TYPE_BIREYSEL = "BIREYSEL"
RECIPIENT_LEGAL_TYPE_UNKNOWN = "UNKNOWN"

_VALID_VERIFIED_LEGAL_TYPES = {
    RECIPIENT_LEGAL_TYPE_TACIR,
    RECIPIENT_LEGAL_TYPE_ESNAF,
    RECIPIENT_LEGAL_TYPE_BIREYSEL,
}

# İYS durumu bu değerlerden biriyken PROSPECT_RECIPIENT gönderimi
# engellenmez — owner netleştirene kadar settings.outreach_iys_status
# varsayılanı IYS_UNKNOWN'dır (bkz. core/config.py), yani bu liste V1'de
# fiilen HİÇBİR PROSPECT_RECIPIENT gönderimini geçirmez.
_IYS_STATUSES_NOT_BLOCKING = {"IYS_VERIFIED", "IYS_NOT_REQUIRED_OR_SPECIAL_CASE"}

# --- reason_codes: yalnız can_send=False'a KATKI YAPAN nedenler ---
REASON_EMAIL_MISSING = "EMAIL_MISSING"
REASON_EMAIL_INVALID_SYNTAX = "EMAIL_INVALID_SYNTAX"
REASON_RECIPIENT_CONTEXT_MISSING = "RECIPIENT_CONTEXT_MISSING"
REASON_CONTACT_NOT_FOUND = "CONTACT_NOT_FOUND"
REASON_CUSTOMER_NOT_FOUND = "CUSTOMER_NOT_FOUND"
REASON_PROSPECT_COMPANY_NOT_FOUND = "PROSPECT_COMPANY_NOT_FOUND"
REASON_SUPPRESSED = "SUPPRESSED"
REASON_SOURCE_EVIDENCE_MISSING = "SOURCE_EVIDENCE_MISSING"
REASON_KVKK_REVIEW_REQUIRED = "KVKK_REVIEW_REQUIRED"
REASON_KVKK_OPT_IN_REQUIRED = "KVKK_OPT_IN_REQUIRED"
REASON_CONTACT_TYPE_UNRESOLVED = "CONTACT_TYPE_UNRESOLVED"
REASON_RECIPIENT_LEGAL_TYPE_UNVERIFIED = "RECIPIENT_LEGAL_TYPE_UNVERIFIED"
REASON_IYS_STATUS_UNKNOWN = "IYS_STATUS_UNKNOWN"
REASON_IYS_STATUS_BLOCKED = "IYS_STATUS_BLOCKED"


@dataclass
class ComplianceResult:
    """evaluate_email_send_eligibility()'nin dönüş tipi — tam compliance state."""

    can_send: bool
    reason_codes: list[str] = field(default_factory=list)
    recipient_category: str = RECIPIENT_CATEGORY_PROSPECT
    contact_type: Optional[str] = None  # e-posta ÖRÜNTÜSÜ sinyali (KVKK için)
    recipient_legal_type: str = RECIPIENT_LEGAL_TYPE_UNKNOWN  # DOĞRULANMIŞ hukuki statü (İYS/opt-out için)
    iys_status: str = "IYS_UNKNOWN"
    suppression_status: str = "UNKNOWN"  # CLEAR | SUPPRESSED | UNKNOWN
    source_status: str = "NOT_APPLICABLE"  # EVIDENCED | MISSING | NOT_APPLICABLE
    kvkk_status: str = "NOT_APPLICABLE"  # OK | REVIEW_REQUIRED | OPT_IN_REQUIRED | NOT_APPLICABLE
    normalized_email: Optional[str] = None
    evaluated_at: datetime = field(default_factory=datetime.utcnow)

    def as_dict(self) -> dict:
        """OutreachMessage.compliance_snapshot_json'a yazılabilir düz sözlük."""
        return {
            "can_send": self.can_send,
            "reason_codes": list(self.reason_codes),
            "recipient_category": self.recipient_category,
            "contact_type": self.contact_type,
            "recipient_legal_type": self.recipient_legal_type,
            "iys_status": self.iys_status,
            "suppression_status": self.suppression_status,
            "source_status": self.source_status,
            "kvkk_status": self.kvkk_status,
            "normalized_email": self.normalized_email,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


def _derive_kvkk_status(contact_type: str) -> tuple[str, Optional[str]]:
    """
    (kvkk_status, engelleyici_reason_code_veya_None) döner.

    YALNIZ contact_type'a (e-posta örüntüsü) bakar — recipient_legal_type
    ile KARIŞTIRILMAZ (bkz. modül docstring'i).

    GENERAL_CORPORATE/DEPARTMENT → "OK" (kurumsal kanal sinyali, KVKK
    açısından ek inceleme gerektirmiyor — bu, alıcının TACIR/ESNAF
    olduğu ANLAMINA GELMEZ, o ayrı bir gate).
    NAMED_CORPORATE_PERSON → muhafazakar KVKK_REVIEW_REQUIRED (owner:
    "conservative ... block by default" — bu turda owner tarafından
    AÇIKÇA doğru bulundu, DEĞİŞMEDİ).
    PERSONAL_OR_FREE_MAIL → muhtemel gerçek kişi, opt-in şart; V1'de
    onay toplama mekanizması yok → her zaman bloklanır.
    OTHER/tanınamayan → REVIEW_REQUIRED, fail-closed.
    """
    if contact_type in (CONTACT_TYPE_GENERAL, CONTACT_TYPE_DEPARTMENT):
        return "OK", None
    if contact_type == CONTACT_TYPE_NAMED_PERSON:
        return "REVIEW_REQUIRED", REASON_KVKK_REVIEW_REQUIRED
    if contact_type == CONTACT_TYPE_FREE_MAIL:
        return "OPT_IN_REQUIRED", REASON_KVKK_OPT_IN_REQUIRED
    return "REVIEW_REQUIRED", REASON_CONTACT_TYPE_UNRESOLVED


def _resolve_recipient_legal_type(
    company: Optional["db_models.ProspectCompany"],
) -> tuple[str, Optional[str]]:
    """
    (recipient_legal_type, engelleyici_reason_code_veya_None) döner.

    Owner'ın 10.08 düzeltmesi: contact_type/e-posta örüntüsünden TACIR/
    ESNAF SONUCU ÇIKARILMAZ — yalnız ProspectCompany.verified_legal_type
    alanına (insan tarafından, güvenilir kaynaktan doğrulanmış) bakılır.
    Boş/None/geçersiz değer ise UNKNOWN — fail-closed, "sessiz inference
    yapma" ilkesi.
    """
    if company is not None and company.verified_legal_type in _VALID_VERIFIED_LEGAL_TYPES:
        return company.verified_legal_type, None
    return RECIPIENT_LEGAL_TYPE_UNKNOWN, REASON_RECIPIENT_LEGAL_TYPE_UNVERIFIED


def evaluate_email_send_eligibility(
    db: Session,
    tenant_id: str,
    *,
    candidate_email: str,
    prospect_company_id: Optional[int] = None,
    contact_id: Optional[int] = None,
    customer_id: Optional[int] = None,
) -> ComplianceResult:
    """
    HARD GATE — tek yetkili gönderim uygunluk kararı.

    Not: recipient_category, çağıranın isteğinden DEĞİL, YALNIZ
    settings.outreach_test_recipient_email_set üyeliğinden türetilir —
    bu yüzden fonksiyon bilerek bir "requested_category" parametresi
    ALMAZ (owner: "TEST_RECIPIENT ... asla kullanıcı girdisinden veya
    request body'sinden gelmez").

    Not 2: recipient_legal_type de aynı şekilde bir parametre DEĞİLDİR —
    yalnız DB'deki ProspectCompany.verified_legal_type'tan okunur, çağıran
    kod bunu "iddia edemez" (owner'ın 10.08 düzeltmesi, "sessiz inference
    yapma" ilkesinin doğal uzantısı: güvenmeme ilkesi hem e-posta
    örüntüsüne hem de çağıranın beyanına eşit uygulanır).

    Args:
        db: aktif SQLAlchemy session
        tenant_id: X-Tenant-Id (get_tenant_id() ile aynı çözümleme)
        candidate_email: değerlendirilecek ham e-posta (normalize edilecek)
        prospect_company_id: hedef ProspectCompany (source-evidence +
            verified_legal_type okuması için)
        contact_id: hedef ProspectContact (verilirse prospect_company_id
            otomatik türetilir, ayrıca verilmesi gerekmez)
        customer_id: hedef mevcut Customer (prospect bağlamı yoksa —
            V1'de Customer'da verified_legal_type YOK, bu yüzden bu yol
            recipient_legal_type=UNKNOWN'da kalıcı olarak bloke kalır;
            bilinçli V1 kapsam sınırı, bkz. WB3 düzeltme raporu)

    Returns:
        ComplianceResult — can_send + TAM durum (UI'da hepsi gösterilir,
        owner: "compliance-state display" ilkesi).

    Çağrıldığı yerler:
    - (henüz yok) [S5-WB4+]
    """
    reason_codes: list[str] = []
    now = datetime.utcnow()

    # ── 1) E-posta sözdizimi ────────────────────────────────────────────
    normalized = normalize_email(candidate_email)
    if not normalized:
        reason_codes.append(REASON_EMAIL_MISSING)
    elif not is_valid_email_syntax(normalized):
        reason_codes.append(REASON_EMAIL_INVALID_SYNTAX)

    if reason_codes:
        # Sözdizimi geçersizken suppression/recipient_legal_type gibi
        # sonraki adımların hiçbiri anlamlı değil — sahte-kesin bir
        # sınıflandırma UYDURMAK yerine "UNKNOWN"/"NOT_APPLICABLE" ile
        # erken dönülür.
        return ComplianceResult(
            can_send=False,
            reason_codes=reason_codes,
            recipient_category=RECIPIENT_CATEGORY_PROSPECT,
            contact_type=None,
            recipient_legal_type=RECIPIENT_LEGAL_TYPE_UNKNOWN,
            iys_status=settings.outreach_iys_status,
            suppression_status="UNKNOWN",
            source_status="NOT_APPLICABLE",
            kvkk_status="NOT_APPLICABLE",
            normalized_email=normalized,
            evaluated_at=now,
        )

    # ── 2) Suppression — KATEGORİDEN BAĞIMSIZ (owner madde D) ──────────
    suppressed = (
        db.query(db_models.SuppressionEntry.id)
        .filter(
            db_models.SuppressionEntry.tenant_id == tenant_id,
            db_models.SuppressionEntry.email_normalized == normalized,
        )
        .first()
        is not None
    )
    suppression_status = "SUPPRESSED" if suppressed else "CLEAR"
    if suppressed:
        reason_codes.append(REASON_SUPPRESSED)

    # ── 3) recipient_category — YALNIZ config whitelist'inden ──────────
    is_test_recipient = normalized in settings.outreach_test_recipient_email_set
    recipient_category = RECIPIENT_CATEGORY_TEST if is_test_recipient else RECIPIENT_CATEGORY_PROSPECT

    if is_test_recipient:
        # ── 4) TEST_RECIPIENT: İYS/KVKK/legal-type/source-evidence
        #        DEĞERLENDİRİLMEZ, yalnız suppression geçerli.
        return ComplianceResult(
            can_send=(suppression_status == "CLEAR"),
            reason_codes=reason_codes,
            recipient_category=recipient_category,
            contact_type=None,
            recipient_legal_type=RECIPIENT_LEGAL_TYPE_UNKNOWN,
            iys_status="IYS_NOT_APPLICABLE_TEST_RECIPIENT",
            suppression_status=suppression_status,
            source_status="NOT_APPLICABLE",
            kvkk_status="NOT_APPLICABLE",
            normalized_email=normalized,
            evaluated_at=now,
        )

    # ── 5) PROSPECT_RECIPIENT — tam değerlendirme ───────────────────────

    # 5a) Recipient bağlamı çözümleme + tenant sınırı (fail-closed —
    #     app/prospecting ile AYNI "tenant_id filtresi olmadan sorgu yok"
    #     disiplini).
    resolved_prospect_company_id = prospect_company_id

    if contact_id is not None:
        contact = (
            db.query(db_models.ProspectContact)
            .filter(
                db_models.ProspectContact.id == contact_id,
                db_models.ProspectContact.tenant_id == tenant_id,
            )
            .first()
        )
        if not contact:
            reason_codes.append(REASON_CONTACT_NOT_FOUND)
        elif resolved_prospect_company_id is None:
            resolved_prospect_company_id = contact.prospect_company_id

    if customer_id is not None:
        # NOT: Customer tablosunda tenant_id YOK — bu, mevcut proje-genel
        # davranışıdır (app/database.py Customer sınıfı; main.py ve
        # prospecting/service.py'deki TÜM Customer sorguları da yalnız
        # ID ile arar). Burada aynı davranış korunuyor, YENİ bir tenant
        # kuralı İCAT EDİLMİYOR.
        customer = (
            db.query(db_models.Customer)
            .filter(db_models.Customer.id == customer_id)
            .first()
        )
        if not customer:
            reason_codes.append(REASON_CUSTOMER_NOT_FOUND)

    resolved_company = None
    if resolved_prospect_company_id is not None:
        resolved_company = (
            db.query(db_models.ProspectCompany)
            .filter(
                db_models.ProspectCompany.id == resolved_prospect_company_id,
                db_models.ProspectCompany.tenant_id == tenant_id,
            )
            .first()
        )
        if not resolved_company:
            reason_codes.append(REASON_PROSPECT_COMPANY_NOT_FOUND)

    if prospect_company_id is None and contact_id is None and customer_id is None:
        reason_codes.append(REASON_RECIPIENT_CONTEXT_MISSING)

    # 5b) Source evidence — owner: SOURCE EVIDENCE MANDATORY, yalnız
    #     prospect_company bağlamında anlamlı (customer_id-yalnız senaryo
    #     "keşfedilen veri" değil, mevcut müşteri kaydıdır).
    if resolved_company is not None:
        has_source = (
            db.query(db_models.ProspectSource.id)
            .filter(
                db_models.ProspectSource.tenant_id == tenant_id,
                db_models.ProspectSource.prospect_company_id == resolved_company.id,
            )
            .first()
            is not None
        )
        source_status = "EVIDENCED" if has_source else "MISSING"
        if not has_source:
            reason_codes.append(REASON_SOURCE_EVIDENCE_MISSING)
    else:
        source_status = "NOT_APPLICABLE"

    # 5c) contact_type → kvkk_status (heuristik KANAL sinyali — BAĞIMSIZ
    #     eksen, her zaman taze adresten türetilir; bkz. modül docstring'i).
    contact_type = classify_contact_type(normalized)
    kvkk_status, kvkk_reason = _derive_kvkk_status(contact_type)
    if kvkk_reason:
        reason_codes.append(kvkk_reason)

    # 5d) verified_legal_type → recipient_legal_type (DOĞRULANMIŞ hukuki
    #     statü — AYRI, BAĞIMSIZ eksen; contact_type'tan ASLA türetilmez).
    recipient_legal_type, legal_type_reason = _resolve_recipient_legal_type(resolved_company)
    if legal_type_reason:
        reason_codes.append(legal_type_reason)

    # 5e) İYS durumu — owner: "IYS: UNKNOWN / CREDENTIALS NOT PROVIDED",
    #     bu yüzden settings.outreach_iys_status varsayılanı IYS_UNKNOWN'dır
    #     ve V1'de TÜM PROSPECT_RECIPIENT gönderimini hard-block eder; sahte
    #     bir İYS entegrasyonu İCAT EDİLMEZ (owner: "fake IYS integration YOK").
    iys_status = settings.outreach_iys_status
    if iys_status not in _IYS_STATUSES_NOT_BLOCKING:
        reason_codes.append(
            REASON_IYS_STATUS_BLOCKED if iys_status == "IYS_BLOCKED" else REASON_IYS_STATUS_UNKNOWN
        )

    return ComplianceResult(
        can_send=(len(reason_codes) == 0),
        reason_codes=reason_codes,
        recipient_category=recipient_category,
        contact_type=contact_type,
        recipient_legal_type=recipient_legal_type,
        iys_status=iys_status,
        suppression_status=suppression_status,
        source_status=source_status,
        kvkk_status=kvkk_status,
        normalized_email=normalized,
        evaluated_at=now,
    )
