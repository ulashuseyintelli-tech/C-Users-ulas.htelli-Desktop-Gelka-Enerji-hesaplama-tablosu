# Nominal vs Gerçek Marj Analizi — Teknik Tasarım

## Mimari Genel Bakış

```
┌──────────────────────────────────────────────────────────────┐
│                     Frontend (App.tsx)                         │
│                                                                │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │              ANA KARAR PANELİ                            │  │
│  │  ┌───────────────────────────────────────────────────┐  │  │
│  │  │  ● MARJ ERİYOR                          -2.3%    │  │  │
│  │  │                                                    │  │  │
│  │  │  Nominal Marj:  %4.0   →   4.200 TL              │  │  │
│  │  │  Gerçek Marj:   %1.7   →   1.853 TL              │  │  │
│  │  │  Sapma:        -2.3%   →  -2.347 TL              │  │  │
│  │  │                                                    │  │  │
│  │  │  Girilen Katsayı: ×1.04                           │  │  │
│  │  │  Effective Multiplier: ×1.017                     │  │  │
│  │  │  Break-even: ×1.003  |  Güvenli: ×1.013          │  │  │
│  │  └───────────────────────────────────────────────────┘  │  │
│  │                                                          │  │
│  │  ┌─────────────────┐  ┌──────────────────────────────┐  │  │
│  │  │ En Kötü 10 Saat │  │ En İyi 10 Saat              │  │  │
│  │  │ (zarar)         │  │ (kâr)                        │  │  │
│  │  └─────────────────┘  └──────────────────────────────┘  │  │
│  │                                                          │  │
│  │  ┌───────────────────────────────────────────────────┐  │  │
│  │  │ Profil Riski (yardımcı)                           │  │  │
│  │  │ T1: %70 | T2: %25 | T3: %5 | Sapma: %5.2       │  │  │
│  │  └───────────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────┘  │
└───────────────────────────┬──────────────────────────────────┘
                            │ POST /api/pricing/analyze
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                  Backend (pricing/)                            │
│                                                                │
│  Mevcut akış (KORUNUYOR — dokunulmaz):                        │
│  ├─ weighted_prices      → ağırlıklı PTF                     │
│  ├─ time_zone_breakdown  → T1/T2/T3                          │
│  ├─ risk_score           → profil riski (yardımcı)           │
│  ├─ loss_map             → zarar haritası                     │
│  └─ safe_multiplier      → güvenli katsayı                   │
│                                                                │
│  YENİ eklenen:                                                │
│  └─ margin_reality       → gerçek marj hesabı                │
│     ├─ margin_reality.py :: calculate_margin_reality()        │
│     ├─ verdict: KARLI / OVERPERFORM / MARJ_ERIYOR / ZARARLI │
│     ├─ effective_multiplier                                   │
│     ├─ break_even_multiplier                                  │
│     ├─ worst_hours / best_hours                               │
│     └─ hourly_margins (histogram verisi)                      │
└──────────────────────────────────────────────────────────────┘
```

## Backend Değişiklikleri

### 1. Yeni Modül: `backend/app/pricing/margin_reality.py`

```python
"""
Nominal vs Gerçek Marj Analizi — Marj Sapma Motoru.

Ana soru: "Ben bu müşteriye %4 marjla sattım; saatlik tüketim
profiline göre gerçekte kaç % marj kazandım?"

Çekirdek gerçek:
  - Sabit fiyat satıyorsun (teklif birim fiyat)
  - Değişken maliyetle alıyorsun (saatlik PTF)
  - Bu yüzden: Marj = profil fonksiyonu
"""

def calculate_margin_reality(
    offer_ptf_tl_per_mwh: float,           # Teklif PTF (dönem ortalaması)
    yekdem_tl_per_mwh: float,              # YEKDEM
    multiplier: float,                      # Katsayı (örn: 1.04)
    hourly_ptf_prices: list[float],         # Saatlik PTF verileri (TL/MWh)
    hourly_consumption_kwh: list[float],    # Saatlik tüketim (kWh)
    include_yekdem: bool = True,
    margin_erosion_threshold_pct: float = 1.0,  # Marj eriyor eşiği (%)
    safe_multiplier_buffer: float = 0.01,       # Güvenli katsayı tamponu
) -> MarginRealityResult:
    """
    Nominal (kağıt üzeri) marj ile gerçek (saatlik) marjı karşılaştır.
    
    Returns:
        MarginRealityResult: Tüm marj metrikleri ve karar.
    """
```

**Hesaplama adımları:**
1. Teklif birim fiyat hesapla (sabit)
2. Her saat için maliyet hesapla (değişken)
3. Her saat için marj hesapla
4. Topla, oranla
5. Effective multiplier hesapla
6. Break-even katsayı hesapla
7. En kötü/en iyi 10 saat belirle
8. Karar ver (verdict)

### 2. Yeni Modeller: `backend/app/pricing/models.py`'ye eklenir

```python
class MarginVerdict(str, Enum):
    """Marj gerçekleşme kararı."""
    PROFITABLE = "Kârlı"           # Gerçek ≈ Nominal (sapma ±eşik içinde)
    OVERPERFORM = "Overperform"     # Gerçek > Nominal
    MARGIN_ERODING = "Marj Eriyor"  # Gerçek < Nominal ama > 0
    LOSS = "Zararlı"                # Gerçek < 0

class HourlyMarginDetail(BaseModel):
    """Tek saatlik marj detayı (en kötü/en iyi tablolar için)."""
    hour: str                       # "2026-03-15 18:00"
    ptf_tl_per_mwh: float
    consumption_kwh: float
    cost_tl: float
    margin_tl: float

class MarginRealityResult(BaseModel):
    """Nominal vs Gerçek Marj Analizi sonucu."""
    verdict: MarginVerdict
    
    # Katsayı bilgisi
    multiplier: float
    effective_multiplier: float     # Gerçekleşen katsayı etkisi
    
    # Nominal (kağıt üzeri) hesap
    nominal_margin_pct: float       # (katsayı - 1) × 100
    nominal_margin_tl: float        # dönem ortalaması ile hesaplanan marj
    
    # Gerçek (saatlik) hesap
    real_margin_pct: float          # saatlik hesapla bulunan gerçek marj %
    real_margin_tl: float           # saatlik hesapla bulunan gerçek marj TL
    
    # Sapma (EN KRİTİK METRİK)
    margin_deviation_pct: float     # gerçek - nominal (+ iyi, - kötü)
    margin_deviation_tl: float
    
    # Saat detayları
    total_hours: int
    negative_margin_hours: int
    negative_margin_total_tl: float
    positive_margin_total_tl: float
    
    # Katsayı önerileri
    break_even_multiplier: float
    safe_multiplier: float
    
    # Toplam tutarlar
    total_offer_tl: float
    total_cost_tl: float
    total_consumption_kwh: float
    offer_unit_price_tl_per_kwh: float
    weighted_cost_tl_per_kwh: float
    
    # En kötü / en iyi saatler
    worst_hours: list[HourlyMarginDetail]   # En çok zarar edilen 10 saat
    best_hours: list[HourlyMarginDetail]    # En çok kâr edilen 10 saat
    
    # Histogram verisi (frontend grafik için)
    hourly_margins_tl: list[float]          # Tüm saatlerin marj değerleri
```

### 3. API Değişikliği: `POST /api/pricing/analyze` response

```json
{
  "supplier_cost": { ... },
  "pricing": { ... },
  "time_zone_breakdown": { ... },
  "loss_map": { ... },
  "risk_score": { ... },              // KORUNUYOR (yardımcı)
  "safe_multiplier": { ... },
  "margin_reality": {                  // YENİ — ANA KARAR
    "verdict": "Marj Eriyor",
    "multiplier": 1.04,
    "effective_multiplier": 1.017,
    "nominal_margin_pct": 4.0,
    "nominal_margin_tl": 4200.0,
    "real_margin_pct": 1.7,
    "real_margin_tl": 1853.0,
    "margin_deviation_pct": -2.3,
    "margin_deviation_tl": -2347.0,
    "total_hours": 744,
    "negative_margin_hours": 421,
    "negative_margin_total_tl": -39161.0,
    "positive_margin_total_tl": 41014.0,
    "break_even_multiplier": 1.003,
    "safe_multiplier": 1.013,
    "total_offer_tl": 109380.0,
    "total_cost_tl": 107527.0,
    "total_consumption_kwh": 44444.0,
    "offer_unit_price_tl_per_kwh": 2.4617,
    "weighted_cost_tl_per_kwh": 2.4197,
    "worst_hours": [
      {"hour": "2026-03-15 18:00", "ptf_tl_per_mwh": 3450, "consumption_kwh": 85, "cost_tl": 357, "margin_tl": -42.5},
      ...
    ],
    "best_hours": [
      {"hour": "2026-03-08 04:00", "ptf_tl_per_mwh": 980, "consumption_kwh": 62, "cost_tl": 107, "margin_tl": 45.2},
      ...
    ],
    "hourly_margins_tl": [12.3, -5.1, 8.7, ...]
  }
}
```

## Frontend Değişiklikleri

### Ana Karar Paneli Değişikliği

**ESKİ (kaldırılacak ana karar olarak):**
```
Risk Seviyesi: Yüksek  ← bu artık ana karar değil
```

**YENİ (ana karar):**
```
┌─────────────────────────────────────────────┐
│  ● MARJ ERİYOR                     -2.3%   │
│                                              │
│  Nominal:  %4.0  →  4.200 TL               │
│  Gerçek:   %1.7  →  1.853 TL               │
│                                              │
│  Katsayı: ×1.04 → Effective: ×1.017        │
│  Break-even: ×1.003 | Güvenli: ×1.013      │
│                                              │
│  Negatif Saat: 421/744                      │
│  Zarar: -39.161 TL | Kâr: +41.014 TL       │
├─────────────────────────────────────────────┤
│  Profil Riski (yardımcı):                   │
│  T1: %70 | T2: %25 | T3: %5 | Sapma: %5.2 │
└─────────────────────────────────────────────┘
```

### Renk Kodları
| Verdict | Arka Plan | Metin | İkon |
|---------|-----------|-------|------|
| Kârlı | `bg-green-50` | `text-green-700` | ✅ |
| Overperform | `bg-blue-50` | `text-blue-700` | 🚀 |
| Marj Eriyor | `bg-amber-50` | `text-amber-700` | ⚠️ |
| Zararlı | `bg-red-50` | `text-red-700` | 🔴 |

## Dosya Değişiklikleri

| Dosya | Değişiklik | Dokunulacak mı? |
|-------|-----------|-----------------|
| `backend/app/pricing/margin_reality.py` | **YENİ** — ana hesaplama motoru | Yeni dosya |
| `backend/app/pricing/models.py` | MarginVerdict, HourlyMarginDetail, MarginRealityResult eklenir | Ekleme |
| `backend/app/pricing/router.py` | analyze endpoint'ine margin_reality hesabı eklenir | Ekleme |
| `backend/app/pricing/risk_calculator.py` | **DOKUNULMAZ** — mevcut profil riski korunur | Hayır |
| `frontend/src/App.tsx` | Risk paneli güncellenir — ana karar margin_reality olur | Güncelleme |

## Doğrulama Kontrol Listesi

Bu 5 soruya EVET cevabı verilmeli:

| # | Soru | Cevap |
|---|------|-------|
| 1 | "Gerçek Saatlik Marj TL" ve "Gerçek Marj %" var mı? | ✅ |
| 2 | "Marj Sapması" (gerçek – nominal) tanımlı mı? | ✅ |
| 3 | Sistem hâlâ "risk yüksek/düşük" mü ana karar olarak konuşuyor? | ❌ Hayır, yardımcı |
| 4 | Teklif fiyatı sabit, maliyet saatlik mi? | ✅ |
| 5 | Break-even katsayı var mı? | ✅ |

**Kritik test:** "%4 sattım ama aslında kaç % kazandım?" sorusuna net cevap veriyor mu? → **EVET**

---

## Faz 2 (İleri Özellikler)

### F2.1: Worst-case Simülasyonu
Profil %10 daha pahalı saatlere kayarsa marj ne olur?

### F2.2: Segment Bazlı Eşikler
Farklı müşteri tipleri için farklı marj eriyor eşikleri.

### F2.3: Çoklu Dönem Karşılaştırması
Aynı müşterinin farklı dönemlerdeki marj sapması trendi.

### F2.4: Portföy Analizi
Tüm müşterilerin toplam marj sapması dashboard'u.
