# Runtime Call Graph — gerçek çalışan akış

> **Kaynak:** A6 import closure (`phase1_imports.json::alive_from_main`) + A3/A5 endpoint wiring + A7 invoice flow source map
> **İlke (R1):** Bu diyagram yalnızca `main.py` üzerinden `app.include_router`, `app.add_middleware` ve doğrudan endpoint dekoratörleri ile ulaşılabilir kodu gösterir. Commit mesajı "wiring complete" dese de, buradaki akışta görünmüyorsa sistemde yoktur.

---

## 1. Uygulama ayağa kalkış zinciri

```
python -m backend.app.main
  │
  ├─ app = FastAPI()
  ├─ app.include_router(pricing_router)          [pricing/router.py — 15 endpoint]
  │
  ├─ app.add_middleware(CORSMiddleware)          [stdlib]
  ├─ app.add_middleware(MetricsMiddleware)       [app.metrics_middleware]
  ├─ app.add_middleware(GuardDecisionMiddleware) [app.guards.guard_decision_middleware — DORMANT, flag OFF]
  ├─ app.add_middleware(OpsGuardMiddleware)      [app.ops_guard_middleware]
  │
  └─ @app.on_event("startup")
       ├─ check_production_guard         [app.incident_service]
       ├─ validate_environment           [app.incident_service]
       ├─ validate_config                [app.config]
       ├─ load_guard_config              [app.guard_config]
       ├─ log_pilot_config               [app.pilot_guard]
       ├─ init_db                        [app.database]
       └─ seed_profile_templates         [app.pricing.profile_templates]
```

**Ayağa kalkış sırasında yüklenmez:**

- `app.pdf_api` — `include_router` çağrılmamış (orphan router)
- `app.invoice.validation.*` — import yok, sadece testlerde çağrılıyor (DEAD stack)
- `app.adaptive_control.*` — guard_config flag OFF (dormant, yüklenmez)
- `app.guards.drift_guard` — flag OFF (dormant)

---

## 2. Fatura → Teklif akışı (canlı production yolu)

### 2.1 `POST /analyze-invoice` (ACTIVE — FE çağırıyor)

```
HTTP POST /analyze-invoice  (main.py:776 analyze_invoice)
  │
  ├─ file.content_type kontrol
  │    └─ .html → app.html_render.render_html_to_image_async
  │    └─ .pdf  → app.pdf_text_extractor.extract_text_from_pdf
  │
  ├─ app.guards.dependency_wrapper.Wrapper.call(external_api)
  │    └─ app.extractor.extract_invoice_data      [OpenAI / canonical extractor]
  │
  └─ app.validator.validate_extraction            ◄── LEGACY canonical validator
       ├─ app.distribution_tariffs.get_distribution_unit_price_from_extraction
       └─ app.config.THRESHOLDS
```

### 2.2 `POST /full-process` (ACTIVE — FE `api.post('/full-process')`)

```
HTTP POST /full-process  (main.py:947 full_process)
  │
  ├─ [Sayfa 1 PDF işleme — aynı aile]
  │    ├─ app.html_render
  │    ├─ app.pdf_text_extractor
  │    └─ app.region_extractor (ROI)
  │
  ├─ app.extractor.extract_invoice_data
  ├─ app.validator.validate_extraction             ◄── LEGACY canonical
  ├─ app.calculator.calculate_offer                ◄── dolaylı yol: PTF/YEKDEM okur
  │    ├─ app.database (hourly_market_prices, monthly_yekdem_prices, market_reference_prices)
  │    └─ app.parse_tr.reconcile_amount
  │
  └─ (opsiyonel) app.pdf_generator.generate_offer_pdf_bytes  [sync PDF]
```

### 2.3 `PATCH /extraction/patch-fields` + `POST /extraction/apply-suggested-fixes` (DEAD — FE çağırmıyor)

```
HTTP PATCH /extraction/patch-fields    (main.py:2311)
HTTP POST  /extraction/apply-suggested-fixes  (main.py:2361)
  │
  └─ app.validator.validate_extraction             ◄── LEGACY canonical
       (tekrar validate)
```

**Not:** Endpoint'ler DEAD (A8 usage_class=DEAD) ama **validator modülü CANLI** çünkü `/analyze-invoice` ve `/full-process` de çağırıyor. Endpoint silinebilir, modül kalır.

---

## 3. Pricing Risk Engine akışı (ACTIVE — FE `fetch(${API_BASE}/api/pricing/*)`)

```
app.include_router(pricing_router)  → prefix=/api/pricing
  │
  ├─ POST /api/pricing/analyze   (pricing/router.py:443 analyze)
  │    ├─ app.pricing.pricing_engine.analyze
  │    │    ├─ app.pricing.yekdem_service     → monthly_yekdem_prices  [CANONICAL YEKDEM]
  │    │    ├─ app.pricing.imbalance           → hourly_market_prices  [CANONICAL PTF]
  │    │    ├─ app.pricing.margin_reality
  │    │    ├─ app.pricing.multiplier_simulator
  │    │    └─ app.pricing.risk_calculator
  │    └─ app.pricing.pricing_cache
  │
  ├─ POST /api/pricing/simulate
  ├─ POST /api/pricing/compare
  ├─ POST /api/pricing/report/pdf   → app.pricing.pricing_report + app.pdf_generator
  ├─ POST /api/pricing/report/excel → app.pricing.excel_parser
  ├─ POST /api/pricing/upload-market-data
  ├─ POST /api/pricing/upload-consumption → app.pricing.consumption_service
  ├─ POST /api/pricing/yekdem       → app.pricing.yekdem_service
  ├─ GET  /api/pricing/yekdem/{period}
  ├─ GET  /api/pricing/yekdem
  ├─ GET  /api/pricing/templates    → app.pricing.profile_templates
  ├─ GET  /api/pricing/periods
  ├─ GET  /api/pricing/bayi-segments
  ├─ GET  /api/pricing/distribution-tariffs         [DEAD — FE çağırmıyor]
  └─ GET  /api/pricing/distribution-tariffs/lookup  [DEAD]
```

---

## 4. EPİAŞ akışı (ACTIVE)

```
HTTP GET /api/epias/prices/{period}  (main.py:4503)
HTTP POST /api/epias/prices/{period} (main.py:4544)
  │
  ├─ app.epias_client  → EPİAŞ API (external)
  └─ app.database      → hourly_market_prices [CANONICAL PTF yazma]
                      → market_reference_prices [LEGACY, Hybrid-C'de yazma yok]

FE tarafında DUAL çağrı (F-DUAL_FE bulgusu):
  - frontend/src/api.ts:653 → api.get(`/api/epias/prices/${period}`)
  - frontend/src/App.tsx:678 → fetch(`${API_BASE}/api/epias/prices/${period}`)
```

---

## 5. Admin market-prices akışı (ACTIVE — FE yeni `marketPricesApi.ts`)

```
FE: frontend/src/market-prices/marketPricesApi.ts  [CANONICAL FE]
  │   (adminApi axios instance, X-Admin-Key interceptor)
  │
  ├─ GET  /admin/market-prices              (main.py:3340 list_market_prices)
  ├─ POST /admin/market-prices              (main.py:3662 upsert)
  ├─ POST /admin/market-prices/import/preview (main.py:3929)
  ├─ POST /admin/market-prices/import/apply   (main.py:4045)
  └─ GET  /admin/market-prices/history         (main.py:3449)
      │
      └─ app.market_price_admin_service
          ├─ app.market_price_validator
          └─ app.database → market_reference_prices (eski; yeni tablo bağlantısı ayrı spec)

FE: frontend/src/api.ts  [DEPRECATED — dual adapter]
  ├─ getMarketPrices(limit)       → GET  /admin/market-prices?limit=N
  ├─ upsertMarketPrice(...)       → POST /admin/market-prices
  ├─ getMarketPrice(period)       → GET  /admin/market-prices/{period}
  └─ lockMarketPrice(period)      → POST /admin/market-prices/{period}/lock
```

---

## 6. Orphan / yüklenmeyen dallar (bilgilendirme)

Aşağıdakiler `main.py` zincirinde **yer almaz** — kod var, yol yok:

| Modül / endpoint | Neden yok? | A6/A7 artifact referansı |
|---|---|---|
| `app.pdf_api.router` (3 endpoint `/pdf/*`) | `include_router` çağrılmamış | `orphan_routers[0]` |
| `app.invoice.validation.validator::validate` | main import etmiyor | `alive_from_tests_only` |
| `app.invoice.validation.enforcement::apply_enforcement` | main import etmiyor | `alive_from_tests_only` |
| `app.invoice.validation.shadow::shadow_validate_hook` | main import etmiyor, legacy validator da çağırmıyor | `alive_from_tests_only` |
| `app.canonical_extractor`, `app.fast_extractor` | main import etmiyor | orphan, fully-dead |
| `app.rq_worker`, `app.worker`, `app.worker_pg` | main import etmiyor | orphan, fully-dead |
| `app.seed_market_prices` | main import etmiyor (bir kerelik seed script olabilir) | orphan |
| `app.adaptive_control.*` | `guard_config::adaptive_control_enabled=False` | dormant |
| `app.guards.drift_guard` | `drift_guard_enabled=False` | dormant |
| `app.guards.guard_decision` | `decision_layer_enabled=False` | dormant (middleware yüklü ama flag kapalı) |

---

## 7. Kritik çıkarım (R1 ilkesi uygulaması)

**Repo'da 156 modül var. Üretim runtime'ında sadece 68'i yüklü.** Geri kalanlar:

- **59 modül** sadece testlerden erişilebilir (= CI'da test yapılır ama canlıda kodu kimse çağırmaz)
- **15 modül** ne main ne test — orphan (hiçbir yerden erişim yok)
- **14 modül** flag kapalı (dormant, yüklenmez)

Bu tablo özellikle şu soruyu somutlaştırır:

> "`app.invoice.validation.*` içinde `Phase F: enforcement engine + wiring + 12 tests (54 total, 0 failed)` commit'i var. Wire oldu mu?"

Cevap: **hayır.** Modül adı main.py'de geçmiyor, import yok, `include_router` çalıştırmadığı için orphan router statüsünde de değil — doğrudan **hiçbir production yolunda yok.** Testler `app.invoice.validation.X` modüllerini doğrudan import ediyor; bu nedenle test-only.

Bu durumun tehlikesi: birisi "wiring" commit'ini görüp yeni stack'i **üretimde aktif sanarak** üzerine kod yazarsa, sessiz veri hatası üretir. Steering'deki yasak kalıp (`from app.invoice.validation`) bunu CI seviyesinde önlemeye dönük bir guard'dır.
