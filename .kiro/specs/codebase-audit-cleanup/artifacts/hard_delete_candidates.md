# Hard Delete Candidates — risksiz silinebilir (kanıtlı)

> **Kaynak:** A6 import closure + A8 usage signal
> **İlke:** Bu dosya **audit kapsamında silme yapmaz** — sadece her biri için silme emniyeti kanıtlarını toplar. Gerçek silme `ptf-sot-unification`, `pdf-render-worker`, `invoice-validation-prod-hardening` gibi devrilen spec'lerde veya özel bir `codebase-dead-code-removal` spec'inde yapılır.
>
> **Emniyet kriteri (3/3 zorunlu):**
> 1. **A6:** `status=orphan` VE `imported_by_main=false` VE `imported_by_tests=[]`
> 2. **A8:** `external_mention_count=0` (shell/docs/load-test/non-FE kodda geçmiyor)
> 3. **A9:** git arkeolojisinde son değişiklik ≥30 gün önce (yeni eklenmiş kod değil)

Eğer üçünden biri bile sağlanmazsa dosya bu listeye girmez (SoT steering §4 "yasak kalıplar"a alınıp bekletilir).

---

## 1. Kategori A — Fully-dead modüller (9 dosya)

Hepsi A6 `status=orphan`, A8 `external_mention_count=0`.

| # | Modül | Dosya | Neden var? (git niyet) | Risk |
|---|---|---|---|---|
| 1 | `app.canonical_extractor` | `backend/app/canonical_extractor.py` | 2026-01-18 "Sprint 8.9.1: Production Ready" — repo açılışında eklenmiş ama üretimde `app.extractor` kullanılmış; "canonical" isim aldatıcı | Yok — hiçbir yerden referans |
| 2 | `app.fast_extractor` | `backend/app/fast_extractor.py` | 2026-01-18 aynı commit — hızlı yol olarak tasarlanmış, `app.extractor.fast_mode=True` parametresi bunun yerine geçmiş | Yok |
| 3 | `app.pricing.excel_formatter` | `backend/app/pricing/excel_formatter.py` | 2026-05-01 "feat: Pricing Risk Engine" — `app.pricing.excel_parser` kullanılıyor, formatter duplicate | Yok — `excel_parser` üretimde canlı |
| 4 | `app.rq_worker` | `backend/app/rq_worker.py` | 2026-01-18 — RQ-based background worker planı; sistem async job'a geçmedi, sync kaldı | Yok (ama aynı aileden `app.rq_adapter` canlı — ayrı dosya) |
| 5 | `app.worker` | `backend/app/worker.py` | Aynı aile — ikinci worker denemesi | Yok |
| 6 | `app.worker_pg` | `backend/app/worker_pg.py` | Aynı aile — PostgreSQL worker varyantı | Yok |
| 7 | `app.seed_market_prices` | `backend/app/seed_market_prices.py` | 2026-02-06 — tek sefer seed script, startup'ta `seed_profile_templates` kullanılıyor | **Dikkat** — silmeden önce bir kez çalıştırılmış mı teyit (git log) |
| 8 | `app.services.job_claim` | `backend/app/services/job_claim.py` | Async worker ailesiyle birlikte | Yok |
| 9 | `app.services.webhook_manager` | `backend/app/services/webhook_manager.py` | `/webhooks` endpoint'leri DEAD; webhook yönetimi test-only seviyede | Yok — webhook endpoint'leri de DEAD (kategori B) |

**Not — "canonical" kelime aldatıcısı:** `canonical_extractor.py` dosyası canonical değil, aksine orphan. Gerçek canonical `app.extractor`. İsim niyet bildirir, kod gerçeği belirler.

---

## 2. Kategori B — Orphan router + endpoint'leri (3 endpoint + 1 dosya)

`app.pdf_api` — tanımlı ama `app.include_router()` yok. Tüm 3 endpoint çalıştırıldığında 404 döner.

| Endpoint | Fonksiyon | Satır |
|---|---|---|
| `POST /pdf/jobs` | `create_pdf_job` | `backend/app/pdf_api.py:122` |
| `GET /pdf/jobs/{job_id}` | `get_pdf_job_status` | `backend/app/pdf_api.py:196` |
| `GET /pdf/jobs/{job_id}/download` | `download_pdf` | `backend/app/pdf_api.py:219` |

**Silme kararı yetkisi:** `pdf-render-worker` spec'i. İki seçenek:
- (A) Router'ı `app.include_router()` ile bağla (async PDF pipeline aktif et)
- (B) `pdf_api.py` + ilgili `pdf_job_store.py` + `pdf_artifact_store.py` hiyerarşisini sil

**Audit tarafından silme yapılmaz** (SoT steering §6 "user-decision").

---

## 3. Kategori C — DEAD endpoint'ler (27 adet, modül canlı kalır)

A8 usage_class=DEAD olan 27 endpoint. **Modül silinmez**, sadece dekoratörlü fonksiyon + route kaldırılır. Birçoğu `main.py` içindeki canlı modül fonksiyonlarıdır (örn. `/extraction/patch-fields` validator'ı çağırır ama endpoint kimse çağırmıyor).

### main.py admin endpoints
- `GET /admin/distribution-tariffs/parse` (line 4763)
- `POST /admin/epias/sync-all` (line 4619)
- `GET /admin/incidents/{incident_id}` (line 4823)
- `PATCH /admin/incidents/{incident_id}` (line 4857)
- `PATCH /admin/incidents/{incident_id}/feedback` (line 4993)
- `GET /api/epias/missing-periods` (line 4590)

### pricing_router — DEAD
- `GET /api/pricing/distribution-tariffs` (router.py:1079)
- `GET /api/pricing/distribution-tariffs/lookup` (router.py:1099)

### main.py audit + stats — DEAD (muhtemelen admin UI'da eksik)
- `GET /audit-logs` (line 3246)
- `GET /audit-logs/stats` (line 3301)
- `GET /stats` (line 2282)

### Offers CRUD — DEAD (FE offers panel mevcut değil veya kaldırılmış)
- `GET /offers` (line 1612)
- `POST /offers` (line 1569)
- `GET /offers/{offer_id}` (line 1649)
- `GET /offers/{offer_id}/download` (line 1856)
- `POST /offers/{offer_id}/generate-html` (line 1934)
- `POST /offers/{offer_id}/generate-pdf` (line 1788)
- `PUT /offers/{offer_id}/status` (line 1686)

### Webhooks CRUD — DEAD (webhook feature kullanılmıyor)
- `GET /webhooks` (line 3164)
- `POST /webhooks` (line 3111)
- `DELETE /webhooks/{webhook_id}` (line 3190)
- `PUT /webhooks/{webhook_id}/toggle` (line 3214)

### Generate PDF (direct) — DEAD
- `POST /generate-html-direct` (line 2260)
- `POST /generate-pdf-direct` (line 1962)

### Extraction patch — DEAD (ama validator çağrısı legacy path'te zaten var)
- `POST /calculate-offer` (line 906)
- `POST /extraction/apply-suggested-fixes` (line 2361)
- `PATCH /extraction/patch-fields` (line 2311)

> **Silme kararı yetkisi:** Bu endpoint'lerin her biri için **kullanıcı teyidi** gerekir. "FE kullanmıyor" = "curl/cron/admin panel eksik" de olabilir. A8 usage-signal 5 kaynak taradı (non-FE code, shell, load-test, docs, tests) ve hiçbiri ref vermedi — bu güçlü sinyal ama %100 değil. Özellikle offers/webhooks feature'ları iş planında tekrar gündeme gelebilir.
>
> **Öneri:** Phase B baseline (B1) öncesi kullanıcı onayı ile 3 alt küme:
> - **Kesin sil** (güvenli): `/generate-pdf-direct`, `/generate-html-direct`, `/admin/epias/sync-all`, `/admin/distribution-tariffs/parse`, `/api/pricing/distribution-tariffs/*` — ikinci yol (admin panel) var, bu tekrarlar kör
> - **Kullanıcı karar** (iş planı): `/offers/*` (7 endpoint), `/webhooks/*` (4 endpoint), `/audit-logs/*`, `/stats` — ürün kararı
> - **İncele, sonra karar**: `/calculate-offer`, `/extraction/*` — legacy path'te validator'a bağlı; duplicate olduğu teyit edilmeli

---

## 4. Özet tablo

| Kategori | Sayı | Kanıt kilidi | Silme riski | Karar kimde |
|---|---:|---|---|---|
| A — Fully-dead modül | 9 | 3/3 | **Düşük** | Cleanup spec (audit-cleanup sonrası) |
| B — Orphan router (pdf_api) | 3 endpoint + 1 dosya | 3/3 | **Orta** (async pipeline geri gelir mi?) | `pdf-render-worker` spec |
| C — DEAD endpoint | 27 | 3/3 ama kullanıcı onayı gerekir | **Orta** (FE eksikliği mi, kesin DEAD mi?) | Kullanıcı + ilgili product spec |
| **Toplam silme adayı** | **40** | — | — | — |

---

## 5. Yapmayacağımız işler (audit scope sınırı, R16)

- ❌ Hiçbir dosya silinmez, rename edilmez
- ❌ Hiçbir endpoint kaldırılmaz
- ❌ Hiçbir `include_router` eklenmez (bu `pdf-render-worker` işi)
- ❌ Hiçbir deprecation decorator eklenmez (bu `pricing-consistency-fixes` işi)
- ✅ Her biri için kanıt toplanır ve steering'e (§4 direktifler, §5 yasak kalıplar) yazılır

---

## 6. Artifact referansları

- `phase1_imports.json` → modules[status=orphan], orphan_routers
- `phase3_duplications.json` → cleanup_list.silinebilir_aday, endpoint_usage[usage_class=DEAD|UNREACHABLE]
- `phase4_sot_matrix.json` → archaeology (introduced_at kanıtı)
- `phase3_parallel_paths.json` → PP-PDF_JOBS, PP-EXTRACTION (orphan_paths listesi)
