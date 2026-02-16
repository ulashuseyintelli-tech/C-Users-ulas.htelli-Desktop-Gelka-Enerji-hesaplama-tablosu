# Tasarım: Preflight Telemetry — PR-17

## Genel Bakış

PR-17, preflight sonuçlarını yapılandırılmış metrik olarak dışa aktarır. Pure Python modül — harici bağımlılık yok. Prometheus text exposition formatı ve JSON formatı desteklenir. Dashboard tanımı Grafana JSON olarak üretilir. Gerçek Prometheus/Grafana altyapısı bu PR'ın scope'u dışındadır.

Tasarım felsefesi: mevcut zincire minimal dokunuş. Preflight CLI'ya tek flag (`--metrics-dir`) eklenir; metrik üretimi ayrı modülde yaşar.

## Mimari

### Mevcut Zincir (PR-15/16)

```
Preflight CLI → ReleasePolicy → ReleaseReport → ReleaseGate → stdout + exit code + artifact
```

### PR-17 Ekleme

```
Preflight CLI → ... mevcut akış ...
    └── (--metrics-dir sağlandıysa)
        → MetricExporter.from_preflight_output(json_output)
            → PreflightMetric
                → MetricStore.add(metric)
                    → export_json(store) → metrics/<timestamp>.json
                    → export_prometheus(store) → metrics/preflight.prom
```

### Bileşen Diyagramı

```
┌─────────────────────────────────────────────┐
│ Preflight CLI (release_preflight.py)        │
│   --metrics-dir artifacts/metrics/          │
└──────────────┬──────────────────────────────┘
               │ preflight JSON output
               ▼
┌─────────────────────────────────────────────┐
│ MetricExporter (preflight_metrics.py)       │
│   from_preflight_output(dict) → Metric      │
│   export_json(store) → JSON file            │
│   export_prometheus(store) → .prom file     │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│ MetricStore (preflight_metrics.py)          │
│   add(metric) / query(filters)              │
│   Thread-safe (Lock)                        │
└──────────────┬──────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────┐
│ DashboardSpec (preflight_dashboard.json)    │
│   3 panel: verdict trend, reasons, override │
│   Grafana JSON format                       │
└─────────────────────────────────────────────┘
```

## Bileşenler ve Arayüzler

### 1. PreflightMetric (Veri Modeli)

```python
@dataclass(frozen=True)
class PreflightMetric:
    timestamp: str          # ISO 8601
    verdict: str            # "release_ok" | "release_hold" | "release_block"
    exit_code: int          # 0 | 1 | 2
    reasons: list[str]      # ["TIER_FAIL", "FLAKY_TESTS", ...]
    override_applied: bool
    contract_breach: bool
    override_result: str    # "applied" | "rejected" | "breach" | "ignored" | "none"
    duration_ms: float      # preflight koşum süresi
    spec_hash: str
```

`override_result` türetme kuralı:
- `override_applied=True` → `"applied"`
- `contract_breach=True` → `"breach"`
- override flag'leri sağlanmış + BLOCK + breach yok → `"rejected"`
- override flag'leri sağlanmış + OK → `"ignored"`
- override flag'leri sağlanmamış → `"none"`

### 2. MetricStore

```python
class MetricStore:
    """Thread-safe in-memory metrik deposu."""

    def add(self, metric: PreflightMetric) -> None: ...
    def query(
        self,
        verdict: str | None = None,
        since: str | None = None,
    ) -> list[PreflightMetric]: ...
    def verdict_counts(self) -> dict[str, int]: ...
    def reason_counts(self) -> dict[str, int]: ...
    def override_counts(self) -> dict[str, int]: ...
    def __len__(self) -> int: ...
```

### 3. MetricExporter

```python
class MetricExporter:
    """Preflight sonuçlarını metrik formatına dönüştürür."""

    @staticmethod
    def from_preflight_output(output: dict) -> PreflightMetric: ...

    @staticmethod
    def export_json(store: MetricStore) -> str: ...

    @staticmethod
    def export_prometheus(store: MetricStore) -> str: ...
```

#### Sabit Metrik İsimleri ve Label Seti (Breaking Change Sözleşmesi)

Bu isimler ve label'lar sabittir. Değişiklik "breaking change" sayılır.

| Metrik | Tip | Label | Değerler |
|---|---|---|---|
| `release_preflight_verdict_total` | counter | `verdict` | `OK`, `HOLD`, `BLOCK` |
| `release_preflight_reason_total` | counter | `reason` | `BlockReasonCode` enum üyeleri |
| `release_preflight_override_total` | counter | `kind` | `attempt`, `applied`, `breach` |
| `release_preflight_telemetry_write_failures_total` | counter | — | (label yok) |

Metrik modeli: Seçenek B — Cumulative totals. MetricStore JSON persistence ile birikimli sayım tutar. Her run: `load_from_dir` → `add` → `save_to_dir`. Counter'lar monoton artar. `_total` suffix'i gerçek counter semantiğiyle eşleşir.

Tüm label değerleri her zaman yazılır (sıfır dahil). Sparse time series yok — Prometheus sorgularında `absent()` gerekmez.

**Label cardinality kuralı:** Tüm label değerleri sabit enum setlerinden gelir. `override_by` asla Prometheus label olmaz — yalnızca JSON audit alanıdır. Bu, cardinality patlamasını önler.

**Override kind semantiği:**
- `attempt`: override flag'leri sağlandı + BLOCK verdict → override reddedildi (ABSOLUTE olmasa bile BLOCK override edilemez)
- `applied`: override flag'leri sağlandı + HOLD verdict → override kabul edildi, exit 0
- `breach`: override flag'leri sağlandı + BLOCK verdict + ABSOLUTE_BLOCK_REASONS → CONTRACT_BREACH, sözleşme ihlali kaydı

**Fail-open gözlemlenebilirliği:** `release_preflight_telemetry_write_failures_total` counter'ı telemetry yazım hatalarını sayar. Telemetry tamamen çökse bile bu counter scrape'de görünür (son başarılı yazımdaki değer).

#### Prometheus Text Exposition Formatı

```
# HELP release_preflight_verdict_total Preflight verdict counter
# TYPE release_preflight_verdict_total counter
release_preflight_verdict_total{verdict="OK"} 5
release_preflight_verdict_total{verdict="HOLD"} 2
release_preflight_verdict_total{verdict="BLOCK"} 1

# HELP release_preflight_reason_total Preflight reason code counter
# TYPE release_preflight_reason_total counter
release_preflight_reason_total{reason="TIER_FAIL"} 2
release_preflight_reason_total{reason="FLAKY_TESTS"} 1
release_preflight_reason_total{reason="GUARD_VIOLATION"} 1
...

# HELP release_preflight_override_total Override attempt counter
# TYPE release_preflight_override_total counter
release_preflight_override_total{kind="attempt"} 2
release_preflight_override_total{kind="applied"} 1
release_preflight_override_total{kind="breach"} 1
```

### 4. Dashboard Tanımı

`monitoring/grafana/preflight-dashboard.json` — Grafana dashboard JSON.

3 panel:
1. **Verdict Trend**: Zaman serisi çizgi grafik, verdict bazlı (timeseries panel)
2. **Top Block Reasons**: Bar chart, reason code bazlı frekans (barchart panel)
3. **Override Attempts**: Pie chart, override sonucu bazlı (piechart panel)

### 5. CLI Genişletmesi

```python
# release_preflight.py — mevcut run_preflight() fonksiyonuna ekleme
def run_preflight(
    ...,
    metrics_dir: str | None = None,  # YENİ
) -> int:
    ...
    # Mevcut akış sonrası:
    if metrics_dir:
        from backend.app.testing.preflight_metrics import MetricExporter, MetricStore
        metric = MetricExporter.from_preflight_output(output)
        store = MetricStore()
        # Mevcut metrikleri yükle (varsa)
        store.load_from_dir(metrics_dir)
        store.add(metric)
        store.save_to_dir(metrics_dir)
```

### 6. Dosya Yazım Kuralları (--metrics-dir)

**Write-once per run:** Her preflight koşumu tek dosya yazar. Dosya adı deterministik: `preflight_metrics.json` (JSON) ve `preflight.prom` (Prometheus).

**Atomic write:** Temp dosyaya yaz → `os.replace()` ile rename. CI'da yarım dosya riskini keser.

```python
import tempfile, os

def _atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp, str(path))
    except Exception:
        os.close(fd) if not os.get_inheritable(fd) else None
        os.unlink(tmp)
        raise
```

**Fail-open:** Metrik yazımı başarısızsa preflight verdict'i ve exit code'u değişmez. Stderr'e uyarı yazılır, JSON audit'e `"metrics_write_failed": true` eklenir.

## Doğruluk Özellikleri

### Property 19: Determinism

Aynı preflight sonucu + aynı label'lar ⇒ aynı metrik çıktısı (byte-level). `export_prometheus(store)` ve `export_json(store)` deterministiktir.

**Validates: Requirements 1.3, 1.4**

### Property 20: Monotonic counters

Aynı run içinde counter asla azalmaz. Birden çok `add()` çağrısı olsa bile counter yalnız artar. `verdict_counts()[v] >= önceki_değer` her zaman doğrudur.

**Validates: Requirements 2.4**

### Property 21: Label safety / boundedness

Label cardinality bounded: `reason`, `verdict`, `kind` sabit enum setlerinden gelir. `override_by` label değil, sadece JSON audit alanıdır. Hiçbir metrik label'ı kullanıcı girdisinden türetilmez.

**Validates: Requirements 4.1-4.4**

## Hata Yönetimi

| Durum | Davranış |
|---|---|
| `--metrics-dir` sağlanmadı | Metrik üretilmez, mevcut davranış korunur |
| Metrik dizini yazılamaz | Stderr'e uyarı, exit code değişmez (fail-open) |
| Atomic write başarısız | Temp dosya temizlenir, stderr uyarı, exit code değişmez |
| Mevcut metrik dosyası bozuk JSON | Yeni store başlatılır, uyarı verilir |
| Preflight çıktısında beklenmeyen alan | Eksik alanlar varsayılan değerle doldurulur |
| `override_by` label olarak kullanılmaya çalışılırsa | Derleme/test hatası — label seti sabit enum'dan gelir |

## Test Stratejisi

### Test Dosyası

`backend/tests/test_preflight_metrics.py`

### Test Planı

**Unit Testler:**
1. `from_preflight_output()` — OK/HOLD/BLOCK çıktılarından doğru metrik oluşturma
2. `MetricStore.add()` + `verdict_counts()` — counter doğruluğu
3. `MetricStore.reason_counts()` — tüm enum üyeleri mevcut
4. `MetricStore.override_counts()` — dört sonuç tipi doğru
5. `export_json()` — geçerli JSON, round-trip
6. `export_prometheus()` — text exposition formatı doğruluğu
7. Dashboard JSON — geçerli Grafana formatı, 3 panel mevcut

**Parametrize Testler (property-like):**
8. Verdict counter monotonluğu (Property 19)
9. Override result ayrıklığı (Property 21)

**Geriye Dönük Uyumluluk:**
10. `--metrics-dir` olmadan preflight davranışı değişmez
