# Requirements Document

## Introduction

`invoice-recon-engine-v2-cost-headline` is the v2 evolution of the shipped v1 `invoice-recon-engine` feature. v1 already parses distribution-portal hourly consumption Excel files, splits them into monthly periods, computes T1/T2/T3 totals, runs reconciliation against optional invoice declarations, and computes PTF + YEKDEM cost only when invoice values are present.

Operational acceptance Phase A (RUN-001) was suspended on 2026-05-17 because the v1 information hierarchy is wrong. The tool's actual value proposition is **cost transparency**: a side-by-side comparison of (a) what the customer pays today on the invoice, (b) the EPİAŞ PTF + YEKDEM-based wholesale market reference energy cost, (c) the implied supplier markup, and (d) the Gelka offer estimate. v1 surfaces T1/T2/T3 and reconciliation deltas as the headline; v2 inverts this so the four cost numbers are the headline and consumption breakdown becomes secondary detail.

v2 is **additive** to v1. All v1 parsing, splitting, classification, reconciliation, and PTF/YEKDEM lookup logic is reused unchanged; v2 adds an always-on reference cost path, a markup computation, a v2 schema extension, and a frontend hierarchy redesign. The v1 request body shape is preserved; v1 response fields remain present and unchanged in semantics.

**Positioning constraint (non-negotiable):** the wholesale-market-derived figure MUST NEVER be labelled "gerçek maliyet", "actual cost", or "true cost". Allowed labels are listed in Requirement REQ-4. We do not claim to know the supplier's real cost; we surface a market reference.

**Scope (v2):** always-on reference cost computation, supplier markup disclosure, headline information hierarchy, terminology lock, fail-closed semantics preserved per period, v2 schema fields, FE period card v2 layout, performance budget, observability for cost computation.

**Out of scope (v3+):** hourly cost dashboard or chart, e-Fatura XML upload, multi-period invoice form (Phase A BUG-002 follow-up), PDF export of cost report.

## Glossary

- **Recon_Engine_v2**: The v2 evolution of the v1 invoice reconciliation engine that exposes cost transparency as the primary output. Implementation reuses v1 modules under `backend/app/recon/` and adds new helpers in `cost_engine.py` and `comparator.py`.
- **API_Endpoint**: HTTP endpoint `POST /api/recon/analyze` (unchanged path from v1) implemented by `backend/app/recon/router.py`.
- **PTF**: Piyasa Takas Fiyatı, hourly wholesale electricity price in TL/MWh. Canonical source: `hourly_market_prices` table. Forbidden source: `market_reference_prices`.
- **YEKDEM**: Yenilenebilir Enerji Kaynakları Destekleme Mekanizması, monthly renewable energy support fee in TL/MWh. Canonical source: `monthly_yekdem_prices` table. Forbidden source: `market_reference_prices`.
- **Reference_Energy_Cost**: The PTF + YEKDEM based wholesale market reference energy cost for a period, in TL. Computed as the sum over the period of `(consumption_kwh_hour × PTF_TL_per_MWh_hour / 1000)` plus `(total_kwh_period × YEKDEM_TL_per_MWh_period / 1000)`. Full Turkish label: "EPİAŞ PTF + YEKDEM bazlı referans enerji maliyeti". Short label: "Referans enerji maliyeti". Very short label (tight UI): "PTF + YEKDEM referansı". Forbidden labels: "gerçek maliyet", "actual cost", "true cost".
- **Invoice_Total_TL**: The energy invoice total in TL provided by the operator as input via `InvoiceInput.declared_total_tl` (existing v1 field). Represents what the customer currently pays the existing supplier for energy in that period. v2 reuses the v1 field name; no new field is introduced for this concept.
- **Supplier_Markup_TL**: The TL difference between the invoice total and the reference energy cost for a period, computed as `Invoice_Total_TL − Reference_Energy_Cost_TL`. May be negative if the invoice is below the reference. The internal field name `supplier_markup_tl` is preserved for backend compatibility, but user-facing labels SHALL use neutral terminology — "Mevcut fatura ile referans maliyet farkı" — that does not imply knowledge of the supplier's actual cost structure (hedge cost, financing, risk premium, operational overhead, portfolio cost, etc.).
- **Supplier_Markup_Pct**: The TL difference expressed as a percentage of `Reference_Energy_Cost_TL`, computed as `(Invoice_Total_TL − Reference_Energy_Cost_TL) / Reference_Energy_Cost_TL × 100`. Undefined (null) when `Reference_Energy_Cost_TL` is zero or null. Turkish label: "Mevcut fatura ile referans maliyet farkı (%)".
- **Gelka_Margin_Multiplier**: Configurable multiplier applied to the reference energy cost to produce the Gelka offer estimate. Default value: 1.05. Provided via `ComparisonConfig.gelka_margin_multiplier` (existing v1 field). MUST be `>= 1.0` — values below 1.0 are rejected with a `ValueError` because they would imply Gelka selling below the wholesale reference, which is operationally meaningless and likely a configuration mistake.
- **Gelka_Estimate_TL**: The Gelka offer estimate in TL for a period, computed as `Reference_Energy_Cost_TL × Gelka_Margin_Multiplier`. Turkish label: "Gelka teklif tahmini".
- **Potential_Savings_TL**: The potential savings versus the current invoice in TL for a period, computed as `Invoice_Total_TL − Gelka_Estimate_TL`. Positive value means Gelka is cheaper. Turkish label: "Gelka ile potansiyel tasarruf".
- **Period_Cost_Inputs**: Diagnostic metadata describing the inputs that fed `Reference_Energy_Cost_TL` for a period. Includes `ptf_source` (string identifier of the PTF table), `yekdem_source` (string identifier of the YEKDEM table), `period_start` (YYYY-MM-DD), `period_end` (YYYY-MM-DD), `total_hours` (integer count of hourly records consumed), `complete` (boolean indicating no PTF or YEKDEM gaps).
- **Period_Status**: Per-period status field with values "ok" (full reference cost computed) or "partial" (reference cost is null because PTF or YEKDEM data is missing for one or more hours). Distinct from the report-level `status` field which v1 already exposes.
- **Quote_Blocked_Period**: A period where `Reference_Energy_Cost_TL` is null because at least one PTF hour is missing for that period or YEKDEM is missing for that period. v1 already exposes `quote_blocked` and `quote_block_reason` on each period; v2 reuses these fields.
- **Period_Card_v2**: The frontend per-period UI component that v2 introduces, replacing the v1 single-row layout. Implemented in `frontend/src/recon/ReconPage.tsx`. Renders the four cost headline numbers as the primary row and exposes T1/T2/T3 breakdown, reconciliation detail, and per-hour distribution as collapsed accordions below the headline.
- **Cost_Headline**: The primary horizontal row of `Period_Card_v2` that displays Invoice_Total_TL, Reference_Energy_Cost_TL, Supplier_Markup_TL with Supplier_Markup_Pct, and Gelka_Estimate_TL with Potential_Savings_TL.
- **Decimal_Boundary**: The serialization boundary at which the Recon_Engine_v2 converts internal Decimal values to JSON-serializable floats. Defined as the Pydantic schema layer in `backend/app/recon/schemas.py`. All arithmetic upstream of this boundary uses `decimal.Decimal`.
- **api_version**: The integer field in the response body declaring the response contract major version. v1 emits `api_version=1`. v2 emits `api_version=2`.

## Requirements

### Requirement 1: Always-On Reference Energy Cost Computation (REQ-1)

**User Story:** As a sales operator analysing a customer's hourly Excel without yet having their invoice values, I want the system to compute the EPİAŞ PTF + YEKDEM based reference energy cost for every period, so that I can show the wholesale market cost headline immediately on Excel upload alone.

#### Acceptance Criteria

1. THE Recon_Engine_v2 SHALL compute Reference_Energy_Cost_TL for every period present in the parsed Excel regardless of whether `ReconRequest.invoices` contains an entry for that period.
2. WHEN a period has at least one parsed hourly record AND `hourly_market_prices` contains a PTF row matching every parsed hourly record's `record_hour_utc` for that period AND `monthly_yekdem_prices` contains a row whose `period_yyyymm` equals the period's YYYY-MM key, THE Recon_Engine_v2 SHALL set `PeriodResult.reference_energy_cost_tl` to the sum over the parsed hourly records of `(record.consumption_kwh × ptf_row.tl_per_mwh / Decimal("1000"))` plus `(sum(record.consumption_kwh) × yekdem_row.tl_per_mwh / Decimal("1000"))`, computed in `decimal.Decimal` arithmetic and rounded to 2 decimal places at the Decimal_Boundary.
3. THE Recon_Engine_v2 SHALL read PTF values exclusively from the `hourly_market_prices` table.
4. THE Recon_Engine_v2 SHALL read YEKDEM values exclusively from the `monthly_yekdem_prices` table.
5. WHEN computing Reference_Energy_Cost_TL, THE Recon_Engine_v2 SHALL NOT read from the `market_reference_prices` table.
6. WHERE `ReconRequest.invoices` is empty or omitted, THE Recon_Engine_v2 SHALL still populate `PeriodResult.reference_energy_cost_tl` for every period that satisfies the data completeness condition in REQ-1 acceptance criterion 2.
7. THE Recon_Engine_v2 SHALL expose a callable function `compute_period_reference_cost(records, period, db) -> ReferenceEnergyCostResult` in `backend/app/recon/cost_engine.py` that returns the period reference cost without requiring any invoice input parameter.

---

### Requirement 2: Supplier Markup Disclosure (REQ-2)

**User Story:** As a sales operator who has entered the customer's current invoice total, I want the system to surface the implied supplier commercial margin alongside the reference cost, so that I can quantify how much markup the existing supplier is charging above wholesale.

#### Acceptance Criteria

1. WHEN `InvoiceInput.declared_total_tl` is provided for a period AND `PeriodResult.reference_energy_cost_tl` is non-null for that period, THE Recon_Engine_v2 SHALL set `PeriodResult.supplier_markup_tl` to `declared_total_tl − reference_energy_cost_tl` computed in `decimal.Decimal` arithmetic and rounded to 2 decimal places at the Decimal_Boundary.
2. WHEN `InvoiceInput.declared_total_tl` is provided for a period AND `PeriodResult.reference_energy_cost_tl` is non-null AND `reference_energy_cost_tl` is greater than zero, THE Recon_Engine_v2 SHALL set `PeriodResult.supplier_markup_pct` to `(declared_total_tl − reference_energy_cost_tl) / reference_energy_cost_tl × 100` computed in `decimal.Decimal` arithmetic and rounded to 2 decimal places at the Decimal_Boundary.
3. WHEN `InvoiceInput.declared_total_tl` is provided for a period AND `PeriodResult.reference_energy_cost_tl` is non-null, THE Recon_Engine_v2 SHALL set `PeriodResult.gelka_estimate_tl` to `reference_energy_cost_tl × ComparisonConfig.gelka_margin_multiplier` rounded to 2 decimal places at the Decimal_Boundary.
4. WHEN `InvoiceInput.declared_total_tl` is provided for a period AND `PeriodResult.gelka_estimate_tl` is non-null, THE Recon_Engine_v2 SHALL set `PeriodResult.potential_savings_tl` to `declared_total_tl − gelka_estimate_tl` rounded to 2 decimal places at the Decimal_Boundary.
5. THE Recon_Engine_v2 SHALL expose a callable function `compute_markup(invoice_total_tl: Decimal, reference_cost_tl: Decimal, gelka_margin_multiplier: Decimal) -> MarkupResult` in `backend/app/recon/comparator.py` that returns markup TL, markup pct, gelka estimate TL, and potential savings TL given the three scalar inputs.
6. THE `compute_markup` function SHALL raise a `ValueError` when `gelka_margin_multiplier < Decimal("1.0")` because such a value would imply Gelka selling below the wholesale reference, which is operationally meaningless.
7. WHEN `InvoiceInput.declared_total_tl` is omitted for a period, THE Recon_Engine_v2 SHALL set `PeriodResult.supplier_markup_tl`, `PeriodResult.supplier_markup_pct`, `PeriodResult.gelka_estimate_tl`, and `PeriodResult.potential_savings_tl` all to null in the response body for that period.

---

### Requirement 3: Headline Information Hierarchy (REQ-3)

**User Story:** As a sales operator presenting the analysis to a prospect, I want the four cost numbers to be the primary headline of each period, so that the customer immediately understands the cost transparency story without being distracted by reconciliation detail.

#### Acceptance Criteria

1. THE Period_Card_v2 SHALL render Invoice_Total_TL, Reference_Energy_Cost_TL, Supplier_Markup_TL with Supplier_Markup_Pct, and Gelka_Estimate_TL with Potential_Savings_TL as the topmost four-cell row inside the card.
2. THE Period_Card_v2 SHALL render T1 kWh, T2 kWh, T3 kWh totals and percentages inside a section labelled "Tüketim profili (T1/T2/T3)" that defaults to collapsed.
3. THE Period_Card_v2 SHALL render reconciliation items (the array `PeriodResult.reconciliation`) inside a section labelled "Mutabakat detayı" that defaults to collapsed.
4. THE Period_Card_v2 SHALL render the missing hours count and any per-period warnings inside a section labelled "Mutabakat detayı" or a subsection of it that defaults to collapsed.
5. THE Period_Card_v2 SHALL render the period label and `PeriodResult.overall_status` badge in the card header above the headline row.
6. WHEN the user clicks the section header for a collapsed accordion, THE Period_Card_v2 SHALL toggle the visibility of that accordion's body without affecting other accordions.

---

### Requirement 4: Terminology Lock (REQ-4)

**User Story:** As a Gelka stakeholder responsible for legal positioning, I want the system to use only approved labels for the wholesale-derived cost figure, so that we never imply we know the supplier's real cost.

#### Acceptance Criteria

1. WHERE the user-facing label refers to Reference_Energy_Cost_TL in long form, THE Recon_Engine_v2 frontend SHALL use the literal string "EPİAŞ PTF + YEKDEM bazlı referans enerji maliyeti".
2. WHERE the user-facing label refers to Reference_Energy_Cost_TL in short form, THE Recon_Engine_v2 frontend SHALL use the literal string "Referans enerji maliyeti".
3. WHERE the user-facing label refers to Reference_Energy_Cost_TL in very short form on a tight UI element, THE Recon_Engine_v2 frontend SHALL use the literal string "PTF + YEKDEM referansı".
4. THE Recon_Engine_v2 frontend SHALL NOT render the strings "gerçek maliyet", "actual cost", or "true cost" anywhere in the recon page UI in connection with Reference_Energy_Cost_TL.
5. THE Recon_Engine_v2 backend SHALL NOT include the strings "gerçek maliyet", "actual cost", or "true cost" in any field value, message, warning, or log line emitted by the recon module in connection with Reference_Energy_Cost_TL.
6. THE Recon_Engine_v2 backend SHALL include the string "referans" or "reference" in the diagnostic field name used for the cost computation source identifiers (`cost_inputs.ptf_source`, `cost_inputs.yekdem_source`).

---

### Requirement 5: Fail-Closed Semantics Per Period (REQ-5)

**User Story:** As a sales operator who uploads an Excel covering several periods where some periods have full PTF data and others do not, I want each period to be evaluated independently for cost completeness, so that the periods with full data still show their cost headline even if other periods are blocked.

#### Acceptance Criteria

1. IF for a given period at least one parsed hourly record has no PTF row in `hourly_market_prices` whose `record_hour_utc` matches the parsed record's `record_hour_utc`, THEN THE Recon_Engine_v2 SHALL set `PeriodResult.reference_energy_cost_tl` to null for that period.
2. IF for a given period the `monthly_yekdem_prices` table has no row for the period's YYYY-MM key, THEN THE Recon_Engine_v2 SHALL set `PeriodResult.reference_energy_cost_tl` to null for that period.
3. IF `PeriodResult.reference_energy_cost_tl` is null for a given period, THEN THE Recon_Engine_v2 SHALL set `PeriodResult.supplier_markup_tl`, `PeriodResult.supplier_markup_pct`, `PeriodResult.gelka_estimate_tl`, and `PeriodResult.potential_savings_tl` all to null for that period.
4. IF `PeriodResult.reference_energy_cost_tl` is null for a given period, THEN THE Recon_Engine_v2 SHALL set `PeriodResult.quote_blocked` to true and SHALL populate `PeriodResult.quote_block_reason` with a string identifying the missing data category ("PTF data missing for N hours" or "YEKDEM data missing for period").
5. IF at least one period in the response has `reference_energy_cost_tl` null OR any v1 condition that already sets `ReconReport.status` to "partial" is triggered (e.g. period-level reconciliation mismatch beyond tolerance), THEN THE Recon_Engine_v2 SHALL set `ReconReport.status` to "partial".
6. WHEN `ReconReport.status` is "partial" because some periods have null `reference_energy_cost_tl` while other periods have valid cost headline data, THE API_Endpoint SHALL return HTTP status code 200. This is consistent with R26 (Hybrid-C policy) for preview-class flows that surface partial results without blocking the response.
7. WHEN one period in the response has `reference_energy_cost_tl` null AND another period in the same response has `reference_energy_cost_tl` non-null, THE Recon_Engine_v2 SHALL include the cost headline values for the second period unchanged.
8. WHEN `PeriodResult.reference_energy_cost_tl` is null for a given period because of missing PTF or YEKDEM data, THE Period_Card_v2 SHALL render that period's cost headline cells with an amber visual treatment.
9. WHEN `PeriodResult.reference_energy_cost_tl` is null for a given period, THE Period_Card_v2 SHALL NOT render that period with a red error visual treatment.

---

### Requirement 6: Markup Edge Cases (REQ-6)

**User Story:** As a sales operator who occasionally encounters atypical invoice values, I want the markup display to handle missing reference data, negative margins, and unusually high margins consistently, so that I can interpret the output without manual workarounds.

#### Acceptance Criteria

1. IF `InvoiceInput.declared_total_tl` is provided for a period AND `PeriodResult.reference_energy_cost_tl` is null for that period, THEN THE Recon_Engine_v2 SHALL include `declared_total_tl` in the response for that period AND SHALL set `PeriodResult.supplier_markup_tl`, `PeriodResult.supplier_markup_pct`, `PeriodResult.gelka_estimate_tl`, and `PeriodResult.potential_savings_tl` all to null.
2. IF for a period the conditions in REQ-6 acceptance criterion 1 hold, THEN THE Period_Card_v2 SHALL render the Invoice_Total_TL cell, SHALL hide the Supplier_Markup row content, AND SHALL render an explanatory note labelled "Referans enerji maliyeti hesaplanamadı — fatura tutarı gösterimi sınırlı".
3. WHEN `PeriodResult.supplier_markup_tl` is less than zero, THE Period_Card_v2 SHALL render the markup cell with the literal label "negatif fark — fatura referans maliyetin altında" alongside the numeric value.
4. WHEN `PeriodResult.supplier_markup_tl` is less than zero, THE Period_Card_v2 SHALL NOT prefix the markup value with any judgmental qualifier such as "şüpheli", "anormal", or "kâr".
5. WHEN `PeriodResult.supplier_markup_pct` is greater than 100, THE Recon_Engine_v2 SHALL still emit the computed `supplier_markup_pct` value in the response without clipping or capping.
6. WHEN `PeriodResult.supplier_markup_pct` is greater than 100, THE Period_Card_v2 SHALL render the value as is without truncation or replacement.
7. WHEN `PeriodResult.reference_energy_cost_tl` equals zero AND `InvoiceInput.declared_total_tl` is provided for that period, THE Recon_Engine_v2 SHALL set `PeriodResult.supplier_markup_pct` to null and SHALL set `PeriodResult.supplier_markup_tl` to `declared_total_tl − 0`.

---

### Requirement 7: Schema Bump (REQ-7)

**User Story:** As a frontend developer integrating against the v2 backend, I want the response body to declare api_version=2 and to expose the new cost fields under each period without breaking the v1 contract, so that I can rely on a versioned, additive schema.

#### Acceptance Criteria

1. THE Recon_Engine_v2 SHALL set `ReconReport.api_version` to the integer value 2 in every response emitted by `POST /api/recon/analyze`.
2. THE Recon_Engine_v2 SHALL include the field `reference_energy_cost_tl` of type float-or-null on every `PeriodResult` in the response body.
3. THE Recon_Engine_v2 SHALL include the field `supplier_markup_tl` of type float-or-null on every `PeriodResult` in the response body.
4. THE Recon_Engine_v2 SHALL include the field `supplier_markup_pct` of type float-or-null on every `PeriodResult` in the response body.
5. THE Recon_Engine_v2 SHALL include the field `gelka_estimate_tl` of type float-or-null on every `PeriodResult` in the response body.
6. THE Recon_Engine_v2 SHALL include the field `potential_savings_tl` of type float-or-null on every `PeriodResult` in the response body.
7. THE Recon_Engine_v2 SHALL include the object field `cost_inputs` on every `PeriodResult` in the response body. The `cost_inputs` object SHALL contain the string field `ptf_source` set to the literal value "hourly_market_prices", the string field `yekdem_source` set to the literal value "monthly_yekdem_prices", the string field `period_start` in YYYY-MM-DD format, the string field `period_end` in YYYY-MM-DD format, the integer field `total_hours` equal to the count of hourly records consumed for that period, and the boolean field `complete` set to true when REQ-5 acceptance criteria 1 and 2 are both not triggered for that period and false otherwise.
8. THE Recon_Engine_v2 SHALL preserve every field name and field semantic that v1 emits on `ReconReport`, `PeriodResult`, `ReconciliationItem`, `PtfCostResult`, `YekdemCostResult`, and `CostComparison`.
9. THE Recon_Engine_v2 SHALL keep the v1 `PeriodResult.cost_comparison` field populated with the same semantics as v1 when invoice unit price and distribution unit price are provided.

---

### Requirement 8: Backend Always-On Cost Flow (REQ-8)

**User Story:** As a backend engineer extending the recon pipeline, I want the report builder to invoke the reference cost path for every period unconditionally, so that there is a single code path producing the cost headline data.

#### Acceptance Criteria

1. THE `report_builder` module SHALL invoke `cost_engine.compute_period_reference_cost(records, period, db)` for every period produced by the splitter, regardless of the contents of `ReconRequest.invoices`.
2. WHEN `cost_engine.compute_period_reference_cost` returns a non-null reference cost AND `InvoiceInput.declared_total_tl` is present for that period, THE `report_builder` SHALL invoke `comparator.compute_markup(invoice_total_tl, reference_cost_tl)` and SHALL place the returned markup, gelka estimate, and potential savings values onto the `PeriodResult`.
3. WHEN `cost_engine.compute_period_reference_cost` returns a null reference cost, THE `report_builder` SHALL set the markup-related fields on `PeriodResult` to null per REQ-5 acceptance criterion 3 without invoking `comparator.compute_markup`.
4. THE `cost_engine.compute_period_reference_cost` function SHALL accept the parameters `records: list[HourlyRecord]`, `period: str`, `db: Session` and SHALL NOT take any invoice-related parameter.
5. THE `comparator.compute_markup` function SHALL accept the parameters `invoice_total_tl: Decimal` and `reference_cost_tl: Decimal` and `gelka_margin_multiplier: Decimal` and SHALL return a structure carrying markup TL, markup pct, gelka estimate TL, and potential savings TL.
6. THE Recon_Engine_v2 SHALL perform all reference cost arithmetic and all markup arithmetic in `decimal.Decimal` and SHALL convert to float only at the Decimal_Boundary defined in the Glossary.
7. THE Recon_Engine_v2 SHALL NOT apply the Format_A `multiplier` metadata to consumption values during reference cost computation; the multiplier remains metadata-only as in v1.

---

### Requirement 9: Frontend Period Card v2 Layout (REQ-9)

**User Story:** As a sales operator using the recon page, I want each period card to lead with the cost headline and tuck the consumption and reconciliation detail behind accordions, so that the most valuable numbers appear first and the secondary detail stays available on demand.

#### Acceptance Criteria

1. THE Period_Card_v2 SHALL render a header row containing the period label (YYYY-MM) and the `PeriodResult.overall_status` badge.
2. THE Period_Card_v2 SHALL render the Cost_Headline as a horizontally laid-out row of four cells immediately below the header row, in the order Invoice_Total_TL, Reference_Energy_Cost_TL, Supplier_Markup_TL with Supplier_Markup_Pct, Gelka_Estimate_TL with Potential_Savings_TL.
3. WHEN `PeriodResult.reference_energy_cost_tl` is non-null for a period, THE Period_Card_v2 SHALL render that period's Cost_Headline cells with semantic colour coding that distinguishes the four numbers visually.
4. THE Period_Card_v2 SHALL render an accordion labelled "Tüketim profili (T1/T2/T3)" that defaults to collapsed and that contains T1 kWh, T2 kWh, T3 kWh totals and percentages.
5. THE Period_Card_v2 SHALL render an accordion labelled "Mutabakat detayı" that defaults to collapsed and that contains the reconciliation items, missing hours count, and per-period warnings.
6. WHEN no per-hour distribution data is rendered in v2, THE Period_Card_v2 SHALL omit the "Saatlik dağılım" accordion entirely.
7. THE Period_Card_v2 SHALL format every TL value using the Turkish locale (thousand separator dot, decimal separator comma, two decimal places) via the existing `numberFormat.ts` helper.
8. THE Period_Card_v2 SHALL format every kWh value with three decimal places using the Turkish locale.
9. WHERE `PeriodResult.supplier_markup_tl` is null because invoice values were not provided, THE Period_Card_v2 SHALL render the Supplier_Markup cell with a placeholder dash "—" and SHALL NOT render the cell as an error state.

---

### Requirement 10: Backward Compatibility and Migration (REQ-10)

**User Story:** As an existing API consumer of the v1 endpoint, I want the v2 release to leave my existing request body and response field semantics intact, so that I can adopt v2 fields incrementally.

#### Acceptance Criteria

1. THE API_Endpoint SHALL accept the v1 `ReconRequest` body shape (fields `invoices`, `tolerance`, `comparison`) without modification of field names, types, or required-vs-optional flags.
2. WHEN the v1 `ReconRequest` body shape is submitted, THE API_Endpoint SHALL return a response that satisfies all v1 field semantics on `ReconReport`, `PeriodResult`, `ReconciliationItem`, `PtfCostResult`, `YekdemCostResult`, and `CostComparison`.
3. THE API_Endpoint SHALL emit the v2 fields listed in REQ-7 in every response body emitted regardless of whether the request opted in via any flag.
4. THE Recon_Engine_v2 frontend SHALL reuse the existing `analyzeRecon` API client function, the existing `ReconRequest` type alias, the existing `numberFormat.ts` helper, and the existing `parseTurkishNumber` helper without renaming or behavioural change.
5. THE Recon_Engine_v2 frontend SHALL preserve all hooks and state-management constructs of the v1 ReconPage (file state, drag-and-drop handler, invoice-input state, loading flag, error flag, report flag) and SHALL only refactor the rendering output of `ReconResults` and the `PeriodCard` component into Period_Card_v2.

---

### Requirement 11: Testing Scope (REQ-11)

**User Story:** As a maintainer of the recon module, I want the v2 release to add new tests covering the always-on cost path and the markup math while keeping all existing v1 tests passing, so that v2 ships with full regression coverage.

#### Acceptance Criteria

1. THE Recon_Engine_v2 test suite SHALL keep all existing v1 tests of the recon module passing after the v2 changes are applied.
2. THE Recon_Engine_v2 test suite SHALL include at least one example-based test that asserts `PeriodResult.reference_energy_cost_tl` is non-null when invoice values are not provided and PTF and YEKDEM data are complete for the period.
3. THE Recon_Engine_v2 test suite SHALL include at least one example-based test that asserts `PeriodResult.supplier_markup_tl` equals `declared_total_tl − reference_energy_cost_tl` for a period with full data and a positive markup.
4. THE Recon_Engine_v2 test suite SHALL include at least one example-based test that asserts `PeriodResult.supplier_markup_tl` is negative when `declared_total_tl` is less than `reference_energy_cost_tl` and SHALL assert the response field is emitted unchanged with no clipping.
5. THE Recon_Engine_v2 test suite SHALL include at least one example-based test that asserts all four markup-related fields are null when `declared_total_tl` is omitted.
6. THE Recon_Engine_v2 test suite SHALL include at least one example-based test that asserts all four markup-related fields are null and `quote_blocked` is true when PTF or YEKDEM data is missing for a period.
7. THE Recon_Engine_v2 test suite SHALL include at least one frontend test using vitest that asserts the Period_Card_v2 renders the four cost headline cells in the documented order and that the consumption and reconciliation accordions default to collapsed.
8. THE Recon_Engine_v2 test suite SHALL include at least one property-based test that asserts `reference_energy_cost_tl` is non-negative for any input where every hourly consumption value is non-negative and every PTF and YEKDEM value is non-negative.
9. THE Recon_Engine_v2 test suite SHALL include at least one property-based test that asserts `reference_energy_cost_tl` is monotonically non-decreasing when any single hourly consumption value is increased while all PTF and YEKDEM values are held fixed and remain non-negative.
10. THE Recon_Engine_v2 test suite SHALL include a new golden snapshot test for the Cansu Su / BKA Enerji v2 response body that captures the new fields specified in REQ-7. The existing v1 golden snapshot SHALL remain in place unchanged.

---

### Requirement 12: Out-of-Scope Boundaries (REQ-12)

**User Story:** As a product owner scoping v2, I want the deferred items to be explicitly enumerated as out of scope for this release, so that they are not implicitly pulled in during implementation.

#### Acceptance Criteria

1. THE Recon_Engine_v2 SHALL NOT introduce an hourly cost dashboard or chart in this release.
2. THE Recon_Engine_v2 SHALL NOT introduce e-Fatura XML upload in this release.
3. THE Recon_Engine_v2 SHALL NOT introduce a multi-period invoice form in this release.
4. THE Recon_Engine_v2 SHALL NOT introduce a PDF export of the cost report in this release.
5. WHERE a v3 follow-up is anticipated for any of the items in REQ-12 acceptance criteria 1 to 4, THE Recon_Engine_v2 backend SHALL NOT add API fields, request flags, or database columns specifically to support those future items in this release.

---

## Non-Functional Requirements

### Requirement 13: Performance Budget (NFR-1)

**User Story:** As an operator running the recon analysis interactively, I want the v2 cost computation to add a small and bounded amount of time to the existing v1 analysis latency, so that the interactive experience does not regress.

#### Acceptance Criteria

1. THE API_Endpoint SHALL produce v2 responses such that the additional wall-clock time spent on reference cost computation and markup computation per request, measured as `t_v2_endpoint − t_v1_baseline_endpoint` where `t_v1_baseline_endpoint` is captured by running the same input fixture against a build with the v2 cost path bypassed via a feature flag (`RECON_V2_COST_PATH=off`), is at most 200 milliseconds at the 95th percentile across the v1 regression input fixtures defined in `backend/tests/recon/fixtures/`.
2. THE Recon_Engine_v2 SHALL load PTF rows for a given period from `hourly_market_prices` using a single bulk query per period rather than per-hour queries.
3. THE Recon_Engine_v2 SHALL load YEKDEM rows for a given period from `monthly_yekdem_prices` using at most one query per period.

---

### Requirement 14: Observability for Cost Computation (NFR-2)

**User Story:** As an SRE investigating partial responses in production, I want every reference cost computation to leave a structured log entry, so that I can correlate missing-data periods to specific inputs without re-running the request.

#### Acceptance Criteria

1. WHEN `cost_engine.compute_period_reference_cost` returns for a period, THE Recon_Engine_v2 SHALL emit a single structured log entry at INFO level containing the period identifier (YYYY-MM), the integer field `total_hours`, the boolean field `complete`, and the boolean field `reference_energy_cost_present` indicating whether `reference_energy_cost_tl` is non-null.
2. WHEN `cost_engine.compute_period_reference_cost` produces a null reference cost because of missing PTF data, THE Recon_Engine_v2 SHALL include in the same log entry an integer field `ptf_hours_missing` set to the count of hourly records that did not match a PTF row.
3. WHEN `cost_engine.compute_period_reference_cost` produces a null reference cost because of missing YEKDEM data, THE Recon_Engine_v2 SHALL include in the same log entry a boolean field `yekdem_missing` set to true.
4. THE Recon_Engine_v2 SHALL NOT include in any log entry the strings "gerçek maliyet", "actual cost", or "true cost" in connection with Reference_Energy_Cost_TL.
5. THE Recon_Engine_v2 SHALL NOT include personally identifying customer information (customer name, supplier name, tariff group) in cost computation log entries.
