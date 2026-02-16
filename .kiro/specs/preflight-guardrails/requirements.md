# Gereksinimler: Preflight Guard-Rails

## Giriş

PR-17 ile preflight telemetry altyapısı kuruldu: MetricStore, MetricExporter, Prometheus text exposition, Grafana dashboard. Bu spec, mevcut altyapının üretimde bozulmaması için 5 koruma mekanizması (guard-rail) ekler:

1. **Monotonluk garantisi** — counter reset/rollback tespiti
2. **Cardinality guard** — BlockReasonCode enum büyümesine sert sınır
3. **Override semantik netliği** — runbook ve naming açıklığı
4. **Fail-open counter dayanıklılığı** — write failure counter'ın in-memory birikimi doğrulaması
5. **Hazır PromQL paketleri** — alert kuralları, dashboard sorguları ve runbook maddeleri

Scope sınırı: Mevcut `preflight_metrics.py` modülüne minimal ekleme + `ptf-admin-alerts.yml`'e yeni alert grubu + runbook genişletmesi + dashboard güncelleme. Yeni modül oluşturulmaz.

## Sözlük

- **MetricStore**: Preflight metrik kayıtlarını bellekte tutan ve JSON persistence ile birikimli sayım yapan yapı (`preflight_metrics.py`)
- **MetricExporter**: Preflight sonuçlarını Prometheus text exposition ve JSON formatına dönüştüren modül
- **BlockReasonCode**: Release policy'deki block/hold nedenlerini tanımlayan enum (`release_policy.py`)
- **Cardinality**: Bir metrik label'ının alabileceği farklı değer sayısı; yüksek cardinality Prometheus performansını düşürür
- **Counter_Reset**: Prometheus counter'ının beklenmedik şekilde sıfırlanması (container restart, disk kaybı vb.)
- **Fail-Open**: Telemetry yazım hatası durumunda preflight verdict'in değişmemesi prensibi
- **PromQL**: Prometheus Query Language — metrik sorgulama dili
- **Runbook**: Alert tetiklendiğinde izlenecek troubleshooting adımlarını içeren operasyonel doküman
- **Store_Generation**: MetricStore'un kaç kez yüklenip kaydedildiğini izleyen gauge metriği

## Gereksinimler

### Gereksinim 1: Monotonluk Garantisi — Counter Reset Tespiti

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, container restart veya disk kaybı sonrası counter reset'lerini otomatik tespit etmek istiyorum; böylece monotonluk bozulmasını hızla fark edebilirim.

#### Kabul Kriterleri

1. THE MetricExporter SHALL `release_preflight_store_generation` gauge metriği üretir; bu değer MetricStore her `load_from_dir` → `save_to_dir` döngüsünde 1 artar
2. THE MetricStore SHALL `store_generation` alanını JSON persistence dosyasında saklar ve yüklemede geri okur
3. WHEN MetricStore yeni bir boş store ile başlatıldığında (mevcut dosya bulunamadığında), THE MetricStore SHALL `store_generation` değerini 0 olarak başlatır
4. THE MetricExporter SHALL `release_preflight_store_start_timestamp` gauge metriği üretir; bu değer store'un ilk oluşturulma zamanını (Unix epoch saniye) içerir
5. WHEN MetricStore `load_from_dir` çağrısında mevcut dosyayı başarıyla yüklediğinde, THE MetricStore SHALL `store_start_timestamp` değerini mevcut dosyadan korur
6. WHEN MetricStore `load_from_dir` çağrısında dosya bulunamadığında veya bozuk olduğunda, THE MetricStore SHALL `store_start_timestamp` değerini şimdiki zamana ayarlar

### Gereksinim 2: Cardinality Guard — BlockReasonCode Enum Sınırı

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, BlockReasonCode enum'unun kontrolsüz büyümesini engellemek istiyorum; böylece Prometheus cardinality patlaması önlenir.

#### Kabul Kriterleri

1. THE test_suite SHALL BlockReasonCode enum üye sayısının 50'yi aşmadığını doğrular (`assert len(BlockReasonCode) <= 50`)
2. THE test_suite SHALL bu sınırı bir regression guard testi olarak çalıştırır; enum'a yeni üye eklendiğinde sınır aşılırsa test fail olur
3. THE MetricExporter SHALL Prometheus çıktısında reason label cardinality'sini `len(BlockReasonCode)` ile sınırlar; enum dışı değerler filtrelenir

### Gereksinim 3: Override Semantik Netliği — Runbook ve Naming

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, override metriklerindeki "attempt", "applied" ve "breach" terimlerinin ne anlama geldiğini runbook'ta net olarak görmek istiyorum; böylece alert'leri doğru yorumlayabilirim.

#### Kabul Kriterleri

1. THE Runbook SHALL "Preflight Override Semantiği" başlığı altında `attempt`, `applied` ve `breach` terimlerinin tanımlarını içerir
2. THE Runbook SHALL her override kind için somut senaryo örnekleri sunar (hangi durumda hangi kind üretilir)
3. THE Runbook SHALL `attempt` teriminin "override reddedildi (BLOCK verdict)" anlamına geldiğini açıkça belirtir
4. THE Runbook SHALL `breach` teriminin "sözleşme ihlali — ABSOLUTE_BLOCK_REASONS override girişimi" anlamına geldiğini açıkça belirtir

### Gereksinim 4: Fail-Open Counter Dayanıklılığı

**Kullanıcı Hikayesi:** Bir geliştirici olarak, `telemetry_write_failures_total` counter'ının disk yazım hatası sırasında bile in-memory olarak artmaya devam ettiğini doğrulamak istiyorum; böylece bir sonraki başarılı save'de persist edilir.

#### Kabul Kriterleri

1. THE MetricStore SHALL `write_failures` counter'ını in-memory olarak tutar; `save_to_dir` başarısız olduğunda bu counter artar
2. WHEN bir sonraki `save_to_dir` çağrısı başarılı olduğunda, THE MetricStore SHALL birikmiş `write_failures` değerini JSON dosyasına persist eder
3. THE test_suite SHALL şu senaryoyu doğrular: save fail → write_failures artar → sonraki başarılı save → load → write_failures değeri korunur
4. THE MetricStore SHALL `write_failures` counter'ını `record_write_failure()` çağrısı ile artırır; bu çağrı thread-safe olmalıdır

### Gereksinim 5: Hazır PromQL Paketleri — Alert Kuralları ve Dashboard

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, preflight metriklerini izleyen hazır alert kuralları ve dashboard sorguları istiyorum; böylece BLOCK spike, contract breach ve telemetry write failure durumlarını anında tespit edebilirim.

#### Kabul Kriterleri

1. THE Alert_Rules SHALL `PreflightBlockSpike` alert kuralı içerir: `increase(release_preflight_verdict_total{verdict="BLOCK"}[15m])` belirli bir eşiği aştığında tetiklenir
2. THE Alert_Rules SHALL `PreflightContractBreach` alert kuralı içerir: `increase(release_preflight_override_total{kind="breach"}[5m]) > 0` olduğunda anında tetiklenir (page-worthy severity)
3. THE Alert_Rules SHALL `PreflightTelemetryWriteFailure` alert kuralı içerir: `increase(release_preflight_telemetry_write_failures_total[15m]) > 0` olduğunda warning severity ile tetiklenir
4. THE Alert_Rules SHALL `PreflightCounterReset` alert kuralı içerir: `resets(release_preflight_verdict_total[1h]) > 0` olduğunda warning severity ile tetiklenir
5. THE DashboardSpec SHALL "Block Ratio" paneli içerir: `increase(release_preflight_verdict_total{verdict="BLOCK"}[15m]) / increase(release_preflight_verdict_total[15m])` sorgusu ile
6. THE DashboardSpec SHALL "Override Applied Rate" paneli içerir: `increase(release_preflight_override_total{kind="applied"}[1h])` sorgusu ile
7. THE DashboardSpec SHALL "Telemetry Health" paneli içerir: `release_preflight_telemetry_write_failures_total` ve `release_preflight_store_generation` gauge'larını gösteren stat paneli
8. THE Runbook SHALL her alert kuralı için troubleshooting adımları içerir (olası nedenler, ilk kontroller, müdahale adımları)
9. THE test_suite SHALL alert kuralları YAML dosyasının geçerli PrometheusRule formatında olduğunu doğrular
10. THE test_suite SHALL dashboard JSON'un yeni panelleri içerdiğini yapısal olarak doğrular
