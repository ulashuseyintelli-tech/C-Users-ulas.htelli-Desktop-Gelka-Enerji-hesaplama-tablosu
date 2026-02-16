# Uygulama Planı: Release Gate Telemetry

## Genel Bakış

ReleaseGate enforcement hook'una gözlemlenebilirlik katmanı eklenir: GateMetricStore (3 counter), ReleaseGate entegrasyonu, Prometheus alert kuralları, Grafana dashboard ve runbook güncellemesi. Mevcut preflight_metrics.py deseni takip edilir.

## Görevler

- [-] 1. GateMetricStore implementasyonu
  - [x] 1.1 `backend/app/testing/gate_metrics.py` dosyasını oluştur — GateMetricStore sınıfı
    - Sabit label kümeleri tanımla: `_DECISION_LABELS`, `_REASON_LABELS`, `_BREACH_KINDS`
    - Thread-safe counter'lar: `_decision_counts`, `_reason_counts`, `_breach_counts`, `_audit_write_failures`, `_metric_write_failures`
    - `record_decision(allowed, reasons)`: decision ve reason sayaçlarını artır, geçersiz reason'ları sessizce atla
    - `record_breach()`: NO_OVERRIDE sayacını artır
    - `record_audit_write_failure()`: audit yazım hatası sayacını artır
    - `record_metric_write_failure()`: dahili metrik yazım hatası sayacını artır
    - Okuma metotları: `decision_counts()`, `reason_counts()`, `breach_counts()`, `audit_write_failures()`, `metric_write_failures()`
    - Store metadata: `_store_generation`, `_store_start_timestamp`
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 3.1, 5.1, 5.2, 9.1, 9.2, 9.3, 9.4_

  - [x] 1.2 JSON persistence ekle — `to_dict()`, `from_dict()`, `save_to_dir()`, `load_from_dir()`
    - `to_dict()`: tüm counter'ları ve metadata'yı JSON-serializable dict'e dönüştür
    - `from_dict(data)`: dict'ten GateMetricStore oluştur
    - `save_to_dir(path)`: atomik yazım (temp → rename), başarısızlıkta metric_write_failures artır
    - `load_from_dir(path)`: fail-open, bozuk dosya → yeni store
    - `_atomic_write` yardımcı fonksiyonu (preflight_metrics.py'deki deseni takip et)
    - _Requirements: 5.3, 5.5_

  - [x] 1.3 GateMetricExporter ekle — `export_prometheus()`, `export_json()`
    - `export_prometheus(store)`: deterministik Prometheus text exposition çıktısı
    - Metrik isimleri: `release_gate_decision_total`, `release_gate_reason_total`, `release_gate_contract_breach_total`, `release_gate_audit_write_failures_total`, `release_gate_metric_write_failures_total`
    - `export_json(store)`: JSON formatında export (sort_keys=True)
    - _Requirements: 5.4_

  - [x] 1.4 GateMetricStore unit testleri yaz
    - Sıfır başlangıç değerleri kontrolü
    - record_decision ALLOW/DENY doğru artırma
    - record_decision geçersiz reason sessizce atlanması
    - record_breach NO_OVERRIDE artırma
    - record_audit_write_failure artırma
    - save_to_dir / load_from_dir başarılı senaryo
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 3.1, 3.2_

  - [ ]* 1.5 Property test: Karar sayacı doğru artırılır (Property 1)
    - **Property 1: Karar sayacı doğru artırılır**
    - **Validates: Requirements 1.1, 1.4, 1.5, 6.2**

  - [ ]* 1.6 Property test: Etiket kardinalitesi sınırlıdır (Property 4)
    - **Property 4: Etiket kardinalitesi sınırlıdır (bounded label invariant)**
    - **Validates: Requirements 1.2, 1.3, 2.2, 9.1, 9.2, 9.3**

  - [ ]* 1.7 Property test: JSON persistence round-trip (Property 6)
    - **Property 6: JSON persistence round-trip**
    - **Validates: Requirements 5.3**

  - [ ]* 1.8 Property test: Prometheus export determinizmi (Property 7)
    - **Property 7: Prometheus export determinizmi**
    - **Validates: Requirements 5.4**

- [-] 2. ReleaseGate entegrasyonu
  - [x] 2.1 `backend/app/testing/release_gate.py` — metric_store parametresi ve emit metotları ekle
    - `__init__` parametresine `metric_store: GateMetricStore | None = None` ekle
    - `_emit_metrics(decision)`: fail-open, her check() sonunda çağrılır
    - `_emit_breach()`: CONTRACT_BREACH_NO_OVERRIDE dalında çağrılır
    - `_emit_audit_failure()`: `_record_audit()` başarısız olduğunda çağrılır
    - `_record_audit()` metodunu try/except ile sar, hata durumunda `_emit_audit_failure()` çağır
    - Mevcut karar mantığı DEĞİŞMEZ — yalnızca sayaç artırma çağrıları eklenir
    - _Requirements: 4.1, 4.2, 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 2.2 Property test: Sözleşme ihlali sayacı (Property 2)
    - **Property 2: Sözleşme ihlali sayacı yalnızca CONTRACT_BREACH path'inde artar**
    - **Validates: Requirements 2.1, 2.3, 6.3**

  - [ ]* 2.3 Property test: Audit yazım hatası sayacı (Property 3)
    - **Property 3: Audit yazım hatası sayacı doğru artırılır**
    - **Validates: Requirements 3.1, 3.2, 6.4**

  - [ ]* 2.4 Property test: Fail-open metrik emisyonu (Property 5)
    - **Property 5: Metrik emisyonu fail-open — gate kararı değişmez**
    - **Validates: Requirements 4.1, 4.2, 6.5**

- [ ] 3. Checkpoint — Tüm testlerin geçtiğinden emin ol
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

- [ ] 4. Prometheus alert kuralları
  - [ ] 4.1 `monitoring/prometheus/ptf-admin-alerts.yml` — yeni alert grubu ekle
    - Grup adı: `ptf-admin-release-gate`
    - RG1: `ReleaseGateContractBreach` — `increase(release_gate_contract_breach_total[5m]) > 0`, severity: critical
    - RG2: `ReleaseGateAuditWriteFailure` — `increase(release_gate_audit_write_failures_total[15m]) > 0`, severity: warning
    - RG3: `ReleaseGateDenySpike` — `increase(release_gate_decision_total{decision="DENY"}[15m]) > 10`, severity: warning
    - Her alert için runbook_url, summary, description alanları
    - Mevcut alert grupları DEĞİŞTİRİLMEZ
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [ ]* 4.2 Alert kuralları unit testleri yaz
    - YAML yapı doğrulama: grup adı, kural sayısı
    - Her kural için expr, severity, annotations alanlarının varlığı
    - runbook_url formatı kontrolü
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [ ] 5. Grafana dashboard
  - [ ] 5.1 `monitoring/grafana/release-gate-dashboard.json` oluştur
    - Panel 1: Allow vs Deny Rate (timeseries) — `release_gate_decision_total{decision="ALLOW|DENY"}`
    - Panel 2: Top Deny Reasons (barchart) — `topk(10, release_gate_reason_total)`
    - Panel 3: Audit Write Failures (stat) — `release_gate_audit_write_failures_total`
    - Panel 4: Contract Breaches (stat) — `release_gate_contract_breach_total`
    - Mevcut preflight-dashboard.json yapısını ve stilini takip et
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ]* 5.2 Dashboard unit testleri yaz
    - JSON yapı doğrulama: uid, title, panels dizisi
    - Gerekli panel tiplerinin varlığı (timeseries, barchart, stat)
    - Her panelde targets ve expr alanlarının varlığı
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

- [ ] 6. Runbook güncellemesi
  - [ ] 6.1 `monitoring/runbooks/ptf-admin-runbook.md` — gate telemetrisi bölümleri ekle
    - ReleaseGateContractBreach: PromQL snippet'leri, kök nedenler, müdahale adımları
    - ReleaseGateAuditWriteFailure: PromQL snippet'leri, kök nedenler, müdahale adımları
    - ReleaseGateDenySpike: PromQL snippet'leri, kök nedenler, müdahale adımları
    - _Requirements: 10.1, 10.2, 10.3_

- [ ] 7. Final checkpoint — Tüm testlerin geçtiğinden emin ol
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Her görev belirli gereksinimleri referans alır (izlenebilirlik)
- Checkpoint'ler artımlı doğrulama sağlar
- Property testleri evrensel doğruluk özelliklerini doğrular
- Unit testler belirli örnekleri ve edge case'leri doğrular
