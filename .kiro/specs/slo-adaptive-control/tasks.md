# Uygulama Planı: SLO-Aware Adaptive Control (rev2)

## Genel Bakış

SLO-farkında adaptive control plane'i 5 paket halinde inşa edilir. Her paket kendi test alt-görevleriyle birlikte gelir; 25 correctness property ilgili implementasyon paketlerine dağıtılır. Uygulama sırası: Config → Telemetry → Budget → Decision+Orchestrator → Events/Metrics → Test Harness. Her adım bir öncekinin üzerine inşa edilir ve orphan kod bırakılmaz.

Decision ve Orchestrator aynı pakette birleştirilmiştir çünkü: 429=HOLD, telemetry-insufficient, allowlist bypass gibi branch'lerde "0 call / no side-effect" garantisini orchestrator enforce eder; decision engine pure function olsa da production davranışı orchestrator belirler. Bu iki katmanın ayrı paketlerde test edilmesi side-effect sınırlarını kaçırır.

Dil: Python (hypothesis PBT, dataclasses, mevcut backend yapısıyla uyumlu).

## MUST PBT Listesi (Non-Optional)

Aşağıdaki property'ler production incident'i direkt engeller ve MVP'de bile atlanamaz:

| # | Property | Neden MUST |
|---|----------|-----------|
| P1 | Monotonic-Safe Transitions | ENFORCE→SHADOW only, illegal transitions reject |
| P2 | Priority Ladder Determinism | Tie-breaker stability, deterministik karar |
| P4 | Allowlist Scoping Invariant | Tenant/dependency boundary izolasyonu |
| P5 | Audit Completeness Invariant | Event olmadan mod geçişi yok |
| P11 | Error Budget Formula Correctness | Rolling 30d + reset semantics (config-change only) |
| P16 | Backpressure Hard Block (HOLD) | 429 → HOLD hard block (no queue/backoff/retry) |
| P17 | Fail-Safe State Preservation | Fail-safe'de side-effect = 0 |
| P18 | Telemetry Insufficiency → No-Op | Insufficient → no-op + alert-only |

Diğer property'ler (`*` ile işaretli) MVP hızlandırma için atlanabilir.

## Görevler

- [x] 1. Config + Validation (Allowlist Scoping, Threshold/Dwell Params, Canonical SLO Signals)
  - [x] 1.1 `backend/app/adaptive_control/config.py` — AdaptiveControlConfig dataclass oluştur
    - Tüm parametreler: control_loop_interval_seconds, p95_latency_enter/exit_threshold, queue_depth_enter/exit_threshold, error_budget_window_seconds, guard/pdf_slo_target, burn_rate_threshold, dwell_time_seconds, cooldown_period_seconds, oscillation_window_size, oscillation_max_transitions, min_sample_ratio, min_bucket_coverage_pct, targets (AllowlistEntry list), guard_slo_query, pdf_slo_query
    - `validate() -> list[str]` metodu: exit ≥ enter kontrolü, SLO target (0,1] aralığı, pozitif süre kontrolü, burn_rate > 0
    - Environment variable'lardan yükleme: `load_adaptive_control_config()` fonksiyonu, `GuardConfig` ile tutarlı pattern
    - `_FALLBACK_DEFAULTS` dict ile güvenli varsayılanlar
    - _Requirements: 9.1, 9.2, 9.4, CC.1_

  - [x] 1.2 `backend/app/adaptive_control/config.py` — AllowlistEntry dataclass + AllowlistManager sınıfı
    - `AllowlistEntry(frozen=True)`: tenant_id, endpoint_class, subsystem_id
    - `AllowlistManager`: is_in_scope(), update() (audit log üretir), is_empty property
    - Boş allowlist → hiçbir hedef üzerinde aksiyon alınmaz
    - _Requirements: 9.5, 9.6, 9.7, CC.5_

  - [x] 1.3 `backend/app/adaptive_control/config.py` — Canonical SLO signal parametreleri ve config drift kontrolü
    - guard_slo_query ve pdf_slo_query sabit tanımları
    - `check_config_drift()` fonksiyonu: query parametreleri canonical tanımla uyuşmuyorsa "config_drift_detected" hatası
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 1.4 Property test: Config Validation — MUST (Property 21)
    - **Property 21: Configuration Validation**
    - Random geçersiz config değerleri → validate() hata döndürür, mevcut config korunur
    - **Validates: Requirements 9.2**

  - [x] 1.5 Property test: Allowlist Scoping Invariant — MUST (Property 4)
    - **Property 4: Allowlist Scoping Invariant**
    - Random allowlist + random target kombinasyonları → allowlist dışı hedef için sinyal üretilmez; boş allowlist → sıfır sinyal
    - **Validates: Requirements CC.5, 7.5, 9.5, 9.6**

  - [x]* 1.6 Property test: Config Drift Detection (Property 10)
    - **Property 10: Config Drift Detection**
    - Random uyumsuz config'ler → config_drift_detected hatası, kontrol sinyali üretilmez
    - **Validates: Requirements 2.5**

  - [x]* 1.7 Property test: Configuration Change Audit (Property 22)
    - **Property 22: Configuration Change Audit**
    - Random config/allowlist değişiklikleri → audit log entry: old value, new value, actor, timestamp
    - **Validates: Requirements 9.3, 9.7**

  - [x]* 1.8 Unit tests: Config + Allowlist
    - `test_canonical_guard_slo_signal_config` (Req 2.2)
    - `test_canonical_pdf_slo_signal_config` (Req 2.3)
    - `test_error_budget_config_format` (Req 3.3)
    - `test_default_config_from_env` (Req 9.4)
    - `test_separate_enter_exit_thresholds` (Req 5.1)
    - `test_empty_allowlist_no_action` (edge case)
    - _Requirements: 2.2, 2.3, 3.3, 5.1, 9.4_

- [x] 2. Checkpoint — Config paketi
  - **Test gate:** Task 1.4 (P21) ve 1.5 (P4) MUST property'leri %100 geçmeli; 1.6–1.8 varsa onlar da
  - **Invariant gate:** AllowlistManager.is_in_scope() dışı hedef için sıfır sinyal kanıtlanmış; validate() geçersiz config'i reject etmiş
  - **No-regression gate:** AdaptiveControlConfig field listesi ve AllowlistEntry schema freeze; mevcut test suite kırılmamış

- [x] 3. Telemetry Ingestion + Windowing (rate() pipeline, stale + coverage + min sample)
  - [x] 3.1 `backend/app/adaptive_control/metrics_collector.py` — MetricsCollector sınıfı
    - `ingest(source_id, sample: MetricSample)`: sample ekle, last_seen güncelle
    - `get_samples(source_id, window_start_ms, window_end_ms)`: pencere içi sample'lar
    - `get_all_samples(window_start_ms, window_end_ms)`: tüm kaynaklardan birleştir
    - `check_health(now_ms) -> list[SourceHealth]`: her kaynak için stale kontrolü
    - SourceHealth dataclass: source_id, last_sample_ms, is_stale
    - Mevcut `MetricSample` formatı ile uyumlu (slo_evaluator.py)
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 3.2 `backend/app/adaptive_control/sufficiency.py` — TelemetrySufficiencyChecker sınıfı
    - SufficiencyConfig dataclass: min_samples, min_bucket_coverage_pct, check_source_stale
    - SufficiencyResult dataclass: is_sufficient, sample_count, required_samples, bucket_coverage_pct, stale_sources, reason
    - `check(samples, source_health) -> SufficiencyResult`
    - Üç koşul: (a) min N sample, (b) bucket coverage ≥ 80%, (c) source_stale kontrolü
    - _Requirements: 6.3, 6.4_

  - [x] 3.3 Property test: Telemetry Insufficiency → No-Op + Alert — MUST (Property 18)
    - **Property 18: Telemetry Insufficiency → No-Op + Alert**
    - Random yetersiz veri senaryoları → kontrol sinyali üretilmez, telemetry_insufficient alert üretilir
    - **Validates: Requirements 6.3, 6.4**

  - [x]* 3.4 Property test: Metric Collection Round-Trip (Property 8)
    - **Property 8: Metric Collection Round-Trip**
    - Random MetricSample setleri → ingest → query → tüm sample'lar doğru timestamp ile döner
    - **Validates: Requirements 1.1, 1.2, 1.3**

  - [x]* 3.5 Property test: Source Stale Detection (Property 9)
    - **Property 9: Source Stale Detection**
    - Random timestamp + interval kombinasyonları → control_loop_interval içinde veri yoksa is_stale=True
    - **Validates: Requirements 1.4**

  - [x]* 3.6 Unit tests: MetricsCollector + SufficiencyChecker
    - `test_all_sources_stale_suspend` (edge case: tüm kaynaklar stale → suspend)
    - `test_zero_request_rate_budget` (edge case: request_rate=0 → division by zero koruması)
    - _Requirements: 1.1, 1.4, 6.2, 6.4_

- [x] 4. Checkpoint — Telemetry paketi
  - **Test gate:** Task 3.3 (P18) MUST property %100 geçmeli; 3.4–3.6 varsa onlar da
  - **Invariant gate:** Insufficient telemetry → sıfır ControlSignal kanıtlanmış; source_stale detection doğru çalışıyor
  - **No-regression gate:** MetricSample format uyumluluğu slo_evaluator.py ile korunmuş; mevcut test suite kırılmamış

- [x] 5. Budget Engine (Rolling 30d Aggregator, Reset Semantics)
  - [x] 5.1 `backend/app/adaptive_control/budget.py` — ErrorBudgetCalculator sınıfı
    - ErrorBudgetConfig dataclass: metric, window_seconds (30*86400), slo_target, burn_rate_threshold
    - BudgetStatus dataclass: subsystem_id, metric, budget_total, budget_consumed, budget_remaining_pct, burn_rate, is_exhausted, is_burn_rate_exceeded
    - `evaluate(samples, now_ms) -> list[BudgetStatus]`
    - Formül: `allowed_errors = (1 - SLO_target) × window_duration × request_rate`
    - Rolling 30-day window: her iterasyonda kayar (calendar month değil)
    - Budget reset yalnızca config değişikliği ile, audit log'a kaydedilir
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 3.7_

  - [x] 5.2 Property test: Error Budget Formula Correctness — MUST (Property 11)
    - **Property 11: Error Budget Formula Correctness**
    - Random (target, window, rate) tuple'ları → `allowed_errors = (1 - t) × w × r`, budget_remaining_pct doğru
    - **Validates: Requirements 3.1, 3.2, 3.6, 3.7**

  - [x]* 5.3 Property test: Burn Rate Threshold Triggering (Property 12)
    - **Property 12: Burn Rate Threshold Triggering**
    - Random burn rate değerleri threshold etrafında → threshold aşıldığında koruyucu sinyal üretilir
    - **Validates: Requirements 3.4, 4.5**

  - [x]* 5.4 Unit tests: ErrorBudgetCalculator
    - `test_error_budget_config_format` (Req 3.3)
    - `test_zero_request_rate_budget` (edge case: division by zero koruması)
    - `test_budget_reset_audit_log` (Req 3.7: reset audit kaydı)
    - _Requirements: 3.1, 3.3, 3.6, 3.7_

- [x] 6. Checkpoint — Budget engine
  - **Test gate:** Task 5.2 (P11) MUST property %100 geçmeli; 5.3–5.4 varsa onlar da
  - **Invariant gate:** Budget formülü `(1-t)×w×r` doğrulanmış; rolling 30d window kayma doğru; reset yalnızca config change ile
  - **No-regression gate:** BudgetStatus schema freeze; mevcut test suite kırılmamış

- [x] 7. Decision Engine + Orchestrator (Birleşik Paket: Pure Decision + Side-Effect Boundaries)
  - [x] 7.1 `backend/app/adaptive_control/signals.py` — SignalType, PriorityLevel enum'ları ve ControlSignal dataclass
    - SignalType: SWITCH_TO_SHADOW, RESTORE_ENFORCE, STOP_ACCEPTING_JOBS, RESUME_ACCEPTING_JOBS (kapalı küme)
    - PriorityLevel: KILLSWITCH=1, MANUAL_OVERRIDE=2, ADAPTIVE_CONTROL=3, DEFAULT_CONFIG=4
    - ControlSignal(frozen=True): signal_type, subsystem_id, metric_name, tenant_id, trigger_value, threshold, priority, correlation_id, timestamp_ms
    - _Requirements: CC.1, CC.3_

  - [x] 7.2 `backend/app/adaptive_control/decision_engine.py` — DecisionEngine sınıfı
    - `decide(budget_statuses, eval_results, now_ms) -> list[ControlSignal]`
    - 4-level priority ladder: KillSwitch → Manual Override → Adaptive → Default
    - `_apply_tie_breaker(signals)`: subsystem_id → metric_name → tenant_id (lexicographic)
    - KillSwitch aktifse → ilgili subsystem için no-op
    - Manual override aktifse → ilgili subsystem için no-op
    - Mevcut `KillSwitchManager` ile entegrasyon
    - _Requirements: CC.3, CC.4, 10.1, 10.2, 10.3, 10.4_

  - [x] 7.3 `backend/app/adaptive_control/hysteresis.py` — HysteresisFilter sınıfı
    - HysteresisState dataclass: last_transition_ms, last_signal_ms, current_mode, transition_history
    - `apply(signals, now_ms) -> list[ControlSignal]`: dwell time + cooldown filtresi
    - `detect_oscillation(subsystem_id) -> bool`: son N karar içinde M'den fazla geçiş → True
    - Dwell time ve cooldown bypass edilemez
    - _Requirements: CC.7, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 7.4 `backend/app/adaptive_control/decision_engine.py` — Guard mode geçiş mantığı
    - p95 latency > enter_threshold → SWITCH_TO_SHADOW sinyali
    - p95 latency < exit_threshold + dwell_time dolmuş → RESTORE_ENFORCE sinyali
    - v1: yalnızca ENFORCE→SHADOW, OFF modu kapsam dışı
    - Monotonic-safe: otomatik enforcement artırma yok
    - _Requirements: 4.2, 4.3, 7.1, 7.2, 7.3, CC.2_

  - [x] 7.5 `backend/app/adaptive_control/decision_engine.py` — PDF backpressure mantığı
    - queue_depth > enter_threshold → STOP_ACCEPTING_JOBS sinyali
    - queue_depth < exit_threshold + dwell_time dolmuş → RESUME_ACCEPTING_JOBS sinyali
    - HTTP 429 + Retry-After + BACKPRESSURE_ACTIVE error code
    - HOLD semantiği: hard block, kuyruğa alınmaz, yavaşlatılmaz
    - Mevcut job'lar işlenmeye devam eder
    - _Requirements: 4.4, 8.1, 8.2, 8.3, 8.4_

  - [x] 7.6 `backend/app/adaptive_control/controller.py` — AdaptiveController orkestratör sınıfı
    - AdaptiveControllerState enum: RUNNING, FAILSAFE, SUSPENDED
    - Constructor: config, metrics_collector, slo_evaluator, budget_calculator, decision_engine, hysteresis_filter, sufficiency_checker, guard_decision, pdf_job_store
    - `tick(now_ms) -> list[ControlSignal]`: tam control loop iterasyonu
    - `apply_signal(signal)`: sinyal → ilgili subsystem'e uygulama (side-effect boundary)
    - `state` property: mevcut controller durumu
    - Fail-safe: internal exception → FAILSAFE state, mevcut durumu koru
    - All sources stale → SUSPENDED, kararları askıya al
    - Telemetry insufficient → no-op + alert
    - Recovery: kaynaklar sağlıklı → otomatik normal operasyona dönüş
    - **Side-effect sınırları:** apply_signal() dışında hiçbir metod subsystem state değiştirmez; 429=HOLD, telemetry-insufficient, allowlist bypass branch'lerinde apply_signal() çağrılmaz (0 side-effect garantisi)
    - _Requirements: 4.1, 4.6, 6.1, 6.2, 6.3, 6.5, 6.7, 6.8_

  - [x] 7.7 `backend/app/adaptive_control/controller.py` — Guard_Decision ve PDF_Job_Store entegrasyonu
    - switch_to_shadow → mevcut `Guard_Decision` modunu shadow'a geçir (Allowlist kapsamında)
    - restore_enforce → manual ops veya recovery koşulu ile ENFORCE'a dön
    - stop_accepting_jobs → `PDF_Job_Store` yeni job kabulünü durdur (HTTP 429 + HOLD)
    - resume_accepting_jobs → job kabulünü yeniden aç
    - Mevcut `guard_decision.py` ve `pdf_job_store.py` ile wiring
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 7.8 `backend/app/adaptive_control/controller.py` — KillSwitch öncelik entegrasyonu
    - Her tick'te aktif killswitch/manual override kontrolü
    - KillSwitch aktifse → ilgili subsystem için adaptive kararları askıya al
    - KillSwitch deactivation → cooldown_period_seconds sonrasında ilk karar
    - Override durumunu metrik olarak raporla
    - Mevcut `KillSwitchManager` ile entegrasyon
    - _Requirements: 10.3, 10.4, 10.5, 10.6_

  - [x] 7.9 Property test: Priority Ladder Determinism — MUST (Property 2)
    - **Property 2: Priority Ladder Determinism**
    - Random sinyal setleri farklı priority level'larda → en yüksek priority kazanır; aynı seviyede tie-breaker deterministik
    - **Validates: Requirements CC.3, CC.4, 10.1, 10.2**

  - [x] 7.10 Property test: Monotonic-Safe Transitions — MUST (Property 1)
    - **Property 1: Monotonic-Safe Transitions**
    - Random metrik dizileri + random başlangıç durumları → enforcement artıran sinyal üretilmez; yalnızca ENFORCE→SHADOW
    - **Validates: Requirements CC.2, 4.3, 7.2**

  - [x] 7.11 Property test: Backpressure Hard Block — HOLD — MUST (Property 16)
    - **Property 16: Backpressure Hard Block (HOLD)**
    - Random job creation istekleri backpressure aktifken → HTTP 429, Retry-After, BACKPRESSURE_ACTIVE; job kuyruğa alınmaz; mevcut job'lar devam eder
    - **Validates: Requirements 8.1, 8.2, 8.4**

  - [x] 7.12 Property test: Fail-Safe State Preservation — MUST (Property 17)
    - **Property 17: Fail-Safe State Preservation**
    - Random exception + state kombinasyonları → FAILSAFE state, mevcut guard mode ve PDF acceptance korunur; otomatik downgrade yapılmaz
    - **Validates: Requirements 6.1, 6.2**

  - [x]* 7.13 Property test: Bounded Action Set (Property 3)
    - **Property 3: Bounded Action Set**
    - Random metrik girdileri → signal_type her zaman {switch_to_shadow, restore_enforce, stop_accepting_jobs, resume_accepting_jobs} kümesinde
    - **Validates: Requirements CC.1**

  - [x]* 7.14 Property test: Dwell Time Enforcement (Property 6)
    - **Property 6: Dwell Time Enforcement**
    - Hızlı oscillating metrik dizileri → aynı subsystem için dwell_time_seconds içinde iki geçiş olmaz
    - **Validates: Requirements CC.7, 5.2, 5.6**

  - [x]* 7.15 Property test: Cooldown Period Enforcement (Property 7)
    - **Property 7: Cooldown Period Enforcement**
    - Hızlı sinyal dizileri → cooldown_period_seconds içinde aynı türde sinyal üretilmez; ihlaller loglanır ama aksiyon alınmaz
    - **Validates: Requirements 5.3, 5.4**

  - [x]* 7.16 Property test: Latency Threshold → Shadow Signal (Property 13)
    - **Property 13: Latency Threshold → Shadow Signal**
    - Random latency değerleri enter threshold etrafında → koşullar sağlandığında switch_to_shadow üretilir
    - **Validates: Requirements 4.2, 7.1**

  - [x]* 7.17 Property test: Latency Recovery → Restore Signal (Property 14)
    - **Property 14: Latency Recovery → Restore Signal**
    - Random latency değerleri exit threshold etrafında → adaptive-initiated shadow modunda ve dwell_time dolmuşsa restore_enforce üretilir
    - **Validates: Requirements 7.3**

  - [x]* 7.18 Property test: Queue Depth Threshold → Backpressure Signal (Property 15)
    - **Property 15: Queue Depth Threshold → Backpressure Signal**
    - Random queue depth değerleri enter threshold etrafında → koşullar sağlandığında stop_accepting_jobs üretilir
    - **Validates: Requirements 4.4, 8.3**

  - [x]* 7.19 Property test: KillSwitch Suppresses Adaptive Control (Property 20)
    - **Property 20: KillSwitch Suppresses Adaptive Control**
    - Random killswitch + adaptive kombinasyonları → aktif killswitch'te sinyal üretilmez; deactivation sonrası cooldown_period_seconds beklenir
    - **Validates: Requirements 10.3, 10.4, 10.5**

  - [x]* 7.20 Property test: Oscillation Detection (Property 23)
    - **Property 23: Oscillation Detection**
    - Random transition history'leri → oscillation_max_transitions aşıldığında oscillation_detected alert üretilir
    - **Validates: Requirements 5.5**

  - [x]* 7.21 Property test: Fail-Safe Recovery (Property 19)
    - **Property 19: Fail-Safe Recovery**
    - Random failsafe→healthy dizileri → kaynaklar sağlıklı olduğunda otomatik normal operasyona dönüş
    - **Validates: Requirements 6.7**

  - [x]* 7.22 Unit tests: DecisionEngine + HysteresisFilter + AdaptiveController
    - `test_http_429_response_format` (Req 8.1: 429 response body formatı)
    - `test_retry_after_header_present` (Req 8.1: Retry-After header)
    - `test_concurrent_killswitch_and_adaptive` (edge case: aynı anda killswitch + adaptive)
    - `test_config_update_during_cooldown` (edge case: cooldown sırasında config değişikliği)
    - `test_dwell_time_boundary` (edge case: tam dwell_time sınırında geçiş)
    - `test_failsafe_metric_increment` (Req 6.6: failsafe metrik artışı)
    - `test_failsafe_reason_recorded` (Req 6.8: failsafe nedeni kaydı)
    - `test_shadow_duration_metric` (Req 7.4: shadow süre metriği)
    - `test_override_status_metric` (Req 10.6: override durum metriği)
    - _Requirements: 5.1, 5.2, 6.6, 6.8, 7.4, 8.1, 10.1, 10.2, 10.6_

- [x] 8. Checkpoint — Decision+Orchestrator paketi
  - **Test gate:** Task 7.9 (P2), 7.10 (P1), 7.11 (P16), 7.12 (P17) MUST property'leri %100 geçmeli; 7.13–7.22 varsa onlar da
  - **Invariant gate:** Monotonic-safe (ENFORCE→SHADOW only) kanıtlanmış; 429=HOLD hard block kanıtlanmış; fail-safe'de 0 side-effect kanıtlanmış; priority ladder deterministik; apply_signal() dışında subsystem state değişmez
  - **No-regression gate:** ControlSignal schema freeze; ControlDecisionEvent schema freeze (early freeze — events paketi bu schema'yı kullanacak); mevcut test suite kırılmamış

- [x] 9. Events, Metrics + Alerts (Control_Decision_Event, correlation_id, alert-only path, enforcement alerts)
  - [x] 9.1 `backend/app/adaptive_control/events.py` — ControlDecisionEvent dataclass ve event üretimi
    - ControlDecisionEvent(frozen=True): event_id, correlation_id, reason, previous_mode, new_mode, subsystem_id, transition_timestamp_ms, trigger_metric, trigger_value, threshold, burn_rate, actor
    - `emit_control_decision_event(signal, previous_mode, new_mode)`: structured JSON log + metric counter
    - Mod geçişi olmadan event üretilmez; event üretilmeden mod geçişi yapılmaz
    - _Requirements: CC.6, 3.5, 11.6, 11.7, 11.8_

  - [x] 9.2a `backend/app/adaptive_control/metrics.py` — Minimum viable observability metrikleri (MVP Core)
    - 5 temel metrik tanımı ve emission:
      - `adaptive_control_decisions_total` (Counter, labels: outcome={PASS|HOLD|NOOP}, reason={kapalı küme: budget_exhausted|latency_exceeded|queue_depth_exceeded|backpressure_active|telemetry_insufficient|killswitch_active|disabled|normal})
      - `adaptive_control_enabled` (Gauge, 0/1 — disabled-by-default doğru yansıtılmalı)
      - `adaptive_control_backpressure_active` (Gauge, 0/1)
      - `adaptive_control_telemetry_insufficient_total` (Counter, labels: reason={MIN_SAMPLES|BUCKET_COVERAGE|SOURCE_STALE})
      - `adaptive_control_retry_after_seconds` (Gauge — son Retry-After değeri)
    - Label cardinality kontrolü: tüm label'lar kapalı küme, tenant label yok (v1)
    - DoD: HOLD path → counter + gauge güncelleniyor; telemetry-insufficient → decision counter artmıyor, health counter artıyor; disabled-by-default → enable gauge = 0
    - _Requirements: 11.1, 11.2, 11.3, 8.5_

  - [ ]* 9.2b `backend/app/adaptive_control/metrics.py` — Extended observability (dashboard/advanced) {SOFT:NICE}
    - Histogramlar: adaptive_control_loop_duration_seconds
    - Ek counter'lar: adaptive_control_signal_total, adaptive_guard_mode_transition_total, adaptive_pdf_jobs_rejected_total, adaptive_oscillation_detected_total, adaptive_control_failsafe
    - Ek gauge'lar: adaptive_control_state, adaptive_cooldown_active, adaptive_error_budget_remaining_pct
    - Dashboard JSON, alert rules, advanced label setleri
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 8.5_

  - [x] 9.3 `backend/app/adaptive_control/events.py` — Structured JSON log formatı
    - Her ControlSignal için JSON log: timestamp, signal_type, trigger_metric, trigger_value, threshold, action
    - Fail-safe geçişleri için structured error log: level, component, event, reason, exception_type, current states, correlation_id
    - _Requirements: 11.5, 6.6_

  - [x] 9.4 Property test: Audit Completeness Invariant — MUST (Property 5)
    - **Property 5: Audit Completeness Invariant**
    - Random transitions → her mod geçişinde ControlDecisionEvent üretilir, tüm required field'lar mevcut; error budget exhaustion'da burn_rate dahil
    - **Validates: Requirements CC.6, 3.5, 11.6, 11.7, 11.8**

  - [x]* 9.5 Property test: Metric Emission Completeness (Property 24)
    - **Property 24: Metric Emission Completeness**
    - Random control loop tick'leri → loop_duration, signal_total, state metrikleri emit edilir; guard transition → transition counter; backpressure → gauge; failsafe → counter
    - **Validates: Requirements 11.1, 11.2, 11.3, 11.4, 6.6, 8.5**

  - [x]* 9.6 Property test: Structured Log Format (Property 25)
    - **Property 25: Structured Log Format**
    - Random control signal'leri → JSON log entry tüm required field'ları içerir: timestamp, signal_type, trigger_metric, trigger_value, threshold, action
    - **Validates: Requirements 11.5**

  - [x]* 9.7 Unit tests: Events + Metrics
    - `test_control_decision_event_fields` (Req 11.6: event field completeness)
    - `test_no_transition_without_event` (Req 11.8: event olmadan geçiş yok)
    - `test_backpressure_rejected_jobs_counter` (Req 8.5: reddedilen job sayısı)
    - _Requirements: 11.5, 11.6, 11.7, 11.8_

- [x] 10. Checkpoint — Events/metrics paketi
  - **Test gate:** Task 9.4 (P5) MUST property %100 geçmeli; 9.5–9.7 varsa onlar da
  - **Invariant gate:** Mod geçişi ↔ ControlDecisionEvent bire-bir eşleşme kanıtlanmış; event olmadan geçiş yok, geçiş olmadan event yok
  - **No-regression gate:** ControlDecisionEvent schema freeze; metrik isimleri ve label'lar freeze; mevcut test suite kırılmamış

- [x] 11. Wiring + Integration (Mevcut subsystem'lerle bağlantı)
  - [x] 11.1 `backend/app/adaptive_control/__init__.py` — Modül init ve factory fonksiyonu
    - `create_adaptive_controller(guard_config, guard_decision, pdf_job_store, killswitch_manager, metrics)` factory
    - Tüm bileşenleri oluştur ve wire et
    - _Requirements: 4.1, 7.1, 8.1_

  - [x] 11.2 `backend/app/guard_config.py` — Adaptive control config alanları ekle
    - `adaptive_control_enabled: bool = False` (güvenli varsayılan)
    - Environment variable: `ADAPTIVE_CONTROL_ENABLED`
    - _Requirements: 9.1, 9.4_

  - [x] 11.3 `backend/app/services/pdf_job_store.py` — Backpressure hook entegrasyonu
    - Backpressure aktifken yeni job oluşturma → HTTP 429 + Retry-After + BACKPRESSURE_ACTIVE
    - HOLD semantiği: hard block, kuyruğa alınmaz
    - Mevcut job'lar işlenmeye devam eder
    - `set_backpressure(active: bool, retry_after_seconds: int)` metodu
    - _Requirements: 8.1, 8.2, 8.4_

  - [x]* 11.4 Unit tests: Integration wiring
    - `test_factory_creates_all_components` (wiring doğruluğu)
    - `test_backpressure_hook_in_pdf_job_store` (Req 8.1: 429 response)
    - `test_adaptive_control_disabled_by_default` (güvenli varsayılan)
    - _Requirements: 4.1, 8.1, 9.4_

- [x] 12. Test Harness (Determinism PBT seeded, fixture/matrix, tüm property'lerin entegrasyon doğrulaması)
  - [x] 12.1 `backend/tests/test_adaptive_control_properties.py` — PBT test harness setup
    - Hypothesis settings: `max_examples=100`, `derandomize=True` (seeded determinism)
    - Shared Hypothesis strategies: metric_samples, control_signals, valid_configs (design dokümanındaki generator'lar)
    - Fixture'lar: `make_controller()`, `make_config()`, `make_metrics_collector()` factory helper'ları
    - Tag formatı: `Feature: slo-adaptive-control, Property {N}: {title}`
    - Tüm 25 property test bu dosyada veya ilgili paket test dosyalarında organize edilir
    - _Requirements: Tüm correctness properties (1-25)_

  - [x] 12.2 `backend/tests/test_adaptive_control_edge_cases.py` — Edge case test matrisi
    - `test_empty_allowlist_no_action`: boş allowlist → sıfır sinyal
    - `test_all_sources_stale_suspend`: tüm kaynaklar stale → suspend
    - `test_zero_request_rate_budget`: request_rate=0 → division by zero koruması
    - `test_concurrent_killswitch_and_adaptive`: aynı anda killswitch + adaptive sinyal
    - `test_config_update_during_cooldown`: cooldown sırasında config değişikliği
    - `test_dwell_time_boundary`: tam dwell_time sınırında geçiş
    - _Requirements: CC.5, 6.2, 3.6, 10.3, 5.3, 5.2_

- [x] 13. Final Checkpoint — Tüm testlerin geçtiğinden emin ol
  - **Test gate:** 8 MUST property (P1, P2, P4, P5, P11, P16, P17, P18) %100 geçmeli; tüm optional property'ler ve unit testler de geçmeli
  - **Invariant gate:** 25 correctness property'nin tamamı test edilmiş; monotonic-safe, priority ladder, allowlist isolation, audit completeness, fail-safe, HOLD semantics, budget formula, telemetry-insufficient invariant'ları kanıtlanmış
  - **No-regression gate:** Mevcut 223+ test suite kırılmamış; tüm schema'lar (config, signal, event, metric) freeze durumunda

## Çift Yön Traceability Tablosu (Requirements ↔ Tasks ↔ Tests)

| Requirement | Implement Task(s) | Test/Property |
|-------------|-------------------|---------------|
| 1.1 Metrik toplama | 3.1 | P8, 3.6 unit |
| 1.2 MetricSample format | 3.1 | P8 |
| 1.3 Timestamp kaydı | 3.1 | P8 |
| 1.4 Source stale | 3.1 | P9, 3.6 unit |
| 2.1 SLO sinyal parametreleri | 1.3 | 1.8 unit |
| 2.2 Guard canonical sinyal | 1.3 | 1.8 unit |
| 2.3 PDF canonical sinyal | 1.3 | 1.8 unit |
| 2.4 Config drift test-gated | 1.3 | P10 |
| 2.5 Config drift hatası | 1.3 | P10 |
| 3.1 Guard error budget | 5.1 | P11 |
| 3.2 PDF error budget | 5.1 | P11 |
| 3.3 Budget config formatı | 5.1 | 5.4 unit |
| 3.4 Burn rate threshold | 5.1 | P12 |
| 3.5 Budget exhaustion event | 9.1 | P5 |
| 3.6 Budget formülü | 5.1 | P11 |
| 3.7 Rolling 30d + reset | 5.1 | P11, 5.4 unit |
| 4.1 Control loop | 7.6 | 7.22 unit |
| 4.2 p95 → shadow | 7.4 | P13 |
| 4.3 ENFORCE→SHADOW only | 7.4 | P1 |
| 4.4 Queue → backpressure | 7.5 | P15 |
| 4.5 Budget → koruyucu | 5.1, 7.2 | P12 |
| 4.6 Audit trail | 7.6 | P5 |
| 5.1 Enter/exit threshold | 7.3 | P6, 7.22 unit |
| 5.2 Dwell time | 7.3 | P6, 7.22 unit |
| 5.3 Cooldown period | 7.3 | P7 |
| 5.4 Cooldown log | 7.3 | P7 |
| 5.5 Oscillation detection | 7.3 | P23 |
| 5.6 Hysteresis bypass yok | 7.3 | P6 |
| 6.1 Fail-safe state | 7.6 | P17 |
| 6.2 All stale → suspend | 7.6 | P17, 3.6 unit |
| 6.3 Insufficient → no-op | 3.2, 7.6 | P18 |
| 6.4 Insufficient tanımı | 3.2 | P18, 3.6 unit |
| 6.5 Crash → mod koru | 7.6 | P17 |
| 6.6 Failsafe metrik | 7.6, 9.3 | 7.22 unit, P24 |
| 6.7 Failsafe recovery | 7.6 | P19 |
| 6.8 Failsafe neden kaydı | 7.6 | 7.22 unit |
| 7.1 switch_to_shadow | 7.4, 7.7 | P13 |
| 7.2 ENFORCE→SHADOW only | 7.4 | P1 |
| 7.3 restore_enforce | 7.4, 7.7 | P14 |
| 7.4 Shadow süre metriği | 7.7 | 7.22 unit |
| 7.5 Allowlist guard scope | 7.7 | P4 |
| 8.1 HTTP 429 + HOLD | 7.5, 7.7, 11.3 | P16, 7.22 unit |
| 8.2 HOLD hard block | 7.5, 11.3 | P16 |
| 8.3 resume_accepting | 7.5, 7.7 | P15 |
| 8.4 Mevcut job'lar devam | 7.5, 11.3 | P16 |
| 8.5 Backpressure metrikleri | 9.2a, 9.2b | P24, 9.7 unit |
| 9.1 Config parametreleri | 1.1, 11.2 | P21 |
| 9.2 Config validation | 1.1 | P21 |
| 9.3 Config audit log | 1.1 | P22 |
| 9.4 Env variable yükleme | 1.1, 11.2 | 1.8 unit |
| 9.5 Allowlist targets | 1.2 | P4 |
| 9.6 Boş allowlist → no-op | 1.2 | P4 |
| 9.7 Allowlist audit | 1.2 | P22 |
| 10.1 Priority sırası | 7.2 | P2 |
| 10.2 Tie-breaker | 7.2 | P2 |
| 10.3 KillSwitch → askıya al | 7.8 | P20 |
| 10.4 KillSwitch aktif log | 7.8 | P20 |
| 10.5 KillSwitch deactivation | 7.8 | P20 |
| 10.6 Override metrik | 7.8 | 7.22 unit |
| 11.1 Loop metrikleri | 9.2a, 9.2b | P24 |
| 11.2 Guard transition metrik | 9.2a, 9.2b | P24 |
| 11.3 Backpressure metrik | 9.2a, 9.2b | P24 |
| 11.4 Cooldown/oscillation metrik | 9.2b | P24 |
| 11.5 Structured JSON log | 9.3 | P25 |
| 11.6 ControlDecisionEvent | 9.1 | P5 |
| 11.7 Event + metric counter | 9.1 | P5, 9.7 unit |
| 11.8 Event olmadan geçiş yok | 9.1 | P5, 9.7 unit |
| CC.1 Bounded action set | 7.1 | P3 |
| CC.2 Monotonic-safe | 7.4, 7.6 | P1 |
| CC.3 Priority order | 7.2 | P2 |
| CC.4 Priority ihlali → no-op | 7.2 | P2 |
| CC.5 Allowlist scoping | 1.2, 7.7 | P4 |
| CC.6 Audit event | 9.1 | P5 |
| CC.7 Hysteresis + dwell | 7.3 | P6 |

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve daha hızlı MVP için atlanabilir; ancak MUST PBT listesindeki property'ler asla atlanamaz
- Her görev ilgili gereksinimlere referans verir (traceability)
- Checkpoint'ler 3 kapı kriteri ile gated: test gate, invariant gate, no-regression gate
- Decision Engine ve Orchestrator aynı pakette (Task 7): side-effect boundary'leri birlikte test edilir
- Property testleri evrensel doğruluk özelliklerini doğrular (hypothesis, seeded)
- Unit testler belirli örnekleri ve edge case'leri doğrular
- 25 PBT testi ilgili implementasyon paketlerine dağıtılmıştır (lumped değil)
- Mevcut dosyalar: guard_decision.py, pdf_job_store.py, slo_evaluator.py, guard_config.py, kill_switch.py, metrics_middleware.py
