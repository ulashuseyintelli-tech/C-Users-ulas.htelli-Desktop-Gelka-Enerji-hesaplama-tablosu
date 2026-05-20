# Phase A — Operational Acceptance Validation

> **Status:** Release Candidate Validation
> **Scope:** invoice-recon-engine (`POST /api/recon/analyze` + `frontend/src/recon/ReconPage`)
> **Principle:** "Çalışıyor gibi" ≠ operational acceptance. Her senaryo PASS/FAIL kriterine göre değerlendirilir; sübjektif "iyi görünüyor" yargısı kabul edilmez.

## Yapısı

| Dosya | Amaç |
|---|---|
| `RUN-001-checklist.md` | Tek bir validation run için PASS/FAIL checklist (artifact, freeze edilir) |
| `metadata.json` | Run metadata (SHA, timestamp, env, dataset hash) |
| `bug-list.md` | Phase A sırasında tespit edilen bug'lar — Phase B sprint girdisi |
| `screenshots/` | Manuel doğrulama gerektiren senaryolar için ekran kanıtı (özellikle status="partial") |

## Workflow

1. **Pre-flight** — Backend SHA, frontend SHA, dataset hash freeze'i (metadata.json içinde)
2. **Server start** — Backend (uvicorn) + Frontend (vite) ayrı terminallerde
3. **Walk-through** — RUN-XXX-checklist.md içindeki 14 senaryo sırayla
4. **Result capture** — Her senaryo için PASS/FAIL + elapsed time + observation note
5. **Freeze** — Run tamamlanınca checklist commit edilir; tekrar kullanılmaz (yeni run = yeni RUN-XXX)
6. **Bug triage** — bug-list.md → Phase B (bugfix sprint)

## Artifact freeze kuralı

- Bir RUN-XXX-checklist.md dosyası tamamlandıktan sonra **değiştirilmez**
- Aynı sistem yeniden test edilecekse yeni run dosyası açılır (RUN-002, RUN-003...)
- Bu sayede: regression görünür, audit izi var, "test ediliyordu galiba" yargısı imkânsız
