"""
S4 — Prospecting — Pydantic şemaları.

Alan sözleşmesi owner'ın DATA MODEL bölümüyle birebir — bkz.
app/database.py ProspectCompany/ProspectContact/ProspectSource
docstring'leri.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

ProspectStatus = Literal["DISCOVERED", "VERIFIED", "QUALIFIED", "DISQUALIFIED", "CONVERTED"]
QualificationReason = Literal[
    "sector_fit", "location_fit", "has_corporate_contact", "energy_intensive_signal",
    "too_small_unsuitable", "duplicate", "other",
]
ContactType = Literal["GENERAL_CORPORATE", "DEPARTMENT", "NAMED_CORPORATE_PERSON", "PERSONAL_OR_FREE_MAIL", "OTHER"]
DedupVerdict = Literal["exact_duplicate", "probable_duplicate", "distinct"]


class ProspectCompanyCreateRequest(BaseModel):
    legal_name: Optional[str] = Field(default=None, max_length=255)
    trade_name: Optional[str] = Field(default=None, max_length=255)
    website: Optional[str] = Field(default=None, max_length=500)
    sector: Optional[str] = Field(default=None, max_length=255)
    city: Optional[str] = Field(default=None, max_length=100)
    district: Optional[str] = Field(default=None, max_length=100)
    industrial_zone: Optional[str] = Field(default=None, max_length=255)
    address: Optional[str] = None
    phone: Optional[str] = Field(default=None, max_length=50)
    source_url: Optional[str] = Field(default=None, max_length=1000)  # discovery candidate'tan geliyorsa
    source_type: Optional[str] = Field(default=None, max_length=30)
    # Dedup review sonrası "kullanıcı yine de ayrı kayıt istiyor" akışı —
    # owner: "silent merge YOK", bu yüzden bu her zaman EXPLICIT bir
    # ikinci istektir, otomatik değil.
    force_create_despite_duplicate: bool = False

    @model_validator(mode="after")
    def _at_least_one_identifier(self) -> "ProspectCompanyCreateRequest":
        if not (self.legal_name or self.trade_name or self.website):
            raise ValueError("legal_name, trade_name veya website alanlarından en az biri gerekli.")
        return self


class ProspectCompanyUpdateRequest(BaseModel):
    legal_name: Optional[str] = Field(default=None, max_length=255)
    trade_name: Optional[str] = Field(default=None, max_length=255)
    website: Optional[str] = Field(default=None, max_length=500)
    sector: Optional[str] = Field(default=None, max_length=255)
    city: Optional[str] = Field(default=None, max_length=100)
    district: Optional[str] = Field(default=None, max_length=100)
    industrial_zone: Optional[str] = Field(default=None, max_length=255)
    address: Optional[str] = None
    phone: Optional[str] = Field(default=None, max_length=50)


class ProspectSourceOut(BaseModel):
    id: int
    prospect_company_id: int
    source_url: str
    source_type: str
    source_title: Optional[str] = None
    evidence_text: Optional[str] = None
    fetch_status: str
    discovered_at: Optional[str] = None
    last_checked_at: Optional[str] = None


class ProspectContactOut(BaseModel):
    id: int
    prospect_company_id: int
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    contact_type: str
    verification_status: str
    source_id: Optional[int] = None
    created_at: Optional[str] = None


class ProspectCompanyOut(BaseModel):
    id: int
    legal_name: Optional[str] = None
    trade_name: Optional[str] = None
    website: Optional[str] = None
    normalized_domain: Optional[str] = None
    sector: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    industrial_zone: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    status: str
    qualification_reason: Optional[str] = None
    qualification_note: Optional[str] = None
    duplicate_of_id: Optional[int] = None
    customer_id: Optional[int] = None
    discovered_at: Optional[str] = None
    last_verified_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    contact_count: int = 0
    source_count: int = 0


class ProspectCompanyListResponse(BaseModel):
    items: list[ProspectCompanyOut]
    total: int


class DedupMatchOut(BaseModel):
    company_id: int
    match_signal: str
    display_name: Optional[str] = None
    website: Optional[str] = None
    city: Optional[str] = None


class ProspectCreateResponse(BaseModel):
    # "created": yeni kayıt oluşturuldu (distinct veya force ile).
    # "exact_duplicate": mevcut kayıt döndürüldü, YENİ KAYIT AÇILMADI.
    # "review_required": olası duplicate — kullanıcı karar vermeden HİÇBİR
    #   kayıt oluşturulmadı (owner: "silent merge YOK").
    dedup_verdict: Literal["created", "exact_duplicate", "review_required"]
    matches: list[DedupMatchOut] = []
    prospect: Optional[ProspectCompanyOut] = None


class QualifyRequest(BaseModel):
    reason: QualificationReason
    note: Optional[str] = None


class DisqualifyRequest(BaseModel):
    reason: QualificationReason
    note: Optional[str] = None


class DiscoverRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=255)
    city: Optional[str] = Field(default=None, max_length=100)
    district: Optional[str] = Field(default=None, max_length=100)


class DiscoverCandidateOut(BaseModel):
    title: str
    url: str
    snippet: str


class DiscoverResponse(BaseModel):
    status: Literal["OK", "UNAVAILABLE", "FETCH_FAILED"]
    message: Optional[str] = None
    candidates: list[DiscoverCandidateOut] = []


class EnrichResponse(BaseModel):
    prospect: ProspectCompanyOut
    pages_fetched: int
    new_contacts: list[ProspectContactOut]
    new_sources: list[ProspectSourceOut]


class CustomerMatchOut(BaseModel):
    customer_id: int
    name: str
    company: Optional[str] = None
    email: Optional[str] = None


class ConvertRequest(BaseModel):
    existing_customer_id: Optional[int] = None
    # Dedup adayı gösterildikten SONRA kullanıcı "hayır, yine de yeni
    # Customer oluştur" derse bu True gönderilir — owner: "user
    # confirmation" adımı, sessiz otomatik karar YOK.
    force_create_new_customer: bool = False
    create_activity: bool = True
    create_first_task: bool = False
    first_task_title: Optional[str] = Field(default=None, max_length=255)
    first_task_due_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _task_title_required_if_task(self) -> "ConvertRequest":
        if self.create_first_task and not (self.first_task_title and self.first_task_title.strip()):
            raise ValueError("create_first_task=true ise first_task_title zorunludur.")
        return self


class ConvertResponse(BaseModel):
    status: Literal["confirmation_required", "converted"]
    potential_matches: list[CustomerMatchOut] = []
    prospect: Optional[ProspectCompanyOut] = None
    customer_id: Optional[int] = None
    customer_created: Optional[bool] = None
    activity_created: bool = False
    task_created: bool = False
    # Post-commit ikincil adım (Activity/Task) başarısız olursa (owner
    # emsali: "post-commit offer lifecycle failure isolation") ana
    # conversion YİNE DE başarılı sayılır ama bu alan kullanıcıya
    # AÇIKÇA bildirir — sessizce yutulmaz.
    warnings: list[str] = []
