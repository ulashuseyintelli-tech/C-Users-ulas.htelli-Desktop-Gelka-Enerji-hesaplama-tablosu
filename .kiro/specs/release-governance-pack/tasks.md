# Uygulama Planı: Release Governance Pack (PR-13)

## Genel Bakış

PR-11 ve PR-12 ile kanıtlanan release-governance zincirini ekip dışına devredilebilir hale getirir. Yeni production karar mantığı yok; çıktılar dokümantasyon, CI komut referansı, spec hash versiyonlama ve paket bütünlüğü testi.

## Görevler

- [x] 1. Spec hash versiyonlama ve reason code tablosu üretimi
  - [x] 1.1 `backend/app/testing/release_version.py` dosyasını oluştur:
    - `spec_hash(base_dir)` fonksiyonu: release_policy.py + release_report.py + release_gate.py dosyalarının SHA-256 hash'i
    - `generate_reason_code_table()` fonksiyonu: `_ACTION_DESCRIPTIONS` + `ABSOLUTE_BLOCK_REASONS` → markdown tablosu
    - Deterministik: aynı dosya içeriği → aynı hash, enum sırasına göre tablo
    - _Requirements: 2.1-2.4, 4.1-4.5_

- [x] 2. Index README
  - [x] 2.1 `.kiro/specs/release-governance/README.md` dosyasını oluştur:
    - Sistem özeti (3-5 cümle)
    - Dosya haritası tablosu (modül → dosya yolu)
    - Test haritası tablosu (test dosyası → ne test ediyor)
    - CI komutları bölümü (tüm testler, sadece unit, sadece PBT, tek modül, seed kullanımı)
    - Reason code → required action tablosu (generate_reason_code_table() çıktısı gömülü)
    - Runbook ve spec referans linkleri
    - _Requirements: 1.1-1.4, 2.4, 3.1-3.4_

- [x] 3. Paket bütünlüğü testi
  - [x] 3.1 `backend/tests/test_release_pack.py` dosyasını oluştur (≥6 unit test + 2 PBT):
    - Import smoke: release_policy, release_report, release_gate, release_version modülleri
    - Instantiation: ReleasePolicy, ReleaseReportGenerator, ReleaseGate
    - spec_hash() not empty + deterministic
    - generate_reason_code_table() not empty + tüm BlockReasonCode'lar mevcut
    - _Requirements: 5.1-5.5_
  - [x]* 3.2 Property test: Spec hash determinizmi
    - **Property 15: Spec hash determinizmi**
    - **Validates: Requirements 4.3, 4.5**
  - [x]* 3.3 Property test: Reason code tablosu bütünlüğü
    - **Property 16: Reason code tablosu bütünlüğü**
    - **Validates: Requirements 2.1, 2.2, 2.3**

- [x] 4. Final checkpoint — Tüm testler
  - Tüm PR-11 + PR-12 + PR-13 testlerinin birlikte geçtiğinden emin ol, 0 flaky test. Sorular varsa kullanıcıya sor.

## Notlar

- `*` ile işaretli görevler opsiyoneldir
- Yeni production karar mantığı yok; sadece utility fonksiyonlar + dokümantasyon + smoke test
- Reason code tablosu `_ACTION_DESCRIPTIONS` dict'inden otomatik üretilir, elle yazılmaz
- `spec_hash()` bağımsız fonksiyon, ReleaseReportGenerator ile entegre edilmez
- DoD: ≥6 unit test + 2 PBT, 0 flaky
