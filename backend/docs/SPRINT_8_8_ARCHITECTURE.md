# Sprint 8.8: Prod Readiness + Observability Hardening

## Tarih: 2026-01-17
## Durum: TASARIM AŞAMASI

---

## 1. Goals & Non-Goals

### 1.1 Goals (Sprint Bittiğinde)

**Ana Hedef:** "Prod'da 1 gün çalışınca, 'sistem çalıştı mı / saçmaladı mı / ne kadar incident var / feedback geliyor mu' soruları 30 saniyede cevaplanıyor."

| # | Hedef | Ölçüm |
|---|-------|-------|
| G1 | Config tek kaynaktan yönetiliyor | Grep ile hard-coded threshold = 0 |
| G2 | Bozuk config prod'a giremiyor | Startup validation fail → app başlamıyor |
| G3 | Sistem sağlığı tek bakışta görülüyor | `/health/ready` + run summary |
| G4 | E2E smoke test var | 3 fixture → full pipeline → assertions |
| G5 | Operasyonel runbook var | 5 senaryo için aksiyon planı |

### 1.2 Non-Goals (Bu Sprint'te Yapılmayacak)

| # | Kapsam Dışı | Neden |
|---|-------------|-------|
| NG1 | Yeni feature | Prod veri olmadan tahmin |
| NG2 | Performans optimizasyonu | Ölçmeden yapılmaz |
| NG3 | Test genişletme (490 → 600) | Mevcut coverage yeterli |
| NG4 | UI/Dashboard | Backend-first, UI sonra |

---

## 2. Config Consolidation Plan

### 2.1 Kural: Tek Kaynak + Tek Import

```
❌ YANLIŞ: Her dosyada kendi sabiti
   incident_service.py: ROUNDING_DELTA_THRESHOLD = 10.0
   calculator.py: TOTAL_MISMATCH_RATIO_THRESHOLD = 0.05

✅ DOĞRU: Tek kaynak, tek import
   config.py: class Thresholds: ROUNDING_DELTA = 10.0
   incident_service.py: from .config import Thresholds
```

**Enforcement:** CI/CD'de grep gate:
```bash
# Hard-coded threshold yasak
grep -rn "THRESHOLD\s*=" --include="*.py" | grep -v "config.py" | grep -v "test_"
# Çıktı boş olmalı
```

### 2.2 Config Mapping Table

| Eski Konum | Eski Sabit | Yeni Config Path |
|------------|------------|------------------|
| `incident_service.py` | `ROUNDING_DELTA_THRESHOLD` | `Thresholds.Mismatch.ROUNDING_DELTA` |
| `incident_service.py` | `ROUNDING_RATIO_THRESHOLD` | `Thresholds.Mismatch.ROUNDING_RATIO` |
| `calculator.py` | `TOTAL_MISMATCH_RATIO_THRESHOLD` | `Thresholds.Mismatch.RATIO` |
| `calculator.py` | `TOTAL_MISMATCH_ABSOLUTE_THRESHOLD` | `Thresholds.Mismatch.ABSOLUTE` |
| `calculator.py` | `TOTAL_MISMATCH_SEVERE_RATIO` | `Thresholds.Mismatch.SEVERE_RATIO` |
| `calculator.py` | `TOTAL_MISMATCH_SEVERE_ABSOLUTE` | `Thresholds.Mismatch.SEVERE_ABSOLUTE` |
| `incident_metrics.py` | `DRIFT_MIN_SAMPLE` | `Thresholds.Drift.MIN_SAMPLE` |
| `incident_metrics.py` | `DRIFT_MIN_ABSOLUTE_DELTA` | `Thresholds.Drift.MIN_ABSOLUTE_DELTA` |
| `incident_metrics.py` | `DRIFT_RATE_MULTIPLIER` | `Thresholds.Drift.RATE_MULTIPLIER` |
| `incident_metrics.py` | `TOP_OFFENDERS_MIN_INVOICES` | `Thresholds.Drift.TOP_OFFENDERS_MIN_INVOICES` |
| `incident_digest.py` | `AlertConfig.bug_report_rate_threshold` | `Thresholds.Alert.BUG_REPORT_RATE` |
| `incident_digest.py` | `AlertConfig.exhausted_rate_threshold` | `Thresholds.Alert.EXHAUSTED_RATE` |
| `incident_digest.py` | `AlertConfig.stuck_count_threshold` | `Thresholds.Alert.STUCK_COUNT` |
| `incident_digest.py` | `AlertConfig.recompute_limit_threshold` | `Thresholds.Alert.RECOMPUTE_LIMIT` |
| `resolution_reasons.py` | `STUCK_THRESHOLD_MINUTES` | `Thresholds.Recovery.STUCK_MINUTES` |
| `validator.py` | `LOW_CONFIDENCE_THRESHOLD` | `Thresholds.Validation.LOW_CONFIDENCE` |
| `validator.py` | `MIN_UNIT_PRICE` | `Thresholds.Validation.MIN_UNIT_PRICE` |
| `validator.py` | `MAX_UNIT_PRICE` | `Thresholds.Validation.MAX_UNIT_PRICE` |
| `validator.py` | `MIN_DIST_PRICE` | `Thresholds.Validation.MIN_DIST_PRICE` |
| `validator.py` | `MAX_DIST_PRICE` | `Thresholds.Validation.MAX_DIST_PRICE` |
| `validator.py` | `LINE_CONSISTENCY_TOLERANCE` | `Thresholds.Validation.LINE_CONSISTENCY_TOLERANCE` |
| `validator.py` | `HARD_STOP_DELTA_THRESHOLD` | `Thresholds.Validation.HARD_STOP_DELTA` |
| `validator.py` | `ENERGY_CROSSCHECK_TOLERANCE` | `Thresholds.Validation.ENERGY_CROSSCHECK_TOLERANCE` |

### 2.3 Config Module Structure

```python
# backend/app/config.py

from dataclasses import dataclass
from typing import Optional
import os


@dataclass(frozen=True)
class MismatchThresholds:
    """Total mismatch detection thresholds."""
    RATIO: float = 0.05              # %5 → S2
    ABSOLUTE: float = 50.0           # 50 TL → S2
    SEVERE_RATIO: float = 0.20       # %20 → S1 escalation
    SEVERE_ABSOLUTE: float = 500.0   # 500 TL → S1 escalation
    ROUNDING_DELTA: float = 10.0     # TL - rounding tolerance
    ROUNDING_RATIO: float = 0.005    # %0.5 - rounding tolerance


@dataclass(frozen=True)
class DriftThresholds:
    """Drift detection thresholds."""
    MIN_SAMPLE: int = 20
    MIN_ABSOLUTE_DELTA: int = 5
    RATE_MULTIPLIER: float = 2.0
    TOP_OFFENDERS_MIN_INVOICES: int = 20


@dataclass(frozen=True)
class AlertThresholds:
    """Alert configuration thresholds."""
    BUG_REPORT_RATE: float = 0.10    # %10
    EXHAUSTED_RATE: float = 0.20     # %20
    STUCK_COUNT: int = 1
    RECOMPUTE_LIMIT: int = 1


@dataclass(frozen=True)
class RecoveryThresholds:
    """Recovery and retry thresholds."""
    STUCK_MINUTES: int = 10


@dataclass(frozen=True)
class ValidationThresholds:
    """Validation thresholds."""
    LOW_CONFIDENCE: float = 0.6
    MIN_UNIT_PRICE: float = 0.5      # TL/kWh
    MAX_UNIT_PRICE: float = 15.0     # TL/kWh
    MIN_DIST_PRICE: float = 0.0      # TL/kWh
    MAX_DIST_PRICE: float = 5.0      # TL/kWh
    LINE_CONSISTENCY_TOLERANCE: float = 2.0   # %2
    HARD_STOP_DELTA: float = 20.0    # %20
    ENERGY_CROSSCHECK_TOLERANCE: float = 5.0  # %5


@dataclass(frozen=True)
class Thresholds:
    """All system thresholds - SINGLE SOURCE OF TRUTH."""
    Mismatch: MismatchThresholds = MismatchThresholds()
    Drift: DriftThresholds = DriftThresholds()
    Alert: AlertThresholds = AlertThresholds()
    Recovery: RecoveryThresholds = RecoveryThresholds()
    Validation: ValidationThresholds = ValidationThresholds()


# Singleton instance
THRESHOLDS = Thresholds()


# Environment config
VALID_ENVIRONMENTS = {"development", "staging", "production"}
```

---

## 3. Config Validation Invariants

### 3.1 Startup Validation Function

```python
# backend/app/config.py (devamı)

class ConfigValidationError(Exception):
    """Raised when config validation fails."""
    pass


def validate_config(thresholds: Thresholds = THRESHOLDS) -> None:
    """
    Validate config invariants at startup.
    
    Raises:
        ConfigValidationError: If any invariant is violated
    """
    errors = []
    m = thresholds.Mismatch
    v = thresholds.Validation
    
    # Invariant 1: Severe thresholds >= normal thresholds
    if m.SEVERE_RATIO < m.RATIO:
        errors.append(
            f"SEVERE_RATIO ({m.SEVERE_RATIO}) must be >= RATIO ({m.RATIO})"
        )
    
    if m.SEVERE_ABSOLUTE < m.ABSOLUTE:
        errors.append(
            f"SEVERE_ABSOLUTE ({m.SEVERE_ABSOLUTE}) must be >= ABSOLUTE ({m.ABSOLUTE})"
        )
    
    # Invariant 2: Rounding threshold < mismatch threshold
    if m.ROUNDING_RATIO >= m.RATIO:
        errors.append(
            f"ROUNDING_RATIO ({m.ROUNDING_RATIO}) must be < RATIO ({m.RATIO}) "
            "to prevent rounding from swallowing real mismatches"
        )
    
    # Invariant 3: Min < Max for ranges
    if v.MIN_UNIT_PRICE >= v.MAX_UNIT_PRICE:
        errors.append(
            f"MIN_UNIT_PRICE ({v.MIN_UNIT_PRICE}) must be < MAX_UNIT_PRICE ({v.MAX_UNIT_PRICE})"
        )
    
    if v.MIN_DIST_PRICE >= v.MAX_DIST_PRICE:
        errors.append(
            f"MIN_DIST_PRICE ({v.MIN_DIST_PRICE}) must be < MAX_DIST_PRICE ({v.MAX_DIST_PRICE})"
        )
    
    # Invariant 4: Hard stop >= severe ratio
    if v.HARD_STOP_DELTA < m.SEVERE_RATIO * 100:
        errors.append(
            f"HARD_STOP_DELTA ({v.HARD_STOP_DELTA}%) must be >= SEVERE_RATIO ({m.SEVERE_RATIO * 100}%) "
            "to avoid conflicting alarms"
        )
    
    # Invariant 5: All positive values
    all_values = [
        ("RATIO", m.RATIO),
        ("ABSOLUTE", m.ABSOLUTE),
        ("ROUNDING_DELTA", m.ROUNDING_DELTA),
        ("MIN_SAMPLE", thresholds.Drift.MIN_SAMPLE),
        ("STUCK_MINUTES", thresholds.Recovery.STUCK_MINUTES),
        ("LOW_CONFIDENCE", v.LOW_CONFIDENCE),
    ]
    for name, value in all_values:
        if value <= 0:
            errors.append(f"{name} ({value}) must be > 0")
    
    # Invariant 6: Confidence in valid range
    if not (0 < v.LOW_CONFIDENCE < 1):
        errors.append(
            f"LOW_CONFIDENCE ({v.LOW_CONFIDENCE}) must be in range (0, 1)"
        )
    
    if errors:
        raise ConfigValidationError(
            f"Config validation failed with {len(errors)} error(s):\n" +
            "\n".join(f"  - {e}" for e in errors)
        )
```

### 3.2 Invariant Summary Table

| # | Invariant | Rationale |
|---|-----------|-----------|
| I1 | `SEVERE_RATIO >= RATIO` | S1 escalation S2'den sonra olmalı |
| I2 | `SEVERE_ABSOLUTE >= ABSOLUTE` | S1 escalation S2'den sonra olmalı |
| I3 | `ROUNDING_RATIO < RATIO` | Rounding gerçek mismatch'i yutmasın |
| I4 | `MIN_UNIT_PRICE < MAX_UNIT_PRICE` | Geçerli aralık |
| I5 | `MIN_DIST_PRICE < MAX_DIST_PRICE` | Geçerli aralık |
| I6 | `HARD_STOP_DELTA >= SEVERE_RATIO` | Çakışan alarm önleme |
| I7 | Tüm threshold'lar > 0 | Sıfır/negatif anlamsız |
| I8 | `0 < LOW_CONFIDENCE < 1` | Confidence [0,1] aralığında |

---

## 4. `/health/ready` Contract

### 4.1 Endpoint Specification

```
GET /health/ready

Response 200 (Ready):
{
  "status": "ready",
  "timestamp": "2026-01-17T15:00:00Z",
  "checks": {
    "database": {"status": "ok", "latency_ms": 5},
    "config": {"status": "ok", "validated": true},
    "openai_api": {"status": "ok", "model": "gpt-4o-2024-08-06"},
    "queue": {"status": "ok", "depth": 0}
  },
  "last_activity": {
    "last_incident_at": "2026-01-17T14:55:00Z",
    "last_extraction_at": "2026-01-17T14:58:00Z"
  }
}

Response 503 (Not Ready):
{
  "status": "not_ready",
  "timestamp": "2026-01-17T15:00:00Z",
  "checks": {
    "database": {"status": "ok", "latency_ms": 5},
    "config": {"status": "error", "message": "SEVERE_RATIO < RATIO"},
    "openai_api": {"status": "ok"},
    "queue": {"status": "warning", "depth": 150, "message": "Queue backlog"}
  },
  "failing_checks": ["config"]
}
```

### 4.2 Check Definitions

| Check | OK Condition | Warning | Error |
|-------|--------------|---------|-------|
| `database` | SELECT 1 < 100ms | 100-500ms | > 500ms veya timeout |
| `config` | `validate_config()` pass | - | Validation fail |
| `openai_api` | API key set + valid | - | Key missing/invalid |
| `queue` | depth = 0 | depth > 0 ama ilerliyor | depth > 0 ve stuck |

### 4.3 Ready vs Alive Farkı

| Endpoint | Amaç | Kullanım |
|----------|------|----------|
| `GET /health` | Alive check | Load balancer, "process çalışıyor mu?" |
| `GET /health/ready` | Ready check | Deployment, "trafik alabilir mi?" |

**Kritik:** Queue depth > 0 ama pipeline ilerlemiyor = "alive" ama "not ready"

---

## 5. E2E Smoke Test Design

### 5.1 Test Fixtures (3 Senaryo)

#### Fixture 1: Happy Path
```python
FIXTURE_HAPPY = {
    "name": "happy_path_invoice",
    "extraction": {
        "consumption_kwh": {"value": 10000, "confidence": 0.95},
        "current_active_unit_price_tl_per_kwh": {"value": 3.5, "confidence": 0.92},
        "invoice_total_with_vat_tl": {"value": 42000, "confidence": 0.98},
        # ... diğer alanlar
    },
    "expected": {
        "incident_created": False,
        "quality_grade": "OK",
        "mismatch": False,
    }
}
```

#### Fixture 2: S2 Mismatch (VERIFY_INVOICE_LOGIC)
```python
FIXTURE_S2_MISMATCH = {
    "name": "s2_mismatch_invoice",
    "extraction": {
        "consumption_kwh": {"value": 10000, "confidence": 0.90},
        "current_active_unit_price_tl_per_kwh": {"value": 3.5, "confidence": 0.88},
        "invoice_total_with_vat_tl": {"value": 50000, "confidence": 0.95},
        # computed_total ≈ 42000, delta = 8000, ratio = 16%
    },
    "expected": {
        "incident_created": True,
        "severity": "S2",
        "primary_flag": "INVOICE_TOTAL_MISMATCH",
        "action_hint": {
            "action_class": "VERIFY_INVOICE_LOGIC",
            "primary_suspect": "INVOICE_LOGIC",
        },
        "mismatch_info": {
            "has_mismatch": True,
            "severity": "S2",
        }
    }
}
```

#### Fixture 3: S1 + OCR Suspect (VERIFY_OCR)
```python
FIXTURE_S1_OCR = {
    "name": "s1_ocr_suspect_invoice",
    "extraction": {
        "consumption_kwh": {"value": 10000, "confidence": 0.55},  # Low confidence!
        "current_active_unit_price_tl_per_kwh": {"value": 3.5, "confidence": 0.52},
        "invoice_total_with_vat_tl": {"value": 100000, "confidence": 0.60},
        # computed_total ≈ 42000, delta = 58000, ratio = 58%
    },
    "expected": {
        "incident_created": True,
        "severity": "S1",
        "primary_flag": "INVOICE_TOTAL_MISMATCH",
        "action_hint": {
            "action_class": "VERIFY_OCR",
            "primary_suspect": "OCR_LOCALE_SUSPECT",
        },
        "mismatch_info": {
            "has_mismatch": True,
            "severity": "S1",
            "suspect_reason": "OCR_LOCALE_SUSPECT",
        }
    }
}
```

### 5.2 Full Pipeline Test Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    E2E SMOKE TEST FLOW                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. SETUP                                                       │
│     └─ Clean test DB                                            │
│     └─ Load 3 fixtures                                          │
│                                                                 │
│  2. EXTRACTION + VALIDATION + CALCULATION                       │
│     └─ For each fixture:                                        │
│        └─ validate_extraction()                                 │
│        └─ calculate_offer()                                     │
│        └─ calculate_quality_score()                             │
│                                                                 │
│  3. INCIDENT GENERATION                                         │
│     └─ create_incidents_from_quality()                          │
│     └─ Assert: incident count matches expected                  │
│     └─ Assert: severity, primary_flag, action_hint correct      │
│                                                                 │
│  4. SYSTEM HEALTH SNAPSHOT                                      │
│     └─ generate_system_health_report()                          │
│     └─ Assert: histogram buckets correct                        │
│     └─ Assert: Happy path → 0 in mismatch buckets               │
│     └─ Assert: No 500 errors in period                          │
│                                                                 │
│  5. FEEDBACK WRITE                                              │
│     └─ For S2 incident: submit_feedback()                       │
│     └─ Assert: feedback_json populated                          │
│     └─ Assert: updated_at changed                               │
│                                                                 │
│  6. FEEDBACK STATS READ                                         │
│     └─ get_feedback_stats()                                     │
│     └─ Assert: hint_accuracy_rate calculable                    │
│     └─ Assert: feedback_coverage > 0                            │
│                                                                 │
│  7. ASSERTIONS SUMMARY                                          │
│     └─ All 3 fixtures processed                                 │
│     └─ Expected incidents created                               │
│     └─ System health report valid                               │
│     └─ Feedback loop functional                                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 5.3 Test File Location

```
backend/tests/test_e2e_smoke.py
```

---

## 6. Run Summary Schema

### 6.1 JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "RunSummary",
  "type": "object",
  "required": ["generated_at", "period", "counts", "rates", "latency", "errors"],
  "properties": {
    "generated_at": {
      "type": "string",
      "format": "date-time"
    },
    "period": {
      "type": "object",
      "properties": {
        "start": {"type": "string", "format": "date-time"},
        "end": {"type": "string", "format": "date-time"}
      }
    },
    "counts": {
      "type": "object",
      "properties": {
        "total_invoices": {"type": "integer"},
        "incident_count": {"type": "integer"},
        "s1_count": {"type": "integer"},
        "s2_count": {"type": "integer"},
        "ocr_suspect_count": {"type": "integer"},
        "resolved_count": {"type": "integer"},
        "feedback_count": {"type": "integer"}
      }
    },
    "rates": {
      "type": "object",
      "properties": {
        "mismatch_rate": {"type": "number"},
        "s1_rate": {"type": "number"},
        "ocr_suspect_rate": {"type": "number"},
        "feedback_coverage": {"type": "number"},
        "hint_accuracy_rate": {"type": "number"}
      }
    },
    "latency": {
      "type": "object",
      "properties": {
        "pipeline_total_ms": {
          "type": "object",
          "properties": {
            "p50": {"type": "number"},
            "p95": {"type": "number"},
            "p99": {"type": "number"}
          }
        },
        "extraction_ms": {
          "type": "object",
          "properties": {
            "p50": {"type": "number"},
            "p95": {"type": "number"},
            "p99": {"type": "number"}
          }
        }
      }
    },
    "errors": {
      "type": "object",
      "properties": {
        "by_code": {
          "type": "object",
          "additionalProperties": {"type": "integer"}
        },
        "total_4xx": {"type": "integer"},
        "total_5xx": {"type": "integer"}
      }
    },
    "queue": {
      "type": "object",
      "properties": {
        "current_depth": {"type": "integer"},
        "max_depth_in_period": {"type": "integer"},
        "stuck_detected": {"type": "boolean"}
      }
    }
  }
}
```

### 6.2 Example Output

```json
{
  "generated_at": "2026-01-17T16:00:00Z",
  "period": {
    "start": "2026-01-17T00:00:00Z",
    "end": "2026-01-17T16:00:00Z"
  },
  "counts": {
    "total_invoices": 150,
    "incident_count": 23,
    "s1_count": 3,
    "s2_count": 20,
    "ocr_suspect_count": 5,
    "resolved_count": 18,
    "feedback_count": 12
  },
  "rates": {
    "mismatch_rate": 0.1533,
    "s1_rate": 0.1304,
    "ocr_suspect_rate": 0.2174,
    "feedback_coverage": 0.6667,
    "hint_accuracy_rate": 0.75
  },
  "latency": {
    "pipeline_total_ms": {"p50": 2100, "p95": 4500, "p99": 6200},
    "extraction_ms": {"p50": 1800, "p95": 3800, "p99": 5500}
  },
  "errors": {
    "by_code": {"400": 5, "404": 2, "500": 0},
    "total_4xx": 7,
    "total_5xx": 0
  },
  "queue": {
    "current_depth": 0,
    "max_depth_in_period": 3,
    "stuck_detected": false
  }
}
```

### 6.3 Latency Measurement Points

| Metric | Start | End | Includes |
|--------|-------|-----|----------|
| `pipeline_total_ms` | Request received | Response sent | Everything |
| `extraction_ms` | OpenAI call start | OpenAI response | Only extraction |

**Sprint 8.8 Scope:** `pipeline_total_ms` zorunlu, `extraction_ms` opsiyonel.

---

## 7. Operational Runbook

### 7.1 Senaryo 1: S1 Rate Spike

**Belirti:** S1 oranı 2x arttı (drift alert triggered)

**Olası Nedenler:**
1. Yeni tedarikçi formatı tanınmıyor
2. OpenAI model değişikliği
3. EPDK tarife güncellemesi yapılmadı

**Aksiyon Planı:**
```
1. [ ] System health dashboard'u kontrol et
2. [ ] Top offenders by rate listesine bak
3. [ ] Son 24 saatteki S1 incident'ları listele:
       GET /admin/incidents?severity=S1&limit=50
4. [ ] Primary flag dağılımına bak:
       - CALC_BUG → Engine regression, rollback düşün
       - MARKET_PRICE_MISSING → PTF/YEKDEM tablosu kontrol
       - TARIFF_LOOKUP_FAILED → EPDK tarife tablosu kontrol
5. [ ] Eğer tek provider'da yoğunlaşma varsa:
       - O provider'ın son faturalarını manuel incele
       - Extraction prompt'u güncellenmeli mi?
6. [ ] Rollback kararı: Son 1 saatte S1 > %30 ise rollback
```

**Escalation:** 30 dakika içinde çözülmezse → On-call engineer

---

### 7.2 Senaryo 2: OCR Suspect Rate Spike

**Belirti:** OCR_LOCALE_SUSPECT oranı 2x arttı

**Olası Nedenler:**
1. Düşük kaliteli görsel yüklemeleri
2. Yeni fatura formatı (farklı font/layout)
3. OpenAI Vision API degradation

**Aksiyon Planı:**
```
1. [ ] Action class distribution'a bak:
       - VERIFY_OCR %80+ ise → OCR sorunu kesin
2. [ ] Son OCR suspect incident'ların extraction'larını incele:
       - confidence değerleri ne kadar düşük?
       - Hangi alanlar düşük confidence?
3. [ ] OpenAI status page kontrol: status.openai.com
4. [ ] Eğer belirli provider'da yoğunlaşma:
       - O provider'ın fatura formatı değişmiş olabilir
       - Extraction prompt'a örnek ekle
5. [ ] Geçici çözüm: Düşük confidence threshold'u düşür
       (dikkat: false positive artabilir)
```

**Escalation:** OCR suspect > %50 ve 1 saat içinde düzelmezse → Product owner

---

### 7.3 Senaryo 3: Feedback Coverage Drop

**Belirti:** feedback_coverage %10'un altına düştü

**Olası Nedenler:**
1. Operatör eğitimi eksik
2. Feedback UI/UX sorunu
3. Resolved incident sayısı arttı ama feedback verilmiyor

**Aksiyon Planı:**
```
1. [ ] Resolved vs feedback count karşılaştır:
       - resolved_total artıyor mu?
       - feedback_count sabit mi?
2. [ ] Son 7 günlük trend'e bak:
       - Ani düşüş mü, kademeli mi?
3. [ ] Operatör aktivitesini kontrol et:
       - Hangi operatörler feedback veriyor?
       - Yeni operatör eklendi mi?
4. [ ] UI/UX kontrol:
       - Feedback butonu görünür mü?
       - Form çalışıyor mu?
5. [ ] Eğer operatör sorunu:
       - Eğitim materyali güncelle
       - Feedback önemini vurgula
```

**Escalation:** 3 gün üst üste %10 altında → Team lead

---

### 7.4 Senaryo 4: Queue Backlog (Stuck Worker)

**Belirti:** Queue depth > 0 ve pipeline ilerlemiyor

**Olası Nedenler:**
1. Worker process crash
2. OpenAI rate limit
3. Database connection pool exhausted
4. Memory leak

**Aksiyon Planı:**
```
1. [ ] /health/ready endpoint kontrol:
       - queue.stuck_detected = true mi?
2. [ ] Worker process durumu:
       - ps aux | grep worker
       - Worker log'larını kontrol et
3. [ ] OpenAI rate limit kontrolü:
       - 429 error var mı log'larda?
       - Günlük quota dolmuş mu?
4. [ ] Database connection kontrolü:
       - Active connection sayısı
       - Connection timeout var mı?
5. [ ] Acil müdahale:
       - Worker restart: systemctl restart invoice-worker
       - Eğer rate limit: Bekleme süresi ekle
6. [ ] Queue drain kontrolü:
       - Restart sonrası queue depth azalıyor mu?
       - 5 dakika içinde 0'a inmeli
```

**Escalation:** 15 dakika içinde queue drain olmuyorsa → Infrastructure team

---

### 7.5 Senaryo 5: Elevated Latency

**Belirti:** p95 latency > 5000ms (normal: ~2500ms)

**Olası Nedenler:**
1. OpenAI API yavaşladı
2. Database query yavaşladı
3. Network latency
4. Büyük dosya yüklemeleri

**Aksiyon Planı:**
```
1. [ ] Latency breakdown'a bak:
       - extraction_ms yüksek mi? → OpenAI sorunu
       - pipeline_total - extraction yüksek mi? → Backend sorunu
2. [ ] OpenAI API latency kontrolü:
       - Son 1 saatteki extraction süreleri
       - OpenAI status page
3. [ ] Database query analizi:
       - Slow query log kontrol
       - Index eksik mi?
4. [ ] Request size analizi:
       - Ortalama dosya boyutu arttı mı?
       - Büyük PDF'ler mi yükleniyor?
5. [ ] Geçici çözümler:
       - Timeout artır (dikkat: UX kötüleşir)
       - Rate limit uygula
       - Cache hit oranını artır
```

**Escalation:** p99 > 10000ms ve 30 dakika sürerse → Infrastructure team

---

## 8. Acceptance Criteria (Definition of Done)

### 8.1 Config Consolidation
- [ ] `backend/app/config.py` oluşturuldu
- [ ] Tüm threshold'lar mapping table'a göre taşındı
- [ ] Eski dosyalardaki sabitler silindi
- [ ] Tüm import'lar `from .config import THRESHOLDS` şeklinde
- [ ] Grep gate: `grep -rn "THRESHOLD\s*=" --include="*.py" | grep -v config.py | grep -v test_` boş

### 8.2 Config Validation
- [ ] `validate_config()` fonksiyonu yazıldı
- [ ] 8 invariant kontrol ediliyor
- [ ] Startup'ta `validate_config()` çağrılıyor
- [ ] Bozuk config ile app başlamıyor (test ile kanıtla)

### 8.3 Health Ready Endpoint
- [ ] `GET /health/ready` endpoint eklendi
- [ ] 4 check (database, config, openai_api, queue) implemente
- [ ] 200 (ready) ve 503 (not_ready) response'ları doğru
- [ ] Stuck queue detection çalışıyor

### 8.4 E2E Smoke Test
- [ ] `backend/tests/test_e2e_smoke.py` oluşturuldu
- [ ] 3 fixture tanımlı (happy, S2, S1+OCR)
- [ ] Full pipeline test (extraction → incident → health → feedback → stats)
- [ ] Tüm assertions geçiyor

### 8.5 Run Summary
- [ ] `generate_run_summary()` fonksiyonu yazıldı
- [ ] JSON schema'ya uygun output
- [ ] Latency percentiles hesaplanıyor (en az pipeline_total_ms)
- [ ] Error distribution hesaplanıyor

### 8.6 Runbook
- [ ] `backend/docs/RUNBOOK.md` oluşturuldu (veya bu dosyada Section 7)
- [ ] 5 senaryo dokümante edildi
- [ ] Her senaryo için aksiyon planı var
- [ ] Escalation path'ler tanımlı

### 8.7 Test Coverage
- [ ] Mevcut 490 test hala geçiyor
- [ ] Config validation için yeni testler eklendi
- [ ] Health ready endpoint için testler eklendi
- [ ] E2E smoke test geçiyor

---

## 9. Implementation Order

```
1. config.py oluştur (8.8.1)
   └─ Threshold dataclass'ları
   └─ THRESHOLDS singleton
   └─ validate_config()

2. Mevcut dosyaları güncelle (8.8.1 devam)
   └─ incident_service.py → from .config import THRESHOLDS
   └─ calculator.py → from .config import THRESHOLDS
   └─ validator.py → from .config import THRESHOLDS
   └─ incident_metrics.py → from .config import THRESHOLDS
   └─ incident_digest.py → from .config import THRESHOLDS
   └─ resolution_reasons.py → from .config import THRESHOLDS

3. Startup validation ekle (8.8.2)
   └─ main.py startup_event'e validate_config() ekle
   └─ Test: bozuk config ile başlatma dene

4. /health/ready endpoint (8.8.5)
   └─ main.py'ye endpoint ekle
   └─ Check fonksiyonları yaz
   └─ Test: ready ve not_ready senaryoları

5. Run summary generator (8.8.4)
   └─ incident_metrics.py'ye generate_run_summary() ekle
   └─ Latency tracking (opsiyonel: middleware)
   └─ Test: schema validation

6. E2E smoke test (8.8.3)
   └─ test_e2e_smoke.py oluştur
   └─ 3 fixture tanımla
   └─ Full pipeline test yaz

7. Runbook finalize (8.8.6)
   └─ Bu dokümandaki Section 7 yeterli
   └─ Gerekirse ayrı RUNBOOK.md
```

---

## 10. Risk Assessment

| Risk | Olasılık | Etki | Mitigation |
|------|----------|------|------------|
| Config migration sırasında typo | Orta | Yüksek | Invariant validation yakalar |
| Import döngüsü (circular import) | Düşük | Orta | config.py bağımsız modül |
| Latency tracking overhead | Düşük | Düşük | Middleware lightweight |
| E2E test flaky | Orta | Düşük | Deterministic fixtures |

---

## 11. Sprint 8.8 Bitişi

**Tanım:** Aşağıdaki sorular 30 saniyede cevaplanabiliyorsa sprint tamamdır:

1. ✅ "Sistem çalışıyor mu?" → `GET /health/ready`
2. ✅ "Kaç incident var?" → Run summary `counts.incident_count`
3. ✅ "S1 patladı mı?" → Run summary `rates.s1_rate` + drift alerts
4. ✅ "Feedback geliyor mu?" → Run summary `rates.feedback_coverage`
5. ✅ "Bozuk config prod'a girdi mi?" → Startup validation engelliyor

**Deploy sonrası kontrol listesi:**
```
[ ] /health/ready → 200
[ ] Run summary üretilebiliyor
[ ] 3 E2E fixture geçiyor
[ ] Config validation aktif
[ ] Runbook erişilebilir
```
