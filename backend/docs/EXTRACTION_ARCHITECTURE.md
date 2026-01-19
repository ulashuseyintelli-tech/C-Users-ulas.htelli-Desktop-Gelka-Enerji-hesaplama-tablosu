# Fatura Extraction Mimarisi

## Güncel Durum (Ocak 2026 - Sprint 9)

### Hibrit Pipeline (5 Katman)
```
PDF Girdi
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ KATMAN 1: PDF Metin Çıkarma (pdfplumber)                        │
│ - Dijital PDF'lerden metin çıkar                                │
│ - Regex ile kritik değerleri bul:                               │
│   • Ödenecek Tutar                                              │
│   • KDV Tutarı                                                  │
│   • KDV Matrahı                                                 │
│   • Toplam Tüketim                                              │
│ - Taranmış PDF'lerde ÇALIŞMAZ → KATMAN 2.5'e geç                │
│ - ✓ ENTEGRE EDİLDİ                                              │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ KATMAN 2: Görsel Render (pypdfium2)                             │
│ - PDF sayfalarını PNG'ye çevir                                  │
│ - Tüm sayfaları dikey birleştir (max 3 sayfa)                   │
│ - ✓ ÇALIŞIYOR                                                   │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ KATMAN 2.5: ROI Crop (Bölge Kırpma) - YENİ!                     │
│ - Sadece KATMAN 1 başarısız olduğunda çalışır                   │
│ - Multi-crop hunting stratejisi:                                │
│   • Sağ üst (Fatura Özeti genelde burada)                       │
│   • Sağ orta                                                    │
│   • Sağ şerit                                                   │
│ - Her crop'u minimal prompt ile OpenAI'a gönder                 │
│ - Sadece "Ödenecek Tutar" sor                                   │
│ - ✓ ENTEGRE EDİLDİ                                              │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ KATMAN 3: OpenAI Vision (gpt-4o) + Text Hint                    │
│ - Tam görsel → JSON extraction                                  │
│ - KATMAN 1 veya 2.5'ten gelen değerler prompt'a eklenir         │
│ - Hint örneği:                                                  │
│   "⚠️ PDF'den okunan Ödenecek Tutar: 593.740,00 TL              │
│    Bu değeri doğrula ve JSON'a yaz!"                            │
│ - ✓ TEXT HINT ENTEGRE EDİLDİ                                    │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ KATMAN 4: Cross-Validation (reconcile_amount)                   │
│ - Referans değer (pdfplumber > ROI > Vision) vs Vision          │
│ - Tolerans: %0.10 tam eşleşme, %0.50 yuvarlama                  │
│ - Sonuçlar:                                                     │
│   • text_confirmed: Her iki kaynak uyumlu                       │
│   • text_with_rounding: Küçük fark, referans kullan             │
│   • text_only: Sadece referans var                              │
│   • vision_only: Sadece vision var                              │
│   • HARD_MISMATCH: Ciddi fark, manuel kontrol                   │
│ - ✓ ENTEGRE EDİLDİ                                              │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ KATMAN 5: Post-Processing                                       │
│ - Line items'dan eksik değerleri türet                          │
│ - consumption_kwh: ✓ TÜRETİLİYOR                                │
│ - unit_price: ✓ TÜRETİLİYOR                                     │
│ - invoice_total: Cross-validation sonucu veya line items        │
└─────────────────────────────────────────────────────────────────┘
```

## Dosya Yapısı

```
backend/app/
├── pdf_text_extractor.py   # KATMAN 1: pdfplumber + regex
├── region_extractor.py     # KATMAN 2.5: ROI crop + multi-crop hunting (YENİ!)
├── parse_tr.py             # Türkçe sayı parser + reconcile_amount
├── extractor.py            # KATMAN 3: OpenAI Vision (text_hint parametresi)
├── main.py                 # Pipeline orchestration (5 katman entegrasyonu)
└── calculator.py           # Hesaplama motoru
```

## ROI Crop Stratejisi (KATMAN 2.5)

### Multi-Crop Hunting
```
Sayfa 1 Görseli
┌────────────────────────────────────┐
│                    │ ┌───────────┐ │
│                    │ │ SAĞ ÜST   │ │ ← Crop 1: Fatura Özeti genelde burada
│                    │ │ (50-100%) │ │
│                    │ └───────────┘ │
│                    │ ┌───────────┐ │
│                    │ │ SAĞ ORTA  │ │ ← Crop 2: Alternatif konum
│                    │ │ (25-60%)  │ │
│                    │ └───────────┘ │
│                    │ ┌───────────┐ │
│                    │ │ SAĞ ŞERİT │ │ ← Crop 3: Tüm sağ taraf
│                    │ │ (0-50%)   │ │
│                    │ └───────────┘ │
└────────────────────────────────────┘
```

### Minimal Prompt (Sadece Ödenecek Tutar)
```json
{
    "payable_total": "593.740,00",
    "currency": "TL",
    "confidence": 0.95,
    "evidence": "Ödenecek Tutar: 593.740,00 TL"
}
```

### Avantajlar
1. **Odaklanmış dikkat**: Vision küçük bölgeye konsantre olur
2. **Düşük maliyet**: Küçük görsel = az token
3. **Hızlı**: Minimal prompt = hızlı yanıt
4. **Yüksek doğruluk**: Tek görev = daha az hata

## Akış Diyagramı

```
PDF Yüklendi
    │
    ├─► pdfplumber metin çıkar
    │       │
    │       ├─► Metin VAR → Ödenecek Tutar regex ile bul
    │       │                   │
    │       │                   └─► Hint olarak Vision'a ver
    │       │
    │       └─► Metin YOK (taranmış) → ROI Crop
    │                                     │
    │                                     ├─► Crop 1 dene → Buldu? → Hint olarak ekle
    │                                     ├─► Crop 2 dene → Buldu? → Hint olarak ekle
    │                                     └─► Crop 3 dene → Buldu? → Hint olarak ekle
    │
    ├─► Vision tam sayfa extraction (hint ile)
    │
    ├─► Cross-validation (referans vs vision)
    │       │
    │       ├─► Uyumlu → Referans değeri kullan
    │       └─► Uyumsuz → HARD_MISMATCH flag
    │
    └─► Sonuç döndür
```

## Öncelik Sırası (Cross-Validation)

1. **pdfplumber** (en güvenilir - dijital metin)
2. **ROI crop** (güvenilir - odaklanmış vision)
3. **Vision tam sayfa** (en az güvenilir - dikkat dağınık)

## Sonraki Adımlar

1. [x] ROI crop modülü oluşturuldu
2. [x] Multi-crop hunting entegre edildi
3. [x] Cross-validation ROI desteği eklendi
4. [ ] Tesseract OCR entegrasyonu (taranmış PDF'ler için backup)
5. [ ] Vendor-specific ROI koordinatları (CK, Enerjisa, vb.)
6. [ ] KDV oranı otomatik tespit (%0, %10, %20)
