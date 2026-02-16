# Tasarım: Concurrency PBT — Guard Decision Layer

## Genel Bakış

Guard Decision Layer'ın eşzamanlı request'ler altında doğruluk garantilerini kanıtlayan property-based test suite. Tenant-enable spec'inin üzerine inşa edilir. Amaç "çok test" değil; doğru invariants ve gerçekçi concurrency harness.

Değişen/eklenen dosyalar:
- `backend/tests/test_concurrency_pbt.py` — 5 PBT property testi
- `backend/tests/concurrency_harness.py` — paylaşılan harness utilities

## Concurrency Harness

### İki Pratik Yol

Her ikisi de desteklenir:

1. **`ThreadPoolExecutor`** — en basit, gerçek OS-level concurrency
2. **`asyncio.gather`** — ASGI pipeline ile uyumlu, event loop concurrency

Test hedefi: aynı anda N=20..100 request/build koşturmak.

### Harness Bileşenleri

```python
# concurrency_harness.py

def parallel_snapshot_builds(
    build_args_list: list[dict],
    max_workers: int = 20,
) -> list[GuardDecisionSnapshot | None]:
    """
    ThreadPoolExecutor ile paralel SnapshotFactory.build() çağrıları.
    Her build bağımsız; sonuçlar input sırasıyla eşleşir.
    """

def parallel_snapshot_builds_async(
    build_args_list: list[dict],
) -> list[GuardDecisionSnapshot | None]:
    """
    asyncio.gather ile paralel build. Event loop concurrency.
    """

def deterministic_now_ms(base: int = 1_700_000_000_000) -> int:
    """Sabit now_ms üretici — determinism testleri için."""
```

### Kontrol Edilebilir `now_ms`

Determinism için `SnapshotFactory.build(..., now_ms=...)` parametresi zaten mevcut. Monkeypatch gerekmez.

### Tenant Set

| Tenant | Mode | Açıklama |
|--------|------|----------|
| tenantA | enforce | Gerçek blok |
| tenantB | shadow | Log + metrik, blok yok |
| tenantC | off | Passthrough |
| tenantX | (unknown) | default_mode uygulanır, metrik label "_other" |

Config:
```python
decision_layer_enabled = True
decision_layer_default_mode = "shadow"
decision_layer_tenant_modes_json = '{"tenantA":"enforce","tenantB":"shadow","tenantC":"off"}'
decision_layer_tenant_allowlist_json = '["tenantA","tenantB","tenantC"]'
```

## Property Set (5 PBT)

### P-C1: Tenant Isolation

**Validates: C1.1, C1.2, C1.3**

Rastgele tenant dizisi (A/B/C/X) ile paralel `SnapshotFactory.build()`:
- Her snapshot'ın `tenant_id` değeri input ile eşleşir
- Her snapshot'ın `tenant_mode` değeri `resolve_tenant_mode()` sonucuyla eşleşir
- Her snapshot'ın `risk_context_hash` değeri canonical payload ile eşleşir
- Hiçbir snapshot'ta başka tenant'ın mode'u görünmez

```
∀ tenant_ids ∈ random_list(["tenantA","tenantB","tenantC","tenantX"]):
  snapshots = parallel_build(tenant_ids)
  ∀ i: snapshots[i].tenant_id == tenant_ids[i]
       ∧ snapshots[i].tenant_mode == resolve(tenant_ids[i])
```

### P-C2: Hash Determinism

**Validates: C2.1, C2.2**

Aynı tenant + endpoint + windowParams + now_ms ile 50 paralel build:
- Tüm hash'ler eşit

```
∀ (tenant, endpoint, now_ms, config):
  hashes = parallel_build_50(same_args)
  len(set(hashes)) == 1
```

### P-C3: Mode Freeze vs Mid-Flight Change

**Validates: C4.1, C4.2**

Request başlat → snapshot build → hemen sonra config değiştir (tenant map):
- Zaten üretilmiş snapshot'ın `tenant_mode` değeri değişmez
- Frozen dataclass garantisi

```
snapshot = build(tenant_id="tenantA", config_v1)
mutate_config(config_v2)  # tenantA: enforce → shadow
snapshot.tenant_mode == TenantMode.ENFORCE  # hâlâ v1
```

Not: Snapshot frozen dataclass olduğundan mutation zaten imkansız. Test, config değişikliğinin mevcut snapshot'ı etkilemediğini ve yeni build'in yeni config'i kullandığını doğrular.

### P-C4: Metrics Monotonic Under Concurrency

**Validates: C5.1, C5.2**

Paralel 50+ request'te counter increment:
- `after >= before` (non-decreasing)
- Counter hiçbir zaman azalmaz

```
before = read_counter()
parallel_builds(N=50, trigger_block=True)
after = read_counter()
after >= before
```

Not: Prometheus client thread-safe ama exact delta garanti etmek zor olabilir. Önce monotonic + lower bound ile kilitle.

### P-C5: Fail-Open Containment

**Validates: C7.1, C7.2, C7.3**

Paralel isteklerin %k'sında build crash inject:
- Crash'li build'ler None döner (fail-open)
- Crash'siz build'ler normal snapshot döner
- Sistem deadlock olmaz (timeout ile garanti)
- `snapshot_build_failures_total` artar (veya en azından azalmaz)

```
∀ builds with injected_crash_rate=0.3:
  crashed_results are None
  healthy_results are valid snapshots
  no deadlock (completes within timeout)
```

## Hata Yönetimi

| Durum | Davranış |
|-------|----------|
| ThreadPoolExecutor thread crash | Future exception yakalanır, None olarak raporlanır |
| asyncio task exception | gather(return_exceptions=True) ile yakalanır |
| Prometheus counter race | Thread-safe increment; exact delta yerine monotonic kontrol |
| Config mutation mid-test | Snapshot freeze garantisi; yeni build yeni config kullanır |
| Deadlock riski | ThreadPoolExecutor timeout + test-level timeout ile korunur |

## Uygulama Notları (Sık Tuzaklar)

1. **Test-only snapshot exposure**: Snapshot'ı response'a koymak prod code'u kirletmesin. Concurrency PBT'de doğrudan `SnapshotFactory.build()` çağrısı yapılır (middleware bypass); ASGI entegrasyonu gerektiğinde test-only debug endpoint kullanılır.

2. **Prometheus counters exact delta**: `prometheus_client` thread-safe ama registry state paylaşımı var. Exact delta yerine önce monotonic + lower bound ile kilitle.

3. **Env var mid-flight**: Python'da `os.environ` process-global. Concurrency testinde izolasyon için `GuardConfig.model_construct()` ile config snapshot'ı request başında read edilir (zaten snapshot bunu hedefliyor).

4. **GIL ve gerçek paralellik**: CPython GIL nedeniyle ThreadPoolExecutor CPU-bound concurrency sağlamaz ama I/O-bound ve state isolation testleri için yeterlidir. Asıl hedef shared state corruption tespiti.

## Test Stratejisi

- Kütüphane: **Hypothesis** (Python)
- Her property: `@settings(max_examples=200)`
- Concurrency: `ThreadPoolExecutor(max_workers=20)` default
- Timeout: Her test max 60s (Hypothesis deadline=None, test-level timeout)
- Tenant set: A (enforce), B (shadow), C (off), X (unknown/default)
