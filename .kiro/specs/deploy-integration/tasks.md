# Implementation Plan: Deploy Integration (Monitoring Artifacts)

## Overview

Observability Pack'in 3 statik artifact'ini (Grafana dashboard JSON, PrometheusRule YAML, Runbook MD) Kustomize ile Kubernetes'e deploy edilebilir hale getirmek. Hedef: `kubectl apply -k monitoring/deploy/overlays/production` tek komutuyla tüm monitoring kaynaklarının uygulanması.

## Scope / Non-goals

**Scope:** Kustomize ile observability-pack çıktılarının deploy edilebilir hale getirilmesi — ConfigMap dashboards + PrometheusRule alerts + runbook referansları + CI doğrulama.

**Non-goals:** Mevcut PTFMetrics, MetricsMiddleware, mevcut alert rule semantiğini değiştirmek yok. Sadece deploy wiring.

## Kritik Kararlar

- `disableNameSuffixHash: true` — Grafana sidecar pickup stabil isim bekler
- Route/label cardinality policy değiştirilmeyecek
- PTFMetrics ve app koduna dokunmak yok
- Hedef namespace: `monitoring` (production overlay'de)
- Prometheus label standardı: `prometheus: kube-prometheus`
- `--load-restrictor=LoadRestrictionsNone` zorunlu (base → `../../grafana/` referansı)

## Tasks

- [x] 1. Dizin yapısı ve Kustomize base oluştur
  - [x] 1.1 `monitoring/deploy/base/` ve `monitoring/deploy/overlays/production/` dizinlerini oluştur
  - [x] 1.2 `monitoring/deploy/base/kustomization.yaml` yaz:
    - `labels` ile `app.kubernetes.io/part-of: ptf-admin-monitoring` (Kustomize v5+ `labels` + `pairs` syntax)
    - `resources`: `../../prometheus/ptf-admin-alerts.yml`
    - `configMapGenerator`: dashboard JSON → ConfigMap
    - Namespace base'de tanımlı değil (overlay'de belirlenir)
  - _Requirements: 1.1, 5.1_
  - **DoD:** `kustomize build monitoring/deploy/base --load-restrictor=LoadRestrictionsNone` hatasız çalışır
  - **Rollback:** `monitoring/deploy/base/` dizinini sil

- [x] 2. Grafana Dashboard ConfigMap wiring
  - [x] 2.1 `configMapGenerator` ile ConfigMap üret:
    - İsim: `ptf-admin-dashboard`
    - `options.disableNameSuffixHash: true`
    - Label: `grafana_dashboard: "1"` (sidecar discovery)
    - Annotation: `grafana_folder: "PTF Admin"` (klasörleme)
    - Data key: `ptf-admin-dashboard.json` → `../../grafana/ptf-admin-dashboard.json`
  - _Requirements: 2.1, 2.2, 5.1_
  - **DoD:** `kustomize build` çıktısında ConfigMap var; data key'i JSON dosyasıyla eşleşiyor; label/annotation doğru
  - **Rollback:** `configMapGenerator` bloğunu kustomization.yaml'dan kaldır

- [x] 3. PrometheusRule wiring
  - [x] 3.1 `ptf-admin-alerts.yml` kustomize resources listesinde
  - [x] 3.2 PrometheusRule metadata label'ları korunuyor: `prometheus: kube-prometheus`, `app: ptf-admin`
  - [x] 3.3 `commonLabels` / `labels` ile `app.kubernetes.io/part-of` ekleniyor
  - _Requirements: 3.1, 3.2_
  - **DoD:** `kustomize build` çıktısında PrometheusRule var; `prometheus: kube-prometheus` label'ı korunmuş; 9 alert rule mevcut
  - **Rollback:** `resources` listesinden alerts referansını kaldır

- [x] 4. Production overlay oluştur
  - [x] 4.1 `monitoring/deploy/overlays/production/kustomization.yaml`:
    - `namespace: monitoring`
    - `resources: [../../base]`
  - _Requirements: 1.2_
  - **DoD:** `kustomize build monitoring/deploy/overlays/production --load-restrictor=LoadRestrictionsNone` hatasız; tüm kaynaklar `monitoring` namespace'inde
  - **Rollback:** `monitoring/deploy/overlays/production/` dizinini sil

- [x] 5. Checkpoint — Kustomize build doğrulaması
  - [x] 5.1 `kustomize build overlays/production` çıktısını doğrula:
    - ConfigMap `ptf-admin-dashboard` mevcut
    - PrometheusRule `ptf-admin-alerts` mevcut
    - Namespace: `monitoring`
    - Labels: `app.kubernetes.io/part-of`, `grafana_dashboard`, `prometheus`
  - **DoD:** Manuel veya CI'da `kustomize build` hatasız

- [x] 6. Yapısal doğrulama testleri yaz (`monitoring/tests/test_deploy_structure.py`)
  - [x] 6.1 Kustomize build çıktısını parse eden pytest testleri:
    - Build en az 2 document üretiyor (ConfigMap + PrometheusRule)
    - ConfigMap: `grafana_dashboard: "1"` label, `grafana_folder: "PTF Admin"` annotation, `ptf-admin-dashboard.json` data key
    - PrometheusRule: `prometheus: kube-prometheus` label, 9 alert rule
    - Tüm kaynaklar `monitoring` namespace'inde
    - `app.kubernetes.io/part-of: ptf-admin-monitoring` label tüm kaynaklarda
  - [x] 6.2 Kustomize yoksa `pytest.skip()` ile atla (CI esnekliği)
  - _Requirements: 6.1, 6.2_
  - **DoD:** `pytest monitoring/tests/test_deploy_structure.py` yeşil (kustomize varsa); kustomize yoksa skip
  - **Rollback:** Test dosyasını sil

- [x] 7. Deploy dokümantasyonu (`monitoring/deploy/README.md`)
  - [x] 7.1 README içeriği:
    - Ön koşullar (K8s, Prometheus Operator, Grafana sidecar)
    - Tek komutla deploy (`kubectl apply -k`)
    - Overlay yapısı açıklaması
    - Dashboard provisioning akışı
    - Runbook erişim yolu
    - `--load-restrictor` notu
    - `disableNameSuffixHash` notu
  - _Requirements: 7.1_
  - **DoD:** README mevcut ve güncel
  - **Rollback:** README'yi sil

- [x] 8. Runbook URL linkleme doğrulaması
  - [x] 8.1 PrometheusRule'daki her alert'in `runbook_url` annotation'ı `monitoring/runbooks/ptf-admin-runbook.md#<anchor>` formatında
  - [x] 8.2 Her `runbook_url` anchor'ı runbook'taki gerçek heading ile eşleşiyor
  - [x] 8.3 Bu doğrulamayı test olarak ekle (`test_runbook_coverage.py::TestRunbookUrlAnchors`)
  - _Requirements: 3.2, 4.1_
  - **DoD:** Test yeşil — her alert'in runbook_url'i geçerli anchor'a işaret ediyor
  - **Rollback:** Eklenen testi sil

- [x] 9. Deploy property testleri (PBT) — `monitoring/tests/test_deploy_properties.py`
  - [x] 9.1 Kustomize output üzerinde property testleri:
    - **Property 1: Resource Completeness** — build çıktısında en az 1 ConfigMap + 1 PrometheusRule
    - **Property 2: Namespace Consistency** — tüm kaynaklar aynı namespace'te
    - **Property 3: Label Propagation** — `app.kubernetes.io/part-of` tüm kaynaklarda mevcut
    - **Property 4: ConfigMap Stability** — ConfigMap adı hash suffix içermiyor (`ptf-admin-dashboard` exact match)
  - _Requirements: 6.1, 6.2_
  - **DoD:** PBT'ler yeşil, flaky yok
  - **Rollback:** PBT dosyasını sil

- [x] 10. Final checkpoint — deploy-integration DONE
  - [x] 10.1 Tüm testler yeşil: 127 monitoring tests / 0 fail
  - [x] 10.2 `kustomize build overlays/production` çıktı özeti:
    - ConfigMap sayısı / isimleri
    - PrometheusRule sayısı / isimleri
    - Resource isimleri stable ve deterministik
  - **DoD:** Tüm testler yeşil, checkpoint raporu tasks.md'de

## Notes

- Task 1–7 zaten tamamlanmış durumda (mevcut repo'da dosyalar mevcut ve testler geçiyor)
- Task 8–10 kalan işler: runbook URL doğrulaması, PBT'ler, final checkpoint
- Kustomize binary CI'da mevcut olmalı; yoksa deploy testleri `pytest.skip()` ile atlanır
- `--load-restrictor=LoadRestrictionsNone` flag'ı zorunlu (base → `../../grafana/` referansı)
- Tüm dosyalar statik artifact — runtime kod değişikliği gerekmez
