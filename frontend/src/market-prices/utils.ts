// =============================================================================
// PTF Admin Frontend — Utility Functions
// =============================================================================

import type { ListParams, ApiErrorResponse, BulkImportError } from './types';
import { ERROR_CODE_MAP, DEFAULT_LIST_PARAMS } from './constants';

// ---------------------------------------------------------------------------
// Price Formatting
// ---------------------------------------------------------------------------

const trPriceFormatter = new Intl.NumberFormat('tr-TR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * Format a number to Turkish locale: 2508.80 → "2.508,80"
 */
export function formatPrice(value: number): string {
  return trPriceFormatter.format(value);
}

// ---------------------------------------------------------------------------
// DateTime Formatting
// ---------------------------------------------------------------------------

const trDateTimeFormatter = new Intl.DateTimeFormat('tr-TR', {
  timeZone: 'Europe/Istanbul',
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

/**
 * Convert ISO 8601 UTC string to "DD.MM.YYYY HH:mm" in Europe/Istanbul timezone.
 */
export function formatDateTime(isoString: string): string {
  const date = new Date(isoString);
  const parts = trDateTimeFormatter.formatToParts(date);

  const get = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((p) => p.type === type)?.value ?? '';

  const day = get('day');
  const month = get('month');
  const year = get('year');
  const hour = get('hour');
  const minute = get('minute');

  return `${day}.${month}.${year} ${hour}:${minute}`;
}

// ---------------------------------------------------------------------------
// URL Parameter Parsing
// ---------------------------------------------------------------------------

/**
 * Parse URL query string to Partial<ListParams>.
 * Only non-default, valid values are included in the result.
 */
export function parseUrlParams(search: string): Partial<ListParams> {
  const params = new URLSearchParams(search);
  const result: Partial<ListParams> = {};

  // page
  const pageStr = params.get('page');
  if (pageStr !== null) {
    const page = Number(pageStr);
    if (Number.isInteger(page) && page >= 1) {
      result.page = page;
    }
  }

  // page_size
  const pageSizeStr = params.get('page_size');
  if (pageSizeStr !== null) {
    const pageSize = Number(pageSizeStr);
    if (Number.isInteger(pageSize) && pageSize >= 1) {
      result.page_size = pageSize;
    }
  }

  // sort_by
  const sortBy = params.get('sort_by');
  if (sortBy !== null && sortBy !== '') {
    result.sort_by = sortBy;
  }

  // sort_order
  const sortOrder = params.get('sort_order');
  if (sortOrder === 'asc' || sortOrder === 'desc') {
    result.sort_order = sortOrder;
  }

  // status
  const status = params.get('status');
  if (status === 'provisional' || status === 'final') {
    result.status = status;
  }

  // from_period
  const fromPeriod = params.get('from_period');
  if (fromPeriod !== null && fromPeriod !== '') {
    result.from_period = fromPeriod;
  }

  // to_period
  const toPeriod = params.get('to_period');
  if (toPeriod !== null && toPeriod !== '') {
    result.to_period = toPeriod;
  }

  return result;
}

// ---------------------------------------------------------------------------
// URL Parameter Serialization
// ---------------------------------------------------------------------------

/**
 * Serialize Partial<ListParams> to URL query string.
 * Omits default values (page=1, page_size=20, sort_by="period", sort_order="desc").
 * Omits empty strings. Returns string WITHOUT leading "?".
 */
export function serializeUrlParams(params: Partial<ListParams>): string {
  const searchParams = new URLSearchParams();

  if (params.page !== undefined && params.page !== DEFAULT_LIST_PARAMS.page) {
    searchParams.set('page', String(params.page));
  }

  if (params.page_size !== undefined && params.page_size !== DEFAULT_LIST_PARAMS.page_size) {
    searchParams.set('page_size', String(params.page_size));
  }

  if (params.sort_by !== undefined && params.sort_by !== DEFAULT_LIST_PARAMS.sort_by) {
    searchParams.set('sort_by', params.sort_by);
  }

  if (params.sort_order !== undefined && params.sort_order !== DEFAULT_LIST_PARAMS.sort_order) {
    searchParams.set('sort_order', params.sort_order);
  }

  if (params.status !== undefined) {
    searchParams.set('status', params.status);
  }

  if (params.from_period !== undefined && params.from_period !== '') {
    searchParams.set('from_period', params.from_period);
  }

  if (params.to_period !== undefined && params.to_period !== '') {
    searchParams.set('to_period', params.to_period);
  }

  return searchParams.toString();
}

// ---------------------------------------------------------------------------
// Error Parsing
// ---------------------------------------------------------------------------

/**
 * Map backend error response to field→message record using ERROR_CODE_MAP.
 * If error_code has a field mapping, return {[field]: message}.
 * If no field mapping, return empty object (error goes to global toast instead).
 */
export function parseFieldErrors(error: ApiErrorResponse): Record<string, string> {
  const mapping = ERROR_CODE_MAP[error.error_code];
  if (mapping && mapping.field) {
    return { [mapping.field]: mapping.message };
  }
  return {};
}

// ---------------------------------------------------------------------------
// CSV Export
// ---------------------------------------------------------------------------

/**
 * Escape a CSV field: wrap in quotes if it contains commas, quotes, or newlines.
 * Double any existing quotes.
 */
function escapeCsvField(value: string): string {
  if (value.includes(',') || value.includes('"') || value.includes('\n') || value.includes('\r')) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

/**
 * Convert BulkImportError array to CSV string with header "row,field,error"
 * and one row per error. Properly escapes fields containing commas or quotes.
 */
export function exportFailedRowsCsv(errors: BulkImportError[]): string {
  const header = 'row,field,error';
  const rows = errors.map(
    (e) => `${e.row},${escapeCsvField(e.field)},${escapeCsvField(e.error)}`
  );
  return [header, ...rows].join('\n');
}

// ---------------------------------------------------------------------------
// JSON Export
// ---------------------------------------------------------------------------

/**
 * Convert BulkImportError array to pretty-printed JSON string.
 */
export function exportFailedRowsJson(errors: BulkImportError[]): string {
  return JSON.stringify(errors, null, 2);
}
