# Tasarım: Drift Guard for Guard Decision Middleware

## Genel Bakış

Mevcut Guard Decision Middleware pipeline'ına drift detection adımı eklenir. Drift guard, request bazında "beklenen davranıştan sapma" tespit eder. Kill-switch ile tamamen devre dışı bırakılabilir (0 call garantisi — side-effect yok).

Değişen/eklenen dosyalar:
- `backend/app/guards/drift_guard.py` — DriftInputProvider, evaluate_drift, DriftDecision, DriftReasonCode
- `backend/app/guard_config.py` — `drift_guard_enabled`, `drift_guard_killswitch` config alanları
- `backend/app/guards/guard_decision_middleware.py` — drift step wiring
- `backend/app/ptf_metrics.py` — `ptf_admin_drift_evaluation_total` counter
- `backend/tests/test_drift_guard.py` — unit + integration testleri

Mevcut dosyalar davranış olarak KORUNUR:
- `SnapshotFactory.build()` — aynı kalır
- `evaluate()` — aynı kalır
- Mevcut PBT'ler (P1–P3, EP-1..EP-3) — geçerliliğini korur

## Mimari

```mermaid
graph TD
    REQ[Request] --> MW[GuardDecisionMiddleware]
    MW --> SKIP{Skip path?}
    SKIP -->|yes| PASS[passthrough]
    SKIP -->|no| GLOBAL{global enabled?}
    GLOBAL -->|no| PASS
    GLOBAL -->|yes| TENANT[resolve_tenant_mode]
    TENANT --> TOFF{tenant OFF?}
    TOFF -->|yes| PASS
    TOFF -->|no| DRIFTKS{drift kill-switch?}
    DRIFTKS -->|ON| NODRIFT[skip drift entirely]
    DRIFTKS -->|OFF| DRIFTEN{drift enabled?}
    DRIFTEN -->|no| NODRIFT
    DRIFTEN -->|yes| PROVIDER[DriftInputProvider.get_input]
    PROVIDER -->|error| DRIFTERR[DRIFT:PROVIDER_ERROR]
    PROVIDER -->|ok| EVAL[evaluate_drift]
    EVAL --> DRIFTDEC{drift detected?}
    DRIFTDEC -->|no| NODRIFT
    DRIFTDEC -->|yes| DRIFTREASON[DRIFT:reason_code]
    DRIFTERR --> MODEDISPATCH{mode?}
    DRIFTREASON --> MODEDISPATCH
    MODEDISPATCH -->|shadow| LOG[log + proceed + wouldEnforce=true]
    MODEDISPATCH -->|enforce| BLOCK[503 block]
    NODRIFT --> BUILD[SnapshotFactory.build]
    LOG --> BUILD
    BUILD --> EXISTING[existing eval flow]
```

**Kritik tasarım kararı:** Kill-switch check pipeline'ın EN ÜSTÜNDEDİR. "DRIFT:* yok" yetmez — "0 call" şartı burada tasarım seviyesinde garanti edilir. Provider çağrılmaz, evaluator çağrılmaz, metrik basılmaz, telemetry enrichment yapılmaz.

## Bileşenler ve Arayüzler

### 1. DriftReasonCode Enum (`drift_guard.py`)

```python
class DriftReasonCode(str, Enum):
    """Kapalı küme — drift reason code'ları. DRIFT: prefix'i ile kullanılır."""
    PROVIDER_ERROR = "DRIFT:PROVIDER_ERROR"
    THRESHOLD_EXCEEDED = "DRIFT:THRESHOLD_EXCEEDED"
    INPUT_ANOMALY = "DRIFT:INPUT_ANOMALY"
```

3 değer, bounded cardinality. Tüm reason code'lar `DRIFT:` prefix'i taşır.

### 2. DriftInput Dataclass (`drift_guard.py`)

```python
@dataclass(frozen=True)
class DriftInput:
    """Provider'dan gelen drift evaluation input'u. Frozen."""
    endpoint: str
    method: str
    tenant_id: str
    request_signature: str  # endpoint + method + risk_class hash
    config_hash: str         # GuardConfig.config_hash snapshot
    timestamp_ms: int
```

### 3. DriftBaseline Dataclass (`drift_guard.py`) — v0 ekleme

```python
@dataclass(frozen=True)
class DriftBaseline:
    """Startup'ta hesaplanan immutable baseline. Process lifetime boyunca sabit."""
    config_hash: str                          # Startup anındaki GuardConfig hash
    known_endpoint_signatures: frozenset[str] # Bilinen endpoint+method+risk_class hash'leri
    created_at_ms: int
```

Baseline startup'ta bir kez hesaplanır, hot-reload yok (v0). Yenileme sadece process restart ile.

### 4. DriftDecision Dataclass (`drift_guard.py`)

```python
@dataclass(frozen=True)
class DriftDecision:
    """Drift evaluation sonucu. Frozen."""
    is_drift: bool
    reason_code: DriftReasonCode | None = None
    detail: str = ""
    would_enforce: bool = False  # shadow modda "enforce olsaydı block olurdu"
```

### 5. DriftInputProvider Protocol (`drift_guard.py`)

```python
class DriftInputProvider(Protocol):
    def get_input(self, request: Request, endpoint: str, method: str, tenant_id: str, config: GuardConfig) -> DriftInput:
        """Request'ten drift input üretir. Exception → DRIFT:PROVIDER_ERROR."""
        ...
```

İki implementasyon:
- `StubDriftInputProvider` — her zaman geçerli `DriftInput` döner (no-drift baseline, mevcut)
- `HashDriftInputProvider` — v0: config_hash + endpoint_signature hash hesaplar (deterministik, IO-free)

### 6. evaluate_drift Fonksiyonu (`drift_guard.py`) — v0 güncelleme

```python
def evaluate_drift(drift_input: DriftInput, baseline: DriftBaseline) -> DriftDecision:
    """
    Pure function: DriftInput × DriftBaseline → DriftDecision.
    v0 politika:
      - config_hash mismatch → DRIFT:THRESHOLD_EXCEEDED
      - bilinmeyen endpoint signature → DRIFT:INPUT_ANOMALY
      - else → no drift
    """
```

### 7. GuardConfig Güncellemesi

```python
class GuardConfig(BaseSettings):
    # ... mevcut alanlar ...
    drift_guard_enabled: bool = False          # Varsayılan OFF
    drift_guard_killswitch: bool = False       # Kill-switch (ON → 0 call)
    drift_guard_fail_open: bool = True         # v0: fail-open (shadow+enforce)
    drift_guard_provider_timeout_ms: int = 100 # Provider call timeout
```

### 8. Mode Resolution — Tek Kaynak (v0 refactor)

Drift step'teki ad-hoc `_drift_is_shadow` hesaplaması kaldırılır. Yerine:

```python
# guard_decision_middleware.py — drift step içinde
from .guard_decision import resolve_effective_mode

effective = resolve_effective_mode(tenant_mode, risk_class)
# effective == OFF → drift bypass
# effective == SHADOW → log + proceed
# effective == ENFORCE → block (drift detected ise)
```

Bu, snapshot build'deki mode resolution ile birebir aynı fonksiyonu kullanır. Tek kaynak.

### 9. Middleware Drift Step Wiring (v0 güncelleme)

```python
# guard_decision_middleware.py — _evaluate_decision() içinde
# Tenant mode OFF check'inden SONRA, snapshot build'den ÖNCE:

from .guard_decision import resolve_effective_mode

# ── Drift guard step ──────────────────────────────────────────
if not config.drift_guard_killswitch and config.drift_guard_enabled:
    effective = resolve_effective_mode(tenant_mode, risk_class)
    if effective != TenantMode.OFF:
        _drift_is_shadow = (effective == TenantMode.SHADOW)
        _drift_mode_label = "shadow" if _drift_is_shadow else "enforce"
        try:
            drift_input = drift_provider.get_input(request, endpoint, method, tenant_id, config)
            drift_decision = evaluate_drift(drift_input, drift_baseline)
            if drift_decision.is_drift:
                reason_code = drift_decision.reason_code.value
                _emit_drift_metric(_drift_mode_label, "drift_detected")
                if not _drift_is_shadow:
                    return _build_block_response("OPS_GUARD_DRIFT", [reason_code])
                logger.info(f"[GUARD-DECISION] SHADOW drift: {reason_code}")
            else:
                _emit_drift_metric(_drift_mode_label, "no_drift")
        except Exception as exc:
            _emit_drift_metric(_drift_mode_label, "provider_error")
            if not _drift_is_shadow and not config.drift_guard_fail_open:
                return _build_block_response("OPS_GUARD_DRIFT", [DriftReasonCode.PROVIDER_ERROR.value])
            logger.info(f"[GUARD-DECISION] Drift provider error (fail-open): {exc}")
```

### 10. Metrik Güncellemesi

```python
# ptf_metrics.py
self._drift_evaluation_total = Counter(
    "ptf_admin_drift_evaluation_total",
    "Drift guard evaluation outcomes",
    labelnames=["mode", "outcome"],
    registry=self._registry,
)
# outcome: "no_drift" | "drift_detected" | "provider_error"
# mode: "shadow" | "enforce"
# Bounded: 2 × 3 = 6 zaman serisi
```

## Kill-Switch Short-Circuit Garantisi (Hard Invariant)

Kill-switch ON iken drift subsystem tamamen görünmez:

| Bileşen | Kill-switch ON | Disabled | Aktif |
|---|---|---|---|
| `provider.get_input()` | 0 call | 0 call | 1 call |
| `evaluate_drift()` | 0 call | 0 call | 0-1 call |
| drift metrikleri | 0 call | 0 call | 1 call |
| drift telemetry | 0 call | 0 call | 0-1 call |
| `DRIFT:*` reason codes | yok | yok | 0-N |
| `wouldEnforce` (drift kaynaklı) | false | false | true/false |

Bu tablo 4'lü spy testi ile doğrulanır (Task 4.11).

## Hata Yönetimi

| Hata Durumu | Shadow | Enforce (fail-open=true) | Enforce (fail-open=false) | Kill-switch ON |
|---|---|---|---|---|
| Provider exception | proceed + DRIFT:PROVIDER_ERROR | proceed + DRIFT:PROVIDER_ERROR | 503 block | N/A (provider çağrılmaz) |
| Provider timeout | proceed + DRIFT:PROVIDER_ERROR | proceed + DRIFT:PROVIDER_ERROR | 503 block | N/A |
| Evaluator exception | proceed + DRIFT:PROVIDER_ERROR | proceed + DRIFT:PROVIDER_ERROR | 503 block | N/A |
| Drift detected | proceed + log + wouldEnforce | 503 block | 503 block | N/A |
| No drift | proceed | proceed | proceed | proceed |

**v0 varsayılan:** `drift_guard_fail_open=true` → Shadow ve Enforce'ta fail-open. Prod deneyimiyle `fail_open=false` geçişi config ile yapılır.

## Test Stratejisi

### Unit Test Odağı

- Kill-switch 4'lü spy: provider/evaluator/metrics/telemetry hepsi 0 call
- Provider failure: shadow → proceed + reason, enforce → fail-open (varsayılan) veya 503 (fail_open=false)
- Provider timeout: timeout_ms aşılınca DRIFT:PROVIDER_ERROR
- Disabled: provider çağrılmaz
- Mode dispatch: shadow log + proceed, enforce 503
- Mode resolution tek kaynak: drift step resolve_effective_mode kullanır
- Reason code kapalı küme: sadece DRIFT:* prefix
- wouldEnforce semantiği: shadow+drift → true, disabled → false, kill-switch → false
- Baseline: config_hash mismatch → THRESHOLD_EXCEEDED, bilinmeyen endpoint → INPUT_ANOMALY

### Property-Based Testing

- DP-1: Kill-switch ON → 0 side-effect (provider/eval/metric/telemetry)
- DP-2: Reason code prefix invariant: tüm drift reason'lar DRIFT: ile başlar
- DP-3: Monotonic safety: drift guard hiçbir koşulda mevcut guard kararını "daha agresif" yapamaz
- DP-4: Mode consistency: drift step ve snapshot build aynı effective_mode'u hesaplar (resolve_effective_mode tek kaynak)
