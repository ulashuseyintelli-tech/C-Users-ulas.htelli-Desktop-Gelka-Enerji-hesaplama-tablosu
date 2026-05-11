# Pricing Consistency Fixes — Bugfix Design (v3)

## Overview

Bu doküman, enerji fiyatlandırma uygulamasındaki kritik hesaplama/veri tutarlılığı hatalarının düzeltme tasarımını içerir. v3 güncellemesi ticari model boşluklarını kapatır.

**Kritik Mimari Kararlar:**
1. **Dual Margin Model** — Enerji Brüt Marjı ve Toplam Brüt Marj ayrı üretilir
2. **Dual Sales Price** — Enerji satış fiyatı ve efektif toplam fiyat ayrı üretilir
3. **Tam Net Marj Formülü** — Tüm gider kalemleri tek formülde
4. **Per-MWh vs Total TL Ayrışması** — Açık naming convention zorunlu
5. **YEKDEM Severity** — Sadece warning değil, severity + impact flag
6. **Distribution Lookup → Engine Entegrasyonu** — router → lookup → engine akışı
7. **Frontend Cache + Fallback + UI Gösterim Stratejisi**

**Scope Kararları:**
- `calculator.py` — SCOPE DIŞI (farklı iş akışı: fatura analizi vs risk analizi)
- Bayi komisyon tabanı — DEĞİŞMEYECEK (mevcut puan paylaşımı modeli bilinçli tasarım kararı, bkz. "Tasarım Kararları" bölümü)
- 3 katmanlı mimari (Cost/Risk/Commercial) — Ayrı spec olarak planlanacak, bu bugfix'te yapılmayacak

## Finansal Model (Tek Doğru Kaynak)

```
═══════════════════════════════════════════════════════════════════
ENERJI TEDARİK FİYATLAMA MODELİ — KANONİK FORMÜLLER (v3)
═══════════════════════════════════════════════════════════════════

MALIYET KATEGORİLERİ:
  Enerji Maliyeti     = PTF + YEKDEM           (tedarikçi kontrol eder)
  Regüle Maliyet      = Dağıtım + İletim       (EPDK belirler, pass-through)
  Dengesizlik Maliyeti = f(PTF, SMF, forecast)  (piyasa riski)

SATIŞ (İKİLİ FİYAT MODELİ):
  ┌─────────────────────────────────────────────────────────────┐
  │ Enerji Satış Fiyatı (tedarikçinin belirlediği)             │
  │   = (PTF + YEKDEM) × Katsayı                               │
  │   → Katsayı üzerinden marj buradan gelir                   │
  │   → Faturada "Aktif Enerji Bedeli" satırı                  │
  ├─────────────────────────────────────────────────────────────┤
  │ Efektif Toplam Fiyat (müşterinin ödediği birim fiyat)      │
  │   = Enerji Satış Fiyatı + Dağıtım Birim Fiyatı            │
  │   → Müşteri BU değere bakar                                │
  │   → "kWh başına toplam maliyet" sorusunun cevabı           │
  └─────────────────────────────────────────────────────────────┘

  Satış Geliri (TL)   = Σ(kWh_h × Enerji Satış Fiyatı_h / 1000)

MARJLAR (İKİLİ MODEL — İKİSİ DE ÜRETİLİR):
  ┌─────────────────────────────────────────────────────────────┐
  │ Enerji Brüt Marjı (Tedarikçi Marjı)                       │
  │   = Satış Geliri - Enerji Maliyeti                         │
  │   = Satış Geliri - (PTF + YEKDEM) toplam                   │
  │   → Tedarikçinin enerji üzerinden kazandığı gerçek marj    │
  │   → Satış ekibi BU değere bakar                            │
  │   → Katsayı 1.15 ise bu ≈ %15 × Enerji Maliyeti           │
  ├─────────────────────────────────────────────────────────────┤
  │ Toplam Brüt Marj (Ticari Marj)                             │
  │   = Satış Geliri - (Enerji + Regüle) toplam                │
  │   = Satış Geliri - (PTF + YEKDEM + Dağıtım) toplam        │
  │   → Müşteriye gösterilen toplam maliyet farkı              │
  │   → Müşteri BU değere bakar                                │
  │   → Dağıtım pass-through olduğu için her zaman ≤ Enerji   │
  └─────────────────────────────────────────────────────────────┘

NET MARJ (TEK FORMÜL — TÜM GİDERLER DAHİL):
  Net Marj = Satış Geliri
             - PTF toplam
             - YEKDEM toplam
             - Dağıtım toplam
             - Dengesizlik toplam
             - Bayi Komisyonu

BAYİ KOMİSYONU (PUAN PAYLAŞIMI MODELİ — DEĞİŞMEYECEK):
  Baz Enerji       = Σ(kWh_h × (PTF_h + YEKDEM) / 1000)
  Toplam Puan      = (Katsayı - 1) × 100        (örn: 1.15 → 15 puan)
  Bayi Puanı       = Segment tablosundan sabit   (örn: Yüksek+ → 3 puan)
  Bayi Komisyonu   = Baz Enerji × (Bayi Puanı / 100)
  Gelka Net        = Baz Enerji × ((Toplam Puan - Bayi Puanı) / 100)

  NOT: Bayi komisyonu maliyet tabanında (Baz Enerji) hesaplanır,
  marj tabanında değil. Bu bilinçli bir tasarım kararıdır:
  - Minimum katsayı 1.01 → enerji marjı her zaman pozitif
  - Puan modeli sabit ve öngörülebilir (bayi ne alacağını bilir)
  - Marj bazlı model volatil olur (PTF değişince bayi payı değişir)
  - Bu modeli değiştirmek ayrı bir ticari karar gerektirir

DENGESİZLİK MALİYETİ MODELİ (MEVCUT — imbalance.py):
  SMF bazlı (smf_based_imbalance_enabled=true):
    imbalance_cost = forecast_error_rate × abs(weighted_smf - weighted_ptf)
  Sabit maliyet (smf_based_imbalance_enabled=false):
    imbalance_cost = imbalance_cost_tl_per_mwh (kullanıcı girer)
  Fallback (parametre yoksa):
    imbalance_cost = 0 (uyarı ile)

NAMING CONVENTION (ZORUNLU):
  *_per_mwh    → TL/MWh biriminde (per-unit)
  *_total_tl   → Toplam TL (absolute)
  
  Tam alan listesi:
    sales_energy_price_per_mwh         → (PTF+YEKDEM) × katsayı
    sales_effective_price_per_mwh      → enerji fiyatı + dağıtım
    gross_margin_energy_per_mwh        / gross_margin_energy_total_tl
    gross_margin_total_per_mwh         / gross_margin_total_total_tl
    net_margin_per_mwh                 / net_margin_total_tl
    energy_cost_per_mwh                / energy_cost_total_tl
    distribution_cost_per_mwh          / distribution_cost_total_tl
    imbalance_cost_per_mwh             / imbalance_cost_total_tl
    dealer_commission_per_mwh          / dealer_commission_total_tl
═══════════════════════════════════════════════════════════════════
```

## Glossary

- **Enerji Satış Fiyatı**: (PTF + YEKDEM) × Katsayı. Tedarikçinin belirlediği enerji birim fiyatı.
- **Efektif Toplam Fiyat**: Enerji Satış Fiyatı + Dağıtım. Müşterinin ödediği kWh başı toplam.
- **Enerji Brüt Marjı**: Satış - (PTF + YEKDEM). Tedarikçinin gerçek kazancı.
- **Toplam Brüt Marj**: Satış - (PTF + YEKDEM + Dağıtım). Müşteriye gösterilen fark.
- **Net Marj**: Tüm giderler düşüldükten sonra kalan.
- **Regüle Maliyet**: EPDK tarafından belirlenen pass-through maliyetler.
- **Baz Enerji**: kWh × (PTF + YEKDEM) / 1000 — bayi komisyon hesap tabanı.

## Bug Details

### Bug Condition

**C1 — Brüt Marj Tek Boyutlu + Satış Fiyatı Eksik:** Sistem tek brüt marj üretiyor. Enerji vs toplam ayrışmamış. Efektif toplam fiyat alanı yok.

**C2 — Net Marj Eksik Giderler:** Per-MWh net marjda dağıtım ve dengesizlik düşülmez. Toplam TL hesabında dengesizlik var ama dağıtım yok. Modüller arası tutarsızlık.

**C3 — Frontend Hardcode Tarife:** `TARIFF_PERIODS` hardcode dizisinden okur, backend API çağırmaz.

**C4 — YEKDEM 404:** YEKDEM kaydı yoksa `HTTPException(404)` fırlatılır.

### Examples

- **C1:** Katsayı 1.15, PTF=1500, YEKDEM=150, Dağıtım=0.81 TL/kWh. Sistem tek `gross_margin` gösterir. Satış ekibi "%15 marj" der ama dağıtım düşünce toplam marj çok düşük. İkili model + efektif fiyat olsaydı: `energy_price=1897.5`, `effective_price=2707.5 TL/MWh`, `energy_margin=247.5`, `total_margin=-562.5` → satış ekibi fiyatı düzeltirdi.
- **C2:** `net_margin_per_mwh = 222` ama gerçek net marj dağıtım ve dengesizlik düşünce `-613`. Raporlar tutarsız.
- **C3:** EPDK Nisan 2026 tarifesi backend'de güncellenir, frontend eski değerleri gösterir.
- **C4:** `period=2025-06`, YEKDEM yok → 404, analiz başarısız.

## Expected Behavior

### Preservation Requirements

**Unchanged:**
- Saatlik `base_cost_tl = kWh × (PTF + YEKDEM) / 1000` korunur
- Ağırlıklı PTF hesaplama (kWh ağırlıklı ortalama) korunur
- Bayi komisyon modeli (puan paylaşımı, maliyet tabanı) korunur
- `calculator.py` dokunulmaz (scope dışı)
- Admin endpoint'ler korunur
- Cache mekanizması korunur
- Frontend BTV, KDV, tasarruf hesaplamaları korunur
- Dengesizlik hesaplama modeli (`imbalance.py`) korunur

## Tasarım Kararları

### Kabul Edilen Değişiklikler
1. ✅ Dual sales price (energy + effective)
2. ✅ Dual margin model (energy + total)
3. ✅ Tam net marj formülü (tüm giderler)
4. ✅ Per-MWh / Total-TL naming convention
5. ✅ YEKDEM severity + impact flag
6. ✅ Distribution lookup → engine entegrasyonu
7. ✅ Frontend cache + fallback + UI gösterim stratejisi

### Safety Guards (v3.1 — Ticari Risk Kontrolü)
8. ✅ Dealer commission cap: `dealer_commission = max(0, min(dealer_commission, energy_margin))` — negatif marjda 0, pozitif marjda marjı aşamaz
9. ✅ Imbalance floor (per-MWh bazlı): `imbalance_cost_per_mwh = max(calculated_per_mwh, weighted_ptf * RISK_FLOOR)` sonra `total_tl = per_mwh * kwh / 1000`
10. ✅ Customer savings metric + source: `customer_savings_per_mwh = customer_ref_price - effective_price` + `customer_reference_price_source: "invoice" | "manual_input" | "market_estimate"`
11. ✅ Risk flags (öncelik sıralı): P1: `LOSS_RISK` (net < 0), P2: `UNPROFITABLE_OFFER` (gross_total < 0). İkisi de aynı anda olabilir, ikisi de döner. Frontend: LOSS_RISK → kırmızı + teklif önerme kapalı, UNPROFITABLE → sarı + uyarı

### Reddedilen / Ertelenen Değişiklikler
1. ❌ Bayi komisyon tabanını marj bazlı yapmak — Mevcut puan modeli bilinçli tasarım. Bunun yerine commission cap eklendi (safety guard #8).
2. ❌ 3 katmanlı mimari (Cost/Risk/Commercial) — Doğru hedef ama bu bugfix scope'u değil. Ayrı spec olarak planlanacak.
3. ❌ Dengesizlik modeli değişikliği — Mevcut `imbalance.py` korunur. Bunun yerine imbalance floor eklendi (safety guard #9).

## Hypothesized Root Cause

1. **C1:** `pricing_engine.py` tek `total_gross_margin` üretiyor. `router.py` tek `gross_margin_per_mwh` üretiyor. Efektif fiyat alanı hiçbir yerde yok.
2. **C2:** `router.py` per-MWh: `net = gross - dealer` (dağıtım ve dengesizlik eksik). `pricing_engine.py` total: `net = gross - dealer - imbalance` (dağıtım eksik).
3. **C3:** `App.tsx` hardcode `TARIFF_PERIODS` dizisi. Public API endpoint yok.
4. **C4:** `router.py`: `if not yekdem_record: raise HTTPException(404)`

## Correctness Properties

**Property 0: Safety Guards**

_For any_ pricing result:
- `dealer_commission_total_tl >= 0` always (no negative dealer payout)
- `dealer_commission_total_tl <= max(0, gross_margin_energy_total_tl)` (commission cap)
- `imbalance_cost_per_mwh >= weighted_ptf * RISK_FLOOR` where RISK_FLOOR = 0.01 (imbalance floor, per-MWh bazlı)
- `imbalance_cost_total_tl = imbalance_cost_per_mwh * consumption_kwh / 1000` (birim tutarlılığı)
- If `net_margin_total_tl < 0` → risk_flags includes `{"type": "LOSS_RISK", "priority": 1}`
- If `gross_margin_total_per_mwh < 0` → risk_flags includes `{"type": "UNPROFITABLE_OFFER", "priority": 2}`
- Both flags can coexist. Frontend renders by priority: P1 red block, P2 yellow warning.

**Property 1: Dual Margin + Dual Price**

_For any_ pricing request, the system SHALL produce:
- `sales_energy_price_per_mwh` = (PTF + YEKDEM) × multiplier
- `sales_effective_price_per_mwh` = energy_price + distribution_per_mwh
- `gross_margin_energy` = sales - energy_cost (per-MWh AND total-TL)
- `gross_margin_total` = sales - energy_cost - distribution_cost (per-MWh AND total-TL)

Invariant: `gross_margin_energy >= gross_margin_total` (when distribution >= 0)

**Property 2: Complete Net Margin**

_For any_ pricing request:
```
net_margin = sales - ptf - yekdem - distribution - imbalance - dealer_commission
```
Consistent across per-MWh and total-TL. Consistent across all modules.

Invariant: `net_margin <= gross_margin_total` (when imbalance >= 0 and dealer >= 0)

**Property 3: Per-MWh / Total-TL Consistency**

```
total_tl ≈ per_mwh × total_consumption_kwh / 1000  (±0.02 TL rounding)
```

**Property 4: YEKDEM Graceful Fallback with Severity**

When YEKDEM missing: HTTP 200, yekdem=0, warning with `severity: "high"`, `impact: "pricing_accuracy_low"`.

**Property 5: Preservation**

When YEKDEM exists and inputs valid: same hourly costs, weighted PTF, cache behavior, admin API responses.

## Fix Implementation

### Changes Required

---

**File**: `backend/app/pricing/models.py`

**New/Updated Fields:**

HourlyCostResult:
```python
# Dual Margin (total TL)
gross_margin_energy_total_tl: float    # Satış - (PTF + YEKDEM)
gross_margin_total_total_tl: float     # Satış - (PTF + YEKDEM + Dağıtım)
net_margin_total_tl: float             # Tam formül
distribution_cost_total_tl: float
# Backward compat: total_gross_margin_tl → alias to gross_margin_energy_total_tl
# Backward compat: total_net_margin_tl → alias to net_margin_total_tl
```

PricingSummary:
```python
# Dual Sales Price (per MWh)
sales_energy_price_per_mwh: float      # (PTF+YEKDEM) × katsayı
sales_effective_price_per_mwh: float   # enerji fiyatı + dağıtım

# Dual Margin (per MWh)
gross_margin_energy_per_mwh: float
gross_margin_total_per_mwh: float
net_margin_per_mwh: float              # Tam formül

# Cost breakdown (per MWh)
distribution_cost_per_mwh: float
imbalance_cost_per_mwh: float
dealer_commission_per_mwh: float

# Customer savings + source metadata
customer_savings_per_mwh: Optional[float]  # customer_price - effective_price
customer_reference_price_per_mwh: Optional[float]
customer_reference_price_source: Optional[str]  # "invoice" | "manual_input" | "market_estimate"

# Risk flags (priority ordered: P1 > P2, both can coexist)
risk_flags: list[dict]  # [{"type": "LOSS_RISK", "priority": 1, ...}]
```

---

**File**: `backend/app/pricing/pricing_engine.py`

**Function**: `calculate_hourly_costs`

1. Yeni parametre: `distribution_unit_price_tl_per_kwh: float = 0.0`
2. Dağıtım toplam: `distribution_cost_total = dist_price * total_consumption_kwh`
3. Dual brüt marj:
   ```python
   gross_margin_energy = total_sales - total_base_cost
   gross_margin_total  = total_sales - total_base_cost - distribution_cost_total
   ```
4. Tam net marj:
   ```python
   net_margin = gross_margin_total - dealer_commission - imbalance_share
   ```
5. **Safety guards:**
   ```python
   # Dealer commission cap — bayi payı enerji marjını aşamaz, negatif olamaz
   dealer_commission = max(0, min(dealer_commission, gross_margin_energy))
   
   # Imbalance floor — per-MWh bazlı, minimum %1 PTF taban
   RISK_FLOOR = 0.01
   imbalance_cost_per_mwh = max(calculated_imbalance_per_mwh, weighted_ptf * RISK_FLOOR)
   imbalance_share = imbalance_cost_per_mwh * total_consumption_kwh / 1000
   ```
6. Yeni alanları HourlyCostResult'a set et

---

**File**: `backend/app/pricing/router.py`

**Function**: `analyze`

1. Distribution lookup → engine:
   ```python
   dist_info = _calculate_distribution_info(voltage_level=..., total_kwh=..., ...)
   dist_unit_price = dist_info.unit_price_tl_per_kwh if dist_info else 0.0
   
   hourly_result = calculate_hourly_costs(
       ..., distribution_unit_price_tl_per_kwh=dist_unit_price)
   ```

2. Per-MWh dual price + dual margin:
   ```python
   dist_per_mwh = dist_unit_price * 1000
   
   sales_energy_price_per_mwh = energy_cost * req.multiplier
   sales_effective_price_per_mwh = sales_energy_price_per_mwh + dist_per_mwh
   
   gross_margin_energy_per_mwh = sales_energy_price_per_mwh - energy_cost
   gross_margin_total_per_mwh  = sales_energy_price_per_mwh - energy_cost - dist_per_mwh
   
   net_margin_per_mwh = gross_margin_total_per_mwh - dealer_per_mwh - imbalance_per_mwh
   ```

3. YEKDEM graceful (analyze, simulate, compare):
   ```python
   yekdem_record = get_yekdem(db, period)
   if not yekdem_record:
       yekdem = 0.0
       warnings.append({
           "type": "critical_missing_data",
           "severity": "high",
           "impact": "pricing_accuracy_low",
           "message": f"{period} dönemi için YEKDEM verisi bulunamadı, "
                      f"hesaplama 0 YEKDEM ile yapıldı.",
           "yekdem_unit_price": 0,
       })
   else:
       yekdem = yekdem_record.yekdem_tl_per_mwh
   ```

4. PricingSummary'ye tüm yeni alanlar + risk flags:
   ```python
   # Risk flags (priority ordered)
   risk_flags = []
   if net_margin_total_tl < 0:
       risk_flags.append({"type": "LOSS_RISK", "priority": 1, "message": "Net marj negatif"})
   if gross_margin_total_per_mwh < 0:
       risk_flags.append({"type": "UNPROFITABLE_OFFER", "priority": 2, "message": "Toplam brüt marj negatif"})
   
   # Customer savings + source metadata
   customer_savings_per_mwh = customer_current_price_per_mwh - sales_effective_price_per_mwh
   customer_reference_price_source = "invoice" | "manual_input" | "market_estimate"
   ```

---

**File**: `backend/app/pricing/router.py`

**New Endpoints:**

1. Tablo: `GET /api/pricing/distribution-tariffs?period=YYYY-MM` (public, no admin key)
2. Lookup: `GET /api/pricing/distribution-tariffs/lookup?voltage=OG&group=sanayi&term=TT&period=2026-04` (public)

---

**File**: `frontend/src/App.tsx`

1. Hardcode kaldır: `TARIFF_PERIODS`, `OSB_TARIFFS`, `getDistributionTariffsForPeriod` sil
2. API çağrısı: `GET /api/pricing/distribution-tariffs?period=YYYY-MM`
3. Cache: `localStorage` ile tarife cache
4. Fallback: API down → son bilinen tarife + uyarı
5. Dual margin liveCalculation:
   ```typescript
   const gross_margin_energy = offer_energy_tl - base_energy_cost;
   const gross_margin_total  = gross_margin_energy - offer_distribution_tl;
   const net_margin = gross_margin_total - imbalance_share - dealer_commission;
   ```
6. **UI Gösterim Stratejisi + Risk Flag Handling:**
   ```
   ┌─────────────────────────────────────────┐
   │ Enerji Marjı (senin kazancın): X TL     │  ← satış ekibi buna bakar
   │ Toplam Etki (müşteri farkı):   Y TL     │  ← müşteriye bunu göster
   │ Net Marj (gerçek kâr):         Z TL     │  ← yönetim buna bakar
   ├─────────────────────────────────────────┤
   │ Birim Fiyatlar:                         │
   │   Enerji Satış: A TL/MWh               │
   │   Efektif Toplam: B TL/MWh             │
   └─────────────────────────────────────────┘
   
   Risk Flag UI Davranışı:
   LOSS_RISK (P1) → Kırmızı banner + "Teklif Öner" butonu devre dışı
   UNPROFITABLE   (P2) → Sarı uyarı banner + buton aktif ama uyarılı
   İkisi birden   → Kırmızı banner (P1 öncelikli) + buton devre dışı
   ```

---

**Files**: `backend/app/pricing/router.py` (simulate, compare)

- Dağıtım parametresi geç
- YEKDEM graceful handling (compare: dönem bazlı — bazılarında var bazılarında yok)

## Testing Strategy

### Exploratory Bug Condition Checking

1. **Dual Margin Test:** Sonuçta hem `gross_margin_energy` hem `gross_margin_total` var mı, farklı mı (fail on unfixed)
2. **Dual Price Test:** `sales_energy_price_per_mwh` ve `sales_effective_price_per_mwh` var mı (fail on unfixed)
3. **Net Marj Tam Formül:** Tüm 5 gider düşülmüş mü (fail on unfixed)
4. **Per-MWh/Total Tutarlılık:** `total ≈ per_mwh × consumption / 1000` (fail on unfixed)
5. **YEKDEM Severity:** 200 + severity warning (fail on unfixed — 404)

### Fix Checking

```
FOR ALL input WHERE isBugCondition(input) DO
  result := fixedSystem(input)
  
  ASSERT result.gross_margin_energy != result.gross_margin_total WHEN dist > 0
  ASSERT result.sales_effective_price == result.sales_energy_price + dist_per_mwh
  ASSERT result.net_margin == result.sales - ptf - yekdem - dist - imbalance - dealer
  ASSERT abs(result.net_margin_total_tl - result.net_margin_per_mwh * kwh / 1000) < 0.02
  ASSERT result.yekdem_warning.severity == "high" WHEN yekdem missing
END FOR
```

### Preservation Checking

```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT original.hourly_costs == fixed.hourly_costs
  ASSERT original.weighted_ptf == fixed.weighted_ptf
  ASSERT original.dealer_commission == fixed.dealer_commission
END FOR
```

### Unit Tests
- Dual margin: energy != total when distribution > 0
- Dual price: effective = energy + distribution
- Net marj: 5 gider kalemi düşülmüş
- Per-MWh/Total tutarlılık
- YEKDEM severity + impact flag
- Distribution API tablo + lookup
- **Dealer commission cap:** katsayı 1.01, bayi 3p → commission capped to energy margin; energy_margin < 0 → commission = 0
- **Imbalance floor (per-MWh):** calculated=0 → floor = ptf*0.01; total_tl = floor * kwh / 1000
- **Risk flags priority:** net<0 AND gross_total<0 → both flags, P1 first; only gross_total<0 → P2 only
- **Customer savings source:** source="invoice" vs "manual_input" vs "market_estimate" metadata
- Edge: sıfır tüketim, dağıtım yok, YEKDEM=0

### Property-Based Tests
- Random PTF, YEKDEM, consumption, multiplier, distribution:
  - `gross_margin_energy >= gross_margin_total`
  - `net_margin <= gross_margin_total`
  - `total_tl ≈ per_mwh × consumption / 1000`
- Preservation: YEKDEM mevcut dönemlerde saatlik hesaplama aynı

### Integration Tests
- Full analyze: dual margin + dual price + tam net marj
- YEKDEM missing: 200 + severity warning
- Distribution API → frontend: dönem → API → cache → render
- Frontend fallback: API down → localStorage → uyarı
- Compare: bazı dönemlerde YEKDEM var/yok → graceful
