/**
 * Invoice Reconciliation Engine — Backend Response Contract Mirror
 *
 * Bu dosya backend ReconReport schema'sı ile BİREBİR uyumlu olmalıdır.
 * Backend değişmeden bu dosya değişmez.
 *
 * status="partial" asla error state'e düşmemeli — valid result.
 *
 * v2 (cost-headline release):
 *   - api_version: 2 (literal lock — drift guard'ı için const)
 *   - PeriodResult'a v2 cost-headline alanları eklendi
 *   - cost_inputs HER period için zorunlu (fail-closed durumunda bile dolu)
 *   - summary tip artık MultiPeriodSummary — v2 aggregate alanları dahil
 *   - AnnualProjection.label disclaimer string'i kontratta sabit
 *
 * Önemli FE notu (Task 11):
 *   summary.total_* alanları number | null. null ≠ 0; FE bu alanları
 *   render ederken "0 TL" gibi göstermemeli — "—" / "hesaplanamadı".
 */

// ── Request Types ──

export interface InvoiceInput {
  period: string;
  supplier_name?: string;
  tariff_group?: string;
  unit_price_tl_per_kwh?: number;
  discount_pct?: number;
  distribution_unit_price_tl_per_kwh?: number;
  declared_t1_kwh?: number;
  declared_t2_kwh?: number;
  declared_t3_kwh?: number;
  declared_total_kwh?: number;
  declared_total_tl?: number;
}

export interface ToleranceConfig {
  pct_tolerance?: number;  // default 1.0
  abs_tolerance_kwh?: number;  // default 1.0
}

export interface ComparisonConfig {
  gelka_margin_multiplier?: number;  // default 1.05
}

export interface ReconRequest {
  invoices?: InvoiceInput[];
  tolerance?: ToleranceConfig;
  comparison?: ComparisonConfig;
}

// ── Response Types ──

export type ReconStatus = 'ok' | 'partial';
export type ReconciliationStatusValue = 'UYUMLU' | 'UYUMSUZ' | 'KONTROL_EDILMEDI';
export type SeverityValue = 'LOW' | 'WARNING' | 'CRITICAL';
export type ExcelFormatValue = 'format_a' | 'format_b';

export interface ReconciliationItem {
  field: string;  // "t1_kwh", "t2_kwh", "t3_kwh", "total_kwh"
  excel_total_kwh: number;
  invoice_total_kwh: number;
  delta_kwh: number;
  delta_pct: number;
  status: ReconciliationStatusValue;
  severity: SeverityValue | null;
}

export interface PtfCostResult {
  total_ptf_cost_tl: number;
  weighted_avg_ptf_tl_per_mwh: number;
  hours_matched: number;
  hours_missing_ptf: number;
  missing_ptf_pct: number;
  ptf_data_sufficient: boolean;
  warning: string | null;
}

export interface YekdemCostResult {
  yekdem_tl_per_mwh: number;
  total_yekdem_cost_tl: number;
  available: boolean;
}

export interface CostComparison {
  invoice_energy_tl: number;
  invoice_distribution_tl: number;
  invoice_total_tl: number;
  gelka_energy_tl: number;
  gelka_distribution_tl: number;
  gelka_total_tl: number;
  diff_tl: number;
  diff_pct: number;
  message: string;
}

// ── v2: Cost Inputs metadata (per period — always populated) ───────────────────
//
// SoT lock: ptf_source ve yekdem_source backend'de Pydantic Literal[...]
// olarak sabitlendi. FE de literal type kullanır → accidental drift compile
// time'da yakalanır.
export interface CostInputs {
  ptf_source: 'hourly_market_prices';
  yekdem_source: 'monthly_yekdem_prices';
  period_start: string;  // YYYY-MM-DD
  period_end: string;    // YYYY-MM-DD
  total_hours: number;
  /**
   * true  → reference_energy_cost_tl başarıyla hesaplandı (PTF + YEKDEM tam)
   * false → en az bir saat PTF eksik VEYA YEKDEM eksik VEYA empty records
   */
  complete: boolean;
}

export interface PeriodResult {
  period: string;
  total_kwh: number;
  t1_kwh: number;
  t2_kwh: number;
  t3_kwh: number;
  t1_pct: number;
  t2_pct: number;
  t3_pct: number;
  missing_hours: number;
  duplicate_hours: number;
  reconciliation: ReconciliationItem[];
  overall_status: ReconciliationStatusValue;
  overall_severity: SeverityValue | null;
  ptf_cost: PtfCostResult | null;
  yekdem_cost: YekdemCostResult | null;
  cost_comparison: CostComparison | null;
  quote_blocked: boolean;
  quote_block_reason: string | null;
  warnings: string[];

  // ── v2: Cost headline fields ───────────────────────────────────────────────
  //
  // Hepsi null olabilir (fail-closed). cost_inputs ZORUNLU — fail-closed
  // durumunda bile cost_inputs.complete=false ile dolu döner.
  reference_energy_cost_tl: number | null;
  supplier_markup_tl: number | null;
  supplier_markup_pct: number | null;
  gelka_estimate_tl: number | null;
  potential_savings_tl: number | null;
  cost_inputs: CostInputs;
}

export interface ParseStats {
  total_rows: number;
  successful_rows: number;
  failed_rows: number;
}

// ── v2: Annual projection (multi-period summary subblock) ──────────────────────
//
// Disclaimer label string'i kontratta SABİT — "tahmini yıllık projeksiyon".
// "Gerçek yıllık tasarruf" gibi alternatif label backend'de de FE'de de
// üretilmemeli (terminology lock — REQ-4).
//
// based_on_periods: kaç valid (reference_energy_cost_tl != null) dönem üzerinden
// extrapolate edildiği. FE kullanıcıya disclosure için göstermeli.
//
// annualized_* alanları null olabilir (ör. invoice yoksa savings null kalır).
export interface AnnualProjection {
  based_on_periods: number;
  label: 'tahmini yıllık projeksiyon';
  annualized_reference_cost_tl: number | null;
  annualized_supplier_markup_tl: number | null;
  annualized_potential_savings_tl: number | null;
}

// ── v2: Multi-period summary ──────────────────────────────────────────────────
//
// v1 alanları (period_count, total_kwh, t1/t2/t3_kwh, total_*_tl,
// periods_with_quotes, periods_blocked) preserved — semantikleri değişmedi.
//
// v2 alanları:
//   - count fields: valid/partial/total_period_count
//   - sum fields: total_reference_energy_cost_tl, total_supplier_markup_tl,
//                 total_gelka_estimate_tl, total_potential_savings_tl
//                 → number | null. null = hiçbir dönem hesaplanamadı veya
//                   markup/savings için yeterli veri yok. ASLA "0" gibi render
//                   edilmemeli.
//   - annual_projection: AnnualProjection | null. null = valid_period_count == 0.
//
// Multi-period olmadığında (tek dönem) backend summary alanını null bırakır.
export interface MultiPeriodSummary {
  // v1 (preserved)
  period_count: number;
  total_kwh: number;
  t1_kwh: number;
  t2_kwh: number;
  t3_kwh: number;
  total_ptf_cost_tl: number;
  total_yekdem_cost_tl: number;
  total_invoice_tl: number;
  total_gelka_tl: number;
  total_diff_tl: number;
  periods_with_quotes: number;
  periods_blocked: number;

  // v2 — count fields
  valid_period_count: number;
  partial_period_count: number;
  total_period_count: number;

  // v2 — sum fields (null when no underlying period contributes; FE: null ≠ 0)
  total_reference_energy_cost_tl: number | null;
  total_supplier_markup_tl: number | null;
  total_gelka_estimate_tl: number | null;
  total_potential_savings_tl: number | null;

  // v2 — annual projection (null when valid_period_count == 0)
  annual_projection: AnnualProjection | null;
}

export interface ReconReport {
  api_version: 2;  // literal — v2 contract lock; drift guard at compile time
  status: ReconStatus;  // "ok" | "partial" — BOTH are valid results, NOT errors
  format_detected: ExcelFormatValue;
  parse_stats: ParseStats;
  periods: PeriodResult[];
  summary: MultiPeriodSummary | null;  // null when single period
  warnings: string[];
  multiplier_metadata: number | null;  // Format A çarpan (info only, never applied)
}

// ── Error Response (only for HTTP 400/500) ──

export interface ErrorResponse {
  error: string;
  message: string;
  details?: Record<string, unknown>;
}
