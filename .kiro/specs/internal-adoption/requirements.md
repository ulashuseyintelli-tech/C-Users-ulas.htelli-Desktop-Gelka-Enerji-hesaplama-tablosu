# Gereksinimler: Internal Adoption — PR-15

## Genel Bakış

Release Governance Framework (v1.0.0) teknik olarak tamamlanmış ve test edilmiş durumda. Bu PR, framework'ü başka repo/ekiplerin kopyala-yapıştır tüketebileceği hale getirir: CI pipeline snippet, preflight CLI komutu, config referansı ve triage kılavuzu.

## Kullanıcı Hikayeleri

### Gereksinim 1: CI Pipeline Snippet (GitHub Actions)

Bir geliştirici olarak, release governance testlerini CI'da çalıştırmak için kopyala-yapıştır bir GitHub Actions workflow dosyası istiyorum.

#### Kabul Kriterleri

- 1.1 `docs/ci/release-governance.yml` dosyası oluşturulur (GitHub Actions workflow)
- 1.2 Workflow şu adımları içerir: Python setup, dependency install, smoke/core test, PBT test, release preflight (gate check), artifact upload (report JSON + text)
- 1.3 Workflow `push` ve `pull_request` event'lerinde tetiklenir (paths filter: `backend/app/testing/**`)
- 1.4 Workflow dosyası geçerli YAML'dır (parse edilebilir)
- 1.5 Preflight adımı verdict + spec_hash + reason code summary çıktısı üretir

### Gereksinim 2: Preflight CLI Komutu

Bir geliştirici olarak, release gate kontrolünü tek komutla çalıştırabilmek istiyorum; çıktı: verdict, spec_hash, reason summary.

#### Kabul Kriterleri

- 2.1 `backend/app/testing/release_preflight.py` dosyası oluşturulur
- 2.2 `python -m backend.app.testing.release_preflight` ile çalıştırılabilir (`__main__` block)
- 2.3 Çıktı: verdict (OK/HOLD/BLOCK), spec_hash (64-char hex), reason code listesi (varsa), report dosya yolu (varsa)
- 2.4 Exit code: 0 = OK, 1 = HOLD, 2 = BLOCK
- 2.5 `--json` flag ile JSON çıktı desteği
- 2.6 `--output-dir` flag ile rapor dosyası yazma desteği (text + JSON)
- 2.7 Sinyal verisi olmadan çağrıldığında "dry-run" modu: NO_TIER_DATA + NO_FLAKE_DATA → BLOCK (beklenen davranış, hata değil)

### Gereksinim 3: README Quickstart Bölümü

Bir geliştirici olarak, release governance'ı CI'a entegre etmek için 10 satırlık bir quickstart bölümü görmek istiyorum.

#### Kabul Kriterleri

- 3.1 `.kiro/specs/release-governance/README.md`'ye "Quickstart: CI Entegrasyonu" bölümü eklenir
- 3.2 Bölüm şunları içerir: preflight komutu, CI snippet referansı, artifact yolu
- 3.3 Bölüm 15 satırı geçmez (kısa ve öz)

### Gereksinim 4: Config / Env Referansı

Bir geliştirici olarak, framework'ün çalışması için gereken minimal config/env listesini görmek istiyorum.

#### Kabul Kriterleri

- 4.1 README'ye "Gereksinimler ve Bağımlılıklar" bölümü eklenir
- 4.2 Bölüm şunları listeler: Python versiyonu, pip bağımlılıkları (pytest, hypothesis), dosya yapısı gereksinimleri
- 4.3 Harici servis bağımlılığı yok (pure Python, IO-free) — bu açıkça belirtilir

### Gereksinim 5: HOLD/BLOCK Triage Kılavuzu

Bir operasyon mühendisi olarak, CI'da HOLD veya BLOCK çıktığında ne yapacağımı bilmek istiyorum.

#### Kabul Kriterleri

- 5.1 README'ye "CI'da HOLD/BLOCK Triage" bölümü eklenir veya runbook'a referans verilir
- 5.2 Her verdict seviyesi için: ne anlama gelir, ilk adım ne, kime eskale edilir
- 5.3 Runbook.md'ye link verilir (detaylı prosedür orada)

### Gereksinim 6: Preflight Smoke Testi

Bir geliştirici olarak, preflight CLI komutunun doğru çalıştığını doğrulayan basit bir test istiyorum.

#### Kabul Kriterleri

- 6.1 `backend/tests/test_release_preflight.py` dosyası oluşturulur
- 6.2 Dry-run modu test edilir: sinyal verisi olmadan → BLOCK verdict, exit code 2
- 6.3 JSON çıktı formatı test edilir: geçerli JSON, beklenen alanlar mevcut
- 6.4 spec_hash çıktıda mevcut ve 64-char hex
