# Pilot İlk 24 Saat Değerlendirme Şablonu

## Tarih: 2026-01-18
## Versiyon: 1.1
## Durum: OPERASYONEL - FİNAL

---

## ⚠️ ALTIN KURAL (HER KARAR ÖNCESİ OKU)

```
╔═════════════════════════════════════════════════════════════════════════════╗
║                                                                             ║
║   n < 20 ise HİÇBİR metrik için STOP / ROLLBACK kararı verilmez.           ║
║                                                                             ║
║   Bu durumda tek aksiyon: GÖZLEME DEVAM + NOT ALMA                          ║
║                                                                             ║
╚═════════════════════════════════════════════════════════════════════════════╝
```

---

## 0. Temel Kural (Detay)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MİNİMUM SAMPLE GUARD                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   n < 20 ise HİÇBİR "durdur / rollback" kararı verilmez.                   │
│                                                                             │
│   Bu durumda tek aksiyon:                                                   │
│   → Gözleme devam                                                           │
│   → Not al                                                                  │
│   → Bekle                                                                   │
│                                                                             │
│   ÖRNEK:                                                                    │
│   ✗ 5 fatura, 2 S1 = %40 → KARAR VERİLMEZ (n < 20)                         │
│   ✓ 25 fatura, 8 S1 = %32 → PILOT STOP (n >= 20 AND rate > %30)            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Saat Saat Pilot İzleme Tablosu

### Saat 0-1: Sistem Ayakta mı?

| Kontrol | Komut/Yöntem | Beklenen | Aksiyon (Fail) |
|---------|--------------|----------|----------------|
| post_deploy_check.py | `python scripts/post_deploy_check.py` | Exit 0 veya 4 | Exit 1-2 → ROLLBACK |
| /health/ready | `GET /health/ready` | 200 + status=ready | 503 → ROLLBACK |
| pilot.enabled | Response → pilot.enabled | `true` | `false` → Config kontrol |
| Config hash | Response → config_hash | Beklenen hash | Farklı → Yanlış deploy |
| Build ID | Response → build_id | Beklenen commit | Farklı → Yanlış deploy |
| Queue depth | Response → checks.queue.depth | 0-5 | >50 → Investigate |

**Bu saatte karar:** Sadece "sistem çalışıyor mu?" - Evet ise devam, hayır ise rollback.

> ⚠️ **False Positive Farkındalığı:** Bu aralıkta görülen S1, OCR suspect, latency spike'ları istatistiksel anlam taşımaz. Yorum yapılmaz, sadece kaydedilir.

---

### Saat 1-4: İlk Incident Davranışı

| Metrik | Nasıl Bakılır | Not Al |
|--------|---------------|--------|
| İlk incident oluştu mu? | DB veya /admin/incidents | Evet/Hayır |
| Severity dağılımı | S1 vs S2 sayısı | Ham sayılar |
| Primary flag dağılımı | Hangi flag'ler çıkıyor | Liste |
| Action class dağılımı | VERIFY_OCR vs VERIFY_INVOICE_LOGIC | Oran |
| Extraction süresi | Log'lardan p50/p95 | ms cinsinden |

**Bu saatte karar:** 
- n < 20 → Karar yok, sadece gözlem
- Sistem çalışıyor, incident oluşuyor → Devam
- Hiç incident yok ve fatura işlendi → İyi (veya bug?)

---

### Saat 4-12: Pattern Var mı?

| Metrik | Formül | Gözlem |
|--------|--------|--------|
| S1 rate | S1_count / total_incidents | Trend yukarı mı? |
| OCR suspect rate | OCR_suspect / total_incidents | Sabit mi, artıyor mu? |
| Feedback coverage | feedback_count / resolved_count | Operatör kullanıyor mu? |
| p95 latency | Pipeline total ms | Stabil mi? |
| Error rate | 5xx_count / total_requests | 0'a yakın mı? |

**Bu saatte karar:**
- n < 20 → Hala karar yok
- n >= 20 ve metrikler normal → Devam
- n >= 20 ve bir metrik alarm eşiğinde → Karar ağacına git (Bölüm 3)

---

### Saat 12-24: Trend mi, Gürültü mü?

| Analiz | Yöntem | Sonuç |
|--------|--------|-------|
| S1 rate trendi | 4h vs 8h vs 12h karşılaştır | Artıyor / Sabit / Azalıyor |
| Provider yoğunlaşması | Top 3 provider'da S1 oranı | Tek provider mı sorunlu? |
| Saat bazlı dağılım | Sabah vs öğlen vs akşam | Zaman bağımlı mı? |
| Feedback kalitesi | root_cause alanları dolu mu? | Operatör anlamlı yazıyor mu? |

**Bu saatte karar:**
- Trend stabil ve eşikler altında → 24h başarılı, pilot devam
- Trend yukarı ama eşik altında → 24h daha gözlem
- Trend yukarı ve eşik aşıldı (n >= 20) → Karar ağacına git

---

## 2. Metrik → Eşik → Aksiyon Tablosu

### 2.0 STOP vs ROLLBACK Ayrımı (KRİTİK)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         STOP vs ROLLBACK FARKI                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  STOP (Pilot Stop)                                                          │
│  ─────────────────                                                          │
│  Tetikleyici:                                                               │
│    • S1 rate > %30 AND n >= 20                                              │
│    • feedback_coverage < %10 AND n >= 20 (resolved)                         │
│  Aksiyon:                                                                   │
│    → PILOT_ENABLED=false                                                    │
│    → Sistem AYAKTA kalır                                                    │
│    → Pilot durur, prod etkilenmez                                           │
│                                                                             │
│  ROLLBACK (Sistem Geri Alma)                                                │
│  ──────────────────────────────                                             │
│  Tetikleyici:                                                               │
│    • /health/ready = 503 > 5 dakika                                         │
│    • 5xx error rate > %5 (10 dakika window, n >= 100 request)               │
│    • Queue stuck > 15 dakika (depth artıyor + 0 tüketim)                    │
│  Aksiyon:                                                                   │
│    → Deploy rollback (önceki stable image)                                  │
│    → PILOT_ENABLED=false                                                    │
│    → Sistem eski haline döner                                               │
│                                                                             │
│  ⚠️ STOP ≠ ROLLBACK: Panik anında her şeyi geri alma refleksinden kaçın!   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 Kritik Metrikler (Rollback Tetikleyici)

| Metrik | STOP Eşiği | Min n | Aksiyon |
|--------|------------|-------|---------|
| /health/ready | 503 > 5 dakika | - | ROLLBACK (n bağımsız) |
| 5xx error rate | > 5% | 100 request | ROLLBACK |
| Queue stuck | depth artıyor + 0 tüketim > 15dk | - | ROLLBACK (n bağımsız) |
| S1 rate | > 30% | 20 incident | PILOT STOP |

### 2.2 Uyarı Metrikleri (Investigate)

| Metrik | Uyarı Eşiği | Min n | Aksiyon |
|--------|-------------|-------|---------|
| S1 rate | > 20% | 20 | Investigate, durma |
| OCR suspect rate | > 40% | 20 | Investigate, durma |
| p95 latency | > 2x baseline (6000ms) | 20 | Investigate, durma |
| Feedback coverage | < 10% | 20 resolved | Operatör eğitimi kontrol |

### 2.3 Bilgi Metrikleri (Sadece Not Al)

| Metrik | Normal Aralık | Not |
|--------|---------------|-----|
| S2 rate | 10-25% | Çok düşük = OCR çok iyi veya bug |
| Accept rounding rate | 5-15% | Çok yüksek = threshold çok gevşek |
| Avg extraction time | 1500-3000ms | Model bağımlı |

---

## 3. Karar Ağacı

```
                              ┌─────────────────┐
                              │ Metrik Kontrolü │
                              └────────┬────────┘
                                       │
                         ┌─────────────┴─────────────┐
                         │                           │
                         ▼                           ▼
                 ┌───────────────┐          ┌───────────────┐
                 │ n >= 20 mi?   │          │ Sistem hatası │
                 └───────┬───────┘          │ (503, stuck)  │
                         │                  └───────┬───────┘
              ┌──────────┴──────────┐               │
              │                     │               ▼
              ▼                     ▼        ┌─────────────┐
       ┌─────────────┐       ┌─────────────┐ │  ROLLBACK   │
       │   n >= 20   │       │   n < 20    │ │  (hemen)    │
       └──────┬──────┘       └──────┬──────┘ └─────────────┘
              │                     │
              ▼                     ▼
       ┌─────────────┐       ┌─────────────┐
       │ Eşik aşıldı │       │  GÖZLEME    │
       │     mı?     │       │   DEVAM     │
       └──────┬──────┘       │  (not al)   │
              │              └─────────────┘
    ┌─────────┴─────────┐
    │                   │
    ▼                   ▼
┌─────────┐      ┌─────────────┐
│  EVET   │      │    HAYIR    │
└────┬────┘      └──────┬──────┘
     │                  │
     ▼                  ▼
┌─────────────┐  ┌─────────────┐
│ Kritik mi?  │  │   DEVAM     │
│ (S1>30%,    │  │  (normal)   │
│  5xx>5%)    │  └─────────────┘
└──────┬──────┘
       │
  ┌────┴────┐
  │         │
  ▼         ▼
┌─────┐  ┌──────────┐
│EVET │  │  HAYIR   │
└──┬──┘  └────┬─────┘
   │          │
   ▼          ▼
┌────────┐ ┌───────────┐
│ PILOT  │ │INVESTIGATE│
│  STOP  │ │ (15 dk)   │
└────────┘ └─────┬─────┘
                 │
           ┌─────┴─────┐
           │           │
           ▼           ▼
      ┌────────┐  ┌────────┐
      │Düzeldi │  │Düzelmedi│
      └───┬────┘  └───┬────┘
          │           │
          ▼           ▼
      ┌──────┐   ┌────────┐
      │DEVAM │   │ PILOT  │
      └──────┘   │  STOP  │
                 └────────┘
```

---

## 4. Pilot Stop Prosedürü

Karar ağacında "PILOT STOP" çıktıysa:

```bash
# 1. Kill switch (hemen)
export PILOT_ENABLED=false
# veya: kubectl set env deployment/api PILOT_ENABLED=false

# 2. Verify
curl -s https://api.example.com/health/ready | jq '.pilot.enabled'
# Beklenen: false

# 3. Not al
echo "$(date): Pilot stopped - Reason: [SEBEP]" >> pilot_log.txt

# 4. Rollback gerekli mi?
# - Sadece pilot stop → Rollback GEREKMEZ
# - Sistem hatası (503, stuck) → Rollback GEREKIR
```

---

## 5. 24 Saat Sonu Değerlendirme Formu

Pilot 24 saat sonunda bu formu doldur:

```
PILOT 24H DEĞERLENDİRME
=======================
Tarih: ____________________
Değerlendiren: ____________________

1. ÖZET METRİKLER
   Total invoices processed: ____
   Total incidents created: ____
   S1 count: ____ (___%)
   S2 count: ____ (___%)
   OCR suspect count: ____ (___%)
   Feedback count: ____ (coverage: ___%)

2. SİSTEM SAĞLIĞI
   /health/ready uptime: ____%
   5xx error count: ____
   Max queue depth: ____
   p95 latency: ____ ms

3. GÖZLEMLER
   En sık primary_flag: ____________________
   En sık action_class: ____________________
   Sorunlu provider (varsa): ____________________
   Operatör feedback kalitesi: [İyi / Orta / Zayıf]

4. KARAR
   [ ] PILOT DEVAM - Metrikler normal, genişletmeye hazır
   [ ] PILOT DEVAM - Gözlem süresi uzat (sebep: _________)
   [ ] PILOT DURDUR - Kritik eşik aşıldı (sebep: _________)
   [ ] ROLLBACK - Sistem hatası (sebep: _________)

5. NOTLAR
   ________________________________________________
   ________________________________________________
   ________________________________________________

İmza: ____________________
```

---

## 6. Hızlı Referans Kartı (Yazdır ve As)

```
┌─────────────────────────────────────────────────────────────────┐
│                    PILOT 24H HIZLI REFERANS                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ⚠️ ALTIN KURAL: n < 20 → HİÇBİR STOP/ROLLBACK KARARI VERME   │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  ROLLBACK (hemen, sistem hatası)                                │
│  • /health/ready 503 > 5 dakika                                 │
│  • Queue stuck > 15 dakika                                      │
│  • 5xx > 5% (n >= 100 request)                                  │
│  → Deploy geri al + PILOT_ENABLED=false                         │
├─────────────────────────────────────────────────────────────────┤
│  PILOT STOP (n >= 20 zorunlu, sistem ayakta kalır)              │
│  • S1 rate > 30%                                                │
│  • Feedback coverage < 10%                                      │
│  → PILOT_ENABLED=false (deploy geri alınmaz)                    │
├─────────────────────────────────────────────────────────────────┤
│  INVESTIGATE (n >= 20 zorunlu, durma)                           │
│  • S1 rate > 20%                                                │
│  • OCR suspect > 40%                                            │
│  • p95 latency > 6000ms                                         │
├─────────────────────────────────────────────────────────────────┤
│  KONTROL SIKLIĞI                                                │
│  • Saat 0-1: Her 15 dakika                                      │
│  • Saat 1-4: Her 30 dakika                                      │
│  • Saat 4-12: Her 2 saat                                        │
│  • Saat 12-24: Her 4 saat                                       │
├─────────────────────────────────────────────────────────────────┤
│  KOMUTLAR                                                       │
│  • Health: curl /health/ready | jq .                            │
│  • Stop: export PILOT_ENABLED=false                             │
│  • Check: python scripts/post_deploy_check.py                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. Appendix: Metrik Toplama Komutları

> ⚠️ **Zaman Penceresi Notu:** Pilot değerlendirmesinde sorgular mutlaka son X saat ile sınırlandırılmalıdır. Tüm tablo taraması yanlış büyük sayılarla karar verilmesine yol açar.

```bash
# /health/ready tam response
curl -s https://api.example.com/health/ready | jq .

# Pilot status
curl -s https://api.example.com/health/ready | jq '.pilot'

# Son 1 saatteki incident sayısı
# NOT: created_at filtresini değerlendirme penceresine göre ayarlayın
sqlite3 gelka_enerji.db "
  SELECT 
    COUNT(*) as total,
    SUM(CASE WHEN severity='S1' THEN 1 ELSE 0 END) as s1_count,
    SUM(CASE WHEN severity='S2' THEN 1 ELSE 0 END) as s2_count
  FROM incidents 
  WHERE created_at > datetime('now', '-1 hour')
    AND tenant_id = 'pilot'
"

# S1 rate hesaplama (son 4 saat)
# NOT: Zaman penceresini duruma göre ayarlayın (-1 hour, -4 hours, -24 hours)
sqlite3 gelka_enerji.db "
  SELECT 
    COUNT(*) as total_incidents,
    ROUND(100.0 * SUM(CASE WHEN severity='S1' THEN 1 ELSE 0 END) / COUNT(*), 2) as s1_rate_pct
  FROM incidents 
  WHERE tenant_id = 'pilot'
    AND created_at > datetime('now', '-4 hours')
"

# Primary flag dağılımı (son 24 saat)
sqlite3 gelka_enerji.db "
  SELECT primary_flag, COUNT(*) as cnt
  FROM incidents 
  WHERE tenant_id = 'pilot'
    AND created_at > datetime('now', '-24 hours')
  GROUP BY primary_flag
  ORDER BY cnt DESC
"

# Feedback coverage (son 24 saat, sadece resolved)
sqlite3 gelka_enerji.db "
  SELECT 
    COUNT(*) as resolved,
    SUM(CASE WHEN feedback_json IS NOT NULL THEN 1 ELSE 0 END) as with_feedback,
    ROUND(100.0 * SUM(CASE WHEN feedback_json IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 2) as coverage_pct
  FROM incidents 
  WHERE tenant_id = 'pilot' 
    AND status = 'RESOLVED'
    AND created_at > datetime('now', '-24 hours')
"
```

---

**Bu doküman operasyon ekibine verilir. Tartışma çıkarmaz.**
