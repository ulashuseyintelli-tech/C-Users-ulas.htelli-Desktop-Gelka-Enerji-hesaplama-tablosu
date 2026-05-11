# Learning Sector Profiles / Öğrenen Sektörel Profil Motoru

## Durum: BACKLOG (gelecek sprint için planlanacak)

## Kapsam

Her analizde müşteri sektörünü, T1/T2/T3 kWh değerlerini, oranlarını ve ay bilgisini kaydet.
Zamanla sektör bazlı ortalama T1/T2/T3 profili çıkar.
Statik şablon ile öğrenilmiş sektör ortalamasını karşılaştır.
Yeterli veri varsa yeni müşteriye öğrenilmiş profil öner.
Veri sayısı azsa statik şablon + risk buffer kullanılmaya devam et.
Gerçek saatlik OSOS/EPİAŞ verisi gelirse manuel T1/T2/T3 verisine göre daha yüksek öncelik ver.

## Veri Öncelik Sırası

1. **Gerçek saatlik veri** (OSOS/EPİAŞ sayaç verisi — en güvenilir)
2. **Gerçek T1/T2/T3 fatura verisi** (müşterinin faturasından okunan)
3. **Öğrenilmiş sektör profili** (aynı sektördeki geçmiş müşterilerden)
4. **Statik şablon** (kodda tanımlı 16 sektörel profil + risk buffer)
5. **Default güvenli profil** (düz dağılım + yüksek risk buffer)

## Teknik Gereksinimler

- Her analiz sonucunda kaydet: sektör, dönem, T1/T2/T3 kWh, T1/T2/T3 %, kaynak (fatura/manuel/şablon)
- Minimum 10 veri noktası → sektör ortalaması hesaplanabilir
- Standart sapma yüksekse → risk buffer artır
- Mevsimsel fark: yaz/kış ayrımı (klima yükü)
- API endpoint: GET /api/pricing/sector-profiles?sector=otel → öğrenilmiş profil döner

## Bağımlılıklar

- Mevcut T1/T2/T3 giriş modu tamamlanmış olmalı
- Dağıtım bedeli entegrasyonu tamamlanmış olmalı
- Risk paneli çalışır durumda olmalı

## NOT

Bu spec mevcut implementasyonu BÖLMEZ.
Mevcut statik şablonlar + risk buffer sistemi çalışmaya devam eder.
Öğrenilmiş profiller ek bir katman olarak eklenir.
