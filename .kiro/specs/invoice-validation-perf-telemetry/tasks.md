# Uygulama Planı: Fatura Doğrulama Performans Telemetrisi (Faz G)

## Genel Bakış

Bu plan, fatura doğrulama pipeline'ına performans telemetrisi ekler: faz etiketli histogram, mod gauge, yapılandırılabilir gecikme bütçesi ve `ValidationBlockedError` terminal durum semantiği. Mevcut 54 test korunur; yeni dosyalar `telemetry.py` ve `telemetry_config.py`, güncellemeler `validator.py`, `shadow.py`, `enforcement.py` üzerindedir.

## Görevler

- [x] 1. Gecikme bütçesi yapılandırması — `telemetry_config.py`
  - [x] 1.1 `backend/app/invoice/validation/telemetry_config.py` dosyasını oluştur
    - `LatencyBudgetConfig` frozen dataclass: `p95_ms: float | None`, `p99_ms: float | None`
    - `_parse_positive_float(raw, name)`: pozitif float parse, geçersiz → `None` + log (fail-closed, ValueError yok)
    - `load_latency_budget_config()`: `INVOICE_VALIDATION_LATENCY_BUDGET_P95_MS` ve `INVOICE_VALIDATION_LATENCY_BUDGET_P99_MS` ortam değişkenlerini oku
    - Geçersiz mod parse → varsayılan `shadow` + log fallback davranışı
    - _Gereksinimler: 7.1, 7.2, 7.3, 7.4, 7.5_

- [x] 2. Telemetri modülü — `telemetry.py`
  - [x] 2.1 `backend/app/invoice/validation/telemetry.py` dosyasını oluştur
    - `Phase(str, Enum)`: `TOTAL`, `SHADOW`, `ENFORCEMENT` — kapalı küme
    - `VALID_PHASES: frozenset[str]` ve `VALID_MODES: frozenset[str]`
    - `_duration_observations: dict[str, list[float]]` — histogram in-memory store (test ortamı)
    - `observe_duration(phase, duration_seconds)`: geçerli phase → gözlem ekle; geçersiz → log + skip (fail-closed)
    - `get_duration_observations()`, `reset_duration_observations()` — test inspection/cleanup
    - `_mode_gauge: dict[str, int]` — mod gauge state
    - `set_mode_gauge(active_mode)`: aktif mod → 1, diğerleri → 0; geçersiz → log + skip
    - `get_mode_gauge()`, `reset_mode_gauge()` — test inspection/cleanup
    - `Timer` context manager: `time.monotonic()` tabanlı, `try/finally` ile exception izolasyonu
    - _Gereksinimler: 1.1, 1.2, 1.3, 1.4, 5.1, 5.2, 5.3, 5.4, 5.5, 9.1, 9.2, 9.3, 9.4_

- [x] 3. Enstrümantasyon noktaları — zamanlayıcı entegrasyonu
  - [x] 3.1 `validator.py` güncelle — toplam süre zamanlayıcısı
    - `extract_canonical()` veya `validate()` çağrı noktasında `Timer` + `observe_duration(Phase.TOTAL.value, ...)` ekle
    - Toplam süre her fatura için kaydedilir (örnekleme bağımsız)
    - _Gereksinimler: 2.1, 2.2, 2.3_

  - [x] 3.2 `shadow.py` güncelle — shadow fazı zamanlayıcısı
    - `shadow_validate_hook()` içinde `Timer` + `observe_duration(Phase.SHADOW.value, ...)` ekle
    - Yalnızca örneklenen faturalarda kaydedilir
    - _Gereksinimler: 3.1, 3.2, 3.3_

  - [x] 3.3 `enforcement.py` güncelle — enforcement fazı zamanlayıcısı
    - `enforce_validation()` içinde `enforce_soft` / `enforce_hard` modlarında `Timer` + `observe_duration(Phase.ENFORCEMENT.value, ...)` ekle
    - `shadow` / `off` modlarında enforcement gözlemi kaydedilmez
    - _Gereksinimler: 4.1, 4.2, 4.3_

  - [x] 3.4 `enforcement.py` güncelle — `ValidationBlockedError` terminal durum semantiği
    - `ValidationBlockedError` sınıfına `terminal: bool = True` sentinel özelliği ekle
    - Worker retry guard pattern'i için docstring güncelle
    - _Gereksinimler: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 3.5 `__init__.py` export'larını güncelle
    - Yeni modüllerden export ekle: `Phase`, `VALID_PHASES`, `VALID_MODES`, `observe_duration`, `set_mode_gauge`, `get_mode_gauge`, `Timer`, `LatencyBudgetConfig`, `load_latency_budget_config`
    - _Gereksinimler: 1.1, 5.1, 7.1_

- [x] 4. Birim testleri + PBT
  - [x] 4.1 `backend/tests/test_invoice_telemetry_g.py` dosyasını oluştur — birim testleri
    - `test_histogram_single_metric`: tek histogram metriği doğrulama (ayrı metrik yok)
    - `test_mode_gauge_initial_state`: gauge başlangıç durumu (tümü 0)
    - `test_validation_blocked_error_terminal`: `terminal` özelliği `True`
    - `test_latency_budget_unset_no_warning`: bütçe tanımsız → uyarı yok
    - `test_latency_budget_exceeded_logs`: bütçe aşımı → log kaydı
    - `test_histogram_not_reset_on_mode_change`: mod geçişinde veri korunması
    - `test_no_mode_label_on_histogram`: histogram'da mode label yok (kardinalite kontrolü)
    - `test_timer_exception_isolation`: Timer context manager exception propagate etmez
    - `test_invalid_phase_fail_closed`: geçersiz phase → gözlem atlanır, hata loglanır
    - `test_invalid_mode_fail_closed`: geçersiz mode → gauge güncellenmez, hata loglanır
    - _Gereksinimler: 1.1, 1.3, 5.1, 5.4, 6.2, 7.3, 7.4, 8.4, 9.1, 9.2_

  - [x]* 4.2 Phase kapalı küme kontrolü PBT testi yaz
    - **Property 1: Phase Kapalı Küme Kontrolü**
    - **Doğrular: Gereksinimler 1.2, 1.3, 9.3**

  - [x]* 4.3 Mod gauge tek aktif invariantı PBT testi yaz
    - **Property 5: Mod Gauge Tek Aktif İnvariantı**
    - **Doğrular: Gereksinimler 5.2, 5.3, 5.5**

  - [x]* 4.4 Mod geçişinde histogram korunması PBT testi yaz
    - **Property 8: Mod Geçişinde Histogram Korunması**
    - **Doğrular: Gereksinimler 8.4**

  - [x]* 4.5 Gecikme bütçesi config parse round-trip PBT testi yaz
    - **Property 7: Gecikme Bütçesi Config Parse Round-Trip**
    - **Doğrular: Gereksinimler 7.1, 7.2, 7.5**

  - [x]* 4.6 Toplam gözlem her fatura için kaydedilir PBT testi yaz
    - **Property 2: Total Gözlem Her Fatura İçin Kaydedilir**
    - **Doğrular: Gereksinimler 2.1, 2.2, 2.3**

  - [x]* 4.7 Shadow gözlem yalnızca örneklenen faturalarda PBT testi yaz
    - **Property 3: Shadow Gözlem Yalnızca Örneklenen Faturalarda**
    - **Doğrular: Gereksinimler 3.1, 3.2**

  - [x]* 4.8 Enforcement gözlem yalnızca enforcement modlarında PBT testi yaz
    - **Property 4: Enforcement Gözlem Yalnızca Enforcement Modlarında**
    - **Doğrular: Gereksinimler 4.1, 4.2**

  - [x]* 4.9 ValidationBlockedError enforce hard + blocker PBT testi yaz
    - **Property 6: ValidationBlockedError Enforce Hard + Blocker**
    - **Doğrular: Gereksinimler 6.1**

- [x] 5. Regresyon kontrol noktası
  - [x] 5.1 Mevcut 54 fatura doğrulama testinin geçtiğini doğrula
    - Faz A/B testleri: 14 test
    - Faz C testleri: 7 test
    - Faz D testleri: 8 test
    - Faz E testleri: 13 test
    - Faz F testleri: 12 test
    - Faz G testleri: yeni birim + PBT testleri
    - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Her görev izlenebilirlik için belirli gereksinimlere referans verir
- Kontrol noktaları artımlı doğrulama sağlar
- Property testleri evrensel doğruluk özelliklerini doğrular
- Birim testleri belirli örnekleri ve edge case'leri doğrular
- Timer context manager `try/finally` kullanır — exception'lar pipeline'a propagate edilmez
- Histogram varsayılan Prometheus client bucket setini kullanır
- Geçersiz mod parse → varsayılan `shadow` + log fallback
- Phase label'ları kapalı küme: {total, shadow, enforcement}
- Mode label'ları: {off, shadow, enforce_soft, enforce_hard}
