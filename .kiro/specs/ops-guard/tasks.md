# Uygulama Planı: Ops-Guard

## Genel Bakış

PTF Admin sistemine operasyonel koruma katmanı eklenir. Mevcut altyapı (PTFMetrics, MetricsMiddleware, PrometheusRule, Grafana dashboard) üzerine inşa edilir. Sıralama: config → metrikler → kill-switch → rate limiter → circuit breaker → middleware entegrasyonu → monitoring artifacts.

## Görevler

- [ ] 1. GuardConfig ve yeni PTFMetrics metrikleri
  - [ ] 1.1 `backend/app/guard_config.py` oluştur — GuardConfig pydantic-settings sınıfı (SLO eşikleri, kill-switch varsayılanları, rate limit, circuit breaker ayarları, `OPS_GUARD_` env prefix, HD-4 versiyonlama alanları: `schema_version`, `config_version`, `last_updated_at`) + `GuardDenyReason` enum (HD-3)
    - Güvenli varsayılan değerler, config yükleme hatası durumunda fallback (ASLA reject etme — HD-4)
    - Geçersiz config → `ptf_admin_guard_config_fallback_total` counter + WARNING log
    - _Requirements: 1.1, 7.4, 7.5_
  - [ ] 1.2 `backend/app/ptf_metrics.py` genişlet — yeni metrikler ekle: `ptf_admin_slo_violation_total{slo_name}`, `ptf_admin_sentinel_impossible_state_total`, `ptf_admin_killswitch_state{switch_name}`, `ptf_admin_killswitch_error_total{endpoint_class,error_type}` (HD-1), `ptf_admin_killswitch_fallback_open_total` (HD-1), `ptf_admin_rate_limit_total{endpoint,decision}`, `ptf_admin_circuit_breaker_state{dependency}`, `ptf_admin_guard_config_fallback_total` (HD-4), `ptf_admin_guard_config_schema_mismatch_total` (HD-4), `ptf_admin_guard_config_loaded{schema_version,config_version}` (HD-4) ve ilgili inc/set metodları
    - Kardinalite bütçesine uy (HD-5): label'lar sabit enum, yüksek kardinalite label YASAK
    - Mevcut metrikler ve testler bozulmamalı
    - _Requirements: 1.4, 1.5, 3.5, 4.7, 4.8, 7.1_
  - [ ]* 1.3 Property testleri: GuardConfig round-trip ve env var round-trip
    - **Property 1: GuardConfig Yapılandırma Round-Trip**
    - **Validates: Requirements 1.1**
    - **Property 15: Config Ortam Değişkeni Round-Trip**
    - **Validates: Requirements 7.5**
  - [ ]* 1.4 Property testi: Metrik namespace uyumu
    - **Property 13: Metrik Namespace Uyumu**
    - **Validates: Requirements 7.1**

- [ ] 2. SLI Calculator
  - [ ] 2.1 `backend/app/sli_calculator.py` oluştur — availability hesaplama (2xx+4xx başarılı, 5xx başarısız), import SLI metrikleri (p95 süre, reject oranı, kuyruk derinliği), SLO ihlal kontrolü ve metrik artışı
    - _Requirements: 1.2, 1.3, 1.4, 1.5_
  - [ ]* 2.2 Property testleri: SLI availability hesaplama ve import metrikleri
    - **Property 2: SLI Availability Hesaplama Doğruluğu**
    - **Validates: Requirements 1.2**
    - **Property 3: SLI Import Metrikleri Kaydı**
    - **Validates: Requirements 1.3**
  - [ ]* 2.3 Property testi: SLO/sentinel metrik artışları
    - **Property 4: SLO ve Sentinel Metrik Artışları**
    - **Validates: Requirements 1.4, 1.5**

- [ ] 3. Checkpoint — Config ve SLI testleri
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

- [ ] 4. Kill-Switch Manager
  - [ ] 4.1 `backend/app/kill_switch.py` oluştur — KillSwitchManager sınıfı: global import kill-switch, per-tenant kill-switch, degrade mode; durum değişikliğinde gauge metrik güncelleme ve audit log kaydı; HD-1 failure semantics (high-risk → fail-closed, diğer → fail-open)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
  - [ ] 4.2 Admin API endpoint'leri ekle (`backend/app/main.py` veya ayrı router): `GET /admin/ops/kill-switches`, `PUT /admin/ops/kill-switches/{switch_name}`, `GET /admin/ops/status` — hepsi `require_admin_key()` ile korunmalı
    - _Requirements: 3.7, 3.8, 7.3_
  - [ ]* 4.3 Property testleri: Kill-switch kapsam engelleme ve degrade mode
    - **Property 5: Kill-Switch Kapsam Engelleme**
    - **Validates: Requirements 3.1, 3.2**
    - **Property 6: Degrade Mode Write Engelleme**
    - **Validates: Requirements 3.3**
  - [ ]* 4.4 Property testleri: Kill-switch gözlemlenebilirlik ve API round-trip
    - **Property 7: Kill-Switch Gözlemlenebilirlik**
    - **Validates: Requirements 3.5, 3.6**
    - **Property 8: Kill-Switch API Round-Trip**
    - **Validates: Requirements 3.8**

- [ ] 5. Endpoint Rate Limiter
  - [ ] 5.1 `backend/app/guards/rate_limit_guard.py` oluştur — endpoint kategori eşlemesi (import, heavy_read, default), `check_endpoint_rate_limit()` fonksiyonu, fail-closed politikası, mevcut `check_rate_limit()` altyapısını kullanma
    - _Requirements: 4.1, 4.2, 4.3_
  - [ ]* 5.2 Property testleri: Endpoint rate limit kategorizasyonu ve uygulama
    - **Property 9: Endpoint Rate Limit Kategorizasyonu**
    - **Validates: Requirements 4.1**
    - **Property 10: Rate Limit Uygulama**
    - **Validates: Requirements 4.2**

- [ ] 6. Circuit Breaker
  - [ ] 6.1 `backend/app/guards/circuit_breaker.py` oluştur — CircuitBreaker sınıfı: closed/open/half-open durum makinesi, `allow_request()`, `record_success()`, `record_failure()`, gauge metrik güncelleme
    - _Requirements: 4.4, 4.5, 4.6, 4.8_
  - [ ]* 6.2 Property testleri: Circuit breaker durum makinesi ve guard metrikleri
    - **Property 11: Circuit Breaker Durum Makinesi**
    - **Validates: Requirements 4.4, 4.5, 4.6**
    - **Property 12: Guard Bileşen Metrikleri**
    - **Validates: Requirements 4.7, 4.8**

- [ ] 7. Checkpoint — Guard bileşen testleri
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

- [ ] 8. OpsGuard Middleware ve Entegrasyon
  - [ ] 8.1 `backend/app/ops_guard_middleware.py` oluştur — OpsGuardMiddleware: kill-switch → rate limit → circuit breaker sırasıyla kontrol (HD-2: sıralama sabittir), HD-1 failure semantics uygulanır, mevcut MetricsMiddleware'den sonra eklenir
    - _Requirements: 3.1, 3.2, 3.3, 4.1, 4.2, 4.4_
  - [ ] 8.2 `backend/app/main.py` içinde OpsGuardMiddleware'i app'e ekle, GuardConfig singleton'ı başlat, KillSwitchManager ve CircuitBreaker instance'larını oluştur
    - _Requirements: 7.2, 7.4_
  - [ ]* 8.3 Property testi: Admin auth zorunluluğu
    - **Property 14: Admin Auth Zorunluluğu**
    - **Validates: Requirements 7.3**
  - [ ]* 8.4 Entegrasyon testleri: Middleware zinciri (kill-switch aktif → 503, rate limit aşımı → 429, circuit breaker open → 503)
    - _Requirements: 3.1, 4.2, 4.4_

- [ ] 9. PrometheusRule Alert Kuralları
  - [ ] 9.1 `monitoring/prometheus/ptf-admin-alerts.yml` genişlet — yeni `ptf-admin-ops-guard` grubu: HD-6 zorunlu alert seti (SLO burn-rate fast/slow, kill-switch unexpected toggle, rate limit sustained spike, circuit open sustained, error budget forecast). İsteğe bağlı alert'ler (P2) MVP sonrasına bırakılır.
    - Mevcut `ptf-admin-alerts` grubu değiştirilmemeli
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.9_
  - [ ]* 9.2 Alert yapısal testleri — mevcut `monitoring/tests/test_alert_rules.py` pattern'ini genişlet: yeni alert'lerin severity, labels, annotations, runbook_url alanlarını doğrula
    - _Requirements: 2.9_

- [ ] 10. Runbook Genişletmesi
  - [ ] 10.1 `monitoring/runbooks/ptf-admin-runbook.md` genişlet — her yeni P0/P1 alert için: Belirti, Hızlı Tanı (dashboard link, log sorgusu), Müdahale (kill-switch komutu, rate limit ayarı), Kurtarma (backlog drain, retry), Postmortem bölümleri
    - Mevcut runbook bölümleri değiştirilmemeli
    - _Requirements: 5.1, 5.2, 5.3_
  - [ ]* 10.2 Runbook kapsam testleri — mevcut `monitoring/tests/test_runbook_coverage.py` pattern'ini genişlet: yeni alert'lerin runbook'ta karşılığı olduğunu doğrula
    - _Requirements: 5.2_

- [ ] 11. Dashboard Genişletmesi
  - [ ] 11.1 `monitoring/grafana/ptf-admin-dashboard.json` genişlet — yeni satırlar: "Ops Guard Status" (kill-switch gauge, circuit breaker state, rate limit kararları), "Golden Signals" (latency, traffic, errors, saturation), "Error Taxonomy" (hata kodları dağılımı)
    - Mevcut 4 satır (id: 100-400) değiştirilmemeli
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_
  - [ ]* 11.2 Dashboard yapısal testleri — mevcut `monitoring/tests/test_dashboard_structure.py` pattern'ini genişlet: yeni panel'lerin varlığını ve PromQL sorgularını doğrula
    - _Requirements: 6.5_

- [ ] 12. Final Checkpoint — Tüm testler
  - Tüm testlerin (mevcut 263 + yeni) geçtiğinden emin ol, sorular varsa kullanıcıya sor.

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Her görev spesifik gereksinimleri referans eder (izlenebilirlik)
- Checkpoint'ler artımlı doğrulama sağlar
- Property testleri evrensel doğruluk özelliklerini doğrular (Hypothesis, min 100 iterasyon)
- Unit testler spesifik örnekleri ve edge case'leri doğrular
- Mevcut prod kodu minimal düzeyde değiştirilir (yalnızca PTFMetrics genişletme + middleware ekleme)
