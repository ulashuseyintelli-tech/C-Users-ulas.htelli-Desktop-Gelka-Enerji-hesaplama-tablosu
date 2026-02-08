# Requirements Document

## Introduction

Mevcut PTF Admin backend'inde üretim ortamında çalışan Prometheus metrikleri (`ptf_admin_*` prefix, `GET /metrics` endpoint) bulunmaktadır. Bu özellik, bu metrikleri operasyonel olarak kullanılabilir hale getirmek için Grafana dashboard'ları (JSON), Prometheus alert kuralları (YAML) ve runbook notları oluşturmayı kapsar.

Hedef ortam: Prometheus Operator (kube-prometheus-stack) + Grafana. Çıktılar, ConfigMap veya PrometheusRule CRD olarak uygulanabilecek statik JSON/YAML dosyalarıdır.

## Glossary

- **Dashboard**: Grafana JSON formatında tanımlanan, metrik panellerini içeren görselleştirme dosyası
- **Alert_Rule**: PrometheusRule CRD formatında tanımlanan, metrik eşik ihlallerinde tetiklenen alarm kuralı
- **Runbook**: Her alarm için olası nedenler, ilk kontrol adımları ve müdahale yöntemlerini içeren operasyonel rehber
- **Panel**: Dashboard içindeki tek bir grafik veya tablo bileşeni
- **PromQL**: Prometheus Query Language — metrik sorgulama dili
- **PrometheusRule_CRD**: Prometheus Operator tarafından yönetilen Kubernetes Custom Resource Definition formatı
- **Status_Class**: HTTP durum kodlarının sınıf seviyesinde normalizasyonu ("2xx", "3xx", "4xx", "5xx", "0xx")
- **Self_Exclude**: `/metrics` endpoint'inin middleware metriklerinden hariç tutulması mekanizması

## Requirements

### Requirement 1: API Traffic & Health Dashboard Bölümü

**User Story:** As a DevOps engineer, I want to visualize API traffic patterns and error rates on a Grafana dashboard, so that I can quickly identify service health issues.

#### Acceptance Criteria

1. THE Dashboard SHALL contain an "API Traffic & Health" bölümü (row) içinde en az 4 panel
2. WHEN the Dashboard is loaded, THE Dashboard SHALL display a request rate panel showing `sum(rate(ptf_admin_api_request_total[5m])) by (endpoint, method, status_class)` PromQL sorgusu
3. WHEN the Dashboard is loaded, THE Dashboard SHALL display an error rate panel showing 4xx, 5xx ve 0xx Status_Class değerlerini ayrı seriler olarak gösteren PromQL sorgusu
4. WHEN the Dashboard is loaded, THE Dashboard SHALL display a P95 latency panel showing `histogram_quantile(0.95, sum(rate(ptf_admin_api_request_duration_seconds_bucket[5m])) by (le, endpoint))` PromQL sorgusu
5. THE Dashboard SHALL verify Self_Exclude by ensuring the `/metrics` endpoint does not appear in any panel (or shows 0 value)

### Requirement 2: Import/Upsert Business Health Dashboard Bölümü

**User Story:** As a DevOps engineer, I want to monitor import and upsert business metrics, so that I can detect data quality issues early.

#### Acceptance Criteria

1. THE Dashboard SHALL contain an "Import/Upsert Business Health" bölümü (row) içinde en az 3 panel
2. WHEN the Dashboard is loaded, THE Dashboard SHALL display an upsert rate panel showing `sum(rate(ptf_admin_upsert_total[5m])) by (status)` PromQL sorgusu ile "provisional" ve "final" ayrımı
3. WHEN the Dashboard is loaded, THE Dashboard SHALL display an import rows panel showing `sum(rate(ptf_admin_import_rows_total[5m])) by (outcome)` PromQL sorgusu ile "accepted" ve "rejected" ayrımı
4. WHEN the Dashboard is loaded, THE Dashboard SHALL display an import apply duration P95 panel showing `histogram_quantile(0.95, sum(rate(ptf_admin_import_apply_duration_seconds_bucket[5m])) by (le))` PromQL sorgusu

### Requirement 3: Lookup / History Dashboard Bölümü

**User Story:** As a DevOps engineer, I want to monitor lookup cache performance and history query patterns, so that I can optimize data access paths.

#### Acceptance Criteria

1. THE Dashboard SHALL contain a "Lookup / History" bölümü (row) içinde en az 3 panel
2. WHEN the Dashboard is loaded, THE Dashboard SHALL display a lookup hit/miss rate panel showing `sum(rate(ptf_admin_lookup_total[5m])) by (hit, status)` PromQL sorgusu
3. WHEN the Dashboard is loaded, THE Dashboard SHALL display a history query rate panel showing `sum(rate(ptf_admin_history_query_total[5m]))` PromQL sorgusu
4. WHEN the Dashboard is loaded, THE Dashboard SHALL display a history query duration P95 panel showing `histogram_quantile(0.95, sum(rate(ptf_admin_history_query_duration_seconds_bucket[5m])) by (le))` PromQL sorgusu

### Requirement 4: Frontend Telemetry Dashboard Bölümü

**User Story:** As a product owner, I want to see frontend event patterns and telemetry endpoint health, so that I can understand user behavior and detect abuse.

#### Acceptance Criteria

1. THE Dashboard SHALL contain a "Frontend Telemetry" bölümü (row) içinde en az 2 panel
2. WHEN the Dashboard is loaded, THE Dashboard SHALL display a top events panel showing `topk(20, sum(increase(ptf_admin_frontend_events_total[1h])) by (event_name))` PromQL sorgusu
3. WHEN the Dashboard is loaded, THE Dashboard SHALL display an event ingest endpoint health panel showing `/admin/telemetry/events` endpoint'ine ait request rate ve 4xx Status_Class oranını

### Requirement 5: Dashboard Genel Yapılandırma

**User Story:** As a DevOps engineer, I want the dashboard to be provisioning-ready and follow Grafana best practices, so that I can import it without manual configuration.

#### Acceptance Criteria

1. THE Dashboard SHALL be a valid Grafana dashboard JSON file that can be imported via Grafana UI or provisioned via ConfigMap
2. THE Dashboard SHALL use a configurable `datasource` variable (Prometheus type) so that the dashboard works across different Grafana installations
3. THE Dashboard SHALL use row-based layout with collapsible sections for each metric category
4. THE Dashboard SHALL include a dashboard-level time range default of "Last 1 hour" with auto-refresh interval of 30 seconds
5. THE Dashboard SHALL use consistent color coding: green for success (2xx), yellow for client errors (4xx), red for server errors (5xx), purple for exception path (0xx)

### Requirement 6: Scrape/Liveness Alert Kuralı (S1)

**User Story:** As a DevOps engineer, I want to be alerted when the application stops producing metrics, so that I can respond to outages immediately.

#### Acceptance Criteria

1. WHEN `ptf_admin_api_request_total` metric is absent for 5 minutes, THE Alert_Rule SHALL fire a "PTFAdminMetricsAbsent" critical severity alert
2. WHEN the Prometheus `up` metric for the PTF Admin target equals 0 for 2 minutes, THE Alert_Rule SHALL fire a "PTFAdminTargetDown" critical severity alert
3. THE Alert_Rule SHALL include `runbook_url` annotation pointing to the corresponding Runbook section

### Requirement 7: Error Spike Alert Kuralı (S2)

**User Story:** As a DevOps engineer, I want to be alerted on sudden increases in server errors, so that I can investigate and mitigate production issues.

#### Acceptance Criteria

1. WHEN the rate of 5xx responses exceeds 5% of total requests over a 5-minute window, THE Alert_Rule SHALL fire a "PTFAdmin5xxSpike" warning severity alert
2. WHEN any 0xx (exception path) response is observed (rate > 0 over 5 minutes), THE Alert_Rule SHALL fire a "PTFAdminExceptionPath" critical severity alert
3. THE Alert_Rule SHALL include `runbook_url` annotation pointing to the corresponding Runbook section

### Requirement 8: Latency Regression Alert Kuralı (S3)

**User Story:** As a DevOps engineer, I want to be alerted when API latency exceeds acceptable thresholds, so that I can investigate performance regressions.

#### Acceptance Criteria

1. WHEN the P95 latency of any endpoint exceeds 2 seconds over a 5-minute window, THE Alert_Rule SHALL fire a "PTFAdminHighLatency" warning severity alert
2. WHEN the P95 latency of the `/admin/telemetry/events` endpoint exceeds 500 milliseconds over a 5-minute window, THE Alert_Rule SHALL fire a "PTFAdminTelemetryLatency" warning severity alert
3. WHEN the P95 latency of import apply operations exceeds 10 seconds over a 5-minute window, THE Alert_Rule SHALL fire a "PTFAdminImportLatency" warning severity alert
4. THE Alert_Rule SHALL include `runbook_url` annotation pointing to the corresponding Runbook section

### Requirement 9: Abuse/Rate Limit Alert Kuralı (S4)

**User Story:** As a DevOps engineer, I want to be alerted on potential abuse of the telemetry endpoint, so that I can take protective measures.

#### Acceptance Criteria

1. WHEN the rate of 4xx responses on the `/admin/telemetry/events` endpoint exceeds 10 requests per minute over a 5-minute window, THE Alert_Rule SHALL fire a "PTFAdminTelemetryAbuse" warning severity alert
2. THE Alert_Rule SHALL include `runbook_url` annotation pointing to the corresponding Runbook section

### Requirement 10: Import Quality Alert Kuralı (S5)

**User Story:** As a DevOps engineer, I want to be alerted when import rejection rates are abnormally high, so that I can investigate data quality issues.

#### Acceptance Criteria

1. WHEN the ratio of rejected import rows to total import rows exceeds 20% over a 15-minute window, THE Alert_Rule SHALL fire a "PTFAdminImportRejectRatio" warning severity alert
2. THE Alert_Rule SHALL include `runbook_url` annotation pointing to the corresponding Runbook section

### Requirement 11: Alert Kuralları Genel Yapılandırma

**User Story:** As a DevOps engineer, I want alert rules in Prometheus Operator CRD format, so that I can deploy them via kubectl apply.

#### Acceptance Criteria

1. THE Alert_Rule dosyası SHALL be a valid PrometheusRule_CRD YAML file with `apiVersion: monitoring.coreos.com/v1` and `kind: PrometheusRule`
2. THE Alert_Rule dosyası SHALL group all rules under a single `rules` group named `ptf-admin-alerts`
3. THE Alert_Rule dosyası SHALL include standard labels: `severity` (critical/warning), `team`, and `service` on each alert
4. THE Alert_Rule dosyası SHALL include annotations: `summary`, `description`, and `runbook_url` on each alert

### Requirement 12: Runbook Dokümantasyonu

**User Story:** As an on-call engineer, I want a runbook with per-alert troubleshooting guides, so that I can quickly diagnose and resolve issues.

#### Acceptance Criteria

1. THE Runbook SHALL contain a section for each Alert_Rule defined in the alert rules file
2. WHEN an alert fires, THE Runbook section for that alert SHALL list probable causes (en az 3 madde)
3. WHEN an alert fires, THE Runbook section for that alert SHALL list first 3 diagnostic checks (logs, recent deploys, DB health, upstream dependencies)
4. WHEN an alert fires, THE Runbook section for that alert SHALL list mitigation steps with concrete commands or actions
5. THE Runbook SHALL be a Markdown file that can be linked from alert annotations via `runbook_url`
