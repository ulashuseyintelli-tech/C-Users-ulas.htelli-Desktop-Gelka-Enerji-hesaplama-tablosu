# Gereksinimler: CI Enforcement Mode — PR-16

## Giriş

PR-15 ile release governance preflight kontrolü CI'da çalışır hale geldi, ancak `continue-on-error: true` ile yalnızca raporlama modunda çalışıyor — merge'i engellemiyor. PR-16, preflight exit code'unu gerçek bir CI kapısına dönüştürür: BLOCK (exit 2) → job başarısız → PR merge edilemez; HOLD (exit 1) → job başarısız, ancak manuel override mekanizması ile geçilebilir. BLOCK verdict'inde mutlak blok nedenleri (GUARD_VIOLATION, OPS_GATE_FAIL) asla override edilemez — bu sözleşme ihlali sayılır.

## Sözlük

- **Preflight_CLI**: `backend/app/testing/release_preflight.py` — release governance zincirini çalıştıran komut satırı aracı
- **CI_Workflow**: `docs/ci/release-governance.yml` — GitHub Actions workflow dosyası
- **ReleaseGate**: `backend/app/testing/release_gate.py` — enforcement hook, override doğrulama
- **ReleaseOverride**: `release_gate.py` içindeki dataclass — TTL, scope, reason, created_by alanları
- **ABSOLUTE_BLOCK_REASONS**: `GUARD_VIOLATION` ve `OPS_GATE_FAIL` — override ile geçilemez neden kodları
- **Exit_Code_Sözleşmesi**: 0 = RELEASE_OK, 1 = RELEASE_HOLD, 2 = RELEASE_BLOCK, 64 = usage error
- **Enforcement_Mode**: Preflight exit code'unun CI job başarı/başarısızlığını doğrudan kontrol ettiği mod
- **Override_Flags**: `--override-reason`, `--override-scope`, `--override-by` — HOLD verdict'ini CI'da override etmek için CLI bayrakları

## Gereksinimler

### Gereksinim 1: CI Workflow Enforcement

**Kullanıcı Hikayesi:** Bir geliştirici olarak, preflight BLOCK veya HOLD verdiğinde PR'ın merge edilememesini istiyorum; böylece release governance kararları CI'da zorunlu hale gelir.

#### Kabul Kriterleri

1. WHEN Preflight_CLI exit code 2 (BLOCK) döndürdüğünde, THE CI_Workflow SHALL job'ı başarısız olarak işaretler ve PR merge'i engellenir
2. WHEN Preflight_CLI exit code 1 (HOLD) döndürdüğünde ve override flag'leri sağlanmadığında, THE CI_Workflow SHALL job'ı başarısız olarak işaretler
3. WHEN Preflight_CLI exit code 0 (OK) döndürdüğünde, THE CI_Workflow SHALL job'ı başarılı olarak işaretler
4. THE CI_Workflow SHALL preflight adımından `continue-on-error: true` ayarını kaldırır ve exit code'u doğrudan job sonucuna yansıtır
5. THE CI_Workflow SHALL mevcut raporlama ve artifact upload işlevselliğini korur (geriye dönük uyumluluk)

### Gereksinim 2: HOLD Override Mekanizması (CLI)

**Kullanıcı Hikayesi:** Bir geliştirici olarak, HOLD verdict'ini CI'da override edebilmek için CLI flag'leri kullanmak istiyorum; böylece düzeltilebilir sorunları bilinçli olarak geçebilirim.

#### Kabul Kriterleri

1. THE Preflight_CLI SHALL `--override-reason`, `--override-scope` ve `--override-by` flag'lerini kabul eder
2. WHEN üç override flag'inin tamamı sağlandığında ve verdict HOLD olduğunda, THE Preflight_CLI SHALL ReleaseOverride oluşturarak ReleaseGate'e tekrar kontrol yaptırır
3. WHEN override geçerli olduğunda (TTL ve scope eşleşmesi), THE Preflight_CLI SHALL exit code 0 döndürür
4. WHEN override flag'lerinden herhangi biri eksik olduğunda (kısmi sağlama), THE Preflight_CLI SHALL override işlemini yok sayar ve normal akışı sürdürür
5. WHEN override flag'leri sağlandığında ve verdict RELEASE_OK olduğunda, THE Preflight_CLI SHALL override'ı yok sayar ve exit code 0 döndürür (override gereksiz)

### Gereksinim 3: BLOCK Override Engeli (Sözleşme)

**Kullanıcı Hikayesi:** Bir operasyon mühendisi olarak, ABSOLUTE_BLOCK_REASONS (GUARD_VIOLATION, OPS_GATE_FAIL) içeren BLOCK verdict'lerinin CI'da asla override edilememesini istiyorum; böylece sözleşme ihlalleri korunur.

#### Kabul Kriterleri

1. WHEN override flag'leri sağlandığında ve verdict BLOCK olduğunda ve nedenler ABSOLUTE_BLOCK_REASONS içerdiğinde, THE Preflight_CLI SHALL override'ı reddeder ve exit code 2 döndürür
2. WHEN override flag'leri sağlandığında ve verdict BLOCK olduğunda ve nedenler ABSOLUTE_BLOCK_REASONS içerdiğinde, THE Preflight_CLI SHALL çıktıda "CONTRACT_BREACH" uyarısı gösterir
3. THE Preflight_CLI SHALL BLOCK verdict'inde override flag'lerinin varlığından bağımsız olarak her zaman exit code 2 döndürür

### Gereksinim 4: CI Workflow Override Entegrasyonu

**Kullanıcı Hikayesi:** Bir geliştirici olarak, CI workflow'unda override flag'lerini environment variable veya workflow_dispatch input olarak sağlayabilmek istiyorum; böylece HOLD override'ı CI pipeline'ında kullanılabilir.

#### Kabul Kriterleri

1. THE CI_Workflow SHALL `workflow_dispatch` event'i ile `override_reason`, `override_scope` ve `override_by` input'larını kabul eder
2. WHEN workflow_dispatch input'ları sağlandığında, THE CI_Workflow SHALL bu değerleri Preflight_CLI'ya `--override-reason`, `--override-scope`, `--override-by` flag'leri olarak iletir
3. WHEN workflow_dispatch input'ları sağlanmadığında, THE CI_Workflow SHALL preflight'ı override flag'leri olmadan çalıştırır (mevcut davranış)
4. THE CI_Workflow SHALL override input'larını step summary'de görünür kılar (audit trail)

### Gereksinim 5: Enforcement Mode Testleri

**Kullanıcı Hikayesi:** Bir geliştirici olarak, enforcement mode davranışlarını doğrulayan testler istiyorum; böylece BLOCK/HOLD/override sözleşmesi regresyona karşı korunur.

#### Kabul Kriterleri

1. WHEN BLOCK verdict'i oluştuğunda, THE test_suite SHALL exit code 2 döndüğünü doğrular
2. WHEN HOLD verdict'i oluştuğunda ve override flag'leri sağlanmadığında, THE test_suite SHALL exit code 1 döndüğünü doğrular
3. WHEN HOLD verdict'i oluştuğunda ve geçerli override flag'leri sağlandığında, THE test_suite SHALL exit code 0 döndüğünü doğrular
4. WHEN BLOCK verdict'i ABSOLUTE_BLOCK_REASONS içerdiğinde ve override flag'leri sağlandığında, THE test_suite SHALL exit code 2 döndüğünü ve override'ın reddedildiğini doğrular
5. WHEN kısmi override flag'leri sağlandığında, THE test_suite SHALL override'ın yok sayıldığını doğrular

### Gereksinim 6: Dokümantasyon Güncellemeleri

**Kullanıcı Hikayesi:** Bir geliştirici olarak, enforcement mode ve CI override prosedürünü README ve runbook'ta görmek istiyorum; böylece CI'da HOLD çıktığında ne yapacağımı bilirim.

#### Kabul Kriterleri

1. THE README SHALL "Enforcement Mode" bölümü içerir: enforcement davranışı, override flag'leri, exit code sözleşmesi
2. THE Runbook SHALL "CI Override Prosedürü" bölümü içerir: workflow_dispatch ile override adımları, hangi durumlarda override yapılabilir/yapılamaz
3. THE README SHALL override flag'lerinin kullanım örneklerini içerir (CLI komutu)
