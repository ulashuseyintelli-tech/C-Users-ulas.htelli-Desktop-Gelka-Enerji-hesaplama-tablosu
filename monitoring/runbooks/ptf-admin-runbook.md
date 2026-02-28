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

# Ops Guard — Operasyonel Prosedürler

Bu bölüm, ops-guard bileşenlerinin (kill-switch, rate limiter, circuit breaker) operasyonel yönetimi için adım adım prosedürleri içerir. Alert triage sonrası müdahale için referans olarak kullanılır.

---

### Kill-Switch Yönetimi

### Kill-Switch Açma (Acil Engelleme)

**Ne zaman:** Bulk import hasarı, abuse tespiti veya planlı bakım sırasında belirli işlevleri anında durdurmak gerektiğinde.

**Adımlar:**

1. Mevcut durumu kontrol et:
   ```bash
   curl -s -H "X-Admin-Key: $ADMIN_KEY" https://<host>/admin/ops/kill-switches | jq .
   ```

2. Global import kill-switch aç:
   ```bash
   curl -s -X PUT -H "X-Admin-Key: $ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"enabled": true, "reason": "Incident #1234 — bulk import durduruldu"}' \
     https://<host>/admin/ops/kill-switches/global_import
   ```

3. Degrade mode aç (tüm write path kapatılır, sadece read):
   ```bash
   curl -s -X PUT -H "X-Admin-Key: $ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"enabled": true, "reason": "DB maintenance — read-only mode"}' \
     https://<host>/admin/ops/kill-switches/degrade_mode
   ```

4. Tenant bazlı engelleme:
   ```bash
   curl -s -X PUT -H "X-Admin-Key: $ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"enabled": true, "reason": "Tenant abuse tespiti"}' \
     https://<host>/admin/ops/kill-switches/tenant:TENANT_ID
   ```

5. Doğrulama:
   - `ptf_admin_killswitch_state{switch_name}` gauge → 1 olmalı
   - Audit loglarında `[KILLSWITCH]` entry doğrula
   - Grafana → Ops Guard Status → Kill-Switch State panelinde kırmızı

### Kill-Switch Kapatma (Normal Operasyona Dönüş)

1. Switch'i deaktive et:
   ```bash
   curl -s -X PUT -H "X-Admin-Key: $ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{"enabled": false, "reason": "Incident #1234 resolved"}' \
     https://<host>/admin/ops/kill-switches/global_import
   ```

2. Doğrulama:
   - `ptf_admin_killswitch_state{switch_name}` gauge → 0
   - Trafik akışını izle — 503'ler durmalı
   - PTFAdminKillSwitchActivated alert'i resolve olmalı

### Kill-Switch Failure Semantiği

| Endpoint Sınıfı | Hata Davranışı | Gerekçe |
|---|---|---|
| High-risk (import/apply, bulk write) | Fail-closed (503) | Kontrolsüz bulk write veriyi bozabilir |
| Diğer (GET, tekil upsert, lookup) | Fail-open (istek geçer) | Read/tekil write durdurulmamalı |

Fail-open durumda `ptf_admin_killswitch_fallback_open_total` counter artar → PTFAdminGuardInternalError alert'i tetiklenir.

---

### Rate Limit Tuning

### Mevcut Limitleri Görüntüleme

```bash
curl -s -H "X-Admin-Key: $ADMIN_KEY" https://<host>/admin/ops/status | jq .
```

### Limit Değerlerini Değiştirme

Rate limit eşikleri env var ile konfigüre edilir. Değişiklik redeploy gerektirir.

| Env Var | Default | Açıklama |
|---------|---------|----------|
| `OPS_GUARD_RATE_LIMIT_IMPORT_PER_MINUTE` | 10 | Bulk import endpoint limiti |
| `OPS_GUARD_RATE_LIMIT_HEAVY_READ_PER_MINUTE` | 120 | Heavy read endpoint limiti |
| `OPS_GUARD_RATE_LIMIT_DEFAULT_PER_MINUTE` | 60 | Diğer endpoint'ler |

**Adımlar:**

1. Mevcut deny rate'i kontrol et:
   ```promql
   sum(rate(ptf_admin_rate_limit_total{decision="rejected"}[5m])) by (endpoint)
   ```

2. Hangi endpoint'lerin etkilendiğini belirle:
   ```promql
   topk(10, sum(rate(ptf_admin_rate_limit_total{decision="rejected"}[15m])) by (endpoint))
   ```

3. Env var'ı güncelle ve redeploy et:
   ```bash
   # Örnek: import limitini 10 → 20'ye çıkar
   kubectl set env deployment/ptf-admin OPS_GUARD_RATE_LIMIT_IMPORT_PER_MINUTE=20
   ```

4. Doğrulama:
   - `ptf_admin_rate_limit_total{decision="rejected"}` rate düşmeli
   - PTFAdminRateLimitSpike alert'i resolve olmalı

### Rate Limit Fail-Closed Politikası

Rate limiter iç hatası durumunda istek reddedilir (fail-closed). Bu güvenlik öncelikli bir karardır. Eğer rate limiter hatası nedeniyle meşru trafik engelleniyorsa:

1. `ptf_admin_rate_limit_total` metriğini kontrol et — decision label'ı "rejected" mi
2. Uygulama loglarında rate limiter exception'larını ara
3. Acil bypass gerekiyorsa: pod restart genellikle rate limiter state'ini sıfırlar

---

### Circuit Breaker Reset Prosedürü

### CB Durumunu Kontrol Etme

```bash
curl -s -H "X-Admin-Key: $ADMIN_KEY" https://<host>/admin/ops/status | jq .circuit_breakers
```

```promql
# CB state by dependency (0=closed, 1=half-open, 2=open)
ptf_admin_circuit_breaker_state
```

### CB Açık Kaldığında (Open State)

CB open state'te kalması bağımlılık arızasının devam ettiği anlamına gelir. Normal akış:

1. CB open → `cb_open_duration_seconds` (default: 30s) sonra half-open'a geçer
2. Half-open'da `cb_half_open_max_requests` (default: 3) kadar probe isteği gönderilir
3. Probe başarılı → closed'a döner
4. Probe başarısız → tekrar open'a döner

**Bağımlılık hâlâ down ise:**
- CB otomatik koruma sağlıyor, müdahale gerekmez
- Bağımlılığı kurtarmaya odaklanın (DB restart, upstream fix)
- CB recovery otomatik olacaktır

**Bağımlılık düzeldi ama CB hâlâ open ise:**
- `cb_open_duration_seconds` süresini bekleyin (half-open'a geçecek)
- Acil reset gerekiyorsa: pod restart CB state'ini sıfırlar
  ```bash
  kubectl rollout restart deployment/ptf-admin
  ```

### CB Threshold Tuning

| Env Var | Default | Açıklama |
|---------|---------|----------|
| `OPS_GUARD_CB_ERROR_THRESHOLD_PCT` | 50.0 | Hata oranı eşiği (%) |
| `OPS_GUARD_CB_OPEN_DURATION_SECONDS` | 30.0 | Open → half-open geçiş süresi |
| `OPS_GUARD_CB_HALF_OPEN_MAX_REQUESTS` | 3 | Half-open'da max probe sayısı |
| `OPS_GUARD_CB_WINDOW_SECONDS` | 60.0 | Hata oranı hesaplama penceresi |
| `OPS_GUARD_CB_MIN_SAMPLES` | 10 | Threshold uygulanmadan önce min event |

**CB çok agresif açılıyorsa:**
1. `cb_min_samples` artır (düşük trafik + birkaç hata → erken açılma)
2. `cb_error_threshold_pct` yükselt (daha toleranslı)
3. `cb_window_seconds` genişlet (daha uzun pencere, anlık spike'ları yumuşatır)

**CB çok geç açılıyorsa:**
1. `cb_error_threshold_pct` düşür
2. `cb_min_samples` azalt
3. `cb_window_seconds` daralt

### Dependency Enum (Sabit Set — HD-5)

CB `dependency` label'ı sabit enum'dan gelir:

| Dependency | Açıklama |
|------------|----------|
| `db_primary` | Ana veritabanı |
| `db_replica` | Okuma replikası |
| `cache` | Cache katmanı |
| `external_api` | Dış API bağımlılıkları |
| `import_worker` | Import worker servisi |

---

### Guard Durumu Özet Kontrolü

Tüm guard bileşenlerinin durumunu tek sorguda görmek için:

```bash
curl -s -H "X-Admin-Key: $ADMIN_KEY" https://<host>/admin/ops/status | jq .
```

### Grafana Dashboard Referansı

- Ops Guard Status row → Kill-Switch State, CB State, Rate Limit Distribution, Top Rate-Limited Endpoints
- Guard Decision Layer row → Decision Request Rate, Block Rate, Snapshot Build Failures

### PromQL — Hızlı Durum Kontrolü

```promql
# Kill-switch aktif mi?
max(ptf_admin_killswitch_state) == 1

# CB open olan dependency var mı?
max(ptf_admin_circuit_breaker_state) == 2

# Rate limit deny rate (son 5 dk)
sum(rate(ptf_admin_rate_limit_total{decision="rejected"}[5m])) * 60

# Guard config fallback aktif mi?
increase(ptf_admin_guard_config_fallback_total[15m])
```

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

### Genel Bilgi

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

### PromQL — Risk Class Breakdown (Endpoint-Class Policy)

```promql
# Request dağılımı (mode × risk_class)
sum by (mode, risk_class) (increase(ptf_admin_guard_decision_requests_by_risk_total[15m]))

# Block dağılımı (kind × mode × risk_class)
sum by (kind, mode, risk_class) (increase(ptf_admin_guard_decision_block_by_risk_total[15m]))

# Sadece HIGH risk endpoint block'ları
sum by (kind, mode) (increase(ptf_admin_guard_decision_block_by_risk_total{risk_class="high"}[1h]))

# Risk class bazında request oranı
sum by (risk_class) (rate(ptf_admin_guard_decision_requests_by_risk_total[5m]))
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

### Özellik Özeti

Runtime Guard Decision Layer: mevcut OpsGuard zincirinin üzerine oturan, config freshness ve CB mapping sufficiency sinyallerini değerlendiren ek karar katmanı.

### Checklist

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
| `ptf_admin_guard_decision_requests_by_risk_total` | Counter | `mode` (shadow/enforce), `risk_class` (low/medium/high) |
| `ptf_admin_guard_decision_block_by_risk_total` | Counter | `kind` (stale/insufficient), `mode` (shadow/enforce), `risk_class` (low/medium/high) |

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

---

# Release Gate Telemetry

Bu bölüm, Release Gate enforcement hook'unun telemetri alert'leri tetiklendiğinde izlenecek troubleshooting adımlarını içerir. Dashboard: `monitoring/grafana/release-gate-dashboard.json` (uid: release-gate-telemetry).

---

## ReleaseGateContractBreach

**Severity:** critical
**PromQL:** `increase(release_gate_contract_breach_total[5m]) > 0`

### Olası Nedenler
1. Geliştirici ABSOLUTE_BLOCK_REASONS (GUARD_VIOLATION, OPS_GATE_FAIL) içeren bir kararı override etmeye çalıştı
2. CI pipeline'da yanlış yapılandırılmış override flag'i — otomasyon bilinçsizce override gönderiyor
3. Policy config değişikliği — ABSOLUTE_BLOCK_REASONS kümesi genişletildi, mevcut override'lar breach'e dönüştü

### İlk 3 Kontrol
1. `increase(release_gate_contract_breach_total[1h])` — breach sayısını ve artış zamanını kontrol et
2. Audit loglarında `CONTRACT_BREACH_NO_OVERRIDE` olan kayıtları bul — hangi repo/branch/user
3. `sum by (reason) (increase(release_gate_decision_total{decision="DENY"}[1h]))` — hangi reason'lar breach tetikledi

### Müdahale Adımları
1. Tek seferlik ise: geliştiriciyi bilgilendir, ABSOLUTE_BLOCK_REASONS'ın override edilemeyeceğini açıkla
2. Tekrarlayan ise: CI pipeline config'ini incele, override flag'inin yanlış kullanılıp kullanılmadığını kontrol et
3. Otomasyon kaynaklı ise: otomasyon aracının override mantığını düzelt

### PromQL — Tanılama

```promql
# Breach trend (son 24 saat)
increase(release_gate_contract_breach_total[1h])

# Breach ile eşzamanlı DENY nedenleri
sum by (reason) (increase(release_gate_decision_total{decision="DENY"}[1h]))

# ALLOW/DENY dağılımı
sum by (decision) (increase(release_gate_decision_total[15m]))
```

---

## ReleaseGateAuditWriteFailure

**Severity:** warning
**PromQL:** `increase(release_gate_audit_write_failures_total[15m]) > 0`

### Olası Nedenler
1. Disk dolu — audit dosyası yazılamıyor
2. İzin hatası — audit log dosyasına yazma izni yok
3. Atomic write başarısızlığı — temp dosya oluşturulamıyor veya rename başarısız
4. NFS/network filesystem sorunu — uzak dosya sistemi geçici olarak erişilemez

### İlk 3 Kontrol
1. `increase(release_gate_audit_write_failures_total[1h])` — failure sayısını ve artış hızını kontrol et
2. Disk kullanımı — `df -h` ile audit dizininin disk doluluk oranını kontrol et
3. Gate kararlarını kontrol et — audit failure durumunda R3 invariantı gereği gate allowed=False döner

### Müdahale Adımları
1. Disk dolu ise: eski log/temp dosyalarını temizle, disk kapasitesini artır
2. İzin hatası ise: dosya/dizin izinlerini düzelt
3. R3 invariantı aktif: audit yazılamadığında gate otomatik olarak DENY döner — "kanıt yoksa izin yok". Audit düzeltilene kadar tüm release'ler bloklanır

### PromQL — Tanılama

```promql
# Audit failure trend (son 24 saat)
increase(release_gate_audit_write_failures_total[1h])

# Gate karar dağılımı (audit failure DENY artışı ile korelasyon)
sum by (decision) (increase(release_gate_decision_total[15m]))

# Metric write failures (dahili telemetri sağlığı)
increase(release_gate_metric_write_failures_total[15m])
```

---

## ReleaseGateDenySpike

**Severity:** warning
**PromQL:** `increase(release_gate_decision_total{decision="DENY"}[15m]) > 10 and increase(release_gate_decision_total{decision="DENY"}[15m]) / clamp_min(increase(release_gate_decision_total[15m]), 1) > 0.3`

### Olası Nedenler
1. Yeni release policy kuralı eklendi — daha fazla repo/branch DENY alıyor
2. Altyapı sorunu — tier check, coverage check veya security check geçici olarak hep fail dönüyor
3. Toplu deploy dalgası — çok sayıda repo aynı anda gate'den geçmeye çalışıyor ve çoğu DENY alıyor
4. Policy config değişikliği — threshold'lar sıkılaştırıldı

### İlk 3 Kontrol
1. `topk(10, sum by (reason) (increase(release_gate_decision_total{decision="DENY"}[1h])))` — hangi reason'lar artıyor
2. Son policy config değişikliği — `git log` ile release_policy.py ve release_gate.py değişikliklerini kontrol et
3. `sum by (decision) (increase(release_gate_decision_total[15m]))` — ALLOW/DENY dağılımını incele

### Müdahale Adımları
1. Policy değişikliği kaynaklı ise: değişikliği gözden geçir, gerekirse rollback
2. Altyapı sorunu ise: ilgili check'in bağımlılığını (tier service, coverage API) kontrol et
3. Geçici spike ise: monitoring'e devam et, 30 dk içinde normale dönmezse investigate et

### PromQL — Tanılama

```promql
# DENY spike trend
increase(release_gate_decision_total{decision="DENY"}[15m])

# Top deny reasons
topk(10, sum by (reason) (increase(release_gate_decision_total{decision="DENY"}[1h])))

# DENY oranı
increase(release_gate_decision_total{decision="DENY"}[15m])
/
clamp_min(increase(release_gate_decision_total[15m]), 1)

# Contract breach korelasyonu
increase(release_gate_contract_breach_total[15m])
```

---

## PTFAdminPdfQueueUnavailable

**Severity:** warning
**PromQL:** `increase(ptf_admin_pdf_job_failures_total{error_code="QUEUE_UNAVAILABLE"}[15m]) > 0`

### Olası Nedenler
1. Redis down veya bağlantı kopmuş — RQ enqueue başarısız
2. RQ worker crash — worker process ölmüş, kuyruk yazılamıyor
3. Connection pool exhaustion — Redis bağlantı havuzu dolmuş
4. Network partition — uygulama ile Redis arasında ağ sorunu

### İlk 3 Kontrol
1. `redis-cli ping` — Redis erişilebilir mi kontrol et
2. `rq info` — RQ worker durumunu ve kuyruk boyutunu kontrol et
3. Uygulama logları — `QUEUE_UNAVAILABLE` hata mesajlarını filtrele

### Müdahale Adımları
1. Redis down ise: Redis'i restart et veya failover'a geç
2. Connection pool ise: `REDIS_MAX_CONNECTIONS` artır ve redeploy et
3. Worker crash ise: `rq worker` process'ini restart et, OOM/crash loglarını incele

### PromQL Referansı

```promql
# Queue unavailable trend
increase(ptf_admin_pdf_job_failures_total{error_code="QUEUE_UNAVAILABLE"}[1h])

# Tüm failure'lar by error_code
sum by (error_code) (increase(ptf_admin_pdf_job_failures_total[1h]))
```

---

## PTFAdminPdfFailureSpike

**Severity:** warning
**PromQL:** `increase(ptf_admin_pdf_jobs_total{status="failed"}[15m]) > 3 and ... > 0.2`

### Olası Nedenler
1. BROWSER_LAUNCH_FAILED — Playwright browser başlatılamıyor (sandbox, binary eksik)
2. NAVIGATION_TIMEOUT — Sayfa render süresi aşıldı (karmaşık HTML, büyük payload)
3. TEMPLATE_ERROR — Geçersiz template veya HTML içeriği
4. ARTIFACT_WRITE_FAILED — PDF dosyası yazılamıyor (disk dolu, izin hatası)
5. UNSUPPORTED_PLATFORM — Playwright desteklenmeyen platform

### İlk 3 Kontrol
1. `ptf_admin_pdf_job_failures_total` by error_code — hangi hata tipi baskın
2. Uygulama logları — `Job .* failed:` pattern'ini filtrele
3. `ptf_admin_pdf_job_duration_seconds` p95 — render süresi artmış mı

### Müdahale Adımları
1. Hata tipine göre aşağıdaki adımları uygulayın:

**BROWSER_LAUNCH_FAILED:**
1. Playwright binary kurulu mu: `playwright install chromium`
2. Sandbox izinleri: `--no-sandbox` flag'i gerekebilir (container ortamında)
3. Memory: browser launch için yeterli memory var mı kontrol et

**NAVIGATION_TIMEOUT:**
1. HTML payload boyutunu kontrol et — çok büyük payload'lar timeout'a neden olur
2. `PDF_HARD_TIMEOUT` env var'ını artır (varsayılan: 60s)
3. Karmaşık CSS/JS içeren template'leri optimize et

**ARTIFACT_WRITE_FAILED:**
1. Disk kullanımı: `df -h` ile artifact dizininin doluluk oranını kontrol et
2. İzinler: artifact dizinine yazma izni var mı
3. Storage backend (S3) erişilebilir mi

### PromQL Referansı

```promql
# Failure by error_code (son 1 saat)
sum by (error_code) (increase(ptf_admin_pdf_job_failures_total[1h]))

# Failure rate
increase(ptf_admin_pdf_jobs_total{status="failed"}[15m])
/
clamp_min(increase(ptf_admin_pdf_jobs_total[15m]), 1)

# Render duration p95
histogram_quantile(0.95, sum(rate(ptf_admin_pdf_job_duration_seconds_bucket[15m])) by (le))
```

---

## PTFAdminPdfQueueBacklog

**Severity:** warning
**PromQL:** `ptf_admin_pdf_queue_depth > 50`

**Not:** `ptf_admin_pdf_queue_depth` gauge'u uygulama tarafından set edilir (`set_pdf_queue_depth()`). Kaynak: RQ/Redis'den okunan gerçek kuyruk derinliği veya store'daki QUEUED job sayısı — hangisi kullanılıyorsa worker bootstrap kodunda belirlenir. Alert eşiği: 50 (PW3 ile senkron).

### Olası Nedenler
1. Worker sayısı yetersiz — gelen iş yükü worker kapasitesini aşıyor
2. Worker crash — worker process'leri ölmüş, kuyruk işlenmiyor
3. Render süresi artmış — her job daha uzun sürüyor, throughput düşmüş
4. Burst traffic — ani PDF oluşturma talebi artışı

### İlk 3 Kontrol
1. `rq info` — aktif worker sayısını ve kuyruk boyutunu kontrol et
2. `ptf_admin_pdf_job_duration_seconds` p95 — render süresi artmış mı
3. `ptf_admin_pdf_jobs_total{status="succeeded"}` rate — throughput düşmüş mü

### Müdahale Adımları
1. Worker yetersiz ise: worker replica sayısını artır
2. Worker crash ise: worker loglarını incele, restart et
3. Render süresi artmış ise: template optimizasyonu veya timeout ayarı
4. Burst traffic ise: geçici, kuyruk boşalmasını bekle; tekrarlıyorsa worker scale-up planlayın

### PromQL Referansı

```promql
# Queue depth
ptf_admin_pdf_queue_depth

# Throughput (succeeded/min)
sum(rate(ptf_admin_pdf_jobs_total{status="succeeded"}[5m])) * 60

# Duration p95
histogram_quantile(0.95, sum(rate(ptf_admin_pdf_job_duration_seconds_bucket[15m])) by (le))
```

---

# Prod Rollout Checklist

Bu checklist, PTF Admin sisteminin production ortamına ilk deploy'u veya major versiyon güncellemesi için kullanılır. Her madde doğrulanmadan bir sonraki aşamaya geçilmez.

---

### 0) Ön Koşullar

| # | Kontrol | Doğrulama |
|---|---------|-----------|
| 0.1 | Redis erişilebilir (PDF worker + RQ) | `redis-cli ping` → PONG |
| 0.2 | RQ worker deployment hazır (en az 1 instance) | `rq info` → worker count ≥ 1 |
| 0.3 | Storage backend prod'da aktif (S3/minio/local persistent) | `PDF_STORAGE_BACKEND` env set |
| 0.4 | `PDF_ENV=production` doğrulanmış | Env var kontrolü |
| 0.5 | `CORS_ALLOWED_ORIGINS` prod'da kısıtlı (wildcard değil) | Env var kontrolü |
| 0.6 | `PDF_TEMPLATE_ALLOWLIST` prod'da set (boş değil) | Env var kontrolü — boş ise tüm template'ler açık |
| 0.7 | `ADMIN_API_KEY_ENABLED=true` ve `API_KEY_ENABLED=true` | Kill-switch admin API auth olmadan açık kalmamalı |
| 0.8 | DB schema uyumlu — migration çalıştırılmış | `alembic current` veya `init_db()` startup log kontrolü |
| 0.9 | Tüm testler yeşil (CI son run) | CI pipeline son commit'te pass |

---

### 1) Deploy Sırası

### 1.1 Monitoring önce

- [ ] Prometheus scrape targets yeni metrikleri görüyor:
  - `ptf_admin_api_request_total` (runtime)
  - `release_gate_decision_total` (release gate)
  - `ptf_admin_pdf_jobs_total` (PDF worker)
- [ ] Grafana dashboard'lar provision edildi (4 dashboard):
  - `ptf-admin-dashboard.json`
  - `pdf-worker-dashboard.json`
  - `preflight-dashboard.json`
  - `release-gate-dashboard.json`
- [ ] Alert rule'lar yüklendi — `ptf-admin-alerts.yml` 7 grup, "pending/firing" hatası yok
- [ ] Runbook erişilebilir — alert annotation'larındaki `runbook_url` linkleri çalışıyor

### 1.2 Worker önce (PDF)

- [ ] Worker up — `rq info` worker count ≥ 1
- [ ] Queue depth 0'a dönüyor — `ptf_admin_pdf_queue_depth` gauge çalışıyor
- [ ] Worker log'larında Playwright launch loop yok (crash-restart döngüsü)
- [ ] Artifact TTL cleanup cron aktif — en az 1 kez çalıştığı gözlemlendi

### 1.3 API sonra

- [ ] Backend deploy — pod'lar Running, restart count 0
- [ ] `/health/ready` → 200 (config, DB, OpenAI, queue kontrolü geçiyor)
- [ ] `/metrics` → 200, `ptf_admin_` prefix'li metrikler mevcut
- [ ] Frontend deploy — CORS hataları yok, telemetry endpoint'e ulaşılıyor

---

### 2) Canary / Smoke Test (Prod)

### 2.1 Runtime Guard

```bash
# Baseline: guard decision layer kapalı
# OPS_GUARD_DECISION_LAYER_ENABLED=false
curl -s https://<host>/health/ready | jq .status  # "ok"
curl -s https://<host>/metrics | grep guard_decision_requests_total
# → metrik yok veya 0 (beklenen)
```

- [ ] `OPS_GUARD_DECISION_LAYER_ENABLED=false` ile baseline doğrulandı — 503 yok
- [ ] `enabled=true` + `default_mode=shadow` ile geçiş yapıldı
- [ ] `guard_decision_requests_total{mode="shadow"}` artıyor
- [ ] 503 yok (shadow modda block olmamalı)
- [ ] Endpoint-class policy: high-risk endpoint'ler sınıflandırılmış, shadow davranışı beklenen

### 2.2 Release Gate Telemetry

- [ ] `release_gate_decision_total` artıyor (allow/deny)
- [ ] `release_gate_contract_breach_total` = 0 (normal operasyonda)
- [ ] `release_gate_audit_write_failures_total` = 0 (normal operasyonda)

### 2.3 PDF Worker

```bash
# Job oluştur
JOB=$(curl -s -X POST https://<host>/pdf/jobs \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: <key>" \
  -d '{"template_name":"<allowed_template>","payload":{}}' | jq -r .job_id)

# Status kontrol (queued → running → succeeded)
curl -s https://<host>/pdf/jobs/$JOB | jq .status

# Download
curl -s -o test.pdf https://<host>/pdf/jobs/$JOB/download
# PDF açılıyor mu kontrol et
```

- [ ] `POST /pdf/jobs` → 202 + job_id
- [ ] `GET /pdf/jobs/{id}` → queued → running → succeeded
- [ ] `GET /pdf/jobs/{id}/download` → 200, PDF açılıyor
- [ ] `ptf_admin_pdf_jobs_total{status="succeeded"}` artıyor
- [ ] `ptf_admin_pdf_job_duration_seconds` histogram doluyor

### 2.4 Frontend Telemetry

- [ ] Frontend'den `POST /admin/telemetry/events` başarılı (CORS hatası yok)
- [ ] `ptf_admin_frontend_events_total` artıyor
- [ ] Rate limit çalışıyor — 60 req/min/IP aşıldığında 429

---

### 3) Rollout Gates (Ne Zaman İlerlenir?)

### Shadow → Enforce (Runtime Guard)

| Gate | Kriter | Doğrulama |
|------|--------|-----------|
| False positive yok | `block_total{mode="shadow"}` beklenen seviyede | Grafana panel inceleme |
| Build failure ~0 | `snapshot_build_failures_total` ≈ 0 | `/metrics` grep |
| Silent alert yok | `PTFAdminGuardDecisionSilent` firing değil | Prometheus alerts UI |
| Yeterli veri | En az 24 saat shadow data | Zaman kontrolü |

Enforce'a geçiş: tenant/endpoint sınırlı başla (mümkünse), rollback hazır tut.

### PDF Scale

| Gate | Kriter | Doğrulama |
|------|--------|-----------|
| p95 duration stabil | `pdf_job_duration_seconds` p95 < SLO | Grafana panel |
| Queue depth alarm yok | `PTFAdminPdfQueueBacklog` firing değil | Prometheus alerts UI |
| Failure spike yok | `PTFAdminPdfFailureSpike` firing değil | Prometheus alerts UI |
| Worker health | Restart count 0, log'da crash yok | `kubectl get pods` + logs |

---

### 4) Rollback Prosedürü

| Bileşen | Rollback Komutu | Etki |
|---------|----------------|------|
| Runtime Guard | `OPS_GUARD_DECISION_LAYER_ENABLED=false` → redeploy | Guard decision layer devre dışı, mevcut guard chain çalışmaya devam eder |
| PDF Worker | Worker scale 0 + API `/pdf` endpoint'lerini kill-switch ile kapat | Yeni job kabul edilmez, mevcut queue drain olur |
| Release Gate Telemetry | Alert rule disable (core alert'lere dokunma) | Telemetri toplanmaya devam eder, alert'ler sessizleşir |
| Full rollback | `kubectl rollout undo deployment/ptf-admin` | Önceki versiyona dön |

**Bilinen sınırlama:** DB schema değişikliği içeren deploy'larda `rollout undo` yetmez — schema rollback ayrıca planlanmalıdır. Mevcut sistemde `init_db()` additive schema kullanır (yeni tablo/kolon ekler, silmez), bu nedenle çoğu durumda geriye uyumludur.

---

### 5) Post-Deploy Doğrulama (İlk 24 Saat)

- [ ] SLO burn-rate alert'leri firing değil (`PTFAdminSLOBurnRateFast`, `PTFAdminSLOBurnRateSlow`)
- [ ] Error rate baseline'da — `ptf_admin_api_request_total{status_class="5xx"}` rate < %1
- [ ] Guard fail-open counter artmıyor — `ptf_admin_guard_failopen_total` stabil
- [ ] PDF queue depth stabil — `ptf_admin_pdf_queue_depth` < 10 (normal operasyonda)
- [ ] Kill-switch'ler pasif — `ptf_admin_killswitch_state` tüm switch'ler 0
- [ ] Circuit breaker'lar kapalı — `ptf_admin_circuit_breaker_state` tüm dependency'ler 0 (closed)


---

# Yük Testi Planı

Bu plan, PTF Admin sisteminin üç kritik hattını (PDF worker, runtime guard, release gate telemetry) stres altında doğrular. Tek sprintte koşulabilir. Ağırlık PDF hattındadır — en pahalı ve en çok sürpriz çıkaran bileşen.

---

### Hedefler (Ölçülebilir)

| Hat | Metrik | Hedef |
|-----|--------|-------|
| PDF | p95 render süresi | Baseline'da ölçülecek, sonraki senaryolarda regresyon yok |
| PDF | Queue backlog davranışı | Burst sonrası drain, monoton artış yok |
| PDF | Retry oranı | Transient hatalarda bounded (max 2), permanent'ta 0 |
| API | 99p latency (guard açık/kapalı) | Shadow'da baseline'dan < %10 sapma |
| API | Error rate (guard açık/kapalı) | Shadow'da 0 ek hata, enforce'da sadece beklenen sınıfta |
| Stabilite | Memory leak | `process_resident_memory_bytes` senaryo başı/sonu < %20 artış |
| Stabilite | Worker stuck | RUNNING > 5 dk olan job yok |
| Stabilite | TTL cleanup | `cleanup_expired()` çağrısı sonrası expired count > 0 |
| Observability | Alert eşikleri | Beklenen alert'ler firing, beklenmeyen'ler silent |

---

### Test Katmanları

Plan iki katmanda koşar. Katmanlar birbirini dışlamaz.

| Katman | Araç | Ortam | Kapsam |
|--------|------|-------|--------|
| Katman 1: In-process | Mevcut `load_harness.py` + `scenario_runner.py` | CI (mock'lu) | Guard, dependency, retry policy, write-path güvenliği |
| Katman 2: HTTP E2E | k6 | Staging (gerçek Redis + Playwright) | PDF end-to-end, queue davranışı, alert doğrulaması |

Rapor formatı:
- Katman 1 → mevcut `StressReport` dataclass (programatik, CI assertion'lı)
- Katman 2 → k6 JSON summary + Grafana annotation (görsel, manuel review)

---

### İzlenecek Paneller

| Dashboard | Panel | Senaryo |
|-----------|-------|---------|
| PDF Worker | Status / Failures / Duration / Queue Depth | S1, S2, S3 |
| PTF Admin Overview | API Traffic & Health, Guard Decision Layer | S4, S5 |
| Release Gate | Decision Total, Breach Counter | S4 |
| Dependency Health | Call Rate by Outcome, P95 Latency | S5 |

Log sampling: worker + api (error only, `level=ERROR` filter).

---

### Senaryo 1 — PDF "Steady-State" (Baseline)

**Katman:** 2 (k6 HTTP)
**Amaç:** Nominal yükte p95 ve hata oranını ölç. İlk çalıştırma baseline olur.

| Parametre | Değer |
|-----------|-------|
| Süre | 20 dk |
| Yük | 2 job/dk → 5 job/dk → 10 job/dk (5'er dk ramp) |
| Template | 1–2 tip (allowlist içinden) |
| Payload | Tipik boyut (20–50 KB) |

**Başarı kriterleri:**

| # | Kriter | Metrik | Eşik |
|---|--------|--------|------|
| S1.1 | Failure rate düşük | `ptf_admin_pdf_jobs_total{status="failed"} / total` | < %2 |
| S1.2 | p95 duration baseline | `ptf_admin_pdf_job_duration_seconds` p95 | İlk ölçümde kaydet |
| S1.3 | Queue depth stabil | `ptf_admin_pdf_queue_depth` | < 10 (steady-state) |
| S1.4 | Memory stabil | `process_resident_memory_bytes` delta | < %20 artış |
| S1.5 | TTL cleanup çalışıyor | Senaryo sonrası `cleanup_expired()` | expired count > 0 |

---

### Senaryo 2 — PDF "Burst / Backlog"

**Katman:** 2 (k6 HTTP)
**Amaç:** Queue davranışı + backlog alert doğrulaması.

| Parametre | Değer |
|-----------|-------|
| Burst | 200 job / 60 sn |
| Drain | 10 dk "no new jobs" |
| Toplam süre | ~12 dk |

> **Not:** Burst boyutu 200 (100 değil) — `PTFAdminPdfQueueBacklog` alert'i `for: 10m` olduğu için yeterli backlog oluşması gerekir. Alternatif: test ortamında `for` süresini 2 dk'ya kısalt.

**Başarı kriterleri:**

| # | Kriter | Metrik | Eşik |
|---|--------|--------|------|
| S2.1 | Queue yükselir ve drain olur | `ptf_admin_pdf_queue_depth` | Monoton değil, düşmeli |
| S2.2 | Backlog alert firing | `PTFAdminPdfQueueBacklog` | pending/firing olmalı |
| S2.3 | Worker stuck yok | RUNNING job'lar | Hepsi 5 dk içinde tamamlanıyor |
| S2.4 | Memory stabil | `process_resident_memory_bytes` delta | < %20 artış |

**Worker stuck detection yöntemi:**
```promql
# 5 dk'dan uzun RUNNING kalan job var mı?
# Job store'dan: status=running AND started_at < now()-300
# k6 script'inde: poll loop 5 dk timeout, aşarsa FAIL flag
```

---

### Senaryo 3 — PDF "Retry Injection"

**Katman:** 2 (k6 HTTP)
**Amaç:** Retry policy + failure taxonomy doğrulaması.

**Fault injection mekanizması:**
Mevcut PDF worker'da test-mode flag yok ve `load-characterization` R10 production kodu değişikliğini yasaklıyor. Bu nedenle "doğal hata" yaklaşımı kullanılır:

| Hata Türü | Simülasyon Yöntemi |
|-----------|-------------------|
| TEMPLATE_ERROR | Allowlist'te olmayan template gönder → 403 (API seviyesi, worker'a ulaşmaz) |
| NAVIGATION_TIMEOUT | Kasıtlı olarak ağır template (infinite loop JS, büyük DOM) → worker timeout |
| BROWSER_LAUNCH_FAILED | Worker'ı resource-constrained ortamda çalıştır (memory limit) |

Alternatif (R10 kısıtı gevşetilirse): `PDF_FAULT_INJECTION_RATE=0.2` env var'ı ile worker'da %20 oranla simüle.

| Parametre | Değer |
|-----------|-------|
| Süre | 15 dk |
| Yük | 5 job/dk |
| Hata oranı | ~%20 (ağır template mix ile) |

**Başarı kriterleri:**

| # | Kriter | Metrik | Eşik |
|---|--------|--------|------|
| S3.1 | Failure counter artıyor | `ptf_admin_pdf_job_failures_total{error_code="..."}` | > 0 |
| S3.2 | Retry bounded | `retry_count` per job | max 2 (transient), 0 (permanent) |
| S3.3 | Job'lar terminal state'e ulaşıyor | Tüm job'lar | SUCCEEDED veya FAILED (stuck yok) |
| S3.4 | Failure spike alert | `PTFAdminPdfFailureSpike` | Eşik aşılınca firing, altında silent |

---

### Senaryo 4 — API + Guard (Shadow vs Enforce)

**Katman:** 1 (in-process, mevcut harness) + 2 (k6 HTTP, staging)

**Amaç:** Guard açıkken latency/error değişimi.

**Endpoint mix (explicit):**

| Endpoint | Oran | Risk Class | Tip |
|----------|------|------------|-----|
| `GET /admin/market-prices` | %40 | low | read |
| `GET /admin/market-prices/{id}/history` | %20 | low | read |
| `POST /admin/market-prices` | %15 | medium | write |
| `POST /admin/market-prices/import` | %5 | high | write |
| `POST /pdf/jobs` | %10 | medium | write |
| `GET /pdf/jobs/{id}` | %5 | low | read |
| `POST /admin/telemetry/events` | %5 | — (skip path) | write |

**4A) Guard OFF baseline**

| Parametre | Değer |
|-----------|-------|
| Süre | 10 dk |
| RPS | 20 (sabit) |
| Guard | `OPS_GUARD_DECISION_LAYER_ENABLED=false` |

**4B) Guard SHADOW**

| Parametre | Değer |
|-----------|-------|
| Süre | 10 dk |
| RPS | 20 (sabit, aynı mix) |
| Guard | `enabled=true`, `default_mode=shadow` |

**4C) Guard ENFORCE (sınırlı)**

| Parametre | Değer |
|-----------|-------|
| Süre | 10 dk |
| RPS | 20 (sabit, aynı mix) |
| Guard | `enabled=true`, high-risk endpoint'lere enforce, diğerleri shadow |

**Başarı kriterleri:**

| # | Kriter | Metrik | Eşik |
|---|--------|--------|------|
| S4.1 | Shadow'da error rate değişmez | 4A vs 4B error rate delta | < %1 |
| S4.2 | Shadow'da 503 yok | `status_class="5xx"` (guard kaynaklı) | 0 |
| S4.3 | Decision counter artıyor | `guard_decision_requests_total{mode="shadow"}` | > 0 |
| S4.4 | Enforce'da 503 sadece beklenen sınıfta | `block_total{kind, mode, risk_class}` | Sadece high-risk'te |
| S4.5 | Risk class kırılımı anlamlı | `sum by (risk_class, mode)` | 3 risk_class × 2 mode |
| S4.6 | Telemetry endpoint çalışıyor | `ptf_admin_frontend_events_total` | > 0 (CORS hatası yok) |

---

### Senaryo 5 — "Dependency Outage" (Ops Guard)

**Katman:** 1 (in-process, mevcut harness + FaultInjector)

**Amaç:** Circuit open / rate limit bypass doğrulaması.

**5A) Circuit breaker tetikleme**

- Downstream'i "fail" ettir (FaultInjector + StubServer)
- `ptf_admin_circuit_breaker_state` → 2 (OPEN)
- `PTFAdminCircuitBreakerOpen` alert firing

**5B) Rate limiter tetikleme**

- Yükü kısa süre artır (burst RPS)
- `ptf_admin_rate_limit_total{decision="deny"}` artıyor
- 429 + `Retry-After` header doğrulaması

**Başarı kriterleri:**

| # | Kriter | Metrik | Eşik |
|---|--------|--------|------|
| S5.1 | 429 semantiği korunur | Response status + `Retry-After` header | Header mevcut |
| S5.2 | OpsGuard deny path'te decision layer bypass | `guard_decision_requests_total` | Artmamalı (deny path'te) |
| S5.3 | CB open alert firing | `PTFAdminCircuitBreakerOpen` | firing |
| S5.4 | Rate limit alert firing | `PTFAdminRateLimitSpike` | firing |
| S5.5 | Fail-open counter stabil | `ptf_admin_guard_failopen_total` | Artmamalı (normal operasyonda) |

---

### Koşma Sırası ve Öneri

| Sıra | Senaryo | Öncelik | Gerekçe |
|------|---------|---------|---------|
| 1 | S1 (PDF steady) | Kritik | Baseline olmadan diğer senaryolar anlamsız |
| 2 | S2 (PDF burst) | Kritik | Queue davranışı + alert doğrulaması |
| 3 | S3 (PDF retry) | Yüksek | Failure taxonomy + retry policy |
| 4 | S4 (Guard shadow/enforce) | Yüksek | Latency impact + risk class doğrulaması |
| 5 | S5 (Dependency outage) | Orta | CB/rate limit — mevcut harness testleri zaten kapsıyor |

S1 + S2 temizse guard tarafını koşmak kolay. S5 büyük ölçüde mevcut `test_lc_failure_matrix.py` ve `test_lc_cb_lifecycle.py` testleri ile kapsanıyor — staging'de tekrar doğrulama niteliğinde.

---

### Rapor Şablonu (Her Senaryo İçin)

| Alan | Açıklama |
|------|----------|
| Senaryo | S1 / S2 / S3 / S4 / S5 |
| RPS / Job Rate | Gerçekleşen değer |
| p50 / p95 Duration | ms cinsinden |
| Queue Depth Max | Burst senaryolarında |
| Time-to-Drain | Queue depth 0'a dönme süresi |
| Failure Breakdown | `error_code` bazında sayılar |
| Retry Oranı | `retry_count / total_jobs` |
| Memory Delta | `process_resident_memory_bytes` başlangıç → bitiş |
| Firing Alert Listesi | Beklenen / Beklenmeyen ayrımı |
| Stuck Job Count | RUNNING > 5 dk |
| TTL Cleanup Count | `cleanup_expired()` sonucu |


---

# Yük Testi Sonuç Raporu Şablonu

Bu şablon, yük testi planındaki senaryoların (S1–S5) koşum sonuçlarını tek sayfada özetler. Üstte go/no-go kararı, altta metrik ve alert kanıtları. Her koşumda bu şablon kopyalanıp doldurulur.

---

### Koşum Bilgileri

| Alan | Değer |
|------|-------|
| Run ID | `<YYYYMMDD-HHMM>` |
| Ortam | staging / prod-like |
| Commit / Versiyon | `<git sha / tag>` |
| Test Penceresi | `<başlangıç>` — `<bitiş>` (TZ: Europe/Istanbul) |
| Instance'lar | API=`<n>`, RQ Worker=`<n>`, Redis=`<single/cluster>` |

### Bayraklar (Koşum Anındaki Konfigürasyon)

| Flag | Değer |
|------|-------|
| Guard Decision Layer | `enabled=<true/false>`, `default_mode=<off/shadow/enforce>` |
| Endpoint-class policy | `risk_map=<empty/non-empty>` |
| PDF | `PDF_ENV=production`, `ALLOWLIST=<set/missing>` |
| Admin Auth | `ADMIN_API_KEY_ENABLED=<true/false>` |

---

### Go / No-Go Kararı

| Alan | Değer |
|------|-------|
| Karar | ✅ GO / ❌ NO-GO |
| Gerekçe (tek satır) | `<ör: PDF p95 stabil, stuck yok, alert'ler temiz>` |

### Kabul Kontrolleri

- [ ] K6-1 steady: p95 < `<hedef>` ve fail rate < `<hedef>`
- [ ] K6-2 burst: backlog drain oluyor, PW3 beklenen şekilde firing/pending
- [ ] K6-3 retry: retry bounded (≤2), error taxonomy doğru
- [ ] S4 API mix: shadow'da 0 block; enforce'da sadece beklenen risk_class'ta block
- [ ] Worker stuck yok; memory artışı < %20
- [ ] TTL cleanup doğrulandı

---

### PDF Metrikleri (k6 — S1, S2, S3)

| Senaryo | Yük | p50 | p95 | Fail % | Queue Depth Max | Drain Süresi | Retry/Job | Notlar |
|---------|-----|-----|-----|--------|-----------------|--------------|-----------|--------|
| K6-1 Steady | `<job/dk>` | `<ms>` | `<ms>` | `<%>` | `<n>` | n/a | `<x>` | |
| K6-2 Burst | 200/60s | `<ms>` | `<ms>` | `<%>` | `<n>` | `<mm:ss>` | `<x>` | |
| K6-3 Retry | `<job/dk>` | `<ms>` | `<ms>` | `<%>` | `<n>` | n/a | `<x>` | |

### PDF PromQL Kanıtları

```promql
# Fail count (15 dk pencere)
sum(increase(ptf_admin_pdf_jobs_total{status="failed"}[15m])) = <n>

# Queue depth max
max_over_time(ptf_admin_pdf_queue_depth[15m]) = <n>

# p95 duration
histogram_quantile(0.95, sum(rate(ptf_admin_pdf_job_duration_seconds_bucket[15m])) by (le)) = <s>
```

---

### API / Guard Metrikleri (k6 — S4)

| Faz | RPS | p95 Latency | Error % | Block (shadow/enforce) | Notlar |
|-----|-----|-------------|---------|------------------------|--------|
| S4A Guard OFF | `<rps>` | `<ms>` | `<%>` | n/a | |
| S4B Shadow | `<rps>` | `<ms>` | `<%>` | shadow=`<n>` | |
| S4C Enforce | `<rps>` | `<ms>` | `<%>` | enforce=`<n>` | |

### Guard PromQL Kanıtları

```promql
# Decision counter (mode × risk_class kırılımı)
sum by (mode, risk_class) (increase(ptf_admin_guard_decision_requests_total[15m]))

# Block counter (kind × mode × risk_class kırılımı)
sum by (mode, risk_class, kind) (increase(ptf_admin_guard_decision_block_total[15m]))
```

---

### Alert Gözlemleri (Beklenen vs Beklenmeyen)

| Alert | Beklenen? | Fired? | Severity | Süre | Notlar |
|-------|-----------|--------|----------|------|--------|
| PTFAdminPdfQueueUnavailable | Hayır | Evet/Hayır | warn | `<mm:ss>` | |
| PTFAdminPdfFailureSpike | Sadece K6-3 | Evet/Hayır | warn | `<mm:ss>` | |
| PTFAdminPdfQueueBacklog | Evet (K6-2) | Evet/Hayır | warn | `<mm:ss>` | |
| PTFAdminGuardDecisionSilent | Hayır | Evet/Hayır | warn | `<mm:ss>` | |
| PTFAdminReleaseGateContractBreach | Hayır | Evet/Hayır | high | `<mm:ss>` | |
| PTFAdminSLOBurnRateFast | Hayır | Evet/Hayır | critical | `<mm:ss>` | |
| PTFAdminSLOBurnRateSlow | Hayır | Evet/Hayır | warn | `<mm:ss>` | |
| PTFAdminCircuitBreakerOpen | Sadece S5 | Evet/Hayır | warn | `<mm:ss>` | |
| PTFAdminRateLimitSpike | Sadece S5 | Evet/Hayır | warn | `<mm:ss>` | |

---

### Güvenilirlik Kontrolleri

### Worker Stuck Detection

| Alan | Değer |
|------|-------|
| Tanım | Job RUNNING > 5 dk VEYA RUNNING count hiç düşmüyor |
| Sonuç | ✅ Pass / ❌ Fail |
| Kanıt | k6 poll loop: stuck count = `<n>` |

### Memory Leak Kontrolü

| Bileşen | Başlangıç | Bitiş | Delta (%) | Sonuç |
|---------|-----------|-------|-----------|-------|
| API | `<MB>` | `<MB>` | `<%>` | ✅ / ❌ |
| Worker | `<MB>` | `<MB>` | `<%>` | ✅ / ❌ |

Eşik: > %20 artış = ❌ Fail

### TTL Cleanup Doğrulaması

| Alan | Değer |
|------|-------|
| Expired job sayısı | `<n>` |
| Silinen artifact sayısı | `<n>` |
| Cleanup hata sayısı | `<n>` (`artifact_cleanup_failures_total`) |
| Sonuç | ✅ Pass / ❌ Fail |

---

### Ekler ve Bağlantılar

| Kaynak | Konum |
|--------|-------|
| k6 summary JSON | `<path>` |
| Grafana snapshot'ları | `<link veya snapshot id>` |
| StressReport (in-process) | `<path>` |
| İlgili loglar (error-only) | `<path>` |

---

### Aksiyonlar (Sadece Gerekirse)

| # | Aksiyon | Sahip | Tarih |
|---|---------|-------|-------|
| 1 | `<tuning item>` | `<isim>` | `<tarih>` |
| 2 | `<tuning item>` | `<isim>` | `<tarih>` |

---

### Kontrol Notları

- [ ] Template allowlist prod'da zorunlu
- [ ] Enqueue fail semantics (QUEUE_UNAVAILABLE) doğru çalışıyor
- [ ] Risk map empty ⇒ enforce tenant'ta shadow'a düşüyor
- [ ] CORS + frontend telemetry smoke test geçti
