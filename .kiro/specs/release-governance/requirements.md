# Gereksinimler Dokümanı — Release Governance + Change Management (Ops Policy Layer)

## Giriş

PR-11, PR-10'da kurulan test disiplini çıktılarını (tier sonuçları, bütçe kullanımı, flake sentinel, drift monitor) "release kararlarına" bağlayan bir yönetişim katmanıdır. Üç ana bileşenden oluşur: (1) ReleasePolicy — saf-matematik karar fonksiyonu, (2) Release Notes Generator — deterministik denetim raporu, (3) Enforcement Hook — pipeline seviyesinde engelleme mekanizması.

## Sözlük

- **ReleasePolicy**: Tier sonuçları, FlakeSentinel snapshot'ı, DriftMonitor snapshot'ı, PolicyCanary sınıflandırması ve Ops contract gate sonuçlarını girdi olarak alıp RELEASE_OK / RELEASE_HOLD / RELEASE_BLOCK kararı üreten saf fonksiyon.
- **ReleaseVerdict**: ReleasePolicy'nin ürettiği karar (RELEASE_OK, RELEASE_HOLD, RELEASE_BLOCK).
- **BlockReason**: RELEASE_BLOCK veya RELEASE_HOLD kararının deterministik neden kodu.
- **RequiredAction**: Bir HOLD veya BLOCK kararını çözmek için gereken somut eylem açıklaması.
- **ReleaseReport**: Yavaş testler, bütçe kullanımı, policy drift özeti, override aktivitesi ve guard durumunu içeren deterministik denetim raporu.
- **EnforcementHook**: Orchestrator/pipeline seviyesinde ReleasePolicy kararını uygulayan kapı mekanizması.
- **TierRunResult**: Bir test tier'ının çalışma sonucu (süre, bütçe, pass/fail, en yavaş testler).
- **FlakeSentinel**: Kayan pencere üzerinde flaky test tespiti yapan bileşen.
- **DriftSnapshot**: DriftMonitor'un ürettiği abort/override oranları ve alert durumu.
- **PolicyCanaryResult**: PolicyCanary'nin ürettiği sınıflandırma (SAFE, UPGRADE, BREAKING, GUARD_VIOLATION).
- **OpsGateStatus**: Ops contract gate'inin geçti/kaldı durumu.
- **ManualOverride**: RELEASE_HOLD durumunda TTL ve scope kısıtlamaları altında yapılan manuel onay.

## Gereksinimler

### Gereksinim 1: ReleasePolicy Karar Fonksiyonu

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, tüm test ve yönetişim sinyallerini tek bir deterministik karar fonksiyonunda birleştirmek istiyorum; böylece release kararları tutarlı, tekrarlanabilir ve denetlenebilir olur.

#### Kabul Kriterleri

1. WHEN tüm tier sonuçları pass, FlakeSentinel temiz, DriftSnapshot alert yok, PolicyCanaryResult SAFE ve OpsGateStatus geçti ise, THE ReleasePolicy SHALL RELEASE_OK kararı üretir.
2. WHEN herhangi bir tier sonucu fail ise, THE ReleasePolicy SHALL RELEASE_HOLD kararı üretir ve ilgili tier'ı BlockReason olarak bildirir.
3. WHEN FlakeSentinel en az bir flaky test tespit ettiğinde, THE ReleasePolicy SHALL RELEASE_HOLD veya RELEASE_BLOCK kararı üretir ve flaky test listesini BlockReason olarak bildirir.
4. WHEN DriftSnapshot alert durumunda ise, THE ReleasePolicy SHALL RELEASE_HOLD kararı üretir ve drift oranlarını BlockReason olarak bildirir.
5. WHEN PolicyCanaryResult BREAKING ise, THE ReleasePolicy SHALL RELEASE_HOLD kararı üretir ve breaking drift sayısını BlockReason olarak bildirir.
6. WHEN PolicyCanaryResult GUARD_VIOLATION ise, THE ReleasePolicy SHALL RELEASE_BLOCK kararı üretir ve guard violation detaylarını BlockReason olarak bildirir.
7. WHEN OpsGateStatus kaldı (passed=false) ise, THE ReleasePolicy SHALL RELEASE_BLOCK kararı üretir ve ops gate failure'ı BlockReason olarak bildirir.
8. THE ReleasePolicy SHALL her karar için deterministik BlockReason kodları listesi üretir; aynı girdi her zaman aynı neden kodlarını üretir.
9. THE ReleasePolicy SHALL her HOLD veya BLOCK kararı için en az bir RequiredAction üretir.
10. WHEN birden fazla blok koşulu aynı anda mevcut ise, THE ReleasePolicy SHALL en kısıtlayıcı kararı seçer (BLOCK > HOLD > OK) ve tüm neden kodlarını birleştirir.

### Gereksinim 2: ReleasePolicy Girdi Doğrulama

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, ReleasePolicy'ye geçersiz veya eksik girdi verildiğinde güvenli bir şekilde başarısız olmasını istiyorum; böylece hatalı veriyle yanlış release kararı alınmaz.

#### Kabul Kriterleri

1. WHEN tier sonuçları listesi boş ise, THE ReleasePolicy SHALL RELEASE_BLOCK kararı üretir ve "NO_TIER_DATA" BlockReason kodu bildirir.
2. WHEN FlakeSentinel snapshot'ı None/eksik ise, THE ReleasePolicy SHALL RELEASE_BLOCK kararı üretir ve "NO_FLAKE_DATA" BlockReason kodu bildirir.
3. WHEN DriftSnapshot None/eksik ise, THE ReleasePolicy SHALL RELEASE_HOLD kararı üretir ve "NO_DRIFT_DATA" BlockReason kodu bildirir.
4. WHEN PolicyCanaryResult None/eksik ise, THE ReleasePolicy SHALL RELEASE_HOLD kararı üretir ve "NO_CANARY_DATA" BlockReason kodu bildirir.

### Gereksinim 3: Release Notes Generator

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, her release kararıyla birlikte deterministik bir denetim raporu oluşturmak istiyorum; böylece karar gerekçesi ve sistem durumu arşivlenebilir.

#### Kabul Kriterleri

1. THE ReleaseReport SHALL en yavaş testler listesini (tier bazında, en fazla 10 test/tier) içerir.
2. THE ReleaseReport SHALL her tier için bütçe kullanım yüzdesini (kullanılan/bütçe) içerir.
3. THE ReleaseReport SHALL DriftSnapshot'tan abort oranı, override oranı ve alert durumunu içerir.
4. THE ReleaseReport SHALL override aktivite özetini (toplam override sayısı, aktif override sayısı, süresi dolmuş override sayısı) içerir.
5. THE ReleaseReport SHALL guard durumunu (aktif guard'lar, ihlal edilen guard'lar) içerir.
6. THE ReleaseReport SHALL ReleaseVerdict, tüm BlockReason kodları ve RequiredAction listesini içerir.
7. THE ReleaseReport SHALL aynı girdi verildiğinde her zaman aynı çıktıyı üretir (deterministik).
8. WHEN ReleaseReport metin formatında üretildiğinde, THE ReleaseReport SHALL yapılandırılmış ve okunabilir bir düz metin formatı kullanır.
9. WHEN ReleaseReport veri yapısı olarak üretildiğinde, THE ReleaseReport SHALL JSON serileştirilebilir bir veri yapısı döndürür.
10. FOR ALL geçerli ReleaseReport veri yapıları, JSON serileştirme sonrası deserileştirme eşdeğer bir nesne üretir (round-trip özelliği).

### Gereksinim 4: Enforcement Hook (Pipeline Gate)

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, ReleasePolicy kararının pipeline/orchestrator seviyesinde otomatik olarak uygulanmasını istiyorum; böylece BLOCK kararı olan bir release asla promote edilemez.

#### Kabul Kriterleri

1. WHEN ReleaseVerdict RELEASE_BLOCK ise, THE EnforcementHook SHALL promote işlemini engeller ve blok nedenlerini raporlar.
2. WHEN ReleaseVerdict RELEASE_OK ise, THE EnforcementHook SHALL promote işlemine izin verir.
3. WHEN ReleaseVerdict RELEASE_HOLD ise, THE EnforcementHook SHALL promote işlemini ManualOverride gerektirir olarak işaretler.
4. WHEN RELEASE_HOLD durumunda ManualOverride sağlandığında, THE EnforcementHook SHALL override'ın TTL süresi içinde olduğunu doğrular.
5. WHEN RELEASE_HOLD durumunda ManualOverride'ın TTL süresi dolmuş ise, THE EnforcementHook SHALL promote işlemini engeller ve "OVERRIDE_EXPIRED" BlockReason kodu bildirir.
6. WHEN RELEASE_HOLD durumunda ManualOverride sağlandığında, THE EnforcementHook SHALL override scope'unun mevcut release ile eşleştiğini doğrular.
7. THE EnforcementHook SHALL her karar (izin/engel/override) için bir denetim kaydı oluşturur.

### Gereksinim 5: Monotonik Blok Kuralı

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, bir sinyal BLOCK gerektiriyorsa ek sinyallerin bu kararı asla OK'e düşürmemesini istiyorum; böylece güvenlik garantisi korunur.

#### Kabul Kriterleri

1. WHEN herhangi bir girdi sinyali RELEASE_BLOCK gerektirdiğinde, THE ReleasePolicy SHALL diğer sinyallerden bağımsız olarak nihai kararı RELEASE_BLOCK veya daha kısıtlayıcı tutar.
2. WHEN herhangi bir girdi sinyali RELEASE_HOLD gerektirdiğinde ve hiçbir sinyal RELEASE_BLOCK gerektirmediğinde, THE ReleasePolicy SHALL nihai kararı en az RELEASE_HOLD tutar.

### Gereksinim 6: Mutlak Blok Kuralları (Sözleşme İhlalleri)

**Kullanıcı Hikayesi:** Bir platform mühendisi olarak, GUARD_VIOLATION ve OPS gate failure durumlarının her zaman mutlak BLOCK olmasını istiyorum; çünkü bunlar "bilinçli risk" değil "sözleşme ihlali"dir ve override ile geçilemez.

#### Kabul Kriterleri

1. WHEN PolicyCanaryResult GUARD_VIOLATION ise, THE ReleasePolicy SHALL RELEASE_BLOCK kararı üretir; bu karar ManualOverride ile geçersiz kılınamaz.
2. WHEN OpsGateStatus kaldı (passed=false) ise, THE ReleasePolicy SHALL RELEASE_BLOCK kararı üretir; bu karar ManualOverride ile geçersiz kılınamaz.
3. WHEN EnforcementHook bir GUARD_VIOLATION veya OPS gate failure kaynaklı BLOCK ile karşılaştığında, THE EnforcementHook SHALL ManualOverride girişimlerini reddeder ve "CONTRACT_BREACH_NO_OVERRIDE" neden kodu bildirir.
