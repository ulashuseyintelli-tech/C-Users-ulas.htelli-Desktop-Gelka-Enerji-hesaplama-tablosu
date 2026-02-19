# Gereksinimler: Drift Guard for Guard Decision Middleware

## Genel Bakış

Guard Decision Middleware pipeline'ına drift detection alt-sistemi eklenir. Drift guard, request bazında "beklenen davranıştan sapma" (drift) tespit eder ve mode'a göre (shadow/enforce) aksiyon alır. Kill-switch ile tamamen devre dışı bırakılabilir (0 call garantisi).

## Kullanıcı Hikayeleri

### US-1: Drift Guard Injection
Bir ops mühendisi olarak, guard decision middleware'e drift evaluation adımı eklensin istiyorum ki request bazında davranış sapması tespit edilebilsin.

#### Kabul Kriterleri
- DR1.1: Guard decision middleware pipeline'ına drift evaluation adımı eklenir
- DR1.2: Drift guard toggle'lı: `drift_guard_enabled` config flag'i ile kontrol edilir
- DR1.3: `drift_guard_enabled=false` (varsayılan) iken drift alt-sistemi hiç çağrılmaz
- DR1.4: Drift evaluation, tenant mode resolution'dan sonra ve snapshot build'den önce çalışır

### US-2: Kill-Switch Precedence (Hard Invariant)
Bir ops mühendisi olarak, drift kill-switch ON iken drift alt-sisteminin tamamen görünmez olmasını istiyorum — hiçbir side-effect üretmemeli.

#### Kabul Kriterleri
- DR2.1: Kill-switch ON iken `DriftInputProvider.get_input()` çağrılmaz (0 call)
- DR2.2: Kill-switch ON iken `evaluate_drift()` çağrılmaz (0 call)
- DR2.3: Kill-switch ON iken drift metrikleri increment edilmez (0 call)
- DR2.4: Kill-switch ON iken drift telemetry/log enrichment yapılmaz (0 call)
- DR2.5: Kill-switch check pipeline'ın ilk adımında yapılır (return early)
- DR2.6: Kill-switch ON iken `guardDecision` snapshot'ında drift alanı / drift reason code bulunmaz

### US-3: Shadow / Enforce / Disabled Modları
Bir ops mühendisi olarak, drift guard'ın mevcut guard decision mode'larıyla tutarlı çalışmasını istiyorum.

#### Kabul Kriterleri
- DR3.1: Shadow mode + drift tespit → request devam eder (`call_next` çağrılır)
- DR3.2: Shadow mode + drift tespit → `reasonCodes` listesine `DRIFT:*` prefix'li reason code eklenir
- DR3.3: Shadow mode + drift tespit → `wouldEnforce=true` set edilir
- DR3.4: Enforce mode + drift tespit → 503 block döner (`call_next` çağrılmaz)
- DR3.5: Enforce mode + drift tespit → `reasonCodes` listesine `DRIFT:*` reason code eklenir
- DR3.6: Disabled mode → drift provider çağrılmaz, drift evaluation yapılmaz
- DR3.7: Drift reason code'ları kapalı küme: `DRIFT:PROVIDER_ERROR`, `DRIFT:THRESHOLD_EXCEEDED`, `DRIFT:INPUT_ANOMALY`

### US-4: Provider Failure Semantics
Bir ops mühendisi olarak, drift provider hata verdiğinde mode'a göre tutarlı davranış istiyorum.

#### Kabul Kriterleri
- DR4.1: Shadow mode + provider exception → request devam eder + `DRIFT:PROVIDER_ERROR` reason code
- DR4.2: Shadow mode + provider exception → drift metriği increment edilir
- DR4.3: Enforce mode + provider exception → 503 block + `DRIFT:PROVIDER_ERROR` reason code
- DR4.4: Enforce mode + provider exception → `call_next` çağrılmaz
- DR4.5: Disabled mode + provider exception → provider çağrılmaz (exception oluşmaz)

### US-5: Observability
Bir ops mühendisi olarak, drift guard'ın metrik ve telemetry çıktısının sadece drift gerçekten evaluate edildiğinde üretilmesini istiyorum.

#### Kabul Kriterleri
- DR5.1: Drift metrik gating `DRIFT:` reason prefix'i ile çalışır
- DR5.2: Telemetry/log enrichment sadece drift evaluation gerçekten çalıştığında yapılır
- DR5.3: Kill-switch ON veya disabled modda drift metrikleri ve telemetry üretilmez
- DR5.4: `ptf_admin_drift_evaluation_total{mode, outcome}` counter eklenir (outcome: `no_drift|drift_detected|provider_error`)
- DR5.5: Bounded cardinality: 2 mode × 3 outcome = 6 zaman serisi

### US-6: wouldEnforce Semantiği
Bir ops mühendisi olarak, `wouldEnforce` alanının drift bağlamında doğru set edilmesini istiyorum.

#### Kabul Kriterleri
- DR6.1: Shadow + drift decision (gerçek drift veya provider error) → `wouldEnforce=true`
- DR6.2: Disabled → `wouldEnforce` drift kaynaklı set edilmez (false)
- DR6.3: Kill-switch ON → drift kaynaklı `wouldEnforce` set edilmez (false)
