from pydantic import BaseModel
from typing import Optional, List
from enum import Enum


class InvoiceStatus(str, Enum):
    """Fatura işlem durumu"""
    UPLOADED = "UPLOADED"      # Yüklendi, henüz analiz edilmedi
    PROCESSING = "PROCESSING"  # İşleniyor (async job)
    EXTRACTED = "EXTRACTED"    # Extraction tamamlandı
    NEEDS_INPUT = "NEEDS_INPUT"  # Eksik alan var, kullanıcı girişi gerekli
    READY = "READY"            # Teklif hesaplamaya hazır
    FAILED = "FAILED"          # İşlem başarısız


class JobType(str, Enum):
    """Job tipleri"""
    EXTRACT = "EXTRACT"
    VALIDATE = "VALIDATE"
    EXTRACT_AND_VALIDATE = "EXTRACT_AND_VALIDATE"


class JobStatus(str, Enum):
    """Job durumları"""
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class OfferStatus(str, Enum):
    """Teklif yaşam döngüsü durumları"""
    DRAFT = "draft"           # Taslak - henüz gönderilmedi
    SENT = "sent"             # Müşteriye gönderildi
    VIEWED = "viewed"         # Müşteri teklifi görüntüledi
    ACCEPTED = "accepted"     # Müşteri kabul etti
    REJECTED = "rejected"     # Müşteri reddetti
    CONTRACTING = "contracting"  # Sözleşme aşamasında
    COMPLETED = "completed"   # Sözleşme tamamlandı
    EXPIRED = "expired"       # Teklif süresi doldu


class AuditAction(str, Enum):
    """Audit log aksiyonları"""
    INVOICE_UPLOADED = "invoice_uploaded"
    INVOICE_EXTRACTED = "invoice_extracted"
    INVOICE_VALIDATED = "invoice_validated"
    OFFER_CREATED = "offer_created"
    OFFER_STATUS_CHANGED = "offer_status_changed"
    OFFER_PDF_GENERATED = "offer_pdf_generated"
    CUSTOMER_CREATED = "customer_created"
    CUSTOMER_UPDATED = "customer_updated"
    WEBHOOK_SENT = "webhook_sent"
    WEBHOOK_FAILED = "webhook_failed"


# ═══════════════════════════════════════════════════════════════════════════════
# DEBUG META - P0.5: Tek bakışta teşhis için
# ═══════════════════════════════════════════════════════════════════════════════

class DebugMeta(BaseModel):
    """
    Debug bilgileri - UI'da "Detay" panelinde gösterilir.
    Hesaplama ve fiyatlama hatalarını tek bakışta teşhis eder.
    """
    # Trace
    trace_id: str = ""  # UUID - log'larla eşleştirme için
    
    # Dönem ve Fiyatlama
    pricing_period: Optional[str] = None  # YYYY-MM
    pricing_source: str = "unknown"  # reference | override | default | not_found
    ptf_tl_per_mwh: float = 0
    yekdem_tl_per_mwh: float = 0
    
    # Dağıtım Tarifesi
    epdk_tariff_key: Optional[str] = None  # SANAYI/OG/CIFT
    distribution_unit_price_tl_per_kwh: float = 0
    distribution_source: str = "unknown"  # epdk_tariff | manual_override | extracted | not_found
    
    # Hesaplanan Tutarlar
    consumption_kwh: float = 0
    energy_amount_tl: float = 0
    distribution_amount_tl: float = 0
    btv_amount_tl: float = 0
    kdv_amount_tl: float = 0
    total_amount_tl: float = 0
    
    # Uyarılar ve Hatalar
    warnings: List[str] = []
    errors: List[str] = []
    
    # Extraction Debug (opsiyonel - ?debug=1 ile)
    llm_model_used: Optional[str] = None
    llm_raw_output_truncated: Optional[str] = None  # İlk 2000 char
    json_repair_applied: bool = False
    extraction_cache_hit: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# QUALITY SCORE - Sprint 3
# ═══════════════════════════════════════════════════════════════════════════════

class QualityFlagDetail(BaseModel):
    """Kalite bayrağı detayı"""
    code: str
    severity: str
    message: str
    deduction: int = 0


class QualityScoreResult(BaseModel):
    """Kalite skoru sonucu"""
    score: int  # 0-100
    grade: str  # OK, CHECK, BAD
    flags: List[str] = []
    flag_details: List[QualityFlagDetail] = []


class FieldValue(BaseModel):
    value: Optional[float] = None
    confidence: float = 0.0
    evidence: str = ""
    page: int = 1

class RawBreakdown(BaseModel):
    energy_total_tl: Optional[FieldValue] = None
    distribution_total_tl: Optional[FieldValue] = None
    btv_tl: Optional[FieldValue] = None
    vat_tl: Optional[FieldValue] = None

class InvoiceMeta(BaseModel):
    """Fatura meta bilgileri (tahminler)"""
    tariff_group_guess: str = "unknown"  # Mesken, Ticarethane, Sanayi, Tarimsal, Aydinlatma
    voltage_guess: str = "unknown"  # AG, OG, YG
    term_type_guess: str = "unknown"  # Tek Terim, Çift Terim, Çok Zamanlı
    invoice_type_guess: str = "unknown"  # Tip-1..Tip-7 (fatura yapı tipi)
    # Tip-1: Toplam kWh + birim fiyat açık (Enerjisa)
    # Tip-2: Çok zamanlı ama toplam satırı var (Ekvator)
    # Tip-3: Kademeli/çok satırlı enerji (CK) - ağırlıklı ortalama gerekir
    # Tip-4: Dağıtım birim fiyatı yok, sadece toplam var
    # Tip-5: Demand/güç bedeli/reaktif var (OSB, OG, sanayi)
    # Tip-6: Birden fazla sayaç/tesisat
    # Tip-7: Mahsuplaşma/iade/düzeltme (negatif satırlar)


class ExtraItem(BaseModel):
    """Ek kalem (reaktif, mahsuplaşma, düzeltme, etc.) - Tip-5/7 için"""
    label: str = ""  # "Reaktif Bedel", "Mahsup", "Sayaç Hizmet Bedeli", etc.
    amount_tl: Optional[float] = None  # Tutar (negatif olabilir - mahsuplaşma için)
    confidence: float = 0.0  # Extraction confidence
    evidence: str = ""  # Faturadaki kaynak satır
    page: int = 1  # Sayfa numarası
    category: str = "other"  # reactive, adjustment, service_fee, other
    included_in_offer: bool = False  # Teklif hesabına dahil mi?


class MeterReading(BaseModel):
    """Sayaç okuma - Tip-6 (Multi sayaç) için"""
    meter_id: str = ""  # Sayaç numarası
    meter_type: str = "main"  # main, sub, backup
    consumption_kwh: float = 0  # Bu sayacın tüketimi
    unit_price_tl_per_kwh: Optional[float] = None  # Sayaca özel birim fiyat (varsa)
    tariff_type: str = "single"  # single, multi_time, tiered
    evidence: str = ""  # Faturadaki kaynak satır
    page: int = 1


class AdjustmentItem(BaseModel):
    """Mahsuplaşma/İade kalemi - Tip-7 için"""
    label: str = ""  # "Önceki Dönem Mahsubu", "İade", "Düzeltme"
    amount_tl: float = 0  # Tutar (genelde negatif)
    period: str = ""  # İlgili dönem (örn: "2024-10")
    reason: str = ""  # Mahsup nedeni
    evidence: str = ""
    page: int = 1


class LineType(str, Enum):
    """Satır tipi sınıflandırması"""
    ENERGY = "energy"           # Aktif enerji bedeli
    DISTRIBUTION = "distribution"  # Dağıtım bedeli (kWh bazlı)
    DEMAND = "demand"           # Güç/demand bedeli (kW bazlı)
    TAX = "tax"                 # Vergi (BTV, KDV, TRT, vb.)
    SERVICE = "service"         # Hizmet bedeli (sayaç okuma, vb.)
    REACTIVE = "reactive"       # Reaktif enerji
    ADJUSTMENT = "adjustment"   # Mahsuplaşma/düzeltme
    OTHER = "other"             # Diğer


class MeasurementUnit(str, Enum):
    """Ölçüm birimi"""
    TL_PER_KWH = "TL/kWh"       # Enerji, dağıtım
    TL_PER_KW = "TL/kW"         # Demand/güç
    TL_PER_KVARH = "TL/kVArh"   # Reaktif
    TL_PER_DAY = "TL/day"       # Günlük hizmet
    TL_PER_MONTH = "TL/month"   # Aylık hizmet
    TL = "TL"                   # Sabit tutar
    PERCENT = "%"               # Yüzde (vergi)
    KWH = "kWh"                 # Miktar birimi
    KW = "kW"                   # Güç birimi
    KVARH = "kVArh"             # Reaktif birimi


class LineItem(BaseModel):
    """Kalem bazlı satır - cross-check için kritik"""
    label: str = ""  # "Yüksek Kademe", "Düşük Kademe", "Gündüz", "Puant", "Gece", "Aktif Enerji", "Dağıtım Bedeli"
    qty: float = 0  # Miktar (kWh, kW, gün, vb.)
    unit: str = "kWh"  # Miktar birimi (kWh, kW, day, vb.)
    
    # YENİ: Satır tipi ve birim fiyat birimi
    line_type: str = "other"  # energy, distribution, demand, tax, service, reactive, adjustment, other
    measurement_unit: str = "TL/kWh"  # TL/kWh, TL/kW, TL/day, TL, %, vb.
    unit_price: Optional[float] = None  # Birim fiyat (TL/kWh)
    amount_tl: Optional[float] = None  # Tutar (TL)
    confidence: float = 0.0
    evidence: str = ""
    page: int = 1
    # Cross-check sonucu
    crosscheck_passed: bool = True  # qty × unit_price ≈ amount_tl?
    crosscheck_delta: Optional[float] = None  # Fark oranı (%)

class StringFieldValue(BaseModel):
    """String değerli alanlar için (ETTN, fatura no, vb.)"""
    value: Optional[str] = None
    confidence: float = 0.0
    evidence: str = ""
    page: int = 1


class ConsumerInfo(BaseModel):
    """Tüketici bilgileri"""
    title: Optional[StringFieldValue] = None  # Tüketici Ünvanı
    vkn: Optional[StringFieldValue] = None  # Vergi Kimlik No
    tckn: Optional[StringFieldValue] = None  # TC Kimlik No (bireysel)
    facility_address: Optional[StringFieldValue] = None  # Tesis Adresi
    eic_code: Optional[StringFieldValue] = None  # EIC Kodu
    contract_no: Optional[StringFieldValue] = None  # Sözleşme No
    meter_no: Optional[StringFieldValue] = None  # Sayaç No


class ConsumptionDetails(BaseModel):
    """Tüketim detayları (çok zamanlı ve reaktif dahil)"""
    total_kwh: FieldValue  # Toplam tüketim
    t1_kwh: Optional[FieldValue] = None  # T1 (Gündüz)
    t2_kwh: Optional[FieldValue] = None  # T2 (Puant)
    t3_kwh: Optional[FieldValue] = None  # T3 (Gece)
    reactive_inductive_kvarh: Optional[FieldValue] = None  # Endüktif reaktif
    reactive_capacitive_kvarh: Optional[FieldValue] = None  # Kapasitif reaktif
    demand_kw: Optional[FieldValue] = None  # Demand (maksimum güç)


class ChargeBreakdown(BaseModel):
    """Kalem bazlı tutarlar"""
    active_energy_amount: Optional[FieldValue] = None  # Aktif enerji bedeli
    distribution_amount: Optional[FieldValue] = None  # Dağıtım bedeli
    yek_amount: Optional[FieldValue] = None  # YEK/YEKDEM bedeli
    reactive_penalty_amount: Optional[FieldValue] = None  # Reaktif ceza
    consumption_tax: Optional[FieldValue] = None  # Elektrik tüketim vergisi
    energy_fund: Optional[FieldValue] = None  # Enerji fonu
    trt_share: Optional[FieldValue] = None  # TRT payı
    vat_amount: Optional[FieldValue] = None  # KDV
    total_amount: Optional[FieldValue] = None  # Ödenecek tutar


class UnitPrices(BaseModel):
    """Birim fiyatlar"""
    active_energy: Optional[FieldValue] = None  # TL/kWh
    distribution: Optional[FieldValue] = None  # TL/kWh
    yek: Optional[FieldValue] = None  # TL/kWh
    t1: Optional[FieldValue] = None  # T1 birim fiyat
    t2: Optional[FieldValue] = None  # T2 birim fiyat
    t3: Optional[FieldValue] = None  # T3 birim fiyat
    demand: Optional[FieldValue] = None  # TL/kW


class TariffInfo(BaseModel):
    """Tarife bilgileri"""
    voltage_level: str = "unknown"  # AG, OG, YG
    tariff_type: str = "unknown"  # mesken, ticarethane, sanayi, tarimsal, aydinlatma
    time_of_use: str = "unknown"  # single, multi_time, tiered


class InvoiceExtraction(BaseModel):
    """Genişletilmiş fatura extraction modeli"""
    # Temel bilgiler
    vendor: str = "unknown"
    distributor: str = "unknown"
    
    # Fatura kimlik bilgileri
    ettn: Optional[StringFieldValue] = None  # e-Fatura Tekil Numarası
    invoice_no: Optional[StringFieldValue] = None  # Fatura Numarası
    invoice_date: Optional[StringFieldValue] = None  # Fatura Tarihi (YYYY-MM-DD)
    invoice_period: str = ""  # Fatura Dönemi (YYYY-MM)
    due_date: Optional[StringFieldValue] = None  # Son Ödeme Tarihi
    
    # Tüketici bilgileri
    consumer: Optional[ConsumerInfo] = None
    
    # Tüketim bilgileri (genişletilmiş)
    consumption: Optional[ConsumptionDetails] = None
    
    # Kalem bazlı tutarlar
    charges: Optional[ChargeBreakdown] = None
    
    # Birim fiyatlar
    unit_prices: Optional[UnitPrices] = None
    
    # Tarife bilgileri
    tariff: Optional[TariffInfo] = None
    
    # Eski alanlar (geriye uyumluluk için)
    consumption_kwh: FieldValue = FieldValue()
    current_active_unit_price_tl_per_kwh: FieldValue = FieldValue()
    distribution_unit_price_tl_per_kwh: FieldValue = FieldValue()
    demand_qty: FieldValue = FieldValue()
    demand_unit_price_tl_per_unit: FieldValue = FieldValue()
    invoice_total_with_vat_tl: FieldValue = FieldValue()
    raw_breakdown: Optional[RawBreakdown] = None
    meta: Optional[InvoiceMeta] = None
    
    # Tip-5/7: Ek kalemler (reaktif, mahsuplaşma, etc.)
    extra_items: list[ExtraItem] = []
    # Tip-6: Multi sayaç desteği
    meters: list[MeterReading] = []  # Birden fazla sayaç varsa
    is_multi_meter: bool = False  # Tip-6 fatura mı?
    # Tip-7: Mahsuplaşma/İade
    adjustments: list[AdjustmentItem] = []  # Mahsup kalemleri
    has_adjustments: bool = False  # Tip-7 fatura mı?
    # Kalem bazlı extraction (cross-check için)
    line_items: list[LineItem] = []  # Enerji satırları (Yüksek/Düşük kademe, T1/T2/T3, etc.)

class OfferParams(BaseModel):
    """Teklif hesaplama parametreleri"""
    weighted_ptf_tl_per_mwh: Optional[float] = None  # None = DB'den otomatik çek
    yekdem_tl_per_mwh: Optional[float] = None  # None = DB'den otomatik çek
    agreement_multiplier: float = 1.01
    
    # Override flag: True ise kullanıcı değerleri kullan, False ise DB'den çek
    use_reference_prices: bool = True  # Default: DB'den çek
    
    # KDV oranı (default %20, tarımsal sulama için %10)
    vat_rate: float = 0.20  # 0.20 = %20, 0.10 = %10
    
    # UI Switches
    include_yekdem_in_offer: bool = False  # YEKDEM'i teklife dahil et? (default: hayır)
    extra_items_apply_to_offer: bool = False  # Ek kalemleri teklife dahil et?
    use_offer_distribution: bool = False  # Dağıtımı farklı hesapla?
    offer_distribution_unit_price_tl_per_kwh: Optional[float] = None  # Teklif dağıtım birim fiyatı

class CalculationResult(BaseModel):
    # Mevcut fatura
    current_energy_tl: float
    current_distribution_tl: float
    current_demand_tl: float
    current_btv_tl: float
    current_vat_matrah_tl: float
    current_vat_tl: float
    current_total_with_vat_tl: float
    
    # Ek kalemler (Tip-5/7: reaktif, mahsuplaşma, etc.)
    current_extra_items_tl: float = 0  # Mevcut faturadaki ek kalemler toplamı
    
    # Birim fiyatlar (UI için)
    current_energy_unit_tl_per_kwh: float = 0
    current_distribution_unit_tl_per_kwh: float = 0
    
    # Teklif fatura
    offer_ptf_tl: float
    offer_yekdem_tl: float
    offer_energy_tl: float
    offer_distribution_tl: float
    offer_demand_tl: float
    offer_btv_tl: float
    offer_vat_matrah_tl: float
    offer_vat_tl: float
    offer_total_with_vat_tl: float
    
    # Teklif birim fiyatlar (UI için)
    offer_energy_unit_tl_per_kwh: float = 0
    offer_distribution_unit_tl_per_kwh: float = 0
    
    # Ek kalemler notu (teklif kapsamı dışı)
    offer_extra_items_tl: float = 0  # Genelde 0 (teklif kapsamı dışı)
    extra_items_note: str = ""  # "Reaktif ve mahsuplaşma kalemleri teklif kapsamı dışındadır"
    
    # Fark ve tasarruf
    difference_excl_vat_tl: float
    difference_incl_vat_tl: float
    savings_ratio: float
    unit_price_savings_ratio: float
    
    # kWh başı tasarruf (satış için)
    current_total_tl_per_kwh: float = 0  # Mevcut toplam / kWh
    offer_total_tl_per_kwh: float = 0  # Teklif toplam / kWh
    saving_tl_per_kwh: float = 0  # kWh başı tasarruf
    
    # Yıllık projeksiyon (satış için)
    annual_saving_tl: float = 0  # Yıllık tahmini tasarruf (12x)
    
    # Meta: Hesaplama parametreleri (UI için)
    meta_extra_items_apply_to_offer: bool = False
    meta_use_offer_distribution: bool = False
    meta_include_yekdem_in_offer: bool = False  # YEKDEM teklife dahil mi?
    meta_consumption_kwh: float = 0  # Tüketim (projeksiyon için)
    meta_vat_rate: float = 0.20  # KDV oranı (0.20 = %20, 0.10 = %10)
    
    # Dağıtım kaynağı bilgisi (debug/UI için)
    meta_distribution_source: str = "unknown"  # "epdk_tariff:sanayi/OG/çift_terim", "manual_override", "extracted_from_invoice", "not_found"
    meta_distribution_tariff_key: Optional[str] = None  # "sanayi/OG/çift_terim"
    meta_distribution_mismatch_warning: Optional[str] = None  # Faturadan okunan vs EPDK farkı
    
    # PTF/YEKDEM kaynağı bilgisi (debug/UI için)
    meta_pricing_source: str = "unknown"  # "reference" (DB'den), "override" (kullanıcıdan), "default" (fallback)
    meta_pricing_period: Optional[str] = None  # "2025-01"
    meta_ptf_tl_per_mwh: float = 0  # Kullanılan PTF değeri
    meta_yekdem_tl_per_mwh: float = 0  # Kullanılan YEKDEM değeri
    
    # Total mismatch bilgisi (Sprint 8.3)
    # invoice_total vs computed_total farkı > %5 veya > 50 TL ise flag
    meta_total_mismatch: bool = False  # INVOICE_TOTAL_MISMATCH flag
    meta_total_mismatch_info: Optional[dict] = None  # {invoice_total, computed_total, delta, ratio}


class Question(BaseModel):
    field_name: str
    why_needed: str
    example_answer_format: str

class SuggestedFix(BaseModel):
    """Türetilebilir alanlar için önerilen değer"""
    field_name: str
    suggested_value: float
    basis: str  # Nasıl türetildi (örn: "energy_total_tl / consumption_kwh")
    confidence: float  # Önerinin güvenilirliği (0-1)

class SanityCheck(BaseModel):
    """Hesap tutarlılığı kontrolü"""
    energy_est_tl: Optional[float] = None
    dist_est_tl: Optional[float] = None
    demand_est_tl: Optional[float] = None
    extra_items_est_tl: Optional[float] = None  # Ek kalemler toplamı (Tip-5/7)
    btv_est_tl: Optional[float] = None
    vat_est_tl: Optional[float] = None
    total_est_tl: Optional[float] = None
    invoice_total_with_vat_tl: Optional[float] = None
    delta_ratio: Optional[float] = None  # Fark oranı (%)


class LineItemAnalysis(BaseModel):
    """Satır bazlı analiz sonucu"""
    label: str = ""
    line_type: str = ""  # energy, distribution, demand, tax, etc.
    qty: float = 0
    unit: str = ""
    unit_price: Optional[float] = None
    measurement_unit: str = ""  # TL/kWh, TL/kW, TL/day, etc.
    amount_tl: Optional[float] = None
    calculated_amount: Optional[float] = None  # qty × unit_price
    delta_percent: Optional[float] = None  # Fark yüzdesi
    is_consistent: bool = True  # Satır tutarlı mı?


class EnergyDistributionAnalysis(BaseModel):
    """Enerji/Dağıtım ayrımı analizi - DEBUG için"""
    # Toplam tüketim
    total_kwh: float = 0
    
    # Enerji satırı analizi
    energy_line_qty: Optional[float] = None
    energy_line_unit_price: Optional[float] = None
    energy_line_amount: Optional[float] = None
    energy_line_consistent: bool = True
    
    # Dağıtım satırı analizi (kWh bazlı)
    distribution_kwh_line_qty: Optional[float] = None
    distribution_kwh_line_unit_price: Optional[float] = None
    distribution_kwh_line_amount: Optional[float] = None
    distribution_kwh_line_consistent: bool = True
    
    # Diğer dağıtım kalemleri (demand, hizmet, vb.)
    distribution_other_lines: list[LineItemAnalysis] = []
    
    # Hesaplanan ortalama birim fiyatlar
    computed_avg_unit_price_total: Optional[float] = None  # (energy + dist) / kwh
    computed_energy_unit_price_from_line: Optional[float] = None  # energy_amount / kwh
    
    # Extracted değerler (model çıktısı)
    extracted_energy_unit_price: Optional[float] = None
    extracted_distribution_unit_price: Optional[float] = None
    
    # 3'lü kontrol bayrakları
    flag_a_line_consistency: str = "unknown"  # pass, fail, unknown
    flag_b_energy_includes_distribution: str = "unknown"  # pass, suspicious, fail, unknown
    flag_c_addition_pattern: str = "unknown"  # pass, fail, unknown
    
    # Sonuç
    overall_status: str = "unknown"  # clean, suspicious, error
    status_message: str = ""


class ValidationResult(BaseModel):
    is_ready_for_pricing: bool
    missing_fields: list[str]
    questions: list[Question]
    errors: list[dict]
    warnings: list[dict]
    suggested_fixes: list[SuggestedFix] = []
    sanity_check: Optional[SanityCheck] = None
    
    # YENİ: Enerji/Dağıtım ayrımı analizi (debug için)
    energy_distribution_analysis: Optional[EnergyDistributionAnalysis] = None
    
    # Dağıtım tarife bayrakları
    distribution_tariff_meta_missing: bool = False  # Tarife bilgisi (grup/gerilim/terim) eksik
    distribution_tariff_lookup_failed: bool = False  # EPDK tablosunda bulunamadı
    distribution_computed_from_tariff: bool = False  # EPDK tarifesinden hesaplandı
    distribution_line_mismatch: bool = False  # Faturadan okunan vs EPDK farkı var
    distribution_tariff_key: Optional[str] = None  # "sanayi/OG/çift_terim"
