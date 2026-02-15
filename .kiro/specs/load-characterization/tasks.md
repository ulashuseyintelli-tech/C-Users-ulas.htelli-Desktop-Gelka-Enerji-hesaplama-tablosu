# Implementation Plan: Load Characterization & Failure Injection

## Overview

Mevcut FaultInjector, DependencyWrapper ve AlertValidator altyapısı üzerine inşa edilen yük karakterizasyon sistemi. Dört ana modül (load_harness, metrics_capture, scenario_runner, stress_report) ve bunların testleri oluşturulacak. Tüm kod `backend/app/testing/` ve `backend/tests/` dizinlerinde yaşar.

**Requirements Lock Checkpoint**: Bu task listesi, kilitlenmiş requirements.md (GNK-1/2/3, R1-R10) ve güncellenmiş design.md ile senkronizedir.

## Tasks

- [ ] 1. Load Harness — Async yük üreteci [R1]
  - [ ] 1.1 Implement `backend/app/testing/load_harness.py` — `ProfileType` enum, `LoadProfile` dataclass, `LoadResult` dataclass, `DEFAULT_PROFILES` dict ve `LoadHarness` sınıfı [R1]
    - `LoadHarness.run_profile()`: profil türüne göre yük üretir (burst → döngülü, diğerleri → tek pencere)
    - `LoadHarness._run_window()`: saniye bazlı batch task oluşturma, p95 hesaplama
    - `LoadHarness._timed_call()`: tek istek zamanlama, CircuitOpenError ayrımı
    - `LoadHarness._merge_results()`: burst döngü sonuçlarını birleştirme
    - `scale_factor` desteği: RPS ve süreyi orantılı küçültme, `scale_factor < 0.01` → ValueError [R1 AC4]
    - Profil bazlı minimum istek sayısı enforcement: Baseline/Peak ≥ 200, Stress/Burst ≥ 500 [GNK-3]
    - FAIL diagnostic payload formatı: `scenario_id, dependency, outcome, observed, expected, seed` [GNK-1]
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, GNK-1, GNK-3_

  - [ ]* 1.2 Write property test: LoadResult invariant [R1 AC5]
    - **Property 1: LoadResult İnvariantı — Toplam = Başarılı + Başarısız**
    - **Validates: Requirements 1.5**

  - [ ]* 1.3 Write property test: RPS tolerance [R1 AC3]
    - **Property 2: RPS Toleransı (±%30)**
    - **Validates: Requirements 1.3**

  - [ ]* 1.4 Write property test: Scale factor metamorphic [R1 AC4, AC7]
    - **Property 3: Ölçekleme Faktörü Metamorfik Özelliği**
    - **Validates: Requirements 1.4, 1.7**

- [ ] 2. Metrics Capture — Metrik yakalama ve delta hesaplama [R2]
  - [ ] 2.1 Implement `backend/app/testing/metrics_capture.py` — `MetricSnapshot` dataclass, `MetricDelta` dataclass ve `MetricsCapture` sınıfı [R2]
    - `MetricsCapture.__init__()`: izole CollectorRegistry + PTFMetrics oluşturma (LC-4)
    - `MetricsCapture.take_snapshot()`: prometheus_client collector'larından metrik okuma
    - `MetricsCapture.compute_delta()`: before/after farkı + retry_amplification_factor hesaplama
    - Retry amplifikasyon toleransı: `abs(diff) > max(1e-6, 1e-4 × expected)` → FAIL [R2 AC4]
    - Yakalanan metrikler: dependency_call_total, dependency_retry_total, circuit_breaker_state, guard_failopen_total, dependency_map_miss_total
    - FAIL diagnostic payload formatı [GNK-1]
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, GNK-1_

  - [ ]* 2.2 Write property test: Retry amplification formula [R2 AC4]
    - **Property 4: Retry Amplifikasyon Formülü**
    - Tolerans: `abs(diff) <= max(1e-6, 1e-4 × expected)` PASS, aksi FAIL
    - **Validates: Requirements 2.4**

  - [ ]* 2.3 Write property test: MetricsCapture isolation [R2 AC5]
    - **Property 5: MetricsCapture İzolasyonu**
    - **Validates: Requirements 2.5, 3.2**

- [ ] 3. Scenario Runner — Orkestrasyon bileşeni [R3]
  - [ ] 3.1 Implement `backend/app/testing/scenario_runner.py` — `InjectionConfig` dataclass, `ScenarioResult` dataclass ve `ScenarioRunner` sınıfı [R3]
    - `ScenarioRunner.run_scenario()`: izole bileşen oluşturma → enjeksiyon → snapshot → yük → snapshot → delta → temizlik
    - `ScenarioRunner.run_multi_instance_scenario()`: N ayrı CBRegistry ile paralel çalıştırma (LC-3)
    - `ScenarioRunner._configure_injection()`: failure_type → enjeksiyon mekanizması eşleme
    - `ScenarioRunner._create_target_fn()`: oran bazlı hata üreten async fonksiyon oluşturma, `random.Random(seed)` ile deterministik [GNK-2]
    - `finally` bloğunda `disable_all()` + `reset_instance()` garantisi [R3 AC4]
    - FAIL diagnostic payload formatı [GNK-1]
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, GNK-1, GNK-2_

  - [ ]* 3.2 Write property test: Scenario cleanup invariant [R3 AC4]
    - **Property 6: Senaryo Temizlik İnvariantı**
    - **Validates: Requirements 3.4**

- [ ] 4. Checkpoint — Temel altyapı doğrulaması
  - GNK-1 (diagnostic payload), GNK-2 (determinism scope), GNK-3 (min request counts) doğrulaması
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Failure Matrix testleri [R4]
  - [ ] 5.1 Implement `backend/tests/test_lc_failure_matrix.py` — Hata enjeksiyon matrisi entegrasyon testleri [R4]
    - FM-1: %10 Timeout → retry artışı, CB CLOSED kalır [R4 AC1]
    - FM-2: %40 Timeout → CB OPEN'a geçer [R4 AC2]
    - FM-3: %30 5xx → CB OPEN eşiğine ulaşır [R4 AC3]
    - FM-4: %100 ConnectionError → hızlı CB OPEN [R4 AC4]
    - FM-5: %100 Latency 2× → gecikme artışı, CB CLOSED kalır [R4 AC5]
    - Her test ScenarioRunner kullanır, küçük scale_factor ile CI-safe
    - Determinism: `random.Random(seed)` ile sabit seed, aynı seed → aynı CB state + retry count [R4 AC7, GNK-2]
    - FAIL diagnostic payload formatı [GNK-1]
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, GNK-1, GNK-2_

  - [ ]* 5.2 Write property test: 100% failure → CB OPEN guarantee [R4 AC4]
    - **Property 7: %100 Hata Oranında CB OPEN Garantisi**
    - **Validates: Requirements 4.4**

- [ ] 6. Multi-Instance CB sapma testleri [R5]
  - [ ] 6.1 Implement `backend/tests/test_lc_multi_instance.py` — Çoklu-instance CB sapma testleri [R5]
    - 2+ ayrı CircuitBreakerRegistry instance'ı ile %40 hata enjeksiyonu [R5 AC1]
    - CB durum geçiş zamanlarını kaydetme (monotonic timestamp) [R5 AC2]
    - divergence_window hesaplama: `|t1 - t2|` [R5 AC3]
    - Clock skew compensation: `compensated_divergence = max(0, |t1 - t2| - max_clock_skew)`, max_clock_skew default 50ms [R5 AC4]
    - Eşik karşılaştırması compensated değer üzerinden: `compensated_divergence > cb_open_duration × 2` → TuningRecommendation [R5 AC5]
    - Çift yönlü FAIL: eşik aşımında öneri zorunlu, eşik altında öneri yasak [R5 AC5]
    - ScenarioRunner.run_multi_instance_scenario() kullanımı
    - FAIL diagnostic payload formatı [GNK-1]
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, GNK-1, GNK-2_

  - [ ]* 6.2 Write property test: CB registry independence [R5 AC1]
    - **Property 8: CB Registry Bağımsızlığı**
    - **Validates: Requirements 5.2**

  - [ ]* 6.3 Write property test: Compensated divergence threshold → tuning recommendation [R5 AC4, AC5]
    - **Property 9: Sapma Eşiği ve Ayar Önerisi (compensated_divergence ile)**
    - Clock skew compensation doğrulaması: `max(0, divergence - max_clock_skew)` formülü
    - Çift yönlü FAIL: eşik aşımında öneri var, eşik altında öneri yok
    - **Validates: Requirements 5.4, 5.5**

- [ ] 7. Alert doğrulama testleri [R6]
  - [ ] 7.1 Implement `backend/tests/test_lc_alert_validation.py` — Alert tetikleme doğrulama testleri [R6]
    - Her failure matrix senaryosu sonrası beklenen alert'lerin tetiklendiğini doğrulama [R6 AC1]
    - İlgisiz alert'lerin sessiz kaldığını doğrulama (sessizlik doğrulaması) [R6 AC2]
    - FM-1 ile FM-5 arası explicit beklenen/suskun alert listeleri [R6 AC3]
    - DH1-DH8 alert'leri için `check_*` metodları (mevcut AlertValidator genişletmesi) [R6 AC4]
    - Alert fire latency ölçümü: `alert_fire_latency_seconds ≤ 2 × eval_interval` (default 60s) [R6 AC5]
    - eval_interval = 30s (Prometheus default, repo'da explicit tanım yok)
    - FAIL diagnostic payload formatı [GNK-1]
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, GNK-1_

  - [ ]* 7.2 Write property test: Alert validation consistency [R6 AC1, AC2]
    - **Property 10: Alert Doğrulama Tutarlılığı**
    - **Validates: Requirements 6.1, 6.2**

  - [ ]* 7.3 Write property test: Alert fire latency upper bound [R6 AC5]
    - **Property 13: Alert Fire Latency Üst Sınırı**
    - Limit, runtime eval interval paramından türetilir: `eval_interval_seconds = int(os.getenv("EVAL_INTERVAL_SECONDS", "60"))`
    - `alert_fire_latency_seconds ≤ 2 × eval_interval_seconds`
    - ENV yoksa fallback 60s; CI'da override edilebilir
    - **Validates: Requirements 6.5**

- [ ] 8. Write-path güvenlik testleri [R7]
  - [ ] 8.1 Implement `backend/tests/test_lc_write_safety.py` — Write-path retry=0 doğrulama testleri [R7]
    - Stress profili altında is_write=True çağrıları [R7 AC1]
    - dependency_retry_total metriğinin sıfır kaldığını doğrulama [R7 AC2]
    - Minimum istek sayısı ≥ 50 (write-path özel kuralı) [R7 AC3]
    - DW-1 politikasının stres altında korunduğunu kanıtlama [R7 AC4]
    - FAIL diagnostic payload formatı [GNK-1]
    - _Requirements: 7.1, 7.2, 7.3, 7.4, GNK-1_

  - [ ]* 8.2 Write property test: Write-path retry zero guarantee [R7 AC1, AC2]
    - **Property 11: Write-Path Retry Sıfır Garantisi**
    - **Validates: Requirements 7.1, 7.2, 7.3**

- [ ] 9. Checkpoint — Senaryo testleri doğrulaması
  - GNK-1 (diagnostic payload), GNK-2 (determinism), R6 AC5 (alert latency) doğrulaması
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. Stress Report — Rapor üretimi [R8]
  - [ ] 10.1 Implement `backend/app/testing/stress_report.py` — `TuningRecommendation` dataclass, `FlakyCorrelationSegment` dataclass ve `StressReport` sınıfı [R8, R9]
    - `StressReport.generate_metrics_table()`: her senaryo için metrik satırı üretme [R8 AC1]
    - `StressReport.generate_recommendations()`: CB tuning, retry tuning, alert tuning önerileri [R8 AC2, AC3, AC4]
    - Divergence analizi → CB pencere ayar önerisi (`compensated_divergence > cb_open_duration × 2`) [R8 AC2]
    - Retry amplifikasyon analizi → retry üst sınır önerisi (`amplification > 2.0`) [R8 AC3]
    - Write-path güvenlik onayı (`write_path_safe: bool`, retry=0 doğrulaması bazlı) [R8 AC5]
    - Flaky test korelasyon: eşik > 100ms → segment dolu (3 zorunlu alan: timing_deviation_ms, suspected_source, repro_steps) [R9 AC3, AC4]
    - FAIL diagnostic payload formatı [GNK-1]
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 9.3, 9.4, GNK-1_

  - [ ]* 10.2 Write property test: Compensated divergence threshold → tuning recommendation (report level) [R8 AC2]
    - **Property 9: Sapma Eşiği ve Ayar Önerisi (rapor seviyesinde)**
    - **Validates: Requirements 5.4, 5.5, 8.2**

  - [ ]* 10.3 Write property test: Report completeness [R8 AC1]
    - **Property 12: Rapor Tamlığı**
    - **Validates: Requirements 8.1**

- [ ] 11. Flaky test korelasyon gözlemi [R9]
  - [ ] 11.1 Implement `backend/tests/test_lc_flaky_correlation.py` — Flaky test korelasyon testleri [R9]
    - Yüksek eşzamanlılık senaryosunda `test_provisional_to_final_allowed` simülasyonu [R9 AC1]
    - Zamanlama sapması metriklerini kaydetme: `timing_deviation_ms = |actual - expected|` [R9 AC2]
    - Çift yönlü eşik doğrulaması: > 100ms → segment dolu, ≤ 100ms → segment boş [R9 AC3]
    - Dolu segment 3 zorunlu alan: timing_deviation_ms, suspected_source, repro_steps [R9 AC4]
    - FAIL diagnostic payload formatı [GNK-1]
    - _Requirements: 9.1, 9.2, 9.3, 9.4, GNK-1_

- [ ] 12. Entegrasyon ve son doğrulama [R10]
  - [ ] 12.1 Wire all components — Tüm bileşenleri birleştiren entegrasyon testi [R10]
    - `backend/tests/test_lc_integration.py`: Tam senaryo akışı (profil → enjeksiyon → metrik → rapor)
    - Baseline profil ile uçtan uca test
    - Mevcut testlerin kırılmadığını doğrulama [R10 AC1, AC2]
    - `ptf_admin_` namespace kullanımı doğrulaması [R10 AC3]
    - Production kodu değişikliği olmadığını doğrulama [R10 AC4, AC5]
    - CI toplam süre < 4 dakika doğrulaması [R10 AC7]
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

- [ ] 13. Final checkpoint — Tüm testlerin geçtiğini doğrula
  - GNK-1/2/3 compliance kontrolü
  - R1-R10 traceability matrix doğrulaması
  - Ensure all tests pass, ask the user if questions arise.

## Traceability Matrix

| Requirement | Task(s) | Property Test(s) |
|-------------|---------|-------------------|
| R1 (Load Harness) | 1.1 | P1 (1.2), P2 (1.3), P3 (1.4) |
| R2 (Metrics Capture) | 2.1 | P4 (2.2), P5 (2.3) |
| R3 (Scenario Runner) | 3.1 | P6 (3.2) |
| R4 (Failure Matrix) | 5.1 | P7 (5.2) |
| R5 (Multi-Instance CB) | 6.1 | P8 (6.2), P9 (6.3) |
| R6 (Alert Validation) | 7.1 | P10 (7.2), P13 (7.3) |
| R7 (Write-Path Safety) | 8.1 | P11 (8.2) |
| R8 (Stress Report) | 10.1 | P9 (10.2), P12 (10.3) |
| R9 (Flaky Correlation) | 11.1 | — |
| R10 (System Compat) | 12.1 | — |
| GNK-1 (Diagnostic Payload) | All impl tasks | — |
| GNK-2 (Determinism Scope) | 3.1, 5.1, 6.1 | — |
| GNK-3 (Min Request Counts) | 1.1 | — |

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements [R#] and GNK rules for traceability
- Checkpoints ensure incremental validation and spec drift prevention
- Property tests validate universal correctness properties (Hypothesis, min 100 iterations)
- Unit tests validate specific examples and edge cases
- Tüm yük testleri küçük `scale_factor` (0.01-0.1) ile CI-safe çalışır
- PBT Performans Kuralı: `st.from_regex(...)` kullanılmaz; kompozisyonel stratejiler tercih edilir
- eval_interval_seconds = `int(os.getenv("EVAL_INTERVAL_SECONDS", "60"))` — runtime param, ENV yoksa fallback 60s
