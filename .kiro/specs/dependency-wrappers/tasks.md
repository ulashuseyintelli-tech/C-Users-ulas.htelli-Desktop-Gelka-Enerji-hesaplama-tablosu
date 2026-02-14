# Implementasyon Planı: Dependency Wrappers

## Genel Bakış

Mevcut CircuitBreaker altyapısını istek yoluna bağlayan, endpoint→dependency eşlemesi oluşturan, timeout/retry/failure taxonomy standardizasyonu sağlayan ve middleware fail-open metriği ekleyen implementasyon planı.

Blok sıralaması "breakage riskini" minimize eder:
- Blok 1: Temel Contracts (config, metrik, taxonomy) — hiçbir davranış değişmez
- Blok 2: Mapping ve CB Pre-check — middleware'e flag ile kontrollü ekleme
- Blok 3: Wrapper implementasyonları — bağımsız, handler'a henüz bağlanmaz
- Blok 4: Wiring — handler'ların dependency çağrılarını wrapper'a geçir

## Kilit Tasarım Kararları (Blueprint'ten)

**DW-1: Retry sadece idempotent operasyonlarda (GET/read)**
Write/transaction path'lerde retry default kapalı. Aksi halde double-write riski.
Max retry küçük (1–2) ve jitter + exponential backoff.

**DW-2: CB Pre-check flag ile kontrollü**
`OPS_GUARD_CB_PRECHECK_ENABLED=true/false` (default: true).
Koşullu dependency seçimi olan endpoint'lerde pre-check yanlış deny üretebilir.
Flag kapalıyken sadece wrapper-level enforcement kalır.

**DW-3: Wrapper iç hatası → fail-open + metrik**
Wrapper'ın kendi kodu exception fırlatırsa: isteği engelleme, `ptf_admin_guard_failopen_total` artır, log yaz.
CB hatası ile wrapper iç hatası ayrı: CB OPEN → CircuitOpenError (beklenen), wrapper bug → fail-open (beklenmeyen).

**DW-4: Failure Taxonomy tek dosyada kilitli**
Tüm exception→CB failure sınıflandırması `failure_taxonomy.py`'de. Wrapper'lar kendi sınıflandırması yapmaz.

## Görevler

### Blok 1 — Temel Contracts (config, metrik, taxonomy)

- [x] 1. Guard Config genişletmesi — wrapper timeout, retry ve pre-check ayarları
  - [x] 1.1 GuardConfig'e yeni alanlar ekle
    - `backend/app/guard_config.py` → `GuardConfig` sınıfına:
      - `wrapper_timeout_db: float = 5.0`
      - `wrapper_timeout_external_api: float = 10.0`
      - `wrapper_timeout_cache: float = 2.0`
      - `wrapper_max_retries: int = 2` (toplam 3 deneme)
      - `wrapper_retry_base_delay: float = 0.5` (exponential backoff base)
      - `wrapper_retry_on_write: bool = False` (DW-1: write path'te retry kapalı)
      - `cb_precheck_enabled: bool = True` (DW-2: pre-check flag)
    - Ortam değişkeni prefix'i mevcut `OPS_GUARD_` kullanılacak
    - `load_guard_config()` fallback dict'ine yeni alanların varsayılanları eklenecek
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 1.2 Guard Config wrapper ayarları property testi yaz
    - `backend/tests/test_guard_config.py` dosyasını genişlet
    - **Property 8: Guard Config Wrapper Ayarları Round-Trip**
    - Env var set → GuardConfig doğru yüklüyor mu? Geçersiz değer → fallback mu?
    - `wrapper_retry_on_write=False` default doğrulaması
    - `cb_precheck_enabled=True` default doğrulaması
    - _Validates: Requirements 7.1, 7.2_

- [x] 2. PTFMetrics genişletmesi — dependency wrapper ve fail-open metrikleri
  - [x] 2.1 PTFMetrics'e yeni metrikler ekle
    - `backend/app/ptf_metrics.py` dosyasına ekle:
      - `ptf_admin_dependency_call_total{dependency, outcome}` Counter
        - outcome enum: `success` | `failure` | `timeout` | `circuit_open`
        - dependency: mevcut HD-5 enum (`db_primary`, `db_replica`, `cache`, `external_api`, `import_worker`)
      - `ptf_admin_dependency_call_duration_seconds{dependency}` Histogram
      - `ptf_admin_dependency_retry_total{dependency}` Counter
      - `ptf_admin_guard_failopen_total` Counter (label'sız — middleware + wrapper ortak)
    - Helper metodlar: `inc_dependency_call(dep, outcome)`, `observe_dependency_call_duration(dep, dur)`, `inc_dependency_retry(dep)`, `inc_guard_failopen()`
    - Kardinalite bütçesi: dependency(5) × outcome(4) = 20 max
    - _Requirements: 3.6, 3.7, 5.5, 6.1_

  - [x] 2.2 Metrik kayıt unit testleri yaz
    - `backend/tests/test_ptf_metrics.py` dosyasını genişlet
    - Her yeni metriğin doğru artışını test et
    - Geçersiz outcome/dependency değerlerinde davranış testi
    - _Requirements: 3.6, 3.7, 5.5, 6.1_

- [x] 3. Failure Taxonomy implementasyonu (DW-4: tek dosya, tek kaynak)
  - [x] 3.1 Failure taxonomy modülü oluştur
    - `backend/app/guards/failure_taxonomy.py` dosyası oluştur
    - `is_cb_failure(exc: Exception) -> bool`:
      - `True`: TimeoutError, ConnectionError, ConnectionRefusedError, OSError (socket), HTTP 5xx
      - `False`: HTTP 429, HTTP 4xx (429 hariç), ValueError, ValidationError
    - `is_cb_failure_status(status_code: int) -> bool`: 5xx → True, diğer → False
    - `is_retryable(exc: Exception) -> bool`: CB failure olan + idempotent kontrol wrapper'a bırakılır
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 3.2 Failure taxonomy property testi yaz
    - `backend/tests/test_failure_taxonomy.py` oluştur
    - **Property 5: Failure Taxonomy Sınıflandırma Tutarlılığı**
    - Hypothesis ile exception türleri + HTTP status code kombinasyonları
    - 429 → kesinlikle False, 5xx → kesinlikle True, 4xx → kesinlikle False
    - _Validates: Requirements 4.1, 4.2, 4.3, 4.4_

- [x] 4. Checkpoint — Blok 1 doğrulaması
  - Mevcut ~655 test kırılmadı
  - Yeni config alanları + metrikler + taxonomy testleri geçiyor
  - Henüz hiçbir production davranışı değişmedi

### Blok 2 — Mapping ve CB Pre-check

- [ ] 5. Endpoint Dependency Map implementasyonu
  - [ ] 5.1 Endpoint dependency map modülü oluştur
    - `backend/app/guards/endpoint_dependency_map.py` dosyası oluştur
    - `ENDPOINT_DEPENDENCY_MAP: dict[str, list[Dependency]]` statik dict
    - `get_dependencies(endpoint_template: str) -> list[Dependency]`
    - Bilinmeyen endpoint → boş liste (CB pre-check'ten muaf)
    - Yalnızca mevcut `Dependency` enum değerleri (HD-5)
    - Mapping sadece deterministik endpoint'ler için (koşullu dependency yok)
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [ ] 5.2 Endpoint dependency map property testi yaz
    - `backend/tests/test_endpoint_dependency_map.py` oluştur
    - **Property 1: Endpoint Dependency Map Geçerliliği**
    - Map'teki her değer geçerli Dependency enum üyesi
    - Map'te olmayan endpoint → boş liste
    - _Validates: Requirements 1.1, 1.2, 1.3_

- [ ] 6. Middleware CB pre-check entegrasyonu (DW-2: flag ile kontrollü)
  - [ ] 6.1 OpsGuardMiddleware'e CB pre-check ekle
    - `backend/app/ops_guard_middleware.py` → `_evaluate_guards()` içindeki yorum satırını aktifleştir
    - `_check_circuit_breaker(self, endpoint_template: str) -> Optional[GuardDenyReason]` metodu ekle
    - `cb_precheck_enabled` flag kontrolü: False ise → None dön (pre-check atla)
    - Endpoint_Dependency_Map'ten bağımlılıkları sorgula
    - Herhangi bir CB OPEN → `GuardDenyReason.CIRCUIT_OPEN`
    - CB pre-check iç hatası → fail-open (log + `ptf_admin_guard_failopen_total` artır + None dön)
    - HD-2 sırasına uygun: KS → RL → CB
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ] 6.2 Middleware fail-open metriği ekle
    - `dispatch()` catch-all bloğuna `ptf_admin_guard_failopen_total` artışı ekle
    - Mevcut log mesajı korunur, metrik eklenir
    - _Requirements: 6.1, 6.2_

  - [ ] 6.3 CB pre-check + fail-open testleri yaz
    - `backend/tests/test_ops_guard_middleware.py` dosyasını genişlet veya `backend/tests/test_cb_precheck.py` oluştur
    - **Property 2: CB Pre-Check Karar Doğruluğu** — herhangi bir dep OPEN → deny, tümü CLOSED/HALF_OPEN → allow
    - **Property 3: Guard Zinciri Sırası Korunması** — KS deny → CB çağrılmaz, RL deny → CB çağrılmaz
    - `cb_precheck_enabled=False` → pre-check atlanıyor mu?
    - CB pre-check iç hatası → fail-open + metrik artışı
    - _Validates: Requirements 2.1, 2.2, 2.3, 2.5, 6.1, 6.2_

- [ ] 7. Checkpoint — Blok 2 doğrulaması
  - Mevcut ~655 test kırılmadı
  - CB pre-check aktif (flag=True default)
  - Pre-check determinism: yanlış deny yok (bilinmeyen endpoint → boş liste → geç)
  - Fail-open metriği çalışıyor

### Blok 3 — Wrapper Implementasyonları

- [ ] 8. Dependency Wrapper base sınıf ve concrete wrapper'lar
  - [ ] 8.1 Dependency wrapper modülü oluştur
    - `backend/app/guards/dependency_wrapper.py` dosyası oluştur
    - `CircuitOpenError(Exception)` — CB OPEN durumunda fırlatılır
    - `DependencyWrapper` base sınıf:
      - `async call(fn, *args, **kwargs) -> T`
      - Akış: CB allow_request? → timeout → call → success/failure → retry?
      - Timeout: `asyncio.wait_for()` ile, config'den dependency-specific değer
      - Retry: DW-1 kuralı — `is_write` parametresi ile kontrol
        - `is_write=True` ve `wrapper_retry_on_write=False` → retry yapma
        - `is_write=False` → max_retries kadar retry, exponential backoff + jitter
      - CB integration: success → `record_success()`, CB failure → `record_failure()`
      - Failure taxonomy: `is_cb_failure()` ile sınıflandır (DW-4)
      - Wrapper iç hatası → fail-open + `ptf_admin_guard_failopen_total` artır (DW-3)
      - Metrik: her çağrıda `dependency_call_total`, `dependency_call_duration_seconds`, retry'da `dependency_retry_total`
    - Concrete sınıflar (şimdilik base'den türer, ileride özelleştirilebilir):
      - `DBClientWrapper(DependencyWrapper)`
      - `ExternalAPIClientWrapper(DependencyWrapper)`
      - `CacheClientWrapper(DependencyWrapper)`
    - `create_wrapper(dependency, cb_registry, config, metrics) -> DependencyWrapper` factory
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 5.1, 5.2, 5.3, 5.4_

  - [ ] 8.2 Wrapper CB entegrasyonu property testi yaz
    - `backend/tests/test_dependency_wrapper.py` oluştur
    - **Property 4: Wrapper CB Entegrasyonu**
    - CB CLOSED → call yapılır, success → record_success
    - CB CLOSED → call yapılır, CB failure → record_failure
    - CB OPEN → call yapılmaz, CircuitOpenError fırlatılır
    - Non-CB failure (4xx, ValueError) → record_failure çağrılmaz, retry yapılmaz
    - _Validates: Requirements 3.2, 3.3, 3.4_

  - [ ] 8.3 Retry politikası property testi yaz
    - **Property 6: Retry Politikası Doğruluğu**
    - `is_write=False` + CB failure → max_retries kadar retry
    - `is_write=True` + `wrapper_retry_on_write=False` → retry yok (DW-1)
    - CB OPEN'a geçildiğinde retry durur
    - Her retry'da `ptf_admin_dependency_retry_total` artıyor
    - Exponential backoff: delay = base * 2^attempt
    - _Validates: Requirements 5.1, 5.2, 5.4, 5.5_

  - [ ] 8.4 Wrapper metrik kaydı property testi yaz
    - **Property 7: Wrapper Metrik Kaydı**
    - Her outcome için doğru `dependency_call_total{dep, outcome}` artışı
    - Duration histogram güncelleniyor
    - Wrapper iç hatası → `guard_failopen_total` artışı (DW-3)
    - _Validates: Requirements 3.6, 3.7_

- [ ] 9. Checkpoint — Blok 3 doğrulaması
  - Wrapper'lar bağımsız çalışıyor (henüz handler'a bağlı değil)
  - Tüm property testleri geçiyor
  - Mevcut ~655 test kırılmadı

### Blok 4 — Wiring

- [ ] 10. CircuitBreakerRegistry singleton ve wrapper factory wiring
  - [ ] 10.1 main.py'de CB registry singleton oluştur
    - `backend/app/main.py` → `_get_cb_registry()` lazy singleton
    - Wrapper factory'yi endpoint handler'lardan erişilebilir yap
    - _Requirements: 8.4, 8.5_

  - [ ] 10.2 Handler'ların dependency çağrılarını wrapper'a geçir
    - Kritik yol endpoint'leri: import/apply, import/preview, market-prices CRUD, lookup
    - Her handler'da: `wrapper = create_wrapper(dep, registry, config, metrics)`
    - `await wrapper.call(actual_dependency_fn, ..., is_write=True/False)`
    - _Requirements: 3.1, 8.4_

- [ ] 11. Final Checkpoint — Tüm testler
  - Mevcut ~655 test + yeni testlerin tamamı geçiyor
  - Tüm kritik yol dependency çağrıları wrapper'dan geçiyor
  - Failure taxonomy tek kaynak + testle kanıt
  - Retry policy yalnızca read/idempotent'ta aktif (DW-1)
  - CB enforcement call-site'ta aktif: OPEN → CircuitOpenError → 503
  - Pre-check sadece deterministik mapping endpoint'lerde açık (DW-2)
  - Wrapper internal error → fail-open + metrik (DW-3)
  - Full suite yeşil

## DoD (Bu Faz Bitti Sayılması İçin)

1. ✅ Tüm dependency çağrıları wrapper'dan geçiyor (en azından kritik yol)
2. ✅ Failure taxonomy tek kaynak + testle kanıt
3. ✅ Retry policy yalnızca doğru sınıfta aktif (read/idempotent)
4. ✅ CB enforcement call-site'ta aktif (OPEN → CircuitOpenError → 503)
5. ✅ Pre-check sadece deterministik mapping endpoint'lerde açık
6. ✅ Wrapper internal error → fail-open + metrik
7. ✅ Full suite yeşil (~655 mevcut + yeni testler)

## Notlar

- Her görev spesifik gereksinimleri referans eder
- Checkpoint'ler artımlı doğrulama sağlar
- Property testleri Hypothesis ile min 100 iterasyon
- PBT Perf Rule: `st.from_regex(...)` kullanılmaz; küçük boyut limitleri ile kompozisyonel stratejiler tercih edilir
- `ptf_admin_` metrik namespace korunur — yeni namespace YASAK
- Guard zinciri sırası (HD-2): KillSwitch → RateLimiter → CircuitBreaker → Handler
