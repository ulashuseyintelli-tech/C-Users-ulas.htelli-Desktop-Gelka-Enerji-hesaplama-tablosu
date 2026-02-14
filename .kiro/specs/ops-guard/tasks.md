# Uygulama Planı: Ops-Guard

## Genel Bakış

PTF Admin sistemine operasyonel koruma katmanı eklenir. Mevcut altyapı (PTFMetrics, MetricsMiddleware, PrometheusRule, Grafana dashboard) üzerine inşa edilir. Katman: backend middleware (FastAPI). Sıralama: config → skeleton → normalization → kill-switch → rate limiter → circuit breaker → decision precedence → alerts → runbook/dashboard → final checkpoint.

## Scope / Non-goals

**Scope:** GuardConfig, kill-switch, endpoint rate limiter, circuit breaker, OpsGuardMiddleware, monitoring artifacts (alerts, runbook, dashboard genişletmesi).

**Non-goals:** Mevcut PTFMetrics/MetricsMiddleware semantiğini değiştirmek yok. Mevcut `ptf-admin-alerts` grubundaki alert'ler korunur. Ingress/gateway katmanı kapsam dışı.

## Kilitli Kararlar (tasks.md başına sabit)

- Invalid config → fallback defaults + `ptf_admin_guard_config_fallback_total` metric + WARNING log (ASLA reject etme — HD-4)
- `config_version` → hash (deterministik) + log'da görünür (HD-4)
- Guard sıralaması sabit: KillSwitch → RateLimiter → CircuitBreaker → Handler (HD-2)
- Kill-switch failure: high-risk → fail-closed, diğer → fail-open (HD-1)
- Rate limiter failure: fail-closed (HD-3)
- Circuit breaker failure: fail-open
- `disableNameSuffixHash: true` (Grafana pickup stabil)
- Kardinalite bütçesi: HD-5 sabit enum'lar, yüksek kardinalite label YASAK
- `ptf_admin_` namespace korunur, yeni namespace yok (Req 7.1)

## Görevler

- [x] 1. GuardConfig + schema validation ✅ DONE
  - **Evidence:** `test_guard_config.py` → 26 passed; `pytest backend/tests -q` → 1151 passed, 0 failed
  - [x] 1.1 `backend/app/guard_config.py` oluştur:
    - `GuardConfig(BaseSettings)` — `OPS_GUARD_` env prefix, `extra="ignore"`
    - Alanlar: `schema_version`, `config_version`, `last_updated_at` (HD-4 versiyonlama)
    - SLO eşikleri: `slo_availability_target=0.995`, `slo_p95_latency_ms=300`, `slo_p99_latency_ms=800`
    - Kill-switch varsayılanları: `killswitch_global_import_disabled=False`, `killswitch_degrade_mode=False`, `killswitch_disabled_tenants=""`
    - Rate limit: `rate_limit_import_per_minute=10`, `rate_limit_heavy_read_per_minute=120`, `rate_limit_default_per_minute=60`
    - Circuit breaker: `cb_error_threshold_pct=50.0`, `cb_open_duration_seconds=30.0`, `cb_half_open_max_requests=3`, `cb_window_seconds=60.0`
    - `GuardDenyReason(str, Enum)`: `KILL_SWITCHED`, `RATE_LIMITED`, `CIRCUIT_OPEN`, `INTERNAL_ERROR` (HD-3)
    - Geçersiz config → fallback defaults + metric + WARNING log
  - [x] 1.2 `backend/app/ptf_metrics.py` genişlet — yeni metrikler:
    - `ptf_admin_guard_config_fallback_total` (Counter, label'sız)
    - `ptf_admin_guard_config_schema_mismatch_total` (Counter, label'sız)
    - `ptf_admin_guard_config_loaded{schema_version, config_version}` (Gauge)
    - `ptf_admin_slo_violation_total{slo_name}` (Counter, slo_name sabit enum)
    - `ptf_admin_sentinel_impossible_state_total` (Counter, label'sız)
    - `ptf_admin_killswitch_state{switch_name}` (Gauge)
    - `ptf_admin_killswitch_error_total{endpoint_class, error_type}` (Counter)
    - `ptf_admin_killswitch_fallback_open_total` (Counter, label'sız)
    - `ptf_admin_rate_limit_total{endpoint, decision}` (Counter)
    - `ptf_admin_circuit_breaker_state{dependency}` (Gauge)
    - İlgili `inc_*` / `set_*` metodları
    - Mevcut metrikler ve testler bozulmamalı
  - [x] 1.3 Unit testler: `backend/tests/test_guard_config.py`
    - Valid config parse, invalid config fallback, env var override
    - `ptf_admin_guard_config_fallback_total` artışı doğrulama
    - Mevcut test suite kırılmıyor
  - _Requirements: 1.1, 1.4, 1.5, 3.5, 4.7, 4.8, 7.1, 7.4, 7.5_
  - **DoD:** Unit test yeşil; `GuardConfig()` default'larla oluşuyor; invalid config → fallback + metric; mevcut testler kırılmıyor
  - **Rollback:** `guard_config.py` sil, `ptf_metrics.py` değişikliklerini geri al
  - [ ]* 1.4 Property testleri: GuardConfig round-trip + env var round-trip + metrik namespace uyumu
    - **Property 1: GuardConfig Yapılandırma Round-Trip**
    - **Validates: Requirements 1.1**
    - **Property 13: Metrik Namespace Uyumu**
    - **Validates: Requirements 7.1**
    - **Property 15: Config Ortam Değişkeni Round-Trip**
    - **Validates: Requirements 7.5**

- [x] 2. OpsGuardMiddleware skeleton (no-op) ✅ DONE
  - **Evidence:** `test_ops_guard_middleware.py` → 4 passed; mevcut suite kırılmadı
  - [x] 2.1 `backend/app/ops_guard_middleware.py` oluştur:
    - `OpsGuardMiddleware(BaseHTTPMiddleware)` — request context üretir (route template, method, actor bucket)
    - "decision" object üretir ama her zaman ALLOW döner (no-op)
    - `ptf_admin_ops_guard_requests_total{decision="allow"}` metric (opsiyonel, veya mevcut rate_limit_total kullan)
    - Mevcut `MetricsMiddleware`'den sonra, handler'dan önce eklenir
  - [x] 2.2 `backend/app/main.py` içinde middleware'i app'e ekle (MetricsMiddleware'den sonra)
    - GuardConfig singleton başlat
  - [x] 2.3 Integration test: middleware aktif, mevcut handler'lar aynı çalışıyor, mevcut testler kırılmıyor
  - _Requirements: 7.2_
  - **DoD:** Integration test yeşil; middleware aktif ama davranış değiştirmiyor; mevcut test suite kırılmıyor
  - **Rollback:** Middleware'i `main.py`'den kaldır, `ops_guard_middleware.py` sil

- [x] 3. Endpoint normalization + cardinality policy ✅ DONE
  - **Artifacts:** `backend/app/endpoint_normalization.py` (tek otorite), `backend/app/metrics_middleware.py` (centralized import)
  - **Evidence:** `test_endpoint_normalization.py` → 35 passed (25 unit + 10 PBT) in 12s; `pytest backend/tests -q` → 1151 passed, 0 failed
  - [x] 3.1 Route template resolve: mevcut `MetricsMiddleware._sanitize_path()` mantığını paylaş veya import et
    - `/v1/markets/:id` gibi template'ler
    - Query parametreleri strip
    - Actor keying: bounded (ip hash / user id hash), raw değil
  - [x] 3.2 Unit test: random path/query → template bounded set
  - _Requirements: 7.1 (HD-5 cardinality budget)_
  - **DoD:** Unit test yeşil; path normalization deterministik; kardinalite bounded ✅
  - **Rollback:** Normalization fonksiyonlarını sil
  - [x]* 3.3 Property test: random path/query → template bounded set (Hypothesis)

- [x] 4. Kill-switch (hard/soft modes) ✅ DONE
  - **Artifacts:** `backend/app/kill_switch.py`, admin API endpoints in `main.py`
  - **Evidence:** `test_kill_switch.py` → 32 passed; `pytest backend/tests -q` → 1183 passed, 0 failed
  - [x] 4.1 `backend/app/kill_switch.py` oluştur:
    - `KillSwitchManager` sınıfı: `is_import_disabled()`, `is_degrade_mode()`, `set_switch()`, `get_all_switches()`, `get_disabled_tenants()`
    - Hard mode: endpoint kapalı → HTTP 503 + deterministic error code
    - Soft mode: sadece warn + metric, request geçer
    - HD-1 failure semantics: high-risk → fail-closed, diğer → fail-open + `ptf_admin_killswitch_fallback_open_total`
    - Audit log: `[KILLSWITCH] actor={actor} switch={name} old={old} new={new} timestamp={ts}`
    - `ptf_admin_killswitch_state{switch_name}` gauge güncelleme
  - [x] 4.2 Admin API endpoint'leri (`backend/app/main.py` veya ayrı router):
    - `GET /admin/ops/kill-switches` — tüm switch durumları, `require_admin_key()`
    - `PUT /admin/ops/kill-switches/{switch_name}` — durum değiştir, `require_admin_key()`
    - `GET /admin/ops/status` — guard durumu özeti, `require_admin_key()`
  - [x] 4.3 Unit testler: `backend/tests/test_kill_switch.py`
    - Hard/soft mode, lookup fail (fail-closed vs fail-open), audit log, metric artışı
    - Admin API: auth kontrolü, round-trip (PUT → GET)
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 7.3_
  - **DoD:** Unit test yeşil; kill-switch hard/soft çalışıyor; admin API auth korumalı; metric doğru
  - **Rollback:** `kill_switch.py` sil, admin endpoint'leri `main.py`'den kaldır
  - [ ]* 4.4 Property testleri: kill-switch kapsam engelleme, degrade mode, gözlemlenebilirlik, API round-trip
    - **Property 5: Kill-Switch Kapsam Engelleme**
    - **Validates: Requirements 3.1, 3.2**
    - **Property 6: Degrade Mode Write Engelleme**
    - **Validates: Requirements 3.3**
    - **Property 7: Kill-Switch Gözlemlenebilirlik**
    - **Validates: Requirements 3.5, 3.6**
    - **Property 8: Kill-Switch API Round-Trip**
    - **Validates: Requirements 3.8**

- [x] 5. Endpoint rate limiter (deterministic + bounded) ✅ DONE
  - **Artifacts:** `backend/app/guards/rate_limit_guard.py`, `backend/tests/test_rate_limit_guard.py`
  - **Evidence:** `test_rate_limit_guard.py` → 28 passed in 0.72s; full suite → 800 passed, 0 failed  - [x] 5.1 `backend/app/guards/rate_limit_guard.py` oluştur:
    - Fixed window + burst (sliding window yerine — daha deterministik)
    - Key: `(routeTemplate, method, actorBucket)`
    - Endpoint kategorileri: import, heavy_read, default → farklı limit eşikleri
    - Over-limit: HTTP 429 + `Retry-After` header
    - Fail-closed politikası (rate limiter iç hatası → reject)
    - `ptf_admin_rate_limit_total{endpoint, decision}` metric
  - [x] 5.2 Unit testler: `backend/tests/test_rate_limit_guard.py`
    - Allow/deny, reset window, kategori eşlemesi, fail-closed
  - _Requirements: 4.1, 4.2, 4.3_
  - **Evidence:** `test_rate_limit_guard.py` → 28 passed in 0.72s; full suite → 800 passed, 0 failed
  - **DoD:** Unit test yeşil; rate limit deterministik; 429 + Retry-After doğru ✅
  - **Rollback:** `guards/rate_limit_guard.py` sil
  - [ ]* 5.3 Property testleri: endpoint rate limit kategorizasyonu ve uygulama
    - **Property 9: Endpoint Rate Limit Kategorizasyonu**
    - **Validates: Requirements 4.1**
    - **Property 10: Rate Limit Uygulama**
    - **Validates: Requirements 4.2**

- [x] 6. Circuit breaker (dependency-scoped) ✅ DONE
  - **Artifacts:** `backend/app/guards/circuit_breaker.py`, `backend/tests/test_circuit_breaker.py`
  - **Evidence:** `test_circuit_breaker.py` → 35 passed in 0.70s; ops-guard suite → 190 passed, 0 failed
  - [x] 6.1 `backend/app/guards/circuit_breaker.py` oluştur:
    - `CircuitBreaker` sınıfı: closed → open → half-open → closed durum makinesi
    - `allow_request()`, `record_success()`, `record_failure()`
    - Dependency enum: `db_primary`, `db_replica`, `cache`, `external_api`, `import_worker` (HD-5)
    - Open criteria: consecutive failures veya error-rate eşiği
    - Half-open probing: düşük QPS (max `cb_half_open_max_requests`)
    - `ptf_admin_circuit_breaker_state{dependency}` gauge güncelleme
  - [x] 6.2 Unit testler: `backend/tests/test_circuit_breaker.py`
    - closed→open→half-open→closed geçişleri, zamanlama, metric doğrulama
  - _Requirements: 4.4, 4.5, 4.6, 4.8_
  - **DoD:** Unit test yeşil; durum makinesi doğru; metric doğru
  - **Rollback:** `guards/circuit_breaker.py` sil
  - [ ]* 6.3 Property testleri: circuit breaker durum makinesi ve guard metrikleri
    - **Property 11: Circuit Breaker Durum Makinesi**
    - **Validates: Requirements 4.4, 4.5, 4.6**
    - **Property 12: Guard Bileşen Metrikleri**
    - **Validates: Requirements 4.7, 4.8**

- [x] 7. Decision precedence + error mapping ✅ DONE
  - **Artifacts:** `backend/app/ops_guard_middleware.py` (rewritten), `backend/tests/test_ops_guard_middleware.py` (rewritten), `backend/tests/conftest.py` (autouse fixture)
  - **Evidence:** `test_ops_guard_middleware.py` → 16 passed; full suite → 847 passed, 0 failed
  - [x] 7.1 `ops_guard_middleware.py` güncelle — no-op'tan gerçek guard zincirine geç:
    - Sıralama (HD-2): KillSwitch → RateLimiter → CircuitBreaker → Handler
    - HD-1 failure semantics uygulanır
    - `GuardDenyReason` enum ile deterministic error code
    - Error code map sabit ve frontend telemetry ile uyumlu (`ptf_admin.ops_guard_*`)
  - [x] 7.2 Unit testler: precedence (kill-switch aktif → rate limit atlanır), error code map
  - [x] 7.3 Integration testler: middleware zinciri (kill-switch aktif → 503, rate limit aşımı → 429, circuit breaker open → 503)
  - _Requirements: 3.1, 4.2, 4.4, 7.2_
  - **DoD:** Unit + integration test yeşil; precedence doğru; error code'lar deterministik
  - **Rollback:** Middleware'i no-op'a geri al
  - [ ]* 7.4 Property testi: Admin auth zorunluluğu
    - **Property 14: Admin Auth Zorunluluğu**
    - **Validates: Requirements 7.3**

- [x] 8. PrometheusRule alert kuralları ✅ DONE
  - **Evidence:** `monitoring/tests/ → 171 passed`; `backend/tests/ → 1258 passed, 0 failed`; mevcut 9 alert korundu
  - [x] 8.1 `monitoring/prometheus/ptf-admin-alerts.yml` genişletildi — yeni `ptf-admin-ops-guard` grubu (7 alert):
    - PTFAdminKillSwitchActivated: `max(ptf_admin_killswitch_state) == 1` — for: 0m, severity: critical
    - PTFAdminCircuitBreakerOpen: `max(ptf_admin_circuit_breaker_state) == 2` — for: 5m, severity: critical
    - PTFAdminRateLimitSpike: rate limit deny > 5 req/min — for: 2m, severity: warning
    - PTFAdminGuardConfigInvalid: config fallback artışı — for: 5m, severity: warning
    - PTFAdminGuardInternalError: killswitch error / fail-open — for: 5m, severity: critical
    - PTFAdminSLOBurnRateFast: 1h error rate > 1% — for: 5m, severity: critical
    - PTFAdminSLOBurnRateSlow: 6h error rate > 0.5% — for: 30m, severity: warning
    - Her alert'e `runbook_url`, `summary`, `description` annotation'ları eklendi
    - Mevcut `ptf-admin-alerts` grubu (9 alert) DEĞİŞTİRİLMEDİ
  - [x] 8.2 Alert yapısal testleri — `monitoring/tests/test_alert_rules.py` genişletildi:
    - TestOpsGuardAlertGroup: grup varlığı, orijinal grup korunması, toplam 2 grup, 7 alert sayısı
    - TestOpsGuardAlertCompleteness: severity, for, labels, annotations, service label, runbook anchor
    - TestOpsGuardAlertExpressions: her alert'in PromQL doğrulaması
    - Mevcut testler (9 alert) kırılmadı
  - [x] 8.3 Runbook genişletildi — 7 yeni ops-guard alert bölümü eklendi (mevcut bölümler korundu)
  - [x] 8.4 Runbook coverage + property testleri güncellendi — tüm grupları tarıyor
  - [x] 8.5 Deploy structure + alert properties testleri güncellendi — tüm grupları tarıyor
  - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.9_
  - **DoD:** `kustomize build` hatasız; alert testleri yeşil; mevcut alert'ler bozulmamış ✅
  - **Rollback:** Yeni alert grubunu YAML'dan kaldır

- [ ] 9. Runbook + dashboard panel ekleri
  - [ ] 9.1 `monitoring/runbooks/ptf-admin-runbook.md` genişlet — her yeni P0/P1 alert için:
    - Belirti, Hızlı Tanı (dashboard link, log sorgusu), Müdahale (kill-switch komutu, rate limit ayarı), Kurtarma, Postmortem
    - "Kill-switch nasıl aç/kapat", "rate limit tuning", "circuit breaker reset" prosedürleri
    - Mevcut runbook bölümleri DEĞİŞTİRİLMEMELİ
  - [ ] 9.2 `monitoring/grafana/ptf-admin-dashboard.json` genişlet — yeni satır(lar):
    - "Ops Guard Status": kill-switch gauge, circuit breaker state, rate limit karar dağılımı, top endpoints (bounded)
    - Mevcut 4 satır (id: 100-400) DEĞİŞTİRİLMEMELİ
  - [ ] 9.3 Monitoring testleri:
    - `test_runbook_coverage.py` / `test_runbook_properties.py` genişlet: yeni alert'lerin runbook'ta karşılığı
    - `test_dashboard_structure.py` / `test_dashboard_properties.py` genişlet: yeni panel'lerin varlığı
  - _Requirements: 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5_
  - **DoD:** Runbook/dashboard testleri yeşil; mevcut bölümler bozulmamış
  - **Rollback:** Yeni runbook bölümlerini ve dashboard satırlarını kaldır

- [ ] 10. Final checkpoint
  - [ ] 10.1 Full test suite yeşil (backend + monitoring, mevcut + yeni)
  - [ ] 10.2 "no-op → kill-switch → rate limit → breaker" minimal e2e senaryoları (2-3 integration test)
  - [ ] 10.3 `kustomize build overlays/production` hatasız
  - [ ] 10.4 Checkpoint özeti: test sayısı, yeni metrik listesi, alert listesi
  - **DoD:** Tüm testler yeşil; e2e senaryolar geçiyor; kustomize build hatasız

## Notes

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Her görev spesifik gereksinimlere referans verir (izlenebilirlik)
- Property testleri Hypothesis ile, min 100 iterasyon, bounded generator + seed sabit
- Mevcut prod kodu minimal düzeyde değiştirilir: PTFMetrics genişletme + middleware ekleme
- Guard katmanı backend middleware olarak çalışır (FastAPI `BaseHTTPMiddleware`)
- Hedef namespace: `monitoring`, Prometheus label: `prometheus: kube-prometheus`
- **PBT Perf Rule:** `st.from_regex(...)` kullanmaktan kaçın (yavaş). Tercih: `st.text(alphabet=...)`, `st.lists(st.sampled_from(...))`, `st.one_of(...)`, `st.integers(...)` + küçük size sınırları. Task 3'te `from_regex` → compositional geçişi 60s+ timeout'u 12s'e düşürdü.
