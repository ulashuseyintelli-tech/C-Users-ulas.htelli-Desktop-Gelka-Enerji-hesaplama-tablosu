# Uygulama Planı: Endpoint-Class Policy

## Genel Bakış

Guard Decision Layer'a endpoint risk sınıfı katmanı eklenir. Tenant mode + risk class → efektif mod. Yüksek riskli write endpoint'ler enforce'a geçerken düşük riskli read endpoint'ler shadow'da kalır. Mevcut yapı bozulmaz.

İki merge-blocker kritik yolda:
1. Risk map precedence ve normalization (Task 1–2)
2. Observability — risk_class label bounded + metrikler (Task 6)

## Görevler

- [x] 1. RiskClass enum, parse ve resolve fonksiyonları
  - [x] 1.1 `backend/app/guards/guard_decision.py` — RiskClass enum ekle
    - `RiskClass(str, Enum)`: HIGH, MEDIUM, LOW (3 değer, bounded)
    - _Requirements: E2.1, E2.3_

  - [x] 1.2 `backend/app/guards/guard_decision.py` — `parse_endpoint_risk_map()` ekle
    - JSON string → `dict[str, RiskClass]` eşlemesi
    - Geçersiz JSON → boş dict + log (fail-open, mevcut `parse_tenant_modes` pattern)
    - Geçersiz risk class değeri → entry atlanır + log
    - Boş/None → boş dict
    - _Requirements: E3.1, E3.2, E3.4, E3.5_

  - [x] 1.3 `backend/app/guards/guard_decision.py` — `resolve_endpoint_risk_class()` ekle
    - Pure fonksiyon: `(endpoint: str, risk_map: dict[str, RiskClass]) → RiskClass`
    - Precedence sırası (sabit): exact match → longest prefix → default LOW
    - Endpoint key'i `normalize_endpoint()` çıktısı (template) üzerinden çözülür
    - Aynı endpoint birden fazla pattern'e match olduğunda precedence deterministik
    - _Requirements: E3.3, E3.6, E3.7, E3.8_

  - [x] 1.4 Unit testler: RiskClass enum, parse_endpoint_risk_map, resolve_endpoint_risk_class
    - RiskClass enum 3 üye kontrolü
    - Geçerli JSON parse
    - Geçersiz JSON → boş dict
    - Geçersiz risk class değeri → entry atlanır
    - Boş string → boş dict
    - Precedence: exact match > longest prefix > default LOW
    - Aynı endpoint birden fazla pattern — precedence beklenen
    - Normalization: raw path → normalized key üzerinden match
    - _Requirements: E2.1, E3.1, E3.4, E3.5, E3.6, E3.7, E3.8_

  - [ ]* 1.5 Property test: RiskClass cardinality bounded (EP-5) {SOFT:NICE}
    - **Property EP-5: RiskClass Cardinality Bounded**
    - **Validates: Requirements E2.1, E2.3, E3.5, E7.1**

  - [ ]* 1.6 Property test: Bilinmeyen endpoint → LOW fallback (EP-6) {SOFT:NICE}
    - **Property EP-6: Bilinmeyen Endpoint → LOW Fallback**
    - **Validates: Requirements E2.2, E3.3**

  - [ ]* 1.7 Property test: Precedence determinizmi (EP-8) {SOFT:SAFETY}
    - **Property EP-8: Precedence Determinizmi**
    - **Validates: Requirements E3.7, E3.8**

  - [ ]* 1.8 Property test: Exact match precedence (EP-9) {SOFT:NICE}
    - **Property EP-9: Exact Match Precedence**
    - **Validates: Requirements E3.7**

- [x] 2. resolve_effective_mode fonksiyonu
  - [x] 2.1 `backend/app/guards/guard_decision.py` — `resolve_effective_mode()` ekle
    - Pure fonksiyon: `(tenant_mode: TenantMode, risk_class: RiskClass) → TenantMode`
    - Resolve tablosu: OFF→OFF, SHADOW→SHADOW, ENFORCE+HIGH→ENFORCE, ENFORCE+MEDIUM→ENFORCE, ENFORCE+LOW→SHADOW
    - _Requirements: E4.1, E4.2, E4.4_

  - [x] 2.2 Unit testler: resolve_effective_mode tam tablo doğrulama
    - 9 kombinasyon (3 tenant_mode × 3 risk_class) tam doğrulama
    - OFF dominasyonu: her risk class için OFF
    - SHADOW korunması: her risk class için SHADOW
    - ENFORCE + LOW → SHADOW
    - _Requirements: E4.1, E4.2_

  - [x]* 2.3 Property test: Resolve tablosu determinizmi (EP-1)
    - **Property EP-1: Resolve Tablosu Determinizmi**
    - **Validates: Requirements E4.1, E4.4**

  - [x]* 2.4 Property test: OFF dominasyonu korunur (EP-2)
    - **Property EP-2: OFF Dominasyonu Korunur**
    - **Validates: Requirements E1.1, E4.2**

  - [x]* 2.5 Property test: SHADOW yükseltme yok (EP-3)
    - **Property EP-3: SHADOW Yükseltme Yok**
    - **Validates: Requirements E4.2**

  - [x]* 2.6 Property test: ENFORCE + LOW → SHADOW (EP-4)
    - **Property EP-4: ENFORCE + LOW → SHADOW**
    - **Validates: Requirements E4.2**

- [x] 3. GuardConfig ve Snapshot güncellemesi
  - [x] 3.1 `backend/app/guard_config.py` — `decision_layer_endpoint_risk_map_json` config alanı ekle
    - Tip: `str`, varsayılan: `""` (boş → tüm endpoint'ler LOW)
    - Env var: `OPS_GUARD_DECISION_LAYER_ENDPOINT_RISK_MAP_JSON`
    - _Requirements: E3.1_

  - [x] 3.2 `backend/app/guards/guard_decision.py` — GuardDecisionSnapshot'a `risk_class` ve `effective_mode` alanları ekle
    - `risk_class: RiskClass = RiskClass.LOW`
    - `effective_mode: TenantMode = TenantMode.SHADOW`
    - Snapshot frozen — request boyunca değişmez
    - _Requirements: E5.1, E5.3_

  - [x] 3.3 `backend/app/guards/guard_decision.py` — SnapshotFactory.build() güncellemesi
    - `risk_class` parametresi ekle (varsayılan: `RiskClass.LOW`)
    - `resolve_effective_mode(tenant_mode, risk_class)` çağır
    - Snapshot'a `risk_class` ve `effective_mode` dahil et
    - _Requirements: E5.2_

  - [x] 3.4 Unit testler: Snapshot risk_class ve effective_mode alanları
    - SnapshotFactory.build() risk_class parametresi ile çağrılabilir
    - Snapshot'ta risk_class ve effective_mode doğru set ediliyor
    - Varsayılan risk_class = LOW
    - _Requirements: E5.1, E5.2, E5.3_

- [x] 4. Checkpoint — Mevcut testlerin geçtiğinden emin ol
  - [x] CPK-1: Snapshot correctness (3 senaryo: empty+ENFORCE, exact HIGH+ENFORCE, prefix MEDIUM+SHADOW)
  - [x] CPK-2: Hash determinism / sensitivity (risk_class değişince hash değişir, aynı input → aynı hash)
  - [x] CPK-3: Backward compatibility (parse fail fallback, geçersiz JSON crash etmez, ENFORCE+LOW→SHADOW)
  - [x] CPK-4: Existing invariants (OpsGuard deny bypass, snapshot immutability, fail-open, endpoint normalize)
  - [x] Mevcut tenant-enable PBT'leri (P1–P3) geçer
  - [x] Mevcut concurrency PBT'leri (P-C1–P-C5) geçer
  - [x] Mevcut proof matrix gate testi geçer
  - [x] Mevcut wiring integration testleri (W1–W8, I1–I4) geçer
  - _Requirements: E8.1, E8.2, E8.3_

- [x] 5. Middleware entegrasyonu
  - [x] 5.1 `backend/app/guards/guard_decision_middleware.py` — efektif mod kullanımı
    - `parse_endpoint_risk_map()` ile risk map parse et
    - `resolve_endpoint_risk_class(endpoint, risk_map)` ile normalized template üzerinden risk class çözümle
    - `SnapshotFactory.build()` çağrısına `risk_class` parametresi ekle
    - `snapshot.effective_mode` kullanarak enforcement kararı ver (mevcut `tenant_mode` yerine)
    - Efektif mod OFF → passthrough (snapshot build sonrası kontrol)
    - OpsGuard deny bypass korunur
    - _Requirements: E6.1, E6.2, E6.3, E6.4, E6.5_

  - [x] 5.2 ASGI testler: Middleware efektif mod gating (9 test)
    - E1: Empty risk map + ENFORCE → effective SHADOW → NO BLOCK (2 test)
    - E2: Risk HIGH + ENFORCE → effective ENFORCE → BLOCK 503 (2 test)
    - E3: Risk MEDIUM + ENFORCE → ENFORCE → BLOCK 503 (1 test)
    - E4: Tenant SHADOW + risk HIGH → SHADOW → NO BLOCK (1 test)
    - E5: Tenant OFF → NOOP regardless of risk (2 test)
    - E6: OpsGuard deny bypass unchanged — rate limit 429 (1 test)
    - _Requirements: E6.1, E6.3, E6.4, E6.5_

  - [ ]* 5.3 Property test: Mevcut P1 korunur — Global OFF (EP-7) {SOFT:SAFETY}
    - **Property EP-7: Mevcut P1 Korunur (Global OFF)**
    - **Validates: Requirements E1.2, E8.1**

- [x] 6. Metrik güncellemesi (Merge-blocker 2)
  - [x] 6.1 `backend/app/ptf_metrics.py` — Yol A: yeni metrik isimleri (eski kırılmaz)
    - `ptf_admin_guard_decision_requests_by_risk_total{mode, risk_class}` — 3×2=6 seri
    - `ptf_admin_guard_decision_block_by_risk_total{kind, mode, risk_class}` — 2×2×3=12 seri
    - Eski metrikler (`_requests_total`, `_block_total`) aynen korunur
    - _Requirements: E7.1, E7.4_

  - [x] 6.2 `backend/app/ptf_metrics.py` — increment metodları
    - `inc_guard_decision_request_by_risk(mode, risk_class)` — bounded validation
    - `inc_guard_decision_block_by_risk(kind, mode, risk_class)` — bounded validation
    - Geçersiz label → log + no-op (cardinality koruması)
    - _Requirements: E7.1, E7.5_

  - [x] 6.3 `backend/app/guards/guard_decision_middleware.py` — metrik çağrılarını risk_class ile güncelle
    - `inc_guard_decision_request_by_risk(mode, risk_class)` çağrısı eklendi
    - `_emit_block_metric` hem legacy hem yeni metriği emit ediyor
    - Endpoint label KONMAZ
    - _Requirements: E7.1, E7.2, E7.3_

  - [x] 6.4 Unit testler: 18 metrik testi (M1–M8)
    - M1: Empty risk map → LOW label (2 test)
    - M2: HIGH endpoint → risk_class="high" (2 test)
    - M3: MEDIUM prefix → risk_class="medium" (2 test)
    - M4: Block counters split by risk_class (2 test)
    - M5: Cardinality invariant — bounded labels, no endpoint/tenant (4 test)
    - M6: Backward compat — legacy metrics still increment (2 test)
    - M7: Invalid label values rejected (2 test)
    - M8: Request counter accumulation + independence (2 test)
    - _Requirements: E7.1, E7.4, E7.5, E7.6_

  - [x] 6.5 Dashboard + Runbook güncellemesi
    - Dashboard: 2 yeni panel (Request Rate by Risk Class, Block Rate by Risk Class)
    - Runbook: metrik tablosu + PromQL risk class breakdown bölümü
    - _Requirements: E7.1_

- [x] 7. Final checkpoint — Tüm testlerin geçtiğinden emin ol
  - [x] Yeni unit testler geçer (230 test yeşil)
  - [x] Mevcut tenant-enable PBT'leri (P1–P3) geçer
  - [x] Mevcut concurrency PBT'leri (P-C1–P-C5) geçer
  - [x] Mevcut integration testleri (I1–I4) geçer
  - _Requirements: E8.1, E8.2, E8.3_

## Notlar

- `*` ile işaretli görevler opsiyoneldir ve hızlı MVP için atlanabilir
- Her görev belirli gereksinimleri referans alır (izlenebilirlik)
- Checkpoint'ler artımlı doğrulama sağlar — mevcut testler kırılmamalı
- resolve_effective_mode ve resolve_endpoint_risk_class pure fonksiyonlar — test edilmesi kolay, side-effect yok
- Risk map boşken tüm endpoint'ler LOW → ENFORCE tenant'ta SHADOW olur (güvenli varsayılan)
- Merge-blocker 1 (precedence + normalization) Task 1.3 + 1.4'te kapanır
- Merge-blocker 2 (observability + metrics) Task 6'da kapanır
- Middleware'e dokunma (Task 5) ancak her iki blocker'ın prerequisite'leri tamamlandıktan sonra
