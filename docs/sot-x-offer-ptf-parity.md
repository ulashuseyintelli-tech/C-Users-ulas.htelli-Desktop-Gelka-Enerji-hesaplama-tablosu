# SoT-X: Offer PTF Kaynak Paritesi — Forensic & Ölçüm

**Tarih:** 2026-06-11 · **Durum:** Seviye 1 kararlandı; implementasyon bekliyor · **İlgili:** R2 (extraction guard), A (PTF=0 guard)

## 1. Problem
Offer (teklif) motoru ile pricing/recon motorları PTF'i **farklı kaynaktan ve
farklı granülerlikte** okuyor. Bu, tepeli tüketim profillerinde teklifin
**maliyet-altı** üretilmesine yol açıyor (ölçüldü: %26).

## 2. Kaynak Haritası (master)
| Motor | PTF kaynağı | YEKDEM kaynağı | Granülerlik |
|-------|-------------|----------------|-------------|
| Pricing | `hourly_market_prices` (period + is_active==1) | `monthly_yekdem_prices` (period) | saatlik, tüketim-ağırlıklı |
| Offer (calculator) | `market_reference_prices` (period + price_type) | `market_reference_prices` | **aylık skaler, ağırlıksız** |
| Recon-v2 | `hourly_market_prices` (period + is_active==1) | `monthly_yekdem_prices` (period) | saatlik — *master'da değil (unmerged)* |

## 3. Forensic Bulguları (kanıtlı)
1. **Pricing, Excel'i compute anında okumaz** — `hourly_market_prices`'tan okur
   (`pricing/router.py:_load_market_records`). Excel yalnızca ingest formatı
   (`router.py` upload → hourly_market_prices, versiyonlama + tek aktif versiyon).
2. **Pricing ↔ Recon parite-güvenli**: ikisi de hourly'i **birebir aynı filtreyle**
   (period + is_active==1) ve monthly_yekdem'i (period) okur → aynı dönem/saat aynı
   sayı. "Excel vs hourly" sapması **yoktur**.
3. **Asıl aykırı: Offer** — `market_reference_prices` aylık ağırlıksız skaler.
4. **Senkron manuel/tesadüfi**: 2026-01..04 için skaler = hourly **basit ortalaması**
   (DB'de fark=0), ama bu ağırlıksız; otomatik senkron yok. 2026-05+ için skaler=0
   (hourly de yok).

## 4. Ölçüm — Skaler vs Ağırlıklı (read-only simülasyon)
**Dönem 2026-04** (720 saat, skaler PTF=921,06 TL/MWh), **2.672.000 kWh** sabit.
Offer PTF maliyeti (skaler × kWh) = **2.461.063 TL**.

| Profil | Ağırlıklı PTF | Hourly maliyet | Fark (TL) | % | kr/kWh |
|--------|---------------|----------------|-----------|---|--------|
| Düz | 921,06 | 2.461.063 | 0 | 0,0% | 0,00 |
| **Puant-ağır** | 1.249,04 | 3.337.439 | **+876.376** | **+26,3%** | **+32,80** |
| Gece-ağır | 797,82 | 2.131.763 | −329.301 | −15,4% | −12,32 |

**Hüküm:** Puant-ağır profil (sanayi/ticari) offer'ı **%26 maliyet-altı** fiyatlatıyor.
Çarpan %1-3 marj bunu karşılamaz → yapısal under-pricing. YEKDEM bu sapmaya dahil
değil (aylık, her iki yolda aynı); sapma tamamen PTF ağırlıklandırmasından.

## 5. Çözüm Yönü (Karar Ağacı)
```
market_reference_prices'a weighted skaler YAZMAK = geçici yama; tek skaler tüm
  profillere hizmet edemez → ÖNERİLMEZ.
Offer'ın hesap anında hourly_market_prices'tan türetmesi = KALICI ÇÖZÜM.
  Seviye 1 = hesap-anında türet + VARSAYILAN profil (fail-safe).
  Seviye 2 = hesap-anında türet + GERÇEK profil + merkezi helper + parite testi.
```

## 6. Kararlar
- **Default profil**: Sanayi/Ticarethane → **puant-ağır**; Mesken → **düz**;
  Gece-ağır yalnızca açıkça seçilirse. Gerekçe: underprice riskinde fail-safe taraf
  maliyeti yukarı çekmektir.
- **Fallback**: hourly yoksa `market_reference_prices` skaler kullanılabilir, ama
  **zorunlu uyarı**: *"Saatlik PTF verisi yok; aylık ortalama kullanıldı. Puant/gece
  ağırlıklı profillerde teklif sapabilir."* Skaler de yoksa / PTF ≤ 0 → **fail-closed**.
- **Sıra**: Commit 1 (bu doc) → Commit 2 (Seviye 1 weighted PTF) → Seviye 2 sonraya.

## 7. Etkilenen Dosyalar (referans)
- `backend/app/calculator.py` (`get_ptf_yekdem_for_period`, `calculate_offer`)
- `backend/app/market_prices.py` (`get_market_prices`)
- `backend/app/pricing/router.py` (`_load_market_records`), `pricing/pricing_engine.py`
  (`calculate_weighted_prices` — Seviye 2 merkezi helper adayı), `pricing/time_zones.py`
- `backend/app/pricing/schemas.py` (`HourlyMarketPrice`), `database.py` (`MarketReferencePrice`)

## 8. Açık Borç / Notlar
- recon-v2 master'da değil; parite testi (Seviye 2) master'da offer↔pricing olur.
- `market_reference_prices` PTF kolonu Seviye 2'de deprecate edilir (YEKDEM aylık kalabilir).
