# Uygulama Planı: Tenant-Level Guard Decision Override

## Genel Bakış

Mevcut Guard Decision Layer'a tenant bazlı override yeteneği eklenir. Config alanları → pure fonksiyonlar → snapshot entegrasyonu → middleware wiring sırasıyla ilerler. Her adım önceki adımın üzerine inşa edilir.

## Görevler

- [x] 1. GuardConfig'e tenant config alanları ekle
  - [x] 1.1 `decision_layer_default_mode`, `decision_layer_tenant_modes_json`, `decision_layer_tenant_allowlist_json` alanlarını `backend/app/guard_config.py`'ye ekle
    - `decision_layer_default_mode`: str, default="shadow", validator ile "shadow"|"enforce"|"off" kısıtlaması
    - `decision_layer_tenant_modes_json`: str, default=""
    - `decision_layer_tenant_allowlist_json`: str, default=""
    - `_FALLBACK_DEFAULTS` dict'ine yeni alanların default değerlerini ekle
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [ ]* 1.2 GuardConfig tenant alanları için unit test yaz
    - Geçerli default_mode değerleri kabul edilir
    - Geçersiz default_mode değeri validator hatası üretir
    - Boş JSON string'ler kabul edilir
    - _Requirements: 3.1, 3.4_

- [x] 2. TenantMode enum ve parse fonksiyonları implement et
  - [x] 2.1 `TenantMode` enum'unu `backend/app/guards/guard_decision.py`'ye ekle
    - `SHADOW = "shadow"`, `ENFORCE = "enforce"`, `OFF = "off"`
    - _Requirements: 2.1_

  - [x] 2.2 `parse_tenant_modes(raw_json: str) → dict[str, TenantMode]` fonksiyonunu implement et
    - Geçersiz JSON → boş dict + log (fail-open)
    - Geçersiz mod değeri olan tenant'lar atlanır + log
    - `backend/app/guards/guard_decision.py`'ye ekle
    - _Requirements: 3.5, 3.6, 4.1_

  - [x] 2.3 `parse_tenant_allowlist(raw_json: str) → frozenset[str]` fonksiyonunu implement et
    - Geçersiz JSON → boş frozenset + log (fail-open)
    - `backend/app/guards/guard_decision.py`'ye ekle
    - _Requirements: 3.3, 4.2_

  - [x]* 2.4 Parse fonksiyonları için property test yaz (Hypothesis)
    - **Property 3: Fail-Open JSON Parse Güvenliği**
    - **Validates: Requirements 3.5, 4.1, 4.2, 4.3**

  - [x]* 2.5 Parse fonksiyonları için property test yaz (Hypothesis)
    - **Property 4: Geçersiz Mod Değeri Filtreleme**
    - **Validates: Requirements 3.6**

- [x] 3. Tenant mod çözümleme fonksiyonlarını implement et
  - [x] 3.1 `sanitize_tenant_id(raw: str | None) → str` fonksiyonunu implement et
    - None veya boş string → "default"
    - `backend/app/guards/guard_decision.py`'ye ekle
    - _Requirements: 2.4, 7.1_

  - [x] 3.2 `resolve_tenant_mode(tenant_id, default_mode, tenant_modes) → TenantMode` pure fonksiyonunu implement et
    - tenant_id normalize et (sanitize_tenant_id)
    - Map'te varsa o mod, yoksa default_mode
    - `backend/app/guards/guard_decision.py`'ye ekle
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x]* 3.3 resolve_tenant_mode için property test yaz (Hypothesis)
    - **Property 2: Tenant Çözümleme Determinizmi**
    - **Validates: Requirements 2.1, 2.2, 2.3**

- [x] 4. Checkpoint — Parse ve resolve fonksiyonları doğrulaması
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

- [x] 5. GuardDecisionSnapshot ve SnapshotFactory güncelle
  - [x] 5.1 `GuardDecisionSnapshot`'a `tenant_mode: TenantMode` alanı ekle
    - Frozen dataclass'a yeni alan eklenir
    - `backend/app/guards/guard_decision.py`'de güncelle
    - _Requirements: 5.3_

  - [x] 5.2 `SnapshotFactory.build()`'i güncelle: tenant mode çözümlemesi ekle
    - `parse_tenant_modes` ile config'ten tenant_modes map'i parse et
    - `resolve_tenant_mode` ile tenant mode çöz
    - Snapshot'a `tenant_mode` dahil et
    - `default_mode` parametresini `TenantMode` olarak parse et (geçersiz → SHADOW fallback)
    - _Requirements: 5.1, 5.2_

  - [x]* 5.3 Snapshot tenant_mode doğruluğu için property test yaz (Hypothesis)
    - **Property 5: Snapshot Tenant Mode Doğruluğu**
    - **Validates: Requirements 5.1, 5.2, 6.4**

- [x] 6. Metrik tenant sanitizasyonu implement et
  - [x] 6.1 `sanitize_metric_tenant(tenant_id, allowlist) → str` fonksiyonunu implement et
    - Allowlist'te varsa → tenant_id, yoksa → "_other"
    - `backend/app/guards/guard_decision.py`'ye ekle
    - _Requirements: 8.1, 8.2, 8.3_

  - [x]* 6.2 Metrik tenant sanitizasyonu için property test yaz (Hypothesis)
    - **Property 6: Metrik Tenant Sanitizasyonu**
    - **Validates: Requirements 8.1, 8.2, 8.3**

- [x] 7. Middleware'i tenant mode ile güncelle
  - [x] 7.1 `guard_decision_middleware.py`'deki `_evaluate_decision` metodunu güncelle
    - Global OFF kontrolü korunur
    - `resolve_tenant_mode` ile tenant mode çöz
    - `tenant_mode == OFF` → passthrough (snapshot build etme)
    - `tenant_mode == SHADOW` → build + evaluate, BLOCK'ta log+metrik, no block
    - `tenant_mode == ENFORCE` → build + evaluate, BLOCK'ta 503
    - Mevcut global `decision_layer_mode` kullanımını tenant bazlı mode ile değiştir
    - Metrik emisyonunda `sanitize_metric_tenant` kullan
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 8.1_

  - [ ]* 7.2 Middleware tenant mode entegrasyonu için unit test yaz
    - tenant_mode=off → passthrough
    - tenant_mode=shadow → BLOCK verdict'te log+metrik, request geçer
    - tenant_mode=enforce → BLOCK verdict'te 503
    - tenantA=enforce, tenantB=shadow, default=off → doğru davranış
    - _Requirements: 6.1, 6.2, 6.3_

- [x] 8. Global OFF önceliği property testi
  - [x]* 8.1 Global OFF önceliği için property test yaz (Hypothesis)
    - **Property 1: Global OFF Önceliği**
    - **Validates: Requirements 1.1**

- [x] 9. End-to-end entegrasyon unit testleri
  - [x]* 9.1 Entegrasyon senaryoları için unit test yaz
    - tenantA=enforce, tenantB=shadow, default=off → her tenant doğru davranış
    - JSON bozuk → default mode uygulanır
    - Mid-flight config değişikliği snapshot'ı etkilemez (snapshot determinism)
    - _Requirements: 1.1, 2.2, 2.3, 4.1, 5.2_

- [x] 10. Final checkpoint — Tüm testler geçiyor
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Her görev spesifik gereksinimleri referans eder (izlenebilirlik)
- Checkpoint'ler artımlı doğrulama sağlar
- Property testler evrensel doğruluk özelliklerini doğrular
- Unit testler spesifik örnekleri ve edge case'leri doğrular
- `tenant_id` snapshot'ın birinci sınıf parametresi olarak kalır (sonraki concurrency PBT spec'i için)
