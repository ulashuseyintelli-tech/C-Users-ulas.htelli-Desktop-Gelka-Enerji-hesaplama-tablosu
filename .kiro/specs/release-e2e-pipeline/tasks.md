# Uygulama Planı: End-to-End Release Pipeline Simulation (PR-12)

## Genel Bakış

PR-11'de ayrı ayrı kanıtlanan ReleasePolicy, ReleaseReportGenerator ve ReleaseGate bileşenlerini tek bir zincir olarak birleştirip uçtan uca doğrular. Orchestrator entegrasyonunu simüle eder ve deterministik "golden" audit artifact'ları üretir.

## Görevler

- [x] 1. E2E test altyapısı ve temel senaryo testleri
  - [x] 1.1 `backend/tests/test_release_e2e.py` dosyasını oluştur: `_run_pipeline()` helper metodu (input → policy → report → gate → orchestrator zinciri)
    - Her katmanın çıktısını tuple olarak döndür
    - Orchestrator'ı sadece gate allowed ise çalıştır
    - _Requirements: 1.1-1.8, 2.1-2.5, 4.1-4.4_
  - [x] 1.2 E2E senaryo testleri yaz (≥8 unit test):
    - Tüm temiz → OK → allowed → orchestrator executes
    - Tier fail → HOLD → denied → no effects
    - OPS_GATE_FAIL → BLOCK → denied → override rejected (CONTRACT_BREACH)
    - GUARD_VIOLATION → BLOCK → denied → override rejected (CONTRACT_BREACH)
    - Canary BREAKING → HOLD → denied
    - HOLD + geçerli override → allowed → orchestrator executes
    - HOLD + expired override → denied
    - HOLD + scope mismatch override → denied
    - _Requirements: 1.1-1.8_

- [x] 2. Zincir bütünlüğü testleri
  - [x] 2.1 Cross-layer invariant testleri yaz (≥3 unit test):
    - Policy verdict == gate verdict (her senaryoda)
    - Policy reasons == rapor reasons (her senaryoda)
    - Gate denied → orchestrator applied_count değişmez
    - _Requirements: 2.1-2.5_

- [x] 3. Golden audit artifact testleri
  - [x] 3.1 Golden snapshot testleri yaz (≥3 unit test):
    - OK senaryosu: sabit girdi → deterministik JSON + text + gate detail
    - HOLD senaryosu: sabit girdi → deterministik JSON + text + gate detail
    - BLOCK senaryosu: sabit girdi → deterministik JSON + text + gate detail + breach audit
    - Her snapshot'ın iki kez üretildiğinde byte-level eşit olduğunu doğrula
    - _Requirements: 3.1-3.6_

- [x] 4. Yan etki izolasyonu testleri
  - [x] 4.1 Side-effect isolation testleri yaz:
    - HOLD → orchestrator.applied_count = 0
    - BLOCK → orchestrator.applied_count = 0
    - OK → orchestrator.applied_count > 0
    - Override ile geçilen HOLD → orchestrator.applied_count > 0
    - _Requirements: 4.1-4.4_

- [x] 5. Property-based testler
  - [x]* 5.1 Property test: E2E zincir determinizmi
    - **Property 12: E2E Zincir Determinizmi**
    - **Validates: Requirements 5.1**
  - [x]* 5.2 Property test: Gate-Orchestrator yan etki izolasyonu
    - **Property 13: Gate-Orchestrator Yan Etki İzolasyonu**
    - **Validates: Requirements 5.2, 4.1, 4.2**
  - [x]* 5.3 Property test: Mutlak blok zincir garantisi
    - **Property 14: Mutlak Blok Zincir Garantisi**
    - **Validates: Requirements 5.3, 1.3, 1.4**

- [x] 6. Final checkpoint — Tüm testler
  - Tüm PR-11 + PR-12 testlerinin birlikte geçtiğinden emin ol, 0 flaky test. Sorular varsa kullanıcıya sor.

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Yeni modül/sınıf oluşturulmaz; sadece test dosyası
- Mevcut PR-11 bileşenleri import edilir, değiştirilmez
- Golden artifact'lar test içinde inline olarak doğrulanır (ayrı dosya değil)
- DoD: ≥14 unit test + 3 PBT, 0 flaky
