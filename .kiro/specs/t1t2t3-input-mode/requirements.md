# Requirements Document

## Introduction

Risk Analizi paneline T1/T2/T3 giriş modu eklenmesi ve dağıtım bedeli sonuçlarının frontend'de görünür hale getirilmesi. Mevcut backend altyapısı (time_zones.py, distribution_tariffs.py, tariff_simulator.py) kullanılarak, kullanıcının fatura/CK bilgisindeki Gündüz (T1), Puant (T2), Gece (T3) kWh değerlerini doğrudan girebilmesi ve bu değerlerden 744 saatlik tüketim profili üretilmesi sağlanacaktır.

## Glossary

- **Risk_Paneli**: Frontend'deki Risk Analizi paneli; tüketim profili, katsayı ve dönem bilgilerine göre fiyatlama risk analizi yapan UI bileşeni
- **Giriş_Modu_Seçici**: Risk panelinde tüketim verisi kaynağını belirleyen seçim kontrolü; "Şablon Profili" veya "Gerçek T1/T2/T3" seçeneklerini sunar
- **Şablon_Modu**: Sektör profil şablonu (3 Vardiya Sanayi, Otel vb.) ve aylık toplam kWh kullanarak saatlik tüketim profili üreten mevcut mod
- **T1T2T3_Modu**: Kullanıcının Gündüz (T1), Puant (T2), Gece (T3) kWh değerlerini doğrudan girdiği yeni tüketim giriş modu
- **T1**: Gündüz zaman dilimi; 06:00–16:59 arası (günde 11 saat)
- **T2**: Puant zaman dilimi; 17:00–21:59 arası (günde 5 saat)
- **T3**: Gece zaman dilimi; 22:00–05:59 arası (günde 8 saat)
- **Tüketim_Profili**: 744 saatlik (31 gün × 24 saat) saatlik kWh tüketim dizisi; backend analiz endpoint'ine gönderilen veri
- **Dağıtım_Bedeli**: EPDK tarafından belirlenen, tarife grubuna ve gerilim seviyesine göre değişen dağıtım sistemi kullanım bedeli (TL/kWh)
- **Hesaplama_Motoru**: Backend'deki pricing analiz servisi; time_zones.py, distribution_tariffs.py ve tariff_simulator.py modüllerini kullanan hesaplama katmanı
- **Profil_Üretici**: T1/T2/T3 kWh değerlerinden 744 saatlik tüketim profili üreten backend fonksiyonu
- **Puant_Risk_Uyarısı**: T2 (puant) tüketim oranı belirli bir eşiği aştığında gösterilen uyarı mesajı

## Requirements

### Requirement 1: Giriş Modu Seçici

**User Story:** As a enerji satış uzmanı, I want to Risk Analizi panelinde tüketim verisi kaynağını seçmek (şablon veya gerçek T1/T2/T3), so that müşterinin fatura bilgisine göre daha doğru analiz yapabilirim.

#### Acceptance Criteria

1. WHEN Risk_Paneli etkinleştirildiğinde, THE Giriş_Modu_Seçici SHALL "Şablon Profili" ve "Gerçek T1/T2/T3" olmak üzere iki seçenek sunmalıdır
2. THE Giriş_Modu_Seçici SHALL varsayılan olarak "Şablon Profili" seçili durumda başlamalıdır
3. WHEN "Şablon Profili" seçildiğinde, THE Risk_Paneli SHALL mevcut şablon seçimi ve aylık kWh giriş alanlarını göstermelidir
4. WHEN "Gerçek T1/T2/T3" seçildiğinde, THE Risk_Paneli SHALL şablon seçimini gizlemeli ve T1, T2, T3 kWh giriş alanlarını göstermelidir
5. WHEN giriş modu değiştirildiğinde, THE Risk_Paneli SHALL önceki modun sonuçlarını temizlemeli ve yeni moda göre analizi yeniden tetiklemelidir

---

### Requirement 2: T1/T2/T3 kWh Giriş Alanları

**User Story:** As a enerji satış uzmanı, I want to müşterinin faturasındaki Gündüz, Puant ve Gece kWh değerlerini ayrı ayrı girmek, so that gerçek tüketim dağılımına dayalı analiz yapabilirim.

#### Acceptance Criteria

1. WHEN T1T2T3_Modu seçildiğinde, THE Risk_Paneli SHALL üç ayrı sayısal giriş alanı göstermelidir: "Gündüz / T1 (kWh)", "Puant / T2 (kWh)", "Gece / T3 (kWh)"
2. THE Risk_Paneli SHALL her giriş alanı için yalnızca sıfır veya pozitif sayısal değer kabul etmelidir
3. THE Risk_Paneli SHALL girilen T1, T2, T3 değerlerinin toplamını "Toplam Tüketim" olarak hesaplayıp göstermelidir
4. WHEN herhangi bir T1/T2/T3 alanı değiştirildiğinde, THE Risk_Paneli SHALL toplam tüketimi otomatik olarak güncellemelidir
5. IF üç alanın tamamı sıfır veya boş ise, THEN THE Risk_Paneli SHALL analiz butonunu devre dışı bırakmalı ve "En az bir zaman diliminde tüketim giriniz" uyarısı göstermelidir
6. THE Risk_Paneli SHALL giriş alanlarında Türkçe sayı formatını (binlik ayırıcı nokta, ondalık virgül) desteklemelidir

---

### Requirement 3: T1/T2/T3 Değerlerinden 744 Saatlik Profil Üretimi

**User Story:** As a enerji satış uzmanı, I want to girdiğim T1/T2/T3 kWh değerlerinin otomatik olarak saatlik tüketim profiline dönüştürülmesini, so that backend analiz motoru bu profili kullanarak doğru hesaplama yapabilsin.

#### Acceptance Criteria

1. WHEN T1/T2/T3 kWh değerleri ve dönem bilgisi verildiğinde, THE Profil_Üretici SHALL dönemdeki gün sayısına göre 744 saatlik (veya ilgili ayın saat sayısı kadar) tüketim profili üretmelidir
2. THE Profil_Üretici SHALL T1 kWh değerini her günün 06:00–16:59 saatlerine (11 saat) eşit olarak dağıtmalıdır; her saatin tüketimi = T1_kWh / (gün_sayısı × 11)
3. THE Profil_Üretici SHALL T2 kWh değerini her günün 17:00–21:59 saatlerine (5 saat) eşit olarak dağıtmalıdır; her saatin tüketimi = T2_kWh / (gün_sayısı × 5)
4. THE Profil_Üretici SHALL T3 kWh değerini her günün 22:00–05:59 saatlerine (8 saat) eşit olarak dağıtmalıdır; her saatin tüketimi = T3_kWh / (gün_sayısı × 8)
5. FOR ALL üretilen profiller, üretilen profilin toplam tüketimi (kWh) girilen T1 + T2 + T3 toplamına eşit olmalıdır (yuvarlama toleransı: ±0.01 kWh) (round-trip özelliği)
6. FOR ALL üretilen profiller, her saatin tüketim değeri sıfır veya pozitif olmalıdır
7. THE Profil_Üretici SHALL mevcut time_zones.py modülündeki classify_hour() fonksiyonunu kullanarak saat-dilim eşleştirmesi yapmalıdır

---

### Requirement 4: Şablon Modu Geriye Uyumluluk

**User Story:** As a enerji satış uzmanı, I want to mevcut şablon profili modunun aynı şekilde çalışmaya devam etmesini, so that T1/T2/T3 verisi olmayan müşteriler için sektör şablonlarını kullanabilirim.

#### Acceptance Criteria

1. WHEN Şablon_Modu seçildiğinde, THE Risk_Paneli SHALL mevcut sektör şablonu seçimi ve aylık kWh giriş alanını göstermelidir
2. WHEN Şablon_Modu seçildiğinde, THE Hesaplama_Motoru SHALL mevcut generate_hourly_consumption() fonksiyonunu kullanarak profil üretmelidir
3. THE Hesaplama_Motoru SHALL Şablon_Modu ile yapılan analizlerde mevcut API yanıt yapısını değiştirmemelidir
4. WHEN T1T2T3_Modu seçildiğinde, THE Hesaplama_Motoru SHALL şablon profilini devre dışı bırakmalı ve T1/T2/T3 tabanlı profili kullanmalıdır

---

### Requirement 5: Analiz API Entegrasyonu

**User Story:** As a enerji satış uzmanı, I want to T1/T2/T3 giriş modunda da aynı analiz endpoint'ini kullanarak sonuç almak, so that her iki modda da tutarlı analiz sonuçları görebilirim.

#### Acceptance Criteria

1. THE Hesaplama_Motoru SHALL mevcut /api/pricing/analyze endpoint'inde T1/T2/T3 tabanlı tüketim profilini kabul edecek yeni bir parametre seti desteklemelidir
2. WHEN use_template=false ve t1_kwh, t2_kwh, t3_kwh parametreleri verildiğinde, THE Hesaplama_Motoru SHALL T1/T2/T3 değerlerinden profil üretmeli ve analizi bu profille çalıştırmalıdır
3. WHEN use_template=true olduğunda, THE Hesaplama_Motoru SHALL mevcut şablon tabanlı profil üretim davranışını korumalıdır
4. THE Hesaplama_Motoru SHALL T1/T2/T3 modu ile üretilen profil için de time_zone_breakdown, loss_map, risk_score ve safe_multiplier sonuçlarını döndürmelidir
5. IF t1_kwh + t2_kwh + t3_kwh toplamı sıfır ise, THEN THE Hesaplama_Motoru SHALL açıklayıcı bir hata mesajı döndürmelidir

---

### Requirement 6: Dağıtım Bedeli Görünürlüğü

**User Story:** As a enerji satış uzmanı, I want to dağıtım bedeli hesaplama sonuçlarını Risk Analizi panelinde görmek, so that müşteriye sunacağım teklifin toplam maliyetini değerlendirebilirim.

#### Acceptance Criteria

1. WHEN analiz tamamlandığında, THE Risk_Paneli SHALL dağıtım bedeli toplamını (TL) ayrı bir satır olarak göstermelidir
2. WHEN analiz tamamlandığında, THE Risk_Paneli SHALL enerji maliyeti toplamını (TL) ayrı bir satır olarak göstermelidir
3. THE Risk_Paneli SHALL dağıtım bedeli hesaplamasında mevcut distribution_tariffs.py ve tariff_simulator.py modüllerini kullanmalıdır
4. WHEN seçili dönem 2026 Nisan veya sonrası olduğunda, THE Risk_Paneli SHALL 2026 EPDK tarife tablosundaki güncel dağıtım birim fiyatını kullanmalıdır
5. THE Risk_Paneli SHALL dağıtım birim fiyatını (TL/kWh) ve toplam dağıtım bedelini (TL) birlikte göstermelidir

---

### Requirement 7: T1/T2/T3 Dağılım Gösterimi

**User Story:** As a enerji satış uzmanı, I want to analiz sonuçlarında T1/T2/T3 tüketim dağılımını ve oranlarını görmek, so that müşterinin tüketim profilinin risk düzeyini değerlendirebilirim.

#### Acceptance Criteria

1. WHEN analiz tamamlandığında, THE Risk_Paneli SHALL her zaman dilimi için tüketim miktarını (kWh) ve yüzdesel oranını (%) göstermelidir
2. THE Risk_Paneli SHALL T1, T2, T3 dağılımını "T1: X kWh (%Y) | T2: X kWh (%Y) | T3: X kWh (%Y)" formatında göstermelidir
3. WHEN T2 (puant) tüketim oranı %40 veya üzerinde olduğunda, THE Risk_Paneli SHALL "Puant tüketim oranı yüksek — enerji maliyeti artabilir" uyarısı göstermelidir
4. WHEN T2 (puant) tüketim oranı %55 veya üzerinde olduğunda, THE Risk_Paneli SHALL "Kritik puant yoğunlaşması — fiyatlama riski yüksek" uyarısı göstermelidir
5. THE Risk_Paneli SHALL T1/T2/T3 dağılım bilgisini backend'den dönen time_zone_breakdown verisinden okumalıdır

---

### Requirement 8: Tahmini Brüt Marj Gösterimi

**User Story:** As a enerji satış uzmanı, I want to Risk Analizi panelinde tahmini brüt marjı görmek, so that seçilen katsayıdaki kârlılığı hızlıca değerlendirebilirim.

#### Acceptance Criteria

1. WHEN analiz tamamlandığında, THE Risk_Paneli SHALL tahmini brüt marj tutarını (TL) göstermelidir
2. THE Risk_Paneli SHALL brüt marj değerini backend'den dönen pricing.total_gross_margin_tl alanından okumalıdır
3. WHEN brüt marj negatif olduğunda, THE Risk_Paneli SHALL değeri kırmızı renkte göstermeli ve "Zarar" etiketi eklemelidir
4. WHEN brüt marj pozitif olduğunda, THE Risk_Paneli SHALL değeri yeşil renkte göstermelidir

---

### Requirement 9: T1/T2/T3 Profil Üretici Round-Trip Doğrulaması

**User Story:** As a geliştirici, I want to T1/T2/T3 değerlerinden üretilen profilin doğruluğunu otomatik olarak doğrulamak, so that profil üretim sürecinde veri kaybı olmadığından emin olabilirim.

#### Acceptance Criteria

1. FOR ALL geçerli T1/T2/T3 kWh değerleri ve dönem kombinasyonları, üretilen profilin T1 saatlerindeki toplam tüketim girilen T1 kWh değerine eşit olmalıdır (±0.01 kWh tolerans)
2. FOR ALL geçerli T1/T2/T3 kWh değerleri ve dönem kombinasyonları, üretilen profilin T2 saatlerindeki toplam tüketim girilen T2 kWh değerine eşit olmalıdır (±0.01 kWh tolerans)
3. FOR ALL geçerli T1/T2/T3 kWh değerleri ve dönem kombinasyonları, üretilen profilin T3 saatlerindeki toplam tüketim girilen T3 kWh değerine eşit olmalıdır (±0.01 kWh tolerans)
4. FOR ALL üretilen profiller, her kayıttaki saat değeri classify_hour() ile sınıflandırıldığında beklenen zaman dilimine ait olmalıdır (idempotence özelliği)
5. FOR ALL geçerli dönemler, üretilen profildeki kayıt sayısı dönemdeki gün sayısı × 24 değerine eşit olmalıdır
