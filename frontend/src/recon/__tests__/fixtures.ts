/**
 * Test fixtures for v2 cost-headline FE tests.
 *
 * Provides minimal valid PeriodResult / MultiPeriodSummary objects with
 * easy override hooks so each test can pin a specific surface (null cell,
 * negative savings, partial period, etc.) without re-typing the whole shape.
 */

import type {
  CostInputs,
  MultiPeriodSummary,
  PeriodResult,
  AnnualProjection,
} from '../types';

export function makeCostInputs(overrides: Partial<CostInputs> = {}): CostInputs {
  return {
    ptf_source: 'hourly_market_prices',
    yekdem_source: 'monthly_yekdem_prices',
    period_start: '2026-01-01',
    period_end: '2026-01-31',
    total_hours: 744,
    complete: true,
    ...overrides,
  };
}

export function makePeriod(
  overrides: Partial<PeriodResult> = {},
): PeriodResult {
  return {
    period: '2026-01',
    total_kwh: 194412.847,
    t1_kwh: 87525.087,
    t2_kwh: 41397.41,
    t3_kwh: 65490.35,
    t1_pct: 45.02,
    t2_pct: 21.29,
    t3_pct: 33.69,
    missing_hours: 0,
    duplicate_hours: 0,
    reconciliation: [],
    overall_status: 'KONTROL_EDILMEDI',
    overall_severity: null,
    ptf_cost: null,
    yekdem_cost: null,
    cost_comparison: null,
    quote_blocked: false,
    quote_block_reason: null,
    warnings: [],
    reference_energy_cost_tl: 661003.68,
    supplier_markup_tl: 132200.74,
    supplier_markup_pct: 20,
    gelka_estimate_tl: 694053.87,
    potential_savings_tl: 99150.55,
    cost_inputs: makeCostInputs(),
    ...overrides,
  };
}

export function makeAnnualProjection(
  overrides: Partial<AnnualProjection> = {},
): AnnualProjection {
  return {
    based_on_periods: 4,
    label: 'tahmini yıllık projeksiyon',
    annualized_reference_cost_tl: 7797855.21,
    annualized_supplier_markup_tl: 1559571.06,
    annualized_potential_savings_tl: 1169678.25,
    ...overrides,
  };
}

export function makeSummary(
  overrides: Partial<MultiPeriodSummary> = {},
): MultiPeriodSummary {
  return {
    period_count: 4,
    total_kwh: 764495.61,
    t1_kwh: 353426.86,
    t2_kwh: 156505.97,
    t3_kwh: 254562.78,
    total_ptf_cost_tl: 0,
    total_yekdem_cost_tl: 0,
    total_invoice_tl: 3119142.08,
    total_gelka_tl: 0,
    total_diff_tl: 0,
    periods_with_quotes: 4,
    periods_blocked: 0,
    valid_period_count: 4,
    partial_period_count: 0,
    total_period_count: 4,
    total_reference_energy_cost_tl: 2599285.07,
    total_supplier_markup_tl: 519857.02,
    total_gelka_estimate_tl: 2729249.34,
    total_potential_savings_tl: 389892.75,
    annual_projection: makeAnnualProjection(),
    ...overrides,
  };
}
