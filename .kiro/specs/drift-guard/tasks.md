# Uygulama Planı: Drift Guard for Guard Decision Middleware

## Genel Bakış

Guard Decision Middleware pipeline'ına drift detection alt-sistemi eklenir. Kill-switch ile tamamen devre dışı bırakılabilir (0 call garantisi). Stub provider/evaluator ile başlanır — wiring kırmadan pipeline'a girer.

Uygulama sırası: scaffolding → interfaces → wiring → tests. Kill-switch precedence tasarım seviyesinde "return early" olarak kilitlenir.

## Görevler

- [x] 0. Scaffolding — config + kill-switch hook
  - [x] 0.1 `backend/app/guard_config.py` — `drift_guard_enabled: bool = False` ve `drift_guard_killswitch: bool = False` config alanları ekle
    - Env var: `OPS_GUARD_DRIFT_GUARD_ENABLED`, `OPS_GUARD_DRIFT_GUARD_KILLSWITCH`
    - Varsayılan: her ikisi de `False` (güvenli varsayılan)
    - _Requirements: DR1.2, DR1.3, DR2.5_
    - **Evidence:** `guard_config.py::GuardConfig` → `drift_guard_enabled: bool = False`, `drift_guard_killswitch: bool = False` | `_FALLBACK_DEFAULTS` → both False | Tests: `test_drift_guard.py::TestDriftGuardConfig` (3 tests)

  - [x] 0.2 `backend/app/guard_config.py` — `_FALLBACK_DEFAULTS` dict'ine yeni alanları ekle
    - `drift_guard_enabled=False`, `drift_guard_killswitch=False`
    - _Requirements: DR1.2_
    - **Evidence:** `guard_config.py::_FALLBACK_DEFAULTS` → `drift_guard_enabled=False, drift_guard_killswitch=False`

- [x] 1. Provider interface + stub implementasyon
  - [x] 1.1 `backend/app/guards/drift_guard.py` — DriftReasonCode enum, DriftInput, DriftDecision dataclass'ları
    - `DriftReasonCode(str, Enum)`: PROVIDER_ERROR, THRESHOLD_EXCEEDED, INPUT_ANOMALY (kapalı küme, DRIFT: prefix)
    - `DriftInput(frozen=True)`: endpoint, method, tenant_id, request_signature, timestamp_ms
    - `DriftDecision(frozen=True)`: is_drift, reason_code, detail, would_enforce
    - _Requirements: DR3.7_
    - **Evidence:** `drift_guard.py` → `DriftReasonCode` (3 members, DRIFT: prefix), `DriftInput` (frozen), `DriftDecision` (frozen) | Tests: `test_drift_guard.py::TestDriftReasonCode` (4 tests), `TestDriftInput` (2 tests), `TestDriftDecision` (3 tests)

  - [x] 1.2 `backend/app/guards/drift_guard.py` — DriftInputProvider protocol + StubDriftInputProvider
    - Protocol: `get_input(request, endpoint, method, tenant_id) → DriftInput`
    - Stub: her zaman geçerli DriftInput döner (no-drift baseline)
    - _Requirements: DR1.1_
    - **Evidence:** `drift_guard.py::DriftInputProvider` (Protocol), `StubDriftInputProvider` | Tests: `test_drift_guard.py::TestStubDriftInputProvider` (1 test)

- [x] 2. Evaluator interface + stub implementasyon
  - [x] 2.1 `backend/app/guards/drift_guard.py` — `evaluate_drift(drift_input) → DriftDecision`
    - Pure function, stub: her zaman `is_drift=False` döner
    - Gerçek implementasyon ileride threshold-based olacak
    - _Requirements: DR1.1_
    - **Evidence:** `drift_guard.py::evaluate_drift()` → stub, always `DriftDecision(is_drift=False)` | Tests: `test_drift_guard.py::TestEvaluateDrift` (1 test)

- [x] 3. Middleware wiring + snapshot + wouldEnforce semantics
  - [x] 3.1 `backend/app/guards/guard_decision_middleware.py` — drift step wiring
    - Kill-switch check EN ÜSTTE (return early, 0 call garantisi)
    - `drift_guard_enabled` check
    - Provider call + exception handling
    - evaluate_drift call
    - Mode dispatch: shadow → log + proceed + wouldEnforce, enforce → 503
    - Drift reason codes snapshot'a eklenir
    - _Requirements: DR1.1, DR1.4, DR2.1–DR2.6, DR3.1–DR3.6, DR4.1–DR4.4_
    - **Evidence:** `guard_decision_middleware.py::_evaluate_decision()` → drift step wired after tenant mode OFF check, before snapshot build. Kill-switch + enabled check → StubDriftInputProvider → evaluate_drift → mode dispatch (shadow: log+proceed, enforce: 503 block)

  - [x] 3.2 `backend/app/ptf_metrics.py` — `ptf_admin_drift_evaluation_total{mode, outcome}` counter
    - outcome: `no_drift|drift_detected|provider_error`
    - mode: `shadow|enforce`
    - Bounded: 2 × 3 = 6 zaman serisi
    - _Requirements: DR5.4, DR5.5_
    - **Evidence:** `ptf_metrics.py::PTFMetrics._drift_evaluation_total` Counter with labelnames=["mode", "outcome"]

  - [x] 3.3 `backend/app/ptf_metrics.py` — `inc_drift_evaluation(mode, outcome)` metodu
    - Outcome validation: sadece `no_drift|drift_detected|provider_error`
    - Mode validation: sadece `shadow|enforce`
    - _Requirements: DR5.4_
    - **Evidence:** `ptf_metrics.py::PTFMetrics.inc_drift_evaluation()` with `_VALID_DRIFT_MODES` and `_VALID_DRIFT_OUTCOMES` frozensets | Tests: `test_drift_guard.py::TestDriftMetrics` (4 tests)

- [x] 4. Test paketi
  - [x] 4.1 Unit testler: DriftReasonCode enum, DriftInput, DriftDecision dataclass'ları
    - Kapalı küme: 3 reason code, hepsi DRIFT: prefix
    - Frozen: DriftInput ve DriftDecision immutable
    - _Requirements: DR3.7_
    - **Evidence:** `test_drift_guard.py::TestDriftReasonCode` (4 tests), `TestDriftInput` (2 tests), `TestDriftDecision` (3 tests)

  - [x] 4.2 Unit testler: StubDriftInputProvider + evaluate_drift stub
    - Stub provider geçerli DriftInput döner
    - Stub evaluator is_drift=False döner
    - _Requirements: DR1.1_
    - **Evidence:** `test_drift_guard.py::TestStubDriftInputProvider` (1 test), `TestEvaluateDrift` (1 test)

  - [x] 4.3 Unit testler: Config alanları
    - drift_guard_enabled varsayılan False
    - drift_guard_killswitch varsayılan False
    - _Requirements: DR1.2, DR1.3_
    - **Evidence:** `test_drift_guard.py::TestDriftGuardConfig` (3 tests)

  - [x] 4.9 Provider failure semantiği (shadow vs enforce)
    - Shadow: provider throws → proceed + DRIFT:PROVIDER_ERROR + metric++
    - Enforce + fail_open=true: provider throws → proceed + DRIFT:PROVIDER_ERROR + metric++
    - Enforce + fail_open=false: provider throws → 503 + next not called + DRIFT:PROVIDER_ERROR
    - Disabled: provider not called even if configured to throw (e2e)
    - Shadow + drift → wouldEnforce=true
    - _Requirements: DR4.1, DR4.2, DR4.3, DR4.4, DR4.5, DR4.6, DR4.7, DR6.1_
    - **Evidence:** `test_drift_guard_middleware_v0.py::TestDGM49ProviderFailure` (4 tests)

  - [x] 4.10 Disabled mode: provider not called
    - drift_guard_enabled=false → provider.get_input 0 call
    - drift_guard_enabled=false → evaluate_drift 0 call
    - drift_guard_enabled=false → drift metrikleri 0 call
    - _Requirements: DR1.3, DR3.6_
    - **Evidence:** `test_drift_guard_middleware_v0.py::TestDGM410DisabledMode` (3 tests)

  - [x] 4.11 Kill-switch short-circuit (4'lü spy)
    - **Hard invariant: kill-switch ON → drift subsystem tamamen görünmez**
    - Parametrik: shadow + kill-switch ON, enforce + kill-switch ON
    - 4'lü spy assertion:
      - `provider.get_input` → 0 call
      - `evaluate_drift` → 0 call
      - drift metrikleri → 0 call
      - drift telemetry/log enrichment → 0 call
    - Ek: guardDecision snapshot'ında DRIFT:* reason code yok
    - Ek: wouldEnforce drift kaynaklı set edilmemiş
    - _Requirements: DR2.1, DR2.2, DR2.3, DR2.4, DR2.5, DR2.6, DR6.3_
    - **Evidence:** `test_drift_guard_middleware_v0.py::TestDGM411KillSwitch` (2 tests, parametrik shadow+enforce)

  - [x] 4.12 Mode dispatch: shadow log + proceed, enforce 503
    - Shadow + drift detected → call_next çağrılır + DRIFT:THRESHOLD_EXCEEDED reason
    - Enforce + drift detected → 503 + call_next çağrılmaz
    - No drift → her iki modda proceed
    - _Requirements: DR3.1, DR3.2, DR3.4, DR3.5_
    - **Evidence:** `test_drift_guard_middleware_v0.py::TestDGM412ModeDispatch` (3 tests)

  - [x] 4.13 wouldEnforce mikro ayar
    - Shadow + drift → wouldEnforce=true
    - Disabled → wouldEnforce=false (drift kaynaklı)
    - Kill-switch ON → wouldEnforce=false (drift kaynaklı)
    - _Requirements: DR6.1, DR6.2, DR6.3_
    - **Evidence:** `test_drift_guard_middleware_v0.py::TestDGM413WouldEnforce` (3 tests)

  - [x] 4.14 Mode resolution tek kaynak testi
    - Drift step resolve_effective_mode(tenant_mode, risk_class) kullanır
    - ENFORCE + LOW → SHADOW downgrade drift step'te de geçerli
    - Drift step ve snapshot build aynı effective_mode'u hesaplar
    - _Requirements: DR8.1, DR8.2, DR8.3_
    - **Evidence:** `test_drift_guard_v0.py::TestModeResolutionSingleSource` (4 tests)

  - [x] 4.15 Baseline testleri
    - DriftBaseline frozen, startup'ta hesaplanır
    - config_hash mismatch → DRIFT:THRESHOLD_EXCEEDED
    - Bilinmeyen endpoint signature → DRIFT:INPUT_ANOMALY
    - Bilinen endpoint + aynı config → no drift
    - _Requirements: DR7.1, DR7.2, DR7.3, DR7.4, DR7.5, DR7.6_
    - **Evidence:** `test_drift_guard_v0.py::TestDriftBaseline` (5 tests) + `TestEvaluateDriftV0` (6 tests)

- [x] 5. Final checkpoint — mevcut testlerin geçtiğinden emin ol
  - Mevcut guard decision testleri geçer ✅ (185 passed)
  - Yeni drift guard testleri geçer ✅ (18 passed)
  - _Requirements: tüm DR*_
  - **Evidence:** `test_guard_decision.py` + `test_guard_decision_wiring.py` + `test_guard_config.py` = 185 passed | `test_drift_guard.py` = 18 passed | Toplam: 203 passed, 0 failed

- [x] 6. v0 — Config güncellemesi
  - [x] 6.1 `backend/app/guard_config.py` — `drift_guard_fail_open: bool = True` config alanı ekle
    - Env var: `OPS_GUARD_DRIFT_GUARD_FAIL_OPEN`
    - Varsayılan: `True` (güvenli varsayılan — shadow+enforce fail-open)
    - _Requirements: DR4.3, DR4.4_
  - [x] 6.2 `backend/app/guard_config.py` — `drift_guard_provider_timeout_ms: int = 100` config alanı ekle
    - Env var: `OPS_GUARD_DRIFT_GUARD_PROVIDER_TIMEOUT_MS`
    - Varsayılan: 100ms
    - Validator: > 0, <= 5000
    - _Requirements: DR4.6_
  - [x] 6.3 `backend/app/guard_config.py` — `_FALLBACK_DEFAULTS` dict'ine yeni alanları ekle
    - `drift_guard_fail_open=True`, `drift_guard_provider_timeout_ms=100`

- [x] 7. v0 — DriftBaseline + HashDriftInputProvider
  - [x] 7.1 `backend/app/guards/drift_guard.py` — `DriftBaseline` frozen dataclass
    - `config_hash: str` — startup anındaki GuardConfig hash
    - `known_endpoint_signatures: frozenset[str]` — bilinen endpoint+method+risk_class hash'leri
    - `created_at_ms: int`
    - _Requirements: DR7.1, DR7.2, DR7.3_
  - [x] 7.2 `backend/app/guards/drift_guard.py` — `DriftInput` güncelleme: `config_hash` alanı ekle
    - _Requirements: DR7.2_
  - [x] 7.3 `backend/app/guards/drift_guard.py` — `HashDriftInputProvider` implementasyonu
    - `get_input()`: endpoint + method + risk_class hash + config.config_hash hesaplar
    - Deterministik, IO-free (hashlib.sha256)
    - _Requirements: DR7.2, DR7.3_
  - [x] 7.4 `backend/app/guards/drift_guard.py` — `evaluate_drift(input, baseline)` v0 güncelleme
    - Signature değişikliği: `evaluate_drift(drift_input, baseline)` → iki argüman
    - config_hash mismatch → `DRIFT:THRESHOLD_EXCEEDED`
    - Bilinmeyen endpoint signature → `DRIFT:INPUT_ANOMALY`
    - Else → `is_drift=False`
    - _Requirements: DR7.4, DR7.5, DR7.6_
  - [x] 7.5 `backend/app/guards/drift_guard.py` — `build_baseline(config, known_endpoints)` factory
    - Startup'ta çağrılır, `DriftBaseline` döner
    - _Requirements: DR7.1, DR7.7_

- [x] 8. v0 — Mode resolution refactor + middleware wiring güncelleme
  - [x] 8.1 `backend/app/guards/guard_decision_middleware.py` — drift step mode resolution refactor
    - Ad-hoc `_drift_is_shadow` hesaplaması kaldırılır
    - `resolve_effective_mode(tenant_mode, risk_class)` doğrudan çağrılır
    - `effective == OFF` → drift bypass
    - _Requirements: DR8.1, DR8.2, DR8.3_
  - [x] 8.2 `backend/app/guards/guard_decision_middleware.py` — StubDriftInputProvider → HashDriftInputProvider geçişi
    - Provider timeout: `asyncio.wait_for` veya sync timeout wrapper
    - Fail-open/fail-closed: `config.drift_guard_fail_open` flag'i ile kontrol
    - _Requirements: DR4.3, DR4.6, DR4.7_
  - [x] 8.3 `backend/app/guards/guard_decision_middleware.py` — evaluate_drift signature güncelleme
    - `evaluate_drift(drift_input, drift_baseline)` çağrısı
    - Baseline process startup'ta hesaplanır
    - _Requirements: DR7.4_

- [x] 9. v0 — Final checkpoint
  - Mevcut guard decision testleri geçer (185+)
  - Mevcut drift guard testleri geçer (18)
  - Yeni v0 testleri geçer (4.9–4.15)
  - _Requirements: tüm DR*_
  - **Evidence:** `test_drift_guard.py` (18) + `test_drift_guard_v0.py` (25) + `test_drift_guard_middleware_v0.py` (15) + guard decision tests (185) = 243 passed, 0 failed

## Reality-Sync Özeti

### Tamamlanan (✅) — Phase 1: Scaffolding + Wiring
- Task 0: Config scaffolding (drift_guard_enabled, drift_guard_killswitch) — TAM
- Task 1: DriftReasonCode, DriftInput, DriftDecision, DriftInputProvider, StubDriftInputProvider — TAM
- Task 2: evaluate_drift stub — TAM
- Task 3: Middleware wiring + metrics counter + inc_drift_evaluation — TAM
- Task 4.1–4.3: Unit testler (types, stubs, config) — TAM (18 tests)
- Task 5: Final checkpoint — TAM (203 tests passed)

### Tamamlanan (✅) — Phase 2: Gerçek Drift Detection (v0)
- Task 6: Config güncellemesi (fail_open, provider_timeout_ms) — TAM
- Task 7: DriftBaseline + HashDriftInputProvider + evaluate_drift v0 — TAM
- Task 8: Mode resolution refactor + middleware wiring güncelleme — TAM
- Task 4.9–4.15: Integration + unit testleri — TAM (25 + 15 = 40 tests)
- Task 9: v0 final checkpoint — TAM (243 tests passed, 0 failed)

### Kilitlenmiş Kararlar (v0)
- **Fail davranışı:** Shadow fail-open, Enforce fail-open (varsayılan), configurable (`drift_guard_fail_open`)
- **Mode resolution:** `resolve_effective_mode(tenant_mode, risk_class)` reuse — yeni fonksiyon yok
- **Drift step yeri:** Snapshot build'den ÖNCE (değişmedi)
- **Baseline:** Startup'ta hesaplanır, immutable, hot-reload yok (v0)
- **Provider:** Deterministik, IO-free (hashlib.sha256), timeout configurable

## Notlar

- Task numaraları (4.9, 4.10, 4.11) kullanıcının orijinal kontratından korunmuştur
- Kill-switch "return early" demek — "provider çağrılır ama sonucu discard edilir" DEĞİL
- v0: HashDriftInputProvider deterministik, IO-free (hashlib.sha256)
- v0: evaluate_drift(input, baseline) — baseline startup'ta hesaplanır, hot-reload yok
- v0: fail-open varsayılan (shadow+enforce), configurable
- v0: provider timeout 100ms varsayılan, configurable
- Mode resolution tek kaynak: resolve_effective_mode(tenant_mode, risk_class) — drift step ve snapshot build aynı fonksiyonu kullanır
- Mevcut middleware davranışı korunur — drift guard opsiyonel bir katman
- `drift_guard_enabled=false` (varsayılan) iken mevcut davranış %100 aynı
