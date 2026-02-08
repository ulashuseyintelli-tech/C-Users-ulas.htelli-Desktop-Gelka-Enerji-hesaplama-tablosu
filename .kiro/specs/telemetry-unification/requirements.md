# Requirements Document

## Introduction

Backend telemetri altyapısını Prometheus-uyumlu metrik sistemine dönüştürme ve frontend tarafında aynı namespace ile event tracking ekleme özelliği. Mevcut `PTFMetrics` sınıfındaki in-memory sayaçlar `prometheus_client` kütüphanesi ile değiştirilecek, `/metrics` endpoint'i üzerinden Grafana scraping desteği sağlanacak. Frontend tarafında kullanıcı aksiyonları hafif bir telemetri modülü ile izlenecek.

Bu özellik, planlanan üç aşamanın ilkidir (Telemetry → YEKDEM → E2E).

## Glossary

- **Metrics_Exporter**: Prometheus text formatında metrik döndüren `GET /metrics` endpoint'i
- **PTF_Metrics**: `prometheus_client` Counter ve Histogram tipleri kullanan refactored metrik sınıfı
- **Event_Tracker**: Frontend tarafında kullanıcı aksiyonlarını izleyen `telemetry.ts` modülü
- **Event_Ingestion_API**: Frontend event'lerini alan `POST /admin/telemetry/events` endpoint'i
- **Telemetry_Event**: Frontend'den gönderilen tek bir kullanıcı aksiyonu kaydı
- **Prom_Counter**: `prometheus_client.Counter` tipi — monoton artan sayaç
- **Prom_Histogram**: `prometheus_client.Histogram` tipi — süre dağılımı ölçümü
- **Metric_Namespace**: Tüm metriklerde kullanılan `ptf_admin_` prefix'i

## Requirements

### Requirement 1: Prometheus Metrics Exposition

**User Story:** As a DevOps engineer, I want to scrape application metrics in Prometheus text format, so that I can monitor the system via Grafana dashboards.

#### Acceptance Criteria

1. THE Metrics_Exporter SHALL expose a `GET /metrics` endpoint that returns metrics in Prometheus text exposition format
2. WHEN the `GET /metrics` endpoint is called, THE Metrics_Exporter SHALL return a response with `Content-Type: text/plain; version=0.0.4; charset=utf-8`
3. THE Metrics_Exporter SHALL serve the `/metrics` endpoint without requiring authentication (X-Admin-Key or X-Api-Key). THE `/metrics` endpoint SHALL NOT be exposed to public internet; it is intended for internal Prometheus scraping only (network policy / reverse proxy ile korunmalı)
4. WHEN Prometheus scrapes the `/metrics` endpoint, THE Metrics_Exporter SHALL return all registered PTF_Metrics counters and histograms with the `ptf_admin_` namespace prefix
5. WHEN the `/metrics` endpoint is called, THE Metrics_Exporter SHALL include standard `prometheus_client` process metrics alongside PTF_Metrics

### Requirement 2: Prometheus Client Entegrasyonu

**User Story:** As a developer, I want the existing in-memory counters migrated to prometheus_client types, so that metrics are compatible with the Prometheus ecosystem.

#### Acceptance Criteria

1. THE PTF_Metrics SHALL use `prometheus_client.Counter` for `ptf_admin_upsert_total`, `ptf_admin_import_rows_total`, and `ptf_admin_lookup_total` metrics
2. THE PTF_Metrics SHALL use `prometheus_client.Histogram` for `ptf_admin_import_apply_duration_seconds` metric
3. THE PTF_Metrics SHALL preserve the existing label dimensions: `outcome` for import_rows, `status` for upsert, `hit` and `status` for lookup
4. THE PTF_Metrics SHALL maintain the existing public API methods: `inc_upsert()`, `inc_import_rows()`, `inc_lookup()`, `observe_import_apply_duration()`, `time_import_apply()`
5. WHEN `snapshot()` is called, THE PTF_Metrics SHALL return a dictionary with the same structure as the current implementation by reading values from prometheus_client collectors. `snapshot()` is intended for test/debug purposes only and SHALL NOT be used in production request paths
6. WHEN `reset()` is called, THE PTF_Metrics SHALL clear all metric values using a fresh `prometheus_client.CollectorRegistry` to support test isolation. `reset()` SHALL only be called in test environments
7. THE PTF_Metrics SHALL remain thread-safe by relying on `prometheus_client` built-in thread safety

### Requirement 3: Metrik Kapsam Genişletme

**User Story:** As a DevOps engineer, I want additional metrics for audit history queries and general HTTP request tracking, so that I have full observability over all endpoints.

#### Acceptance Criteria

1. WHEN the `GET /admin/market-prices/history` endpoint processes a request, THE PTF_Metrics SHALL increment a `ptf_admin_history_query_total` counter
2. WHEN the `GET /admin/market-prices/history` endpoint processes a request, THE PTF_Metrics SHALL observe the query duration in a `ptf_admin_history_query_duration_seconds` histogram
3. WHEN any HTTP request is processed by the FastAPI application, THE PTF_Metrics SHALL increment a `ptf_admin_api_request_total` counter with labels `endpoint`, `method`, and `status_class`. THE `status_class` label SHALL be the HTTP status code normalized to class level (e.g. 200→"2xx", 404→"4xx", 500→"5xx"). Exact status codes SHALL NOT be used as Prometheus labels to prevent cardinality explosion; exact codes belong in logs/traces. THE `endpoint` label SHALL be resolved using a 3-level fallback strategy:
   - **Level 1 (preferred):** Route template from `request.scope.get("route").path` (e.g. `/admin/market-prices/{period}`)
   - **Level 2 (no route match):** Sanitized path bucket — first 2 segments preserved, dynamic segments replaced with `*` (e.g. `/admin/market-prices/*`)
   - **Level 3 (404 / unmatched):** `unmatched:{first_2_segments}/*` format (e.g. `unmatched:/admin/market-prices/*`)
   This prevents high-cardinality label explosion from path parameters
4. WHEN any HTTP request is processed by the FastAPI application, THE PTF_Metrics SHALL observe the request duration in a `ptf_admin_api_request_duration_seconds` histogram with label `endpoint` (normalized route template, same rule as 3.3)

### Requirement 4: Frontend Event Tracking Modülü

**User Story:** As a developer, I want a lightweight frontend telemetry module, so that I can track user actions without impacting UI performance.

#### Acceptance Criteria

1. THE Event_Tracker SHALL export a `trackEvent(event: string, properties?: Record<string, unknown>)` function
2. WHEN `trackEvent()` is called, THE Event_Tracker SHALL construct a Telemetry_Event with the provided `event` name, `properties` object, and an ISO 8601 `timestamp`
3. WHEN `trackEvent()` is called, THE Event_Tracker SHALL send the event via a fire-and-forget HTTP POST without blocking the calling code
4. IF the HTTP POST to Event_Ingestion_API fails, THEN THE Event_Tracker SHALL silently log the error to `console.warn` and NOT throw an exception
5. WHEN multiple events are generated in rapid succession, THE Event_Tracker SHALL batch events and send them in a single HTTP POST within a configurable flush interval
6. THE Event_Tracker SHALL enforce a `MAX_BUFFER_SIZE` (default: 200 events). WHEN the buffer exceeds this limit, THE Event_Tracker SHALL drop the oldest events to make room for new ones and log a `console.warn` indicating dropped count
7. IF a flush HTTP POST fails, THE Event_Tracker SHALL discard the failed batch and log a `console.warn`. THE Event_Tracker SHALL NOT retry failed flushes (fire-and-forget semantics)

### Requirement 5: Frontend Telemetri Entegrasyonu

**User Story:** As a product owner, I want user actions tracked across the admin panel, so that I can understand usage patterns.

#### Acceptance Criteria

1. WHEN a user submits the upsert form, THE Event_Tracker SHALL send a `ptf_admin.upsert_submit` event with `period`, `price_type`, and `status` properties
2. WHEN an upsert operation succeeds, THE Event_Tracker SHALL send a `ptf_admin.upsert_success` event with `action` (created/updated) property
3. WHEN an upsert operation fails, THE Event_Tracker SHALL send a `ptf_admin.upsert_error` event with `error_code` property
4. WHEN a user starts a bulk import by uploading a file, THE Event_Tracker SHALL send a `ptf_admin.bulk_import_start` event with `file_type` and `row_count` properties
5. WHEN a bulk import apply completes, THE Event_Tracker SHALL send a `ptf_admin.bulk_import_complete` event with `imported_count`, `skipped_count`, and `error_count` properties
6. WHEN a bulk import fails, THE Event_Tracker SHALL send a `ptf_admin.bulk_import_error` event with `error_code` property
7. WHEN a user opens the history panel for a record, THE Event_Tracker SHALL send a `ptf_admin.history_open` event with `period` and `price_type` properties
8. WHEN a user changes filter values in PriceFilters, THE Event_Tracker SHALL send a `ptf_admin.filter_change` event with the updated filter state

### Requirement 6: Backend Event Ingestion

**User Story:** As a backend developer, I want to receive and store frontend telemetry events, so that user actions are available for analysis.

#### Acceptance Criteria

1. WHEN a `POST /admin/telemetry/events` request is received with a valid JSON body, THE Event_Ingestion_API SHALL accept the events and increment aggregate counters
2. THE Event_Ingestion_API SHALL accept a JSON body containing an `events` array, where each element has `event` (string), `properties` (object), and `timestamp` (ISO 8601 string) fields
3. THE Event_Ingestion_API SHALL NOT require authentication. This is a conscious, scoped exception: the endpoint only increments a counter and performs no other write/read operations. The risk profile is equivalent to `GET /metrics` (also unauthenticated). Event payloads are never stored or read back
4. WHEN the `events` array is empty, THE Event_Ingestion_API SHALL return HTTP 200 with `accepted_count: 0`
5. IF a single event in the batch has invalid structure, THEN THE Event_Ingestion_API SHALL skip that event, increment a `rejected_count`, and continue processing remaining events
6. WHEN events are accepted, THE Event_Ingestion_API SHALL increment per-`event_name` aggregate counters in-memory (counter-only, no event payload storage). THE Event_Ingestion_API SHALL log a single INFO-level aggregate line per request (e.g. `"accepted=5 rejected=1 events={upsert_submit:3, filter_change:2}"`) and SHALL NOT log individual event payloads to prevent PII/secret leakage
7. THE Event_Ingestion_API SHALL expose accepted event counts via a `ptf_admin_frontend_events_total` Prometheus counter with label `event_name`
8. THE Event_Ingestion_API SHALL enforce a maximum batch size of 100 events per request. Batches exceeding this limit SHALL be rejected with HTTP 400
9. THE Event_Ingestion_API SHALL validate `event` names against an allowlist of known event prefixes (`ptf_admin.`). Events with unknown prefixes SHALL be counted in `rejected_count` and skipped
10. THE Event_Ingestion_API SHALL enforce a rate limit of 60 requests per minute per IP address. Requests exceeding this limit SHALL be rejected with HTTP 429. The purpose is spam/inflation mitigation, not security
11. THE Event_Ingestion_API SHALL only increment the `ptf_admin_frontend_events_total` counter. It SHALL NOT perform any other write or read operation. Event payloads SHALL NOT be stored or read back
12. THE Event_Ingestion_API SHALL use only the `event_name` label (from the allowlist) on the Prometheus counter. The `properties` field SHALL NEVER be converted to Prometheus labels. This ensures low cardinality

### Known Limitation: Unauthenticated Telemetry Ingestion

Telemetry ingestion (`POST /admin/telemetry/events`) is unauthenticated due to absence of session/bearer auth in the codebase. The current `X-Admin-Key` mechanism is a static shared secret unsuitable for browser-side use.

This is accepted as a scoped exception because:
- The endpoint only increments a Prometheus counter (counter-only design)
- No payload is stored or read back
- The risk profile is equivalent to `GET /metrics` (also unauthenticated)
- Rate limiting (60 req/min/IP) mitigates spam inflation

**Planned follow-up: Admin Auth Hardening → telemetry auth alignment.** When session/bearer auth is implemented in a future phase, this endpoint will be brought under the same auth umbrella
