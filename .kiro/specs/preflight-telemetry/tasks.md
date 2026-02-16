# Uygulama Planı: Preflight Telemetry — PR-17

## Genel Bakış

Preflight sonuçlarını yapılandırılmış metrik olarak dışa aktarır. Pure Python metrik modülü, Prometheus/JSON export, Grafana dashboard tanımı. Mevcut zincire minimal dokunuş: tek flag (`--metrics-dir`) eklenir.

## Görevler

- [x] 1. Metrik modülü (`preflight_metrics.py`)
  - [x] 1.1 `PreflightMetric` dataclass tanımla
    - Alanlar: timestamp, verdict, exit_code, reasons, override_applied, contract_breach, override_result, duration_ms, spec_hash
    - `override_result` türetme kuralı: applied/rejected/breach/ignored/none
    - frozen=True (immutable)
    - _Requirements: 1.1_

  - [x] 1.2 `MetricStore` sınıfı
    - Thread-safe in-memory depo (threading.Lock)
    - `add(metric)`, `query(verdict?, since?)`, `__len__()`
    - `verdict_counts()` → `{"release_ok": N, "release_hold": N, "release_block": N}`
    - `reason_counts()` → tüm BlockReasonCode üyeleri (sıfır dahil)
    - `override_counts()` → `{"applied": N, "rejected": N, "breach": N, "ignored": N, "none": N}`
    - `load_from_dir(path)` / `save_to_dir(path)` — JSON persistence
    - _Requirements: 1.2, 1.5, 2.1-2.4, 3.1-3.2, 4.1-4.4_

  - [x] 1.3 `MetricExporter` sınıfı
    - `from_preflight_output(dict) → PreflightMetric`
    - `export_json(store) → str` (JSON formatı)
    - `export_prometheus(store) → str` (Prometheus text exposition)
    - _Requirements: 1.1, 1.3, 1.4_

- [x] 2. Metrik modülü testleri
  - [x]* 2.1 Unit testler (`test_preflight_metrics.py`)
    - PreflightMetric oluşturma (OK/HOLD/BLOCK)
    - override_result türetme (5 durum)
    - MetricStore add/query/counts
    - verdict_counts monotonluğu (Property 19)
    - reason_counts tüm enum üyeleri (Property 20)
    - override_counts ayrıklığı (Property 21)
    - export_json round-trip
    - export_prometheus format doğruluğu
    - _Requirements: 7.1-7.4_

  - [x] 2.2 Save/Load Round-Trip Property Tests (Hypothesis PBT)
    - [x] 2.2.1 Core round-trip property: random ops → save → load → equality
      - Rastgele ops dizisi ile store doldur (ADD + ara SAVE/RELOAD)
      - Final: SAVE → LOAD → canonical deep-compare
      - Eşdeğerlik: meta.store_start_timestamp, store_generation (save sonrası snapshot), tüm counter map'leri
      - Canonical equality helper (key ordering + float/int normalize)
      - Evidence: `test_preflight_metrics_roundtrip.py::TestCoreRoundTrip::test_random_ops_save_load_equality`
      - _Validates: Requirements 1.2, 1.5_
    - [x] 2.2.2 Save fail injection → atomicity property
      - _atomic_write fail injection ile SAVE sırasında hata
      - Sonuç: ya eski valid JSON kalmalı ya hiç dosya olmamalı
      - LOAD: ya önceki state ya defaults — yarım state asla gözlemlenmemeli
      - Evidence: `test_preflight_metrics_roundtrip.py::TestAtomicitySaveFailInjection::test_fail_injection_preserves_old_or_empty`
      - Evidence: `test_preflight_metrics_roundtrip.py::TestAtomicitySaveFailInjection::test_corrupted_json_triggers_failopen_defaults`
      - _Validates: Requirements 1.2, 1.5_
    - [x] 2.2.3 Legacy backfill state → save → load → equality
      - store_start_timestamp eksik (eski format) state üret
      - Save → load → meta normalize sonrası equality
      - Evidence: `test_preflight_metrics_roundtrip.py::TestLegacyBackfillRoundTrip::test_legacy_format_without_timestamp_roundtrips`
      - Evidence: `test_preflight_metrics_roundtrip.py::TestLegacyBackfillRoundTrip::test_extra_fields_in_json_ignored_gracefully`
      - _Validates: Requirements 1.2_
    - DoD: En az 3 Hypothesis property, 200 example; canonical equality helper; shrink çıktısı debug edilebilir (ops trace)

- [x] 3. Dashboard tanımı
  - [x] 3.1 `monitoring/grafana/preflight-dashboard.json` oluştur
    - Verdict Trend paneli (timeseries)
    - Top Block Reasons paneli (barchart)
    - Override Attempts paneli (piechart)
    - Geçerli Grafana dashboard JSON formatı
    - _Requirements: 5.1-5.4_

  - [x]* 3.2 Dashboard yapısal doğrulama testi
    - JSON geçerli parse edilir
    - 3 panel mevcut (verdict, reasons, override)
    - Panel tipleri doğru
    - _Requirements: 5.5_

- [x] 4. CLI entegrasyonu
  - [x] 4.1 `release_preflight.py`'ye `--metrics-dir` flag'i ekle
    - run_preflight() imzasına metrics_dir parametresi ekle
    - Preflight sonrası MetricExporter ile metrik üret ve kaydet
    - Geriye dönük uyumluluk: flag olmadan davranış değişmez
    - _Requirements: 6.1-6.4_

  - [x] 4.2 CI workflow'a metrik artifact upload ekle
    - `--metrics-dir artifacts/metrics/` flag'ini preflight komutuna ekle
    - Metrik artifact'ını ayrı upload adımı ile yükle
    - _Requirements: 6.3_

  - [x]* 4.3 CLI entegrasyon testleri
    - `--metrics-dir` ile metrik dosyası üretilir
    - `--metrics-dir` olmadan metrik üretilmez
    - _Requirements: 7.6_

- [x] 5. Final checkpoint
  - Tüm testlerin geçtiğini doğrula (mevcut + yeni), sorular varsa kullanıcıya sor.

## Notlar

- Harici bağımlılık yok — pure Python
- Prometheus client kütüphanesi kullanılmaz; text exposition formatı elle üretilir
- MetricStore bellekte çalışır; CI'da her koşum önceki metrikleri JSON'dan yükler
- `*` ile işaretli görevler opsiyoneldir
- Dashboard JSON Grafana 9+ formatında olacak
