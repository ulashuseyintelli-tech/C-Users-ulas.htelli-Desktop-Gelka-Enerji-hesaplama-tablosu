# Design: Fatura Analiz ve Teklif Hesaplama Sistemi

## Mimari Genel Bakış

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              MOBILE APP                                      │
│                         (React Native / Expo)                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │  Invoice    │  │  Extraction │  │   Params    │  │   Result    │         │
│  │  Uploader   │  │    Card     │  │   Input     │  │    Card     │         │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘         │
│         │                                                                    │
│         ▼                                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │                    Image Processor                               │        │
│  │              (Resize + Compress + JPEG Convert)                  │        │
│  └─────────────────────────────────────────────────────────────────┘        │
│         │                                                                    │
│         ▼                                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │                      API Client                                  │        │
│  │              (axios + error handling + types)                    │        │
│  └─────────────────────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ HTTP/REST
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              BACKEND API                                     │
│                           (FastAPI + Python)                                 │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │                         Endpoints                                │        │
│  │  POST /analyze-invoice  POST /calculate-offer  POST /full-process│        │
│  │  GET /health            DELETE /cache                            │        │
│  └─────────────────────────────────────────────────────────────────┘        │
│         │                         │                                          │
│         ▼                         ▼                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │  Extractor  │  │  Validator  │  │ Calculator  │  │   Models    │         │
│  │  (OpenAI)   │  │  (Rules)    │  │  (Formulas) │  │  (Pydantic) │         │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘         │
│         │                                                                    │
│         ▼                                                                    │
│  ┌─────────────────────────────────────────────────────────────────┐        │
│  │                    Extraction Cache                              │        │
│  │                  (SHA-256 hash based)                            │        │
│  └─────────────────────────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ API Call
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           OpenAI Vision API                                  │
│                    (gpt-4o-2024-08-06 + Structured Outputs)                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Bileşenler ve Arayüzler

### Backend Bileşenleri

#### 1. Models (models.py)
Pydantic modelleri ile tip güvenliği ve validasyon.

```python
class FieldValue:
    value: Optional[float]      # Çıkarılan değer
    confidence: float           # 0-1 arası güvenilirlik
    evidence: str               # Faturadan alınan kanıt metni
    page: int                   # Sayfa numarası

class InvoiceExtraction:
    vendor: str                 # enerjisa, ck_bogazici, ekvator, yelden, unknown
    invoice_period: str         # YYYY-MM formatı
    consumption_kwh: FieldValue
    current_active_unit_price_tl_per_kwh: FieldValue
    distribution_unit_price_tl_per_kwh: FieldValue
    demand_qty: FieldValue
    demand_unit_price_tl_per_unit: FieldValue
    invoice_total_with_vat_tl: FieldValue
    raw_breakdown: Optional[RawBreakdown]

class ValidationResult:
    is_ready_for_pricing: bool
    missing_fields: list[str]
    questions: list[Question]
    errors: list[dict]
    warnings: list[dict]

class CalculationResult:
    # Mevcut fatura kalemleri
    current_energy_tl, current_distribution_tl, current_demand_tl
    current_btv_tl, current_vat_matrah_tl, current_vat_tl, current_total_with_vat_tl
    # Teklif fatura kalemleri
    offer_ptf_tl, offer_yekdem_tl, offer_energy_tl
    offer_distribution_tl, offer_demand_tl, offer_btv_tl
    offer_vat_matrah_tl, offer_vat_tl, offer_total_with_vat_tl
    # Tasarruf
    difference_excl_vat_tl, difference_incl_vat_tl
    savings_ratio, unit_price_savings_ratio
```

#### 2. Extractor (extractor.py)
OpenAI Vision API ile fatura görselinden veri çıkarma.

```python
def extract_invoice_data(image_bytes: bytes, mime_type: str) -> InvoiceExtraction:
    """
    1. Hash hesapla (SHA-256)
    2. Cache kontrol et
    3. Cache miss ise OpenAI API çağır
    4. Structured Outputs ile JSON parse
    5. Cache'e kaydet
    6. InvoiceExtraction döndür
    """
```

**Özellikler:**
- Structured Outputs: %100 şemaya uygun JSON garantisi
- Hash-based cache: Aynı görsel tekrar analiz edilmez
- PII maskeleme: Log'larda hassas veriler gizlenir

#### 3. Validator (validator.py)
Extraction sonucunu doğrulama ve eksik alan tespiti.

```python
def validate_extraction(extraction: InvoiceExtraction) -> ValidationResult:
    """
    Validation Rules:
    - 4.1: consumption_kwh null/zero kontrolü
    - 4.2: current_active_unit_price 0.1-30 TL/kWh aralık kontrolü
    - 4.3: confidence < 0.6 uyarı
    - 4.4: demand_qty varsa demand_unit_price zorunlu
    - 4.5: invoice_total_with_vat karşılaştırma
    - 4.6: ±5% tolerans kontrolü
    - 4.7: is_ready_for_pricing belirleme
    """
```

**Türetme Fonksiyonları:**
- `_try_derive_unit_price()`: energy_total_tl / consumption_kwh
- `_try_derive_distribution_price()`: distribution_total_tl / consumption_kwh

#### 4. Calculator (calculator.py)
Mevcut fatura ve teklif hesaplama.

```python
def calculate_offer(extraction: InvoiceExtraction, params: OfferParams) -> CalculationResult:
    """
    Mevcut Fatura:
    - current_energy_tl = kwh × current_unit_price
    - current_distribution_tl = kwh × dist_unit_price
    - current_demand_tl = demand_qty × demand_unit_price
    - current_btv_tl = current_energy_tl × 0.01
    - current_vat_matrah_tl = energy + distribution + demand + btv
    - current_vat_tl = matrah × 0.20
    - current_total_with_vat_tl = matrah + vat

    Teklif Fatura:
    - offer_ptf_tl = (ptf_tl_per_mwh / 1000) × kwh
    - offer_yekdem_tl = (yekdem_tl_per_mwh / 1000) × kwh
    - offer_energy_tl = (ptf + yekdem) × agreement_multiplier
    - ... (aynı formüller)

    Tasarruf:
    - difference_excl_vat_tl = current_matrah - offer_matrah
    - difference_incl_vat_tl = current_total - offer_total
    - savings_ratio = difference_incl_vat / current_total
    """
```

### Mobile Bileşenleri

#### 1. API Client (client.ts)
Backend ile iletişim ve hata yönetimi.

```typescript
class ApiError extends Error {
  code: string;           // file_too_large, unsupported_file_type, etc.
  statusCode: number;
  detail: ApiErrorDetail;
  
  getUserFriendlyMessage(): string;  // Türkçe kullanıcı dostu mesaj
}

async function analyzeInvoice(fileUri, fileName, mimeType): Promise<{extraction, validation}>
async function fullProcess(fileUri, fileName, mimeType, params?): Promise<FullProcessResponse>
```

#### 2. Image Processor (imageProcessor.ts)
Görsel optimizasyonu.

```typescript
async function processImage(uri: string, options?: ProcessingOptions): Promise<ProcessedImage>
// - maxDimension: 2048px (varsayılan)
// - quality: 0.85 (JPEG sıkıştırma)
// - format: JPEG
```

#### 3. UI Bileşenleri

| Bileşen | Sorumluluk |
|---------|------------|
| InvoiceUploader | Galeri/Kamera/PDF seçimi, görsel işleme |
| ExtractionCard | Çıkarılan verileri gösterme, confidence badge'leri |
| ParamsInput | PTF, YEKDEM, çarpan parametreleri |
| ResultCard | Mevcut vs Teklif karşılaştırma, tasarruf oranı |
| MissingFieldsCard | Eksik alan soruları, manuel giriş |
| ErrorCard | Hata mesajları, retry butonu |
| SkeletonLoader | Loading durumu gösterimi |

---

## Veri Modelleri

### API Request/Response

```
POST /analyze-invoice
Request: multipart/form-data (file)
Response: {
  extraction: InvoiceExtraction,
  validation: ValidationResult
}

POST /calculate-offer
Request: {
  extraction: InvoiceExtraction,
  params?: OfferParams
}
Response: CalculationResult

POST /full-process
Request: multipart/form-data (file) + query params
Response: {
  extraction: InvoiceExtraction,
  validation: ValidationResult,
  calculation: CalculationResult | null
}
```

### Veri Akışı

```
1. Kullanıcı fatura yükler
   ↓
2. Mobile: Görsel işleme (resize + compress)
   ↓
3. API: /analyze-invoice çağrısı
   ↓
4. Backend: Cache kontrol → OpenAI Vision API → Extraction
   ↓
5. Backend: Validation (eksik alan, hata, uyarı tespiti)
   ↓
6. Mobile: ExtractionCard gösterimi
   ↓
7. Kullanıcı: Eksik alanları doldurur (varsa)
   ↓
8. Mobile: /calculate-offer çağrısı
   ↓
9. Backend: Mevcut + Teklif hesaplama
   ↓
10. Mobile: ResultCard gösterimi (tasarruf oranı)
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Calculator Determinism
*For any* valid InvoiceExtraction and OfferParams, calling `calculate_offer()` multiple times with the same inputs SHALL produce identical CalculationResult values.
**Validates: Requirements 5.2, 5.3**

### Property 2: Calculator Non-Negative Outputs
*For any* valid InvoiceExtraction with non-negative input values, all calculated monetary values in CalculationResult SHALL be non-negative.
**Validates: Requirements 5.2, 5.3, 5.4**

### Property 3: Savings Ratio Bounds
*For any* CalculationResult where current_total_with_vat_tl > 0, the savings_ratio SHALL be in the range [-1, 1] (can be negative if offer is more expensive).
**Validates: Requirements 5.4**

### Property 4: VAT Calculation Consistency
*For any* CalculationResult, the VAT values SHALL equal exactly 20% of their respective matrah values (current_vat_tl = current_vat_matrah_tl × 0.20).
**Validates: Requirements 5.2, 5.3**

### Property 5: BTV Calculation Consistency
*For any* CalculationResult, the BTV values SHALL equal exactly 1% of their respective energy values (current_btv_tl = current_energy_tl × 0.01).
**Validates: Requirements 5.2, 5.3**

### Property 6: Validator Missing Field Detection
*For any* InvoiceExtraction where consumption_kwh.value is null or ≤ 0, the ValidationResult SHALL include "consumption_kwh" in missing_fields.
**Validates: Requirements 4.1**

### Property 7: Validator Unit Price Range Check
*For any* InvoiceExtraction where current_active_unit_price_tl_per_kwh.value is outside [0.1, 30.0] TL/kWh range, the ValidationResult SHALL include an error for that field.
**Validates: Requirements 4.2**

### Property 8: Validator Demand Consistency
*For any* InvoiceExtraction where demand_qty.value > 0 and demand_unit_price_tl_per_unit.value is null, the ValidationResult SHALL include "demand_unit_price_tl_per_unit" in missing_fields.
**Validates: Requirements 4.4**

### Property 9: Validator Ready State
*For any* InvoiceExtraction, is_ready_for_pricing SHALL be true if and only if missing_fields is empty AND errors is empty.
**Validates: Requirements 4.7**

### Property 10: Total Calculation Consistency
*For any* CalculationResult, current_total_with_vat_tl SHALL equal current_vat_matrah_tl + current_vat_tl (within floating point tolerance).
**Validates: Requirements 5.2**

### Property 11: Matrah Calculation Consistency
*For any* CalculationResult, current_vat_matrah_tl SHALL equal current_energy_tl + current_distribution_tl + current_demand_tl + current_btv_tl.
**Validates: Requirements 5.2**

### Property 12: File Validation - Size Limit
*For any* uploaded file larger than 10MB, the API SHALL return a 400 error with code "file_too_large".
**Validates: Requirements 1.2, 1.4**

### Property 13: File Validation - MIME Type
*For any* uploaded file with unsupported MIME type, the API SHALL return a 400 error with code "unsupported_file_type".
**Validates: Requirements 1.1, 1.4**

### Property 14: File Validation - Empty File
*For any* uploaded file with 0 bytes, the API SHALL return a 400 error with code "empty_file".
**Validates: Requirements 1.2, 1.4**

### Property 15: Cache Idempotence
*For any* image, calling extract_invoice_data() twice with the same image bytes SHALL return equivalent InvoiceExtraction results (cache hit).
**Validates: Requirements 2.7**

### Property 16: Total Mismatch Detection - S2 Threshold
*For any* invoice_total and computed_total where (ratio >= 5% OR delta >= 50 TL), check_total_mismatch() SHALL return has_mismatch=True.
**Validates: Requirements 4.6**

### Property 17: Total Mismatch Severity Escalation - S1
*For any* mismatch where (ratio >= 20% AND delta >= 50) OR delta >= 500, the severity SHALL be "S1".
**Validates: Requirements 4.6**

### Property 18: Total Mismatch Small Invoice Protection
*For any* mismatch where ratio >= 20% but delta < 50 TL, the severity SHALL remain "S2" (not escalate to S1).
**Validates: Requirements 4.6**

### Property 19: OCR Locale Suspect Detection
*For any* mismatch with extraction_confidence < 0.7, the suspect_reason SHALL be "OCR_LOCALE_SUSPECT".
**Validates: Requirements 4.6**

### Property 20: Calculator Contract - Invoice Total as Source of Truth
*For any* InvoiceExtraction with invoice_total_with_vat_tl, the CalculationResult.current_total_with_vat_tl SHALL equal the invoice_total (not computed).
**Validates: Requirements 5.2**

### Property 21: YEKDEM Inclusion Rule
*For any* InvoiceExtraction where yek_amount > 0, the offer calculation SHALL include YEKDEM. Where yek_amount = 0 or null, YEKDEM SHALL be excluded.
**Validates: Requirements 5.3, 6.4**

### Property 22: Actionability Determinism
*For any* (flag_code, mismatch_info, extraction_confidence) tuple, calling generate_action_hint() multiple times SHALL produce identical ActionHint results.
**Validates: Requirements 4.8**

### Property 23: Action Class Coverage
*For any* INVOICE_TOTAL_MISMATCH with has_mismatch=True, generate_action_hint() SHALL return a non-null ActionHint with one of: VERIFY_OCR, VERIFY_INVOICE_LOGIC, or ACCEPT_ROUNDING_TOLERANCE.
**Validates: Requirements 4.8**

### Property 24: Recommended Checks Ordering
*For any* ActionHint, the recommended_checks list SHALL be ordered by probability (most likely cause first) and contain at most 5 items.
**Validates: Requirements 4.8**

### Property 25: Rounding Tolerance Guard
*For any* mismatch where delta < 10 TL AND ratio < 0.5%, the action_class SHALL be ACCEPT_ROUNDING_TOLERANCE. If either condition fails, it SHALL NOT be ACCEPT_ROUNDING_TOLERANCE.
**Validates: Requirements 4.8**

### Property 26: Drift Detection Triple Guard
*For any* drift alert, it SHALL only trigger when ALL conditions are met: curr_total >= 20 AND abs(curr_count - prev_count) >= 5 AND (prev_rate == 0 OR curr_rate >= 2 * prev_rate).
**Validates: Requirements 4.9**

### Property 27: Drift Detection Zero Rate Handling
*For any* drift check where prev_rate == 0, the rate multiplier guard SHALL be skipped, and alert triggers only if count guards pass AND curr_count >= min_abs_delta.
**Validates: Requirements 4.9**

### Property 28: Top Offenders Rate Calculation
*For any* provider in top offenders list, the mismatch_rate SHALL equal mismatch_count / total_count (not raw count).
**Validates: Requirements 4.9**

### Property 29: Top Offenders Minimum Volume Guard
*For any* provider in top offenders by rate list, total_count SHALL be >= 20. Providers with fewer invoices are excluded from rate-based ranking.
**Validates: Requirements 4.9**

### Property 30: Histogram Bucket Coverage
*For any* mismatch ratio, it SHALL fall into exactly one histogram bucket: [0-2%, 2-5%, 5-10%, 10-20%, 20%+].
**Validates: Requirements 4.9**

### Property 31: Mismatch Ratio Denominator
*For any* mismatch ratio calculation, the denominator SHALL be invoice_total (source of truth), with epsilon=0.01 for zero protection.
**Validates: Requirements 4.9**

### Property 32: System Health Report Completeness
*For any* SystemHealthReport, it SHALL contain: period_stats, drift_alerts, top_offenders_by_rate, top_offenders_by_count, and histogram data.
**Validates: Requirements 4.9**

### Property 33: Feedback Action Enum Validity
*For any* feedback submission, action_taken SHALL be one of: VERIFIED_OCR, VERIFIED_LOGIC, ACCEPTED_ROUNDING, ESCALATED, or NO_ACTION_REQUIRED. Invalid values SHALL return 400 with code "invalid_feedback_action".
**Validates: Requirements 4.10**

### Property 34: Feedback Timestamp Consistency
*For any* feedback submission, feedback_at SHALL be set to the server timestamp at submission time, not client-provided. Client-provided timestamps SHALL be ignored.
**Validates: Requirements 4.10**

### Property 35: Feedback Upsert Semantics
*For any* incident, submitting feedback multiple times SHALL overwrite the previous feedback. Each submission SHALL update both `feedback_at` and `updated_at` timestamps, even if payload is identical.
**Validates: Requirements 4.10**

### Property 36: Hint Accuracy Calculation
*For any* feedback stats calculation, hint_accuracy_rate SHALL equal count(was_hint_correct=true) / count(all_feedback). When total_feedback=0, rate SHALL be 0.0.
**Validates: Requirements 4.10**

### Property 37: Feedback Stats Null Safety
*For any* feedback stats request with zero feedback records, all rates SHALL return 0.0 (not null or error). feedback_coverage with zero resolved incidents SHALL return 0.0.
**Validates: Requirements 4.10**

### Property 38: Feedback State Guard
*For any* feedback submission on an incident with status != RESOLVED, the API SHALL return 400 error with code "incident_not_resolved".
**Validates: Requirements 4.10**

### Property 39: Feedback User Required
*For any* feedback submission, feedback_by SHALL be populated from auth context (not request body). Missing auth SHALL result in 401/403, not 400.
**Validates: Requirements 4.10**

### Property 40: Feedback Validation Invariants
*For any* feedback submission: (1) was_hint_correct SHALL NOT be null, (2) resolution_time_seconds SHALL be >= 0, (3) actual_root_cause SHALL be <= 200 characters. Violations SHALL return 400 with code "invalid_feedback_data".
**Validates: Requirements 4.10**

### Property 41: Feedback Coverage Calculation
*For any* feedback stats, feedback_coverage SHALL equal count(resolved_incidents_with_feedback) / count(all_resolved_incidents). When resolved_total=0, coverage SHALL be 0.0.
**Validates: Requirements 4.10**

---

## Sprint 8.8: Prod Readiness Properties

### Property 42: Config Single Source of Truth
*For all* threshold values used in the system, they SHALL be defined in `config.py` and imported via `from .config import THRESHOLDS`. Hard-coded threshold values outside config.py SHALL NOT exist (enforced by grep gate).
**Validates: Requirements 9.4 (Operational Readiness)**

### Property 43: Config Validation Invariants
*At startup*, the system SHALL validate 8 config invariants:
- I1: SEVERE_RATIO >= RATIO
- I2: SEVERE_ABSOLUTE >= ABSOLUTE
- I3: ROUNDING_RATIO < RATIO
- I4: MIN_UNIT_PRICE < MAX_UNIT_PRICE
- I5: MIN_DIST_PRICE < MAX_DIST_PRICE
- I6: HARD_STOP_DELTA >= SEVERE_RATIO * 100
- I7: All thresholds > 0
- I8: 0 < LOW_CONFIDENCE < 1

If any invariant fails, the application SHALL NOT start and SHALL log a CRITICAL error.
**Validates: Requirements 9.4 (Operational Readiness)**

### Property 44: Health Ready Endpoint Contract
*For any* GET /health/ready request:
- Response SHALL include checks for: config, database, openai_api, queue
- If all checks pass, response SHALL be 200 with status="ready"
- If any critical check fails, response SHALL be 503 with status="not_ready" and failing_checks array
- Database latency > 500ms SHALL be treated as error
**Validates: Requirements 9.4 (Operational Readiness)**

### Property 45: Run Summary Schema
*For any* run summary generation:
- Output SHALL include: generated_at, period, counts, rates, latency, errors, queue
- counts SHALL include: total_invoices, incident_count, s1_count, s2_count, ocr_suspect_count
- rates SHALL include: mismatch_rate, s1_rate, feedback_coverage, hint_accuracy_rate
- latency.pipeline_total_ms SHALL include p50, p95, p99 percentiles when samples available
**Validates: Requirements 9.4 (Operational Readiness)**

### Property 46: E2E Smoke Test Coverage
*For any* deployment, E2E smoke tests SHALL verify:
- Happy path: No incident created, quality grade OK
- S2 Mismatch: Incident created with severity S2, action_class VERIFY_INVOICE_LOGIC
- S1 + OCR Suspect: Incident created with severity S1, action_class VERIFY_OCR, suspect_reason OCR_LOCALE_SUSPECT
- Full pipeline: extraction → validation → calculation → incident → health → feedback → stats
**Validates: Requirements 9.4 (Operational Readiness)**

---

## Error Handling

### Backend Hata Kodları

| Kod | HTTP Status | Açıklama |
|-----|-------------|----------|
| `file_too_large` | 400 | Dosya > 10MB |
| `unsupported_file_type` | 400 | Desteklenmeyen MIME type |
| `empty_file` | 400 | Boş dosya |
| `pdf_conversion_error` | 500 | PDF → görsel dönüşüm hatası |
| `analysis_error` | 500 | OpenAI API hatası |

### Mobile Hata Yönetimi

```typescript
class ApiError {
  getUserFriendlyMessage(): string {
    switch (this.code) {
      case 'file_too_large': return 'Dosya çok büyük. Maksimum 10 MB.';
      case 'unsupported_file_type': return 'Desteklenmeyen format. JPG, PNG veya PDF yükleyin.';
      case 'timeout': return 'İşlem zaman aşımına uğradı. Tekrar deneyin.';
      case 'network_error': return 'Sunucuya bağlanılamadı. İnternet bağlantınızı kontrol edin.';
      // ...
    }
  }
}
```

---

## Testing Strategy

### Unit Tests
- Specific examples ve edge cases için
- Her modül için ayrı test dosyası
- pytest kullanımı

### Property-Based Tests
- Hypothesis kütüphanesi ile
- Her property için minimum 100 iterasyon
- Rastgele input üretimi ile kapsamlı test

### Test Dosyaları

| Dosya | Kapsam |
|-------|--------|
| `test_calculator_properties.py` | Property 1-5, 10-11 |
| `test_validator_properties.py` | Property 6-9 |
| `test_api_properties.py` | Property 12-15 |
| `test_integration.py` | End-to-end senaryolar |

### Test Konfigürasyonu

```python
from hypothesis import given, settings, strategies as st

@settings(max_examples=100)
@given(
    kwh=st.floats(min_value=0, max_value=1_000_000),
    unit_price=st.floats(min_value=0.1, max_value=30.0),
    # ...
)
def test_calculator_determinism(kwh, unit_price, ...):
    """Property 1: Calculator Determinism"""
    # ...
```
