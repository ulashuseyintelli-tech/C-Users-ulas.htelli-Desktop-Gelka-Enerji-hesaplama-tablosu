# Uygulama Planı: Drift Guard for Guard Decision Middleware

## Genel Bakış

Guard Decision Middleware pipeline'ına drift detection alt-sistemi eklenir. Kill-switch ile tamamen devre dışı bırakılabilir (0 call garantisi). Stub provider/evaluator ile başlanır — wiring kırmadan pipeline'a girer.

Uygulama sırası: scaffolding → interfaces → wiring → tests. Kill-switch precedence tasarım seviyesinde "return early" olarak kilitlenir.

## Görevler

- [ ] 0. Scaffolding — config + kill-switch hook
  - [ ] 0.1 `backend/app/guard_config.py` — `drift_guard_enabled: bool = False` ve `drift_guard_killswitch: bool = False` config alanları ekle
    - Env var: `OPS_GUARD_DRIFT_GUARD_ENABLED`, `OPS_GUARD_DRIFT_GUARD_KILLSWITCH`
    - Varsayılan: her ikisi de `False` (güvenli varsayılan)
    - _Requirements: DR1.2, DR1.3, DR2.5_

  - [ ] 0.2 `backend/app/guard_config.py` — `_FALLBACK_DEFAULTS` dict'ine yeni alanları ekle
    - `drift_guard_enabled=False`, `drift_guard_killswitch=False`
    - _Requirements: DR1.2_

- [ ] 1. Provider interface + stub implementasyon
  - [ ] 1.1 `backend/app/guards/drift_guard.py` — DriftReasonCode enum, DriftInput, DriftDecision dataclass'ları
    - `DriftReasonCode(str, Enum)`: PROVIDER_ERROR, THRESHOLD_EXCEEDED, INPUT_ANOMALY (kapalı küme, DRIFT: prefix)
    - `DriftInput(frozen=True)`: endpoint, method, tenant_id, request_signature, timestamp_ms
    - `DriftDecision(frozen=True)`: is_drift, reason_code, detail, would_enforce
    - _Requirements: DR3.7_

  - [ ] 1.2 `backend/app/guards/drift_guard.py` — DriftInputProvider protocol + StubDriftInputProvider
    - Protocol: `get_input(request, endpoint, method, tenant_id) → DriftInput`
    - Stub: her zaman geçerli DriftInput döner (no-drift baseline)
    - _Requirements: DR1.1_

- [ ] 2. Evaluator interface + stub implementasyon
  - [ ] 2.1 `backend/app/guards/drift_guard.py` — `evaluate_drift(drift_input) → DriftDecision`
    - Pure function, stub: her zaman `is_drift=False` döner
    - Gerçek implementasyon ileride threshold-based olacak
    - _Requirements: DR1.1_

- [ ] 3. Middleware wiring + snapshot + wouldEnforce semantics
  - [ ] 3.1 `backend/app/guards/guard_decision_middleware.py` — drift step wiring
    - Kill-switch check EN ÜSTTE (return early, 0 call garantisi)
    - `drift_guard_enabled` check
    - Provider call + exception handling
    - evaluate_drift call
    - Mode dispatch: shadow → log + proceed + wouldEnforce, enforce → 503
    - Drift reason codes snapshot'a eklenir
    - _Requirements: DR1.1, DR1.4, DR2.1–DR2.6, DR3.1–DR3.6, DR4.1–DR4.4_

  - [ ] 3.2 `backend/app/ptf_metrics.py` — `ptf_admin_drift_evaluation_total{mode, outcome}` counter
    - outcome: `no_drift|drift_detected|provider_error`
    - mode: `shadow|enforce`
    - Bounded: 2 × 3 = 6 zaman serisi
    - _Requirements: DR5.4, DR5.5_

  - [ ] 3.3 `backend/app/ptf_metrics.py` — `inc_drift_evaluation(mode, outcome)` metodu
    - Outcome validation: sadece `no_drift|drift_detected|provider_error`
    - Mode validation: sadece `shadow|enforce`
    - _Requirements: DR5.4_

- [ ] 4. Test paketi
  - [ ] 4.1 Unit testler: DriftReasonCode enum, DriftInput, DriftDecision dataclass'ları
    - Kapalı küme: 3 reason code, hepsi DRIFT: prefix
    - Frozen: DriftInput ve DriftDecision immutable
    - _Requirements: DR3.7_

  - [ ] 4.2 Unit testler: StubDriftInputProvider + evaluate_drift stub
    - Stub provider geçerli DriftInput döner
    - Stub evaluator is_drift=False döner
    - _Requirements: DR1.1_

  - [ ] 4.3 Unit testler: Config alanları
    - drift_guard_enabled varsayılan False
    - drift_guard_killswitch varsayılan False
    - _Requirements: DR1.2, DR1.3_

  - [ ] 4.9 Provider failure semantiği (shadow vs enforce)
    - Shadow: provider throws → proceed + DRIFT:PROVIDER_ERROR + metric++
    - Enforce: provider throws → 503 + next not called + DRIFT:PROVIDER_ERROR
    - Disabled: provider not called even if configured to throw (e2e)
    - Shadow + drift → wouldEnforce=true
    - _Requirements: DR4.1, DR4.2, DR4.3, DR4.4, DR4.5, DR6.1_

  - [ ] 4.10 Disabled mode: provider not called
    - drift_guard_enabled=false → provider.get_input 0 call
    - drift_guard_enabled=false → evaluate_drift 0 call
    - drift_guard_enabled=false → drift metrikleri 0 call
    - _Requirements: DR1.3, DR3.6_

  - [ ] 4.11 Kill-switch short-circuit (4'lü spy)
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

  - [ ] 4.12 Mode dispatch: shadow log + proceed, enforce 503
    - Shadow + drift detected → call_next çağrılır + DRIFT:THRESHOLD_EXCEEDED reason
    - Enforce + drift detected → 503 + call_next çağrılmaz
    - No drift → her iki modda proceed
    - _Requirements: DR3.1, DR3.2, DR3.4, DR3.5_

  - [ ] 4.13 wouldEnforce mikro ayar
    - Shadow + drift → wouldEnforce=true
    - Disabled → wouldEnforce=false (drift kaynaklı)
    - Kill-switch ON → wouldEnforce=false (drift kaynaklı)
    - _Requirements: DR6.1, DR6.2, DR6.3_

- [ ] 5. Final checkpoint — mevcut testlerin geçtiğinden emin ol
  - Mevcut guard decision testleri geçer
  - Mevcut tenant-enable PBT'leri (P1–P3) geçer
  - Mevcut endpoint-class-policy testleri geçer
  - Yeni drift guard testleri geçer
  - _Requirements: tüm DR*_

## Notlar

- Task numaraları (4.9, 4.10, 4.11) kullanıcının orijinal kontratından korunmuştur
- Kill-switch "return early" demek — "provider çağrılır ama sonucu discard edilir" DEĞİL
- Stub provider/evaluator ile başlanır; gerçek drift detection logic ileride eklenir
- Mevcut middleware davranışı korunur — drift guard opsiyonel bir katman
- `drift_guard_enabled=false` (varsayılan) iken mevcut davranış %100 aynı
