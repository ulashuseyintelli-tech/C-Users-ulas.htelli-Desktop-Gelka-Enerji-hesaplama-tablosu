# Uygulama Notları (TR)

## 1) “Bütün faturaları yanlış okuyor” neden olur?
Çünkü PDF içinde aynı anda şunlar var:
- faturalandırılan tüketim (kalem tablosunda)
- günlük ortalama tüketim (kWh/gün)
- geçmiş yıl tüketimleri
- sayaç endeksleri / farklar

**Doğru kural:** `total_kwh` ve `enerji_bedeli` sadece `Fatura Detayı` satırlarından gelir.

## 2) CK Boğaziçi için bölge (region) yaklaşımı
CK Boğaziçi sayfasında üç kritik bölge var:
- Özet (Ödenecek Tutar, Son Ödeme)
- Fatura Detayı (kalem tablosu)  ✅ tek güvenilir tüketim/bedel kaynağı
- Vergi ve Fonlar (BTV, KDV)

Bu pakette `profiles/ck_bogazici_regions.py` içinde örnek koordinatlar var.
Koordinatlar **sayfa boyutuna göre normalize** edilmiştir (0..1).

## 3) OpenAI Vision prompt şablonu (önerilen)
Aynı PDF’den “her şeyi çıkar” demek hataya davetiye.
Bunun yerine *bölge görselini* gönderip şu JSON’u zorla:

```json
{
  "section": "fatura_detayi",
  "lines":[
    {"label":"...", "qty_kwh":"...", "unit_price_tl_per_kwh":"...", "amount_tl":"..."}
  ]
}
```

Sonra TR sayı normalize et.

Prompt örnekleri: `docs/openai_prompt_templates.md`

## 4) Validator kural seti (kritik)
- `payable` ile `total` 5 TL toleransla yakın olmalı.
- `subtotal + vat_amount` toplamla %1 toleransla yakın olmalı.
- `total_kwh` kalemlerden türetilir; “kWh/gün” asla kullanılmaz.

## 5) Demand qty ama unit price yok
- `demand_qty` var, `demand_unit_price` yoksa:
  - hesaplamada `0` kullan
  - **hata değil uyarı** üret (`WARN_DEMAND_PRICE_MISSING`)
