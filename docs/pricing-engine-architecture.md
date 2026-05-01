# Pricing Risk Engine — Mimari Doküman

## Sistem Özeti

Saatlik PTF/SMF bazlı müşteri fiyatlama ve risk analiz motoru.
EPİAŞ uzlaştırma Excel'inden saatlik piyasa verilerini yükleyerek,
müşterinin gerçek tüketim profili üzerinden ağırlıklı maliyet hesabı,
katsayı simülasyonu, güvenli katsayı önerisi, risk skoru ve detaylı
analiz raporu üretir.

**Durum:** Production-ready (127 test, 10 property test)
**Tarih:** Mayıs 2026

---

## Mimari Diyagram

```
┌─────────────────────────────────────────────────────────────┐
│                    FRONTEND (React/TS)                       │
│  App.tsx (mevcut teklif sistemi)                            │
│  [Pricing UI — henüz yok, API hazır]                        │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP/JSON
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                 FastAPI Backend (main.py)                     │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │           pricing_router (12 endpoint)               │    │
│  │                                                      │    │
│  │  AUTH LAYER                                          │    │
│  │  ├─ Admin: upload-market-data, yekdem upsert        │    │
│  │  └─ User:  analyze, simulate, compare, report       │    │
│  │                                                      │    │
│  │  CACHE LAYER (SHA256 + TTL 24h)                     │    │
│  │  ├─ analyze → cache check → cache write             │    │
│  │  └─ invalidation: upload/yekdem → period/customer   │    │
│  └──────────┬──────────────────────────────────────────┘    │
│             │                                                │
│  ┌──────────▼──────────────────────────────────────────┐    │
│  │              HESAPLAMA KATMANI                        │    │
│  │                                                      │    │
│  │  pricing_engine.py                                   │    │
│  │  ├─ calculate_weighted_prices()                      │    │
│  │  └─ calculate_hourly_costs()                         │    │
│  │      KRİTİK: TL = kWh × (TL/MWh) / 1000           │    │
│  │                                                      │    │
│  │  multiplier_simulator.py                             │    │
│  │  ├─ run_simulation()     [float-safe integer step]  │    │
│  │  └─ calculate_safe_multiplier()  [5. persentil]     │    │
│  │      KRİTİK: 1001-1100 integer tarama               │    │
│  │                                                      │    │
│  │  risk_calculator.py                                  │    │
│  │  ├─ calculate_risk_score()  [3 katmanlı model]      │    │
│  │  ├─ generate_offer_warning()                         │    │
│  │  └─ check_risk_safe_multiplier_coherence()          │    │
│  │                                                      │    │
│  │  time_zones.py    [T1/T2/T3 sınıflandırma]         │    │
│  │  imbalance.py     [SMF/flat dengesizlik]            │    │
│  └──────────┬──────────────────────────────────────────┘    │
│             │                                                │
│  ┌──────────▼──────────────────────────────────────────┐    │
│  │              VERİ KATMANI                             │    │
│  │                                                      │    │
│  │  excel_parser.py      [EPİAŞ + tüketim parser]      │    │
│  │  excel_formatter.py   [round-trip export]            │    │
│  │  yekdem_service.py    [CRUD + validation]            │    │
│  │  consumption_service.py [versiyonlama dahil]         │    │
│  │  profile_templates.py [12 sektörel şablon]           │    │
│  │  version_manager.py   [archive + create]             │    │
│  │  pricing_cache.py     [SHA256 + invalidation]        │    │
│  └──────────┬──────────────────────────────────────────┘    │
│             │                                                │
│  ┌──────────▼──────────────────────────────────────────┐    │
│  │              ÇIKTI KATMANI                            │    │
│  │                                                      │    │
│  │  pricing_report.py                                   │    │
│  │  ├─ generate_pdf_report()   [2 sayfa, hikaye dahil] │    │
│  │  └─ generate_excel_report() [5 sheet]               │    │
│  │                                                      │    │
│  │  Render: Playwright → WeasyPrint fallback            │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    SQLite Database                            │
│                                                              │
│  hourly_market_prices    (744 satır/ay, versiyonlu)         │
│  monthly_yekdem_prices   (aylık sabit, unique period)       │
│  consumption_profiles    (müşteri bazlı, versiyonlu)        │
│  consumption_hourly_data (FK → profiles, cascade delete)    │
│  profile_templates       (12 yerleşik şablon)               │
│  data_versions           (yükleme geçmişi)                  │
│  analysis_cache          (SHA256 key, TTL, hit_count)       │
└─────────────────────────────────────────────────────────────┘
```

---

## API Endpoint Haritası

| Method | Path | Auth | Açıklama |
|--------|------|------|----------|
| POST | /api/pricing/upload-market-data | Admin | EPİAŞ Excel → DB |
| POST | /api/pricing/upload-consumption | User | Tüketim Excel → DB |
| POST | /api/pricing/analyze | User | **Tam fiyatlama analizi** |
| POST | /api/pricing/simulate | User | Katsayı simülasyonu |
| POST | /api/pricing/compare | User | Çoklu ay karşılaştırma |
| POST | /api/pricing/report/pdf | User | PDF rapor indirme |
| POST | /api/pricing/report/excel | User | Excel rapor indirme |
| POST | /api/pricing/yekdem | Admin | YEKDEM upsert |
| GET | /api/pricing/yekdem/{period} | Public | YEKDEM sorgula |
| GET | /api/pricing/yekdem | Public | YEKDEM listele |
| GET | /api/pricing/templates | Public | Profil şablonları |
| GET | /api/pricing/periods | Public | Yüklü dönemler |

---

## Hesaplama Akışı

```
EPİAŞ Excel → parse → hourly_market_prices (744 satır)
                                    │
Tüketim Excel/Şablon → parse → consumption_hourly_data (744 satır)
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
            Ağırlıklı PTF    T1/T2/T3 Dağılım   Dengesizlik
            (tüketim×PTF)    (saat sınıflandırma) (SMF/flat)
                    │               │               │
                    └───────┬───────┘               │
                            ▼                       │
                    Saatlik Maliyet ◄───────────────┘
                    (base_cost, sales, margin)
                            │
                    ┌───────┼───────┐
                    ▼       ▼       ▼
              Simülasyon  Güvenli   Risk
              (×1.02-1.10) Katsayı  Skoru
                           (5.pct)  (3 katman)
                    │       │       │
                    └───────┼───────┘
                            ▼
                    Teklif Uyarı + Tutarlılık Guard
                            │
                    ┌───────┼───────┐
                    ▼       ▼       ▼
                   PDF    Excel    JSON
                  Rapor   Rapor   Response
```

---

## Kritik Tasarım Kararları

### 1. kWh/MWh Dönüşümü
```
TL = kWh × (TL/MWh) / 1000
```
Bu bölme her maliyet hesabında zorunlu. Unutulursa 1000× hata.

### 2. Integer Step (Güvenli Katsayı)
```
1001-1100 arası integer tarama (1001 = ×1.001)
```
Float kayması riski sıfır. `PRICING_MAX_MULTIPLIER_INT` env var ile genişletilebilir.

### 3. 5. Persentil Algoritması
```python
idx = int(len(sorted_list) * 0.05)
safe_value = sorted_list[idx]
```
- Tek ay: 744 saatlik marj dağılımı
- Çoklu ay: aylık net marj dağılımı

### 4. Risk Modeli (3 Katman)
```
Katman 1: Sapma (>5%=Yüksek, 2-5%=Orta, <2%=Düşük)
Katman 2: T2 override (>55%=Yüksek, >40%=en az Orta)
Katman 3: Peak concentration (>45%=en az Orta)
Override sadece yükseltir, asla düşürmez.
```

### 5. Cache Invalidation
```
Piyasa verisi güncelleme → dönem cache sil
Tüketim verisi güncelleme → müşteri cache sil
YEKDEM güncelleme → dönem cache sil
```

---

## Env Değişkenleri

| Değişken | Varsayılan | Açıklama |
|----------|-----------|----------|
| PRICING_CACHE_TTL_HOURS | 24 | Cache TTL (saat) |
| PRICING_MAX_MULTIPLIER_INT | 1100 | Güvenli katsayı üst sınır (1100=×1.100) |
| API_KEY_ENABLED | false | API key kontrolü |
| ADMIN_API_KEY_ENABLED | false | Admin key kontrolü |
| ENV | development | Ortam (production'da auth zorla açılır) |

### Production Guard
`ENV=production` olduğunda:
- `API_KEY_ENABLED` ve `ADMIN_API_KEY_ENABLED` otomatik `true` yapılır
- Dev bypass kesinlikle kapalıdır
- Key yoksa CRITICAL log yazılır

### Public Endpoint Rate Limit (v1.1 — TODO)
`yekdem read`, `templates`, `periods` endpoint'leri şu an public.
Production'da abuse riski var — ileride basit rate limit eklenmeli:
- IP bazlı: saniyede max 10 request
- Veya mevcut ops-guard rate limiter'a bağlama

### PDF Rapor Modları
| Mod | Watermark | Kullanım |
|-----|-----------|----------|
| `internal` | Yok | İç kullanım, gerçek teklif |
| `demo` | "ÖN ANALİZ RAPORUDUR — TİCARİ TEKLİF YERİNE GEÇMEZ" | Müşteri demo, rakip koruması |

---

## Test Coverage

| Dosya | Test Sayısı | Tür |
|-------|------------|-----|
| test_pricing_core.py | 31 | Birim + 3 property |
| test_multiplier_simulator.py | 32 | Birim + 7 property |
| test_risk_calculator.py | 25 | Birim |
| test_pricing_cache.py | 22 | Birim |
| test_pricing_report.py | 9 | Birim |
| test_sanity_real_scenario.py | 8 | Entegrasyon |
| **TOPLAM** | **127** | |

---

## Bilinen Sınırlamalar (v1)

1. **DST desteği yok** — 23/25 saatlik günler (Mart/Ekim) desteklenmiyor
2. **Float hassasiyeti** — Decimal yerine float kullanılıyor (MVP kararı)
3. **Alembic yok** — Schema değişikliğinde `create_all()` kullanılıyor
4. **Frontend UI yok** — Sadece API üzerinden erişim
5. **Tek bölge** — Sadece TR1 bölgesi destekleniyor
6. **Veri kalite raporu boş** — `DataQualityReport()` placeholder dönüyor

---

## v2 Yol Haritası

1. **Pricing accuracy feedback loop** (BİRİNCİ ÖNCELİK)
   - Tahmin edilen marj vs gerçekleşen fatura marjı
   - Tahmin edilen ağırlıklı PTF vs gerçekleşen ağırlıklı PTF
   - Önerilen katsayı vs gerçekleşen kârlılık
   - Aylık sapma raporu → model kalibrasyonu
2. **Frontend pricing UI** — React ekranı
3. **EPİAŞ API → pricing entegrasyonu** — Otomatik veri çekme
4. **Bayi onay mekanizması** — Merkez onayı olmadan teklif engelleme
5. **Çoklu bölge** — TR1 dışı bölgeler
6. **DST aware parser** — 23/25 saatlik günler
7. **Float → Decimal** — Finansal hassasiyet
8. **Alembic migration** — Production schema yönetimi
9. **DB tabanlı API key yönetimi** — api_keys tablosu (50+ kullanıcı olduğunda)
