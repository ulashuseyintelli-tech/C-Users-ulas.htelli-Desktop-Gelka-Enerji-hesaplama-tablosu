# Implementation Plan: Deploy Integration (Monitoring Artifacts)

## Overview

Observability Pack artifact'lerini Kubernetes'e deploy edilebilir hale getiren Kustomize yapısının oluşturulması, doğrulama testlerinin yazılması ve dokümantasyon.

## Tasks

- [x] 1. Kustomize base yapısını oluştur
  - `monitoring/deploy/base/kustomization.yaml` dosyasını oluştur
  - `configMapGenerator` ile dashboard JSON'u ConfigMap'e sar
  - `resources` listesine PrometheusRule YAML'ı ekle
  - `labels` ile `app.kubernetes.io/part-of: ptf-admin-monitoring` ekle (kustomize v5 uyumlu)
  - ConfigMap label: `grafana_dashboard: "1"`, annotation: `grafana_folder: "PTF Admin"`
  - _Requirements: 1.1, 2.1, 2.2, 3.1, 5.1_

- [x] 2. Production overlay oluştur
  - `monitoring/deploy/overlays/production/kustomization.yaml` dosyasını oluştur
  - `namespace: monitoring` ayarla
  - Base'i referans et (`../../base`)
  - _Requirements: 1.2_

- [x] 3. Checkpoint — Kustomize build doğrulaması
  - `kubectl kustomize --load-restrictor=LoadRestrictionsNone` exit code 0
  - Çıktıda ConfigMap (ptf-admin-dashboard) ve PrometheusRule (ptf-admin-alerts) mevcut
  - Not: `--load-restrictor=LoadRestrictionsNone` gerekli — base dışı dosya referansları için

- [x] 4. Deploy yapısal testlerini yaz
  - `monitoring/tests/test_deploy_structure.py` — 12 test
  - ConfigMap varlığı, label, annotation, data key, namespace kontrolü
  - PrometheusRule varlığı, namespace, commonLabel, prometheus label kontrolü
  - Kustomize yoksa `pytest.skip()` ile atlanır
  - _Requirements: 6.1, 6.2_

- [x] 5. Deploy README dokümantasyonu
  - `monitoring/deploy/README.md` — ön koşullar, tek komutla deploy, overlay, provisioning, doğrulama
  - _Requirements: 7.1_

- [x] 6. Final checkpoint — Tüm testlerin geçtiğini doğrula
  - Monitoring: 94 passed
  - Backend: 1084 passed, 5 skipped, 2 flaky (Hypothesis non-deterministic — pre-existing, deploy ile ilgisiz)

## Notes

- Kustomize binary'si CI'da mevcut olmalı; yoksa deploy testleri skip edilir
- Runbook URL'deki `<repo>` placeholder'ı kullanıcı tarafından düzenlenmelidir
- Staging overlay eklemek için `overlays/staging/` dizini oluşturmak yeterlidir
