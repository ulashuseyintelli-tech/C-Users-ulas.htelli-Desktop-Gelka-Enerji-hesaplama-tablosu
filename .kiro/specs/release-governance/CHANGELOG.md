# Changelog — Release Governance Framework

Tüm önemli değişiklikler bu dosyada belgelenir.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) uyumlu.
Versiyonlama: [Semantic Versioning](https://semver.org/spec/v2.0.0.html) uyumlu.

## [1.0.0] — 2026-02-15

### Added

- ReleasePolicy: deterministik karar motoru (OK / HOLD / BLOCK)
  - 10 BlockReasonCode, monotonik verdict birleştirme
  - Girdi doğrulama (eksik veri → BLOCK veya HOLD)
  - RequiredAction üretimi (her HOLD/BLOCK için en az bir aksiyon)
- ReleaseReportGenerator: kanonik audit artifact üretimi
  - Text + JSON formatları, byte-level determinizm
  - JSON round-trip: `from_dict(to_dict(report)) == report`
  - Tier özeti, drift özeti, override özeti, guard özeti
- ReleaseGate: enforcement hook (allow/deny + override doğrulama)
  - TTL + scope tabanlı override mekanizması
  - Her `check()` çağrısı audit kaydı üretir
- `spec_hash()`: SHA-256 traceability (policy + report + gate dosyaları)
- `generate_reason_code_table()`: enum reflection ile otomatik markdown tablosu
- End-to-end release pipeline testleri (golden artifact determinizmi)
- Tiered test disiplini (SMOKE < CORE < CONCURRENCY < SOAK)

### Security

- GUARD_VIOLATION ve OPS_GATE_FAIL mutlak blok — override ile geçilemez
- Override girişimi → CONTRACT_BREACH_NO_OVERRIDE hard reject
- PBT ile kanıtlı: rastgele girdi kombinasyonlarında mutlak blok asla aşılamaz

### Stability

- 108 test (91 unit + 17 smoke), 16 PBT (v1.0.0 release-time snapshot)
- 0 flaky test
- Tier bütçeleri enforce edilir
- End-to-end determinizm kilitli
- Golden artifact snapshot'ları byte-level deterministik
