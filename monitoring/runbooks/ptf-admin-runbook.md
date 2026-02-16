# PTF Admin Runbook

Bu runbook, PTF Admin Prometheus alert'leri tetiklendiğinde izlenecek troubleshooting adımlarını içerir.

---

## PTFAdminMetricsAbsent

**Severity:** critical
**PromQL:** `absent(ptf_admin_api_request_total)`

### Olası Nedenler
1. Pod crash veya OOM kill — container restart döngüsünde olabilir
2. Deployment hatası — yeni versiyon başarısız rollout, pod schedule edilememiş
3. Prometheus scrape config değişikliği — ServiceMonitor silinmiş veya label selector uyumsuz
4. Network policy — pod ile Prometheus arasında ağ erişimi engellenmiş

### İlk 3 Kontrol
1. `kubectl get pods -l app=ptf-admin` — pod durumunu kontrol et (Running/CrashLoopBackOff/Pending)
2. `kubectl logs -l app=ptf-admin --tail=50` — son logları incele (OOM, startup failure)
3. Prometheus Targets UI — `ptf-admin` target'ının durumunu kontrol et (UP/DOWN/missing)

### Müdahale Adımları
1. Pod CrashLoopBackOff ise: `kubectl describe pod <pod>` ile event'leri incele, OOM ise resource limit artır
2. Deployment hatası ise: `kubectl rollout undo deployment/ptf-admin` ile önceki versiyona dön
3. ServiceMonitor eksik ise: `kubectl get servicemonitor -l app=ptf-admin` ile kontrol et, gerekirse yeniden uygula

---

## PTFAdminTargetDown

**Severity:** critical
**PromQL:** `up{job="ptf-admin"} == 0`

### Olası Nedenler
1. Service endpoint değişikliği — pod IP değişmiş, service selector uyumsuz
2. Network policy veya firewall — Prometheus'un pod'a erişimi engellenmiş
3. Prometheus config hatası — scrape interval veya timeout yanlış ayarlanmış
4. /metrics endpoint hatası — uygulama başlamış ama metrics endpoint yanıt vermiyor

### İlk 3 Kontrol
1. `kubectl get endpoints ptf-admin` — endpoint listesinde pod IP'leri var mı kontrol et
2. `kubectl port-forward svc/ptf-admin 8000:8000` sonra `curl localhost:8000/metrics` — endpoint erişilebilir mi test et
3. Prometheus Targets UI — target'ın son scrape hatasını oku

### Müdahale Adımları
1. Endpoint boş ise: service selector'ı ve pod label'larını karşılaştır, düzelt
2. Network erişim sorunu ise: NetworkPolicy'leri incele, Prometheus namespace'inden erişime izin ver
3. /metrics yanıt vermiyorsa: uygulama loglarını incele, gerekirse pod'u restart et

---

## PTFAdmin5xxSpike

**Severity:** warning
**PromQL:** `sum(rate(ptf_admin_api_request_total{status_class="5xx"}[5m])) / sum(rate(ptf_admin_api_request_total[5m])) > 0.05`

### Olası Nedenler
1. Unhandled exception — yeni deploy'da yakalanmamış hata
2. DB connection pool exhaustion — bağlantı havuzu tükenmiş, timeout'lar 500 dönüyor
3. Upstream service timeout — bağımlı servis yanıt vermiyor
4. Resource contention — CPU/memory limitlerine yaklaşılmış

### İlk 3 Kontrol
1. `kubectl logs -l app=ptf-admin --tail=100 | grep -i "error\|500\|traceback"` — 5xx loglarını filtrele
2. DB connection pool metrikleri — aktif/idle bağlantı sayısını kontrol et
3. `kubectl rollout history deployment/ptf-admin` — son deploy zamanını kontrol et (korelasyon)

### Müdahale Adımları
1. Yeni deploy kaynaklı ise: `kubectl rollout undo deployment/ptf-admin` ile rollback
2. DB connection pool ise: pool size artır veya idle timeout ayarla
3. Upstream timeout ise: circuit breaker / retry config'i gözden geçir

---

## PTFAdminExceptionPath

**Severity:** critical
**PromQL:** `sum(rate(ptf_admin_api_request_total{status_class="0xx"}[5m])) > 0`

### Olası Nedenler
1. Uncaught exception in middleware — middleware zincirinde yakalanmamış hata
2. Dependency crash — kritik bağımlılık (DB, cache) tamamen erişilemez
3. Memory corruption — OOM yaklaşımı, garbage collection sorunları
4. Framework bug — FastAPI/Starlette seviyesinde beklenmeyen davranış

### İlk 3 Kontrol
1. `kubectl logs -l app=ptf-admin --tail=100 | grep -i "traceback\|exception\|unhandled"` — exception stack trace'leri bul
2. Middleware chain'i incele — hangi middleware'de exception fırladığını belirle
3. Dependency health check — DB, cache, upstream servislerin durumunu kontrol et

### Müdahale Adımları
1. Bilinen exception ise: hotfix deploy et veya rollback yap
2. Dependency kaynaklı ise: bağımlı servisi restart et / failover'a geç
3. Tekrarlayan ise: pod'u restart et (`kubectl delete pod <pod>`) ve logları topla

---

## PTFAdminHighLatency

**Severity:** warning
**PromQL:** `histogram_quantile(0.95, sum(rate(ptf_admin_api_request_duration_seconds_bucket[5m])) by (le, endpoint)) > 2`

### Olası Nedenler
1. DB slow queries — eksik index, tablo lock, büyük result set
2. Connection pool exhaustion — tüm bağlantılar meşgul, yeni istekler kuyrukta bekliyor
3. Resource contention — CPU throttling veya memory pressure
4. Upstream latency — bağımlı servis yavaşlamış

### İlk 3 Kontrol
1. DB slow query log — son 15 dakikadaki yavaş sorguları incele
2. Connection pool metrikleri — active/idle/waiting bağlantı sayıları
3. `kubectl top pod -l app=ptf-admin` — pod CPU/memory kullanımını kontrol et

### Müdahale Adımları
1. Slow query ise: eksik index ekle veya sorguyu optimize et
2. Connection pool ise: pool size artır, idle timeout kısalt
3. Resource ise: pod resource limit'lerini artır veya HPA threshold'unu düşür

---

## PTFAdminTelemetryLatency

**Severity:** warning
**PromQL:** `histogram_quantile(0.95, sum(rate(ptf_admin_api_request_duration_seconds_bucket{endpoint="/admin/telemetry/events"}[5m])) by (le)) > 0.5`

### Olası Nedenler
1. Event ingestion bottleneck — yüksek event hacmi, işlem süresi artmış
2. Rate limiter overhead — çok sayıda rate limit kontrolü
3. Validation overhead — büyük batch'lerde event validation süresi
4. GC pressure — yüksek memory allocation rate

### İlk 3 Kontrol
1. Telemetry endpoint logları — ortalama batch size ve işlem süresini kontrol et
2. Rate limiter state — aktif rate limit sayısını ve 429 oranını incele
3. `kubectl top pod -l app=ptf-admin` — CPU kullanımını kontrol et

### Müdahale Adımları
1. Yüksek hacim ise: batch size limitini düşür (mevcut: 100)
2. Rate limiter ise: rate limit threshold'unu ayarla
3. Geçici ise: telemetry endpoint'i disable et (feature flag ile)

---

## PTFAdminImportLatency

**Severity:** warning
**PromQL:** `histogram_quantile(0.95, sum(rate(ptf_admin_import_apply_duration_seconds_bucket[5m])) by (le)) > 10`

### Olası Nedenler
1. Large batch size — çok büyük import dosyası, tek transaction'da çok satır
2. DB lock contention — concurrent import'lar birbirini bekliyor
3. Disk I/O bottleneck — yoğun yazma operasyonları disk'i doyurmuş
4. Index rebuild overhead — çok sayıda insert sonrası index güncelleme maliyeti

### İlk 3 Kontrol
1. Import batch size'ları — son import'ların satır sayılarını kontrol et
2. DB lock waits — `pg_stat_activity` ile bekleyen sorguları incele
3. Disk metrikleri — IOPS ve latency değerlerini kontrol et

### Müdahale Adımları
1. Büyük batch ise: import'u daha küçük parçalara böl
2. Lock contention ise: concurrent import sayısını sınırla
3. Disk I/O ise: storage class'ı gözden geçir, gerekirse scale up

---

## PTFAdminTelemetryAbuse

**Severity:** warning
**PromQL:** `sum(rate(ptf_admin_api_request_total{endpoint="/admin/telemetry/events",status_class="4xx"}[5m])) * 60 > 10`

### Olası Nedenler
1. Bot/scraper traffic — otomatik araçlar telemetry endpoint'ine istek gönderiyor
2. Misconfigured frontend — frontend hatalı event gönderiyor (validation fail)
3. Rate limit trigger — meşru trafik rate limit'e takılıyor
4. Brute force/fuzzing — güvenlik taraması veya saldırı denemesi

### İlk 3 Kontrol
1. Access logları — yüksek 4xx üreten IP adreslerini belirle
2. Rate limiter 429 sayısı — rate limit mi yoksa validation hatası mı ayırt et
3. Son frontend deploy — frontend'de telemetry config değişikliği var mı kontrol et

### Müdahale Adımları
1. Tek IP kaynaklı ise: IP bazlı block uygula (WAF/ingress)
2. Frontend hatası ise: frontend hotfix deploy et
3. Rate limit yetersiz ise: threshold'u düşür (mevcut: 60 rpm/IP)

---

## PTFAdminImportRejectRatio

**Severity:** warning
**PromQL:** `sum(rate(ptf_admin_import_rows_total{outcome="rejected"}[15m])) / sum(rate(ptf_admin_import_rows_total[15m])) > 0.2`

### Olası Nedenler
1. Veri format değişikliği — upstream veri kaynağı format değiştirmiş
2. Upstream data quality — kaynak veride kalite düşüşü (eksik alanlar, geçersiz değerler)
3. Validation rule change — yeni deploy'da validation kuralları sıkılaştırılmış
4. Encoding sorunu — karakter encoding uyumsuzluğu

### İlk 3 Kontrol
1. Rejected row örnekleri — son reject edilen satırların hata nedenlerini incele
2. Import source analizi — hangi kaynaktan gelen veriler reject ediliyor
3. Son validation değişiklikleri — `git log` ile validation kurallarındaki değişiklikleri kontrol et

### Müdahale Adımları
1. Format değişikliği ise: parser'ı güncelle veya upstream ile iletişime geç
2. Geçici kalite düşüşü ise: reject threshold'unu geçici olarak artır, monitoring'i sıklaştır
3. Validation rule change ise: rollback veya rule'u gevşet (bilinçli karar ile)

---

## PTFAdminKillSwitchActivated

**Severity:** critical
**PromQL:** `max(ptf_admin_killswitch_state) == 1`

### Olası Nedenler
1. Bilinçli operatör müdahalesi — planlı bakım veya incident response sırasında kill-switch açılmış
2. Otomatik tetikleme — monitoring veya orchestration aracı tarafından otomatik olarak aktive edilmiş
3. Beklenmeyen toggle — config değişikliği veya deploy sırasında yanlışlıkla aktive olmuş
4. Güvenlik olayı — abuse tespit edilmiş ve acil engelleme uygulanmış

### İlk 3 Kontrol
1. `GET /admin/ops/kill-switches` — hangi switch'lerin aktif olduğunu kontrol et
2. Audit logları — `[KILLSWITCH]` log entry'lerini incele, kim/ne zaman açtığını belirle
3. Son deploy/config değişikliği — `kubectl rollout history` ve config map değişikliklerini kontrol et

### Müdahale Adımları
1. Planlı ise: işlem tamamlandığında `PUT /admin/ops/kill-switches/{name}` ile deaktive et
2. Yanlışlıkla açılmış ise: hemen deaktive et ve root cause'u belirle
3. Güvenlik kaynaklı ise: güvenlik ekibiyle koordine et, deaktive etmeden önce onay al

---

## PTFAdminCircuitBreakerOpen

**Severity:** critical
**PromQL:** `max(ptf_admin_circuit_breaker_state) == 2`

### Olası Nedenler
1. Bağımlılık arızası — DB, cache veya external API tamamen erişilemez
2. Network partition — bağımlı servis ile ağ bağlantısı kopmuş
3. Bağımlılık overload — downstream servis aşırı yük altında, timeout'lar artmış
4. DNS çözümleme hatası — bağımlılık endpoint'i çözümlenemiyor

### İlk 3 Kontrol
1. `ptf_admin_circuit_breaker_state` gauge — hangi dependency'nin open olduğunu belirle
2. Bağımlılık health check — ilgili DB/cache/API'nin durumunu doğrudan kontrol et
3. Network connectivity — `kubectl exec` ile pod'dan bağımlılığa erişimi test et

### Müdahale Adımları
1. Bağımlılık down ise: bağımlılığı restart et veya failover'a geç
2. Network sorunu ise: NetworkPolicy ve DNS ayarlarını kontrol et
3. Overload ise: bağımlılığı scale up et, circuit breaker half-open recovery'yi bekle

---

## PTFAdminRateLimitSpike

**Severity:** warning
**PromQL:** `sum(rate(ptf_admin_rate_limit_total{decision="deny"}[5m])) * 60 > 5`

### Olası Nedenler
1. Abuse/bot traffic — otomatik araçlar veya scriptler yüksek hızda istek gönderiyor
2. Runaway client — hatalı yapılandırılmış client retry loop'ta
3. Rate limit threshold çok düşük — meşru trafik artışı threshold'u aşıyor
4. Burst traffic — kampanya veya toplu işlem sonrası ani trafik artışı

### İlk 3 Kontrol
1. `ptf_admin_rate_limit_total{decision="deny"}` by endpoint — hangi endpoint'lerin etkilendiğini belirle
2. Access logları — yüksek deny alan IP/actor'ları tespit et
3. Rate limit config — `GET /admin/ops/status` ile mevcut threshold'ları kontrol et

### Müdahale Adımları
1. Abuse ise: IP bazlı block uygula (WAF/ingress seviyesinde)
2. Meşru trafik ise: `OPS_GUARD_RATE_LIMIT_*` env var'larını artır ve redeploy et
3. Runaway client ise: client'ı tespit et ve düzeltmesini sağla

---

## PTFAdminGuardConfigInvalid

**Severity:** warning
**PromQL:** `increase(ptf_admin_guard_config_fallback_total[15m]) > 0`

### Olası Nedenler
1. Geçersiz env var — `OPS_GUARD_*` env var'larında typo veya geçersiz değer
2. ConfigMap hatası — Kubernetes ConfigMap'te yanlış format
3. Deploy sırasında config kaybı — yeni deploy'da env var eksik
4. Schema uyumsuzluğu — config schema versiyonu ile kod versiyonu uyumsuz

### İlk 3 Kontrol
1. `kubectl describe pod -l app=ptf-admin` — env var'ları kontrol et
2. Uygulama logları — `WARNING` seviyesinde guard config fallback mesajlarını ara
3. ConfigMap — `kubectl get configmap ptf-admin-config -o yaml` ile config'i doğrula

### Müdahale Adımları
1. Env var hatası ise: ConfigMap veya Secret'ı düzelt ve redeploy et
2. Schema uyumsuzluğu ise: config'i yeni schema'ya uygun güncelle
3. Acil ise: fallback defaults güvenli çalışır, ancak custom threshold'lar kaybolmuştur — düzeltmeyi planlayın

---

## PTFAdminGuardInternalError

**Severity:** critical
**PromQL:** `sum(rate(ptf_admin_killswitch_error_total[5m])) > 0 or sum(rate(ptf_admin_killswitch_fallback_open_total[5m])) > 0`

### Olası Nedenler
1. Guard katmanı internal exception — kill-switch veya rate limiter'da beklenmeyen hata
2. Memory corruption — guard state'inde tutarsızlık
3. Concurrency bug — race condition guard state erişiminde
4. Dependency injection hatası — guard bileşenleri doğru initialize edilmemiş

### İlk 3 Kontrol
1. Uygulama logları — `[KILLSWITCH]` ve `[GUARD]` error loglarını incele
2. `ptf_admin_killswitch_error_total` by error_type — hata tipini belirle
3. `ptf_admin_killswitch_fallback_open_total` — fail-open sayısını kontrol et

### Müdahale Adımları
1. Tekrarlayan hata ise: pod'u restart et (`kubectl delete pod <pod>`)
2. Kod hatası ise: hotfix deploy et veya guard middleware'i geçici olarak devre dışı bırak
3. Fail-open aktif ise: koruma devre dışı demektir — risk değerlendirmesi yap, gerekirse kill-switch ile manuel koruma uygula

---

## PTFAdminSLOBurnRateFast

**Severity:** critical
**PromQL:** `sum(rate(ptf_admin_api_request_total{status_class=~"5xx|0xx"}[1h])) / sum(rate(ptf_admin_api_request_total[1h])) > 0.01`

### Olası Nedenler
1. Major incident — birden fazla bileşen aynı anda arızalı
2. Bad deploy — yeni versiyon kritik hata içeriyor
3. Infrastructure failure — node, disk veya network seviyesinde arıza
4. Cascading failure — bir bağımlılık arızası diğerlerini tetiklemiş

### İlk 3 Kontrol
1. Diğer alert'ler — PTFAdmin5xxSpike, PTFAdminCircuitBreakerOpen, PTFAdminExceptionPath aktif mi kontrol et
2. Son deploy — `kubectl rollout history deployment/ptf-admin` ile korelasyon ara
3. Infrastructure — node durumu, disk kullanımı, network latency kontrol et

### Müdahale Adımları
1. Bad deploy ise: hemen rollback (`kubectl rollout undo deployment/ptf-admin`)
2. Infrastructure ise: etkilenen node'u drain et, pod'ları sağlıklı node'lara taşı
3. Cascading failure ise: circuit breaker'ların açık olduğunu doğrula, bağımlılıkları sırayla kurtarın

---

## PTFAdminSLOBurnRateSlow

**Severity:** warning
**PromQL:** `sum(rate(ptf_admin_api_request_total{status_class=~"5xx|0xx"}[6h])) / sum(rate(ptf_admin_api_request_total[6h])) > 0.005`

### Olası Nedenler
1. Gradual degradation — yavaş yavaş artan hata oranı, tek bir root cause belirgin değil
2. Resource leak — memory leak, connection leak zamanla birikiyor
3. Data quality issue — belirli veri pattern'leri hatalara neden oluyor
4. Intermittent dependency — bağımlılık aralıklı olarak timeout veriyor

### İlk 3 Kontrol
1. Error rate trend — son 24 saatte error rate grafiğini incele (artış trendi var mı)
2. Resource metrikleri — memory, CPU, connection pool trend'lerini kontrol et
3. Error logları — tekrarlayan hata pattern'lerini belirle

### Müdahale Adımları
1. Resource leak ise: pod'ları rolling restart et, leak'i fix eden patch planlayın
2. Data quality ise: problematik veri pattern'lerini belirle ve validation ekle
3. Intermittent dependency ise: circuit breaker threshold'larını gözden geçir, retry config'i optimize et

---

## PTFAdminGuardFailOpen

**Severity:** critical
**PromQL:** `increase(ptf_admin_guard_failopen_total[5m]) > 0`

### Olası Nedenler
1. Wrapper internal error — dependency wrapper kodu exception fırlattı
2. Middleware internal error — ops-guard middleware'de beklenmeyen hata
3. CB registry initialization failure — CircuitBreakerRegistry oluşturulamadı
4. Metrics subsystem error — PTFMetrics yazım hatası

### İlk 3 Kontrol
1. `kubectl logs -l app=ptf-admin --tail=100 | grep "fail-open\|failopen\|internal error"` — fail-open log'larını bul
2. Grafana → Guard Fail-Open panel — artış zamanlamasını deploy ile karşılaştır
3. `ptf_admin_guard_failopen_total` counter değerini kontrol et — tek seferlik mi sürekli mi

### Müdahale Adımları
1. Deploy kaynaklı ise: rollback yap, wrapper kodunu incele
2. Sürekli artıyorsa: guard katmanında bug var, acil fix gerekli
3. Tek seferlik ise: log'dan root cause belirle, monitoring'e devam et

---

## PTFAdminDependencyMapMiss

**Severity:** warning
**PromQL:** `increase(ptf_admin_dependency_map_miss_total[10m]) > 0`

### Olası Nedenler
1. Yeni endpoint eklendi ama dependency map güncellenmedi (wiring kaçırma)
2. Endpoint path normalization hatası — template ile gerçek path uyuşmuyor
3. Koşullu dependency endpoint'i map'e eklendi (DW-2 ihlali)

### İlk 3 Kontrol
1. Son deploy'da yeni endpoint eklenmiş mi kontrol et
2. `endpoint_dependency_map.py` dosyasını incele — eksik mapping var mı
3. Middleware log'larında hangi endpoint'in miss verdiğini bul

### Müdahale Adımları
1. Eksik mapping varsa: `endpoint_dependency_map.py`'ye ekle ve deploy et
2. Path normalization sorunu ise: middleware'deki template extraction'ı düzelt
3. Koşullu dependency ise: map'e ekleme, DW-2 kuralı gereği pre-check atlanmalı

---

## PTFAdminDependencyTimeoutRate

**Severity:** warning
**PromQL:** `sum by (dependency)(rate(ptf_admin_dependency_call_total{outcome="timeout"}[5m])) / sum by (dependency)(rate(ptf_admin_dependency_call_total[5m])) > 0.02`

### Olası Nedenler
1. Upstream latency artışı — bağımlılık yavaşladı
2. Timeout threshold çok düşük — normal operasyonda bile timeout oluyor
3. Network latency — pod ile bağımlılık arasında ağ gecikmesi
4. Connection pool exhaustion — bağımlılık bağlantı havuzu doldu

### İlk 3 Kontrol
1. Grafana → Dependency P95 Latency panel — hangi dependency yavaş
2. `wrapper_timeout_seconds_by_dependency` config'ini kontrol et
3. Upstream bağımlılığın kendi health check'ini kontrol et

### Müdahale Adımları
1. Upstream yavaşsa: upstream ekibini bilgilendir, timeout threshold'u geçici artır
2. Threshold düşükse: `OPS_GUARD_WRAPPER_TIMEOUT_SECONDS_BY_DEPENDENCY` ile artır
3. Connection pool ise: pool size artır veya idle timeout ayarla

---

## PTFAdminDependencyFailureRate

**Severity:** critical
**PromQL:** `sum by (dependency)(rate(ptf_admin_dependency_call_total{outcome="failure"}[5m])) / sum by (dependency)(rate(ptf_admin_dependency_call_total[5m])) > 0.01`

### Olası Nedenler
1. Upstream arızası — bağımlılık 5xx dönüyor veya connection refused
2. DNS resolution hatası — bağımlılık adresi çözümlenemiyor
3. TLS/SSL sertifika sorunu — sertifika süresi dolmuş
4. Bağımlılık deploy'u — upstream yeni versiyon deploy etti, uyumsuzluk

### İlk 3 Kontrol
1. Grafana → Dependency Failure Rate panel — hangi dependency etkilenmiş
2. `ptf_admin_circuit_breaker_state` gauge — CB açılmış mı
3. Upstream bağımlılığın status page'ini kontrol et

### Müdahale Adımları
1. Upstream down ise: CB otomatik koruma sağlıyor, upstream recovery bekle
2. DNS/TLS ise: infra ekibini bilgilendir
3. CB çok agresif açılıyorsa: `cb_min_samples` veya `cb_error_threshold_pct` ayarla

---

## PTFAdminDependencyClientErrorRate

**Severity:** warning
**PromQL:** `sum by (dependency)(rate(ptf_admin_dependency_call_total{outcome="client_error"}[5m])) / sum by (dependency)(rate(ptf_admin_dependency_call_total[5m])) > 0.05`

### Olası Nedenler
1. Contract drift — upstream API değişti, bizim çağrılarımız artık 4xx alıyor
2. Yanlış parametre — handler'dan gelen veri formatı değişti
3. Rate limiting (429) — upstream bizi rate limit'liyor
4. Auth token süresi dolmuş — 401/403 hataları

### İlk 3 Kontrol
1. Error log'larında 4xx status code'larını filtrele — hangi dependency, hangi status
2. Upstream API changelog'unu kontrol et — breaking change var mı
3. Auth token/API key geçerliliğini kontrol et

### Müdahale Adımları
1. Contract drift ise: client kodunu upstream'e uyumlu güncelle
2. Rate limiting ise: çağrı sıklığını azalt veya upstream ile limit artışı görüş
3. Auth sorunu ise: token/key yenile

---

## PTFAdminRetryStorm

**Severity:** warning
**PromQL:** `sum by (dependency)(rate(ptf_admin_dependency_retry_total[5m])) / sum by (dependency)(rate(ptf_admin_dependency_call_total[5m])) > 0.2`

### Olası Nedenler
1. Upstream sürekli hata dönüyor — her çağrı retry'a giriyor
2. Retry budget çok yüksek — max_retries fazla, backoff yetersiz
3. Zincirleme retry — birden fazla katman retry yapıyor (amplification)
4. Timeout + retry kombinasyonu — her timeout retry tetikliyor

### İlk 3 Kontrol
1. Grafana → Retry Storm panel — hangi dependency'de retry patlaması var
2. `ptf_admin_dependency_call_total{outcome="failure"}` ve `{outcome="timeout"}` oranlarını karşılaştır
3. `wrapper_retry_max_attempts_by_dependency` config'ini kontrol et

### Müdahale Adımları
1. Upstream down ise: CB açılmasını bekle (retry otomatik duracak)
2. Retry budget yüksekse: `OPS_GUARD_WRAPPER_RETRY_MAX_ATTEMPTS_DEFAULT` azalt
3. Backoff yetersizse: `OPS_GUARD_WRAPPER_RETRY_BACKOFF_BASE_MS` artır

---

## PTFAdminDependencyLatencyP95

**Severity:** warning
**PromQL:** `histogram_quantile(0.95, sum by (le, dependency)(rate(ptf_admin_dependency_call_duration_seconds_bucket[5m]))) > 0.8`

### Olası Nedenler
1. Upstream yavaşlama — bağımlılık response time artmış
2. Connection pool exhaustion — bağlantı havuzu dolmuş, kuyrukta bekleme
3. Network latency — pod ile bağımlılık arasında gecikme
4. Query complexity — DB'de yavaş sorgu (index eksik, full table scan)

### İlk 3 Kontrol
1. Grafana → Dependency P95 Latency panel — hangi dependency yavaş
2. Upstream bağımlılığın kendi latency metriklerini kontrol et
3. DB ise: slow query log'larını incele

### Müdahale Adımları
1. DB yavaşsa: index ekle veya query optimize et
2. External API yavaşsa: timeout threshold'u geçici artır, upstream'i bilgilendir
3. Connection pool ise: pool size artır

---

## PTFAdminDependencyCircuitOpenRate

**Severity:** critical
**PromQL:** `sum by (dependency)(rate(ptf_admin_dependency_call_total{outcome="circuit_open"}[5m])) > 0`

### Olası Nedenler
1. Upstream arızası devam ediyor — CB açık, istekler reddediliyor
2. CB threshold çok agresif — düşük trafik + birkaç hata CB'yi açtı
3. Half-open recovery başarısız — probe istekleri de başarısız oluyor
4. Timeout storm → CB açılması — timeout'lar CB failure sayılıyor

### İlk 3 Kontrol
1. `ptf_admin_circuit_breaker_state` gauge — hangi dependency'nin CB'si açık
2. Upstream bağımlılığın health check'ini kontrol et
3. CB config: `cb_error_threshold_pct`, `cb_min_samples`, `cb_open_duration_seconds`

### Müdahale Adımları
1. Upstream down ise: recovery bekle, CB otomatik half-open'a geçecek
2. CB çok agresif ise: `cb_min_samples` artır veya `cb_error_threshold_pct` yükselt
3. Acil bypass gerekiyorsa: `OPS_GUARD_CB_PRECHECK_ENABLED=false` ile pre-check kapat (wrapper-level enforcement kalır)

---

# Preflight Override Semantiği

Preflight override metrikleri üç `kind` label değeri kullanır. Bu bölüm her birinin ne anlama geldiğini ve hangi senaryoda üretildiğini açıklar.

| Kind | Anlam | Senaryo |
|------|-------|---------|
| `attempt` | Override reddedildi | BLOCK verdict + override flag'leri sağlandı → override reddedildi (BLOCK verdict override edilemez) |
| `applied` | Override kabul edildi | HOLD verdict + override flag'leri sağlandı → exit 0 ile geçirildi |
| `breach` | Sözleşme ihlali | BLOCK verdict + ABSOLUTE_BLOCK_REASONS + override girişimi → CONTRACT_BREACH kaydı |

**Önemli:** `attempt` "override denendi ama reddedildi" demektir, "override başarılı" değil. `applied` ise "override başarıyla uygulandı" anlamına gelir.

### Senaryo Örnekleri

1. **attempt senaryosu:** Geliştirici `--override-by=john` flag'i ile preflight çalıştırır. Sonuç BLOCK (örn. TIER_FAIL). Override reddedilir çünkü BLOCK verdict override edilemez. `override_total{kind="attempt"}` +1 artar.

2. **applied senaryosu:** Geliştirici `--override-by=john` flag'i ile preflight çalıştırır. Sonuç HOLD (örn. COVERAGE_LOW). Override kabul edilir, exit code 0 döner. `override_total{kind="applied"}` +1 artar.

3. **breach senaryosu:** Geliştirici `--override-by=john` flag'i ile preflight çalıştırır. Sonuç BLOCK ve neden ABSOLUTE_BLOCK_REASONS listesinde (örn. SECURITY_CRITICAL). Bu bir sözleşme ihlalidir. `override_total{kind="breach"}` +1 artar. Bu durum PreflightContractBreach alert'ini tetikler.

### PromQL Referansı

```promql
# Override dağılımı
release_preflight_override_total

# Sadece breach'ler (sözleşme ihlalleri)
increase(release_preflight_override_total{kind="breach"}[5m])

# Applied/attempt oranı
increase(release_preflight_override_total{kind="applied"}[1h])
/
(increase(release_preflight_override_total{kind="applied"}[1h]) + increase(release_preflight_override_total{kind="attempt"}[1h]))
```

---

## PreflightContractBreach

**Severity:** critical
**PromQL:** `increase(release_preflight_override_total{kind="breach"}[5m]) > 0`

### Olası Nedenler
1. Geliştirici ABSOLUTE_BLOCK_REASONS listesindeki bir nedeni override etmeye çalıştı
2. CI pipeline'da yanlış yapılandırılmış override flag'i
3. Otomasyon aracı bilinçsizce override flag'i gönderiyor

### İlk 3 Kontrol
1. `release_preflight_override_total{kind="breach"}` — breach sayısını ve artış zamanını kontrol et
2. Preflight JSON audit loglarında `contract_breach: true` olan kayıtları bul — hangi repo/branch/user
3. `release_preflight_reason_total` — hangi reason'lar breach tetikledi

### Müdahale Adımları
1. Tek seferlik ise: geliştiriciyi bilgilendir, ABSOLUTE_BLOCK_REASONS'ın override edilemeyeceğini açıkla
2. Tekrarlayan ise: CI pipeline config'ini incele, override flag'inin yanlış kullanılıp kullanılmadığını kontrol et
3. Otomasyon kaynaklı ise: otomasyon aracının override mantığını düzelt

---

## PreflightBlockSpike

**Severity:** warning
**PromQL:** `increase(release_preflight_verdict_total{verdict="BLOCK"}[15m]) > 5 and increase(release_preflight_verdict_total{verdict="BLOCK"}[15m]) / increase(release_preflight_verdict_total[15m]) > 0.2`

### Olası Nedenler
1. Yeni release policy kuralı eklendi — daha fazla repo/branch BLOCK alıyor
2. Altyapı sorunu — tier check, coverage check veya security check geçici olarak hep fail dönüyor
3. Toplu deploy dalgası — çok sayıda repo aynı anda preflight çalıştırıyor ve çoğu BLOCK alıyor
4. Policy config değişikliği — threshold'lar sıkılaştırıldı

### İlk 3 Kontrol
1. `release_preflight_reason_total` — hangi reason'lar artıyor (TIER_FAIL, COVERAGE_LOW, vb.)
2. Son policy config değişikliği — `git log` ile release_policy.py değişikliklerini kontrol et
3. `release_preflight_verdict_total` by verdict — OK/HOLD/BLOCK dağılımını incele

### Müdahale Adımları
1. Policy değişikliği kaynaklı ise: değişikliği gözden geçir, gerekirse rollback
2. Altyapı sorunu ise: ilgili check'in bağımlılığını (tier service, coverage API) kontrol et
3. Geçici spike ise: monitoring'e devam et, 30 dk içinde normale dönmezse investigate et

---

## PreflightTelemetryWriteFailure

**Severity:** warning
**PromQL:** `increase(release_preflight_telemetry_write_failures_total[15m]) > 0`

### Olası Nedenler
1. Disk dolu — metrik dosyası yazılamıyor
2. İzin hatası — preflight_metrics.json dosyasına yazma izni yok
3. Atomic write başarısızlığı — temp dosya oluşturulamıyor veya rename başarısız
4. NFS/network filesystem sorunu — uzak dosya sistemi geçici olarak erişilemez

### İlk 3 Kontrol
1. `release_preflight_telemetry_write_failures_total` — failure sayısını ve artış hızını kontrol et
2. `release_preflight_store_generation` — generation artmaya devam ediyor mu (bazı save'ler başarılı mı)
3. Disk kullanımı — `df -h` ile metrik dizininin disk doluluk oranını kontrol et

### Müdahale Adımları
1. Disk dolu ise: eski log/temp dosyalarını temizle, disk kapasitesini artır
2. İzin hatası ise: dosya/dizin izinlerini düzelt
3. Geçici ise: fail-open politikası gereği preflight verdict etkilenmez, ancak metrik kaybı olur — monitoring'e devam et

### PromQL — Failure Trend

```promql
# Write failure trend (son 24 saat)
increase(release_preflight_telemetry_write_failures_total[1h])

# Store generation ile karşılaştır (başarılı save'ler)
release_preflight_store_generation
```

---

## PreflightCounterReset

**Severity:** warning
**PromQL:** `resets(release_preflight_verdict_total[6h]) > 0`

### Olası Nedenler
1. Container restart — pod yeniden başlatıldı, store dosyası kayboldu
2. Disk kaybı — persistent volume silinmiş veya erişilemez
3. Store dosyası silinmiş — manuel müdahale veya cleanup script
4. Yeni deployment — store dosyası olmayan yeni pod başlatıldı

### İlk 3 Kontrol
1. `release_preflight_store_generation` — generation sıfırlandı mı (yeni store başlatıldı mı)
2. `release_preflight_store_start_time_seconds` — store başlangıç zamanı son deploy zamanıyla eşleşiyor mu
3. Pod restart history — `kubectl get pods -l app=<preflight-app>` ile restart sayısını kontrol et

### Müdahale Adımları
1. Planlı deployment ise: beklenen davranış, counter reset normal
2. Beklenmeyen restart ise: pod loglarını incele, OOM/crash nedenini belirle
3. Disk kaybı ise: persistent volume claim durumunu kontrol et, gerekirse yeniden oluştur

### Counter Reset Triage — Rollout Timestamp Karşılaştırması

```promql
# Store başlangıç zamanı (Unix epoch)
release_preflight_store_start_time_seconds

# Store generation (0 ise yeni store)
release_preflight_store_generation

# Verdict counter reset sayısı (son 6 saat)
resets(release_preflight_verdict_total[6h])
```

Rollout timestamp ile `store_start_time_seconds` karşılaştırması:
- `store_start_time_seconds` ≈ rollout zamanı → planlı restart, beklenen reset
- `store_start_time_seconds` ≈ şimdiki zaman ve generation = 0 → beklenmeyen restart, investigate et

---

# Ek: HTTP Hata Kodu Semantiği (Error Mapping)

### 502 vs 503 Ayrımı (Alert Routing İçin)

| HTTP | Anlam | Kaynak | Alert Route |
|------|-------|--------|-------------|
| 503 | Biz bilinçli reddettik (CB guard, kill-switch) | Guard katmanı | ops → guard config/tuning |
| 502 | Upstream hatalı veya ulaşılamıyor | Dependency | ops → upstream sağlığı |
| 504 | Upstream zaman aşımı | Dependency timeout | ops → latency/timeout tuning |

Bu ayrım, incident sırasında "sorun bizde mi upstream'de mi" kararını hızlandırır.

---

# Ek: Dependency Wrapper — Retry & Backoff Referansı

### Retry Politikası (DW-1)

| Path | Retry | Neden |
|------|-------|-------|
| Read (is_write=False) | Aktif (max N retry) | İdempotent, güvenli |
| Write (is_write=True) | Default KAPALI | Double-write riski; ancak idempotency key garantisi varsa `wrapper_retry_on_write=True` ile açılabilir |

### Backoff Formülü

```
delay_ms = min(base_ms * 2^attempt, cap_ms)
jitter   = uniform(0, delay_ms * jitter_pct)
total    = (delay_ms + jitter) / 1000  # seconds
```

- Jitter tipi: **Decorrelated (additive uniform)** — `uniform(0, delay * pct)` eklenir
- Bu "full jitter" değil (full jitter: `uniform(0, cap)`), "equal jitter" de değil (equal: `delay/2 + uniform(0, delay/2)`)
- Mevcut implementasyon: base delay korunur, üzerine `[0, delay*pct]` aralığında rastgele ekleme yapılır
- `jitter_pct=0.2` default → delay'in %0–20'si kadar ek süre

### Varsayılan Değerler

| Parametre | Default | Env Var |
|-----------|---------|---------|
| `wrapper_retry_max_attempts_default` | 2 | `OPS_GUARD_WRAPPER_RETRY_MAX_ATTEMPTS_DEFAULT` |
| `wrapper_retry_backoff_base_ms` | 500 | `OPS_GUARD_WRAPPER_RETRY_BACKOFF_BASE_MS` |
| `wrapper_retry_backoff_cap_ms` | 5000 | `OPS_GUARD_WRAPPER_RETRY_BACKOFF_CAP_MS` |
| `wrapper_retry_jitter_pct` | 0.2 | `OPS_GUARD_WRAPPER_RETRY_JITTER_PCT` |
| `wrapper_retry_on_write` | False | `OPS_GUARD_WRAPPER_RETRY_ON_WRITE` |

### Outcome Metrikleri

`ptf_admin_dependency_call_total{dependency, outcome}` outcome değerleri:

| Outcome | Anlam | CB'ye failure sayılır mı? | Retry yapılır mı? |
|---------|-------|---------------------------|-------------------|
| `success` | Başarılı çağrı | Hayır (record_success) | — |
| `failure` | CB failure (5xx, ConnectionError, OSError) | Evet (record_failure) | Evet (read path) |
| `timeout` | asyncio.TimeoutError | Evet (record_failure) | Evet (read path) |
| `circuit_open` | CB OPEN, çağrı yapılmadı | — | Hayır |
| `client_error` | Non-CB failure (4xx, ValueError) | Hayır | Hayır |

### Timeout CB İlişkisi

Timeout → `record_failure()` çağrılır → CB failure sayılır. Agresif CB açılma riski varsa:
- `cb_min_samples` artırılabilir (default: 10)
- `cb_error_threshold_pct` yükseltilebilir (default: 50%)
- İleride timeout'a ayrı threshold eklenebilir (ör. "N timeout in window")

---

# Ek: Bypass Test Failure Troubleshooting

CI'da `TestBypassProtection` testleri fail olduğunda izlenecek adımlar.

### Neden Fail Olur?

`TestBypassProtection`, FastAPI route registry'den `CRITICAL_PATH_PREFIXES` ile eşleşen tüm endpoint'leri otomatik keşfeder ve her birinin:
- `_get_wrapper()` kullandığını
- `_map_wrapper_error_to_http()` kullandığını
- Doğrudan DB çağrısı yapmadığını

doğrular. Fail olması şu anlama gelir: **yeni bir kritik yol endpoint'i wrapper olmadan eklendi**.

### Olası Nedenler

1. Yeni endpoint eklendi, `_get_wrapper()` ile sarılmadı
2. Yeni endpoint eklendi, `_map_wrapper_error_to_http()` ile error mapping yapılmadı
3. Endpoint doğrudan `db.query()` / `db.execute()` / `db.commit()` çağırıyor (wrapper bypass)
4. Deprecated/meta endpoint `EXEMPT_PATHS`'e eklenmedi

### Çözüm Adımları

1. **Wrapper eksik ise**: Endpoint'i `_get_wrapper(dependency_name)` + `asyncio.to_thread` ile sarın. Error handling'de `_map_wrapper_error_to_http()` kullanın. Örnek: mevcut `list_market_prices` veya `upsert_market_price` handler'larına bakın.

2. **Muafiyet gerekiyorsa**: Endpoint gerçekten DB'ye erişmiyorsa veya başka bir wrapper'lı endpoint'e delege ediyorsa, `EXEMPT_PATHS`'e ekleyin. **Mutlaka "Neden?" yorumu yazın**:
   ```python
   EXEMPT_PATHS = {
       # Neden: <açıklama>
       "/admin/market-prices/yeni-endpoint",
   }
   ```

3. **Closure pattern kullanıyorsa** (unlock_market_price gibi): `test_no_direct_db_calls_in_critical_endpoints` testindeki `CLOSURE_ENDPOINTS` set'ine ekleyin.

### Muafiyet Politikası

- `EXEMPT_PATHS` küçük tutulmalı (şu an 3 endpoint)
- Her muafiyetin yanında "Neden?" yorumu zorunlu
- Deprecated endpoint kaldırılınca muafiyet de silinmeli
- Yeni muafiyet eklemek PR review gerektirir


---

# Guard Decision Layer — Operasyonel Runbook

## Genel Bilgi

Guard Decision Layer, mevcut OpsGuard middleware zincirinin (KillSwitch → RateLimiter → CircuitBreaker) üzerine oturan ek bir karar katmanıdır. Config freshness ve CB mapping sufficiency sinyallerini değerlendirir.

### Temel Özellikler

| Özellik | Değer |
|---------|-------|
| Flag adı | `OPS_GUARD_DECISION_LAYER_ENABLED` |
| Varsayılan | `false` (OFF) |
| Middleware sırası | OpsGuard (dış) → GuardDecision (iç) |
| Fail-open | Evet — crash/exception → mevcut davranış korunur |
| 429 semantiği | Değişmez — RATE_LIMITED → 429 + Retry-After aynen korunur |

### 503 Error Code'ları

| errorCode | Anlam | Tetikleyen Sinyal |
|-----------|-------|-------------------|
| `OPS_GUARD_STALE` | Config yaşı threshold'u aştı | CONFIG_FRESHNESS → STALE |
| `OPS_GUARD_INSUFFICIENT` | Veri eksik (config timestamp yok veya CB mapping miss) | CONFIG_FRESHNESS → INSUFFICIENT veya CB_MAPPING → INSUFFICIENT |

### reasonCodes (Canonical Ordering)

Response payload'daki `reasonCodes` listesi deterministik sıralıdır:
1. SignalName enum ordinal (CONFIG_FRESHNESS < CB_MAPPING)
2. SignalReasonCode string value (lexicographic)

Olası değerler (bounded set):
- `CONFIG_TIMESTAMP_MISSING` — last_updated_at boş
- `CONFIG_TIMESTAMP_PARSE_ERROR` — last_updated_at parse edilemedi
- `CONFIG_STALE` — config yaşı > max_config_age_ms
- `CB_MAPPING_MISS` — endpoint → dependency eşlemesi bulunamadı

### Enable Prosedürü (Kademeli Rollout — Shadow → Enforce)

**Adım 1: Shadow mode ile aç**

```bash
OPS_GUARD_DECISION_LAYER_ENABLED=true
OPS_GUARD_DECISION_LAYER_MODE=shadow    # default, açıkça yazılması önerilir
OPS_GUARD_LAST_UPDATED_AT=<ISO-8601>    # config freshness için gerekli
```

1. Deploy et
2. Grafana → Guard Decision Layer row'unu aç
3. `guard_decision_requests_total` artışını doğrula (middleware çalışıyor)
4. `guard_decision_block_total{kind}` izle — shadow modda 503 dönmez ama counter artar
5. 24–48 saat gözlem yap:
   - `block_total{kind="stale"}` > 0 ise: `OPS_GUARD_LAST_UPDATED_AT` güncel mi kontrol et
   - `block_total{kind="insufficient"}` > 0 ise: `endpoint_dependency_map.py` eksik mapping var mı kontrol et
   - Her iki counter 0 ise: policy sağlıklı, enforce'a geçilebilir

**Adım 2: Enforce mode'a geç**

```bash
OPS_GUARD_DECISION_LAYER_MODE=enforce
```

1. Deploy et
2. 15 dk izle — `guard_decision_block_total` artışı gerçek 503'lere dönüşecek
3. False positive varsa hemen rollback:
   ```bash
   OPS_GUARD_DECISION_LAYER_ENABLED=false
   ```

**Rollback (acil)**

```bash
# Seçenek 1: Katmanı tamamen kapat
OPS_GUARD_DECISION_LAYER_ENABLED=false

# Seçenek 2: Shadow'a geri dön (metrik toplamaya devam et)
OPS_GUARD_DECISION_LAYER_MODE=shadow
```

---

## PTFAdminGuardDecisionBuildFailure

**Severity:** warning
**PromQL:** `increase(ptf_admin_guard_decision_snapshot_build_failures_total[15m]) > 0`

### Olası Nedenler
1. SnapshotFactory.build() internal exception — config parse, hash computation veya signal producer hatası
2. GuardConfig singleton henüz initialize edilmemiş (startup race)
3. endpoint_normalization veya endpoint_dependency_map import hatası

### İlk 3 Kontrol
1. `kubectl logs -l app=ptf-admin --tail=100 | grep "GUARD-DECISION.*SnapshotFactory"` — exception stack trace'i bul
2. `ptf_admin_guard_decision_requests_total` — decision layer çalışıyor mu (counter artıyor mu)
3. `ptf_admin_guard_decision_snapshot_build_failures_total` — failure sayısı ve artış hızı

### Müdahale Adımları
1. Tek seferlik ise: log'dan root cause belirle, monitoring'e devam et (fail-open aktif, trafik etkilenmez)
2. Sürekli artıyorsa: decision layer'da bug var — `OPS_GUARD_DECISION_LAYER_ENABLED=false` ile kapat, fix deploy et
3. Startup race ise: pod restart sonrası düzelir, kalıcı ise startup sırasını incele

### PromQL — Triage

```promql
# Build failure trend (son 1 saat)
increase(ptf_admin_guard_decision_snapshot_build_failures_total[1h])

# Failure rate vs request rate
increase(ptf_admin_guard_decision_snapshot_build_failures_total[15m])
/
increase(ptf_admin_guard_decision_requests_total[15m])
```

---

## PTFAdminGuardDecisionBlockRate

**Severity:** warning
**PromQL:** `sum(increase(ptf_admin_guard_decision_block_total[15m])) > 5`

### Olası Nedenler
1. Config stale — `OPS_GUARD_LAST_UPDATED_AT` güncellenmemiş, config yaşı threshold'u aştı
2. CB mapping miss — yeni endpoint eklendi ama `endpoint_dependency_map.py` güncellenmedi
3. Config timestamp parse error — `last_updated_at` formatı bozuk

### İlk 3 Kontrol
1. `ptf_admin_guard_decision_block_total` by kind — stale mi insufficient mi
2. `OPS_GUARD_LAST_UPDATED_AT` env var'ını kontrol et — boş mu, eski mi, parse edilebilir mi
3. `endpoint_dependency_map.py` — son deploy'da yeni endpoint eklenmiş mi

### Müdahale Adımları
1. Config stale ise: `OPS_GUARD_LAST_UPDATED_AT` güncelle ve redeploy et
2. Mapping miss ise: `endpoint_dependency_map.py`'ye eksik endpoint'i ekle
3. Acil bypass: `OPS_GUARD_DECISION_LAYER_ENABLED=false` ile karar katmanını kapat

### PromQL — Block Breakdown

```promql
# Block dağılımı (kind bazında)
sum by (kind) (increase(ptf_admin_guard_decision_block_total[1h]))

# Block dağılımı (mode bazında — shadow vs enforce ayırımı)
sum by (kind, mode) (increase(ptf_admin_guard_decision_block_total[1h]))

# Sadece enforce modda gerçek block'lar
sum by (kind) (increase(ptf_admin_guard_decision_block_total{mode="enforce"}[1h]))

# Sadece shadow modda potansiyel block'lar
sum by (kind) (increase(ptf_admin_guard_decision_block_total{mode="shadow"}[1h]))

# Block rate (15 dk pencere)
sum(increase(ptf_admin_guard_decision_block_total[15m]))

# Snapshot build failure rate (fail-open tespiti)
increase(ptf_admin_guard_decision_snapshot_build_failures_total[15m])
```

---

## PTFAdminGuardDecisionSilent

**Severity:** warning
**PromQL:** `increase(ptf_admin_guard_decision_requests_total[15m]) == 0 and increase(ptf_admin_api_request_total[15m]) > 10`

### Olası Nedenler
1. `OPS_GUARD_DECISION_LAYER_ENABLED=false` — flag kapalı (beklenen davranış olabilir)
2. Middleware sırası yanlış — GuardDecisionMiddleware OpsGuard'dan dışta, tüm request'ler skip ediliyor
3. `_SKIP_PATHS` çok geniş — tüm trafik skip path'e düşüyor
4. Middleware import hatası — GuardDecisionMiddleware yüklenemedi

### İlk 3 Kontrol
1. `OPS_GUARD_DECISION_LAYER_ENABLED` env var'ını kontrol et — true mu
2. `main.py`'de middleware kayıt sırasını doğrula — GuardDecision inner, OpsGuard outer olmalı
3. Uygulama loglarında `[GUARD-DECISION]` entry var mı kontrol et

### Müdahale Adımları
1. Flag kapalı ise: beklenen davranış, alert'i acknowledge et
2. Middleware sırası yanlış ise: `main.py`'de `add_middleware` sırasını düzelt ve redeploy et
3. Import hatası ise: uygulama loglarını incele, dependency'leri kontrol et


---

# Guard Decision Layer — Release Note Checklist

## Özellik Özeti

Runtime Guard Decision Layer: mevcut OpsGuard zincirinin üzerine oturan, config freshness ve CB mapping sufficiency sinyallerini değerlendiren ek karar katmanı.

## Checklist

### 1. Feature Flags

| Env Var | Default | Açıklama |
|---------|---------|----------|
| `OPS_GUARD_DECISION_LAYER_ENABLED` | `false` | Katmanı aç/kapat |
| `OPS_GUARD_DECISION_LAYER_MODE` | `shadow` | `shadow`: metrik only, `enforce`: gerçek 503 |

### 2. Yeni 503 Error Code'ları

| errorCode | HTTP | Anlam |
|-----------|------|-------|
| `OPS_GUARD_STALE` | 503 | Config yaşı threshold'u aştı |
| `OPS_GUARD_INSUFFICIENT` | 503 | Config timestamp eksik veya CB mapping miss |

Mevcut 429 (RATE_LIMITED + Retry-After) semantiği değişmez.

### 3. Prometheus Alert'leri (3 yeni)

| Alert | Severity | Tetik |
|-------|----------|-------|
| `PTFAdminGuardDecisionBuildFailure` | warning | Snapshot build failure (fail-open aktif) |
| `PTFAdminGuardDecisionBlockRate` | warning | Block sayısı > 5 AND oran > %1 (15dk) |
| `PTFAdminGuardDecisionSilent` | warning | Trafik var ama decision layer çalışmıyor |

### 4. Grafana Dashboard

Row: "Guard Decision Layer" (id=600, 3 panel)
- Request Rate (layer active)
- Block Rate by Kind (stale/insufficient)
- Snapshot Build Failures (stat panel)

### 5. Yeni Metrikler

| Metrik | Tip | Label |
|--------|-----|-------|
| `ptf_admin_guard_decision_requests_total` | Counter | — |
| `ptf_admin_guard_decision_block_total` | Counter | `kind` (stale/insufficient), `mode` (shadow/enforce) |
| `ptf_admin_guard_decision_snapshot_build_failures_total` | Counter | — |

### 6. Rollout Prosedürü

```
shadow (24-48h gözlem) → enforce → prod
```

Sorun varsa: `OPS_GUARD_DECISION_LAYER_ENABLED=false` ile anında rollback.

### 7. Rollback

```bash
# Tam kapatma
OPS_GUARD_DECISION_LAYER_ENABLED=false

# Shadow'a geri dönme (metrik toplamaya devam)
OPS_GUARD_DECISION_LAYER_MODE=shadow
```

### 8. Bilinen Kısıtlamalar (v1)

- Tenant bazlı enable yok — global aç/kapat
- `tenant_id` sabit "default"
- WindowParams (max_config_age_ms, clock_skew_allowance_ms) henüz env var ile konfigüre edilemiyor (kod default'ları kullanılır)
