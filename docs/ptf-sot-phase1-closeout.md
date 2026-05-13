# PTF SoT Unification — Phase 1 Closeout

**Spec:** `.kiro/specs/ptf-sot-unification/`
**Phase:** 1 (Write Lock + Read Dispatcher + Kill Switch)
**Freeze tag:** `phase1-ptf-sot-freeze` @ `dee54a8`
**Baseline:** `baselines/2026-05-13_post-phase1-ptf-sot_baseline.json`
**Status:** ✅ COMPLETE

---

## TL;DR

Sistem ilk kez **deterministic rollback-capable migration layer**'a sahip. PTF okuma kaynağı tek bir env flag ile canonical (`hourly_market_prices`) ↔ legacy (`market_reference_prices`) arasında geçirilebilir; cache izolasyonu test-proven, manuel yazma yolları kilitli, eksik veri (canonical PTF veya YEKDEM) silent fallback üretmek yerine 404 atıyor.

---

## Yapılanlar

### Implementasyon (8 task)

| Task | Commit | Özet |
|---|---|---|
| T1.1 baseline rerun | (önceki) | v2 cache key ile pre-migration baseline (ardından T1.7 ile post baseline tek dosyada birleşti) |
| T1.2 guard switches | `a569c4f` | `use_legacy_ptf`, `ptf_drift_log_enabled` env flagleri + tripwire test |
| T1.3 drift log model | `d57bf71` + `63c3def` | `ptf_drift_log` tablosu (alembic 012) + fail-open write helper + DriftRecord dataclass |
| T1.4 read dispatcher | `6bea83c` | `_load_market_records` canonical/legacy routing; cache key'e `ptf_source` boyutu |
| T1.5 write lock | `47170fa` | Manuel PTF yazma yolları (admin upsert, sample seed, bulk import, seed_market_prices) → 409 / no-op |
| P0 YEKDEM hard-block | `46bbeb2` | YEKDEM eksikse analyze 404 döner — silent `yekdem=0` fallback yasak |
| T1.6 kill switch behavior tests | `a7f6f6a` | Rollback shape, cache namespace, stale cache isolation testleri |
| T1.7 post-phase1 baseline | `dee54a8` | 30 senaryo, low ≠ high hash doğrulandı, freeze tag |
| T1.8 closeout | bu commit | Tasks sync, runbook, closeout doc |

**Toplam:** ~8 commit, ~125 yeni test, 1 yeni alembic migration, 1 freeze tag, 2 yeni doküman.

### Mimari Kazanım

Eskiden:

> Sistem "bir şekilde" fiyat üretiyordu. Hangi tablodan okunduğu, switch sonrası ne olacağı, eksik veride ne döneceği belirsizdi.

Şimdi:

| Boyut | Kontrat |
|---|---|
| Kaynak | `hourly_market_prices` (canonical) — `OPS_GUARD_USE_LEGACY_PTF=true` ise `market_reference_prices` |
| Eksik veri davranışı | 404 `market_data_not_found` (Hybrid-C, silent fallback yok) |
| Eksik YEKDEM davranışı | 404 `yekdem_data_not_found` (P0 financial safety) |
| Cache namespace | `ptf_source` boyutu cache key'in parçası — toggle stale dönmez |
| Manuel yazma | 409 `manual_ptf_disabled` / `bulk_ptf_disabled` / `ptf_seed_disabled` |
| Rollback süresi | env flag + worker reload ≤ 10 dk |
| Test coverage | 8 farklı garanti behavior test ile kayıt altında |

---

## Gerçeklik / Plan Sapmaları

### T1.4 — plan ↔ uygulama farkı

**Plan:** EPİAŞ POST endpoint'i canonical yazsın; eski yazıcı `use_legacy_ptf` branch altında kalsın.

**Uygulama:** Read-side dispatcher (`_load_market_records` → canonical/legacy). Yazıcı tarafı T1.5'e devredildi.

**Gerekçe:** Phase 1'in temel amacı **deterministic rollback-capable read path**. Canonical writer'ı bu phase'de işleyip aynı anda lock'lamak risk yığar. Read dispatcher + write lock kombinasyonu, canonical'a hangi yoldan veri girdiğini bağımsız bir konuya çevirdi (Phase 4 öncesinde EPİAŞ POST'un canonical'a yazması ayrı bir issue olarak takip edilebilir; şu an admin UI üzerinden zaten canonical'a giriliyor).

Sapma user-decision değil; daha temkinli bir tercih. DoD'yi bozmuyor.

### T1.6 — metrik ertelendi

`ptf_legacy_fallback_total{period}` Prometheus metriği plan'da T1.6'da öngörülmüştü. Phase 2 telemetry işine kaydırıldı; Phase 1'in core'u behavior testleri ile zaten kayıt altında. Bu sapma da risksiz — metrik gözlenebilirlik artırır, davranış değiştirmez.

---

## Phase 2 Geçişi İçin Direktifler

### Branch stratejisi

**Yeni branch'i `phase1-ptf-sot-freeze` tag'inden alın, HEAD'den değil.** Tag güvenli anchor; HEAD ileride değişirse rollback noktası kayar.

```bash
git checkout phase1-ptf-sot-freeze
git checkout -b ptf-sot-unification-phase2-dual-read
```

### Phase 2 davranış kuralları (ZORUNLU)

1. **Dual-read = observe-only**. İlk iteration'da:
   - Request **fail etmesin**
   - User'a giden response **değişmesin**
   - Sadece telemetry toplansın

2. **Authoritative kaynak hâlâ canonical.** Legacy paralel okunup sadece drift log'a yazılır.

3. **Canonical missing → 404 davranışı korunur.** Phase 2'de bile. "Dual-read var" diye automatic fallback başlatılmaz; bu çizgi bozulursa migration anlamsızlaşır.

### Drift gate kriterleri

`source-of-truth.md` §3 ve `ptf-sot-unification/tasks.md::T2.6`'ya göre:

- Ortalama `diff_percent` ≤ 0.5% → PASS
- `severity=high` oranı ≤ 5% → PASS

Bu kriterler karşılanmadan Phase 3 başlamaz.

---

## Teknik Borç (Phase 2 başlamadan)

### `datetime.utcnow()` deprecation

Test çıktılarında artık görünür hale geldi:

```
DeprecationWarning: datetime.datetime.utcnow() is deprecated...
Use timezone-aware objects: datetime.datetime.now(datetime.UTC)
```

Etkilenen alanlar:

- `pricing_cache.py:210` — cache yazma timestamp
- SQLAlchemy default'lar (`func.now()` kullanıyor zaten — sadece app-level utcnow'lar)
- Telemetry / drift logs (Phase 2'de canlıya çıkacak)

**Şimdilik blocker değil**, ama Phase 2'de drift log timestamp'leri canlıya yazılmaya başlamadan önce temizlenmeli (UTC correctness). Backlog'a alındı.

### Diğer küçük not

- `regex` → `pattern` (FastAPI Query) deprecation 2 yerde aktif (`main.py:1667`, `main.py:2801`). Pricing path'i değil; takip edilebilir.
- `on_event` → lifespan handler (FastAPI deprecation). Ayrı temizlik işi.

---

## DoD Kontrol Listesi (Phase 1)

- [x] Guard switch alanları + tripwire test
- [x] Drift log tablosu + fail-open helper
- [x] Read dispatcher (canonical/legacy)
- [x] Manuel yazma yolları kilitli
- [x] Kill switch behavior testleri (rollback shape + cache isolation)
- [x] YEKDEM hard-block (P0 safety net)
- [x] Post-phase1 baseline (30 senaryo, low ≠ high)
- [x] Freeze tag
- [x] Steering update (write_locked + post-audit aksiyon kaydı)
- [x] Rollback runbook
- [x] Phase 1 closeout doc (bu dosya)

**Phase 1 ✅ KAPANDI.**

---

## Referanslar

- Spec: `.kiro/specs/ptf-sot-unification/{requirements,design,tasks}.md`
- Steering: `.kiro/steering/source-of-truth.md`
- Rollback runbook: `docs/runbooks/ptf-sot-rollback.md`
- Baseline (post-phase1): `baselines/2026-05-13_post-phase1-ptf-sot_baseline.json`
- Tag: `phase1-ptf-sot-freeze` @ `dee54a8`
