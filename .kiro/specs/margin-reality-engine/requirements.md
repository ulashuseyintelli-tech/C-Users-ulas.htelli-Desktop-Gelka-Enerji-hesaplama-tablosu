# Nominal vs Gerçek Marj Analizi (Marj Sapma Motoru) — Gereksinimler

## Modül Adı
**Nominal vs Gerçek Marj Analizi (Marj Sapma Motoru)**

## Amaç (tek cümle)
Girilen katsayı ile oluşan kağıt üzeri marjın, müşterinin saatlik tüketim profili nedeniyle gerçekte kaç %'ye düştüğünü veya çıktığını hesaplamak.

## Ana Soru
> "Ben bu müşteriye %4 marjla sattım; saatlik tüketim profiline göre gerçekte kaç % marj kazandım?"

## Temel Gerçek (Çekirdek Mantık)

Sen:
- **Sabit fiyat** satıyorsun → Teklif Birim Fiyat her saat aynı
- **Değişken maliyetle** alıyorsun → Saatlik PTF her saat farklı

Bu yüzden: **Marj = profil fonksiyonu**

Aynı katsayıyla iki farklı müşteriye satış yapsan bile:
- Ucuz saatlerde tüketen müşteri → gerçek marj nominal marjdan yüksek
- Pahalı saatlerde tüketen müşteri → gerçek marj nominal marjdan düşük

---

## Hesap Akışı (Net ve Doğru Form)

### 1) Sabit Satış Fiyatı
```
Teklif Birim Fiyat (TL/kWh) = (Teklif_PTF + YEKDEM) / 1000 × Katsayı
```
Bu değer tüm saatler için sabittir.

### 2) Saatlik Maliyet
```
Her saat h için:
  Saatlik Maliyet(h) (TL/kWh) = (Saatlik_PTF(h) + YEKDEM) / 1000
```
Bu değer her saat değişir.

### 3) Saatlik Kâr/Zarar
```
Her saat h için:
  Saatlik Marj(h) (TL) = (Teklif Birim Fiyat - Saatlik Maliyet(h)) × Saatlik kWh(h)
```

### 4) Toplam Gerçek Marj
```
Toplam Gerçek Marj (TL) = Σ Saatlik Marj(h)   [tüm saatler]
Toplam Maliyet (TL)     = Σ Saatlik Maliyet(h) × Saatlik kWh(h)
Toplam Teklif (TL)      = Teklif Birim Fiyat × Toplam kWh
```

### 5) Gerçek Marj Oranı (EN KRİTİK METRİK)
```
Gerçek Marj %  = Toplam Gerçek Marj / Toplam Maliyet × 100
Manuel Marj %  = (Katsayı - 1) × 100
Marj Sapması % = Gerçek Marj % - Manuel Marj %
```

### 6) Effective Multiplier (Gerçekleşen Katsayı Etkisi)
```
Ağırlıklı Gerçek Maliyet = Toplam Maliyet / Toplam kWh
Effective Multiplier      = Teklif Birim Fiyat / Ağırlıklı Gerçek Maliyet
```
Bu sana şunu söyler: "Sen ×1.04 sattığını sanıyorsun ama aslında ×1.02'ye satmışsın."

### 7) Break-even Katsayı
```
Break-even Multiplier = Ağırlıklı Gerçek Maliyet / ((Teklif_PTF + YEKDEM) / 1000)
```
Bu müşteri için gerçek marjı 0 TL yapan minimum katsayı.

### 8) Güvenli Katsayı
```
Güvenli Katsayı = Break-even Katsayı + Tampon
Tampon varsayılan: +0.01 (yani +%1)
Tampon ayarlanabilir: +0.01 veya +0.02
```

---

## Karar Dili

| Durum | Koşul | Renk | Anlam |
|-------|-------|------|-------|
| **KÂRLI** | Gerçek Marj > 0 ve Gerçek ≈ Nominal (sapma ±%1 içinde) | Yeşil | Marj korunuyor, teklif doğru |
| **OVERPERFORM** | Gerçek Marj > Nominal Marj (sapma > +%1) | Mavi/Yeşil | Müşteri sana fazla para kazandırıyor |
| **MARJ ERİYOR** | Gerçek Marj > 0 ama Gerçek < Nominal (sapma < -%1) | Turuncu | Müşteri marjını eritiyor |
| **ZARARLI** | Gerçek Marj < 0 | Kırmızı | Bu müşteriden zarar ediyorsun |

Marj Sapması yorumu:
- `+` → müşteri sana para kazandırıyor
- `-` → müşteri marjını eritiyor

---

## Ekranda Olması Gereken Ana Metrikler

### Ana Panel (büyük, görünür)
| # | Metrik | Örnek |
|---|--------|-------|
| 1 | Nominal Marj % | %4.0 |
| 2 | Gerçek Marj % | %1.7 veya %6.3 |
| 3 | **Marj Sapması %** | -2.3% veya +2.3% |
| 4 | **Durum Etiketi** | MARJ ERİYOR / KÂRLI / OVERPERFORM / ZARARLI |

### Detay Tablosu
| # | Metrik | Açıklama |
|---|--------|----------|
| 5 | Girilen Katsayı | ×1.04 |
| 6 | Effective Multiplier | ×1.017 (gerçekte bu katsayıya satmışsın) |
| 7 | Manuel Marj TL | Kağıt üzeri beklenen kâr |
| 8 | Gerçek Marj TL | Saatlik hesapla bulunan gerçek kâr |
| 9 | Marj Sapması TL | Gerçek - Manuel farkı |
| 10 | Negatif Marj Saat Sayısı | Kaç saatte zarar edildi |
| 11 | Toplam Negatif Saat Zararı TL | Zarar saatlerinin toplamı |
| 12 | Toplam Pozitif Saat Kârı TL | Kâr saatlerinin toplamı |
| 13 | Break-even Katsayı | Gerçek marj = 0 olan katsayı |
| 14 | Güvenli Katsayı | Break-even + tampon |

### En Kötü / En İyi 10 Saat Tablosu
| Saat | PTF (TL/MWh) | Tüketim (kWh) | Maliyet (TL) | Marj (TL) |
|------|-------------|---------------|-------------|-----------|
| 2026-03-15 18:00 | 3.450 | 85 | 357 | -42.5 |
| ... | ... | ... | ... | ... |

### Profil Riski (Yardımcı Gösterge — küçük alan)
- T1/T2/T3 dağılımı
- Sapma oranı
- Peak concentration
- Negatif marj saat sayısı

**Profil riski ana karar değildir.** Teklifin ticari olarak uygun olup olmadığına Gerçek Marj ve Break-even Katsayısı karar verir.

---

## Gereksinimler Listesi

### R1: Gerçek Marj Hesaplama
Saatlik PTF verileri ve müşteri tüketim profili kullanılarak gerçek marj hesaplanacak.

### R2: Dağıtım Bedeli Dahil Değil
Dağıtım bedeli regüle kalemdir ve müşteriye aynı şekilde yansıtılır. Ana marj hesabı sadece enerji tarafını ölçer.

### R3: Karar Dili
Ana karar alanında KÂRLI / OVERPERFORM / MARJ ERİYOR / ZARARLI ifadeleri kullanılacak. Eski "Risk Yüksek/Düşük" dili ana karar alanından kaldırılacak.

### R4: Marj Eriyor Eşiği
Varsayılan: Gerçek Marj < Nominal Marj (sapma < -%1). Parametrik tanımlanacak, ileride müşteri segmentine göre değiştirilebilir.

### R5: Break-even ve Güvenli Katsayı
- Break-even: Bu profil için gerçek marjı 0 TL yapan katsayı
- Güvenli: Break-even + tampon (varsayılan +%1, ayarlanabilir +%1 veya +%2)

### R6: Effective Multiplier
Gerçekleşen katsayı etkisi hesaplanacak. "Sen ×1.04 sattığını sanıyorsun ama aslında ×1.02'ye satmışsın."

### R7: En Kötü / En İyi 10 Saat
Saatlik marj bazında en çok zarar edilen 10 saat ve en çok kâr edilen 10 saat listelenecek.

### R8: Profil Riski Yardımcı Gösterge
Mevcut profil riski (T1/T2/T3, sapma, peak concentration) silinmeyecek. Küçük yardımcı alan olarak kalacak. Ana karar kriteri profil riski değil, gerçek marj.

### R9: Mevcut API Geriye Uyumluluk
- Mevcut `risk_score` alanı API'de korunacak
- Yeni `margin_reality` alanı eklenecek
- Frontend'de ana karar `margin_reality.verdict` olacak

### R10: Saatlik Marj Histogramı
Saatlik marj dağılımını gösteren basit histogram. İlk versiyonda basit grafik yeterli.

---

## Kritik Test

> Bu müşteri %4 ile satıldığında sistem bana "gerçekte %2 kazandın" diyebiliyor mu?

- EVET → doğru mimari
- HAYIR → hâlâ yanlış yerdeyiz

---

## Örnek Senaryolar

### Senaryo 1: Marj Eriyor
```
Müşteri: Fabrika A (pahalı saatlerde yoğun tüketim)
Katsayı: ×1.04 → Manuel Marj: %4
Gerçek Marj: %1.7
Marj Sapması: -2.3%
Effective Multiplier: ×1.017
Durum: MARJ ERİYOR (turuncu)
```

### Senaryo 2: Overperform
```
Müşteri: Fabrika B (ucuz saatlerde yoğun tüketim)
Katsayı: ×1.04 → Manuel Marj: %4
Gerçek Marj: %6.3
Marj Sapması: +2.3%
Effective Multiplier: ×1.063
Durum: OVERPERFORM (mavi)
```

### Senaryo 3: Zararlı
```
Müşteri: Fabrika C (çok yoğun puant tüketimi)
Katsayı: ×1.01 → Manuel Marj: %1
Gerçek Marj: -%0.8
Marj Sapması: -1.8%
Effective Multiplier: ×0.992
Durum: ZARARLI (kırmızı)
```

---

## Faz 2 (İleri Özellikler — Ana Motor Tamamlandıktan Sonra)

### F2.1: Worst-case Simülasyonu
"Bu profil %10 daha pahalı saatlere kayarsa ne olur?" stres testi.

### F2.2: Müşteri Segmentine Göre Eşikler
Farklı müşteri tipleri için farklı marj eriyor eşikleri.

### F2.3: Çoklu Dönem Karşılaştırması
Aynı müşterinin farklı dönemlerdeki marj sapmasını karşılaştırma.

### F2.4: Portföy Bazlı Analiz
Tüm müşterilerin toplam marj sapmasını gösterme.
