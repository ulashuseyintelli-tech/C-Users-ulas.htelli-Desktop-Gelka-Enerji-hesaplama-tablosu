// =============================================================================
// PTF Admin Frontend — TypeScript Interfaces & Types
// =============================================================================

// ---------------------------------------------------------------------------
// Backend API Response Types
// ---------------------------------------------------------------------------

/** Backend list item representing a single market price record */
export interface MarketPriceRecord {
  period: string;                          // "2025-01"
  ptf_tl_per_mwh: number;                 // 2508.80
  status: 'provisional' | 'final';
  price_type: string;                      // "PTF"
  captured_at: string;                     // ISO 8601 UTC
  updated_at: string;                      // ISO 8601 UTC
  updated_by: string;
  source: string;                          // "epias_manual" | "epias_api" | "migration" | "seed"
  source_note: string;
  change_reason: string;
  is_locked: boolean;
  yekdem_tl_per_mwh: number;
}

/** Paginated list response from GET /admin/market-prices */
export interface MarketPricesListResponse {
  status: string;
  total: number;
  page: number;
  page_size: number;
  items: MarketPriceRecord[];
}

// ---------------------------------------------------------------------------
// Upsert (Create / Update) Types
// ---------------------------------------------------------------------------

/** Request body for POST /admin/market-prices */
export interface UpsertMarketPriceRequest {
  period: string;
  value: number;
  price_type: 'PTF';
  status: 'provisional' | 'final';
  source_note?: string;
  change_reason?: string;
  force_update: boolean;
}

/** Success response from POST /admin/market-prices */
export interface UpsertMarketPriceResponse {
  status: 'ok';
  action: 'created' | 'updated';
  period: string;
  warnings?: string[];
}

// ---------------------------------------------------------------------------
// Error Response
// ---------------------------------------------------------------------------

/** Backend error response structure */
export interface ApiErrorResponse {
  status: 'error';
  error_code: string;
  message: string;
  field?: string;
  row_index?: number | null;
  details?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Bulk Import Types
// ---------------------------------------------------------------------------

/** Single error entry from bulk import preview/apply */
export interface BulkImportError {
  row: number;
  field: string;
  error: string;
}

/** Response from POST /admin/market-prices/import/preview */
export interface BulkImportPreviewResponse {
  status: 'ok';
  preview: {
    total_rows: number;
    valid_rows: number;
    invalid_rows: number;
    new_records: number;
    updates: number;
    unchanged: number;
    final_conflicts: number;
    errors: BulkImportError[];
  };
}

/** Response from POST /admin/market-prices/import/apply */
export interface BulkImportApplyResponse {
  status: 'ok';
  result: {
    success: boolean;
    imported_count: number;
    skipped_count: number;
    error_count: number;
    details: BulkImportError[];
  };
}

// ---------------------------------------------------------------------------
// Internal UI State Types
// ---------------------------------------------------------------------------

/** Pagination state for the price list table */
export interface PaginationState {
  page: number;
  pageSize: number;
  total: number;
}

/** Parameters sent to the list API endpoint */
export interface ListParams {
  page: number;
  page_size: number;
  sort_by: string;
  sort_order: 'asc' | 'desc';
  price_type: 'PTF';
  status?: 'provisional' | 'final';
  from_period?: string;
  to_period?: string;
}

/** Filter state managed by PriceFilters component */
export interface FilterState {
  status: 'all' | 'provisional' | 'final';
  fromPeriod: string;   // "YYYY-MM" or ""
  toPeriod: string;     // "YYYY-MM" or ""
}

/** Toast notification message */
export interface ToastMessage {
  id: string;
  type: 'success' | 'info' | 'warning' | 'error';
  title: string;
  detail?: string;       // Backend error_code for debug display
  autoClose?: number;    // ms, default 5000
}

/** Upsert form internal state */
export interface UpsertFormState {
  period: string;
  value: string;          // String for input control, parsed to number on submit
  status: 'provisional' | 'final';
  changeReason: string;
  sourceNote: string;
  forceUpdate: boolean;
}

/** Bulk import wizard step */
export type BulkImportStep = 'upload' | 'preview' | 'result';

// ---------------------------------------------------------------------------
// Audit History Types
// ---------------------------------------------------------------------------

/** Single history entry from GET /admin/market-prices/history */
export interface AuditHistoryEntry {
  id: number;
  action: 'INSERT' | 'UPDATE';
  old_value: number | null;
  new_value: number;
  old_status: string | null;
  new_status: string;
  change_reason: string | null;
  updated_by: string | null;
  source: string | null;
  created_at: string;            // ISO 8601
}

/** Response from GET /admin/market-prices/history */
export interface AuditHistoryResponse {
  status: 'ok';
  period: string;
  price_type: string;
  history: AuditHistoryEntry[];
}
