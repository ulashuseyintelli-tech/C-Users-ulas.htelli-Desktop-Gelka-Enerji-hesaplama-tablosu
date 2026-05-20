# Phase A — Bug List (RUN-001)

> Phase A sırasında tespit edilen bug'lar. Phase B (bugfix sprint) girdisi.
> Format: bir bug = bir entry. Run tamamlandıktan sonra freeze.

## Severity rubrik

| Level | Tanım |
|---|---|
| **P0 / blocker** | Kategori A,B,C senaryolarından biri FAIL — sistem operational acceptable değil. Pilot için stop. |
| **P1 / critical** | Kategori D,E senaryolarından sistematik FAIL — kullanıcı deneyimi kabul edilemez ama veri akışı doğru. |
| **P2 / minor** | UX glitch, edge case, kozmetik. Phase B'ye opsiyonel. |

## Template

```
### BUG-001
- Scenario: S-XX
- Severity: P0|P1|P2
- Reproduction: 1) ... 2) ... 3) ...
- Expected: ...
- Actual: ...
- Module guess: backend/app/recon/... | frontend/src/recon/...
- Screenshot: screenshots/...png (varsa)
- Notes: ...
```

---

## Bugs

_(henüz tespit edilmedi — Phase A walk-through başladıktan sonra doldurulur)_


---

## RUN-001 — Erken sonlandırma kararı (2026-05-17)

**Durum:** SUSPENDED — `invoice-recon-engine-v2-cost-headline` spec'i tamamlanana kadar devam ettirilmeyecek.

**Sebep:** Operational acceptance sırasında ortaya çıktı ki mevcut UI'ın bilgi hiyerarşisi yanlış. Tool'un asıl değer önerisi (mevcut fatura vs PTF-bazlı referans enerji maliyeti vs Gelka teklifi) hâlâ FE'de **secondary** durumda — primary olan T1/T2/T3 + mutabakat. Phase A'yı bu yapı üzerinden bitirmek "yanlış UX'i sertifikalamak" olur.

**Tamamlanan adımlar (kayıt için kalsın):**
- ✅ Pre-flight P1–P4 (backend + frontend up)
- ✅ S-01 PASS (parse + 4 dönem render)
- ✅ S-02 PASS (delta_kwh ≈ 0, severity LOW, gerçek BKA Ocak faturasıyla mutabakat birebir)
- ⏸ S-03–S-14 yürütülmedi

**Phase B'ye taşınan bug'lar (BUG-001, BUG-002):**
- BUG-001 (P2): kWh sayıları FE'de 2 desimal gösteriliyor (örn. "41.397,41 kWh"), olması gereken 3 desimal ("41.397,409 kWh"). Backend doğru, sadece FE format. — *v2 spec içinde çözülecek (yeni FE iskeletinde).*
- BUG-002 (P2): Eski form (number input) TR sayı formatı kabul etmiyordu. **HOTFIX UYGULANDI** — `parseTurkishNumber` helper + `inputMode="decimal"` + `<input type="month">`. 20/20 vitest yeşil. — *v2 iskeletine taşınacak.*

**Sıradaki:** `invoice-recon-engine-v2-cost-headline` spec'i (Requirements-First).
