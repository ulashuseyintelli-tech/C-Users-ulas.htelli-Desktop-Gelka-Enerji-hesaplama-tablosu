# Gereksinimler: Concurrency PBT — Guard Decision Layer

## Genel Bakış

Guard Decision Layer'ın eşzamanlı (concurrent) request'ler altında doğruluk garantilerini Hypothesis property-based testing ile kanıtlar. Tenant-enable spec'inin üzerine inşa edilir; temel hedef "tenant isolation under concurrency".

## Gereksinimler

### C1 — Request Isolation
- C1.1: Eşzamanlı request'lerin snapshot'ları birbirine sızmaz.
- C1.2: Her snapshot'ın `tenant_id`, `tenant_mode`, `risk_context_hash`, `reasonCodes` alanları yalnızca kendi request'inin input'larını yansıtır.
- C1.3: Farklı tenant'ların mode'ları eşzamanlı request'lerde karışmaz.

### C2 — Determinism Under Fixed Inputs
- C2.1: Aynı `tenant_id` + aynı `endpoint` + aynı `config` + aynı `now_ms` + aynı `windowParams` ile üretilen snapshot her zaman aynı `risk_context_hash` üretir.
- C2.2: Bu garanti concurrent execution altında da geçerlidir (N=50 paralel build → tüm hash'ler eşit).

### C3 — No Shared Mutable State
- C3.1: `SnapshotFactory.build()` ve `evaluate()` path'i global mutable state'e yazmaz.
- C3.2: Frozen dataclass garantisi concurrent access altında da korunur.

### C4 — Mode Resolution Stability
- C4.1: Request başladıktan sonra env/config değişse bile `snapshot.tenant_mode` değişmez.
- C4.2: Snapshot freeze semantiği mid-flight config mutation'a karşı dayanıklıdır.

### C5 — Metrics Monotonicity
- C5.1: `*_total` counter'lar concurrent increment altında monoton artar (process içinde).
- C5.2: Counter değeri hiçbir zaman azalmaz (non-decreasing guarantee).

### C6 — Middleware Bypass Correctness
- C6.1: OpsGuard deny path'te (rate limit → 429) decision layer concurrency altında da bypass olur.
- C6.2: Eşzamanlı deny + allow request'leri birbirini etkilemez.

### C7 — Failure Containment
- C7.1: `SnapshotFactory.build()` crash'i (injected) concurrent load altında bile passthrough'u bozmaz.
- C7.2: Crash rate `snapshot_build_failures_total` counter'ında metriklenir.
- C7.3: Sistem deadlock olmaz; crash'li request'ler diğer request'leri bloklamaz.
