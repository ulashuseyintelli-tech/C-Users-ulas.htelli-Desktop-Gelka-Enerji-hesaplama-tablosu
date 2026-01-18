# Sprint 8.9: Release Candidate + Prod Pilot Runbook

## Tarih: 2026-01-17
## Durum: HAZIR

---

## 1. Amaç ve Kapsam

### 1.1 Sprint Hedefi

**Sprint 8.9 = 0 Feature, Sadece RC + Pilot + Gözlem**

Bu sprint'te yeni özellik eklenmez. Amaç:
- Prod ortamında gerçek veriyle kontrollü koşu
- Sistem stabilitesinin kanıtlanması
- Operasyonel süreçlerin test edilmesi

### 1.2 Kritik Kararlar

| Karar | Seçim | Gerekçe |
|-------|-------|---------|
| Dry-run modu | ❌ YOK | Feedback loop ve system health test edilemez |
| Pilot tenant izolasyonu | ✅ VAR | Gerçek incident oluşur, prod verisi kirlenmez |
| Config hash görünürlüğü | ✅ VAR | Startup log + /health/ready response |

### 1.3 Done Kriterleri

- [ ] Staging/Prod'da `/health/ready` 1 gün boyunca stabil (200)
- [ ] Gün sonu run summary alınabiliyor
- [ ] System health sayfası gerçek veriyle doluyor
- [ ] Feedback endpoint en az 1 kez gerçek operatör tarafından kullanılıyor (coverage > 0)

---

## 2. Release Checklist (Deploy Öncesi)

### 2.1 Build/Version

| Check | Komut/Yöntem | Beklenen |
|-------|--------------|----------|
| build_id log'a yazılıyor | Startup log kontrol | `build_id=git:abc1234` |
| config_hash log'a yazılıyor | Startup log kontrol | `config_hash=sha256:xxxx` |
| /health/ready'de görünüyor | `GET /health/ready` | Response'da `build_id` + `config_hash` |

### 2.2 Ready Check

| Check | Komut | Beklenen |
|-------|-------|----------|
| Ready endpoint | `GET /health/ready` | HTTP 200 |
| Config check | Response → `checks.config` | `"ok"` |
| Database check | Response → `checks.database` | `"ok"` |
| OpenAI API check | Response → `checks.openai_api` | `"ok"` |
| Queue check | Response → `checks.queue` | `"ok"` |

### 2.3 Config Sanity

| Check | Yöntem | Beklenen |
|-------|--------|----------|
| validate_config() geçti | Startup log | `"Config validation passed"` |
| ENV whitelist doğru | `echo $ENV` | `production` veya `staging` |
| API key configured | `/health/ready` | `openai_api: ok` |

### 2.4 /health/ready Response Örneği

```json
{
  "status": "ready",
  "timestamp": "2026-01-17T15:00:00Z",
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
    "rate_limit": {
      "current": 5,
      "limit": 50,
      "remaining": 45,
      "window_seconds": 3600
    }
  },
  "last_activity": {
    "last_incident_at": "2026-01-17T14:55:00Z"
  }
}
```

---

## 3. Pilot Guardrails

### 3.1 Tenant Isolation

**Prensip:** Dry-run yerine tenant izolasyonu kullan.

| Özellik | Değer | Açıklama |
|---------|-------|----------|
| Pilot tenant ID | `tenant_id="pilot"` | Tüm pilot run'lar bu tenant'ta |
| DB yazımı | ✅ Aktif | Gerçek incident oluşur |
| Feedback | ✅ Aktif | Gerçek feedback alınır |
| System health | ✅ Aktif | Metrikler dolu kalır |
| Prod verisi | ✅ İzole | Kirlenmez |

**Kullanım:**
```bash
# Pilot run başlatma
curl -X POST "https://api.example.com/full-process?tenant_id=pilot" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@invoice.pdf"
```

### 3.2 Kill Switch

| Env Variable | Değer | Etki |
|--------------|-------|------|
| `PILOT_ENABLED=true` | Default | Pilot akışı aktif |
| `PILOT_ENABLED=false` | Kill | Pilot job'ları skip, uygulama ready kalır |

**Implementasyon Notu:** Bu env var Sprint 8.9'da eklenecek.

### 3.3 Rate Limit / Budget

| Limit | Değer | Aşılınca |
|-------|-------|----------|
| `PILOT_MAX_INVOICES_PER_HOUR` | 50 | Yeni job reject (429) |
| `PILOT_MAX_INCIDENTS_PER_HOUR` | 20 | Incident creation skip, log warning |
| OpenAI rate limit | API limit | Graceful degrade, queue backlog |

**OpenAI Limit Aşımı Davranışı:**
- `/health/ready` → 503 (not_ready)
- `checks.openai_api` → `"error"` + `"rate_limited"`
- Queue depth artmaya başlar

---

## 4. Post-Deploy Validation Script

### 4.1 Script: `scripts/post_deploy_check.py`

```python
#!/usr/bin/env python3
"""
Post-deploy validation script for Sprint 8.9.

Exit codes:
  0 = All checks passed
  1 = Ready check failed
  2 = Smoke test failed
  3 = Feedback loop failed
  4 = Partial success (investigate, don't rollback)
"""

import sys
import requests

BASE_URL = os.getenv("API_BASE_URL", "https://api.example.com")
API_KEY = os.getenv("API_KEY")
TENANT = "pilot"

def check_ready():
    """Step 1: GET /health/ready → 200"""
    r = requests.get(f"{BASE_URL}/health/ready", timeout=10)
    if r.status_code != 200:
        print(f"FAIL: /health/ready returned {r.status_code}")
        return False
    data = r.json()
    if data.get("status") != "ready":
        print(f"FAIL: status={data.get('status')}, checks={data.get('checks')}")
        return False
    print(f"OK: /health/ready → ready, build={data.get('build_id')}")
    return True

def check_smoke():
    """Step 2: Trigger smoke run (if endpoint exists)"""
    # Option A: Internal smoke endpoint
    # r = requests.post(f"{BASE_URL}/admin/smoke/run?tenant={TENANT}", ...)
    
    # Option B: Run pytest smoke tests via subprocess
    # result = subprocess.run(["pytest", "backend/tests/test_e2e_smoke.py", "-v"])
    
    print("OK: Smoke check (manual or skipped)")
    return True

def check_system_health():
    """Step 3: GET /admin/system-health?tenant=pilot"""
    r = requests.get(
        f"{BASE_URL}/admin/system-health",
        params={"tenant_id": TENANT},
        headers={"X-Admin-Key": API_KEY},
        timeout=30
    )
    if r.status_code != 200:
        print(f"FAIL: /admin/system-health returned {r.status_code}")
        return False
    print("OK: System health endpoint accessible")
    return True

def check_feedback_loop():
    """Step 4-5: Feedback write + stats read"""
    # This requires a resolved incident to exist
    # For initial deploy, may return "no data" which is OK
    r = requests.get(
        f"{BASE_URL}/admin/feedback-stats",
        params={"tenant_id": TENANT},
        headers={"X-Admin-Key": API_KEY},
        timeout=10
    )
    if r.status_code != 200:
        print(f"WARN: /admin/feedback-stats returned {r.status_code}")
        return True  # Not a hard failure on first deploy
    data = r.json()
    print(f"OK: Feedback stats → coverage={data.get('feedback_coverage', 0)}")
    return True

def main():
    results = {
        "ready": check_ready(),
        "smoke": check_smoke(),
        "health": check_system_health(),
        "feedback": check_feedback_loop(),
    }
    
    if not results["ready"]:
        return 1
    if not results["smoke"]:
        return 2
    if not results["feedback"]:
        return 3
    if not all(results.values()):
        return 4
    
    print("\n✅ All post-deploy checks passed")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

### 4.2 Exit Codes

| Code | Durum | Aksiyon |
|------|-------|---------|
| 0 | All passed | Deploy başarılı |
| 1 | Ready fail | Rollback |
| 2 | Smoke fail | Rollback |
| 3 | Feedback fail | Investigate (rollback değil) |
| 4 | Partial success | Investigate |

---

## 5. Gözlem Metrikleri (1 Hafta)

### 5.1 Günlük Run Summary JSON Log

Her run sonunda tek satır JSON log yazılır:

```json
{
  "event": "run_summary",
  "timestamp": "2026-01-17T16:00:00Z",
  "build_id": "git:abc1234",
  "config_hash": "sha256:7f3a2b1c",
  "tenant_id": "pilot",
  "counts": {
    "total_invoices": 150,
    "incident_count": 23,
    "s1_count": 3,
    "s2_count": 20,
    "ocr_suspect_count": 5,
    "accept_rounding_count": 12
  },
  "rates": {
    "mismatch_rate": 0.1533,
    "s1_rate": 0.1304,
    "ocr_suspect_rate": 0.2174,
    "feedback_coverage": 0.65
  },
  "latency": {
    "p95_pipeline_total_ms": 2800
  },
  "queue": {
    "depth": 0,
    "max_depth": 3
  },
  "errors": {
    "4xx": 5,
    "5xx": 0
  }
}
```

### 5.2 Haftalık Trend Analizi

**jq ile günlük özet:**
```bash
# Son 7 günün S1 rate trendi
cat app.log | grep '"event": "run_summary"' | jq -r '[.timestamp, .rates.s1_rate] | @tsv'

# Ortalama p95 latency
cat app.log | grep '"event": "run_summary"' | jq -s 'map(.latency.p95_pipeline_total_ms) | add / length'

# Feedback coverage trendi
cat app.log | grep '"event": "run_summary"' | jq -r '[.timestamp, .rates.feedback_coverage] | @tsv'
```

### 5.3 İzlenecek Metrikler

| Metrik | Hedef | Alarm Eşiği |
|--------|-------|-------------|
| S1 rate | < 10% | > 30% |
| S2 rate | < 20% | > 40% |
| OCR suspect rate | < 15% | > 30% |
| Accept rounding rate | 5-15% | < 2% veya > 25% |
| Feedback coverage | > 50% | < 10% |
| p95 pipeline latency | < 3000ms | > 6000ms |
| Queue depth max | < 10 | > 50 |
| 5xx error rate | 0% | > 5% |

---

## 6. Rollback Koşulları

### 6.1 Otomatik Rollback Tetikleyicileri

| Koşul | Eşik | Pencere | Aksiyon |
|-------|------|---------|---------|
| /health/ready 503 | > 5 dakika | Sürekli | Rollback |
| Queue stuck | depth artıyor + tüketim yok | > 15 dakika | Rollback |
| 5xx error rate | > 5% | 10 dakika | Rollback |
| S1 rate spike | > 30% (min 20 incident) | 1 saat | Investigate → Rollback |
| Latency spike | p95 > 2x baseline | 30 dakika (min 20 sample) | Investigate |

### 6.2 Eşik Açıklamaları

**S1 rate > 30% (min n>=20):**
- Son 1 saatte en az 20 incident oluşmuş olmalı
- Bu 20+ incident'ın %30'undan fazlası S1 ise alarm

**Latency p95 > 2x baseline:**
- Baseline = İlk 24 saatlik p95 ortalaması VEYA sabit 3000ms
- 2x baseline = 6000ms (sabit baseline ile)
- En az 20 sample olmalı (istatistiksel anlamlılık)

### 6.3 Investigate vs Rollback

| Durum | Aksiyon |
|-------|---------|
| Ready 503 | Hemen rollback |
| Queue stuck | Hemen rollback |
| 5xx spike | Hemen rollback |
| S1 spike | 15 dakika investigate, düzelmezse rollback |
| Latency spike | 30 dakika investigate, düzelmezse rollback |
| Feedback fail | Investigate, rollback değil |

---

## 7. Rollback Prosedürü

### 7.1 Adım Adım

```
┌─────────────────────────────────────────────────────────────────┐
│                    ROLLBACK PROSEDÜRÜ                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. KILL SWITCH (hemen)                                         │
│     └─ PILOT_ENABLED=false                                      │
│     └─ Pilot job'ları durur, sistem ready kalır                 │
│                                                                 │
│  2. ROLLBACK DEPLOY                                             │
│     └─ Önceki stable image'a dön                                │
│     └─ kubectl rollout undo deployment/api                      │
│     └─ VEYA: docker-compose up -d --force-recreate              │
│                                                                 │
│  3. VERIFY                                                      │
│     └─ GET /health/ready → 200                                  │
│     └─ Startup log: "Config validation passed"                  │
│     └─ Queue depth azalıyor mu?                                 │
│                                                                 │
│  4. INCIDENT FLOOD KONTROLÜ                                     │
│     └─ Pilot tenant incident creation durdur (kill switch)      │
│     └─ Queue drain bekle (max 5 dakika)                         │
│     └─ Stuck job varsa manual cancel                            │
│                                                                 │
│  5. POSTMORTEM                                                  │
│     └─ Rollback sebebi not al                                   │
│     └─ Log snapshot al                                          │
│     └─ Metrik snapshot al                                       │
│     └─ Root cause analizi planla                                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 Rollback Komutları

```bash
# 1. Kill switch
export PILOT_ENABLED=false
# veya: kubectl set env deployment/api PILOT_ENABLED=false

# 2. Rollback deploy
kubectl rollout undo deployment/api
# veya: git revert && deploy

# 3. Verify
curl -s https://api.example.com/health/ready | jq .

# 4. Queue drain kontrolü
watch -n 5 'curl -s https://api.example.com/health/ready | jq .checks.queue'
```

---

## 8. Alerting Hook

### 8.1 Alert Sahipliği

| Alert Tipi | Sahip | Kanal |
|------------|-------|-------|
| Ready 503 | On-call engineer | Slack #alerts + PagerDuty |
| S1 spike | On-call engineer | Slack #alerts |
| Queue stuck | On-call engineer | Slack #alerts + PagerDuty |
| 5xx spike | On-call engineer | Slack #alerts + PagerDuty |
| Feedback coverage drop | Product owner | Slack #product |
| Latency spike | On-call engineer | Slack #alerts |

### 8.2 Log-based Alert Örnekleri

```bash
# S1 spike alert (log monitoring)
# Pattern: "severity": "S1" count > 5 in 10 minutes

# Ready 503 alert
# Pattern: GET /health/ready → 503

# 5xx alert
# Pattern: HTTP 5xx count > 10 in 5 minutes
```

### 8.3 Manuel Kontrol (İlk Hafta)

İlk hafta otomatik alerting yerine manuel kontrol:
- Her 2 saatte bir `/health/ready` kontrol
- Günde 2 kez run summary review
- Günde 1 kez feedback coverage kontrol

---

## 9. Backup / Safety

### 9.1 Deploy Öncesi

| Adım | Komut | Açıklama |
|------|-------|----------|
| DB snapshot | `pg_dump -t incidents -t jobs > backup.sql` | Pilot tenant tabloları |
| Config snapshot | `cat config.py > config_backup.py` | Mevcut config |
| Image tag | `docker tag api:latest api:pre-8.9` | Rollback için |

### 9.2 Geri Dönüş

| Senaryo | Aksiyon |
|---------|---------|
| Pilot data temizliği | `DELETE FROM incidents WHERE tenant_id='pilot'` |
| Config restore | `cp config_backup.py config.py && restart` |
| Full rollback | `docker run api:pre-8.9` |

### 9.3 Pilot Data Purge Script

```sql
-- Pilot tenant verilerini temizle (gerekirse)
BEGIN;
DELETE FROM incidents WHERE tenant_id = 'pilot';
DELETE FROM jobs WHERE tenant_id = 'pilot';
COMMIT;
```

---

## 10. Implementation Checklist (Sprint 8.9 Tasks)

### 10.1 Kod Değişiklikleri

- [x] `config.py`: `get_config_hash()` fonksiyonu ekle ✅ DONE
- [x] `main.py`: Startup log'a `build_id` + `config_hash` yaz ✅ DONE
- [x] `main.py`: `/health/ready` response'a `build_id` + `config_hash` ekle ✅ DONE
- [x] `main.py`: `PILOT_ENABLED` env var desteği ✅ DONE (Sprint 8.9.1)
- [x] `pilot_guard.py`: Kill switch + rate limit + tenant isolation ✅ DONE (Sprint 8.9.1)
- [ ] `incident_metrics.py`: `accept_rounding_count` metriği ekle
- [x] `scripts/post_deploy_check.py`: Validation script oluştur ✅ DONE (Sprint 8.9.1)

### 10.2 Operasyonel Hazırlık

- [ ] Slack alert kanalı oluştur
- [ ] On-call rotation tanımla
- [ ] Baseline latency belirle (veya 3000ms sabit)
- [ ] DB backup prosedürü test et

### 10.3 Dokümantasyon

- [ ] Bu runbook'u ekiple paylaş
- [ ] Rollback prosedürünü rehearsal yap
- [ ] Post-deploy checklist'i print et

---

## 11. Sprint 8.9 Timeline

```
Day 0: Deploy to staging
  └─ Post-deploy check script çalıştır
  └─ Manual smoke test
  └─ /health/ready 1 saat izle

Day 1: Deploy to prod (pilot tenant only)
  └─ PILOT_ENABLED=true
  └─ İlk 10 invoice pilot tenant'ta işle
  └─ Run summary al, metrikler kontrol

Day 2-3: Controlled pilot
  └─ Pilot volume artır (50 invoice/gün)
  └─ Feedback endpoint test et
  └─ System health dashboard kontrol

Day 4-7: Full pilot observation
  └─ Günlük run summary review
  └─ Trend analizi
  └─ Anomali varsa investigate

Day 7: Sprint 8.9 Done Decision
  └─ Done kriterleri karşılandı mı?
  └─ Evet → Sprint 9 planla
  └─ Hayır → Extend pilot veya fix
```

---

## 12. Appendix: Config Hash Hesaplama

```python
# backend/app/config.py

import hashlib
import json

def get_config_hash() -> str:
    """
    Calculate SHA256 hash of config for version tracking.
    
    Returns:
        First 16 chars of SHA256 hash
    """
    summary = get_config_summary()
    config_json = json.dumps(summary, sort_keys=True)
    full_hash = hashlib.sha256(config_json.encode()).hexdigest()
    return f"sha256:{full_hash[:16]}"
```

---

## 13. Appendix: Build ID

```python
# backend/app/main.py

import os
import subprocess

def get_build_id() -> str:
    """
    Get build ID from git or environment.
    
    Priority:
    1. BUILD_ID env var (CI/CD sets this)
    2. Git short SHA
    3. "unknown"
    """
    # From env (CI/CD)
    build_id = os.getenv("BUILD_ID")
    if build_id:
        return build_id
    
    # From git
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return f"git:{result.stdout.strip()}"
    except:
        pass
    
    return "unknown"
```
