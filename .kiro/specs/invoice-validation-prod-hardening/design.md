# Tasarım Dokümanı — Fatura Doğrulama Production Hardening (Faz H0)

## §1 Kapsam ve Amaç

### Amaç

Fatura doğrulama pipeline'ının shadow → soft → hard mod geçişini production ortamında
güvenli, gözlemlenebilir ve geri alınabilir şekilde gerçekleştirmek.

### Hacim Profili ve Gerekçe

- Günlük ortalama: ~5 fatura
- 7 günde ≈35, 14 günde ≈70 fatura
- Bu hacimde yüzde bazlı sampling/canary istatistiksel olarak anlamsızdır
- Rollout: **tam trafik (%100) + süre bazlı (7 gün pencereler)**
- Barajlarda oran yerine **mutlak sayı** kullanılır

### Rollout Özeti

| Aşama | Mod | Trafik | Süre | Amaç |
|-------|-----|--------|------|------|
| D0 | shadow | %100, sample_rate=1.0 | 7 gün (~35 fatura) | Baseline + mismatch haritası |
| D1 | enforce_soft | %100 | 7 gün (~35 fatura) | Block etmeyen uyarı doğrulama |
| D2 | enforce_hard | %100 | 7 gün (~35 fatura) | Gerçek blok davranışı |

Toplam rollout süresi: **~21 gün**.

### Bağımlılıklar

- Faz A–F: Doğrulama motoru (54 test, tamamlandı)
- Faz G: Performans telemetrisi (35 test, tamamlandı)
- Mevcut metrikler: `invoice_validation_duration_seconds{phase}`, `invoice_validation_mode{mode}`
- Mevcut config: `INVOICE_VALIDATION_MODE`, `INVOICE_VALIDATION_LATENCY_BUDGET_P95_MS`, `INVOICE_VALIDATION_LATENCY_BUDGET_P99_MS`

### Parametreler (TBD — operasyonel girdi ile kalibre edilecek)

| Parametre | Açıklama | Varsayılan | Kaynak |
|-----------|----------|------------|--------|
| Worker retry politikası | Terminal exception davranışı | TBD | Ops |
| Pipeline SLO (P95/P99) | Latency hedefi (ms) | TBD (best effort) | Ops |


## §2 Runbook — 21 Günlük Rollout Checklist

### D0: Shadow Baseline (Gün 1–7)

**Giriş koşulu:** `INVOICE_VALIDATION_MODE=shadow`, `INVOICE_SHADOW_SAMPLE_RATE=1.0`

#### Günlük Checklist

| # | Kontrol | Metrik / Log | Beklenen |
|---|---------|-------------|----------|
| 1 | Total P95/P99 snapshot | `invoice_validation_duration_seconds{phase="total"}` | Stabil (baseline oluşuyor) |
| 2 | Shadow P95/P99 snapshot | `invoice_validation_duration_seconds{phase="shadow"}` | Stabil |
| 3 | Actionable mismatch count | `invoice_validation_shadow_mismatch_total` − whitelisted | == 0 |
| 4 | Whitelisted divergence sayısı | `missing_totals_skips` vb. | Sabit (artmıyor) |
| 5 | Latency budget ihlali logları | `telemetry_config` logger WARNING | 0 (veya budget tanımlı değilse N/A) |
| 6 | İşlenen fatura sayısı (kümülatif) | Uygulama logu veya metrik | Gün 4'te ≥20 (N_min) |
| 7 | Mode gauge doğru mu? | `invoice_validation_mode{mode="shadow"}` == 1 | Evet |

#### N_min Kontrolü

> IF gözlem penceresi sonunda `observed_invoice_count < N_min` THEN aşama değerlendirmesi ertelenir.
> 7 gün dolmuş olsa bile N_min sağlanmadan D1'e geçilmez. Aşama süresi uzatılır.

#### D0 Sonu Değerlendirme

- [ ] Kümülatif fatura ≥ N_min (20) — **sağlanmadan diğer şartlar değerlendirilmez**
- [ ] Actionable mismatch count == 0
- [ ] Whitelisted divergence sabit
- [ ] Baseline P95/P99 kaydedildi (7 günlük median — kalibrasyon girdisi)
- [ ] Latency budget kalibre edildi: `P95_MS = baseline_P95 + Δ`, `P99_MS = baseline_P99 + Δ`

**Tümü ✅ → D1'e geçiş. Herhangi biri ❌ → D0 uzatılır veya sorun giderilir.**

### D1: Enforce Soft (Gün 8–14)

**Giriş koşulu:** `INVOICE_VALIDATION_MODE=enforce_soft`

#### Günlük Checklist

| # | Kontrol | Metrik / Log | Beklenen |
|---|---------|-------------|----------|
| 1 | Total P95/P99 | `phase="total"` histogram | ≤ baseline + Δ |
| 2 | Enforcement P95/P99 | `phase="enforcement"` histogram | Stabil |
| 3 | Soft uyarı sayısı (warn) | Enforcement WARNING logları | Beklenen pattern |
| 4 | Uyarı code dağılımı | Blocker code breakdown | Bilinen kodlar |
| 5 | Actionable mismatch count | Shadow mismatch (hâlâ çalışıyor) | == 0 |
| 6 | İş akışı etkisi | Support ticket / manuel inceleme | Yok |
| 7 | Mode gauge | `invoice_validation_mode{mode="enforce_soft"}` == 1 | Evet |
| 8 | Latency budget ihlali | Budget WARNING logları | 0 |

#### N_min Kontrolü

> IF gözlem penceresi sonunda `observed_invoice_count < N_min` THEN aşama değerlendirmesi ertelenir.
> 7 gün dolmuş olsa bile N_min sağlanmadan D2'ye geçilmez.

#### D1 Sonu Değerlendirme

- [ ] Kümülatif fatura (D1 içinde) ≥ N_min (20) — **sağlanmadan diğer şartlar değerlendirilmez**
- [ ] P95/P99 ≤ baseline + Δ
- [ ] Actionable mismatch count == 0
- [ ] Soft uyarılar açıklanabilir (false positive yok veya kabul edilebilir)
- [ ] İş akışı etkisi yok (support ticket yok)

**Tümü ✅ → D2'ye geçiş. Herhangi biri ❌ → D1 uzatılır veya MODE=shadow rollback.**

### D2: Enforce Hard (Gün 15–21)

**Giriş koşulu:** `INVOICE_VALIDATION_MODE=enforce_hard`, rollback hazır

#### Günlük Checklist

| # | Kontrol | Metrik / Log | Beklenen |
|---|---------|-------------|----------|
| 1 | Total P95/P99 | `phase="total"` histogram | ≤ baseline + Δ |
| 2 | Enforcement P95/P99 | `phase="enforcement"` histogram | Stabil |
| 3 | Block count | `ValidationBlockedError` log sayısı | Beklenen pattern (0 veya açıklanabilir) |
| 4 | **Safety: retry loop** | Retry sayacı / worker log | **== 0 (kesin)** |
| 5 | Terminal state doğrulama | `ValidationBlockedError.terminal` log | Block → terminal, retry yok |
| 6 | Quality: blok triage | Her blok için root cause | Açıklanabilir (expected vs unexpected)¹ |
| 7 | Mode gauge | `invoice_validation_mode{mode="enforce_hard"}` == 1 | Evet |
| 8 | Latency budget ihlali | Budget WARNING logları | 0 |

#### Unexpected Block Tanımı

> ¹ **Unexpected block** = bilinen bir doğrulama kuralına veya dokümante edilmiş bir iş senaryosuna eşlenemeyen blok.
> D2 süresince herhangi bir unexpected block tespit edilirse → **anında MODE=shadow rollback + incident**.
> Expected block (gerçek hata yakalama, bilinen kural tetiklenmesi) rollback tetiklemez.

#### N_min Kontrolü

> IF gözlem penceresi sonunda `observed_invoice_count < N_min` THEN aşama değerlendirmesi ertelenir.
> 7 gün dolmuş olsa bile N_min sağlanmadan "kalıcı hard" kararı verilmez.

#### D2 Sonu Değerlendirme

- [ ] Kümülatif fatura (D2 içinde) ≥ N_min (20) — **sağlanmadan diğer şartlar değerlendirilmez**
- [ ] Safety barajı: retry_loop_count == 0 (kesin)
- [ ] Quality barajı: tüm bloklar triage ile açıklanmış; unexpected block = 0
- [ ] P95/P99 ≤ baseline + Δ
- [ ] Sistematik false positive yok

**Tümü ✅ → enforce_hard kalıcı. Safety barajı ❌ veya unexpected block ❌ → anında MODE=shadow + incident.**

### Incident Template

```
INCIDENT: Invoice Validation Rollout — [D0/D1/D2] Baraj İhlali
Tarih: YYYY-MM-DD
Aşama: [D0 Shadow / D1 Soft / D2 Hard]
İhlal edilen baraj: [Latency / Mismatch / Safety / Quality]
Detay: [kısa açıklama]
Etki: [iş etkisi varsa]
Aksiyon: INVOICE_VALIDATION_MODE=shadow uygulandı [saat]
Rollback onayı: [Owner adı/rolü]
Root cause: [TBD — triage sonrası]
Çözüm: [TBD]
```


## §3 Baraj Hesaplama

### Minimum Örneklem (N_min)

- Varsayılan: **N_min = 20** (≈4 gün, 5/gün hacimde)
- Yapılandırılabilir: `INVOICE_VALIDATION_GATE_N_MIN` env var (opsiyonel)
- N_min sağlanmadan baraj değerlendirmesi yapılmaz; aşama geçişi bekletilir
- Gerekçe: 5/gün hacimde 1–2 günlük veri ile "0 mismatch" demek erken false confidence üretir

### Gecikme Barajı

```
P95_total ≤ baseline_P95 + Δ
P99_total ≤ baseline_P99 + Δ
```

- Δ: `INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS` (mutlak ms, düşük hacimde 10–20 ms önerisi)
- Baseline: D0 süresince (7 gün) toplanan `invoice_validation_duration_seconds{phase="total"}` P95/P99 değerlerinin **7 günlük median'ı** (tek günlük spike baseline'ı bozmasın)
- SLO tanımlıysa: `min(baseline + Δ, SLO)` üst sınır olarak kullanılır

### Uyumsuzluk Barajı

```
actionable_mismatch_count == 0  (varsayılan)
```

- Eşik: `INVOICE_VALIDATION_MISMATCH_GATE_COUNT` (varsayılan=0)
- Whitelisted divergence (`missing_totals_skips` vb.) hesaba katılmaz
- Whitelisted divergence sayısının sabit kaldığı da kontrol edilir (artış → alarm)
- Düşük hacimde tek actionable bile sinyal; oran bazlı eşik kullanılmaz

### Güvenlik Barajı (İki Katmanlı)

| Katman | Koşul | İhlal Aksiyonu |
|--------|-------|----------------|
| Safety (kesin) | `retry_loop_count == 0` | Anında rollback + incident |
| Quality (triage) | Bloklar açıklanabilir | Ops triage kararı; sistematik false positive → rollback |

- Safety barajı otomatik; Quality barajı ops kararı gerektirir
- D2'de beklenen bloklar (gerçek hata yakalama) rollback tetiklemez
- Yalnızca açıklanamayan blok paterni veya sistematik false positive → rollback

## §4 Konfigürasyon

### Mevcut (Faz G'den)

| Env Var | Tip | Varsayılan | Açıklama |
|---------|-----|------------|----------|
| `INVOICE_VALIDATION_MODE` | string | `shadow` | Aktif mod (off/shadow/enforce_soft/enforce_hard) |
| `INVOICE_SHADOW_SAMPLE_RATE` | float | `0.05` | Shadow sampling oranı (D0'da 1.0 yapılır) |
| `INVOICE_VALIDATION_LATENCY_BUDGET_P95_MS` | float | (yok) | P95 gecikme bütçesi (opsiyonel) |
| `INVOICE_VALIDATION_LATENCY_BUDGET_P99_MS` | float | (yok) | P99 gecikme bütçesi (opsiyonel) |

### Yeni (Faz H0)

| Env Var | Tip | Varsayılan | Açıklama |
|---------|-----|------------|----------|
| `INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS` | float | (yok) | Gecikme barajı toleransı (ms) |
| `INVOICE_VALIDATION_MISMATCH_GATE_COUNT` | int | `0` | Uyumsuzluk barajı eşiği (mutlak sayı) |
| `INVOICE_VALIDATION_GATE_N_MIN` | int | `20` | Baraj değerlendirmesi için minimum örneklem |
| `INVOICE_VALIDATION_ROLLOUT_STAGE` | string | (yok) | Aktif rollout aşaması (D0/D1/D2, opsiyonel, bilgi amaçlı) |

### Geçersiz Değer Davranışı (Fail-Closed)

| Durum | Davranış |
|-------|----------|
| `LATENCY_GATE_DELTA_MS` negatif/non-numeric | Gecikme barajı devre dışı + WARNING log |
| `MISMATCH_GATE_COUNT` negatif/non-numeric | Varsayılan=0 kullanılır + WARNING log |
| `GATE_N_MIN` negatif/non-numeric | Varsayılan=20 kullanılır + WARNING log |
| `ROLLOUT_STAGE` geçersiz değer | Yok sayılır + WARNING log |

### Mode Flip Prosedürü

Config değişikliklerinin uygulanma yöntemi altyapıya bağlıdır:

| Senaryo | Uygulama Yöntemi |
|---------|-----------------|
| Hot-reload destekli (env var watch) | Env var değişikliği → uygulama otomatik algılar, restart gerekmez |
| Hot-reload yok, container/process bazlı | Env var değişikliği + rolling restart (zero-downtime) |
| Config dosyası bazlı | Config dosyası güncelleme + reload signal (SIGHUP vb.) |

> **Rollout başlamadan önce** hangi yöntemin geçerli olduğu belirlenmeli ve playbook'a yazılmalıdır.
> Rollback prosedürü aynı mekanizmayı kullanır; "config flip" tek başına yeterli değildir,
> deploy adımı (kim, nasıl, hangi araçla) net olmalıdır.

### Shadow Sampling — Düşük Hacim Notu

`INVOICE_SHADOW_SAMPLE_RATE` yapılandırılabilir kalır ancak düşük hacim rollout'u süresince **1.0 olarak ayarlanır**.
Bu, ileride hacim artarsa modeli yüzde bazlı sampling'e geçirmeyi kolaylaştırır.
Rollout tamamlandıktan sonra (kalıcı hard kararı) sample rate operasyonel ihtiyaca göre ayarlanabilir.

## §5 RACI — Sorumluluk Matrisi

| Aktivite | Owner | Oncall | Triage Lead |
|----------|-------|--------|-------------|
| Mode flip (aşama geçişi) | R, A | I | I |
| Günlük checklist kontrolü | I | R | I |
| Baraj değerlendirmesi (aşama sonu) | R, A | C | C |
| Rollback kararı | A | R | C |
| Rollback uygulaması (env var) | I | R | I |
| Incident kaydı açma | I | R | I |
| Blok triage (Quality barajı) | C | I | R, A |
| Kalibrasyon kararı (bütçe ayarı) | R, A | C | C |
| Kalıcı hard kararı (D2 sonu) | R, A | C | C |

**R** = Responsible (yapan), **A** = Accountable (onaylayan), **C** = Consulted, **I** = Informed

### Rol Tanımları

- **Owner**: Rollout'un sahibi; aşama geçiş ve kalıcılık kararlarını verir
- **Oncall**: Günlük checklist'i yürütür; rollback uygular; incident açar
- **Triage Lead**: Blok analizi ve quality barajı değerlendirmesi yapar

## §6 Exit Criteria — Aşama Geçiş Şartları

### D0 → D1 Geçiş Şartları

| # | Şart | Zorunlu |
|---|------|---------|
| 1 | Kümülatif fatura ≥ N_min (20) | Evet |
| 2 | Actionable mismatch count == 0 | Evet |
| 3 | Whitelisted divergence sabit | Evet |
| 4 | Baseline P95/P99 kaydedildi (7 günlük median) | Evet |
| 5 | Latency budget kalibre edildi | Evet |
| 6 | Owner onayı | Evet |

### D1 → D2 Geçiş Şartları

| # | Şart | Zorunlu |
|---|------|---------|
| 1 | Kümülatif fatura (D1) ≥ N_min (20) | Evet |
| 2 | P95/P99 ≤ baseline + Δ | Evet |
| 3 | Actionable mismatch count == 0 | Evet |
| 4 | Soft uyarılar açıklanabilir | Evet |
| 5 | İş akışı etkisi yok (support ticket yok) | Evet |
| 6 | Owner onayı | Evet |

### D2 Sonunda "Kalıcı Hard" Kararı

| # | Şart | Zorunlu |
|---|------|---------|
| 1 | Kümülatif fatura (D2) ≥ N_min (20) | Evet |
| 2 | Safety barajı: retry_loop_count == 0 | Evet |
| 3 | Quality barajı: tüm bloklar triage ile açıklanmış; unexpected block = 0 | Evet |
| 4 | P95/P99 ≤ baseline + Δ | Evet |
| 5 | Sistematik false positive yok | Evet |
| 6 | Owner + Triage Lead ortak onayı | Evet |

**Kalıcı hard kararı sonrası:** `INVOICE_VALIDATION_ROLLOUT_STAGE` kaldırılır, `MODE=enforce_hard` kalıcı olur. Rollback prosedürü playbook'ta kalır (her zaman hazır).
