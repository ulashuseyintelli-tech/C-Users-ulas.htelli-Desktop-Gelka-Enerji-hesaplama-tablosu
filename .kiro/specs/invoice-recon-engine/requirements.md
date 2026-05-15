# Requirements Document

## Introduction

Fatura Mutabakat Motoru (Invoice Reconciliation Engine) — Phase 1. Dağıtım portalından indirilen saatlik tüketim Excel dosyalarını parse eder, aylara böler, T1/T2/T3 hesaplar ve fatura değerleriyle karşılaştırarak mutabakat özeti üretir. Saatlik PTF verileriyle (`hourly_market_prices` — SoT) maliyet hesaplaması yaparak mevcut tedarikçi faturası ile Gelka teklifi arasındaki farkı ortaya koyar.

**Kapsam (Phase 1):** Excel parser, aylık bölme, T1/T2/T3 hesaplama, fatura doğrulama, mutabakat özeti.
**Kapsam dışı (Phase 2+):** OCR/fatura görsel okuma, otomatik fatura PDF parse, FE entegrasyonu.

## Glossary

- **Recon_Engine**: Saatlik tüketim Excel verisi ile fatura bilgilerini karşılaştıran mutabakat motoru
- **Format_A**: Büyük tüketici dağıtım portalı Excel formatı; kolonlar: "Profil Tarihi", "Tüketim (Çekiş)", "Çarpan" (metadata — değerler zaten nihai, çarpan uygulanmış)
- **Format_B**: Küçük tüketici dağıtım portalı Excel formatı; kolonlar: "Tarih", "Aktif Çekiş" (çarpan kolonu yok)
- **Tarih_Formatı**: Her iki formatta da DD/MM/YYYY HH:MM:SS string tarih formatı
- **kWh_Değer**: String olarak yazılmış kWh tüketim değeri (Türkçe format: virgül ondalık ayırıcı olabilir)
- **T1**: Gündüz zaman dilimi; 06:00–16:59 arası (günde 11 saat)
- **T2**: Puant zaman dilimi; 17:00–21:59 arası (günde 5 saat)
- **T3**: Gece zaman dilimi; 22:00–05:59 arası (günde 8 saat)
- **Fatura_Dönemi**: Bir faturanın kapsadığı ay (YYYY-MM formatında)
- **Mutabakat_Özeti**: Excel tüketim toplamları ile fatura beyan değerleri arasındaki karşılaştırma raporu
- **PTF**: Piyasa Takas Fiyatı (TL/MWh); `hourly_market_prices` tablosundan saatlik olarak okunur
- **YEKDEM**: Yenilenebilir Enerji Kaynakları Destekleme Mekanizması bedeli (TL/MWh); `monthly_yekdem_prices` tablosundan aylık okunur
- **Dağıtım_Bedeli**: EPDK tarafından belirlenen dağıtım sistemi kullanım bedeli (TL/kWh)
- **Birim_Fiyat**: Tedarikçinin faturada uyguladığı enerji birim fiyatı (TL/kWh)
- **İskonto**: Tedarikçinin uyguladığı yüzdesel indirim oranı
- **Tarife_Grubu**: Müşterinin bağlı olduğu tarife (ör. SANAYİ OG TEK TERİM)
- **Gelka_Teklifi**: Gelka'nın saatlik PTF + YEKDEM + dağıtım + marj ile hesapladığı alternatif maliyet

## Requirements

### Requirement 1: Excel Dosya Yükleme ve Format Algılama

**User Story:** As a enerji satış uzmanı, I want to dağıtım portalından indirdiğim saatlik tüketim Excel dosyasını yüklemek ve formatın otomatik algılanmasını, so that farklı portal formatlarını manuel ayar yapmadan işleyebilirim.

#### Acceptance Criteria

1. WHEN bir Excel dosyası (.xlsx veya .xls) yüklendiğinde, THE Recon_Engine SHALL dosyayı başarıyla okuyabilmelidir
2. THE Recon_Engine SHALL kolon başlıklarını inceleyerek Format_A ("Profil Tarihi" + "Tüketim (Çekiş)") veya Format_B ("Tarih" + "Aktif Çekiş") formatını otomatik algılamalıdır
3. WHEN kolon başlıkları bilinen hiçbir formata uymadığında, THE Recon_Engine SHALL açıklayıcı bir hata mesajı döndürmelidir: "Tanınmayan Excel formatı. Beklenen kolonlar: [Format A] veya [Format B]"
4. THE Recon_Engine SHALL Format_A'daki "Çarpan" kolonunu metadata olarak saklamalı ancak tüketim değerlerine otomatik olarak UYGULAMAMALIDIR — değerler zaten nihai olarak kabul edilir
5. THE Recon_Engine SHALL Çarpan değerini rapor çıktısında bilgi amaçlı göstermelidir ancak hiçbir hesaplamada kullanmamalıdır
6. THE Recon_Engine SHALL boş satırları ve başlık öncesi metadata satırlarını atlayabilmelidir
7. WHEN dosya birden fazla sheet içerdiğinde, THE Recon_Engine SHALL ilk sheet'i varsayılan olarak işlemeli veya tüketim verisi içeren sheet'i otomatik algılamalıdır

---

### Requirement 2: Tarih ve Değer Parse Etme

**User Story:** As a enerji satış uzmanı, I want to Excel'deki tarih ve tüketim değerlerinin doğru parse edilmesini, so that saatlik tüketim verisi hatasız işlensin.

#### Acceptance Criteria

1. THE Recon_Engine SHALL DD/MM/YYYY HH:MM:SS formatındaki string tarihleri doğru olarak datetime nesnesine dönüştürmelidir
2. THE Recon_Engine SHALL Excel'in native datetime formatındaki hücreleri de desteklemelidir (openpyxl datetime nesneleri)
3. THE Recon_Engine SHALL kWh değerlerini string'den float'a dönüştürürken hem nokta (.) hem virgül (,) ondalık ayırıcısını desteklemelidir
4. THE Recon_Engine SHALL binlik ayırıcı olarak kullanılan nokta veya boşluk karakterlerini doğru yorumlamalıdır (ör. "1.234,56" → 1234.56)
5. WHEN bir satırda tarih veya tüketim değeri parse edilemediğinde, THE Recon_Engine SHALL o satırı hata listesine eklemeli ve işleme devam etmelidir
6. THE Recon_Engine SHALL parse edilen toplam satır sayısını, başarılı satır sayısını ve hatalı satır sayısını raporlamalıdır
7. FOR ALL parse edilen kayıtlar, saat değeri 0–23 aralığında olmalıdır

---

### Requirement 3: Çoklu Ay Bölme (Monthly Split)

**User Story:** As a enerji satış uzmanı, I want to birden fazla ay içeren Excel dosyasının otomatik olarak aylara bölünmesini, so that her fatura dönemi için ayrı mutabakat yapabileyim.

#### Acceptance Criteria

1. THE Recon_Engine SHALL parse edilen kayıtları fatura dönemine (YYYY-MM) göre gruplandırmalıdır
2. WHEN Excel dosyası tek bir ay içerdiğinde, THE Recon_Engine SHALL tek dönem olarak işlemelidir
3. WHEN Excel dosyası birden fazla ay içerdiğinde, THE Recon_Engine SHALL her ay için ayrı bir dönem özeti üretmelidir
4. THE Recon_Engine SHALL her dönem için kayıt sayısını (beklenen: gün_sayısı × 24) ve eksik saat sayısını raporlamalıdır
5. WHEN bir dönemde eksik saatler tespit edildiğinde, THE Recon_Engine SHALL eksik saat listesini uyarı olarak raporlamalıdır
6. THE Recon_Engine SHALL dönemleri kronolojik sırada (en eski → en yeni) sunmalıdır

---

### Requirement 4: T1/T2/T3 Hesaplama

**User Story:** As a enerji satış uzmanı, I want to her dönem için T1 (Gündüz), T2 (Puant), T3 (Gece) tüketim toplamlarının otomatik hesaplanmasını, so that faturadaki T1/T2/T3 değerleriyle karşılaştırabileyim.

#### Acceptance Criteria

1. THE Recon_Engine SHALL her saatlik kaydı T1 (06:00–16:59), T2 (17:00–21:59) veya T3 (22:00–05:59) zaman dilimine sınıflandırmalıdır
2. THE Recon_Engine SHALL mevcut `time_zones.py` modülündeki `classify_hour()` fonksiyonunu kullanarak saat-dilim eşleştirmesi yapmalıdır
3. THE Recon_Engine SHALL her dönem için T1 toplam kWh, T2 toplam kWh, T3 toplam kWh ve genel toplam kWh hesaplamalıdır
4. FOR ALL dönemler, T1 + T2 + T3 toplamı genel toplam kWh değerine eşit olmalıdır (±0.01 kWh tolerans)
5. THE Recon_Engine SHALL her zaman dilimi için yüzdesel dağılımı (%) hesaplamalıdır
6. THE Recon_Engine SHALL hesaplanan T1/T2/T3 değerlerini fatura girişiyle karşılaştırmak üzere döndürmelidir

---

### Requirement 5: Fatura Bilgisi Girişi

**User Story:** As a enerji satış uzmanı, I want to mevcut fatura bilgilerini (tedarikçi, tarife, birim fiyat, iskonto, T1/T2/T3 beyan değerleri) girmek, so that Excel tüketim verisiyle karşılaştırma yapılabilsin.

#### Acceptance Criteria

1. THE Recon_Engine SHALL aşağıdaki fatura bilgilerini kabul etmelidir: tedarikçi adı, tarife grubu, fatura dönemi (YYYY-MM), birim fiyat (TL/kWh), iskonto oranı (%), dağıtım birim fiyatı (TL/kWh)
2. THE Recon_Engine SHALL opsiyonel olarak faturadaki T1, T2, T3 kWh beyan değerlerini kabul etmelidir
3. THE Recon_Engine SHALL opsiyonel olarak faturadaki toplam tüketim (kWh) beyan değerini kabul etmelidir
4. THE Recon_Engine SHALL opsiyonel olarak faturadaki toplam tutar (TL) beyan değerini kabul etmelidir
5. WHEN iskonto oranı girildiğinde, THE Recon_Engine SHALL efektif birim fiyatı hesaplamalıdır: efektif_fiyat = birim_fiyat × (1 - iskonto/100)
6. THE Recon_Engine SHALL birden fazla dönem için farklı fatura bilgileri kabul edebilmelidir (dönem bazlı fatura parametreleri)

---

### Requirement 6: Tüketim Mutabakat Doğrulaması

**User Story:** As a enerji satış uzmanı, I want to Excel'den hesaplanan tüketim değerlerinin fatura beyan değerleriyle karşılaştırılmasını, so that fatura tutarsızlıklarını tespit edebileyim.

#### Acceptance Criteria

1. WHEN faturada T1/T2/T3 beyan değerleri girilmişse, THE Recon_Engine SHALL Excel'den hesaplanan T1/T2/T3 değerleriyle karşılaştırmalı ve farkı (kWh ve %) raporlamalıdır
2. WHEN faturada toplam tüketim beyan değeri girilmişse, THE Recon_Engine SHALL Excel toplam tüketimi ile karşılaştırmalı ve farkı raporlamalıdır
3. THE Recon_Engine SHALL hem yüzdesel hem mutlak tolerans eşiği desteklemelidir; varsayılan yüzdesel tolerans: ±%1, varsayılan mutlak tolerans: ±1 kWh. Bir fark her iki eşiğin de altındaysa "UYUMLU" olarak işaretlenmelidir
4. THE Recon_Engine SHALL uyumsuzluk şiddetini üç seviyede sınıflandırmalıdır: "LOW" (tolerans aşımı ≤%2 veya ≤5 kWh), "WARNING" (tolerans aşımı ≤%5 veya ≤20 kWh), "CRITICAL" (tolerans aşımı >%5 veya >20 kWh)
5. THE Recon_Engine SHALL tolerans eşiklerini konfigüre edilebilir parametreler olarak kabul etmelidir (varsayılanlar: pct_tolerance=1.0, abs_tolerance_kwh=1.0)
6. THE Recon_Engine SHALL mutabakat sonucunu dönem bazında bir özet tablosu olarak döndürmelidir; her satırda uyumsuzluk şiddeti (severity) alanı bulunmalıdır

---

### Requirement 7: Saatlik PTF ile Maliyet Hesaplama

**User Story:** As a enerji satış uzmanı, I want to Excel tüketim verisinin saatlik PTF fiyatlarıyla çarpılarak toplam enerji maliyetinin hesaplanmasını, so that piyasa bazlı gerçek maliyeti görebileyim.

#### Acceptance Criteria

1. THE Recon_Engine SHALL her saatlik tüketim kaydını `hourly_market_prices` tablosundaki ilgili saat PTF değeriyle (TL/MWh) eşleştirmelidir
2. THE Recon_Engine SHALL saatlik maliyeti hesaplamalıdır: saat_maliyet_TL = tüketim_kWh × (PTF_TL_per_MWh / 1000)
3. THE Recon_Engine SHALL dönem bazında toplam PTF maliyetini (TL) hesaplamalıdır
4. THE Recon_Engine SHALL dönem bazında ağırlıklı ortalama PTF'yi (TL/MWh) hesaplamalıdır: toplam_maliyet / toplam_tüketim × 1000
5. WHEN bir saat için PTF verisi bulunamadığında, THE Recon_Engine SHALL o saati "PTF eksik" olarak işaretlemeli ve eksik saat sayısını raporlamalıdır
6. IF dönemdeki eksik PTF oranı %10'u aşarsa, THEN THE Recon_Engine SHALL "Yetersiz PTF verisi — maliyet hesaplaması güvenilir değil" uyarısı vermelidir
7. THE Recon_Engine SHALL YEKDEM bedelini `monthly_yekdem_prices` tablosundan okuyarak toplam maliyete eklemelidir: yekdem_maliyet = toplam_kWh × (YEKDEM_TL_per_MWh / 1000)
8. IF bir dönem için saatlik PTF veya YEKDEM verisi tamamen eksikse, THEN THE Recon_Engine SHALL teklif üretimini (quote generation) engellemeli (fail-closed) ancak parse ve mutabakat raporunu yine de döndürmelidir
9. WHEN PTF/YEKDEM eksikliği nedeniyle teklif üretilemediğinde, THE Recon_Engine SHALL raporda "quote_blocked: true" ve "quote_block_reason" alanlarını doldurmalıdır

---

### Requirement 8: Fatura vs Gelka Teklifi Karşılaştırma

**User Story:** As a enerji satış uzmanı, I want to mevcut tedarikçi fatura maliyeti ile Gelka'nın piyasa bazlı teklif maliyetini yan yana görmek, so that müşteriye tasarruf potansiyelini sunabileyim.

#### Acceptance Criteria

1. THE Recon_Engine SHALL mevcut fatura maliyetini hesaplamalıdır: fatura_enerji_TL = toplam_kWh × efektif_birim_fiyat
2. THE Recon_Engine SHALL mevcut fatura dağıtım bedelini hesaplamalıdır: fatura_dagitim_TL = toplam_kWh × dagitim_birim_fiyat
3. THE Recon_Engine SHALL Gelka teklif maliyetini hesaplamalıdır: gelka_enerji_TL = PTF_maliyet + YEKDEM_maliyet + gelka_marj
4. THE Recon_Engine SHALL Gelka marjını konfigüre edilebilir bir katsayı (varsayılan: 1.05 = %5 marj) olarak uygulamalıdır: gelka_enerji_TL = (PTF_maliyet + YEKDEM_maliyet) × katsayı
5. THE Recon_Engine SHALL karşılaştırma özetini döndürmelidir: fatura toplam TL, Gelka teklif toplam TL, fark TL, fark yüzdesi (%)
6. WHEN Gelka teklifi faturadan düşükse, THE Recon_Engine SHALL "Tasarruf potansiyeli: X TL (%Y)" mesajı üretmelidir
7. WHEN Gelka teklifi faturadan yüksekse, THE Recon_Engine SHALL "Mevcut tedarikçi avantajlı: X TL (%Y)" mesajı üretmelidir

---

### Requirement 9: Mutabakat Raporu Çıktısı

**User Story:** As a enerji satış uzmanı, I want to tüm mutabakat sonuçlarını yapılandırılmış bir rapor olarak almak, so that müşteriye sunabileceğim veya iç değerlendirme yapabileceğim bir özet elde edeyim.

#### Acceptance Criteria

1. THE Recon_Engine SHALL her dönem için aşağıdaki bilgileri içeren bir mutabakat raporu üretmelidir: dönem, toplam kWh, T1/T2/T3 kWh ve %, eksik saat sayısı, mutabakat durumu, PTF maliyet, YEKDEM maliyet, fatura maliyet, Gelka teklif maliyet, fark
2. THE Recon_Engine SHALL raporu JSON formatında döndürmelidir (API response olarak)
3. THE Recon_Engine SHALL raporda parse istatistiklerini içermelidir: toplam satır, başarılı satır, hatalı satır, algılanan format
4. THE Recon_Engine SHALL raporda uyarı listesini içermelidir (eksik saatler, PTF eksikleri, kritik uyumsuzluklar)
5. THE Recon_Engine SHALL çoklu dönem raporunda dönemler arası toplam özet satırı da üretmelidir
6. FOR ALL rapor çıktıları, TL değerleri 2 ondalık basamağa yuvarlanmalıdır; kWh değerleri 3 ondalık basamağa yuvarlanmalıdır

---

### Requirement 10: Hata Yönetimi ve Sınır Durumları

**User Story:** As a geliştirici, I want to parser'ın bozuk veya beklenmeyen verilerle karşılaştığında graceful şekilde hata yönetmesini, so that sistem çökmeden kullanıcıya anlamlı geri bildirim verebilsin.

#### Acceptance Criteria

1. WHEN Excel dosyası boş olduğunda, THE Recon_Engine SHALL "Dosya boş veya tüketim verisi bulunamadı" hatası döndürmelidir
2. WHEN Excel dosyası 100.000 satırdan fazla veri içerdiğinde, THE Recon_Engine SHALL dosyayı işleyebilmeli veya bellek limiti uyarısı vermelidir
3. WHEN bir dönemde 24×gün_sayısı'ndan fazla kayıt bulunduğunda (duplike saatler), THE Recon_Engine SHALL duplike kayıtları tespit etmeli ve uyarı vermelidir
4. WHEN tüketim değeri negatif olduğunda, THE Recon_Engine SHALL o kaydı uyarı listesine eklemeli ve mutlak değerini kullanmalıdır
5. THE Recon_Engine SHALL dosya boyutu limitini 50 MB olarak uygulamalıdır; aşıldığında açıklayıcı hata döndürmelidir
6. WHEN tarih sıralaması bozuk olduğunda (kronolojik olmayan kayıtlar), THE Recon_Engine SHALL kayıtları tarih sırasına göre sıralamalı ve işlemeye devam etmelidir
7. FOR ALL hata durumları, HTTP yanıt kodu ve hata mesajı tutarlı bir error schema ile döndürülmelidir

