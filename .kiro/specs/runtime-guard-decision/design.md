# Tasarım: Runtime Guard Decision Layer

## Genel Bakış

Mevcut Ops Guard middleware zincirinin (KillSwitch → RateLimiter → CircuitBreaker) üzerine oturan, per-request immutable decision snapshot üreten yeni katman. Mevcut guard'lar "signal producer" olarak kullanılır; HTTP semantikleri (429, 503 vb.) korunur.

3 dosya: `guard_decision.py` (model + snapshot factory + hash), `guard_enforcement.py` (pure enforcer), `test_guard_decision.py` (testler).

## Mimari

### Mevcut Yapı (değişmez)

```
Request → OpsGuardMiddleware._evaluate_guards()
    → KillSwitch.check_request() → GuardDenyReason | None
    → RateLimitGuard.check_request() → GuardDenyReason | None
    → CircuitBreaker.allow_request() → bool
    → First deny wins → 429/503 response OR handler
```

### Yeni Katman (wrap, don't replace)

```
Request
    ↓
SnapshotFactory.build(guard_deny_reason, config, endpoint, now_ms)
    - config freshness signal (last_updated_at parse + age check)
    - CB mapping signal (endpoint → dependency mapping check)
    - derive_signal_flags(signals)
    - compute_risk_context_hash(payload + window_params)
    - freeze → GuardDecisionSnapshot
    ↓
Enforcer.evaluate(snapshot)
    - guard_deny_reason not None → PASSTHROUGH (mevcut davranış)
    - derived_has_insufficient → BLOCK_INSUFFICIENT
    - derived_has_stale → BLOCK_STALE
    - else → ALLOW
    ↓
(Middleware wiring — ayrı task)
```

### Bileşen Diyagramı

```
┌─────────────────────────────────────────────────┐
│ OpsGuardMiddleware (MEVCUT — değişmez)          │
│   KillSwitch → RateLimiter → CircuitBreaker     │
│   → GuardDenyReason | None                      │
└──────────────────────┬──────────────────────────┘
                       │ guard_deny_reason
                       ▼
┌─────────────────────────────────────────────────┐
│ guard_decision.py (YENİ)                        │
│                                                 │
│  SignalStatus (OK | STALE | INSUFFICIENT)       │
│  SignalName (CONFIG_FRESHNESS | CB_MAPPING)      │
│  GuardSignal (name, status, reason_code, ...)   │
│  WindowParams (max_config_age_ms, ...)          │
│  GuardDecisionSnapshot (frozen, immutable)      │
│                                                 │
│  Signal Producers:                              │
│    check_config_freshness(config, now_ms, wp)   │
│    check_cb_mapping(endpoint, dependencies)     │
│                                                 │
│  Helpers:                                       │
│    derive_signal_flags(signals)                 │
│    compute_risk_context_hash(...)               │
│                                                 │
│  Factory:                                       │
│    SnapshotFactory.build(...)                   │
└──────────────────────┬──────────────────────────┘
                       │ snapshot
                       ▼
┌─────────────────────────────────────────────────┐
│ guard_enforcement.py (YENİ)                     │
│                                                 │
│  EnforcementVerdict (ALLOW | PASSTHROUGH |      │
│    BLOCK_STALE | BLOCK_INSUFFICIENT)            │
│                                                 │
│  Enforcer.evaluate(snapshot) → verdict          │
│    Pure function, no side effects               │
└─────────────────────────────────────────────────┘
```

## Veri Modelleri

### SignalStatus

```python
class SignalStatus(str, Enum):
    OK = "OK"
    STALE = "STALE"
    INSUFFICIENT = "INSUFFICIENT"
```

### SignalName

```python
class SignalName(str, Enum):
    CONFIG_FRESHNESS = "CONFIG_FRESHNESS"
    CB_MAPPING = "CB_MAPPING"
```

### ReasonCode

```python
class SignalReasonCode(str, Enum):
    CONFIG_TIMESTAMP_MISSING = "CONFIG_TIMESTAMP_MISSING"
    CONFIG_TIMESTAMP_PARSE_ERROR = "CONFIG_TIMESTAMP_PARSE_ERROR"
    CONFIG_STALE = "CONFIG_STALE"
    CB_MAPPING_MISS = "CB_MAPPING_MISS"
    OK = "OK"
```

### WindowParams

```python
@dataclass(frozen=True)
class WindowParams:
    max_config_age_ms: int = 86_400_000  # 24h default
    clock_skew_allowance_ms: int = 5_000  # 5s default
```

### GuardSignal

```python
@dataclass(frozen=True)
class GuardSignal:
    name: SignalName
    status: SignalStatus
    reason_code: SignalReasonCode
    observed_at_ms: int
    detail: str = ""  # debug-only, never exported to Prom labels
```

### GuardDecisionSnapshot

```python
@dataclass(frozen=True)
class GuardDecisionSnapshot:
    now_ms: int
    tenant_id: str                          # "default" for v1
    endpoint: str                           # normalized endpoint template
    method: str                             # HTTP method
    window_params: WindowParams
    config_hash: str                        # GuardConfig.config_hash
    risk_context_hash: str                  # includes window_params
    guard_deny_reason: GuardDenyReason | None  # from existing guard chain
    signals: tuple[GuardSignal, ...]
    derived_has_stale: bool
    derived_has_insufficient: bool
    is_degrade_mode: bool                   # from KillSwitchManager
```

### EnforcementVerdict

```python
class EnforcementVerdict(str, Enum):
    ALLOW = "ALLOW"                         # no deny, no stale/insufficient
    PASSTHROUGH = "PASSTHROUGH"             # existing deny reason → mevcut davranış
    BLOCK_STALE = "BLOCK_STALE"             # config stale → 503
    BLOCK_INSUFFICIENT = "BLOCK_INSUFFICIENT"  # data insufficient → 503
```

## Signal Producers

### check_config_freshness

```python
def check_config_freshness(
    config: GuardConfig,
    now_ms: int,
    window_params: WindowParams,
) -> GuardSignal:
    """
    Config freshness signal producer.

    Rules (R4):
      - last_updated_at empty → INSUFFICIENT (CONFIG_TIMESTAMP_MISSING)
      - last_updated_at parse error → INSUFFICIENT (CONFIG_TIMESTAMP_PARSE_ERROR)
      - age > max_config_age_ms → STALE (CONFIG_STALE)
      - else → OK
    """
```

### check_cb_mapping

```python
def check_cb_mapping(
    endpoint: str,
    dependencies: list | None,
    now_ms: int,
) -> GuardSignal:
    """
    CB mapping signal producer.

    Rules (R4):
      - dependencies is None or empty → INSUFFICIENT (CB_MAPPING_MISS)
      - else → OK
    """
```

## Hash Computation

```python
def compute_risk_context_hash(
    tenant_id: str,
    endpoint: str,
    method: str,
    config_hash: str,
    window_params: WindowParams,
    guard_deny_reason_name: str | None,
    derived_has_stale: bool,
    derived_has_insufficient: bool,
) -> str:
    """
    Deterministic hash. Canonical JSON + SHA-256.

    Payload includes window_params (R5).
    Canonicalization: json.dumps(sort_keys=True, separators=(',',':'))
    """
```

## Enforcement Logic

```python
def evaluate(snapshot: GuardDecisionSnapshot | None) -> EnforcementVerdict:
    """
    Pure function. No side effects.

    Decision tree:
      1. snapshot is None → ALLOW (fail-open, R6)
      2. guard_deny_reason is not None → PASSTHROUGH (R1, R8.1)
      3. derived_has_insufficient → BLOCK_INSUFFICIENT (R8.2)
      4. derived_has_stale → BLOCK_STALE (R8.3)
      5. else → ALLOW (R8.4)
    """
```

## reasonCodes Canonical Ordering

503 response payload'daki `reasonCodes` listesi deterministik sıralıdır:
1. SignalName enum value (string, lexicographic): `CB_MAPPING` < `CONFIG_FRESHNESS`
2. SignalReasonCode value (string, lexicographic) — tie-break

Bu kural `_extract_reason_codes()` fonksiyonunda uygulanır. `list(set(...))` veya sırasız iterasyon YASAKTIR.

## Shadow / Enforce Mode (Güvenli Rollout)

Guard Decision Layer iki modda çalışır:

| Mod | Env Var Değeri | Davranış |
|-----|---------------|----------|
| Shadow | `OPS_GUARD_DECISION_LAYER_MODE=shadow` (default) | Snapshot build + evaluate çalışır, metrikler artar, ama BLOCK verdict'te 503 dönmez — request passthrough |
| Enforce | `OPS_GUARD_DECISION_LAYER_MODE=enforce` | Tam enforcement — BLOCK verdict'te 503 döner |

### Rollout Akışı

```
1. decision_layer_enabled=true, decision_layer_mode=shadow
   → 24-48 saat gözlem
   → guard_decision_block_total{kind} artıyor mu? False positive var mı?
   
2. Eğer block_total makul ve false positive yoksa:
   decision_layer_mode=enforce
   → Gerçek 503 enforcement başlar

3. Sorun varsa:
   decision_layer_enabled=false
   → Katman tamamen devre dışı
```

### Shadow Mode Detayları

Shadow modda middleware şu adımları izler:
1. SnapshotFactory.build() → snapshot (normal)
2. evaluate(snapshot) → verdict (normal)
3. Verdict BLOCK_STALE veya BLOCK_INSUFFICIENT ise:
   - `guard_decision_block_total{kind}` counter artar (metrik kaydedilir)
   - `[GUARD-DECISION] SHADOW block: ...` log satırı yazılır
   - Request passthrough — 503 dönmez, handler'a ulaşır
4. Verdict ALLOW veya PASSTHROUGH ise: normal akış

Bu sayede gerçek trafik üzerinde policy tuning yapılabilir, false positive oranı ölçülür.

### Env Var'lar

| Env Var | Değerler | Default |
|---------|----------|---------|
| `OPS_GUARD_DECISION_LAYER_ENABLED` | `true` / `false` | `false` |
| `OPS_GUARD_DECISION_LAYER_MODE` | `shadow` / `enforce` | `shadow` |

## Hata Yönetimi

| Durum | Davranış |
|-------|----------|
| SnapshotFactory.build() exception | None döner, log + metric (R6) |
| Enforcer.evaluate(None) | ALLOW (fail-open) |
| config.last_updated_at parse error | INSUFFICIENT signal, snapshot üretilir |
| CB mapping miss | INSUFFICIENT signal, snapshot üretilir |
| Hash computation error | Fallback hash "error", snapshot üretilir |

## Doğruluk Özellikleri (Correctness Properties)

### Property 1: Derived flags yalnızca signals'tan türer

*Her* GuardDecisionSnapshot için, `derived_has_stale == any(s.status == STALE for s in signals)` ve `derived_has_insufficient == any(s.status == INSUFFICIENT for s in signals)`.

**Validates: Requirement 3**

### Property 2: Hash windowParams'a duyarlı

*Her* iki farklı WindowParams değeri için, diğer tüm alanlar aynıyken hash farklı olmalıdır.

**Validates: Requirement 5**

### Property 3: Snapshot immutability

*Her* üretilen snapshot için, hiçbir alan sonradan değiştirilemez (frozen dataclass).

**Validates: Requirement 2**

### Property 4: Enforcement determinism

*Her* aynı snapshot için, `evaluate()` her zaman aynı verdict'i döner.

**Validates: Requirement 8**

### Property 5: Fail-open safety

*Her* None snapshot için, `evaluate()` ALLOW döner.

**Validates: Requirement 6**

## Test Stratejisi

### Test Dosyası

- `backend/tests/test_guard_decision.py` — tüm unit + property testleri

### Test Planı

1. **Signal derivation**: stale signal var → derived_has_stale=True; insufficient signal var → derived_has_insufficient=True; tüm OK → her ikisi False
2. **Caller flag bypass**: snapshot'a dışarıdan flag inject edilemez; yalnızca signals belirler
3. **Config freshness — INSUFFICIENT**: last_updated_at="" → INSUFFICIENT
4. **Config freshness — INSUFFICIENT**: last_updated_at="not-a-date" → INSUFFICIENT
5. **Config freshness — STALE**: last_updated_at 48h önce, max_config_age_ms=24h → STALE
6. **Config freshness — OK**: last_updated_at 1h önce, max_config_age_ms=24h → OK
7. **CB mapping — INSUFFICIENT**: dependencies=None → INSUFFICIENT
8. **CB mapping — OK**: dependencies=["db_primary"] → OK
9. **Hash — windowParams sensitivity**: aynı input, farklı max_config_age_ms → farklı hash
10. **Hash — determinism**: aynı input → aynı hash
11. **Snapshot immutability**: frozen dataclass, attribute assignment → TypeError
12. **Enforcement — PASSTHROUGH**: guard_deny_reason=RATE_LIMITED → PASSTHROUGH
13. **Enforcement — BLOCK_INSUFFICIENT**: no deny + insufficient signal → BLOCK_INSUFFICIENT
14. **Enforcement — BLOCK_STALE**: no deny + stale signal → BLOCK_STALE
15. **Enforcement — ALLOW**: no deny + all OK → ALLOW
16. **Enforcement — fail-open**: snapshot=None → ALLOW
17. **SnapshotFactory — fail-open**: build() exception → None
