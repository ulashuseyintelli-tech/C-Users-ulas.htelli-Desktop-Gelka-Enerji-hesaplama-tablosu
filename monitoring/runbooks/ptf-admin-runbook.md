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
