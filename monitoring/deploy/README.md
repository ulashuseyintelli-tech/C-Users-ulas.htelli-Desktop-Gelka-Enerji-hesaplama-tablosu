# PTF Admin Monitoring — Deploy Guide

## Ön Koşullar

- Kubernetes cluster (1.24+)
- Prometheus Operator (kube-prometheus-stack)
- Grafana sidecar provisioning aktif (`grafana_dashboard: "1"` label'lı ConfigMap'leri izleyen sidecar)
- `kubectl` (kustomize v5+ dahil)

## Tek Komutla Deploy

```bash
kubectl apply -k monitoring/deploy/overlays/production --load-restrictor=LoadRestrictionsNone
```

Bu komut aşağıdaki kaynakları `monitoring` namespace'ine uygular:

| Kaynak | Tür | Açıklama |
|--------|-----|----------|
| `ptf-admin-dashboard` | ConfigMap | Grafana dashboard JSON — sidecar tarafından otomatik provision edilir |
| `ptf-admin-alerts` | PrometheusRule | 9 alert kuralı (S1–S5) — Prometheus Operator tarafından otomatik yüklenir |

## Overlay Yapısı

```
monitoring/deploy/
├── base/
│   └── kustomization.yaml      # ConfigMap + PrometheusRule
└── overlays/
    └── production/
        └── kustomization.yaml  # namespace: monitoring
```

Yeni ortam eklemek için `overlays/<ortam>/kustomization.yaml` oluşturup namespace'i değiştirmek yeterlidir.

## Dashboard Provisioning

Grafana sidecar, `grafana_dashboard: "1"` label'lı ConfigMap'leri otomatik algılar ve dashboard'u "PTF Admin" klasörüne provision eder.

Dashboard güncellemesi sonrası sidecar'ın değişikliği algılaması birkaç dakika sürebilir. Hızlandırmak için:

```bash
kubectl rollout restart deployment/grafana -n monitoring
```

## Runbook

Alert annotation'larındaki `runbook_url` değerleri repo'daki `monitoring/runbooks/ptf-admin-runbook.md` dosyasına işaret eder. URL'deki `<repo>` placeholder'ını gerçek repo URL'si ile değiştirin.

## Doğrulama

Build çıktısını önizlemek için:

```bash
kubectl kustomize monitoring/deploy/overlays/production --load-restrictor=LoadRestrictionsNone
```

## Notlar

- `--load-restrictor=LoadRestrictionsNone` flag'ı gereklidir çünkü kustomize base, `monitoring/grafana/` ve `monitoring/prometheus/` dizinlerindeki dosyalara referans verir.
- `disableNameSuffixHash: true` ayarı ConfigMap adını sabit tutar — Grafana sidecar sabit isim bekler.
