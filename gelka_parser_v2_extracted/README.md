# Gelka Fatura Parser (v2) — Doğru okuma paketi

Bu paket, elektrik e-fatura PDF'lerini **yanlış alanlardan okumayı** engellemek için v2 parser + validator setini içerir.

## Bu v2 neyi düzeltir?
- **CK Boğaziçi** gibi image tabanlı PDF'lerde: "Günlük kWh" / "Fatura Ort.Tük" gibi alanları **tüketim sanma** hatasını bitirir.
- Tüketimi ve bedelleri **sadece FATURA DETAYI (kalem tablosu)** üzerinden toplar.
- TR sayı formatı (1.234,56) için tek bir normalize fonksiyonu kullanır.
- Tutarlılık doğrulaması: (Kalemler + KDV) ≈ Toplam değilse **başarılı saymaz**.

## Çalışma şekli
1) PDF -> sayfa görseli (render)
2) Profil bazlı **bölge kırpma** (Fatura Detayı, Vergi/Fonlar, Özet)
3) OCR / Vision (OpenAI veya kendi OCR'unuz) ile metin çıkarma
4) Kural tabanlı parse + validator

> Not: Bu repo OpenAI çağrısını “adapter” olarak bırakır. Siz kendi OpenAI client'ınıza bağlarsınız.

## Kullanım (örnek)
```bash
pip install -r requirements.txt
python -m gelka_invoice_parser.cli parse --pdf "/path/fatura.pdf" --out out.json
```

Detay: `docs/IMPLEMENTATION_NOTES_TR.md`
