# Requirements Document

## Introduction

Saatlik PTF/SMF bazlı müşteri fiyatlama ve risk analiz modülü. Mevcut sistemdeki aylık ortalama PTF/YEKDEM verileri yerine, EPİAŞ uzlaştırma Excel'inden saatlik PTF ve SMF verilerini yükleyerek müşterinin gerçek tüketim profili üzerinden ağırlıklı maliyet hesabı, katsayı simülasyonu, risk skoru ve teklif önerisi üreten bir hesaplama motorudur.

Mevcut sistem fatura bazlı teklif üretirken (fatura yükle → extraction → hesaplama → PDF), bu modül müşteri profili bazlı proaktif fiyatlama ve risk analizi yapar. İki sistem birbirini tamamlar: mevcut sistem geriye dönük fatura analizi, bu modül ileriye dönük teklif fiyatlama ve risk yönetimi sağlar.

## Glossary

- **PTF**: Piyasa Takas Fiyatı — EPİAŞ tarafından saatlik olarak belirlenen elektrik toptan satış fiyatı (TL/MWh)
- **SMF**: Sistem Marjinal Fiyatı — Dengeleme güç piyasasında oluşan saatlik fiyat (TL/MWh)
- **YEKDEM**: Yenilenebilir Enerji Kaynakları Destekleme Mekanizması — Aylık sabit birim bedel (TL/MWh)
- **EPİAŞ**: Enerji Piyasaları İşletme A.Ş. — Türkiye elektrik piyasası operatörü
- **Uzlaştırma_Excel**: EPİAŞ'ın aylık yayınladığı saatlik PTF ve SMF verilerini içeren Excel dosyası
- **Tüketim_Profili**: Bir müşterinin saatlik elektrik tüketim dağılımını gösteren veri seti (kWh/saat)
- **Profil_Şablonu**: Önceden tanımlanmış sektörel tüketim kalıpları (3 vardiya sanayi, ofis, otel vb.)
- **Ağırlıklı_PTF**: Müşterinin saatlik tüketimi ile saatlik PTF'nin çarpımlarının toplamının, toplam tüketime bölünmesiyle elde edilen ortalama maliyet (TL/MWh)
- **Ağırlıklı_SMF**: Müşterinin saatlik tüketimi ile saatlik SMF'nin çarpımlarının toplamının, toplam tüketime bölünmesiyle elde edilen ortalama maliyet (TL/MWh)
- **Katsayı**: Tedarikçinin enerji maliyeti üzerine uyguladığı çarpan (örn: ×1.05 = %5 marj)
- **Dengesizlik_Maliyeti**: Tedarikçinin gerçek tüketim ile öngörülen tüketim arasındaki farktan kaynaklanan ek maliyet (TL/MWh)
- **Risk_Skoru**: Müşterinin tüketim profilinin PTF dalgalanmalarına duyarlılığını gösteren sınıflandırma (Düşük/Orta/Yüksek)
- **Zarar_Haritası**: Satış fiyatının saatlik PTF+YEKDEM maliyetinin altında kaldığı saatleri gösteren analiz
- **Güvenli_Katsayı**: Çoklu ay analizinde aylık net marj dağılımının 5. persentilinde zarar etmeyen en düşük katsayı. Tek ay verisi varsa o ayın saatlik dağılımı kullanılır
- **T1**: Gündüz zaman dilimi (06:00–17:00)
- **T2**: Puant zaman dilimi (17:00–22:00)
- **T3**: Gece zaman dilimi (22:00–06:00)
- **Bayi_Komisyonu**: Bayiye ödenen komisyon tutarı, brüt marjın yüzdesi olarak hesaplanır
- **Net_Marj**: Satış gelirinden PTF maliyeti, YEKDEM, dengesizlik maliyeti ve bayi komisyonu düşüldükten sonra kalan tutar
- **Hesaplama_Motoru**: Saatlik PTF/SMF verileri ve tüketim profili üzerinden maliyet, marj ve risk hesaplamalarını yapan backend servisi
- **Parser**: EPİAŞ uzlaştırma Excel dosyasını okuyup yapılandırılmış veri modeline dönüştüren bileşen
- **Formatter**: Saatlik PTF/SMF verilerini Excel veya yapılandırılmış formata geri yazan bileşen
- **Birim Dönüşümü**: PTF/SMF/YEKDEM verileri TL/MWh birimindedir. Tüketim verileri kWh birimindedir. Saatlik maliyet hesabında: Saatlik_PTF_Maliyeti_TL = Saatlik_Tüketim_kWh × Saatlik_PTF_TL_per_MWh / 1000

## Requirements

### Requirement 1: EPİAŞ Uzlaştırma Excel Yükleme ve Ayrıştırma

**User Story:** As a enerji tedarikçisi, I want to EPİAŞ uzlaştırma Excel dosyasını sisteme yüklemek, so that saatlik PTF ve SMF verilerini müşteri fiyatlama hesaplamalarında kullanabilirim.

#### Acceptance Criteria

1. WHEN bir EPİAŞ uzlaştırma Excel dosyası yüklendiğinde, THE Parser SHALL dosyayı ayrıştırarak her satır için tarih, saat, PTF (TL/MWh), SMF (TL/MWh), para birimi, dönem ve kaynak alanlarını çıkarmalıdır
2. WHEN ayrıştırma tamamlandığında, THE Parser SHALL çıkarılan saatlik verileri hourly_market_prices tablosuna kaydetmelidir
3. WHEN bir ay 31 gün içerdiğinde, THE Parser SHALL 744 saatlik veri beklemelidir; 30 gün için 720 saat, 28 gün için 672 saat, 29 gün için 696 saat beklemelidir
4. IF yüklenen dosya beklenen EPİAŞ Excel formatına uymuyorsa, THEN THE Parser SHALL dosyayı reddetmeli ve eksik veya hatalı sütunları belirten bir hata mesajı döndürmelidir
5. IF ayrıştırılan satır sayısı ilgili ayın beklenen saat sayısıyla eşleşmiyorsa, THEN THE Parser SHALL eksik saat sayısını belirten bir uyarı mesajı üretmelidir
6. WHEN aynı dönem için tekrar Excel yüklendiğinde, THE Parser SHALL mevcut saatlik verileri yeni verilerle değiştirmelidir (upsert davranışı)
7. THE Formatter SHALL hourly_market_prices tablosundaki verileri EPİAŞ Excel formatına uygun yapılandırılmış formata geri yazabilmelidir
8. FOR ALL geçerli EPİAŞ Excel dosyaları, yükleme sonrası dışa aktarma sonrası tekrar yükleme işlemi orijinal verilerle eşdeğer sonuç üretmelidir (round-trip özelliği)

---

### Requirement 2: Saatlik Piyasa Verisi Veri Modeli

**User Story:** As a sistem geliştiricisi, I want to saatlik PTF ve SMF verilerini yapılandırılmış bir veri modelinde saklamak, so that hesaplama motoru bu verilere verimli şekilde erişebilsin.

#### Acceptance Criteria

1. THE Hesaplama_Motoru SHALL her saatlik piyasa verisi kaydını şu alanlarla saklamalıdır: id, dönem (YYYY-MM), tarih (YYYY-MM-DD), saat (0–23), ptf_tl_per_mwh, smf_tl_per_mwh, para_birimi, kaynak, oluşturulma_tarihi
2. THE Hesaplama_Motoru SHALL dönem ve tarih-saat kombinasyonu üzerinde tekil kısıt (unique constraint) uygulamalıdır
3. THE Hesaplama_Motoru SHALL dönem bazlı sorgularda indeks kullanarak 744 saatlik veriyi 200ms altında döndürmelidir
4. WHEN saatlik PTF veya SMF değeri 0 TL/MWh altında veya 50.000 TL/MWh üstünde olduğunda, THE Hesaplama_Motoru SHALL kaydı reddetmeli ve aralık dışı değer hatası döndürmelidir

---

### Requirement 3: Aylık YEKDEM Veri Modeli

**User Story:** As a sistem geliştiricisi, I want to aylık YEKDEM birim bedellerini saatlik piyasa verilerinden ayrı bir tabloda saklamak, so that YEKDEM'in aylık sabit yapısı korunarak hesaplama motorunda doğru şekilde kullanılabilsin.

#### Acceptance Criteria

1. THE Hesaplama_Motoru SHALL aylık YEKDEM verilerini hourly_market_prices tablosundan AYRI bir monthly_yekdem_prices tablosunda saklamalıdır; YEKDEM aylık sabit bir bedeldir ve saatlik piyasa verisine gömülmemelidir
2. THE Hesaplama_Motoru SHALL monthly_yekdem_prices tablosunu şu alanlarla tanımlamalıdır: id, period (YYYY-MM), yekdem_tl_per_mwh, source, created_at, updated_at
3. THE Hesaplama_Motoru SHALL period alanı üzerinde tekil kısıt (unique constraint) uygulamalıdır
4. WHEN aynı dönem için YEKDEM verisi tekrar yüklendiğinde, THE Hesaplama_Motoru SHALL mevcut kaydı güncellemeli ve updated_at alanını yenilemelidir
5. IF yekdem_tl_per_mwh değeri 0 TL/MWh altında veya 10.000 TL/MWh üstünde olduğunda, THEN THE Hesaplama_Motoru SHALL kaydı reddetmeli ve aralık dışı değer hatası döndürmelidir

---

### Requirement 4: Müşteri Tüketim Profili Yükleme

**User Story:** As a enerji tedarikçisi, I want to müşterinin gerçek saatlik tüketim verisini Excel olarak yüklemek, so that müşteriye özel ağırlıklı maliyet hesabı yapabilirim.

#### Acceptance Criteria

1. WHEN bir müşteri tüketim Excel dosyası yüklendiğinde, THE Parser SHALL dosyayı ayrıştırarak her satır için tarih, saat ve tüketim (kWh) alanlarını çıkarmalıdır
2. WHEN ayrıştırma tamamlandığında, THE Hesaplama_Motoru SHALL tüketim verilerini müşteri kimliği ve dönem ile ilişkilendirerek saklamalıdır
3. IF yüklenen tüketim dosyası beklenen formata uymuyorsa, THEN THE Parser SHALL dosyayı reddetmeli ve format hatası mesajı döndürmelidir
4. IF herhangi bir saatlik tüketim değeri negatifse, THEN THE Parser SHALL ilgili satırı işaretlemeli ve uyarı mesajı üretmelidir
5. THE Formatter SHALL müşteri tüketim verilerini yapılandırılmış Excel formatına geri yazabilmelidir
6. FOR ALL geçerli tüketim Excel dosyaları, yükleme sonrası dışa aktarma sonrası tekrar yükleme işlemi orijinal verilerle eşdeğer sonuç üretmelidir (round-trip özelliği)

---

### Requirement 5: Profil Şablonları

**User Story:** As a enerji tedarikçisi, I want to gerçek sayaç verisi olmayan müşteriler için sektörel profil şablonu seçmek, so that yaklaşık bir tüketim profili üzerinden fiyatlama yapabilirim.

#### Acceptance Criteria

1. THE Hesaplama_Motoru SHALL şu profil şablonlarını sunmalıdır: 3 vardiya sanayi, tek vardiya fabrika, ofis, otel, restoran, soğuk hava deposu, gece ağırlıklı üretim, AVM, akaryakıt istasyonu, market/süpermarket, hastane, tarımsal sulama
2. WHEN bir profil şablonu seçildiğinde, THE Hesaplama_Motoru SHALL şablonun 24 saatlik normalize edilmiş tüketim dağılımını (her saat için 0.0–1.0 arası ağırlık) ve toplam aylık tüketim (kWh) parametresini kullanarak saatlik tüketim serisi üretmelidir
3. THE Hesaplama_Motoru SHALL her profil şablonunun 24 saatlik ağırlıklarının toplamını 1.0 olarak normalize etmelidir
4. WHEN kullanıcı özel bir profil şablonu tanımlamak istediğinde, THE Hesaplama_Motoru SHALL 24 saatlik ağırlık dizisi ve şablon adı kabul ederek yeni şablon kaydetmelidir

---

### Requirement 6: T1/T2/T3 Zaman Dilimi Dağılım Motoru

**User Story:** As a enerji tedarikçisi, I want to tüketim ve maliyet verilerini T1 (gündüz), T2 (puant) ve T3 (gece) zaman dilimlerine göre görmek, so that hangi zaman diliminde ne kadar maliyet oluştuğunu analiz edebilirim.

#### Acceptance Criteria

1. THE Hesaplama_Motoru SHALL saatleri şu dilimlere ayırmalıdır: T1 (Gündüz) = 06:00–16:59, T2 (Puant) = 17:00–21:59, T3 (Gece) = 22:00–05:59
2. WHEN bir tüketim profili ve saatlik PTF verisi verildiğinde, THE Hesaplama_Motoru SHALL her zaman dilimi için toplam tüketim (kWh), ağırlıklı ortalama PTF (TL/MWh), ağırlıklı ortalama SMF (TL/MWh) ve toplam maliyet (TL) hesaplamalıdır
3. THE Hesaplama_Motoru SHALL T1 + T2 + T3 toplam tüketiminin genel toplam tüketime eşit olduğunu doğrulamalıdır
4. THE Hesaplama_Motoru SHALL T1 + T2 + T3 toplam maliyetinin genel toplam maliyete eşit olduğunu doğrulamalıdır (yuvarlama toleransı: ±0.01 TL)

---

### Requirement 7: Ağırlıklı PTF ve Ağırlıklı SMF Hesaplama

**User Story:** As a enerji tedarikçisi, I want to müşterinin tüketim profiline göre ağırlıklı PTF ve SMF hesaplamak, so that müşteriye özel gerçekçi enerji maliyeti belirleyebilirim.

#### Acceptance Criteria

1. WHEN bir tüketim profili ve saatlik PTF verisi verildiğinde, THE Hesaplama_Motoru SHALL Ağırlıklı_PTF değerini şu formülle hesaplamalıdır: Σ(Saatlik_Tüketim × Saatlik_PTF) / Σ(Saatlik_Tüketim)
2. WHEN bir tüketim profili ve saatlik SMF verisi verildiğinde, THE Hesaplama_Motoru SHALL Ağırlıklı_SMF değerini şu formülle hesaplamalıdır: Σ(Saatlik_Tüketim × Saatlik_SMF) / Σ(Saatlik_Tüketim)
3. IF toplam tüketim sıfır ise, THEN THE Hesaplama_Motoru SHALL sıfıra bölme hatası yerine açıklayıcı bir hata mesajı döndürmelidir
4. THE Hesaplama_Motoru SHALL ağırlıklı hesaplamalarda TL/MWh birimini kullanmalı ve sonucu iki ondalık basamağa yuvarlamalıdır
5. FOR ALL tüketim profilleri, Ağırlıklı_PTF değeri ilgili dönemin minimum saatlik PTF değerinden küçük ve maksimum saatlik PTF değerinden büyük olmamalıdır (sınır özelliği)
6. WHEN tüm saatlerde eşit tüketim olduğunda, THE Hesaplama_Motoru SHALL Ağırlıklı_PTF değerini aritmetik ortalama PTF değerine eşit hesaplamalıdır (±0.01 TL/MWh tolerans)

---

### Requirement 8: Saatlik Maliyet Hesaplama Motoru

**User Story:** As a enerji tedarikçisi, I want to her saat için baz maliyet, satış fiyatı ve marj hesaplamak, so that saatlik kârlılık analizini görebilirim.

#### Acceptance Criteria

1. WHEN saatlik PTF ve aylık YEKDEM değerleri verildiğinde, THE Hesaplama_Motoru SHALL her saat için Saatlik_Baz_Maliyet değerini şu formülle hesaplamalıdır: Saatlik_PTF + Aylık_YEKDEM
2. WHEN ağırlıklı PTF, YEKDEM ve tahmini dengesizlik maliyeti verildiğinde, THE Hesaplama_Motoru SHALL Tedarikçi_Gerçek_Maliyet değerini şu formülle hesaplamalıdır: Ağırlıklı_PTF + YEKDEM + Tahmini_Dengesizlik_Maliyeti
3. THE Hesaplama_Motoru SHALL dengesizlik maliyeti hesaplamasında şu parametreleri kabul etmelidir: forecast_error_rate (tahmini öngörü hata oranı, %, varsayılan %5), imbalance_cost_tl_per_mwh (dengesizlik birim maliyeti, TL/MWh), smf_based_imbalance_enabled (SMF bazlı dengesizlik hesabı aktif mi, boolean)
4. WHEN smf_based_imbalance_enabled=true olduğunda, THE Hesaplama_Motoru SHALL dengesizlik maliyetini şu formülle hesaplamalıdır: Dengesizlik = |Ağırlıklı_SMF − Ağırlıklı_PTF| × forecast_error_rate
5. WHEN smf_based_imbalance_enabled=false olduğunda, THE Hesaplama_Motoru SHALL dengesizlik maliyetini şu formülle hesaplamalıdır: Dengesizlik = imbalance_cost_tl_per_mwh × forecast_error_rate
6. WHEN enerji maliyeti ve katsayı verildiğinde, THE Hesaplama_Motoru SHALL Satış_Fiyatı değerini şu formülle hesaplamalıdır: Enerji_Maliyeti × Katsayı
7. WHEN satış geliri, PTF maliyeti, YEKDEM, dengesizlik maliyeti ve bayi komisyonu verildiğinde, THE Hesaplama_Motoru SHALL Net_Marj değerini şu formülle hesaplamalıdır: Satış_Geliri − PTF_Maliyeti − YEKDEM − Dengesizlik − Bayi_Komisyonu
8. THE Hesaplama_Motoru SHALL tüm parasal hesaplamaları TL cinsinden ve iki ondalık basamak hassasiyetinde yapmalıdır

---

### Requirement 9: Saat Bazlı Zarar Haritası

**User Story:** As a enerji tedarikçisi, I want to hangi saatlerde satış fiyatının maliyetin altında kaldığını görmek, so that riskli saatleri tespit edip fiyatlama stratejimi ayarlayabilirim.

#### Acceptance Criteria

1. WHEN bir katsayı ve saatlik maliyet verileri verildiğinde, THE Hesaplama_Motoru SHALL her saat için satış fiyatı ile (PTF + YEKDEM) maliyetini karşılaştırmalı ve satış fiyatının maliyetin altında kaldığı saatleri işaretlemelidir
2. THE Hesaplama_Motoru SHALL zarar haritasında her zararlı saat için tarih, saat, PTF değeri, satış fiyatı ve zarar tutarı (TL/MWh) bilgilerini içermelidir
3. THE Hesaplama_Motoru SHALL toplam zararlı saat sayısını ve toplam zarar tutarını (TL) özetlemelidir
4. THE Hesaplama_Motoru SHALL zarar haritasını T1/T2/T3 zaman dilimlerine göre gruplandırarak her dilim için zararlı saat sayısını raporlamalıdır

---

### Requirement 10: Katsayı Simülasyonu

**User Story:** As a enerji tedarikçisi, I want to farklı katsayı değerlerinde aylık kâr/zarar simülasyonu görmek, so that en uygun katsayıyı belirleyebilirim.

#### Acceptance Criteria

1. WHEN bir tüketim profili ve saatlik PTF verisi verildiğinde, THE Hesaplama_Motoru SHALL ×1.02 ile ×1.10 arasında 0.01 artışlarla her katsayı değeri için aylık toplam satış geliri, toplam maliyet, brüt marj, bayi komisyonu ve net marj hesaplamalıdır
2. THE Hesaplama_Motoru SHALL simülasyon sonuçlarını katsayı değerine göre sıralı bir tablo olarak döndürmelidir
3. THE Hesaplama_Motoru SHALL her katsayı için zararlı saat sayısını ve toplam zarar tutarını da raporlamalıdır
4. WHEN kullanıcı özel bir katsayı aralığı ve adım değeri belirttiğinde, THE Hesaplama_Motoru SHALL belirtilen aralık ve adımla simülasyon yapmalıdır

---

### Requirement 11: Minimum Güvenli Katsayı Hesaplama

**User Story:** As a enerji tedarikçisi, I want to %95 güven düzeyinde zarar etmeme eşiğini bulmak, so that müşteriye güvenli bir katsayı önerebilirim.

#### Acceptance Criteria

1. WHEN bir tüketim profili, saatlik PTF verisi ve maliyet parametreleri verildiğinde, THE Hesaplama_Motoru SHALL çoklu ay analizinde aylık net marj dağılımının 5. persentilinde zarar etmeyen en düşük katsayı değerini hesaplamalıdır
2. IF yalnızca tek ay verisi mevcutsa, THEN THE Hesaplama_Motoru SHALL o ayın saatlik dağılımını kullanarak güvenli katsayıyı hesaplamalıdır
3. THE Hesaplama_Motoru SHALL güvenli katsayı hesaplamasında YEKDEM, tahmini dengesizlik maliyeti ve bayi komisyonunu dahil etmelidir
4. THE Hesaplama_Motoru SHALL güvenli katsayı değerini üç ondalık basamak hassasiyetinde (örn: ×1.057) döndürmelidir
5. IF hesaplanan güvenli katsayı ×1.10 üzerindeyse, THEN THE Hesaplama_Motoru SHALL "Bu profil için ×1.10 altında güvenli katsayı bulunamadı" uyarısı üretmelidir

---

### Requirement 12: Profil Risk Skoru

**User Story:** As a enerji tedarikçisi, I want to müşterinin tüketim profilinin risk seviyesini görmek, so that fiyatlama kararlarımı risk düzeyine göre ayarlayabilirim.

#### Acceptance Criteria

1. THE Hesaplama_Motoru SHALL her tüketim profili için Düşük, Orta veya Yüksek risk sınıflandırması üretmelidir
2. THE Hesaplama_Motoru SHALL risk skorunu şu faktörlere göre hesaplamalıdır: tüketimin yüksek PTF saatlerine yoğunlaşma oranı, T2 (puant) dilimindeki tüketim payı ve ağırlıklı PTF ile aritmetik ortalama PTF arasındaki fark oranı
3. WHEN ağırlıklı PTF, aritmetik ortalama PTF'den %5'ten fazla yüksekse, THE Hesaplama_Motoru SHALL risk skorunu Yüksek olarak belirlemelidir
4. WHEN ağırlıklı PTF, aritmetik ortalama PTF'den %2 ile %5 arasında yüksekse, THE Hesaplama_Motoru SHALL risk skorunu Orta olarak belirlemelidir
5. WHEN ağırlıklı PTF, aritmetik ortalama PTF'den %2'den az yüksek veya düşükse, THE Hesaplama_Motoru SHALL risk skorunu Düşük olarak belirlemelidir

---

### Requirement 13: Teklif Uyarı Sistemi

**User Story:** As a enerji tedarikçisi, I want to riskli katsayı kullanıldığında otomatik uyarı almak, so that zararlı teklifler vermekten kaçınabilirim.

#### Acceptance Criteria

1. WHEN kullanıcının seçtiği katsayı, hesaplanan güvenli katsayının altında olduğunda, THE Hesaplama_Motoru SHALL "Bu müşteri için ×{seçilen_katsayı} riskli. Minimum güvenli katsayı: ×{güvenli_katsayı}. Önerilen: ×{önerilen_katsayı}" formatında uyarı mesajı üretmelidir
2. THE Hesaplama_Motoru SHALL önerilen katsayıyı güvenli katsayının bir üst 0.01 adımına yuvarlayarak belirlemelidir (örn: güvenli katsayı ×1.057 ise önerilen ×1.06)
3. WHEN kullanıcının seçtiği katsayı güvenli katsayının üzerinde olduğunda, THE Hesaplama_Motoru SHALL uyarı mesajı üretmemelidir
4. THE Hesaplama_Motoru SHALL uyarı mesajını risk skoru (Düşük/Orta/Yüksek) bilgisiyle birlikte döndürmelidir

---

### Requirement 14: Bayi Komisyon Entegrasyonu

**User Story:** As a enerji tedarikçisi, I want to bayi komisyonunu net marj hesabına dahil etmek, so that gerçek kârlılığı doğru görebilirim.

#### Acceptance Criteria

1. WHEN bayi komisyon yüzdesi verildiğinde, THE Hesaplama_Motoru SHALL bayi komisyonunu brüt marjın belirtilen yüzdesi olarak hesaplamalıdır
2. THE Hesaplama_Motoru SHALL Net_Marj değerini şu formülle hesaplamalıdır: Brüt_Marj − Bayi_Komisyonu
3. THE Hesaplama_Motoru SHALL bayi komisyon yüzdesini 0 ile 100 arasında kabul etmelidir
4. IF bayi komisyon yüzdesi belirtilmemişse, THEN THE Hesaplama_Motoru SHALL bayi komisyonunu 0 olarak varsaymalıdır
5. THE Hesaplama_Motoru SHALL katsayı simülasyonunda her katsayı için bayi komisyonu dahil net marjı hesaplamalıdır

---

### Requirement 15: Çoklu Ay Karşılaştırma Analizi

**User Story:** As a enerji tedarikçisi, I want to birden fazla ay için analiz sonuçlarını karşılaştırmak, so that mevsimsel trendleri ve maliyet değişimlerini görebilirim.

#### Acceptance Criteria

1. WHEN birden fazla dönem seçildiğinde, THE Hesaplama_Motoru SHALL her dönem için ağırlıklı PTF, ağırlıklı SMF, toplam maliyet, net marj ve risk skoru hesaplamalıdır
2. THE Hesaplama_Motoru SHALL dönemler arası karşılaştırmada her metrik için değişim yüzdesini hesaplamalıdır
3. THE Hesaplama_Motoru SHALL en az 2, en fazla 12 dönem için karşılaştırma analizi yapabilmelidir
4. IF seçilen dönemlerden biri için saatlik PTF verisi yoksa, THEN THE Hesaplama_Motoru SHALL eksik dönemleri belirten bir uyarı mesajı döndürmeli ve mevcut dönemlerle analizi tamamlamalıdır

---

### Requirement 16: Fiyatlama Analiz API'si

**User Story:** As a frontend geliştiricisi, I want to fiyatlama ve risk analiz sonuçlarına REST API üzerinden erişmek, so that analiz ekranını oluşturabilirim.

#### Acceptance Criteria

1. THE Hesaplama_Motoru SHALL EPİAŞ uzlaştırma Excel yükleme için POST /api/pricing/upload-market-data endpoint'i sunmalıdır
2. THE Hesaplama_Motoru SHALL müşteri tüketim Excel yükleme için POST /api/pricing/upload-consumption endpoint'i sunmalıdır
3. THE Hesaplama_Motoru SHALL tam fiyatlama analizi için POST /api/pricing/analyze endpoint'i sunmalıdır; bu endpoint tüketim profili (veya şablon seçimi), dönem, katsayı, bayi komisyon yüzdesi ve dengesizlik maliyeti parametrelerini kabul etmelidir
4. THE Hesaplama_Motoru SHALL katsayı simülasyonu için POST /api/pricing/simulate endpoint'i sunmalıdır
5. THE Hesaplama_Motoru SHALL çoklu ay karşılaştırma için POST /api/pricing/compare endpoint'i sunmalıdır
6. THE Hesaplama_Motoru SHALL profil şablonları listesi için GET /api/pricing/templates endpoint'i sunmalıdır
7. THE Hesaplama_Motoru SHALL yüklü dönemlerin listesi için GET /api/pricing/periods endpoint'i sunmalıdır
8. IF API isteğinde zorunlu parametre eksikse, THEN THE Hesaplama_Motoru SHALL HTTP 422 durum kodu ve eksik parametreleri belirten hata mesajı döndürmelidir

---

### Requirement 17: Analiz Sonucu Rapor Çıktısı

**User Story:** As a enerji tedarikçisi, I want to analiz sonuçlarını PDF veya Excel olarak indirmek, so that müşteriye profesyonel bir teklif raporu sunabilirim.

#### Acceptance Criteria

1. WHEN analiz tamamlandığında, THE Hesaplama_Motoru SHALL sonuçları PDF formatında dışa aktarabilmelidir
2. WHEN analiz tamamlandığında, THE Hesaplama_Motoru SHALL sonuçları Excel formatında dışa aktarabilmelidir
3. THE Hesaplama_Motoru SHALL raporda şu bölümleri içermelidir: müşteri bilgileri, dönem özeti, ağırlıklı PTF/SMF, T1/T2/T3 dağılımı, katsayı simülasyonu tablosu, risk skoru, güvenli katsayı önerisi ve zarar haritası özeti
4. THE Hesaplama_Motoru SHALL PDF raporunda şirket logosu ve başlık bilgilerini mevcut teklif PDF şablonuyla tutarlı şekilde kullanmalıdır

---

### Requirement 18: Veri Kalite Raporu

**User Story:** As a enerji tedarikçisi, I want to yüklenen piyasa ve tüketim verilerinin kalite raporunu görmek, so that hatalı veya eksik verilerle analiz yapmaktan kaçınabilirim.

#### Acceptance Criteria

1. WHEN piyasa verisi veya tüketim verisi yüklendiğinde, THE Hesaplama_Motoru SHALL yüklenen veri için otomatik kalite kontrolü yapmalıdır
2. THE Hesaplama_Motoru SHALL şu kalite kontrollerini gerçekleştirmelidir: eksik saatler, mükerrer saatler, negatif tüketim değerleri, aykırı PTF/SMF değerleri (>3σ) ve sıfır tüketimli saatler
3. THE Hesaplama_Motoru SHALL her kalite kontrolü sonucunda 0–100 arası bir kalite skoru döndürmelidir
4. THE Hesaplama_Motoru SHALL tespit edilen sorunları tür, saat, değer ve açıklama bilgileriyle listeleyerek döndürmelidir
5. IF kalite skoru 80'in altındaysa, THEN THE Hesaplama_Motoru SHALL kullanıcıya veri kalitesi uyarısı göstermelidir

---

### Requirement 19: Yetki ve Erişim Kontrolü

**User Story:** As a sistem yöneticisi, I want to piyasa verisi yükleme işlemini yalnızca yetkili kullanıcılarla sınırlamak, so that veri bütünlüğünü koruyabilirim.

#### Acceptance Criteria

1. THE Hesaplama_Motoru SHALL piyasa verisi yükleme (upload-market-data) işlemini yalnızca admin veya operations rolüne sahip kullanıcılara izin vermelidir
2. THE Hesaplama_Motoru SHALL tüketim verisi yükleme (upload-consumption) işlemini kimliği doğrulanmış tüm kullanıcılara izin vermelidir
3. THE Hesaplama_Motoru SHALL analiz sonuçlarına erişimi kimliği doğrulanmış tüm kullanıcılara izin vermelidir
4. IF yetkisiz bir kullanıcı kısıtlı bir endpoint'e erişmeye çalışırsa, THEN THE Hesaplama_Motoru SHALL HTTP 403 durum kodu ve yetki hatası mesajı döndürmelidir

---

### Requirement 20: Veri Versiyonlama

**User Story:** As a enerji tedarikçisi, I want to aynı dönem için tekrar veri yüklendiğinde önceki versiyonun arşivlenmesini, so that geçmiş verilere ihtiyaç duyduğumda erişebilirim.

#### Acceptance Criteria

1. WHEN aynı dönem için piyasa verisi veya tüketim verisi tekrar yüklendiğinde, THE Hesaplama_Motoru SHALL önceki versiyonu arşivde saklamalıdır
2. THE Hesaplama_Motoru SHALL her dönem için yükleme geçmişini (versiyon numarası, yükleme tarihi, yükleyen kullanıcı) listeleyebilmelidir
3. THE Hesaplama_Motoru SHALL varsayılan olarak en son yüklenen versiyonu aktif olarak kullanmalı, önceki versiyonları arşivlenmiş olarak işaretlemelidir
4. THE Hesaplama_Motoru SHALL arşivlenmiş versiyonların görüntülenmesine izin vermeli ancak analiz hesaplamalarında yalnızca aktif versiyonu kullanmalıdır

---

### Requirement 21: Analiz Cache

**User Story:** As a sistem geliştiricisi, I want to aynı müşteri/dönem/katsayı kombinasyonu için analiz sonuçlarını önbelleğe almak, so that tekrarlayan hesaplamalarda performansı artırabilirim.

#### Acceptance Criteria

1. THE Hesaplama_Motoru SHALL aynı müşteri, dönem ve katsayı kombinasyonu için analiz sonuçlarını önbelleğe almalıdır
2. WHEN ilgili müşterinin tüketim verisi veya dönemin piyasa verisi güncellendiğinde, THE Hesaplama_Motoru SHALL ilgili önbellek kayıtlarını geçersiz kılmalıdır (cache invalidation)
3. THE Hesaplama_Motoru SHALL önbellek süresini (TTL) yapılandırılabilir olarak sunmalı ve varsayılan değeri 24 saat olarak belirlemelidir
4. THE Hesaplama_Motoru SHALL önbellek isabet oranını (cache hit rate) izlenebilir bir metrik olarak sunmalıdır
