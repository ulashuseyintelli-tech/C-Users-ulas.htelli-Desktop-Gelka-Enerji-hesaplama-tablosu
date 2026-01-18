# Requirements: Fatura Analiz ve Teklif Hesaplama Sistemi

## Genel Bakış
Elektrik faturalarını görsel/PDF olarak analiz eden, veri çıkaran ve alternatif enerji teklifi hesaplayan full-stack sistem. Türkiye'deki tüm elektrik tedarikçilerinin faturalarını tanıyabilir.

---

## 1. Fatura Yükleme ve Görsel İşleme

### 1.1 Desteklenen Formatlar
- Görsel: JPG, PNG, WebP, GIF, BMP, TIFF
- Doküman: PDF (ilk sayfa otomatik görsele çevrilir)

### 1.2 Dosya Boyutu Limiti
- Maksimum: 10 MB
- Boş dosya kontrolü

### 1.3 Görsel Optimizasyonu (Mobile)
- Büyük görseller 2048px'e küçültülür
- JPEG sıkıştırma (%85 kalite)
- İşleme sırasında loading gösterimi

### 1.4 Hata Durumları
- `file_too_large`: Dosya boyutu aşımı
- `unsupported_file_type`: Desteklenmeyen format
- `empty_file`: Boş dosya
- `pdf_conversion_error`: PDF dönüştürme hatası

---

## 2. Fatura Veri Çıkarma (Extraction) - 2 Katmanlı Parser

### 2.1 Katman 1: Kimlik Tespiti (Fatura Profili)
WHEN bir fatura yüklendiğinde THE Parser SHALL aşağıdaki sırayla kimlik tespiti yapmalı:
1. QR kod / ETTN varlığı kontrolü
2. e-Fatura logosu tespiti
3. "Özelleştirme No / Senaryo / Fatura Tipi" alanları
4. Tedarikçi adı (regex + keyword)
5. Dağıtım şirketi logosu/adı

### 2.2 Katman 2: Anlamsal Okuma (Alan Eşleştirme Sözlüğü)
THE Parser SHALL farklı tedarikçilerin aynı anlama gelen etiketlerini tanımalı:

| Standart Alan | Alternatif Etiketler |
|---------------|---------------------|
| `active_energy_amount` | "Aktif Enerji Bedeli", "ENERJİ TÜKETİM BEDELİ", "Enerji Bedeli (Tüketim)", "Toplam Enerji Bedeli" |
| `distribution_amount` | "Dağıtım Bedeli", "Elk. Dağıtım", "DSKB", "Dağıtım Sistemi Kullanım Bedeli" |
| `yek_amount` | "YEK Bedeli", "YEKDEM", "Yenilenebilir Enerji Kaynak Destekleme" |
| `consumption_tax` | "Elektrik Tüketim Vergisi", "ETV", "Tüketim Vergisi" |
| `total_amount` | "Ödenecek Tutar", "FATURA TUTARI", "GENEL TOPLAM", "KDV DAHİL TOPLAM" |

### 2.3 Çekirdek Alanlar (Değişmeyen)
| Alan | Birim | Açıklama |
|------|-------|----------|
| `ettn` | string | e-Fatura Tekil Numarası (UUID format) |
| `invoice_no` | string | Fatura Numarası |
| `invoice_date` | YYYY-MM-DD | Fatura Tarihi |
| `invoice_period` | YYYY-MM | Fatura Dönemi |
| `due_date` | YYYY-MM-DD | Son Ödeme Tarihi |
| `supplier` | string | Tedarikçi (Enerjisa, Uludağ, CK Boğaziçi, Osmangazi, Kolen, vb.) |
| `distributor` | string | Dağıtım Şirketi (UEDAŞ, BEDAŞ, AYEDAŞ, vb.) |

### 2.4 Tüketici Bilgileri
| Alan | Açıklama |
|------|----------|
| `consumer_title` | Tüketici Ünvanı |
| `consumer_vkn` | Vergi Kimlik No |
| `consumer_tckn` | TC Kimlik No (bireysel) |
| `facility_address` | Tesis Adresi |
| `eic_code` | EIC Kodu (Avrupa Enerji Tanımlama Kodu) |
| `contract_no` | Sözleşme Numarası |
| `meter_no` | Sayaç Numarası |

### 2.5 Tüketim Bilgileri
| Alan | Birim | Açıklama |
|------|-------|----------|
| `total_consumption_kwh` | kWh | Toplam Tüketim |
| `t1_consumption_kwh` | kWh | T1 (Gündüz) Tüketim - Çok Zamanlı |
| `t2_consumption_kwh` | kWh | T2 (Puant) Tüketim - Çok Zamanlı |
| `t3_consumption_kwh` | kWh | T3 (Gece) Tüketim - Çok Zamanlı |
| `reactive_inductive_kvarh` | kVArh | Endüktif Reaktif Tüketim |
| `reactive_capacitive_kvarh` | kVArh | Kapasitif Reaktif Tüketim |
| `demand_kw` | kW | Demand (Maksimum Güç) |

### 2.6 Kalem Bazlı Tutarlar
| Alan | Birim | Açıklama |
|------|-------|----------|
| `active_energy_amount` | TL | Aktif Enerji Bedeli |
| `distribution_amount` | TL | Dağıtım Bedeli |
| `yek_amount` | TL | YEK/YEKDEM Bedeli |
| `reactive_penalty_amount` | TL | Reaktif Ceza Bedeli |
| `consumption_tax` | TL | Elektrik Tüketim Vergisi |
| `energy_fund` | TL | Enerji Fonu |
| `trt_share` | TL | TRT Payı |
| `vat_amount` | TL | KDV Tutarı |
| `total_amount` | TL | Ödenecek Tutar (KDV dahil) |

### 2.7 Birim Fiyatlar
| Alan | Birim | Açıklama |
|------|-------|----------|
| `active_energy_unit_price` | TL/kWh | Aktif Enerji Birim Fiyatı |
| `distribution_unit_price` | TL/kWh | Dağıtım Birim Fiyatı |
| `yek_unit_price` | TL/kWh | YEK Birim Fiyatı |
| `t1_unit_price` | TL/kWh | T1 Birim Fiyatı (Çok Zamanlı) |
| `t2_unit_price` | TL/kWh | T2 Birim Fiyatı (Çok Zamanlı) |
| `t3_unit_price` | TL/kWh | T3 Birim Fiyatı (Çok Zamanlı) |

### 2.8 Tarife Bilgileri
| Alan | Değerler |
|------|----------|
| `voltage_level` | AG (Alçak Gerilim), OG (Orta Gerilim), YG (Yüksek Gerilim) |
| `tariff_type` | Mesken, Ticarethane, Sanayi, Tarımsal Sulama, Aydınlatma |
| `time_of_use` | Tek Zamanlı, Çok Zamanlı (T1-T2-T3), Kademeli |

### 2.9 Field Value Yapısı
Her alan şu bilgileri içerir:
- `value`: Sayısal/string değer (null olabilir)
- `confidence`: Güvenilirlik skoru (0-1)
- `evidence`: Faturadan alınan kanıt metni
- `page`: Sayfa numarası

### 2.10 Birim Dönüşümleri
- TL/MWh → TL/kWh (÷1000)
- Kr/kWh → TL/kWh (÷100)
- TR format (1.234,56) → Standart (1234.56)

### 2.11 Cache Mekanizması
- SHA-256 hash bazlı görsel cache
- Aynı fatura tekrar analiz edilmez
- `/cache` DELETE endpoint ile temizleme

### 2.12 PII Maskeleme
- Telefon numarası maskeleme
- TC Kimlik No maskeleme
- Email maskeleme
- Abone numarası maskeleme

---

## 3. Tedarikçi Tespit Kuralları (Genişletilmiş)

### 3.1 Enerjisa Grubu
Anahtar kelimeler: "Enerjisa", "Enerjisa Perakende", "Toroslar EDAŞ", "AYEDAŞ", "BAŞKENT EDAŞ", "Enerjisa Başkent"
Dağıtım: AYEDAŞ, Başkent EDAŞ, Toroslar EDAŞ

### 3.2 CK Grubu
Anahtar kelimeler: "CK", "CK Boğaziçi", "BEDAŞ", "Boğaziçi Elektrik", "CK Enerji"
Dağıtım: BEDAŞ

### 3.3 Uludağ Elektrik
Anahtar kelimeler: "Uludağ", "UEDAŞ", "Uludağ Elektrik"
Dağıtım: UEDAŞ

### 3.4 Osmangazi Elektrik
Anahtar kelimeler: "Osmangazi", "OEDAŞ", "Osmangazi Elektrik"
Dağıtım: OEDAŞ

### 3.5 Kolen Enerji
Anahtar kelimeler: "Kolen", "Kolen Enerji"

### 3.6 Ekvator Enerji
Anahtar kelimeler: "Ekvator", "Ekvator Enerji"

### 3.7 Yelden Enerji
Anahtar kelimeler: "Yelden", "Yelden Enerji"

### 3.8 Aksa Elektrik
Anahtar kelimeler: "Aksa", "Aksa Elektrik", "AKEDAŞ"
Dağıtım: AKEDAŞ

### 3.9 Dicle Elektrik
Anahtar kelimeler: "Dicle", "DEDAŞ", "Dicle Elektrik"
Dağıtım: DEDAŞ

### 3.10 Gediz Elektrik
Anahtar kelimeler: "Gediz", "GEDAŞ", "Gediz Elektrik"
Dağıtım: GEDAŞ

### 3.11 Trakya Elektrik
Anahtar kelimeler: "Trakya", "TEDAŞ", "Trakya Elektrik"

### 3.12 Zorlu Enerji
Anahtar kelimeler: "Zorlu", "Zorlu Enerji"

### 3.13 Limak Enerji
Anahtar kelimeler: "Limak", "Limak Enerji"

---

## 4. Validasyon Kuralları

### 4.1 Zorunlu Alan Kontrolü
- `total_consumption_kwh`: null veya ≤0 ise hata
- `ettn` veya `invoice_no`: En az biri zorunlu

### 4.2 Birim Fiyat Aralık Kontrolü
- Aktif Enerji: Min 0.1, Max 30.0 TL/kWh
- Dağıtım: Min 0.0, Max 10.0 TL/kWh
- Aralık dışı değerler için hata mesajı

### 4.3 Düşük Confidence Uyarısı
- Threshold: 0.6
- Kritik alanlarda düşük confidence varsa uyarı

### 4.4 Demand Tutarlılık
- `demand_kw` > 0 ise `demand_unit_price` zorunlu

### 4.5 Reaktif Ceza Kontrolü
- Endüktif/Kapasitif oranı %33'ü geçerse reaktif ceza beklenir
- Ceza yoksa uyarı

### 4.6 Toplam Karşılaştırma (INVOICE_TOTAL_MISMATCH)
- Hesaplanan toplam vs faturadaki toplam
- S2 Mismatch koşulu (OR):
  - ratio >= 5% (yuvarlama/mahsup değil)
  - delta >= 50 TL (küçük faturada ratio yakalar, büyük faturada delta yakalar)
- S1 Escalation koşulu:
  - (ratio >= 20% AND delta >= 50) OR delta >= 500 TL
  - Küçük fatura koruması: yüksek ratio ama delta < 50 → S2 kalır
- OCR_LOCALE_SUSPECT tag:
  - extraction_confidence < 0.7 + mismatch → suspect_reason eklenir
  - Ayrı flag değil, mevcut flag'e metadata olarak eklenir

### 4.7 Çok Zamanlı Tutarlılık
- T1 + T2 + T3 = Toplam Tüketim (±%1 tolerans)

### 4.8 is_ready_for_pricing
- Eksik alan yok VE hata yok → true
- Aksi halde → false

---

### 4.8 Incident Actionability (Sprint 8.5)

Her incident için "3 adımda karar" prensibi:
1. action_class: Ne tür bir aksiyon gerekiyor?
2. primary_suspect: Ana şüpheli ne?
3. recommended_checks: Hangi kontroller yapılmalı? (olasılık sırasına göre)

#### Action Classes
| Class | Açıklama | Tipik Senaryo |
|-------|----------|---------------|
| VERIFY_OCR | OCR/locale hatası şüphesi | Düşük confidence + mismatch |
| VERIFY_INVOICE_LOGIC | Fatura mantık kontrolü | Yüksek delta, normal confidence |
| ACCEPT_ROUNDING_TOLERANCE | Kabul edilebilir yuvarlama | delta < 10 TL AND ratio < 0.5% |

#### Recommended Checks Sıralaması
- İlk 2 check %80 vakayı çözmeli
- Olasılık sırasına göre (en olası → en az olası)
- Maksimum 5 check

#### Determinizm Kuralı
Aynı (flag_code, mismatch_info, extraction_confidence) → Aynı ActionHint

---

### 4.9 System Health Dashboard (Sprint 8.6)

Amaç: "Sistem bozuldu mu, yoksa dünya mı bozuk?" sorusunu cevaplayabilmek.

#### Haftalık Dağılım Snapshot
| Metrik | Açıklama |
|--------|----------|
| mismatch_ratio_histogram | Bucket'lar: [0-2%, 2-5%, 5-10%, 10-20%, 20%+] |
| s1_s2_ratio | S1 / (S1 + S2) oranı |
| ocr_locale_suspect_rate | OCR_LOCALE_SUSPECT tag'li incident oranı |
| action_class_distribution | VERIFY_OCR / VERIFY_INVOICE_LOGIC / ACCEPT_ROUNDING dağılımı |

#### Drift Detection (Triple Guard)
Alarm koşulu (tüm koşullar AND):
- curr_total >= 20 (minimum sample)
- abs(curr_count - prev_count) >= 5 (absolute count delta)
- prev_rate > 0 ise: curr_rate >= 2 * prev_rate (rate doubling)
- prev_rate == 0 ise: rate guard atlanır, sadece count guard + curr_count >= 5 ile WARNING

| Alert | Koşul | Seviye |
|-------|-------|--------|
| S1_RATE_DRIFT | S1 oranı 2x arttı | WARNING |
| OCR_SUSPECT_DRIFT | OCR_LOCALE_SUSPECT oranı 2x arttı | WARNING |
| MISMATCH_RATE_DRIFT | Mismatch oranı 2x arttı | WARNING |

#### Top Offenders
- Provider bazlı mismatch RATE (count değil!)
- rate = mismatch_count / total_count
- Minimum volume guard: total_count >= 20 (düşük hacimli provider'lar hariç)
- İki liste:
  - Top by rate (min_n >= 20): En acil - yüksek hata oranı
  - Top by count: En büyük etki - en çok mismatch üreten

#### Mismatch Ratio Tanımı
```
ratio = abs(invoice_total - computed_total) / max(invoice_total, 0.01)
```
- Denominator: invoice_total (SOURCE OF TRUTH - 8.3 kontratı ile tutarlı)
- Epsilon: 0.01 TL (sıfıra bölme koruması)

#### Dashboard Endpoint
- `/admin/system-health` - Tek sayfa, tüm metrikler
- Warning'lar dashboard etiketi olarak gösterilir (alarm değil)

---

### 4.10 Feedback Loop (Sprint 8.7)

Amaç: Operatör geri bildirimi ile hint kalitesini ölçmek ve gelecekte kalibrasyon için veri toplamak.

#### Feedback Schema
```json
{
  "action_taken": "VERIFIED_OCR" | "VERIFIED_LOGIC" | "ACCEPTED_ROUNDING" | "ESCALATED" | "NO_ACTION_REQUIRED",
  "was_hint_correct": true | false,
  "actual_root_cause": "optional string (max 200 char)",
  "resolution_time_seconds": 120,
  "feedback_at": "2025-01-17T15:00:00Z",
  "feedback_by": "user_id"
}
```

#### Kurallar
- Feedback OPSIYONEL (zorunlu değil)
- Feedback sadece RESOLVED state'indeki incident'lara yazılabilir (açık incident'e feedback = tahmin, çözülmüş = gerçek)
- UPSERT semantiği: Her feedback submission önceki feedback'i overwrite eder, `updated_at` ve `feedback_at` her zaman güncellenir
- `feedback_by` zorunlu: Auth context'ten gelir, request'ten gelmez. Auth yoksa endpoint erişilemez (admin only)
- `feedback_at` server-time: Client timestamp kabul edilmez
- Otomasyon yok - sadece veri toplama
- Gelecekte kalibrasyon için kullanılacak

#### Validation Kuralları
| Kural | Açıklama |
|-------|----------|
| `was_hint_correct` required | null olamaz, true veya false olmalı |
| `resolution_time_seconds` >= 0 | Negatif değer yasak |
| `actual_root_cause` max 200 char | Uzun metin yasak |
| `action_taken` enum içinde | Enum dışı değer yasak |

#### Feedback Actions
| Action | Açıklama |
|--------|----------|
| VERIFIED_OCR | OCR/locale hatası doğrulandı ve düzeltildi |
| VERIFIED_LOGIC | Fatura mantık hatası doğrulandı |
| ACCEPTED_ROUNDING | Yuvarlama farkı kabul edildi |
| ESCALATED | Üst seviyeye iletildi |
| NO_ACTION_REQUIRED | İncelendi, aksiyon gerekmedi (beklenen davranış) |

#### Kalibrasyon Metrikleri
| Metrik | Açıklama |
|--------|----------|
| hint_accuracy_rate | was_hint_correct=true / total_feedback (0.0 if total=0) |
| action_class_accuracy | Her action class için doğruluk oranı |
| avg_resolution_time_by_class | Action class bazlı ortalama çözüm süresi |
| feedback_coverage | resolved_with_feedback / resolved_total (0.0 if total=0) |

#### Error Codes
| Code | HTTP | Açıklama |
|------|------|----------|
| `incident_not_found` | 404 | Incident bulunamadı |
| `incident_not_resolved` | 400 | State guard: RESOLVED değil |
| `invalid_feedback_action` | 400 | Enum dışı action_taken |
| `invalid_feedback_data` | 400 | Validation hatası |

#### API Endpoints
- `PATCH /admin/incidents/{id}/feedback` - Feedback kaydet (upsert)
- `GET /admin/feedback-stats` - Kalibrasyon metrikleri

---

## 5. Teklif Hesaplama

### 5.1 Giriş Parametreleri
| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `weighted_ptf_tl_per_mwh` | 2974.1 | Ağırlıklı PTF |
| `yekdem_tl_per_mwh` | 364.0 | YEKDEM bedeli |
| `agreement_multiplier` | 1.01 | Anlaşma çarpanı |

### 5.2 Mevcut Fatura Hesabı (Calculator Contract)
```
KONTRAT:
- current_total_with_vat_tl = invoice_total_with_vat_tl (SOURCE OF TRUTH)
- Faturadaki gerçek toplam kullanılır, HESAPLANMAZ
- current_* kalemleri sadece breakdown/evidence amaçlı

Breakdown (evidence için):
current_energy_tl = raw_breakdown.energy_total_tl veya (kwh × current_unit_price)
current_distribution_tl = raw_breakdown.distribution_total_tl veya (kwh × dist_unit_price)
current_demand_tl = demand_qty × demand_unit_price
current_btv_tl = raw_breakdown.btv_tl veya (current_energy_tl × 0.01)
current_vat_tl = raw_breakdown.vat_tl veya (matrah × 0.20)
current_vat_matrah_tl = current_total_with_vat_tl - current_vat_tl
```

### 5.3 Teklif Fatura Hesabı
```
YEKDEM KURALI:
- Faturada YEKDEM bedeli varsa (yek_amount > 0) → Teklife YEKDEM dahil
- Faturada YEKDEM bedeli yoksa veya 0 ise → Teklife YEKDEM dahil DEĞİL

offer_ptf_tl = (ptf_tl_per_mwh / 1000) × kwh
offer_yekdem_tl = should_include_yekdem ? (yekdem_tl_per_mwh / 1000) × kwh : 0
offer_energy_tl = (ptf + yekdem) × agreement_multiplier
offer_distribution_tl = kwh × dist_unit_price
offer_demand_tl = demand_qty × demand_unit_price
offer_btv_tl = offer_energy_tl × 0.01
offer_vat_matrah_tl = energy + distribution + demand + btv
offer_vat_tl = matrah × 0.20
offer_total_with_vat_tl = matrah + vat
```

### 5.4 Tasarruf Hesabı
```
difference_excl_vat_tl = current_matrah - offer_matrah
difference_incl_vat_tl = current_total - offer_total
savings_ratio = difference_incl_vat / current_total
unit_price_savings_ratio = (current_unit - offer_unit) / current_unit
```

---

## 6. Kritik Farklar (Handle Edilmesi Gereken)

### 6.1 OG / AG Farkı
- OG (Orta Gerilim): Genelde sanayi, daha düşük birim fiyat
- AG (Alçak Gerilim): Mesken, ticarethane
- Dağıtım bedeli farklı hesaplanır

### 6.2 Tek Zaman / Çok Zaman (T1-T2-T3)
- Tek Zamanlı: Tek birim fiyat
- Çok Zamanlı: T1 (Gündüz), T2 (Puant), T3 (Gece) farklı fiyatlar
- Ağırlıklı ortalama hesaplanmalı

### 6.3 Endüktif / Kapasitif Cezalar
- Endüktif reaktif: Aktif enerjinin %33'ünü geçerse ceza
- Kapasitif reaktif: Aktif enerjinin %20'sini geçerse ceza
- Ceza hesabı ayrı kalem olarak gösterilmeli

### 6.4 YEK Bedeli Hesaplama
- Bazen kWh bazlı (TL/kWh × tüketim)
- Bazen sabit tutar
- Faturadan hangisi olduğu anlaşılmalı

### 6.5 KDV Oranları
- Mesken: %10 (bazı dönemlerde)
- Sanayi/Ticarethane: %20
- Tarımsal Sulama: %1 veya %8

### 6.6 Yuvarlama Farkları
- Faturalarda kuruş yuvarlama farkları olabilir
- ±0.01 TL tolerans kabul edilmeli

---

## 7. API Endpoints

### 7.1 GET /health
Sağlık kontrolü

### 7.2 POST /analyze-invoice
Fatura analizi (extraction + validation)

### 7.3 POST /calculate-offer
Teklif hesaplama

### 7.4 POST /full-process
Tek endpoint: Yükle → Analiz → Hesapla

### 7.5 DELETE /cache
Extraction cache temizleme

### 7.6 GET /suppliers
Desteklenen tedarikçi listesi

---

## 8. Mobile Uygulama

### 8.1 Fatura Yükleme
- Galeri seçimi
- Kamera çekimi
- PDF dosya seçimi

### 8.2 Görsel İşleme
- Otomatik resize (2048px max)
- JPEG sıkıştırma

### 8.3 Extraction Görüntüleme
- Alan bazlı kart gösterimi
- Confidence badge'leri
- Evidence tooltip

### 8.4 Parametre Girişi
- PTF, YEKDEM, çarpan düzenleme
- Varsayılan değerler

### 8.5 Sonuç Gösterimi
- Mevcut vs Teklif karşılaştırma
- Tasarruf oranı vurgulama

### 8.6 Eksik Alan Yönetimi
- Soru kartları
- Manuel değer girişi
- Önerilen değer gösterimi

### 8.7 Hata Yönetimi
- Kullanıcı dostu hata mesajları
- Retry mekanizması

---

## 9. Güvenlik ve Performans

### 9.1 Input Validasyonu
- Dosya boyutu kontrolü
- MIME type kontrolü
- Boş dosya kontrolü

### 9.2 PII Koruma
- Log'larda hassas veri maskeleme
- Telefon, TC, email, abone no

### 9.3 Cache
- Hash bazlı extraction cache
- API ile temizlenebilir

### 9.4 Timeout
- API timeout: 60 saniye
- Kullanıcı bilgilendirmesi

### 9.5 CORS
- Tüm origin'lere izin (development)
