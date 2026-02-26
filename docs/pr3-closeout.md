# PR-3 Closeout — PDF Render Endpoint Observability

## Summary

| Metric | Value |
|--------|-------|
| PR-3 tests | 15 |
| PR-2 tests | 19 |
| Combined | 34/34 PASSED |
| Duration | ~18s |
| Regressions | 0 |
| PR-3 test file | `backend/tests/test_pr3_observability.py` |
| PR-2 test file | `backend/tests/test_pr2_closure.py` |

Verdict: PR-3 merge-ready. Combined regression run confirms zero breakage.

---

## Scope

PR-3 adds 8 Prometheus metrics to the `POST /generate-pdf-simple` endpoint, covering all 4 exit paths (200, 429, 500, 504). Changes are isolated to:

- `backend/app/ptf_metrics.py` — metric registration + accessor methods
- `backend/app/main.py` — endpoint instrumentation (generate_pdf_simple)
- `backend/tests/test_pr3_observability.py` — contract test suite

---

## Evidence by Contract Area

### A) Label Boundedness (5 tests)

`status` labels: closed set {200, 429, 500, 504} — invalid values silently rejected.
`reason` labels: closed set {empty_pdf, timeout, internal_error} — 429 is intentional rejection, not an error.

### B) Inflight Safety (2 tests)

- inc/dec symmetry: balanced calls return gauge to 0
- Under concurrent load, inflight never exceeds `_PDF_MAX_CONCURRENT` (2)

### C) Acquire Histogram on 429 (1 test)

Semaphore acquire duration is observed on both success and 429 timeout paths. 5 concurrent requests → histogram `_count` = 5 (not just successful acquires).

### D) Arithmetic Consistency (1 test)

`overhead = max(0, total - acquire - executor)` — clamped to avoid negative values from timer resolution noise. Test verifies `overhead_sum >= 0`.

### E) Bytes Metric (2 tests)

- 200 path: observes actual PDF byte count (> 0)
- 500 (empty_pdf) path: observes 0 bytes

### F) Metric Registration (2 tests)

All 8 metrics appear in both isolated registry output and live `/metrics` endpoint.

### G) Request Counter Accuracy (2 tests)

- 3 sequential 200s → `requests_total{status="200"}` = 3.0
- Exception path → `requests_total{status="500"}` = 1.0

---

## 429 Path Instrumentation Scope

On 429 (semaphore acquire timeout), the following metrics are observed:

| Metric | Observed | Reason |
|--------|----------|--------|
| `semaphore_acquire_seconds` | Yes | Captures ~2s timeout wait |
| `requests_total{status="429"}` | Yes | Response counter |
| `total_seconds` | Yes | Entry-to-response duration |
| `bytes` | Yes (0) | No PDF produced |
| `inflight` | No | Semaphore not acquired |
| `executor_seconds` | No | Executor never ran |
| `overhead_seconds` | No | No render pipeline to attribute overhead |

This is by design: 429 means the request was rejected before entering the render pipeline.

---

## Overhead Computation

```python
overhead = max(0.0, total - acquire - executor)
```

The `max(0, ...)` clamp prevents negative values caused by:
- Timer resolution differences between `time.monotonic()` calls
- OS scheduling noise between measurement points
- Async context switch overhead between timer reads

No epsilon tolerance is needed — the clamp itself is the safeguard.

---

## Known Follow-Up: PR-4 SLO Query Alignment

`CANONICAL_PDF_SLO_QUERY` in `backend/app/adaptive_control/config.py` references `pdf_render_duration_seconds_bucket`. No metric with this name exists in the Prometheus registry. This is not a rename mismatch — the metric was never created under that name.

Files requiring update in PR-4:

| # | File | What to change |
|---|------|---------------|
| 1 | `backend/app/adaptive_control/config.py` | Update `CANONICAL_PDF_SLO_QUERY` constant |
| 2 | `backend/tests/test_adaptive_config.py` | Update SLO query assertion (line 265) |
| 3 | `.kiro/specs/slo-adaptive-control/design.md` | Update config dataclass example |
| 4 | `.kiro/specs/slo-adaptive-control/requirements.md` | Update Req 2.3 canonical signal definition |
| 5 | `.kiro/specs/pdf-render-worker/requirements.md` | Update Req 5.2 metric name |

PR-4 decision:
- SLO target: `ptf_admin_pdf_render_total_seconds` (end-to-end user experience)
- Capacity tuning: `ptf_admin_pdf_render_executor_seconds` (CPU/IO bottleneck)
- Overhead: informational only, no SLO, no alerts

---

## Alert / Dashboard Wiring

PR-3 metrics are not yet wired to Prometheus alert rules or Grafana dashboards. This is intentional:

- Alert thresholds require production baseline data (p95/p99 under real load)
- Dashboard panels will be added after initial data collection confirms metric shape
- No existing alerts or dashboards reference `pdf_render` — zero breakage risk

Wiring will be done in a follow-up PR after baseline collection.

---

## Metric Namespace Collision Check

All PR-3 metrics use the `ptf_admin_pdf_render_` prefix. Verified no collision with existing `ptf_admin_` metrics:

- `ptf_admin_pdf_jobs_total` (PDF Worker) — different prefix segment (`pdf_jobs` vs `pdf_render`)
- `ptf_admin_pdf_job_failures_total` (PDF Worker) — different prefix segment
- `ptf_admin_pdf_job_duration_seconds` (PDF Worker) — different prefix segment
- `ptf_admin_pdf_queue_depth` (PDF Worker) — different prefix segment

No overlap. Safe to merge.

---

## Run Command

```bash
# PR-3 only
python -m pytest backend/tests/test_pr3_observability.py -v --tb=short

# PR-2 + PR-3 combined regression
python -m pytest backend/tests/test_pr2_closure.py backend/tests/test_pr3_observability.py -v --tb=short
```
