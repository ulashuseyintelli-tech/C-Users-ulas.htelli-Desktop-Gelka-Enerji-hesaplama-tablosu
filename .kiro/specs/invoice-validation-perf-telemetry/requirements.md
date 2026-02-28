# Gereksinimler Dokümanı — Fatura Doğrulama Performans Telemetrisi (Faz G)

## Giriş

Bu doküman, fatura doğrulama pipeline'ının performans telemetri altyapısını tanımlar.
Faz A–F tamamlanmış durumdadır (54 test, 0 fail). Shadow + Enforcement + Wiring aktiftir.
Varsayılan mod = shadow (davranış değişikliği yok). Blocker kümesi config-driven'dır.
Bilinen divergence (`missing_totals_skips`) bilinçli whitelist'tedir.
Deterministik sampling ve metrik bütçesi mevcuttur.

Faz G'nin amacı: doğrulama pipeline'ının gecikme maliyetini ölçmek, mod geçişlerinin
etkisini karşılaştırmak ve rollout öncesi yapılandırılabilir gecikme bütçesi tanımlamaktır.

### Kapsam Dışı

- Grafana dashboard JSON üretimi (ops sorumluluğu)
- Tedarikçi eşleme/normalizasyon (Faz H)
- Retry politikası refaktörü (ayrı iş; burada yalnızca terminal-state kısıtı eklenir)
- Uyumsuzluk oranı eşiği yapılandırması (Faz E shadow telemetry domain'i; `INVOICE_SHADOW_ALERT_RATE` burada ele alınmaz)

## Sözlük

- **Pipeline**: `extract_canonical()` içindeki doğrulama akışı — shadow karşılaştırma ve enforcement kararını kapsar
- **Shadow_Fazı**: Yeni validator'ın eski validator ile paralel çalıştırılıp sonuçların karşılaştırıldığı aşama (karar vermez)
- **Enforcement_Fazı**: Yeni validator'ın mod'a göre (soft/hard) karar verdiği aşama
- **Toplam_Süre**: Bir fatura için pipeline'ın tamamının geçen süresi (shadow + enforcement dahil)
- **Telemetri_Modülü**: `backend/app/invoice/validation/` altında performans metriklerini kaydeden modül
- **Gecikme_Bütçesi**: P95/P99 yüzdelik dilimler için yapılandırılabilir üst sınır (milisaniye)
- **Terminal_Durum**: Worker retry mekanizmasının tekrar denemeyeceği, iş reddi kategorisindeki nihai durum
- **Histogram**: Prometheus histogram tipi metrik — bucket'lar üzerinden yüzdelik dilim hesabı sağlar
- **Gauge**: Prometheus gauge tipi metrik — anlık değer gösteren sayaç
- **Faz_Etiketi**: Histogram metriğindeki `phase` label'ı — kapalı küme: `total`, `shadow`, `enforcement`
- **Mod_Gauge**: Aktif doğrulama modunu gösteren gauge metriği — her mod için ayrı zaman serisi
- **Örnekleme**: Deterministik SHA-256 tabanlı sampling — shadow fazı yalnızca örneklenen faturalarda çalışır
- **ValidationBlockedError**: `enforce_hard` modunda blocker kod tespit edildiğinde fırlatılan exception

## Gereksinimler

### Gereksinim 1: Faz Etiketli Histogram Metriği

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, doğrulama pipeline'ının her fazının gecikme dağılımını görmek istiyorum, böylece darboğazları tespit edebilirim.

#### Kabul Kriterleri

1. THE Telemetri_Modülü SHALL `invoice_validation_duration_seconds` adında tek bir histogram metriği sunacaktır; faz ayrımı `phase` label'ı ile sağlanacaktır
2. THE Telemetri_Modülü SHALL `phase` label değerlerini kapalı küme olarak zorunlu kılacaktır; geçerli değerler yalnızca `total`, `shadow` ve `enforcement` olacaktır
3. IF `phase` label'ına kapalı küme dışında bir değer verilirse, THEN THE Telemetri_Modülü SHALL metric gözlemi kaydetmeyecek ve error loglanacaktır (fail-closed)
4. THE Telemetri_Modülü SHALL histogram metriğini ayrı metrikler yerine label tabanlı sunacaktır (kardinalite kontrolü)

### Gereksinim 2: Toplam Süre Ölçümü

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, her fatura için toplam doğrulama süresini ölçmek istiyorum, böylece pipeline'ın genel performansını izleyebilirim.

#### Kabul Kriterleri

1. THE Telemetri_Modülü SHALL her fatura için `phase="total"` etiketiyle toplam doğrulama süresini kaydedecektir
2. THE Telemetri_Modülü SHALL toplam süre ölçümünü örnekleme durumundan bağımsız olarak her fatura için gerçekleştirecektir
3. WHEN doğrulama pipeline'ı tamamlandığında, THE Telemetri_Modülü SHALL süreyi saniye cinsinden histogram'a gözlem olarak ekleyecektir

### Gereksinim 3: Shadow Fazı Süre Ölçümü

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, shadow karşılaştırmanın eklediği gecikmeyi ayrıca ölçmek istiyorum, böylece shadow modunun maliyetini değerlendirebilirim.

#### Kabul Kriterleri

1. WHILE mod shadow veya enforcement iken, THE Telemetri_Modülü SHALL shadow fazı süresini yalnızca örneklenen faturalar için `phase="shadow"` etiketiyle kaydedecektir
2. WHEN fatura örnekleme dışı kaldığında, THE Telemetri_Modülü SHALL shadow fazı için gözlem kaydetmeyecektir
3. THE Telemetri_Modülü SHALL shadow fazı süresini toplam süreden bağımsız olarak ayrıca ölçecektir

### Gereksinim 4: Enforcement Fazı Süre Ölçümü

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, enforcement kararının eklediği gecikmeyi ölçmek istiyorum, böylece mod geçişlerinin performans etkisini karşılaştırabilirim.

#### Kabul Kriterleri

1. WHILE mod `enforce_soft` veya `enforce_hard` iken, THE Telemetri_Modülü SHALL her fatura için `phase="enforcement"` etiketiyle enforcement fazı süresini kaydedecektir
2. WHILE mod `shadow` veya `off` iken, THE Telemetri_Modülü SHALL enforcement fazı için gözlem kaydetmeyecektir (enforcement çalışmaz)
3. THE Telemetri_Modülü SHALL enforcement fazı süresini toplam süreden bağımsız olarak ayrıca ölçecektir

### Gereksinim 5: Mod Gauge Metriği

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, aktif doğrulama modunu anlık olarak görmek istiyorum, böylece mod geçişlerini izleyebilirim.

#### Kabul Kriterleri

1. THE Telemetri_Modülü SHALL `invoice_validation_mode` adında bir gauge metriği sunacaktır; mod ayrımı `mode` label'ı ile sağlanacaktır
2. THE Telemetri_Modülü SHALL aktif mod için gauge değerini `1`, diğer modlar için `0` olarak ayarlayacaktır
3. THE Telemetri_Modülü SHALL `mode` label değerlerini `shadow`, `enforce_soft`, `enforce_hard` ve `off` olarak sınırlayacaktır
4. THE Telemetri_Modülü SHALL mod gauge'unu süre histogram'ının label'ı olarak kullanmayacaktır (kardinalite patlaması önlemi)
5. WHEN mod değiştirildiğinde, THE Telemetri_Modülü SHALL yalnızca yeni aktif modun gauge değerini `1` yapacak, diğerlerini `0` yapacaktır


### Gereksinim 6: Terminal Durum — ValidationBlockedError Retry Engeli

**Kullanıcı Hikayesi:** Bir geliştirici olarak, `enforce_hard` modunda bloklanan faturaların worker tarafından tekrar denenmemesini istiyorum, böylece sonsuz döngü riski ortadan kalkar.

#### Kabul Kriterleri

1. WHEN `enforce_hard` modunda bir fatura bloklandığında, THE Pipeline SHALL `ValidationBlockedError` fırlatacaktır
2. THE ValidationBlockedError SHALL terminal durum (iş reddi) kategorisinde sınıflandırılacaktır
3. IF bir worker `ValidationBlockedError` yakaladığında, THEN THE Worker SHALL bu faturayı tekrar denemeyecektir
4. THE Pipeline SHALL `ValidationBlockedError` için retry sayacını artırmayacaktır
5. WHEN `ValidationBlockedError` fırlatıldığında, THE Pipeline SHALL hatayı loglayacak ve faturayı terminal hata olarak işaretleyecektir; retry edilmeyecektir

### Gereksinim 7: Yapılandırılabilir Gecikme Bütçesi

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, doğrulama pipeline'ı için P95/P99 gecikme bütçesi tanımlamak istiyorum, böylece rollout öncesi performans regresyonlarını tespit edebilirim.

#### Kabul Kriterleri

1. THE Telemetri_Modülü SHALL `INVOICE_VALIDATION_LATENCY_BUDGET_P95_MS` ortam değişkenini opsiyonel olarak okuyacaktır
2. THE Telemetri_Modülü SHALL `INVOICE_VALIDATION_LATENCY_BUDGET_P99_MS` ortam değişkenini opsiyonel olarak okuyacaktır
3. WHILE gecikme bütçesi tanımlı değilken, THE Telemetri_Modülü SHALL yalnızca ölçüm yapacak, uyarı üretmeyecektir
4. WHILE gecikme bütçesi tanımlıyken, THE Telemetri_Modülü SHALL bütçe aşımını tespit edecek ve log kaydı oluşturacaktır
5. THE Telemetri_Modülü SHALL bütçe değerlerini pozitif sayı olarak doğrulayacaktır; geçersiz değerlerde bütçe devre dışı kalacaktır

### Gereksinim 8: Mod Geçişi Karşılaştırma Senaryoları

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, farklı modlar arasındaki gecikme farkını karşılaştırmak istiyorum, böylece mod geçişinin performans etkisini ölçebilirim.

#### Kabul Kriterleri

1. WHEN mod `shadow` iken, THE Telemetri_Modülü SHALL baseline P95/P99 değerlerini `phase="total"` histogram'ından hesaplayabilecek veri sağlayacaktır
2. WHEN mod `enforce_soft` olarak değiştirildiğinde, THE Telemetri_Modülü SHALL ek enforcement maliyetini `phase="enforcement"` histogram'ından ayrıca gösterecektir
3. WHEN mod `enforce_hard` olarak değiştirildiğinde, THE Telemetri_Modülü SHALL enforcement maliyetini `phase="enforcement"` histogram'ından ayrıca gösterecektir
4. THE Telemetri_Modülü SHALL mod geçişlerinde mevcut histogram verilerini sıfırlamayacaktır (Prometheus doğal davranışı)

### Gereksinim 9: Metrik Bütünlüğü ve Kardinalite Kontrolü

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, metrik kardinalitesinin kontrol altında kalmasını istiyorum, böylece Prometheus depolama maliyeti yönetilebilir olur.

#### Kabul Kriterleri

1. THE Telemetri_Modülü SHALL süre ölçümü için tek histogram metriği kullanacaktır (ayrı metrikler yerine label tabanlı)
2. THE Telemetri_Modülü SHALL mod bilgisini süre histogram'ının label'ı olarak eklemeyecektir
3. THE Telemetri_Modülü SHALL `phase` label'ının kardinalitesini 3 ile sınırlayacaktır (total, shadow, enforcement)
4. THE Telemetri_Modülü SHALL `mode` label'ının kardinalitesini 4 ile sınırlayacaktır (off, shadow, enforce_soft, enforce_hard)
