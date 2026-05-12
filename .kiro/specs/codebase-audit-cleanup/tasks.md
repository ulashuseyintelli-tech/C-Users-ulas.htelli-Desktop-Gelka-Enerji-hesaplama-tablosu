# Tasks — Codebase Audit & Cleanup

## Yaklaşım

Bu spec **sadece audit yapar ve kanıt üretir**. Hiçbir fix uygulanmaz; mimari değişiklikler ayrı spec'lere devredilir (örn. `ptf-sot-unification`, `yekdem-legacy-migration`).

**3 faz, 28 task:**
- **Phase A — Audit (10 task):** Envanter, haritalama, tespit
- **Phase B — Guard & Proof (10 task):** Invariant testler, drift testleri, baseline
- **Phase C — Output (8 task):** Rapor, roadmap, steering, devir

Her task: **Giriş** (ne alır) / **Çıktı** (ne üretir) / **Kanıt** (başarı ölçütü).

**Bağımlılık kuralı:** A1 → A2 → ... lineer; B'nin bazı taskları A'ya bağımlı (belirtilecek); C tüm A ve B'yi bekler.

**Kilit kararlar (tartışma kapalı):**
- F-PTF → P0, `ptf-sot-unification` spec'ine devredilir
- F-YEKDEM-eski → P1, `yekdem-legacy-migration` spec'ine devredilir
- Golden baseline cleanup'tan önce kilitlenir (R22)
- Audit max 60 dk (R15)

---

## Phase A — Audit (10 task)

### [x] A1 — Pre-flight: Schema drift ön kontrolü
- **Giriş:** `backend/gelka_enerji.db`, `backend/alembic/versions/*.py`
- **Çıktı:** `artifacts/phase0_schema_drift.json` (alembic_current vs model karşılaştırması)
- **Kanıt:**
  - Script `scripts/00_preflight_schema.py` mevcut ve çalışır
  - `in_sync == true` veya drift raporu kullanıcıya sunulmuş
  - DoD: `alembic current` = `011_market_prices_ptf_admin` teyit (smoke'dan biliniyor)

### [x] A2 — DB envanteri (v2)
- **Giriş:** canlı SQLite DB
- **Çıktı:** `artifacts/phase1_db_inventory.json` + stdout raporu
- **Kanıt:**
  - ✅ Smoke'da tamamlandı: 19 tablo, 7 rol etiketi, F-PTF auto-flag P0, cross-source period diff çıktısı
  - Artifact dosyası disk'te mevcut
  - F-PTF ve iki YEKDEM kaynağı raporlanmış

### [x] A3 — FastAPI endpoint envanteri
- **Giriş:** `backend/app/**/*.py` (main.py + routers)
- **Çıktı:** `artifacts/phase1_endpoints.json` — her endpoint için `{method, path, function, file, line, router}`
- **Kanıt:**
  - AST parse ile tüm `@app.*` ve `@*_router.*` decorator'ları listelenir
  - `/api/pricing/analyze`, `/api/epias/prices/{period}`, `/api/full-process` listede görünür (elle spot check)
  - Script `scripts/01_inventory_endpoints.py` deterministik, idempotent
- **Sonuç:**
  - ✅ 156 dosya tarandı, 91 endpoint bulundu (app=73, pricing_router=15, router/pdf=3)
  - ✅ Router prefix tablosu: `pricing_router=/api/pricing`, `router=/pdf`
  - ✅ İki art arda çalıştırma SHA256 eşit → idempotent DoD ✓
  - ✓ `/api/pricing/analyze` bulundu (pricing/router.py:443)
  - ✓ `/api/epias/prices/{period}` bulundu (main.py:4503 GET, 4544 POST)
  - ✗ `/api/full-process` yok — gerçek path `/full-process` (main.py:947). FE `api.ts:150` `/full-process` çağırıyor. **Bulgu:** task'taki spot-check etiketinde path prefix hatası; audit kod değiştirmez, bulgu A5'te işlenecek
  - **Baseline bulgu:** `pdf_api.router` (3 endpoint, `/pdf` prefix) `app.include_router()` çağrılmamış → A6 import kapanışında "orphan/dormant" adayı

### [x] A4 — Frontend fetch çağrı envanteri
- **Giriş:** `frontend/src/**/*.{ts,tsx}`
- **Çıktı:** `artifacts/phase1_fe_fetches.json` — her çağrı için `{file, line, method, url_template, interpolations}`
- **Kanıt:**
  - Regex + AST hibrit tarama; `fetch()`, `axios.*`, `api.ts::*` çağrıları yakalanır
  - Manuel mod'un `/api/epias/prices/${period}` çağrısı yakalanmış (App.tsx:664-701 — smoke'da teyit)
  - Çağrı sayımı: `total_fetch_calls > 0` ve örnek 3 çağrı rapora çıkar
- **Sonuç:**
  - ✅ 26 dosya tarandı (test dışı), 18 test dosyası dışlandı
  - ✅ 28 ham çağrı, **25 benzersiz** `(method, path)` kombinasyonu
  - ✅ 12 dinamik path yakalandı: `${period}`, `${id}`, `${limit}` vb. → `{period}`, `{id}`, `{limit}` placeholder'ına normalize edildi
  - ✅ İki art arda çalıştırma SHA256 eşit → idempotent DoD ✓
  - ✅ Multi-line çağrı desteği (A5 gözden geçirme sırasında eklendi): `adminApi.get<T>(` yeni satırda path → yakalanır
  - Usage dağılımı: admin-market-prices=8, admin=6, pricing=5, epias=3, health=1, invoice-flow=1, invoice-offer=1
  - Script: `scripts/02_inventory_fe_fetches.py` — axios instance + native fetch + `new URL()` kalıplarını `re.DOTALL` ile multi-line yakalar; `//...` ve `/* ... */` yorumları satır sayısı korunarak temizlenir
  - ✓ Spot-check: `/api/pricing/analyze`, `/api/epias/prices/{period}`, `/full-process` — 3/3 geçti
  - **Baseline bulgu:** `api.ts` dosyasında `@deprecated` ile işaretli fonksiyonlar (`getMarketPrices`, `getMarketPrice`, `upsertMarketPrice`, `lockMarketPrice` vb.) hâlâ `/admin/market-prices` endpoint'lerini çağırıyor; paralel yeni client `market-prices/marketPricesApi.ts` aynı endpoint'lere gidiyor → **dual FE-client kaynağı**. A5 eşleşme task'ında BE endpoint'e 2 FE kaynak düşüyor; A6 import kapanışı eski fonksiyonların kullanıcısı var mı teyit edecek

### [x] A5 — Endpoint ↔ FE fetch matching
- **Giriş:** A3 + A4 artifactları
- **Çıktı:** `artifacts/phase2_endpoint_mapping.json` — `{matched, endpoint_only, fetch_only}`
- **Kanıt:**
  - URL template normalize edilip regex match yapılır
  - `endpoint_only` set'i "ölü endpoint" adayı, `fetch_only` set'i "kırık bağ"
  - `/api/pricing/analyze` matched ✓, `/api/epias/prices/{period}` matched ✓
- **Sonuç:**
  - ✅ Script `scripts/03_match_endpoints.py` — path-param isim-duyarsız eşleşme (`{period}` ↔ `{_}`), trailing slash normalize, query string yoksay, idempotent SHA256 ✓
  - ✅ FE coverage: **25/25 → %100** sınıflandırıldı
  - ✅ Kategoriler: MATCHED=25, METHOD_MISMATCH=0, FE_ONLY=0, BE_ONLY=66, DUAL_FE_CLIENT=3
  - **A3 "false alarm" kapandı:** `/full-process` POST → `main.py:947` MATCHED (tasks.md'deki `/api/full-process` etiketi yalnızca metin hatasıydı)
  - **Dead router teyidi:** `/pdf/jobs`, `/pdf/jobs/{_}`, `/pdf/jobs/{_}/download` → 3 adet **BE_ONLY** (pdf_api.router include edilmemiş)
  - **DUAL_FE_CLIENT (3):**
    - `GET /admin/market-prices` → `api.ts:433` (deprecated `getMarketPrices`) + `marketPricesApi.ts:31` (`listMarketPrices`)
    - `POST /admin/market-prices` → `api.ts:458` (deprecated `upsertMarketPrice`) + `marketPricesApi.ts:59` (`upsertMarketPrice`)
    - `GET /api/epias/prices/{period}` → `api.ts:653` (axios `api.get`) + `App.tsx:678` (native `fetch`)
  - **BE_ONLY öne çıkanlar (66):** 63'ü main.py'deki admin/*, invoices/*, jobs/*, webhooks/*, customers/*, offers/*, extraction/*, stats endpoints (FE hâlâ `api.ts` paslı yoldan okuyor olabilir, A6 import kapanışı teyit edecek); 3'ü `pdf_api.router` (teyit edilmiş orphan)

### [x] A6 — Import kapanışı (canlı vs ölü modül)
- **Giriş:** `backend/app/main.py` (kök), `backend/tests/` (test-only set)
- **Çıktı:** `artifacts/phase1_imports.json` — `{alive_from_main, alive_from_tests_only, orphan, dormant}`
- **Kanıt:**
  - BFS ile transitive import kapanışı; lazy import'lar da dahil
  - `guard_config.py::ADAPTIVE_ENABLED == False` → `adaptive_control/*` dormant işaretli
  - Orphan sayımı `> 0` ise örnek 3 modül listelenir
- **Sonuç:**
  - ✅ Script `scripts/04_import_closure.py` — AST import graph + relative-import semantiği (module vs package ayrımı), lazy-import flag (fn/class/if/try içi), idempotent SHA256 ✓
  - ✅ **156 app modülü** için status dağılımı: alive_from_main=68, alive_from_tests_only=59, **orphan=15**, dormant=14
  - ✅ **Endpoint reachability: 88/91 reachable, 3 unreachable** (tam olarak `/pdf/*` — `pdf_api.router` include_router çağrısı hiç yapılmamış)
  - ✅ Router erişilebilirlik tablosu: `app`=✓, `pricing_router`=✓ (include edilmiş), `router` (pdf_api)=✗ (orphan router)
  - ✅ **Dormant flag tespiti:** `adaptive_control_enabled=False`, `decision_layer_enabled=False`, `drift_guard_enabled=False` → 14 modül dormant (feature gate OFF; modül yüklü ama yol açılmadı)
  - **Orphan modüller (15)** — ne main.py ne test dosyalarından erişilmiyor:
    - `app.canonical_extractor`, `app.fast_extractor`, `app.extractor`, `app.extraction_prompt`, `app.html_render`, `app.image_prep`, `app.job_queue`, `app.rq_worker`, `app.seed_market_prices`, `app.pricing.excel_formatter`
    - `__init__.py` paketleri: `app.core`, `app.guards`, `app.invoice`, `app.pricing`, `app.services` (boş veya re-export eksik — alt modüller ayrı imports ile canlı)
  - **Baseline bulgu (invoice validation):** `app.invoice.validation.*` paketinin **tamamı `alive_from_tests_only`** — yani test harness'ı tam kapsam ama `main.py` üretim yolunda import etmiyor. Bu invoice validation pipeline'ının **gerçek request handler'ına bağlı olmadığı** anlamına gelir. Ciddi bir production gap olabilir
  - **Teyit edilen bulgular:**
    - `pdf_api.router` → **orphan router**, 3 endpoint DEAD (A3+A5 ile çapraz kanıtlı)
    - `api.ts` deprecated market-prices fonksiyonları hâlâ canlı (`api.ts` main.py sayılmaz ama FE'de canlı) — FE tarafı ayrı; A5 dual-FE bulgusu devam ediyor

### [x] A7 — Fatura kontrol akışı kaynak taraması (kısıtlı derinlik)
- **Giriş:** `backend/app/invoice/**`, `backend/app/extractor.py`, `backend/app/calculator.py`, `backend/app/validator.py` (hangi isimdeyse)
- **Çıktı:** `artifacts/phase2_invoice_flow_sources.json` — her adımın okuduğu PTF/YEKDEM kaynağı
- **Kanıt:**
  - Üç soru kanıtla cevaplandı: (a) PTF nereden, (b) YEKDEM nereden, (c) fallback flag/log var mı
  - Kanıt: grep ile `hourly_market_prices|market_reference_prices|monthly_yekdem_prices` referansları
  - Rapora tek sayfalık "Invoice Flow Source Map" bölümü eklenir
  - Not: Performans/edge-case taraması YAPILMAZ (kapsam sınırı P-B)
- **Sonuç:**
  - ✅ Script `scripts/05_invoice_flow_sources.py` — handler body AST scan + top/lazy import resolution + A6 cross-reference, idempotent SHA256 ✓
  - ✅ **21 invoice flow endpoint** tarandı (analyze-invoice, full-process, calculate-offer, invoices/*, extraction/*, offers/*)
  - **Akış zinciri (koddan kanıt):**
    - `POST /analyze-invoice` → `app.extractor::extract_invoice_data` → `app.validator::validate_extraction`
    - `POST /full-process` → `app.extractor::extract_invoice_data` → `app.validator::validate_extraction` → `app.calculator::calculate_offer`
    - `POST /calculate-offer` → `app.calculator::calculate_offer`
    - `PATCH /extraction/patch-fields`, `POST /extraction/apply-suggested-fixes` → `app.validator::validate_extraction`
    - `POST /invoices/{id}/validate` ve `POST /invoices/{id}/extract` → `app.validator::validate_extraction`
  - **Legacy validator (app.validator::validate_extraction) — CANLI (üretim yolu):**
    - `reachable_from_main = True`
    - 6 handler'dan 9 ayrı çağrı noktası (analyze-invoice x2, full-process x2, patch-fields, apply-suggested-fixes, invoices/{id}/validate, invoices/{id}/extract)
  - **Yeni validation stack (app.invoice.validation.*) — VERDICT: 🔴 DEAD**
    - `reachable_from_main = False`
    - Üretim handler çağıranı: **0**
    - Giriş noktaları: `validator::validate`, `enforcement::apply_enforcement`, `shadow::shadow_validate_hook` — hepsi test harness'ından erişiliyor ama main.py zincirinde YOK
    - Legacy validator içinde de `from .invoice.validation` referansı YOK — eski validator'a bağlanacak bir "shadow hook" çağrısı da mevcut değil
    - Sebep: paket tamamen izole test-only; üretim kodu hâlâ tek dosya `app/validator.py`'ye bağımlı
  - **PTF/YEKDEM tablo referansları (kod tabanında):**
    - `hourly_market_prices` (canonical PTF): **2 ref** — `backend/app/pricing/schemas.py` (model + docstring)
    - `market_reference_prices` (legacy PTF + legacy YEKDEM): **9 ref** — `backend/app/database.py` + `backend/app/seed_market_prices.py` + diğer legacy yollar
    - `monthly_yekdem_prices` (canonical YEKDEM): **4 ref**
  - **Handler body → tablo direkt ref:** YOK. Tablo erişimi `handler → extractor/calculator` zinciri üzerinden (beklenen mimari; direct query yok)
  - **Net verdict:**
    - ✅ Invoice flow **çalışıyor** — legacy validator üretim yolunda
    - 🔴 Yeni validation stack **tamamen DEAD** — testler geçiyor ama 0 kullanıcı
    - **P1 roadmap maddesi (C3/C5):** `invoice-validation-prod-hardening` veya `invoice-validation-perf-telemetry` spec'lerinden biri **yeni stack'in main.py'ye bağlanması** (shadow-mode wiring veya enforcement entegrasyonu) görevini almalı; aksi halde 20+ dosyalık yeni kod "ölü" kalır

### [x] A8 — Sessiz duplikasyon konsolidasyonu + Usage signal (birleşik)
- **Giriş:** A2 + A3 + A4 + A5 + A6 + A7 bulguları
- **Çıktı:** `artifacts/phase3_duplications.json` — `duplications[] + endpoint_usage[] + module_usage[] + cleanup_list + roadmap_input`
- **Kanıt:**
  - F-PTF (P0, R24) ve F-YEKDEM-eski (P1) kayıtları mevcut
  - Script `scripts/06_usage_and_duplications.py` — A8a (domain-bazlı duplikasyon) + A8b (5-kaynak usage sinyali) birleşik, idempotent SHA256 ✓
  - Kapsam: backend non-app Python + scripts/ + k6 load tests + *.sh/*.bat/*.ps1 + docs/ + monitoring/runbooks/
- **A8a — Duplikasyon bulguları (5):**
  - 🔴 **F-PTF (P0)** → `ptf-sot-unification` (canonical=`hourly_market_prices` vs legacy=`market_reference_prices`)
  - 🟠 **F-YEKDEM-eski (P1)** → `yekdem-legacy-migration` (canonical=`monthly_yekdem_prices` vs legacy=`market_reference_prices`)
  - 🟠 **F-VALIDATION (P1)** → `invoice-validation-prod-hardening` (legacy `app.validator` canlı; yeni `app.invoice.validation.*` DEAD)
  - 🟠 **F-DEAD_ROUTER (P1)** → `pdf-render-worker` (pdf_api.router include edilmemiş, 3 endpoint ulaşılamaz)
  - 🟡 **F-DUAL_FE (P2)** → `pricing-consistency-fixes` (api.ts deprecated fn'ler + marketPricesApi.ts aynı endpoint'te paralel)
- **A8b — Endpoint usage dağılımı (n=91):**
  | Sınıf | Sayı | Anlam |
  |---|---:|---|
  | ACTIVE | 24 | FE veya non-FE kullanıyor |
  | INTERNAL | 27 | FE yok; docs/shell/load-test/non-FE kodda ref var |
  | TEST_ONLY | 10 | Yalnızca test referansı |
  | DEAD | 27 | Hiçbir kaynakta ref yok |
  | UNREACHABLE | 3 | Router wire değil (pdf/*) |
- **Modül durumu (A6'dan çapraz):**
  - **9 tamamen ölü orphan** (external mention=0): `canonical_extractor`, `fast_extractor`, `pricing.excel_formatter`, `rq_worker`, `seed_market_prices`, `services.job_claim`, `services.webhook_manager`, `worker`, + 1 `__init__.py` paketi
  - **6 orphan "dikkat" (external mention>0)**: `app.core` (alembic), `app.guards/invoice/pricing/services/testing` hepsi yalnızca `build-desktop.bat`'ta (PyInstaller bundling referansı) — yani mention var ama canlı kullanım değil
  - **14 dormant** (flag OFF): `adaptive_control/*`, `guards.drift_guard`, `guards.guard_decision*` — feature gate kapalı
- **Cleanup listesi:**
  - **silinebilir_aday: 40** (27 DEAD endpoint + 3 UNREACHABLE + 9 fully-dead orphan + 1 orphan router)
  - **migrasyon_aday: 2** (F-PTF, F-YEKDEM-eski)
  - **baglanacak: 1** (F-VALIDATION)
  - **fe_bağımlı_ama_ulaşılamaz: 0** (FE hiçbir DEAD endpoint'e dokunmuyor — iyi haber)
- **Doğrulanan ilkeler:**
  - "aynı ticari akışta birleşme" kriteri otomatik severity atar ✓
  - Duplikasyon ≠ snapshot ayrımı (v2 rol etiketleri) uygulandı ✓ (offers, invoices, price_change_history dup sayılmadı)
  - Audit kod değiştirmez — sadece `silinebilir_aday` listesi üretir ✓
- **Yan kazanım (A4 regression fix):** A8 gözden geçirmede `telemetry.ts::fetch(TELEMETRY_ENDPOINT, ...)` yakalanmadığı tespit edildi. A4 script'ine `const X = '/path'` + `fetch(X,...)` deseni için `FETCH_CALL_VAR_RE` eklendi → FE ham çağrı 28→29, benzersiz 25→26, MATCHED 25→26, BE_ONLY 66→65. `/admin/telemetry/events` artık ACTIVE sınıfında

### [x] A9 — SoT matrisi + niyet analizi
- **Giriş:** A8 duplikasyonları, `git log` + alembic migration geçmişi
- **Çıktı:** `artifacts/phase4_sot_matrix.json`
- **Kanıt:**
  - Her canonical domain için `{concept, canonical_source, canonical_writer, readers, deprecated, migration_status}` dolu
  - PTF canonical = `hourly_market_prices` (karar kilit)
  - YEKDEM canonical = `monthly_yekdem_prices` (karar kilit)
  - Git arkeolojisi her kaynağın `introduced_at` commit'ini içerir
- **Sonuç:**
  - ✅ Script `scripts/07_sot_matrix_archaeology.py` — SoT matrisi (5 domain) + git arkeolojisi (3 tablo + 15 modül); pickaxe aramalarında `*.py`, `*.sql`, `*.md` pathspec (binary dosyaları hariç tutar); idempotent SHA256 ✓
  - ✅ **5 SoT satırı** — hepsi delegated_to_spec ile bağlı:
    - ptf → `ptf-sot-unification` (P0, parallel_unresolved)
    - yekdem → `yekdem-legacy-migration` (P1, legacy_rows_exist)
    - invoice_validation → `invoice-validation-prod-hardening` (P1, new_stack_dead)
    - pdf_jobs → `pdf-render-worker` (P1, router_unregistered)
    - fe_admin_market_prices → `pricing-consistency-fixes` (P2, dual_active)
  - ✅ **Git arkeolojisi (18 artifact, 18/18 introduced_at ✓):**
    - `market_reference_prices` (legacy): **2026-01-18** — `Sprint 8.9.1: Production Ready` (repo başlangıcı)
    - `hourly_market_prices` + `monthly_yekdem_prices` (canonical): **2026-05-01** — `feat: Pricing Risk Engine + frontend entegrasyon + bayi puan modeli` (3.5 ay sonra yeni mimari)
    - `app.validator` (legacy, canlı): **2026-01-18** — baştan beri üretimde
    - `app.invoice.validation.*` (yeni stack, DEAD): **2026-02-28** — `Phase F: enforcement engine + wiring + 12 tests (54 total, 0 failed)` commit mesajı "wiring" iddia ediyor ama main.py'ye bağlanma yapılmamış (false "done" sinyali!)
    - `pdf_api.py` (orphan router): **2026-02-19** — boş mesajlı commit (`18.02.2026`), 2.5 ay wire edilmeyi beklemekte
    - Diğer orphan modüller: 01/18 (repo açılışı), 02/06, 02/14, 05/01 gibi farklı sprint'lerde eklenmiş
  - **Kritik yorum (intent analizi):**
    - Legacy PTF/YEKDEM kaynağı `market_reference_prices` repo başlangıcından beri (ilk MVP'nin temel tablosu); yeni canonical tablolar 3.5 ay sonra geldi → migration planı eksik
    - Yeni validation stack "Phase F wiring" diye commit edildi ama **gerçek wiring yok** — commit mesajı ≠ kod gerçeği (R1 ilkesi: sadece koda inan)
    - orphan modüller farklı sprint'lerden birikmiş; tek seferlik "dead feature" değil, sürekli sızıntı
  - **B3+/C3 için girdi hazır:** canonical/writer/readers matrisi → `source-of-truth.md` steering'e (C2) + invariant test üretimine (B7)

### [x] A10 — Parallel path detection
- **Giriş:** A5 + A7 + A8
- **Çıktı:** `artifacts/phase3_parallel_paths.json` — R24 bulguları
- **Kanıt:**
  - F-PTF bu listenin ilk kaydı (otomatik P0, smoke'dan kanıtlı)
  - Aynı domain'i farklı kaynaktan hesaplayan ≥2 endpoint çifti listelenir
  - Her çift için "aynı iş akışında birleşme var mı" boolean alanı
- **Sonuç:**
  - ✅ Script `scripts/08_parallel_paths.py` — A5+A6+A7+A8+A9 çapraz tabloları konsolide eder, idempotent SHA256 ✓
  - ✅ **8 paralel yol**, severity dağılımı: P0=1, P1=3, P2=4
  - ✅ Convergence (R24 kriteri): **6 convergent** (aynı iş akışında buluşan — temizlik önceliği) / **2 non-convergent** (PP-PDF_JOBS orphan router, PP-EXTRACTION orphan modüller)
  - **Paralel yol kayıtları (severity sırasıyla):**

  | ID | Dom | Status | Canonical | Orphan | Spec |
  |---|---|---|---|---:|---|
  | PP-PTF (P0) | ptf | parallel_unresolved | `hourly_market_prices` | 0 | ptf-sot-unification |
  | PP-VALIDATION (P1) | validation | unconnected_alternative | `app.validator.validate_extraction` | 3 | invoice-validation-prod-hardening |
  | PP-YEKDEM (P1) | yekdem | legacy_rows_exist | `monthly_yekdem_prices` | 0 | yekdem-legacy-migration |
  | PP-PDF_JOBS (P1) | pdf_jobs | router_unregistered | (sync inline canlı) | 1 | pdf-render-worker |
  | PP-EXTRACTION (P2) | extraction | unconnected_alternative | `app.extractor` | 2 | (henüz yok — cleanup adayı) |
  | PP-FE-DUAL GET/POST `/admin/market-prices` (P2×2) | fe_admin | dual_active | `marketPricesApi.ts` | 0 | pricing-consistency-fixes |
  | PP-FE-DUAL GET `/api/epias/prices/{period}` (P2) | fe_epias | dual_active | **(belirlenmedi)** | 0 | — |
  - **Kritik gözlem:** PP-FE-DUAL epias için canonical önerisi YOK — epias FE akışında hangi yol (inline `App.tsx::fetch` vs `api.ts::getEpiasPrices`) canonical olmalı kararı Phase C'de alınmalı
  - **Audit DoD:** Henüz hiçbir path silinmedi; her orphan işaretli, her canonical ise SoT matrisine ankraj (C2 `source-of-truth.md` steering girdisi hazır)

---

## Phase A → B Geçiş Kararı (Stratejik Pivot)

**Tarih:** 2026-05-12 (kullanıcı kararı)

Phase A (10/10) bittikten sonra kullanıcı stratejik bir gözlem yaptı: bu sistemin asıl sorunu bug değil, **"karar verilmemiş kodların sistemde tutulması"**. Yani:

- **PTF çift SoT (P0)** — statik analiz kanıtlı
- **"Production-ready ama bağlı değil" pattern'i (P1 en kritik)** — commit mesajları ≠ kod gerçeği
  - `invoice.validation.*` "Phase F wiring" commit'i var ama main'e bağlı değil → sessiz veri hatası riski
- **pdf_api.router (P1 sessiz borç)** — orphan; yarın biri "aktif sanıp" üzerine kod yazar
- **Extractor karmaşası (P2)** — `canonical_extractor`, `fast_extractor` orphan → çöpe

**Karar:** Phase B'ye (baseline + invariant tests) geçmeden önce **3 pratik deliverable** üretilecek. Bunlar C fazının çıktıları değil; Phase B'nin karar zeminini oluşturan governance artefaktları. Audit spec kapsamı gereği kod değiştirilmez, sadece işaretlenir.

**3 deliverable (C fazı öncesi bridge):**
1. `.kiro/steering/source-of-truth.md` — SoT kararları (P-A, inclusion: always) ✅
2. `artifacts/runtime_call_graph.md` — gerçek çalışan akış (main.py'den üretilen) ✅
3. `artifacts/hard_delete_candidates.md` — risksiz silinecek dosyalar (kanıtlı) ✅
4. `artifacts/wiring_gaps.md` — bağlanması gereken modüller (kanıtlı) ✅

Phase B bu 4 dosyadan sonra başlayacak.

---

### [ ] B1 — Golden baseline capture (zorunlu, cleanup'tan ÖNCE)
- **Giriş:** canlı backend + 5 temsili dönem: 2026-01, 2026-02, 2026-03, 2026-04, 2025-12 (404 case)
- **Çıktı:** `baselines/YYYY-MM-DD_golden_baseline.json`
- **Kanıt:**
  - Her dönem için `/api/pricing/analyze`, `/api/epias/prices/{period}`, `/api/full-process` (varsa) response'u hash'lenir
  - 2025-12 dönemi 404 + `market_data_not_found` olarak kilitlenir
  - Dosya git'e commit edilir (bu spec kapatıldığında referans kalsın)
  - R22 tamamlandı

### [ ] B2 — Frontend replica hesap fonksiyonları çıkarımı
- **Giriş:** `frontend/src/App.tsx::liveCalculation`, `frontend/src/market-prices/*`
- **Çıktı:** `scripts/fe_replica_calc.py` — FE formüllerinin Python replikası
- **Kanıt:**
  - Replika en az şu hesapları kapsar: yekdem_inclusive_unit_price, total_cost, sales_price, net_margin
  - Her fonksiyon docstring'inde FE karşılığı `dosya:satır` referanslı
  - Birim testi: replika fonksiyon 3 temsili girdiyle beklenen çıktıyı döndürür

### [ ] B3 — FE/BE calc diff testi
- **Giriş:** B1 baseline + B2 replika + canlı backend
- **Çıktı:** `artifacts/phase3_calc_diff.json` — her calc-pair için (BE, FE_replica, diff)
- **Kanıt:**
  - 5 dönem × N calc-pair çalıştırılır
  - Tolerans 0.01 TL; aşılırsa bulgu P0 etiketli
  - Hiç fark yoksa "MODEL CONSISTENT" raporlanır
  - R6 + R25 tamamlandı

### [ ] B4 — Input parametre eşleşmesi
- **Giriş:** B2 replika + BE fonksiyon imzaları (AST)
- **Çıktı:** `artifacts/phase3_input_matching.json` — her calc-pair için param matrix
- **Kanıt:**
  - Her çift için `{be_param, fe_param, unit, default_be, default_fe, match_status}` satırı
  - Mismatch'ler severity atanır (birim fark → P0, default fark → P1, naming → P3)
  - R21 tamamlandı

### [ ] B5 — Cross-source period diff test
- **Giriş:** A2 artifact, canonical set (PTF=hourly, YEKDEM=monthly_yekdem)
- **Çıktı:** `artifacts/phase3_period_integrity.json`
- **Kanıt:**
  - Smoke'dan bilinen: `hourly_market_prices` 56 dönem eksik, `monthly_yekdem_prices` 39 dönem eksik
  - Her eksiklik için etkilenen iş akışı (teklif/risk/fatura) etiketlenir
  - R7 tamamlandı

### [ ] B6 — Cache versioning guard
- **Giriş:** A2 cache tablo envanteri
- **Çıktı:** `artifacts/phase3_cache_audit.json`
- **Kanıt:**
  - `analysis_cache`: key şeması `{period, params_hash}` — `params_hash` **source version ile beslenmiyor** → P0 aday
  - Her cache entry için `{has_version_in_key, invalidation_trigger, stale_risk}` satırı
  - R19 tamamlandı

### [ ] B7 — Invariant test dosyası üretimi
- **Giriş:** A9 SoT matrisi, B3 FE/BE diff, B6 cache kuralları
- **Çıktı:** `backend/tests/test_invariants.py` (otomatik üretilmiş, CI'da koşacak)
- **Kanıt:** Dosya şu testleri içerir:
  - `test_fe_be_parity` — property-based (hypothesis), 100+ örnek, tolerans 0.01
  - `test_no_new_yekdem_writers` — grep guard (SoT haricinde yazıcı yok)
  - `test_no_new_ptf_writers` — grep guard
  - `test_no_parallel_calc_path` — canonical hesap fonksiyonları dışında matematiğin duplike edilmediği
  - `test_cache_keys_have_version` — regex guard
  - `test_no_silent_fallback` — fallback_mode=true ise response şeması R26 alanlarını içeriyor
  - R25 + P-D tamamlandı

### [ ] B8 — Invariant testleri çalıştırma (baseline state)
- **Giriş:** B7 test dosyası
- **Çıktı:** `pytest backend/tests/test_invariants.py` çıktısı + `artifacts/phase3_invariants_run.txt`
- **Kanıt:**
  - Mevcut state'te F-PTF ve F-YEKDEM-eski testleri **başarısız olacak** (kanıtlanmış duplikasyon) — bu beklenen davranış
  - Fail listesi rapora yazılır; fix spec'lerinin DoD'si bu testlerin GEÇMESİDİR
  - Geçen testler (örn. cache version) raporda "invariant established" olarak işaretlenir

### [ ] B9 — Schema drift guard eklemesi
- **Giriş:** A1 pre-flight çıktısı
- **Çıktı:** `backend/tests/test_invariants.py` içine `test_schema_in_sync_with_alembic`
- **Kanıt:**
  - Her CI run'ında `alembic current == alembic heads` doğrulanır
  - `PRAGMA table_info` ile canlı şema vs Alembic model karşılaştırması
  - R23 CI'da kalıcı

### [ ] B10 — Audit scriptlerinin kendi regresyon testi
- **Giriş:** `scripts/01_inventory_db.py` + diğer audit scriptleri
- **Çıktı:** `backend/tests/test_audit_scripts.py`
- **Kanıt:**
  - Her script idempotent (iki kez çalışınca aynı artifact hash)
  - Regex eşleşmeleri unit test ile sabitlenir (örn. `ptf_tl_per_mwh` → `ptf` domain; smoke v1 bug'ını önler)
  - Snapshot tablo exclude kuralı test edilir

---

## Phase C — Output (8 task)

### [ ] C1 — audit-report.md üretimi
- **Giriş:** Tüm A ve B artifactları
- **Çıktı:** `.kiro/specs/codebase-audit-cleanup/audit-report.md`
- **Kanıt:**
  - R14'deki 13 bölüm dolu: metadata, yönetici özeti, 8 denetim alanı, inline-fix log (bu spec'te boş; kural gereği fix yok), user-decision, roadmap
  - F-PTF ilk bulgu olarak 14. bölümde (audit-report) ve roadmap'te
  - Her bulgu için kanıt (SQL/file/API) referanslı
  - Metadata bloğu: tarih, sha, bulgu sayımı, P0/P1/P2/P3 dağılımı

### [ ] C2 — SoT matrisi → `source-of-truth.md` steering
- **Giriş:** A9 SoT artifact
- **Çıktı:** `.kiro/steering/source-of-truth.md` (`inclusion: always`)
- **Kanıt:**
  - Her agent oturumu açılışında bu dosya context'e yüklenir (P-A)
  - "Yasak davranışlar" bölümü: yeni YEKDEM/PTF yazıcı eklenmesin, cache versionsuz yazılmasın, FE'de hardcoded tarife olmasın
  - Canonical kaynak tablosu SoT matrisinden otomatik türetilir

### [ ] C3 — Cleanup roadmap konsolidasyonu (P0 → P3)
- **Giriş:** Tüm bulgular, classification sonuçları
- **Çıktı:** `audit-report.md` §13 "Cleanup Roadmap" bölümü
- **Kanıt:**
  - Her madde: `{id, title, severity, effort_s_m_l, depends_on, delegated_to_spec}`
  - P0 maddeler: F-PTF → `ptf-sot-unification` (devir)
  - P1 maddeler: F-YEKDEM-eski → `yekdem-legacy-migration` (devir)
  - P2/P3 maddeler: audit spec'inde kalır, backlog

### [ ] C4 — `ptf-sot-unification` spec iskelet devri
- **Giriş:** F-PTF bulgusu + R26 Hybrid-C policy
- **Çıktı:** `.kiro/specs/ptf-sot-unification/` klasörü + `README.md` (iskelet)
- **Kanıt:**
  - Klasör içinde placeholder `requirements.md` (F-PTF referansı) ve `README.md` (devir notu) var
  - Audit-report.md'de C3 roadmap → bu spec referansı mevcut
  - Bu spec'in içeriği ayrı bir oturumda doldurulacak (audit-cleanup scope değil)

### [ ] C5 — `yekdem-legacy-migration` spec iskelet devri
- **Giriş:** F-YEKDEM-eski bulgusu
- **Çıktı:** `.kiro/specs/yekdem-legacy-migration/` klasörü + `README.md` (iskelet)
- **Kanıt:**
  - 39 eksik dönem listesi README'de kilitli
  - Devir notu: "mirror + metric log + gelecekte fallback kapatma"
  - Audit-report.md'de referans

### [ ] C6 — Hook: her PR'da invariant testleri zorunlu
- **Giriş:** B7/B8/B9 test dosyaları
- **Çıktı:** `.kiro/hooks/invariants-on-push.json` (hook tanımı)
- **Kanıt:**
  - Hook tanımı schema-valid (eventType: preToolUse veya CI gate şeklinde)
  - `test_invariants.py` fail ederse push/PR engellenir (ya da hook uyarı verir)
  - README'de hook'un amacı açıklanmış

### [ ] C7 — Audit DoD kontrolü ve kapanış checklist
- **Giriş:** Tüm A/B/C çıktıları
- **Çıktı:** `audit-report.md` sonunda "Definition of Done" bölümü
- **Kanıt (R18):**
  - ✅ Faz A-B-C tamamlandı, her faz için artifact mevcut
  - ✅ R2-R9 denetim alanlarının her birinde en az 1 kanıtlı bulgu veya "yok" teyidi
  - ✅ Inline-fix log boş (scope gereği) — bu spec fix yapmıyor
  - ✅ Roadmap en az 1 P0 + 1 P1 madde içeriyor (F-PTF + F-YEKDEM-eski)
  - ✅ Drift testi PASS (mevcut state için baseline kilitli)
  - ✅ `source-of-truth.md` yazıldı
  - ✅ `test_invariants.py` yazıldı ve CI'da (mevcut fail'ler beklenen)
  - ✅ Özet mesaj: toplam bulgu, P0/P1/P2/P3 dağılımı, devredilen spec'ler

### [ ] C8 — Kullanıcı özet bildirimi + devir
- **Giriş:** C7 DoD raporu
- **Çıktı:** Agent'tan kullanıcıya Türkçe özet mesaj:
  - "Audit tamamlandı. X bulgu, {P0: N, P1: M, P2: K, P3: L}. İki spec devredildi: `ptf-sot-unification`, `yekdem-legacy-migration`. Invariant testleri CI'da. source-of-truth.md steering aktif. Cleanup başlatmak için hangi spec?"
- **Kanıt:**
  - Mesaj chat'e yazılır
  - audit-report.md yolu verilir
  - Sıradaki adım (fix spec'i seç) açıkça sorulur

---

## Görev İstatistikleri

| Faz | Task | Bağımlılıklar | Çıktı tipi |
|---|---|---|---|
| A (Audit) | 10 | Lineer + A2 smoke'dan done | JSON artifact |
| B (Guard & Proof) | 10 | A2-A9'a bağımlı | Test + artifact |
| C (Output) | 8 | A + B bitmeli | Rapor + spec iskelet |
| **Toplam** | **28** | | |

## Kritik Hatırlatmalar

- ⚠ **Bu spec fix yapmaz.** F-PTF ve F-YEKDEM-eski sadece raporlanır, düzeltmeleri ayrı spec'lerde yapılır (C4, C5).
- ⚠ **Baseline B1 zorunlu** ve cleanup spec'leri başlamadan önce commit edilmeli; yoksa drift test imkansız.
- ⚠ **Invariant testler (B7, B8) mevcut durumda fail edecek** — bu bug değil, beklenen kanıt. Fix spec'leri bu testleri yeşile çevirir.
- ⚠ **Smoke test'ten gelen kanıt (A2) tekrarlanmayacak**; zaten done olarak işaretli.

---

**Spec hazır.** Başlamak için A1'den sıra ile ilerlenir. A2 ✅ (smoke'da tamamlandı).
