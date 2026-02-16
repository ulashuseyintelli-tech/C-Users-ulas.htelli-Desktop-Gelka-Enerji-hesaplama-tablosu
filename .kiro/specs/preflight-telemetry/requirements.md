# Gereksinimler: Preflight Telemetry — PR-17

## Giriş

PR-15/16 ile release governance preflight kontrolü CI'da enforced çalışıyor. Ancak preflight sonuçları (verdict, reason, override) yalnızca artifact olarak saklanıyor — zaman serisi görünürlük yok. PR-17, preflight sonuçlarını yapılandırılmış metrik olarak dışa aktarır ve bu metrikleri görselleştiren dashboard panelleri tanımlar. Amaç: verdict trend, top block reasons ve override attempt'leri tek bakışta görmek.

Scope sınırı: Bu PR gerçek bir Prometheus/Grafana altyapısı kurmaz. Pure Python metrik modülü + JSON metrik çıktısı + dashboard JSON tanımı üretir. Gerçek altyapı bağlantısı pilot rollout'ta yapılır.

## Sözlük

- **PreflightMetric**: Tek bir preflight koşumunun metrik kaydı (verdict, reasons, override durumu, süre)
- **MetricExporter**: Preflight sonuçlarını yapılandırılmış metrik formatına dönüştüren modül
- **MetricStore**: Metrik kayıtlarını bellekte tutan ve sorgulanabilir kılan yapı
- **DashboardSpec**: Grafana dashboard JSON tanımı (panel yapısı, sorgu şablonları)
- **VerdictCounter**: Verdict bazlı sayaç (OK/HOLD/BLOCK ayrımı)
- **ReasonHistogram**: Reason code bazlı frekans dağılımı
- **OverrideTracker**: Override girişimlerinin sayısı ve sonucu (applied/rejected/breach)

## Gereksinimler

### Gereksinim 1: Metrik Modülü

**Kullanıcı Hikayesi:** Bir geliştirici olarak, preflight sonuçlarını yapılandırılmış metrik olarak kaydetmek istiyorum; böylece zaman serisi analiz yapılabilir.

#### Kabul Kriterleri

1. THE MetricExporter SHALL preflight JSON çıktısından `PreflightMetric` kaydı oluşturur: verdict, reasons listesi, override_applied, contract_breach, exit_code, duration_ms, timestamp
2. THE MetricStore SHALL metrik kayıtlarını bellekte tutar ve `add(metric)` / `query(filters)` arayüzü sunar
3. THE MetricExporter SHALL `export_json(store)` ile tüm metrikleri JSON formatında dışa aktarır
4. THE MetricExporter SHALL `export_prometheus(store)` ile Prometheus text exposition formatında dışa aktarır (counter + histogram)
5. THE MetricStore SHALL thread-safe olmalıdır (concurrent add/query)

### Gereksinim 2: Verdict Counter Metrikleri

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, verdict dağılımını zaman serisi olarak görmek istiyorum; böylece OK/HOLD/BLOCK trendini izleyebilirim.

#### Kabul Kriterleri

1. THE MetricExporter SHALL `preflight_verdict_total{verdict="release_ok"}` counter metriği üretir
2. THE MetricExporter SHALL `preflight_verdict_total{verdict="release_hold"}` counter metriği üretir
3. THE MetricExporter SHALL `preflight_verdict_total{verdict="release_block"}` counter metriği üretir
4. THE MetricExporter SHALL her verdict counter'ını monoton artan tutar (reset yok)

### Gereksinim 3: Reason Histogram Metrikleri

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, en sık karşılaşılan block/hold nedenlerini görmek istiyorum; böylece iyileştirme önceliğini belirleyebilirim.

#### Kabul Kriterleri

1. THE MetricExporter SHALL `preflight_reason_total{reason="TIER_FAIL"}` gibi reason bazlı counter üretir
2. THE MetricExporter SHALL tüm `BlockReasonCode` enum üyeleri için counter üretir (sıfır olanlar dahil)
3. THE MetricExporter SHALL birden fazla reason içeren preflight sonuçlarında her reason'ı ayrı sayar

### Gereksinim 4: Override Tracker Metrikleri

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, override girişimlerini ve sonuçlarını izlemek istiyorum; böylece override kötüye kullanımını tespit edebilirim.

#### Kabul Kriterleri

1. THE MetricExporter SHALL `preflight_override_total{result="applied"}` counter üretir
2. THE MetricExporter SHALL `preflight_override_total{result="rejected"}` counter üretir (BLOCK + override girişimi)
3. THE MetricExporter SHALL `preflight_override_total{result="breach"}` counter üretir (CONTRACT_BREACH)
4. THE MetricExporter SHALL `preflight_override_total{result="ignored"}` counter üretir (OK + override veya kısmi flag)

### Gereksinim 5: Dashboard Tanımı

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, preflight metriklerini Grafana dashboard'unda görmek istiyorum; böylece tek bakışta durumu anlayabilirim.

#### Kabul Kriterleri

1. THE DashboardSpec SHALL "Verdict Trend" paneli içerir: zaman serisi, verdict bazlı çizgi grafik
2. THE DashboardSpec SHALL "Top Block Reasons" paneli içerir: reason bazlı bar chart (son N koşum)
3. THE DashboardSpec SHALL "Override Attempts" paneli içerir: override sonucu bazlı pie/donut chart
4. THE DashboardSpec SHALL geçerli Grafana dashboard JSON formatında olmalıdır
5. THE DashboardSpec SHALL yapısal doğrulama testi ile kilitlenmelidir

### Gereksinim 6: CI Entegrasyonu

**Kullanıcı Hikayesi:** Bir geliştirici olarak, preflight koşumu sonrasında metriklerin otomatik kaydedilmesini istiyorum; böylece manuel adım gerekmez.

#### Kabul Kriterleri

1. THE Preflight_CLI SHALL `--metrics-dir` flag'i ile metrik çıktı dizini kabul eder
2. WHEN `--metrics-dir` sağlandığında, THE Preflight_CLI SHALL preflight sonucunu MetricExporter'a iletir ve JSON metrik dosyası üretir
3. THE CI_Workflow SHALL metrik artifact'ını preflight report ile birlikte yükler
4. WHEN `--metrics-dir` sağlanmadığında, THE Preflight_CLI SHALL mevcut davranışı korur (geriye dönük uyumluluk)

### Gereksinim 7: Telemetry Testleri

**Kullanıcı Hikayesi:** Bir geliştirici olarak, metrik üretimi ve dashboard tanımını doğrulayan testler istiyorum; böylece telemetry sözleşmesi regresyona karşı korunur.

#### Kabul Kriterleri

1. THE test_suite SHALL MetricExporter'ın preflight JSON'dan doğru PreflightMetric oluşturduğunu doğrular
2. THE test_suite SHALL verdict counter'larının monoton arttığını doğrular
3. THE test_suite SHALL reason histogram'ın tüm BlockReasonCode üyelerini kapsadığını doğrular
4. THE test_suite SHALL override tracker'ın dört sonuç tipini (applied/rejected/breach/ignored) doğru saydığını doğrular
5. THE test_suite SHALL dashboard JSON'un geçerli Grafana formatında olduğunu doğrular
6. THE test_suite SHALL `--metrics-dir` flag'inin geriye dönük uyumlu olduğunu doğrular


## Kanıt Matrisi (Proof Matrix)

| Requirement | Test Dosyası :: Test Adı |
|---|---|
| 1.1 (MetricExporter → PreflightMetric) | `test_preflight_metrics.py::TestFromPreflightOutput::test_ok_verdict` + `test_hold_verdict` + `test_block_verdict` + `test_block_breach` |
| 1.2 (MetricStore add/query) | `test_preflight_metrics.py::TestMetricStore::test_add_increments_verdict` / `test_preflight_metrics_roundtrip.py::TestCoreRoundTrip::test_random_ops_save_load_equality` |
| 1.3 (export_json) | `test_preflight_metrics.py::TestJsonExport::test_json_valid` + `test_json_round_trip` |
| 1.4 (export_prometheus) | `test_preflight_metrics.py::TestPrometheusFormat::test_contains_help_and_type` + `test_verdict_lines` + `test_reason_lines` |
| 1.5 (thread-safe) | `test_preflight_metrics.py::TestThreadSafety::test_concurrent_adds` / `test_preflight_metrics_roundtrip.py::TestAtomicitySaveFailInjection` |
| 2.1-2.3 (verdict counters) | `test_preflight_metrics.py::TestPrometheusFormat::test_verdict_lines` |
| 2.4 (monotonic) | `test_preflight_metrics.py::TestMonotonicCounters::test_verdict_monotonic` |
| 3.1-3.2 (reason histogram) | `test_preflight_metrics.py::TestReasonCountsComplete::test_all_reason_codes_present` + `test_reason_count_matches_enum_size` |
| 4.1-4.4 (override tracker) | `test_preflight_metrics.py::TestOverrideKindSemantics` (4 test) |
| 5.1-5.4 (dashboard) | `test_preflight_dashboard.py` |
| 6.1-6.4 (CLI --metrics-dir) | `test_release_preflight.py` (CLI entegrasyon testleri) |
| 7.1-7.4 (telemetry testleri) | `test_preflight_metrics.py` (43 test) + `test_preflight_metrics_roundtrip.py` (5 PBT, 200 example each) |
