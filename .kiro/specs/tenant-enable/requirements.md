# Gereksinimler: Tenant-Level Guard Decision Override

## Giriş

Mevcut Guard Decision Layer global bir ON/OFF flag'i (`OPS_GUARD_DECISION_LAYER_ENABLED`) ile kontrol edilir. Bu spec, global flag'in üzerine tenant bazlı override yeteneği ekler. Amaç: her tenant için bağımsız olarak `shadow`, `enforce` veya `off` modunda çalışabilmek; böylece yeni guard policy'leri tenant bazlı kademeli rollout yapılabilir.

Mevcut global flag korunur ve en yüksek önceliğe sahiptir: Global OFF → tüm tenant'lar için katman devre dışı.

## Sözlük

- **TenantMode**: Bir tenant için guard decision layer'ın çalışma modu (`shadow` | `enforce` | `off`)
- **DefaultMode**: Global ON durumunda, tenant listesinde bulunmayan tenant'lar için uygulanan varsayılan mod
- **TenantModesMap**: Tenant ID → TenantMode eşlemesi; JSON formatında env var ile sağlanır
- **TenantAllowlist**: Metrik emisyonuna izin verilen tenant listesi (cardinality kontrolü)
- **ConfigSnapshot**: Request başında alınan, request boyunca değişmeyen config kopyası
- **GuardDecisionSnapshot**: Mevcut immutable per-request karar kaydı (runtime-guard-decision spec'inden)
- **SnapshotFactory**: GuardDecisionSnapshot üreten fabrika sınıfı

## Gereksinimler

### Gereksinim 1: Global Flag Önceliği

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, global OFF flag'inin tüm tenant override'larını geçersiz kılmasını istiyorum; böylece acil durumda tek bir switch ile tüm sistemi devre dışı bırakabilirim.

#### Kabul Kriterleri

1. WHEN `OPS_GUARD_DECISION_LAYER_ENABLED` değeri `false` ise, THE decision layer SHALL tüm tenant'lar için devre dışı kalır; tenant config'i değerlendirilmez
2. WHEN `OPS_GUARD_DECISION_LAYER_ENABLED` değeri `true` ise, THE decision layer SHALL tenant bazlı mod çözümlemesi yapar

### Gereksinim 2: Tenant Mod Çözümlemesi

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, her tenant için bağımsız olarak `shadow`, `enforce` veya `off` modu atayabilmek istiyorum; böylece yeni policy'leri kademeli olarak rollout yapabilirim.

#### Kabul Kriterleri

1. THE resolve_tenant_mode fonksiyonu SHALL tenant_id ve config parametreleri alarak deterministik bir TenantMode döner
2. WHEN tenant_id, `OPS_GUARD_DECISION_LAYER_TENANT_MODES_JSON` map'inde bulunduğunda, THE resolver SHALL o tenant'a atanmış modu döner
3. WHEN tenant_id, tenant modes map'inde bulunmadığında, THE resolver SHALL `OPS_GUARD_DECISION_LAYER_DEFAULT_MODE` değerini döner
4. WHEN tenant_id boş string veya None ise, THE resolver SHALL tenant_id olarak `"default"` kullanır ve default mode döner

### Gereksinim 3: Config Yüzeyi

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, tenant override konfigürasyonunu environment variable'lar ile yönetmek istiyorum; böylece mevcut deployment pipeline'ına uyumlu şekilde config değişikliği yapabilirim.

#### Kabul Kriterleri

1. THE GuardConfig SHALL `decision_layer_default_mode` alanını destekler; geçerli değerler: `shadow`, `enforce`, `off`; varsayılan: `shadow`
2. THE GuardConfig SHALL `decision_layer_tenant_modes_json` alanını destekler; JSON string formatında tenant_id → mode eşlemesi
3. THE GuardConfig SHALL `decision_layer_tenant_allowlist_json` alanını destekler; JSON string formatında tenant_id listesi (metrik allowlist)
4. WHEN `decision_layer_default_mode` geçersiz bir değer içerdiğinde, THE GuardConfig validator SHALL hata üretir ve fallback default'a döner
5. WHEN `decision_layer_tenant_modes_json` geçersiz JSON içerdiğinde, THE config parser SHALL boş map döner ve default mode uygulanır (fail-open)
6. WHEN `decision_layer_tenant_modes_json` içinde geçersiz mod değeri bulunan bir tenant varsa, THE config parser SHALL o tenant'ı atlar ve loglama yapar

### Gereksinim 4: Fail-Open JSON Parse

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, bozuk JSON config'in sistemi kırmamasını istiyorum; parse hatası durumunda güvenli varsayılana dönülmeli.

#### Kabul Kriterleri

1. WHEN `decision_layer_tenant_modes_json` parse edilemediğinde, THE parser SHALL boş map döner, log yazar ve default mode uygulanır
2. WHEN `decision_layer_tenant_allowlist_json` parse edilemediğinde, THE parser SHALL boş liste döner ve log yazar
3. THE fail-open davranışı SHALL hiçbir zaman request'i bloklamaz veya exception fırlatmaz

### Gereksinim 5: Snapshot Determinizmi

**Kullanıcı Hikayesi:** Bir geliştirici olarak, request başında alınan tenant config snapshot'ının request boyunca değişmemesini istiyorum; mid-flight config değişikliği aynı request'in kararını etkilememeli.

#### Kabul Kriterleri

1. THE SnapshotFactory.build() SHALL request başında tenant mode'u çözer ve snapshot'a dahil eder
2. WHEN config mid-flight değiştiğinde, THE snapshot SHALL request boyunca aynı tenant mode'u korur
3. THE GuardDecisionSnapshot SHALL `tenant_mode` alanını içerir (TenantMode tipinde)

### Gereksinim 6: Tenant Mode Enforcement Entegrasyonu

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, tenant mode'unun mevcut enforcement akışına entegre olmasını istiyorum; `off` modundaki tenant'lar için decision layer atlanmalı, `shadow` modundaki tenant'lar için sadece metrik/log üretilmeli.

#### Kabul Kriterleri

1. WHEN tenant mode `off` ise, THE middleware SHALL decision layer'ı atlar ve request'i doğrudan handler'a iletir
2. WHEN tenant mode `shadow` ise, THE middleware SHALL snapshot build + evaluate yapar, BLOCK verdict'te metrik ve log üretir ama request'i bloklamaz
3. WHEN tenant mode `enforce` ise, THE middleware SHALL tam enforcement uygular; BLOCK verdict'te 503 döner
4. THE middleware SHALL mevcut global `decision_layer_mode` yerine tenant bazlı mode kullanır

### Gereksinim 7: Tenant ID Extraction

**Kullanıcı Hikayesi:** Bir geliştirici olarak, request'ten tenant_id çıkarılamaması durumunda güvenli bir varsayılana dönülmesini istiyorum.

#### Kabul Kriterleri

1. WHEN request'ten tenant_id çıkarılamadığında, THE extractor SHALL `"default"` tenant_id döner
2. THE tenant_id extraction fonksiyonu SHALL pure function olarak implement edilir (side-effect yok)

### Gereksinim 8: Metrik Cardinality Kontrolü

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, tenant bazlı metriklerin Prometheus cardinality patlamasına yol açmamasını istiyorum; yalnızca allowlist'teki tenant'lar için tenant label'ı eklenmeli.

#### Kabul Kriterleri

1. THE metrik emisyonu SHALL tenant label'ını yalnızca `OPS_GUARD_DECISION_LAYER_TENANT_ALLOWLIST_JSON` listesindeki tenant'lar için ekler
2. WHEN tenant allowlist'te bulunmadığında, THE metrik emisyonu SHALL tenant label'ı olarak `"_other"` kullanır
3. WHEN allowlist boş veya parse edilemez durumda ise, THE metrik emisyonu SHALL tüm tenant'lar için `"_other"` label'ı kullanır
