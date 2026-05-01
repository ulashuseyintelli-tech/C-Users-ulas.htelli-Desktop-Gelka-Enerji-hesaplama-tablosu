# Uygulama Planı: Pricing Risk Engine

## Genel Bakış

Saatlik PTF/SMF bazlı müşteri fiyatlama ve risk analiz motoru. EPİAŞ uzlaştırma Excel'inden saatlik piyasa verilerini yükleyerek, müşterinin gerçek tüketim profili üzerinden ağırlıklı maliyet hesabı, katsayı simülasyonu, güvenli katsayı önerisi, risk skoru ve detaylı analiz raporu üretir. Python/FastAPI backend, SQLite DB, modül yolu: `backend/app/pricing/`.

## Görevler

- [x] 1. Migration ve DB tabloları
  - [x] 1.1 Alembic migration dosyası oluştur — pricing modülü tabloları
    - `backend/alembic/versions/` altında yeni migration dosyası oluştur
    - `hourly_market_prices` tablosu: id, period, date, hour, ptf_tl_per_mwh, smf_tl_per_mwh, currency, source, version, is_active, created_at, updated_at + unique constraint (period, date, hour, version) + indeksler
    - `monthly_yekdem_prices` tablosu: id, period, yekdem_tl_per_mwh, source, created_at, updated_at + unique constraint (period) + indeks
    - `consumption_profiles` tablosu: id, customer_id, customer_name, period, profile_type, template_name, total_kwh, source, version, is_active, created_at, updated_at + unique constraint (customer_id, period, version) + indeksler
    - `consumption_hourly_data` tablosu: id, profile_id (FK → consumption_profiles.id ON DELETE CASCADE), date, hour, consumption_kwh + unique constraint (profile_id, date, hour) + indeks
    - `profile_templates` tablosu: id, name (unique), display_name, description, hourly_weights (JSON text), is_builtin, created_at, updated_at
    - `data_versions` tablosu: id, data_type, period, customer_id, version, uploaded_by, upload_filename, row_count, quality_score, is_active, created_at + unique constraint (data_type, period, customer_id, version) + indeks
    - `analysis_cache` tablosu: id, cache_key (unique), customer_id, period, params_hash, result_json, created_at, expires_at, hit_count + indeksler
    - CHECK constraint'leri: hour 0–23, ptf/smf 0–50000, yekdem 0–10000, total_kwh >= 0
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.5, 4.2, 20.1, 21.1_

  - [x] 1.2 SQLAlchemy ORM modelleri oluştur (`backend/app/pricing/schemas.py`)
    - `import sqlalchemy as sa` dahil edilmeli (design'daki `sa.UniqueConstraint` kullanımı için)
    - `HourlyMarketPrice`, `MonthlyYekdemPrice`, `ConsumptionProfile`, `ConsumptionHourlyData`, `ProfileTemplate`, `DataVersion`, `AnalysisCache` ORM sınıfları
    - `ConsumptionProfile` → `ConsumptionHourlyData` relationship (cascade="all, delete-orphan")
    - `from ..database import Base` import'u ile mevcut Base kullanılmalı
    - `__table_args__` ile UniqueConstraint tanımları
    - _Requirements: 2.1, 2.2, 3.2, 3.3, 4.2_

  - [x] 1.3 `backend/app/pricing/__init__.py` modül init dosyası oluştur
    - Boş `__init__.py` veya schemas import'u
    - _Requirements: 2.1_

- [x] 2. Pydantic modeller (`backend/app/pricing/models.py`)
  - [x] 2.1 Request/Response Pydantic modelleri oluştur
    - `RiskLevel` enum: Düşük, Orta, Yüksek
    - `TimeZone` enum: T1, T2, T3
    - `ImbalanceParams` model: forecast_error_rate, imbalance_cost_tl_per_mwh, smf_based_imbalance_enabled
    - `ExcelParseResult` model: success, period, total_rows, expected_hours, missing_hours, rejected_rows, warnings, quality_score
    - `ConsumptionParseResult` model: success, customer_id, period, total_rows, total_kwh, negative_hours, quality_score, profile_id
    - `WeightedPriceResult` model: weighted_ptf_tl_per_mwh, weighted_smf_tl_per_mwh, arithmetic_avg_ptf, arithmetic_avg_smf, total_consumption_kwh, total_cost_tl, hours_count
    - `HourlyCostEntry` model: date, hour, consumption_kwh, ptf_tl_per_mwh, smf_tl_per_mwh, yekdem_tl_per_mwh, base_cost_tl, sales_price_tl, margin_tl, is_loss_hour, time_zone
    - `HourlyCostResult` model: hour_costs, total_base_cost_tl, total_sales_revenue_tl, total_gross_margin_tl, total_net_margin_tl, supplier_real_cost_tl_per_mwh
    - `SimulationRow` model: multiplier, total_sales_tl, total_cost_tl, gross_margin_tl, dealer_commission_tl, net_margin_tl, loss_hours, total_loss_tl
    - `SafeMultiplierResult` model: safe_multiplier, recommended_multiplier, confidence_level, periods_analyzed, monthly_margins, warning
    - `RiskScoreResult` model: score (RiskLevel), weighted_ptf, arithmetic_avg_ptf, deviation_pct, t2_consumption_pct, peak_concentration
    - `AnalyzeRequest`, `SimulateRequest`, `CompareRequest`, `ReportRequest` API request modelleri
    - `AnalyzeResponse`, `SimulateResponse`, `CompareResponse` API response modelleri
    - Bayi komisyon yüzdesi validasyonu: 0–100 arası, varsayılan 0
    - _Requirements: 7.1, 7.4, 8.1, 8.3, 9.2, 10.1, 11.4, 12.1, 14.1, 14.3, 14.4, 16.3_

- [x] 3. Checkpoint — Migration ve modeller
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

- [x] 4. EPİAŞ Excel parser (`backend/app/pricing/excel_parser.py`)
  - [x] 4.1 `parse_epias_excel()` fonksiyonu
    - openpyxl ile Excel dosyasını oku
    - Header satırını bul (case-insensitive, Türkçe karakter toleranslı sütun eşleştirme: Tarih, Saat, PTF, SMF)
    - Satır satır ayrıştır: tarih DD.MM.YYYY → YYYY-MM-DD, saat 0–23 integer, PTF/SMF float (virgül→nokta)
    - Değer aralık kontrolü: 0 ≤ PTF ≤ 50000, 0 ≤ SMF ≤ 50000
    - Dönem çıkar: YYYY-MM
    - `expected_hours_for_period()` ile beklenen saat sayısı kontrolü (calendar.monthrange)
    - Eksik saat tespiti ve uyarı üretimi
    - Reddedilen satırlar listesi (sebep ile)
    - Kalite skoru hesaplama (0–100)
    - `ExcelParseResult` döndür
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.4, 18.1, 18.2, 18.3, 18.4_

  - [x] 4.2 `parse_consumption_excel()` fonksiyonu
    - Müşteri tüketim Excel dosyasını ayrıştır: tarih, saat, tüketim (kWh)
    - Negatif tüketim değeri tespiti ve uyarı
    - Format kontrolü ve hata mesajı
    - `ConsumptionParseResult` döndür
    - _Requirements: 4.1, 4.3, 4.4_

  - [x] 4.3 `expected_hours_for_period()` yardımcı fonksiyonu
    - `calendar.monthrange(year, month)[1] * 24` formülü
    - _Requirements: 1.3_

  - [ ]* 4.4 Property test: EPİAŞ Excel Round-Trip
    - **Property 1: EPİAŞ Excel Round-Trip**
    - **Validates: Requirements 1.1, 1.7, 1.8**

  - [ ]* 4.5 Property test: Beklenen Saat Sayısı
    - **Property 3: Beklenen Saat Sayısı Hesaplama**
    - **Validates: Requirements 1.3**

  - [ ]* 4.6 Property test: Geçersiz Excel Reddi
    - **Property 4: Geçersiz Excel Reddi**
    - **Validates: Requirements 1.4, 4.3**

- [x] 5. Excel formatter (`backend/app/pricing/excel_formatter.py`)
  - [x] 5.1 `export_market_data_to_excel()` fonksiyonu
    - DB'deki saatlik piyasa verilerini EPİAŞ Excel formatına yaz (openpyxl)
    - Tarih formatı: DD.MM.YYYY, saat: integer, PTF/SMF: float
    - _Requirements: 1.7, 1.8_

  - [x] 5.2 `export_consumption_to_excel()` fonksiyonu
    - Tüketim profilini Excel formatına yaz
    - _Requirements: 4.5, 4.6_

  - [ ]* 5.3 Property test: Tüketim Excel Round-Trip
    - **Property 2: Tüketim Excel Round-Trip**
    - **Validates: Requirements 4.1, 4.5, 4.6**

- [x] 6. YEKDEM CRUD işlemleri
  - [x] 6.1 YEKDEM CRUD fonksiyonları (`backend/app/pricing/router.py` veya ayrı servis)
    - `create_or_update_yekdem(period, yekdem_tl_per_mwh, source)` — upsert davranışı
    - `get_yekdem(period)` — dönem bazlı sorgulama
    - `list_yekdem()` — tüm dönemler listesi
    - Aralık kontrolü: 0 ≤ yekdem ≤ 10000
    - Aynı dönem için güncelleme: updated_at yenileme
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 7. Profil şablon seed datası (`backend/app/pricing/profile_templates.py`)
  - [x] 7.1 Sektörel profil şablonları tanımla ve seed fonksiyonu yaz
    - 12 yerleşik şablon: 3_vardiya_sanayi, tek_vardiya_fabrika, ofis, otel, restoran, soguk_hava_deposu, gece_agirlikli_uretim, avm, akaryakit_istasyonu, market_supermarket, hastane, tarimsal_sulama
    - Her şablon için 24 saatlik normalize ağırlık dizisi (toplam = 1.0)
    - `seed_profile_templates(db)` fonksiyonu — idempotent (varsa atla)
    - `generate_hourly_consumption(template_name, total_monthly_kwh, period)` fonksiyonu — şablondan saatlik tüketim serisi üret
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [ ]* 7.2 Property test: Profil Şablonu Ağırlık Normalizasyonu
    - **Property 5: Profil Şablonu Ağırlık Normalizasyonu ve Tüketim Üretimi**
    - **Validates: Requirements 5.2, 5.3**

- [x] 8. Checkpoint — Parser, formatter, YEKDEM, şablonlar
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

- [x] 9. Tüketim profili yükleme servisi
  - [x] 9.1 Tüketim profili DB kayıt servisi
    - Excel parse sonucunu `consumption_profiles` + `consumption_hourly_data` tablolarına kaydet
    - Müşteri kimliği ve dönem ile ilişkilendirme
    - Versiyonlama: aynı müşteri/dönem için tekrar yükleme → önceki versiyon arşivle (is_active=0), yeni versiyon is_active=1
    - `data_versions` tablosuna kayıt ekle
    - Cache invalidation: ilgili müşterinin cache kayıtlarını sil
    - _Requirements: 4.1, 4.2, 4.4, 20.1, 20.2, 20.3, 21.2_

- [x] 10. Zaman dilimi motoru (`backend/app/pricing/time_zones.py`)
  - [x] 10.1 T1/T2/T3 sınıflandırma ve dağılım fonksiyonları
    - `classify_hour(hour: int) -> TimeZone` — saat 6–16 → T1, 17–21 → T2, 22–23 veya 0–5 → T3
    - `calculate_time_zone_breakdown(hourly_prices, consumption_profile)` — her dilim için toplam tüketim, ağırlıklı PTF/SMF, toplam maliyet
    - T1+T2+T3 toplam tüketim = genel toplam tüketim doğrulaması
    - T1+T2+T3 toplam maliyet = genel toplam maliyet doğrulaması (±0.01 TL tolerans)
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [ ]* 10.2 Property test: T1/T2/T3 Zaman Dilimi Sınıflandırması
    - **Property 6: T1/T2/T3 Zaman Dilimi Sınıflandırması**
    - **Validates: Requirements 6.1**

  - [ ]* 10.3 Property test: T1/T2/T3 Bölümleme Değişmezi
    - **Property 7: T1/T2/T3 Bölümleme Değişmezi**
    - **Validates: Requirements 6.3, 6.4**

- [ ] 11. Hesaplama motoru (`backend/app/pricing/pricing_engine.py`)
  - [x] 11.1 Ağırlıklı PTF/SMF hesaplama
    - `calculate_weighted_prices(hourly_prices, consumption_profile) -> WeightedPriceResult`
    - Formül: Σ(Saatlik_Tüketim × Saatlik_PTF) / Σ(Saatlik_Tüketim)
    - Sıfıra bölme koruması: toplam tüketim = 0 ise açıklayıcı hata mesajı
    - Sonuç iki ondalık basamağa yuvarlanır
    - Aritmetik ortalama PTF/SMF de hesaplanır
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 11.2 Saatlik maliyet hesaplama
    - `calculate_hourly_costs(hourly_prices, consumption_profile, yekdem_tl_per_mwh, multiplier, imbalance_params, dealer_commission_pct) -> HourlyCostResult`
    - Her saat için: base_cost = (PTF + YEKDEM) × kWh / 1000
    - Satış fiyatı = Enerji_Maliyeti × Katsayı × kWh / 1000
    - Brüt marj = satış - base_cost
    - Bayi komisyonu = brüt marj × bayi yüzdesi / 100 (design'daki formül)
    - Net marj = brüt marj - bayi komisyonu - dengesizlik payı
    - Zarar saati tespiti: margin < 0 → is_loss_hour = true
    - Tedarikçi gerçek maliyet: Ağırlıklı_PTF + YEKDEM + Dengesizlik
    - Tüm parasal hesaplamalar TL, iki ondalık basamak
    - _Requirements: 8.1, 8.2, 8.6, 8.7, 8.8, 9.1, 9.2, 9.3, 14.1, 14.2_

  - [x] 11.3 Property test: Ağırlıklı Ortalama Sınır Özelliği
    - **Property 8: Ağırlıklı Ortalama Sınır Özelliği**
    - **Validates: Requirements 7.5**

  - [x] 11.4 Property test: Eşit Tüketimde Ağırlıklı Ortalama = Aritmetik Ortalama
    - **Property 9: Eşit Tüketimde Ağırlıklı Ortalama = Aritmetik Ortalama**
    - **Validates: Requirements 7.6**

  - [x] 11.5 Property test: Zarar Saati Tutarlılığı
    - **Property 10: Zarar Saati Tutarlılığı**
    - **Validates: Requirements 9.1, 9.2**

- [x] 12. Dengesizlik motoru (`backend/app/pricing/imbalance.py`)
  - [x] 12.1 Dengesizlik maliyeti hesaplama
    - `calculate_imbalance_cost(weighted_ptf, weighted_smf, params: ImbalanceParams) -> float`
    - SMF bazlı mod: |Ağırlıklı_SMF − Ağırlıklı_PTF| × forecast_error_rate
    - Sabit oran modu: imbalance_cost_tl_per_mwh × forecast_error_rate
    - _Requirements: 8.3, 8.4, 8.5_

- [x] 13. Checkpoint — Hesaplama motoru, zaman dilimleri, dengesizlik
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

- [x] 14. Katsayı simülasyonu (`backend/app/pricing/multiplier_simulator.py`)
  - [x] 14.1 Katsayı simülasyonu fonksiyonu
    - `run_simulation(hourly_prices, consumption_profile, yekdem, imbalance_params, dealer_commission_pct, multiplier_start, multiplier_end, multiplier_step) -> list[SimulationRow]`
    - Varsayılan aralık: ×1.02 – ×1.10, adım 0.01
    - Her katsayı için: toplam satış, toplam maliyet, brüt marj, bayi komisyonu, net marj, zararlı saat sayısı, toplam zarar
    - Kullanıcı özel aralık ve adım belirtebilir
    - Sonuçlar katsayıya göre sıralı
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 14.5_

  - [x] 14.2 Property test: Katsayı Simülasyonu Monotonluğu
    - **Property 11: Katsayı Simülasyonu Monotonluğu**
    - **Validates: Requirements 10.1, 10.2, 10.3**

- [ ] 15. Güvenli katsayı hesaplama
  - [x] 15.1 Güvenli katsayı algoritması (`backend/app/pricing/multiplier_simulator.py`)
    - `calculate_safe_multiplier(periods_data, yekdem, imbalance_params, dealer_commission_pct, confidence_level) -> SafeMultiplierResult`
    - **ÖNEMLİ**: Binary search'te float kayması riski nedeniyle integer step kullanılmalı: 1001–1100 arası integer tarama (1001 = ×1.001, 1100 = ×1.100), sonuçta integer / 1000 ile float'a dönüştür
    - Tek ay verisi: saatlik marj dağılımı (744 veri noktası) üzerinden 5. persentil
    - Çoklu ay verisi: aylık net marj dağılımı üzerinden 5. persentil
    - Güvenli katsayı = 5. persentilde net_margin ≥ 0 olan en düşük katsayı
    - Önerilen katsayı = ceil(safe_multiplier × 100) / 100 (bir üst 0.01 adımı)
    - ×1.10 üzeri uyarısı: "Bu profil için ×1.10 altında güvenli katsayı bulunamadı"
    - Üç ondalık basamak hassasiyeti (örn: ×1.057)
    - YEKDEM, dengesizlik ve bayi komisyonu dahil
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 13.2_

  - [x] 15.2 Property test: Güvenli Katsayı Sınır Doğrulaması
    - **Property 12: Güvenli Katsayı Sınır Doğrulaması**
    - **Validates: Requirements 11.1, 11.2, 11.3**

- [x] 16. Risk skoru (`backend/app/pricing/risk_calculator.py`)
  - [x] 16.1 Profil risk skoru hesaplama
    - `calculate_risk_score(weighted_result, time_zone_result) -> RiskScoreResult`
    - Sapma yüzdesi: (weighted_ptf - arithmetic_avg_ptf) / arithmetic_avg_ptf × 100
    - Eşikler: sapma > %5 → Yüksek, %2–%5 → Orta, < %2 → Düşük
    - T2 tüketim payı ve peak concentration hesaplama
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [x] 16.2 Teklif uyarı sistemi
    - Seçilen katsayı < güvenli katsayı → uyarı mesajı üret
    - Format: "Bu müşteri için ×{seçilen} riskli. Minimum güvenli katsayı: ×{güvenli}. Önerilen: ×{önerilen}"
    - Seçilen katsayı ≥ güvenli katsayı → uyarı yok
    - Uyarı mesajı risk skoru ile birlikte döndürülür
    - _Requirements: 13.1, 13.2, 13.3, 13.4_

  - [ ]* 16.3 Property test: Risk Skoru Eşik Tutarlılığı
    - **Property 13: Risk Skoru Eşik Tutarlılığı**
    - **Validates: Requirements 12.2, 12.3, 12.4, 12.5**

  - [ ]* 16.4 Property test: Uyarı Sistemi Tutarlılığı
    - **Property 14: Uyarı Sistemi Tutarlılığı**
    - **Validates: Requirements 13.1, 13.3**

- [x] 17. Checkpoint — Simülasyon, güvenli katsayı, risk skoru
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.
  - **Checkpoint sonucu**: 112 test geçti, 0 hata. Gerçek senaryo sanity check yapıldı.
  - **İyileştirmeler uygulandı**: (1) Risk skoruna reasons eklendi, (2) Warning'e risk seviyesi eklendi, (3) Risk/safe_multiplier tutarlılık guard'ı eklendi.

- [x] 18. API endpoint'leri (`backend/app/pricing/router.py`)
  - [x] 18.1 FastAPI router ve temel endpoint'ler oluştur
    - `pricing_router = APIRouter(prefix="/api/pricing", tags=["pricing"])`
    - `POST /api/pricing/upload-market-data` — EPİAŞ Excel yükleme (admin/operations rolü)
      - multipart/form-data file kabul
      - Parser çağır → DB'ye upsert → versiyonlama → cache invalidation
      - Başarı: period, total_rows, quality_score, version döndür
      - Hata: 422 format hatası, eksik sütunlar
    - `POST /api/pricing/upload-consumption` — Müşteri tüketim Excel yükleme
      - multipart/form-data file + customer_id + customer_name (opsiyonel)
      - Parser çağır → DB'ye kaydet → versiyonlama
    - `POST /api/pricing/analyze` — Tam fiyatlama analizi
      - customer_id, period, multiplier, dealer_commission_pct, imbalance_params kabul
      - Şablon desteği: use_template, template_name, template_monthly_kwh
      - Cache kontrolü → hesaplama → cache'e yaz → sonuç döndür
      - Ağırlıklı PTF/SMF, tedarikçi maliyeti, fiyatlama, T1/T2/T3 dağılımı, zarar haritası, risk skoru, güvenli katsayı, uyarılar, veri kalitesi
    - `POST /api/pricing/simulate` — Katsayı simülasyonu
      - customer_id, period, dealer_commission_pct, imbalance_params, multiplier_start/end/step
    - `POST /api/pricing/compare` — Çoklu ay karşılaştırma
      - customer_id, periods (2–12 dönem), multiplier, dealer_commission_pct, imbalance_params
      - Her dönem için analiz + dönemler arası değişim yüzdesi
      - Eksik dönem uyarısı
    - `GET /api/pricing/templates` — Profil şablonları listesi
    - `GET /api/pricing/periods` — Yüklü dönemler listesi (market data, yekdem, consumption profiles)
    - YEKDEM CRUD endpoint'leri (Task 6'da tanımlanan fonksiyonları bağla)
    - Zorunlu parametre eksikse HTTP 422 + eksik parametre listesi
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 15.1, 15.2, 15.3, 15.4_

  - [x] 18.2 Yetki ve erişim kontrolü
    - Upload market data + YEKDEM upsert: admin key (X-Admin-Key)
    - Analyze/simulate/compare/report/upload-consumption: normal API key
    - Dev mode: API_KEY_ENABLED=false → bypass
    - _Requirements: 19.1, 19.2, 19.3, 19.4_

  - [x] 18.3 Router'ı `backend/app/main.py`'ye bağla
    - `from .pricing.router import pricing_router` import
    - `app.include_router(pricing_router)` ekleme
    - Startup event'te `seed_profile_templates()` çağrısı
    - Pricing tabloları init_db'ye dahil edilmeli
    - _Requirements: 16.1_

- [x] 19. Cache ve versiyonlama (`backend/app/pricing/pricing_cache.py`, `backend/app/pricing/version_manager.py`)
  - [x] 19.1 Cache yönetimi
    - `build_cache_key(customer_id, period, params) -> str` — SHA256 hash
    - `get_cached_result(db, cache_key) -> dict | None` — TTL kontrolü + hit_count++
    - `set_cached_result(db, cache_key, customer_id, period, params_hash, result)` — cache'e yaz
    - `invalidate_cache_for_customer(db, customer_id)` — müşteri cache temizle
    - `invalidate_cache_for_period(db, period)` — dönem cache temizle
    - TTL yapılandırması: `PRICING_CACHE_TTL_HOURS` env var, varsayılan 24 saat
    - Cache invalidation kuralları: tüketim verisi güncelleme → müşteri cache sil, piyasa verisi güncelleme → dönem cache sil, YEKDEM güncelleme → dönem cache sil
    - _Requirements: 21.1, 21.2, 21.3, 21.4_

  - [x] 19.2 Veri versiyonlama
    - `archive_and_create_version(db, data_type, period, customer_id, row_count, quality_score, filename)` — mevcut aktif versiyonu arşivle, yeni versiyon oluştur
    - `list_versions(db, data_type, period, customer_id)` — yükleme geçmişi listele
    - `get_active_version(db, data_type, period, customer_id)` — aktif versiyon bilgisi
    - Arşivlenmiş versiyonlar görüntülenebilir ama hesaplamada kullanılmaz
    - _Requirements: 20.1, 20.2, 20.3, 20.4_

- [ ] 20. Veri kalite raporu (`backend/app/pricing/data_quality.py`)
  - [ ] 20.1 Veri kalite kontrol fonksiyonları
    - `calculate_quality_score(parse_result) -> int` — 0–100 kalite skoru
    - Kontroller: eksik saatler, mükerrer saatler, negatif tüketim, aykırı PTF/SMF (>3σ), sıfır tüketimli saatler
    - Her sorun için: tür, saat, değer, açıklama
    - Kalite skoru < 80 → uyarı mesajı
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5_

- [ ] 21. Checkpoint — API, cache, versiyonlama, kalite
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

- [x] 22. PDF/Excel rapor üretimi (`backend/app/pricing/pricing_report.py`)
  - [x] 22.1 PDF rapor üretimi
    - `generate_pdf_report(analysis_result, customer_info) -> bytes`
    - Jinja2 template: `backend/app/templates/pricing_analysis_template.html`
    - Sayfa 1: Kapak + Özet (şirket logosu, müşteri bilgileri, dönem, ağırlıklı PTF/SMF, risk skoru badge, güvenli katsayı)
    - Sayfa 2: T1/T2/T3 dağılım + maliyet karşılaştırma tablosu
    - Sayfa 3: Katsayı simülasyonu tablosu (×1.02–×1.10), güvenli katsayı vurgusu
    - Sayfa 4: Zarar haritası özeti + yasal uyarılar
    - Mevcut PDF altyapısı kullanılmalı: WeasyPrint → Playwright → ReportLab fallback zinciri
    - Mevcut `offer_template.html` ile tutarlı header/footer
    - _Requirements: 17.1, 17.3, 17.4_

  - [x] 22.2 Excel rapor üretimi
    - `generate_excel_report(analysis_result, customer_info) -> bytes`
    - openpyxl ile oluştur
    - Sheet 1: Özet (müşteri bilgileri, ağırlıklı PTF/SMF, risk skoru, güvenli katsayı)
    - Sheet 2: T1/T2/T3 Dağılım tablosu
    - Sheet 3: Katsayı Simülasyonu tablosu
    - Sheet 4: Saatlik Detay (744 satır)
    - Sheet 5: Zarar Haritası
    - _Requirements: 17.2, 17.3_

  - [x] 22.3 Rapor endpoint'leri
    - `POST /api/pricing/report/pdf` — PDF rapor indirme (Content-Type: application/pdf)
    - `POST /api/pricing/report/excel` — Excel rapor indirme (Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet)
    - Request: customer_id, period, multiplier, dealer_commission_pct, imbalance_params, customer_name, contact_person
    - Analiz hesapla veya cache'den al → rapor üret → binary döndür
    - _Requirements: 17.1, 17.2_

- [x] 23. Checkpoint — Rapor üretimi
  - PDF template + Excel rapor + endpoint'ler tamamlandı. 124 test geçti.
  - Product iyileştirmeler uygulandı: MAX_MULTIPLIER configurable, analyze logging, PDF hikaye cümlesi.

- [ ] 24. Entegrasyon ve birim testleri
  - [ ]* 24.1 Parser birim testleri (`backend/tests/test_pricing_parser.py`)
    - Bilinen EPİAŞ Excel dosyası ile parser doğrulama
    - Geçersiz format reddi
    - Eksik sütun tespiti
    - Aralık dışı değer reddi
    - Negatif tüketim uyarısı
    - _Requirements: 1.1, 1.4, 1.5, 4.1, 4.3, 4.4_

  - [ ]* 24.2 Hesaplama motoru birim testleri (`backend/tests/test_pricing_engine.py`)
    - Bilinen PTF verileri ile ağırlıklı ortalama hesaplama doğrulama
    - Saatlik maliyet hesaplama örneği
    - Sıfır tüketim hata mesajı
    - Bayi komisyonu dahil net marj hesaplama
    - _Requirements: 7.1, 7.3, 8.1, 8.7, 14.1, 14.2_

  - [ ]* 24.3 API entegrasyon testleri (`backend/tests/test_pricing_api.py`)
    - Upload market data endpoint testi
    - Upload consumption endpoint testi
    - Analyze endpoint testi (tam akış)
    - Simulate endpoint testi
    - Compare endpoint testi
    - Templates ve periods endpoint testleri
    - 422/403/404 hata senaryoları
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 19.4_

  - [ ]* 24.4 Cache ve versiyonlama testleri (`backend/tests/test_pricing_cache.py`)
    - Cache hit/miss senaryoları
    - Cache invalidation: veri güncelleme sonrası cache temizlenme
    - TTL süresi dolma
    - Versiyonlama: 2 kez yükleme sonrası arşiv kontrolü
    - _Requirements: 21.1, 21.2, 21.3, 20.1, 20.3_

- [ ] 25. Son checkpoint — Tüm testler ve entegrasyon
  - Tüm testlerin geçtiğinden emin ol, sorular varsa kullanıcıya sor.

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve daha hızlı MVP için atlanabilir (NOT: Property 8, 9, 10, 11, 12 testleri hesap motorunun güvenlik frenidir ve MVP'de bile zorunludur — bu testler `*` işareti taşımaz)
- Her görev belirli gereksinimleri referans alır (izlenebilirlik)
- Checkpoint'ler artımlı doğrulama sağlar
- Property testler evrensel doğruluk özelliklerini doğrular (Hypothesis kütüphanesi)
- Birim testler spesifik örnekleri ve edge case'leri doğrular
- **Kritik tasarım notu**: SQLAlchemy ORM'de `import sqlalchemy as sa` Task 1.2'de dahil edilmelidir
- **Kritik tasarım notu**: Güvenli katsayı binary search'te float kayması riski — Task 15.1'de integer step (1001–1100) kullanılmalıdır
- **Kritik tasarım notu**: Bayi komisyonu = brüt marj × bayi yüzdesi (design'daki sabit formül)
