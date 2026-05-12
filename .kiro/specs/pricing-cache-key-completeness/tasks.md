# Tasks — Pricing Cache Key Completeness (Bugfix)

## Yaklaşım

4 phase, atomik commit prensibi, Decision ↔ Task 1:1 izlenebilirlik. **Phase sırası kesindir** — bir phase DoD'si karşılanmadan sonraki phase başlamaz. Phase 3 testleri Phase 4 cleanup'tan önce çalışır (testleri sona bırakma yasağı).

**Kilit kararlar (tartışma kapalı):**
- P0 production financial error — hot-fix fast-track PR (tek commit revert ile rollback)
- Cache version bump `v1 → v2` (TRUNCATE YOK, eski kayıtlar TTL ile temizlenir)
- Cache sadece `/api/pricing/analyze`'ta; diğer 4 endpoint cache kullanmıyor (scope lock)
- Bu spec kapanmadan `ptf-sot-unification` Phase 1 T1.1 rerun olamaz (baseline invalid)

**Yasaklar:**
- ❌ Task'ları birleştirme (her task tek Decision'a bağlı)
- ❌ Testleri sona bırakma
- ❌ Cache fix + PTF migration aynı PR

---

## 🔴 Phase 1 — Core Fix (bloklayıcı)

### [ ] T1 — Cache version bump (Decision 1)
- **Dosya:** `backend/app/pricing/pricing_cache.py`
- **Giriş:** modül seviyesinde sabit yok; `build_cache_key.key_data` version bilgisi içermiyor
- **Çıktı:**
  - `CACHE_KEY_VERSION = "v2"` modül sabiti eklenir (`PRICING_CACHE_TTL_HOURS`'un hemen altında)
  - `key_data` dict'inin **ilk** alanı olarak `"_cache_version": CACHE_KEY_VERSION` eklenir (underscore prefix alfabetik sıralamada başa düşer, sorted_keys altında determinism)
- **Kanıt (DoD):**
  - `grep -n "CACHE_KEY_VERSION" backend/app/pricing/pricing_cache.py` → sabit görünür
  - Unit test: mevcut 7 alan ile çağrılan `build_cache_key(...)` fix öncesi key'den **farklı** SHA256 döndürür (version bump etkisi ampirik)
  - `import build_cache_key` çalışır, signature hatası yok
- **Requirement ref:** 2.6 (cache satırlarının izolasyonu), 3.2 (SHA256 format korunur)
- **Rollback:** tek commit revert — v1 key'e geri döner, cache collision davranışı da geri gelir

### [ ] T2 — Cache key alanlarını tamamla (Decision 2)
- **Dosya:** `backend/app/pricing/pricing_cache.py`
- **Giriş:** T1 sonrası `build_cache_key` 7 parametre, 8 key_data alanı (`_cache_version` dahil)
- **Çıktı:**
  - `build_cache_key()` signature'a 5 yeni parametre (hepsi `default=None`):
    - `t1_kwh: Optional[float] = None`
    - `t2_kwh: Optional[float] = None`
    - `t3_kwh: Optional[float] = None`
    - `use_template: Optional[bool] = None`
    - `voltage_level: Optional[str] = None`
  - `key_data` dict'ine 5 yeni alan (round() normalize ile, T4'te precision sabitlenecek):
    - `"t1_kwh"`, `"t2_kwh"`, `"t3_kwh"` — `round(x, 2) if x is not None else None`
    - `"use_template"` — `bool(x) if x is not None else None` (None korunur)
    - `"voltage_level"` — T3'te `or "og"` eklenecek
  - Docstring güncellenir — 12 alan listesi + Requirements tag
- **Kanıt (DoD):**
  - `build_cache_key(..., t1_kwh=25000, t2_kwh=12500, t3_kwh=12500, ...)` ≠ `build_cache_key(..., t1_kwh=250000, t2_kwh=125000, t3_kwh=125000, ...)` (diğer alanlar sabit)
  - `build_cache_key(..., use_template=True, ...)` ≠ `build_cache_key(..., use_template=False, ...)`
  - `build_cache_key(..., use_template=None, ...)` ≠ `build_cache_key(..., use_template=False, ...)` (None korunur)
- **Requirement ref:** 2.1, 2.2, 2.4
- **Rollback:** tek commit revert — signature eski haline döner; T3/T4 bu task'a bağlı olduğu için onlar da revert olur

### [ ] T3 — voltage_level canonical normalize (Decision 10)
- **Dosya:** `backend/app/pricing/pricing_cache.py`
- **Giriş:** T2 sonrası key_data'da `"voltage_level": voltage_level` naif yazım
- **Çıktı:**
  - `key_data["voltage_level"] = voltage_level or "og"` — None ve empty string canonical `"og"`'a düşer
- **Kanıt (DoD):**
  - Unit: `build_cache_key(..., voltage_level=None, ...) == build_cache_key(..., voltage_level="og", ...)` (aynı key — counter-property)
  - Unit: `build_cache_key(..., voltage_level="og", ...) != build_cache_key(..., voltage_level="ag", ...)` (farklı domain value farklı key)
- **Requirement ref:** 2.9
- **Rollback:** tek satır — `voltage_level or "og"` → `voltage_level`; mantık etkilenmez ama INV-3 counter-property kaybolur

### [ ] T4 — Float precision normalization (Decision 11)
- **Dosya:** `backend/app/pricing/pricing_cache.py`
- **Giriş:** T2 sonrası `round(x, 2)` sabit; design'da alan-alan precision tablosu var
- **Çıktı:** Design §11 tablosuna göre key_data float alanları:
  - `multiplier`: `round(x, 6)` (mevcut, değişmez)
  - `dealer_commission_pct`: `round(x, 2)` (mevcut, değişmez)
  - `imbalance.forecast_error_rate`: `round(x, 4)` (mevcut)
  - `imbalance.imbalance_cost_tl_per_mwh`: `round(x, 2)` (mevcut)
  - `template_monthly_kwh`: `round(x, 2) if not None else None` (mevcut)
  - **YENİ:** `t1_kwh`, `t2_kwh`, `t3_kwh`: `round(x, 2) if not None else None`
- **Kanıt (DoD):**
  - Unit: `build_cache_key(..., multiplier=1.100000) == build_cache_key(..., multiplier=1.1)` (aynı key — floating-point representation tolere edilir)
  - Unit: `build_cache_key(..., t1_kwh=25000.00) == build_cache_key(..., t1_kwh=25000.0000001)` (precision 2 hane içinde aynı key)
- **Requirement ref:** 2.4 (determinism), 3.3 (mevcut precision pattern korunur)
- **Rollback:** tek commit revert — precision tablosu eski haline döner

**Phase 1 DoD:**
- [ ] T1-T4 merge edildi
- [ ] `pricing_cache.py::build_cache_key` 12 parametre + version prefix + canonical normalize + precision pattern
- [ ] Manual probe: `python -c "from app.pricing.pricing_cache import build_cache_key; print(build_cache_key(None, '2026-03', 1.1, 0, {}, t1_kwh=25000, t2_kwh=12500, t3_kwh=12500, use_template=False, voltage_level='og'))"` çalışır, SHA256 döner

---

## 🟠 Phase 2 — Response Contract

### [ ] T5 — `CacheInfo` Pydantic model (Decision 9)
- **Dosya:** `backend/app/pricing/models.py`
- **Giriş:** `AnalyzeResponse`'ta sadece `cache_hit: bool` alanı var
- **Çıktı:** `AnalyzeResponse` sınıfının üstüne yeni Pydantic BaseModel:
  ```python
  class CacheInfo(BaseModel):
      """Cache observability — key version visibility for production rollout monitoring."""
      hit: bool = Field(..., description="Cache hit flag (mirror of legacy cache_hit)")
      key_version: str = Field(..., description="Active cache key version at request time (canlıda 'v2')")
      cached_key_version: Optional[str] = Field(default=None, description="Cache kaydının key version'u (hit durumunda)")
  ```
- **Kanıt (DoD):**
  - `from app.pricing.models import CacheInfo` import başarılı
  - `CacheInfo(hit=False, key_version="v2").model_dump()` → 3 alanlı dict
  - Pydantic validation: `hit` ve `key_version` zorunlu
- **Requirement ref:** 2.8 (yapılandırılmış cache objesi)
- **Rollback:** class silinir, tek commit revert

### [ ] T6 — `AnalyzeResponse` genişletme + backward compat (Decision 9)
- **Dosya:** `backend/app/pricing/models.py`
- **Giriş:** T5 sonrası `CacheInfo` tanımlı ama `AnalyzeResponse`'a bağlı değil
- **Çıktı:**
  - `AnalyzeResponse` içine `cache: CacheInfo` alanı eklenir (Field description: "Cache observability — structured replacement for cache_hit")
  - **Mevcut `cache_hit: bool` alanı korunur** — silinmez, deprecated olarak işaretlenmez (prod consumer'ları koruma)
  - Response schema dokümantasyonunda `cache.hit == cache_hit` invariant'ı not olarak düşülür
- **Kanıt (DoD):**
  - `AnalyzeResponse(...)` construct ederken `cache` zorunlu (Pydantic validation)
  - `cache_hit` hâlâ mevcut (geriye uyumluluk testi: `assert "cache_hit" in AnalyzeResponse.model_fields`)
  - Mevcut response serialization `cache_hit` key'i korunur
- **Requirement ref:** 2.8 (yeni alan), 3.8 (cache_hit preservation)
- **Rollback:** `cache` alanı silinir, tek commit revert

### [ ] T7 — Handler cache populate (Decision 9)
- **Dosya:** `backend/app/pricing/router.py` (analyze handler, satır 456-720 civarı)
- **Giriş:** T1-T6 sonrası `build_cache_key` 12 parametre, `AnalyzeResponse.cache` zorunlu
- **Çıktı:**
  - `build_cache_key(...)` çağrısı 5 yeni kwarg ile genişletilir: `t1_kwh=req.t1_kwh, t2_kwh=req.t2_kwh, t3_kwh=req.t3_kwh, use_template=req.use_template, voltage_level=req.voltage_level`
  - Import: `from .pricing_cache import build_cache_key, get_cached_result, set_cached_result, CACHE_KEY_VERSION`
  - Import: `from .models import CacheInfo` (zaten AnalyzeResponse import'unda ise tekrar eklemez)
  - Cache **miss** yolunda response construct sırasında:
    ```python
    response.cache = CacheInfo(
        hit=False,
        key_version=CACHE_KEY_VERSION,
        cached_key_version=None,
    )
    ```
  - Cache **hit** yolunda:
    ```python
    cached["cache"] = CacheInfo(
        hit=True,
        key_version=CACHE_KEY_VERSION,
        cached_key_version=CACHE_KEY_VERSION,  # v2 fix sonrası tek değer
    ).model_dump()
    cached["cache_hit"] = True  # mevcut alan korunur
    ```
- **Kanıt (DoD):**
  - Manual probe: canlı backend'e `POST /api/pricing/analyze` → response JSON'da `cache` objesi var, `cache.key_version == "v2"`
  - Cache miss: `cache.hit == false`, `cache.cached_key_version == null`
  - Cache hit: `cache.hit == true`, `cache.cached_key_version == "v2"`
  - `cache_hit` mevcut alan eski davranışla aynı (hit'te `true`, miss'te `false`)
- **Requirement ref:** 2.8, 3.8, 3.1 (cache hit davranışı korunur)
- **Rollback:** 4 satır — `build_cache_key` kwarg'ları kaldır, `cache` populate sil; cache_hit davranışı etkilenmez

**Phase 2 DoD:**
- [ ] T5-T7 merge edildi
- [ ] Response JSON'da `cache` objesi görünür (3 alanlı)
- [ ] `cache_hit` mevcut alan korunur ve `cache.hit` ile eşit değer
- [ ] Integration testleri Phase 3'te bu contract'ı doğrulayacak

---

## 🟡 Phase 3 — Tests (zorunlu, sona bırakma)

### [ ] T8 — PBT test dosyası oluştur (PBT)
- **Dosya:** `backend/tests/test_pricing_cache_key_completeness_pbt.py` (yeni)
- **Çıktı:**
  - Docstring: "PBT suite for pricing cache key completeness fix (Decision 3 invariants)"
  - Hypothesis stratejileri (module-level):
    - `kwh_strategy = st.floats(min_value=0, max_value=1_000_000, allow_nan=False, allow_infinity=False)`
    - `voltage_strategy = st.sampled_from(["og", "ag"])`  # canonical domain
    - `use_template_strategy = st.booleans() | st.none()`
    - `period_strategy = st.sampled_from(["2026-01", "2026-02", "2026-03", "2026-04"])`
  - Helper: `base_kwargs()` fixture — core 7 alan için sabit değerler (period=2026-03, customer_id=None, multiplier=1.1, vb.)
- **Kanıt (DoD):** dosya import edilebilir, `pytest backend/tests/test_pricing_cache_key_completeness_pbt.py --collect-only` 0+ test gösterir
- **Requirement ref:** Decision 3 invariant seti
- **Rollback:** dosya silinir

### [ ] T9 — INV-1 Determinism test (PBT)
- **Dosya:** `backend/tests/test_pricing_cache_key_completeness_pbt.py`
- **Giriş:** T8 dosyası
- **Çıktı:** Test `TestInv1Determinism::test_same_input_same_key`:
  ```python
  @given(t1=kwh_strategy, t2=kwh_strategy, t3=kwh_strategy, use_tpl=use_template_strategy, vlt=voltage_strategy)
  @settings(max_examples=200, deadline=None)
  def test_same_input_same_key(t1, t2, t3, use_tpl, vlt):
      kwargs = {**base_kwargs(), "t1_kwh": t1, "t2_kwh": t2, "t3_kwh": t3, "use_template": use_tpl, "voltage_level": vlt}
      assert build_cache_key(**kwargs) == build_cache_key(**kwargs)
  ```
- **Kanıt (DoD):** `pytest -k test_same_input_same_key -v` PASS
- **Requirement ref:** 2.4
- **Rollback:** test fonksiyonu silinir

### [ ] T10 — INV-2 t1/t2/t3 discriminator test (PBT)
- **Dosya:** `backend/tests/test_pricing_cache_key_completeness_pbt.py`
- **Çıktı:** Test `TestInv2T1T2T3Discriminator::test_different_consumption_different_key`:
  ```python
  @given(low_t1=kwh_strategy, low_t2=kwh_strategy, low_t3=kwh_strategy,
         high_delta=st.floats(min_value=0.01, max_value=1000))
  @settings(max_examples=200, deadline=None)
  @example(low_t1=25000, low_t2=12500, low_t3=12500, high_delta=225000)  # B1 baseline
  def test_different_consumption_different_key(low_t1, low_t2, low_t3, high_delta):
      low_kwargs = {**base_kwargs(), "t1_kwh": low_t1, "t2_kwh": low_t2, "t3_kwh": low_t3}
      high_kwargs = {**base_kwargs(), "t1_kwh": low_t1 + high_delta, "t2_kwh": low_t2, "t3_kwh": low_t3}
      assert build_cache_key(**low_kwargs) != build_cache_key(**high_kwargs)
  ```
- **Kanıt (DoD):** `pytest -k test_different_consumption_different_key -v` PASS, B1 baseline example'ı deterministik kapsanır
- **Requirement ref:** 2.1
- **Rollback:** test fonksiyonu silinir

### [ ] T11 — INV-3 voltage_level test (PBT)
- **Dosya:** `backend/tests/test_pricing_cache_key_completeness_pbt.py`
- **Çıktı:** İki test case tek sınıf altında:
  - `TestInv3VoltageLevel::test_og_and_ag_different_key` — `voltage_level="og"` ≠ `voltage_level="ag"` (positive)
  - `TestInv3VoltageLevel::test_none_equals_og_counter_property` — `voltage_level=None == voltage_level="og"` (counter-property, Decision 10)
  - `TestInv3VoltageLevel::test_empty_string_equals_og` — `voltage_level="" == voltage_level="og"` (falsy normalize)
- **Kanıt (DoD):** 3 test PASS, counter-property ayrı işaretli
- **Requirement ref:** 2.9
- **Rollback:** test sınıfı silinir

### [ ] T12 — INV-4 use_template test (PBT)
- **Dosya:** `backend/tests/test_pricing_cache_key_completeness_pbt.py`
- **Çıktı:** Test sınıfı `TestInv4UseTemplate`:
  - `test_true_vs_false_different_key` — `use_template=True` ≠ `use_template=False`
  - `test_none_vs_false_different_key` — `use_template=None` ≠ `use_template=False` (None korunur, Decision 2)
  - `test_none_vs_true_different_key` — `use_template=None` ≠ `use_template=True`
- **Kanıt (DoD):** 3 test PASS, 3 domain value ikişer ikişer ayırt edici
- **Requirement ref:** 2.2
- **Rollback:** test sınıfı silinir

### [ ] T13 — INV-5 core-7 regression (PBT)
- **Dosya:** `backend/tests/test_pricing_cache_key_completeness_pbt.py`
- **Çıktı:** Test sınıfı `TestInv5Core7Regression`:
  - `@pytest.mark.parametrize` ile 7 alanın her biri için "değişirse farklı key" test case
  - Örnek parametrize entries:
    - `("customer_id", "A", "B")`
    - `("period", "2026-01", "2026-02")`
    - `("multiplier", 1.1, 1.2)`
    - `("dealer_commission_pct", 0.0, 5.0)`
    - `("template_name", "3_vardiya_sanayi", "ticari_buro")`
    - `("template_monthly_kwh", 50000.0, 100000.0)`
    - `("imbalance_params", {"forecast_error_rate": 0.05, ...}, {"forecast_error_rate": 0.10, ...})`
- **Kanıt (DoD):** 7 parametrize case PASS — core-7 ayırt ediciliği korunur (fix öncesi de PASS olması gerekir, regression koruması)
- **Requirement ref:** 3.3
- **Rollback:** test sınıfı silinir

### [ ] T14 — Cache version isolation test
- **Dosya:** `backend/tests/test_pricing_cache_key_completeness_pbt.py`
- **Çıktı:** Test `TestCacheVersionIsolation::test_v1_v2_produce_different_keys`:
  ```python
  def test_v1_v2_produce_different_keys(monkeypatch):
      kwargs = {**base_kwargs(), "t1_kwh": 25000, "t2_kwh": 12500, "t3_kwh": 12500, ...}
      key_v2 = build_cache_key(**kwargs)
      monkeypatch.setattr("app.pricing.pricing_cache.CACHE_KEY_VERSION", "v1")
      key_v1 = build_cache_key(**kwargs)
      assert key_v1 != key_v2
      # ampirik kanıt: version bump eski kayıtları izole ediyor
  ```
- **Kanıt (DoD):** PASS; version bump Decision 1'in davranışsal doğrulaması
- **Requirement ref:** 2.6
- **Rollback:** test fonksiyonu silinir

### [ ] T15 — Integration test dosyası oluştur
- **Dosya:** `backend/tests/test_pricing_cache_key_completeness_integration.py` (yeni)
- **Çıktı:**
  - Docstring: "Integration suite for cache key completeness fix — B1 baseline regression replay + cache observability"
  - FastAPI TestClient fixture
  - In-memory SQLite DB fixture (proje standartı)
  - Seeded market data: `hourly_market_prices` + `monthly_yekdem_prices` (2026-03 için minimum)
  - Helper: `response_hash(resp_json)` — sanitize + SHA256 (B1 baseline script'iyle paralel)
- **Kanıt (DoD):** dosya import edilebilir, `pytest --collect-only` 0+ test
- **Requirement ref:** 2.1, 2.7
- **Rollback:** dosya silinir

### [ ] T16 — LOW vs HIGH integration (Decision 4, Test 2)
- **Dosya:** `backend/tests/test_pricing_cache_key_completeness_integration.py`
- **Çıktı:** Test `TestLowVsHighProfile::test_different_consumption_different_response`:
  ```python
  def test_different_consumption_different_response(client, seeded_db):
      base = {"period": "2026-03", "customer_id": "TEST-CUST", "multiplier": 1.05,
              "dealer_commission_pct": 0.0, "imbalance_params": DEFAULT_IMBALANCE,
              "use_template": False, "voltage_level": "og"}
      resp_low = client.post("/api/pricing/analyze",
                             json={**base, "t1_kwh": 25000, "t2_kwh": 12500, "t3_kwh": 12500})
      resp_high = client.post("/api/pricing/analyze",
                              json={**base, "t1_kwh": 250000, "t2_kwh": 125000, "t3_kwh": 125000})
      assert resp_low.status_code == 200
      assert resp_high.status_code == 200
      assert resp_low.json()["cache_hit"] is False
      assert resp_high.json()["cache_hit"] is False   # B cache miss — farklı key
      assert response_hash(resp_low.json()) != response_hash(resp_high.json())
      assert resp_low.json()["weighted_prices"]["total_consumption_kwh"] == 50000.0
      assert resp_high.json()["weighted_prices"]["total_consumption_kwh"] == 500000.0
  ```
- **Kanıt (DoD):** PASS; B1 baseline regression replay (LOW hash ≠ HIGH hash)
- **Requirement ref:** 2.1, 2.7
- **Rollback:** test silinir

### [ ] T17 — Cache hit determinism integration (Decision 4, Test 4)
- **Dosya:** `backend/tests/test_pricing_cache_key_completeness_integration.py`
- **Çıktı:** Test `TestCacheHitDeterminism::test_same_request_hits_cache`:
  ```python
  def test_same_request_hits_cache(client, seeded_db):
      payload = {...}  # tam 12 alan identik
      resp1 = client.post("/api/pricing/analyze", json=payload)
      resp2 = client.post("/api/pricing/analyze", json=payload)
      assert resp1.json()["cache_hit"] is False   # miss + write
      assert resp2.json()["cache_hit"] is True    # hit
      assert response_hash(resp1.json()) == response_hash(resp2.json())
  ```
- **Kanıt (DoD):** PASS; INV-1 end-to-end doğrulaması
- **Requirement ref:** 2.4, 3.1
- **Rollback:** test silinir

### [ ] T18 — Cache observability integration (Decision 9)
- **Dosya:** `backend/tests/test_pricing_cache_key_completeness_integration.py`
- **Çıktı:** Test sınıfı `TestCacheObservability`:
  - `test_cache_field_on_miss` — miss'te `cache.hit == False`, `cache.key_version == "v2"`, `cache.cached_key_version is None`
  - `test_cache_field_on_hit` — hit'te `cache.hit == True`, `cache.key_version == "v2"`, `cache.cached_key_version == "v2"`
  - `test_cache_hit_mirror` — her durumda `response.cache.hit == response.cache_hit` (backward compat invariant)
- **Kanıt (DoD):** 3 test PASS; response şeması `cache` objesi içeriyor
- **Requirement ref:** 2.8, 3.8
- **Rollback:** test sınıfı silinir

**Phase 3 DoD:**
- [ ] T8-T18 merge edildi
- [ ] `pytest backend/tests/test_pricing_cache_key_completeness_*.py -v` exit 0
- [ ] 11 test class/function: INV-1..INV-5 (5) + version isolation (1) + integration (4 — LOW vs HIGH, hit determinism, 3 cache observability) + file scaffolds (2×8)
- [ ] B1 baseline vakaları (25k vs 250k) deterministik PASS

---

## 🔵 Phase 4 — Cleanup & Integration

### [ ] T19 — Mevcut `test_pricing_cache.py` regression verify
- **Dosya:** `backend/tests/test_pricing_cache.py` (değişmez)
- **Giriş:** Mevcut `TestBuildCacheKey` suite (test_deterministic, test_multiplier_change_differs, test_customer_change_differs, test_period_change_differs, test_dealer_commission_change_differs, test_template_vs_real_differs, test_sha256_format)
- **Çıktı:** DOSYA DEĞİŞMEZ — sadece doğrulama:
  - `pytest backend/tests/test_pricing_cache.py -v` exit 0
  - Hiçbir test FAIL değil (yeni 5 parametre default None olduğu için mevcut çağrılar bozulmaz)
- **Kanıt (DoD):**
  - 7+ mevcut test PASS
  - INV-5 (core-7 ayırt ediciliği) ampirik olarak doğrulanmış
- **Requirement ref:** 3.3, 3.4
- **Rollback:** task değişiklik içermediği için rollback yok; FAIL olursa T2/T3/T4'e dön

### [ ] T20 — Full pricing cache test suite run
- **Dosya:** `backend/tests/` (çalıştırma, değişiklik yok)
- **Giriş:** T1-T19 merge edildi
- **Çıktı:** Tek komut smoke:
  ```cmd
  pytest backend/tests/test_pricing_cache.py backend/tests/test_pricing_cache_key_completeness_pbt.py backend/tests/test_pricing_cache_key_completeness_integration.py -v
  ```
  - Exit code 0
  - Toplam test sayısı: mevcut (7) + yeni PBT (6+) + yeni integration (5+) ≈ 18+
- **Kanıt (DoD):**
  - Tüm testler PASS
  - Hiçbir xfail veya skip (fix tamam, coverage tam)
  - Hypothesis `max_examples=200` ayarı ile 200+ örnek test edildi (PBT)
- **Requirement ref:** DoD (tüm suite yeşil)
- **Rollback:** yok (smoke test)

### [ ] T21 — Baseline invalidation note + downstream spec refresh
- **Dosya:** `baselines/2026-05-12_pre-ptf-unification_baseline.json` + `.kiro/specs/ptf-sot-unification/tasks.md` + `.kiro/steering/source-of-truth.md`
- **Giriş:** T1-T20 fix canlıda; mevcut baseline cache kontaminasyonu içerdiği için invalid
- **Çıktı:**
  - `baselines/2026-05-12_pre-ptf-unification_baseline.json` dosyası TOP-LEVEL `_meta` objesine `"invalidated_by": "pricing-cache-key-completeness"`, `"invalidation_reason": "cache_key_collision_v1"`, `"status": "INVALIDATED"` alanları eklenir (silinmez, audit trail)
  - `.kiro/specs/ptf-sot-unification/tasks.md` T1.1 entry'sine not: "PRE-REQUISITE: pricing-cache-key-completeness merged; baseline re-run with v2 cache key required"
  - `.kiro/steering/source-of-truth.md` §7 kanıt zinciri listesine `pricing-cache-key-completeness` commit SHA'sı eklenir
- **Kanıt (DoD):**
  - Baseline dosyasında `_meta.status == "INVALIDATED"`
  - `ptf-sot-unification/tasks.md` T1.1 entry'si yeni bir not içerir
  - Steering dokümanı güncellendi
- **Requirement ref:** Decision 6
- **Rollback:** metadata edit; tek commit revert

**Phase 4 DoD:**
- [ ] T19-T21 merge edildi
- [ ] Baseline invalidated, downstream spec'ler bilgilendirildi
- [ ] Fix merge → PR title: `fix(pricing): P0 cache key collision — add t1/t2/t3/use_template/voltage_level + cache observability (v1→v2 bump)`

---

## Acceptance Criteria (Spec-level)

Bu spec şu kriterler yeşile döndüğünde CLOSED:

- ✔ **LOW ≠ HIGH response** — T16 integration test PASS (B1 baseline collision replay reddedilir)
- ✔ **Cache hit doğru çalışır** — T17 integration test PASS (determinism end-to-end)
- ✔ **v1 → v2 contamination yok** — T14 + T1 (version bump + isolation unit test)
- ✔ **voltage None == "og"** — T11 counter-property PASS (Decision 10)
- ✔ **Response cache metadata içerir** — T18 integration PASS (3 alan `cache.hit`, `cache.key_version`, `cache.cached_key_version`)
- ✔ **PBT invariants PASS** — T9-T13 hypothesis 200× ornek deterministik
- ✔ **Mevcut test suite hâlâ geçer** — T19 regression PASS (backward compat)
- ✔ **Full suite run exit 0** — T20
- ✔ **Baseline invalidated + downstream bilgilendirildi** — T21

## İstatistikler

| Phase | Task | Etki alanı | Tahmini süre | Risk |
|---|---|---|---|---|
| Phase 1 (Core Fix) | 4 | `pricing_cache.py` tek dosya | 30-45 dk | Düşük (atomik commit, rollback kolay) |
| Phase 2 (Response Contract) | 3 | `models.py` + `router.py` | 30-45 dk | Düşük (backward compat korunur) |
| Phase 3 (Tests) | 11 | 2 yeni test dosyası | 1-2 saat | Düşük (PBT seed sabit, integration deterministik) |
| Phase 4 (Cleanup) | 3 | metadata + downstream | 15 dk | Yok |
| **Toplam** | **21** | **4 kod dosyası + 2 test + 3 metadata** | **2.5-4 saat** | **P0 hot-fix** |

## Sonraki Spec

1. **Baseline re-run** — `ptf-sot-unification/tasks.md` T1.1 tekrar çalıştırılır (v2 cache key ile yeni 30-snapshot baseline, `baselines/<YYYY-MM-DD>_pre-ptf-unification_baseline_v2.json`)
2. **PTF SoT migration** — `ptf-sot-unification` Phase 1 başlar (kill switch + write lock)
