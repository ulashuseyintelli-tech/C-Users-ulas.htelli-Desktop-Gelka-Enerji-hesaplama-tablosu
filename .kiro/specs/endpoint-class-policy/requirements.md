# Gereksinimler: Endpoint-Class Policy — Risk Sınıfına Göre Kademeli Enforcement

## Genel Bakış

Guard Decision Layer'ın tenant-mode çözümlemesine ek olarak, endpoint'lerin risk sınıfına (RiskClass) göre efektif enforcement modunu belirleyen bir politika katmanı eklenir. Amaç: yüksek riskli write endpoint'lere enforce açılırken, düşük riskli read endpoint'ler shadow'da kalabilir — blast radius küçülür, rollout kademeli olur.

Mevcut yapıyı bozmaz; tenant-mode çözümlemesinin çıktısını risk sınıfıyla birleştirerek "efektif mod" üretir.

## Gereksinimler

### E1 — Global OFF Dominasyonu Korunur
- E1.1: `decision_layer_enabled=false` olduğunda endpoint-class policy değerlendirilmez; katman tamamen devre dışıdır.
- E1.2: Mevcut tenant-enable P1 property'si (Global OFF Dominates) geçerliliğini korur.

### E2 — RiskClass Enum (Bounded)
- E2.1: `RiskClass` enum'u tam olarak 3 değer içerir: `HIGH`, `MEDIUM`, `LOW`.
- E2.2: Bilinmeyen/geçersiz risk class değerleri `LOW` olarak fallback edilir (fail-open).
- E2.3: RiskClass cardinality bounded — metrik label olarak güvenle kullanılabilir.

### E3 — Endpoint → RiskClass Eşlemesi (Config-Driven)
- E3.1: `decision_layer_endpoint_risk_map_json` config alanı ile endpoint pattern → risk class eşlemesi tanımlanır.
- E3.2: Eşleme formatı: JSON object, key = endpoint pattern, value = risk class string.
- E3.3: Bilinmeyen endpoint (map'te yok) → `LOW` (varsayılan risk class).
- E3.4: Geçersiz JSON → boş map + log (fail-open, mevcut parse pattern).
- E3.5: Geçersiz risk class değeri olan entry'ler atlanır + log (mevcut tenant_modes pattern).
- E3.6: Endpoint key'leri `normalize_endpoint()` çıktısı (template) üzerinden çözülür — raw path değil. Path param drift risk map'i kırmaz.
- E3.7: Precedence sırası (tek satır kural, sabit):
  1. Exact match (template == key)
  2. Longest prefix match (key, endpoint template'in prefix'i)
  3. Default LOW
- E3.8: Aynı endpoint birden fazla pattern'e match olduğunda precedence sırası deterministik sonuç üretir — ambiguity yok.

### E4 — Efektif Mod Çözümlemesi (Resolve Table)
- E4.1: `resolve_effective_mode(tenant_mode, risk_class, policy_overrides)` pure fonksiyonu, tenant_mode ve risk_class'ı birleştirerek efektif TenantMode döner.
- E4.2: Varsayılan resolve tablosu (policy_overrides boşken):
  - `tenant_mode=OFF` → her risk class için `OFF` (tenant OFF her zaman kazanır)
  - `tenant_mode=SHADOW` → her risk class için `SHADOW` (shadow tenant'ta risk class yükseltme yok)
  - `tenant_mode=ENFORCE` + `HIGH` → `ENFORCE`
  - `tenant_mode=ENFORCE` + `MEDIUM` → `ENFORCE`
  - `tenant_mode=ENFORCE` + `LOW` → `SHADOW` (düşük risk enforce'a yükseltilmez)
- E4.3: Policy override config ile resolve tablosu özelleştirilebilir (opsiyonel, MVP'de sabit tablo yeterli).
- E4.4: Resolve fonksiyonu pure — aynı input → aynı output, side-effect yok.

### E5 — Snapshot Entegrasyonu
- E5.1: `GuardDecisionSnapshot`'a `risk_class: RiskClass` alanı eklenir.
- E5.2: `SnapshotFactory.build()` endpoint'in risk class'ını resolve eder ve snapshot'a dahil eder.
- E5.3: Snapshot frozen — risk_class request boyunca değişmez.

### E6 — Middleware Gating
- E6.1: Middleware, `resolve_effective_mode()` sonucunu kullanarak enforcement kararı verir (mevcut `tenant_mode` yerine `effective_mode`).
- E6.2: OpsGuard deny bypass korunur — 429 path'te decision layer çalışmaz.
- E6.3: Efektif mod `OFF` → passthrough (snapshot build edilmez).
- E6.4: Efektif mod `SHADOW` → build + evaluate, BLOCK'ta log+metrik, no block.
- E6.5: Efektif mod `ENFORCE` → build + evaluate, BLOCK'ta 503.

### E7 — Metrik Cardinality Kontrolü ve Observability
- E7.1: Metrik label'larında `risk_class` kullanılır (bounded: 3 değer).
- E7.2: Endpoint template metrik label'ına KONMAZ (cardinality explosion riski).
- E7.3: Mevcut `tenant` label'ı (sanitize_metric_tenant) korunur.
- E7.4: `guard_decision_requests_total` metriğine `mode` ve `risk_class` label'ları eklenir.
- E7.5: `guard_decision_block_total` metriğine `risk_class` label'ı eklenir.
- E7.6: Risk map boş → `risk_class=low` durumunda decision layer çalışır ama enforce yok (SHADOW) — silent alert doğru çalışmaya devam eder.

### E8 — Mevcut Testlerin Geriye Uyumluluğu
- E8.1: Mevcut tenant-enable PBT'leri (P1–P3) ve integration testleri (I1–I4) geçerliliğini korur.
- E8.2: Mevcut concurrency PBT'leri (P-C1 – P-C5) geçerliliğini korur.
- E8.3: Proof matrix gate testi geçerliliğini korur.
