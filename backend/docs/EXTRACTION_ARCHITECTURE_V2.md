# Hibrit Extraction Mimarisi v2

## Mevcut Durum (Ocak 2026)

### Çalışan Katmanlar:
```
┌─────────────────────────────────────────────────────────────────┐
│ KATMAN 1: pdfplumber (Dijital PDF)                              │
│ ├─ Metin katmanı varsa → regex ile değer çıkar                  │
│ ├─ Ödenecek Tutar, KDV, Tüketim pattern'leri                    │
│ └─ Sonuç: text_hint olarak Vision'a verilir                     │
├─────────────────────────────────────────────────────────────────┤
│ KATMAN 2: pypdfium2 (PDF → Image)                               │
│ ├─ Sayfa 1 ayrı render (ROI için)                               │
│ └─ Tüm sayfalar birleşik render (Vision için)                   │
├─────────────────────────────────────────────────────────────────┤
│ KATMAN 2.5: ROI Crop (Taranmış PDF) ✓ YENİ                      │
│ ├─ pdfplumber başarısız → ROI devreye                           │
│ ├─ Multi-crop hunting: 3 aday bölge                             │
│ ├─ Minimal prompt: sadece payable_total                         │
│ └─ Sonuç: roi_payable_total                                     │
├─────────────────────────────────────────────────────────────────┤
│ KATMAN 3: OpenAI Vision (Tam Sayfa)                             │
│ ├─ text_hint varsa prompt'a eklenir                             │
│ ├─ Tüm alanları çıkarır (schema v3)                             │
│ └─ Sonuç: extraction object                                     │
├─────────────────────────────────────────────────────────────────┤
│ KATMAN 4: Cross-validation ✓ YENİ                               │
│ ├─ Öncelik: pdfplumber > ROI > Vision                           │
│ ├─ reconcile_amount() ile karşılaştır                           │
│ └─ Güvenilir kaynak varsa extraction'ı güncelle                 │
├─────────────────────────────────────────────────────────────────┤
│ KATMAN 5: Post-processing                                       │
│ ├─ invoice_period düzeltme (fatura no'dan)                      │
│ ├─ line_items derivation                                        │
│ └─ TOTAL_MISMATCH kontrolü (tolerans bandı)                     │
└─────────────────────────────────────────────────────────────────┘
```

### Test Sonuçları (CK Boğaziçi):
| Alan | Beklenen | Vision | ROI | Final | Durum |
|------|----------|--------|-----|-------|-------|
| Ödenecek Tutar | 593.740,00 | 593.690,86 | 593.740,00 | 593.740,00 | ✓ |
| Tüketim | 116.145,63 | 116.145,63 | - | 116.145,63 | ✓ |
| KDV | 98.956,24 | ? | - | ? | ⚠️ |
| Birim Fiyat | ? | 4,36 | - | 4,36 | ? |

### Sorunlar:
1. **ROI sadece payable_total için** - diğer alanlar Vision'a bağımlı
2. **OCR yok** - scan PDF'lerde metin çıkmıyor
3. **KDV okunmuyor** - TOTAL_MISMATCH hesaplaması güvenilmez
4. **Kırılgan başarı** - ROI doğru bölgeyi bulursa çalışıyor

---

## Hedef Mimari (Sprint 10)

### Yeni Katman: OCR (Tesseract/PaddleOCR)
```
┌─────────────────────────────────────────────────────────────────┐
│ KATMAN 1: pdfplumber (Dijital PDF)                              │
│ └─ Değişiklik yok                                               │
├─────────────────────────────────────────────────────────────────┤
│ KATMAN 1.5: OCR (Taranmış PDF) ← YENİ                           │
│ ├─ pdfplumber başarısız → OCR devreye                           │
│ ├─ Tesseract veya PaddleOCR                                     │
│ ├─ Türkçe dil desteği (tur)                                     │
│ ├─ Regex ile tüm kritik alanları çıkar:                         │
│ │   - Ödenecek Tutar                                            │
│ │   - KDV Tutarı                                                │
│ │   - Toplam Tüketim                                            │
│ │   - Birim Fiyatlar                                            │
│ └─ Sonuç: ocr_extracted (pdfplumber ile aynı format)            │
├─────────────────────────────────────────────────────────────────┤
│ KATMAN 2: pypdfium2 (PDF → Image)                               │
│ └─ Değişiklik yok                                               │
├─────────────────────────────────────────────────────────────────┤
│ KATMAN 2.5: ROI Crop (Fallback)                                 │
│ ├─ OCR başarısız veya düşük confidence → ROI devreye            │
│ ├─ Genişletilmiş ROI: payable_total + vat + consumption         │
│ └─ Multi-field extraction                                       │
├─────────────────────────────────────────────────────────────────┤
│ KATMAN 3: OpenAI Vision (Verifier)                              │
│ ├─ Primary extractor DEĞİL, verifier                            │
│ ├─ OCR/ROI değerlerini doğrula                                  │
│ ├─ Eksik alanları tamamla                                       │
│ └─ Düşük confidence alanlarda karar ver                         │
├─────────────────────────────────────────────────────────────────┤
│ KATMAN 4: Cross-validation (Güçlendirilmiş)                     │
│ ├─ Öncelik: pdfplumber > OCR > ROI > Vision                     │
│ ├─ Her alan için ayrı confidence                                │
│ └─ Çoklu kaynak uyuşması → yüksek confidence                    │
├─────────────────────────────────────────────────────────────────┤
│ KATMAN 5: Post-processing                                       │
│ └─ Değişiklik yok                                               │
└─────────────────────────────────────────────────────────────────┘
```

### Hedef Akış:
```
PDF Yüklendi
    │
    ▼
┌─────────────┐
│ pdfplumber  │──── Metin var? ────► Regex extraction
└─────────────┘         │
        │ Metin yok    │
        ▼              │
┌─────────────┐        │
│    OCR      │──── Metin çıktı? ──► Regex extraction
└─────────────┘         │
        │ Başarısız    │
        ▼              │
┌─────────────┐        │
│  ROI Crop   │──── Değer bulundu? ─► Minimal extraction
└─────────────┘         │
        │              │
        ▼              ▼
┌─────────────────────────────────┐
│      OpenAI Vision              │
│  (text_hint ile desteklenmiş)   │
│  Rol: Verifier + Gap filler     │
└─────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────┐
│      Cross-validation           │
│  pdfplumber > OCR > ROI > Vision│
└─────────────────────────────────┘
        │
        ▼
    Final Extraction
```

---

## Uygulama Planı

### Faz 1: ROI Genişletme (Bugün)
- [ ] ROI crop'u diğer alanlara genişlet:
  - `vat_tl` (KDV Tutarı)
  - `consumption_kwh` (Toplam Tüketim)
  - `energy_total_tl` (Enerji Bedeli)
- [ ] CK Boğaziçi için özel bölge tanımları
- [ ] Test: Tüm kritik alanlar ROI'den okunabilmeli

### Faz 2: OCR Entegrasyonu (48 saat)
- [ ] Tesseract kurulumu ve Türkçe dil paketi
- [ ] `ocr_extractor.py` modülü:
  - `extract_text_with_ocr(image_bytes) -> str`
  - `parse_ocr_text(text) -> OcrExtracted`
- [ ] Regex pattern'leri (pdfplumber ile aynı)
- [ ] Pipeline entegrasyonu (KATMAN 1.5)
- [ ] Test: Scan PDF'lerde OCR > Vision doğruluğu

### Faz 3: Vision'ı Verifier'a Dönüştür (1 hafta)
- [ ] Vision prompt'unu güncelle: "Bu değerleri doğrula"
- [ ] Confidence-based decision logic
- [ ] Multi-source agreement scoring
- [ ] Test: OCR + Vision uyuşması → %99 doğruluk

---

## Kritik Metrikler

### Mevcut (Vision-primary):
- Ödenecek Tutar doğruluğu: ~95% (ROI ile)
- KDV doğruluğu: ~70% (Vision tek başına)
- Tüketim doğruluğu: ~90%
- TOTAL_MISMATCH oranı: ~20%

### Hedef (OCR-primary):
- Ödenecek Tutar doğruluğu: >99%
- KDV doğruluğu: >95%
- Tüketim doğruluğu: >98%
- TOTAL_MISMATCH oranı: <5%

---

## Sonuç

**Mevcut durum**: ROI crop ile "Ödenecek Tutar" sorunu çözüldü, ama diğer alanlar hala Vision'a bağımlı.

**Kök neden**: Scan PDF'lerde metin yok → Vision tek başına güvenilmez.

**Çözüm**: OCR katmanı ekle → Vision'ı verifier'a dönüştür.

**Öncelik**: 
1. ROI'yi genişlet (bugün)
2. OCR ekle (48 saat)
3. Vision'ı verifier yap (1 hafta)
