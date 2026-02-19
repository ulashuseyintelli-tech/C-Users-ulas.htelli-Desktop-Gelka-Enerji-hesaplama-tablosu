# PTF Admin — Mimari Genel Bakış

**Versiyon:** 1.0.0 · **Tarih:** 2026-02-19

---

## Sistem Özeti

PTF Admin, fatura analizi, piyasa fiyatı yönetimi ve PDF render işlemlerini kapsayan bir platform servisidir. Üzerine operasyonel koruma katmanı (guard chain), deterministik release governance ve derin gözlemlenebilirlik (observability) inşa edilmiştir.

Temel tasarım prensipleri:
- **Fail-closed** yüksek riskli yollar için (bulk import, write path)
- **Fail-open** standart okuma yolları için (operasyonel dayanıklılık)
- **Deterministik kararlar** — aynı girdi → aynı çıktı (guard, release, policy)
- **Feedback-first** — önce metrik yaz, sonra guard yaz, sonra alert bağla

---

## İstek Akışı (Request Flow)

```mermaid
flowchart TD
    REQ["HTTP Request"] --> MW_METRICS["MetricsMiddleware\n(timer + endpoint normalization)"]
    MW_METRICS --> MW_GUARD["OpsGuardMiddleware\n(Guard Chain)"]

    MW_GUARD --> KS{"Kill-Switch\ncheck"}
    KS -->|deny| R503_KS["503 KILL_SWITCHED"]
    KS -->|pass| RL{"Rate Limiter\ncheck"}
    RL -->|deny| R429["429 RATE_LIMITED\n+ Retry-After"]
    RL -->|pass| CB{"Circuit Breaker\npre-check"}
    CB -->|deny| R503_CB["503 CIRCUIT_OPEN"]
    CB -->|pass| GD["GuardDecisionMiddleware\n(shadow / enforce)"]

    GD --> HANDLER["Endpoint Handler\n(Business Logic)"]
    HANDLER --> DEP_WRAP["Dependency Wrapper\n(retry + timeout + CB)"]
    DEP_WRAP --> DOWNSTREAM["Downstream\n(DB, Cache, API)"]

    HANDLER --> RESP["HTTP Response"]
    RESP --> MW_METRICS
```

Guard zinciri sırası sabittir (HD-2): **KillSwitch → RateLimiter → CircuitBreaker → Handler**. İlk deny kazanır; sonraki guard'lar değerlendirilmez.

---

## Alt Sistem Haritası

```mermaid
graph LR
    subgraph RUNTIME["Runtime Guard Chain"]
        GC["GuardConfig\n(OPS_GUARD_* env)"]
        KS2["KillSwitch"]
        RL2["RateLimitGuard"]
        CB2["CircuitBreaker"]
        GD2["GuardDecision\n(snapshot + enforce)"]
        DW["DependencyWrapper\n(retry + timeout)"]
    end

    subgraph RELEASE["Release Governance"]
        RP["ReleasePolicy\n(OK/HOLD/BLOCK)"]
        RR["ReleaseReport\n(audit artifact)"]
        RG["ReleaseGate\n(enforcement hook)"]
        PF["Preflight\n(CI entegrasyon)"]
    end

    subgraph PDF["PDF Worker"]
        API["PDF API\n(POST/GET/download)"]
        JS["PdfJobStore\n(state machine)"]
        AS["PdfArtifactStore\n(storage backend)"]
        WK["PdfRenderWorker\n(async processor)"]
    end

    subgraph OBS["Observability"]
        PTF["PTFMetrics\n(prom-client registry)"]
        MW["MetricsMiddleware"]
        GM["GateMetricStore\n(pure Python)"]
        PM["PreflightMetrics\n(pure Python)"]
    end

    subgraph MON["Monitoring Stack"]
        PROM["Prometheus\n(ptf-admin-alerts.yml)"]
        GRAF["Grafana\n(4 dashboard)"]
        RUN["Runbook\n(ptf-admin-runbook.md)"]
    end

    GC --> KS2 & RL2 & CB2 & GD2
    PTF --> PROM --> GRAF
    PTF --> RUN
    GM --> PROM
    PM --> PROM
```

---

## Bileşen Detayları

### 1. Runtime Guard Chain

| Bileşen | Dosya | Görevi | Fail Modu |
|---------|-------|--------|-----------|
| GuardConfig | `guard_config.py` | Merkezi yapılandırma (Pydantic Settings) | Geçersiz config → fallback defaults + metrik |
| KillSwitch | `kill_switch.py` | Global/tenant/degrade mode kontrol | High-risk: fail-closed · Standard: fail-open |
| RateLimitGuard | `guards/rate_limit_guard.py` | Endpoint-kategori bazlı hız sınırlama | Fail-closed (HD-1) |
| CircuitBreaker | `guards/circuit_breaker.py` | Bağımlılık sağlık izleme (closed→open→half-open) | CB açık → 503 |
| GuardDecision | `guards/guard_decision.py` | İmmutable snapshot + sinyal değerlendirme | Shadow: log only · Enforce: 503 |
| DependencyWrapper | `guards/dependency_wrapper.py` | Retry + timeout + CB enforcement | Fail-open (DW-3) |
| OpsGuardMiddleware | `ops_guard_middleware.py` | Guard zinciri orkestrasyon | İlk deny kazanır |

**Endpoint Sınıfları:**
- `HIGH_RISK` (bulk import, apply) → guard hatası = **503 (fail-closed)**
- `STANDARD` (read, lookup, upsert) → guard hatası = **pass (fail-open)**

### 2. Release Governance

```mermaid
flowchart LR
    SIGNALS["Tier Results\nFlake Sentinel\nDrift Monitor\nPolicy Canary\nOps Gate"] --> RP2["ReleasePolicy"]
    RP2 -->|OK / HOLD / BLOCK| RG2["ReleaseGate"]
    RG2 -->|allowed / denied| AUDIT["Audit Log\n(JSON artifact)"]
    RG2 --> METRICS2["GateMetricStore\n(decision + breach + audit fail)"]
    RG2 --> PF2["Preflight CLI\n(CI exit code)"]
```

| Verdict | Exit Code | Override |
|---------|-----------|---------|
| OK | 0 | Gereksiz |
| HOLD + override | 0 | Uygulandı |
| HOLD (no override) | 1 | Sağlanmadı |
| BLOCK | 2 | İmkansız (ABSOLUTE) |

**Mutlak Bloklar** (override edilemez): `GUARD_VIOLATION`, `OPS_GATE_FAIL`

**R3 İnvariantı:** Audit yazılamadığında gate `allowed=False` döner — "kanıt yoksa izin yok."

### 3. PDF Worker

```mermaid
stateDiagram-v2
    [*] --> QUEUED: POST /pdf/jobs
    QUEUED --> RUNNING: Worker picks up
    RUNNING --> SUCCEEDED: Render OK
    RUNNING --> FAILED: Error (bounded retry)
    FAILED --> QUEUED: Retry (max 3)
    FAILED --> [*]: Max retry exceeded
    SUCCEEDED --> [*]: Download available
```

| Özellik | Değer |
|---------|-------|
| Template allowlist | `PDF_TEMPLATE_ALLOWLIST` env (prod zorunlu) |
| Payload limit | 256 KB (default) |
| Max retry | 3 |
| Artifact TTL | Configurable cleanup |
| Dedup | Aynı template+payload → mevcut job döner |

### 4. Metrics & Telemetry

**Namespace:** Tüm metrikler `ptf_admin_` prefix'i kullanır.

| Kategori | Örnek Metrikler | Kaynak |
|----------|----------------|--------|
| API | `api_request_total{endpoint, method, status_class}` | MetricsMiddleware |
| Business | `upsert_total{status}`, `import_rows_total{outcome}` | Endpoint handlers |
| Guard | `killswitch_state{switch_name}`, `circuit_breaker_state{dependency}` | Guard bileşenleri |
| SLO | `slo_violation_total{slo_name}` | SLI calculator |
| PDF | `pdf_jobs_total{status}`, `pdf_queue_depth` | PDF API + Worker |
| Release Gate | `release_gate_decision_total{decision}` | GateMetricStore (pure Python) |
| Preflight | `release_preflight_verdict_total{verdict}` | PreflightMetrics (pure Python) |
| Frontend | `frontend_events_total{event_name}` | Event ingestion API |

**Label Politikası (HD-5):**
- `status_class`: 2xx/3xx/4xx/5xx/0xx (exact code YASAK)
- `endpoint`: route template (raw URL path YASAK)
- `dependency`: sabit enum (5 değer)
- `user_id`, `tenant_id`, `period` gibi yüksek kardinalite label'lar YASAK

### 5. Monitoring Stack

**Prometheus Alert Grupları** (`ptf-admin-alerts.yml`):

| Grup | Alert Sayısı | Kapsam |
|------|-------------|--------|
| `ptf-admin-alerts` | 8 | API hataları, latency, import kalitesi |
| `ptf-admin-preflight-guardrails` | 4 | Contract breach, BLOCK spike, telemetry write fail |
| `ptf-admin-ops-guard` | 7 | Kill-switch, CB, rate limit, SLO burn-rate |
| `ptf-admin-dependency-health` | 8 | Dependency timeout/failure/retry, CB open |
| `ptf-admin-guard-decision` | 3 | Snapshot build failure, block rate, silent layer |
| `ptf-admin-release-gate` | 3 | Contract breach, audit fail, DENY spike |
| `ptf-admin-pdf-worker` | 4+ | Queue unavailable, failure spike, stuck jobs |

**Grafana Dashboard'ları** (`monitoring/grafana/`):

| Dashboard | Paneller |
|-----------|---------|
| `ptf-admin-dashboard.json` | Golden signals, guard state, error taksonomisi |
| `pdf-worker-dashboard.json` | Job queue, success/failure, duration dağılımı |
| `preflight-dashboard.json` | Verdict dağılımı, override geçmişi |
| `release-gate-dashboard.json` | ALLOW/DENY trend, top deny reasons, breach count |

**Runbook:** `monitoring/runbooks/ptf-admin-runbook.md` — her P0/P1 alert için: belirti, hızlı tanı, müdahale, kurtarma, postmortem verisi.

---

## Middleware Yığını (Execution Order)

```
1. CORSMiddleware          — CORS headers
2. MetricsMiddleware       — Request count + duration + endpoint normalization
3. GuardDecisionMiddleware — Immutable snapshot + shadow/enforce (opsiyonel)
4. OpsGuardMiddleware      — KillSwitch → RateLimiter → CircuitBreaker
5. Endpoint Handler        — Business logic
```

---

## Dosya Haritası (Özet)

```
backend/app/
├── main.py                          # FastAPI app, middleware wiring, startup
├── ptf_metrics.py                   # Prometheus metrik registry (singleton)
├── metrics_middleware.py            # HTTP request metrics collection
├── ops_guard_middleware.py          # Guard chain orchestration
├── guard_config.py                  # Centralized guard config (Pydantic)
├── kill_switch.py                   # Kill-switch state + admin API
├── endpoint_normalization.py        # 3-level endpoint label fallback
├── pdf_api.py                       # PDF job endpoints
├── guards/
│   ├── guard_decision.py            # Snapshot factory + signal producers
│   ├── guard_decision_middleware.py  # Guard Decision Layer middleware
│   ├── circuit_breaker.py           # CB state machine
│   ├── rate_limit_guard.py          # Endpoint-category rate limiter
│   ├── dependency_wrapper.py        # Retry + timeout + CB wrapper
│   └── endpoint_dependency_map.py   # Endpoint → dependency mapping
├── services/
│   ├── pdf_job_store.py             # PDF job state persistence
│   └── pdf_artifact_store.py        # PDF file storage
└── testing/
    ├── release_policy.py            # Deterministic release decision
    ├── release_gate.py              # Enforcement + override validation
    ├── release_report.py            # Audit artifact generation
    ├── gate_metrics.py              # Release gate telemetry (pure Python)
    ├── preflight_metrics.py         # Preflight telemetry (pure Python)
    └── chaos_harness.py             # Fault injection framework

monitoring/
├── prometheus/ptf-admin-alerts.yml  # 37+ alert kuralı (7 grup)
├── grafana/                         # 4 dashboard JSON
└── runbooks/ptf-admin-runbook.md    # Operasyonel runbook
```

---

## Tasarım Kararları Referans Tablosu

| Karar | Değer | Gerekçe |
|-------|-------|---------|
| Guard sırası | KS → RL → CB | Devre dışı endpoint'e rate limit penceresi açılmamalı |
| High-risk fail modu | Fail-closed (503) | Kontrolsüz bulk write prod veriyi bozabilir |
| Standard fail modu | Fail-open | Read/tekil write durdurulmamalı |
| Release ABSOLUTE_BLOCK | Override edilemez | GUARD_VIOLATION + OPS_GATE_FAIL sözleşme ihlali |
| Audit fail → deny | R3 invariantı | Kanıt yoksa izin yok |
| Shadow → Enforce rollout | Güvenli geçiş | False positive tespiti sonra enforcement |
| Label cardinality budget | HD-5 tablosu | Prometheus'ta kardinalite patlaması önlemi |
| Config invalid → fallback | Asla reject etme | Reject prod'u config hatasında kilitler |

---

## Test Katmanları

| Katman | Araç | Kapsam |
|--------|------|--------|
| Unit | pytest | Bireysel bileşenler (guard, metrics, policy) |
| Property-Based | Hypothesis | Determinizm, monotoniklik, round-trip, cardinality |
| Integration | pytest + TestClient | Middleware wiring, E2E akışlar |
| Load | Custom harness | Concurrency, SLO doğrulama |
| Chaos | ChaosHarness | Fault injection (time, IO, split-brain) |
| Monitoring | pytest | Alert kuralları, dashboard yapısı, runbook kapsam |


---

## Prod Rollout Checklist

Detaylı checklist: [`monitoring/runbooks/ptf-admin-runbook.md` → "Prod Rollout Checklist"](../monitoring/runbooks/ptf-admin-runbook.md)

Özet deploy sırası:
1. **Monitoring önce** — Prometheus targets, Grafana dashboards, alert rules
2. **Worker önce** — PDF worker up, queue depth 0, Playwright stabil
3. **API sonra** — Backend deploy, `/health/ready` → 200, frontend deploy
4. **Shadow → Enforce** — 24 saat shadow data, false positive yok, sonra enforce

---

## Yük Testi Planı

Detaylı plan: [`monitoring/runbooks/ptf-admin-runbook.md` → "Yük Testi Planı"](../monitoring/runbooks/ptf-admin-runbook.md)

5 senaryo, 2 katman:

| Senaryo | Katman | Hedef |
|---------|--------|-------|
| S1: PDF Steady-State | k6 HTTP | p95 baseline, failure rate < %2 |
| S2: PDF Burst/Backlog | k6 HTTP | Queue drain davranışı, backlog alert |
| S3: PDF Retry Injection | k6 HTTP | Retry bounded (max 2), failure taxonomy |
| S4: API + Guard | In-process + k6 | Shadow/enforce latency impact, risk class |
| S5: Dependency Outage | In-process | CB open, rate limit, fail-open doğrulaması |

---

## Yük Testi Sonuç Raporu Şablonu

Detaylı şablon: [`monitoring/runbooks/ptf-admin-runbook.md` → "Yük Testi Sonuç Raporu Şablonu"](../monitoring/runbooks/ptf-admin-runbook.md)

Tek sayfalık go/no-go raporu: üstte karar + kabul kontrolleri, altta PDF metrikleri, guard metrikleri, alert gözlemleri, güvenilirlik kontrolleri (stuck/memory/TTL), ekler ve aksiyonlar.

k6 script'leri: [`k6/pdf_jobs.js`](../k6/pdf_jobs.js) (S1/S2/S3), [`k6/api_mix.js`](../k6/api_mix.js) (S4).
