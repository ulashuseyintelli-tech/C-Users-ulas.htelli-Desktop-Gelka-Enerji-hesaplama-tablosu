# Gereksinimler Dokümanı — Fatura Doğrulama Production Hardening (Faz H0)

## Giriş

Bu doküman, fatura doğrulama pipeline'ının production ortamına güvenli geçişini tanımlar.
Faz A–F (doğrulama motoru) ve Faz G (performans telemetrisi) tamamlanmıştır (89 test, 0 fail).
Shadow + Enforcement + Telemetry altyapısı aktiftir.

Faz H0'ın amacı: shadow → soft → hard mod geçişini kademeli ve geri alınabilir şekilde
yönetmek, gecikme/uyumsuzluk bütçelerini gerçek veriye göre kalibre etmek ve terminal-state
davranışının production'da retry loop üretmediğini doğrulamaktır.

### Hacim Profili

Günlük ortalama fatura adedi: **~5**. Bu düşük hacim nedeniyle:

- Yüzde bazlı sampling/canary istatistiksel olarak anlamsızdır
- Rollout **tam trafik (%100) + süre bazlı** yapılır
- Barajlarda oran yerine **mutlak sayı** kullanılır
- Her aşamada minimum 7 gün (≈35 fatura) gözlem gerekir
- Toplam rollout süresi: **~21 gün** (3 × 7 gün)

### Kapsam

- 3 aşamalı rollout planı (D0–D2), tam trafik + süre bazlı
- Gecikme ve uyumsuzluk bütçesi kalibrasyonu (mutlak sayı bazlı)
- Baraj (gate) tanımları ve rollback kuralları
- Terminal-state / retry davranışı doğrulaması
- Incident playbook (mod flip + oncall)

### Kapsam Dışı

- Tedarikçi eşleme/normalizasyon (Faz H — ayrı spec)
- Grafana dashboard JSON üretimi (ops sorumluluğu; spec yalnızca metrik gereksinimlerini tanımlar)
- Worker retry altyapısı refaktörü (mevcut davranış doğrulanır, değiştirilmez)
- Yeni doğrulama kuralları eklenmesi
- Yüzde bazlı sampling veya canary (hacim bunu desteklemiyor)

## Sözlük

- **Rollout_Aşaması**: D0–D2 arası kademeli geçiş adımı; her aşama belirli bir mod + tam trafik + gözlem süresi tanımlar
- **Baraj (Gate)**: Bir sonraki rollout aşamasına geçiş için sağlanması gereken yapılandırılabilir koşullar kümesi
- **Baseline**: D0 (shadow, %100 trafik) aşamasında toplanan referans metrik değerleri
- **Gecikme_Bütçesi**: P95/P99 yüzdelik dilimler için yapılandırılabilir üst sınır (ms); Faz G'de tanımlanan env var'lar kullanılır
- **Uyumsuzluk_Sayısı**: Shadow fazında tespit edilen actionable mismatch mutlak sayısı (düşük hacimde oran anlamsız)
- **Actionable_Mismatch**: Whitelisted divergence dışında kalan, gerçek iş etkisi olan uyumsuzluk
- **Terminal_Durum**: `ValidationBlockedError` ile işaretlenen, worker'ın retry etmeyeceği nihai durum
- **Mod_Flip**: `INVOICE_VALIDATION_MODE` env var değişikliği ile anlık mod geçişi

## Operasyonel Girdiler

| Girdi | Açıklama | Değer |
|-------|----------|-------|
| Günlük fatura adedi | Ortalama günlük invoice sayısı | **~5** |
| Worker retry politikası | Terminal exception'lar nasıl ele alınıyor? (retry / stop / DLQ) | **TBD** |
| Pipeline SLO | P95/P99 latency hedefi (ms) — varsa | **TBD** |


## Gereksinimler

### Gereksinim 1: 3 Aşamalı Tam Trafik Rollout (D0–D2)

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, mod geçişini tam trafik üzerinde kademeli ve süre bazlı yapmak istiyorum, böylece düşük hacimde bile yeterli gözlem verisi toplayabilirim.

#### Kabul Kriterleri

1. THE Rollout_Planı SHALL üç aşamadan oluşacaktır: D0 (Shadow Baseline), D1 (Enforce Soft), D2 (Enforce Hard)
2. THE D0 aşaması SHALL `mode=shadow`, `sample_rate=1.0` (%100 trafik) ile başlayacak ve minimum 7 gün (≈35 fatura) gözlem süresi gerektirecektir
3. THE D1 aşaması SHALL `mode=enforce_soft`, %100 trafik ile minimum 7 gün çalışacaktır
4. THE D2 aşaması SHALL `mode=enforce_hard`, %100 trafik ile minimum 7 gün çalışacak ve rollback hazır olacaktır
5. EACH aşama geçişi SHALL bir önceki aşamanın tüm baraj koşullarının sağlanmasını gerektirecektir
6. THE Rollout_Planı SHALL yüzde bazlı sampling veya canary kullanmayacaktır (günlük 5 fatura hacminde istatistiksel olarak anlamsız)
7. THE Rollout_Planı SHALL toplam ~21 gün sürecektir (3 × 7 gün minimum)

### Gereksinim 2: Gecikme Barajı (Latency Gate)

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, mod geçişinin gecikmeyi kabul edilemez seviyeye çıkarmadığını doğrulamak istiyorum.

#### Kabul Kriterleri

1. THE Baraj SHALL `P95_total <= baseline_P95 + Δ` koşulunu kontrol edecektir; Δ yapılandırılabilir olacaktır (ms cinsinden mutlak değer)
2. THE Baraj SHALL `P99_total <= baseline_P99 + Δ` koşulunu kontrol edecektir; Δ yapılandırılabilir olacaktır
3. THE Baraj değerleri SHALL ortam değişkeni ile tanımlanacaktır (`INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS`); hardcode olmayacaktır
4. WHEN gecikme barajı ihlal edildiğinde, THE Sistem SHALL uyarı logu üretecektir
5. THE Baseline değerleri SHALL D0 aşamasında toplanan `invoice_validation_duration_seconds{phase="total"}` histogram'ından hesaplanacaktır
6. IF Pipeline SLO tanımlıysa, THEN THE Baraj SHALL SLO değerlerini de üst sınır olarak dikkate alacaktır

### Gereksinim 3: Uyumsuzluk Barajı (Mismatch Gate) — Mutlak Sayı Bazlı

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, shadow fazındaki actionable mismatch sayısının sıfır olduğunu doğrulamak istiyorum, çünkü günlük 5 fatura hacminde tek bir actionable mismatch bile alarm gerektirir.

#### Kabul Kriterleri

1. THE Baraj SHALL `actionable_mismatch_count == 0` koşulunu varsayılan olarak kontrol edecektir (düşük hacimde oran anlamsız; tek actionable bile alarm)
2. THE Baraj SHALL whitelisted divergence'ı (ör. `missing_totals_skips`) mismatch hesabından hariç tutacaktır
3. THE Baraj eşiği SHALL ortam değişkeni ile yapılandırılabilir olacaktır (`INVOICE_VALIDATION_MISMATCH_GATE_COUNT`); varsayılan = 0
4. WHEN actionable mismatch tespit edildiğinde, THE Sistem SHALL uyarı logu üretecek ve sonraki aşamaya geçişi engelleyecektir
5. THE Baraj SHALL whitelisted divergence sayısının sabit kaldığını (artmadığını) da kontrol edecektir
6. IF gözlem penceresi içinde toplam işlenen fatura sayısı < N_min (varsayılan=20, yapılandırılabilir) ise, THEN THE Baraj değerlendirmesi yapılmayacaktır; aşama geçişi bekletilecektir (erken false confidence önlemi)

### Gereksinim 4: Güvenlik Barajı — Terminal State Doğrulaması

**Kullanıcı Hikayesi:** Bir geliştirici olarak, `ValidationBlockedError` sonrası worker'ın retry loop'a girmediğini production'da doğrulamak istiyorum.

#### Kabul Kriterleri

1. THE Doğrulama SHALL `ValidationBlockedError.terminal = True` sentinel'inin worker tarafından tanındığını kanıtlayacaktır
2. THE Doğrulama SHALL `ValidationBlockedError` sonrası retry sayacının artmadığını log/metrik ile gösterecektir
3. IF worker retry politikası DLQ destekliyorsa, THEN THE Doğrulama SHALL bloklanmış faturaların DLQ'ya yönlendirildiğini doğrulayacaktır
4. IF worker retry politikası DLQ desteklemiyorsa, THEN THE Doğrulama SHALL bloklanmış faturaların drop edildiğini ve loglandığını doğrulayacaktır
5. THE Doğrulama SHALL D2 (Enforce Hard) aşamasında en az bir gerçek bloklanma senaryosu gözlemleyecektir
6. THE Güvenlik barajı iki alt barajdan oluşacaktır:
   - **Safety barajı (kesin):** `retry_loop_count == 0` (enforce_hard süresince); ihlali → anında rollback
   - **Quality barajı (triage):** blokların açıklanabilir olması; beklenen/istenen bloklar (gerçek hata yakalama) rollback tetiklemez, yalnızca sistematik false positive veya açıklanamayan blok paterni rollback tetikler
7. THE Quality barajı değerlendirmesi SHALL runbook'taki triage prosedürüne göre yapılacaktır (otomatik değil, ops kararı)

### Gereksinim 5: Rollback Kuralı

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, herhangi bir baraj ihlalinde anında güvenli duruma dönebilmek istiyorum.

#### Kabul Kriterleri

1. WHEN herhangi bir baraj ihlal edildiğinde, THE Operatör SHALL `INVOICE_VALIDATION_MODE=shadow` ayarlayarak anında rollback yapabilecektir
2. THE Rollback SHALL mod flip ile gerçekleşecektir; deployment veya restart gerektirmeyecektir
3. THE Rollback sonrası SHALL tüm metrikler normal akışa dönecektir (histogram sıfırlanmaz, gauge güncellenir)
4. THE Rollback prosedürü SHALL incident playbook'ta dokümante edilecektir
5. THE Rollback kuralı SHALL tek cümle ile ifade edilebilecektir: "Baraj ihlali → MODE=shadow (immediate)"
6. WHEN baraj ihlali tespit edildiğinde, THE Operatör SHALL aynı gün içinde incident kaydı açacaktır (rollback + incident kaydı birlikte)
7. THE Playbook SHALL rollback'in kim tarafından ve hangi mekanizma ile uygulanacağını (env var deploy yöntemi) açıkça belirtecektir

### Gereksinim 6: Aşama Sonu Telemetri Raporu

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, her rollout aşamasının sonunda yapılandırılmış bir telemetri raporu görmek istiyorum.

#### Kabul Kriterleri

1. THE Rapor SHALL `invoice_validation_duration_seconds` P95/P99 değerlerini phase bazında (total/shadow/enforcement) içerecektir
2. THE Rapor SHALL `invoice_validation_shadow_mismatch_total` ve actionable mismatch mutlak sayısını içerecektir
3. THE Rapor SHALL enforcement soft/hard block count değerini içerecektir
4. THE Rapor SHALL her aşama geçişinde baraj durumunu (pass/fail) gösterecektir
5. THE Rapor SHALL toplam işlenen fatura sayısını ve gözlem süresini (gün) içerecektir
6. THE Rapor formatı SHALL makine tarafından okunabilir olacaktır (JSON veya structured log)

### Gereksinim 7: Kalibre Edilmiş Bütçeler

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, D0 baseline verisine dayalı gerçekçi bütçe değerleri belirlemek istiyorum.

#### Kabul Kriterleri

1. THE Kalibrasyon SHALL D0 aşamasında toplanan baseline P95/P99 değerlerini referans alacaktır (≈35 fatura üzerinden)
2. THE Kalibrasyon SHALL `INVOICE_VALIDATION_LATENCY_BUDGET_P95_MS` değerini `baseline_P95 + margin` olarak önerecektir; margin yapılandırılabilir olacaktır
3. THE Kalibrasyon SHALL `INVOICE_VALIDATION_LATENCY_BUDGET_P99_MS` değerini `baseline_P99 + margin` olarak önerecektir
4. THE Kalibrasyon SHALL düşük hacimde Δ için mutlak değer önerecektir (ör. 10–20 ms); yüzde bazlı margin düşük örneklemde güvenilmezdir
5. WHEN operasyonel girdiler (SLO) sağlandığında, THE Kalibrasyon SHALL bütçe değerlerini SLO'ya göre ayarlayacaktır

### Gereksinim 8: Incident Playbook

**Kullanıcı Hikayesi:** Bir oncall mühendisi olarak, mod geçişi sırasında sorun yaşandığında ne yapacağımı bilmek istiyorum.

#### Kabul Kriterleri

1. THE Playbook SHALL "mod flip + oncall" senaryosunu adım adım tanımlayacaktır
2. THE Playbook SHALL rollback komutunu (env var değişikliği) açıkça belirtecektir
3. THE Playbook SHALL hangi metriklerin izleneceğini ve eşik değerlerini listeleyecektir
4. THE Playbook SHALL eskalasyon prosedürünü tanımlayacaktır
5. THE Playbook SHALL PII içermeyecektir; log örnekleri anonimize edilecektir
6. THE Playbook SHALL düşük hacim bağlamını dikkate alacaktır (tek fatura bloğu bile incident tetikler)

### Gereksinim 9: Yapılandırma Dışsallaştırması

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, tüm baraj değerlerinin ve rollout parametrelerinin hardcode değil, yapılandırılabilir olmasını istiyorum.

#### Kabul Kriterleri

1. THE Sistem SHALL aşağıdaki değerleri ortam değişkeni olarak kabul edecektir:
   - `INVOICE_VALIDATION_MODE` (mevcut)
   - `INVOICE_VALIDATION_LATENCY_BUDGET_P95_MS` (mevcut, Faz G)
   - `INVOICE_VALIDATION_LATENCY_BUDGET_P99_MS` (mevcut, Faz G)
   - `INVOICE_VALIDATION_LATENCY_GATE_DELTA_MS` (yeni — gecikme barajı toleransı, ms)
   - `INVOICE_VALIDATION_MISMATCH_GATE_COUNT` (yeni — uyumsuzluk barajı eşiği, mutlak sayı, varsayılan=0)
   - `INVOICE_VALIDATION_ROLLOUT_STAGE` (yeni — aktif rollout aşaması, opsiyonel, D0/D1/D2)
2. THE Sistem SHALL geçersiz değerlerde güvenli varsayılana dönecektir (fail-closed prensibi)
3. THE Sistem SHALL yapılandırma değişikliklerini restart gerektirmeden uygulayabilecektir (env var reload)
