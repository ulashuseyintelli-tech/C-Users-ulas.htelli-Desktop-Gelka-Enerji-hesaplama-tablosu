// ═══════════════════════════════════════════════════════════════════════════════
// Invoice Reconciliation Engine — Backend Response Contract Mirror
// ═══════════════════════════════════════════════════════════════════════════════

// ── Request Types ──

export interface InvoiceInput {
  period: string;
  unit_price?: number;
  discount_pct?: number;
  distribution_unit_price?: number;
  declared_t1_kwh?: number;
  declared_t2_kwh?: number;
  declared_t3_kwh?: number;
  declared_total_kwh?: number;
}

export interface ToleranceConfig {
  kwh_abs?: number;
  kwh_pct?: number;
  cost_abs?: number;
  cost_pct?: number;
}

export interface ComparisonConfig {
  include_ptf?: boolean;
  include_yekdem?: boolean;
  include_distribution?: boolean;
}

export interface ReconRequest {
  invoice_input?: InvoiceInput;
  tolerance?: ToleranceConfig;
  comparison?: ComparisonConfig;
}

// ── Response Types ──

export interface ReconciliationItem {
  field: string;
  excel_total_kwh: number;
  invoice_total_kwh: number;
  delta_kwh: number;
  delta_pct: number;
  status: 'UYUMLU' | 'UYUMSUZ' | 'KONTROL_EDILMEDI';
  severity: 'LOW' | 'WARNING' | 'CRITICAL' | null;
}

export interface PtfCostResult {
  period: string;
  total_cost_tl: number;
  avg_ptf_tl_per_mwh: number;
  hours_matched: number;
  hours_missing: number;
}

export interface YekdemCostResult {
  period: string;
  total_cost_tl: number;
  yekdem_tl_per_mwh: number;
}

export interface CostComparison {
  invoice_total_tl: number;
  gelka_total_tl: number;
  difference_tl: number;
  difference_pct: number;
  message: string;
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
  overall_status: string;
  overall_severity: string;
  ptf_cost: PtfCostResult | null;
  yekdem_cost: YekdemCostResult | null;
  cost_comparison: CostComparison | null;
  quote_blocked: boolean;
  quote_block_reason: string | null;
  warnings: string[];
}

export interface ReconReport {
  api_version: number;
  status: 'ok' | 'partial';
  format_detected: string;
  parse_stats: {
    total_rows: number;
    parsed_rows: number;
    error_rows: number;
    errors: string[];
  };
  periods: PeriodResult[];
  summary: {
    total_kwh: number;
    total_periods: number;
    periods_ok: number;
    periods_blocked: number;
  };
  warnings: string[];
  multiplier_metadata: {
    applied: boolean;
    value: number | null;
    source: string | null;
  };
}

// ── Error Response ──

export interface ErrorResponse {
  error: string;
  message: string;
  details?: string;
}
