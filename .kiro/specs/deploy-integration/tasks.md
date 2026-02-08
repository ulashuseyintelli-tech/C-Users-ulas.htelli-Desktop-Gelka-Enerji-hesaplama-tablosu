# Implementation Plan: Deploy Integration (Monitoring Artifacts)

## Overview

Observability Pack artifact'lerini Kubernetes'e deploy edilebilir hale getiren Kustomize yapısının oluşturulması, doğrulama testlerinin yazılması ve dokümantasyon.

## Tasks

- [ ] 1. Kustomize base yapısını oluştur
  - `monitoring/deploy/base/kustomization.yaml` dosyasını oluştur
  - `configMapGenerator` ile dashboard JSON'u ConfigMap'e sar
  - `resources` listesine PrometheusRule YAML'ı ekle
  - `commonLabels` ile `app.kubernetes.io/part-of: ptf-admin-monitoring` ekle
  - ConfigMap label: `grafana_dashboard: "1"`, annotation: `grafana_folder: "PTF Admin"`
  - _Requirements: 1.1, 2.1, 2.2, 3.1, 5.1_

- [ ] 2. Production overlay oluştur
  - `monitoring/deploy/overlays/production/kustomization.yaml` dosyasını oluştur
  - `namespace: monitoring` ayarla
  - Base'i referans et (`../../base`)
  - _Requirements: 1.2_

- [ ] 3. Checkpoint — Kustomize build doğrulaması
  - `kustomize build monitoring/deploy/overlays/production` komutunun hatasız çalıştığını doğrula
  - Çıktıda ConfigMap ve PrometheusRule kaynaklarının bulunduğunu kontrol et
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Deploy yapısal testlerini yaz
  - `monitoring/tests/test_deploy_structure.py` dosyasını oluştur
  - Kustomize build output'unu parse eden testler yaz
  - ConfigMap varlığı, label, annotation, data key kontrolü
  - PrometheusRule varlığı, namespace kontrolü
  - commonLabels kontrolü
  - Kustomize yoksa `pytest.skip()` ile atla
  - _Requirements: 6.1, 6.2_

- [ ] 5. Deploy README dokümantasyonu
  - `monitoring/deploy/README.md` dosyasını oluştur
  - Ön koşullar, tek komutla deploy, overlay kullanımı, dashboard provisioning açıklaması
  - _Requirements: 7.1_

- [ ] 6. Final checkpoint — Tüm testlerin geçtiğini doğrula
  - Monitoring testleri + backend testleri yeşil
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Kustomize binary'si CI'da mevcut olmalı; yoksa deploy testleri skip edilir
- Runbook URL'deki `<repo>` placeholder'ı kullanıcı tarafından düzenlenmelidir
- Staging overlay eklemek için `overlays/staging/` dizini oluşturmak yeterlidir
