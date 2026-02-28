# Fatura Doğrulama Rollout Playbook

---

## 0. Activation Checklist (Freeze + Observe)

### 0.1 Freeze

- [ ] Release tag atıldı (örn. `v2.0-validation-platform`)
- [ ] CI yeşil (116 test) ve tag commit'ine bağlı
- [ ] Config değişiklikleri dışında kod deploy edilmeyecek (freeze)

### 0.2 Deploy (D0 Shadow)

Env var'lar:

```env
INVOICE_VALIDATION_MODE=shadow
INVOICE_VALIDATION_ROLLOUT_STAGE=D0
INVOICE_SHADOW_SAMPLE_RATE=1.0
INVOICE_VALIDATION_GATE_N_MIN=20
INVOICE_VALIDATION_MISMATCH_GATE_COUNT=0
INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS=          # SLO yoksa boş bırak (None → gate disabled)
```

### 0.3 Günlük Operasyon (7 gün)

Owner her gün:

| Kontrol | Beklenen |
|---------|----------|
| `generate_stage_report()` al | Rapor oluşuyor |
| `observed_count >= N_min (20)` | 7. güne kadar sağlanmalı |
| `overall_verdict` | PASS veya DEFER (FAIL → triage) |
| `safety gate` (retry_loop) | PASS (=0) |
| `actionable_mismatch_count` | 0 |
| `unexpected_block_count` | 0 |
| Latency P95/P99 snapshot | İzleniyor (budget varsa) |

Oncall: Unexpected block veya retry loop → immediate rollback (§4)

Triage Lead: WARN/FAIL varsa reason sınıflandırması (bug vs veri kalitesi vs yanlış kural)

### 0.4 D0 → D1 Geçiş Koşulu

Tümü sağlanmalı:

- `observed_count >= N_min`
- `overall_verdict == PASS`
- Safety PASS (retry_loop == 0)
- Actionable mismatch == 0
- Unexpected block == 0
- 7 gün gözlem süresi tamamlandı

Geçiş: `ROLLOUT_STAGE=D1`, mode ops kararına göre shadow veya enforce_soft

### 0.5 Rollback Kuralı (tek satır)

```
Herhangi bir Safety FAIL veya unexpected block → MODE=shadow, stage geriye çek, incident aç.
```

### 0.6 Kritik Uyarı

Düşük hacimde "7 gün gözlem" kuralı minimum veri içindir. 3 günde 20 invoice dolsa bile erken geçiş yapmayın. Variance yüksek; 7 gün median baseline tanımı var.

---

## 1. Genel Bakış

Bu playbook, fatura doğrulama sisteminin shadow → soft → hard moduna geçiş sürecini yönetir.

- **Günlük fatura hacmi:** ~5 (düşük hacim)
- **Rollout stratejisi:** %100 trafik, zaman bazlı (7 gün/aşama, ~21 gün toplam)
- **Aşamalar:** D0 (shadow) → D1 (enforce_soft) → D2 (enforce_hard)
- **N_min:** 20 (minimum örneklem — aşama geçişi için zorunlu)

## 2. Aşama Geçiş Kontrol Listesi

### D0 → D1 Geçişi (shadow → enforce_soft)

1. `observed_invoice_count >= N_min (20)` — sağlanmadıysa geçiş ertelenir
2. 7 gün gözlem süresi tamamlandı
3. Latency gate: PASS (current P95/P99 ≤ baseline + Δ)
4. Mismatch gate: PASS (actionable_count ≤ threshold)
5. Safety gate: PASS (retry_loop_count == 0)
6. Unexpected block gate: PASS (unexpected_block_count == 0)
7. `generate_stage_report()` çıktısı incelendi ve onaylandı

### D1 → D2 Geçişi (enforce_soft → enforce_hard)

Aynı kontrol listesi + ek:
- D1 süresince hiç unexpected block gözlemlenmedi
- Soft warn'lar triage edildi ve kabul edilebilir seviyede

### N_min Kuralı

```
IF observed_invoice_count < N_min THEN stage evaluation is deferred.
```

7 gün dolsa bile yeterli örneklem yoksa aşama geçişi yapılmaz.

## 3. Mode Flip Prosedürü

| Adım | Komut / İşlem | Sorumlu |
|------|---------------|---------|
| 1 | Env var güncelle: `INVOICE_VALIDATION_MODE=enforce_soft` | Ops |
| 2 | Env var güncelle: `INVOICE_VALIDATION_ROLLOUT_STAGE=D1` | Ops |
| 3 | Uygulama restart / config reload (hot-reload desteklenmiyorsa deploy gerekir) | Ops |
| 4 | Log'da `gate_evaluation_info` mesajını doğrula | Ops |
| 5 | İlk fatura işlendikten sonra `generate_stage_report()` çıktısını kontrol et | Ops |

### Hot-Reload Desteği

- Mevcut yapı: env var değişikliği restart gerektirir
- Config değişikliği sonrası doğrulama: log'da yeni mode ve stage değerlerini kontrol et
- Rollback: aynı prosedür, ters yönde (env var → restart)

## 4. Rollback Prosedürü

### Anında Rollback Tetikleyicileri

- Safety gate FAIL (retry_loop_count != 0)
- Unexpected block (D2'de herhangi bir unexpected block)
- Latency gate FAIL (P95/P99 bütçe aşımı)

### Rollback Adımları

1. `INVOICE_VALIDATION_MODE=shadow` olarak güncelle
2. `INVOICE_VALIDATION_ROLLOUT_STAGE` değerini bir önceki aşamaya çek (veya kaldır)
3. Uygulama restart
4. Log'da `mode=shadow` doğrula
5. Aynı gün incident kaydı oluştur

### Unexpected Block Tanımı

```
Unexpected block = bilinen doğrulama kuralına veya dokümante edilmiş
iş senaryosuna eşlenemeyen blok.
```

D2'de herhangi bir unexpected block → anında rollback (MODE=shadow).

## 5. İzlenecek Metrikler

| Metrik | Eşik | Aksiyon |
|--------|-------|---------|
| `retry_loop_count` | > 0 | Anında rollback |
| `unexpected_block_count` | > 0 (D2) | Anında rollback |
| Latency P95 | > baseline + Δ | Gate FAIL → rollback |
| Latency P99 | > baseline + Δ | Gate FAIL → rollback |
| `actionable_mismatch_count` | > threshold | Gate FAIL → triage |

### Baseline Tanımı

```
Baseline = D0 shadow phase P95/P99 median of 7 days.
```

Tek snapshot değil, 7 günün median'ı kullanılır.

## 6. Eskalasyon Prosedürü

| Seviye | Koşul | Aksiyon |
|--------|-------|---------|
| L1 | Mismatch gate FAIL | Ops triage — bilinen fark mı? |
| L2 | Latency gate FAIL | Ops + Dev — performans analizi |
| L3 | Safety gate FAIL | Anında rollback + incident kaydı |
| L3 | Unexpected block (D2) | Anında rollback + incident kaydı |

## 7. Incident Template

```
Tarih: [YYYY-MM-DD]
Aşama: [D0/D1/D2]
Tetikleyici: [gate adı + FAIL nedeni]
Etki: [kaç fatura etkilendi]
Aksiyon: [rollback yapıldı / triage devam ediyor]
Çözüm: [kök neden + düzeltme]
Sorumlu: [isim]
```

## 8. Shadow Sampling Notu

```
INVOICE_SHADOW_SAMPLE_RATE = 1.0 (düşük hacim rollout süresince)
```

Konfigüre edilebilir kalır — ileride hacim artarsa örnekleme oranı düşürülebilir.

## 9. Konfigürasyon Referansı

| Env Var | Varsayılan | Açıklama |
|---------|-----------|----------|
| `INVOICE_VALIDATION_MODE` | `shadow` | off / shadow / enforce_soft / enforce_hard |
| `INVOICE_VALIDATION_ROLLOUT_STAGE` | (yok) | D0 / D1 / D2 |
| `INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS` | (yok) | Latency bütçe farkı (ms) |
| `INVOICE_VALIDATION_MISMATCH_GATE_COUNT` | `0` | Mismatch eşiği (None=devre dışı) |
| `INVOICE_VALIDATION_GATE_N_MIN` | `20` | Minimum örneklem sayısı |
| `INVOICE_SHADOW_SAMPLE_RATE` | `1.0` | Shadow örnekleme oranı |
