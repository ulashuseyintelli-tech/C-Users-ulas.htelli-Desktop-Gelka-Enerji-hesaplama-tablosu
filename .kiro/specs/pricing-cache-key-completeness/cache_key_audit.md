# Cache Key Parameter Audit

**Tablo:** `backend/app/pricing/pricing_cache.py::build_cache_key()` v2 formülündeki her parametrenin cache key hesabına dahil olup olmadığı, normalize kuralı ve gerekçesi.

**Amaç:** ileride yeni alan eklenirken veya code review sırasında "acaba bunu key'e katmalı mıydım?" sorusunu tek tablodan cevaplayabilmek. Bir alanın response'u etkilemesine rağmen key'de olmaması, bu dosyada kanıtlanan bug sınıfıdır (pricing-cache-key-completeness).

## Canonical key bileşenleri (v2, sıralı)

| # | Param | Key'de mi | Normalize | Response etkisi | Gerekçe |
|---|---|:---:|---|---|---|
| 0 | `_cache_version` | ✅ | sabit `"v2"` | — | Hash izolasyonu; v1/v2 kayıtlarını segmente eder (Decision 1) |
| 1 | `customer_id` | ✅ | `None → "__template__"` | `weighted_prices.customer_id` | Null string olursa consistent key; template senaryoları ayrı bucket |
| 2 | `period` | ✅ | `str` "YYYY-MM" | PTF/YEKDEM lookup + hour count | Farklı dönem = farklı piyasa verisi |
| 3 | `multiplier` | ✅ | `round(x, 6)` | `pricing.sales_energy_price_per_mwh` | Finansal çarpan; float drift önleme |
| 4 | `dealer_commission_pct` | ✅ | `round(x, 2)` | `pricing.dealer_commission_per_mwh` | Bayi komisyonu; 2 ondalık yeterli |
| 5 | `imbalance.forecast_error_rate` | ✅ | `round(x, 4)` | `pricing.imbalance_cost_per_mwh` | Dengesizlik modelinin ana girdisi |
| 5 | `imbalance.imbalance_cost_tl_per_mwh` | ✅ | `round(x, 2)` | `pricing.imbalance_cost_per_mwh` | TL para birimi, kuruş hassasiyeti |
| 5 | `imbalance.smf_based_imbalance_enabled` | ✅ | `bool` | Hesaplama formülü değişir | Boolean flag; mod seçimi |
| 6 | `template_name` | ✅ | `str` veya `None` | Template modda `time_zone_breakdown` | Farklı şablon = farklı profil ağırlığı |
| 7 | `template_monthly_kwh` | ✅ | `round(x, 2) or None` | Template modda `total_consumption_kwh` | Toplam tüketim miktarı |
| 8 | `t1_kwh` | ✅ | `round(x, 4) or None` | T1 zone consumption + total | **T2 fix kritik alanı** |
| 9 | `t2_kwh` | ✅ | `round(x, 4) or None` | T2 zone consumption + total | **T2 fix kritik alanı** |
| 10 | `t3_kwh` | ✅ | `round(x, 4) or None` | T3 zone consumption + total | **T2 fix kritik alanı** |
| 11 | `use_template` | ✅ | `bool` korunur, `None` ≠ `False` | Validation path seçimi | **T2 fix**; semantik fark korunur |
| 12 | `voltage_level` | ✅ | `None → "og"` canonical | `distribution.unit_price_tl_per_kwh` | **T3 fix**; None ≡ "og" (handler default) |

## Key'de OLMAYAN alanlar (bilinçli)

| Param | Neden key'de yok | Risk seviyesi |
|---|---|---|
| `auto_fetch` (epias endpoint) | Analyze endpoint'inde yok, sadece GET epias'ta | n/a (başka endpoint) |
| `include_report_types` | Response format flag, hesap değişmez | düşük |
| request headers | Kimlik zaten `customer_id` ile kapsanmış | düşük |

## YEKDEM & PTF dolaylı key bileşenleri

Aşağıdaki veriler key'e **doğrudan** girmez ama `period` key'de olduğu için dolaylı olarak segmente edilir:

| Veri | Lookup | Invalidation tetikleyicisi |
|---|---|---|
| PTF saatlik (hourly_market_prices) | `period` bazlı | `invalidate_cache_for_period(period)` — piyasa verisi UPDATE |
| YEKDEM aylık (monthly_yekdem_prices) | `period` bazlı | `invalidate_cache_for_period(period)` — YEKDEM UPDATE |
| Distribution tarifesi | `voltage_level` + `period` bazlı | Tarife değişiklik = yeni period cache (manuel) |
| Profile template ağırlıkları | `template_name` bazlı | Template değiştirilirse eski kayıtlar stale kalabilir — **TODO** |

**TODO note:** ProfileTemplate.hourly_weights değiştirilirse mevcut cache invalidate edilmiyor. Genelde template'ler immutable kabul ediliyor ama explicit güvence için bir hook gerekebilir. Bu spec kapsamı dışında; olası `pricing-template-invalidation` spec'inde ele alınabilir.

## Invalidation çağrı noktaları

| Tetikleyici | Dosya:satır | Çağrı |
|---|---|---|
| Piyasa verisi yüklendi (`/api/pricing/upload-market-data`) | `router.py:373` | `invalidate_cache_for_period(db, period)` |
| YEKDEM yüklendi (`/api/pricing/upload-yekdem`) | `router.py:932` | `invalidate_cache_for_period(db, period)` |
| TTL süresi doldu | otomatik, her read'te | `get_cached_result` içinde |
| Manuel (admin) | `/api/admin/cache/invalidate/*` | `invalidate_cache_for_customer` / `_for_period` |

## Fallback davranışları (cache yazar mı?)

| Senaryo | Response | Cache yazılır mı? |
|---|---|---|
| PTF yok → HTTP 404 `market_data_not_found` | exception | ❌ hayır (write satırına erişim yok) |
| YEKDEM yok → `yekdem=0.0` graceful fallback | warning + response | ✅ evet, ama YEKDEM INSERT bu cache'i invalidate eder |
| PTF eksik dönem (kısmi saat) | warning + mevcut saatlerle hesap | ✅ evet, period cache |
| Template bulunamadı | HTTP 400 validation error | ❌ exception |

## Versioning protokolü

v1 → v2 bump sırasında uygulanan kurallar:

1. `CACHE_KEY_VERSION` sabiti değiştirildi, `_cache_version` field'ı key'e eklendi
2. Eski v1 kayıtları **silinmedi** (TRUNCATE yok) — TTL ile doğal temizlik
3. Yeni v2 request'leri v1 kayıtlarına match olmaz (farklı hash)
4. Response `cache.key_version` alanı ile canlıda v2 çalıştığı gözlemlenebilir
5. Gelecekte v3 bump'ı aynı pattern ile: sabit güncellenir, test version isolation güncellenir

## Referans linkleri

- Spec: `.kiro/specs/pricing-cache-key-completeness/{bugfix.md, design.md, tasks.md}`
- PBT: `backend/tests/test_pricing_cache_key_completeness_pbt.py` (17 test)
- Integration: `backend/tests/test_pricing_cache_key_completeness_integration.py` (5 test)
- Fonksiyon: `backend/app/pricing/pricing_cache.py::build_cache_key`
- Handler: `backend/app/pricing/router.py::analyze` (satır 457)
- Response model: `backend/app/pricing/models.py::CacheInfo`, `AnalyzeResponse.cache`
