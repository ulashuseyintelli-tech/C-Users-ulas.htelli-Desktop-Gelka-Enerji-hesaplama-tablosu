# Implementation Plan: T1/T2/T3 Giriş Modu

## Overview

Risk Analizi paneline gerçek T1/T2/T3 kWh giriş modu eklenmesi. Backend'de request model genişletme, deterministik profil üretici fonksiyonu, API öncelik sırası ve dağıtım bedeli entegrasyonu; frontend'de mod seçici, T1/T2/T3 inputları ve risk paneli çıktı güncellemeleri yapılacaktır. Kullanıcının belirttiği sıra takip edilir.

## Tasks

- [x] 1. Backend request model + validation (T1/T2/T3 alanları ve toplam > 0 kontrolü)
  - [x] 1.1 AnalyzeRequest modeline t1_kwh, t2_kwh, t3_kwh ve voltage_level alanlarını ekle
    - `backend/app/pricing/models.py` dosyasında `AnalyzeRequest` sınıfına `t1_kwh: Optional[float] = Field(default=None, ge=0)`, `t2_kwh: Optional[float] = Field(default=None, ge=0)`, `t3_kwh: Optional[float] = Field(default=None, ge=0)` ve `voltage_level: Optional[str] = Field(default="og")` alanlarını ekle
    - Pydantic `ge=0` validasyonu ile negatif değerleri otomatik reddet
    - _Requirements: 5.1, 5.2_
  - [x] 1.2 AnalyzeRequest'e model_validator ekleyerek t1+t2+t3 > 0 kontrolü yap
    - `use_template=false` (veya None) ve t1/t2/t3 alanları verildiğinde toplam > 0 olmalı; aksi halde `ValueError("Toplam tüketim sıfır olamaz. En az bir zaman diliminde tüketim giriniz.")` fırlat
    - `use_template=true` olduğunda t1/t2/t3 alanlarını yoksay (geriye uyumluluk)
    - _Requirements: 5.5, 2.5_
  - [x] 1.3 AnalyzeRequest validasyon unit testleri yaz
    - t1+t2+t3=0 → ValidationError, negatif değer → ValidationError, geçerli değerler → OK, use_template=true + t1/t2/t3 → yoksayılır
    - _Requirements: 5.5, 5.2_

- [ ] 2. generate_t1t2t3_consumption() fonksiyonu + residual fix (deterministik, floating point düzeltme)
  - [x] 2.1 generate_t1t2t3_consumption() fonksiyonunu implement et
    - `backend/app/pricing/profile_templates.py` dosyasına yeni fonksiyon ekle
    - `calendar.monthrange(year, month)` ile dönemin gün sayısını belirle (28/29/30/31)
    - T1_hours = gün × 11, T2_hours = gün × 5, T3_hours = gün × 8
    - Her saat için `classify_hour(h)` kullanarak zone belirle, `zone_kwh / zone_total_hours` ile saatlik kWh hesapla
    - `round(value, 4)` ile yuvarla
    - Fonksiyon `db` parametresi ALMAZ — saf hesaplama fonksiyonu
    - Fonksiyon deterministik olmalı: aynı input → aynı output, rastgelelik yok
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.7_
  - [x] 2.2 Residual fix uygula — her zone'un son saatine artık ekle
    - `distributed_total = hourly_kwh * zone_total_hours` hesapla
    - `residual = zone_kwh - distributed_total` farkını bul
    - Her zone'un son saatinin `consumption_kwh` değerine residual ekle
    - Bu sayede `sum(zone_hours) == zone_kwh` TAM EŞİT olur (floating point hatası sıfırlanır)
    - _Requirements: 3.5, 9.1, 9.2, 9.3_
  - [x] 2.3 _get_or_generate_consumption() fonksiyonunu T1/T2/T3 dalı ile güncelle
    - `backend/app/pricing/router.py` dosyasında `_get_or_generate_consumption()` fonksiyonuna t1_kwh, t2_kwh, t3_kwh parametreleri ekle
    - Öncelik sırası: 1) T1/T2/T3 (use_template=false + t1/t2/t3 > 0) → 2) template → 3) DB historical
    - T1/T2/T3 dalında `generate_t1t2t3_consumption()` çağır
    - _Requirements: 5.2, 5.3, 4.4_
  - [x] 2.4 analyze() endpoint'ini T1/T2/T3 parametrelerini _get_or_generate_consumption()'a iletecek şekilde güncelle
    - `router.py` dosyasındaki `analyze()` fonksiyonunda `req.t1_kwh`, `req.t2_kwh`, `req.t3_kwh` değerlerini `_get_or_generate_consumption()` çağrısına ekle
    - _Requirements: 5.1, 5.4_

- [x] 3. Checkpoint — Backend temel fonksiyonellik doğrulaması
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Dinamik ay/saat hesaplama testleri (28/30/31 gün, PBT round-trip)
  - [x] 4.1 Property test: Per-zone round-trip (Property 1)
    - **Property 1: Per-zone round-trip (Partition Fidelity)**
    - Hypothesis ile: rastgele T1/T2/T3 kWh (her biri ≥ 0, toplam > 0) ve rastgele geçerli dönem üret
    - `generate_t1t2t3_consumption()` çağır, çıktıdaki T1/T2/T3 zone toplamlarının girişe eşit olduğunu doğrula (±0.1% tolerans)
    - **Validates: Requirements 9.1, 9.2, 9.3, 3.2, 3.3, 3.4, 3.5**
  - [x] 4.2 Property test: Record count invariant (Property 2)
    - **Property 2: Record count invariant**
    - Hypothesis ile: rastgele geçerli dönem ve T1/T2/T3 kWh üret
    - Çıktıdaki kayıt sayısının `days_in_month × 24` olduğunu doğrula
    - **Validates: Requirements 9.5, 3.1**
  - [x] 4.3 Property test: Non-negative output invariant (Property 3)
    - **Property 3: Non-negative output invariant**
    - Hypothesis ile: rastgele T1/T2/T3 kWh (her biri ≥ 0) üret
    - Tüm kayıtlarda `consumption_kwh ≥ 0` olduğunu doğrula
    - **Validates: Requirements 3.6**
  - [x] 4.4 Property test: Zone classification consistency (Property 4)
    - **Property 4: Zone classification consistency**
    - Hypothesis ile: rastgele profil üret, her kaydın `classify_hour(hour)` sonucunun beklenen zone ile tutarlı olduğunu doğrula
    - **Validates: Requirements 9.4, 3.7**
  - [x] 4.5 Property test: Determinism (Property 8)
    - **Property 8: Determinism**
    - Hypothesis ile: aynı argümanlarla iki kez çağır, çıktıların birebir aynı olduğunu doğrula
    - **Validates: Design constraint (deterministic guarantee)**
  - [x] 4.6 Property test: Residual fix exactness (Property 9)
    - **Property 9: Residual fix exactness**
    - Hypothesis ile: rastgele T1/T2/T3 kWh üret, per-zone toplamın girişe TAM EŞİT olduğunu doğrula (tolerans yok, `==` kontrolü)
    - **Validates: Design constraint (residual fix), Requirements 9.1, 9.2, 9.3**
  - [x] 4.7 Unit testler: Dinamik ay/saat hesaplama örnekleri
    - Şubat 2024 (artık yıl, 29 gün) → 696 kayıt, T1_hours=29×11=319, T2_hours=29×5=145, T3_hours=29×8=232
    - Şubat 2025 (28 gün) → 672 kayıt, T1_hours=28×11=308, T2_hours=28×5=140, T3_hours=28×8=224
    - Nisan 2026 (30 gün) → 720 kayıt, T1_hours=30×11=330, T2_hours=30×5=150, T3_hours=30×8=240
    - Ocak 2026 (31 gün) → 744 kayıt, T1_hours=31×11=341, T2_hours=31×5=155, T3_hours=31×8=248
    - Tek zone testi: T1=10000, T2=0, T3=0 → sadece T1 saatlerinde tüketim
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 9.5_

- [x] 5. API priority order testleri (T1/T2/T3 > template > DB)
  - [x] 5.1 API öncelik sırası integration testleri yaz
    - Test 1: T1/T2/T3 + template params birlikte gönderildiğinde → T1/T2/T3 öncelikli (template yoksayılır)
    - Test 2: use_template=true + template_name + template_monthly_kwh → mevcut şablon davranışı korunur
    - Test 3: use_template=false + t1/t2/t3 = 0 → HTTP 422 hata
    - Test 4: use_template=true + t1/t2/t3 verilmiş → template öncelikli (t1/t2/t3 yoksayılır)
    - Test 5: Şablon modu geriye uyumluluk — mevcut API yanıt yapısı değişmez
    - _Requirements: 5.1, 5.2, 5.3, 4.1, 4.2, 4.3_

- [x] 6. Checkpoint — Backend tamamlandı, API testleri geçiyor
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Dağıtım bedeli (voltage_level AG/OG) entegrasyonu
  - [x] 7.1 voltage_level parametresini analiz akışına entegre et
    - `router.py` analyze endpoint'inde `req.voltage_level` değerini dağıtım bedeli hesaplamasına aktar
    - Mevcut `distribution_tariffs.py` modülündeki `get_distribution_unit_price()` fonksiyonunu kullanarak AG/OG'ye göre birim fiyat belirle
    - Dağıtım bedeli = toplam_kwh × birim_fiyat (TL/kWh)
    - _Requirements: 6.3, 6.4, 6.5_
  - [x] 7.2 Dağıtım bedeli unit testleri yaz
    - AG vs OG dağıtım bedeli farkı doğrulaması
    - 2026 Nisan ve sonrası dönemler için güncel EPDK tarife tablosu kullanımı
    - _Requirements: 6.3, 6.4_

- [ ] 8. Frontend mode toggle + T1/T2/T3 inputları
  - [x] 8.1 Giriş modu seçici (segmented control) ekle
    - `frontend/src/App.tsx` dosyasında Risk Paneli bölümüne `inputMode` state ekle: `'template' | 't1t2t3'`
    - Varsayılan mod: `'template'` (Şablon Profili)
    - İki seçenekli segmented control: "Şablon Profili" / "Gerçek T1/T2/T3"
    - Mod değiştiğinde önceki sonuçları temizle (`setRiskResult(null)`)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_
  - [x] 8.2 T1/T2/T3 kWh giriş alanları ekle
    - Üç sayısal input: "Gündüz / T1 (kWh)", "Puant / T2 (kWh)", "Gece / T3 (kWh)"
    - Mevcut `parseNumber()` / `formatNumber()` fonksiyonlarını kullanarak Türkçe sayı formatı desteği
    - Otomatik toplam hesaplama: `totalKwh = t1Kwh + t2Kwh + t3Kwh` — ayrı satırda göster
    - Tümü sıfır/boş ise analiz butonu disabled + "En az bir zaman diliminde tüketim giriniz" uyarısı
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
  - [x] 8.3 Gerilim seviyesi (AG/OG) seçici ekle
    - T1/T2/T3 modunda dropdown veya radio: "OG (Orta Gerilim)" / "AG (Alçak Gerilim)"
    - Varsayılan: OG
    - _Requirements: 6.3_
  - [x] 8.4 runRiskAnalysis() fonksiyonunu T1/T2/T3 modunu destekleyecek şekilde güncelle
    - `inputMode === 't1t2t3'` ise: `use_template: false`, `t1_kwh`, `t2_kwh`, `t3_kwh`, `voltage_level` parametrelerini gönder
    - `inputMode === 'template'` ise: mevcut davranış korunur
    - _Requirements: 5.1, 5.2, 5.3_
  - [x] 8.5 PricingAnalyzeRequest TypeScript interface'ini güncelle
    - `frontend/src/api.ts` dosyasında `PricingAnalyzeRequest` interface'ine `t1_kwh?`, `t2_kwh?`, `t3_kwh?`, `voltage_level?` alanlarını ekle
    - _Requirements: 5.1_

- [ ] 9. Risk paneli çıktı güncellemeleri (T1/T2/T3 dağılım, dağıtım bedeli, brüt marj, puant uyarısı)
  - [x] 9.1 T1/T2/T3 dağılım gösterimini ekle
    - Backend'den dönen `time_zone_breakdown` verisini kullanarak: "T1: X kWh (%Y) | T2: X kWh (%Y) | T3: X kWh (%Y)" formatında göster
    - _Requirements: 7.1, 7.2, 7.5_
  - [x] 9.2 Puant risk uyarılarını ekle
    - T2 tüketim oranı ≥ %40: ⚠️ "Puant tüketim oranı yüksek — enerji maliyeti artabilir"
    - T2 tüketim oranı ≥ %55: 🔴 "Kritik puant yoğunlaşması — fiyatlama riski yüksek"
    - _Requirements: 7.3, 7.4_
  - [x] 9.3 Dağıtım bedeli satırını ekle
    - "Dağıtım Bedeli (OG/AG): X.XX TL/kWh × Y kWh = Z.ZZ TL" formatında göster
    - Enerji maliyeti toplamını ayrı satır olarak göster
    - _Requirements: 6.1, 6.2, 6.5_
  - [x] 9.4 Brüt marj gösterimini ekle
    - Backend'den dönen `pricing.total_gross_margin_tl` alanını göster
    - Pozitif → yeşil renk, Negatif → kırmızı renk + "Zarar" etiketi
    - Brüt Marj = Satış Fiyatı - (PTF + YEKDEM + Dağıtım Bedeli)
    - _Requirements: 8.1, 8.2, 8.3, 8.4_
  - [ ]* 9.5 Property test: Peak warning threshold correctness (Property 5)
    - **Property 5: Peak warning threshold correctness**
    - fast-check ile: rastgele T1/T2/T3 kWh üret, T2 yüzdesine göre doğru uyarının gösterildiğini doğrula
    - T2 ≥ %55 → kritik uyarı, %40 ≤ T2 < %55 → standart uyarı, T2 < %40 → uyarı yok
    - **Validates: Requirements 7.3, 7.4**
  - [ ]* 9.6 Property test: Auto-total computation (Property 6)
    - **Property 6: Auto-total computation**
    - fast-check ile: rastgele üç non-negative sayı üret, toplamın T1+T2+T3'e tam eşit olduğunu doğrula
    - **Validates: Requirements 2.3, 2.4**
  - [ ]* 9.7 Property test: Turkish number format round-trip (Property 7)
    - **Property 7: Turkish number format round-trip**
    - fast-check ile: rastgele non-negative sayı üret, formatNumber → parseNumber round-trip'in orijinal değeri koruduğunu doğrula
    - **Validates: Requirements 2.6**

- [x] 10. Checkpoint — Frontend tamamlandı, tüm bileşenler entegre
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. End-to-end analiz testi
  - [x] 11.1 End-to-end integration testleri yaz
    - Test 1: T1/T2/T3 modunda tam analiz akışı — T1=5000, T2=3000, T3=3000, period=2026-04 → 200 OK, time_zone_breakdown + pricing + risk_score + loss_map mevcut
    - Test 2: Şablon modunda geriye uyumluluk — use_template=true → mevcut yanıt yapısı korunur
    - Test 3: Brüt marj formülü doğrulaması — Brüt Marj = Satış - (PTF + YEKDEM + Dağıtım) 
    - Test 4: Dağıtım bedeli AG vs OG farkı — aynı tüketim, farklı voltage_level → farklı dağıtım bedeli
    - Test 5: T2 puant uyarı eşikleri — %40 ve %55 sınırlarında doğru uyarı
    - _Requirements: 5.4, 6.1, 6.3, 7.3, 7.4, 8.1, 4.3_

- [x] 12. Final checkpoint — Tüm testler geçiyor, feature tamamlandı
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties (Hypothesis for Python, fast-check for TypeScript)
- Unit tests validate specific examples and edge cases
- `generate_t1t2t3_consumption()` MUST be deterministic — same input → same output, no randomness
- Residual fix ensures exact round-trip: `sum(zone_hours) == zone_kwh` with zero floating point error
- API priority order is LOCKED: T1/T2/T3 > template > DB historical
- Brüt Marj = Satış Fiyatı - (PTF + YEKDEM + Dağıtım Bedeli)
- v1: uniform distribution (no weekday/weekend), v2: weekday/weekend weighting (future)
