# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** — Pricing Consistency Defects (Dual Margin, Net Margin, YEKDEM 404)
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bugs exist
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate all 4 bugs exist in the current codebase
  - **Scoped PBT Approach**: Use concrete failing cases for deterministic bugs, property-based for formula invariants
  - Test file: `backend/tests/test_pricing_consistency_bug_condition.py`
  - **Bug C1 — Single Margin (no dual model):**
    - `isBugCondition`: system produces only `gross_margin_tl_per_mwh` (single), no `gross_margin_energy_per_mwh` / `gross_margin_total_per_mwh` fields
    - `expectedBehavior`: PricingSummary MUST have both `gross_margin_energy_per_mwh` and `gross_margin_total_per_mwh`; when distribution > 0, `gross_margin_energy_per_mwh > gross_margin_total_per_mwh`
    - Test: create AnalyzeRequest with PTF=1500, YEKDEM=150, multiplier=1.15, distribution=0.81 TL/kWh → assert response has dual margin fields and dual sales price fields (`sales_energy_price_per_mwh`, `sales_effective_price_per_mwh`)
  - **Bug C2 — Incomplete Net Margin:**
    - `isBugCondition`: `net_margin_per_mwh = gross_margin - dealer` (missing distribution and imbalance deductions)
    - `expectedBehavior`: `net_margin_per_mwh = gross_margin_total - dealer - imbalance` (complete formula)
    - Test: with known PTF, YEKDEM, distribution, imbalance, dealer → assert `net_margin_per_mwh` equals `gross_margin_total_per_mwh - dealer_commission_per_mwh - imbalance_cost_per_mwh`
  - **Bug C3 — Frontend Hardcode (structural):**
    - Skip PBT for this — frontend hardcode is a structural issue, tested via code inspection in implementation
  - **Bug C4 — YEKDEM 404:**
    - `isBugCondition`: YEKDEM record missing for period → HTTP 404
    - `expectedBehavior`: HTTP 200 with yekdem=0 and warning with `severity: "high"`, `impact: "pricing_accuracy_low"`
    - Test: call analyze endpoint with period that has no YEKDEM → assert status 200, yekdem=0 in supplier_cost, warning present
  - **Safety Guards (v3.1):**
    - Property: `dealer_commission_total_tl >= 0` AND `dealer_commission_total_tl <= max(0, gross_margin_energy_total_tl)`
    - Property: `imbalance_cost_per_mwh >= weighted_ptf * 0.01` (RISK_FLOOR)
    - Property: if `net_margin_total_tl < 0` → `risk_flags` includes `LOSS_RISK` with priority 1
    - Property: if `gross_margin_total_per_mwh < 0` → `risk_flags` includes `UNPROFITABLE_OFFER` with priority 2
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bugs exist)
  - Document counterexamples found to understand root cause
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.5, 1.6, 1.9, 1.10, 2.1, 2.2, 2.3, 2.5, 2.6, 2.10, 2.11_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** — Existing Pricing Behavior Unchanged for Non-Bug Inputs
  - **IMPORTANT**: Follow observation-first methodology
  - Test file: `backend/tests/test_pricing_consistency_preservation.py`
  - **Observation Phase** (run on UNFIXED code, record actual outputs):
    - Observe: `calculate_hourly_costs(...)` with valid PTF, YEKDEM, consumption → record `total_base_cost_tl`, `total_sales_revenue_tl`, `total_gross_margin_tl`
    - Observe: `calculate_weighted_prices(...)` → record `weighted_ptf_tl_per_mwh`, `total_consumption_kwh`
    - Observe: dealer commission calculation → record `total_dealer_commission_tl` for given `dealer_commission_pct`
    - Observe: cache behavior → same params → `cache_hit=True`
  - **Preservation Properties** (non-bug-condition: YEKDEM exists, valid inputs):
    - Property: `base_cost_tl = kWh × (PTF + YEKDEM) / 1000` per hour (saatlik hesaplama korunur) — from Preservation Req 3.2
    - Property: weighted PTF = `Σ(kWh_h × PTF_h) / Σ(kWh_h)` (ağırlıklı ortalama korunur) — from Preservation Req 3.1
    - Property: dealer commission model unchanged (puan paylaşımı, maliyet tabanı) — from Preservation Req 3.3
    - Property: `calculator.py` untouched (scope dışı) — from Preservation Req 3.4
    - Property: admin endpoints unchanged — from Preservation Req 3.5
    - Property: YEKDEM existing periods → same calculation logic — from Preservation Req 3.6
    - Property: frontend BTV, KDV, tasarruf calculations unchanged — from Preservation Req 3.7
    - Property: cache mechanism unchanged — from Preservation Req 3.8
  - **PBT Strategy**: Generate random valid PTF (100–5000), YEKDEM (50–500), consumption (1–1000 kWh), multiplier (1.01–2.0), dealer_pct (0–10)
    - For all generated inputs: `total_base_cost ≈ Σ(kWh × (PTF + YEKDEM) / 1000)` within ±0.02 TL
    - For all generated inputs: `total_gross_margin_tl` (backward-compat alias) == `gross_margin_energy_total_tl` == `total_sales - total_base_cost` (energy gross margin, NOT total commercial margin)
    - For all generated inputs: weighted PTF formula invariant holds
  - Verify tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 3. Backend Models — Add dual margin, dual price, safety guard fields to models.py

  - [x] 3.1 Add dual margin and dual price fields to HourlyCostResult
    - Add `gross_margin_energy_total_tl: float` — Satış - (PTF + YEKDEM)
    - Add `gross_margin_total_total_tl: float` — Satış - (PTF + YEKDEM + Dağıtım)
    - Add `distribution_cost_total_tl: float = 0.0`
    - Add `imbalance_cost_total_tl: float = 0.0`
    - Add `dealer_commission_total_tl: float = 0.0`
    - Backward compat: keep `total_gross_margin_tl` (backward-compat alias → `gross_margin_energy_total_tl`, NOT total commercial margin)
    - Backward compat: keep `total_net_margin_tl` (backward-compat alias → `net_margin_total_tl`)
    - _Bug_Condition: C1 — system produces single gross_margin, no dual model_
    - _Expected_Behavior: HourlyCostResult has energy + total gross margin fields_
    - _Preservation: existing total_gross_margin_tl and total_net_margin_tl fields still work via alias_
    - _Requirements: 2.1, 2.2, 3.2_

  - [x] 3.2 Add dual sales price, dual margin, cost breakdown, customer savings, and risk flags to PricingSummary
    - Add `sales_energy_price_per_mwh: float` — (PTF+YEKDEM) × katsayı
    - Add `sales_effective_price_per_mwh: float` — enerji fiyatı + dağıtım
    - Add `gross_margin_energy_per_mwh: float`
    - Add `gross_margin_total_per_mwh: float`
    - Add `distribution_cost_per_mwh: float = 0.0`
    - Add `imbalance_cost_per_mwh: float = 0.0`
    - Add `dealer_commission_per_mwh: float` (rename from `dealer_commission_tl_per_mwh`)
    - Add `customer_savings_per_mwh: Optional[float] = None`
    - Add `customer_reference_price_per_mwh: Optional[float] = None`
    - Add `customer_reference_price_source: Optional[str] = None` — "invoice" | "manual_input" | "market_estimate"
    - Add `risk_flags: list[dict] = Field(default_factory=list)` — priority ordered: P1 LOSS_RISK, P2 UNPROFITABLE_OFFER
    - Keep backward compat: `sales_price_tl_per_mwh` → alias to `sales_energy_price_per_mwh`
    - Keep backward compat: `gross_margin_tl_per_mwh` → alias to `gross_margin_energy_per_mwh`
    - Keep backward compat: `net_margin_tl_per_mwh` → backward-compat alias to canonical `net_margin_per_mwh`
    - **Canonical naming convention:**
      - `net_margin_per_mwh` = canonical new field
      - `net_margin_tl_per_mwh` = backward-compatible alias
      - `net_margin_total_tl` = canonical new total
      - `total_net_margin_tl` = backward-compatible alias
    - _Bug_Condition: C1 — single sales price, single margin; C2 — incomplete net margin_
    - _Expected_Behavior: dual prices, dual margins, complete net margin, risk flags, customer savings_
    - _Preservation: backward compat aliases for existing field names_
    - _Requirements: 2.1, 2.3, 2.5, 2.6_

- [x] 4. Pricing Engine — Implement dual margin, safety guards in pricing_engine.py

  - [x] 4.1 Add `distribution_unit_price_tl_per_kwh` parameter to `calculate_hourly_costs`
    - New parameter: `distribution_unit_price_tl_per_kwh: float = 0.0`
    - Calculate: `distribution_cost_total = dist_price * total_consumption_kwh`
    - Calculate: `dist_per_mwh = dist_price * 1000`
    - _Bug_Condition: C1 — distribution cost not integrated into engine_
    - _Expected_Behavior: engine receives distribution price and computes distribution cost_
    - _Preservation: default 0.0 means existing callers unchanged_
    - _Requirements: 2.1, 2.2, 3.2_

  - [x] 4.2 Implement dual gross margin calculation
    - `gross_margin_energy = total_sales - total_base_cost` (enerji brüt marjı)
    - `gross_margin_total = total_sales - total_base_cost - distribution_cost_total` (toplam brüt marj)
    - Set both on HourlyCostResult
    - Invariant: `gross_margin_energy >= gross_margin_total` when distribution >= 0
    - _Bug_Condition: C1 — only single gross_margin computed_
    - _Expected_Behavior: both energy and total gross margins produced_
    - _Requirements: 2.1, 2.2_

  - [x] 4.3 Implement safety guards (dealer commission cap + imbalance floor)
    - Dealer commission cap: `dealer_commission = max(0, min(dealer_commission, gross_margin_energy))`
    - Imbalance floor (per-MWh): `RISK_FLOOR = 0.01; imbalance_cost_per_mwh = max(calculated, weighted_ptf * RISK_FLOOR)`
    - Imbalance total: `imbalance_share = imbalance_cost_per_mwh * total_consumption / 1000`
    - _Bug_Condition: no safety guards — dealer can exceed margin, imbalance can be zero_
    - _Expected_Behavior: dealer capped to energy margin, imbalance has minimum floor_
    - _Requirements: 2.1, 2.5_

  - [x] 4.4 Implement complete net margin formula
    - `net_margin = gross_margin_total - dealer_commission - imbalance_share`
    - This replaces: `net_margin = gross_margin - dealer - imbalance` (which missed distribution)
    - Set `net_margin_total_tl` on HourlyCostResult
    - _Bug_Condition: C2 — net margin missing distribution deduction_
    - _Expected_Behavior: net margin includes all 5 cost components_
    - _Requirements: 2.5, 2.6_

  - [x] 4.5 Set all new fields on HourlyCostResult return value
    - `gross_margin_energy_total_tl`, `gross_margin_total_total_tl`
    - `distribution_cost_total_tl`, `imbalance_cost_total_tl`, `dealer_commission_total_tl`
    - `net_margin_total_tl` (updated formula)
    - Keep backward compat: `total_gross_margin_tl` still set (alias → `gross_margin_energy_total_tl`)
    - Keep backward compat: `total_net_margin_tl` still set (alias → `net_margin_total_tl`)
    - _Requirements: 2.1, 2.2, 2.5_

- [x] 5. Router — Integrate distribution lookup, dual pricing, YEKDEM graceful, risk flags, new endpoints

  - [x] 5.1 YEKDEM graceful fallback in analyze endpoint
    - Replace `raise HTTPException(404)` with: `yekdem = 0.0` + warning
    - Warning format: `{"type": "critical_missing_data", "severity": "high", "impact": "pricing_accuracy_low", "message": "...", "yekdem_unit_price": 0}`
    - Move warnings list initialization BEFORE YEKDEM check
    - _Bug_Condition: C4 — YEKDEM missing → 404_
    - _Expected_Behavior: HTTP 200, yekdem=0, severity warning_
    - _Requirements: 2.10, 2.11_

  - [x] 5.2 YEKDEM graceful fallback in simulate endpoint
    - Same pattern: `yekdem = 0.0` + warning instead of 404
    - _Bug_Condition: C4 — simulate also throws 404_
    - _Expected_Behavior: simulate continues with yekdem=0_
    - _Requirements: 2.12_

  - [x] 5.3 YEKDEM graceful fallback in compare endpoint
    - Per-period: if YEKDEM missing for a period, use 0 + warning instead of skipping period
    - _Bug_Condition: C4 — compare skips periods without YEKDEM_
    - _Expected_Behavior: compare includes period with yekdem=0 and warning_
    - _Requirements: 2.12_

  - [x] 5.4 Distribution lookup → engine integration in analyze
    - Pass `distribution_unit_price_tl_per_kwh` to `calculate_hourly_costs`
    - `dist_info = _calculate_distribution_info(voltage_level=..., total_kwh=...)`
    - `dist_unit_price = dist_info.unit_price_tl_per_kwh if dist_info else 0.0`
    - _Bug_Condition: C1 — distribution not passed to engine_
    - _Expected_Behavior: engine receives distribution for dual margin calculation_
    - _Requirements: 2.1, 2.2_

  - [x] 5.5 Per-MWh dual price + dual margin calculations in analyze
    - `dist_per_mwh = dist_unit_price * 1000`
    - `sales_energy_price_per_mwh = energy_cost * req.multiplier`
    - `sales_effective_price_per_mwh = sales_energy_price_per_mwh + dist_per_mwh`
    - `gross_margin_energy_per_mwh = sales_energy_price_per_mwh - energy_cost`
    - `gross_margin_total_per_mwh = sales_energy_price_per_mwh - energy_cost - dist_per_mwh`
    - `net_margin_per_mwh = gross_margin_total_per_mwh - dealer_per_mwh - imbalance_per_mwh`
    - _Bug_Condition: C1 — single price/margin; C2 — incomplete net margin per-MWh_
    - _Expected_Behavior: dual prices, dual margins, complete net margin_
    - _Requirements: 2.1, 2.3, 2.5_

  - [x] 5.6 Risk flags + customer savings in PricingSummary construction
    - Risk flags: `LOSS_RISK` (P1) if `net_margin_total_tl < 0`; `UNPROFITABLE_OFFER` (P2) if `gross_margin_total_per_mwh < 0`
    - Both can coexist
    - Customer savings: `customer_savings_per_mwh = customer_ref_price - effective_price` (when ref available)
    - Customer reference price source metadata
    - _Expected_Behavior: risk flags priority ordered, customer savings with source_
    - _Requirements: 2.1, 2.5_

  - [x] 5.7 Add public distribution tariff endpoints
    - `GET /api/pricing/distribution-tariffs?period=YYYY-MM` — list tariffs for period
    - `GET /api/pricing/distribution-tariffs/lookup?voltage=OG&group=sanayi&term=TT&period=2026-04` — single lookup
    - No admin key required (public endpoints)
    - _Bug_Condition: C3 — no public API for frontend to fetch tariffs_
    - _Expected_Behavior: frontend can fetch tariffs from backend API_
    - _Requirements: 2.7, 2.8_

- [x] 6. Frontend — Remove hardcode, add API call, dual margin display, risk flag UI

  - [x] 6.1 Remove hardcoded tariff data from App.tsx
    - Delete `TARIFF_PERIODS` array
    - Delete `OSB_TARIFFS` array
    - Delete `getDistributionTariffsForPeriod` function
    - _Bug_Condition: C3 — hardcoded EPDK tariffs in frontend_
    - _Expected_Behavior: no hardcoded tariff data_
    - _Requirements: 2.9_

  - [x] 6.2 Add API call to fetch distribution tariffs from backend
    - Call `GET /api/pricing/distribution-tariffs?period=YYYY-MM` on period change
    - localStorage cache with TTL (24h)
    - Fallback: API down → last known tariff from localStorage + warning toast
    - _Bug_Condition: C3 — frontend doesn't call backend for tariffs_
    - _Expected_Behavior: frontend fetches tariffs from backend API with cache + fallback_
    - _Requirements: 2.7, 2.9_

  - [x] 6.3 Dual margin display in liveCalculation
    - `gross_margin_energy = offer_energy_tl - base_energy_cost`
    - `gross_margin_total = gross_margin_energy - offer_distribution_tl`
    - `net_margin = gross_margin_total - imbalance_share - dealer_commission`
    - UI labels: Enerji Marjı / Toplam Etki / Net Marj
    - Dual price display: Enerji Satış / Efektif Toplam
    - _Bug_Condition: C1 — frontend shows inconsistent single margin_
    - _Expected_Behavior: frontend shows dual margins matching backend formulas_
    - _Requirements: 2.4, 3.7_

  - [x] 6.4 Risk flag UI rendering
    - `LOSS_RISK` (P1) → red banner + "Teklif Öner" button disabled
    - `UNPROFITABLE_OFFER` (P2) → yellow warning banner + button active with warning
    - Both flags → red banner (P1 priority) + button disabled
    - _Expected_Behavior: risk flags rendered by priority with appropriate UI treatment_
    - _Requirements: 2.1_

- [x] 7. Verify bug condition exploration test now passes

  - [x] 7.1 Re-run bug condition exploration test after fix
    - **Property 1: Expected Behavior** — Pricing Consistency Fixed
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied
    - Run `backend/tests/test_pricing_consistency_bug_condition.py`
    - **EXPECTED OUTCOME**: Test PASSES (confirms all 4 bugs are fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.10, 2.11_

  - [x] 7.2 Verify preservation tests still pass after fix
    - **Property 2: Preservation** — Existing Behavior Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run `backend/tests/test_pricing_consistency_preservation.py`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all preservation properties still hold after fix
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 8. Checkpoint — Ensure all tests pass
  - Run full test suite: `pytest backend/tests/test_pricing_consistency_bug_condition.py backend/tests/test_pricing_consistency_preservation.py -v`
  - Run existing pricing tests to confirm no regressions: `pytest backend/tests/ -k pricing -v`
  - Verify Per-MWh / Total-TL consistency: `total_tl ≈ per_mwh × consumption / 1000` (±0.02 TL)
  - Verify dual margin invariant: `gross_margin_energy >= gross_margin_total` when distribution >= 0
  - Verify net margin invariant: `net_margin <= gross_margin_total` when imbalance >= 0 and dealer >= 0
  - Ensure all tests pass. If ambiguity arises, make the safest backward-compatible implementation and document the assumption.
