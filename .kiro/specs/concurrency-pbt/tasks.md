# Uygulama Planı: Concurrency PBT — Guard Decision Layer

## Genel Bakış

Guard Decision Layer'ın eşzamanlı request'ler altında doğruluk garantilerini kanıtlayan 5 Hypothesis PBT property testi. Harness utilities + snapshot capture + 5 property + evidence matrix.

## Görevler

- [x] 1. Harness utilities oluştur
  - [x] 1.1 `backend/tests/concurrency_harness.py` dosyasını oluştur
    - `parallel_snapshot_builds(build_args_list, max_workers=20)` — ThreadPoolExecutor ile paralel build
    - `make_test_config(**overrides)` — tenant-aware test config factory
    - `TENANT_MODES_JSON` / `TENANT_ALLOWLIST_JSON` sabitleri (tenantA=enforce, tenantB=shadow, tenantC=off)
    - _Requirements: C1, C2, C7_

- [x] 2. P-C1: Tenant Isolation property testi (200 examples)
  - [x] 2.1 `backend/tests/test_concurrency_pbt.py` dosyasını oluştur, P-C1 property'sini yaz
    - Rastgele tenant dizisi (A/B/C/X) ile paralel `SnapshotFactory.build()`
    - Her snapshot'ın `tenant_id` değeri input ile eşleşir
    - Her snapshot'ın `tenant_mode` değeri `resolve_tenant_mode()` sonucuyla eşleşir
    - Hiçbir snapshot'ta başka tenant'ın mode'u görünmez
    - _Requirements: C1.1, C1.2, C1.3_

- [x] 3. P-C2: Hash Determinism property testi (200 examples)
  - [x] 3.1 P-C2 property'sini `test_concurrency_pbt.py`'ye ekle
    - Aynı tenant + endpoint + config + now_ms ile 50 paralel build
    - Tüm hash'ler eşit
    - _Requirements: C2.1, C2.2_

- [x] 4. P-C3: Mode Freeze vs Mid-Flight Change property testi (200 examples)
  - [x] 4.1 P-C3 property'sini `test_concurrency_pbt.py`'ye ekle
    - Snapshot build → config değiştir → snapshot.tenant_mode değişmemiş
    - Yeni build yeni config'i kullanıyor
    - Frozen dataclass mutation attempt → FrozenInstanceError
    - _Requirements: C4.1, C4.2_

- [x] 5. P-C4: Metrics Monotonic Under Concurrency property testi (200 examples)
  - [x] 5.1 P-C4 property'sini `test_concurrency_pbt.py`'ye ekle
    - Paralel 50 build, block tetikleyen config ile
    - Counter `after >= before` (non-decreasing)
    - _Requirements: C5.1, C5.2_

- [x] 6. P-C5: Fail-Open Containment property testi (200 examples)
  - [x] 6.1 P-C5 property'sini `test_concurrency_pbt.py`'ye ekle
    - Paralel build'lerin %30'unda crash inject (monkeypatch)
    - Crash'li build'ler None döner
    - Crash'siz build'ler valid snapshot döner
    - Sistem deadlock olmaz (timeout)
    - _Requirements: C7.1, C7.2, C7.3_

- [x] 7. Checkpoint — Tüm PBT testleri geçiyor
  - Tüm 5 property testinin geçtiğinden emin ol
  - 0 diagnostic
  - _Requirements: C1–C7_

- [x] 8. Evidence + proof matrix
  - [x] 8.1 Her requirement satırında test referansı ekle
    - C1 → P-C1, C2 → P-C2, C3 → P-C1+P-C2 (implicit), C4 → P-C3, C5 → P-C4, C6 → tenant-enable I1, C7 → P-C5
    - _Requirements: tümü_

## Proof Matrix

| Requirement | Property Test | Açıklama |
|-------------|--------------|----------|
| C1.1, C1.2, C1.3 | P-C1 | Tenant isolation — snapshot sızma yok |
| C2.1, C2.2 | P-C2 | Hash determinism — paralel build aynı hash |
| C3.1, C3.2 | P-C1 + P-C2 | Implicit — frozen dataclass + pure functions |
| C4.1, C4.2 | P-C3 | Mode freeze — mid-flight config change |
| C5.1, C5.2 | P-C4 | Metrics monotonic — counter non-decreasing |
| C6.1, C6.2 | tenant-enable I1 | Middleware bypass — zaten kanıtlandı |
| C7.1, C7.2, C7.3 | P-C5 | Fail-open containment — crash inject |

## Notlar

- C6 (middleware bypass correctness) tenant-enable spec'inin I1 entegrasyon testiyle zaten kanıtlandı; burada tekrar edilmez
- Her property `@settings(max_examples=200)` ile çalışır
- ThreadPoolExecutor default `max_workers=20`
- Hypothesis `deadline=None` (concurrency testlerinde deadline false positive üretir)
- Test-level timeout: 60s per property
