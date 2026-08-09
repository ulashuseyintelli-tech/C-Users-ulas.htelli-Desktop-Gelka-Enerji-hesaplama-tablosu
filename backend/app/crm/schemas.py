"""
S2 — Activity & Task Engine — Pydantic şemaları.

Subject modeli: her Activity/Task tam olarak bir subject'e (customer_id
VEYA offer_id VEYA contract_id) bağlıdır — hiçbiri veya birden fazlası
DEĞİL. Bu invariant burada (request şemalarında) validator ile, DB
seviyesinde ise app/crm/service.py'de (subject'in gerçekten var olduğunu
doğrulayan fail-closed kontrol) uygulanır — bkz. app/database.py Activity/
Task docstring'leri (owner kararı, S2 preflight).

created_by/assignee alanları BİLİNÇLİ OLARAK yok (canonical User modeli
yok, SINGLE OPERATOR — owner kararı madde 5).
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

ActivityType = Literal["NOTE", "CALL", "EMAIL", "MEETING", "TASK_COMPLETED"]
TaskStatus = Literal["OPEN", "COMPLETED", "CANCELLED"]

# "TASK_COMPLETED" yalnız sistem tarafından (Task tamamlanınca) üretilir —
# kullanıcı doğrudan bu tipte bir Activity OLUŞTURAMAZ (owner: "Activity'den
# otomatik Task üretimi yalnız açıkça tanımlanan davranışlarda yapılacak").
_USER_CREATABLE_ACTIVITY_TYPES: tuple[str, ...] = ("NOTE", "CALL", "EMAIL", "MEETING")


class SubjectMixin(BaseModel):
    customer_id: Optional[int] = None
    offer_id: Optional[int] = None
    contract_id: Optional[int] = None

    @model_validator(mode="after")
    def _exactly_one_subject(self) -> "SubjectMixin":
        provided = [v for v in (self.customer_id, self.offer_id, self.contract_id) if v is not None]
        if len(provided) != 1:
            raise ValueError(
                "Tam olarak bir subject belirtilmeli: customer_id, offer_id veya contract_id (ikisi birden ya da hiçbiri olmaz)."
            )
        return self


class ActivityCreateRequest(SubjectMixin):
    activity_type: ActivityType
    title: Optional[str] = Field(default=None, max_length=255)
    body: Optional[str] = None
    occurred_at: Optional[datetime] = None  # verilmezse "şimdi" (naive UTC)

    @model_validator(mode="after")
    def _user_creatable_type(self) -> "ActivityCreateRequest":
        if self.activity_type not in _USER_CREATABLE_ACTIVITY_TYPES:
            raise ValueError(
                f"'{self.activity_type}' türünde bir Activity doğrudan oluşturulamaz — yalnız sistem tarafından üretilir."
            )
        return self


class ActivityOut(BaseModel):
    id: int
    customer_id: Optional[int] = None
    offer_id: Optional[int] = None
    contract_id: Optional[int] = None
    activity_type: str
    title: Optional[str] = None
    body: Optional[str] = None
    occurred_at: str
    created_at: str
    # "manual" → activities tablosundan; "audit" → audit_logs projection'ından
    # (yalnız offer subject'inde, OFFER_STATUS_CHANGED). Duplicate yazma
    # yapılmadığının kanıtı: bu alan olmadan iki kaynağı ayırt edemezdik.
    source: Literal["manual", "audit"] = "manual"


class TaskCreateRequest(SubjectMixin):
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    due_at: Optional[datetime] = None


class TaskUpdateRequest(BaseModel):
    """Yalnız editable alanlar — status burada YOK (complete/cancel ayrı endpoint)."""
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    due_at: Optional[datetime] = None


class TaskOut(BaseModel):
    id: int
    customer_id: Optional[int] = None
    offer_id: Optional[int] = None
    contract_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    due_at: Optional[str] = None
    status: str
    completed_at: Optional[str] = None
    created_at: str
    updated_at: str


class TodayResponse(BaseModel):
    """
    S2 "Bugün" projeksiyonu — ayrı bir tablo değil, mevcut Task/Activity/
    S1 verisinden anlık türetilir (owner kararı, madde 3).
    """
    due_today_count: int
    overdue_count: int
    due_today_tasks: list[TaskOut]
    overdue_tasks: list[TaskOut]
    recent_activities: list[ActivityOut]
    # S1'in güvenilir özet metrikleri (GET /stats ile aynı kaynak, N+1 yok)
    total_customers: int
    total_open_offers: int
    total_accepted_offers: int
    total_finalized_contracts: int


# =============================================================================
# S3 — Sales Pipeline — derived projection şemaları.
#
# Yeni tablo/kolon YOK (owner kararı, S3 GO madde 1) — bu şemalar Offer/
# Contract/Activity/Task'in mevcut alanlarından anlık hesaplanan bir
# GÖRÜNÜMÜ temsil eder, DB'ye hiçbir şey yazmaz.
# =============================================================================

# Canonical UI kolonları (owner kararı, S3 GO madde 2). "İletişimde" ve
# "Görüşme/Takip" burada YOK — bunlar pipeline_stage değil, kart üzerinde
# last_activity/next_open_task olarak ayrıca gösterilen CONTEXT'tir.
PipelineStage = Literal["DRAFT", "SENT", "ACCEPTED", "CONTRACT", "COMPLETED", "LOST"]

# Owner'ın 3 örneği (madde 3 precedence + madde 13) + defensive fallback.
# UNKNOWN_OFFER_STATUS pratikte hiç tetiklenmemeli (offer_lifecycle.py'de
# sabit 8 durum var, S3 yeni bir OfferStatus icat etmiyor) — yalnız "hiçbir
# kayıt sessizce kaybolmayacak" ilkesini kod seviyesinde garanti eder.
PipelineWarning = Literal[
    "CONTRACT_STATUS_WITHOUT_CONTRACT",
    "COMPLETED_WITHOUT_CONTRACT",
    "MISSING_CUSTOMER",
    "UNKNOWN_OFFER_STATUS",
]


class PipelineCardOut(BaseModel):
    offer_id: int
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None  # None → frontend "Müşterisiz" gösterir (S1 konvansiyonu, ContractsScreen.tsx ile aynı)
    offer_date: str  # Offer.created_at (ISO, naive UTC)
    offer_total: float
    agreement_multiplier: float
    offer_status: str  # ham OfferStatus — frontend VALID_OFFER_TRANSITIONS kopyalamaz (owner madde 8)
    pipeline_stage: PipelineStage
    # Tekil alan (owner'ın verdiği isimle uyumlu): precedence-kaynaklı
    # tutarsızlık (CONTRACT_STATUS_WITHOUT_CONTRACT/COMPLETED_WITHOUT_CONTRACT)
    # MISSING_CUSTOMER'dan öncelikli — ikisi birden oluşursa precedence
    # warning'i gösterilir (bkz. service.py _compute_pipeline_stage).
    pipeline_warning: Optional[PipelineWarning] = None
    has_contract: bool
    contract_id: Optional[int] = None
    contract_status: Optional[str] = None
    last_activity: Optional[ActivityOut] = None
    next_open_task: Optional[TaskOut] = None
    overdue_task_count: int
    allowed_transitions: list[str]


class PipelineResponse(BaseModel):
    cards: list[PipelineCardOut]
    total: int  # filtre sonrası toplam kayıt sayısı (frontend pagination)
