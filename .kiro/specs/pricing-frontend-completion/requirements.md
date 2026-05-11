# Gereksinimler Dokümanı

## Giriş

Bu doküman, `pricing-consistency-fixes` bugfix spec'inin tamamlanması için gereken zorunlu frontend işlerini kapsar. Backend tarafı (8 görev) tamamlanmıştır ancak spec kapanamamaktadır çünkü 4 zorunlu frontend görevi kalmıştır. Bu görevler yeni UI davranışı eklediği için ayrı bir feature spec olarak takip edilmektedir.

Kapsam:
1. TypeScript nullability hatalarının giderilmesi (build/typecheck geçmeli)
2. Risk Buffer UI — `base_margin_pct`, `risk_buffer_pct`, `recommended_margin_pct` alanlarının ayrı gösterimi
3. Template API frontend entegrasyonu — `t1_pct`, `t2_pct`, `t3_pct`, `risk_level`, `risk_buffer_pct` alanlarının kullanımı
4. Frontend build/typecheck PASS — `tsc --noEmit` sıfır hata ile geçmeli

**Not:** Frontend property testleri opsiyoneldir. Manuel sanity check kullanıcı tarafından gerçek fatura ile ayrıca yapılacaktır.

## Sözlük

- **Frontend**: React + TypeScript tabanlı kullanıcı arayüzü uygulaması (`frontend/src/` dizini)
- **TypeScript_Derleyici**: `tsc --noEmit` komutu ile çalıştırılan TypeScript tip kontrolü; `strict: true` modunda çalışır
- **Nullability_Hatası**: TypeScript TS18047 hata kodu; bir değişkenin `null` olabileceği durumlarda güvenli erişim yapılmadığını belirtir
- **Risk_Paneli**: Frontend'deki Risk Analizi paneli; tüketim profili, katsayı ve dönem bilgilerine göre fiyatlama risk analizi yapan UI bileşeni
- **Template_API**: Backend'deki `GET /api/pricing/templates` endpoint'i; profil şablonlarının listesini `t1_pct`, `t2_pct`, `t3_pct`, `risk_level`, `risk_buffer_pct` alanlarıyla döndürür
- **Risk_Buffer**: Şablon profilinin risk seviyesine göre önerilen katsayıya eklenecek tampon yüzdesi; `risk_buffer_pct` alanı ile ifade edilir
- **Base_Margin_Pct**: Kullanıcının seçtiği katsayıdan türetilen baz marj yüzdesi; `(katsayı - 1) × 100` formülü ile hesaplanır (örn: katsayı 1.15 → %15)
- **Recommended_Margin_Pct**: Önerilen toplam marj yüzdesi; `base_margin_pct + risk_buffer_pct` formülü ile hesaplanır
- **T1_Pct**: Gündüz zaman dilimi (06:00–16:59) tüketim dağılım yüzdesi; şablon profilinde tanımlıdır
- **T2_Pct**: Puant zaman dilimi (17:00–21:59) tüketim dağılım yüzdesi; şablon profilinde tanımlıdır
- **T3_Pct**: Gece zaman dilimi (22:00–05:59) tüketim dağılım yüzdesi; şablon profilinde tanımlıdır
- **Risk_Level**: Şablon profilinin risk sınıflandırması; `low`, `medium`, `high`, `very_high` değerlerinden birini alır
- **PricingTemplatesResponse**: Frontend'deki TypeScript arayüzü; Template_API yanıtının tip tanımını içerir
- **App_Bileşeni**: `frontend/src/App.tsx` dosyasındaki ana React bileşeni; fatura analizi, teklif hesaplama ve risk analizi panellerini barındırır

## Gereksinimler

### Gereksinim 1: TypeScript Nullability Hatalarının Giderilmesi

**Kullanıcı Hikayesi:** As a geliştirici, I want to App.tsx dosyasındaki "result is possibly null" TypeScript hatalarını gidermek, so that frontend build/typecheck sıfır hata ile geçsin.

#### Kabul Kriterleri

1. THE Frontend SHALL `App.tsx` dosyasındaki tüm TS18047 ("is possibly null") hatalarını güvenli erişim operatörleri (optional chaining `?.`), null kontrolleri veya tip daraltma (type narrowing) ile çözmelidir
2. WHEN `result` değişkeni `null` olabilecek bir bağlamda kullanıldığında, THE App_Bileşeni SHALL erişim öncesinde null kontrolü yapmalı veya optional chaining kullanmalıdır
3. THE Frontend SHALL `App.tsx` dosyasındaki TS2339 ("Property does not exist") hatasını ilgili TypeScript arayüzüne eksik alan eklenerek veya güvenli erişim ile çözmelidir
4. THE Frontend SHALL mevcut çalışma zamanı davranışını değiştirmeden yalnızca tip güvenliğini sağlamalıdır; null durumunda mevcut fallback değerleri (0, boş string vb.) korunmalıdır

---

### Gereksinim 2: Test Dosyalarındaki TypeScript Hatalarının Giderilmesi

**Kullanıcı Hikayesi:** As a geliştirici, I want to test dosyalarındaki TypeScript tip hatalarını gidermek, so that tüm frontend kaynak kodunda `tsc --noEmit` sıfır hata ile geçsin.

#### Kabul Kriterleri

1. THE Frontend SHALL `market-prices/__tests__/` dizinindeki tüm TS2339 ("Property does not exist") hatalarını çözmelidir; bu hatalar `toBeInTheDocument`, `toHaveAttribute`, `toHaveValue`, `toHaveTextContent`, `toBeDisabled` gibi DOM test matcher'larının tip tanımlarının eksikliğinden kaynaklanmaktadır
2. WHEN test dosyaları Vitest ve `@testing-library` kullanıyorsa, THE Frontend SHALL uygun tip tanım dosyalarını (`@testing-library/jest-dom` veya eşdeğeri) TypeScript yapılandırmasına eklemelidir
3. THE Frontend SHALL test dosyalarındaki tip düzeltmelerinin mevcut test davranışını değiştirmemesini sağlamalıdır

---

### Gereksinim 3: Risk Buffer UI Gösterimi

**Kullanıcı Hikayesi:** As a enerji satış uzmanı, I want to Risk Analizi panelinde baz marj, risk tamponu ve önerilen marj yüzdelerini ayrı ayrı görmek, so that seçtiğim katsayının risk tamponunu karşılayıp karşılamadığını değerlendirebilirim.

#### Kabul Kriterleri

1. WHEN Risk_Paneli etkinleştirildiğinde ve bir şablon profili seçildiğinde, THE Risk_Paneli SHALL `base_margin_pct` değerini ayrı bir satır olarak göstermelidir; bu değer `(katsayı - 1) × 100` formülü ile hesaplanır
2. WHEN Risk_Paneli etkinleştirildiğinde ve bir şablon profili seçildiğinde, THE Risk_Paneli SHALL `risk_buffer_pct` değerini Template_API yanıtından okuyarak ayrı bir satır olarak göstermelidir
3. WHEN Risk_Paneli etkinleştirildiğinde ve bir şablon profili seçildiğinde, THE Risk_Paneli SHALL `recommended_margin_pct` değerini `base_margin_pct + risk_buffer_pct` formülü ile hesaplayarak ayrı bir satır olarak göstermelidir
4. WHEN `base_margin_pct` değeri `recommended_margin_pct` değerinden düşük olduğunda, THE Risk_Paneli SHALL kullanıcıya "Seçilen katsayı önerilen marjın altında — risk tamponu karşılanmıyor" uyarısı göstermelidir
5. WHEN `risk_buffer_pct` değeri 0 olduğunda, THE Risk_Paneli SHALL risk tamponu satırını "Tampon: %0 (düşük riskli profil)" olarak göstermelidir
6. THE Risk_Paneli SHALL üç değeri şu formatta göstermelidir: "Baz Marj: %X | Risk Tamponu: %Y | Önerilen: %Z"

---

### Gereksinim 4: Template API Frontend Entegrasyonu

**Kullanıcı Hikayesi:** As a enerji satış uzmanı, I want to şablon profili seçtiğimde T1/T2/T3 dağılım yüzdelerini ve risk seviyesini görmek, so that şablonun tüketim karakteristiğini ve risk profilini anlayabilirim.

#### Kabul Kriterleri

1. THE PricingTemplatesResponse SHALL `items` dizisindeki her eleman için `t1_pct`, `t2_pct`, `t3_pct`, `risk_level` ve `risk_buffer_pct` alanlarını tip tanımında içermelidir
2. WHEN bir şablon profili seçildiğinde, THE Risk_Paneli SHALL seçilen şablonun `t1_pct`, `t2_pct`, `t3_pct` değerlerini "T1: %X | T2: %Y | T3: %Z" formatında göstermelidir
3. WHEN bir şablon profili seçildiğinde, THE Risk_Paneli SHALL seçilen şablonun `risk_level` değerini Türkçe etiketle göstermelidir: `low` → "Düşük", `medium` → "Orta", `high` → "Yüksek", `very_high` → "Çok Yüksek"
4. WHEN bir şablon profili seçildiğinde, THE Risk_Paneli SHALL seçilen şablonun `risk_buffer_pct` değerini Gereksinim 3'teki Risk Buffer UI hesaplamasında kullanmalıdır
5. THE Frontend SHALL Template_API'den dönen `t1_pct + t2_pct + t3_pct` toplamının 100'e eşit olduğunu varsaymalı ve bu değerleri doğrudan göstermelidir

---

### Gereksinim 5: Frontend Build/Typecheck Geçiş Kriteri

**Kullanıcı Hikayesi:** As a geliştirici, I want to frontend projesinin `tsc --noEmit` komutunu sıfır hata ile geçmesini, so that pricing-consistency-fixes spec'i kapatılabilsin.

#### Kabul Kriterleri

1. WHEN `tsc --noEmit` komutu `frontend/` dizininde çalıştırıldığında, THE TypeScript_Derleyici SHALL sıfır hata ile tamamlanmalıdır (çıkış kodu 0)
2. THE Frontend SHALL `tsconfig.json` dosyasındaki mevcut `strict: true` ayarını korumalıdır; strictness seviyesi düşürülerek hata gizlenmemelidir
3. THE Frontend SHALL `noUnusedLocals: true` ve `noUnusedParameters: true` ayarlarını korumalıdır
4. IF yeni tip tanım dosyaları (`@types/*`) eklenirse, THEN THE Frontend SHALL bu bağımlılıkları `devDependencies` olarak `package.json` dosyasına eklemelidir

---

### Gereksinim 6: Mevcut Davranış Korunması

**Kullanıcı Hikayesi:** As a enerji satış uzmanı, I want to mevcut fatura analizi, teklif hesaplama ve PDF indirme işlevlerinin aynı şekilde çalışmaya devam etmesini, so that günlük iş akışım kesintiye uğramasın.

#### Kabul Kriterleri

1. THE App_Bileşeni SHALL mevcut fatura yükleme, analiz ve hesaplama akışını değiştirmemelidir
2. THE App_Bileşeni SHALL mevcut dual margin hesaplama mantığını (`gross_margin_energy`, `gross_margin_total`, `net_margin`) korumalıdır
3. THE App_Bileşeni SHALL mevcut bayi komisyon puan paylaşımı modelini korumalıdır
4. THE App_Bileşeni SHALL mevcut PDF indirme ve bayi rapor oluşturma işlevlerini korumalıdır
5. THE App_Bileşeni SHALL mevcut dağıtım tarife API entegrasyonunu (cache + fallback) korumalıdır
6. THE App_Bileşeni SHALL mevcut risk flag UI davranışını (LOSS_RISK kırmızı banner, UNPROFITABLE_OFFER sarı uyarı) korumalıdır
