# Requirements: Deploy Integration (Monitoring Artifacts)

## Overview

Observability Pack'te üretilen 3 statik artifact'in (Grafana dashboard JSON, PrometheusRule YAML, Runbook MD) Kubernetes ortamına deploy edilebilir hale getirilmesi. Kustomize tabanlı yapı ile `kubectl apply -k` tek komutla tüm monitoring stack'inin uygulanması hedeflenir.

## Bağımlılıklar

- Observability Pack (kilitli): `monitoring/grafana/ptf-admin-dashboard.json`, `monitoring/prometheus/ptf-admin-alerts.yml`, `monitoring/runbooks/ptf-admin-runbook.md`
- Hedef ortam: Kubernetes + Prometheus Operator (kube-prometheus-stack) + Grafana (sidecar provisioning)

---

## Requirement 1: Kustomize Base Yapısı

### 1.1
As a platform engineer, I want a `monitoring/deploy/base/kustomization.yaml` file so that all monitoring resources can be applied with a single `kubectl apply -k` command.

**Acceptance Criteria:**
- `kustomization.yaml` dosyası `resources` listesinde tüm monitoring K8s manifest'lerini referans eder
- `commonLabels` ile `app.kubernetes.io/part-of: ptf-admin-monitoring` etiketi eklenir
- `namespace` alanı tanımlı değildir (overlay'de belirlenir)

### 1.2
As a platform engineer, I want a namespace-aware overlay structure so that monitoring resources can be deployed to different namespaces (e.g., `monitoring`, `staging-monitoring`).

**Acceptance Criteria:**
- `monitoring/deploy/overlays/production/kustomization.yaml` dosyası `namespace: monitoring` ile base'i referans eder
- Overlay yapısı ek ortamlar (staging vb.) için genişletilebilir

---

## Requirement 2: Grafana Dashboard ConfigMap

### 2.1
As a platform engineer, I want the Grafana dashboard JSON to be wrapped in a ConfigMap so that Grafana sidecar can auto-discover and provision it.

**Acceptance Criteria:**
- ConfigMap adı: `ptf-admin-dashboard`
- ConfigMap label'ı: `grafana_dashboard: "1"` (Grafana sidecar discovery label'ı)
- Dashboard JSON, ConfigMap'in `data` alanında `ptf-admin-dashboard.json` key'i altında yer alır

### 2.2
As a platform engineer, I want the dashboard ConfigMap to include folder annotation so that the dashboard appears in the correct Grafana folder.

**Acceptance Criteria:**
- Annotation: `grafana_folder: "PTF Admin"` — dashboard Grafana'da "PTF Admin" klasöründe görünür

---

## Requirement 3: PrometheusRule Deployment

### 3.1
As a platform engineer, I want the PrometheusRule YAML to be directly deployable via kustomize so that Prometheus Operator picks it up automatically.

**Acceptance Criteria:**
- `ptf-admin-alerts.yml` kustomize resources listesinde yer alır
- PrometheusRule metadata label'ları Prometheus Operator'ın selector'ına uyar (`prometheus: kube-prometheus`)

### 3.2
As a platform engineer, I want the alert rules to reference the correct runbook URL base so that on-call engineers can reach the runbook from alert notifications.

**Acceptance Criteria:**
- Runbook URL base'i kustomize overlay'de configurable olmalı (varsayılan: repo GitHub URL)
- Production overlay'de `RUNBOOK_BASE_URL` patch'i uygulanabilir

---

## Requirement 4: Runbook Erişilebilirliği

### 4.1
As a platform engineer, I want the runbook to be accessible via a stable URL so that alert annotations can link to it.

**Acceptance Criteria:**
- Runbook dosyası repo'da `monitoring/runbooks/ptf-admin-runbook.md` yolunda kalır (zaten mevcut)
- Alert annotation'larındaki `runbook_url` değerleri bu dosyaya işaret eder
- README veya deploy docs'ta runbook erişim yolu belgelenir

---

## Requirement 5: ConfigMap Generator (Dashboard)

### 5.1
As a platform engineer, I want kustomize to use `configMapGenerator` so that dashboard ConfigMap'i her değişiklikte hash suffix alır ve rolling update tetikler.

**Acceptance Criteria:**
- `configMapGenerator` kullanılır (statik ConfigMap yerine)
- Generator `behavior: create` ile çalışır
- Label'lar `generatorOptions` veya overlay ile eklenir

---

## Requirement 6: Yapısal Doğrulama

### 6.1
As a developer, I want `kustomize build` output'unun geçerli YAML ürettiğini doğrulayan testler so that CI'da deploy artifact'leri validate edilir.

**Acceptance Criteria:**
- `kustomize build monitoring/deploy/overlays/production` komutu hatasız çalışır
- Çıktıda ConfigMap ve PrometheusRule kaynakları bulunur
- ConfigMap'te `grafana_dashboard: "1"` label'ı mevcuttur

### 6.2
As a developer, I want the deploy structure to be validated by pytest so that regressions are caught early.

**Acceptance Criteria:**
- `monitoring/tests/test_deploy_structure.py` dosyası kustomize output'unu parse eder
- ConfigMap key kontrolü, PrometheusRule varlığı, label kontrolü testleri bulunur

---

## Requirement 7: Dokümantasyon

### 7.1
As a platform engineer, I want a deploy README so that the deployment process is documented.

**Acceptance Criteria:**
- `monitoring/deploy/README.md` dosyası oluşturulur
- İçerik: ön koşullar, tek komutla deploy, overlay kullanımı, dashboard provisioning açıklaması
