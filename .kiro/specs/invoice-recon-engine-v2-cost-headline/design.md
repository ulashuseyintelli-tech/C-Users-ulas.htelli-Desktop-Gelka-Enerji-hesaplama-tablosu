# Design Document

## Overview

v2 adds an **always-on reference energy cost path** and a **supplier markup disclosure** to the existing v1 recon pipeline. The v1 pipeline (parse → split → classify → reconcile → cost → compare → report) is preserved intact; v2 inserts two new pure-function modules (`cost_engine_v2.py` and `comparator_v2.py`) and extends the existing schemas additively. The frontend `PeriodCard` component is replaced with `PeriodCardV2` that renders the four-cell cost headline as the primary row and tucks T1/T2/T3 + reconciliation into collapsible accordions.

**Design principles:**
- v1 code paths remain untouched — no renames, no signature changes, no field removals.
- New v2 fields are always emitted (null when data is missing) — no opt-in flag.
- All arithmetic uses `decimal.Decimal`; float conversion happens only at the Pydantic serialization boundary.
- SoT compliance: PTF from `hourly_market_prices`, YEKDEM from `monthly_yekdem_prices`. No reads from `market_reference_prices`.

## Architecture

### Pipeline Extension (Backend)

```
v1 pipeline (unchanged):
  parse → split → classify → reconcile → cost_engine(v1) → comparator(v1) → report_builder

v2 additions (inserted after classify, before v1 cost_engine):
  ... → classify → [cost_engine_v2.compute_period_reference_cost] → [comparator_v2.compute_markup] → v1 cost_engine → v1 comparator → report_builder(v2)
```

The `_run_pipeline` function in `router.py` gains two new calls per period:
1. `cost_engine_v2.compute_period_reference_cost(records, period, db)` — always invoked.
2. `comparator_v2.compute_markup(invoice_total_tl, reference_cost_tl, gelka_margin_multiplier)` — invoked only when both reference cost is non-null AND `declared_total_tl` is present.

### Module Map

| Module | Status | Responsibility |
|--------|--------|----------------|
| `backend/app/recon/cost_engine_v2.py` | **NEW** | `compute_period_reference_cost()` — always-on PTF+YEKDEM reference cost |
| `backend/app/recon/comparator_v2.py` | **NEW** | `compute_markup()` — supplier markup, gelka estimate, potential savings |
| `backend/app/recon/schemas.py` | **EXTEND** | Add v2 fields to `PeriodResult`, `ReconReport`; add `CostInputs`, `ReferenceEnergyCostResult`, `MarkupResult` |
| `backend/app/recon/router.py` | **EXTEND** | Wire v2 calls in `_run_pipeline`; bump `api_version` to 2 |
| `backend/app/recon/report_builder.py` | **EXTEND** | Accept v2 fields, include in multi-period summary |
| `frontend/src/recon/types.ts` | **EXTEND** | Add v2 fields to `PeriodResult`, `ReconReport` |
| `frontend/src/recon/ReconPage.tsx` | **REFACTOR** | Replace `PeriodCard` with `PeriodCardV2` (headline + accordions) |

### Unchanged Modules (no edits)

- `parser.py`, `splitter.py`, `classifier.py`, `reconciler.py`
- `cost_engine.py` (v1 — still invoked for `ptf_cost` and `yekdem_cost` fields)
- `comparator.py` (v1 — still invoked for `cost_comparison` field)
- `reconApi.ts`, `numberFormat.ts`

## Components and Interfaces

### Backend Components

| Component | File | Interface |
|-----------|------|-----------|
| `compute_period_reference_cost` | `backend/app/recon/cost_engine_v2.py` | `(records: list[HourlyRecord], period: str, db: Session) -> ReferenceEnergyCostResult` |
| `compute_markup` | `backend/app/recon/comparator_v2.py` | `(invoice_total_tl: Decimal, reference_cost_tl: Decimal, gelka_margin_multiplier: Decimal) -> MarkupResult` |
| `CostInputs` schema | `backend/app/recon/schemas.py` | Pydantic model — serialized as JSON object on `PeriodResult.cost_inputs` |
| `_run_pipeline` (extended) | `backend/app/recon/router.py` | Existing function — gains v2 calls after classify step |
| `build_report` (extended) | `backend/app/recon/report_builder.py` | Existing function — v2 fields flow through `PeriodResult` |

### Frontend Components

| Component | File | Interface |
|-----------|------|-----------|
| `PeriodCardV2` | `frontend/src/recon/ReconPage.tsx` | `({ period: PeriodResult }) => JSX.Element` |
| `CostHeadline` | `frontend/src/recon/ReconPage.tsx` | `({ period: PeriodResult }) => JSX.Element` — 4-cell row |
| `Accordion` | `frontend/src/recon/ReconPage.tsx` | `({ label: string, open: boolean, onToggle: () => void, children }) => JSX.Element` |

### Inter-Component Data Flow

```
router._run_pipeline
  ├── cost_engine_v2.compute_period_reference_cost(records, period, db)
  │     └── returns ReferenceEnergyCostResult
  ├── comparator_v2.compute_markup(invoice_total, ref_cost, margin)  [conditional]
  │     └── returns MarkupResult
  ├── PeriodResult constructed with v2 fields
  └── ReconReport.api_version = 2
```

## Data Models

### New Backend Models

```python
@dataclass
class ReferenceEnergyCostResult:
    reference_energy_cost_tl: Optional[Decimal]
    ptf_source: str  # "hourly_market_prices"
    yekdem_source: str  # "monthly_yekdem_prices"
    period_start: str  # YYYY-MM-DD (first day of period)
    period_end: str  # YYYY-MM-DD (last day of period)
    total_hours: int  # count of parsed records
    complete: bool  # True when no PTF/YEKDEM gaps
    ptf_hours_missing: int
    yekdem_missing: bool

@dataclass
class MarkupResult:
    supplier_markup_tl: Decimal
    supplier_markup_pct: Optional[Decimal]  # None when ref_cost == 0
    gelka_estimate_tl: Decimal
    potential_savings_tl: Decimal

class CostInputs(BaseModel):  # Pydantic — serialized in response
    ptf_source: str = "hourly_market_prices"
    yekdem_source: str = "monthly_yekdem_prices"
    period_start: str
    period_end: str
    total_hours: int
    complete: bool
```

### Extended Models (v2 fields added)

```python
class PeriodResult(BaseModel):
    # ... all v1 fields preserved ...
    reference_energy_cost_tl: Optional[float] = None
    supplier_markup_tl: Optional[float] = None
    supplier_markup_pct: Optional[float] = None
    gelka_estimate_tl: Optional[float] = None
    potential_savings_tl: Optional[float] = None
    cost_inputs: Optional[CostInputs] = None

class ReconReport(BaseModel):
    api_version: int = 2  # bumped from 1
    # ... all other fields unchanged ...
```

### Database Tables Read (no writes, no schema changes)

| Table | Access | Fields Used |
|-------|--------|-------------|
| `hourly_market_prices` | SELECT (bulk per period) | `period`, `date`, `hour`, `ptf_tl_per_mwh`, `is_active` |
| `monthly_yekdem_prices` | SELECT (single per period) | `period`, `yekdem_tl_per_mwh` |

No new tables. No migrations. No writes.

## Detailed Design

### 1. `cost_engine_v2.py` — Reference Energy Cost

```python
# backend/app/recon/cost_engine_v2.py

from decimal import Decimal
from dataclasses import dataclass
from typing import Optional
import logging

from sqlalchemy.orm import Session

from ..pricing.schemas import HourlyMarketPrice, MonthlyYekdemPrice
from .schemas import HourlyRecord

logger = logging.getLogger(__name__)


@dataclass
class ReferenceEnergyCostResult:
    """Return type for compute_period_reference_cost."""
    reference_energy_cost_tl: Optional[Decimal]  # None if data incomplete
    ptf_source: str  # always "hourly_market_prices"
    yekdem_source: str  # always "monthly_yekdem_prices"
    period_start: str  # YYYY-MM-DD
    period_end: str  # YYYY-MM-DD
    total_hours: int  # count of parsed hourly records
    complete: bool  # True if no PTF/YEKDEM gaps
    ptf_hours_missing: int  # count of records without PTF match
    yekdem_missing: bool  # True if YEKDEM row absent


def compute_period_reference_cost(
    records: list[HourlyRecord],
    period: str,
    db: Session,
) -> ReferenceEnergyCostResult:
    """Always-on reference energy cost computation.

    REQ-1, REQ-8.4: No invoice parameter.
    REQ-1.3/1.4/1.5: Reads exclusively from hourly_market_prices + monthly_yekdem_prices.

    Formula:
      ptf_component = Σ (record.consumption_kwh × ptf_tl_per_mwh / 1000)
      yekdem_component = total_kwh × yekdem_tl_per_mwh / 1000
      reference_energy_cost_tl = ptf_component + yekdem_component

    Fail-closed (REQ-5):
      If any record has no PTF match → null.
      If YEKDEM row missing → null.
    """
    ...  # Implementation in tasks
```

**Key decisions:**
- Single bulk query per period for PTF rows (REQ-13.2): `WHERE period = :period AND is_active = 1`.
- Single query for YEKDEM (REQ-13.3): `WHERE period = :period`.
- Returns `ReferenceEnergyCostResult` dataclass (not Pydantic) to avoid serialization overhead in internal path.
- Emits structured log at INFO level per REQ-14 (NFR-2).

### 2. `comparator_v2.py` — Markup Computation

```python
# backend/app/recon/comparator_v2.py

from decimal import Decimal
from dataclasses import dataclass
from typing import Optional


@dataclass
class MarkupResult:
    """Return type for compute_markup."""
    supplier_markup_tl: Decimal
    supplier_markup_pct: Optional[Decimal]  # None when reference_cost == 0
    gelka_estimate_tl: Decimal
    potential_savings_tl: Decimal


def compute_markup(
    invoice_total_tl: Decimal,
    reference_cost_tl: Decimal,
    gelka_margin_multiplier: Decimal,
) -> MarkupResult:
    """Compute supplier markup and Gelka estimate.

    REQ-2, REQ-8.5: Three scalar parameters.
    REQ-6.7: When reference_cost_tl == 0, markup_pct = None.

    All arithmetic in Decimal. Rounding to 2dp happens at caller (Decimal_Boundary).
    """
    ...  # Implementation in tasks
```

**Key decisions:**
- Pure function, no DB access, no side effects.
- Does NOT round internally — caller rounds at Decimal_Boundary (schemas layer).
- `supplier_markup_pct` is `None` when `reference_cost_tl == 0` (REQ-6.7).

### 3. Schema Extensions (`schemas.py`)

#### New models added:

```python
class CostInputs(BaseModel):
    """REQ-7.7: Diagnostic metadata for reference cost computation."""
    ptf_source: str = "hourly_market_prices"
    yekdem_source: str = "monthly_yekdem_prices"
    period_start: str  # YYYY-MM-DD
    period_end: str  # YYYY-MM-DD
    total_hours: int
    complete: bool
```

#### `PeriodResult` extensions (all Optional, default None):

```python
class PeriodResult(BaseModel):
    # ... existing v1 fields unchanged ...

    # v2 cost headline fields (REQ-7.2–7.7)
    reference_energy_cost_tl: Optional[float] = None
    supplier_markup_tl: Optional[float] = None
    supplier_markup_pct: Optional[float] = None
    gelka_estimate_tl: Optional[float] = None
    potential_savings_tl: Optional[float] = None
    cost_inputs: Optional[CostInputs] = None
```

#### `ReconReport` change:

```python
class ReconReport(BaseModel):
    api_version: int = 2  # bumped from 1 (REQ-7.1)
    # ... all other fields unchanged ...
```

### 4. Router Extension (`router.py`)

In `_run_pipeline`, after `classify_period_records` and before v1 `calculate_ptf_cost`:

```python
from .cost_engine_v2 import compute_period_reference_cost
from .comparator_v2 import compute_markup

# v2: Always-on reference cost
ref_result = compute_period_reference_cost(records, period, db)

# v2: Markup (only if reference cost available AND invoice total provided)
v2_markup_tl = None
v2_markup_pct = None
v2_gelka_estimate = None
v2_potential_savings = None

if ref_result.reference_energy_cost_tl is not None and invoice and invoice.declared_total_tl is not None:
    markup = compute_markup(
        invoice_total_tl=invoice.declared_total_tl,
        reference_cost_tl=ref_result.reference_energy_cost_tl,
        gelka_margin_multiplier=request.comparison.gelka_margin_multiplier,
    )
    v2_markup_tl = float(markup.supplier_markup_tl.quantize(Decimal("0.01")))
    v2_markup_pct = float(markup.supplier_markup_pct.quantize(Decimal("0.01"))) if markup.supplier_markup_pct is not None else None
    v2_gelka_estimate = float(markup.gelka_estimate_tl.quantize(Decimal("0.01")))
    v2_potential_savings = float(markup.potential_savings_tl.quantize(Decimal("0.01")))
```

Then populate `PeriodResult` with v2 fields:

```python
period_results.append(PeriodResult(
    # ... existing v1 fields ...
    reference_energy_cost_tl=float(ref_result.reference_energy_cost_tl.quantize(Decimal("0.01"))) if ref_result.reference_energy_cost_tl is not None else None,
    supplier_markup_tl=v2_markup_tl,
    supplier_markup_pct=v2_markup_pct,
    gelka_estimate_tl=v2_gelka_estimate,
    potential_savings_tl=v2_potential_savings,
    cost_inputs=CostInputs(
        ptf_source=ref_result.ptf_source,
        yekdem_source=ref_result.yekdem_source,
        period_start=ref_result.period_start,
        period_end=ref_result.period_end,
        total_hours=ref_result.total_hours,
        complete=ref_result.complete,
    ),
))
```

### 5. Frontend `PeriodCardV2` Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ [2025-01]  [UYUMLU badge]                                       │  ← header
├─────────────────────────────────────────────────────────────────┤
│ Fatura Tutarı │ Referans Enerji │ Tedarikçi Marjı │ Gelka Teklif│  ← Cost_Headline
│ 598.432,17 ₺  │ 487.210,33 ₺   │ 111.221,84 ₺    │ 511.570,85 ₺│
│               │                 │ %22,84           │ Tasarruf:    │
│               │                 │                  │ 86.861,32 ₺  │
├─────────────────────────────────────────────────────────────────┤
│ ▶ Tüketim profili (T1/T2/T3)                      [collapsed]  │
├─────────────────────────────────────────────────────────────────┤
│ ▶ Mutabakat detayı                                 [collapsed]  │
└─────────────────────────────────────────────────────────────────┘
```

**Component structure:**

```tsx
// frontend/src/recon/ReconPage.tsx (refactored section)

function PeriodCardV2({ period }: { period: PeriodResult }) {
  const [showConsumption, setShowConsumption] = useState(false);
  const [showRecon, setShowRecon] = useState(false);

  return (
    <div className="...">
      {/* Header: period label + status badge */}
      <PeriodHeader period={period} />

      {/* Cost Headline: 4-cell row */}
      <CostHeadline period={period} />

      {/* Accordion: Tüketim profili (T1/T2/T3) — collapsed by default */}
      <Accordion label="Tüketim profili (T1/T2/T3)" open={showConsumption} onToggle={...}>
        <ConsumptionProfile period={period} />
      </Accordion>

      {/* Accordion: Mutabakat detayı — collapsed by default */}
      <Accordion label="Mutabakat detayı" open={showRecon} onToggle={...}>
        <ReconciliationDetail period={period} />
      </Accordion>
    </div>
  );
}
```

**Label rules (REQ-4):**
- Cost headline cell 2 label: "Referans enerji maliyeti" (short form)
- Tooltip on hover: "EPİAŞ PTF + YEKDEM bazlı referans enerji maliyeti" (long form)
- Tight badge fallback: "PTF + YEKDEM referansı" (very short form)
- NEVER: "gerçek maliyet", "actual cost", "true cost"

**Amber treatment (REQ-5.8):**
- When `reference_energy_cost_tl === null`: cost headline cells get `bg-amber-50 border-amber-200` styling.
- When `supplier_markup_tl === null` because no invoice: show "—" placeholder (REQ-9.9).
- When `supplier_markup_tl < 0`: show "negatif marj — tedarikçi piyasa altında satmış" label (REQ-6.3).

### 6. TypeScript Type Extensions

```typescript
// frontend/src/recon/types.ts — additions

export interface CostInputs {
  ptf_source: string;
  yekdem_source: string;
  period_start: string;
  period_end: string;
  total_hours: number;
  complete: boolean;
}

export interface PeriodResult {
  // ... existing v1 fields ...

  // v2 cost headline
  reference_energy_cost_tl: number | null;
  supplier_markup_tl: number | null;
  supplier_markup_pct: number | null;
  gelka_estimate_tl: number | null;
  potential_savings_tl: number | null;
  cost_inputs: CostInputs | null;
}

export interface ReconReport {
  api_version: number;  // now 2
  // ... rest unchanged ...
}
```

### 7. Observability (NFR-2)

`cost_engine_v2.compute_period_reference_cost` emits one structured log per period:

```python
logger.info(
    "recon_v2_reference_cost",
    extra={
        "period": period,
        "total_hours": len(records),
        "complete": ref_result.complete,
        "reference_energy_cost_present": ref_result.reference_energy_cost_tl is not None,
        "ptf_hours_missing": ref_result.ptf_hours_missing,
        "yekdem_missing": ref_result.yekdem_missing,
    },
)
```

No PII (customer name, supplier name, tariff group) in log entries (NFR-2.5).
No "gerçek maliyet" / "actual cost" / "true cost" in log messages (NFR-2.4).

### 8. Performance Strategy (NFR-1)

- PTF bulk query: single `SELECT ... WHERE period = :period AND is_active = 1` per period (already indexed by v1 schema).
- YEKDEM: single `SELECT ... WHERE period = :period` per period.
- No N+1 queries — same pattern as v1 `calculate_ptf_cost`.
- Target: ≤200ms additional latency at p95 over v1 baseline.
- Measurement: feature flag `RECON_V2_COST_PATH` (env var, default `on`). When `off`, v2 calls are skipped — enables A/B timing comparison.

### 9. Backward Compatibility (REQ-10)

- `api_version` bumped from 1 to 2 — only breaking change for strict version checkers.
- All v1 fields remain present with identical semantics.
- v1 `cost_comparison` field still populated when invoice unit price + distribution price are provided (v1 comparator still runs).
- Request body shape unchanged — no new required fields.
- Frontend reuses `analyzeRecon`, `ReconRequest`, `numberFormat.ts`, `parseTurkishNumber` without changes.

## Testing Strategy

| Layer | Type | Coverage |
|-------|------|----------|
| `cost_engine_v2` | Unit (example-based) | REQ-11.2, REQ-11.5, REQ-11.6 |
| `cost_engine_v2` | PBT (hypothesis) | REQ-11.8, REQ-11.9 |
| `comparator_v2` | Unit (example-based) | REQ-11.3, REQ-11.4 |
| `comparator_v2` | Unit (edge cases) | REQ-6.7 (zero ref cost) |
| Router integration | Golden snapshot | REQ-11.10 (v2 Cansu Su fixture) |
| Router integration | Regression | REQ-11.1 (all v1 tests pass) |
| Frontend | Vitest + RTL | REQ-11.7 (headline order, accordion collapsed) |

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| PTF bulk query slow for large periods (744 hours) | p95 latency breach | Already indexed; v1 uses same query pattern without issues |
| v1 golden snapshot breaks due to `api_version` bump | CI red | Update v1 snapshot to expect `api_version: 2` (additive, not breaking) |
| Negative markup confuses operators | UX confusion | Explicit label "negatif marj — tedarikçi piyasa altında satmış" (REQ-6.3) |
| `declared_total_tl` field reuse ambiguity | Wrong markup calc | Field semantics documented in Glossary; no new field introduced |

## Correctness Properties

### Property 1: Non-negativity
`reference_energy_cost_tl >= 0` when all consumption, PTF, YEKDEM values are non-negative. Verified by PBT (REQ-11.8).

**Validates: Requirements 1.2, 11.8**

### Property 2: Monotonicity
Increasing any single hourly consumption (with fixed non-negative PTF/YEKDEM) does not decrease `reference_energy_cost_tl`. Verified by PBT (REQ-11.9).

**Validates: Requirements 1.2, 11.9**

### Property 3: Markup identity
`supplier_markup_tl == declared_total_tl - reference_energy_cost_tl` (exact Decimal equality before rounding). Verified by unit test (REQ-11.3).

**Validates: Requirements 2.1, 11.3**

### Property 4: Savings identity
`potential_savings_tl == declared_total_tl - gelka_estimate_tl`. Verified by unit test.

**Validates: Requirements 2.4**

### Property 5: Gelka estimate
`gelka_estimate_tl == reference_energy_cost_tl × gelka_margin_multiplier`. Verified by unit test.

**Validates: Requirements 2.3**

### Property 6: Null cascade
`reference_energy_cost_tl == null` ⟹ all markup fields (`supplier_markup_tl`, `supplier_markup_pct`, `gelka_estimate_tl`, `potential_savings_tl`) are null. Verified by unit test (REQ-11.6).

**Validates: Requirements 5.3, 11.6**

### Property 7: v1 preservation
All v1 test assertions pass unchanged after v2 merge. Verified by regression suite (REQ-11.1).

**Validates: Requirements 10.1, 10.2, 11.1**

### Property 8: Terminology guard
No occurrence of "gerçek maliyet" / "actual cost" / "true cost" in recon module source files or log output. Verified by grep guard + unit test.

**Validates: Requirements 4.4, 4.5, 14.4**

## Error Handling

| Scenario | Behaviour | HTTP Status |
|----------|-----------|-------------|
| PTF data missing for ≥1 hour in period | `reference_energy_cost_tl = null`, `quote_blocked = true`, `quote_block_reason` populated | 200 (status="partial") |
| YEKDEM data missing for period | Same as above | 200 (status="partial") |
| `declared_total_tl` not provided | Markup fields all null, no error | 200 |
| `reference_energy_cost_tl == 0` and invoice provided | `supplier_markup_pct = null`, `supplier_markup_tl = declared_total_tl - 0` | 200 |
| DB connection failure during PTF/YEKDEM query | Exception propagates to router, caught by generic handler | 500 |
| `cost_engine_v2` raises unexpected exception | Caught in router, logged, returns 500 error response | 500 |

**Fail-closed principle:** Missing market data never produces a fabricated cost number. The system returns null and explains why via `quote_block_reason`. The rest of the report (parse stats, T1/T2/T3, reconciliation) is still returned.
