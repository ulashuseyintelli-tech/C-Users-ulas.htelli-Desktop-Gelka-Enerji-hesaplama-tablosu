# Gereksinimler Dokümanı — Load Characterization & Failure Injection

## Giriş

Load Characterization, PTF Admin sisteminin dayanıklılık mekanizmalarını (circuit breaker, retry, fail-open) "tasarlanmış dayanıklılık"tan "kanıtlanmış dayanıklılık"a taşıyan Faz 6 özelliğidir. Mevcut fault-injection altyapısı (FaultInjector, StubServer, AlertValidator) ve dependency-wrapper katmanı (DependencyWrapper, CircuitBreakerRegistry, PTFMetrics) üzerine inşa edilir.

Bu özellik, deterministik yük profilleri altında gerçek eşzamanlılık ile retry amplifikasyonu, CB açılma/kapanma davranışı, alert tetikleme eşikleri, fail-open frekansı, write-path güvenliği ve çoklu-instance CB sapmasını ölçer ve raporlar. Tüm yük testleri in-process async (asyncio) olarak çalışır — harici yük test aracı gerektirmez, CI-güvenlidir.

Mevcut ~1473 backend ve ~222 monitoring testini bozmadan, `ptf_admin_` metrik namespace'ini kullanarak, yalnızca test kodunda yaşayan yük karakterizasyon altyapısı ekler.

## Sözlük

- **Load_Harness**: Async yük üreteci; yapılandırılabilir profiller ile eşzamanlı istek simülasyonu yapan bileşen
- **Load_Profile**: RPS (saniyedeki istek sayısı), süre ve hedef tanımlayan deterministik yük profili (Baseline, Peak, Stress, Burst)
- **Scenario_Runner**: Yük profili + hata enjeksiyonu + metrik yakalama orkestrasyon bileşeni
- **Metrics_Capture**: Senaryo öncesi/sonrası metrik anlık görüntüsü alarak delta hesaplayan yardımcı bileşen
- **Retry_Amplification_Factor**: `toplam_retry / toplam_çağrı` oranı; retry'ların sisteme eklediği ek yükü ölçen metrik
- **CB_Divergence**: Çoklu CB instance'larının aynı hata koşullarında farklı zamanlarda durum değiştirmesi durumu
- **CB_Divergence_Window**: İki CB instance'ının aynı duruma ulaşması arasındaki süre farkı (saniye cinsinden)
- **Stress_Report**: Senaryo sonuçlarını, metrik tablolarını ve ayar önerilerini içeren çıktı dokümanı
- **Failure_Matrix**: Hata türü × enjeksiyon oranı × beklenen sistem davranışı kombinasyonlarını tanımlayan matris
- **FaultInjector**: Mevcut test-only singleton; hata enjeksiyon noktalarını yöneten kontrol nesnesi (`backend/app/testing/fault_injection.py`)
- **StubServer**: Mevcut in-process HTTP sunucusu; downstream API simülasyonu (`backend/app/testing/stub_server.py`)
- **AlertValidator**: Mevcut PromQL alert değerlendirme yardımcısı (`backend/app/testing/alert_validator.py`)
- **DependencyWrapper**: Mevcut dış bağımlılık çağrı sarmalayıcısı; CB, retry, timeout entegrasyonu (`backend/app/guards/dependency_wrapper.py`)
- **CircuitBreakerRegistry**: Mevcut per-dependency CB instance yöneticisi (`backend/app/guards/circuit_breaker.py`)
- **PTFMetrics**: Mevcut `ptf_admin_` namespace'li Prometheus metrik sınıfı (`backend/app/ptf_metrics.py`)
- **Guard_Config**: Mevcut merkezi yapılandırma nesnesi (`backend/app/guard_config.py`)
- **FAIL**: Test başarısızlık durumu — ilgili assertion ihlal edildiğinde test kırmızı olur

## Genel Normatif Kurallar

### GNK-1: FAIL Diagnostic Payload Zorunluluğu
Her FAIL koşulu tetiklendiğinde, test çıktısı tek satır diagnostic payload içermelidir. Zorunlu alanlar: `scenario_id`, `dependency`, `outcome`, `observed`, `expected`, `seed`. FAIL: diagnostic payload eksik veya zorunlu alanlardan herhangi biri boş.

### GNK-2: Determinism Scope
Bu kurallar tüm gereksinimler için geçerlidir:
1. **Determinism target** = metrik agregasyonu + karar çıktıları (alert fire/silent, retry_total, outcome counts, mapping completeness, CB state transitions). Bu çıktılar aynı seed ve aynı girdi ile her çalıştırmada aynı sonucu üretmelidir.
2. **Non-deterministic tolerated** = per-request zamanlama jitter, exact request ordering, asyncio scheduling sırası, OS timing farkları. Bu değerler çalıştırmalar arasında farklılık gösterebilir ve kesin değer assertion'ı yapılmaz.

### GNK-3: Profil Bazlı Minimum İstek Sayıları
Yük profillerinde istatistiksel anlamlılık için minimum istek sayıları (scale_factor uygulandıktan sonra):
- Baseline / Peak: ≥ 200
- Stress / Burst: ≥ 500
FAIL: profil bazlı minimum istek sayısının altında kalınırsa (R7 write-path hariç, orada ≥ 50 geçerlidir).

## Gereksinimler

### Gereksinim 1: Async Yük Üreteci (Load Harness)

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, deterministik yük profilleri ile eşzamanlı istek simülasyonu yapabilmek istiyorum, böylece sistem davranışını kontrollü koşullarda ölçebileyim.

#### Kabul Kriterleri

1. THE Load_Harness SHALL dört deterministik yük profili desteklemek: Baseline (50 RPS, 10 dk), Peak (200 RPS, 10 dk), Stress (500 RPS, 5 dk), Burst (1000 RPS, 30 sn × 3 döngü)
2. THE Load_Harness SHALL asyncio tabanlı eşzamanlı görevler ile yük üretmek; harici yük test aracı gerektirmemek (LC-1)
3. WHEN bir yük profili çalıştırıldığında, THE Load_Harness SHALL hedef RPS'in ±%30 toleransı içinde gerçek RPS üretmek. FAIL: `|actual_rps - (target_rps × scale_factor)| / (target_rps × scale_factor) > 0.30`
4. THE Load_Harness SHALL her profil için yapılandırılabilir ölçekleme faktörü desteklemek (CI ortamında orantılı küçültme için). Alt sınır: `scale_factor ≥ 0.01`. FAIL: `scale_factor < 0.01` kabul edilmez, ValueError fırlatılmalı
5. WHEN yük profili tamamlandığında, THE Load_Harness SHALL `LoadResult` döndürmek ve şu invariant'ı sağlamak: `total_requests == successful + failed` ve `circuit_open_rejected ≤ failed`. FAIL: invariant ihlali
6. THE Load_Harness SHALL pytest testleri olarak çalışmak ve yapılandırılabilir parametreler kabul etmek (LC-6)
7. WHEN scale_factor s1 < s2 ile aynı profil çalıştırıldığında, s2'nin `total_requests` değeri s1'inkinden büyük veya eşit olmalıdır. FAIL: `total_requests(s2) < total_requests(s1)` (metamorfik özellik)

#### FAIL Özeti
| Koşul | FAIL |
|-------|------|
| RPS tolerans aşımı | `\|actual - target×scale\| / (target×scale) > 0.30` |
| scale_factor alt sınır ihlali | `scale_factor < 0.01` → ValueError |
| LoadResult invariant ihlali | `total ≠ success + failed` veya `circuit_open > failed` |
| Metamorfik özellik ihlali | `requests(s2) < requests(s1)` where `s1 < s2` |


### Gereksinim 2: Metrik Yakalama ve Karşılaştırma

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, her senaryo öncesi ve sonrası metrik anlık görüntüsü alarak delta hesaplayabilmek istiyorum, böylece senaryonun sistem üzerindeki etkisini kesin olarak ölçebileyim.

#### Kabul Kriterleri

1. THE Metrics_Capture SHALL senaryo başlangıcında PTFMetrics'ten anlık görüntü (snapshot) almak
2. THE Metrics_Capture SHALL senaryo sonunda ikinci bir anlık görüntü alarak delta (fark) hesaplamak
3. THE Metrics_Capture SHALL şu metrikleri yakalamak: `dependency_call_total` (outcome bazlı), `dependency_retry_total`, p95 gecikme, `circuit_breaker_state`, `guard_failopen_total`, `dependency_map_miss_total`
4. THE Metrics_Capture SHALL retry amplifikasyon faktörünü hesaplamak: `retry_amplification = toplam_retry / toplam_çağrı`. WHEN `toplam_çağrı == 0` THEN `retry_amplification = 0.0`. FAIL: formül sonucu ile hesaplanan değer arasında `abs(diff) > max(1e-6, 1e-4 × expected)` (hem mutlak hem göreli tolerans; 1e-9 floating-point false-FAIL üretir)
5. THE Metrics_Capture SHALL her senaryo için izole PTFMetrics registry kullanmak. İzolasyon doğrulaması: instance_A'da N kez `inc_dependency_call()` çağrıldığında, instance_B'nin snapshot'ında ilgili metrik değeri 0 olmalıdır. FAIL: instance_B snapshot'ında ilgili metrik > 0 (LC-4)
6. THE Metrics_Capture SHALL metrik sonuçlarını yapılandırılmış bir veri yapısında (MetricDelta dataclass) döndürmek; tüm alanlar sayısal (int veya float) olmalı

#### FAIL Özeti
| Koşul | FAIL |
|-------|------|
| Retry amplifikasyon formül hatası | `abs(computed - (retries/calls)) > max(1e-6, 1e-4 × expected)` |
| Division by zero handling | `calls == 0` iken `amplification ≠ 0.0` |
| İzolasyon ihlali | instance_B'de instance_A'nın metrikleri görünür |

### Gereksinim 3: Senaryo Orkestrasyon

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, yük profili + hata enjeksiyonu + metrik yakalama adımlarını tek bir orkestrasyon bileşeni ile yönetmek istiyorum, böylece senaryoları tekrarlanabilir ve tutarlı şekilde çalıştırabileyim.

#### Kabul Kriterleri

1. THE Scenario_Runner SHALL yük profili, hata enjeksiyon yapılandırması ve metrik yakalama adımlarını sıralı olarak orkestre etmek
2. WHEN bir senaryo başlatıldığında, THE Scenario_Runner SHALL izole PTFMetrics registry ve CircuitBreakerRegistry oluşturmak (LC-4)
3. WHEN bir senaryo başlatıldığında, THE Scenario_Runner SHALL mevcut FaultInjector'ı kullanarak hata enjeksiyonunu yapılandırmak (LC-2)
4. WHEN senaryo tamamlandığında (başarılı veya hatalı), THE Scenario_Runner SHALL `finally` bloğunda `FaultInjector.disable_all()` ve `FaultInjector.reset_instance()` çağırmak. FAIL: senaryo sonrası herhangi bir `InjectionPoint` için `is_enabled() == True`
5. THE Scenario_Runner SHALL DependencyWrapper katmanı üzerinden çağrı yapmak; production kod dosyalarında (`backend/app/` altında `testing/` hariç) değişiklik yapılmamalı. FAIL: production dosyasında diff varsa
6. THE Scenario_Runner SHALL senaryo sonuçlarını `ScenarioResult` dataclass olarak döndürmek; `load_result`, `metric_delta` ve `cb_states` alanları dolu olmalı. FAIL: herhangi bir alan None

#### FAIL Özeti
| Koşul | FAIL |
|-------|------|
| Temizlik ihlali | Senaryo sonrası `is_enabled(point) == True` herhangi bir point için |
| Production kodu değişikliği | `backend/app/` altında `testing/` hariç dosyada diff |
| Eksik sonuç alanı | `ScenarioResult` alanlarından herhangi biri None |

### Gereksinim 4: Hata Enjeksiyon Matrisi Testleri

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, farklı hata türleri ve enjeksiyon oranlarında sistemin beklenen davranışı sergilediğini doğrulamak istiyorum, böylece dayanıklılık mekanizmalarının doğru çalıştığından emin olabileyim.

#### Kabul Kriterleri

1. WHEN %10 Timeout enjeksiyonu uygulandığında (≥ `cb_min_samples` istek gönderildiğinde), THE DependencyWrapper SHALL `dependency_retry_total > 0` üretmek VE Circuit_Breaker CLOSED durumunda kalmak. FAIL: `retry_total == 0` VEYA `cb_state != CLOSED`
2. WHEN %40 Timeout enjeksiyonu uygulandığında (≥ `cb_min_samples` istek gönderildiğinde), THE Circuit_Breaker SHALL OPEN durumuna geçmek. FAIL: `cb_state != OPEN` (cb_min_samples istek sonrası)
3. WHEN %30 5xx enjeksiyonu uygulandığında (≥ `cb_min_samples` istek gönderildiğinde), THE Circuit_Breaker SHALL OPEN durumuna geçmek. FAIL: `cb_state != OPEN` (cb_min_samples istek sonrası)
4. WHEN %100 ConnectionError enjeksiyonu uygulandığında, THE Circuit_Breaker SHALL `cb_min_samples` istek içinde OPEN durumuna geçmek. FAIL: `cb_min_samples` istek sonrası `cb_state != OPEN`
5. WHEN %100 2× gecikme enjeksiyonu uygulandığında (gecikme = `timeout × 0.8`, timeout altında), THE DependencyWrapper SHALL tüm istekleri başarıyla tamamlamak VE Circuit_Breaker CLOSED durumunda kalmak. FAIL: `cb_state != CLOSED` VEYA `failed > 0` (timeout aşılmadığı sürece)
6. THE Failure_Matrix testleri SHALL her kombinasyon için beklenen CB durumunu, retry sayısını ve metrik değerlerini explicit assert ile doğrulamak. Her matris satırı ayrı bir test fonksiyonu olmalı.
7. Determinism: Tüm enjeksiyon fonksiyonları `random.Random(seed)` ile sabit seed kullanmalı. Aynı seed ile aynı sonuç üretilmeli. FAIL: aynı seed ile iki çalıştırma farklı CB durumu üretirse

#### Failure Matrix — Explicit Beklentiler

| # | Hata Türü | Oran | Beklenen CB | Beklenen Retry | FAIL Koşulu |
|---|-----------|------|-------------|----------------|-------------|
| FM-1 | Timeout | %10 | CLOSED | > 0 | `cb != CLOSED` veya `retry == 0` |
| FM-2 | Timeout | %40 | OPEN | CB açılınca durur | `cb != OPEN` |
| FM-3 | 5xx | %30 | OPEN | CB açılınca durur | `cb != OPEN` |
| FM-4 | ConnectionError | %100 | OPEN (≤ min_samples) | CB açılınca durur | `cb != OPEN` |
| FM-5 | Latency 2× | %100 | CLOSED | 0 | `cb != CLOSED` veya `failed > 0` |

#### FAIL Özeti
| Koşul | FAIL |
|-------|------|
| FM-1 retry üretmedi | `retry_total == 0` |
| FM-1 CB açıldı | `cb_state != CLOSED` |
| FM-2/3/4 CB açılmadı | `cb_state != OPEN` after min_samples |
| FM-5 istek başarısız | `failed > 0` |
| Determinism ihlali | Aynı seed, farklı sonuç |

#### Determinism Scope — Normatif Kurallar (Failure Matrix)
Bu kurallar bilgilendirme notu değil, zorunlu spesifikasyon kapsamıdır:
1. **Determinism target** = metrik agregasyonu + karar çıktıları (CB state, retry_total, outcome counts). Aynı seed + aynı enjeksiyon oranı ile her çalıştırmada aynı CB durumu ve aynı retry sayısı üretilmelidir.
2. **Non-deterministic tolerated** = per-request zamanlama, exact request ordering, asyncio scheduling sırası. Bu değerler kesin assertion'a tabi değildir.


### Gereksinim 5: Çoklu-Instance CB Sapma Testi

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, birden fazla CB instance'ının aynı hata koşullarında nasıl davrandığını ölçmek istiyorum, böylece çoklu-worker ortamında CB tutarlılığını değerlendirebilir ve somut ayar önerileri üretebiliyim.

#### Kabul Kriterleri

1. THE Scenario_Runner SHALL en az 2 ayrı CircuitBreakerRegistry instance'ı oluşturarak çoklu-instance simülasyonu yapmak (LC-3)
2. WHEN %40 hata enjeksiyonu uygulandığında, THE Scenario_Runner SHALL her instance'ın CB durum geçiş zamanlarını (monotonic timestamp) kaydetmek. FAIL: geçiş zamanı kaydedilmemişse (boş liste)
3. THE Metrics_Capture SHALL CB_Divergence_Window'u hesaplamak: `divergence_window = |instance_1_open_time - instance_2_open_time|` (saniye cinsinden). FAIL: hesaplama yapılamamışsa (None veya negatif değer)
4. THE Metrics_Capture SHALL clock skew compensation uygulamak: `compensated_divergence = max(0, |t1 - t2| - max_clock_skew)`. `max_clock_skew` default 50ms (test harness'ta ölçülebilir, yapılandırılabilir). Eşik karşılaştırması compensated değer üzerinden yapılır. FAIL: compensation uygulanmadan ham divergence ile eşik karşılaştırması yapılırsa
5. IF `compensated_divergence > cb_open_duration_seconds × 2` THEN THE Stress_Report SHALL en az 1 adet `TuningRecommendation` üretmek (parameter: `cb_open_duration_seconds`, reason: divergence açıklaması). IF `compensated_divergence ≤ cb_open_duration_seconds × 2` THEN öneri üretilmemeli. FAIL: eşik aşıldığında öneri yok VEYA eşik aşılmadığında gereksiz öneri var
6. THE Scenario_Runner SHALL her instance için bağımsız `ScenarioResult` döndürmek. Her result'ta `cb_states` alanı dolu olmalı. FAIL: herhangi bir instance result'ında `cb_states` boş

#### Determinism Scope — Normatif Kurallar
Bu kurallar bilgilendirme notu değil, zorunlu spesifikasyon kapsamıdır:
1. **Determinism target** = metrik agregasyonu + karar çıktıları (alert fire/silent, retry_total, outcome counts, mapping completeness, CB state transitions). Bu çıktılar aynı seed ve aynı girdi ile her çalıştırmada aynı sonucu üretmelidir.
2. **Non-deterministic tolerated** = per-request zamanlama jitter, exact request ordering (tek tek request timeline'ı), asyncio scheduling sırası, OS timing farkları. Bu değerler çalıştırmalar arasında farklılık gösterebilir ve kesin değer assertion'ı yapılmaz.
3. Çoklu-instance testleri `asyncio.gather` ile paralel çalışır. `compensated_divergence` değeri non-deterministic tolerated kapsamındadır; yalnızca hesaplanabilirlik (sayısal, ≥ 0) ve eşik mantığı doğrulanır.
4. Eşik karşılaştırması (AC 5.5) deterministik: hesaplanan compensated değer vs sabit eşik.

#### FAIL Özeti
| Koşul | FAIL |
|-------|------|
| Geçiş zamanı kaydedilmedi | `transition_times` boş liste |
| Divergence hesaplanamadı | `divergence_window` None veya < 0 |
| Clock skew compensation eksik | Ham divergence ile eşik karşılaştırması (compensation atlandı) |
| Eşik aşımında öneri yok | `compensated_divergence > threshold` ama `recommendations == []` |
| Eşik altında gereksiz öneri | `compensated_divergence ≤ threshold` ama `recommendations != []` |
| Instance result eksik | Herhangi bir `cb_states` boş |

### Gereksinim 6: Alert Doğrulama Testleri

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, her yük senaryosunda beklenen alert'lerin tetiklendiğini ve ilgisiz alert'lerin suskun kaldığını doğrulamak istiyorum, böylece alert yapılandırmasının doğru olduğundan emin olabileyim.

#### Kabul Kriterleri

1. WHEN bir yük senaryosu tamamlandığında, THE AlertValidator SHALL beklenen alert'lerin PromQL koşullarının karşılandığını doğrulamak (`would_fire == True`). FAIL: beklenen alert için `would_fire == False`
2. WHEN bir yük senaryosu tamamlandığında, THE AlertValidator SHALL ilgisiz alert'lerin tetiklenmediğini doğrulamak (`would_fire == False`). FAIL: beklenmeyen alert için `would_fire == True` (sessizlik doğrulaması)
3. Her failure matrix senaryosu (FM-1 ile FM-5) için beklenen ve beklenmeyen alert listesi explicit olarak tanımlanmalı:

| Senaryo | Beklenen Alert'ler | Suskun Kalması Gereken Alert'ler |
|---------|-------------------|----------------------------------|
| FM-1 (%10 Timeout) | — (eşik altı) | DH3, DH4, DH8 |
| FM-2 (%40 Timeout) | DH3 (timeout rate >2%), DH8 (CB open) | DH1, DH5 |
| FM-3 (%30 5xx) | DH4 (failure rate >1%), DH8 (CB open) | DH1, DH5 |
| FM-4 (%100 ConnErr) | DH4, DH8 | DH1, DH5 |
| FM-5 (%100 Latency) | DH7 (p95 >0.8s, eğer eşik aşılırsa) | DH3, DH4, DH8 |

4. THE AlertValidator SHALL mevcut AlertValidator'ı kullanmak ve dependency-health alert'leri (DH1-DH8) için yeni `check_*` metodları eklemek (LC-5). FAIL: DH1-DH8 alert'lerinden herhangi biri için `check_*` metodu yoksa
5. WHEN beklenen bir alert tetiklendiğinde, THE AlertValidator SHALL `alert_fire_latency_seconds` değerini ölçmek (senaryo başlangıcından alert koşulunun sağlandığı ana kadar geçen süre). `alert_fire_latency_seconds ≤ 2 × eval_interval_seconds`. `eval_interval_seconds` runtime paramından okunur: `int(os.getenv("EVAL_INTERVAL_SECONDS", "60"))`. ENV yoksa fallback 60s. FAIL: `alert_fire_latency_seconds > 2 × eval_interval_seconds`

#### FAIL Özeti
| Koşul | FAIL |
|-------|------|
| Beklenen alert tetiklenmedi | `would_fire == False` beklenen alert için |
| Beklenmeyen alert tetiklendi | `would_fire == True` suskun kalması gereken alert için |
| Alert check metodu eksik | DH1-DH8 için `check_*` metodu yok |
| Alert fire latency aşımı | `alert_fire_latency_seconds > 2 × eval_interval_seconds` (ENV fallback 60s) |

### Gereksinim 7: Write-Path Güvenlik Doğrulaması

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, write operasyonlarında stres altında bile retry=0 politikasının korunduğunu doğrulamak istiyorum, böylece double-write riskinin olmadığından emin olabileyim.

#### Kabul Kriterleri

1. WHEN stres yükü altında write operasyonu (`is_write=True`) çağrıldığında, THE DependencyWrapper SHALL retry YAPMAMAK (DW-1 politikası). FAIL: write path'te `dependency_retry_total > 0`
2. WHEN stres yükü altında write operasyonları çalıştırıldığında, THE Metrics_Capture SHALL write path için `dependency_retry_total` delta değerinin tam olarak 0 olduğunu doğrulamak. FAIL: `retry_delta != 0`
3. THE Write-path testleri SHALL Stress profili (500 RPS, scale_factor ile ölçeklenmiş) altında çalışmak. Minimum istek sayısı: `≥ 50` (scale_factor=0.01 ile bile anlamlı). FAIL: `total_requests < 50`
4. WHEN `wrapper_retry_on_write=False` (default) ise, write path'te retry denemesi yapılmamalı. WHEN `wrapper_retry_on_write=True` olarak override edilirse, retry yapılabilir. FAIL: default config'te write retry > 0

#### FAIL Özeti
| Koşul | FAIL |
|-------|------|
| Write path'te retry | `retry_total > 0` (default config) |
| Retry delta sıfır değil | `retry_delta != 0` |
| Yetersiz istek sayısı | `total_requests < 50` |

### Gereksinim 8: Stres Raporu Üretimi

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, tüm yük senaryolarının sonuçlarını yapılandırılmış bir rapor olarak görmek istiyorum, böylece eşik ayarlamaları ve iyileştirme önerileri üretebiliyim.

#### Kabul Kriterleri

1. THE Stress_Report SHALL her senaryo için metrik tablosu satırı üretmek. Satır sayısı giriş `ScenarioResult` listesi uzunluğuna eşit olmalı. Her satır şu alanları içermeli: `scenario_name`, `total_calls`, `retry_count`, `retry_amplification_factor`, `p95_latency_ms`, `cb_open_count`, `failopen_count`. FAIL: satır sayısı ≠ giriş listesi uzunluğu VEYA herhangi bir alan eksik
2. THE Stress_Report SHALL CB pencere ayar önerileri üretmek: IF herhangi bir senaryoda `CB_Divergence_Window > cb_open_duration_seconds × 2` THEN `TuningRecommendation(parameter="cb_open_duration_seconds", ...)` üretilmeli. FAIL: koşul sağlandığında öneri yok
3. THE Stress_Report SHALL retry üst sınır önerileri üretmek: IF herhangi bir senaryoda `retry_amplification_factor > 2.0` THEN `TuningRecommendation(parameter="wrapper_max_retries", ...)` üretilmeli. FAIL: `amplification > 2.0` iken öneri yok
4. THE Stress_Report SHALL alert eşik ayar önerileri üretmek: IF herhangi bir senaryoda beklenen alert tetiklenmemişse THEN `TuningRecommendation(parameter=alert_name, ...)` üretilmeli. FAIL: tetiklenmeyen beklenen alert için öneri yok
5. THE Stress_Report SHALL write-path güvenlik onayı içermek: `write_path_safe: bool`. `True` ancak ve ancak tüm write-path senaryolarında `retry_delta == 0` ise. FAIL: `retry_delta > 0` iken `write_path_safe == True`
6. THE Stress_Report SHALL yapılandırılmış veri yapısında (dataclass) üretilmek. Tüm alanlar programatik erişime uygun olmalı (dict veya typed attribute). FAIL: rapor None veya erişilemeyen format

#### FAIL Özeti
| Koşul | FAIL |
|-------|------|
| Satır sayısı uyumsuz | `len(table) != len(results)` |
| Eksik alan | Herhangi bir satırda zorunlu alan None |
| CB tuning önerisi eksik | `divergence > threshold` ama öneri yok |
| Retry tuning önerisi eksik | `amplification > 2.0` ama öneri yok |
| Write-path yanlış onay | `retry > 0` ama `write_path_safe == True` |
| Boş rapor | `StressReport` None |


### Gereksinim 9: Flaky Test Korelasyon Gözlemi

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, yüksek eşzamanlılık altında zamanlama sapmasının flaky test davranışıyla korelasyonunu ölçmek istiyorum, böylece flaky testlerin kök nedenini anlayabileyim.

#### Kabul Kriterleri

1. WHEN yüksek eşzamanlılık senaryosu çalıştırıldığında (≥ Peak profili), THE Scenario_Runner SHALL `test_provisional_to_final_allowed` test senaryosunu simüle eden operasyonları dahil etmek: provisional → final state geçişi, zamanlama bağımlı assertion
2. THE Metrics_Capture SHALL zamanlama sapması metriklerini kaydetmek: `timing_deviation_ms = |actual_duration - expected_duration|` (milisaniye cinsinden). FAIL: `timing_deviation_ms` hesaplanamamışsa (None)
3. IF `timing_deviation_ms > 100` (100ms eşik) THEN THE Stress_Report SHALL `flaky_test_correlation` alanını doldurmak (boş olmayan string, sapma değeri ve olası neden içermeli). IF `timing_deviation_ms ≤ 100` THEN `flaky_test_correlation` boş string olmalı. FAIL: eşik aşımında boş string VEYA eşik altında dolu string
4. WHEN `flaky_test_correlation` dolu ise, rapor segmenti en az şu 3 alanı içermelidir: `timing_deviation_ms` (sapma değeri), `suspected_source` (scheduler / io / cb / retry), `repro_steps` (seed + scenario + dependency). FAIL: dolu korelasyonda bu 3 alandan herhangi biri eksik

#### Determinism Notu
- Zamanlama sapması doğası gereği non-deterministic (OS scheduling, event loop yükü).
- Test, sapma değerinin hesaplanabilir olduğunu (sayısal, ≥ 0) ve eşik karşılaştırmasının doğru çalıştığını doğrular.
- Kesin sapma değeri assertion'ı yapılmaz; yalnızca eşik mantığı test edilir.

#### FAIL Özeti
| Koşul | FAIL |
|-------|------|
| Sapma hesaplanamadı | `timing_deviation_ms` None |
| Eşik aşımında boş korelasyon | `deviation > 100` ama `correlation == ""` |
| Eşik altında dolu korelasyon | `deviation ≤ 100` ama `correlation != ""` |
| Rapor segmenti eksik alan | Dolu korelasyonda `timing_deviation_ms`, `suspected_source` veya `repro_steps` eksik |

### Gereksinim 10: Mevcut Sistem Uyumluluğu

**Kullanıcı Hikayesi:** Bir geliştirici olarak, yük karakterizasyon altyapısının mevcut sistemi bozmamasını istiyorum, böylece güvenle test edebilir ve CI'da çalıştırabileyim.

#### Kabul Kriterleri

1. THE Load_Harness SHALL yalnızca `backend/app/testing/` ve `backend/tests/` dizinlerinde yaşamak; production kod yollarını değiştirmemek. FAIL: `backend/app/` altında `testing/` hariç herhangi bir dosyada diff
2. THE Load_Harness SHALL mevcut ~1473 backend ve ~222 monitoring testini kırmamak. FAIL: mevcut test suite'inde herhangi bir test kırmızıya dönerse
3. THE Load_Harness SHALL mevcut `ptf_admin_` metrik namespace'ini kullanmak; yeni namespace oluşturmamak. FAIL: `ptf_admin_` dışında metrik adı kullanılırsa
4. THE Load_Harness SHALL mevcut FaultInjector, StubServer ve AlertValidator bileşenlerini değiştirmeden kullanmak (LC-2, LC-5). FAIL: bu dosyalarda diff varsa
5. THE Load_Harness SHALL mevcut DependencyWrapper ve CircuitBreakerRegistry sınıflarını değiştirmeden kullanmak. FAIL: bu dosyalarda diff varsa
6. THE Load_Harness SHALL mevcut ops-guard tasarım kararlarına (HD-1 ile HD-7) ve dependency-wrapper kararlarına (DW-1 ile DW-4) uymak
7. Tüm yük karakterizasyon testleri (yeni eklenen) toplamda < 4 dakika içinde tamamlanmalı (CI-safe). FAIL: toplam test süresi ≥ 4 dakika

#### FAIL Özeti
| Koşul | FAIL |
|-------|------|
| Production kodu değişikliği | `backend/app/` altında `testing/` hariç diff |
| Mevcut test kırılması | Herhangi bir mevcut test kırmızı |
| Namespace ihlali | `ptf_admin_` dışında metrik adı |
| Mevcut bileşen değişikliği | FaultInjector/StubServer/AlertValidator/DependencyWrapper/CBRegistry dosyalarında diff |
| CI süre aşımı | Toplam test süresi ≥ 4 dakika |
