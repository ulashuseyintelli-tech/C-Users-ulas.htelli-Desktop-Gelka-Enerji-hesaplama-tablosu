# Uzlaştırma Dönemi Verileri

Bu klasör EPİAŞ uzlaştırma dönemi Excel dosyalarını içerir.

## Dosya Formatı

EPİAŞ'tan indirilen "Uzlaştırma Dönemi Detayı" Excel dosyaları:
- Sheet: "Uzlaştırma Dönemi Detayı"
- Sütunlar: Tarih (datetime), Versiyon, Bölge, PTF (TL/MWh), SMF (TL/MWh), ...
- Saatlik veri (saat bilgisi Tarih sütunundaki datetime'dan çıkarılır)
- Bölge: TR1 filtresi uygulanır

## Mevcut Dosyalar

| Dosya | Dönem | Saat | PTF Ort (TL/MWh) |
|-------|-------|------|-------------------|
| 104842_Uzlastirma_Donemi_Detayi_01_2026.xlsx | 2026-01 | 744 | 2.895 |
| 104842_Uzlastirma_Donemi_Detayi_02_2026.xlsx | 2026-02 | 672 | 2.078 |
| 104842_Uzlastirma_Donemi_Detayi_04_2026.xlsx | 2026-04 | 720 | 921 |

## Kullanım

Bu dosyalar `backend/app/pricing/excel_parser.py` tarafından parse edilir ve
`hourly_market_prices` tablosuna yüklenir. Risk analizi modülü bu saatlik
verileri kullanarak:

1. Ağırlıklı PTF hesabı (müşteri profili × saatlik PTF)
2. Katsayı simülasyonu (hangi katsayıda zarar saati oluşur)
3. Güvenli katsayı önerisi (5. persentil algoritması)
4. Risk skoru (volatilite, puant yoğunlaşma, sapma)
5. Çoklu dönem karşılaştırma

hesaplamalarını yapar.

## Not

Excel dosyaları .gitignore'da hariç tutulmuştur (*.xlsx).
Yeni dönem verisi eklemek için dosyayı bu klasöre koyun ve
frontend'den "Piyasa Verisi Yükle" ile upload edin.
