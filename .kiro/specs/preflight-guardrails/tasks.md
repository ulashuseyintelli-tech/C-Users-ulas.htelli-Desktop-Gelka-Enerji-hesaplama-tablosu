# Uygulama Planı: Preflight Guard-Rails

## Genel Bakış

Mevcut preflight telemetry altyapısına 5 koruma mekanizması eklenir: monotonluk garantisi (store_generation + store_start_timestamp), cardinality guard, override semantik netliği (runbook), fail-open counter dayanıklılığı doğrulaması, ve hazır PromQL paketleri (alert kuralları + dashboard + runbook).

## Görevler

- [x] 1. MetricStore'a store_generation ve store_start_timestamp ekle
  - [x] 1.1 `backend/app/testing/preflight_metrics.py` — MetricStore sınıfına `_store_generation: int` ve `_store_start_timestamp: float` alanları ekle
    - `__init__` içinde `_store_generation = 0`, `_store_start_timestamp = time.time()`
    - `store_generation()` ve `store_start_timestamp()` getter metotları ekle (thread-safe, `_lock` ile)
    - `to_dict()` içine `store_generation` ve `store_start_timestamp` alanlarını ekle
    - `from_dict()` içinde bu alanları yükle (yoksa default: generation=0, timestamp=mevcut)
    - `save_to_dir()` içinde başarılı yazım öncesi `_store_generation += 1`
    - `load_from_dir()` başarılı yüklemede mevcut değerleri koru; başarısızda generation=0, timestamp=now
    - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6_

  - [x]* 1.2 Property test: store generation monoton artış (Property 1)
    - **Property 1: Store generation monoton artış**
    - hypothesis ile rastgele N (1-50) save döngüsü, her save sonrası generation == önceki + 1
    - **Validates: Requirements 1.1**

  - [x]* 1.3 Property test: save/load round-trip — generation + timestamp (Property 2)
    - **Property 2: MetricStore save/load round-trip**
    - hypothesis ile rastgele metrik ekle, save → load, generation ve timestamp korunur
    - Evidence: `test_preflight_metrics_roundtrip.py::TestCoreRoundTrip::test_random_ops_save_load_equality`
    - **Validates: Requirements 1.2, 1.5**

- [x] 2. MetricExporter'a store_generation ve store_start_timestamp gauge çıktısı ekle
  - [x] 2.1 `backend/app/testing/preflight_metrics.py` — `export_prometheus()` fonksiyonuna iki yeni gauge satırı ekle
    - `release_preflight_store_generation` gauge (HELP + TYPE + değer)
    - `release_preflight_store_start_timestamp` gauge (HELP + TYPE + değer)
    - Evidence: `preflight_metrics.py::MetricExporter.export_prometheus()` — gauge satırları mevcut
    - _Requirements: 1.1, 1.4_

  - [x]* 2.2 Unit test: Prometheus çıktısında yeni gauge'ların mevcut olduğunu doğrula
    - store_generation ve store_start_timestamp satırlarının Prometheus çıktısında bulunduğunu kontrol et
    - Evidence: `test_preflight_guardrails.py::TestPrometheusGauges` (eklenecek)
    - _Requirements: 1.1, 1.4_

- [x] 3. Cardinality guard testi ekle
  - [x] 3.1 `backend/tests/test_preflight_guardrails.py` — BlockReasonCode enum cardinality guard testi yaz
    - `assert len(BlockReasonCode) <= 50` — regression guard
    - Evidence: `test_preflight_guardrails.py::TestCardinalityGuard::test_block_reason_code_cardinality_cap`
    - _Requirements: 2.1, 2.2_

  - [x]* 3.2 Property test: reason label filtreleme (Property 3)
    - **Property 3: Reason label filtreleme — bounded cardinality**
    - hypothesis ile rastgele string listesi (geçerli + geçersiz karışık), from_preflight_output sonrası sadece enum üyeleri kalır
    - Evidence: `test_preflight_guardrails.py::TestPropertyReasonLabelFiltering::test_only_enum_members_survive_filtering`
    - **Validates: Requirements 2.3**

- [x] 4. Checkpoint — MetricStore ve cardinality testlerini çalıştır
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

- [x] 5. Fail-open counter dayanıklılığı doğrulaması
  - [x]* 5.1 Property test: write failures in-memory birikim ve persist round-trip (Property 4)
    - **Property 4: Write failures in-memory birikim ve persist round-trip**
    - hypothesis ile rastgele N (1-20) record_write_failure, save → load, write_failures == N
    - Evidence: `test_preflight_guardrails.py::TestPropertyWriteFailuresRoundTrip::test_write_failures_accumulate_and_persist`, `::test_fail_fail_ok_pattern_accumulates`, `::test_write_failures_independent_of_metrics`
    - **Validates: Requirements 4.1, 4.2, 4.3**

  - [x]* 5.2 Property test: write failures thread-safety (Property 5)
    - **Property 5: Write failures thread-safety**
    - K thread × M çağrı, toplam == K × M
    - Evidence: `test_preflight_guardrails.py::TestPropertyWriteFailuresThreadSafety::test_concurrent_write_failures_exact_count`, `::test_concurrent_failures_persist_correctly`
    - **Validates: Requirements 4.4**

- [x] 6. Alert kuralları ekle
  - [x] 6.1 `monitoring/prometheus/ptf-admin-alerts.yml` — `ptf-admin-preflight-guardrails` alert grubu ekle
    - PreflightContractBreach: `increase(release_preflight_override_total{kind="breach"}[5m]) > 0`, severity: critical, for: 0m
    - PreflightBlockSpike: mutlak + oran dual threshold, severity: warning, for: 10m
    - PreflightTelemetryWriteFailure: `increase(release_preflight_telemetry_write_failures_total[15m]) > 0`, severity: warning
    - PreflightCounterReset: `resets(release_preflight_verdict_total[6h]) > 0`, severity: warning
    - Evidence: `ptf-admin-alerts.yml::ptf-admin-preflight-guardrails` alert grubu (4 kural)
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x]* 6.2 Yapısal test: alert kuralları YAML doğrulaması
    - 4 alert kuralının mevcut olduğunu, doğru severity ve expr içerdiğini doğrula
    - Evidence: `test_preflight_guardrails.py::TestPreflightGuardrailAlerts` (10 test)
    - _Requirements: 5.9_

- [x] 7. Dashboard genişletmesi
  - [x] 7.1 `monitoring/grafana/preflight-dashboard.json` — 3 yeni panel ekle
    - Block Ratio: gauge panel, `increase(...{verdict="BLOCK"}[15m]) / increase(...[15m])`
    - Override Applied Rate: timeseries panel, `increase(...{kind="applied"}[1h])`
    - Telemetry Health: stat panel, `write_failures_total` + `store_generation`
    - Evidence: `preflight-dashboard.json` paneller id 4-6
    - _Requirements: 5.5, 5.6, 5.7_

  - [x]* 7.2 Yapısal test: dashboard JSON'da 6 panel olduğunu ve yeni panellerin doğru tip/sorgu içerdiğini doğrula
    - Mevcut `test_preflight_dashboard.py` testlerini genişlet
    - Evidence: `test_preflight_dashboard.py::TestDashboardStructure` (12 test)
    - _Requirements: 5.10_

- [x] 8. Runbook genişletmesi
  - [x] 8.1 `monitoring/runbooks/ptf-admin-runbook.md` — Override semantiği bölümü ekle
    - attempt, applied, breach tanımları ve senaryo örnekleri
    - Evidence: `ptf-admin-runbook.md::# Preflight Override Semantiği`
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x] 8.2 `monitoring/runbooks/ptf-admin-runbook.md` — 4 yeni alert troubleshooting bölümü ekle
    - PreflightContractBreach, PreflightBlockSpike, PreflightTelemetryWriteFailure, PreflightCounterReset
    - Her biri: olası nedenler, ilk 3 kontrol, müdahale adımları, PromQL referansı
    - Evidence: `ptf-admin-runbook.md::## PreflightContractBreach` + 3 diğer bölüm
    - _Requirements: 5.8_

  - [x]* 8.3 Yapısal test: runbook'ta override semantiği ve alert bölümlerinin mevcut olduğunu doğrula
    - Evidence: `test_preflight_guardrails.py::TestPreflightRunbookStructure` (11 test)
    - _Requirements: 3.1, 5.8_

- [x] 9. Final checkpoint — Tüm testleri çalıştır
  - 112 test geçti (guardrails 52 + dashboard 12 + metrics 43 + roundtrip 5)

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Her görev belirli gereksinimlere referans verir (izlenebilirlik)
- Checkpoint'ler artımlı doğrulama sağlar
- Property testleri evrensel doğruluk özelliklerini doğrular (hypothesis, min 100 iterasyon)
- Unit testler belirli örnekleri ve edge case'leri doğrular
