# Uygulama Planı: Release Governance + Change Management (Ops Policy Layer)

## Genel Bakış

PR-10 test disiplini çıktılarını release kararlarına bağlayan üç bileşeni (ReleasePolicy, ReleaseReportGenerator, ReleaseGate) aşamalı olarak implemente eder. Her adım önceki adımın üzerine inşa edilir. Mevcut `perf_budget.py`, `policy_engine.py`, `rollout_orchestrator.py` modüllerini tüketir.

## Görevler

- [x] 1. ReleasePolicy veri modelleri ve karar fonksiyonu
  - [x] 1.1 `backend/app/testing/release_policy.py` dosyasını oluştur: ReleaseVerdict enum, BlockReasonCode enum, ABSOLUTE_BLOCK_REASONS frozenset, RequiredAction dataclass, ReleasePolicyInput dataclass, ReleasePolicyResult dataclass tanımla
    - Mevcut `perf_budget.TierRunResult`, `rollout_orchestrator.DriftSnapshot`, `rollout_orchestrator.PolicyCanaryResult`, `policy_engine.OpsGateStatus` import et
    - _Requirements: 1.1-1.10, 2.1-2.4, 5.1-5.2, 6.1-6.2_
  - [x] 1.2 `ReleasePolicy.evaluate()` metodunu implemente et: girdi doğrulama → mutlak bloklar → tier kontrol → flake kontrol → drift kontrol → canary kontrol → monotonik birleştirme
    - GUARD_VIOLATION ve OPS_GATE_FAIL her zaman BLOCK (override ile geçilemez)
    - Monotonik kural: BLOCK > HOLD > OK, sinyal eklemek kararı asla düşürmez
    - Her HOLD/BLOCK için en az bir RequiredAction üret
    - _Requirements: 1.1-1.10, 5.1-5.2, 6.1-6.2_
  - [x]* 1.3 ReleasePolicy unit testleri yaz (`backend/tests/test_release_policy.py`)
    - Tüm bireysel sinyal kontrolleri (tier fail, flaky test, drift alert, canary breaking, guard violation, ops gate fail)
    - Girdi doğrulama edge case'leri (boş tier listesi, None snapshot'lar)
    - Çoklu sinyal kombinasyonları
    - RequiredAction üretimi kontrolleri
    - En az 25 unit test
    - _Requirements: 1.1-1.10, 2.1-2.4, 5.1-5.2, 6.1-6.2_
  - [x]* 1.4 Property test: Tüm temiz sinyaller → RELEASE_OK
    - **Property 1: Tüm temiz sinyaller → RELEASE_OK**
    - **Validates: Requirements 1.1**
  - [x]* 1.5 Property test: Determinizm
    - **Property 2: Determinizm — aynı girdi → aynı çıktı**
    - **Validates: Requirements 1.8**
  - [x]* 1.6 Property test: HOLD/BLOCK → RequiredAction
    - **Property 3: HOLD/BLOCK → en az bir RequiredAction**
    - **Validates: Requirements 1.9**
  - [x]* 1.7 Property test: Monotonik blok kuralı
    - **Property 4: Monotonik blok kuralı**
    - **Validates: Requirements 1.10, 5.1, 5.2**
  - [x]* 1.8 Property test: Mutlak blok — sözleşme ihlalleri
    - **Property 5: Mutlak blok — sözleşme ihlalleri override edilemez**
    - **Validates: Requirements 1.6, 1.7, 6.1, 6.2, 6.3**

- [x] 2. Checkpoint — ReleasePolicy testleri
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

- [x] 3. ReleaseReportGenerator
  - [x] 3.1 `backend/app/testing/release_report.py` dosyasını oluştur: TierSummary, DriftSummary, OverrideSummary, GuardSummary, ReleaseReport dataclass'larını tanımla
    - _Requirements: 3.1-3.6_
  - [x] 3.2 `ReleaseReportGenerator` sınıfını implemente et: `generate()`, `format_text()`, `to_dict()`, `from_dict()` metodları
    - `generate()`: ReleasePolicyResult + ReleasePolicyInput → ReleaseReport (deterministik)
    - `format_text()`: İnsan okunabilir düz metin formatı
    - `to_dict()` / `from_dict()`: JSON round-trip desteği
    - Her tier için en fazla 10 yavaş test, doğru bütçe kullanım yüzdesi
    - _Requirements: 3.1-3.10_
  - [x]* 3.3 ReleaseReportGenerator unit testleri yaz (`backend/tests/test_release_report.py`)
    - Tier özeti doğruluğu (yavaş test limiti, bütçe yüzdesi)
    - Metin formatı yapısal kontrolleri
    - JSON serileştirme kontrolleri
    - Boş/None girdi edge case'leri
    - En az 10 unit test
    - _Requirements: 3.1-3.10_
  - [x]* 3.4 Property test: Rapor bütünlüğü
    - **Property 6: Rapor bütünlüğü**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
  - [x]* 3.5 Property test: Rapor determinizmi
    - **Property 7: Rapor determinizmi**
    - **Validates: Requirements 3.7**
  - [x]* 3.6 Property test: ReleaseReport round-trip
    - **Property 8: ReleaseReport round-trip (serileştirme)**
    - **Validates: Requirements 3.10**

- [x] 4. Checkpoint — ReleaseReportGenerator testleri
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

- [x] 5. ReleaseGate (Enforcement Hook)
  - [x] 5.1 `backend/app/testing/release_gate.py` dosyasını oluştur: ReleaseOverride dataclass, GateDecision dataclass, ReleaseGate sınıfı
    - _Requirements: 4.1-4.7, 6.3_
  - [x] 5.2 `ReleaseGate.check()` metodunu implemente et
    - RELEASE_OK → allowed=True
    - RELEASE_BLOCK → allowed=False (mutlak blok nedenlerinde override reddi)
    - RELEASE_HOLD → override varsa TTL + scope doğrula, geçerliyse izin ver
    - Her karar için audit kaydı oluştur
    - GUARD_VIOLATION/OPS_GATE_FAIL kaynaklı BLOCK'ta override girişimini "CONTRACT_BREACH_NO_OVERRIDE" ile reddet
    - _Requirements: 4.1-4.7, 6.3_
  - [x]* 5.3 ReleaseGate unit testleri yaz (`backend/tests/test_release_gate.py`)
    - OK/BLOCK/HOLD temel akışları
    - Override TTL sınır koşulları
    - Mutlak blok override reddi
    - En az 3 unit test (entegrasyon odaklı)
    - _Requirements: 4.1-4.7, 6.3_
  - [x]* 5.4 Property test: Gate verdict uyumu
    - **Property 9: Gate verdict uyumu**
    - **Validates: Requirements 4.1, 4.2, 4.3**
  - [x]* 5.5 Property test: Override doğrulama
    - **Property 10: Override doğrulama**
    - **Validates: Requirements 4.4, 4.5, 4.6**
  - [x]* 5.6 Property test: Audit kaydı
    - **Property 11: Audit kaydı**
    - **Validates: Requirements 4.7**

- [x] 6. Final checkpoint — Tüm testler
  - Tüm testlerin geçtiğinden emin ol, 0 flaky test, tier bütçeleri korunur. Sorular varsa kullanıcıya sor.

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Her görev spesifik gereksinimleri referans alır
- Checkpoint'ler aşamalı doğrulama sağlar
- Property testleri evrensel doğruluk özelliklerini doğrular
- Unit testler spesifik örnekleri ve edge case'leri doğrular
- DoD: ReleasePolicy ≥25 unit + 5 PBT, ReleaseReport ≥10 unit, ReleaseGate ≥3 entegrasyon testi
