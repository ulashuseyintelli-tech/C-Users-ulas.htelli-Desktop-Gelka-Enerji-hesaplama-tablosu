# Sprint 9: Koşullu Plan (Veri-Driven)

## Tarih: 2026-01-18
## Versiyon: 1.1
## Durum: BEKLEMEDE (Pilot verisi gerekli)

---

## ⚠️ TEMEL PRENSİP

```
╔═════════════════════════════════════════════════════════════════════════════╗
║                                                                             ║
║   Sprint 9, SPEKÜLATİF değil VERİ-DRİVEN'dır.                              ║
║                                                                             ║
║   Hiçbir task pilot verisi olmadan başlatılmaz.                            ║
║   Her task bir KOŞUL'a bağlıdır.                                           ║
║   Koşul sağlanmazsa → task açılmaz.                                        ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
```

---

## 0. Başarı Durumunda Sabır Kuralı (KRİTİK)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    BAŞARI DURUMUNDA SABIR KURALI                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  EĞER tüm ana metrikler normal aralıkta VE n >= 100 İSE:                   │
│                                                                             │
│      → Sprint 9 AÇILMAZ                                                     │
│      → Gözlem süresi uzatılır (7 gün → 14 gün)                             │
│      → Threshold, logic, otomasyon DOKUNULMAZ                               │
│      → Sadece izleme ve veri toplama devam eder                            │
│                                                                             │
│  "Her şey yolunda ama yine de bir şeyler yapalım" refleksi YASAKTIR.       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Normal Aralık Tanımı (KESİN):**

```
"Tüm metrikler normal" = 
  S1 ≤ %5 AND 
  OCR suspect ≤ %25 AND 
  Accept rounding ∈ [%2, %25] AND 
  Hint accuracy ≥ %80 AND 
  p95 latency ≤ 3000ms AND 
  Feedback coverage ≥ %30
```

| Metrik | Normal Eşik | Birim |
|--------|-------------|-------|
| S1 rate | ≤ 5% | incident |
| OCR suspect rate | ≤ 25% | incident |
| Accept rounding rate | 2-25% | incident |
| Hint accuracy | ≥ 80% | feedback |
| p95 latency | ≤ 3000ms | request |
| Feedback coverage | ≥ 30% | resolved |

**Aksiyon (Sprint 9 açılmazsa):**
> Gözlem süresi 7 → 14 gün uzatılır, başka hiçbir teknik değişiklik yapılmaz.

---

## 1. Koşullu Task Tablosu

### 1.1 Tetikleme Formatı

Her task şu formatta tanımlanır:
```
EĞER [metrik] [operatör] [eşik] VE n >= [minimum_sample] İSE
    → Sprint 9.X: [Task Adı]
    → Tip: [POC / Review / Otomasyon]
    → Öncelik: [P1 / P2 / P3]
```

---

### 1.2 Sprint 9.1: OCR Normalization

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  KOŞUL                                                                      │
│  ───────────────────────────────────────────────────────────────────────── │
│  EĞER OCR_suspect_rate > 40% VE n >= 50 incident İSE                       │
│                                                                             │
│  TASK                                                                       │
│  ───────────────────────────────────────────────────────────────────────── │
│  Sprint 9.1: OCR Locale Normalization POC                                   │
│                                                                             │
│  KAPSAM                                                                     │
│  • Türkçe locale sorunlarını analiz et (virgül/nokta, binlik ayraç)        │
│  • En sık OCR suspect olan alanları listele                                │
│  • Normalization rule POC'u yaz (test ortamında)                           │
│  • Başarı kriteri: OCR suspect rate %25'e düşmeli                          │
│                                                                             │
│  TİP: POC (Production'a girmez, sadece test)                               │
│  ÖNCELİK: P1                                                                │
│  TAHMİNİ SÜRE: 2-3 gün                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 1.3 Sprint 9.2: Rounding Threshold Review

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  KOŞUL                                                                      │
│  ───────────────────────────────────────────────────────────────────────── │
│  EĞER accept_rounding_rate > 25% VE n >= 50 incident İSE                   │
│  VEYA accept_rounding_rate < 2% VE n >= 50 incident İSE                    │
│                                                                             │
│  KOŞUL AÇIKLAMASI                                                           │
│  ───────────────────────────────────────────────────────────────────────── │
│  > 25% → Eşikler gevşek, gerçek hatalar yutuluyor olabilir                 │
│  < 2%  → Eşikler çok sıkı, gereksiz incident üretiyor olabilir             │
│                                                                             │
│  TASK                                                                       │
│  ───────────────────────────────────────────────────────────────────────── │
│  Sprint 9.2: Rounding Threshold Review                                      │
│                                                                             │
│  KAPSAM                                                                     │
│  • Mevcut ROUNDING_DELTA (10 TL) ve ROUNDING_RATIO (%0.5) analiz et        │
│  • Accept edilen rounding case'lerin delta dağılımını çıkar                │
│  • Yeni threshold önerisi hazırla (manuel karar)                           │
│  • Başarı kriteri: accept_rounding_rate %5-15 aralığına gelmeli            │
│                                                                             │
│  TİP: Review (Manuel analiz, kod değişikliği sonra)                        │
│  ÖNCELİK: P2                                                                │
│  TAHMİNİ SÜRE: 1 gün analiz + karar                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 1.4 Sprint 9.3: ActionHint Logic Review

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  KOŞUL                                                                      │
│  ───────────────────────────────────────────────────────────────────────── │
│  EĞER hint_accuracy_rate < 70% VE n >= 30 feedback İSE                     │
│                                                                             │
│  TASK                                                                       │
│  ───────────────────────────────────────────────────────────────────────── │
│  Sprint 9.3: ActionHint Logic Review                                        │
│                                                                             │
│  KAPSAM                                                                     │
│  • Yanlış hint verilen case'leri listele                                   │
│  • action_router.py mantığını gözden geçir                                 │
│  • Hangi primary_flag → action_class eşleşmeleri yanlış?                   │
│  • Düzeltme önerisi hazırla                                                │
│  • Başarı kriteri: hint_accuracy_rate %80'e çıkmalı                        │
│                                                                             │
│  TİP: Review + Kod değişikliği                                             │
│  ÖNCELİK: P2                                                                │
│  TAHMİNİ SÜRE: 2 gün                                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 1.5 Sprint 9.4: Provider-Specific Tuning

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  KOŞUL                                                                      │
│  ───────────────────────────────────────────────────────────────────────── │
│  EĞER tek provider'da S1_rate > 30% VE o provider n >= 20 İSE              │
│  VE diğer provider'larda S1_rate < 15% İSE                                 │
│                                                                             │
│  TASK                                                                       │
│  ───────────────────────────────────────────────────────────────────────── │
│  Sprint 9.4: Provider-Specific Tuning                                       │
│                                                                             │
│  KAPSAM                                                                     │
│  • Sorunlu provider'ın fatura formatını analiz et                          │
│  • Extraction prompt'a provider-specific örnek ekle                        │
│  • supplier_profiles.py'de özel kural gerekli mi?                          │
│  • Başarı kriteri: O provider'da S1_rate %15'e düşmeli                     │
│                                                                             │
│  TİP: Targeted fix                                                          │
│  ÖNCELİK: P1 (izole sorun, hızlı çözüm)                                    │
│  TAHMİNİ SÜRE: 1-2 gün                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 1.6 Sprint 9.5: Latency Optimization

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  KOŞUL                                                                      │
│  ───────────────────────────────────────────────────────────────────────── │
│  EĞER p95_latency > 5000ms VE n >= 50 request İSE                          │
│  VE extraction_ms / pipeline_total_ms > 80% İSE                            │
│                                                                             │
│  TASK                                                                       │
│  ───────────────────────────────────────────────────────────────────────── │
│  Sprint 9.5: Extraction Latency Optimization                                │
│                                                                             │
│  KAPSAM                                                                     │
│  • OpenAI API call süresini analiz et                                      │
│  • Image preprocessing optimize edilebilir mi?                             │
│  • Prompt token sayısı azaltılabilir mi?                                   │
│  • Caching stratejisi gözden geçir                                         │
│  • Başarı kriteri: p95_latency < 4000ms                                    │
│                                                                             │
│  TİP: Optimization                                                          │
│  ÖNCELİK: P3 (UX etkisi var ama kritik değil)                              │
│  TAHMİNİ SÜRE: 3-5 gün                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 1.7 Sprint 9.6: Feedback Loop Enhancement

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  KOŞUL                                                                      │
│  ───────────────────────────────────────────────────────────────────────── │
│  EĞER feedback_coverage < 30% VE resolved_count >= 50 İSE                  │
│  VE operatör aktif (en az 1 feedback/gün) İSE                              │
│                                                                             │
│  TASK                                                                       │
│  ───────────────────────────────────────────────────────────────────────── │
│  Sprint 9.6: Feedback UX Enhancement                                        │
│                                                                             │
│  KAPSAM                                                                     │
│  • Operatör feedback vermeme nedenlerini araştır                           │
│  • Feedback form'u basitleştir (daha az alan?)                             │
│  • Feedback reminder mekanizması ekle                                      │
│  • Başarı kriteri: feedback_coverage %50'ye çıkmalı                        │
│                                                                             │
│  TİP: UX improvement                                                        │
│  ÖNCELİK: P2                                                                │
│  TAHMİNİ SÜRE: 2-3 gün                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 1.8 Sprint 9.7: Alert Automation (İKİ AŞAMALI)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  KOŞUL (İKİ AŞAMALI KİLİT)                                                  │
│  ───────────────────────────────────────────────────────────────────────── │
│                                                                             │
│  AŞAMA 1: POC                                                               │
│  EĞER pilot 14 gün stabil çalıştı VE n >= 200 İSE                          │
│  → Sprint 9.7a: Otomasyon POC (test ortamında)                             │
│                                                                             │
│  AŞAMA 2: PROD                                                              │
│  EĞER POC +14 gün stabil çalıştı (toplam 28 gün) İSE                       │
│  → Sprint 9.7b: Prod otomasyon kararı                                      │
│                                                                             │
│  ⚠️ Tek periyotla geri dönüşü olmayan adım atılmaz!                        │
│                                                                             │
│  TASK                                                                       │
│  ───────────────────────────────────────────────────────────────────────── │
│  Sprint 9.7: Automated Alerting                                             │
│                                                                             │
│  KAPSAM (Aşama 1 - POC)                                                     │
│  • Slack webhook entegrasyonu (test kanalı)                                │
│  • S1 spike alert (test ortamında)                                         │
│  • Daily digest test Slack'e gönder                                        │
│                                                                             │
│  KAPSAM (Aşama 2 - Prod)                                                    │
│  • Prod Slack kanalına geçiş                                               │
│  • Alert threshold'ları finalize                                           │
│  • On-call rotation entegrasyonu                                           │
│                                                                             │
│  BAŞARI KRİTERİ: Manuel kontrol sıklığı %50 azalmalı                       │
│                                                                             │
│  TİP: Otomasyon (iki aşamalı)                                              │
│  ÖNCELİK: P3 (nice-to-have, kritik değil)                                  │
│  TAHMİNİ SÜRE: Aşama 1: 2 gün, Aşama 2: 1 gün                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Karar Ağacı

```
                              ┌─────────────────┐
                              │  Pilot Verisi   │
                              │   Toplandı mı?  │
                              └────────┬────────┘
                                       │
                         ┌─────────────┴─────────────┐
                         │                           │
                         ▼                           ▼
                 ┌───────────────┐          ┌───────────────┐
                 │   n >= 100    │          │    n < 100    │
                 └───────┬───────┘          └───────┬───────┘
                         │                          │
                         │                          ▼
                         │                  ┌───────────────┐
                         │                  │   BEKLE       │
                         │                  │ Veri topla    │
                         │                  └───────────────┘
                         │
                         ▼
                 ┌───────────────┐
                 │  Metrikler    │
                 │  Normal mi?   │
                 └───────┬───────┘
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
       ┌─────────────┐       ┌─────────────┐
       │    EVET     │       │    HAYIR    │
       │ (Tümü OK)   │       │ (Anomali)   │
       └──────┬──────┘       └──────┬──────┘
              │                     │
              ▼                     ▼
       ┌─────────────┐       ┌─────────────┐
       │  Sprint 9   │       │  Hangi      │
       │  AÇILMAZ    │       │  metrik?    │
       │             │       └──────┬──────┘
       │  Gözlemi    │              │
       │  uzat:      │    ┌─────────┼─────────┬─────────┐
       │  7→14 gün   │    │         │         │         │
       └─────────────┘    ▼         ▼         ▼         ▼
                      ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐
                      │OCR   │  │Round │  │Hint  │  │Prov. │
                      │>40%  │  │>25%  │  │<70%  │  │S1>30%│
                      └──┬───┘  └──┬───┘  └──┬───┘  └──┬───┘
                         │         │         │         │
                         ▼         ▼         ▼         ▼
                      ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐
                      │ 9.1  │  │ 9.2  │  │ 9.3  │  │ 9.4  │
                      │ OCR  │  │Round │  │Hint  │  │Prov. │
                      │ POC  │  │Review│  │Review│  │Tune  │
                      └──────┘  └──────┘  └──────┘  └──────┘
```

---

## 3. Özet Tablo

| Sprint | Koşul | Min n | Tip | Öncelik |
|--------|-------|-------|-----|---------|
| - | Tüm metrikler normal | 100 | **AÇILMAZ** (gözlem 7→14 gün) | - |
| 9.1 | OCR suspect > 40% | 50 | POC | P1 |
| 9.2 | Accept rounding > 25% veya < 2% | 50 | Review | P2 |
| 9.3 | Hint accuracy < 70% | 30 feedback | Review | P2 |
| 9.4 | Tek provider S1 > 30% | 20 (provider) | Fix | P1 |
| 9.5 | p95 latency > 5000ms | 50 | Optimization | P3 |
| 9.6 | Feedback coverage < 30% | 50 resolved | UX | P2 |
| 9.7a | 14 gün stabil | 200 | Otomasyon POC | P3 |
| 9.7b | +14 gün stabil (28 gün toplam) | 200 | Otomasyon Prod | P3 |

---

## 4. Tetikleme Prosedürü

Pilot değerlendirmesi sonunda:

```
1. PILOT_24H_EVALUATION.md formunu doldur
2. Metrikleri bu dokümandaki koşullarla karşılaştır
3. Tetiklenen koşul var mı?
   
   EVET → İlgili Sprint 9.X task'ını aç
          Task açılırken:
          - Tetikleyen metrik değerini yaz
          - Sample size (n) değerini yaz
          - Tarih yaz
   
   HAYIR → Sprint 9 AÇILMAZ
           Gözlem süresini uzat
           14 gün sonra tekrar değerlendir
```

---

## 5. Anti-Pattern'ler (YAPMA)

| Anti-Pattern | Neden Yanlış | Doğru Yaklaşım |
|--------------|--------------|----------------|
| "S1 %8 ama yine de OCR'ı iyileştirelim" | Koşul sağlanmadı | Bekle, gözle |
| "n=15 ama trend kötü görünüyor" | Yetersiz veri | n >= min_sample bekle |
| "Operatör şikayet etti, hemen yapalım" | Veri yok | Metrik topla, sonra karar |
| "Rakip bunu yapıyor, biz de yapalım" | Spekülatif | Kendi verine bak |
| "Boş durmayalım, bir şeyler yapalım" | Stabiliteyi bozar | Sabır kuralı |

---

## 6. Sprint 9 Açılış Şablonu

Bir Sprint 9.X açılacaksa bu şablonu kullan:

```
SPRINT 9.X AÇILIŞ FORMU
=======================
Tarih: ____________________
Açan: ____________________

1. TETİKLEYEN KOŞUL
   Metrik: ____________________
   Değer: ____________________
   Eşik: ____________________
   Sample size (n): ____________________

2. VERİ KAYNAĞI
   Pilot başlangıç: ____________________
   Pilot bitiş: ____________________
   Toplam gün: ____________________

3. TASK DETAYI
   Sprint: 9.____
   Adı: ____________________
   Tipi: [ ] POC  [ ] Review  [ ] Fix  [ ] Optimization  [ ] UX
   Öncelik: [ ] P1  [ ] P2  [ ] P3

4. BAŞARI KRİTERİ
   Hedef metrik: ____________________
   Hedef değer: ____________________

5. TAHMİNİ SÜRE: ____ gün

6. ONAY
   [ ] Koşul doğrulandı
   [ ] Min sample sağlandı
   [ ] Başarı kriteri net
   
   İmza: ____________________
```

---

## 7. Versiyon Geçmişi

| Versiyon | Tarih | Değişiklik |
|----------|-------|------------|
| 1.0 | 2026-01-18 | İlk sürüm |
| 1.1 | 2026-01-18 | Normal tanımı kesinleştirildi, 9.7 iki aşamalı kilit, 9.2 açıklama eklendi |

---

**Bu plan pilot verisi olmadan uygulanmaz. Spekülasyon yasaktır.**
