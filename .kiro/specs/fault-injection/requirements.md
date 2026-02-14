# Gereksinimler Dokümanı — Fault Injection

## Giriş

Fault Injection, mevcut Ops-Guard koruma katmanının (kill-switch, rate limiter, circuit breaker) gerçek hata koşulları altında doğru çalıştığını kanıtlamak için tasarlanmış bir kaos/hata enjeksiyon test altyapısıdır. Mevcut guard zinciri unit test ve mock'larla test edilmiştir ancak gerçek timeout, 5xx ve iç hata koşullarında doğrulanmamıştır. Bu özellik, test-only kontrol yüzeyi ve entegrasyon testleri aracılığıyla guard davranışlarını gerçek hata senaryolarında doğrular.

Mevcut 1429 testi (1258 backend + 171 monitoring) bozmadan, `ptf_admin_` metrik namespace'ini kullanarak, yalnızca test kodunda yaşayan enjeksiyon mekanizmaları ekler.

## Sözlük

- **Fault_Injector**: Test-only singleton; hata enjeksiyon noktalarını etkinleştiren/devre dışı bırakan kontrol nesnesi
- **Injection_Point**: Hata enjekte edilebilecek noktaları tanımlayan enum (DB_TIMEOUT, EXTERNAL_5XX_BURST, KILLSWITCH_TOGGLE, RATE_LIMIT_SPIKE, GUARD_INTERNAL_ERROR)
- **TTL (Time-To-Live)**: Enjeksiyonun otomatik sona erme süresi (saniye cinsinden)
- **Stub_Server**: Test içinde çalışan in-process HTTP sunucusu; downstream API simülasyonu için kullanılır
- **Circuit_Breaker**: Downstream bağımlılık hata oranı eşiği aşıldığında istekleri reddeden koruma deseni (mevcut `backend/app/guards/circuit_breaker.py`)
- **Kill_Switch**: Belirli işlevleri anında devre dışı bırakan kontrol mekanizması (mevcut `backend/app/kill_switch.py`)
- **Rate_Limiter**: Endpoint bazlı istek hızı sınırlayıcı (mevcut `backend/app/guards/rate_limit_guard.py`)
- **OpsGuard_Middleware**: Guard karar zincirini uygulayan middleware (mevcut `backend/app/ops_guard_middleware.py`)
- **Guard_Config**: Kill-switch, rate limit ve circuit breaker ayarlarını tutan yapılandırma nesnesi (mevcut `backend/app/guard_config.py`)
- **PTFMetrics**: `ptf_admin_` namespace'li Prometheus metrik sınıfı (mevcut `backend/app/ptf_metrics.py`)
- **PromQL**: Prometheus sorgu dili; alert kurallarının ifade edildiği dil
- **Downstream_Simulation**: Gerçek çağrı yolunda (mock değil) hata üreten simülasyon mekanizması

## Gereksinimler

### Gereksinim 1: Hata Enjeksiyon Altyapısı

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, test ortamında kontrollü hata enjeksiyonu yapabilmek istiyorum, böylece guard bileşenlerini gerçek hata koşullarında doğrulayabileyim.

#### Kabul Kriterleri

1. THE Fault_Injector SHALL `InjectionPoint` enum'u ile beş enjeksiyon noktası tanımlamak: DB_TIMEOUT, EXTERNAL_5XX_BURST, KILLSWITCH_TOGGLE, RATE_LIMIT_SPIKE, GUARD_INTERNAL_ERROR
2. WHEN bir enjeksiyon noktası `enable(point, params, ttl_seconds)` ile etkinleştirildiğinde, THE Fault_Injector SHALL enjeksiyonu aktif olarak işaretlemek ve parametreleri saklamak
3. WHEN bir enjeksiyon noktası `disable(point)` ile devre dışı bırakıldığında, THE Fault_Injector SHALL enjeksiyonu pasif olarak işaretlemek
4. WHEN TTL süresi dolduğunda, THE Fault_Injector SHALL enjeksiyonu otomatik olarak devre dışı bırakmak
5. WHEN `is_enabled(point)` sorgulandığında, THE Fault_Injector SHALL TTL kontrolü yaparak enjeksiyonun aktif olup olmadığını doğru döndürmek
6. THE Fault_Injector SHALL singleton pattern ile çalışmak ve yalnızca test kodundan erişilebilir olmak; production endpoint gerektirmemek

### Gereksinim 2: Downstream Simülasyon Altyapısı

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, gerçek çağrı yolunda (mock değil) hata üreten simülasyon mekanizmaları istiyorum, böylece guard'ların gerçek exception/timeout davranışlarıyla test edilmesini sağlayabileyim.

#### Kabul Kriterleri

1. THE Stub_Server SHALL in-process HTTP sunucusu olarak çalışmak ve normal durumda HTTP 200 yanıtı döndürmek
2. WHEN EXTERNAL_5XX_BURST enjeksiyonu aktifken, THE Stub_Server SHALL yapılandırılmış oranda (varsayılan %100) HTTP 500 yanıtı döndürmek
3. WHEN EXTERNAL_5XX_BURST enjeksiyonu belirli bir istek sayısı sonra devre dışı bırakıldığında, THE Stub_Server SHALL kalan isteklere HTTP 200 döndürmek (kurtarma simülasyonu)
4. WHEN DB_TIMEOUT enjeksiyonu aktifken, THE Downstream_Simulation SHALL DB istemci yolunda `TimeoutError` fırlatmak veya yapılandırılmış süre kadar gecikme eklemek
5. THE Downstream_Simulation SHALL mock kullanmamak; gerçek çağrı yolunda gerçek exception/timeout davranışı üretmek

### Gereksinim 3: DB Timeout Enjeksiyonu ile Circuit Breaker Doğrulaması (S1)

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, DB timeout enjeksiyonu ile circuit breaker'ın CLOSED→OPEN geçişini doğrulamak istiyorum, böylece gerçek timeout koşullarında CB'nin doğru çalıştığından emin olabileyim.

#### Kabul Kriterleri

1. WHEN DB_TIMEOUT enjeksiyonu aktifken ve `cb_min_samples` (varsayılan 10) kadar istek gönderildiğinde, THE Circuit_Breaker SHALL `db_primary` bağımlılığı için OPEN durumuna geçmek
2. WHEN Circuit_Breaker OPEN durumundayken, THE OpsGuard_Middleware SHALL sonraki isteklere HTTP 503 ve `CIRCUIT_OPEN` deny reason ile yanıt vermek
3. WHEN DB_TIMEOUT enjeksiyonu aktifken, THE PTFMetrics SHALL `ptf_admin_circuit_breaker_state{dependency="db_primary"}` gauge değerini 2 (OPEN) olarak güncellemek
4. WHEN Circuit_Breaker OPEN durumundayken ve `cb_open_duration_seconds` süresi dolduktan sonra, THE Circuit_Breaker SHALL HALF_OPEN durumuna geçmek

### Gereksinim 4: External API 5xx Burst ile Circuit Breaker Yaşam Döngüsü Doğrulaması (S2)

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, external API 5xx burst enjeksiyonu ile circuit breaker'ın tam yaşam döngüsünü (CLOSED→OPEN→HALF_OPEN→CLOSED) doğrulamak istiyorum, böylece kurtarma mekanizmasının çalıştığından emin olabileyim.

#### Kabul Kriterleri

1. WHEN EXTERNAL_5XX_BURST enjeksiyonu aktifken ve yeterli hata biriktiğinde, THE Circuit_Breaker SHALL `external_api` bağımlılığı için OPEN durumuna geçmek
2. WHEN Circuit_Breaker OPEN durumundayken ve `cb_open_duration_seconds` süresi dolduktan sonra, THE Circuit_Breaker SHALL HALF_OPEN durumuna geçmek ve sınırlı sayıda probe isteği kabul etmek
3. WHEN HALF_OPEN durumunda probe istekleri başarılı olduğunda (stub server 200 döndürdüğünde), THE Circuit_Breaker SHALL CLOSED durumuna dönmek
4. WHEN Circuit_Breaker tam yaşam döngüsünü tamamladığında, THE PTFMetrics SHALL her durum geçişinde `ptf_admin_circuit_breaker_state{dependency="external_api"}` gauge değerini doğru güncellemek (0→2→1→0)

### Gereksinim 5: KillSwitch Runtime Toggle Doğrulaması (S3)

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, kill-switch'in runtime'da toggle edilmesinin doğru çalıştığını entegrasyon testleriyle doğrulamak istiyorum, böylece acil müdahale mekanizmasının güvenilir olduğundan emin olabileyim.

#### Kabul Kriterleri

1. WHEN kill-switch `global_import` etkinleştirildiğinde, THE OpsGuard_Middleware SHALL korunan endpoint'lere HTTP 503 ve `KILL_SWITCHED` deny reason ile yanıt vermek
2. WHEN kill-switch etkinleştirildiğinde, THE PTFMetrics SHALL `ptf_admin_killswitch_state{switch_name="global_import"}` gauge değerini 1 (aktif) olarak güncellemek
3. WHEN kill-switch devre dışı bırakıldığında, THE OpsGuard_Middleware SHALL normal istek akışını geri yüklemek
4. WHEN kill-switch devre dışı bırakıldığında, THE PTFMetrics SHALL `ptf_admin_killswitch_state{switch_name="global_import"}` gauge değerini 0 (pasif) olarak güncellemek

### Gereksinim 6: Rate Limit Spike Doğrulaması (S4)

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, rate limiter'ın sürekli yük altında deterministik ve doğru çalıştığını doğrulamak istiyorum, böylece rate limit mekanizmasının güvenilir olduğundan emin olabileyim.

#### Kabul Kriterleri

1. WHEN rate limit eşiği (test config: 5 istek/pencere) aşıldığında, THE Rate_Limiter SHALL fazla istekleri HTTP 429 ile reddetmek ve `Retry-After` header'ı eklemek
2. WHEN rate limit penceresi boyunca sürekli istek gönderildiğinde, THE Rate_Limiter SHALL deterministik davranmak; aynı koşullarda aynı sonucu üretmek (flakiness olmamalı)
3. WHEN rate limit istekleri reddedildiğinde, THE PTFMetrics SHALL `ptf_admin_rate_limit_total{endpoint, decision="rejected"}` sayacını doğru artırmak
4. WHEN rate limit penceresi sıfırlandığında, THE Rate_Limiter SHALL yeni isteklere izin vermek

### Gereksinim 7: Guard Internal Error Enjeksiyonu ile Fail-Open Doğrulaması (S5)

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, guard katmanında iç hata oluştuğunda fail-open davranışının doğru çalıştığını doğrulamak istiyorum, böylece guard hatalarının sistemi durdurmadığından emin olabileyim.

#### Kabul Kriterleri

1. WHEN GUARD_INTERNAL_ERROR enjeksiyonu aktifken ve middleware karar zincirinde exception fırlatıldığında, THE OpsGuard_Middleware SHALL fail-open davranışı sergilemek (istek handler'a ulaşmalı)
2. WHEN guard iç hatası oluştuğunda, THE PTFMetrics SHALL `ptf_admin_killswitch_error_total` veya `ptf_admin_killswitch_fallback_open_total` sayacını artırmak
3. WHEN GUARD_INTERNAL_ERROR enjeksiyonu devre dışı bırakıldığında, THE OpsGuard_Middleware SHALL normal guard karar zincirini geri yüklemek

### Gereksinim 8: Alert Metrik Doğrulaması

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, fault injection senaryolarının ürettiği metriklerin mevcut alert PromQL ifadelerini karşıladığını doğrulamak istiyorum, böylece alert'lerin gerçek hata koşullarında tetikleneceğinden emin olabileyim.

#### Kabul Kriterleri

1. WHEN S1 (DB timeout → CB open) senaryosu tamamlandığında, THE Alert_Validator SHALL `PTFAdminCircuitBreakerOpen` alert PromQL ifadesinin karşılandığını doğrulamak
2. WHEN S4 (rate limit spike) senaryosu tamamlandığında, THE Alert_Validator SHALL `PTFAdminRateLimitSpike` alert PromQL ifadesinin karşılanabilir metrik ürettiğini doğrulamak
3. WHEN S5 (guard internal error) senaryosu tamamlandığında, THE Alert_Validator SHALL `PTFAdminGuardInternalError` alert PromQL ifadesinin karşılandığını doğrulamak
4. THE Alert_Validator SHALL PromQL ifadelerini deterministik ve CI-safe şekilde değerlendirmek; gerçek Prometheus sunucusu gerektirmemek

### Gereksinim 9: Mevcut Sistem Uyumluluğu

**Kullanıcı Hikayesi:** Bir geliştirici olarak, fault injection altyapısının mevcut sistemi bozmamasını istiyorum, böylece güvenle test edebilir ve deploy edebilirim.

#### Kabul Kriterleri

1. THE Fault_Injector SHALL yalnızca `backend/app/testing/` dizininde yaşamak; production kod yollarını değiştirmemek
2. THE Fault_Injector SHALL mevcut 1429 testi (1258 backend + 171 monitoring) kırmamak
3. THE Fault_Injector SHALL mevcut `ptf_admin_` metrik namespace'ini kullanmak; yeni namespace oluşturmamak
4. THE Fault_Injector SHALL mevcut ops-guard tasarım kararlarına (HD-1 ile HD-7) uymak
5. WHEN fault injection testleri çalıştırıldığında, THE Test_Suite SHALL her senaryo için 5-15 dakika içinde tamamlanmak
