# İmplementasyon Planı: PDF Render Worker

## Genel Bakış

Mevcut RQ + Redis altyapısı üzerine PDF render worker sistemi inşa edilir. Job modeli ve durum makinesi ile başlanır, ardından Redis store, worker, artifact storage, API endpoint'leri, güvenlik, metrikler ve entegrasyon testleri sırasıyla eklenir.

## Görevler

- [ ] 1. Job modeli, durum enum'ları ve hata kodları
  - [ ] 1.1 `PdfJobStatus`, `PdfErrorCode` enum'larını ve `PdfJob` dataclass'ını oluştur (`backend/app/services/pdf_job_store.py`)
    - `TRANSIENT_ERRORS` kümesi ve `MAX_RETRIES` sabiti tanımla
    - `should_retry(error_code, retry_count)` fonksiyonunu implement et
    - `compute_job_key(template_name, payload)` fonksiyonunu implement et (sha256 hash)
    - Geçerli durum geçişleri kümesini `VALID_TRANSITIONS` olarak tanımla
    - _Requirements: 2.1, 3.3, 5.1, 5.2, 5.3_
  - [ ]* 1.2 Job key deterministik hash property testi yaz
    - **Property 2: Job Key Deterministik Hash**
    - **Validates: Requirements 3.3**
  - [ ]* 1.3 Retry politikası property testi yaz
    - **Property 3: Retry Politikası Doğruluğu**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4**
  - [ ]* 1.4 Durum makinesi geçerli geçişler property testi yaz
    - **Property 1: Durum Makinesi Geçerli Geçişler**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6**

- [ ] 2. Redis store ve TTL cleanup
  - [ ] 2.1 `PdfJobStore` sınıfını implement et (`backend/app/services/pdf_job_store.py`)
    - `create_job()`, `get_job()`, `find_by_key()`, `update_status()` metodları
    - Redis key yapısı: `pdf:job:{job_id}`, `pdf:key:{job_key}`, `pdf:jobs:queued`
    - İdempotency mantığı: `find_by_key()` ile mevcut job kontrolü
    - _Requirements: 1.1, 2.1, 2.2, 2.3, 2.4, 2.5, 6.1, 6.2, 6.3_
  - [ ] 2.2 `cleanup_expired()` metodunu implement et
    - TTL süresi dolmuş job'ları `expired` durumuna geçir
    - İlgili artifact'ları sil
    - _Requirements: 2.6, 7.3_
  - [ ]* 2.3 İdempotency property testi yaz
    - **Property 4: İdempotency Garantisi**
    - **Validates: Requirements 6.1, 6.2, 6.3**
  - [ ]* 2.4 TTL cleanup property testi yaz
    - **Property 10: TTL Cleanup Doğruluğu**
    - **Validates: Requirements 7.3**

- [ ] 3. Checkpoint — Tüm testlerin geçtiğinden emin ol
  - Tüm testleri çalıştır, sorular varsa kullanıcıya sor.

- [ ] 4. Worker process (RQ) ve Playwright render fonksiyonu
  - [ ] 4.1 `PdfRenderWorker` sınıfını implement et (`backend/app/services/pdf_render_worker.py`)
    - `process_job()`: job claim → render → artifact store → status update
    - `render_html_to_pdf()`: mevcut `html_to_pdf_bytes_sync_v2()` üzerine timeout wrapper
    - Hata yakalama ve Error_Taxonomy sınıflandırması
    - Retry mantığı: `should_retry()` ile transient hata kontrolü
    - _Requirements: 1.2, 3.1, 3.2, 4.1, 4.2, 4.3, 5.1, 5.3_
  - [ ] 4.2 RQ entegrasyonu: `enqueue_pdf_job()` fonksiyonunu implement et
    - Mevcut `rq_adapter.py` pattern'ini takip et
    - `app.services.pdf_render_worker.process_job` fonksiyonunu RQ'ya kaydet
    - _Requirements: 1.2_

- [ ] 5. Artifact storage ve download endpoint
  - [ ] 5.1 `PdfArtifactStore` sınıfını implement et (`backend/app/services/pdf_artifact_store.py`)
    - Mevcut `StorageBackend` arayüzünü kullan
    - `store_pdf()`, `get_pdf()`, `delete_pdf()`, `exists()`, `generate_key()` metodları
    - Key formatı: `pdfs/{job_id}/{timestamp}.pdf`
    - _Requirements: 7.1, 7.2, 7.5_
  - [ ]* 5.2 Artifact key benzersizliği property testi yaz
    - **Property 9: Artifact Key Benzersizliği**
    - **Validates: Requirements 7.5**

- [ ] 6. API endpoint'leri ve güvenlik
  - [ ] 6.1 Güvenlik bileşenlerini implement et (`backend/app/services/pdf_security.py`)
    - `validate_template()`, `validate_url()`, `sanitize_html_variables()` fonksiyonları
    - Template allowlist ve URL allowlist kontrolü
    - HTML escape (markupsafe veya html.escape)
    - _Requirements: 8.1, 8.2, 8.3, 8.4_
  - [ ] 6.2 API router'ı implement et (`backend/app/pdf_api.py`)
    - `POST /pdf/jobs`: job oluştur, idempotency kontrolü, allowlist doğrulama
    - `GET /pdf/jobs/{job_id}`: durum sorgula
    - `GET /pdf/jobs/{job_id}/download`: PDF indir (auth check)
    - Fail-safe politikası: strict (503) veya degrade (HTML fallback)
    - Router'ı `main.py`'a kaydet
    - _Requirements: 1.3, 8.5, 10.1, 10.2, 10.3, 10.4, 11.1, 11.2, 11.3, 11.4, 11.5_
  - [ ]* 6.3 Allowlist doğrulama property testi yaz
    - **Property 5: Allowlist Doğrulaması**
    - **Validates: Requirements 8.1, 8.2**
  - [ ]* 6.4 HTML sanitizasyon property testi yaz
    - **Property 6: HTML Sanitizasyon**
    - **Validates: Requirements 8.3**
  - [ ]* 6.5 Job oluşturma ve sorgulama round-trip property testi yaz
    - **Property 8: Job Oluşturma ve Sorgulama Round-Trip**
    - **Validates: Requirements 11.1, 11.2**

- [ ] 7. Checkpoint — Tüm testlerin geçtiğinden emin ol
  - Tüm testleri çalıştır, sorular varsa kullanıcıya sor.

- [x] 8. Metrikler ve gözlemlenebilirlik
  - [x] 8.1 PTFMetrics sınıfına PDF metriklerini ekle (`backend/app/ptf_metrics.py`)
    - `ptf_admin_pdf_jobs_total{status}` Counter
    - `ptf_admin_pdf_job_failures_total{error_code}` Counter
    - `ptf_admin_pdf_job_duration_seconds` Histogram
    - `ptf_admin_pdf_queue_depth` Gauge
    - Helper metodlar: `inc_pdf_job()`, `inc_pdf_failure()`, `observe_pdf_job_duration()`, `set_pdf_queue_depth()`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_
  - [x] 8.2 Worker ve API koduna metrik çağrılarını entegre et
    - Job durum değişikliklerinde `inc_pdf_job()` çağır
    - Render sonrasında `observe_pdf_job_duration()` çağır
    - Hata durumlarında `inc_pdf_failure()` çağır
    - _Requirements: 9.1, 9.2, 9.3_
  - [x]* 8.3 Metrik kayıt tutarlılığı testleri (25 test)
    - PM1-PM5: Counter increments, label bounded, cardinality
    - PM6: Worker integration — render_pdf_job emits metrics
    - PM7-PM8: API integration — create/enqueue fail emits metrics
    - _Requirements: 9.1, 9.3_
  - [x] 8.4 Alert kuralları (`monitoring/prometheus/ptf-admin-alerts.yml`)
    - PW1: PTFAdminPdfQueueUnavailable — QUEUE_UNAVAILABLE spike (warning)
    - PW2: PTFAdminPdfFailureSpike — failure rate spike (warning)
    - PW3: PTFAdminPdfQueueBacklog — queue depth > 50 for 10m (warning)
    - _Requirements: 9.1, 9.4_
  - [x] 8.5 Grafana dashboard (`monitoring/grafana/pdf-worker-dashboard.json`)
    - Panel 1: Jobs by Status (timeseries, stacked)
    - Panel 2: Failures by Error Code (barchart)
    - Panel 3: Render Duration p50/p95 (timeseries)
    - Panel 4: Queue Depth (stat)
    - _Requirements: 9.4, 9.5_
  - [x] 8.6 Runbook güncellemesi (`monitoring/runbooks/ptf-admin-runbook.md`)
    - PTFAdminPdfQueueUnavailable: Redis/RQ triage
    - PTFAdminPdfFailureSpike: BROWSER_LAUNCH_FAILED / NAVIGATION_TIMEOUT / ARTIFACT_WRITE_FAILED triage
    - PTFAdminPdfQueueBacklog: worker scaling triage
    - _Requirements: 9.4_
  - [x] 8.7 Alert ve dashboard yapısal testleri (25 test)
    - PA1-PA6: Alert group, rule count, fields, runbook anchors, severity, names
    - PD1-PD6: Dashboard structure, panel types, targets, descriptions, stacking, metric coverage
    - _Requirements: 9.4, 9.5_

- [ ] 9. Entegrasyon testleri
  - [ ]* 9.1 Happy path entegrasyon testi yaz
    - Job oluştur → worker işle → status sorgula → download
    - _Requirements: 11.1, 11.2, 11.3_
  - [ ]* 9.2 Timeout entegrasyon testi yaz
    - Uzun süren render → timeout → failed status
    - _Requirements: 4.1, 4.2_
  - [ ]* 9.3 Worker down entegrasyon testi yaz
    - Redis bağlantısı yok → 503 + PDF_RENDER_UNAVAILABLE
    - Degrade modu → HTML fallback
    - _Requirements: 1.3, 10.1, 10.2_
  - [ ]* 9.4 Retry path entegrasyon testi yaz
    - Transient hata → retry → başarı
    - Max retry aşımı → kalıcı failed
    - _Requirements: 5.1, 5.2_

- [ ] 10. Final checkpoint ve runbook
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Her görev belirli gereksinimleri referans alır (izlenebilirlik)
- Checkpoint'ler artımlı doğrulama sağlar
- Property testleri evrensel doğruluk özelliklerini doğrular
- Unit testler belirli örnekleri ve edge case'leri doğrular
- Mevcut `rq_adapter.py`, `pdf_playwright.py`, `StorageBackend` altyapısı yeniden kullanılır
