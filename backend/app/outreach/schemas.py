"""
S5 — Outreach — Pydantic şemaları.

Alan sözleşmesi app/database.py OutreachMessage/SuppressionEntry/
OutreachTemplate docstring'leriyle birebir. app/prospecting/schemas.py ile
AYNI desen: from_attributes/orm_mode KULLANILMAZ — service.py ORM satırını
manuel olarak Out modeline çevirir (bkz. service.py::_message_to_out()).
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

OutreachMessageStatus = Literal[
    "DRAFT", "READY_FOR_REVIEW", "APPROVED", "SENDING", "SENT", "FAILED",
    "BOUNCED", "REPLIED", "SUPPRESSED", "CANCELLED",
]
RecipientCategory = Literal["PROSPECT_RECIPIENT", "TEST_RECIPIENT"]
RecipientLegalType = Literal["TACIR", "ESNAF", "BIREYSEL", "UNKNOWN"]
SuppressionReason = Literal["USER_REJECTED", "DO_NOT_CONTACT", "PERMANENT_BOUNCE", "LEGAL_BLOCK", "MANUAL_BLOCK"]


class CreateDraftRequest(BaseModel):
    prospect_company_id: Optional[int] = None
    contact_id: Optional[int] = None
    customer_id: Optional[int] = None
    # Owner: "AI yalnız editable body draft üretir." Varsayılan False —
    # AI-assist EXPLICIT bir kullanıcı tercihidir, sessiz varsayılan DEĞİL
    # (hem maliyet hem "deterministic guardrail" ilkesi gereği).
    use_ai: bool = False


class FinalizeDraftRequest(BaseModel):
    subject: Optional[str] = Field(default=None, max_length=500)
    editable_body: Optional[str] = None


class ComplianceOut(BaseModel):
    can_send: bool
    reason_codes: list[str]
    recipient_category: str
    contact_type: Optional[str] = None
    recipient_legal_type: str
    iys_status: str
    suppression_status: str
    source_status: str
    kvkk_status: str
    normalized_email: Optional[str] = None
    evaluated_at: str


class OutreachMessageOut(BaseModel):
    id: int
    prospect_company_id: Optional[int] = None
    customer_id: Optional[int] = None
    contact_id: Optional[int] = None
    recipient_email_snapshot: str
    recipient_legal_type: Optional[str] = None
    recipient_category: str
    channel: str
    subject: str
    body_snapshot: str  # EDİTABLE blok
    system_footer_snapshot: str  # SYSTEM/immutable blok — bkz. app/database.py
    status: str
    provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    approved_at: Optional[str] = None
    sent_at: Optional[str] = None
    failed_at: Optional[str] = None
    failure_code: Optional[str] = None
    source_snapshot_json: Optional[dict] = None
    compliance_snapshot_json: Optional[dict] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SuppressionEntryOut(BaseModel):
    id: int
    email_normalized: str
    reason: str
    source: Optional[str] = None
    note: Optional[str] = None
    created_at: Optional[str] = None
    effective_at: Optional[str] = None


class SuppressionCreateRequest(BaseModel):
    email: str = Field(max_length=255)
    reason: SuppressionReason
    note: Optional[str] = None
