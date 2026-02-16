# Uygulama Planı: Internal Adoption — PR-15

## Genel Bakış

Release Governance Framework'ü (v1.0.0) başka repo/ekiplerin kopyala-yapıştır tüketebileceği hale getirir. Yeni karar mantığı yok; çıktılar CI snippet, preflight CLI, config referansı, triage kılavuzu ve smoke test.

## Görevler

- [x] 1. Preflight CLI komutu
  - [x] 1.1 `backend/app/testing/release_preflight.py` oluştur:
    - `run_preflight(json_mode, output_dir)` fonksiyonu: ReleasePolicy → ReleaseReportGenerator → ReleaseGate zincirini çalıştırır
    - Dry-run modu: sinyal verisi olmadan → boş input → BLOCK (beklenen davranış)
    - Stdout çıktısı: verdict, spec_hash, reasons, report path
    - `--json` flag: JSON formatında çıktı
    - `--output-dir DIR` flag: rapor dosyaları (text + JSON) yazma
    - Exit codes: 0=OK, 1=HOLD, 2=BLOCK, 64=usage error
    - `if __name__ == "__main__"` block ile `python -m backend.app.testing.release_preflight` çalıştırılabilir
    - _Requirements: 2.1-2.7_

- [x] 2. CI pipeline snippet (GitHub Actions)
  - [x] 2.1 `docs/ci/release-governance.yml` oluştur:
    - GitHub Actions workflow: checkout, Python setup, deps install, unit test, PBT test, preflight, artifact upload
    - Tetikleyici: push + pull_request (paths filter: `backend/app/testing/**`)
    - Python 3.11/3.12/3.13 matrix
    - Preflight adımı: `python -m backend.app.testing.release_preflight --json --output-dir artifacts/`
    - Artifact upload: `actions/upload-artifact@v4` ile rapor dosyaları
    - _Requirements: 1.1-1.5_

- [x] 3. README güncellemeleri
  - [x] 3.1 Quickstart: CI Entegrasyonu bölümü (preflight komutu, CI snippet referansı, artifact yolu)
  - [x] 3.2 Gereksinimler ve Bağımlılıklar bölümü (Python 3.11+, pytest, hypothesis, harici servis yok)
  - [x] 3.3 CI'da HOLD/BLOCK Triage bölümü (verdict → anlam → ilk adım tablosu, runbook referansı)
  - _Requirements: 3.1-3.3, 4.1-4.3, 5.1-5.3_

- [x] 4. Preflight smoke testi
  - [x] 4.1 `backend/tests/test_release_preflight.py` oluştur (10 unit test):
    - Dry-run → BLOCK verdict + exit code 2
    - JSON output valid + beklenen alanlar
    - spec_hash 64-char hex (JSON + text)
    - Artifact dosyaları oluşur + verdict dosya adında
    - Exit code contract sabitleri
    - _Requirements: 6.1-6.4_

- [x] 5. Final checkpoint
  - 120 test (110 mevcut + 10 yeni preflight), 0 flaky
  - CI snippet geçerli YAML
  - README 3 yeni bölüm eklenmiş
  - Exit code sözleşmesi: 0=OK, 1=HOLD, 2=BLOCK, 64=usage

## Notlar

- Yeni karar mantığı yok; preflight mevcut zincirin thin wrapper'ı
- Gerçek sinyal toplama (tier runner entegrasyonu) bu PR'ın kapsamı dışında
- CI snippet GitHub Actions hedefli; GitLab/Jenkins talep üzerine eklenebilir
- Preflight dry-run modu "hata" değil, beklenen davranış (veri yoksa BLOCK doğru cevap)
- Stdout: machine-friendly, Stderr: human-readable diagnostics
- Artifact dosya adı: `release_preflight_<verdict>.json` + `.txt`
