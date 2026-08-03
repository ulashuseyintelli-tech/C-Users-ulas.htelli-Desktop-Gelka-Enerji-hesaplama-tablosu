"""
Sözleşme oluşturma V1 — Pydantic şemaları.

Extraction modelleri mevcut FieldValue/StringFieldValue deseninin
(app/models.py) genişletilmiş hali: her alan value + confidence + kaynak
belge + kaynak sayfa + kanıt metni taşır. Hiçbir alan bu şema seviyesinde
"kesinleşmiş" sayılmaz — kullanıcı onayı/düzeltmesi document_field_candidates
tablosunda ayrıca tutulur (bkz. app/database.py).

GÜVENLİK NOTU (owner kararı): T.C. kimlik no yalnız sözleşme hazırlama/
review/preview/final PDF akışlarına özel şemalarda (FieldCandidateOut,
RepresentativeDetailOut) tam görünür. Genel listeleme için
RepresentativeSummaryOut kullanılır — orada national_id YOKTUR.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field


DocumentType = Literal["vergi_levhasi", "imza_sirkusu"]


class DocumentFieldValue(BaseModel):
    """Tek bir alanın OCR çıkarım sonucu — hiçbir zaman doğrudan kesinleşmiş kabul edilmez."""
    value: Optional[str] = None
    confidence: float = 0.0
    source_document: DocumentType
    source_page: int = 1
    source_text: str = ""  # kanıt (evidence)


class TaxCertificateExtraction(BaseModel):
    """Vergi levhasından çıkarılan alanlar."""
    legal_name: DocumentFieldValue
    tax_number: DocumentFieldValue
    tax_office: DocumentFieldValue
    facility_address: DocumentFieldValue
    activity_code: DocumentFieldValue
    establishment_date: DocumentFieldValue


class SignatureCircularExtraction(BaseModel):
    """İmza sirkülerinden çıkarılan alanlar."""
    legal_name: DocumentFieldValue
    registered_address: DocumentFieldValue
    representative_full_name: DocumentFieldValue
    representative_national_id: DocumentFieldValue
    authority_type: DocumentFieldValue
    authority_scope: DocumentFieldValue
    authority_start_date: DocumentFieldValue
    authority_end_date: DocumentFieldValue
    is_indefinite: DocumentFieldValue
    trade_registry_number: DocumentFieldValue
    mersis_number: DocumentFieldValue
    tax_office: DocumentFieldValue
    notary_name: DocumentFieldValue
    notary_date: DocumentFieldValue
    notary_document_number: DocumentFieldValue


# ═══════════════════════════════════════════════════════════════════════════════
# API request/response modelleri
# ═══════════════════════════════════════════════════════════════════════════════

class DocumentUploadResponse(BaseModel):
    document_id: int
    document_type: DocumentType
    processing_status: str
    sha256: str
    is_duplicate: bool = False


class ExtractionStartResponse(BaseModel):
    extraction_run_id: int
    document_id: int
    status: str


class FieldCandidateOut(BaseModel):
    """
    Review ekranı için tek bir alan adayı. BİLİNÇLİ OLARAK national_id/tam
    değer içerebilir (field_name='representative_national_id' olduğunda) —
    bu, owner'ın "review ekranında tam görünür" kararıyla uyumludur. Bu
    şema yalnız extraction-review endpoint'lerinde kullanılır, genel
    listeleme endpoint'lerinde ASLA kullanılmaz.
    """
    id: int
    field_name: str
    document_id: int
    source_document: DocumentType
    raw_value: Optional[str] = None
    normalized_value: Optional[str] = None
    confidence: float
    source_page: int
    source_text: Optional[str] = None
    validation_status: str
    conflict_status: str
    user_decision: Optional[str] = None
    corrected_value: Optional[str] = None


class ExtractionResultOut(BaseModel):
    extraction_run_id: int
    document_id: int
    document_type: DocumentType
    status: str
    error_code: Optional[str] = None
    candidates: list[FieldCandidateOut] = Field(default_factory=list)


class ConflictResolutionRequest(BaseModel):
    """Kullanıcının bir alan adayı için verdiği karar."""
    candidate_id: int
    decision: Literal["accepted", "corrected", "rejected"]
    corrected_value: Optional[str] = None
    decided_by: Optional[str] = None


class ConflictDetectionRequest(BaseModel):
    """Verilen belge id'leri arasında çelişki tespiti tetikler (aynı sözleşme
    hazırlama oturumunda yüklenen vergi levhası + imza sirküleri gibi)."""
    document_ids: list[int]


class ConflictDetectionResponse(BaseModel):
    changed_candidates: list[FieldCandidateOut]


class LegalProfileSaveRequest(BaseModel):
    customer_id: Optional[int] = None
    legal_name: str
    tax_number: str
    tax_office: str
    mersis_number: Optional[str] = None
    trade_registry_number: Optional[str] = None
    registered_address: str
    facility_address: Optional[str] = None
    notification_address: Optional[str] = None


class LegalProfileOut(BaseModel):
    id: int
    customer_id: Optional[int] = None
    legal_name: str
    tax_number: str
    tax_office: str
    mersis_number: Optional[str] = None
    trade_registry_number: Optional[str] = None
    registered_address: str
    facility_address: Optional[str] = None
    notification_address: Optional[str] = None
    verification_status: str


class RepresentativeSaveRequest(BaseModel):
    customer_id: Optional[int] = None
    legal_profile_id: Optional[int] = None
    full_name: str
    national_id: str
    authority_type: Optional[str] = None
    authority_scope: Optional[str] = None
    authority_start_date: Optional[str] = None  # ISO date string
    authority_end_date: Optional[str] = None
    is_indefinite: bool = False
    source_document_id: Optional[int] = None


class RepresentativeSummaryOut(BaseModel):
    """Genel listeleme için GÜVENLİ özet — national_id burada YOK."""
    id: int
    full_name: str
    authority_type: Optional[str] = None
    is_indefinite: bool
    verification_status: str


class RepresentativeDetailOut(RepresentativeSummaryOut):
    """Yalnız sözleşme hazırlama/review/preview akışında kullanılır — tam national_id içerir."""
    national_id: str
    authority_scope: Optional[str] = None
    authority_start_date: Optional[str] = None
    authority_end_date: Optional[str] = None


class ContractDraftCreateRequest(BaseModel):
    offer_id: int
    customer_id: Optional[int] = None
    legal_profile_id: Optional[int] = None
    authorized_representative_id: Optional[int] = None


class ContractCompleteFieldsRequest(BaseModel):
    """
    Ek Protokol'deki, ne vergi levhasından ne imza sirkülerinden ne de
    offer'dan gelen, kullanıcının elle tamamlaması gereken alanlar.
    """
    start_date: str  # ISO date
    duration_months: int
    tariff_group: Optional[str] = None
    subscription_codes: Optional[str] = None


class TariffGroupResolution(BaseModel):
    """Offer.extraction_result JSON'undan tarife grubu çözümlemesi — snapshot'a
    kaynak yolu ve durumla birlikte yazılır (owner madde 13)."""
    value: Optional[str] = None
    source_path: str
    resolution_status: Literal["resolved", "not_found", "ambiguous"]


class ContractPreviewResponse(BaseModel):
    contract_id: int
    status: str
    rendered_html_sections: list[str]  # 4 bölüm, sırayla: ana sözleşme, ek protokol, tahliye, bilgilendirme


class ContractFinalizeResponse(BaseModel):
    contract_id: int
    status: str
    pdf_storage_ref: str
    pdf_sha256: str
    contract_number: Optional[str] = None


class ContractOut(BaseModel):
    """Genel sözleşme özeti — national_id İÇERMEZ."""
    id: int
    customer_id: Optional[int] = None
    offer_id: int
    contract_number: Optional[str] = None
    status: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    created_at: str
