# Implementation Plan: Fatura Analiz ve Teklif Hesaplama Sistemi

## Overview

Bu plan, elektrik faturalarını analiz eden ve alternatif enerji teklifi hesaplayan full-stack sistemin implementasyonunu kapsar. Genişletilmiş tedarikçi desteği, 2 katmanlı parser mimarisi ve yeni alan eşleştirme sözlüğü içerir.

---

## Tasks

- [x] 1. Genişletilmiş Veri Modelleri
  - [x] 1.1 StringFieldValue modeli eklendi (ETTN, fatura no, vb.)
  - [x] 1.2 ConsumerInfo modeli eklendi (tüketici bilgileri)
  - [x] 1.3 ConsumptionDetails modeli eklendi (çok zamanlı + reaktif)
  - [x] 1.4 ChargeBreakdown modeli eklendi (kalem bazlı tutarlar)
  - [x] 1.5 UnitPrices modeli eklendi (birim fiyatlar)
  - [x] 1.6 TariffInfo modeli eklendi (tarife bilgileri)
  - _Requirements: 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

- [x] 2. Genişletilmiş Tedarikçi Desteği
  - [x] 2.1 Yeni tedarikçiler eklendi (Uludağ, Osmangazi, Kolen, Aksa, Dicle, Gediz, Trakya, Zorlu, Limak)
  - [x] 2.2 Dağıtım şirketleri eklendi (UEDAŞ, OEDAŞ, AKEDAŞ, DEDAŞ, GEDAŞ, vb.)
  - [x] 2.3 extraction_v3.txt prompt dosyası oluşturuldu
  - _Requirements: 3.1-3.13_

- [x] 3. OpenAI Schema Güncellemesi
  - [x] 3.1 EXTRACTION_SCHEMA v3 güncellendi
  - [x] 3.2 Yeni alanlar eklendi (ettn, invoice_no, consumer, charges, vb.)
  - [x] 3.3 stringFieldValue tanımı eklendi
  - _Requirements: 2.1, 2.2_

- [x] 10. Webhook Sistemi (Stage 5)
  - [x] 10.1 Webhook servisi oluşturuldu (webhook.py)
    - Event-driven webhook delivery
    - HMAC-SHA256 imzalama
    - Retry mekanizması
    - _Requirements: Stage 5 - Webhooks_
  - [x] 10.2 Webhook manager oluşturuldu (webhook_manager.py)
    - In-memory ve DB-backed modlar
    - Tenant bazlı izolasyon
  - [x] 10.3 Database tabloları (003 migration)
    - webhook_configs tablosu
    - webhook_deliveries tablosu
    - audit_logs tablosu
  - [x] 10.4 API Endpoint'leri (main.py)
    - POST /webhooks - Webhook kaydet
    - GET /webhooks - Webhook listele
    - DELETE /webhooks/{id} - Webhook sil
    - PUT /webhooks/{id}/toggle - Webhook aktif/pasif
    - GET /audit-logs - Audit logları listele
    - GET /audit-logs/stats - Audit istatistikleri
  - [x] 10.5 Offer status webhook entegrasyonu
    - update_offer_status endpoint'inde webhook trigger
  - [x] 10.6 Webhook Unit Testleri
    - [x] 10.6.1 HMAC imza oluşturma/doğrulama testi
    - [x] 10.6.2 Webhook config CRUD testleri
    - [x] 10.6.3 Event filtreleme testi
    - [x] 10.6.4 Delivery kayıt testi
    - _Requirements: Stage 5 - Webhooks_
  - [x] 10.7 Webhook Property Testleri
    - **Property 19: HMAC İmza Round-Trip** ✓
      - *For any* payload ve secret, generate_signature sonucu verify_signature ile doğrulanabilmeli
    - **Property 20: Event Filtreleme Tutarlılığı** ✓
      - *For any* webhook config ve event listesi, sadece kayıtlı event'ler için config dönmeli
    - **Validates: Stage 5 - Webhooks**

- [x] 11. Sprint 8.3 - Calculator Contract + TOTAL_MISMATCH Flag
  - [x] 11.1 Calculator kontratı düzeltildi
    - current_total_with_vat_tl = invoice_total (SOURCE OF TRUTH)
    - offer_* tamamen hesaplanır
    - YEKDEM otomatik tespit: yek_amount > 0 ise dahil
    - _Requirements: 5.2, 5.3_
  - [x] 11.2 INVOICE_TOTAL_MISMATCH flag eklendi
    - S2 severity, deduction=25, priority=35
    - Threshold: ratio >= 5% OR delta >= 50 TL
    - QUALITY_FLAGS, FLAG_PRIORITY, ACTION_MAP güncellendi
    - HintCode.INVOICE_TOTAL_MISMATCH_REVIEW eklendi
    - _Requirements: 4.6_
  - [x] 11.3 check_total_mismatch() fonksiyonu
    - TotalMismatchInfo dataclass
    - Ratio ve absolute threshold kontrolü
    - _Requirements: 4.6_
  - [x] 11.4 Unit testleri (test_total_mismatch.py)
    - 16 test, tümü geçiyor
    - _Requirements: 4.6, 5.2_

- [x] 12. Sprint 8.4 - Severity Escalation + Golden Tests
  - [x] 12.1 S1 Escalation kuralı
    - (ratio >= 20% AND delta >= 50) OR delta >= 500 → S1
    - Küçük fatura koruması: yüksek ratio ama delta < 50 → S2 kalır
    - TOTAL_MISMATCH_SEVERE_RATIO = 0.20
    - TOTAL_MISMATCH_SEVERE_ABSOLUTE = 500.0
    - _Requirements: 4.6_
  - [x] 12.2 OCR_LOCALE_SUSPECT tag
    - extraction_confidence < 0.7 + mismatch → suspect_reason
    - Ayrı flag değil, metadata olarak eklenir
    - TotalMismatchInfo.suspect_reason alanı
    - _Requirements: 4.6_
  - [x] 12.3 add_flag() severity_override desteği
    - calculate_quality_score'da severity mismatch_info'dan alınır
    - suspect_reason flag_details'e eklenir
    - _Requirements: 4.6_
  - [x] 12.4 Golden scenarios (5 test)
    - perfect_match → no flag
    - rounding_diff → no flag (delta=2 TL)
    - real_mismatch → S2 (delta=100, ratio=10%)
    - severe_mismatch → S1 (delta=600, ratio=25%)
    - ocr_suspect → OCR_LOCALE_SUSPECT tag
    - _Requirements: 4.6_
  - [x] 12.5 Full test suite: 423 tests passing

- [d] 4. Validator Güncellemesi — **Deferred: Prod Hardening (veri kalitesi / yanlış pozitif riski — ETTN format, çok zamanlı tutarlılık, reaktif ceza)**
  - [ ] 4.1 Yeni alanlar için validasyon kuralları
    - ETTN format kontrolü (UUID)
    - Çok zamanlı tutarlılık (T1+T2+T3 = Toplam)
    - Reaktif ceza kontrolü (%33 endüktif, %20 kapasitif)
    - _Requirements: 4.1, 4.5, 4.7_

  - [ ] 4.2 Tedarikçi bazlı tolerans güncelleme
    - Yeni tedarikçiler için tolerans değerleri
    - _Requirements: 4.6_

- [d] 5. Checkpoint - Backend Güncellemeleri — **Deferred (depends on Task 4)**

- [d] 6. Property Tests Güncelleme — **Deferred: Prod Hardening (depends on Task 4)**
  - [ ] 6.1 Yeni modeller için property tests
    - **Property 16: Çok Zamanlı Tutarlılık**
    - **Property 17: Reaktif Ceza Kontrolü**
    - **Property 18: ETTN Format Validasyonu**
    - **Validates: Requirements 4.5, 4.7, 2.3**

  - [ ] 6.2 Mevcut testlerin güncellenmesi
    - Yeni model yapısına uyum
    - _Requirements: 5.2, 5.3_

- [d] 7. API Endpoint Güncellemeleri — **Deferred: Prod Hardening (müşteri yüzünde tedarikçi listesi + response model)**
  - [ ] 7.1 GET /suppliers endpoint
    - Desteklenen tedarikçi listesi
    - _Requirements: 7.6_

  - [ ] 7.2 Response model güncellemeleri
    - Yeni alanların API response'a eklenmesi
    - _Requirements: 6.2, 6.4_

- [d] 8. Fatura Test Senaryoları — **Deferred: Prod Hardening (tedarikçi bazlı regression koruması)**
  - [ ] 8.1 Enerjisa fatura testi
  - [ ] 8.2 CK Boğaziçi fatura testi
  - [ ] 8.3 Uludağ fatura testi
  - [ ] 8.4 Çok zamanlı fatura testi (T1-T2-T3)
  - [ ] 8.5 Reaktif cezalı fatura testi
  - _Requirements: 2.1, 2.2, 6.1-6.5_

- [d] 9. Final Checkpoint — **Deferred (depends on Task 4-8)**

- [x] 13. Sprint 8.5 - Actionability
  - [x] 13.1 ActionHint data model
    - ActionClass enum (VERIFY_OCR, VERIFY_INVOICE_LOGIC, ACCEPT_ROUNDING_TOLERANCE)
    - PrimarySuspect enum (OCR_LOCALE_SUSPECT, INVOICE_LOGIC, ROUNDING)
    - ActionHint dataclass (action_class, primary_suspect, recommended_checks, confidence_note)
    - to_dict() metodu
    - _Requirements: 4.8_
  - [x] 13.2 generate_action_hint() fonksiyonu
    - incident_service.py içinde
    - Decision tree implementasyonu
    - OCR_LOCALE_SUSPECT → VERIFY_OCR
    - delta < 10 AND ratio < 0.005 → ACCEPT_ROUNDING_TOLERANCE
    - else → VERIFY_INVOICE_LOGIC
    - _Requirements: 4.8_
  - [x] 13.3 Sabit check listeleri (tam determinism)
    - CHECKS_VERIFY_OCR: Ondalık → Binlik → TL/kuruş → kWh×PTF
    - CHECKS_VERIFY_INVOICE_LOGIC: Mahsup → KDV → Override → Kalem eşleme → Dönem
    - CHECKS_ACCEPT_ROUNDING: Yuvarlama → Kuruş hassasiyeti
    - _Requirements: 4.8_
  - [x] 13.4 Incident entegrasyonu
    - create_incidents_from_quality'de ActionHint üretimi
    - details dict'e action_hint eklenmesi
    - extraction_confidence parametresi eklendi
    - _Requirements: 4.8_
  - [x] 13.5 Golden action tests (test_action_hints_golden.py)
    - 7 golden senaryo (perfect_match, rounding_diff, small_mismatch, true_rounding, real_mismatch, severe_mismatch, ocr_suspect)
    - 3 determinism testi
    - 3 ordering testi
    - _Requirements: 4.8_
  - [x] 13.6 Unit tests (test_action_hints_unit.py)
    - Edge cases (unsupported flag, null mismatch_info, missing fields)
    - Decision tree boundary tests
    - Enum value tests
    - 18 test, tümü geçiyor
    - _Requirements: 4.8_
  - [x] 13.7 Full test suite: 186 tests passing

- [x] 14. Sprint 8.6 - Distribution Sanity Check (System Health Dashboard)
  - [x] 14.1 Data Models (incident_metrics.py)
    - PeriodStats dataclass (total_invoices, mismatch_count, s1_count, s2_count, ocr_suspect_count)
    - DriftAlert dataclass (alert_type, old_rate, new_rate, delta, triggered)
    - TopOffender dataclass (provider, total_count, mismatch_count, mismatch_rate)
    - SystemHealthReport dataclass (period_stats, drift_alerts, top_offenders, histogram)
    - _Requirements: 4.9_
  - [x] 14.2 Histogram hesaplama
    - Bucket'lar: [0-2%, 2-5%, 5-10%, 10-20%, 20%+]
    - calculate_mismatch_histogram() fonksiyonu
    - Ratio tanımı: abs(invoice_total - computed_total) / max(invoice_total, 0.01)
    - _Requirements: 4.9_
  - [x] 14.3 Drift detection (triple guard + zero rate handling)
    - check_rate_drift(): Rate bazlı drift (prev_rate == 0 handling ile)
    - Triple guard: curr_total >= 20 AND abs_delta >= 5 AND rate >= 2x
    - prev_rate == 0 ise rate guard atlanır, count guard yeterli
    - S1_RATE_DRIFT, OCR_SUSPECT_DRIFT, MISMATCH_RATE_DRIFT alert tipleri
    - _Requirements: 4.9_
  - [x] 14.4 Top offenders hesaplama (rate + min volume)
    - Provider bazlı mismatch RATE (count değil!)
    - Minimum volume guard: total_count >= 20
    - İki liste: top_by_rate (min_n>=20) + top_by_count
    - get_top_offenders_by_rate() ve get_top_offenders_by_count() fonksiyonları
    - _Requirements: 4.9_
  - [x] 14.5 Action class distribution
    - VERIFY_OCR / VERIFY_INVOICE_LOGIC / ACCEPT_ROUNDING dağılımı
    - get_action_class_distribution() fonksiyonu
    - _Requirements: 4.9_
  - [x] 14.6 System Health Report builder
    - generate_system_health_report() fonksiyonu
    - 7 günlük period karşılaştırma
    - _Requirements: 4.9_
  - [x] 14.7 API Endpoint
    - GET /admin/system-health
    - main.py'ye ekleme
    - _Requirements: 4.9_
  - [x] 14.8 Unit tests (test_incident_metrics.py)
    - Histogram bucket tests
    - Drift detection triple guard tests
    - Zero rate handling tests
    - Top offenders rate + min volume tests
    - _Requirements: 4.9_
  - [x] 14.9 Golden tests
    - 5 golden senaryo (no_drift, s1_drift, ocr_drift, mismatch_drift, small_sample_protection)
    - Zero rate edge case golden test
    - _Requirements: 4.9_

- [d] 15. Sprint 8.7 - Feedback Loop — **Deferred: Phase 2 (separate sprint, requires feedback data from production)**
  - [ ] 15.1 Data Models (incident_metrics.py)
    - FeedbackAction enum (VERIFIED_OCR, VERIFIED_LOGIC, ACCEPTED_ROUNDING, ESCALATED, NO_ACTION_REQUIRED)
    - IncidentFeedback dataclass
    - FeedbackStats dataclass (hint_accuracy, action_accuracy, avg_resolution, feedback_coverage)
    - _Requirements: 4.10_
  - [ ] 15.2 Database Migration
    - feedback_json column on Incident table (JSON, nullable)
    - Alembic migration file
    - Backfill yok; eski kayıtlar null kalır
    - _Requirements: 4.10_
  - [ ] 15.3 Feedback Service Functions
    - validate_feedback(payload, incident) - Validation kuralları
      - was_hint_correct not null
      - resolution_time_seconds >= 0
      - actual_root_cause max 200 char
      - action_taken in enum
    - submit_feedback(db, incident_id, payload, user_id) - Feedback kaydet
      - State guard: Sadece RESOLVED incident'lara
      - UPSERT semantiği: Her submission overwrite, updated_at değişir
      - feedback_at = server time
      - feedback_by = user_id (auth'dan)
    - get_feedback_stats(tenant_id, start_date, end_date) - Kalibrasyon metrikleri
      - hint_accuracy_rate (null-safe: 0.0 if total=0)
      - action_class_accuracy
      - avg_resolution_time_by_class
      - feedback_coverage (null-safe: 0.0 if resolved=0)
    - _Requirements: 4.10_
  - [ ] 15.4 API Endpoints (main.py)
    - PATCH /admin/incidents/{id}/feedback
      - 200: Success
      - 400: incident_not_resolved, invalid_feedback_action, invalid_feedback_data
      - 404: incident_not_found
    - GET /admin/feedback-stats
    - _Requirements: 4.10_
  - [ ] 15.5 Unit Tests (test_feedback.py)
    - Feedback submission tests
    - Upsert test: same payload still updates updated_at
    - State guard test (non-resolved → 400)
    - Validation tests:
      - missing was_hint_correct → 400
      - negative resolution_time → 400
      - actual_root_cause > 200 char → 400
      - invalid action_taken → 400
    - Stats calculation tests (null-safe)
    - feedback_coverage calculation test
    - _Requirements: 4.10_
  - [ ] 15.6 Property Tests
    - **Property 33: Feedback Action Enum Validity**
    - **Property 34: Feedback Timestamp Consistency**
    - **Property 35: Feedback Upsert Semantics**
    - **Property 36: Hint Accuracy Calculation**
    - **Property 37: Feedback Stats Null Safety**
    - **Property 38: Feedback State Guard**
    - **Property 39: Feedback User Required**
    - **Property 40: Feedback Validation Invariants**
    - **Property 41: Feedback Coverage Calculation**
    - _Requirements: 4.10_

---

## Prod Hardening Sprint Planı

Hedef: ProdReady Gate #1-3 kapatma. 2 faz, risk-azaltan sıra.

### Faz A — Doğruluk Çekirdeği (Gün 1)

| Sıra | Task | Gate | Deliverable | Acceptance Criteria |
|------|------|------|-------------|---------------------|
| A1 | 4.1 Validasyon kuralları | #1 | `ValidationErrorCode` enum (kapalı küme) + ETTN UUID parse + T1+T2+T3 tutarlılık + reaktif ceza kontrol | ETTN regex match, T1+T2+T3=Toplam (tolerans %1), endüktif %33 / kapasitif %20 eşik |
| A2 | 4.2 Tedarikçi tolerans | #1 | Supplier profile → tolerans parametreleri | Her supplier için yuvarlama toleransı + alan isim varyantları tanımlı |
| A3 | 7.2 Response model | #3 | Yeni alanlar API response'a eklendi, versiyon notu | Breaking change yok, eski client'lar çalışır, yeni alanlar nullable |
| — | Milestone: Checkpoint 5 | — | Mevcut 490 test hâlâ yeşil | Regression guard |

### Faz B — Prod Koruması (Gün 2)

| Sıra | Task | Gate | Deliverable | Acceptance Criteria |
|------|------|------|-------------|---------------------|
| B1 | 8.4 Çok zamanlı fatura testi | #2 | T1-T2-T3 fixture + golden test | T1+T2+T3=Toplam doğrulanır, tutarsızlık flag üretir |
| B2 | 8.5 Reaktif cezalı fatura testi | #2 | Reaktif ceza fixture + golden test | Endüktif/kapasitif eşik aşımı doğru tespit edilir |
| B3 | 8.1 Enerjisa fatura testi | #2 | Gerçek fatura fixture + golden test | Extraction + validation + calculator doğru |
| B4 | 8.2 CK Boğaziçi fatura testi | #2 | Gerçek fatura fixture + golden test | Extraction + validation + calculator doğru |
| B5 | 8.3 Uludağ fatura testi | #2 | Gerçek fatura fixture + golden test | Extraction + validation + calculator doğru |
| B6 | 7.1 GET /suppliers endpoint | #3 | Desteklenen tedarikçi listesi API'si | JSON response, supplier listesi doğru |
| B7 | 6.1-6.2 Property tests | Supporting | PBT: ETTN format + T1+T2+T3 tutarlılık + error code kapalı küme | Hypothesis 200 example, tümü yeşil |
| — | Milestone: Checkpoint 9 | — | 3 gate kapanır → ProdReady = YES | Final regression + gate checklist |

### Fixture Convention

```
tests/fixtures/invoices/{supplier}_{scenario}.json
```

Örnekler: `enerjisa_standard.json`, `uludag_multitime.json`, `ckbogazici_reactive_penalty.json`

### Risk Notu

En büyük risk 8.4 + 8.5: çok zamanlı + reaktif cezalı faturalar. Bunlar yeşil olmadan prod'a çıkma — "yanlış teklif" riski burada patlar.

### Prod Çıkış Checklist

- [ ] ValidationErrorCode enum kapalı küme ve dokümante
- [ ] 5 supplier test senaryosu yeşil
- [ ] /suppliers endpoint + response model stable (versiyon notu)
- [ ] Mevcut 490+ test hâlâ yeşil
- [ ] Final checkpoint raporu: "neden prod-ready"

---

## Notes

- Extraction prompt v3 kullanılıyor (genişletilmiş tedarikçi ve alan desteği)
- Geriye uyumluluk korundu (eski alanlar hala mevcut)
- 2 katmanlı parser mimarisi: Kimlik tespiti + Anlamsal okuma
- Alan eşleştirme sözlüğü ile farklı tedarikçilerin aynı anlama gelen etiketleri tanınıyor
- Webhook sistemi Stage 5 paketinden implemente edildi:
  - Event-driven mimari (invoice.*, offer.*, customer.*)
  - HMAC-SHA256 imzalama ile güvenlik
  - Retry mekanizması (1dk, 5dk, 15dk)
  - Tenant bazlı izolasyon
  - Audit logging
- **Sprint 8.3/8.4 - Calculator Contract + TOTAL_MISMATCH** (Tamamlandı):
  - Calculator epistemolojisi düzeltildi: "Faturanın toplamı gerçektir, bizim hesap toplamı teşhistir"
  - current_total = invoice_total (SOURCE OF TRUTH), offer_* = CALCULATED
  - INVOICE_TOTAL_MISMATCH flag: S2 (ratio>=5% OR delta>=50 TL)
  - S1 escalation: (ratio>=20% AND delta>=50) OR delta>=500
  - OCR_LOCALE_SUSPECT: confidence<0.7 + mismatch → tag (ayrı flag değil)
  - 5 golden scenario ile regression koruması
  - 423 test geçiyor
- **Sprint 8.5 - Actionability** (Tamamlandı):
  - "3 adımda karar" prensibi: action_class + primary_suspect + recommended_checks
  - ActionClass enum: VERIFY_OCR, VERIFY_INVOICE_LOGIC, ACCEPT_ROUNDING_TOLERANCE
  - PrimarySuspect enum: OCR_LOCALE_SUSPECT, INVOICE_LOGIC, ROUNDING
  - Sabit check listeleri ile tam determinism
  - Rounding tolerance guard: delta < 10 AND ratio < 0.005
  - 31 yeni test (golden + unit), 186 toplam test geçiyor
- **Sprint 8.6 - System Health Dashboard** (Tamamlandı):
  - Amaç: "Sistem bozuldu mu, yoksa dünya mı bozuk?"
  - Mismatch ratio histogram: [0-2%, 2-5%, 5-10%, 10-20%, 20%+]
  - Drift detection triple guard: n >= 20 AND abs_delta >= 5 AND rate >= 2x
  - Top offenders: mismatch RATE (count değil!)
  - Action class distribution metrikleri
  - `/admin/system-health` endpoint
  - 51 test, 490 toplam test geçiyor
- **Sprint 8.7 - Feedback Loop** (Planlandı):
  - Amaç: Hint kalitesini ölçmek, gelecekte kalibrasyon için veri toplamak
  - FeedbackAction: VERIFIED_OCR, VERIFIED_LOGIC, ACCEPTED_ROUNDING, ESCALATED, NO_ACTION_REQUIRED
  - Feedback sadece RESOLVED incident'lara yazılabilir (state guard)
  - Feedback opsiyonel (zorunlu değil)
  - Kalibrasyon metrikleri: hint_accuracy_rate, action_class_accuracy, avg_resolution_time_by_class
  - Otomasyon yok - sadece veri toplama
- **Sprint 9 - Preventive Fixes** (Beklemede):
  - Sprint 8.7'den gelen feedback verisi olmadan başlanmayacak
  - Kanıta dayalı karar için istatistik gerekli

## Spec Status

```
Status:              CORE_DONE_NOT_PROD_READY
Owner:               —
Done:                8 top-level tasks (1, 2, 3, 10, 11, 12, 13, 14)
Deferred:            7 top-level tasks
  Prod Hardening:    5 tasks (4, 6, 7, 8 + checkpoint 5, 9)
  Phase 2:           1 task (15: feedback loop — prod data bağımlı)
Sub-task hard TODO:  17 (deferred scope altında — prod hardening devreye alınırsa yapılacak iş)
Sub-task soft TODO:  0
Test count:          490 passing

ProdReady Gate (bu üçü yoksa prod'a çıkma):
  ❌ 1. Validator: ETTN format + çok zamanlı tutarlılık + reaktif ceza validasyonu
  ❌ 2. Supplier tests: En az 5 tedarikçi gerçek fatura senaryosu
  ❌ 3. API contract: /suppliers endpoint + response model versiyonlanmış
  Risk: Bu gate'ler olmadan prod = yanlış teklif / yanlış kıyas riski

ExitCriteria_Core:
  ✅ 1. Extraction + calculator + mismatch detection çalışıyor
  ✅ 2. Actionability (action hints) çalışıyor
  ✅ 3. System health dashboard çalışıyor

ExitCriteria_ProdReady:
  1. Task 4 (Validator): ETTN format + çok zamanlı tutarlılık + reaktif ceza validasyonu
  2. Task 8 (Supplier tests): En az 5 tedarikçi için regression test
  3. Task 7.2 (Response model): Yeni alanların API response'a eklenmesi

ExitCriteria_Closeout:
  1. Task 15 (Feedback Loop): Prod'dan feedback data toplandıktan sonra
```
