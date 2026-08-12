# S5 — Owner-Controlled UAT #2: Teslimat Kanıtı

Bu dosya, S5 Outreach modülünün ilk gerçek e-posta teslimatının kanıtıdır.
**Hiçbir secret (SMTP parolası, API anahtarı, AUTH payload) içermez.**

## Gönderilen mesaj (message #2)

| Alan | Değer |
|---|---|
| Message ID | 2 |
| Durum | `SENT` |
| Alıcı | `selingozlu@gelkaenerji.com.tr` |
| Gönderici | `bilgi@gelkaenerji.com.tr` |
| Konu | GELKA Outreach Sistemi — Test İletisi |
| Provider | `smtp` |
| Provider message ID | PRESENT |
| Gönderim zamanı (UTC) | 2026-08-12 07:08:58.875512 |
| Onay zamanı (UTC) | 2026-08-12 07:08:58.707912 |
| Failure code | — |
| recipient_category | `TEST_RECIPIENT` |
| recipient_legal_type | `UNKNOWN` |
| Prospect bağlamı | YOK (owner-controlled test) |
| Customer bağlamı | YOK (owner-controlled test) |

### Gönderim anındaki compliance snapshot

| Alan | Değer |
|---|---|
| can_send | `True` |
| reason_codes | `[]` |
| suppression_status | `CLEAR` |
| iys_status | `IYS_NOT_APPLICABLE_TEST_RECIPIENT` |
| kvkk_status | `NOT_APPLICABLE` |
| source_status | `NOT_APPLICABLE` |

### Gövde (editable blok)

```
Bu ileti GELKA Sales Operating System S5 Outreach modülünün teknik teslimat testi amacıyla hazırlanmıştır. Ticari tanıtım gönderimi değildir.
```

### Yasal footer (SYSTEM/immutable blok — deterministik üretildi)

```
---
GELKA ENERJİ YATIRIM SANAYİ VE TİCARET ANONİM ŞİRKETİ
MERSİS No: 0391067987300001
İletişim: bilgi@gelkaenerji.com.tr · 08505326995
https://www.gelkaenerji.com.tr
Aydınlatma metni: https://www.gelkaenerji.com.tr/kvkk.html
Bu tür ticari elektronik iletileri almak istemiyorsanız bu e-postayı yanıtlayarak RET yazabilirsiniz.
Bu ileti ticari elektronik iletidir; ret bildiriminiz derhal işleme alınır.
```

**Owner inbox doğrulaması:** tek kopya / From / To / konu / gövde / Türkçe karakterler /
footer (MERSİS, telefon, KVKK URL, RET talimatı) / görsel bütünlük — hepsi PASS.
Beklenmeyen bağlantı veya ek: YOK.

## Message #1 — kalıcı başarısızlık kanıtı (DEĞİŞTİRİLMEDİ)

İlk mailbox (`info@gelkaenerji.com.tr`) SMTP AUTH 535 verdiği için başarısız oldu.
Bu kayıt bilinçli olarak olduğu gibi bırakıldı — sıfırlanmadı, silinmedi, tekrar denenmedi.

| Alan | Değer |
|---|---|
| Message ID | 1 |
| Durum | `FAILED` |
| Failure code | `AUTH_FAILED` |
| Provider message ID | ABSENT |
| sent_at | NOT SET (mail gönderilmedi) |

**Kök neden (kodda hata değildi):** `machine-local.env` içinde eski ve yeni `OUTREACH_`
blokları aynı anda bulunuyordu. Dosya sıralı satır-okumayla parse edildiği ve son değer
kazandığı için sürekli eski host/kullanıcı adı okundu. Duplicate anahtarlar temizlenince
aynı kodla AUTH PASS alındı.

## Yan etki doğrulaması (gönderim öncesi → sonrası)

| Tablo | Değişim |
|---|---|
| Activity | 3 → 3 (değişmedi) |
| Task | 6 → 6 (değişmedi) |
| ProspectCompany | 0 → 0 (değişmedi) |
| Customer | 2 → 2 (değişmedi) |
| SuppressionEntry | 0 |
| OutreachMessage (toplam) | 2 |

`EMAIL_SENT` Activity oluşmadı — **beklenen davranış**: owner-controlled test mesajının
Prospect/Customer bağlamı yoktur, `_resolve_customer_id_for_crm()` `None` döner ve
Activity üretimi erken çıkar (bkz. `app/outreach/service.py`).

## Güvenlik / kapsam sınırları

- Prospect e-postası gönderilmedi.
- Customer e-postası gönderilmedi.
- Toplu gönderim yapılmadı.
- Provider çağrı sayısı: **tam olarak 1**.
- Otomatik retry gerçekleşmedi (kodda yok).
- Canlı duplicate/negative test **çalıştırılmadı** (owner talimatı: provider güvenliği
  öncelikli). Duplicate koruma kanıtı: `tests/test_outreach.py` içindeki 3 otomatik test
  (double-click, concurrent-claim, provider-kabul-etti-DB-commit-patladı) + read-only kod
  kanıtı (`service.py`: status kontrolü, provider çağrısından önce gelir).
- SMTP parolası / OpenAI anahtarı / AUTH payload hiçbir çıktıda, logda veya bu dosyada
  yer almaz.

## Program sınırı

Bu UAT, **gerçek prospect outreach yetkisi vermez.** Gerçek prospect gönderimi
`IYS_UNKNOWN` nedeniyle compliance engine tarafından hâlâ hard-blocked durumdadır
(bkz. `app/outreach/compliance.py`).
