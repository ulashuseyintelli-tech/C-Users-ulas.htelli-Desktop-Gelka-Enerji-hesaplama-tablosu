---
inclusion: always
---

# Source-of-Truth (SoT) Matrisi — Gelka Enerji

> **Üretim:** `codebase-audit-cleanup` spec, A9 SoT matrisi + A10 parallel paths
> **Kapsam:** iç kullanımlı Türk enerji fiyatlama aracı — fatura → teklif akışı
> **İlke (R1):** Sadece koda inan. Commit mesajları, test kapsamı veya "wiring" iddiası yeterli değildir; `main.py` ve aktif endpoint path'leri gerçeğin tek kaynağıdır.

---

## 1. Kritik SoT kararları (kilitli)

| Domain | Canonical kaynak | Yazıcı | Okuyucular | Deprecated | Migration durumu | Severity | Devir spec'i |
|---|---|---|---|---|---|---|---|
| **PTF** (saatlik, TL/MWh) | `hourly_market_prices` | admin EPİAŞ sync / `pricing_router` | `pricing_router::analyze/simulate/compare/report`, `main.py::epias endpoints` | `market_reference_prices` (legacy manuel mod) | `parallel_unresolved` | **P0** | `ptf-sot-unification` |
| **YEKDEM** (aylık, TL/MWh) | `monthly_yekdem_prices` | admin sync / bulk import | `calculator` (dolaylı), `validator` (dolaylı) | `market_reference_prices` (legacy YEKDEM rows, 39 eksik dönem) | `legacy_rows_exist` | **P1** | `yekdem-legacy-migration` |
| **invoice validation** | `app.validator::validate_extraction` *(legacy, canlı)* | main.py handler'ları | `/analyze-invoice`, `/full-process`, `/extraction/*`, `/invoices/{id}/validate`, `/invoices/{id}/extract` | — (yeni stack hâlâ bağlanmadı, DEAD) | `new_stack_dead` | **P1** | `invoice-validation-prod-hardening` |
| **pdf_jobs** (async) | **belirlenmedi** (şu an sync inline üretim canlı) | — | — | `app.pdf_api.router` (orphan, 3 endpoint wire değil) | `router_unregistered` | **P1** | `pdf-render-worker` |
| **FE admin market-prices** | `frontend/src/market-prices/marketPricesApi.ts` | admin panel components | `hooks/useMarketPricesList`, `useUpsertMarketPrice`, `useBulkImportPreview`, `useBulkImportApply`, `useAuditHistory` | `frontend/src/api.ts::getMarketPrices/getMarketPrice/upsertMarketPrice/lockMarketPrice` (@deprecated) | `dual_active` | **P2** | `pricing-consistency-fixes` |

### Karar gerekçeleri (kısaca)

- **PTF canonical = `hourly_market_prices`** — yeni Pricing Risk Engine'in saatlik veri modeli (2026-05-01 commit `83f1d3b1 feat: Pricing Risk Engine`). Legacy `market_reference_prices` repo açılışından beri (2026-01-18) kullanılıyor ama saatlik granülarite yok; migration hedefi.
- **YEKDEM canonical = `monthly_yekdem_prices`** — aynı commit'le eklendi. Legacy YEKDEM rows `market_reference_prices` içinde 39 eksik dönem var; migration için veri eksiksizlik kontrolü şart.
- **invoice validation canonical = legacy `app.validator`** — 6 üretim handler'ı çağırıyor, `reachable_from_main=True`. Yeni stack `app.invoice.validation.*` test-only, DEAD. Karar: ya bağla (shadow hook) ya sil. `invoice-validation-prod-hardening` spec'i bu kararı verir.
- **pdf_jobs canonical = belirlenmedi** — `app.pdf_api.router` tasarlanmış ama `app.include_router()` çağrılmamış. Şu an PDF üretim sync (`/api/pricing/report/pdf`, `/offers/{id}/generate-pdf`). Karar `pdf-render-worker` spec'inde.
- **FE admin canonical = `marketPricesApi.ts`** — temiz modüler client; `api.ts`'teki eski fonksiyonlar `@deprecated` ile işaretli. 3 endpoint'te dual aktif.

---

## 2. Snapshot tablolar (SoT değil; dokunulmaz)

Aşağıdaki tablolar **aynı veriyi dublike etmez**; geçmiş kayıt tutarlar. Audit/cleanup'ta hedef **değildir**:

- `offers` — oluşturulan teklifin hesaplanmış anı
- `invoices` — fatura ekstraksiyonu + validation sonucu
- `price_change_history` — market price değişim denetim izi
- `analysis_cache` — hesap sonucu cache'i

Kural: **snapshot ≠ duplication.** Bir domain `hourly_market_prices` gibi operasyonel veri kaynağıysa SoT kuralına tabidir; `offers` gibi bir **dönem fotoğrafı** ise değildir.

---

## 3. Hybrid-C politikası (R26 — kilit karar)

Teklif ve fatura akışlarında PTF/YEKDEM eksikse davranış:

- **Saatlik veri mevcut değilse** → `/api/pricing/analyze` ve diğer teklif/PDF endpoint'leri **HTTP 409** `market_data_not_found` döndürür
- **Preview modları (salt görüntüleme)** serbest — saatlik veri yoksa da çalışır, uyarı gösterir
- Uygulama kuralı: **aylık ortalamadan saatlik türetme yapılmaz** (bu legacy `market_reference_prices` yolu); eksik dönem → hata

---

## 4. Yeni agent oturumu için direktifler (P-A)

Bu steering `inclusion: always`. Her yeni oturumda otomatik yüklenir. Aşağıdaki sorulara doğrudan bu matristen cevap verilir:

- **"PTF nereden?"** → `hourly_market_prices` (canonical). `market_reference_prices` **legacy**, yeni kod yazma.
- **"YEKDEM nereden?"** → `monthly_yekdem_prices`. Legacy `market_reference_prices` YEKDEM rows migration bekliyor.
- **"Fatura validasyonu hangi fonksiyondan?"** → `app.validator::validate_extraction` (legacy canlı). `app.invoice.validation.*` stack DEAD, kullanma.
- **"PDF async job akışı nasıl?"** → Şu an **yok**. Sync üretim: `/api/pricing/report/pdf`, `/offers/{id}/generate-pdf`. `app.pdf_api.router` orphan, çağırma.
- **"FE admin market-prices için hangi client?"** → `frontend/src/market-prices/marketPricesApi.ts`. `api.ts` içindeki `getMarketPrices/upsertMarketPrice` **@deprecated**, yeni kod yazarken kullanma.

---

## 5. Yasak kalıplar (grep guard adayları)

Aşağıdaki pattern'ler yeni kodda tespit edilirse CI fail etmeli (B7 invariant test kapsamı):

- Yeni Python kodunda `market_reference_prices` SELECT/INSERT — eğer amaç PTF/YEKDEM yazmaksa
- Yeni Python kodunda `from app.invoice.validation` — stack DEAD, production wiring kararı öncesi import yasak
- Yeni TS kodunda `api.ts`'den `getMarketPrices`, `getMarketPrice`, `upsertMarketPrice`, `lockMarketPrice` import'u
- Yeni kodda `app.pdf_api`'ye doğrudan import — orphan, `include_router` kararı alınmadan çağırma

---

## 6. Belirsiz alanlar (user-decision beklemekte)

| Konu | Seçenek A | Seçenek B | Karar kimde |
|---|---|---|---|
| `/api/epias/prices/{period}` FE canonical | `App.tsx::fetch` (inline) | `api.ts::getEpiasPrices` (axios wrapper) | C2 veya pricing-consistency-fixes |
| Yeni validation stack | Bağla (shadow hook, sonra enforcement) | Sil (20+ dosya) | invoice-validation-prod-hardening |
| pdf_api | `include_router` + async pipeline aktif et | Silmek (sync yeter) | pdf-render-worker |

---

## 7. Audit kanıt zinciri (referans)

Bu matris şu artifact'lara dayanır (`.kiro/specs/codebase-audit-cleanup/artifacts/`):

- `phase1_db_inventory.json` (A2) — 19 tablo, rol etiketleri, F-PTF auto-flag
- `phase2_endpoint_mapping.json` (A5) — FE↔BE eşleşme, 3 dual FE
- `phase2_invoice_flow_sources.json` (A7) — new_validation_stack.verdict=DEAD
- `phase1_imports.json` (A6) — 156 modül, 88/91 reachable, orphan_routers
- `phase3_duplications.json` (A8) — F-PTF, F-YEKDEM-eski, F-VALIDATION, F-DEAD_ROUTER, F-DUAL_FE
- `phase4_sot_matrix.json` (A9) — canonical/writer/readers + git arkeolojisi
- `phase3_parallel_paths.json` (A10) — 8 paralel yol, 6 convergent

### Post-audit aksiyonlar (kronolojik)

- **2026-05-12** — `baselines/2026-05-12_pre-ptf-unification_baseline.json` alındı; **INVALIDATED** (cache key collision, pricing-cache-key-completeness kapsamında yeniden alınacak)
- **2026-05-12** — `pricing-cache-key-completeness` P0 bugfix spec'i açıldı ve tamamlandı: `build_cache_key` 7→12 alan, `CACHE_KEY_VERSION=v2`, response'a `CacheInfo` eklendi, 44 test yeşil (22 regression + 17 PBT + 5 integration)

> **Değiştirme kuralı:** bu steering'e yeni bir canonical karar eklenecekse, önce ilgili artifact'ta kanıt güncellenmeli. "Benim kararımdır" tipi değişiklik yasak (R1).
