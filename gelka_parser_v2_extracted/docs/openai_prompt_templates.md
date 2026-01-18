# OpenAI Vision Prompt Şablonları (TR)

## A) Fatura Detayı (kalem tablosu)
Sistem mesajı / talimat:
- Sadece bu görseldeki **Fatura Detayı** tablosunu oku.
- Aşağıdaki JSON dışında hiçbir şey yazma.
- Sayıları olduğu gibi yaz (TR formatıyla), biz normalize edeceğiz.

Kullanıcı prompt:
```
Bu görsel CK Boğaziçi elektrik faturasının sadece “Fatura Detayı” tablosudur.
Tablodaki her satır için: açıklama (label), tüketim kWh, birim fiyat TL, bedel TL değerlerini çıkar.
Sadece JSON üret:

{
  "section":"fatura_detayi",
  "lines":[
    {"label":"", "qty_kwh":"", "unit_price_tl_per_kwh":"", "amount_tl":""}
  ]
}
```

## B) Vergi ve Fonlar + KDV
```
Bu görselde sadece “Vergi ve Fonlar” bölümünü oku.
BTV/Elektrik Tüketim Vergisi tutarlarını ve KDV’yi çıkar.
Sadece JSON üret:

{
 "section":"vergiler",
 "btv_tl":"", 
 "other_taxes_tl":"", 
 "vat_base_tl":"", 
 "vat_amount_tl":""
}
```

## C) Özet (Ödenecek Tutar / Son Ödeme)
```
Bu görselde sadece “Fatura Özeti” bölümünü oku.
Ödenecek Tutar ve Son Ödeme Tarihi değerlerini çıkar.
Sadece JSON üret:

{
 "section":"ozet",
 "payable_tl":"",
 "due_date":""
}
```

> Not: “Günlük kWh”, “Fatura Ort. Tük” gibi alanları asla “toplam tüketim” diye dönme.
