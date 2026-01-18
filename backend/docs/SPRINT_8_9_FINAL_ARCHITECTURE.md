# Sprint 8.9 Final Architecture

## Tarih: 2026-01-18
## Durum: PRODUCTION READY

---

## 1. Executive Summary

Sprint 8.9 ile sistem **production-ready** durumuna ulaştı:

| Metrik | Değer | Durum |
|--------|-------|-------|
| Test sayısı | 511 | ✅ Yeşil |
| Config validation | 8 invariant | ✅ Aktif |
| Kill switch | PILOT_ENABLED | ✅ Implemente |
| Post-deploy script | post_deploy_check.py | ✅ Hazır |
| RC Runbook | Detaylı | ✅ Güncel |
| Rollback prosedürü | Tanımlı | ✅ Dokümante |

---

## 2. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           GELKA ENERJİ SYSTEM                               │
│                         Sprint 8.9 - Production Ready                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │   Frontend  │───▶│   FastAPI   │───▶│   OpenAI    │───▶│  Database   │  │
│  │   (React)   │    │   Backend   │    │   Vision    │    │  (SQLite)   │  │
│  └─────────────┘    └──────┬──────┘    └─────────────┘    └─────────────┘  │
│                            │                                                │
│                            ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                      CORE MODULES                                     │  │
│  ├──────────────────────────────────────────────────────────────────────┤  │
│  │                                                                       │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │  │
│  │  │  Extractor  │  │ Calculator  │  │  Validator  │  │   Config    │  │  │
│  │  │  (OCR/LLM)  │  │  (Pricing)  │  │  (Quality)  │  │ (Thresholds)│  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │  │
│  │                                                                       │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    INCIDENT MANAGEMENT                                │  │
│  ├──────────────────────────────────────────────────────────────────────┤  │
│  │                                                                       │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │  │
│  │  │  Incident   │  │   Action    │  │   Retry     │  │  Recompute  │  │  │
│  │  │  Service    │  │   Router    │  │ Orchestrator│  │   Service   │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │  │
│  │                                                                       │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │  │
│  │  │  Incident   │  │   Issue     │  │  Incident   │  │  Incident   │  │  │
│  │  │ Repository  │  │  Reporter   │  │   Digest    │  │   Metrics   │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │  │
│  │                                                                       │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    PRODUCTION SAFETY (Sprint 8.9)                     │  │
│  ├──────────────────────────────────────────────────────────────────────┤  │
│  │                                                                       │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │  │
│  │  │   Pilot     │  │   Config    │  │  /health/   │  │ Post-Deploy │  │  │
│  │  │   Guard     │  │ Validation  │  │   ready     │  │   Check     │  │  │
│  │  │ (Kill Sw.)  │  │ (8 Invar.)  │  │ (5 Checks)  │  │  (Script)   │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  │  │
│  │                                                                       │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Module Inventory

### 3.1 Core Modules

| Modül | Dosya | Sorumluluk |
|-------|-------|------------|
| Config | `config.py` | Tüm threshold'lar, validation, hash |
| Extractor | `extractor.py` | OCR/LLM ile fatura analizi |
| Calculator | `calculator.py` | Teklif hesaplama |
| Validator | `validator.py` | Extraction kalite kontrolü |

### 3.2 Incident Management

| Modül | Dosya | Sorumluluk |
|-------|-------|------------|
| Incident Service | `incident_service.py` | Quality flags, categories, actions |
| Incident Keys | `incident_keys.py` | dedupe_key_v2, invoice_hash |
| Incident Repository | `incident_repository.py` | DB CRUD, status transitions |
| Action Router | `action_router.py` | Route to status/payload |
| Issue Payload | `issue_payload.py` | PII-safe payload builder |
| Issue Reporter | `issue_reporter.py` | External issue tracker |
| Retry Executor | `retry_executor.py` | Retry mechanics |
| Retry Orchestrator | `retry_orchestrator.py` | Retry + Recompute koordinasyonu |
| Recompute Service | `recompute_service.py` | Quality recompute (tek RESOLVED otoritesi) |
| Incident Digest | `incident_digest.py` | Daily digest & KPI queries |
| Incident Metrics | `incident_metrics.py` | System health, drift detection |

### 3.3 Production Safety (Sprint 8.9)

| Modül | Dosya | Sorumluluk |
|-------|-------|------------|
| Pilot Guard | `pilot_guard.py` | Kill switch, tenant isolation, rate limit |
| Config Validation | `config.py` | 8 invariant kontrolü |
| Health Ready | `main.py` | 5 check (config, db, openai, queue, pilot) |
| Post-Deploy Check | `scripts/post_deploy_check.py` | CI/CD validation script |

---

## 4. Data Flow

### 4.1 Invoice Processing Flow

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│ Upload  │───▶│ Extract │───▶│Validate │───▶│Calculate│───▶│ Store   │
│  PDF    │    │  (LLM)  │    │(Quality)│    │ (Offer) │    │  (DB)   │
└─────────┘    └─────────┘    └────┬────┘    └─────────┘    └─────────┘
                                   │
                                   ▼
                            ┌─────────────┐
                            │  Incident?  │
                            │ (Mismatch)  │
                            └──────┬──────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
              ┌─────────┐   ┌─────────┐   ┌─────────┐
              │   S1    │   │   S2    │   │   OK    │
              │(Severe) │   │(Normal) │   │(No Inc.)│
              └────┬────┘   └────┬────┘   └─────────┘
                   │             │
                   ▼             ▼
              ┌─────────────────────┐
              │   Action Router     │
              │ (VERIFY_OCR, etc.)  │
              └─────────────────────┘
```

### 4.2 Incident Lifecycle

```
┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
│  OPEN   │───▶│ PENDING │───▶│ PENDING │───▶│RESOLVED │
│         │    │  RETRY  │    │RECOMPUTE│    │         │
└─────────┘    └────┬────┘    └────┬────┘    └─────────┘
                    │              │
                    │              ▼
                    │        ┌─────────┐
                    │        │Reclassify│
                    │        │(New Flag)│
                    │        └────┬────┘
                    │             │
                    ▼             ▼
              ┌─────────────────────┐
              │   OPEN (Exhausted)  │
              │   action=BUG_REPORT │
              └─────────────────────┘
```

---

## 5. Configuration Architecture

### 5.1 Threshold Hierarchy

```python
THRESHOLDS
├── Mismatch
│   ├── RATIO: 0.05 (5%)
│   ├── ABSOLUTE: 50.0 TL
│   ├── SEVERE_RATIO: 0.20 (20%)
│   ├── SEVERE_ABSOLUTE: 500.0 TL
│   ├── ROUNDING_DELTA: 10.0 TL
│   └── ROUNDING_RATIO: 0.005 (0.5%)
├── Drift
│   ├── MIN_SAMPLE: 20
│   ├── MIN_ABSOLUTE_DELTA: 5
│   ├── RATE_MULTIPLIER: 2.0
│   └── TOP_OFFENDERS_MIN_INVOICES: 20
├── Alert
│   ├── BUG_REPORT_RATE: 0.10 (10%)
│   ├── EXHAUSTED_RATE: 0.20 (20%)
│   ├── STUCK_COUNT: 1
│   └── RECOMPUTE_LIMIT: 1
├── Recovery
│   └── STUCK_MINUTES: 10
├── Validation
│   ├── LOW_CONFIDENCE: 0.6
│   ├── MIN_UNIT_PRICE: 0.5 TL/kWh
│   ├── MAX_UNIT_PRICE: 15.0 TL/kWh
│   ├── MIN_DIST_PRICE: 0.0 TL/kWh
│   ├── MAX_DIST_PRICE: 5.0 TL/kWh
│   ├── LINE_CONSISTENCY_TOLERANCE: 2.0%
│   ├── HARD_STOP_DELTA: 20.0%
│   └── ENERGY_CROSSCHECK_TOLERANCE: 5.0%
└── Feedback
    └── ROOT_CAUSE_MAX_LENGTH: 200
```

### 5.2 Config Validation Invariants

| # | Invariant | Kontrol |
|---|-----------|---------|
| I1 | SEVERE_RATIO >= RATIO | S1 escalation S2'den sonra |
| I2 | SEVERE_ABSOLUTE >= ABSOLUTE | S1 escalation S2'den sonra |
| I3 | ROUNDING_RATIO < RATIO | Rounding gerçek mismatch'i yutmasın |
| I4 | MIN_UNIT_PRICE < MAX_UNIT_PRICE | Geçerli aralık |
| I5 | MIN_DIST_PRICE < MAX_DIST_PRICE | Geçerli aralık |
| I6 | HARD_STOP_DELTA >= SEVERE_RATIO * 100 | Çakışan alarm önleme |
| I7 | Tüm threshold'lar > 0 | Sıfır/negatif anlamsız |
| I8 | 0 < LOW_CONFIDENCE < 1 | Confidence [0,1] aralığında |

---

## 6. Production Safety Features

### 6.1 Pilot Guard

```python
# Environment Variables
PILOT_ENABLED=true|false      # Kill switch (default: true)
PILOT_TENANT_ID=pilot         # Tenant isolation (default: "pilot")
PILOT_MAX_INVOICES_PER_HOUR=50  # Rate limit (default: 50)

# Usage
from app.pilot_guard import is_pilot_enabled, is_pilot_tenant

if not is_pilot_enabled():
    return  # Skip pilot processing

if is_pilot_tenant(tenant_id):
    check_pilot_rate_limit()  # Raises if exceeded
```

### 6.2 /health/ready Endpoint

```json
{
  "status": "ready",
  "timestamp": "2026-01-18T10:00:00Z",
  "build_id": "git:abc1234",
  "config_hash": "sha256:7f3a2b1c",
  "checks": {
    "config": {"status": "ok", "validated": true},
    "database": {"status": "ok", "latency_ms": 5},
    "openai_api": {"status": "ok", "key_configured": true},
    "queue": {"status": "ok", "depth": 0}
  },
  "pilot": {
    "enabled": true,
    "tenant_id": "pilot",
    "rate_limit": {"current": 5, "limit": 50, "remaining": 45}
  }
}
```

### 6.3 Post-Deploy Check Script

```bash
# Exit codes
0 = All passed → Deploy successful
1 = Ready fail → ROLLBACK
2 = Smoke fail → ROLLBACK
3 = Feedback fail → INVESTIGATE
4 = Partial success → INVESTIGATE

# Usage
export API_BASE_URL="https://api.example.com"
python scripts/post_deploy_check.py
echo $?
```

---

## 7. Test Coverage

### 7.1 Test Distribution

| Kategori | Test Sayısı | Dosya |
|----------|-------------|-------|
| Config Validation | 20 | test_config.py |
| Pilot Guard | 15 | test_pilot_guard.py |
| Calculator Properties | 12 | test_calculator_properties.py |
| Incident Service | 25+ | test_incident_*.py |
| Action Router | 15+ | test_action_router.py |
| E2E Smoke | 10+ | test_e2e_smoke.py |
| **TOPLAM** | **511** | |

### 7.2 Test Türleri

- Unit tests: Fonksiyon seviyesi
- Property-based tests: Hypothesis ile
- Golden tests: Fixture-based regression
- E2E smoke tests: Full pipeline
- Contract tests: API/DB contracts

---

## 8. Deployment Architecture

### 8.1 Environment Variables

```bash
# Core
ENV=production|staging|development
OPENAI_API_KEY=sk-...
DATABASE_URL=sqlite:///gelka_enerji.db

# Security
API_KEY=...
API_KEY_ENABLED=true
ADMIN_API_KEY=...
ADMIN_API_KEY_ENABLED=true

# Pilot (Sprint 8.9)
PILOT_ENABLED=true
PILOT_TENANT_ID=pilot
PILOT_MAX_INVOICES_PER_HOUR=50

# Build
BUILD_ID=git:abc1234  # CI/CD sets this
```

### 8.2 Startup Sequence

```
1. Config validation (MUST pass)
2. ENV whitelist check
3. Production guard check
4. Database init
5. Pilot guard config log
6. Market prices seed
7. Distribution tariffs seed
8. Ready to accept traffic
```

---

## 9. Rollback Procedure

### 9.1 Rollback Triggers

| Koşul | Eşik | Aksiyon |
|-------|------|---------|
| /health/ready 503 | > 5 dakika | ROLLBACK |
| Queue stuck | > 15 dakika | ROLLBACK |
| 5xx error rate | > 5% | ROLLBACK |
| S1 rate spike | > 30% (n>=20) | INVESTIGATE → ROLLBACK |

### 9.2 Rollback Steps

```
1. KILL SWITCH: PILOT_ENABLED=false
2. ROLLBACK DEPLOY: kubectl rollout undo
3. VERIFY: GET /health/ready → 200
4. QUEUE DRAIN: Watch queue depth
5. POSTMORTEM: Log + metric snapshot
```

---

## 10. Sprint History

| Sprint | Tarih | Odak |
|--------|-------|------|
| 8.0 | 2026-01 | Tek otorite RESOLVED refactor |
| 8.1 | 2026-01 | Recompute limit guard |
| 8.2 | 2026-01 | Daily digest & KPI queries |
| 8.8 | 2026-01 | Config consolidation, /health/ready |
| 8.9 | 2026-01 | RC + Pilot + Gözlem |
| 8.9.1 | 2026-01-18 | PILOT_ENABLED + post_deploy_check.py |

---

## 11. Next Steps (Sprint 9+)

**Pilot verisi ile tetiklenecek:**
- Threshold tuning (gerçek mismatch oranlarına göre)
- Alert kanalları (Slack/email)
- Dashboard UI
- Performance optimization

**Yapılmayacaklar (şimdilik):**
- Yeni feature
- Preventive fix
- Speculative optimization

---

## 12. Approval

| Rol | İsim | Tarih | Onay |
|-----|------|-------|------|
| Developer | - | 2026-01-18 | ✅ |
| Reviewer | - | - | ⏳ |
| Product Owner | - | - | ⏳ |

---

**Bu mimari dokümanı, Sprint 8.9.1 tamamlandıktan sonra oluşturulmuştur.**
**Tüm kod implementasyonları test edilmiş ve çalışır durumdadır.**
