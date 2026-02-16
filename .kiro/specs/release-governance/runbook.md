# Release Governance — Operasyon Runbook

## Amaç

Bu runbook, release-governance zincirinin (ReleasePolicy → ReleaseReportGenerator → ReleaseGate → Orchestrator) operasyon ekibi tarafından kullanılmasını sağlar. Her adımda ne olur, ne yapılır, ne yapılmaz — kısa ve keskin.

## Zincir Akışı

```
[Sinyal Toplama]
    ↓
ReleasePolicy.evaluate(input)
    ↓
ReleasePolicyResult (verdict + reasons + actions)
    ↓
ReleaseReportGenerator.generate(result, input)
    ↓
ReleaseReport (audit artifact — JSON + text)
    ↓
ReleaseGate.check(result, override?, scope, now_ms)
    ↓
GateDecision (allowed / denied + audit_detail)
    ↓
if allowed → Orchestrator.execute(decision, event_id)
if denied  → NO SIDE EFFECTS, audit kaydı yeterli
```

## Verdict Tablosu

| Verdict | Anlamı | Orchestrator | Override |
|---|---|---|---|
| RELEASE_OK | Tüm sinyaller temiz | ✅ execute | N/A |
| RELEASE_HOLD | Düzeltilebilir sorun var | ❌ bekle | ✅ geçerli override ile izin |
| RELEASE_BLOCK | Kritik sorun | ❌ engelle | ❌ mutlak blok ise asla |

## Mutlak Blok Kuralları (Non-Overridable)

Bu iki neden kodu sözleşme ihlalidir. Override ile geçilemez, istisna yoktur.

| Neden Kodu | Tetikleyici | Aksiyon |
|---|---|---|
| GUARD_VIOLATION | PolicyCanary guard_violations > 0 | Guard ihlallerini düzelt, yeniden çalıştır |
| OPS_GATE_FAIL | OpsGateStatus.passed = false | Ops gate sorunlarını çöz, yeniden çalıştır |

Override girişimi yapılırsa: `CONTRACT_BREACH_NO_OVERRIDE` audit kaydı oluşur, release engellenir.

## HOLD Durumunda Override Prosedürü

1. HOLD nedenlerini raporda incele (tier fail, flaky test, drift alert, canary breaking)
2. Override oluştur:
   - `ttl_seconds`: maksimum süre (saniye)
   - `scope`: release identifier (ör. "v2.4") — release_scope ile eşleşmeli
   - `reason`: neden override yapılıyor
   - `created_by`: kim onaylıyor
3. Gate'e override ile tekrar sor
4. TTL dolmuşsa veya scope eşleşmiyorsa → override reddedilir

## Neden Kodları ve Aksiyonlar

| Neden Kodu | Verdict | Ne Yapılmalı |
|---|---|---|
| TIER_FAIL | HOLD | Başarısız tier testlerini düzelt, tier'ı yeniden çalıştır |
| FLAKY_TESTS | HOLD | Flaky testleri stabilize et |
| DRIFT_ALERT | HOLD | Drift monitor uyarılarını incele, abort/override oranlarını düşür |
| CANARY_BREAKING | HOLD | Breaking policy drift'lerini çöz |
| GUARD_VIOLATION | BLOCK | Guard ihlallerini düzelt — override yok |
| OPS_GATE_FAIL | BLOCK | Ops gate hatalarını düzelt — override yok |
| NO_TIER_DATA | BLOCK | Tier sonuçlarını sağla |
| NO_FLAKE_DATA | BLOCK | Flake snapshot'ını sağla |
| NO_DRIFT_DATA | HOLD | Drift snapshot'ını sağla |
| NO_CANARY_DATA | HOLD | Canary sonucunu sağla |

## Audit Artifact

Her `ReleaseGate.check()` çağrısı AuditLog'a bir kayıt ekler. Rapor iki formatta üretilir:

- `ReleaseReportGenerator.format_text(report)` → insan okunabilir
- `ReleaseReportGenerator.to_dict(report)` → JSON, arşivlenebilir

Rapor deterministiktir: aynı girdi → aynı çıktı (byte-level). Round-trip: `from_dict(to_dict(report))` orijinale eşdeğer.

## Entegrasyon Noktası

```python
from backend.app.testing.release_policy import ReleasePolicy, ReleasePolicyInput
from backend.app.testing.release_report import ReleaseReportGenerator
from backend.app.testing.release_gate import ReleaseGate, ReleaseOverride
from backend.app.testing.policy_engine import AuditLog

# 1. Sinyal topla → input oluştur
input = ReleasePolicyInput(tier_results=..., flake_snapshot=..., ...)

# 2. Policy kararı
policy = ReleasePolicy()
result = policy.evaluate(input)

# 3. Rapor üret (audit artifact)
gen = ReleaseReportGenerator()
report = gen.generate(result, input, generated_at="2026-02-15T15:00:00Z")
print(gen.format_text(report))

# 4. Gate kontrolü
audit = AuditLog()
gate = ReleaseGate(audit_log=audit)
decision = gate.check(result, release_scope="v2.4", now_ms=current_time_ms)

# 5. Orchestrator (sadece allowed ise)
if decision.allowed:
    orchestrator.execute(policy_decision, event_id="release-v2.4")
else:
    # Yan etki yok. Audit kaydı zaten oluştu.
    print(f"Release engellendi: {decision.audit_detail}")
```

## CI Override Prosedürü (PR-16)

PR-16 ile preflight CI'da enforcement modunda çalışır. HOLD verdict'i job'ı başarısız yapar; override ile geçilebilir.

### Ne Zaman Override Yapılabilir?

| Verdict | Neden Kodu | Override | Açıklama |
|---|---|---|---|
| RELEASE_HOLD | TIER_FAIL | ✅ Evet | Bilinen flaky test, hotfix acil |
| RELEASE_HOLD | FLAKY_TESTS | ✅ Evet | Flaky test stabilizasyonu beklenebilir |
| RELEASE_HOLD | DRIFT_ALERT | ✅ Evet | Drift oranı geçici yüksek |
| RELEASE_HOLD | CANARY_BREAKING | ✅ Evet | Breaking drift bilinen ve kabul edilmiş |
| RELEASE_HOLD | NO_DRIFT_DATA | ✅ Evet | Drift verisi geçici eksik |
| RELEASE_HOLD | NO_CANARY_DATA | ✅ Evet | Canary verisi geçici eksik |
| RELEASE_BLOCK | GUARD_VIOLATION | ❌ Hayır | Sözleşme ihlali — override imkansız |
| RELEASE_BLOCK | OPS_GATE_FAIL | ❌ Hayır | Sözleşme ihlali — override imkansız |
| RELEASE_BLOCK | NO_TIER_DATA | ❌ Hayır | Veri eksik — override ile geçilemez |
| RELEASE_BLOCK | NO_FLAKE_DATA | ❌ Hayır | Veri eksik — override ile geçilemez |

### workflow_dispatch ile Override Adımları

1. GitHub → Actions → "Release Governance" workflow'unu seç
2. "Run workflow" butonuna tıkla
3. Branch seç: `main` veya `release/*` (diğer branch'lerde override çalışmaz)
4. Input'ları doldur:
   - `override_reason`: Neden override yapılıyor (ör. "Bilinen flaky test, hotfix acil")
   - `override_scope`: Release identifier (ör. "preflight", "v2.4")
   - `override_by`: Onaylayan kişi (username/handle — email değil)
5. "Run workflow" ile çalıştır
6. Step summary'de override bilgisi görünür

### Override Audit Trail

Her override girişimi JSON artifact'ta kaydedilir:

- `override_applied: true` → Override başarılı, HOLD geçildi
- `override_applied: false` + `contract_breach: true` → BLOCK override girişimi, reddedildi
- `override_applied: false` + `contract_breach: false` → Override sağlanmadı veya kısmi flag

Artifact: `artifacts/release_preflight_<verdict>.json` — 30 gün saklanır.

### Branch Guard Kuralı

Override yalnızca belirli branch'lerde çalışır:
- `main`
- `release/*`

Diğer branch'lerde override input'ları sağlansa bile workflow başarısız olur (branch guard step).

## Sorun Giderme

| Belirti | Olası Neden | Çözüm |
|---|---|---|
| Her release BLOCK | Tier sonuçları boş veya flake snapshot None | Sinyal toplama pipeline'ını kontrol et |
| Override reddediliyor | TTL dolmuş veya scope yanlış | Override parametrelerini kontrol et |
| Override "CONTRACT_BREACH" | Mutlak blok nedeni var | Override ile geçilemez, kök nedeni düzelt |
| Rapor farklı çıkıyor | Girdi değişmiş | Aynı input ile tekrar üret, determinizm garantili |
