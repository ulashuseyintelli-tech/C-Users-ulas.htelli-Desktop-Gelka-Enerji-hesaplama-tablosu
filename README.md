# Gelka Enerji - Otomatik Fatura Analiz ve Teklif Sistemi

> Elektrik faturası görselini yükle → Otomatik analiz → İndirimli satış teklifi al

## 🎯 Proje Amacı

Elektrik faturası görselini veya PDF'ini yükleyen müşteriye, **manuel veri girişi olmadan**, mevcut faturasını analiz eden ve indirimli elektrik satış teklifini otomatik hesaplayıp sunan sistem.

**Amaç sadece "fatura okumak" değil: Satışa hazır, güvenilir, doğrulanmış teklif üretmek.**

## 🔥 Neden Bu Proje?

| Problem | Çözüm |
|---------|-------|
| Elektrik faturaları şirketten şirkete farklı format | Vendor bağımsız, tip bazlı okuma |
| Excel ile manuel hesaplama hatalı ve yavaş | Deterministik hesap motoru |
| Müşteri anında tasarruf cevabı istiyor | Saniyeler içinde teklif |
| Ölçeklenemiyor | API tabanlı, mobil uyumlu |

## 🏗️ Sistem Mimarisi

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Mobile App    │────▶│   FastAPI       │────▶│   OpenAI        │
│   (React Native)│     │   Backend       │     │   Vision API    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                    ┌──────────┼──────────┐
                    ▼          ▼          ▼
              ┌─────────┐ ┌─────────┐ ┌─────────┐
              │Extractor│ │Validator│ │Calculator│
              └─────────┘ └─────────┘ └─────────┘
```

## 📦 Modüller

### MODÜL 1: Fatura Görseli Anlamlandırma (Vision Extraction)
- OpenAI Vision API ile görsel anlamlandırma
- OCR + regex yok, tamamen AI tabanlı
- Strict JSON çıktı garantisi
- Vendor bağımsız, fatura tipine göre okuma

**Çıkarılan Alanlar:**
- `consumption_kwh` - Tüketim (kWh)
- `current_active_unit_price_tl_per_kwh` - Aktif enerji birim fiyatı
- `distribution_unit_price_tl_per_kwh` - Dağıtım birim fiyatı
- `demand_qty` / `demand_unit_price` - Demand (varsa)
- `invoice_total_with_vat_tl` - KDV dahil toplam

### MODÜL 2: Akıllı Doğrulama & Eksik Alan Yönetimi
- Mantık kontrolleri (birim, aralık, sıfır)
- Yaklaşık tutar hesabıyla fatura tutarını kıyaslama
- Vendor-specific tolerans (%5 Enerjisa, %10 CK)
- Eksik alanları tespit, kullanıcıya soru üret
- Otomatik türetilebilir alanlar için öneri

**Çıktı:** `is_ready_for_pricing = true/false`

### MODÜL 3: Hesap Motoru
- Python ile deterministik hesaplama
- Aynı girdiye her zaman aynı sonuç
- Test edilebilir, mobil/web/API uyumlu

**Hesaplar:**
- Enerji bedeli, Dağıtım bedeli, Demand
- BTV (%1), KDV (%20)
- Teklif fiyatı (PTF + YEKDEM × çarpan)
- Tasarruf oranları

### MODÜL 4: Teklif & Satış Çıktısı
- PDF/HTML teklif oluşturma
- Müşteri arşivi (SQLite)
- Teklif durumu takibi (draft/sent/accepted/rejected)

## 📊 Fatura Tipi Stratejisi

| Tip | Açıklama | Durum |
|-----|----------|-------|
| Tip-1 | Toplam kWh + birim fiyat açık (Enerjisa) | ✅ MVP |
| Tip-2 | Çok zamanlı + toplam satırı (Ekvator) | ✅ MVP |
| Tip-3 | Kademeli/çok satırlı (CK Boğaziçi) | ✅ MVP |
| Tip-4 | Dağıtım birim fiyatı yok | ✅ MVP |
| Tip-5 | Demand/güç/reaktif (Sanayi) | 🔄 Genişleme |
| Tip-6 | Çoklu sayaç/tesisat | 🔄 Genişleme |
| Tip-7 | Mahsuplaşma/düzeltme | 🔄 Genişleme |

## 🚀 Kurulum

### Backend
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env  # OPENAI_API_KEY ekle
uvicorn app.main:app --reload
```

### Mobile
```bash
cd mobile
npm install
npx expo start
```

## 📡 API Endpoints

| Endpoint | Method | Açıklama |
|----------|--------|----------|
| `/analyze-invoice` | POST | Fatura analizi |
| `/calculate-offer` | POST | Teklif hesaplama |
| `/full-process` | POST | Tek adımda analiz + hesaplama |
| `/customers` | CRUD | Müşteri yönetimi |
| `/offers` | CRUD | Teklif arşivi |
| `/offers/{id}/generate-pdf` | POST | PDF oluştur |
| `/stats` | GET | İstatistikler |

## 🎯 Veri Hedefleri

| Seviye | Tedarikçi | Fatura | Durum |
|--------|-----------|--------|-------|
| MVP | 10 | 30-50 | 🔄 |
| Sağlam Prod | 15-20 | 100 | ⏳ |
| Enterprise | 20+ | 200+ | ⏳ |

## 💪 Güçlü Yanlar

- ✅ Vendor bağımsız (format kırılmasına dayanıklı)
- ✅ Görsel tabanlı (OCR değil, AI anlamlandırma)
- ✅ Kendi kendini doğrulayan (sanity check)
- ✅ Manuel müdahale gerektirmeyen
- ✅ Satış odaklı tasarlanmış
- ✅ Ölçeklenebilir (API tabanlı)

## 📁 Proje Yapısı

```
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI endpoints
│   │   ├── extractor.py      # OpenAI Vision extraction
│   │   ├── extraction_prompt.py  # AI prompt
│   │   ├── validator.py      # Doğrulama & eksik alan
│   │   ├── calculator.py     # Hesap motoru
│   │   ├── models.py         # Pydantic modeller
│   │   ├── database.py       # SQLite + SQLAlchemy
│   │   ├── pdf_generator.py  # PDF/HTML oluşturma
│   │   ├── pdf_render.py     # PDF → Image (pypdfium2)
│   │   └── image_prep.py     # EXIF fix + preprocessing
│   ├── scripts/
│   │   └── test_pipeline.py  # Pipeline test runner
│   └── tests/
├── mobile/
│   ├── src/
│   │   ├── api/client.ts     # API client
│   │   ├── components/       # UI bileşenleri
│   │   └── utils/            # Image processing
│   └── App.tsx
└── .kiro/specs/              # Spec dokümanları
```

## 🔄 Pipeline Akışı

```
Upload (PDF/foto)
    ↓
Normalize (PDF→image + EXIF fix + preprocess)
    ↓
Extraction (Vision → strict JSON)
    ↓
Validation (eksik/hata kontrolü)
    ↓
Patch (sadece eksik alanları sor)
    ↓
Pricing (deterministik hesap)
    ↓
Offer (PDF/HTML çıktı)
```

## 🧪 Test Pipeline

```bash
# Tek dosya test
cd backend
python scripts/test_pipeline.py ../invoice.pdf

# Klasör test
python scripts/test_pipeline.py ../invoices/

# Tüm PDF'ler
python scripts/test_pipeline.py --all

# JSON çıktı
python scripts/test_pipeline.py invoice.pdf --json
```

## ⚙️ Konfigürasyon

```env
# .env dosyası
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-2024-08-06
OPENAI_MAX_RETRIES=3

DATABASE_URL=sqlite:///./gelka_enerji.db
STORAGE_DIR=./storage

API_KEY=dev-key
API_KEY_ENABLED=false

EXTRACTION_CACHE_ENABLED=true
```

## 🛡️ Prod Sertleştirme Checklist

- [x] Hash-based caching (aynı fatura tekrar okunmasın)
- [x] Retry mekanizması (rate limit, connection error)
- [x] EXIF rotation fix (iPhone/Android)
- [x] Image preprocessing (contrast, sharpness)
- [x] Structured Outputs (strict JSON)
- [x] Vendor-specific tolerans
- [x] Invoice status tracking
- [ ] Rate limiting
- [ ] Audit log
- [ ] Async job queue
- [ ] S3 storage
- [ ] Multi-tenant

## 📄 Lisans

Proprietary - Gelka Enerji © 2026
