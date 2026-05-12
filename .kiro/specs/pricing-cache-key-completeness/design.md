# Pricing Cache Key Completeness — Bugfix Design

## Overview

`backend/app/pricing/pricing_cache.py::build_cache_key()` aktif olarak `/api/pricing/analyze` endpoint'i tarafından SHA256 cache key üretmek için kullanılıyor. Mevcut implementasyon `AnalyzeRequest`'in **yedi** alanını (customer_id, period, multiplier, dealer_commission_pct, imbalance_params, template_name, template_monthly_kwh) hash'liyor; ancak response'u doğrudan etkileyen **beş ek alan** (`t1_kwh`, `t2_kwh`, `t3_kwh`, `use_template`, `voltage_level`) key'den eksik. Bu eksiklik production'da cache key collision'a ve yanlış müşteriye yanlış teklif dönmesine yol açıyor (B1 baseline'ında kanıtlandı).

Fix stratejisi, cerrahi ve geri dönüşü kolaydır: `build_cache_key()` signature'ına eksik beş parametreyi ekleyip `key_data` dict'ine deterministik biçimde katmak; eski (fix öncesi yazılmış) kayıtları izole etmek için key'e `_cache_version = "v2"` sabit alanı iliştirmek; ve handler çağrısını (`router.py:_analyze` içinde) yeni parametreleri geçirecek şekilde güncellemek. DDL değişmez, response şeması değişmez, cache okuma/yazma mekanizması (TTL, hit_count, invalidate_* fonksiyonları) aynı kalır.

## Risk Classification

**Severity: P0 (Production Financial Error)**. Bug üretimde canlı. Cache TTL 24 saat olduğu için bir dönem için aynı multiplier ile ilk çağrılan tüketim profilinin cevabı, sonraki 24 saat içindeki tüm farklı tüketim profillerine yanlış olarak dönüyor. Bu farklı müşterilere yanlış teklif demektir → doğrudan finansal kayıp. Fix hot-fix sırasıyla PR'lanmalı (tek commit, fast-track review).

## Scope / Blast Radius

Fix etki alanı DAR:

- **Tek dosya** (`backend/app/pricing/pricing_cache.py::build_cache_key`) signature + key_data genişlemesi.
- **Tek handler çağrısı** (`backend/app/pricing/router.py::analyze`, satır 457-466 civarı) yeni parametreleri geçirir.
- **Tek çağrı noktası:** `build_cache_key` projede başka hiçbir yerden çağrılmıyor (grep doğrulaması: sadece `router.py` ve test dosyalarından).
- **Etkilenmeyen endpoint'ler:** `/api/pricing/simulate`, `/api/pricing/compare`, `/api/pricing/report/pdf`, `/api/pricing/report/excel` cache kullanmıyor; davranışları değişmez.
- **Schema migration yok:** `analysis_cache` tablo DDL'i dokunulmaz; alembic migration gerekmez.
- **Rollback:** Tek commit revert'i ile fix geri alınır (invalidation stratejisi sayesinde bu durumda eski v1 kayıtlarına geri dönülür, bug yeniden ortaya çıkar ama downtime yoktur).

## Glossary

- **Bug_Condition (C)**: Aynı `(period, customer_id, multiplier, dealer_commission_pct, imbalance_params, template_name, template_monthly_kwh)` tuple'ına sahip **ama** farklı `(t1_kwh, t2_kwh, t3_kwh, use_template, voltage_level)` tuple'ına sahip iki `/api/pricing/analyze` isteğinin aynı cache_key üretmesi.
- **Property (P)**: İki isteğin cache key'inin farklı olması (collision yok) VE determinism'in tam girdi eşitliği altında korunması.
- **Preservation**: Mevcut 7 alanın tek tek değişmesiyle farklı key üretme davranışı, SHA256 formatı (64 hex char), cache okuma/yazma mekanizması (TTL, hit_count, invalidate_*), response şeması ve diğer pricing endpoint'lerinin davranışı.
- **`build_cache_key`**: `backend/app/pricing/pricing_cache.py` içindeki, `dict → sorted JSON → SHA256` zincirini çalıştıran pure function.
- **`AnalyzeRequest`**: `backend/app/pricing/models.py` içindeki Pydantic modeli; tüketim/profil alanlarının canonical kaynağı.
- **`AnalysisCache`**: `backend/app/pricing/schemas.py` içindeki SQLAlchemy tablo modeli; cache satırlarının fiziksel temsili.
- **`CACHE_KEY_VERSION`**: `pricing_cache.py` modül seviyesinde bu fix ile eklenecek sabit; `"v2"` değerini alır, eski v1 kayıtlarını hash düzeyinde izole eder.
- **Response Hash**: `AnalyzeResponse`'un deterministik JSON encode sonrası SHA256'sı; B1 baseline'da snapshot eşitliği için kullanılıyor — aynı key altında collision kanıtı.

## Bug Details

### Bug Condition

Bug, `build_cache_key()`'in `key_data` dict'ine **girdi profilinin beş alanını katmaması** sonucu tetiklenir. Bu beş alan (`t1_kwh`, `t2_kwh`, `t3_kwh`, `use_template`, `voltage_level`) pricing response'unun (tüketim toplamı, zaman dilimi dağılımı, dağıtım bedeli) hesabını doğrudan etkiler; dolayısıyla bu alanların değişmesi key'de de değişikliğe yol açmalıdır. Mevcut kod bunu yapmaz ve collision üretir.

**Formal Specification:**

```
FUNCTION isBugCondition(reqA, reqB)
  INPUT: reqA, reqB of type AnalyzeRequest
  OUTPUT: boolean

  // Mevcut 7 alan identik
  sameCoreFields := reqA.customer_id == reqB.customer_id
                 AND reqA.period == reqB.period
                 AND reqA.multiplier == reqB.multiplier
                 AND reqA.dealer_commission_pct == reqB.dealer_commission_pct
                 AND reqA.imbalance_params == reqB.imbalance_params
                 AND reqA.template_name == reqB.template_name
                 AND reqA.template_monthly_kwh == reqB.template_monthly_kwh

  // Eksik 5 alandan en az biri farklı
  differsInMissingField := reqA.t1_kwh != reqB.t1_kwh
                        OR reqA.t2_kwh != reqB.t2_kwh
                        OR reqA.t3_kwh != reqB.t3_kwh
                        OR reqA.use_template != reqB.use_template
                        OR reqA.voltage_level != reqB.voltage_level

  RETURN sameCoreFields AND differsInMissingField
         AND build_cache_key_current(reqA) == build_cache_key_current(reqB)
END FUNCTION
```

Eşdeğer nokta-formülasyon (PBT için daha kullanışlı):

```
FUNCTION isBugConditionSingleInput(req)
  RETURN EXISTS req' such that
         isCoreIdentical(req, req')
         AND differsInMissingField(req, req')
         AND build_cache_key_current(req) == build_cache_key_current(req')
END FUNCTION
```

### Examples

B1 baseline koşusunda (`baselines/2026-05-12_pre-ptf-unification_baseline.json`) somut olarak gözlemlenen collision'lar:

- **Dönem 2026-03, LOW profile** (`t1=25000, t2=12500, t3=12500`) → `response_hash = 95d6bada181889af…`, `total_consumption_kwh = 50000` ✓
- **Dönem 2026-03, HIGH profile** (`t1=250000, t2=125000, t3=125000`) → **aynı** `response_hash = 95d6bada181889af…`, `total_consumption_kwh = 50000` ✗ (doğrusu 500000)
- Aynı pattern 2026-01, 2026-02, 2026-04 dönemlerinde de tekrarlandı (4/4 canonical dönem kontamine).
- **Edge case (use_template):** `use_template=true + template_monthly_kwh=50000` ile `use_template=false + (t1,t2,t3)=(25000,12500,12500)` aynı core field'larla gönderilirse collision → template modu farkı key'e yansımıyor.
- **Edge case (voltage_level):** Aynı tüm alanlar ama `voltage_level="og"` vs `voltage_level="ag"` → dağıtım bedeli farklı olması gereken iki teklif aynı cache satırından dönüyor.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**

- `build_cache_key()` return tipi: 64 karakter SHA256 hex string. Format ve uzunluk **değişmez**.
- Mevcut 7 alanın tek tek değişmesiyle farklı key üretme davranışı (mevcut `test_pricing_cache.py::TestBuildCacheKey` suite'i geçmeye devam eder).
- `get_cached_result()`, `set_cached_result()`, `invalidate_cache_for_customer()`, `invalidate_cache_for_period()`, `cleanup_expired_cache()` fonksiyonlarının iç davranışı (TTL kontrolü, `hit_count` artırma, corrupt JSON temizliği, silinen kayıt sayısı dönüşü).
- `analysis_cache` tablo şeması (cache_key, customer_id, period, params_hash, result_json, created_at, expires_at, hit_count kolonları). DDL değişikliği yok, alembic migration yok.
- `AnalyzeRequest` Pydantic modeli. Beş alan zaten mevcut; yeni alan eklenmez.
- `AnalyzeResponse` Pydantic modeli. Response şeması (alanlar, `cache_hit` flag davranışı, `warnings` yapısı) aynı.
- `/api/pricing/simulate`, `/api/pricing/compare`, `/api/pricing/report/pdf`, `/api/pricing/report/excel` endpoint'leri — cache kullanmıyorlar, bu fix onları etkilemez.
- Hybrid-C politikası (R26): Saatlik veri yoksa HTTP 409 `market_data_not_found` davranışı aynen korunur.

**Scope:**

Cache key bug condition'ı dışındaki tüm davranışlar değişmeden kalır. Bu özellikle şunları içerir:

- Core 7 alandan birinin değişmesi durumunda yeni key üretimi (INV-5 altında formalize edilir).
- `AnalyzeResponse` alanları (`weighted_prices`, `pricing`, `time_zone_breakdown`, `distribution`, `risk_score`, `safe_multiplier`, `margin_reality`, `warnings`, `data_quality`).
- `cache_hit` flag'inin mevcut davranışı (hit'te `True`, miss'te `False`).
- Pricing hesaplama pipeline'ı (`calculate_weighted_prices`, `calculate_hourly_costs`, `calculate_time_zone_breakdown`, `calculate_imbalance_cost`, `calculate_safe_multiplier`, `calculate_risk_score`, `calculate_margin_reality`).
- `/api/pricing/analyze` handler'ının error path'leri ve warning üretim davranışı.

## Hypothesized Root Cause

Bug description ve kod incelemesi sonucu root cause **kesin olarak biliniyor** (hipotez değil, gözlem). `build_cache_key()` implementasyonu `key_data` dict'ini kurarken beş girdi alanını parametreleştirmemiş:

1. **Missing function parameters**: `build_cache_key(customer_id, period, multiplier, dealer_commission_pct, imbalance_params, template_name, template_monthly_kwh)` signature'ında `t1_kwh`, `t2_kwh`, `t3_kwh`, `use_template`, `voltage_level` yok.

2. **Missing dict members**: `key_data` dict'inde sadece 7 alan var; eksik 5 alan dict'e hiç eklenmiyor, dolayısıyla bu alanlardaki değişiklik `json.dumps(sort_keys=True)` çıktısını değiştirmiyor, SHA256 aynı kalıyor.

3. **Handler-side omission**: `router.py::analyze` handler'ı `build_cache_key(...)` çağrısında `req.t1_kwh`, `req.t2_kwh`, `req.t3_kwh`, `req.use_template`, `req.voltage_level` değerlerini geçirmediği için bilgi zaten çağrı sınırında kayboluyor.

4. **No key versioning**: Key'de versiyon bilgisi yok; bu yüzden fix deploy edildiğinde eski (kontamine) kayıtlar TTL dolana kadar `analysis_cache` tablosunda kalacak ve yeni request ile key collision yapmazlar ancak eski kayıtların kendi aralarındaki collision'ları izole edilemez. (Bu yeni oluşturulan kayıtlar için değil, tablo geçiş dönemi için önemli.)

## Correctness Properties

Property 1: Bug Condition — Missing fields discriminate cache key

_For any_ input where the bug condition holds (isBugCondition returns true — yani mevcut 7 alan identik, eksik 5 alandan en az biri farklı), the fixed `build_cache_key` function SHALL produce **different** SHA256 keys for the two inputs.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation — Full input equality yields key equality

_For any_ input where the bug condition does NOT hold (isBugCondition returns false — yani tüm 12 alan identik), the fixed `build_cache_key` function SHALL produce the same SHA256 key, preserving determinism and existing core-field discrimination behavior. Bu aynı zamanda mevcut 7 alan tek tek değiştiğinde farklı key üretme davranışının aynen devam ettiğini garanti eder (`test_pricing_cache.py::TestBuildCacheKey` suite'i PASS).

**Validates: Requirements 2.4, 3.1, 3.2, 3.3**

## Decision 1 — Cache Invalidation Strategy (Key Version Bump)

**Karar:** **Key Version Bump (A)** — TRUNCATE REDDEDİLDİ.

- `pricing_cache.py` tepesine modül seviyesinde sabit eklenir: `CACHE_KEY_VERSION = "v2"`.
- Yeni `build_cache_key` formülünde `"_cache_version"` alanı `key_data` dict'inin ilk üyesi olarak eklenir (sorted_keys altında underscore prefix ile başa alfabetik sırada da en başta kalır).
- **Artıları:** Eski v1 cache satırları otomatik izole olur (yeni key hash'i farklı olduğu için v1 kayıtlarına yeni request değmez). Production'da güvenli rollout; fix deploy edilince eski kayıtlar hit almamaya başlar, yeni request'ler v2 key ile yazılır. Rollback (v1'e geri dön) tek commit revert ile çalışır — bug geri gelir ama cache collision davranışı eskiye dönmediği için downtime yok.
- **TRUNCATE reddi gerekçesi:** Race condition riski (TRUNCATE sırasında paralel request'ler hit alabilir ve oluşan yeni kayıt eski versiyonla karışabilir) + cache miss spike (24 saatlik TTL doldurma penceresinde CPU'ya gereksiz yük) + manual operasyon gereksinimi (prod DB'ye erişim, fail-safe değil).
- **Eski kayıt temizliği:** Eski `analysis_cache` satırları TTL ile (max 24 saat) doğal olarak `cleanup_expired_cache()` tarafından silinir. **Manual silme yapılmaz.** Geçiş penceresinde eski kayıtlar kendi aralarında hit verebilir ancak yeni request'e collision vermez.

## Decision 2 — Canonical Cache Key Structure

**Karar:** Aşağıdaki key_data yapısı kilitlenir. Alan sırası `json.dumps(sort_keys=True)` ile stabilize; tipler deterministik; tüm float'lar `round()` ile normalize edilir.

```python
key_data = {
    "_cache_version": "v2",                                                          # Decision 1
    "customer_id": customer_id or "__template__",
    "period": period,                                                                # str "YYYY-MM"
    "multiplier": round(multiplier, 6),
    "dealer_commission_pct": round(dealer_commission_pct, 2),
    "imbalance": {
        "forecast_error_rate": round(imbalance_params.get("forecast_error_rate", 0.05), 4),
        "imbalance_cost_tl_per_mwh": round(imbalance_params.get("imbalance_cost_tl_per_mwh", 50.0), 2),
        "smf_based_imbalance_enabled": imbalance_params.get("smf_based_imbalance_enabled", False),
    },
    "template_name": template_name or None,
    "template_monthly_kwh": round(template_monthly_kwh, 2) if template_monthly_kwh is not None else None,
    # YENİ ALANLAR:
    "t1_kwh": round(t1_kwh, 2) if t1_kwh is not None else None,
    "t2_kwh": round(t2_kwh, 2) if t2_kwh is not None else None,
    "t3_kwh": round(t3_kwh, 2) if t3_kwh is not None else None,
    "use_template": bool(use_template) if use_template is not None else None,
    "voltage_level": voltage_level or "og",    # Decision 10: None → "og" normalize
}
```

**Kritik float normalization kuralı:** Bkz. Decision 11 — float precision tablosu.

**`use_template` None-guardı:** `AnalyzeRequest.use_template` `Optional[bool]` tipinde (default `None`); naive `bool(None) == False` olsa da "user explicitly passed False" ile "user did not pass" farkını korumak için None değeri None olarak key'e girer. Bu tip tutarlılığı da PBT property'si ile doğrulanır. Semantic gerekçe: `use_template=None` template alanlarının validate edilmediği durumu temsil eder; `use_template=False` ise T1/T2/T3 zorunlu olduğu için farklı validation path'ine gider. İki davranış aynı key'e düşmemeli.

**`voltage_level` normalization:** Bkz. Decision 10 — `None → "og"` canonical normalize.

## Decision 3 — Determinism Guarantees (PBT Invariant Set)

Aşağıdaki beş invariant design-level kuralı ve `test_pricing_cache_key_completeness_pbt.py` dosyasındaki hypothesis property'leridir:

- **INV-1 (idempotent / full-input equality):** Aynı 12 parametre (core 7 + yeni 5) → aynı SHA256 key. Yani `build_cache_key(x) == build_cache_key(x)`.
- **INV-2 (t1/t2/t3 discriminator):** Diğer 9 alan sabit, `(t1_kwh, t2_kwh, t3_kwh)` tuple'ı farklı → farklı key. Hypothesis stratejisi: `@given(st.floats(min_value=0, max_value=1e9), ...)` ile üç alan üretip en az birini değiştirip invariant doğrula.
- **INV-3 (voltage_level discriminator):** Diğer 11 alan sabit, canonical voltage_level değeri farklı → farklı key. Canonical domain: `{"og", "ag"}`. **Dikkat (Decision 10):** `None` ve `"og"` aynı canonical değere normalize olduğu için **aynı** key üretir (counter-property: `build_cache_key(voltage_level=None) == build_cache_key(voltage_level="og")`). Sadece canonical değerler ayırt edicidir.
- **INV-4 (use_template discriminator):** Diğer 11 alan sabit, `use_template` farklı (`True` / `False` / `None`) → farklı key. Domain: `{True, False, None}`. None korunur çünkü semantik fark vardır (validate edilmemiş vs explicitly False).
- **INV-5 (regression / core-7 discrimination preserved):** Mevcut 7 alan tek tek değiştiğinde hâlâ farklı key üretir. `test_pricing_cache.py::TestBuildCacheKey` 5+ mevcut test geçmeye devam eder. Bu invariant hypothesis ile de test edilir (core field permütasyonları).

Ek PBT hedefi: `@settings(max_examples=200)` ile her property için yeterli çeşitlilik; `@example(...)` ile B1 baseline'ın LOW vs HIGH profillerinin deterministik regresyonu.

## Decision 4 — Regression Test Design (3-Level)

Test stratejisi üç bağımsız katmanda doğrulama yapar. Tüm testler ilk olarak **unfixed kod üzerinde** çalıştırılır (exploration phase) ve beklenen şekilde FAIL olmalıdır; ardından fix uygulanır ve PASS olmalıdır.

### Test 1 — build_cache_key unit test (PBT, hypothesis)

**Dosya:** `backend/tests/test_pricing_cache_key_completeness_pbt.py` (yeni)

**İçerik:** Yukarıdaki INV-1..INV-5 invariantları hypothesis property'leri olarak kodlanır. Stratejiler:

- `st.floats(min_value=0, max_value=1_000_000, allow_nan=False, allow_infinity=False)` — kWh alanları için.
- `st.sampled_from(["og", "ag", None])` — voltage_level.
- `st.booleans() | st.none()` — use_template.
- `@settings(max_examples=200, deadline=None)` — yeterli çeşitlilik.
- `@example(t1=25000, t2=12500, t3=12500, ...)` ve `@example(t1=250000, t2=125000, t3=125000, ...)` — B1 baseline'ın deterministik regresyonu.

**Not:** Bu dosya PBT içerdiği için `tasks.md` içinde PBT task marker kullanılacaktır.

### Test 2 — Integration test: cache correctness (FastAPI TestClient)

**Dosya:** `backend/tests/test_pricing_cache_key_completeness_integration.py` (yeni)

**Fixture:**
- In-memory SQLite session (proje standart fixture'ı).
- Seeded `hourly_market_prices` ve `monthly_yekdem_prices` verisi (PTF canonical, steering R26 uyumlu).

**Senaryo:**
- **Request A (LOW profile):** `period=2026-03, customer_id="TEST-CUST", multiplier=1.05, dealer_commission_pct=0.0, imbalance_params=default, use_template=false, t1_kwh=25000, t2_kwh=12500, t3_kwh=12500, voltage_level="og"`.
- **Request B (HIGH profile):** Request A ile identik, sadece `t1_kwh=250000, t2_kwh=125000, t3_kwh=125000`.

**Assert'ler:**

```python
assert resp_A.json()["cache_hit"] is False
assert resp_B.json()["cache_hit"] is False                # B cache miss: farklı key
assert response_hash(resp_A.json()) != response_hash(resp_B.json())
assert resp_A.json()["weighted_prices"]["total_consumption_kwh"] == 50000
assert resp_B.json()["weighted_prices"]["total_consumption_kwh"] == 500000
```

`response_hash` helper: `AnalyzeResponse` dict'ini deterministik JSON'a çevirip SHA256 hesaplar (B1 baseline snapshot eşitlik kontrolüyle paralel).

### Test 3 — Cache version isolation (unit test)

**Dosya:** `backend/tests/test_pricing_cache_key_completeness_pbt.py` içinde ek test case.

**Senaryo:**
1. `build_cache_key(...)` çağrılır, v2 key alınır.
2. `monkeypatch.setattr("app.pricing.pricing_cache.CACHE_KEY_VERSION", "v1")`.
3. Aynı parametrelerle `build_cache_key(...)` tekrar çağrılır → **farklı** key beklenir.

**Assert:** `key_v2 != key_v1_simulated`. Kanıt: version bump eski kayıtları izole ediyor (Decision 1 doğrulaması).

### Test 4 — Cache hit determinism (integration)

**Dosya:** `backend/tests/test_pricing_cache_key_completeness_integration.py` içinde ek test case.

**Senaryo:** Aynı request 2 kez peşpeşe atılır (tüm 12 alan identik).

**Assert'ler:**
```python
assert resp_1.json()["cache_hit"] is False                # miss + write
assert resp_2.json()["cache_hit"] is True                 # hit
assert response_hash(resp_1.json()) == response_hash(resp_2.json())
```

INV-1'in üretim davranışı doğrulaması.

## Decision 5 — Endpoint Scope Lock

Cache kullanımı **YALNIZCA** `/api/pricing/analyze` endpoint'inde.

- `/api/pricing/simulate`, `/api/pricing/compare`, `/api/pricing/report/pdf`, `/api/pricing/report/excel` endpoint'leri cache kullanmaz; her çağrıda yeniden hesaplanır. Bu bilinçli bir tasarım tercihidir (compare/simulate scenario-driven, report stateless). Bu fix bu 4 endpoint'in davranışını değiştirmez.

**Gelecek refactor koruması (opsiyonel, recommended not required):** `build_cache_key` çağrısı projede tek bir yerde (`router.py::analyze`). Grep ile bu garantiye test eklenebilir:

```python
# backend/tests/test_build_cache_key_single_caller.py  (OPSIYONEL)
# Code search: build_cache_key( pattern; exactly 1 prod site + N test sites.
```

Task listesinde bu test "recommended, not required" olarak işaretlenecek.

## Decision 6 — Relationship with PTF SoT Unification (Baseline Drift Note)

Bu bug fix uygulanmadan önce alınan `baselines/2026-05-12_pre-ptf-unification_baseline.json` **geçersizdir**, çünkü cache kontaminasyonu içeriyor (2026-03 LOW ve HIGH profiller aynı response_hash döndürdü; toplam tüketim 50000 vs 500000 olması gerekirken her ikisi de 50000 göründü). Benzer kontaminasyon 2026-01, 2026-02, 2026-04 dönemlerinde de mevcut.

**Aksiyon:** Bu spec kapandıktan (fix merge + test yeşil) sonra `ptf-sot-unification` spec'inin Phase 1 Task 1.1 (baseline pre-migration snapshot) **tekrar çalıştırılmalı** ve yeni baseline commit edilmelidir. Bu bir ön koşul olarak `ptf-sot-unification/tasks.md` T1.1 DoD'sine referans verilir.

## Decision 7 — Risk Classification

**(Yukarıdaki "Risk Classification" section'ında detaylandırıldı — P0 Production Financial Error, fast-track review.)**

## Decision 8 — Blast Radius

**(Yukarıdaki "Scope / Blast Radius" section'ında detaylandırıldı — tek dosya, tek handler çağrısı, tek çağrı noktası, DDL değişmez, alembic migration yok, tek commit revert ile rollback.)**

## Decision 9 — Cache Observability (Response Field)

**Karar:** `AnalyzeResponse` içinde mevcut `cache_hit: bool` alanının yanına yapılandırılmış bir `cache` objesi eklenir. Bu sahada v1/v2 izolasyonunu gözlemlenebilir kılmak için zorunludur — aksi halde prod'da "bu request eski kayıttan mı, yeni kayıttan mı?" sorusunu cevaplayamayız.

**Response şeması eklemesi:**

```python
# AnalyzeResponse içine eklenecek
class CacheInfo(BaseModel):
    hit: bool                              # mevcut cache_hit ile eşit değer
    key_version: str                       # "v2" — canlıda üretilen tek değer
    cached_key_version: Optional[str] = None  # hit ise cache'deki kayıtta yazan version

# AnalyzeResponse:
cache_hit: bool                            # GERİYE UYUMLU — mevcut alan korunur
cache: CacheInfo                           # YENİ alan
```

**Davranış:**

- Cache **miss**: `cache.hit = False`, `cache.key_version = "v2"`, `cache.cached_key_version = None`. Sonuç hesaplanır, v2 key ile yazılır.
- Cache **hit (v2)**: `cache.hit = True`, `cache.key_version = "v2"`, `cache.cached_key_version = "v2"`. Sonuç cache'den döner.
- Cache **hit (v1 legacy)**: **OLMAMASI GEREKEN** durum. v2 request asla v1 kaydına hit veremez (farklı key). Bu durum görülürse bug göstergesidir. Fakat ileride cache_key_version kontaminasyonu olursa `cache.cached_key_version = "v1"` yazılır (read-only teşhis, kabul edilmez durum).

**Implementation notu:** Cache satırına `params_hash` kolonu zaten var (AnalysisCache schema). `set_cached_result` sırasında `params_hash = cache_key` yazılıyor; v2 key'i zaten `"_cache_version": "v2"` içerdiği için prefix kontrolü yapılmaz. Onun yerine `get_cached_result` dönüşünde sonucun yazıldığı key'in başındaki version bilgisini hash öncesi key_data'dan çıkarmak mümkün değil (SHA256 tek yönlü). Bu yüzden **ek kolon yazılmaz**; mevcut cache hit'leri tanım gereği v2 (çünkü fix sonrası oluşturulan kayıtlar v2 key'e sahip). Eski v1 kayıtları yeni request'lere asla match olmadığı için `cached_key_version` her zaman `"v2"` döner.

**Sadeleştirilmiş davranış:** `cached_key_version` alanı sadece "v2" döneceği için BASİTLEŞTİRME — alan eklenir ama her zaman `"v2"` olur. Gelecekte v3 bump edilirse hem v2 hem v3 kayıtlarının ayrımı için koruma sağlar (fakat v2→v3 bump sırasında aynı izolasyon pattern'i tekrar uygulanır).

**Requirements etkisi:** yeni requirement 2.8 (cache observability field) eklenir — bu karar bugfix requirements'a doğrudan bağlanır.

**Frontend etkisi (dar):** FE `cache_hit` alanını okumaya devam edebilir (geriye uyumluluk). `cache` objesi opsiyonel consumer'lar için (admin debug panel, loglar). FE task **BU SPEC KAPSAMINDA DEĞİL** — FE yalnızca `cache_hit` kullanıyorsa hiçbir şey değişmez.

## Decision 10 — voltage_level Canonical Normalization

**Karar:** Key seviyesinde `voltage_level` için `None` canonical değere normalize edilir: `voltage_level or "og"`.

**Gerekçe:**

- `AnalyzeRequest.voltage_level` default handler seviyesinde `"og"` olarak işler (ve bu pratik olarak her zaman bir değer döner). Ancak request literally `voltage_level: null` gönderilirse Pydantic model `None` tutar ve handler kendi default'unu uygulamadan `req.voltage_level` key'e None olarak girer.
- Eğer key'de `None` ayrı bir değer olarak kalırsa, **aynı gerçek request** (her ikisi de "og" dağıtım bedeli ile hesaplanacak) **farklı cache key** üretir → cache fragmentation. Yani:
  - Request A: `voltage_level=null` → hesap sırasında "og" uygulanır → response X
  - Request B: `voltage_level="og"` → hesap sırasında "og" uygulanır → response X (aynı)
  - İki request aynı sonucu üretir ama farklı cache key'de saklanır → 2 kere hesaplanır, hit oranı düşer.
- Bu bir **soft bug** (correctness değil, efficiency). Yine de düzeltilmesi tercih edilir çünkü `use_template` gibi semantik farkı olan bir alan değil; `voltage_level`'ın None ve `"og"` anlamı identiktir.

**Uygulama:** key_data içinde:

```python
"voltage_level": voltage_level or "og",
```

**use_template ile karşılaştırma (neden farklı davranış?):**

| Alan | None → ? | Gerekçe |
|---|---|---|
| `voltage_level` | `None` → `"og"` (normalize) | Handler her zaman "og" uygular; semantic fark yok |
| `use_template` | `None` korunur | `None` = validate edilmemiş, `False` = T1/T2/T3 zorunlu; **farklı validation path** |

**PBT impact:** INV-3 yeniden formüle edildi (Decision 3). `None` ve `"og"` **aynı** key üretmeli (counter-property); sadece canonical domain `{"og", "ag"}` arasında ayırt edicidir.

## Decision 11 — Float Precision Specification

**Karar:** `round(value, n)` açık ondalık haneyle kullanılır. Alan-alan precision tablosu:

| Alan | Precision (ondalık) | Gerekçe |
|---|---|---|
| `multiplier` | 6 hane | Mevcut kod 6 hane kullanıyor; 1 milyonda 1 hassasiyet |
| `dealer_commission_pct` | 2 hane | Yüzde değeri, iki haneli ondalık gerçek-dünya granüleritesi |
| `imbalance.forecast_error_rate` | 4 hane | 0.0001 hassasiyet (mevcut) |
| `imbalance.imbalance_cost_tl_per_mwh` | 2 hane | TL para birimi, kuruş hassasiyeti (mevcut) |
| `template_monthly_kwh` | 2 hane | kWh genelde tam sayı, 2 hane tampon (mevcut) |
| `t1_kwh`, `t2_kwh`, `t3_kwh` | 2 hane | kWh pratikte tam sayı; yuvarlama tutarlılığı için mevcut pattern tekrar |

**Neden `round(x, n)` ve `f"{x:.{n}f}"` değil?**

- `round(x, 2)` Python'da **banker's rounding** (IEEE 754) uygular — deterministik, `round(0.5) == 0`, `round(1.5) == 2`.
- `f"{x:.2f}"` string formatlaması eşdeğer sonuç verir (`"1.25"`) ama `json.dumps` sırasında string olarak serialize olur — float ile aynı tip değil. Key hash'inin tutarlı olması için hem signature hem dict'te aynı tip (float) korunmalı.
- Mevcut kod `round()` kullanıyor; **uyumluluk için aynı pattern korunur**. Yeni alanlar da `round()` kullanır.

**Edge case:** `round(1.005, 2)` Python'da `1.0` döner (floating-point representation). Bu bilinçli bir tercih: gerçek-dünyada bu precision'da input bekleniyor; input'ları kullanıcı tarafı zaten 2 ondalıkta hazırlıyor.

**PBT katsayı:** Hypothesis stratejileri 2 ondalık precision'da değer üretir:

```python
@st.composite
def kwh_strategy(draw):
    return draw(st.floats(min_value=0, max_value=1_000_000, allow_nan=False, allow_infinity=False))
# INV-1 idempotent property: aynı girdi round ile aynı normalized değere düşer
```

**Determinism invariant:** `build_cache_key(multiplier=1.100000) == build_cache_key(multiplier=1.1)`. PBT test 1A bu eşitliği doğrular.

## Fix Implementation

### Files Modified

1. **`backend/app/pricing/pricing_cache.py`**
   - Modül seviyesinde `CACHE_KEY_VERSION = "v2"` sabiti eklenir (dosyanın tepesinde, `PRICING_CACHE_TTL_HOURS` sabitinin hemen altında).
   - `build_cache_key()` signature'ına 5 yeni parametre eklenir (default `None` / `None` / `None` / `None` / `None`):
     ```python
     def build_cache_key(
         customer_id: Optional[str],
         period: str,
         multiplier: float,
         dealer_commission_pct: float,
         imbalance_params: dict,
         template_name: Optional[str] = None,
         template_monthly_kwh: Optional[float] = None,
         t1_kwh: Optional[float] = None,
         t2_kwh: Optional[float] = None,
         t3_kwh: Optional[float] = None,
         use_template: Optional[bool] = None,
         voltage_level: Optional[str] = None,
     ) -> str: ...
     ```
   - `key_data` dict'i Decision 2'deki yapıya genişletilir (float normalization + voltage_level canonical).
   - Docstring güncellenir: yeni 5 parametre açıklanır; "Cache key bileşenleri (eksiksiz)" listesi 12 alana çıkarılır; voltage_level normalization ve use_template None koruması notlanır; Requirements tag'leri `21.1, 21.2, 21.3, 21.4` satırına ek olarak `pricing-cache-key-completeness/2.1-2.9, 3.1-3.8` referansı eklenir.

2. **`backend/app/pricing/router.py`** (satır 457-466 civarı, `analyze` handler)
   - `build_cache_key(...)` çağrısına 5 yeni kwarg geçirilir:
     ```python
     cache_key = build_cache_key(
         customer_id=req.customer_id,
         period=period,
         multiplier=req.multiplier,
         dealer_commission_pct=req.dealer_commission_pct,
         imbalance_params=imbalance_dict,
         template_name=req.template_name,
         template_monthly_kwh=req.template_monthly_kwh,
         t1_kwh=req.t1_kwh,
         t2_kwh=req.t2_kwh,
         t3_kwh=req.t3_kwh,
         use_template=req.use_template,
         voltage_level=req.voltage_level,
     )
     ```
   - Response oluşturulurken `cache` alanı populate edilir (Decision 9):
     ```python
     # Cache miss yolu:
     response.cache = CacheInfo(hit=False, key_version=CACHE_KEY_VERSION, cached_key_version=None)
     # Cache hit yolu:
     response.cache = CacheInfo(hit=True, key_version=CACHE_KEY_VERSION, cached_key_version=CACHE_KEY_VERSION)
     ```
   - Mevcut `cache_hit` bool alanı korunur (geriye uyumluluk).

3. **`backend/app/pricing/models.py::AnalyzeResponse`**
   - Yeni `CacheInfo` Pydantic modeli eklenir (Decision 9 şeması).
   - `AnalyzeResponse` içine `cache: CacheInfo` alanı eklenir.
   - Mevcut `cache_hit: bool` alanı korunur (deprecated notunu `Field(..., description="Deprecated in favor of cache.hit; kept for backward compatibility")` ile işaretle — ama silinmez).

### Files Created

- **`backend/tests/test_pricing_cache_key_completeness_pbt.py`** — INV-1..INV-5 hypothesis property'leri + cache version isolation testi + voltage_level normalization counter-property (Decision 3, 10; Test 1 + Test 3).
- **`backend/tests/test_pricing_cache_key_completeness_integration.py`** — LOW vs HIGH senaryo + cache hit determinism + `cache` response objesi assertion (Decision 4, 9; Test 2 + Test 4).

### Files NOT Modified

- `backend/app/pricing/models.py::AnalyzeRequest` — alanlar zaten mevcut; yeni alan eklenmez.
- `backend/app/pricing/schemas.py::AnalysisCache` — DDL dokunulmaz; alembic migration yok.
- Diğer pricing endpoint'leri (`simulate`, `compare`, `report/pdf`, `report/excel`) — cache kullanmıyorlar.
- `backend/tests/test_pricing_cache.py::TestBuildCacheKey` — mevcut testler değişmez; INV-5 koşulu altında PASS kalır (default None parametreler mevcut çağrı şeklini bozmaz).

### Implementation Sequence

1. `pricing_cache.py` tepesine `CACHE_KEY_VERSION = "v2"` sabitini ekle.
2. `build_cache_key` signature'ına 5 yeni parametre ekle (default `None`).
3. `key_data` dict'ini Decision 2'deki yapıya genişlet (float normalization + voltage_level canonical `or "og"`).
4. `models.py` içine `CacheInfo` Pydantic modelini ekle ve `AnalyzeResponse` içine `cache: CacheInfo` alanını ekle (Decision 9).
5. `router.py::analyze` içinde `build_cache_key(...)` çağrısına 5 yeni kwarg geçir (`req.t1_kwh, req.t2_kwh, req.t3_kwh, req.use_template, req.voltage_level`).
6. Aynı handler'da response populate sırasında `response.cache = CacheInfo(...)` hem miss hem hit yolunda set et.
7. `backend/tests/test_pricing_cache_key_completeness_pbt.py` dosyasını oluştur (INV-1..INV-5 + cache version isolation + voltage_level normalize counter-property).
8. `backend/tests/test_pricing_cache_key_completeness_integration.py` dosyasını oluştur (B1 baseline'ın LOW vs HIGH replay'i + hit determinism + cache objesi assertions).
9. `pytest backend/tests/test_pricing_cache.py backend/tests/test_pricing_cache_key_completeness_*.py -v` çalıştır; hepsi PASS olmalı.
10. PR açıklamasına: "fixes pricing-cache-key-completeness; P0 financial error; cache version bump v1→v2; voltage_level canonical normalization; cache observability (response.cache); eski satırlar TTL ile temizlenir; baseline pre-ptf-unification geçersiz (ptf-sot-unification T1.1 re-run gerekli)" notu ekle.

## Testing Strategy

### Validation Approach

İki fazlı doğrulama:

1. **Exploratory phase (unfixed code üzerinde):** Yeni testler (`_pbt.py` + `_integration.py`) önce unfixed `build_cache_key` ve unfixed `router.py` üzerinde çalıştırılır. INV-2/3/4 property'leri ve LOW vs HIGH integration testi **FAIL** etmelidir. Bu, root cause analizinin (ve bug condition formalization'ının) doğruluğunu ampirik olarak onaylar.
2. **Validation phase (fixed code üzerinde):** Fix uygulanır, tüm testler (yeni + mevcut) **PASS** olmalıdır. Mevcut `TestBuildCacheKey` suite'i (INV-5 koruması) dahil.

### Exploratory Bug Condition Checking

**Goal:** Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If refuted, re-hypothesize.

**Test Plan:** Yeni PBT dosyasını `main` HEAD (unfixed) üzerinde çalıştır. Hypothesis'in bug condition için counterexample üretmesi beklenir (INV-2/3/4). Integration testinde LOW vs HIGH senaryosu collision producecek (resp_B cache_hit=True gelecek, total_consumption_kwh 50000 olacak — bug kanıtı).

**Test Cases:**
1. **t1/t2/t3 collision test** (INV-2 unfixed): `build_cache_key(..., t1=25000, t2=12500, t3=12500, ...) == build_cache_key(..., t1=250000, t2=125000, t3=125000, ...)` — FAIL EXPECTED on unfixed code (key'ler eşit gelecek, property ihlal).
2. **voltage_level collision test** (INV-3 unfixed): `og` vs `ag` aynı key — FAIL EXPECTED.
3. **use_template collision test** (INV-4 unfixed): `True` vs `False` aynı key — FAIL EXPECTED.
4. **LOW vs HIGH integration** (Test 2 unfixed): `resp_B.cache_hit is False` assertion fail; `total_consumption_kwh == 500000` assertion fail — FAIL EXPECTED.

**Expected Counterexamples:**
- Herhangi `(t1,t2,t3)` çiftinde farklı tuple → aynı SHA256 → INV-2 violation.
- Possible causes: `key_data` dict'te 5 alan eksik (root cause kesin).

### Fix Checking

**Goal:** Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior (farklı key, cache miss).

**Pseudocode:**

```
FOR ALL (reqA, reqB) WHERE isBugCondition(reqA, reqB) DO
  keyA := build_cache_key_fixed(reqA)
  keyB := build_cache_key_fixed(reqB)
  ASSERT keyA != keyB                              // Property 1
  ASSERT isSha256HexString(keyA) AND isSha256HexString(keyB)
END FOR
```

PBT ile: `@given(...)` 200 random örnek üretir, her biri için INV-2/3/4 property'si doğrulanır. `@example(...)` ile B1 baseline vakaları deterministik olarak kapsanır.

### Preservation Checking

**Goal:** Verify that for all inputs where the bug condition does NOT hold (yani 12 alan tamamen identik), the fixed function produces the same key as itself (determinism) AND that existing core-7 discrimination behavior is preserved.

**Pseudocode:**

```
// Determinism (INV-1)
FOR ALL req WHERE validInput(req) DO
  ASSERT build_cache_key_fixed(req) == build_cache_key_fixed(req)
END FOR

// Core-7 discrimination preservation (INV-5)
FOR ALL (reqA, reqB) WHERE
    validInput(reqA) AND validInput(reqB)
    AND differsInExactlyOneCoreField(reqA, reqB)
    AND identicalInAllNewFiveFields(reqA, reqB)
DO
  ASSERT build_cache_key_fixed(reqA) != build_cache_key_fixed(reqB)
END FOR
```

**Testing Approach:** Property-based testing is recommended for preservation checking because:
- Rastgele 12-alan kombinasyonu üretip hem determinism hem core-7 ayırt ediciliği doğrulanır.
- Edge case'ler (None değerler, float hassasiyeti, 0 değerler) otomatik sondalanır.
- Mevcut unit testlerin (`TestBuildCacheKey`) kapsamıyla çakışmaz; PBT daha geniş domain tarar.

**Test Plan:** Observe behavior on UNFIXED code first — mevcut `TestBuildCacheKey` zaten PASS (INV-5 unfixed'de de geçerli). Yeni PBT `INV-1` ve `INV-5` property'lerini unfixed code'da da PASS etmeli. Fix sonrası aynen PASS kalmalı.

**Test Cases:**
1. **INV-1 Determinism (unfixed PASS, fixed PASS):** Aynı 12-alan tuple iki kez çağır → aynı key.
2. **INV-5 Core-7 Discrimination (unfixed PASS, fixed PASS):** Core 7 alandan birini değiştir → farklı key.
3. **`TestBuildCacheKey` regression suite (unfixed PASS, fixed PASS):** Mevcut `test_deterministic`, `test_multiplier_change_differs`, `test_customer_change_differs`, `test_period_change_differs`, `test_dealer_commission_change_differs`, `test_template_vs_real_differs`, `test_sha256_format` testleri fix sonrası aynen PASS.
4. **Cache version isolation (Test 3):** `CACHE_KEY_VERSION="v1"` monkeypatch ile farklı key üretilmesi — v2 ↔ v1 izolasyonunun ampirik kanıtı.

### Unit Tests

- `test_pricing_cache_key_completeness_pbt.py::TestInv1Determinism` — INV-1 property.
- `test_pricing_cache_key_completeness_pbt.py::TestInv2T1T2T3Discriminator` — INV-2 property.
- `test_pricing_cache_key_completeness_pbt.py::TestInv3VoltageLevelDiscriminator` — INV-3 property.
- `test_pricing_cache_key_completeness_pbt.py::TestInv4UseTemplateDiscriminator` — INV-4 property.
- `test_pricing_cache_key_completeness_pbt.py::TestInv5Core7Regression` — INV-5 property.
- `test_pricing_cache_key_completeness_pbt.py::test_cache_version_isolation` — Decision 1 ampirik doğrulaması.

### Property-Based Tests

- Hypothesis `@given` stratejileri ile 12-alan random girdiler (5 yeni alan + 7 core alan) üretip yukarıdaki 5 invariant'ı doğrular.
- `@example(...)` decorator'ü ile B1 baseline'ın LOW vs HIGH tuple'ları deterministik olarak kapsanır.
- `@settings(max_examples=200, deadline=None)` — yeterli çeşitlilik; `deadline=None` çünkü SHA256 hesaplama çok hızlı ama hypothesis'in tune süresi değişken olabiliyor.

### Integration Tests

- **LOW vs HIGH different response** (Test 2): Gerçek FastAPI TestClient ile `/api/pricing/analyze` iki kez çağrılır, key farkını ve response farkını doğrular. B1 baseline collision'ının regresyon kapısı.
- **Cache hit determinism** (Test 4): Aynı request 2 kez → 2. çağrı cache hit, response identik. INV-1'in end-to-end doğrulaması.
- (Opsiyonel) **Single-caller grep test**: `build_cache_key(` patterninin prod kodda yalnızca `router.py::analyze` içinde çağrıldığını doğrular (Decision 5 koruması). Recommended, not required.
