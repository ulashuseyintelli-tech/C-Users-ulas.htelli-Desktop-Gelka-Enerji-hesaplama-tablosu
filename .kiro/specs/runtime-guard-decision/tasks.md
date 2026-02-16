# Uygulama Planı: Runtime Guard Decision Layer

## Genel Bakış

Mevcut Ops Guard middleware zincirinin üzerine oturan per-request immutable decision snapshot katmanı. 3 dosya: model + factory + hash (`guard_decision.py`), enforcer (`guard_enforcement.py`), testler (`test_guard_decision.py`). Middleware wiring ayrı task (bu spec kapsamı dışı).

## Görevler

- [x] 1. Model types + snapshot dataclass
  - [x] 1.1 `backend/app/guards/guard_decision.py` — Temel tipler ve snapshot dataclass
    - `SignalStatus` enum (OK, STALE, INSUFFICIENT)
    - `SignalName` enum (CONFIG_FRESHNESS, CB_MAPPING)
    - `SignalReasonCode` enum (CONFIG_TIMESTAMP_MISSING, CONFIG_TIMESTAMP_PARSE_ERROR, CONFIG_STALE, CB_MAPPING_MISS, OK)
    - `WindowParams` frozen dataclass (max_config_age_ms, clock_skew_allowance_ms)
    - `GuardSignal` frozen dataclass (name, status, reason_code, observed_at_ms, detail)
    - `GuardDecisionSnapshot` frozen dataclass (tüm alanlar — design.md'deki gibi)
    - _Requirements: 2.1, 2.2, 7.1, 7.2, 7.3_

- [x] 2. derive_signal_flags helper
  - [x] 2.1 `backend/app/guards/guard_decision.py` — `derive_signal_flags(signals)` fonksiyonu
    - Input: `tuple[GuardSignal, ...]`
    - Output: `tuple[bool, bool]` → (has_stale, has_insufficient)
    - Yalnızca signals'tan türetir; dışarıdan flag kabul etmez
    - _Requirements: 3.1, 3.2, 3.3_

- [x] 3. Config freshness signal producer
  - [x] 3.1 `backend/app/guards/guard_decision.py` — `check_config_freshness(config, now_ms, window_params)` fonksiyonu
    - `last_updated_at` boş → INSUFFICIENT (CONFIG_TIMESTAMP_MISSING)
    - `last_updated_at` parse error → INSUFFICIENT (CONFIG_TIMESTAMP_PARSE_ERROR)
    - age > max_config_age_ms → STALE (CONFIG_STALE)
    - else → OK
    - _Requirements: 4.1, 4.2, 4.4_

- [x] 4. CB mapping signal producer
  - [x] 4.1 `backend/app/guards/guard_decision.py` — `check_cb_mapping(endpoint, dependencies, now_ms)` fonksiyonu
    - dependencies None veya boş → INSUFFICIENT (CB_MAPPING_MISS)
    - else → OK
    - _Requirements: 4.3, 4.4_

- [x] 5. compute_risk_context_hash
  - [x] 5.1 `backend/app/guards/guard_decision.py` — `compute_risk_context_hash(...)` fonksiyonu
    - Canonical JSON payload: tenant_id, endpoint, method, config_hash, window_params, guard_deny_reason name, derived flags
    - `json.dumps(sort_keys=True, separators=(',',':'))` + `hashlib.sha256` → hex[:16]
    - windowParams dahil (R5)
    - _Requirements: 5.1, 5.2, 5.3_

- [x] 6. SnapshotFactory
  - [x] 6.1 `backend/app/guards/guard_decision.py` — `SnapshotFactory.build(...)` class method
    - Signal producer'ları çağırır
    - derive_signal_flags ile derived flags hesaplar
    - compute_risk_context_hash ile hash hesaplar
    - Frozen GuardDecisionSnapshot döner
    - Exception → None + log (fail-open)
    - _Requirements: 2.1, 2.3, 6.1, 6.3_

- [x] 7. Enforcer
  - [x] 7.1 `backend/app/guards/guard_enforcement.py` — `EnforcementVerdict` enum + `evaluate(snapshot)` fonksiyonu
    - `EnforcementVerdict`: ALLOW, PASSTHROUGH, BLOCK_STALE, BLOCK_INSUFFICIENT
    - snapshot None → ALLOW (fail-open)
    - guard_deny_reason not None → PASSTHROUGH
    - derived_has_insufficient → BLOCK_INSUFFICIENT
    - derived_has_stale → BLOCK_STALE
    - else → ALLOW
    - Pure function, no side effects
    - _Requirements: 6.2, 8.1, 8.2, 8.3, 8.4, 8.5_

- [x] 8. Unit testleri
  - [x] 8.1 `backend/tests/test_guard_decision.py` — 20 test (design.md test planı + 3 ek)
    - Signal derivation (3 test): stale→True, insufficient→True, all OK→False
    - Config freshness (4 test): empty→INSUFFICIENT, parse error→INSUFFICIENT, stale→STALE, fresh→OK
    - CB mapping (2 test): None→INSUFFICIENT, present→OK
    - Hash (2 test): windowParams sensitivity, determinism
    - Snapshot immutability (1 test): frozen attribute assignment → TypeError
    - Enforcement (5 test): PASSTHROUGH, BLOCK_INSUFFICIENT, BLOCK_STALE, ALLOW, fail-open
    - _Requirements: 1-8 (tümü)_

## Notlar

- Middleware wiring (OpsGuardMiddleware entegrasyonu) Task 9-11 ile tamamlandı
- tenant_id v1'de "default" sabit değeri kullanır; tenant extraction sonraki iterasyonda
- is_degrade_mode alanı mevcut KillSwitchManager.is_degrade_mode() değerini snapshot'a kopyalar
- Tüm enum'lar bounded; free-form string label yok (HD-5 uyumlu)
- `decision_layer_enabled` flag'i default OFF; production'da `OPS_GUARD_DECISION_LAYER_ENABLED=true` ile aktif edilir

## Wiring Görevleri

- [x] 9. Metrics — 2 yeni counter
  - [x] 9.1 `backend/app/ptf_metrics.py` — `ptf_admin_guard_decision_block_total{kind}` counter (kind: stale|insufficient)
  - [x] 9.2 `backend/app/ptf_metrics.py` — `ptf_admin_guard_decision_snapshot_build_failures_total` counter

- [x] 10. GuardDecisionMiddleware
  - [x] 10.1 `backend/app/guards/guard_decision_middleware.py` — Yeni Starlette BaseHTTPMiddleware
    - `_SKIP_PATHS` reuse (OpsGuardMiddleware ile aynı)
    - `decision_layer_enabled` flag kontrolü (default OFF)
    - `SnapshotFactory.build()` → `evaluate()` → verdict
    - BLOCK_STALE → 503 + `errorCode: OPS_GUARD_STALE` + `reasonCodes`
    - BLOCK_INSUFFICIENT → 503 + `errorCode: OPS_GUARD_INSUFFICIENT` + `reasonCodes`
    - ALLOW/PASSTHROUGH → `call_next(request)` + `request.state.guard_decision_snapshot`
    - Factory exception → fail-open (call_next)
    - Middleware exception → fail-open (call_next)
  - [x] 10.2 `backend/app/guard_config.py` — `decision_layer_enabled: bool = False` flag eklendi
  - [x] 10.3 `backend/app/main.py` — Middleware kayıt sırası: GuardDecision (inner) → OpsGuard (outer)

- [x] 11. Wiring testleri
  - [x] 11.1 `backend/tests/test_guard_decision_wiring.py` — 8 test (W1-W7 + fail-open variant)
    - W1: Rate-limited → 429 (decision layer bypassed)
    - W2: Kill-switched → 503 KILL_SWITCHED (decision layer bypassed)
    - W3: Circuit-open → 503 CIRCUIT_OPEN (decision layer bypassed)
    - W4: Allow + insufficient → 503 OPS_GUARD_INSUFFICIENT
    - W5: Allow + stale → 503 OPS_GUARD_STALE
    - W6: Allow + all OK → passthrough
    - W7a: SnapshotFactory.build() None → fail-open
    - W7b: Middleware exception → fail-open

- [x] 12. Operasyonel keskinleştirme
  - [x] 12.1 `backend/app/ptf_metrics.py` — `ptf_admin_guard_decision_requests_total` counter (middleware sıra doğrulama)
  - [x] 12.2 `backend/app/guards/guard_decision_middleware.py` — reasonCodes canonical ordering (SignalName → ReasonCode lexicographic)
  - [x] 12.3 `monitoring/prometheus/ptf-admin-alerts.yml` — 3 alert kuralı:
    - GD1: `PTFAdminGuardDecisionBuildFailure` (snapshot build failure → fail-open tespiti)
    - GD2: `PTFAdminGuardDecisionBlockRate` (block sayısı > 5 / 15dk)
    - GD3: `PTFAdminGuardDecisionSilent` (trafik var ama decision layer çalışmıyor)
  - [x] 12.4 `monitoring/grafana/ptf-admin-dashboard.json` — Guard Decision Layer row (3 panel):
    - Request Rate (layer active)
    - Block Rate by Kind (stale/insufficient)
    - Snapshot Build Failures (stat panel, red threshold > 0)
  - [x] 12.5 `monitoring/runbooks/ptf-admin-runbook.md` — Guard Decision Layer runbook bölümü:
    - Enable prosedürü, 503 errorCode'lar, reasonCodes ordering
    - 3 alert triage (BuildFailure, BlockRate, Silent)
    - PromQL kopyala-yapıştır referansları
  - [x] 12.6 `.kiro/specs/runtime-guard-decision/design.md` — reasonCodes canonical ordering kuralı eklendi

- [x] 13. Shadow / Enforce mode (güvenli rollout)
  - [x] 13.1 `backend/app/guard_config.py` — `decision_layer_mode: str = "shadow"` alanı + validator (shadow|enforce)
  - [x] 13.2 `backend/app/guards/guard_decision_middleware.py` — Shadow mode: BLOCK verdict → metrik + log, ama 503 dönmez (passthrough)
  - [x] 13.3 `backend/tests/test_guard_decision_wiring.py` — W8: 2 shadow mode testi (stale + insufficient passthrough)
  - [x] 13.4 `monitoring/prometheus/ptf-admin-alerts.yml` — GD2 BlockRate alert iyileştirmesi (mutlak + oran compound)
  - [x] 13.5 `.kiro/specs/runtime-guard-decision/design.md` — Shadow/enforce mode bölümü eklendi
  - [x] 13.6 `monitoring/runbooks/ptf-admin-runbook.md` — Shadow→enforce geçiş prosedürü eklendi

- [x] 14. Release note checklist
  - [x] 14.1 `monitoring/runbooks/ptf-admin-runbook.md` — Release note / kapanış checklist bölümü eklendi

- [x] 15. Prod öncesi son iyileştirmeler
  - [x] 15.1 Clock skew — `check_config_freshness()` zaten `max_config_age_ms + clock_skew_allowance_ms` kullanıyor (doğrulandı, değişiklik gerekmedi)
  - [x] 15.2 `backend/app/ptf_metrics.py` — `guard_decision_block_total` counter'a `mode` label eklendi (shadow|enforce)
  - [x] 15.3 `backend/app/guards/guard_decision_middleware.py` — `_emit_block_metric()` mode parametresi eklendi
  - [x] 15.4 `monitoring/runbooks/ptf-admin-runbook.md` — PromQL block breakdown'a mode bazlı sorgular eklendi
