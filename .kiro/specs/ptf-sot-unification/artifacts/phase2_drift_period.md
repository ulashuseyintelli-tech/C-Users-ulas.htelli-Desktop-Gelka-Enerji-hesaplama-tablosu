# Phase 2 Drift Observation Window — T2.5

## Pencere Bilgileri

| Alan | Değer |
|---|---|
| Başlangıç | 2026-05-14 (T2.4 push: `1d84815`) |
| Minimum süre | 7 gün (2026-05-21) |
| Maksimum süre | 14 gün (2026-05-28) |
| Erken kapatma koşulu | 7 gün + tüm gate kriterleri PASS |
| Durum | **ACTIVE** |

## Gate Kriterleri (T2.6 kararı için)

Tasks.md'den (R10):
1. Ortalama `delta_pct` ≤ 0.5% tüm dönemler için → PASS
2. `severity=high` oranı ≤ %5 toplam drift kayıtları içinde → PASS
3. Her iki kriter başarısız → user-decision (karar metinli dokümana yazılır)

## Günlük Kontrol Prosedürü

### Prometheus Sorguları

```promql
# Toplam drift gözlemi (son 24h)
increase(ptf_drift_observed_total[24h])

# Severity dağılımı
sum by (severity) (increase(ptf_drift_observed_total[24h]))

# High severity oranı
sum(increase(ptf_drift_observed_total{severity="high"}[24h]))
/
sum(increase(ptf_drift_observed_total[24h]))

# Canonical aylık ortalama (dönem bazlı)
ptf_canonical_monthly_avg
```

### SQLite Doğrudan Sorgu (Grafana yoksa)

```sql
-- Son 24h drift kayıtları
SELECT severity, COUNT(*) as cnt,
       AVG(delta_pct) as avg_pct,
       MAX(delta_pct) as max_pct
FROM ptf_drift_log
WHERE created_at >= datetime('now', '-24 hours')
GROUP BY severity;

-- Dönem bazlı özet
SELECT period, severity, COUNT(*) as cnt,
       AVG(delta_pct) as avg_pct
FROM ptf_drift_log
WHERE created_at >= datetime('now', '-7 days')
GROUP BY period, severity;

-- High severity detay
SELECT * FROM ptf_drift_log
WHERE severity = 'high'
ORDER BY created_at DESC
LIMIT 20;
```

### Günlük Snapshot Formatı

Her gün aşağıdaki bilgiler bu dosyaya eklenir:

```
### Gün N — YYYY-MM-DD
- Toplam drift kayıt: X
- severity=low: X (Y%)
- severity=high: X (Y%)
- severity=missing_legacy: X (Y%)
- Ortalama delta_pct (low+high): X%
- Max delta_pct: X%
- Etkilenen dönemler: [list]
- Anomali: var/yok
- Karar: devam / escalate
```

## Rollback Prosedürü (Pencere İçinde)

Eğer dual-read beklenmeyen davranış gösterirse:

1. `OPS_GUARD_PTF_DRIFT_LOG_ENABLED=false` set et
2. Worker restart (SIGHUP veya deploy)
3. Doğrula: `ptf_drift_observed_total` artışı durdu
4. Bu dosyaya "PAUSED" notu ekle + sebep

## Acil Durum (Kill Switch)

Eğer canonical veri bozuk şüphesi varsa:

1. `OPS_GUARD_USE_LEGACY_PTF=true` set et
2. Worker restart
3. Doğrula: response'lar legacy tablodan geliyor
4. Incident açılır — Phase 2 pencere durdurulur

---

## Günlük Snapshot Kayıtları

> Aşağıya her gün eklenir. İlk kayıt T2.4 deploy sonrası ilk /api/pricing/analyze çağrısından sonra.

(Pencere başladı — ilk snapshot bekleniyor)
