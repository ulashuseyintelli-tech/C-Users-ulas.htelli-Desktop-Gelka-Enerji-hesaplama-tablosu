# Teklif Birim Fiyat Gösterim Modları — Gereksinimler

## Problem
Piyasada tek bir doğru gösterim yok. Müşteri tipine göre teklif birim fiyatın kapsamı değişmeli.

## 3 Gösterim Modu

### 1. Detaylı (Şeffaf Model) — Teknik müşteri
- PTF ayrı satır
- YEKDEM ayrı satır
- Çarpan ayrı gösterilir
- Teklif Birim Fiyat = (PTF + YEKDEM) × Katsayı
- Dağıtım ayrı kalem

### 2. Birleşik Enerji (B2B Default) — Satın alma / muhasebe
- (PTF + YEKDEM) = tek satır "Enerji Birim Maliyet"
- Teklif Birim Fiyat = Enerji Birim Maliyet × Katsayı
- Dağıtım ayrı kalem

### 3. Tek Fiyat (Satış Modu) — Son kullanıcı / hızlı karar
- Teklif Birim Fiyat = (PTF + YEKDEM) × Katsayı + Dağıtım Birim
- Tek satırda "kWh fiyatı" gösterilir
- Altına not: "Dağıtım ve vergiler dahil enerji bedelidir" veya "Vergiler hariç"

## KDV Toggle
- KDV dahil göster / KDV hariç göster
- Konut → KDV dahil bakar
- Sanayi → KDV hariç bakar

## Önemli Kısıtlar
- Bu seçenekler hesaplama motorunu DEĞİŞTİRMEZ
- Tasarruf, enerji bedeli, dağıtım, BTV, KDV matrahı ve toplam hesapları mevcut mantıkla devam eder
- Bu sadece PDF gösterim modu + ekran gösterim modu
- Default: Birleşik Enerji (Model 2)

## Etkilenen Alanlar
- Frontend: Detaylı Karşılaştırma tablosu + "Birim Aktif Enerji" satırı
- PDF: Teklif PDF'indeki "Teklif Birim Fiyat" ve "Mevcut Birim Fiyat" satırları
- Açıklama metni: PDF'deki "Enerji Bedelinin Hesaplama Yapısı" paragrafı

## Durum
⏳ Beklemede — PTF kaydetme ve OSB dropdown sorunları çözüldükten sonra başlanacak.
