# Bugfix Gereksinim Dokümanı

## Giriş

Bu doküman, Türk enerji fiyatlandırma uygulamasındaki 4 kritik hatayı ele almaktadır:

1. **Brüt Marj Formülü Tutarsızlığı** — `pricing_engine.py`, `router.py`, `calculator.py` ve `App.tsx` modüllerinde brüt marj hesaplama formülünün tutarsız uygulanması
2. **Net Marjda Dengesizlik Maliyeti Eksikliği** — `router.py` analyze endpoint'inde per-MWh net marj hesabında dengesizlik maliyetinin düşülmemesi
3. **Dağıtım Tarife Frontend Hardcode** — Frontend'de 32 adet EPDK dağıtım tarifesinin hardcode edilmesi, backend API'den çekilmemesi
4. **YEKDEM Yoksa Graceful Handling** — YEKDEM verisi olmayan dönemlerde 404 hatası yerine uyarı ile devam edilmesi

Bu hatalar fiyatlandırma doğruluğunu, veri tutarlılığını ve kullanıcı deneyimini doğrudan etkiler.

## Bug Analizi

### Mevcut Davranış (Hata)

**Bug 1 — Brüt Marj Formülü Tutarsızlığı:**

1.1 WHEN brüt marj `pricing_engine.py`'de hesaplanırken THEN sistem `total_gross_margin = total_sales - total_base_cost` formülünü kullanır; burada `base_cost = kWh × (PTF + YEKDEM) / 1000` olup dağıtım maliyeti dahil değildir

1.2 WHEN brüt marj `router.py` analyze endpoint'inde per-MWh olarak hesaplanırken THEN sistem `gross_margin_per_mwh = sales_price_per_mwh - supplier_cost.total_cost_tl_per_mwh` formülünü kullanır; burada `supplier_cost.total_cost_tl_per_mwh = PTF + YEKDEM + Dengesizlik` olup dağıtım maliyeti dahil değildir

1.3 WHEN brüt marj `App.tsx` frontend'de `liveCalculation` içinde hesaplanırken THEN sistem `offer_energy_tl + offer_distribution_tl + offer_btv_tl - current costs` şeklinde farklı bir formül kullanır ve modüller arası tutarsızlık oluşur

1.4 WHEN `calculator.py`'de teklif hesaplaması yapılırken THEN brüt marj ayrı bir alan olarak hesaplanmaz; sadece `difference_excl_vat_tl` ve `difference_incl_vat_tl` üzerinden fark gösterilir ve diğer modüllerle karşılaştırılabilir bir brüt marj değeri üretilmez

**Bug 2 — Net Marjda Dengesizlik Maliyeti:**

1.5 WHEN `router.py` analyze endpoint'inde per-MWh net marj hesaplanırken THEN sistem `net_margin_per_mwh = gross_margin_per_mwh - dealer_per_mwh` formülünü kullanır ve dengesizlik maliyetini düşmez

1.6 WHEN `pricing_engine.py`'de toplam net marj hesaplanırken THEN sistem `total_net_margin = total_gross_margin - dealer_commission - imbalance_share` formülünü doğru kullanır; ancak `router.py`'deki per-MWh hesabı bu mantıkla uyumsuz kalır

**Bug 3 — Dağıtım Tarife Frontend Hardcode:**

1.7 WHEN kullanıcı frontend'de dönem seçtiğinde THEN sistem `App.tsx`'teki `TARIFF_PERIODS` ve `OSB_TARIFFS` hardcode dizilerinden dağıtım tarifelerini okur ve backend veritabanındaki güncel EPDK tarifelerini kullanmaz

1.8 WHEN yeni bir EPDK tarife dönemi eklendiğinde THEN frontend'deki hardcode diziler güncellenmediği sürece kullanıcılar eski tarife fiyatlarını görür

**Bug 4 — YEKDEM Yoksa 404 Hatası:**

1.9 WHEN analyze endpoint'ine YEKDEM verisi olmayan bir dönem için istek gönderildiğinde THEN sistem HTTP 404 hatası döner ve analiz tamamen başarısız olur

1.10 WHEN simulate veya compare endpoint'lerine YEKDEM verisi olmayan bir dönem için istek gönderildiğinde THEN sistem HTTP 404 hatası döner ve simülasyon/karşılaştırma tamamen başarısız olur

### Beklenen Davranış (Doğru)

**Bug 1 — Brüt Marj Formülü Tutarsızlığı:**

2.1 WHEN brüt marj herhangi bir modülde hesaplanırken THEN sistem tüm modüllerde tutarlı olarak `Brüt Marj = Satış - (PTF + YEKDEM + Dağıtım)` formülünü KULLANMALIDIR

2.2 WHEN `pricing_engine.py`'de `total_gross_margin` hesaplanırken THEN sistem `base_cost` tanımına dağıtım maliyetini dahil etmeli veya brüt marjdan dağıtım maliyetini ayrıca düşerek `Brüt Marj = Satış - Baz Maliyet - Dağıtım` formülünü UYGULAMALIDIR

2.3 WHEN `router.py`'de `gross_margin_per_mwh` hesaplanırken THEN sistem `supplier_cost.total_cost_tl_per_mwh` değerine dağıtım birim maliyetini ekleyerek veya ayrıca düşerek tutarlı brüt marj HESAPLAMALIDIR

2.4 WHEN `App.tsx`'te `liveCalculation` brüt marj gösterirken THEN sistem backend ile aynı formülü kullanarak `Brüt Marj = Satış Geliri - (PTF + YEKDEM + Dağıtım) Maliyeti` şeklinde HESAPLAMALIDIR

**Bug 2 — Net Marjda Dengesizlik Maliyeti:**

2.5 WHEN `router.py` analyze endpoint'inde per-MWh net marj hesaplanırken THEN sistem `net_margin_per_mwh = gross_margin_per_mwh - dealer_per_mwh - imbalance_tl_per_mwh` formülünü UYGULAMALIDIR

2.6 WHEN `PricingSummary` response modeli oluşturulurken THEN sistem `imbalance_tl_per_mwh` değerini net marj hesabına dahil ettiğini açıkça GÖSTERMELİDİR

**Bug 3 — Dağıtım Tarife Frontend Hardcode:**

2.7 WHEN kullanıcı frontend'de dönem seçtiğinde THEN sistem backend'deki `GET /api/distribution-tariffs?period=YYYY-MM` endpoint'inden güncel EPDK tarifelerini ÇEKMELİDİR

2.8 WHEN backend'e `GET /api/distribution-tariffs?period=YYYY-MM` isteği gönderildiğinde THEN sistem `DistributionTariffDB` tablosundan ilgili döneme ait geçerli tarifeleri dönerek JSON formatında YANIT VERMELİDİR

2.9 WHEN frontend tarife verilerini backend'den aldığında THEN sistem `App.tsx`'teki `TARIFF_PERIODS` ve `OSB_TARIFFS` hardcode dizilerini KULLANMAMALIDIR

**Bug 4 — YEKDEM Yoksa Graceful Handling:**

2.10 WHEN analyze endpoint'ine YEKDEM verisi olmayan bir dönem için istek gönderildiğinde THEN sistem YEKDEM değerini 0 olarak kabul edip analizi uyarı mesajı ile birlikte TAMAMLAMALIDIR

2.11 WHEN YEKDEM verisi bulunamadığında THEN sistem response'daki `warnings` listesine `{"type": "yekdem_not_found", "message": "..."}` formatında bir uyarı EKLEMELİDİR

2.12 WHEN simulate veya compare endpoint'lerinde YEKDEM verisi bulunamadığında THEN sistem YEKDEM=0 ile hesaplamaya devam etmeli ve uyarı mesajı DÖNMELİDİR

### Değişmeyen Davranış (Regresyon Önleme)

3.1 WHEN tüm modüllerde PTF ve YEKDEM verileri mevcut olduğunda THEN sistem mevcut ağırlıklı PTF hesaplama mantığını (kWh ağırlıklı ortalama) KORUMAYA DEVAM ETMELİDİR

3.2 WHEN `pricing_engine.py`'de saatlik maliyet hesaplanırken THEN sistem `base_cost_tl = kWh × (PTF + YEKDEM) / 1000` formülünü saatlik bazda doğru UYGULAMAYA DEVAM ETMELİDİR

3.3 WHEN bayi komisyonu hesaplanırken THEN sistem mevcut puan paylaşımı modelini (segment bazlı sabit puan) KORUMAYA DEVAM ETMELİDİR

3.4 WHEN `calculator.py`'de fatura teklif hesaplaması yapılırken THEN sistem mevcut `offer_energy_tl = (PTF + YEKDEM) × kWh × multiplier` formülünü KORUMAYA DEVAM ETMELİDİR

3.5 WHEN admin endpoint'leri (`/admin/distribution-tariffs`, `/admin/distribution-tariffs/lookup`, `/admin/distribution-tariffs/parse`) çağrıldığında THEN sistem mevcut admin API davranışını KORUMAYA DEVAM ETMELİDİR

3.6 WHEN YEKDEM verisi mevcut olan dönemler için analiz yapıldığında THEN sistem mevcut YEKDEM hesaplama mantığını (DB'den okuma, fiyata ekleme) KORUMAYA DEVAM ETMELİDİR

3.7 WHEN frontend'de manuel mod aktifken hesaplama yapıldığında THEN sistem mevcut `liveCalculation` mantığının BTV, KDV ve tasarruf hesaplamalarını KORUMAYA DEVAM ETMELİDİR

3.8 WHEN cache mekanizması aktifken aynı parametrelerle tekrar istek gönderildiğinde THEN sistem mevcut cache davranışını (cache_hit=true) KORUMAYA DEVAM ETMELİDİR
