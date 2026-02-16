# Gereksinimler: Runtime Guard Decision Layer

## Giriş

Mevcut Ops Guard middleware zinciri (KillSwitch → RateLimiter → CircuitBreaker) binary allow/deny kararları üretir. Bu spec, guard zincirinin üzerine oturan yeni bir "decision snapshot" katmanı ekler. Amaç: guard kararlarını normalize edilmiş sinyallerle zenginleştirmek, config freshness / data sufficiency sorunlarını tespit etmek ve per-request immutable snapshot üretmek.

Mevcut guard'lar değiştirilmez; yeni katman onları "signal producer" olarak kullanır.

## Sözlük

- **GuardDecisionSnapshot**: Request başında üretilen, request boyunca değişmeyen immutable karar kaydı
- **GuardSignal**: Tek bir guard/veri kaynağının normalize edilmiş durumu (OK / STALE / INSUFFICIENT)
- **SignalStatus**: Sinyalin sağlık durumu enum'u (OK, STALE, INSUFFICIENT)
- **WindowParams**: Risk değerlendirme pencere parametreleri (config yaşı, clock skew toleransı vb.)
- **RiskContextHash**: Snapshot'ın deterministik hash'i; aynı koşullar → aynı hash, windowParams dahil
- **Config Freshness**: GuardConfig.last_updated_at alanının yaşı; stale config = sessiz güvenlik riski
- **Mapping Miss**: Endpoint → dependency eşlemesinin bulunamadığı durum (CB pre-check atlanır)

## Gereksinimler

### Gereksinim 1: Wrap, Don't Replace

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, mevcut guard davranışlarının (429 + Retry-After, 503 vb.) korunmasını istiyorum; yeni katman mevcut HTTP semantiğini bozmamalı.

#### Kabul Kriterleri

1. THE GuardDecisionSnapshot SHALL mevcut GuardDenyReason çıktılarını `guard_deny_reason` alanında aynen saklar; RATE_LIMITED → 429 + Retry-After semantiği değişmez
2. THE decision layer SHALL mevcut OpsGuardMiddleware karar zincirini (KillSwitch → RateLimiter → CircuitBreaker) değiştirmez; yalnızca sonuçlarını okur
3. THE decision layer SHALL mevcut guard'ların metrik emisyonlarını etkilemez

### Gereksinim 2: Deterministic Snapshot

**Kullanıcı Hikayesi:** Bir geliştirici olarak, her request için tek bir immutable snapshot üretilmesini istiyorum; böylece mid-flight config değişikliği aynı request'in kararını değiştirmez.

#### Kabul Kriterleri

1. THE SnapshotFactory SHALL request başında tek bir GuardDecisionSnapshot üretir
2. THE GuardDecisionSnapshot SHALL frozen dataclass olarak implement edilir; üretildikten sonra hiçbir alanı değiştirilemez
3. WHEN config mid-flight değiştiğinde, THE snapshot SHALL request boyunca aynı kalır (snapshot üretim anındaki config kullanılır)

### Gereksinim 3: Derived Stale/Insufficient from Signals

**Kullanıcı Hikayesi:** Bir geliştirici olarak, stale/insufficient durumlarının caller flag'lerinden değil, signals'tan türetilmesini istiyorum; böylece caller yanlış flag set etse bile sistem güvenli kalır.

#### Kabul Kriterleri

1. THE `derived_has_stale` alanı SHALL yalnızca `signals` tuple'ındaki en az bir sinyalin `status == STALE` olmasıyla True olur
2. THE `derived_has_insufficient` alanı SHALL yalnızca `signals` tuple'ındaki en az bir sinyalin `status == INSUFFICIENT` olmasıyla True olur
3. THE snapshot SHALL caller'dan `anyStale` / `anyInsufficient` flag'i kabul etmez; bu değerler yalnızca signals'tan derive edilir

### Gereksinim 4: Concrete STALE/INSUFFICIENT Rules (v1)

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, STALE ve INSUFFICIENT durumlarının deterministik, test edilebilir kurallara bağlı olmasını istiyorum.

#### Kabul Kriterleri

1. WHEN `GuardConfig.last_updated_at` boş string veya parse edilemez format ise, THE config freshness signal SHALL `INSUFFICIENT` status üretir
2. WHEN `now_ms - parse(last_updated_at)` > `WindowParams.max_config_age_ms` ise, THE config freshness signal SHALL `STALE` status üretir
3. WHEN endpoint → dependency mapping bulunamadığında (CB registry miss), THE mapping signal SHALL `INSUFFICIENT` status üretir
4. WHEN yukarıdaki koşulların hiçbiri geçerli değilse, THE signal SHALL `OK` status üretir

### Gereksinim 5: Hash Includes WindowParams

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, aynı tenant/endpoint/config ile farklı window parametreleri kullanıldığında hash'in değişmesini istiyorum; böylece audit/debug'da hangi pencere ile karar verildiği ayırt edilebilir.

#### Kabul Kriterleri

1. THE `compute_risk_context_hash` fonksiyonu SHALL hash payload'a `window_params` dahil eder
2. WHEN aynı tenant/endpoint/config_hash ile farklı `window_params` verildiğinde, THE hash SHALL farklı değer üretir
3. THE hash payload SHALL `json.dumps(sort_keys=True, separators=(',',':'))` ile canonicalize edilir

### Gereksinim 6: Fail-Open Enforcement

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, yeni decision layer'ın crash olması durumunda mevcut guard davranışının bozulmamasını istiyorum.

#### Kabul Kriterleri

1. THE SnapshotFactory.build() SHALL internal exception durumunda None döner (crash propagate etmez)
2. THE Enforcer SHALL snapshot=None durumunda mevcut guard kararını aynen geçirir (fail-open)
3. THE decision layer SHALL kendi hatalarını loglayıp metrik emit eder (gözlemlenebilir fail-open)

### Gereksinim 7: Bounded Cardinality

**Kullanıcı Hikayesi:** Bir SRE mühendisi olarak, yeni katmanın ürettiği label/reason değerlerinin bounded olmasını istiyorum; Prometheus cardinality patlaması riski olmamalı.

#### Kabul Kriterleri

1. THE SignalStatus enum SHALL yalnızca 3 değer içerir: OK, STALE, INSUFFICIENT
2. THE GuardSignal.name alanı SHALL bounded string enum'dan gelir (CONFIG_FRESHNESS, CB_MAPPING); free-form string kabul edilmez
3. THE GuardSignal.reason_code alanı SHALL bounded enum'dan gelir

### Gereksinim 8: Enforcement Semantics

**Kullanıcı Hikayesi:** Bir geliştirici olarak, snapshot'taki derived flags'e göre enforcement kararının deterministik olmasını istiyorum.

#### Kabul Kriterleri

1. WHEN `guard_deny_reason` is not None, THE Enforcer SHALL mevcut deny semantiğini aynen uygular (RATE_LIMITED → 429, KILL_SWITCHED → 503, vb.)
2. WHEN `guard_deny_reason` is None AND `derived_has_insufficient` is True, THE Enforcer SHALL `BLOCK_INSUFFICIENT` kararı üretir (503)
3. WHEN `guard_deny_reason` is None AND `derived_has_stale` is True, THE Enforcer SHALL `BLOCK_STALE` kararı üretir (503)
4. WHEN `guard_deny_reason` is None AND no derived flags, THE Enforcer SHALL `ALLOW` kararı üretir
5. THE Enforcer SHALL pure function olarak implement edilir (side-effect yok; HTTP response üretimi middleware wiring task'ında)
