# Gereksinimler Dokümanı — Ops-Guard

## Giriş

Ops-Guard, PTF Admin sisteminin production ortamında güvenli çalışmasını sağlayan operasyonel koruma katmanıdır. SLO tanımları, yüksek sinyalli alert seti, kill-switch mekanizması, rate limiting / circuit breaker politikaları, runbook ve dashboard bileşenlerinden oluşur. Mevcut telemetry-unification, observability-pack ve deploy-integration altyapısı üzerine inşa edilir; mevcut 263 testi ve prod kodunu bozmadan yeni koruma katmanları ekler.

## Sözlük

- **SLO (Service Level Objective)**: Servis kalite hedefi; ölçülebilir ve alert'e bağlanabilir metrik eşikleri
- **SLI (Service Level Indicator)**: SLO'yu besleyen ham metrik (ör. 5xx oranı, p95 latency)
- **Kill_Switch**: Belirli işlevleri (bulk import, write path) anında devre dışı bırakan kontrol mekanizması
- **Circuit_Breaker**: Downstream bağımlılık hata oranı eşiği aşıldığında istekleri hızlıca reddeden koruma deseni
- **Rate_Limiter**: Endpoint bazlı istek hızı sınırlayıcı
- **PTFMetrics**: `backend/app/ptf_metrics.py` içindeki mevcut Prometheus metrik sınıfı (`ptf_admin_*` namespace)
- **Guard_Config**: Kill-switch, rate limit ve circuit breaker ayarlarını tutan yapılandırma nesnesi
- **Degrade_Mode**: Write path kapatılıp sadece read işlemlerine izin verilen kontrollü bozulma modu
- **Golden_Signals**: Latency, traffic, errors, saturation — dört temel gözlemlenebilirlik sinyali
- **Sentinel_Metric**: "İmkansız durum" sayacı; sıfırdan farklı olması hata göstergesi

## Gereksinimler

### Gereksinim 1: SLO Tanımları ve SLI Metrikleri

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, ölçülebilir ve alert'e bağlanabilir SLO'lar tanımlamak istiyorum, böylece servis kalitesini nesnel olarak izleyebilirim.

#### Kabul Kriterleri

1. THE Guard_Config SHALL tanımlanan her SLO için hedef eşik değerlerini (availability ≥ 99.5%, p95 latency < 300ms, p99 latency < 800ms) yapılandırılabilir olarak barındırmak
2. WHEN marketPrices API'ye istek geldiğinde, THE SLI_Calculator SHALL başarı oranını hesaplarken 2xx yanıtları ve beklenen 4xx yanıtları (validation reject) başarılı saymak, yalnızca 5xx yanıtları başarısız saymak
3. WHEN bulk import işlemi tamamlandığında, THE SLI_Calculator SHALL iş tamamlanma süresini (p95), satır hata oranını (rejected / total) ve kuyruk derinliğini SLI olarak kaydetmek
4. THE PTFMetrics SHALL `ptf_admin_slo_violation_total{slo_name}` sayacını her SLO ihlalinde artırmak
5. THE PTFMetrics SHALL `ptf_admin_sentinel_impossible_state_total` sayacını error mapping veya validation "imkansız durum" oluştuğunda artırmak; bu sayacın normal operasyonda sıfır kalması beklenir

### Gereksinim 2: Alert Seti (P0 / P1 / P2)

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, az sayıda ama yüksek sinyalli alert'ler istiyorum, böylece gerçek sorunlara hızlı müdahale edebilir ve alert yorgunluğundan kaçınabilirim.

#### Kabul Kriterleri

1. WHEN 5xx oranı 5 dakikalık pencerede eşik değerini (%5) aştığında, THE Alert_Manager SHALL P0 seviyesinde "5xx Spike" alert'i tetiklemek
2. WHEN p99 latency 5 dakikalık pencerede eşik değerini (800ms) aştığında, THE Alert_Manager SHALL P0 seviyesinde "Latency Spike" alert'i tetiklemek
3. WHEN bir bulk import işi `max_import_duration` süresini aştığında, THE Alert_Manager SHALL P0 seviyesinde "Import Stuck" alert'i tetiklemek
4. WHEN kuyruk derinliği eşiği aşıp pozitif büyüme trendi gösterdiğinde, THE Alert_Manager SHALL P0 seviyesinde "Queue Backlog Runaway" alert'i tetiklemek
5. WHEN validation reject oranı baseline'ın 3 katını aştığında, THE Alert_Manager SHALL P1 seviyesinde "Validation Reject Spike" alert'i tetiklemek
6. WHEN rate limit tetiklenme sayısı eşiği aştığında, THE Alert_Manager SHALL P1 seviyesinde "Rate Limit Activated" alert'i tetiklemek
7. WHEN medyan payload boyutu belirgin artış gösterdiğinde, THE Alert_Manager SHALL P2 seviyesinde "Payload Size Drift" alert'i tetiklemek
8. WHEN belirli bir client/tenant yoğunlaşmış 4xx ürettiğinde, THE Alert_Manager SHALL P2 seviyesinde "Client Error Skew" alert'i tetiklemek
9. THE Alert_Manager SHALL her alert kuralını mevcut PrometheusRule YAML dosyasına yeni bir grup olarak eklemek veya mevcut grubu genişletmek

### Gereksinim 3: Kill-Switch Mekanizması

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, sorun anında belirli işlevleri anında devre dışı bırakabilmek istiyorum, böylece hasarı sınırlayıp sistemi kontrollü şekilde koruyabilirim.

#### Kabul Kriterleri

1. WHEN admin kullanıcı global kill-switch'i aktifleştirdiğinde, THE Kill_Switch_Manager SHALL bulk import endpoint'lerini devre dışı bırakıp HTTP 503 ile "servis geçici olarak durduruldu" mesajı döndürmek
2. WHEN admin kullanıcı belirli bir tenant için kill-switch'i aktifleştirdiğinde, THE Kill_Switch_Manager SHALL yalnızca o tenant'ın bulk import ve pahalı endpoint isteklerini reddetmek
3. WHEN admin kullanıcı degrade mode'u aktifleştirdiğinde, THE Kill_Switch_Manager SHALL write path'i kapatıp yalnızca read endpoint'lerine izin vermek
4. WHILE degrade mode aktifken bulk import isteği geldiğinde, THE Kill_Switch_Manager SHALL isteği kabul edip "kuyrukta ama duraklatıldı" operasyonel mesajı döndürmek
5. THE PTFMetrics SHALL `ptf_admin_killswitch_state{switch_name}` gauge metriğini kill-switch durumu değiştiğinde güncellemek (1 = aktif, 0 = pasif)
6. WHEN kill-switch durumu değiştiğinde, THE Kill_Switch_Manager SHALL değişikliği audit log'a kaydetmek (kim, ne zaman, hangi switch, önceki/sonraki durum)
7. THE Kill_Switch_Manager SHALL kill-switch durumunu `GET /admin/ops/kill-switches` endpoint'i üzerinden sorgulanabilir yapmak; bu endpoint `require_admin_key()` ile korunmak
8. WHEN admin kullanıcı kill-switch durumunu değiştirmek istediğinde, THE Kill_Switch_Manager SHALL `PUT /admin/ops/kill-switches/{switch_name}` endpoint'i üzerinden değişikliği kabul etmek; bu endpoint `require_admin_key()` ile korunmak

### Gereksinim 4: Rate Limiting ve Circuit Breaker Politikası

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, endpoint bazlı rate limiting ve downstream circuit breaker istiyorum, böylece aşırı yük ve bağımlılık hatalarından sistemi koruyabilirim.

#### Kabul Kriterleri

1. THE Rate_Limiter SHALL bulk import endpoint'i için ayrı, heavy read endpoint'leri için ayrı rate limit eşikleri uygulamak
2. WHEN bir endpoint'in rate limit'i aşıldığında, THE Rate_Limiter SHALL HTTP 429 yanıtı döndürmek ve `Retry-After` header'ı eklemek
3. THE Rate_Limiter SHALL rate limit kararlarında fail-closed politikası uygulamak (rate limiter hatası durumunda isteği reddetmek)
4. WHEN downstream bağımlılık hata oranı eşiği (%50) aştığında, THE Circuit_Breaker SHALL devre açık (open) durumuna geçip istekleri hızlıca reddetmek
5. WHILE circuit breaker açık durumdayken, THE Circuit_Breaker SHALL yapılandırılabilir süre sonunda yarı-açık (half-open) duruma geçip sınırlı sayıda deneme isteği göndermek
6. WHEN yarı-açık durumdaki deneme istekleri başarılı olduğunda, THE Circuit_Breaker SHALL devreyi kapalı (closed) duruma döndürmek
7. THE PTFMetrics SHALL `ptf_admin_rate_limit_total{endpoint, decision}` sayacını her rate limit kararında artırmak (decision: "allowed" | "rejected")
8. THE PTFMetrics SHALL `ptf_admin_circuit_breaker_state{dependency}` gauge metriğini circuit breaker durum değişikliğinde güncellemek (0=closed, 1=half-open, 2=open)

### Gereksinim 5: Operasyonel Runbook

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, her P0/P1 alert için yapılandırılmış müdahale adımları istiyorum, böylece olay anında hızlı ve tutarlı müdahale edebilirim.

#### Kabul Kriterleri

1. THE Runbook SHALL her P0 ve P1 alert için şu bölümleri içermek: Belirti (symptom), Hızlı Tanı Kontrol Listesi (dashboard linkleri, log sorgusu), Müdahale (kill-switch açma, rate limit ayarlama, import duraklatma), Kurtarma (backlog drain, retry stratejisi), Postmortem Verisi (hangi metriklerin kaydedileceği)
2. THE Runbook SHALL mevcut `monitoring/runbooks/ptf-admin-runbook.md` dosyasını genişletmek; mevcut alert runbook'larını bozmamak
3. THE Runbook SHALL her müdahale adımında ilgili kill-switch komutunu ve dashboard linkini referans vermek

### Gereksinim 6: Operasyonel Dashboard

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, tek bakışta sistem durumunu görebileceğim bir dashboard istiyorum, böylece golden signals, bulk import durumu ve hata taksonomisini anlık izleyebilirim.

#### Kabul Kriterleri

1. THE Dashboard SHALL golden signals (latency, traffic, errors, saturation) panellerini tek bir satırda göstermek
2. THE Dashboard SHALL bulk import panelini (kuyruk derinliği, iş süreleri, hata dağılımı) ayrı bir satırda göstermek
3. THE Dashboard SHALL hata taksonomisi panelini (hata kodları dağılımı, "unexpected" sınıfı vurgusu) ayrı bir satırda göstermek
4. THE Dashboard SHALL kill-switch ve circuit breaker durumlarını gösteren bir durum paneli içermek
5. THE Dashboard SHALL mevcut Grafana dashboard JSON dosyasını genişletmek veya yeni bir dashboard dosyası oluşturmak

### Gereksinim 7: Entegrasyon ve Güvenlik Kısıtları

**Kullanıcı Hikayesi:** Bir geliştirici olarak, yeni ops-guard bileşenlerinin mevcut sistemi bozmamasını ve güvenlik standartlarına uymasını istiyorum, böylece güvenle deploy edebilirim.

#### Kabul Kriterleri

1. THE Ops_Guard SHALL mevcut `ptf_admin_` metrik namespace'ini kullanmak; yeni namespace oluşturmamak
2. THE Ops_Guard SHALL mevcut 263 testi kırmamak; tüm yeni kodun geriye dönük uyumlu olması
3. THE Ops_Guard SHALL tüm yeni admin endpoint'lerini `require_admin_key()` dependency ile korumak
4. IF Guard_Config yüklenemezse, THEN THE Ops_Guard SHALL güvenli varsayılan değerlerle çalışmaya devam etmek ve uyarı logu yazmak
5. THE Guard_Config SHALL kill-switch, rate limit ve circuit breaker ayarlarını ortam değişkenleri veya yapılandırma dosyası üzerinden değiştirilebilir yapmak
