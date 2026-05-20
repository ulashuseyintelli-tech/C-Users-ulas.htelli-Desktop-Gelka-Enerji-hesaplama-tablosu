# Implementation Plan:

## Overview

v2 cost headline feature for the invoice recon engine. Adds always-on reference energy cost computation, supplier markup disclosure, schema bump to api_version=2, and frontend PeriodCardV2 with cost headline + accordion layout. All v1 behaviour preserved.

## Tasks

- [x] 1. Schema Extensions — Add v2 fields to PeriodResult and ReconReport in `backend/app/recon/schemas.py`. Add `CostInputs` model (ptf_source, yekdem_source, period_start, period_end, total_hours, complete). Add Optional v2 fields to PeriodResult (reference_energy_cost_tl, supplier_markup_tl, supplier_markup_pct, gelka_estimate_tl, potential_savings_tl, cost_inputs). Bump ReconReport.api_version default to 2. Run v1 tests to confirm no regressions.
  Requirements: REQ-7.1, REQ-7.2, REQ-7.3, REQ-7.4, REQ-7.5, REQ-7.6, REQ-7.7, REQ-7.8, REQ-10.1, REQ-10.2

- [x] 2. Implement `cost_engine_v2.compute_period_reference_cost` — Create `backend/app/recon/cost_engine_v2.py`. Define ReferenceEnergyCostResult dataclass. Implement function with signature `(records: list[HourlyRecord], period: str, db: Session) -> ReferenceEnergyCostResult`. Bulk-load PTF from hourly_market_prices (single query). Load YEKDEM from monthly_yekdem_prices (single query). Fail-closed: null if any PTF hour missing or YEKDEM absent. Emit structured INFO log per NFR-2. No reads from market_reference_prices.
  Requirements: REQ-1.1, REQ-1.2, REQ-1.3, REQ-1.4, REQ-1.5, REQ-1.6, REQ-1.7, REQ-5.1, REQ-5.2, REQ-8.4, REQ-8.6, REQ-13.2, REQ-13.3, REQ-14.1, REQ-14.2, REQ-14.3, REQ-14.4, REQ-14.5

- [x] 3. Implement `comparator_v2.compute_markup` — Create `backend/app/recon/comparator_v2.py`. Define MarkupResult dataclass. Implement function with signature `(invoice_total_tl: Decimal, reference_cost_tl: Decimal, gelka_margin_multiplier: Decimal) -> MarkupResult`. Pure function, Decimal arithmetic, no rounding (caller rounds). Handle edge: ref_cost==0 → markup_pct=None.
  Requirements: REQ-2.1, REQ-2.2, REQ-2.3, REQ-2.4, REQ-2.5, REQ-2.6, REQ-6.7, REQ-8.5, REQ-8.6

- [x] 4. Wire v2 calls into router pipeline — In `backend/app/recon/router.py` `_run_pipeline`, after classify step: invoke compute_period_reference_cost for every period, conditionally invoke compute_markup when ref cost non-null AND declared_total_tl present. Round at Decimal_Boundary. Populate PeriodResult v2 fields. Merge quote_blocked logic (OR with v1). Update report_status to "partial" when any period has null reference cost.
  Requirements: REQ-8.1, REQ-8.2, REQ-8.3, REQ-5.3, REQ-5.4, REQ-5.5, REQ-5.6, REQ-5.7, REQ-6.1, REQ-10.3

- [x] 5. Update report_builder for v2 multi-period summary — Extend `_build_multi_period_summary` in `backend/app/recon/report_builder.py` to include total_reference_energy_cost_tl, total_supplier_markup_tl, total_gelka_estimate_tl, total_potential_savings_tl (sum of non-null periods). Preserve all v1 summary fields.
  Requirements: REQ-7.8, REQ-10.2

- [x] 6. Backend unit tests for cost_engine_v2 — Create `backend/tests/test_recon_cost_engine_v2.py`. Test: ref cost computed when all PTF+YEKDEM present. Test: ref cost None when PTF missing. Test: ref cost None when YEKDEM missing. Test: works without invoice input. Test: structured log emitted. Test: no market_reference_prices read.
  Requirements: REQ-11.1, REQ-11.2, REQ-11.5, REQ-11.6

- [x] 7. Backend unit tests for comparator_v2 — Create `backend/tests/test_recon_comparator_v2.py`. Test: positive markup. Test: negative markup (no clipping). Test: zero ref cost → markup_pct None. Test: gelka_estimate formula. Test: potential_savings formula.
  Requirements: REQ-11.3, REQ-11.4, REQ-6.7

- [x] 8. Backend PBT tests — Create `backend/tests/test_recon_v2_pbt.py`. Property: reference_energy_cost_tl >= 0 for non-negative inputs. Property: monotonicity when increasing consumption with fixed PTF/YEKDEM.
  Requirements: REQ-11.8, REQ-11.9

- [x] 9. Backend golden snapshot test — Create `backend/tests/test_recon_v2_golden.py`. Add v2 golden snapshot for Cansu Su / BKA Enerji fixture with all REQ-7 fields. Update v1 snapshot api_version expectation to 2.
  Requirements: REQ-11.10, REQ-11.1

- [x] 10. Frontend TypeScript type extensions — Add CostInputs interface and v2 fields to PeriodResult in `frontend/src/recon/types.ts`. Update api_version comment. No changes to reconApi.ts or numberFormat.ts.
  Requirements: REQ-10.4, REQ-7.2, REQ-7.3, REQ-7.4, REQ-7.5, REQ-7.6, REQ-7.7

- [x] 11. Frontend PeriodCardV2 implementation — Replace PeriodCard with PeriodCardV2 in `frontend/src/recon/ReconPage.tsx`. Render header (period + badge), CostHeadline (4-cell row: Invoice, Reference, Markup, Gelka), Accordion "Tüketim profili (T1/T2/T3)" collapsed, Accordion "Mutabakat detayı" collapsed. Apply terminology lock labels. Amber treatment for null ref cost. Dash placeholder for null markup. Negative markup label. Turkish locale formatting.
  Requirements: REQ-3.1, REQ-3.2, REQ-3.3, REQ-3.4, REQ-3.5, REQ-3.6, REQ-4.1, REQ-4.2, REQ-4.3, REQ-4.4, REQ-5.8, REQ-5.9, REQ-6.2, REQ-6.3, REQ-6.4, REQ-6.5, REQ-6.6, REQ-9.1, REQ-9.2, REQ-9.3, REQ-9.4, REQ-9.5, REQ-9.6, REQ-9.7, REQ-9.8, REQ-9.9

- [x] 12. Frontend vitest for PeriodCardV2 — Create `frontend/src/recon/__tests__/PeriodCardV2.test.tsx`. Test: 4 headline cells in order. Test: accordions default collapsed. Test: toggle works. Test: amber for null ref cost. Test: dash placeholder. Test: negative markup label. Test: no "gerçek maliyet" in output.
  Requirements: REQ-11.7

- [x] 13. v1 regression verification and terminology guard — Run full v1 recon test suite (all pass). Verify cost_comparison still populated. Verify request body unchanged. Grep guard: no "gerçek maliyet" / "actual cost" / "true cost" in backend/app/recon/.
  Requirements: REQ-10.1, REQ-10.2, REQ-11.1, REQ-7.8, REQ-7.9, REQ-4.5

## Task Dependency Graph

```json
{
  "waves": [
    {
      "name": "Wave 1 — Foundation",
      "tasks": [1],
      "description": "Schema extensions (all v2 fields Optional with None defaults)"
    },
    {
      "name": "Wave 2 — Core Logic",
      "tasks": [2, 3, 10],
      "description": "cost_engine_v2, comparator_v2, FE type extensions (parallel, depend on Task 1)"
    },
    {
      "name": "Wave 3 — Integration",
      "tasks": [4, 5, 11],
      "description": "Router wiring, report_builder update, FE PeriodCardV2 (depend on Wave 2)"
    },
    {
      "name": "Wave 4 — Testing",
      "tasks": [6, 7, 8, 9, 12],
      "description": "Unit tests, PBT, golden snapshot, FE vitest (depend on Wave 3)"
    },
    {
      "name": "Wave 5 — Verification",
      "tasks": [13],
      "description": "v1 regression, terminology guard (depends on all previous)"
    }
  ]
}
```

## Notes

- All backend arithmetic uses `decimal.Decimal`; float conversion only at Pydantic serialization boundary (schemas.py).
- SoT compliance: PTF from `hourly_market_prices`, YEKDEM from `monthly_yekdem_prices`. NEVER `market_reference_prices`.
- v1 code paths (cost_engine.py, comparator.py, reconciler.py) remain untouched.
- Feature flag `RECON_V2_COST_PATH` (env var, default "on") can bypass v2 calls for A/B perf measurement.
- Terminology lock: "gerçek maliyet" / "actual cost" / "true cost" forbidden in all recon module code and UI.
