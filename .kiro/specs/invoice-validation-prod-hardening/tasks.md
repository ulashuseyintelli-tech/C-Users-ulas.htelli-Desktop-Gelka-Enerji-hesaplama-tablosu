# Görevler — Fatura Doğrulama Production Hardening (Faz H0)

## H1: Rollout Konfigürasyon Modülü
- [x] `backend/app/invoice/validation/rollout_config.py` oluştur
- [x] Yeni env var'ları parse et: `LATENCY_GATE_DELTA_MS`, `MISMATCH_GATE_COUNT`, `GATE_N_MIN`, `ROLLOUT_STAGE`
- [x] Fail-closed davranış: geçersiz değerlerde varsayılana dön + WARNING log
- [x] `load_rollout_config()` fonksiyonu: frozen dataclass döner
- [x] Gereksinimler: G9 (Yapılandırma Dışsallaştırması)

## H2: Baraj Değerlendirme Modülü
- [x] `backend/app/invoice/validation/gate_evaluator.py` oluştur
- [x] `evaluate_latency_gate(baseline_p95, baseline_p99, current_p95, current_p99, delta_ms)` → pass/fail + log
- [x] `evaluate_mismatch_gate(actionable_count, threshold)` → pass/fail + log
- [x] `evaluate_safety_gate(retry_loop_count)` → pass/fail + log
- [x] `check_n_min(observed_count, n_min)` → ready/deferred
- [x] Baseline tanımı: 7 günlük P95/P99 median (tek snapshot değil)
- [x] Tüm fonksiyonlar fail-closed: exception atmaz, log + skip
- [x] **State kararı:** Gate'ler stateless pure function olarak implemente edilir. Girdi olarak hesaplanmış değerler alır (baseline_p95, current_p95, observed_count vb.). State yönetimi (rolling window, median hesabı, sayaç toplama) çağıran tarafın sorumluluğundadır — gate modülü sadece karar verir. Production'da bu değerler Prometheus query veya in-memory accumulator'dan sağlanır; gate modülü kaynağı bilmez.
- [x] Gereksinimler: G2 (Gecikme Barajı), G3 (Uyumsuzluk Barajı), G4 (Güvenlik Barajı)

## H3: Aşama Raporu Üretici
- [x] `backend/app/invoice/validation/stage_report.py` oluştur
- [x] `generate_stage_report(stage, metrics_snapshot)` → JSON structured log
- [x] Rapor içeriği: phase bazlı P95/P99, actionable mismatch count, block count, baraj durumu (pass/fail), toplam fatura sayısı, gözlem süresi (gün)
- [x] PII içermez; fatura ID'leri anonimize
- [x] Gereksinimler: G6 (Telemetri Raporu), G7 (Kalibre Edilmiş Bütçeler)

## H4: Testler (Unit + PBT)
- [x] `backend/tests/test_invoice_prod_hardening_h0.py` oluştur
- [x] Unit: rollout config parse (geçerli/geçersiz env var'lar, fail-closed)
- [x] Unit: latency gate (pass/fail/baseline+Δ)
- [x] Unit: mismatch gate (count=0 pass, count>0 fail, whitelisted hariç)
- [x] Unit: safety gate (retry_loop=0 pass, >0 fail)
- [x] Unit: N_min check (< N_min → deferred, ≥ N_min → ready)
- [x] Unit: stage report JSON yapısı ve içerik doğrulaması
- [x] Unit: unexpected block tanımı (known rule → expected, unknown → unexpected)
- [x] PBT: config parse round-trip (rastgele string → never raise, always valid default)
- [x] PBT: gate evaluator monotonicity (delta artarsa pass olasılığı azalmaz)
- [x] PBT: N_min guard (rastgele count < N_min → always deferred)
- [x] PBT: report structure invariant (her rapor zorunlu alanları içerir)
- [x] Gereksinimler: Tüm gereksinimler (G1–G9)

## H5: Mevcut Modüllere Entegrasyon
- [x] **H5a:** `rollout_config` → `telemetry_config.py`'ye import ve wiring (rollout stage resolver)
- [x] **H5b:** `enforcement.py`'de mode geçişinde `gate_evaluator` çağrısı (log-only, karar vermez — ops bilgilendirme)
- [x] **H5c:** `__init__.py`'ye yeni exports eklenmesi (rollout_config, gate_evaluator, stage_report)
- [x] Scope notu: H5 yalnızca wiring yapar; regression H7'de doğrulanır
- [x] Gereksinimler: G5 (Rollback), G9 (Yapılandırma)

## H6: Incident Playbook [Dokümantasyon]
- [x] `docs/runbooks/invoice-validation-rollout-playbook.md` oluştur
- [x] Mode flip prosedürü (kim, nasıl, hangi araçla — §4'teki deploy yöntemi tablosuna referans)
- [x] Rollback adımları (env var değişikliği + doğrulama)
- [x] İzlenecek metrikler ve eşik değerleri
- [x] Eskalasyon prosedürü
- [x] Incident template (§2'deki template'den)
- [x] Unexpected block triage prosedürü
- [x] PII yok; log örnekleri anonimize
- [x] **Not:** Bu task kod değil, dokümantasyon deliverable'dır. Regression checkpoint (H7) kapsamı dışındadır.
- [x] Gereksinimler: G5 (Rollback), G8 (Incident Playbook)

## H7: Regression Checkpoint [Yalnızca Kod + Test]
- [x] Mevcut 89 test (Faz A–G) + yeni H0 testleri birlikte çalıştır
- [x] 0 failure
- [x] Komut: `python -m pytest tests/test_invoice_*.py tests/test_invoice_telemetry_g.py tests/test_invoice_prod_hardening_h0.py --tb=short -q`
- [x] **Kapsam:** Yalnızca kod ve test doğrulaması; H6 (playbook) bu checkpoint'a dahil değildir
- [x] **Sonuç:** 116 passed, 0 failures (68 mevcut + 48 yeni H0)
- [x] Gereksinimler: Tüm gereksinimler
