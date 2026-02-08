# Design Document: Deploy Integration (Monitoring Artifacts)

## Overview

Observability Pack'in 3 statik artifact'ini Kubernetes'e deploy edilebilir hale getiren Kustomize tabanlı yapı. Hedef: `kubectl apply -k monitoring/deploy/overlays/production` tek komutuyla tüm monitoring kaynaklarının uygulanması.

## Architecture

```
monitoring/
├── deploy/
│   ├── base/
│   │   ├── kustomization.yaml          # Base kustomization
│   │   ├── dashboard-cm.yaml           # ConfigMap wrapper (labels + annotations)
│   │   └── alerts-patch.yaml           # (opsiyonel) alert label patch
│   ├── overlays/
│   │   └── production/
│   │       ├── kustomization.yaml      # namespace: monitoring + patches
│   │       └── runbook-url-patch.yaml  # runbook_url base override
│   └── README.md                       # Deploy dokümantasyonu
├── grafana/
│   └── ptf-admin-dashboard.json        # (mevcut)
├── prometheus/
│   └── ptf-admin-alerts.yml            # (mevcut)
├── runbooks/
│   └── ptf-admin-runbook.md            # (mevcut)
└── tests/
    ├── test_deploy_structure.py         # Kustomize output validation
    └── ...                              # (mevcut testler)
```

## Components and Interfaces

### Component 1: Kustomize Base (`monitoring/deploy/base/kustomization.yaml`)

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

commonLabels:
  app.kubernetes.io/part-of: ptf-admin-monitoring

resources:
  - ../../prometheus/ptf-admin-alerts.yml

configMapGenerator:
  - name: ptf-admin-dashboard
    files:
      - ptf-admin-dashboard.json=../../grafana/ptf-admin-dashboard.json
    options:
      disableNameSuffixHash: true
      labels:
        grafana_dashboard: "1"
      annotations:
        grafana_folder: "PTF Admin"
```

**Tasarım Kararları:**

| Karar | Seçim | Gerekçe |
|-------|-------|---------|
| configMapGenerator vs statik CM | configMapGenerator | Kustomize native, dosya değişikliğinde hash suffix (opsiyonel) |
| disableNameSuffixHash | true | Grafana sidecar ConfigMap adını sabit bekler |
| commonLabels | `app.kubernetes.io/part-of` | K8s recommended labels standardı |
| Namespace | base'de yok | Overlay'de belirlenir — ortam bağımsızlığı |

### Component 2: Production Overlay (`monitoring/deploy/overlays/production/kustomization.yaml`)

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

namespace: monitoring

resources:
  - ../../base
```

**Genişletilebilirlik:** Staging overlay eklemek için `overlays/staging/` dizini oluşturup `namespace: staging-monitoring` yazmak yeterli.

### Component 3: Dashboard ConfigMap Detayı

Grafana sidecar provisioning modeli:

1. Sidecar container `grafana_dashboard: "1"` label'lı ConfigMap'leri izler
2. ConfigMap'teki JSON dosyasını Grafana'ya provision eder
3. `grafana_folder` annotation'ı ile hedef klasör belirlenir

```
ConfigMap (ptf-admin-dashboard)
├── metadata.labels.grafana_dashboard: "1"
├── metadata.annotations.grafana_folder: "PTF Admin"
└── data:
    └── ptf-admin-dashboard.json: <dashboard JSON content>
```

### Component 4: Runbook URL Yönetimi

Alert annotation'larındaki `runbook_url` değerleri şu formatta:
```
https://github.com/<repo>/blob/main/monitoring/runbooks/ptf-admin-runbook.md#<anchor>
```

Production'da farklı bir base URL gerekirse, overlay'de `runbook-url-patch.yaml` ile PrometheusRule annotation'ları override edilebilir. MVP'de bu patch opsiyoneldir — mevcut `<repo>` placeholder'ı kullanıcı tarafından düzenlenir.

## Deploy Akışı

```
1. kubectl apply -k monitoring/deploy/overlays/production

   → ConfigMap/ptf-admin-dashboard (namespace: monitoring)
     - label: grafana_dashboard=1
     - annotation: grafana_folder=PTF Admin
     - data: ptf-admin-dashboard.json

   → PrometheusRule/ptf-admin-alerts (namespace: monitoring)
     - label: prometheus=kube-prometheus
     - 9 alert rules (S1–S5)

2. Grafana sidecar → ConfigMap detect → dashboard provision → "PTF Admin" folder

3. Prometheus Operator → PrometheusRule detect → rule reload → alerts active
```

## Testing Strategy

### Kustomize Build Validation

`kustomize build` çıktısını parse ederek yapısal doğrulama:

| Test | Doğrulama |
|------|-----------|
| Build başarılı | `kustomize build` exit code 0 |
| ConfigMap varlığı | Kind=ConfigMap, name=ptf-admin-dashboard |
| ConfigMap label | `grafana_dashboard: "1"` |
| ConfigMap annotation | `grafana_folder: PTF Admin` |
| ConfigMap data key | `ptf-admin-dashboard.json` key mevcut |
| PrometheusRule varlığı | Kind=PrometheusRule, name=ptf-admin-alerts |
| Namespace | Tüm kaynaklar `monitoring` namespace'inde |
| commonLabels | `app.kubernetes.io/part-of: ptf-admin-monitoring` tüm kaynaklarda |

### Test Dosyası

```
monitoring/tests/test_deploy_structure.py
```

Kustomize binary'si CI'da mevcut olmalı. Test, `subprocess` ile `kustomize build` çalıştırır ve YAML çıktısını parse eder. Kustomize yoksa test `pytest.skip()` ile atlanır.

## Bilinen Kısıtlamalar

1. **Kustomize bağımlılığı:** CI'da `kustomize` binary'si gerekir. Yoksa deploy testleri skip edilir.
2. **Runbook URL placeholder:** `<repo>` placeholder'ı kullanıcı tarafından düzenlenmelidir. Otomatik değildir.
3. **Grafana sidecar varsayımı:** Dashboard provisioning, Grafana sidecar modeline bağlıdır. API-based provisioning farklı yapı gerektirir.
4. **Tek namespace:** Tüm monitoring kaynakları aynı namespace'e deploy edilir. Cross-namespace senaryosu kapsam dışı.
