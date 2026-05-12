# Requirements — PTF Single Source of Truth Unification

## Bağlam

Bu spec `codebase-audit-cleanup`'ın P0 bulgusu **F-PTF**'yi çözer. Audit artifact'larından (A2/A8/A9/A10) çıkan kanıt:

- İki tablo aynı kavramı tutuyor: `hourly_market_prices` (canonical, saatlik, 2026-01→04 arasında 4 dönem) ve `market_reference_prices` (legacy, aylık ortalama, 2022-01→2026-12 arasında 59 dönem)
- `market_reference_prices` repo açılışından beri (2026-01-18 `Sprint 8.9.1: Production Ready`) canlı
- `hourly_market_prices` 2026-05-01 `feat: Pricing Risk Engine` ile eklenmiş ama migration tamamlanmamış
- Şu an **paralel çalışıyor**: `pricing_router::analyze` canonical tabloyu okuyor, `main.py::epias` endpoint'i legacy'yi okuyor/yazıyor, `yekdem_service.py` ikisinden de mirror yapıyor
- Sonuç: aynı müşteriye aynı dönemde farklı kaynaktan fiyat gösterilebilir → **finansal risk (P0)**

## Amaç

PTF için **tek gerçeklik kaynağı** kurmak: `hourly_market_prices`. Legacy tablo yalnızca migration penceresinde okunur, sonra silinir. Sessiz fallback **yasak**; eksik veri → explicit 409.

## Giriş kabulleri

- [R0] `codebase-audit-cleanup/baselines/` altında pre-migration golden baseline (30 snapshot) mevcut olmalı (B1 DoD).
- [R0.1] `backend/tests/test_main_wiring_invariant.py` CI'da 4 pass + 2 xfail durumunda yeşil (B10 DoD).
- [R0.2] Steering `.kiro/steering/source-of-truth.md` aktif (`inclusion: always`).

## Glossary

- **SoT**: Single Source of Truth (canonical veri kaynağı)
- **Canonical**: yazmanın sadece buraya yapıldığı, okumanın buradan yapıldığı tablo = `hourly_market_prices`
- **Legacy**: deprecated ama migration penceresinde hâlâ okunan tablo = `market_reference_prices`
- **Kill switch**: `USE_LEGACY_PTF` env flag; production'da 10 saniyede toggle edilebilir
- **Drift log**: dual-read penceresinde canonical vs legacy değerlerin farkı; `ptf_drift_log` tablosu
- **Fallback yasağı**: canonical'da veri yoksa legacy'ye düşme yasak; HTTP 409 döner

---

## Requirement 1 — Canonical kaynak kilitleme

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, PTF verisinin yalnızca tek bir tablodan okunup yalnızca tek bir tabloya yazıldığını kodla zorlamak istiyorum; böylece aynı müşteriye iki farklı teklif çıkma riski ortadan kalkar.

### Acceptance Criteria

1. THE canonical PTF tablosu `hourly_market_prices` OLMALI SHALL.
2. WHEN Phase 3 tamamlandığında THE her PTF yazma işlemi (INSERT/UPDATE) yalnızca `hourly_market_prices`'a yapılmalı SHALL.
3. WHEN Phase 3 tamamlandığında THE her PTF okuma işlemi yalnızca `hourly_market_prices`'tan yapılmalı SHALL; legacy `market_reference_prices` tablosundan PTF okunamaz.
4. THE canonical tablo şeması EN AZ şu alanları içermeli SHALL: `period` (YYYY-MM), `date`, `hour` (0-23), `ptf_tl_per_mwh`, `is_active`, `created_at`.
5. FOR EACH (period, date, hour) üçlüsü için THE canonical tabloda en fazla bir `is_active=1` kayıt bulunmalı SHALL; yeniden yükleme önceki kayıtları arşivler (is_active=0).
6. IF herhangi bir modül `market_reference_prices` tablosundan PTF okumaya veya yazmaya çalışırsa THEN CI `test_main_wiring_invariant.py::test_rule2_no_new_legacy_ptf_writers` FAIL vermeli SHALL (Phase 4 sonrası — Phase 1-3'te xfail).

---

## Requirement 2 — Fallback yasağı

**Kullanıcı Hikayesi:** Bir yönetici olarak, canonical kaynakta veri olmadığında sistemin sessizce eski kaynağa düşmesi yerine açık bir hata vermesini istiyorum; çünkü sessiz fallback, hatanın müşteriye farklı fiyat olarak ulaşmasına sebep olur.

### Acceptance Criteria

1. IF `hourly_market_prices` tablosunda verilen dönem için `is_active=1` kayıt yok ise THEN canonical yol (`pricing_router::analyze`, `simulate`, `compare`, `report/pdf`) HTTP 409 `market_data_not_found` dönmeli SHALL.
2. THE 409 response body EN AZ şu alanları içermeli SHALL: `error: "market_data_not_found"`, `message` (TR), `period`, `canonical_source: "hourly_market_prices"`, `legacy_has_data: bool` (audit için; legacy tabloda bu dönem var mı bilgisi).
3. WHERE legacy tablodan PTF türetme veya aylıktan saatliğe interpolasyon uygulanırsa THIS davranış YASAK SHALL; böyle bir kod yolu bulunursa CI guard (R1.6) tetiklenmeli.
4. WHEN kullanıcı manuel modda PTF girerse (admin panel) THEN değer `hourly_market_prices` tablosuna yazılmalı SHALL; `market_reference_prices`'a manuel yazma yasak (Phase 3 sonrası).
5. THE 409 davranışı `source-of-truth.md` steering §3 "Hybrid-C politikası" ile uyumlu SHALL; preview modları bu kural dışı kalır (salt görüntüleme, hesaplama yok).

---

## Requirement 3 — Kill switch

**Kullanıcı Hikayesi:** Bir operasyon sorumlusu olarak, migration sırasında bir aksilik olursa sistemi 10 saniye içinde eski davranışa döndürmek istiyorum; çünkü finansal sistemde uzun rollback süresi kabul edilemez.

### Acceptance Criteria

1. THE `backend/app/guard_config.py` içine `use_legacy_ptf: bool = False` ayarı eklenmeli SHALL (default: False = canonical yol aktif).
2. WHEN `USE_LEGACY_PTF=true` env değişkeni set edilirse THEN sistem yeniden başlatma olmadan bir sonraki istekte legacy davranışa dönmeli SHALL (guard_config zaten runtime reload destekliyor).
3. WHILE `use_legacy_ptf=True` iken THE canonical okumalar legacy tablodan yapılmalı SHALL ve her istekte log satırı üretilmeli: `level=WARNING, event=ptf_legacy_fallback_active, period=X, source=market_reference_prices`.
4. THE kill switch'in toggle süresi (env değiştir → bir sonraki request'te etkili) en fazla **10 saniye** OLMALI SHALL.
5. WHERE kill switch aktif iken THE Prometheus metriği `ptf_legacy_fallback_total{period}` counter artmalı SHALL.
6. WHEN Phase 4 (hard delete) tamamlandığında THE kill switch ve `use_legacy_ptf` flag'i kaldırılmalı SHALL; çünkü legacy tablo artık yok.

---

## Requirement 4 — Dual-read + drift log (Phase 2 penceresi)

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, canonical'a geçiş sırasında canonical ve legacy değerlerin farkını ölçmek istiyorum; böylece migration'ın doğru çalıştığını veri ile kanıtlayabilirim.

### Acceptance Criteria

1. WHILE Phase 2 (dual-read) aktif iken THE sistem her PTF okumasında hem canonical (`hourly_market_prices`) hem legacy (`market_reference_prices`) değeri almalı SHALL.
2. THE response yalnızca canonical değeri döndürmeli SHALL (legacy değeri sadece drift log için kullanılır — kullanıcı görmez).
3. THE sistem `backend/app/ptf_drift_log.py` modülü üzerinden drift kayıt etmeli SHALL; tablo: `ptf_drift_log` (alembic 012).
4. THE drift log satırı EN AZ şu alanları içermeli SHALL:
    - `period` (YYYY-MM)
    - `canonical_value_tl_per_mwh` (hourly ağırlıklı ortalama, float)
    - `legacy_value_tl_per_mwh` (market_reference_prices aylık değer, float)
    - `diff_abs` (abs fark, float)
    - `diff_percent` (% fark, float)
    - `captured_at` (ISO 8601)
    - `source_endpoint` (hangi endpoint tetikledi — örn. `/api/pricing/analyze`)
5. WHERE `diff_percent > 0.5%` iken THE drift log kaydına `severity: "high"` alanı eklenmeli SHALL; aksi halde `"low"`.
6. WHEN Phase 2 süresi 14 gün ile sınırlı SHALL; bu süre içinde `diff_percent > 0.5%` olan tek bir kayıt bile varsa Phase 3'e geçiş BLOKLANMALI (user-decision gerekli).
7. WHEN Phase 3 tamamlandığında THE dual-read kodu ve drift log yazma kapatılmalı SHALL; `ptf_drift_log` tablosu arşivleme için korunur, Phase 4'te silinir.

---

## Requirement 5 — Backfill (legacy → canonical, Phase 1)

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, legacy tablodaki 2022-2026 arası 59 dönemin canonical tabloda olmamasının sebebini ve sonucunu anlamak, gerekirse dönemleri taşımak istiyorum; ama mevcut canonical tablodaki doğru saatlik veriyi bozmadan yapmak istiyorum.

### Acceptance Criteria

1. WHEN Phase 1 migration script'i çalıştığında THE sistem hangi dönemlerin legacy'de olup canonical'da olmadığını listeleyen bir rapor üretmeli SHALL: `artifacts/ptf_backfill_candidates.json`.
2. FOR EACH aday dönem için THE rapor şunları içermeli SHALL: `period`, `legacy_ptf_tl_per_mwh` (aylık), `hourly_exists` (bool), `has_other_data_sources` (YEKDEM legacy var mı, offers var mı vb.).
3. THE backfill script'i **otomatik veri doldurma YAPMAZ** SHALL; sadece aday listesi üretir. Aylıktan saatliğe sentez yapmak = türetme = yasak (R2.3).
4. IF bir dönem için yalnızca aylık legacy veri mevcut AND canonical'da saatlik yok ise THEN bu dönem `status: "legacy_only"` olarak işaretlenir AND migration sonrası bu dönem için teklif akışı 409 döner (Hybrid-C).
5. FOR EACH `status: "legacy_only"` dönem için user-decision: (a) EPİAŞ API'den saatlik veri çekilecek mi, (b) dönem kalıcı olarak "data unavailable" mi işaretlenecek?
6. THE backfill raporu git'e commit edilmeli SHALL; karar bu raporun üzerinde audit trail olarak yazılır.

---

## Requirement 6 — Admin yazıcıların migration'ı

**Kullanıcı Hikayesi:** Bir admin olarak, market price panelinden girdiğim PTF değerlerinin canonical tabloya yazıldığından emin olmak istiyorum; paneli kullanırken iki tablo olduğunu bilmem gerekmemeli.

### Acceptance Criteria

1. THE `backend/app/market_prices.py::upsert_market_price()` fonksiyonu Phase 3 sonrası `hourly_market_prices`'a yazmalı SHALL; saatlik granülarite için ya (a) tüm saatler aynı değer (aylık→saatlik replikasyon — YASAK, R2.3), ya (b) admin panel UI'si saatlik giriş istemeli.
2. THE karar (a) vs (b) için user-decision: bu spec (b)'yi önerir ama panel UI değişikliği `ptf-admin-frontend` spec'inin kapsamına girer; bu spec yalnızca BE wiring'i yapar.
3. WHILE (b) uygulanana kadar iken THE aylık manuel PTF yazma yolu **devre dışı** bırakılmalı SHALL; `POST /admin/market-prices` endpoint'i 409 `manual_ptf_disabled` dönmeli, mesaj: "Manuel aylık PTF girişi kaldırıldı; saatlik veri admin panelinden girilmeli (ptf-admin-frontend bekleniyor)."
4. THE `POST /api/epias/prices/{period}` endpoint'i Phase 3 sonrası `hourly_market_prices`'a yazmalı SHALL (EPİAŞ API saatlik veri döner, dönüşüm basit).
5. WHEN `seed_market_prices.py` ve `main.py::_add_sample_market_prices()` gibi sample/seed yazıcılar Phase 3'te ya canonical'a yönlendirilmeli ya kaldırılmalı SHALL; her ikisinin de mevcut davranışı `market_reference_prices`'a yazıyor.

---

## Requirement 7 — YEKDEM service dual-read cleanup

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, `yekdem_service.py`'deki mevcut "legacy tablo fallback + mirror" pattern'inin fallback yasağı ile uyumlu hale getirilmesini istiyorum; bu dosya PTF migration'ın önlem almasını zorunlu kılan örnek pattern'i barındırıyor.

### Acceptance Criteria

1. THE `backend/app/pricing/yekdem_service.py` içindeki `market_reference_prices` okuma yolu (line 134) Phase 3 sonrası silinmeli SHALL.
2. THE YEKDEM canonical tablosu `monthly_yekdem_prices` (zaten karar kilitli, source-of-truth.md §1).
3. WHEN Phase 3 tamamlandığında THE YEKDEM okumaları yalnızca `monthly_yekdem_prices`'tan yapılmalı SHALL; mirror pattern kaldırılır.
4. IF `monthly_yekdem_prices`'ta veri yok ise THEN `calculator.py` (dolaylı kullanıcı) `yekdem=0.0` yerine HTTP 409 `yekdem_data_not_found` dönmeli SHALL (fallback yasağı — R2).
5. THE YEKDEM legacy migration ayrı spec'te yapılacak (`yekdem-legacy-migration`); PTF migration YEKDEM migration'ını beklemez ama yekdem_service.py'deki PTF-benzeri fallback pattern'i PTF migration kapsamında silinir.

---

## Requirement 8 — Baseline regresyon doğrulaması

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, PTF migration'ının golden baseline'daki 30 senaryoya sıfır regresyon ile uygulandığını kanıtlamak istiyorum; kanıt olmadan production'a çıkmak kabul edilemez.

### Acceptance Criteria

1. THE B1'de alınan `baselines/<tarih>_pre-ptf-unification_baseline.json` dosyası git'e commit edilmiş OLMALI SHALL.
2. WHEN her phase (1, 2, 3, 4) tamamlandığında THE baseline script'i aynı parametrelerle tekrar çalıştırılmalı SHALL; çıktı `baselines/<tarih>_post-phase-<N>_baseline.json` olarak kaydedilir.
3. FOR EACH matched senaryo için (25 senaryo — 2025-12 × 2 profil × 3 endpoint hariç) THE post-phase hash pre-migration hash ile **BYTE-WISE EŞİT** OLMALI SHALL.
4. THE 5 senaryo (2025-12 × 2 profil × ... — Hybrid-C tetiklenir) için `status_code` 409 veya 404 OLMALI SHALL; bu dönem canonical'da olmadığı için.
5. IF herhangi bir matched senaryo hash farklı ise THEN phase DURMALI AND user-decision alınmalı (beklenen fark mı, regression mı).
6. THE karşılaştırma script'i `scripts/10_baseline_compare.py` olarak yazılmalı SHALL; iki baseline JSON dosyası alır, diff üretir, exit code 0/1.

---

## Requirement 9 — Hard delete (Phase 4 DoD)

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, migration tamamlandıktan sonra legacy kodu/tabloyu tamamen silmek istiyorum; yarım silmek "yine hayalet kod" riskini taşır.

### Acceptance Criteria

1. WHEN Phase 4 başladığında THE legacy tablo `market_reference_prices` alembic migration ile `DROP TABLE` edilmeli SHALL (versiyon: 013_drop_market_reference_prices).
2. FOR EACH legacy yolda kalan kod satırı (aşağı listede) THE dosyalar silinmeli veya temizlenmeli SHALL:
    - `backend/app/market_prices.py` (tüm fonksiyonlar — 4 yer)
    - `backend/app/bulk_importer.py` içinde `MarketReferencePrice` kullanımı
    - `backend/app/market_price_admin_service.py` (yalnızca yeni tabloya yazan kısım kalır; legacy query'ler silinir)
    - `backend/app/seed_market_prices.py` (zaten orphan, tüm dosya silinir)
    - `backend/app/main.py::_add_sample_market_prices()` (tüm fonksiyon silinir veya yeni tabloya yönlendirilir)
    - `backend/app/main.py::POST /admin/market-prices/{period}/lock|unlock` endpoint'leri (lock kavramı canonical'da yok; yeni tasarım)
    - `backend/app/pricing/yekdem_service.py:123-152` (legacy fallback + mirror bloğu)
3. THE `kill_switch` (`use_legacy_ptf` flag ve kontrolleri) Phase 4'te silinmeli SHALL; tablo olmadığı için anlamsız.
4. WHEN `test_main_wiring_invariant.py::test_rule2_no_new_legacy_ptf_writers` Phase 4 sonrası **regular pass** olmalı SHALL (xfail değil); legacy tabloya yazan hiçbir kod kalmadığı için.
5. THE `codebase-audit-cleanup/hard_delete_candidates.md`'deki F-PTF ile ilgili kayıtlar "DELETED ✅" olarak işaretlenmeli SHALL.
6. THE post-Phase-4 golden baseline hash'leri pre-migration ile BYTE-WISE EŞİT (R8.3) — aksi halde Phase 4 rollback edilir.

---

## Requirement 10 — Drift log analizi gate (Phase 2 → Phase 3 geçiş kriteri)

**Kullanıcı Hikayesi:** Bir sistem mühendisi olarak, dual-read penceresinde oluşan drift'in production'a geçiş için yeterince küçük olduğunu sayısal olarak kanıtlamak istiyorum.

### Acceptance Criteria

1. WHEN Phase 2 süresi dolduğunda (minimum 7 gün, maksimum 14 gün) THE `scripts/11_drift_analysis.py` çalıştırılmalı SHALL.
2. THE drift analizi raporu EN AZ şu metrikleri içermeli SHALL:
    - Toplam drift log satır sayısı
    - `severity: "high"` oranı
    - Ortalama `diff_percent`
    - P95 `diff_percent`
    - Dönem × endpoint kırılımı
3. IF herhangi bir dönem için ortalama `diff_percent > 0.5%` ise THEN Phase 3'e geçiş BLOKLANMALI; user-decision: (a) backfill eksikliği mi (R5), (b) hesap farkı mı (calculator yanlış tablodan okuyor mu), (c) kabul edilebilir tolerans içinde mi (yazılı onay gerek).
4. IF `severity: "high"` oranı > %5 ise THEN Phase 3'e geçiş otomatik engellenmeli; root-cause analizi zorunlu.
5. THE gate kararı git'e commit edilmeli SHALL: `artifacts/phase2_drift_decision.md` dosyası (karar + gerekçe + analiz raporu referansı).

---

## Scope sınırları

### Dahil

- `hourly_market_prices` kaynak olarak kilitleme (R1)
- `market_reference_prices` PTF erişiminin silinmesi (R9)
- Kill switch, drift log, dual-read pencere mekanikleri (R3, R4)
- Backfill aday raporu (veri doldurma YAPMAZ — R5)
- YEKDEM service dual-read PATTERN'inin silinmesi (R7 — YEKDEM migration ayrı spec)
- 4 faz × her faz için baseline doğrulaması (R8)

### Hariç

- YEKDEM tam migration (`yekdem-legacy-migration` spec'i ayrı)
- FE admin panel UI değişiklikleri (`ptf-admin-frontend` spec'i)
- `offers`, `invoices`, `analysis_cache` snapshot tabloları — steering §2 gereği dokunulmaz
- `pdf_api.router` orphan çözümü (`pdf-render-worker` spec'i)
- Yeni validation stack (`invoice-validation-prod-hardening` spec'i)

### Ön-koşul spec'leri

- `codebase-audit-cleanup` Phase A tamamlanmış (10/10) — **gerçekleşti**
- B1 (golden baseline) + B10 (CI guard) commit edilmiş — **gerçekleşti**
- `source-of-truth.md` steering aktif — **gerçekleşti**

### Bu spec tamamlandığında

- **F-PTF bulgusu kapanır.** P0 risk ortadan kalkar.
- `yekdem-legacy-migration` spec'ine geçiş mümkün olur (aynı pattern'i YEKDEM için uygular).
- `pricing-consistency-fixes` spec'i (FE dual-client) paralel başlayabilir.
