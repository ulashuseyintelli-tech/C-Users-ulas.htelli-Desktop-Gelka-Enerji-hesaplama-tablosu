# Wiring Gaps — bağlanması gereken modüller (kanıtlı)

> **Kaynak:** A6 import closure + A7 invoice flow + A10 parallel paths
> **İlke:** "Kod yazılmış ama sistemde yok" pattern'i. Bu dosya, **production'a bağlanma niyeti** olan ama main.py zincirinde görünmeyen modülleri listeler. Her biri için ya bağlama ya silme kararı verilmeli — **"kalsın, ilerde bağlanır"** seçeneği yasak (sessiz veri hatası riski).
>
> **R1 ilkesi:** Commit mesajındaki "wiring complete", "production ready", "0 failed tests" iddiaları kanıt değildir. Tek kanıt main.py import zinciridir.

---

## 1. 🔴 F-VALIDATION — Invoice validation stack (P1 en kritik)

### Durum
- **Dosya sayısı:** 20+ Python modülü `backend/app/invoice/validation/` altında
- **Test kapsamı:** Tam — 54 test, 0 failed (`test_invoice_validator_*.py`, `test_invoice_telemetry_g.py`, `test_invoice_prod_hardening_h0.py`, ...)
- **Production wiring:** **YOK.** main.py `app.invoice.validation`'dan tek bir import yapmaz
- **Git kanıt (A9):** 2026-02-28 `e113705a` — commit mesajı: `Phase F: enforcement engine + wiring + 12 tests (54 total, 0 failed)` — "wiring" iddiası gerçeğe uymuyor
- **Verdict:** A7 `new_validation_stack.verdict = DEAD`

### Neden bir "gap"?
1. Legacy `app.validator::validate_extraction` 6 handler'dan 9 kez çağrılıyor — üretimde canlı ve çalışıyor
2. Yeni stack'te yazılanlar: `validator`, `enforcement`, `shadow`, `gate_evaluator`, `telemetry`, `rollout_config`, `stage_report`, `shadow_config`, `telemetry_config` — hepsi test-only
3. Yeni stack "shadow mode" yapmaya hazır (`shadow_validate_hook`, `ShadowConfig`, sampling logic) — ama shadow hook **hiçbir production kod yolundan çağrılmıyor**
4. Ne legacy validator'ın içinde `from .invoice.validation` ne main.py'de bu paketten bir import var

### Bağlama için minimum gereken (`invoice-validation-prod-hardening` spec'i)
- Legacy `app.validator::validate_extraction` fonksiyonunun sonuna shadow hook çağrısı eklenmeli:
  ```python
  # app/validator.py içinde
  result = ValidationResult(...)
  try:
      from .invoice.validation.shadow import shadow_validate_hook
      from .invoice.validation.shadow_config import load_config
      shadow_validate_hook(invoice_dict, result.errors, config=load_config())
  except Exception:
      logger.exception("shadow hook failed; kept legacy path intact")
  return result
  ```
- Bu sadece **shadow** (karşılaştır, sonucu değiştirme). Enforcement ayrı bir geçiş (Phase G olmalı, şu an mevcut değil).
- `INVOICE_SHADOW_SAMPLE_RATE` env var ile başlangıçta 0.0 → küçük bir orana çıkarılır.

### Silme için gereken alternatif
- `backend/app/invoice/validation/` klasörü ve 54 test dosyası kaldırılır (~3000 satır kod)
- `invoice-validation`, `invoice-validation-prod-hardening`, `invoice-validation-perf-telemetry` spec'leri kapatılır
- Legacy validator tek yol olarak kalır, yeni validation hedefleri terk edilir

### Karar kimde?
`invoice-validation-prod-hardening` spec'i — steering §6 "user-decision".

**Audit önerisi:** Shadow-wire et (B seçeneği riskli — 20+ dosya ve haftalar). Ama bu audit karar vermez; sadece iki seçeneği kanıtla sunar.

---

## 2. 🟠 F-DEAD_ROUTER — pdf_api (P1)

### Durum
- Dosya: `backend/app/pdf_api.py` (2026-02-19, `b0c0048c` — boş commit mesajı `18.02.2026`)
- Router tanımlı: `router = APIRouter(prefix="/pdf", tags=["pdf"])` (line 119)
- 3 endpoint tanımlı: `POST /pdf/jobs`, `GET /pdf/jobs/{id}`, `GET /pdf/jobs/{id}/download`
- İlgili service modülleri **canlı** (main.py import ediyor): `app.services.pdf_job_store`, `app.services.pdf_artifact_store`
- Ama `app.include_router(pdf_api.router)` **hiç yok**
- FE tarafından çağrı **yok** (A5 FE_ONLY=0; 3 endpoint BE_ONLY)
- `k6/pdf_jobs.js` load test dosyası var — bu endpointleri hedefliyor ama sistem bunu bilmiyor

### Neden bir "gap"?
1. Async PDF üretim pipeline'ı tasarlanmış: `pdf_job_store` (job kuyruğu) + `pdf_artifact_store` (çıktı depolama) + `pdf_api` (HTTP arayüz) + `k6/pdf_jobs.js` (load test)
2. Service modülleri canlı (main.py import ediyor) ama HTTP giriş noktası yok
3. Sistemde şu an **sync inline PDF üretim** (`POST /api/pricing/report/pdf`, `POST /offers/{id}/generate-pdf`) kullanılıyor
4. Load test'ler çağrılamıyor (endpoint 404 döner) — CI'da bu dosyalar dead

### Bağlama için gereken (`pdf-render-worker` spec'i)
```python
# backend/app/main.py
from .pdf_api import router as pdf_router
app.include_router(pdf_router)
```

Tek satırlık değişiklik. Ama:
- Async pipeline altyapısı çalışıyor mu? (pdf_job_store, worker bağlantısı vs.)
- k6 load test'leri guard altında tutulmalı
- Sync endpoint'lerle koexist stratejisi?

### Silme için alternatif
- `backend/app/pdf_api.py` + `backend/app/services/pdf_artifact_store.py` + `backend/app/services/pdf_job_store.py` + `k6/pdf_jobs.js` + ilgili testler silinir
- Sync üretim tek yol olarak kalır

### Karar kimde?
`pdf-render-worker` spec'i.

---

## 3. 🟡 Dormant modüller (14 adet) — bağlansın mı, silinmeli mi?

Bu modüller main.py zincirinde **yüklü** ama guard_config flag'leri OFF olduğu için kod yolu aktif değil. Bağlamak ≠ kod eklemek; flag açmak demek.

| Modül ailesi | Flag | Durum |
|---|---|---|
| `app.adaptive_control.*` (8 modül) | `adaptive_control_enabled=False` | SLO-based adaptive SLA controller |
| `app.guards.drift_guard` | `drift_guard_enabled=False` | Response drift detection |
| `app.guards.guard_decision` + `guard_decision_middleware` | `decision_layer_enabled=False` | Runtime guard decision layer |

### Karar kimde?
- `slo-adaptive-control` spec'i — adaptive controller için flag aç/kapa kararı
- `drift-guard` spec'i — drift guard için aynı
- `runtime-guard-decision` spec'i — decision layer için

**Not:** Dormant ≠ DEAD. Kod main'den erişilebilir, flag sadece yolu açar/kapar. Silmek isteniyorsa ilgili spec'inde karar verilmeli. Bu bir "wiring gap" değil, "feature toggle" meselesi.

---

## 4. 🟡 F-DUAL_FE — FE adapter paralelliği (P2, wiring değil migrasyon)

### Durum
- 3 endpoint'te iki FE caller:
  - `GET /admin/market-prices` — `api.ts::getMarketPrices` (@deprecated) + `marketPricesApi.ts::listMarketPrices` ✓ (canonical)
  - `POST /admin/market-prices` — `api.ts::upsertMarketPrice` (@deprecated) + `marketPricesApi.ts::upsertMarketPrice` ✓ (canonical)
  - `GET /api/epias/prices/{period}` — `api.ts::getEpiasPrices` (axios) + `App.tsx:678::fetch` (inline) — **canonical belirsiz**

### Bu bir "wiring gap" mi?
Tam olarak değil — BE tarafında sorun yok. FE iki paralel adapter tutuyor. Hem eski hem yeni client'ın ref'leri var (muhtemelen migrasyon yarıda kalmış).

### Aksiyon
- Admin endpoints → `pricing-consistency-fixes` spec'i: `api.ts`'teki deprecated fn'leri sil, kullanıcılarını `marketPricesApi.ts`'e yönlendir
- Epias endpoint → canonical karar gerek (steering §6 user-decision)

---

## 5. Özet tablo

| Gap | Severity | Aksiyon | Karar spec'i | Minimum effort |
|---|---|---|---|---|
| **F-VALIDATION** (invoice.validation.*) | **P1** | Bağla veya sil | `invoice-validation-prod-hardening` | Bağla: ~1 gün (shadow hook + env var + smoke test); Sil: 2 saat |
| **F-DEAD_ROUTER** (pdf_api) | **P1** | `include_router` ekle veya sil | `pdf-render-worker` | Bağla: 1 satır + smoke test; Sil: 30 dakika |
| **Dormant 14 modül** | P1/P2 | Flag kararı (ayrı spec'ler) | slo-adaptive-control, drift-guard, runtime-guard-decision | Her biri ayrı — kapsam büyük |
| **F-DUAL_FE admin** | P2 | Deprecated fn'leri sil (FE-only) | `pricing-consistency-fixes` | 1-2 saat |
| **F-DUAL_FE epias** | P2 | Canonical karar + tekilleştirme | steering §6 user-decision | Karar sonrası 1 saat |

---

## 6. Kritik uyarı: "Production-ready ama bağlı değil" pattern'i

Bu tablo, bu repo'da sürekli tekrar eden bir pattern'in kanıtıdır:

1. **Feature tasarlanır** (spec + design)
2. **Kod yazılır** (20+ modül, 50+ test)
3. **Testler geçer** (0 failed, 100% kapsam)
4. **"Phase X complete" / "wiring done" commit'i atılır**
5. **Üretim wiring'i atılmaz**
6. **Başka feature'a geçilir**

Sonuç: her spec'te bu pattern tekrarlanırsa repo'da **yarım implementasyonlar birikir**. `app.invoice.validation.*` (20+ dosya), `app.pdf_api + pdf_job_store + pdf_artifact_store` (4 dosya + k6 load test), `app.adaptive_control` (8 modül, 14 test dosyası) birer örnek.

### Önlem (B7 invariant test için öneri)
- Her yeni spec'in DoD'sine "main.py import edilmiş VEYA include_router yapılmış" zorunluluğu eklenmeli
- CI guard: `app/` altında yazılıp main.py'den erişilemeyen yeni modül eklenirse ilk feature branch PR'ı fail etmeli
- Steering §5 "yasak kalıplar" bunu destekler ama kod seviyesinde enforce etmez; invariant test gerekli

---

## 7. Artifact referansları

- `phase1_imports.json` → status=orphan/dormant + imported_by_main
- `phase2_invoice_flow_sources.json` → new_validation_stack.verdict=DEAD
- `phase3_duplications.json` → cleanup_list.baglanacak
- `phase4_sot_matrix.json` → archaeology.used_in_production
- `phase3_parallel_paths.json` → orphan_paths per parallel path
