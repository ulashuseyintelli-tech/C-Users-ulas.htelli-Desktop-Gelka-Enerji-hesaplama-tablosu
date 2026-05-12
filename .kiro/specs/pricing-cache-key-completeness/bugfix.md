# Bugfix Requirements Document

## Introduction

`backend/app/pricing/pricing_cache.py::build_cache_key()`, `/api/pricing/analyze` için SHA256 cache key üretirken `AnalyzeRequest` modelinin beş tüketim/profil alanını (`t1_kwh`, `t2_kwh`, `t3_kwh`, `use_template`, `voltage_level`) key bileşenlerine **dahil etmiyor**. Bu alanların hepsi response'u (tüketim toplamı, zaman dilimi dağılımı, dağıtım bedeli) doğrudan etkilediği halde key'e girmediği için, aynı `period + multiplier + customer_id + dealer_commission_pct + imbalance_params + template_*` kombinasyonuna ancak farklı T1/T2/T3 (veya farklı `use_template` / `voltage_level`) ile yapılan iki istek, **aynı cache key**'i üretir. İkinci istek, tüketim profili tamamen farklı olsa bile birinci isteğin cevabını hit olarak alır; TTL 24 saat olduğundan bu pencerede iki müşteriden birine yanlış teklif çıkar.

Bug production'da B1 baseline koşusunda somut olarak doğrulandı: `baselines/2026-05-12_pre-ptf-unification_baseline.json` içinde `2026-03::low::analyze` (t1/t2/t3 = 25000/12500/12500) ve `2026-03::high::analyze` (t1/t2/t3 = 250000/125000/125000) snapshot'ları aynı `response_hash = 95d6bada181889af…` ve aynı `total_consumption_kwh = 50000` (high için doğrusu 500000) değerini döndürdü. Aynı pattern 2026-01, 2026-02, 2026-04 için de tekrarlandı — tüm 4 canonical dönemde cache kontaminasyonu kanıtlandı. P0 finansal hata.

**Kapsam notu (grep doğrulaması):** `build_cache_key` şu an sadece `/api/pricing/analyze` handler'ında çağrılıyor (router.py satır 457). `/simulate`, `/compare`, `/report/pdf`, `/report/excel` cache kullanmıyor. Fix scope = tek çağrı noktası.

## Bug Analysis

### Current Behavior (Defect)

`build_cache_key()` beş alanı key hesabına dahil etmiyor (`t1_kwh`, `t2_kwh`, `t3_kwh`, `use_template`, `voltage_level`); bu yüzden tüketim profili veya gerilim seviyesi değişse bile key aynı kalıyor ve `/api/pricing/analyze` yanlış cache hit alıyor.

1.1 WHEN iki `/api/pricing/analyze` isteği aynı `period + customer_id + multiplier + dealer_commission_pct + imbalance_params + template_name + template_monthly_kwh` ile ama farklı `(t1_kwh, t2_kwh, t3_kwh)` tuple'ları ile geldiğinde THEN sistem her iki istek için aynı `cache_key` üretir ve ikinci istek birinci isteğin cevabını cache hit olarak döndürür
1.2 WHEN iki `/api/pricing/analyze` isteği aynı diğer alanlarla ama farklı `use_template` değeri (örn. `true` vs `false`, aynı `template_monthly_kwh`) ile geldiğinde THEN sistem aynı `cache_key` üretir ve template moduna göre farklı olması gereken sonuçlardan birini yanlış olarak cache'den döner
1.3 WHEN iki `/api/pricing/analyze` isteği aynı diğer alanlarla ama farklı `voltage_level` (örn. `"og"` vs `"ag"`) ile geldiğinde THEN sistem aynı `cache_key` üretir ve dağıtım bedeli farklı olması gereken sonuçlardan birini yanlış olarak cache'den döner
1.4 WHEN `/api/pricing/analyze` çağrısı TTL penceresi (24 saat) içinde cache hit aldığında ve hit rekoru 1.1/1.2/1.3'teki gibi key collision sonucu yazılmışsa THEN sistem response olarak ilk müşterinin `total_consumption_kwh`, `weighted_prices`, `pricing`, `time_zone_breakdown` ve `distribution` alanlarını mevcut müşteriye ait gibi sunar
1.5 WHEN üretim cache tablosu (`analysis_cache`) fix öncesi şema ile yazılmış geçerli (TTL'si dolmamış) satırlar içerdiğinde THEN bu satırlar fix sonrasında da hit verebilir çünkü eski satırların key'i eksik alanlardan hesaplanmıştır ve eski collision'ı temsil eder

### Expected Behavior (Correct)

`build_cache_key()` beş alanı da key'e dahil etmelidir; cache key'in her bileşeni response'u etkileyen her input'u temsil etmelidir; eski (fix öncesi) satırlar yeni request'lerle çakışmamalıdır.

2.1 WHEN iki `/api/pricing/analyze` isteği aynı diğer alanlarla ama farklı `(t1_kwh, t2_kwh, t3_kwh)` tuple'ları ile geldiğinde THEN sistem SHALL her istek için **farklı** `cache_key` üretir ve ikinci istek için cache miss alarak gerçek hesaplamayı çalıştırır
2.2 WHEN iki `/api/pricing/analyze` isteği aynı diğer alanlarla ama farklı `use_template` değerleri ile geldiğinde THEN sistem SHALL her istek için **farklı** `cache_key` üretir
2.3 WHEN iki `/api/pricing/analyze` isteği aynı diğer alanlarla ama farklı `voltage_level` değerleri ile geldiğinde THEN sistem SHALL her istek için **farklı** `cache_key` üretir
2.4 WHEN iki `/api/pricing/analyze` isteği **tamamen aynı** tüm parametrelerle (mevcut 7 alan + yeni 5 alan) geldiğinde THEN sistem SHALL aynı `cache_key` üretir (determinism korunur)
2.5 WHEN `/api/pricing/analyze` çağrılırken `t1_kwh`/`t2_kwh`/`t3_kwh` alanları `None` (verilmemiş) ise THEN sistem SHALL bu alanları key'e deterministik bir şekilde (örn. `None` değeri) dahil eder ve yine benzer alan değerleri olan başka bir istekle aynı key'i üretmeye devam eder (None == None collision'ı bug değildir, template modu zaten kapsar)
2.6 WHEN fix deploy edildikten sonra üretim cache'inde eski şema ile yazılmış geçerli satırlar bulunduğunda THEN sistem SHALL bu satırlara key collision vermeyecek şekilde (örn. key'e `"_cache_version"` sabit alanı ekleyerek veya tabloyu invalidate ederek) yeni request'leri eski kayıtlardan izole eder
2.7 WHEN fix uygulandıktan sonra B1 baseline koşusu tekrar çalıştırıldığında THEN sistem SHALL `2026-03::low::analyze` ve `2026-03::high::analyze` snapshot'ları için farklı `response_hash` ve her biri için doğru `total_consumption_kwh` (sırasıyla 50000 ve 500000) döndürür
2.8 WHEN `/api/pricing/analyze` çağrısı yapıldığında (cache miss veya hit farketmez) THEN sistem SHALL response'a yapılandırılmış bir `cache` objesi dahil eder: `cache.hit: bool` (mevcut `cache_hit` ile eşit), `cache.key_version: str` (canlıda sabit `"v2"`), `cache.cached_key_version: Optional[str]` (hit durumunda cache'deki kaydın version'u). Mevcut `cache_hit` alanı geriye uyumluluk için korunur.
2.9 WHEN iki `/api/pricing/analyze` isteği aynı diğer alanlarla ama biri `voltage_level=null` diğeri `voltage_level="og"` ile geldiğinde THEN sistem SHALL her ikisi için **aynı** `cache_key` üretir (None canonical `"og"` değerine normalize edilir); `voltage_level="ag"` ile bu ikisi arasında **farklı** key üretilmeye devam eder

### Unchanged Behavior (Regression Prevention)

Fix, cache key'in mevcut bileşenlerini korumalı; key ekleme/çıkarma dışında hiçbir pricing hesaplama mantığı, response şeması, cache okuma/yazma mekanizması veya invalidation davranışı değişmemelidir.

3.1 WHEN `/api/pricing/analyze` aynı `period + customer_id + multiplier + dealer_commission_pct + imbalance_params + template_name + template_monthly_kwh + t1_kwh + t2_kwh + t3_kwh + use_template + voltage_level` ile iki kez çağrıldığında THEN sistem SHALL CONTINUE TO ikinci çağrıda cache hit döndürür ve `cache_hit=True` flag'ini set eder
3.2 WHEN `build_cache_key()` mevcut 7 parametre ile çağrıldığında (yeni 5 alan default `None`/`False` ile) THEN sistem SHALL CONTINUE TO 64 karakter SHA256 hex string döndürür (return tipi, uzunluğu ve format değişmez)
3.3 WHEN `/api/pricing/analyze` çağrıldığında sadece `multiplier` (veya `customer_id`, `period`, `dealer_commission_pct`, `imbalance_params`, `template_name`, `template_monthly_kwh`) değişirken diğer tüm alanlar sabit tutulduğunda THEN sistem SHALL CONTINUE TO farklı `cache_key` üretmeye devam eder (mevcut 7 bileşenin ayırt ediciliği korunur — `backend/tests/test_pricing_cache.py::TestBuildCacheKey` test sınıfı geçmeye devam eder)
3.4 WHEN `get_cached_result()`, `set_cached_result()`, `invalidate_cache_for_customer()`, `invalidate_cache_for_period()`, `cleanup_expired_cache()` fonksiyonları çağrıldığında THEN sistem SHALL CONTINUE TO mevcut davranışlarını korur (TTL kontrolü, `hit_count` artırma, corrupt JSON temizliği, silinen kayıt sayısı dönüşü)
3.5 WHEN `/api/pricing/simulate`, `/api/pricing/compare`, `/api/pricing/report/pdf`, `/api/pricing/report/excel` endpoint'leri çağrıldığında THEN sistem SHALL CONTINUE TO cache kullanmadan (mevcut davranışları aynı) cevap döndürür — bu endpoint'ler fix kapsamı dışıdır
3.6 WHEN `AnalyzeRequest` modeli sabit kaldığında (alan eklenmediği veya kaldırılmadığı durumlarda) THEN sistem SHALL CONTINUE TO mevcut response şemasını (`AnalyzeResponse` alanları, `cache_hit` flag davranışı, `warnings` yapısı) değiştirmeden döndürür
3.7 WHEN `analysis_cache` tablosu sorgulandığında THEN sistem SHALL CONTINUE TO aynı şema ile (`cache_key`, `customer_id`, `period`, `params_hash`, `result_json`, `created_at`, `expires_at`, `hit_count` kolonları) çalışır — şema migration'ı gerekmez (invalidation stratejisi key-version bump yoluyla yapılır, tablo DDL değişmez)
3.8 WHEN `/api/pricing/analyze` response tüketicisi mevcut `cache_hit: bool` alanını okuduğunda THEN sistem SHALL CONTINUE TO bu alanın mevcut davranışını korur (cache hit'te `True`, miss'te `False`); yeni `cache.hit` alanı ile değeri her zaman eşittir; `cache_hit` geriye uyumluluk için silinmez (FE mevcut consumer'ları etkilenmez)
