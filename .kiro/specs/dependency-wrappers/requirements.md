# Gereksinimler Dokümanı — Dependency Wrappers

## Giriş

Dependency Wrappers, PTF Admin sistemindeki dış bağımlılık çağrılarını (veritabanı, external API, cache) standart bir sarmalayıcı katmanla koruma altına alan özelliktir. Mevcut Circuit Breaker altyapısı (`CircuitBreaker`, `CircuitBreakerRegistry`) var ve test edilmiş durumdadır ancak hiçbir istek yoluna bağlı değildir — middleware'deki CB pre-check yorum satırındadır ve handler seviyesinde wrapper kullanımı yoktur. Bu özellik, CB'yi gerçek istek yoluna bağlar, endpoint→dependency eşlemesi oluşturur, timeout/retry politikalarını standardize eder ve middleware fail-open yoluna metrik ekler.

Mevcut ~655 testi (backend ~424 + monitoring ~171 + fault injection ~60) bozmadan, `ptf_admin_` metrik namespace'ini kullanarak, iki seviyeli koruma (middleware pre-check + handler-level wrapper) sağlar.

## Sözlük

- **Dependency_Wrapper**: Dış bağımlılık çağrılarını saran, timeout/retry/CB entegrasyonu sağlayan sınıf
- **Dependency**: Sabit enum (`db_primary`, `db_replica`, `cache`, `external_api`, `import_worker`) — HD-5 cardinality kısıtı
- **Circuit_Breaker**: Mevcut per-dependency 3-state FSM (CLOSED→OPEN→HALF_OPEN→CLOSED); `backend/app/guards/circuit_breaker.py`
- **CircuitBreakerRegistry**: Mevcut per-dependency CB instance yöneticisi
- **Endpoint_Dependency_Map**: Hangi endpoint şablonunun hangi Dependency değerlerine bağlı olduğunu gösteren statik eşleme
- **OpsGuard_Middleware**: Mevcut guard karar zinciri middleware'i; HD-2 sırası: KillSwitch → RateLimiter → CircuitBreaker → Handler
- **CB_Pre_Check**: Middleware seviyesinde, endpoint'in bağımlılıklarının CB durumunu kontrol eden ön denetim
- **Failure_Taxonomy**: Hangi exception türlerinin CB failure olarak sayılacağını, hangilerinin sayılmayacağını tanımlayan sınıflandırma
- **Guard_Config**: Mevcut merkezi yapılandırma nesnesi (`backend/app/guard_config.py`)
- **PTFMetrics**: Mevcut `ptf_admin_` namespace'li Prometheus metrik sınıfı
- **Retry_Policy**: Hangi hatalarda retry yapılacağını, kaç kez ve hangi backoff stratejisiyle yapılacağını tanımlayan politika
- **Fail_Open**: Guard/middleware iç hatası durumunda isteği engellemeyip geçirme davranışı

## Gereksinimler

### Gereksinim 1: Endpoint→Dependency Eşlemesi

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, hangi endpoint'in hangi bağımlılığa bağlı olduğunu bilmek istiyorum, böylece CB pre-check ve operasyonel görünürlük sağlanabilsin.

#### Kabul Kriterleri

1. THE Endpoint_Dependency_Map SHALL her endpoint şablonunu bir veya daha fazla Dependency enum değerine eşlemek
2. THE Endpoint_Dependency_Map SHALL yalnızca mevcut `Dependency` enum değerlerini (`db_primary`, `db_replica`, `cache`, `external_api`, `import_worker`) kullanmak; yeni enum değeri eklememek
3. WHEN bir endpoint şablonu eşlemede bulunmadığında, THE Endpoint_Dependency_Map SHALL boş bağımlılık listesi döndürmek (bilinmeyen endpoint'ler CB pre-check'ten muaf)
4. THE Endpoint_Dependency_Map SHALL statik yapıda olmak (kod seviyesinde tanımlı, runtime'da değişmez) ve `backend/app/guards/` dizininde yaşamak

### Gereksinim 2: Middleware CB Pre-Check Entegrasyonu

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, middleware seviyesinde CB durumunu kontrol etmek istiyorum, böylece bağımlılığı OPEN olan endpoint'lere istek gönderilmeden hızlıca 503 dönülebilsin.

#### Kabul Kriterleri

1. WHEN bir istek geldiğinde, THE OpsGuard_Middleware SHALL endpoint'in bağımlılıklarını Endpoint_Dependency_Map'ten sorgulayarak her birinin CB durumunu kontrol etmek
2. WHEN endpoint'in herhangi bir bağımlılığının Circuit_Breaker'ı OPEN durumundayken, THE OpsGuard_Middleware SHALL isteği HTTP 503 ve `CIRCUIT_OPEN` deny reason ile reddetmek
3. WHEN endpoint'in tüm bağımlılıklarının Circuit_Breaker'ları CLOSED veya HALF_OPEN durumundayken, THE OpsGuard_Middleware SHALL isteği handler'a iletmek
4. WHEN CB pre-check sırasında iç hata oluştuğunda, THE OpsGuard_Middleware SHALL fail-open davranışı sergilemek (isteği handler'a iletmek) ve hata loglamak
5. THE OpsGuard_Middleware SHALL CB pre-check'i HD-2 guard zinciri sırasına uygun olarak KillSwitch ve RateLimiter kontrollerinden sonra gerçekleştirmek

### Gereksinim 3: Dependency Wrapper Sınıfları

**Kullanıcı Hikayesi:** Bir geliştirici olarak, dış bağımlılık çağrılarını standart bir wrapper ile sarmak istiyorum, böylece timeout, retry ve CB entegrasyonu her çağrı noktasında tutarlı olsun.

#### Kabul Kriterleri

1. THE Dependency_Wrapper SHALL her bağımlılık türü için (`db_primary`, `db_replica`, `cache`, `external_api`) bir wrapper sınıfı sağlamak
2. WHEN bir wrapper üzerinden çağrı yapıldığında, THE Dependency_Wrapper SHALL önce ilgili Circuit_Breaker'ın `allow_request()` metodunu kontrol etmek; OPEN durumunda çağrıyı yapmadan hata döndürmek
3. WHEN bir wrapper çağrısı başarılı olduğunda, THE Dependency_Wrapper SHALL ilgili Circuit_Breaker'ın `record_success()` metodunu çağırmak
4. WHEN bir wrapper çağrısı Failure_Taxonomy'de tanımlı bir hatayla başarısız olduğunda, THE Dependency_Wrapper SHALL ilgili Circuit_Breaker'ın `record_failure()` metodunu çağırmak
5. THE Dependency_Wrapper SHALL her çağrı için yapılandırılabilir timeout uygulamak (varsayılan: DB 5s, external API 10s, cache 2s)
6. THE Dependency_Wrapper SHALL `ptf_admin_dependency_call_total{dependency, outcome}` sayacını her çağrı sonucunda artırmak (outcome: `success`, `failure`, `timeout`, `circuit_open`)
7. THE Dependency_Wrapper SHALL `ptf_admin_dependency_call_duration_seconds{dependency}` histogram metriğini her çağrı süresinde güncellemek

### Gereksinim 4: Failure Taxonomy

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, hangi hataların CB failure olarak sayılacağını net bir şekilde tanımlamak istiyorum, böylece CB yanlış pozitif tetiklenmelerden korunabilsin.

#### Kabul Kriterleri

1. THE Failure_Taxonomy SHALL `TimeoutError`, `ConnectionError`, `ConnectionRefusedError` ve HTTP 5xx yanıtlarını CB failure olarak sınıflandırmak
2. THE Failure_Taxonomy SHALL HTTP 429 (rate-limited) yanıtlarını CB failure olarak SAYMAMAK (mevcut tasarım kararı)
3. THE Failure_Taxonomy SHALL HTTP 4xx yanıtlarını (429 hariç) CB failure olarak SAYMAMAK (istemci hataları bağımlılık hatası değildir)
4. THE Failure_Taxonomy SHALL `ValueError`, `ValidationError` gibi uygulama seviyesi hataları CB failure olarak SAYMAMAK
5. THE Failure_Taxonomy SHALL sınıflandırma kurallarını tek bir fonksiyon veya sınıfta merkezileştirmek; her wrapper'ın kendi sınıflandırması olmamalı

### Gereksinim 5: Retry Politikası

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, geçici hatalarda otomatik retry yapılmasını istiyorum, böylece kısa süreli kesintilerde kullanıcı deneyimi korunabilsin.

#### Kabul Kriterleri

1. THE Retry_Policy SHALL yalnızca Failure_Taxonomy'de CB failure olarak sınıflandırılan hatalarda retry yapmak
2. THE Retry_Policy SHALL maksimum retry sayısını yapılandırılabilir yapmak (varsayılan: 2 retry, toplam 3 deneme)
3. THE Retry_Policy SHALL exponential backoff + jitter stratejisi uygulamak (base: 0.5s, factor: 2x; yani 0.5s, 1.0s + jitter)
4. WHEN Circuit_Breaker OPEN durumundayken, THE Retry_Policy SHALL retry YAPMAMAK (CB açıkken retry anlamsızdır)
5. THE Retry_Policy SHALL retry denemelerini `ptf_admin_dependency_retry_total{dependency}` sayacıyla izlemek
6. WHEN çağrı write/transaction operasyonu olduğunda (is_write=True) ve `wrapper_retry_on_write=False` (varsayılan) olduğunda, THE Retry_Policy SHALL retry YAPMAMAK (double-write riski — DW-1)

### Gereksinim 6: Middleware Fail-Open Metriği

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, middleware catch-all'da fail-open gerçekleştiğinde metrik görmek istiyorum, böylece guard katmanındaki sessiz hataları izleyebilir ve alert oluşturabilirim.

#### Kabul Kriterleri

1. WHEN OpsGuard_Middleware catch-all bloğunda exception yakalandığında, THE PTFMetrics SHALL `ptf_admin_guard_failopen_total` sayacını artırmak
2. WHEN fail-open gerçekleştiğinde, THE OpsGuard_Middleware SHALL mevcut log mesajına ek olarak metrik artışı yapmak (log zaten var, metrik eksik)

### Gereksinim 7: Guard Config Genişletmesi

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, wrapper timeout ve retry ayarlarını merkezi yapılandırmadan yönetmek istiyorum, böylece deploy olmadan ayar değişikliği yapılabilsin.

#### Kabul Kriterleri

1. THE Guard_Config SHALL dependency wrapper timeout değerlerini ortam değişkenleri ile yapılandırılabilir yapmak (`OPS_GUARD_WRAPPER_TIMEOUT_DB`, `OPS_GUARD_WRAPPER_TIMEOUT_EXTERNAL_API`, `OPS_GUARD_WRAPPER_TIMEOUT_CACHE`)
2. THE Guard_Config SHALL retry politikası ayarlarını ortam değişkenleri ile yapılandırılabilir yapmak (`OPS_GUARD_WRAPPER_MAX_RETRIES`, `OPS_GUARD_WRAPPER_RETRY_BASE_DELAY`)
3. IF yeni yapılandırma değerleri geçersizse, THEN THE Guard_Config SHALL güvenli varsayılan değerlerle çalışmaya devam etmek ve uyarı logu yazmak (mevcut HD-4 davranışı)

### Gereksinim 8: Mevcut Sistem Uyumluluğu

**Kullanıcı Hikayesi:** Bir geliştirici olarak, dependency wrapper altyapısının mevcut sistemi bozmamasını istiyorum, böylece güvenle entegre edebilir ve deploy edebilirim.

#### Kabul Kriterleri

1. THE Dependency_Wrapper SHALL mevcut ~655 testi kırmamak
2. THE Dependency_Wrapper SHALL mevcut `ptf_admin_` metrik namespace'ini kullanmak; yeni namespace oluşturmamak
3. THE Dependency_Wrapper SHALL mevcut ops-guard tasarım kararlarına (HD-1 ile HD-7) uymak
4. THE Dependency_Wrapper SHALL mevcut guard zinciri sırasını (HD-2: KillSwitch → RateLimiter → CircuitBreaker → Handler) değiştirmemek
5. THE Dependency_Wrapper SHALL mevcut `CircuitBreaker` ve `CircuitBreakerRegistry` sınıflarını değiştirmeden kullanmak; yeni CB implementasyonu oluşturmamak
