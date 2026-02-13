# Implementation Plan: Telemetry Unification

## Overview

Mevcut `PTFMetrics` sınıfını `prometheus_client` tiplerine migrate et, `/metrics` endpoint'i ekle, request metrics middleware oluştur, frontend event tracking modülü yaz ve hook'lara entegre et, backend event ingestion endpoint'i ekle.

## Tasks

- [x] 1. PTFMetrics sınıfını prometheus_client ile refactor et
  - [x] 1.1 `backend/app/ptf_metrics.py` — `PTFMetrics.__init__()` içinde `CollectorRegistry` oluştur, mevcut in-memory sayaçları `Counter` ve `Histogram` tipleriyle değiştir
    - `ptf_admin_upsert_total` (Counter, labels: status)
    - `ptf_admin_import_rows_total` (Counter, labels: outcome)
    - `ptf_admin_import_apply_duration_seconds` (Histogram)
    - `ptf_admin_lookup_total` (Counter, labels: hit, status)
    - Yeni: `ptf_admin_history_query_total` (Counter), `ptf_admin_history_query_duration_seconds` (Histogram)
    - Yeni: `ptf_admin_api_request_total` (Counter, labels: endpoint, method, status_class), `ptf_admin_api_request_duration_seconds` (Histogram, labels: endpoint)
    - Yeni: `ptf_admin_frontend_events_total` (Counter, labels: event_name)
    - `inc_upsert()`, `inc_import_rows()`, `inc_lookup()`, `observe_import_apply_duration()`, `time_import_apply()` metotlarını prometheus_client çağrılarına dönüştür
    - Yeni metotlar: `inc_history_query()`, `time_history_query()`, `inc_api_request()`, `observe_api_request_duration()`, `inc_frontend_event()`
    - `snapshot()` — prometheus_client registry'den değerleri okuyarak mevcut dict formatını döndür
    - `reset()` — yeni `CollectorRegistry` oluşturarak tüm metrikleri sıfırla
    - `generate_metrics()` — `generate_latest(self._registry)` çağır
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.2_

  - [ ]* 1.2 Property test: Snapshot round-trip correctness
    - **Property 2: Snapshot round-trip correctness**
    - `@given(random_metric_increments())` — rastgele artışlar sonrası snapshot değerleri doğru
    - **Validates: Requirements 2.5**

  - [ ]* 1.3 Property test: Reset clears all metrics
    - **Property 3: Reset clears all metrics**
    - `@given(random_metric_increments())` — reset sonrası tüm değerler sıfır
    - **Validates: Requirements 2.6**

- [x] 2. Mevcut testlerin refactored PTFMetrics ile çalıştığını doğrula
  - `backend/tests/test_ptf_metrics.py` — mevcut testleri çalıştır, gerekirse snapshot format değişikliklerine göre güncelle
  - Mevcut integration testleri (upsert, lookup, import_apply endpoint metrikleri) hala geçmeli
  - _Requirements: 2.4, 2.5_

- [x] 3. GET /metrics endpoint ve middleware ekle
  - [x] 3.1 `backend/app/main.py` — `GET /metrics` endpoint ekle, auth gerektirmesin, `generate_metrics()` çağırsın, Content-Type: `text/plain; version=0.0.4; charset=utf-8`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 3.2 `backend/app/metrics_middleware.py` — `MetricsMiddleware` oluştur, her request'te `api_request_total` ve `api_request_duration` metriklerini güncelle, `/metrics` endpoint'ini hariç tut
    - **3-seviyeli endpoint label normalization**:
      - Level 1: `request.scope.get("route").path` → route template (ör. `/admin/market-prices/{period}`)
      - Level 2 (route yok): Sanitized path bucket — ilk 2 segment + `*` (ör. `/admin/market-prices/*`)
      - Level 3 (404): `unmatched:/admin/market-prices/*` formatı
    - `backend/app/main.py` — middleware'i FastAPI app'e ekle
    - _Requirements: 3.3, 3.4_

  - [x] 3.3 History endpoint'ine metrik entegrasyonu — `GET /admin/market-prices/history` handler'ına `inc_history_query()` ve `time_history_query()` çağrıları ekle
    - _Requirements: 3.1, 3.2_

  - [ ]* 3.4 Property test: Prometheus output validity and completeness
    - **Property 1: Prometheus output validity and completeness**
    - `@given(random_metric_increments())` — /metrics çıktısı valid format ve tüm ptf_admin_ metrikleri içerir
    - **Validates: Requirements 1.1, 1.4**

  - [ ]* 3.5 Property test: HTTP request metrics tracking
    - **Property 4: HTTP request metrics tracking**
    - `@given(random_http_requests())` — middleware doğru label'larla sayaç artırır
    - **Validates: Requirements 3.3, 3.4**

- [x] 4. Checkpoint — Backend metrikleri doğrula
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Backend event ingestion endpoint ekle
  - [x] 5.1 `backend/app/event_store.py` — `EventStore` sınıfı oluştur (thread-safe, counter-only, singleton pattern)
    - **Counter-only tasarım**: event payload saklanmaz, yalnızca per-event_name sayaçlar tutulur (PII/secret sızıntı riski yok)
    - `increment(event_name)`, `increment_rejected()`, `get_counters()`, `get_totals()`, `reset()` metotları
    - _Requirements: 6.6_

  - [x] 5.2 `backend/app/main.py` — `POST /admin/telemetry/events` endpoint ekle
    - `TelemetryEvent` ve `TelemetryEventsRequest` Pydantic modelleri
    - **Auth**: YOK (bilinçli istisna — endpoint yalnızca counter artırır, risk profili GET /metrics ile aynı)
    - **Endpoint scope kilidi**: Yalnızca `ptf_admin_frontend_events_total` counter'ını artırır. Başka write/read yok. Payload saklanmaz, geri okunmaz
    - **Event name allowlist**: Yalnızca `ptf_admin.` prefix'i kabul edilir, bilinmeyen prefix → rejected
    - **Max batch size**: 100 event/request, aşarsa 400
    - **Rate limit**: 60 req/dk/IP, aşarsa 429 (spam şişirme önlemi, güvenlik değil)
    - **Label policy**: Tek label `event_name` (allowlist'ten). `properties` asla label'a dönüştürülmez
    - **Operasyonel görünürlük**: Request başına tek INFO satırı: `accepted=N rejected=M events={name1:count, name2:count}`. Event bazlı log yok
    - Response: `{ status, accepted_count, rejected_count }`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.7, 6.8, 6.9, 6.10, 6.11, 6.12_

  - [ ]* 5.3 Property test: Event ingestion increments counters
    - **Property 7: Event ingestion increments counters for valid events**
    - `@given(random_valid_event_batches())` — tüm valid event'ler için counter artırılır
    - **Validates: Requirements 6.1, 6.2, 6.6**

  - [ ]* 5.4 Property test: Partial batch acceptance
    - **Property 8: Partial batch acceptance**
    - `@given(random_mixed_event_batches())` — valid/invalid ayrımı doğru
    - **Validates: Requirements 6.5**

  - [ ]* 5.5 Property test: Frontend event Prometheus counter
    - **Property 9: Frontend event Prometheus counter**
    - `@given(random_valid_event_batches())` — Prometheus counter doğru artırılır
    - **Validates: Requirements 6.7**

- [x] 6. Checkpoint — Backend tamamlandı
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Frontend telemetry modülü oluştur
  - [x] 7.1 `frontend/src/market-prices/telemetry.ts` — `trackEvent()` fonksiyonu, event buffer, flush mekanizması, `FLUSH_INTERVAL_MS`, `MAX_BATCH_SIZE` ve `MAX_BUFFER_SIZE` sabitleri
    - Fire-and-forget pattern: POST hatası → batch discard + console.warn, retry yok
    - Batching: flush interval veya max batch size'a ulaşınca POST gönder
    - **Buffer overflow koruması**: `MAX_BUFFER_SIZE` (200) aşılırsa en eski event'ler drop edilir + console.warn
    - **Flush failure**: batch discard edilir, retry yapılmaz
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [ ]* 7.2 Property test: Event construction correctness (fast-check)
    - **Property 5: Event construction correctness**
    - Rastgele event name ve properties ile oluşturulan event'in doğru yapıda olduğunu doğrula
    - **Validates: Requirements 4.2**

  - [ ]* 7.3 Property test: Event batching behavior (fast-check)
    - **Property 6: Event batching behavior**
    - N rapid trackEvent çağrısı sonrası tek POST gönderildiğini doğrula
    - **Validates: Requirements 4.5**

- [x] 8. Frontend hook'lara ve component'lere telemetri entegrasyonu
  - [x] 8.1 `frontend/src/market-prices/hooks/useUpsertMarketPrice.ts` — `ptf_admin.upsert_submit`, `ptf_admin.upsert_success`, `ptf_admin.upsert_error` event'leri ekle
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 8.2 `frontend/src/market-prices/hooks/useBulkImportPreview.ts` ve `useBulkImportApply.ts` — `ptf_admin.bulk_import_start`, `ptf_admin.bulk_import_complete`, `ptf_admin.bulk_import_error` event'leri ekle
    - _Requirements: 5.4, 5.5, 5.6_

  - [x] 8.3 `frontend/src/market-prices/hooks/useAuditHistory.ts` — `ptf_admin.history_open` event'i ekle
    - _Requirements: 5.7_

  - [x] 8.4 `frontend/src/market-prices/PriceFilters.tsx` — `ptf_admin.filter_change` event'i ekle
    - _Requirements: 5.8_

  - [ ]* 8.5 Unit tests: Hook telemetri entegrasyonları
    - Mock `trackEvent` ve her hook'ta doğru event name/properties ile çağrıldığını doğrula
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_

- [x] 9. Final checkpoint — Tüm testler geçiyor
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- `*` ile işaretli task'lar opsiyoneldir, hızlı MVP için atlanabilir
- Her task belirli requirement'lara referans verir (traceability)
- Checkpoint'ler incremental doğrulama sağlar
- Property testleri evrensel doğruluk özelliklerini, unit testler spesifik örnekleri ve edge case'leri doğrular
- Backend: pytest + Hypothesis, Frontend: vitest + fast-check
- **Best-effort garantisi**: Telemetri hatası admin request'lerini asla fail etmez, timeout kısa tutulur, exception swallow edilir. "Observability yüzünden prod'u bozma" kuralı birincildir.
- **`/metrics` güvenliği**: Endpoint auth gerektirmez ama public internet'e açık olmamalı. Prometheus internal scrape + network policy ile korunmalı.
- **`snapshot()` / `reset()` semantiği**: Sadece test/debug amaçlı. Prod request path'te kullanılmaz.
- **Pre-existing test fix (CI yeşil)**: `test_market_price_admin_service` — 2 fail audit-history 2-commit pattern'inden kaynaklanıyordu. `assert_called_once()` → doğru call count assertion'larına güncellendi. 1086 passed, 0 failed, 5 skipped.
- **Backend phase kilitli**: Task 1–6 + abuse-hardening + pre-existing fix tamamlandı. Frontend phase (Task 7–9) beklemede.
