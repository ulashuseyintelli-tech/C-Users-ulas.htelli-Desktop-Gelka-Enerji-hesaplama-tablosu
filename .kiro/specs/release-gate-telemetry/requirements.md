# Gereksinimler Dokümanı — Release Gate Telemetry

## Giriş

Bu özellik, mevcut ReleaseGate enforcement hook'una (`backend/app/testing/release_gate.py`) gözlemlenebilirlik (observability) katmanı ekler. ReleaseGate, ReleasePolicy ile Orchestrator arasında oturan saf karar katmanıdır ve GateDecision nesneleri üretir. Gate telemetrisi, mevcut preflight_metrics.py'deki MetricStore/Counter desenini takip eder: saf Python, harici bağımlılık yok, JSON persistence, Prometheus text exposition formatı, atomik yazım.

## Sözlük

- **ReleaseGate**: ReleasePolicy kararlarını uygulayan enforcement hook. `release_gate.py` içinde tanımlı.
- **GateDecision**: ReleaseGate.check() çağrısının ürettiği karar nesnesi (allowed/denied, verdict, reasons).
- **BlockReasonCode**: Release kararlarındaki blok nedenlerini tanımlayan sabit enum (10 üye: TIER_FAIL, FLAKY_TESTS, DRIFT_ALERT, CANARY_BREAKING, GUARD_VIOLATION, OPS_GATE_FAIL, NO_TIER_DATA, NO_FLAKE_DATA, NO_DRIFT_DATA, NO_CANARY_DATA).
- **ABSOLUTE_BLOCK_REASONS**: Override ile geçersiz kılınamayan blok nedenleri kümesi (GUARD_VIOLATION, OPS_GATE_FAIL).
- **MetricStore**: `preflight_metrics.py`'de tanımlı, thread-safe, in-memory metrik deposu. Counter deseni, JSON persistence, Prometheus text exposition.
- **GateMetricStore**: ReleaseGate telemetrisi için oluşturulacak MetricStore benzeri depo.
- **Counter**: Monoton artan sayaç. Yalnızca artırılabilir, azaltılamaz.
- **Label_Cardinality**: Bir metriğin etiket değerlerinin toplam kombinasyon sayısı. Sınırlı tutulmalıdır.
- **Fail_Open_Metric**: Metrik yazım hatası gate kararını engellemez; hata sayacı artırılır.
- **Fail_Closed_Gate**: Audit yazım hatası durumunda gate kararı allowed=False döner (R3 invariantı).
- **PrometheusRule_CRD**: Kubernetes ortamında Prometheus alert kurallarını tanımlayan Custom Resource Definition.
- **Grafana_Dashboard**: Metrik görselleştirme panellerini içeren JSON yapılandırma dosyası.

## Gereksinimler

### Gereksinim 1: Gate Karar Sayacı

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, her ReleaseGate.check() çağrısının karar dağılımını izlemek istiyorum, böylece ALLOW/DENY oranlarını ve blok nedenlerini gözlemleyebilirim.

#### Kabul Kriterleri

1. WHEN ReleaseGate.check() çağrılır ve GateDecision üretilir, THE GateMetricStore SHALL `release_gate_decision_total` sayacını decision ve reason etiketleriyle bir artırmak
2. THE GateMetricStore SHALL decision etiketini yalnızca "ALLOW" ve "DENY" değerleriyle sınırlamak
3. THE GateMetricStore SHALL reason etiketini yalnızca BlockReasonCode enum üyelerinden gelen değerlerle sınırlamak
4. WHEN GateDecision.allowed True ise, THE GateMetricStore SHALL decision etiketini "ALLOW" olarak kaydetmek
5. WHEN GateDecision.allowed False ise, THE GateMetricStore SHALL decision etiketini "DENY" olarak ve her BlockReasonCode için ayrı sayaç artırmak

### Gereksinim 2: Sözleşme İhlali Sayacı

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, ABSOLUTE_BLOCK_REASONS üzerinde override girişimlerini izlemek istiyorum, böylece sözleşme ihlali denemelerini tespit edebilirim.

#### Kabul Kriterleri

1. WHEN bir override ABSOLUTE_BLOCK_REASONS içeren bir RELEASE_BLOCK kararı üzerinde denendiğinde ve hard reject (CONTRACT_BREACH_NO_OVERRIDE) oluştuğunda, THE GateMetricStore SHALL `release_gate_contract_breach_total{kind="NO_OVERRIDE"}` sayacını bir artırmak
2. THE GateMetricStore SHALL kind etiketini yalnızca "NO_OVERRIDE" değeriyle sınırlamak
3. WHEN override girişimi ABSOLUTE_BLOCK_REASONS dışındaki nedenler için yapıldığında, THE GateMetricStore SHALL sözleşme ihlali sayacını artırmamak

### Gereksinim 3: Audit Yazım Hatası Sayacı

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, audit yazım hatalarını izlemek istiyorum, böylece R3 fail-closed invariantının tetiklenme sıklığını gözlemleyebilirim.

#### Kabul Kriterleri

1. WHEN audit yazımı başarısız olduğunda, THE GateMetricStore SHALL `release_gate_audit_write_failures_total` sayacını bir artırmak
2. WHEN audit yazımı başarılı olduğunda, THE GateMetricStore SHALL audit yazım hatası sayacını artırmamak

### Gereksinim 4: Metrik Yazım Fail-Open Davranışı

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, metrik yazım hatalarının gate kararını etkilememesini istiyorum, böylece telemetri arızası release sürecini engellemez.

#### Kabul Kriterleri

1. IF metrik sayacı artırma işlemi başarısız olursa, THEN THE ReleaseGate SHALL gate kararını değiştirmeden döndürmek
2. IF metrik sayacı artırma işlemi başarısız olursa, THEN THE GateMetricStore SHALL hatayı sessizce yutmak ve gate akışını kesmemek
3. THE GateMetricStore SHALL metrik yazım hatalarını ayrı bir dahili hata sayacıyla izlemek

### Gereksinim 5: MetricStore Deseni Uyumu

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, gate telemetrisinin mevcut preflight_metrics.py deseniyle tutarlı olmasını istiyorum, böylece operasyonel karmaşıklık artmaz.

#### Kabul Kriterleri

1. THE GateMetricStore SHALL saf Python ile implemente edilmek ve harici metrik kütüphanesi kullanmamak
2. THE GateMetricStore SHALL thread-safe counter artırma işlemleri sağlamak
3. THE GateMetricStore SHALL JSON persistence desteği sunmak (to_dict / from_dict)
4. THE GateMetricStore SHALL Prometheus text exposition formatında metrik çıktısı üretmek
5. THE GateMetricStore SHALL atomik dosya yazımı kullanmak (temp dosya → rename)

### Gereksinim 6: ReleaseGate Entegrasyonu

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, metrik artırma noktalarının ReleaseGate.check() içinde minimal değişiklikle eklenmesini istiyorum, böylece mevcut gate mantığı bozulmaz.

#### Kabul Kriterleri

1. THE ReleaseGate SHALL tek bir emisyon noktası olarak check() metodu içinden metrik artırma çağrıları yapmak
2. WHEN check() çağrılır, THE ReleaseGate SHALL her çağrıda karar sayacını artırmak
3. WHEN CONTRACT_BREACH_NO_OVERRIDE yolu tetiklendiğinde, THE ReleaseGate SHALL sözleşme ihlali sayacını artırmak
4. WHEN audit yazımı başarısız olduğunda, THE ReleaseGate SHALL audit yazım hatası sayacını artırmak
5. THE ReleaseGate SHALL mevcut check() metodunun karar mantığını değiştirmemek; yalnızca sayaç artırma çağrıları eklemek

### Gereksinim 7: Prometheus Alert Kuralları

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, gate telemetrisi için Prometheus alert kuralları istiyorum, böylece sözleşme ihlalleri ve audit hataları otomatik olarak bildirilir.

#### Kabul Kriterleri

1. THE Alert_Kuralları SHALL `ptf-admin-alerts.yml` dosyasına yeni bir alert grubu olarak eklemek
2. WHEN `release_gate_contract_breach_total` son 5 dakikada artış gösterdiğinde, THE Alert_Kuralları SHALL severity: critical seviyesinde alert üretmek
3. WHEN `release_gate_audit_write_failures_total` son 15 dakikada artış gösterdiğinde, THE Alert_Kuralları SHALL severity: warning seviyesinde alert üretmek
4. WHEN `release_gate_decision_total{decision="DENY"}` son 15 dakikada belirli bir eşiği aştığında, THE Alert_Kuralları SHALL severity: warning seviyesinde alert üretmek
5. THE Alert_Kuralları SHALL her alert için runbook URL'si, açıklayıcı summary ve description alanları içermek

### Gereksinim 8: Grafana Dashboard

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, gate kararlarını görselleştiren bir Grafana dashboard'u istiyorum, böylece ALLOW/DENY trendlerini ve blok nedenlerini hızlıca analiz edebilirim.

#### Kabul Kriterleri

1. THE Dashboard SHALL `monitoring/grafana/` dizininde yeni bir JSON dosyası olarak oluşturulmak
2. THE Dashboard SHALL ALLOW ve DENY oranlarını gösteren bir zaman serisi paneli içermek
3. THE Dashboard SHALL en sık karşılaşılan DENY nedenlerini gösteren bir topk bar chart paneli içermek
4. THE Dashboard SHALL audit yazım hatalarını gösteren bir stat paneli içermek
5. THE Dashboard SHALL mevcut preflight-dashboard.json ile tutarlı panel yapısı ve stil kullanmak

### Gereksinim 9: Etiket Kardinalite Sınırlaması

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, metrik etiketlerinin sınırlı kardinaliteye sahip olmasını istiyorum, böylece Prometheus'ta yüksek kardinalite sorunu oluşmaz.

#### Kabul Kriterleri

1. THE GateMetricStore SHALL decision etiket değerlerini sabit bir kümeyle sınırlamak (yalnızca "ALLOW", "DENY")
2. THE GateMetricStore SHALL reason etiket değerlerini yalnızca BlockReasonCode enum üyelerinden kabul etmek
3. THE GateMetricStore SHALL kind etiket değerlerini sabit bir kümeyle sınırlamak (yalnızca "NO_OVERRIDE")
4. THE GateMetricStore SHALL tenant, kullanıcı veya istek kimliği gibi yüksek kardinaliteli etiketler kullanmamak

### Gereksinim 10: Runbook Güncellemesi

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, gate telemetrisi alert'leri için PromQL snippet'leri içeren runbook güncellemesi istiyorum, böylece olay müdahalesinde hızlı referans sağlanır.

#### Kabul Kriterleri

1. THE Runbook SHALL her yeni alert kuralı için bir bölüm içermek
2. THE Runbook SHALL her bölümde tanılama PromQL sorguları sağlamak
3. THE Runbook SHALL her bölümde olası kök nedenler ve müdahale adımları listelemek
