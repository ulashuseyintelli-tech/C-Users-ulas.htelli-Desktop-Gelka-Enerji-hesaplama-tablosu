# Sprint 8 Architecture Plan

## Durum Analizi (Sprint 7.1.2 Sonrası)

### Mevcut Sorunlar

#### 1. Çift Otorite RESOLVED Problemi (KRİTİK)
```
Şu an iki yer RESOLVED set edebiliyor:
- RetryExecutor.apply_result() → SUCCESS → RESOLVED
- RecomputeService.apply_recompute_result() → is_resolved=True → RESOLVED

Risk: executor SUCCESS deyip RESOLVED yapar, ama recompute'da flags hâlâ var.
Sonuç: "RESOLVED" yalan olur.
```

#### 2. Recompute Sonsuz Döngü Riski
```
Bir bug yüzünden incident sürekli reclassify olabilir:
- recompute → reclassify A→B
- retry → recompute → reclassify B→C
- retry → recompute → reclassify C→A
- ... sonsuz döngü

Şu an: recompute_count sadece sayaç, koruma yok.
```

#### 3. Reclassification Audit Trail Eksikliği
```
previous_primary_flag tek adım tutuyor.
3 kere reclassify olursa geçmiş kayboluyor.
Şimdilik OK, enterprise'da lazım olacak.
```

---

## Sprint 8.0 — Tek Otorite RESOLVED Refactor

### Tasarım Kararı
```
RESOLVED kararını SADECE RecomputeService verir.
RetryExecutor asla RESOLVED set etmez.
```

### RetryExecutor Değişiklikleri

```python
# ÖNCE (yanlış)
if result.status == RetryResultStatus.SUCCESS:
    incident.status = "RESOLVED"  # ❌ Executor karar veriyor

# SONRA (doğru)
if result.status == RetryResultStatus.SUCCESS:
    incident.retry_success = True  # ✅ Sadece flag
    incident.status = "PENDING_RECOMPUTE"  # ✅ Yeni status
    # RESOLVED kararını recompute verecek
```

### Yeni Status: PENDING_RECOMPUTE
```
Status flow:
PENDING_RETRY → (retry success) → PENDING_RECOMPUTE → (recompute) → RESOLVED/OPEN/reclassify
PENDING_RETRY → (retry fail) → PENDING_RETRY (backoff) veya OPEN (exhaust)
```

### Yeni Alan: retry_success (Boolean)
```sql
ALTER TABLE incidents ADD COLUMN retry_success BOOLEAN DEFAULT NULL;
```

### Orchestrator Pattern
```python
class RetryOrchestrator:
    """
    Retry + Recompute koordinasyonu.
    Tek entry point, tek otorite.
    """
    
    def process_retry(self, db, incident_id, context):
        # 1. Retry execute
        retry_result = self.executor.execute(incident)
        
        # 2. Retry sonucunu kaydet (RESOLVED set etmez)
        self.executor.apply_result(db, incident_id, retry_result)
        
        # 3. Success ise recompute
        if retry_result.status == SUCCESS:
            recompute_result = recompute_quality_flags(context)
            apply_recompute_result(db, incident_id, recompute_result)
            # RESOLVED kararı burada verilir
        
        return final_status
```

---

## Sprint 8.1 — Recompute Limit Guard

### Tasarım
```python
MAX_RECOMPUTE_COUNT = 5

def apply_recompute_result(...):
    if incident.recompute_count >= MAX_RECOMPUTE_COUNT:
        # Sonsuz döngü koruması
        incident.status = "OPEN"
        incident.resolution_note = "recompute_limit_exceeded"
        incident.action_type = "BUG_REPORT"  # Manuel review gerekli
        return
```

### Yeni Test Senaryoları
```
- recompute_count=5 → limit aşıldı → OPEN + resolution_note
- recompute_count=4 → normal işlem devam
- limit aşıldığında action_type BUG_REPORT olur
```

---

## Sprint 8.2 — Daily Digest & KPI Queries

### Daily Digest Fonksiyonu
```python
def generate_daily_digest(db, tenant_id, date) -> dict:
    return {
        "date": date,
        "tenant_id": tenant_id,
        "summary": {
            "total_incidents": count,
            "new_today": count,
            "resolved_today": count,
            "pending_retry": count,
            "bug_reports": count,
        },
        "top_primary_flags": [
            {"flag": "CALC_BUG", "count": 15},
            {"flag": "MARKET_PRICE_MISSING", "count": 12},
            ...
        ],
        "top_action_codes": [...],
        "retry_metrics": {
            "attempts_today": count,
            "resolved_by_retry": count,
            "exhausted": count,
            "success_rate": 0.65,
        },
        "reclassification_metrics": {
            "reclassified_today": count,
            "high_recompute_count": [incident_ids],
        },
        "alerts": [
            "BUG_REPORT rate > 10% (current: 15%)",
            "3 incidents with recompute_count > 3",
        ],
    }
```

### KPI Query Set
```python
# 1. Daily counts by status/action_type
def get_daily_counts(db, tenant_id, date_range)

# 2. Top primary flags by provider
def get_top_flags_by_provider(db, tenant_id, limit=10)

# 3. Retry success rate
def get_retry_success_rate(db, tenant_id, date_range)

# 4. Mean time to resolve
def get_mttr(db, tenant_id, date_range)

# 5. Reclassification rate
def get_reclassification_rate(db, tenant_id, date_range)

# 6. Occurrence percentiles (spam detection)
def get_occurrence_percentiles(db, tenant_id)
```

---

## Migration Plan

### 008_retry_orchestrator.py
```sql
-- Yeni status değeri
-- (status zaten VARCHAR, enum değil, migration gerekmez)

-- Yeni alan
ALTER TABLE incidents ADD COLUMN retry_success BOOLEAN DEFAULT NULL;

-- Index for PENDING_RECOMPUTE
CREATE INDEX ix_incidents_pending_recompute 
ON incidents(tenant_id, status) 
WHERE status = 'PENDING_RECOMPUTE';
```

---

## Test Stratejisi

### Sprint 8.0 Testleri (Tek Otorite)
```
1. executor SUCCESS → status=PENDING_RECOMPUTE (not RESOLVED)
2. executor SUCCESS → retry_success=True
3. orchestrator: retry success + recompute resolved → RESOLVED
4. orchestrator: retry success + recompute same-primary → PENDING_RETRY
5. orchestrator: retry success + recompute reclassify → reclassified
6. executor asla RESOLVED set etmez (contract test)
```

### Sprint 8.1 Testleri (Limit Guard)
```
1. recompute_count >= MAX → OPEN + resolution_note
2. recompute_count < MAX → normal flow
3. limit aşıldığında action_type=BUG_REPORT
4. limit aşıldığında external_issue_id korunur
```

### Sprint 8.2 Testleri (Digest & KPI)
```
1. daily_digest doğru sayıları döner
2. top_flags sıralı döner
3. retry_success_rate hesaplaması doğru
4. mttr hesaplaması doğru
5. empty data için graceful handling
```

---

## Dosya Yapısı (Sprint 8 Sonrası)

```
backend/app/
├── incident_service.py      # Quality flags, categories, actions
├── incident_keys.py         # dedupe_key_v2, invoice_hash
├── incident_repository.py   # DB CRUD, upsert, status transitions
├── action_router.py         # Route to status/payload
├── issue_payload.py         # PII-safe payload builder
├── issue_reporter.py        # External issue tracker integration
├── retry_executor.py        # Retry mechanics (RESOLVED set etmez)
├── recompute_service.py     # Quality recompute (tek RESOLVED otoritesi)
├── retry_orchestrator.py    # NEW: Retry + Recompute koordinasyonu
├── incident_digest.py       # NEW: Daily digest & KPI queries
└── database.py              # Models

backend/tests/
├── test_primary_cause.py
├── test_prod_guard.py
├── test_validation_contract.py
├── test_golden_incidents.py
├── test_dedupe_key_v2.py
├── test_issue_payload_builder.py
├── test_action_router.py
├── test_incident_repository.py
├── test_retry_executor.py
├── test_issue_reporter.py
├── test_recompute_service.py
├── test_retry_orchestrator.py   # NEW
└── test_incident_digest.py      # NEW
```

---

## Uygulama Sırası

```
Sprint 8.0 (Kritik - Önce Bu)
├── 1. Migration: retry_success alanı
├── 2. RetryExecutor: RESOLVED set etmeyi kaldır
├── 3. RetryOrchestrator: koordinasyon sınıfı
├── 4. 6 test

Sprint 8.1 (Koruma)
├── 1. MAX_RECOMPUTE_COUNT constant
├── 2. apply_recompute_result limit guard
├── 3. 4 test

Sprint 8.2 (Metrik)
├── 1. incident_digest.py modülü
├── 2. generate_daily_digest()
├── 3. KPI query fonksiyonları
├── 4. 5+ test
```

---

## Eleştiri ve Notlar

### Senin Önerilerine Katılıyorum:
1. ✅ Tek otorite RESOLVED - kritik, hemen yapılmalı
2. ✅ recompute_count limit - sonsuz döngü koruması şart
3. ✅ Daily digest - dashboard'dan önce veri görmek lazım

### Ek Önerilerim:
1. **PENDING_RECOMPUTE status**: Retry success ile recompute arasındaki geçiş durumu. Bu olmadan "retry success ama henüz recompute yapılmadı" durumu belirsiz kalır.

2. **retry_success boolean**: Executor'ın "başarılı oldu" demesi ile "incident çözüldü" demesi ayrı şeyler. Bu alan ikisini ayırır.

3. **RetryOrchestrator**: Executor ve RecomputeService'i koordine eden tek entry point. Bu pattern olmadan iki servis arasında race condition riski var.

### Ertelenebilir (Sprint 9+):
- Reclassification audit trail (incident_transitions tablosu)
- Slack/email entegrasyonu
- Dashboard UI
```

---

## Onay Bekleniyor

Bu mimari planı onaylıyor musun? Onaylarsan Sprint 8.0'dan başlıyorum:
1. Migration (retry_success)
2. RetryExecutor refactor
3. RetryOrchestrator
4. Testler
