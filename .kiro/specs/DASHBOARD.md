# Proje Spec Dashboard

> Son güncelleme: 2026-02-28
> Checkbox: `[x]` done · `[d]` deferred · `[ ]` todo (hard) · `[ ]*` optional (soft)
> Soft etiket: `{SOFT:SAFETY}` prod'dan önce önerilir · `{SOFT:NICE}` non-blocking

## Tek Satır Özet

```
Hard (ProdReady-blocking):      17 sub-tasks (invoice-analysis-system)
Deferred (top-level):           11 (invoice 7 + load 4)
Soft-Safety (recommended):       8 sub-tasks across 4 specs
Soft-Nice (non-blocking):       29 sub-tasks across 7 specs
```

## Sayısal Özet

| Metrik | Değer |
|--------|-------|
| Toplam spec | 26 |
| DONE | 24 |
| CORE_DONE | 1 (load-characterization) |
| CORE_DONE_NOT_PROD_READY | 1 (invoice-analysis-system) |
| Hard remaining (prod-blocking) | 17 sub-tasks |
| Soft-Safety (recommended pre-prod) | 8 sub-tasks |
| Soft-Nice (non-blocking) | 29 sub-tasks |

## 26 Spec Durumu

| # | Spec | Status | Top d/df/t | Sub d/df/t | Safety | Nice | ProdReady | Blocking |
|---|------|--------|------------|------------|--------|------|-----------|----------|
| 1 | audit-history | DONE | 9/0/0 | 16/0/0 | 0 | 0 | YES | — |
| 2 | ci-enforcement | DONE | 5/0/0 | 6/0/0 | 0 | 0 | YES | — |
| 3 | concurrency-pbt | DONE | 8/0/0 | 7/0/0 | 0 | 0 | YES | — |
| 4 | dependency-wrappers | DONE | 18/0/0 | 34/0/0 | 0 | 0 | YES | — |
| 5 | deploy-integration | DONE | 10/0/0 | 17/0/0 | 0 | 0 | YES | — |
| 6 | drift-guard | DONE | 10/0/0 | 29/0/0 | 0 | 0 | YES | — |
| 7 | endpoint-class-policy | DONE | 7/0/0 | 33/0/5 | 2 | 3 | YES | — |
| 8 | fault-injection | DONE | 12/0/0 | 19/0/0 | 0 | 0 | YES | — |
| 9 | internal-adoption | DONE | 5/0/0 | 6/0/0 | 0 | 0 | YES | — |
| 10 | **invoice-analysis** | **NOT_PROD_READY** | 8/7/0 | 44/0/17 | 0 | 0 | **NO** | Gate #1-3 |
| 11 | **load-character.** | **CORE_DONE** | 10/4/0 | 8/16/0 | 0 | 0 | YES | — |
| 12 | observability-pack | DONE | 8/0/0 | 17/0/0 | 0 | 0 | YES | — |
| 13 | ops-guard | DONE | 10/0/0 | 30/0/5 | 2 | 3 | YES | — |
| 14 | pdf-render-worker | DONE | 10/0/0 | 22/0/6 | 2 | 4 | YES | — |
| 15 | preflight-guardrails | DONE | 9/0/0 | 16/0/0 | 0 | 0 | YES | — |
| 16 | preflight-telemetry | DONE | 5/0/0 | 10/0/0 | 0 | 0 | YES | — |
| 17 | ptf-admin-frontend | DONE | 13/0/0 | 26/0/0 | 0 | 0 | YES | — |
| 18 | ptf-admin-management | DONE | 11/0/0 | 26/0/0 | 0 | 0 | YES | — |
| 19 | release-e2e-pipeline | DONE | 6/0/0 | 8/0/0 | 0 | 0 | YES | — |
| 20 | release-gate-telem. | DONE | 4/0/0 | 9/0/8 | 2 | 6 | YES | — |
| 21 | release-governance | DONE | 6/0/0 | 20/0/0 | 0 | 0 | YES | — |
| 22 | release-gov-pack | DONE | 4/0/0 | 5/0/0 | 0 | 0 | YES | — |
| 23 | runtime-guard-dec. | DONE | 15/0/0 | 31/0/0 | 0 | 0 | YES | — |
| 24 | slo-adaptive-ctrl | DONE | 13/0/0 | 53/0/1 | 0 | 1 | YES | — |
| 25 | telemetry-unific. | DONE | 9/0/0 | 11/0/10 | 0 | 10 | YES | — |
| 26 | tenant-enable | DONE | 10/0/0 | 17/0/2 | 0 | 2 | YES | — |

## ProdReady Gate — invoice-analysis-system

```
Status: CORE_DONE_NOT_PROD_READY
Bu üçü yoksa prod'a çıkma:
```

| Gate | Kriter | Blocking Tasks | Durum |
|------|--------|----------------|-------|
| #1 Validator | ETTN format + çok zamanlı tutarlılık + reaktif ceza | Task 4, 5 (checkpoint), 6 (PBT) | ❌ |
| #2 Supplier Tests | En az 5 tedarikçi gerçek fatura senaryosu | Task 8 | ❌ |
| #3 API Contract | /suppliers endpoint + response model versiyonlanmış | Task 7 | ❌ |
| — | Final checkpoint | Task 9 (depends on Gate #1+#2+#3) | ❌ |
| Phase 2 | Feedback Loop (Sprint 8.7) | Task 15 (prod data bağımlı, gate dışı) | — |

Risk: Gate #1-3 olmadan prod = yanlış teklif / yanlış kıyas / tedarikçi regression riski.

### Deferred → Gate Mapping

| Deferred Task | Başlık | Rol | Gate | Açıklama |
|---------------|--------|-----|------|----------|
| Task 4 | Validator Güncellemesi | Primary blocker | Gate #1 | ETTN format, çok zamanlı tutarlılık, reaktif ceza |
| Task 8 | Fatura Test Senaryoları | Primary blocker | Gate #2 | 5 tedarikçi gerçek fatura regression |
| Task 7 | API Endpoint Güncellemeleri | Primary blocker | Gate #3 | /suppliers + response model versiyonlama |
| Task 5 | Checkpoint - Backend | Supporting | Gate #1 chain | Task 4'e bağımlı doğrulama |
| Task 6 | Property Tests Güncelleme | Supporting | Gate #1 quality | Task 4 validasyonlarının PBT kanıtı |
| Task 9 | Final Checkpoint | Closeout | Gate #1+#2+#3 | Tüm gate'lere bağımlı kapanış |
| Task 15 | Feedback Loop (Sprint 8.7) | Phase 2 | — | Prod data bağımlı, gate dışı |

## Deferred Detay — load-characterization

```
Status: CORE_DONE (140 test passing)
```

| Kategori | Task | Sebep |
|----------|------|-------|
| Staging-dep | 7. Alert validation | Production baseline data gerekli |
| Staging-dep | 11. Flaky correlation | Staging timing data gerekli |
| Checkpoint | 9, 13 | Task 7, 11'e bağımlı |
| Optional PBT | 13 property test (sub-tasks) | Core correctness deterministic testlerle kanıtlanmış |

## Optional PBT — SAFETY_INVARIANT (8 test, 4 spec)

Prod'dan önce önerilir. Yanlış karar / güvenlik ihlali / governance blind spot riski.

| Spec | Task | Property | Risk |
|------|------|----------|------|
| endpoint-class-policy | 5.3 | Global OFF korunur (EP-7) | Yanlış enforce |
| endpoint-class-policy | 1.7 | Precedence determinizmi (EP-8) | Belirsiz policy |
| ops-guard | 4.4 | Kill-switch kapsam engelleme + degrade mode | Kill-switch bypass |
| ops-guard | 6.3 | Circuit breaker durum makinesi | CB state yanlışlığı |
| pdf-render-worker | 2.3 | İdempotency | Duplicate job |
| pdf-render-worker | 6.4 | HTML sanitizasyon | XSS / injection |
| release-gate-telemetry | 1.6 | Etiket kardinalitesi sınırlıdır | Observability DoS |
| release-gate-telemetry | 2.4 | Fail-open metrik emisyonu | Governance blind spot |

## Optional PBT — NICE_TO_HAVE (29 test, 7 spec)

Coverage / regression. Prod gate değil.

| Spec | Count | Örnekler |
|------|-------|----------|
| endpoint-class-policy | 3 | RiskClass cardinality, LOW fallback, exact match |
| ops-guard | 3 | Config round-trip, rate limit, admin auth |
| pdf-render-worker | 4 | TTL cleanup, artifact key, allowlist, job round-trip |
| release-gate-telemetry | 6 | Counter artırma, JSON round-trip, Prometheus export, sözleşme/audit sayacı |
| slo-adaptive-control | 1 | Extended observability (dashboard) |
| telemetry-unification | 10 | Snapshot round-trip, reset, Prometheus output, event ingestion, vb. |
| tenant-enable | 2 | Config unit test, middleware unit test |

## Checkbox Convention & Marker Standardı

```
- [x]  Done — iş tamamlandı, test kanıtı var
- [d]  Deferred — bilinçli erteleme, sebep ve koşul belirtilmiş
- [ ]  Todo (hard) — henüz başlanmamış, deferred parent altında gerçek iş
- [ ]* Optional (soft) — nice-to-have veya safety, satır sonunda etiket:
       {SOFT:SAFETY} — prod'dan önce önerilir (yanlış karar/güvenlik riski)
       {SOFT:NICE}   — coverage/regression, prod gate değil
```

Regex kuralları (otomasyon):
```
done:           ^\s*- \[x\]
deferred:       ^\s*- \[d\]
todo_hard:      ^\s*- \[ \](?!\*)
todo_soft:      ^\s*- \[ \]\*
soft_safety:    ^\s*- \[ \]\*.*\{SOFT:SAFETY\}
soft_nice:      ^\s*- \[ \]\*.*\{SOFT:NICE\}
```

## Rapor Script'i

```powershell
Get-ChildItem -Path ".kiro/specs/*/tasks.md" | ForEach-Object {
    $name = $_.Directory.Name
    $c = Get-Content $_.FullName -Raw
    $dt = ([regex]::Matches($c, '(?m)^- \[x\]')).Count
    $dft = ([regex]::Matches($c, '(?m)^- \[d\]')).Count
    $tt = ([regex]::Matches($c, '(?m)^- \[ \](?!\*)')).Count
    $ds = ([regex]::Matches($c, '(?m)^  - \[x\]')).Count
    $dfs = ([regex]::Matches($c, '(?m)^  - \[d\]')).Count
    $ts_hard = ([regex]::Matches($c, '(?m)^  - \[ \](?!\*)')).Count
    $ts_safety = ([regex]::Matches($c, '(?m)^  - \[ \]\*.*\{SOFT:SAFETY\}')).Count
    $ts_nice = ([regex]::Matches($c, '(?m)^  - \[ \]\*(?!.*\{SOFT:SAFETY\})')).Count
    "${name}: top=${dt}/${dft}/${tt} sub=${ds}/${dfs}/${ts_hard} safety=${ts_safety} nice=${ts_nice}"
} | Sort-Object
```

## CI Lint Guard

```
scripts/lint-task-markers.ps1
```

6 Kural:
- R1: `[ ]*` satirlari mutlaka `{SOFT:SAFETY}` veya `{SOFT:NICE}` icermeli
- R2: `{SOFT:...}` etiketi yalnizca `[ ]*` satirlarinda kullanilmali
- R3: `[ ]` (hard) satirlarinda `{SOFT:...}` yasak (R2 ozel hali)
- R4: `{SOFT:...}` yalnizca `SAFETY` veya `NICE` olabilir
- R5: Hard `[ ]` satirlarinda bosluklu yildiz (`- [ ] *`) yasak
- R6: `[d]` blogu altinda hard `[ ]` child varsa uyari (deferred scope kontrolu)

Severity sozlesmesi:
- FAIL (R1-R5) = exit 1, merge block
- WARN (R6) = exit 0, merge ok, dashboard'da gorunur

R6 WARN invoice-analysis-system icin beklenen davranistir (17 hard child under 7 deferred top-level).

CI komutu: `pwsh scripts/lint-task-markers.ps1`

Repo standardi: Script ciktilari ASCII-only. Turkce aciklamalar yalnizca .md dosyalarinda.

## Snapshot Artifact

Son snapshot: `reports/tasks-dashboard-2026-02-28.txt`
