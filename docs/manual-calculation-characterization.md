# Manuel Hesaplama Karakterizasyonu — PATCH 2A

> **Kaynak önceliği (bozulmadı):** 1) çalışan masaüstü kodu, 2) bu dokümanı üretmek için
> gerçekten çalıştırılıp okunan sonuçlar, 3) `backend/tests/fixtures/manual_calculation/golden_fixtures.json`,
> 4) bu doküman. Doküman ile kod arasında fark bulunursa, PATCH 2B implementasyonu
> **kodu ve fixture'ları** esas alır — bu dokümanı sessizce doğru kabul etmez.
>
> Bu doküman `mobile/docs/backend-contract.md`'nin yerini alır (o dosyadaki formül bölümü
> artık ikincil kaynak; API sözleşmesi bölümleri geçerliliğini korur).

## 0. Kapsam

Masaüstü **manuel mod** hesaplama davranışının (`frontend/src/App.tsx`, `liveCalculation`,
satır ~356-410) backend'e canonical pure function olarak taşınması için gereken tam
karakterizasyon. OCR/`InvoiceExtraction`/EPDK-otomatik-lookup akışı (`calculate_offer`,
`backend/app/calculator.py`) bu kapsamın **dışındadır** ve değiştirilmemiştir.

## 1. Girdiler — NİHAİ CONTRACT (kontrat kararları sonrası)

| Girdi | Birim | Kaynak/Not |
|---|---|---|
| `consumption_kwh` | kWh | Kullanıcı girişi. **Zorunlu, > 0** (fail-closed 422) |
| `current_energy_unit_price_tl_per_kwh` | TL/kWh | Kullanıcı girişi. **Zorunlu, >= 0**. Ham-TL fallback dalı (masaüstünde vardı) **MVP'de KALDIRILDI** — karar §7.1 |
| `current_distribution_unit_price_tl_per_kwh` | TL/kWh | Kullanıcı girişi. Masaüstünün ham-TL girişi **DEĞİL** — karar §7.2 |
| `offer_distribution_unit_price_tl_per_kwh` | TL/kWh | Ayrı alan (current'tan bağımsız olabilir). MVP'de UI tek alan gösterip ikisine de aynı değeri map edebilir — karar §7.2 |
| `btv_rate` | oran (0, 0.01, 0.05) | Kategorik seçim: OSB=0, Sanayi=0.01, Ticari/Kamu=0.05. **Backend'den gelmez** |
| `vat_rate` | oran (0.10, 0.20) | Kategorik seçim: Normal=0.20, Mesken/Tarımsal=0.10. **Backend'den gelmez** |
| `offer_type` | `"indexed"` \| `"fixed"` | Discriminator — aktif olmayan varyantın alanları REDDEDİLİR (fail-closed) |
| `ptf_tl_per_mwh` | TL/**MWh** | Yalnız `indexed`. Hesapta `/1000` ile TL/kWh'e çevrilir. `fixed` iken gönderilirse 422 |
| `yekdem_tl_per_mwh` | TL/**MWh** | Yalnız `indexed`. `0` ise teklife hiç dahil edilmez (aşağıya bkz.). `fixed` iken gönderilirse 422 |
| `multiplier` | katsayı (örn 1.05) | Yalnız `indexed`. **Yüzde değil** — `1.05` = "%5 kâr marjı". `fixed` iken gönderilirse 422 |
| `fixed_energy_unit_price_tl_per_kwh` | TL/**kWh** | Yalnız `fixed`. PTF+YEKDEM birleşik, KDV/BTV **hariç** saf enerji birim fiyatı. `indexed` iken gönderilirse 422 |

## 2. Sabitler / doğrulanmış edge-case davranışları

- **Ham-TL fallback YOK (MVP kararı)**: masaüstünde `current_unit_price==0` ise ham `current_energy_tl` girişine düşen dal vardı — **PATCH 2B'de bu yok**, `current_energy_unit_price_tl_per_kwh` her zaman zorunlu girdi.
- **YEKDEM dahil etme**: `yekdem_tl_per_mwh > 0` ise teklife dahil edilir (`ptf/1000 + yekdem/1000`); `yekdem_tl_per_mwh == 0` ise **hiç eklenmez** (0 eklemekle sonuç aynı ama kod dalı farklı — backend'de de aynı ayrımı koru).
- **Sabit modda çarpan uygulanmaz**: `offer_energy_amount_tl = consumption_kwh × fixed_energy_unit_price_tl_per_kwh` — `multiplier` hiç kullanılmaz.
- **Dağıtım formülü offer_type'tan bağımsız, artık birim-fiyat×tüketim**: `current_distribution_amount_tl = consumption_kwh × current_distribution_unit_price_tl_per_kwh`; `offer_distribution_amount_tl = consumption_kwh × offer_distribution_unit_price_tl_per_kwh` (karar §7.2 — masaüstünün ham-TL/tüketim türetmesi YERİNE).
- **BTV/KDV oranı `current` ve `offer` tarafında AYNI**: kullanıcının tek bir toggle seçimi her iki tarafa da uygulanır.
- **Negatif tasarruf mümkün ve geçerlidir**: `difference_incl_vat_tl` ve `saving_rate_percent` negatif olabilir (teklif mevcuttan pahalı çıkabilir). Backend bunu **hata/clamp olarak ele almamalı**. Bkz. fixture #4, #6-10.
- **`saving_rate_percent` payda koruması**: `current_total_tl > 0` değilse `0` (sıfıra bölme koruması). **Ölçek 0-100** (`0.0778` değil `7.78`) — bkz. §7.3 not.

## 3. İşlem sırası (adım adım, her iki teklif tipi için)

```
current_energy_amount_tl       = current_energy_unit_price_tl_per_kwh × consumption_kwh
current_distribution_amount_tl = current_distribution_unit_price_tl_per_kwh × consumption_kwh
current_btv_amount_tl           = current_energy_amount_tl × btv_rate
current_vat_base_tl              = current_energy_amount_tl + current_distribution_amount_tl + current_btv_amount_tl
current_vat_amount_tl             = current_vat_base_tl × vat_rate
current_total_tl                   = current_vat_base_tl + current_vat_amount_tl

# offer_type == "indexed":
#   ⚠️ DOĞRULANMIŞ FORMÜL — çarpan PTF+YEKDEM TOPLAMINA uygulanır, YALNIZ PTF'ye DEĞİL.
#   5 fixture'ın 3'ünde (YEKDEM anlamlı büyüklükteyken) alternatif sıralama YANLIŞ sonuç
#   verir — bkz. §7.3 "Formül düzeltmesi" notu.
offer_energy_unit_price_tl_per_kwh = (ptf_tl_per_mwh/1000 + [yekdem_tl_per_mwh/1000 if >0 else 0]) × multiplier
offer_energy_amount_tl              = offer_energy_unit_price_tl_per_kwh × consumption_kwh

# offer_type == "fixed":
offer_energy_unit_price_tl_per_kwh = fixed_energy_unit_price_tl_per_kwh
offer_energy_amount_tl              = fixed_energy_unit_price_tl_per_kwh × consumption_kwh

# Ortak:
offer_distribution_amount_tl = offer_distribution_unit_price_tl_per_kwh × consumption_kwh
offer_btv_amount_tl            = offer_energy_amount_tl × btv_rate
offer_vat_base_tl                = offer_energy_amount_tl + offer_distribution_amount_tl + offer_btv_amount_tl
offer_vat_amount_tl               = offer_vat_base_tl × vat_rate
offer_total_tl                     = offer_vat_base_tl + offer_vat_amount_tl

difference_incl_vat_tl = current_total_tl − offer_total_tl
saving_rate_percent      = current_total_tl > 0
                            ? (difference_incl_vat_tl / current_total_tl) × 100
                            : 0
```

## 4. Yuvarlama — DOĞRULANMIŞ, kritik bulgu

**Ara işlemlerde YUVARLAMA YOKTUR.** Gerçek uygulama testiyle kanıtlandı (fixture #5, #10):
`current_distribution_tl = 2499.9999` girildiğinde ekranda **"₺2.500,00"** gösterilir
(2 ondalığa yuvarlanmış DISPLAY), ama sonraki `current_vat_matrah_tl` hesaplamasında
**tam hassasiyetli 2499.9999** kullanılır — elle doğrulama: `2996.00 + 2499.9999 + 29.96
= 5525.9599 → yuvarlanır → 5525.96` (ekranda gösterilen değerle birebir eşleşiyor).

**Final tutarlar yalnızca RESPONSE/DISPLAY sınırında 2 ondalığa yuvarlanır** (`toLocaleString('tr-TR', {minimumFractionDigits:2, maximumFractionDigits:2})`).

**Önemli istisna — `difference_incl_vat_tl`:** Bu değer `current_total` ve `offer_total`'ın
**tam hassasiyetli** hallerinden hesaplanır, DEĞİL yuvarlanmış `current_total`/`offer_total`
DEĞERLERİNİN farkından. Bu yüzden `round(current_total) − round(offer_total)` ile
`round(current_total − offer_total)` **±0.01 farklı çıkabilir** (fixture #1: 54960.44 −
50683.78 = 4276.66 matematiksel olarak, ama ekranda "Fark" = **4276.67** gösteriliyor —
1 kuruşluk fark, ayrı yuvarlama zincirinden kaynaklanıyor).

**PATCH 2B için sonuç:** `decimal.Decimal` ile tüm ara işlemleri TAM hassasiyette tut
(`app/recon/cost_engine_v2.py` standardı: `Decimal(str(value))` ile başla, ara adımlarda
`quantize` ÇAĞIRMA, yalnız response'a yazarken `.quantize(Decimal("0.01"), ROUND_HALF_UP)`).
`difference_incl_vat_tl`'i `current_total_with_vat_tl` (tam hassas) − `offer_total_with_vat_tl`
(tam hassas) olarak hesapla, ayrıca yuvarla — yuvarlanmış iki toplamın farkını ALMA.

## 5. Masaüstü test-otomasyonu notu (yalnız metodoloji, backend'i etkilemez)

Golden fixture'ları üretirken **gerçek bir uygulama hatasıyla karşılaşıldı ve düzeltildi**:
"Tüketim" ve "Mevcut Birim Fiyat" alanları `type="text"` + özel `parseNumber()` kullanıyor
(`value.replace(/\./g,'').replace(',','.')`) — yani **Türkçe locale bekliyor** (virgül
ondalık, nokta binlik-ayraç-ve-SİLİNİR). JS-stili `"2.8567"` girilirse nokta silinip
`"28567"` olarak parse ediliyor (28.567 kat büyük sonuç!). Diğer tüm alanlar
(`Dağıtım Bedeli`, PTF, YEKDEM, Çarpan, Sabit Fiyat) native `<input type="number">` —
locale'den bağımsız, nokta-ondalık. **Bu, backend API'sini etkilemez** (API'ye her zaman
düz sayı/JSON float gönderilecek) — yalnız golden fixture'ları doğru üretmek için önemliydi,
kayıt altına alınıyor ki gelecekte biri aynı hataya düşmesin.

## 6. Golden fixture dosyası

`backend/tests/fixtures/manual_calculation/golden_fixtures.json` — 10 fixture (5 endeksli,
5 sabit), hepsi gerçek çalıştırılmış uygulamadan okunmuş, **nihai contract alan adlarıyla**
(§8) güncellendi. Kapsadıkları: sıfır oran (BTV=0), YEKDEM=0 hariç tutma, yüksek tüketim
(250-500 MWh), ondalık/virgüllü girdiler, yuvarlama sınırı (±0.01 hassasiyet testi, fixture
#10 en küçük mutlak farkı — ₺3.85 — test eder), negatif tasarruf (fixture #4, #6-10),
Mesken/Tarımsal %10 KDV. `current_distribution_unit_price_tl_per_kwh` /
`offer_distribution_unit_price_tl_per_kwh` alanları, orijinal capture edilen ham TL
tutarının `consumption_kwh`'a bölünmesiyle **tam hassasiyette** (yuvarlanmadan) türetildi —
geri çarpıldığında orijinal capture edilen tutarı ±0.01 içinde yeniden üretir (doğrulandı).

### 6.1 Fixture #6/#7 düzeltmesi — provenance (circular-golden-test itirazını kapatır)

Sabit mod arayüzü `difference_incl_vat_tl`'i hiç göstermez (tek sütun "Teklif
Faturanız"). Bu yüzden fixture #6/#7'nin bu alanı **implementasyondan kopyalanarak
değil**, önceden karakterize edilmiş bir kural uygulanarak düzeltildi:

- `current_total_tl` ve `offer_total_tl` her ikisi de **çalışan UI'dan gözlemlendi**
  (sabit mod gerçekten "TOPLAM (KDV Dahil)" satırını gösteriyor — bu değerler ham,
  implementasyondan bağımsız).
- `difference_incl_vat_tl` semantiği **endeksli UI'dan** karakterize edildi (§4,
  fixture #1 ile kanıtlandı): `round(tam_hassasiyetli_current_total −
  tam_hassasiyetli_offer_total, 2)` — yuvarlanmış iki toplamın farkı DEĞİL.
- Fixture #6/#7'nin `difference_incl_vat_tl`/`saving_rate_percent` alanları bu
  **önceden-karakterize-edilmiş, çapraz-mod kuralına göre** düzeltildi — yeni backend
  implementasyonunun kendi çıktısına bakılarak "tersten" ayarlanmadı. (Doğrulama
  aracı olarak `calculate_manual_offer()` kullanıldı çünkü bu fonksiyon §3'teki,
  fixture #1-5'te UI'ya karşı bağımsız doğrulanmış AYNI formülü uyguluyor — yani
  implementasyon burada "hakem" değil, zaten kanıtlanmış kuralın bir tekrarı.)

## 7. Kontrat kararları — ÇÖZÜLDÜ

### 7.1 Ham-TL fallback → KALDIRILDI
`consumption_kwh > 0` ve `current_energy_unit_price_tl_per_kwh >= 0` PATCH 2B'de zorunlu
girdi. Masaüstündeki "birim fiyat=0 ise ham TL'ye düş" dalı mobil MVP'ye taşınmadı.
İleride kanıtlanmış ihtiyaç olursa additive `current_energy_input_mode: UNIT_PRICE |
TOTAL_AMOUNT` eklenebilir — ilk sürümde yok.

### 7.2 Dağıtım birim fiyatı → ayrı alanlar, türetme YOK
`current_distribution_unit_price_tl_per_kwh` ve `offer_distribution_unit_price_tl_per_kwh`
**ayrı request alanları**. Masaüstünün `current_distribution_tl / consumption_kwh`
türetmesi PATCH 2B'ye taşınmadı (gerekçe: mevcut fatura dağıtım tutarını teklif dağıtım
bedelinin otoritesi hâline getirmek yanlış — tarife değişmiş olabilir; ayrıca yuvarlanmış
tutardan birim fiyat geri üretmek hassasiyet kaybettirir). Mobil MVP UI'da tek bir "Dağıtım
birim fiyatı" alanı gösterip payload mapper'da ikisine de aynı değeri yazabilir; backend
contract'ı bunu **iki ayrı alan** olarak tutar.

### 7.3 `unitOfferPrice` → `offer_energy_unit_price_tl_per_kwh`, ve bir FORMÜL DÜZELTMESİ

Response alan adı netleşti: **`offer_energy_unit_price_tl_per_kwh`** (yalnız enerji birim
fiyatı — dağıtım/BTV/KDV dahil değil). `consumption_kwh > 0` zorunluluğu (§7.1) zaten
sıfıra bölme riskini ortadan kaldırıyor.

**Formül düzeltmesi (kontrat onayı sırasında yakalandı):** İlk taslakta önerilen
`(ptf/1000 × multiplier) + yekdem/1000` (çarpan yalnız PTF'ye uygulanır) **yanlıştır**.
Doğrulanmış/gerçek formül — çarpan **PTF+YEKDEM toplamına** uygulanır:
```
(ptf_tl_per_mwh/1000 + yekdem_tl_per_mwh/1000) × multiplier
```
Kanıt — 5 endeksli fixture'ın 3'ünde (YEKDEM payı yeterince büyükken) iki formül farklı
sonuç verir, doğru formül HER 5 fixture'da da gerçek uygulama çıktısıyla birebir eşleşir:

| Fixture | Gerçek (uygulama) | Doğru formül (toplam×çarpan) | Yanlış taslak (ptf×çarpan+yekdem) |
|---|---|---|---|
| #1 | 3,5050 | 3,5050 ✅ | 3,4868 ❌ |
| #2 | 2,7517 | 2,7517 ✅ | 2,7468 ❌ |
| #4 | 3,7545 | 3,7545 ✅ | 3,7114 ❌ |

(#3 ve #5'te yekdem payı küçük/sıfıra yakın olduğu için iki formül görünürde örtüşüyor —
bu yüzden tek fixture ile yakalanamazdı, çoklu-fixture karşılaştırması gerekti.)

## 8. NİHAİ CONTRACT — `POST /api/pricing/calculate-manual`

### Request — ortak alanlar
```
consumption_kwh                                  float, > 0, zorunlu
current_energy_unit_price_tl_per_kwh              float, >= 0, zorunlu
current_distribution_unit_price_tl_per_kwh         float, >= 0, zorunlu
offer_distribution_unit_price_tl_per_kwh            float, >= 0, zorunlu
btv_rate                                             float, [0, 1] aralığında, zorunlu
vat_rate                                             float, [0, 1] aralığında, zorunlu
offer_type                                           "indexed" | "fixed", zorunlu
```

### Request — `offer_type` discriminated alanlar
```
# offer_type == "indexed" iken ZORUNLU, "fixed" iken GÖNDERİLİRSE 422:
ptf_tl_per_mwh        float, > 0
yekdem_tl_per_mwh      float, >= 0
multiplier              float, > 0

# offer_type == "fixed" iken ZORUNLU, "indexed" iken GÖNDERİLİRSE 422:
fixed_energy_unit_price_tl_per_kwh   float, > 0
```

### Response
```
current_energy_amount_tl
current_distribution_amount_tl
current_btv_amount_tl
current_vat_base_tl
current_vat_amount_tl
current_total_tl

offer_energy_unit_price_tl_per_kwh
offer_energy_amount_tl
offer_distribution_amount_tl
offer_btv_amount_tl
offer_vat_base_tl
offer_vat_amount_tl
offer_total_tl

difference_incl_vat_tl
saving_rate_percent      # 0-100 ölçek (0-1 DEĞİL) — örn "7.78" = %7,78

calculation_version       # örn "manual-v1"
rounding_version           # örn "round-half-up-2dp-v1"
```

### Validation (fail-closed)
- `consumption_kwh > 0` — 422 değilse
- Tüm fiyat/oran alanları `>= 0` — 422 negatifse
- `btv_rate`, `vat_rate` `[0, 1]` aralığında — 422 aralık dışıysa
- `offer_type=indexed` iken `fixed_energy_unit_price_tl_per_kwh` gönderilmesi — 422
- `offer_type=fixed` iken `ptf_tl_per_mwh`/`yekdem_tl_per_mwh`/`multiplier` gönderilmesi — 422
- `NaN`, `Infinity`, boş string, parse edilemeyen değer — 422
- Şema hatası mesajı alan adını ve nedeni açıkça belirtmeli (fail-closed, sessiz coerce YOK)

### Test kapıları (PATCH 2B tamamlanma kriteri)
- Backend pure-function unit testleri (Decimal aritmetik, ara yuvarlama yok — §4)
- API request/response contract testleri (FastAPI `TestClient`)
- **10/10 golden parity** — `backend/tests/fixtures/manual_calculation/golden_fixtures.json`
- Endeksli/sabit discriminated validation negatif testleri (yanlış varyant alanı → 422)
- Decimal/rounding sınır testleri (fixture #5, #10 özellikle)
- Mevcut `calculate_offer()` / `/calculate-offer` regresyon testleri **PASS** (dokunulmadı, ama bozulmadığı ayrıca doğrulanmalı)
