# Uygulama Planı: Fault Injection

## Genel Bakış

Mevcut Ops-Guard koruma katmanını gerçek hata koşullarında doğrulamak için test-only kaos enjeksiyon altyapısı ve entegrasyon testleri oluşturulur. Tüm kod `backend/app/testing/` ve `backend/tests/` dizinlerinde yaşar; production kodu değiştirilmez.

## Görevler

- [x] 1. FaultInjector çekirdek altyapısı
  - [x] 1.1 `backend/app/testing/__init__.py` ve `backend/app/testing/fault_injection.py` oluştur
    - `InjectionPoint` enum (DB_TIMEOUT, EXTERNAL_5XX_BURST, KILLSWITCH_TOGGLE, RATE_LIMIT_SPIKE, GUARD_INTERNAL_ERROR)
    - `InjectionState` dataclass (enabled, params, enabled_at, ttl_seconds)
    - `FaultInjector` singleton (enable, disable, is_enabled, get_params, disable_all, reset_instance)
    - TTL kontrolü monotonic clock ile
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

  - [x] 1.2 `backend/tests/test_fault_injection.py` — FaultInjector property ve unit testleri (15 test)
    - **Property 1: FaultInjector Enable/Disable Round-Trip** ✅
    - **Property 2: FaultInjector TTL Auto-Expiry** ✅
    - Unit test: singleton, enum, disable_all, initial state, zero TTL ✅
    - _Validates: Requirements 1.1–1.6_

- [x] 2. StubServer ve DB Timeout Hook
  - [x] 2.1 `backend/app/testing/stub_server.py` oluştur ✅
  - [x] 2.2 `backend/app/testing/db_timeout_hook.py` oluştur ✅
  - [x] 2.3 `backend/tests/test_stub_server.py` — StubServer property ve unit testleri (7 test) ✅
    - **Property 3: StubServer Fail Count Behavior** ✅
  - [x] 2.4 `backend/tests/test_fi_db_timeout.py` — DB timeout hook property testi ✅
    - **Property 4: DB Timeout Hook Raises TimeoutError** ✅

- [x] 3. Checkpoint — FaultInjector ve yardımcı bileşen testleri ✅
  - 28 test geçti (Task 1 + Task 2)

- [x] 4. Guard Internal Error Hook
  - [x] 4.1 `backend/app/testing/guard_error_hook.py` oluştur ✅
    - _Requirements: 7.1, 7.3_

- [x] 5. S1 — DB Timeout → Circuit Breaker CLOSED→OPEN entegrasyon testi
  - [x] 5.1 S1 entegrasyon testi (`test_fi_db_timeout.py`) ✅
    - CB state OPEN doğrulaması + gauge == 2 + allow_request() == False
  - [x] 5.2 **Property 5: Circuit Breaker Opens Under Sufficient Failures** ✅
    - _Validates: Requirements 3.1, 3.2, 3.3, 4.1_

- [x] 6. S2 — External 5xx Burst → CB Yaşam Döngüsü entegrasyon testi
  - [x] 6.1 `backend/tests/test_fi_cb_lifecycle.py` oluştur ✅
    - StubServer ile gerçek HTTP, tam yaşam döngüsü: CLOSED→OPEN→HALF_OPEN→CLOSED
  - [x] 6.2 **Property 6: Circuit Breaker Lifecycle Metric Sequence** ✅
    - _Validates: Requirements 4.1, 4.2, 4.3, 4.4_

- [x] 7. S3 — KillSwitch Runtime Toggle entegrasyon testi
  - [x] 7.1 `backend/tests/test_fi_killswitch.py` oluştur ✅
    - TestClient ile 503 KILL_SWITCHED + restore flow + gauge toggle
  - [x] 7.2 **Property 7: KillSwitch Toggle Round-Trip with Metrics** ✅
    - _Validates: Requirements 5.1, 5.2, 5.3, 5.4_

- [x] 8. Checkpoint — S1, S2, S3 entegrasyon testleri ✅
  - 45 test geçti (Task 1–7)

- [x] 9. S4 — Rate Limit Spike entegrasyon testi
  - [x] 9.1 `backend/tests/test_fi_rate_limit.py` oluştur ✅
    - 429 + Retry-After + metric counter doğrulaması + window reset
  - [x] 9.2 **Property 8: Rate Limit Enforcement with Metrics** ✅
  - [x] 9.3 **Property 9: Rate Limit Determinism** ✅
    - _Validates: Requirements 6.1, 6.2, 6.3, 6.4_

- [x] 10. S5 — Guard Internal Error → Fail-Open entegrasyon testi
  - [x] 10.1 `backend/tests/test_fi_guard_error.py` oluştur ✅
    - Monkeypatch + FaultInjector hook → fail-open doğrulaması
    - _Validates: Requirements 7.1, 7.2, 7.3_

- [x] 11. Alert PromQL Doğrulama Testleri
  - [x] 11.1 `backend/app/testing/alert_validator.py` oluştur ✅
    - AlertValidator: YAML parse, basitleştirilmiş PromQL eval, UTF-8 encoding
  - [x] 11.2 `backend/tests/test_fi_alert_validation.py` oluştur ✅
    - S1 → PTFAdminCircuitBreakerOpen, S4 → PTFAdminRateLimitSpike, S5 → PTFAdminGuardInternalError
    - _Validates: Requirements 8.1, 8.2, 8.3, 8.4_

- [x] 12. Final Checkpoint — Tüm testler ✅
  - 60 yeni fault injection testi geçti
  - 424 mevcut backend testi geçti (kırılma yok)
  - 171 monitoring testi geçti (kırılma yok)
  - Production kodu değiştirilmedi
  - _Validates: Requirements 9.1, 9.2, 9.3, 9.4_

## Kanıt Özeti

| Kategori | Test Sayısı | Durum |
|----------|------------|-------|
| Fault Injection (yeni) | 60 | ✅ PASSED |
| Backend (mevcut) | 424 | ✅ PASSED |
| Monitoring (mevcut) | 171 | ✅ PASSED |
| **Toplam** | **655** | ✅ |

## Oluşturulan Dosyalar

### Production-free injection altyapısı (`backend/app/testing/`)
- `__init__.py` — paket tanımı
- `fault_injection.py` — FaultInjector singleton + InjectionPoint enum + InjectionState
- `stub_server.py` — In-process HTTP stub server
- `db_timeout_hook.py` — DB timeout injection hook
- `guard_error_hook.py` — Guard internal error injection hook
- `alert_validator.py` — PromQL alert evaluation helper

### Test dosyaları (`backend/tests/`)
- `test_fault_injection.py` — Property 1, 2 + unit tests (15 test)
- `test_stub_server.py` — Property 3 + unit tests (7 test)
- `test_fi_db_timeout.py` — Property 4, 5 + S1 entegrasyon (8 test)
- `test_fi_cb_lifecycle.py` — Property 6 + S2 entegrasyon (2 test)
- `test_fi_killswitch.py` — Property 7 + S3 entegrasyon (4 test)
- `test_fi_rate_limit.py` — Property 8, 9 + S4 entegrasyon (6 test)
- `test_fi_guard_error.py` — S5 entegrasyon (5 test)
- `test_fi_alert_validation.py` — Alert PromQL doğrulama (15 test)

## Notlar

- Tüm property testleri Hypothesis ile min 100 iterasyon çalışır
- `st.from_regex(...)` kullanılmadı; kompozisyonel stratejiler tercih edildi
- Production behavior değişmedi; injection sadece test-time monkeypatch ile aktif
- `ptf_admin_` metrik namespace korundu
- Guard zinciri sırası (HD-2): KillSwitch → RateLimiter → CircuitBreaker → Handler
