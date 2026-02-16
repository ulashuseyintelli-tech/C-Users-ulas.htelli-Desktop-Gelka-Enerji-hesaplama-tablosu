# Tasarım Dokümanı — Internal Adoption (PR-15)

## Genel Bakış

Release Governance Framework'ü (v1.0.0) başka repo/ekiplerin kopyala-yapıştır tüketebileceği hale getirir. Yeni karar mantığı yok; çıktılar CI snippet, preflight CLI, config referansı ve triage kılavuzu.

## Bileşenler

### 1. CI Pipeline Snippet (`docs/ci/release-governance.yml`)

GitHub Actions workflow dosyası. Doğrudan `.github/workflows/` altına kopyalanabilir.

```yaml
# Yapı:
# Job 1: test (smoke + core + PBT)
# Job 2: preflight (release gate check + artifact upload)
#
# Tetikleyici: push/PR (paths: backend/app/testing/**)
# Python: 3.11+
# Bağımlılıklar: pytest, hypothesis
```

Workflow adımları:

| Adım | Komut | Amaç |
|---|---|---|
| Checkout | `actions/checkout@v4` | Repo klonla |
| Python setup | `actions/setup-python@v5` | Python 3.11+ |
| Install deps | `pip install pytest hypothesis` | Test bağımlılıkları |
| Unit tests | `pytest backend/tests/test_release_*.py -v -k "not PBT"` | Smoke + core |
| PBT tests | `pytest backend/tests/test_release_*.py -v -k "PBT"` | Property-based |
| Preflight | `python -m backend.app.testing.release_preflight --json --output-dir artifacts/` | Gate check |
| Upload | `actions/upload-artifact@v4` | Rapor arşivle |

### 2. Preflight CLI (`backend/app/testing/release_preflight.py`)

Mevcut ReleasePolicy + ReleaseGate + ReleaseReportGenerator zincirini tek komutla çalıştıran thin wrapper.

```python
# Arayüz:
# python -m backend.app.testing.release_preflight [--json] [--output-dir DIR]
#
# Çıktı (stdout):
#   verdict: RELEASE_BLOCK
#   spec_hash: a1b2c3...
#   reasons: NO_TIER_DATA, NO_FLAKE_DATA
#   report: artifacts/release_report.txt
#
# Exit codes:
#   0 = RELEASE_OK
#   1 = RELEASE_HOLD
#   2 = RELEASE_BLOCK
```

Tasarım kararları:
- Sinyal verisi olmadan çağrıldığında "dry-run": boş input → BLOCK (beklenen)
- Gerçek sinyal entegrasyonu bu PR'ın kapsamı dışında (gelecek PR)
- Rapor dosyası opsiyonel (`--output-dir` verilmezse sadece stdout)
- JSON modu: makine tarafından parse edilebilir çıktı

```python
def run_preflight(json_mode: bool = False, output_dir: str | None = None) -> int:
    """
    1. Boş ReleasePolicyInput oluştur (dry-run)
    2. ReleasePolicy.evaluate() → result
    3. ReleaseReportGenerator.generate() → report
    4. ReleaseGate.check() → decision
    5. spec_hash() hesapla
    6. Çıktı yaz (stdout + opsiyonel dosya)
    7. Exit code döndür
    """
```

### 3. README Güncellemeleri

`.kiro/specs/release-governance/README.md`'ye eklenen bölümler:

#### 3a. Quickstart: CI Entegrasyonu
- Preflight komutu (tek satır)
- CI snippet dosya yolu referansı
- Artifact çıktı yolu

#### 3b. Gereksinimler ve Bağımlılıklar
- Python 3.11+
- pytest 8.0+, hypothesis 6.0+
- Harici servis yok (pure Python, IO-free)
- Dosya yapısı: `backend/app/testing/` + `backend/tests/`

#### 3c. CI'da HOLD/BLOCK Triage
- Kısa tablo: verdict → anlam → ilk adım
- Runbook.md referansı (detaylı prosedür)

### 4. Preflight Smoke Testi (`backend/tests/test_release_preflight.py`)

Minimal test seti:

| Test | Doğrulama |
|---|---|
| `test_dry_run_returns_block` | Sinyal verisi olmadan → BLOCK verdict |
| `test_dry_run_exit_code` | Exit code = 2 (BLOCK) |
| `test_json_output_valid` | JSON çıktı parse edilebilir, beklenen alanlar var |
| `test_spec_hash_in_output` | spec_hash 64-char hex |
| `test_output_dir_creates_files` | `--output-dir` ile rapor dosyaları oluşur |

## Kapsam Dışı

- Gerçek sinyal toplama (tier runner, flake sentinel vb. entegrasyonu) — gelecek PR
- GitLab CI / Jenkins snippet'leri — talep üzerine eklenebilir
- Docker wrapper — ürünleştirme fazında
- MCP/CLI tool olarak paketleme — ürünleştirme fazında

## Test Stratejisi

- Kütüphane: `pytest`
- Tek test dosyası: `backend/tests/test_release_preflight.py`
- DoD: ≥5 unit test, 0 flaky
- PBT gerekmez (thin wrapper, karar mantığı yok)
