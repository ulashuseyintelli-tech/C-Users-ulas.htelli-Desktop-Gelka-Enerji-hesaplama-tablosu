# Gereksinimler: Release Governance Pack (PR-13)

## Genel Bakış

PR-11 (release-governance) ve PR-12 (e2e-pipeline) ile kanıtlanan release zincirini ekip dışına devredilebilir hale getirir. Yeni production kodu yok; çıktılar dokümantasyon, CI komut referansı ve spec hash versiyonlaması.

## Kullanıcı Hikayeleri

### Gereksinim 1: Index README

Bir operasyon mühendisi olarak, release-governance sisteminin ne olduğunu, hangi dosyaların nerede olduğunu ve nasıl kullanılacağını tek bir sayfada görmek istiyorum.

#### Kabul Kriterleri

- 1.1 `.kiro/specs/release-governance/README.md` dosyası oluşturulur
- 1.2 README şunları içerir: sistem özeti (3-5 cümle), dosya haritası (modül → dosya yolu), test haritası (test dosyası → ne test ediyor), runbook referansı
- 1.3 README, mevcut runbook.md'ye link verir
- 1.4 README, PR-11 ve PR-12 spec dosyalarına referans verir

### Gereksinim 2: Reason Code → Required Action Referans Tablosu

Bir operasyon mühendisi olarak, tüm neden kodlarının ve karşılık gelen aksiyonların otomatik üretilmiş bir referans tablosunu görmek istiyorum.

#### Kabul Kriterleri

- 2.1 `backend/app/testing/release_policy.py` içindeki `_ACTION_DESCRIPTIONS` dict'inden reason code → action tablosu üretilir
- 2.2 Tablo şu sütunları içerir: Neden Kodu, Verdict Seviyesi (HOLD/BLOCK), Aksiyon Açıklaması, Override Edilebilir mi?
- 2.3 Tablo deterministiktir: aynı kaynak → aynı çıktı
- 2.4 Tablo README veya runbook içinde yer alır (ayrı dosya değil)

### Gereksinim 3: CI Pipeline Komut Referansı

Bir geliştirici olarak, release-governance testlerini CI'da nasıl çalıştıracağımı bilmek istiyorum.

#### Kabul Kriterleri

- 3.1 README'de CI komutları bölümü yer alır
- 3.2 Komutlar: tüm release testleri (unit + PBT), sadece unit, sadece PBT, tek modül (policy/report/gate/e2e)
- 3.3 Her komut copy-paste çalışır (tam pytest komutu)
- 3.4 PBT komutlarında `--hypothesis-seed` kullanımı açıklanır (reproduceability)

### Gereksinim 4: Spec Hash Versiyonlama

Bir geliştirici olarak, policy/report/gate modüllerinin hangi spec versiyonuna karşı kanıtlandığını bilmek istiyorum.

#### Kabul Kriterleri

- 4.1 `backend/app/testing/release_version.py` dosyası oluşturulur
- 4.2 Dosya, release_policy.py + release_report.py + release_gate.py dosyalarının SHA-256 hash'ini hesaplayan `spec_hash()` fonksiyonu içerir
- 4.3 Hash deterministiktir: aynı dosya içeriği → aynı hash
- 4.4 Hash, audit raporlarına eklenebilir (ReleaseReportGenerator ile entegre edilmez, sadece bağımsız fonksiyon)
- 4.5 Basit unit test: hash hesaplanır, None/boş değildir, tekrar hesaplandığında aynıdır

### Gereksinim 5: Paket Bütünlüğü Testi

Bir geliştirici olarak, tüm release-governance dosyalarının mevcut olduğunu ve import edilebilir olduğunu doğrulayan bir "smoke" test istiyorum.

#### Kabul Kriterleri

- 5.1 `backend/tests/test_release_pack.py` dosyası oluşturulur
- 5.2 Tüm modüller import edilebilir: release_policy, release_report, release_gate, release_version
- 5.3 Tüm public sınıflar instantiate edilebilir: ReleasePolicy, ReleaseReportGenerator, ReleaseGate
- 5.4 spec_hash() çağrılabilir ve deterministik
- 5.5 Reason code tablosu üretilebilir (boş değil)
