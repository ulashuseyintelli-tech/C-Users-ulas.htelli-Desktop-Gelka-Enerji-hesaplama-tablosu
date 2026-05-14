# Tasks — PTF Single Source of Truth Unification

## Yaklaşım

4 faz, her faz kendi başına git commit + PR. Her faz başında/sonunda golden baseline doğrulaması. Kill switch (`USE_LEGACY_PTF`) Phase 1'de eklenir, Phase 4'te silinir.

**Bağımlılık kuralı:** Phase sırası kesindir. Bir phase DoD'si karşılanmadan sonraki phase başlamaz. Her phase commit edilir ve baseline tekrarlanır.

**Kilit kararlar (tartışma kapalı):**
- Canonical = `hourly_market_prices` (source-of-truth.md steering §1)
- Fallback yasağı: canonical'da veri yoksa HTTP 409 (aylık→saatlik türetme yasak)
- Kill switch: `USE_LEGACY_PTF=true` → 10 saniye rollback
- Dual-read penceresi: 7-14 gün, drift gate %0.5 üstü durdurur
- Hard delete Phase 4'te; legacy tablo ve tüm legacy kodu aynı PR'da silinir

**Önkoşul kontrolü (bu spec başlamadan):**
- [x] `codebase-audit-cleanup` Phase A tamam (10/10)
- [ ] `baselines/<tarih>_pre-ptf-unification_baseline.json` commit edilmiş (B1 DoD)
- [x] `backend/tests/test_main_wiring_invariant.py` CI'da (B10 DoD)
- [x] `.kiro/steering/source-of-truth.md` aktif (P-A DoD)

---

## Phase 1 — Write Lock + Kill Switch (R1, R3, R6 kısmi)

Amaç: PTF yazma yollarını canonical'a kilitle, kill switch'i kur, henüz okuma değiştirme. Bu faz **geri dönüşümlü**: problem çıkarsa tek commit revert.

### [ ] T1.1 — B1 baseline pre-migration (ÖN KOŞUL)
- **Giriş:** Canlı backend, 30 senaryo matrisi
- **Çıktı:** `baselines/<tarih>_pre-ptf-unification_baseline.json` + git commit
- **Kanıt:**
  - `codebase-audit-cleanup/scripts/09_golden_baseline.py` çalıştırıldı, exit 0
  - Dosya git'e commit edilmiş
  - `_meta.scenario_count == 30`, `expected_409_count == 6` (2025-12 × 2 × 3)
  - git sha pre-migration commit'i
- **⚠️ ÖN KOŞUL (pricing-cache-key-completeness):** İlk baseline denemesi (`baselines/2026-05-12_pre-ptf-unification_baseline.json`) v1 cache key ile yakalandı ve kontamine oldu. Dosya `INVALIDATED` olarak işaretli. Bu task **rerun** olarak çalıştırılmalı; yeni baseline dosyası `baselines/<YYYY-MM-DD>_pre-ptf-unification_baseline_v2.json` adıyla v2 cache key ile yakalanacak. Rerun öncesi `pricing-cache-key-completeness` spec'i merge edilmiş olmalı (v2 key deploy edilmiş).

### [ ] T1.2 — `guard_config` kill switch alanları
- **Giriş:** `backend/app/guard_config.py`
- **Çıktı:** `use_legacy_ptf: bool = False`, `ptf_drift_log_enabled: bool = False` alanları eklendi
- **Kanıt:**
  - Pydantic BaseSettings şeması env vars'tan okur: `USE_LEGACY_PTF`, `PTF_DRIFT_LOG_ENABLED`
  - Default: ikisi de False (Phase 1'de drift log henüz etkin değil)
  - `backend/tests/test_guard_config.py` yeni alanları doğrulayan test ekler
  - `get_guard_config()` LRU cache varsa kaldırılır (10 saniye rollback garantisi — design §2.3)

### [ ] T1.3 — `PtfDriftLog` modeli + alembic 012
- **Giriş:** `backend/alembic/versions/`
- **Çıktı:** `012_ptf_drift_log.py` migration + `backend/app/ptf_drift_log.py` modeli
- **Kanıt:**
  - `alembic upgrade head` → `ptf_drift_log` tablosu oluşturulur
  - `alembic downgrade -1` → temiz geri alma
  - Tablo şeması design §3.1'deki alanlarla eşleşir
  - `backend/tests/test_ptf_drift_log_model.py` — tablo CRUD testi (1 insert, 1 select, severity='high' filtresi)

### [ ] T1.4 — Canonical yazma yolları — `/api/epias/prices/{period}` POST
- **Giriş:** `backend/app/main.py` epias POST endpoint (line 4544)
- **Çıktı:** Endpoint EPİAŞ API'den gelen saatlik veriyi `hourly_market_prices`'a yazar (mevcut: `market_reference_prices`)
- **Kanıt:**
  - EPİAŞ API response'u 24×day saatlik satır olarak parse edilir
  - `HourlyMarketPrice` satırları oluşturulur, `is_active=1`, mevcut aktif kayıtlar `is_active=0` ile arşivlenir
  - Eski kod path'i `if config.use_legacy_ptf:` branch'i altında korunur (rollback için)
  - `backend/tests/test_epias_prices_api.py` — POST sonrası canonical tabloda 24×day satır kontrolü
  - `baselines/09_...` rerun → hash değişmemeli (response şeması aynı)

### [ ] T1.5 — Manuel yazma yollarını devre dışı bırak
- **Giriş:** `backend/app/market_prices.py::upsert_market_price`, `backend/app/main.py::_add_sample_market_prices`, `backend/app/seed_market_prices.py`, `backend/app/bulk_importer.py`
- **Çıktı:** Her biri 409 döner veya no-op loglar
- **Kanıt:**
  - `POST /admin/market-prices` (formdata) → 409 `manual_ptf_disabled`, mesaj design §U2'deki metin
  - `_add_sample_market_prices` → erken return + `logger.warning("ptf_seed_disabled_use_canonical")`
  - `seed_market_prices.py` zaten orphan; hiçbir yerden çağrılmıyor, ama fonksiyon içi assert eklenir (`raise NotImplementedError`)
  - `bulk_importer.py` PTF satırlarında `raise HTTPException(409, "bulk_ptf_disabled")` — YEKDEM satırlarını etkilemez
  - `backend/tests/test_manual_ptf_write_disabled.py` — 4 endpoint/fonksiyon için 409 veya NotImplementedError doğrulaması

### [ ] T1.6 — Kill switch davranışı testleri
- **Giriş:** T1.2-T1.5 çıktıları
- **Çıktı:** `backend/tests/test_ptf_kill_switch.py`
- **Kanıt:**
  - Test A: `USE_LEGACY_PTF=false` iken `/api/pricing/analyze` canonical tabloyu okur (mevcut davranış)
  - Test B: `USE_LEGACY_PTF=true` iken aynı endpoint legacy tabloyu okur + log `ptf_legacy_fallback_active`
  - Test C: Env değişkeni toggle edildikten sonra bir sonraki request'te etkili (`monkeypatch.setenv` sonrası yeni client call)
  - Prometheus metriği `ptf_legacy_fallback_total{period}` artar

### [ ] T1.7 — Phase 1 baseline doğrulaması
- **Giriş:** T1.2-T1.6 merge edildi
- **Çıktı:** `baselines/<tarih>_post-phase-1_baseline.json` + karşılaştırma raporu
- **Kanıt:**
  - Baseline script çalıştırıldı
  - `scripts/10_baseline_compare.py <pre> <post>` exit code 0 (tüm matched hash'ler eşit)
  - 2025-12 için 6/6 senaryo hâlâ 404/409
  - `test_main_wiring_invariant` CI'da yeşil (4 pass + 2 xfail)

### [ ] T1.8 — Phase 1 PR merge + steering güncelle
- **Çıktı:** Phase 1 branch `main`'e merge edilir; `source-of-truth.md` §1 PTF satırı `migration_status=write_locked` olarak güncellenir
- **Kanıt:**
  - PR merge commit'inde `phase-1-write-lock` tag'i
  - Steering değişikliği steering §7 "kanıt zinciri" kuralına uygun (artifact referansı dahil)

---

## Phase 2 — Dual-Read + Drift Log (R4, R10)

Amaç: Canonical ve legacy okumaları yan yana tut, farkı ölç, response sadece canonical. **Süre: 7-14 gün**, gate kriteri: ortalama drift ≤0.5%.

### [ ] T2.1 — Dual-read implementation
- **Giriş:** `backend/app/pricing/router.py::_load_market_records`
- **Çıktı:** `_load_market_records_dual` fonksiyonu + dispatcher branch
- **Kanıt:**
  - Dual-read aktifken: canonical okur (strict, 409 atar veri yoksa), legacy'den paralel okur, drift log yazar, canonical'ı return eder
  - Legacy okuma başarısız → `legacy_value=None`, drift severity=low (design §3.2)
  - `backend/tests/test_ptf_dual_read.py` — her iki tablo değer testleri, drift log yazıldığı doğrulanır

### [ ] T2.2 — Drift log recorder
- **Giriş:** `backend/app/ptf_drift_log.py`
- **Çıktı:** `record_drift()` fonksiyonu + `compute_drift()` helper
- **Kanıt:**
  - `compute_drift` design §3.2'deki edge case'leri handle eder (legacy None, canonical 0, ikisi de 0)
  - Sync DB insert (async değil — Phase 2 pencere kısa, design §U3)
  - `backend/tests/test_ptf_drift_computation.py` — hypothesis property: `compute_drift(a, b).diff_abs == compute_drift(b, a).diff_abs`

### [x] T2.3 — Drift metrikleri + Prometheus
- **Giriş:** `backend/app/ptf_metrics.py`
- **Çıktı:** `ptf_drift_observed_total{period,severity}`, `ptf_canonical_monthly_avg{period}` metrikleri
- **Kanıt:**
  - Her drift_record çağrısı counter'ı artırır
  - `GET /metrics` endpoint'i bu metrikleri expose eder
  - Grafana panel eklenir (monitoring/grafana/ptf-migration-dashboard.json)

### [ ] T2.4 — `ptf_drift_log_enabled=True` toggle
- **Giriş:** guard_config default
- **Çıktı:** Default değer Phase 2'de True olur; env override edilebilir
- **Kanıt:**
  - Deployment config'i güncellenir (docker-compose/env file)
  - Smoke test: 1 `/api/pricing/analyze` sonrası `ptf_drift_log` tablosunda 1 satır

### [ ] T2.5 — Phase 2 pencere metrikleri izleme
- **Giriş:** Canlı sistem, 7-14 gün
- **Çıktı:** `artifacts/phase2_drift_period.md` — günlük snapshot kayıtları
- **Kanıt:**
  - Günlük: `scripts/11_drift_analysis.py --tail 24h` raporu alınır
  - `severity=high` toplam sayısı, etkilenen dönem sayısı, p95 diff_percent kayıt altında
  - 7 gün sonra pencere kapatılabilir veya 14 gün max sürebilir

### [ ] T2.6 — Drift analizi + Phase 2 → 3 gate kararı
- **Giriş:** `scripts/11_drift_analysis.py` (yeni yazılır)
- **Çıktı:** `artifacts/phase2_drift_decision.md` — karar + gerekçe + metrikler
- **Kanıt:**
  - Gate kriterleri (R10):
    - Ortalama `diff_percent` ≤ 0.5% tüm dönemler için → PASS
    - `severity=high` oranı ≤ %5 → PASS
  - Herhangi bir kriter başarısız → user-decision (karar metinli dokümana yazılır)
  - Karar git commit edilir

### [ ] T2.7 — Phase 2 baseline doğrulaması
- **Giriş:** T2.1-T2.6 merge
- **Çıktı:** `baselines/<tarih>_post-phase-2_baseline.json`
- **Kanıt:**
  - Response hash'leri Phase 1 sonrası ile eşit (dual-read response'u değiştirmez, sadece log yazar)
  - Dual-read Phase 3'e geçmeden önce kapanmaz; baseline bu pencerede alınır

### [ ] T2.8 — Phase 2 PR merge
- **Çıktı:** Dual-read + drift log canlıda; `source-of-truth.md` `migration_status=dual_read_observing`
- **Kanıt:** Merge commit + tag `phase-2-dual-read`

---

## Phase 3 — Single Read (Legacy Kapalı)

Amaç: Dual-read'i kaldır, canonical-only yapıya geç, legacy okuma yollarının hepsini sil. Tablo henüz silinmez (Phase 4 işi).

### [ ] T3.1 — Phase 2 gate onayı (ön koşul)
- **Çıktı:** `artifacts/phase2_drift_decision.md` kararı "PASS" OLMALI
- **Kanıt:** T2.6 çıktısı; user-decision gerekiyorsa yazılı onay

### [ ] T3.2 — Dual-read kod yolunu kaldır
- **Giriş:** `backend/app/pricing/router.py::_load_market_records`
- **Çıktı:** `_load_market_records_dual` silinir; dispatcher `strict` branch'e indirgenir
- **Kanıt:**
  - Tek path: `if config.use_legacy_ptf: legacy else: strict`
  - `ptf_drift_log_enabled` flag'i default=False olur (kullanılmıyor)
  - `record_drift()` referansları kaldırılır

### [ ] T3.3 — `market_prices.py` legacy okumalar silinir
- **Giriş:** `backend/app/market_prices.py`
- **Çıktı:** `get_market_prices()`, `get_latest_market_prices()` fonksiyonları silinir (tüm dosya düşer)
- **Kanıt:**
  - Dosya kaldırılır veya sadece import stub kalır (geriye uyumluluk için import hata vermemeli)
  - Callers: `main.py`'de bu fonksiyonları çağıran yer varsa 409 veya canonical'a yönlendirilir
  - Grep: `from app.market_prices import` kalmamalı üretim kodunda

### [ ] T3.4 — Lock/unlock endpoint'leri kaldır
- **Giriş:** `backend/app/main.py` — `POST /admin/market-prices/{period}/lock|unlock` (line 3908+)
- **Çıktı:** Endpoint'ler silinir
- **Kanıt:**
  - A3 envanteri rerun: endpoint sayısı 91 → 89
  - FE çağrısı yoksa (A5 dual_fe_client kontrolü) deprecated `api.ts::lockMarketPrice` fonksiyonu da silinir (pricing-consistency-fixes spec ile koordineli)
  - Lock kavramı yeni admin UI tasarımında (ptf-admin-frontend) yeniden düşünülür

### [ ] T3.5 — `yekdem_service.py` legacy fallback kaldır
- **Giriş:** `backend/app/pricing/yekdem_service.py` lines 123-152
- **Çıktı:** Legacy fallback + mirror bloğu silinir
- **Kanıt:**
  - Fonksiyon sadece `monthly_yekdem_prices`'tan okur; veri yoksa None döner
  - Caller `calculator.py`'de `yekdem=0.0` fallback'i de kaldırılır → 409 `yekdem_data_not_found` (R7.4)
  - `backend/tests/test_yekdem_service.py` — mirror davranışı YOK testi

### [ ] T3.6 — `seed_market_prices.py` dosyası silinir
- **Giriş:** `backend/app/seed_market_prices.py`
- **Çıktı:** Dosya kaldırılır
- **Kanıt:**
  - `test_main_wiring_invariant::test_rule1_every_app_module_is_reachable` — `app.seed_market_prices` KNOWN_ORPHAN_MODULES_XFAIL'dan çıkarılır
  - Test PASS; orphan liste 9 → 8

### [ ] T3.7 — CI guard strict mode — xfail kaldır
- **Giriş:** `backend/tests/test_main_wiring_invariant.py`
- **Çıktı:** `test_rule2_no_new_legacy_ptf_writers` xfail'siz PASS
- **Kanıt:**
  - Production kodda `INSERT INTO market_reference_prices` veya `UPDATE market_reference_prices` yok (alembic hariç)
  - Pattern bulunursa test FAIL; yeni yazıcı eklemeye çalışanlar CI'da yakalanır

### [ ] T3.8 — Phase 3 baseline doğrulaması
- **Giriş:** T3.2-T3.7 merge
- **Çıktı:** `baselines/<tarih>_post-phase-3_baseline.json`
- **Kanıt:**
  - Byte-wise eşit: 25/30 senaryo (2025-12 × 2 × 3 = 5 senaryo 409 kalır)
  - `/api/epias/prices/{period}` GET için özel tolerance kararı uygulanır (design §U5)
  - Eğer regresyon varsa → Phase 3 revert

### [ ] T3.9 — Phase 3 PR merge
- **Çıktı:** Canonical-only davranış canlı; steering `migration_status=single_read_canonical`
- **Kanıt:** Merge commit + tag `phase-3-single-read`

---

## Phase 4 — Hard Delete

Amaç: `market_reference_prices` tablosu, tüm legacy kod, kill switch mekanizması silinir. "Yarım migration" riski ortadan kalkar.

### [ ] T4.1 — Legacy tablo FK'lerini temizle
- **Giriş:** `backend/app/database.py::PriceChangeHistory.price_record_id`
- **Çıktı:** FK `ForeignKey("market_reference_prices.id", ...)` kaldırılır; kolon nullable=True yapılır veya silinir
- **Kanıt:**
  - Alembic migration: `alter_column` + `drop_constraint`
  - `price_type + period` denormalized alanlar yeterli audit trail (mevcut kolonlar)
  - Karar: kolon silinir (denormalized alanlar zaten var)

### [ ] T4.2 — `market_reference_prices` DROP
- **Giriş:** `backend/alembic/versions/013_drop_market_reference_prices.py`
- **Çıktı:** Tablo silinir
- **Kanıt:**
  - `alembic upgrade head` → tablo silindi (canlı DB üzerinde `.schema` kontrolü)
  - `alembic downgrade -1` → tablo geri gelir (rollback garantisi; ama veri boş olur)
  - Migration downgrade'i recreate + backup restore adımı içerir (backup zorunlu)

### [ ] T4.3 — `MarketReferencePrice` model class silinir
- **Giriş:** `backend/app/database.py` line 284+
- **Çıktı:** Class tanımı ve tüm referansları kaldırılır
- **Kanıt:**
  - Grep: `MarketReferencePrice` yalnızca git history'de kalır, canlı kodda yok

### [ ] T4.4 — Admin service legacy yollar temizlenir
- **Giriş:** `backend/app/market_price_admin_service.py`
- **Çıktı:** PTF-ile-ilgili tüm fonksiyonlar silinir; YEKDEM kodu kalır (ayrı spec)
- **Kanıt:**
  - Dosya boyutu azalır; sadece YEKDEM CRUD kalır
  - `backend/tests/test_market_price_admin_service.py` — PTF testleri silinir
  - Alternatif: bu dosya tamamen silinir, YEKDEM için ayrı service yazılır — `ptf-admin-management` spec'inin kararı

### [ ] T4.5 — Kill switch mekanizması silinir
- **Giriş:** `backend/app/guard_config.py`, `backend/app/pricing/router.py`
- **Çıktı:** `use_legacy_ptf`, `ptf_drift_log_enabled` alanları silinir; dispatcher branch'leri kaldırılır
- **Kanıt:**
  - `_load_market_records` tek fonksiyon (dispatcher yok)
  - `backend/tests/test_ptf_kill_switch.py` silinir
  - `ptf_legacy_fallback_total` metriği kaldırılır

### [ ] T4.6 — `ptf_drift_log` tablosu arşivle veya sil
- **Giriş:** Phase 2-3 süresince birikmiş drift log kayıtları
- **Çıktı:** Tablo ya silinir ya "read-only arşiv" olarak işaretlenir
- **Kanıt:**
  - Karar: sil (analiz tamamlandı, Phase 2 decision docüman yazıldı)
  - Alembic migration: `DROP TABLE ptf_drift_log`
  - `backend/app/ptf_drift_log.py` module silinir

### [ ] T4.7 — Hard delete candidates güncelle
- **Giriş:** `codebase-audit-cleanup/artifacts/hard_delete_candidates.md`
- **Çıktı:** F-PTF ile ilgili kayıtlar "DELETED ✅" + tarih işareti
- **Kanıt:** Dokümantasyon güncellemesi; audit trail kaybolmaz

### [ ] T4.8 — Post-Phase-4 baseline doğrulaması
- **Giriş:** T4.1-T4.7 merge
- **Çıktı:** `baselines/<tarih>_post-phase-4_baseline.json`
- **Kanıt:**
  - Byte-wise eşit: 25/30 senaryo (Phase 3 ile AYNI)
  - 2025-12 × 6 senaryo 409 kalır
  - Eğer herhangi bir senaryo değişmişse → Phase 4 revert (backup restore dahil)

### [ ] T4.9 — Steering ve audit-report final
- **Giriş:** Tüm Phase 4 çıktıları
- **Çıktı:**
  - `source-of-truth.md` §1 PTF satırı: `migration_status=unified_complete`, deprecated sütunu boşalır
  - `source-of-truth.md` §5 yasak kalıplar: market_reference_prices INSERT/UPDATE yasağı tarihi işareti ile korunur
  - `audit-report.md` (codebase-audit-cleanup) F-PTF "RESOLVED" olarak işaretlenir
- **Kanıt:** Git commit: `"feat(ptf-sot-unification): Phase 4 complete — F-PTF resolved"`

### [ ] T4.10 — Phase 4 PR merge + spec kapanış
- **Çıktı:**
  - Spec DoD raporu: `requirements.md` 10 requirement, tümü PASS
  - `ptf-sot-unification` spec'i **CLOSED**
  - Kullanıcıya özet mesaj: "PTF çift kaynağı çözüldü. Aynı müşteri aynı dönemde tek kaynaktan fiyat alır. Finansal risk (P0) kapandı."
- **Kanıt:** Git tag `ptf-sot-unification-done`

---

## DoD — Spec tamamlandığında

- [ ] `market_reference_prices` tablosu silindi (alembic 013)
- [ ] 25/30 golden baseline senaryosu byte-wise eşit (pre vs post-Phase-4)
- [ ] `test_main_wiring_invariant::test_rule2_no_new_legacy_ptf_writers` PASS (xfail değil)
- [ ] Kill switch, drift log, dual-read kodu tamamen silindi
- [ ] `yekdem_service.py`'daki legacy fallback pattern'i silindi (YEKDEM canonical'ı kullanıyor)
- [ ] `source-of-truth.md` PTF satırı canonical + migration_complete
- [ ] `audit-report.md` F-PTF RESOLVED

## Sonraki spec'ler (sırayla)

1. `yekdem-legacy-migration` — aynı 4 faz pattern'ini YEKDEM için uygular (PTF migration'dan öğrenilen ders: Phase 2 pencere kısa, drift gate sert)
2. `pricing-consistency-fixes` — FE dual-client temizliği (api.ts deprecated fonksiyonlar)
3. `invoice-validation-prod-hardening` — yeni validation stack'i shadow-wire et veya sil
4. `pdf-render-worker` — pdf_api orphan router kararı
5. Cleanup spec (yeni) — 9 fully-dead orphan modül hard delete

## İstatistikler

| Faz | Task | Tahmini süre | Risk |
|---|---|---|---|
| Phase 1 (Write Lock) | 8 | 1-2 gün | Düşük (geri dönüşümlü, kill switch hazır) |
| Phase 2 (Dual-Read) | 8 | 7-14 gün (pencere) | Orta (drift pattern bulursak backfill gerekebilir) |
| Phase 3 (Single Read) | 9 | 1-2 gün | Orta (legacy kod silme, FE'ye etki) |
| Phase 4 (Hard Delete) | 10 | 1 gün + backup | Yüksek (DROP TABLE; backup garantili) |
| **Toplam** | **35** | **~3 hafta (pencere dahil)** | |
