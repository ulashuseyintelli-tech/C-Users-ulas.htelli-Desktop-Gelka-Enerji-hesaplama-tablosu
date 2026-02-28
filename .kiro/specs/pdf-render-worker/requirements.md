# Gereksinimler Dokümanı — PDF Render Worker

## Giriş

PDF Render Worker, HTML template'lerden PDF üretimini API request path'inden izole eden asenkron iş kuyruğu sistemidir. Mevcut `pdf_playwright.py` (Playwright/Chromium) altyapısını kullanarak, PDF render işlemini ayrı bir worker process'te gerçekleştirir. Job yaşam döngüsü yönetimi (create → queued → running → succeeded/failed → expired), artifact depolama (local disk / S3), güvenlik (URL/template allowlist, SSRF koruması), gözlemlenebilirlik (Prometheus metrikleri) ve fail-safe politikaları (503 + degrade modu) içerir. Mevcut RQ + Redis altyapısı (`rq_adapter.py`, `rq_worker.py`) üzerine inşa edilir.

## Sözlük

- **PdfRenderWorker**: Playwright/Chromium kullanarak HTML → PDF dönüşümünü ayrı process'te gerçekleştiren worker bileşeni
- **PdfJobStore**: Redis üzerinde job durumu, payload hash ve zaman damgalarını tutan veri deposu
- **PdfArtifactStore**: Üretilen PDF dosyalarını saklayan depolama katmanı; dev ortamda local disk (`./artifacts/pdfs`), prod ortamda S3/MinIO
- **Job_Key**: Job payload'unun deterministik hash'i; idempotency kontrolü için kullanılır
- **Artifact_Key**: Üretilen PDF dosyasının depolama referansı (path veya S3 key)
- **Hard_Timeout**: Worker'ın bir job'ı işlemek için izin verilen maksimum süre (varsayılan 60 saniye)
- **Graceful_Cancel**: Hard timeout öncesinde worker'a gönderilen iptal sinyali
- **Transient_Failure**: Geçici hata; browser launch hatası veya navigation timeout gibi yeniden denenebilir hatalar
- **Error_Taxonomy**: Sınıflandırılmış hata kodları kümesi: BROWSER_LAUNCH_FAILED, NAVIGATION_TIMEOUT, TEMPLATE_ERROR, UNSUPPORTED_PLATFORM, UNKNOWN
- **Template_Allowlist**: Render edilmesine izin verilen HTML template'lerin beyaz listesi
- **URL_Allowlist**: Playwright'ın navigate edebileceği URL'lerin beyaz listesi
- **TTL_Cleanup**: Süresi dolan artifact ve job kayıtlarının otomatik temizlenmesi
- **PTFMetrics**: `backend/app/ptf_metrics.py` içindeki mevcut Prometheus metrik sınıfı (`ptf_admin_*` namespace)

## Gereksinimler

### Gereksinim 1: Asenkron İzolasyon

**Kullanıcı Hikayesi:** Bir geliştirici olarak, PDF render işleminin API request path'ini bloklamadan ayrı bir worker process'te çalışmasını istiyorum, böylece API yanıt süreleri PDF üretiminden etkilenmesin.

#### Kabul Kriterleri

1. WHEN bir PDF render isteği alındığında, THE PdfJobStore SHALL yeni bir job kaydı oluşturup job_id döndürmek; Playwright çağrısı API process'inde gerçekleşmemek
2. WHEN bir job kuyruğa eklendiğinde, THE PdfRenderWorker SHALL job'ı ayrı bir worker process'te Redis RQ üzerinden tüketmek
3. IF Redis bağlantısı kurulamıyorsa, THEN THE PdfJobStore SHALL job oluşturma isteğini HTTP 503 ile reddetmek ve `PDF_RENDER_UNAVAILABLE` hata kodu döndürmek

### Gereksinim 2: Job Yaşam Döngüsü

**Kullanıcı Hikayesi:** Bir geliştirici olarak, her PDF render job'unun net bir durum makinesine sahip olmasını istiyorum, böylece job'un hangi aşamada olduğunu her zaman bilebilirim.

#### Kabul Kriterleri

1. THE PdfJobStore SHALL her job için şu durumları desteklemek: `queued`, `running`, `succeeded`, `failed`, `expired`
2. WHEN bir job oluşturulduğunda, THE PdfJobStore SHALL job durumunu `queued` olarak ayarlamak ve `created_at` zaman damgasını kaydetmek
3. WHEN worker bir job'ı işlemeye başladığında, THE PdfJobStore SHALL job durumunu `running` olarak güncellemek ve `started_at` zaman damgasını kaydetmek
4. WHEN worker PDF üretimini başarıyla tamamladığında, THE PdfJobStore SHALL job durumunu `succeeded` olarak güncellemek, `artifact_key` referansını kaydetmek ve `finished_at` zaman damgasını kaydetmek
5. WHEN worker PDF üretiminde hata oluştuğunda, THE PdfJobStore SHALL job durumunu `failed` olarak güncellemek, Error_Taxonomy'den uygun `error_code` kaydetmek ve `finished_at` zaman damgasını kaydetmek
6. WHEN bir job'un TTL süresi dolduğunda, THE PdfJobStore SHALL job durumunu `expired` olarak güncellemek

### Gereksinim 3: Deterministik Çıktı

**Kullanıcı Hikayesi:** Bir geliştirici olarak, aynı HTML input ve aynı template versiyonu ile üretilen PDF'lerin mümkün olduğunca aynı byte çıktısını vermesini istiyorum, böylece cache ve idempotency mekanizmaları güvenilir çalışsın.

#### Kabul Kriterleri

1. THE PdfRenderWorker SHALL Playwright render ayarlarını sabit tutmak: `scale=1.0`, `prefer_css_page_size=True`, `margin=0`, `emulate_media="print"`
2. WHEN aynı HTML içeriği ve aynı Playwright ayarları ile render yapıldığında, THE PdfRenderWorker SHALL aynı PDF byte çıktısını üretmek (font ve sistem bağımlılıkları hariç)
3. THE PdfJobStore SHALL her job payload'u için deterministik bir hash (Job_Key) hesaplamak; bu hash HTML içeriği ve render parametrelerini kapsamak

### Gereksinim 4: Timeout Yönetimi

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, takılmış PDF render job'larının sistemi bloklamasını önlemek istiyorum, böylece worker kaynakları serbest kalıp diğer job'lar işlenebilsin.

#### Kabul Kriterleri

1. THE PdfRenderWorker SHALL her job için yapılandırılabilir bir Hard_Timeout uygulamak (varsayılan 60 saniye)
2. WHEN bir job Hard_Timeout süresini aştığında, THE PdfRenderWorker SHALL Playwright browser instance'ını kapatmak ve job'ı `NAVIGATION_TIMEOUT` hata koduyla `failed` olarak işaretlemek
3. THE PdfRenderWorker SHALL Hard_Timeout'tan önce Graceful_Cancel sinyali göndermek (varsayılan: Hard_Timeout - 5 saniye), böylece browser kaynakları temiz şekilde serbest bırakılabilmek

### Gereksinim 5: Yeniden Deneme (Retry) Politikası

**Kullanıcı Hikayesi:** Bir geliştirici olarak, geçici hatalar nedeniyle başarısız olan job'ların otomatik olarak yeniden denenmesini istiyorum, böylece geçici sorunlar kullanıcıya yansımasın.

#### Kabul Kriterleri

1. WHEN bir job `BROWSER_LAUNCH_FAILED` veya `NAVIGATION_TIMEOUT` hata koduyla başarısız olduğunda, THE PdfRenderWorker SHALL job'ı otomatik olarak yeniden kuyruğa eklemek
2. THE PdfRenderWorker SHALL maksimum yeniden deneme sayısını 2 ile sınırlamak; bu sayı aşıldığında job kalıcı olarak `failed` olarak işaretlenmek
3. WHEN bir job `TEMPLATE_ERROR` veya `UNSUPPORTED_PLATFORM` hata koduyla başarısız olduğunda, THE PdfRenderWorker SHALL job'ı yeniden denememek ve doğrudan kalıcı `failed` olarak işaretlemek
4. THE PdfJobStore SHALL her job için `retry_count` alanını tutmak ve her yeniden denemede artırmak

### Gereksinim 6: İdempotency

**Kullanıcı Hikayesi:** Bir geliştirici olarak, aynı PDF render isteğinin tekrar gönderilmesi durumunda yeni bir job oluşturulmamasını istiyorum, böylece gereksiz kaynak tüketimi önlensin.

#### Kabul Kriterleri

1. WHEN aynı Job_Key (payload hash) ile yeni bir job isteği geldiğinde ve mevcut job `queued` veya `running` durumundaysa, THE PdfJobStore SHALL yeni job oluşturmak yerine mevcut job'un job_id'sini döndürmek
2. WHEN aynı Job_Key ile yeni bir job isteği geldiğinde ve mevcut job `succeeded` durumundaysa ve artifact hâlâ mevcutsa, THE PdfJobStore SHALL mevcut job'un job_id'sini döndürmek
3. WHEN aynı Job_Key ile yeni bir job isteği geldiğinde ve mevcut job `failed` veya `expired` durumundaysa, THE PdfJobStore SHALL yeni bir job oluşturmak

### Gereksinim 7: Artifact Depolama ve TTL Temizliği

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, üretilen PDF dosyalarının güvenli şekilde saklanmasını ve süresi dolanların otomatik temizlenmesini istiyorum, böylece disk/S3 alanı kontrollü kullanılsın.

#### Kabul Kriterleri

1. THE PdfArtifactStore SHALL dev ortamda `./artifacts/pdfs/` dizinine, prod ortamda S3/MinIO bucket'ına PDF dosyalarını kaydetmek
2. THE PdfArtifactStore SHALL mevcut `StorageBackend` arayüzünü (`put_bytes`, `get_bytes`, `exists`, `delete`) kullanmak
3. WHEN bir artifact'ın TTL süresi dolduğunda, THE PdfArtifactStore SHALL artifact dosyasını silmek ve ilgili job kaydını `expired` olarak güncellemek
4. THE PdfArtifactStore SHALL TTL temizlik işlemini periyodik olarak çalıştırmak (varsayılan: her 1 saat)
5. THE PdfArtifactStore SHALL her artifact için benzersiz bir key üretmek; key formatı `pdfs/{job_id}/{timestamp}.pdf` olarak belirlemek

### Gereksinim 8: Güvenlik

**Kullanıcı Hikayesi:** Bir güvenlik mühendisi olarak, PDF render worker'ın yalnızca izin verilen template ve URL'leri işlemesini istiyorum, böylece SSRF saldırıları ve yetkisiz erişim önlensin.

#### Kabul Kriterleri

1. THE PdfRenderWorker SHALL render isteğindeki template adını Template_Allowlist'e karşı doğrulamak; listede olmayan template'leri `TEMPLATE_ERROR` hata koduyla reddetmek
2. ~~WHEN Playwright bir URL'ye navigate etmesi gerektiğinde, THE PdfRenderWorker SHALL URL'yi URL_Allowlist'e karşı doğrulamak; listede olmayan URL'leri reddetmek~~
   > **N/A — Design Drift (2026-02-27).** Render path URL navigation yapmıyor; `_render_in_child()` → `page.set_content(html, ...)` ile doğrudan HTML string render ediyor. Playwright hiçbir zaman harici URL'ye navigate etmiyor. SSRF riski bu mimari için geçerli değil. Evidence: `pdf_render_worker.py::_render_in_child` Line 68.
3. ~~THE PdfRenderWorker SHALL kullanıcı girdilerini HTML injection'a karşı sanitize etmek; template'e enjekte edilen değişkenleri escape etmek~~
   > **N/A — Design Drift (2026-02-27).** Payload, server-side template pipeline tarafından üretiliyor; kullanıcı girdisi doğrudan HTML'e enjekte edilmiyor. API katmanında `PDF_MAX_PAYLOAD_BYTES` size limit ve `PDF_TEMPLATE_ALLOWLIST` template kısıtlaması mevcut. Raw HTML injection vektörü yok. Evidence: `pdf_api.py::create_pdf_job()` — payload dict olarak alınıyor, template_name allowlist'e karşı doğrulanıyor; `pdf_render_worker.py::render_pdf_job()` — `html_renderer(template_name, payload)` callable ile HTML üretiliyor veya payload'dan `html` key'i okunuyor (internal use).
4. THE PdfRenderWorker SHALL Playwright browser'ı `--no-sandbox` olmadan çalıştırmak; sandbox modunu aktif tutmak
5. THE API SHALL PDF download endpoint'ini yetkilendirme kontrolü ile korumak; yalnızca job'u oluşturan kullanıcı veya admin erişebilmek

### Gereksinim 9: Gözlemlenebilirlik (Observability)

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, PDF render worker'ın performansını ve hata dağılımını izleyebilmek istiyorum, böylece sorunları proaktif olarak tespit edip müdahale edebileyim.

#### Kabul Kriterleri

1. THE PTFMetrics SHALL `ptf_admin_pdf_jobs_total{status}` sayacını her job durum değişikliğinde artırmak (status: queued, running, succeeded, failed, expired)
2. THE PTFMetrics SHALL `ptf_admin_pdf_render_total_seconds` histogram metriğini her istek sonrasında kaydetmek (e2e süre; SLO hedefi). Ayrıca `ptf_admin_pdf_render_executor_seconds` executor-internal süreyi kaydeder (kapasite tuning; SLO dışı)
3. THE PTFMetrics SHALL `ptf_admin_pdf_failures_total{error_code}` sayacını her başarısız job'da hata koduna göre artırmak
4. THE PTFMetrics SHALL `ptf_admin_pdf_queue_depth` gauge metriğini kuyruk derinliğini yansıtacak şekilde güncellemek
5. THE PTFMetrics SHALL mevcut `ptf_admin_*` namespace'ini kullanmak; yeni namespace oluşturmamak

### Gereksinim 10: Fail-Safe Politikası

**Kullanıcı Hikayesi:** Bir geliştirici olarak, worker down olduğunda API'nin net ve tutarlı bir hata yanıtı vermesini istiyorum, böylece istemci tarafında uygun fallback mekanizmaları devreye girebilsin.

#### Kabul Kriterleri

1. WHEN PdfRenderWorker erişilemez durumda olduğunda (Redis bağlantısı yok veya worker process down), THE API SHALL HTTP 503 yanıtı döndürmek ve `error_code: PDF_RENDER_UNAVAILABLE` mesajı içermek
2. ~~WHEN fail-safe politikası `degrade` modunda yapılandırıldığında ve worker erişilemez olduğunda, THE API SHALL PDF yerine render edilmiş HTML içeriğini döndürmek ve yanıta `X-Pdf-Fallback: html` header'ı eklemek~~
   > **Deferred — Strict-Only (2026-02-27).** MVP'de yalnızca `strict` modu implement edildi (503 döner). `degrade` modu (HTML fallback) MVP scope'unda değil; istemci tarafı zaten kendi HTML fallback'ini yönetiyor. Gerekirse gelecekte ayrı PR ile eklenebilir. Evidence: `pdf_api.py` — store/enqueue unavailable → 503; `FailSafePolicy` enum ve `pdf_failsafe.py` modülü oluşturulmadı.
3. ~~THE API SHALL fail-safe politikasını yapılandırılabilir yapmak: `strict` (sadece 503) veya `degrade` (HTML fallback)~~
   > **Deferred — Strict-Only (2026-02-27).** Yukarıdaki 10.2 ile aynı gerekçe. Yapılandırılabilir fail-safe politikası MVP scope'unda değil.
4. WHEN worker tekrar erişilebilir duruma geldiğinde, THE API SHALL otomatik olarak normal PDF render moduna dönmek

### Gereksinim 11: API Endpoint'leri

**Kullanıcı Hikayesi:** Bir geliştirici olarak, PDF render job'larını oluşturmak, durumlarını sorgulamak ve sonuçları indirmek için net API endpoint'leri istiyorum, böylece frontend ve diğer servisler entegre olabilsin.

#### Kabul Kriterleri

1. WHEN `POST /pdf/jobs` endpoint'ine geçerli bir payload gönderildiğinde, THE API SHALL yeni bir job oluşturup `{job_id, status: "queued"}` yanıtı döndürmek
2. WHEN `GET /pdf/jobs/{job_id}` endpoint'ine istek yapıldığında, THE API SHALL job'un güncel durumunu, oluşturulma zamanını ve (hazırsa) download URL'ini döndürmek
3. WHEN `GET /pdf/jobs/{job_id}/download` endpoint'ine istek yapıldığında ve job `succeeded` durumundaysa, THE API SHALL PDF byte'larını `application/pdf` content type ile döndürmek
4. WHEN `GET /pdf/jobs/{job_id}/download` endpoint'ine istek yapıldığında ve job henüz tamamlanmamışsa, THE API SHALL HTTP 202 yanıtı döndürmek ve `Retry-After` header'ı eklemek
5. IF geçersiz veya bulunamayan job_id ile istek yapılırsa, THEN THE API SHALL HTTP 404 yanıtı döndürmek
