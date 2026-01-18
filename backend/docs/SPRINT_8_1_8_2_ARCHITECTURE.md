# Sprint 8.1 & 8.2 Architecture Plan

## Durum Analizi (Sprint 8.0 Sonrası)

### Mevcut Durum
- ✅ Tek otorite RESOLVED (RecomputeService)
- ✅ PENDING_RECOMPUTE ara durumu
- ✅ retry_success boolean
- ✅ MAX_RECOMPUTE_COUNT=5 guard
- ✅ process_pending_recomputes() stuck recovery
- ✅ 351 test

### Eksikler (Senin Tespitlerin)
1. Stuck recovery SLA'sı sabit değil (şu an parametre)
2. resolution_note string, enum olsa KPI daha temiz
3. Retry outcome metrikleri yok
4. Daily digest yok

---

## Sprint 8.1 — Resolution Reason Enum & Guard Finalization

### 8.1.1 Resolution Reason Enum

```python
class ResolutionReason:
    """Sabit resolution reason değerleri (KPI için)."""
    RECOMPUTE_RESOLVED = "recompute_resolved"           # Recompute ile çözüldü
    RECOMPUTE_LIMIT_EXCEEDED = "recompute_limit_exceeded"  # Sonsuz döngü guard
    RETRY_EXHAUSTED = "retry_exhausted"                 # 4 retry sonrası exhaust
    MANUAL_RESOLVED = "manual_resolved"                 # Manuel çözüm
    AUTO_RESOLVED = "auto_resolved"                     # FALLBACK_OK
    RECLASSIFIED = "reclassified"                       # Primary değişti
```

### 8.1.2 Stuck Recovery SLA

```python
# Sabitler
STUCK_THRESHOLD_MINUTES = 10  # 10 dakika sonra stuck sayılır
STUCK_RECOVERY_BATCH_SIZE = 50

# Kural:
# PENDING_RECOMPUTE + updated_at <= now - 10m → stuck
# Tekrar recompute dene (recompute_count guard ile)
```

### 8.1.3 Güncellenecek Yerler

1. **RetryOrchestrator**: resolution_note → ResolutionReason enum kullan
2. **RecomputeService**: resolution_note → ResolutionReason enum kullan
3. **RetryExecutor**: resolution_note → ResolutionReason enum kullan

### 8.1.4 Testler (4 test)
```
1. recompute_limit_exceeded → resolution_note = RECOMPUTE_LIMIT_EXCEEDED
2. retry_exhausted → resolution_note = RETRY_EXHAUSTED
3. recompute_resolved → resolution_note = RECOMPUTE_RESOLVED
4. stuck recovery 10 dakika threshold ile çalışır
```

---

## Sprint 8.2 — Daily Digest & KPI Queries

### 8.2.1 KPI Metrikleri

```python
@dataclass
class IncidentMetrics:
    """Incident metrikleri."""
    # Genel
    total_incidents: int
    new_today: int
    resolved_today: int
    
    # Status dağılımı
    by_status: dict[str, int]  # {"OPEN": 5, "RESOLVED": 10, ...}
    
    # Retry funnel
    retry_attempts_total: int
    retry_attempts_success: int  # retry_success=True
    resolved_after_retry: int    # PENDING_RETRY → RESOLVED
    false_success_rate: float    # retry_success=True ama RESOLVED değil
    exhausted_count: int
    
    # Recompute
    recompute_limit_exceeded_count: int
    stuck_pending_recompute_count: int
    reclassified_count: int
    
    # Top lists
    top_primary_flags: list[tuple[str, int]]
    top_action_codes: list[tuple[str, int]]
    top_providers: list[tuple[str, int]]
    
    # Alerts
    alerts: list[str]
```

### 8.2.2 KPI Query Fonksiyonları

```python
# 1. Daily counts
def get_daily_counts(db, tenant_id, date) -> dict

# 2. Status distribution
def get_status_distribution(db, tenant_id) -> dict[str, int]

# 3. Retry funnel
def get_retry_funnel(db, tenant_id, date_range) -> dict

# 4. Top primary flags
def get_top_primary_flags(db, tenant_id, limit=10) -> list[tuple[str, int]]

# 5. Top action codes
def get_top_action_codes(db, tenant_id, limit=10) -> list[tuple[str, int]]

# 6. Stuck pending recompute count
def get_stuck_pending_recompute_count(db, tenant_id, threshold_minutes=10) -> int

# 7. Recompute limit exceeded count
def get_recompute_limit_exceeded_count(db, tenant_id, date_range) -> int

# 8. Mean time to resolve (MTTR)
def get_mttr(db, tenant_id, date_range) -> timedelta

# 9. False success rate
def get_false_success_rate(db, tenant_id, date_range) -> float
```

### 8.2.3 Daily Digest Fonksiyonu

```python
def generate_daily_digest(
    db: Session,
    tenant_id: str,
    date: date,
) -> DailyDigest:
    """
    Günlük özet raporu üretir.
    
    Returns:
        DailyDigest with all metrics and alerts
    """
```

### 8.2.4 Alert Kuralları

```python
ALERT_RULES = {
    "bug_report_rate_high": {
        "condition": "BUG_REPORT / total > 0.10",
        "message": "BUG_REPORT rate > 10%",
    },
    "stuck_pending_recompute": {
        "condition": "stuck_count > 0",
        "message": "{count} incidents stuck in PENDING_RECOMPUTE",
    },
    "recompute_limit_exceeded": {
        "condition": "limit_exceeded_count > 0",
        "message": "{count} incidents hit recompute limit",
    },
    "high_exhausted_rate": {
        "condition": "exhausted / retry_attempts > 0.20",
        "message": "Retry exhausted rate > 20%",
    },
}
```

### 8.2.5 Testler (8 test)

```
1. get_daily_counts doğru sayıları döner
2. get_retry_funnel doğru funnel hesaplar
3. get_top_primary_flags sıralı döner
4. get_stuck_pending_recompute_count threshold ile çalışır
5. get_false_success_rate doğru hesaplar
6. generate_daily_digest tüm metrikleri içerir
7. alert kuralları doğru tetiklenir
8. empty data için graceful handling
```

---

## Dosya Yapısı (Sprint 8.2 Sonrası)

```
backend/app/
├── incident_service.py      # Quality flags, categories, actions
├── incident_keys.py         # dedupe_key_v2, invoice_hash
├── incident_repository.py   # DB CRUD, upsert, status transitions
├── action_router.py         # Route to status/payload
├── issue_payload.py         # PII-safe payload builder
├── issue_reporter.py        # External issue tracker integration
├── retry_executor.py        # Retry mechanics
├── recompute_service.py     # Quality recompute
├── retry_orchestrator.py    # Retry + Recompute koordinasyonu
├── resolution_reasons.py    # NEW: ResolutionReason enum
├── incident_metrics.py      # NEW: KPI query fonksiyonları
├── incident_digest.py       # NEW: Daily digest
└── database.py              # Models

backend/tests/
├── ... (mevcut testler)
├── test_resolution_reasons.py   # NEW
├── test_incident_metrics.py     # NEW
└── test_incident_digest.py      # NEW
```

---

## Uygulama Sırası

```
Sprint 8.1 (Guard Finalization)
├── 1. resolution_reasons.py: ResolutionReason enum
├── 2. STUCK_THRESHOLD_MINUTES constant
├── 3. RetryOrchestrator/RecomputeService/RetryExecutor güncelle
├── 4. 4 test

Sprint 8.2 (Metrics & Digest)
├── 1. incident_metrics.py: KPI query fonksiyonları
├── 2. incident_digest.py: generate_daily_digest()
├── 3. Alert kuralları
├── 4. 8 test
```

---

## Eleştiri ve Notlar

### Senin Önerilerine Katılıyorum:
1. ✅ Stuck recovery SLA'sı sabit olmalı (10 dakika)
2. ✅ resolution_note enum olmalı (KPI için)
3. ✅ Retry outcome metrikleri şart
4. ✅ false_success_rate kritik metrik

### Ek Önerilerim:

1. **MTTR (Mean Time To Resolve)**: first_seen_at → resolved_at arası ortalama süre. Bu, sistemin "ne kadar hızlı iyileştiğini" gösterir.

2. **Retry funnel visualization**: 
   ```
   PENDING_RETRY (100) → retry_success (60) → RESOLVED (45)
                                            → still_pending (15)
                       → retry_fail (40) → exhausted (10)
                                        → backoff (30)
   ```

3. **Provider-based breakdown**: Hangi provider en çok sorun çıkarıyor? Bu, extraction/tariff iyileştirmelerine yön verir.

### Ertelenebilir (Sprint 9+):
- incident_transitions audit trail
- Slack/email entegrasyonu
- Dashboard UI
- Provider-specific SLA'lar

---

## Onay Bekleniyor

Bu mimari planı onaylıyor musun? Onaylarsan Sprint 8.1'den başlıyorum:
1. ResolutionReason enum
2. STUCK_THRESHOLD_MINUTES constant
3. Güncelleme + testler
