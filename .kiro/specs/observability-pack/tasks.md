# Implementation Plan: Observability Pack

## Overview

Mevcut PTF Admin Prometheus metriklerini operasyonel hale getiren üç statik dosyanın (Grafana dashboard JSON, PrometheusRule YAML, Runbook MD) oluşturulması ve yapısal doğrulama testlerinin yazılması.

## Tasks

- [x] 1. Dizin yapısını oluştur ve test altyapısını hazırla
  - `monitoring/grafana/`, `monitoring/prometheus/`, `monitoring/runbooks/`, `monitoring/tests/` dizinlerini oluştur
  - `monitoring/tests/conftest.py` dosyasında dashboard JSON, alert YAML ve runbook MD dosyalarını yükleyen fixture'lar tanımla
  - _Requirements: 5.1, 11.1, 12.5_

- [x] 2. Grafana Dashboard JSON oluştur
  - [x] 2.1 Dashboard iskelet yapısını oluştur (`ptf-admin-dashboard.json`)
    - Üst düzey alanlar: `__inputs`, `title`, `uid`, `tags`, `timezone`, `time`, `refresh`, `templating`
    - Datasource variable: `$datasource` (Prometheus type)
    - Time range: `now-1h` → `now`, refresh: `30s`
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 2.2 Row 1: API Traffic & Health panellerini ekle
    - Request Rate panel (timeseries): `sum(rate(ptf_admin_api_request_total{endpoint!="/metrics"}[5m])) by (endpoint, method, status_class)`
    - Error Rate panel (timeseries): `sum(rate(ptf_admin_api_request_total{status_class=~"4xx|5xx|0xx"}[5m])) by (status_class)`
    - P95 Latency panel (timeseries): `histogram_quantile(0.95, ...)`
    - Self-Exclude Check panel (stat): `sum(rate(ptf_admin_api_request_total{endpoint="/metrics"}[5m]))`
    - Renk kodlaması: 2xx=yeşil, 4xx=sarı, 5xx=kırmızı, 0xx=mor
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 5.5_

  - [x] 2.3 Row 2: Import/Upsert Business Health panellerini ekle
    - Upsert Rate panel: `sum(rate(ptf_admin_upsert_total[5m])) by (status)`
    - Import Rows panel: `sum(rate(ptf_admin_import_rows_total[5m])) by (outcome)`
    - Import Apply P95 panel: `histogram_quantile(0.95, ...)`
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 2.4 Row 3: Lookup / History panellerini ekle
    - Lookup Hit/Miss panel: `sum(rate(ptf_admin_lookup_total[5m])) by (hit, status)`
    - History Query Rate panel: `sum(rate(ptf_admin_history_query_total[5m]))`
    - History Query P95 panel: `histogram_quantile(0.95, ...)`
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 2.5 Row 4: Frontend Telemetry panellerini ekle
    - Top Events panel (bargauge): `topk(20, sum(increase(ptf_admin_frontend_events_total[1h])) by (event_name))`
    - Telemetry Endpoint Health panel (timeseries): `/admin/telemetry/events` request rate + 4xx
    - _Requirements: 4.1, 4.2, 4.3_

- [x] 3. Checkpoint — Dashboard JSON doğrulaması
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. PrometheusRule YAML oluştur
  - [x] 4.1 CRD iskelet yapısını ve S1 (Scrape/Liveness) alert'lerini oluştur (`ptf-admin-alerts.yml`)
    - `apiVersion: monitoring.coreos.com/v1`, `kind: PrometheusRule`
    - Metadata labels: `app: ptf-admin`, `prometheus: kube-prometheus`
    - Rule group: `ptf-admin-alerts`
    - PTFAdminMetricsAbsent: `absent(ptf_admin_api_request_total)` — for: 5m, severity: critical
    - PTFAdminTargetDown: `up{job="ptf-admin"} == 0` — for: 2m, severity: critical
    - Her alert'e `runbook_url`, `summary`, `description` annotation'ları ekle
    - _Requirements: 6.1, 6.2, 6.3, 11.1, 11.2, 11.3, 11.4_

  - [x] 4.2 S2 (Error Spikes) alert'lerini ekle
    - PTFAdmin5xxSpike: 5xx oranı > %5 — for: 5m, severity: warning
    - PTFAdminExceptionPath: 0xx rate > 0 — for: 5m, severity: critical
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 4.3 S3 (Latency Regression) alert'lerini ekle
    - PTFAdminHighLatency: P95 > 2s — for: 5m, severity: warning
    - PTFAdminTelemetryLatency: telemetry P95 > 500ms — for: 5m, severity: warning
    - PTFAdminImportLatency: import P95 > 10s — for: 5m, severity: warning
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [x] 4.4 S4 (Abuse) ve S5 (Import Quality) alert'lerini ekle
    - PTFAdminTelemetryAbuse: telemetry 4xx > 10 req/min — for: 5m, severity: warning
    - PTFAdminImportRejectRatio: reject oranı > %20 — for: 15m, severity: warning
    - _Requirements: 9.1, 9.2, 10.1, 10.2_

- [x] 5. Checkpoint — Alert rules YAML doğrulaması
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Runbook oluştur
  - [x] 6.1 Runbook iskelet yapısını ve S1 alert bölümlerini oluştur (`ptf-admin-runbook.md`)
    - PTFAdminMetricsAbsent: olası nedenler, ilk 3 kontrol, müdahale adımları
    - PTFAdminTargetDown: olası nedenler, ilk 3 kontrol, müdahale adımları
    - _Requirements: 12.1, 12.2, 12.3, 12.4_

  - [x] 6.2 S2–S5 alert bölümlerini ekle
    - PTFAdmin5xxSpike, PTFAdminExceptionPath, PTFAdminHighLatency, PTFAdminTelemetryLatency, PTFAdminImportLatency, PTFAdminTelemetryAbuse, PTFAdminImportRejectRatio bölümleri
    - Her bölümde: severity, PromQL, olası nedenler (≥3), ilk 3 kontrol, müdahale adımları
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

- [x] 7. Yapısal doğrulama testlerini yaz
  - [x] 7.1 Dashboard yapısal testlerini yaz (`monitoring/tests/test_dashboard_structure.py`)
    - Dashboard JSON parse ve required key kontrolü
    - Datasource variable kontrolü, time range ve refresh kontrolü
    - Belirli PromQL sorgularının varlığı (her panel için)
    - _Requirements: 1.2, 1.3, 1.4, 2.2, 2.3, 2.4, 3.2, 3.3, 3.4, 4.2, 4.3, 5.1, 5.2, 5.4_

  - [x] 7.2 Dashboard property testlerini yaz
    - **Property 1: Dashboard Row Panel Sayısı** — her row için minimum panel sayısı kontrolü
    - **Validates: Requirements 1.1, 2.1, 3.1, 4.1**
    - **Property 2: Self-Exclude Doğrulaması** — PromQL'lerde /metrics filtresi kontrolü
    - **Validates: Requirements 1.5**
    - **Property 3: Dashboard JSON Geçerliliği** — required top-level keys kontrolü
    - **Validates: Requirements 5.1**
    - **Property 4: Collapsible Row Yapısı** — tüm row'larda collapsed=true kontrolü
    - **Validates: Requirements 5.3**

  - [x] 7.3 Alert rules yapısal testlerini yaz (`monitoring/tests/test_alert_rules.py`)
    - YAML parse ve CRD yapı kontrolü
    - Her alert için doğru expression, for, severity kontrolü
    - Alert group adı kontrolü
    - _Requirements: 6.1, 6.2, 7.1, 7.2, 8.1, 8.2, 8.3, 9.1, 10.1, 11.1, 11.2_

  - [x] 7.4 Alert rules property testlerini yaz
    - **Property 5: Alert Rule Tamamlığı** — her rule'da required labels + annotations kontrolü
    - **Validates: Requirements 6.3, 7.3, 8.4, 9.2, 10.2, 11.3, 11.4**
    - **Property 6: PrometheusRule CRD Geçerliliği** — apiVersion + kind kontrolü
    - **Validates: Requirements 11.1**

  - [x] 7.5 Runbook kapsam testlerini yaz (`monitoring/tests/test_runbook_coverage.py`)
    - Runbook'ta her alert için bölüm varlığı kontrolü
    - _Requirements: 12.1, 12.5_

  - [x] 7.6 Runbook property testlerini yaz
    - **Property 7: Runbook-Alert Kapsam Eşleştirmesi** — YAML'daki her alert için runbook bölümü kontrolü
    - **Validates: Requirements 12.1, 12.5**
    - **Property 8: Runbook Bölüm Tamamlığı** — her bölümde ≥3 neden, ≥3 kontrol, ≥1 müdahale
    - **Validates: Requirements 12.2, 12.3, 12.4**

- [x] 8. Final checkpoint — Tüm testlerin geçtiğini doğrula
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Her görev spesifik gereksinimlere referans verir (traceability)
- Checkpoint'ler artımlı doğrulama sağlar
- Property testleri evrensel doğruluk özelliklerini, unit testler spesifik örnekleri doğrular
- Tüm dosyalar statik artifact'tir — runtime kod değişikliği gerekmez
