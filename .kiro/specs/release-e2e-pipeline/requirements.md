# Gereksinimler: End-to-End Release Pipeline Simulation + Documentation Artifact Pack (PR-12)

## Genel Bakış

PR-11'de ayrı ayrı kanıtlanan ReleasePolicy, ReleaseReportGenerator ve ReleaseGate bileşenlerinin tek bir zincir olarak birlikte çalıştığını doğrular. Ayrıca operasyon ekibine devredilebilir "golden" audit artifact örnekleri üretir.

## Kullanıcı Hikayeleri

### Gereksinim 1: End-to-End Zincir Doğrulaması

Bir geliştirici olarak, sinyal toplama → policy kararı → rapor üretimi → gate kontrolü → orchestrator yürütme zincirinin uçtan uca doğru çalıştığını görmek istiyorum.

#### Kabul Kriterleri

- 1.1 Tüm sinyaller temiz → RELEASE_OK → gate allowed → orchestrator execute çağrılır → audit "release_ok" içerir
- 1.2 Tier fail → RELEASE_HOLD → gate denied → orchestrator execute çağrılmaz → rapor "required actions" listesi içerir
- 1.3 OPS_GATE_FAIL → RELEASE_BLOCK → gate denied → override girişimi CONTRACT_BREACH_NO_OVERRIDE ile reddedilir → audit breach kaydı içerir
- 1.4 GUARD_VIOLATION → RELEASE_BLOCK → gate denied → override girişimi CONTRACT_BREACH_NO_OVERRIDE ile reddedilir
- 1.5 Canary SAFE/UPGRADE/BREAKING → doğru verdict üretilir (OK veya HOLD/BLOCK)
- 1.6 HOLD + geçerli override → gate allowed → orchestrator execute çağrılır
- 1.7 HOLD + süresi dolmuş override → gate denied
- 1.8 HOLD + scope uyumsuz override → gate denied

### Gereksinim 2: Zincir Bütünlüğü (Chain Integrity)

Bir geliştirici olarak, zincirdeki her bileşenin çıktısının bir sonraki bileşenin girdisiyle uyumlu olduğunu doğrulamak istiyorum.

#### Kabul Kriterleri

- 2.1 Policy verdict ile gate verdict her zaman eşleşir
- 2.2 Policy reasons ile rapor reasons her zaman eşleşir
- 2.3 Policy required_actions ile rapor required_actions sayısı eşleşir
- 2.4 Gate decision.allowed=false ise orchestrator side-effect sayısı 0'dır
- 2.5 Gate decision.allowed=true ise orchestrator en az 1 side-effect üretir

### Gereksinim 3: Golden Audit Artifact

Bir operasyon mühendisi olarak, referans olarak kullanabileceğim deterministik "golden" audit örnekleri görmek istiyorum.

#### Kabul Kriterleri

- 3.1 RELEASE_OK senaryosu için golden JSON snapshot üretilir
- 3.2 RELEASE_HOLD senaryosu için golden JSON snapshot üretilir (tier fail + flaky)
- 3.3 RELEASE_BLOCK senaryosu için golden JSON snapshot üretilir (OPS_GATE_FAIL)
- 3.4 Golden snapshot'lar deterministiktir: aynı girdi → aynı JSON çıktı
- 3.5 Golden snapshot'lar rapor text formatını da içerir
- 3.6 Her golden snapshot gate decision audit_detail alanını içerir

### Gereksinim 4: Yan Etki İzolasyonu (Side-Effect Isolation)

Bir geliştirici olarak, HOLD ve BLOCK durumlarında orchestrator'ın kesinlikle yan etki üretmediğini doğrulamak istiyorum.

#### Kabul Kriterleri

- 4.1 RELEASE_HOLD → orchestrator.execute() çağrılmaz, applied_count = 0
- 4.2 RELEASE_BLOCK → orchestrator.execute() çağrılmaz, applied_count = 0
- 4.3 RELEASE_OK → orchestrator.execute() çağrılır, applied_count > 0
- 4.4 Override ile geçilen HOLD → orchestrator.execute() çağrılır, applied_count > 0

### Gereksinim 5: Deterministik Zincir (PBT)

Bir geliştirici olarak, rastgele girdi kombinasyonlarında tüm zincirin deterministik ve tutarlı olduğunu doğrulamak istiyorum.

#### Kabul Kriterleri

- 5.1 Aynı girdi → aynı policy verdict + aynı rapor + aynı gate decision (PBT)
- 5.2 Gate allowed=false ise orchestrator side-effect yok (PBT)
- 5.3 Mutlak blok nedeni varsa override her zaman reddedilir (PBT)
