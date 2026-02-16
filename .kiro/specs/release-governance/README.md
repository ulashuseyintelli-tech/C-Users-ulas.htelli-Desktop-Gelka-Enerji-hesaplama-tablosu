# Release Governance — Index

**Version:** 1.0.0
**Tarih:** 2026-02-15
**Spec Hash:** `spec_hash()` ile runtime'da üretilir (`from backend.app.testing.release_version import spec_hash, VERSION`)

## Sistem Özeti

Release Governance, PR-10'da kurulan test disiplini çıktılarını (tier sonuçları, flake sentinel, drift monitor, policy canary, ops gate) deterministik bir release kararına (OK / HOLD / BLOCK) bağlayan üç bileşenden oluşur. Tüm bileşenler saf-matematik, IO'suz ve deterministiktir. GUARD_VIOLATION ve OPS_GATE_FAIL mutlak bloktur — override ile geçilemez, sözleşme ihlali sayılır. Karar zinciri: ReleasePolicy → ReleaseReportGenerator → ReleaseGate → Orchestrator.

## Dosya Haritası

| Modül | Dosya Yolu | Açıklama |
|---|---|---|
| ReleasePolicy | `backend/app/testing/release_policy.py` | Deterministik karar fonksiyonu (OK/HOLD/BLOCK) |
| ReleaseReportGenerator | `backend/app/testing/release_report.py` | Audit artifact üretimi (text + JSON, round-trip) |
| ReleaseGate | `backend/app/testing/release_gate.py` | Enforcement hook (allow/deny + override doğrulama) |
| Release Version | `backend/app/testing/release_version.py` | Spec hash + reason code tablosu üretimi |

**Not:** `spec_hash()` dosyaları raw bytes olarak okur, LF/CRLF normalizasyonu yapmaz. Cross-OS ortamda (Linux ↔ Windows) newline farkları hash'i etkiler. Aynı platform üzerinde deterministiktir; farklı platformlar arası karşılaştırma yapılacaksa `.gitattributes` ile newline politikası sabitlenmelidir.

## Test Haritası

| Test Dosyası | Kapsam |
|---|---|
| `backend/tests/test_release_policy.py` | ReleasePolicy unit + PBT (P1-P5): bireysel sinyal kontrolleri, monotoniklik, determinizm |
| `backend/tests/test_release_report.py` | ReleaseReportGenerator unit + PBT (P6-P8): format doğruluğu, round-trip, determinizm |
| `backend/tests/test_release_gate.py` | ReleaseGate unit + PBT (P9-P11): verdict uyumu, override doğrulama, audit kaydı |
| `backend/tests/test_release_e2e.py` | E2E pipeline simulation + PBT (P12-P14): zincir determinizmi, yan etki izolasyonu, golden artifact |
| `backend/tests/test_release_pack.py` | Paket bütünlüğü smoke + PBT (P15-P16): import, hash determinizmi, reason code table drift guard |

Toplam test sayısını doğrulamak için:

```bash
pytest backend/tests/test_release_policy.py backend/tests/test_release_report.py backend/tests/test_release_gate.py backend/tests/test_release_e2e.py backend/tests/test_release_pack.py --collect-only -q
```

Tüm testleri çalıştırmak için:

```bash
pytest backend/tests/test_release_policy.py backend/tests/test_release_report.py backend/tests/test_release_gate.py backend/tests/test_release_e2e.py backend/tests/test_release_pack.py -q
```

## CI Komutları

### Tüm release testleri (unit + PBT)

```bash
pytest backend/tests/test_release_policy.py backend/tests/test_release_report.py backend/tests/test_release_gate.py backend/tests/test_release_e2e.py backend/tests/test_release_pack.py -v
```

### Sadece unit testler (PBT hariç)

```bash
pytest backend/tests/test_release_policy.py backend/tests/test_release_report.py backend/tests/test_release_gate.py backend/tests/test_release_e2e.py backend/tests/test_release_pack.py -v -k "not PBT"
```

### Sadece PBT testler

```bash
pytest backend/tests/test_release_policy.py backend/tests/test_release_report.py backend/tests/test_release_gate.py backend/tests/test_release_e2e.py backend/tests/test_release_pack.py -v -k "PBT"
```

### Tek modül

```bash
# Policy
pytest backend/tests/test_release_policy.py -v

# Report
pytest backend/tests/test_release_report.py -v

# Gate
pytest backend/tests/test_release_gate.py -v

# E2E
pytest backend/tests/test_release_e2e.py -v

# Pack (smoke)
pytest backend/tests/test_release_pack.py -v
```

### PBT seed ile (reproduceability)

```bash
pytest backend/tests/test_release_policy.py -v --hypothesis-seed=12345
```

`--hypothesis-seed` belirli bir seed ile PBT'leri tekrar çalıştırır. Flaky bir PBT bulunursa, Hypothesis çıktısındaki seed değerini bu flag ile kullanarak hatayı reproduce edebilirsiniz.

## Neden Kodu → Aksiyon Referans Tablosu

Bu tablo `release_policy.py` içindeki `_ACTION_DESCRIPTIONS` dict'inden ve `ABSOLUTE_BLOCK_REASONS` frozenset'inden otomatik üretilmiştir. Elle düzenlemeyin; kaynak değişirse `generate_reason_code_table()` ile yeniden üretin.

<!-- REASON_CODE_TABLE:BEGIN -->
| Neden Kodu | Verdict | Aksiyon | Override? |
|---|---|---|---|
| TIER_FAIL | HOLD | Fix failing tier tests and re-run the tier | ✅ Evet |
| FLAKY_TESTS | HOLD | Investigate and stabilise flaky tests | ✅ Evet |
| DRIFT_ALERT | HOLD | Review drift monitor alerts and reduce abort/override rates | ✅ Evet |
| CANARY_BREAKING | HOLD | Resolve breaking policy drifts before release | ✅ Evet |
| GUARD_VIOLATION | BLOCK | Fix guard violations — contract breach, no override allowed | ❌ Hayır (sözleşme ihlali) |
| OPS_GATE_FAIL | BLOCK | Fix ops gate failures — contract breach, no override allowed | ❌ Hayır (sözleşme ihlali) |
| NO_TIER_DATA | BLOCK | Provide tier run results before release evaluation | N/A (veri eksik) |
| NO_FLAKE_DATA | BLOCK | Provide flake sentinel snapshot before release evaluation | N/A (veri eksik) |
| NO_DRIFT_DATA | HOLD | Provide drift monitor snapshot before release evaluation | ✅ Evet |
| NO_CANARY_DATA | HOLD | Provide policy canary result before release evaluation | ✅ Evet |
<!-- REASON_CODE_TABLE:END -->

## Enforcement Mode (PR-16)

PR-16 ile preflight kontrolü raporlama modundan zorunlu moda geçer. Exit code doğrudan CI job sonucunu belirler — `continue-on-error` kaldırılmıştır.

### Exit Code → Job Sonucu

| Exit Code | Verdict | Job Sonucu | Override |
|---|---|---|---|
| 0 | RELEASE_OK | ✅ Başarılı | Gereksiz |
| 0 | RELEASE_HOLD + override | ✅ Başarılı | Uygulandı |
| 1 | RELEASE_HOLD | ❌ Başarısız | Sağlanmadı |
| 2 | RELEASE_BLOCK | ❌ Başarısız | İmkansız (ABSOLUTE) |

### Override Flag'leri

HOLD verdict'ini CI'da override etmek için üç flag da zorunludur:

```bash
python -m backend.app.testing.release_preflight \
  --json \
  --output-dir artifacts/ \
  --override-reason "Bilinen flaky test, hotfix gerekli" \
  --override-scope "preflight" \
  --override-by "dev-lead"
```

Override kuralları:
- Üç flag da sağlanmalı (`--override-reason`, `--override-scope`, `--override-by`)
- Kısmi flag → override yok sayılır, normal akış devam eder
- HOLD + geçerli override → exit 0 (job başarılı)
- BLOCK + override → exit 2 (CONTRACT_BREACH, job başarısız)
- OK + override → override yok sayılır (gereksiz)
- `--override-by` username/handle kabul eder (PII yok, email değil)

### Override Sözleşmesi

| Verdict | Override Sonucu |
|---|---|
| RELEASE_OK | Override yok sayılır |
| RELEASE_HOLD | Override uygulanır → exit 0 |
| RELEASE_BLOCK (ABSOLUTE) | Override reddedilir → CONTRACT_BREACH → exit 2 |

### CI'da Override (workflow_dispatch)

GitHub Actions'ta HOLD override için `workflow_dispatch` kullanılır:

1. Actions → Release Governance → Run workflow
2. Override input'larını doldur: `override_reason`, `override_scope`, `override_by`
3. Branch seç (yalnızca `main` veya `release/*` üzerinde çalışır)
4. Run workflow

Workflow dosyası: [`docs/ci/release-governance.yml`](../../../../docs/ci/release-governance.yml)

### JSON Çıktı (Override Alanları)

Override kullanıldığında JSON artifact'ta ek alanlar:

| Alan | Tip | Açıklama |
|---|---|---|
| `override_applied` | bool | Override uygulandı mı |
| `override_by` | string | Override onaylayan |
| `override_reason` | string | Override nedeni |
| `contract_breach` | bool | Sözleşme ihlali var mı |
| `contract_breach_detail` | string | İhlal detayı (varsa) |

## Referanslar

- [Operasyon Runbook](runbook.md) — zincir akışı, override prosedürü, sorun giderme
- [PR-11 Spec: Release Governance](requirements.md) — gereksinimler ve kabul kriterleri
- [PR-11 Design](design.md) — mimari, correctness properties (P1-P11)
- [PR-12 Spec: E2E Pipeline](../release-e2e-pipeline/requirements.md) — e2e test gereksinimleri
- [PR-12 Design](../release-e2e-pipeline/design.md) — e2e correctness properties (P12-P14)
- [PR-13 Spec: Governance Pack](../release-governance-pack/requirements.md) — packaging gereksinimleri
- [CHANGELOG](CHANGELOG.md) — sürüm geçmişi

## Semantic Versioning Sözleşmesi

Bu framework [Semantic Versioning 2.0.0](https://semver.org/) kurallarına uyar.

**MAJOR** (kırılma — tüm tüketiciler etkilenir):
- `BlockReasonCode` enum'undan üye çıkarılması veya yeniden adlandırılması
- `ReleaseVerdict` sıralamasının değişmesi (OK < HOLD < BLOCK)
- `ABSOLUTE_BLOCK_REASONS` setinin daraltılması
- `spec_hash()` kapsamının değişmesi (dosya ekleme/çıkarma)
- `GateDecision` veya `ReleasePolicyResult` alanlarının kaldırılması

**MINOR** (geriye dönük uyumlu genişleme):
- Yeni `BlockReasonCode` üyesi eklenmesi
- Yeni `RequiredAction` eklenmesi
- `ReleaseReport`'a yeni alan eklenmesi (mevcut alanlar değişmez)
- `ABSOLUTE_BLOCK_REASONS` setinin genişletilmesi

**PATCH** (iç iyileştirme):
- Internal refactor (public API değişmez)
- Test iyileştirme / yeni test ekleme
- README / runbook / CHANGELOG düzeltmesi
- Performans optimizasyonu

## Definition of Stable (v1.0.0 Referans Noktası)

Bu sürüm aşağıdaki stabilite garantilerini sağlar:

- Tüm release governance testleri geçer (doğrulama: `pytest backend/tests/test_release_*.py -q`)
- 0 flaky test
- Tier bütçeleri enforce edilir
- End-to-end determinizm kilitli (golden artifact byte-level eşitlik)
- Mutlak blok (GUARD_VIOLATION, OPS_GATE_FAIL) override edilemez — PBT ile kanıtlı
- Rapor round-trip garantili: `from_dict(to_dict(report)) == report`
- `spec_hash()` deterministik: aynı dosya içeriği → aynı hash
- Reason code tablosu drift guard ile kilitli: enum değişirse test kırılır

Bu blok, gelecekte regression olursa referans noktası olarak kullanılır. Herhangi bir test kırılması, bu garantilerden birinin ihlal edildiği anlamına gelir.

## Quickstart: CI Entegrasyonu

Preflight kontrolü (dry-run, sinyal verisi olmadan):

```bash
python -m backend.app.testing.release_preflight --json --output-dir artifacts/
```

Exit codes: `0` = OK, `1` = HOLD, `2` = BLOCK, `64` = usage error.

GitHub Actions workflow: [`docs/ci/release-governance.yml`](../../../../docs/ci/release-governance.yml) dosyasını `.github/workflows/` altına kopyala.

Artifact çıktısı: `artifacts/release_preflight_<verdict>.json` + `.txt`

## Gereksinimler ve Bağımlılıklar

| Gereksinim | Versiyon |
|---|---|
| Python | 3.11+ |
| pytest | 8.0+ |
| hypothesis | 6.0+ |

Harici servis bağımlılığı yok. Framework tamamen pure Python ve IO-free'dir. Çalışması için yalnızca `backend/app/testing/` ve `backend/tests/` dizin yapısının mevcut olması yeterlidir.

## CI'da HOLD/BLOCK Triage

| Verdict | Anlam | İlk Adım |
|---|---|---|
| `RELEASE_OK` (exit 0) | Tüm sinyaller temiz | Devam et |
| `RELEASE_HOLD` (exit 1) | Düzeltilebilir sorun | Preflight JSON'daki `reasons` alanını oku, ilgili testi düzelt |
| `RELEASE_BLOCK` (exit 2) | Kritik sorun veya eksik veri | `reasons` alanını oku; mutlak blok ise override yok |

Detaylı prosedür: [Operasyon Runbook](runbook.md) — override prosedürü, sorun giderme tablosu.
