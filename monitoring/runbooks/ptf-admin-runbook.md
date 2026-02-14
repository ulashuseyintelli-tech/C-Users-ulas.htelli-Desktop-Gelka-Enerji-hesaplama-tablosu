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
