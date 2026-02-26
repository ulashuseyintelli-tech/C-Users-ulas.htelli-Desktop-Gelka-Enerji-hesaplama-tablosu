# Requirements Document — SLO-Aware Adaptive Control (rev3)

## Giriş

Bu özellik, mevcut runtime guard ve PDF worker subsystem'lerini birleştiren SLO-farkında bir adaptive control plane oluşturur. Sistem, gerçek zamanlı metriklerden (p95 latency, queue depth, error budget) yararlanarak dinamik kararlar alır: guard enforce modunu shadow'a geçirme, PDF job kabulünü durdurma gibi. Feedback-loop tabanlı kontrol sistemi olduğu için oscillation, hysteresis, fail-safe fallback ve alert-auto-control çakışması gibi riskler özel olarak ele alınır.

Kapsam: Global Control Plane — hem PDF worker hem runtime guard üzerinde birleşik adaptif kontrol.

### v1 Kısıtları

- Adaptive control v1 yalnızca **downgrade** (enforcement azaltma) aksiyonları alır; otomatik olarak enforcement artırmaz (monotonic-safe).
- Guard subsystem'inde v1 yalnızca ENFORCE→SHADOW geçişi yapabilir. OFF modu adaptive control kapsamı dışındadır; OFF geçişi ayrı bir ops panic gate mekanizması gerektirir.
- Adaptive control yalnızca allowlist'te tanımlı tenant/endpoint'leri etkiler; allowlist dışı hedefler kontrol kapsamı dışındadır.

## Sözlük (Glossary)

- **Adaptive_Controller**: SLO metriklerini değerlendirip guard mode ve PDF job acceptance üzerinde dinamik kararlar alan merkezi kontrol bileşeni
- **Control_Signal**: Adaptive_Controller'ın ürettiği eylem komutu (örn. "switch_to_shadow", "stop_accepting_jobs")
- **SLO_Evaluator**: Mevcut `slo_evaluator.py` modülü; availability, latency_p99, correctness SLI'larını windowed olarak değerlendirir
- **Guard_Decision**: Mevcut `guard_decision.py` modülü; enforce/shadow/disabled modları arasında geçiş yapan karar katmanı
- **PDF_Job_Store**: Mevcut `pdf_job_store.py` modülü; PDF render job'larının yaşam döngüsünü yöneten store
- **Metrics_Collector**: Runtime guard ve PDF worker metriklerini toplayan ve Adaptive_Controller'a sunan bileşen
- **Hysteresis_Band**: Oscillation'ı önlemek için threshold geçişlerinde uygulanan deadband aralığı; ayrı enter ve exit threshold'ları ile tanımlanır
- **Dwell_Time**: Bir mod geçişinden sonra yeni bir geçiş yapılmadan önce beklenmesi gereken minimum süre (örn. 10 dakika)
- **Cooldown_Period**: Bir control action'dan sonra yeni action alınmadan önce beklenmesi gereken minimum süre
- **Error_Budget**: Belirli bir metrik üzerinden belirli bir zaman penceresi ve threshold ile tanımlanan bütçe; tükendiğinde koruyucu aksiyonlar tetiklenir
- **Shadow_Mode**: Guard kurallarının loglandığı ama enforce edilmediği çalışma modu
- **Backpressure**: PDF worker'ın yeni job kabul etmeyi durdurarak upstream'e baskı uygulaması; HTTP 429 + Retry-After header ile kapasite semantiği kullanır
- **Fail_Safe_State**: Adaptive_Controller'ın kendi hatası durumunda geçtiği güvenli varsayılan durum
- **Control_Loop_Interval**: Adaptive_Controller'ın metrikleri değerlendirme periyodu
- **Control_Decision_Event**: Her mod geçişinde üretilen yapılandırılmış audit event'i (reason, previous_mode, new_mode, timestamps, correlation_id)
- **Allowlist**: Adaptive control'ün etki alanını sınırlayan tenant/endpoint listesi; yalnızca bu listedeki hedefler kontrol kapsamındadır
- **Priority_Order**: Kontrol kararlarının deterministik öncelik sırası: (1) KillSwitch, (2) Manual Override, (3) Adaptive Control, (4) Default Config
- **Monotonic_Safe**: Otomatik aksiyonların yalnızca enforcement azaltma (downgrade) yönünde olması; otomatik enforcement artırma yapılmaması


## Cross-Cutting Kısıtlar (Cross-Cutting Constraints)

Aşağıdaki kısıtlar tüm gereksinimleri keser ve her gereksinimde ayrıca tekrarlanmaz:

1. THE Adaptive_Controller SHALL izin verilen aksiyonları sınırlı ve açıkça numaralandırılmış bir küme olarak tanımlamak; bu küme dışında aksiyon üretmemek
2. THE Adaptive_Controller SHALL otomatik aksiyonlarda monotonic-safe davranmak: yalnızca enforcement azaltma (downgrade) yönünde aksiyon almak, otomatik olarak enforcement artırmamak
3. THE Adaptive_Controller SHALL kontrol kararlarını deterministik bir öncelik sırasına göre uygulamak: (1) KillSwitch (hard), (2) Manual Override (ops), (3) Adaptive Control, (4) Default Config; aynı seviyede birden fazla sinyal varsa tie-breaker: subsystem_id → metric_name → tenant_id (lexicographic)
4. THE Adaptive_Controller SHALL öncelik sırasının doğruluğunu test ile garanti altına almak; öncelik sırası ihlali durumunda aksiyon almamak
5. THE Adaptive_Controller SHALL yalnızca yapılandırılmış Allowlist'teki tenant ve endpoint'leri etkilemek; Allowlist dışı hedeflerin durumunu değiştirmemek
6. THE Adaptive_Controller SHALL her mod geçişinde bir Control_Decision_Event üretmek: reason (tetikleyen SLO), previous_mode, new_mode, timestamps, correlation_id alanlarını içeren structured event ve ilgili metric counter
7. THE Adaptive_Controller SHALL tüm mod geçişlerinde hysteresis (ayrı enter/exit threshold) ve minimum Dwell_Time uygulamak

## Gereksinimler (Requirements)

### Gereksinim 1: Metrik Toplama ve Değerlendirme

**User Story:** Bir platform operatörü olarak, runtime guard ve PDF worker metriklerinin tek bir noktada toplanmasını istiyorum, böylece adaptive controller tutarlı kararlar alabilsin.

#### Acceptance Criteria

1. THE Metrics_Collector SHALL p95 latency, error rate ve queue depth metriklerini hem Guard_Decision hem PDF_Job_Store kaynaklarından toplayarak Adaptive_Controller'a sunmak
2. WHEN yeni metrik sample'ları toplandığında, THE Metrics_Collector SHALL sample'ları SLO_Evaluator ile uyumlu MetricSample formatında saklamak
3. THE Metrics_Collector SHALL her metrik kaynağı için son toplama zamanını (timestamp) kaydetmek
4. IF bir metrik kaynağı Control_Loop_Interval süresince veri üretemezse, THEN THE Metrics_Collector SHALL "source_stale" durumunu raporlamak ve Adaptive_Controller'ı bilgilendirmek

### Gereksinim 2: SLO Sinyal Hassasiyeti (SLO Signal Precision)

**User Story:** Bir platform operatörü olarak, SLO değerlendirmesinin hangi histogram, pencere boyutu ve quantile yöntemi kullandığının açıkça tanımlı olmasını istiyorum, böylece metrik drift'i tespit edilebilsin.

#### Acceptance Criteria

1. THE SLO_Evaluator SHALL her SLO sinyali için şu parametreleri açıkça tanımlamak: kaynak histogram (API latency histogram vs PDF render duration histogram), pencere boyutu (5m veya 15m), quantile hesaplama yöntemi (histogram_quantile + rate(bucket))
2. THE SLO_Evaluator SHALL Guard subsystem'i için tek bir canonical sinyal kullanmak: `p95 API latency over 5m sliding window` (histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))); bu sinyal v1'de bağlayıcıdır ve değiştirilmesi yeni bir config revision + test gate gerektirir
3. THE SLO_Evaluator SHALL PDF subsystem'i için tek bir canonical sinyal kullanmak: `p95 PDF render total duration over 5m sliding window` (histogram_quantile(0.95, rate(ptf_admin_pdf_render_total_seconds_bucket[5m]))); bu sinyal v1'de bağlayıcıdır. Kapasite tuning için ayrıca `ptf_admin_pdf_render_executor_seconds` kullanılır (SLO dışı)
4. THE SLO_Evaluator SHALL sabit query/window yapılandırmasını config olarak tanımlamak; yapılandırma değişikliklerini test gate'i ile korumak (config drift test-gated)
5. IF SLO query parametreleri yapılandırma ile uyuşmuyorsa, THEN THE SLO_Evaluator SHALL "config_drift_detected" hatası üretmek ve kontrol kararı almamak


### Gereksinim 3: Error Budget Tanımı

**User Story:** Bir platform operatörü olarak, error budget'ın somut metrik, zaman penceresi ve threshold ile tanımlanmasını istiyorum, böylece bütçe tükenmesi kararları ölçülebilir ve denetlenebilir olsun.

#### Acceptance Criteria

1. THE Adaptive_Controller SHALL Guard subsystem'i için error budget'ı şu formülle tanımlamak: metrik = 5xx_rate VEYA block_rate VEYA snapshot_failure_rate, pencere = yapılandırılabilir rolling window, threshold = yapılandırılabilir yüzde değeri
2. THE Adaptive_Controller SHALL PDF subsystem'i için error budget'ı şu formülle tanımlamak: metrik = FAILED_jobs / total_jobs VEYA queue_unavailable_rate, pencere = yapılandırılabilir rolling window, threshold = yapılandırılabilir yüzde değeri
3. THE Adaptive_Controller SHALL error budget tanımını "error_budget = <metrik> over <pencere> with <threshold>" formatında yapılandırma dosyasında saklamak
4. THE Adaptive_Controller SHALL error budget burn rate'i hesaplamak ve burn rate threshold aşıldığında koruyucu aksiyonları tetiklemek
5. WHEN error budget tükendiğinde, THE Adaptive_Controller SHALL tetikleyen metrik, mevcut değer, threshold ve burn rate bilgilerini Control_Decision_Event'e dahil etmek
6. THE Adaptive_Controller SHALL error budget'ı `allowed_errors = (1 - SLO_target) × window_duration × request_rate` formülüyle hesaplamak; bu formül v1'de bağlayıcıdır
7. THE Adaptive_Controller SHALL error budget penceresi olarak rolling 30-day window kullanmak; pencere her control loop iterasyonunda kayar (calendar month değil, continuous rolling). Budget reset yalnızca yapılandırma değişikliği ile yapılabilir ve audit log'a kaydedilir

### Gereksinim 4: Adaptive Control Loop

**User Story:** Bir platform operatörü olarak, SLO metriklerine göre otomatik koruyucu aksiyonlar alınmasını istiyorum, böylece SLO ihlalleri manuel müdahale gerektirmeden önlensin.

#### Acceptance Criteria

1. THE Adaptive_Controller SHALL Control_Loop_Interval periyodunda metrikleri değerlendirerek Control_Signal üretmek
2. WHEN p95 latency değeri yapılandırılmış threshold'u aştığında, THE Adaptive_Controller SHALL Guard_Decision modunu "enforce" yerine "shadow" olarak değiştiren bir Control_Signal üretmek
3. THE Adaptive_Controller SHALL v1'de Guard subsystem'i için yalnızca ENFORCE→SHADOW geçişi yapmak; OFF moduna geçiş adaptive control kapsamı dışında olmak (OFF geçişi ayrı ops panic gate gerektirir)
4. WHEN PDF worker queue depth değeri yapılandırılmış threshold'u aştığında, THE Adaptive_Controller SHALL yeni PDF job kabulünü durduran bir Backpressure Control_Signal üretmek
5. WHEN Error_Budget belirli bir yüzdenin altına düştüğünde, THE Adaptive_Controller SHALL koruyucu aksiyonları tetikleyen bir Control_Signal üretmek
6. THE Adaptive_Controller SHALL her control loop iterasyonunda alınan kararı, kullanılan metrikleri ve üretilen Control_Signal'i loglayarak audit trail oluşturmak

### Gereksinim 5: Hysteresis, Dwell Time ve Oscillation Önleme

**User Story:** Bir platform operatörü olarak, adaptive control'ün threshold sınırında sürekli mod değiştirmemesini istiyorum, böylece sistem kararlı kalsın.

#### Acceptance Criteria

1. THE Adaptive_Controller SHALL her mod geçişi için ayrı enter threshold ve exit threshold tanımlamak (örn. queue_depth enter > 50, exit < 20)
2. THE Adaptive_Controller SHALL bir mod geçişinden sonra minimum Dwell_Time (örn. 10 dakika) süresince yeni bir mod geçişi yapmamak
3. THE Adaptive_Controller SHALL bir Control_Signal ürettikten sonra Cooldown_Period süresince aynı türde yeni Control_Signal üretmemek
4. WHEN Dwell_Time veya Cooldown_Period aktifken yeni bir threshold ihlali tespit edildiğinde, THE Adaptive_Controller SHALL ihlali loglamak ancak aksiyon almamak
5. THE Adaptive_Controller SHALL son N control loop kararını saklayarak oscillation detection yapabilmek; aynı threshold için Cooldown_Period içinde M'den fazla geçiş tespit edildiğinde "oscillation_detected" uyarısı üretmek
6. THE Adaptive_Controller SHALL tüm mod geçişlerinde hysteresis ve minimum Dwell_Time uygulamak; bu kısıt bypass edilememek


### Gereksinim 6: Fail-Safe Fallback

**User Story:** Bir platform operatörü olarak, adaptive controller'ın kendi hata durumlarında sistemin güvenli bir varsayılan duruma geçmesini istiyorum, böylece kontrol kaybı yaşanmasın.

#### Acceptance Criteria

1. IF Adaptive_Controller bir iç hata (exception) ile karşılaşırsa, THEN THE Adaptive_Controller SHALL Fail_Safe_State'e geçmek: Guard_Decision modunu mevcut durumda bırakmak ve PDF job kabulünü açık tutmak
2. IF Metrics_Collector tüm kaynaklardan "source_stale" raporlarsa, THEN THE Adaptive_Controller SHALL kontrol kararlarını askıya almak, son bilinen durumu korumak ve otomatik downgrade yapmamak; yalnızca alert üretmek
3. THE Adaptive_Controller SHALL telemetri verisi yetersiz veya eksik olduğunda otomatik downgrade yapmamak; yalnızca "telemetry_insufficient" alert'i üretmek (no-op + alert only)
4. THE Adaptive_Controller SHALL "telemetry_insufficient" durumunu şu koşullardan herhangi biri sağlandığında true olarak değerlendirmek: (a) değerlendirme penceresi içinde minimum N sample toplanamamışsa (varsayılan N = pencere_süresi / control_loop_interval × 0.8), (b) histogram bucket coverage < %80 ise (toplam bucket'ların en az %80'inde veri varsa yeterli), (c) herhangi bir metrik kaynağı "source_stale" durumundaysa. Bu minimum veri şartları yapılandırılabilir ve test-gated olmalıdır
5. IF Adaptive_Controller control plane crash yaşarsa, THEN THE Adaptive_Controller SHALL mevcut modları korumak; manuel ops devralana kadar mod değişikliği yapmamak
6. THE Adaptive_Controller SHALL Fail_Safe_State'e her geçişte "adaptive_control_failsafe" metriğini increment etmek ve structured log üretmek
7. IF Adaptive_Controller Fail_Safe_State'deyken metrik kaynakları tekrar sağlıklı hale gelirse, THEN THE Adaptive_Controller SHALL normal kontrol döngüsüne otomatik olarak dönmek
8. THE Adaptive_Controller SHALL Fail_Safe_State'e geçiş nedenini ve süresini kaydetmek

### Gereksinim 7: Guard Mode Adaptif Kontrolü — Authority Boundary

**User Story:** Bir platform operatörü olarak, yüksek latency durumlarında guard enforce modunun otomatik olarak shadow'a geçmesini istiyorum, böylece guard kuralları kullanıcı deneyimini olumsuz etkilemesin.

#### Acceptance Criteria

1. WHEN Adaptive_Controller "switch_to_shadow" Control_Signal ürettiğinde, THE Guard_Decision SHALL enforce modundan shadow moduna geçmek
2. THE Adaptive_Controller SHALL v1'de Guard subsystem'i için yalnızca ENFORCE→SHADOW geçişi yapmak; OFF moduna geçiş yapamamak (OFF geçişi ayrı ops panic gate mekanizması gerektirir)
3. WHEN p95 latency değeri exit threshold altına düştüğünde ve Dwell_Time dolduğunda, THE Adaptive_Controller SHALL "restore_enforce" Control_Signal üretmek
4. WHILE Guard_Decision shadow modundayken adaptive control tarafından geçirilmişse, THE Adaptive_Controller SHALL shadow moduna geçiş nedenini ve süresini metrik olarak raporlamak
5. THE Adaptive_Controller SHALL guard mode geçişlerini yalnızca Allowlist'te tanımlı endpoint sınıfları (endpoint class) için uygulamak; Allowlist dışı endpoint'lerin modunu değiştirmemek

### Gereksinim 8: PDF Worker Backpressure Kontrolü

**User Story:** Bir platform operatörü olarak, PDF worker queue'su dolduğunda yeni job kabulünün otomatik olarak durdurulmasını istiyorum, böylece worker'ın aşırı yüklenmesi önlensin.

#### Acceptance Criteria

1. WHEN Adaptive_Controller "stop_accepting_jobs" Control_Signal ürettiğinde, THE PDF_Job_Store SHALL yeni job oluşturma isteklerini HTTP 429 (Too Many Requests) ile reddetmek, Retry-After header eklemek ve response body'de "BACKPRESSURE_ACTIVE" error code döndürmek (kapasite semantiği; 503 değil 429 kullanılır)
2. THE PDF_Job_Store SHALL 429 backpressure yanıtını hard block (HOLD) olarak uygulamak: istek kuyruğa alınmaz, yavaşlatılmaz, ertelenmez; client Retry-After süresinden önce tekrar denemelidir. Decision outcome: HOLD (hard block), PASS değil, DEGRADE değil
3. WHEN queue depth değeri exit threshold altına düştüğünde ve Dwell_Time dolduğunda, THE Adaptive_Controller SHALL "resume_accepting_jobs" Control_Signal üretmek
4. WHILE Backpressure aktifken, THE PDF_Job_Store SHALL mevcut queue'daki job'ları işlemeye devam etmek; yalnızca yeni job kabulünü durdurmak
5. THE Adaptive_Controller SHALL backpressure durumunu, aktif olduğu süreyi ve reddedilen job sayısını metrik olarak raporlamak


### Gereksinim 9: Yapılandırma, Threshold Yönetimi ve Allowlist Scoping

**User Story:** Bir platform operatörü olarak, adaptive control threshold'larını, parametrelerini ve etki alanını runtime'da değiştirebilmek istiyorum, böylece sistemi yeniden başlatmadan ayarlayabileyim.

#### Acceptance Criteria

1. THE Adaptive_Controller SHALL aşağıdaki parametreleri yapılandırılabilir olarak kabul etmek: p95_latency_threshold, queue_depth_enter_threshold, queue_depth_exit_threshold, error_budget_metric, error_budget_window, error_budget_threshold, hysteresis_enter_threshold, hysteresis_exit_threshold, dwell_time_seconds, cooldown_period_seconds, control_loop_interval_seconds
2. THE Adaptive_Controller SHALL tüm yapılandırma parametreleri için geçerli aralık doğrulaması yapmak; geçersiz değerleri reddetmek ve mevcut yapılandırmayı korumak
3. WHEN yapılandırma değiştirildiğinde, THE Adaptive_Controller SHALL değişikliği audit log'a kaydetmek (eski değer, yeni değer, değiştiren aktör, zaman damgası)
4. THE Adaptive_Controller SHALL varsayılan yapılandırma değerlerini GuardConfig ile tutarlı bir şekilde environment variable'lardan yüklemek
5. THE Adaptive_Controller SHALL yapılandırmada bir "targets" (Allowlist) listesi bulundurmak; yalnızca bu listede tanımlı tenant ve endpoint'leri etkilemek
6. THE Adaptive_Controller SHALL Allowlist boş olduğunda hiçbir hedef üzerinde aksiyon almamak
7. WHEN Allowlist değiştirildiğinde, THE Adaptive_Controller SHALL değişikliği audit log'a kaydetmek ve yeni Allowlist'i bir sonraki control loop iterasyonunda uygulamak

### Gereksinim 10: KillSwitch Öncelik Sırası ve Çakışma Yönetimi

**User Story:** Bir platform operatörü olarak, manuel müdahalelerin her zaman otomatik kontrol kararlarından öncelikli olmasını ve bu öncelik sırasının deterministik ve test edilebilir olmasını istiyorum.

#### Acceptance Criteria

1. THE Adaptive_Controller SHALL kontrol kararlarını şu deterministik öncelik sırasına göre uygulamak: (1) KillSwitch (hard stop), (2) Manual Override (ops müdahalesi), (3) Adaptive Control (otomatik), (4) Default Config (varsayılan)
2. THE Adaptive_Controller SHALL aynı öncelik seviyesinde birden fazla sinyal/ihlal bulunduğunda deterministik bir tie-breaker uygulamak: önce subsystem_id lexicographic sıra (guard < pdf), sonra metric_name lexicographic sıra, sonra tenant_id lexicographic sıra. Bu tie-breaker sırası test ile garanti altına alınmalıdır
3. THE Adaptive_Controller SHALL öncelik sırasının ve tie-breaker'ın doğruluğunu birim testleri ile garanti altına almak; her seviye bir üst seviye tarafından override edilebilmek
3. WHEN KillSwitchManager tarafından manuel bir switch aktive edildiğinde, THE Adaptive_Controller SHALL ilgili subsystem için otomatik kontrol kararlarını askıya almak
4. WHILE bir KillSwitch aktifken, THE Adaptive_Controller SHALL ilgili subsystem'in durumunu değiştirmeye çalışmamak ve "manual_override_active" durumunu loglamak
5. WHEN KillSwitch deaktive edildiğinde, THE Adaptive_Controller SHALL normal kontrol döngüsüne dönmek; ancak ilk kontrol kararını Cooldown_Period sonrasına ertelemek
6. THE Adaptive_Controller SHALL her control loop iterasyonunda aktif manual override'ları kontrol etmek ve override durumunu metrik olarak raporlamak

### Gereksinim 11: Telemetri, Karar Audit ve Gözlemlenebilirlik

**User Story:** Bir platform operatörü olarak, adaptive control sisteminin tüm kararlarını, mod geçişlerini ve durumunu izleyebilmek ve denetleyebilmek istiyorum, böylece sistemin davranışını anlayabileyim ve sorun giderebilmem.

#### Acceptance Criteria

1. THE Adaptive_Controller SHALL her control loop iterasyonu için şu metrikleri üretmek: adaptive_control_loop_duration_seconds, adaptive_control_signal_total (label: signal_type), adaptive_control_state (label: subsystem, state)
2. THE Adaptive_Controller SHALL guard mode geçişleri için adaptive_guard_mode_transition_total (label: from_mode, to_mode, reason) metriğini üretmek
3. THE Adaptive_Controller SHALL PDF backpressure için adaptive_pdf_backpressure_active gauge ve adaptive_pdf_jobs_rejected_total counter metriklerini üretmek
4. THE Adaptive_Controller SHALL hysteresis ve cooldown durumları için adaptive_cooldown_active gauge ve adaptive_oscillation_detected_total counter metriklerini üretmek
5. THE Adaptive_Controller SHALL tüm Control_Signal'leri structured JSON log formatında kaydetmek; her log entry'de timestamp, signal_type, trigger_metric, trigger_value, threshold, action alanlarını içermek
6. THE Adaptive_Controller SHALL her mod geçişinde bir Control_Decision_Event üretmek; bu event şu alanları içermek: reason (tetikleyen SLO), previous_mode, new_mode, transition_timestamp, correlation_id
7. THE Adaptive_Controller SHALL Control_Decision_Event'leri hem structured log hem de metric counter (adaptive_control_decision_total, label: reason, from_mode, to_mode) olarak yayınlamak
8. THE Adaptive_Controller SHALL tüm geçişlerin denetlenebilir (auditable) olmasını garanti etmek; Control_Decision_Event üretilmeden mod geçişi yapılamamak