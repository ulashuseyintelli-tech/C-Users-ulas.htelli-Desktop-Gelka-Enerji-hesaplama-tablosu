# Uygulama Planı: CI Enforcement Mode — PR-16

## Genel Bakış

Release governance preflight kontrolünü CI'da raporlama modundan zorunlu moda geçirir. Preflight CLI'ya override flag'leri eklenir, CI workflow enforcement mode'a güncellenir, testler ve dokümantasyon yazılır.

## Görevler

- [x] 1. Preflight CLI override mekanizması
  - [x] 1.1 `release_preflight.py`'ye override flag'leri ve mantığı ekle
    - `--override-reason`, `--override-scope`, `--override-by` argparse flag'leri
    - `_build_override()` yardımcı fonksiyonu: üç flag da sağlandıysa ReleaseOverride oluştur, aksi halde None
    - `_now_ms()` yardımcı fonksiyonu: current time in milliseconds
    - `_OVERRIDE_TTL_SECONDS = 3600` sabiti
    - `run_preflight()` imzasına `override_reason`, `override_scope`, `override_by` parametreleri ekle
    - Override akışı: verdict HOLD + override geçerli → gate re-check → exit 0; verdict BLOCK → exit 2 (override etkisiz); verdict OK → override yok sayılır
    - JSON çıktıya `override_applied`, `override_by`, `override_reason`, `contract_breach`, `contract_breach_detail` alanları ekle
    - Text çıktıya override bilgisi ekle
    - Override flag'leri olmadan davranış PR-15 ile aynı kalmalı (geriye dönük uyumluluk)
    - _Requirements: 2.1-2.5, 3.1-3.3_

  - [x]* 1.2 Override mekanizması unit testleri yaz
    - HOLD verdict + tam override → exit 0 (Req 2.2, 2.3, 5.3)
    - HOLD verdict + override yok → exit 1 (Req 5.2)
    - BLOCK + ABSOLUTE_BLOCK_REASONS + override → exit 2 + CONTRACT_BREACH çıktısı (Req 3.1, 3.2, 5.4)
    - OK verdict + override → exit 0, override yok sayılır (Req 2.5)
    - Kısmi override flag kombinasyonları (6 kombinasyon) → override yok sayılır, exit code değişmez (Property 1, Req 2.4, 5.5)
    - BLOCK verdict + çeşitli override durumları → her zaman exit 2 (Property 2, Req 3.3)
    - Mevcut PR-15 testlerinin hala geçtiğini doğrula (geriye dönük uyumluluk)
    - _Requirements: 5.1-5.5_

- [x] 2. Checkpoint — Preflight CLI testleri
  - Tüm mevcut ve yeni preflight testlerinin geçtiğini doğrula, sorular varsa kullanıcıya sor.

- [x] 3. CI workflow enforcement mode
  - [x] 3.1 `docs/ci/release-governance.yml` güncelle
    - `workflow_dispatch` event'i ve `override_reason`, `override_scope`, `override_by` input'ları ekle
    - Preflight adımından `continue-on-error: true` kaldır
    - Preflight run komutuna conditional override flag'leri ekle (workflow_dispatch input'ları sağlandığında)
    - Step summary'ye override bilgisi ekle
    - Artifact upload ve mevcut raporlama adımlarını koru
    - _Requirements: 1.1-1.5, 4.1-4.4_

  - [x]* 3.2 CI workflow YAML yapısal doğrulama testleri yaz
    - YAML geçerli parse edilir
    - `continue-on-error` preflight adımında yok
    - `workflow_dispatch` input'ları mevcut (override_reason, override_scope, override_by)
    - Artifact upload adımı korunmuş
    - Override flag'leri preflight run komutunda conditional olarak mevcut
    - _Requirements: 1.4, 1.5, 4.1-4.4_

- [x] 4. Dokümantasyon güncellemeleri
  - [x] 4.1 README'ye Enforcement Mode bölümü ekle
    - Enforcement davranışı açıklaması (exit code → job sonucu)
    - Override flag'leri kullanım örnekleri (CLI komutu)
    - Override sözleşmesi: HOLD override edilebilir, BLOCK (ABSOLUTE) asla
    - _Requirements: 6.1, 6.3_

  - [x] 4.2 Runbook'a CI Override Prosedürü bölümü ekle
    - workflow_dispatch ile override adımları
    - Hangi durumlarda override yapılabilir/yapılamaz tablosu
    - Override audit trail açıklaması
    - _Requirements: 6.2_

- [x] 5. Final checkpoint
  - Tüm testlerin geçtiğini doğrula (mevcut + yeni), sorular varsa kullanıcıya sor.

## Notlar

- Yeni karar mantığı yok — mevcut ReleaseGate override mekanizması CLI katmanına taşınır
- Override flag'leri olmadan davranış PR-15 ile birebir aynı (geriye dönük uyumluluk)
- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- HOLD verdict üretmek için testlerde `_build_dry_run_input` yerine kontrollü input kullanılacak (mock/patch)
