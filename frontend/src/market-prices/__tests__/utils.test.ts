import { describe, it, expect } from 'vitest';
import {
  formatPrice,
  formatDateTime,
  parseUrlParams,
  serializeUrlParams,
  parseFieldErrors,
  exportFailedRowsCsv,
  exportFailedRowsJson,
} from '../utils';
import type { ApiErrorResponse, BulkImportError } from '../types';

// =============================================================================
// formatPrice
// =============================================================================

describe('formatPrice', () => {
  it('formats integer with two decimals', () => {
    expect(formatPrice(100)).toBe('100,00');
  });

  it('formats thousands with dot separator', () => {
    expect(formatPrice(2508.8)).toBe('2.508,80');
  });

  it('formats zero', () => {
    expect(formatPrice(0)).toBe('0,00');
  });

  it('formats large numbers', () => {
    expect(formatPrice(1234567.89)).toBe('1.234.567,89');
  });

  it('rounds to two decimal places', () => {
    expect(formatPrice(1.999)).toBe('2,00');
  });

  it('formats small decimal', () => {
    expect(formatPrice(0.5)).toBe('0,50');
  });
});

// =============================================================================
// formatDateTime
// =============================================================================

describe('formatDateTime', () => {
  it('formats UTC midnight to Istanbul time (UTC+3)', () => {
    // 2025-01-15T00:00:00Z → Istanbul is UTC+3 in winter → 15.01.2025 03:00
    const result = formatDateTime('2025-01-15T00:00:00Z');
    expect(result).toBe('15.01.2025 03:00');
  });

  it('formats a specific timestamp', () => {
    // 2025-01-15T10:30:00Z → Istanbul UTC+3 → 15.01.2025 13:30
    const result = formatDateTime('2025-01-15T10:30:00Z');
    expect(result).toBe('15.01.2025 13:30');
  });

  it('produces DD.MM.YYYY HH:mm format (16 chars)', () => {
    const result = formatDateTime('2025-06-15T12:00:00Z');
    expect(result).toMatch(/^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$/);
    expect(result).toHaveLength(16);
  });

  it('handles summer time (UTC+3 for Istanbul)', () => {
    // 2025-07-15T21:00:00Z → Istanbul UTC+3 in summer → 16.07.2025 00:00
    const result = formatDateTime('2025-07-15T21:00:00Z');
    expect(result).toBe('16.07.2025 00:00');
  });
});

// =============================================================================
// parseUrlParams
// =============================================================================

describe('parseUrlParams', () => {
  it('returns empty object for empty search string', () => {
    expect(parseUrlParams('')).toEqual({});
  });

  it('parses page parameter', () => {
    expect(parseUrlParams('?page=3')).toEqual({ page: 3 });
  });

  it('parses page_size parameter', () => {
    expect(parseUrlParams('?page_size=50')).toEqual({ page_size: 50 });
  });

  it('parses sort_by parameter', () => {
    expect(parseUrlParams('?sort_by=ptf_tl_per_mwh')).toEqual({ sort_by: 'ptf_tl_per_mwh' });
  });

  it('parses sort_order asc', () => {
    expect(parseUrlParams('?sort_order=asc')).toEqual({ sort_order: 'asc' });
  });

  it('parses sort_order desc', () => {
    expect(parseUrlParams('?sort_order=desc')).toEqual({ sort_order: 'desc' });
  });

  it('ignores invalid sort_order', () => {
    expect(parseUrlParams('?sort_order=invalid')).toEqual({});
  });

  it('parses status provisional', () => {
    expect(parseUrlParams('?status=provisional')).toEqual({ status: 'provisional' });
  });

  it('parses status final', () => {
    expect(parseUrlParams('?status=final')).toEqual({ status: 'final' });
  });

  it('ignores invalid status', () => {
    expect(parseUrlParams('?status=unknown')).toEqual({});
  });

  it('parses from_period and to_period', () => {
    expect(parseUrlParams('?from_period=2024-01&to_period=2025-06')).toEqual({
      from_period: '2024-01',
      to_period: '2025-06',
    });
  });

  it('ignores invalid page (non-integer)', () => {
    expect(parseUrlParams('?page=abc')).toEqual({});
  });

  it('ignores invalid page (zero)', () => {
    expect(parseUrlParams('?page=0')).toEqual({});
  });

  it('ignores invalid page (negative)', () => {
    expect(parseUrlParams('?page=-1')).toEqual({});
  });

  it('ignores invalid page_size (float)', () => {
    expect(parseUrlParams('?page_size=2.5')).toEqual({});
  });

  it('parses multiple parameters together', () => {
    const result = parseUrlParams('?page=2&page_size=50&sort_by=period&sort_order=asc&status=final&from_period=2024-01');
    expect(result).toEqual({
      page: 2,
      page_size: 50,
      sort_by: 'period',
      sort_order: 'asc',
      status: 'final',
      from_period: '2024-01',
    });
  });

  it('ignores empty string values for sort_by', () => {
    expect(parseUrlParams('?sort_by=')).toEqual({});
  });

  it('ignores empty string values for from_period', () => {
    expect(parseUrlParams('?from_period=')).toEqual({});
  });
});

// =============================================================================
// serializeUrlParams
// =============================================================================

describe('serializeUrlParams', () => {
  it('returns empty string for default values', () => {
    expect(serializeUrlParams({ page: 1, page_size: 20, sort_by: 'period', sort_order: 'desc' })).toBe('');
  });

  it('returns empty string for empty object', () => {
    expect(serializeUrlParams({})).toBe('');
  });

  it('includes non-default page', () => {
    expect(serializeUrlParams({ page: 3 })).toBe('page=3');
  });

  it('includes non-default page_size', () => {
    expect(serializeUrlParams({ page_size: 50 })).toBe('page_size=50');
  });

  it('includes non-default sort_by', () => {
    expect(serializeUrlParams({ sort_by: 'ptf_tl_per_mwh' })).toBe('sort_by=ptf_tl_per_mwh');
  });

  it('includes non-default sort_order', () => {
    expect(serializeUrlParams({ sort_order: 'asc' })).toBe('sort_order=asc');
  });

  it('includes status', () => {
    expect(serializeUrlParams({ status: 'final' })).toBe('status=final');
  });

  it('includes from_period and to_period', () => {
    const result = serializeUrlParams({ from_period: '2024-01', to_period: '2025-06' });
    expect(result).toContain('from_period=2024-01');
    expect(result).toContain('to_period=2025-06');
  });

  it('omits empty from_period', () => {
    expect(serializeUrlParams({ from_period: '' })).toBe('');
  });

  it('omits empty to_period', () => {
    expect(serializeUrlParams({ to_period: '' })).toBe('');
  });

  it('does not include leading ?', () => {
    const result = serializeUrlParams({ page: 2 });
    expect(result).not.toMatch(/^\?/);
  });

  it('serializes multiple non-default params', () => {
    const result = serializeUrlParams({
      page: 2,
      page_size: 50,
      sort_order: 'asc',
      status: 'provisional',
    });
    expect(result).toContain('page=2');
    expect(result).toContain('page_size=50');
    expect(result).toContain('sort_order=asc');
    expect(result).toContain('status=provisional');
  });
});

// =============================================================================
// parseFieldErrors
// =============================================================================

describe('parseFieldErrors', () => {
  it('returns field error for known code with field mapping', () => {
    const error: ApiErrorResponse = {
      status: 'error',
      error_code: 'INVALID_PERIOD_FORMAT',
      message: 'Invalid period format',
    };
    expect(parseFieldErrors(error)).toEqual({
      period: 'Geçersiz dönem formatı (YYYY-MM bekleniyor)',
    });
  });

  it('returns field error for INVALID_PTF_VALUE', () => {
    const error: ApiErrorResponse = {
      status: 'error',
      error_code: 'INVALID_PTF_VALUE',
      message: 'Invalid value',
    };
    expect(parseFieldErrors(error)).toEqual({
      value: 'Geçersiz PTF değeri',
    });
  });

  it('returns empty object for code without field mapping', () => {
    const error: ApiErrorResponse = {
      status: 'error',
      error_code: 'PERIOD_NOT_FOUND',
      message: 'Period not found',
    };
    expect(parseFieldErrors(error)).toEqual({});
  });

  it('returns empty object for unknown error code', () => {
    const error: ApiErrorResponse = {
      status: 'error',
      error_code: 'UNKNOWN_CODE',
      message: 'Unknown error',
    };
    expect(parseFieldErrors(error)).toEqual({});
  });

  it('returns field error for FINAL_RECORD_PROTECTED', () => {
    const error: ApiErrorResponse = {
      status: 'error',
      error_code: 'FINAL_RECORD_PROTECTED',
      message: 'Record is protected',
    };
    expect(parseFieldErrors(error)).toEqual({
      force_update: 'Kesinleşmiş kayıt force_update olmadan güncellenemez',
    });
  });
});

// =============================================================================
// exportFailedRowsCsv
// =============================================================================

describe('exportFailedRowsCsv', () => {
  it('returns header only for empty array', () => {
    expect(exportFailedRowsCsv([])).toBe('row,field,error');
  });

  it('formats single error row', () => {
    const errors: BulkImportError[] = [{ row: 1, field: 'period', error: 'Invalid format' }];
    expect(exportFailedRowsCsv(errors)).toBe('row,field,error\n1,period,Invalid format');
  });

  it('formats multiple error rows', () => {
    const errors: BulkImportError[] = [
      { row: 1, field: 'period', error: 'Invalid format' },
      { row: 3, field: 'value', error: 'Negative value' },
    ];
    const csv = exportFailedRowsCsv(errors);
    const lines = csv.split('\n');
    expect(lines).toHaveLength(3);
    expect(lines[0]).toBe('row,field,error');
    expect(lines[1]).toBe('1,period,Invalid format');
    expect(lines[2]).toBe('3,value,Negative value');
  });

  it('escapes fields containing commas', () => {
    const errors: BulkImportError[] = [
      { row: 1, field: 'value', error: 'Expected 100, got 200' },
    ];
    const csv = exportFailedRowsCsv(errors);
    expect(csv).toContain('"Expected 100, got 200"');
  });

  it('escapes fields containing quotes', () => {
    const errors: BulkImportError[] = [
      { row: 1, field: 'value', error: 'Value "abc" is invalid' },
    ];
    const csv = exportFailedRowsCsv(errors);
    expect(csv).toContain('"Value ""abc"" is invalid"');
  });
});

// =============================================================================
// exportFailedRowsJson
// =============================================================================

describe('exportFailedRowsJson', () => {
  it('returns empty array for no errors', () => {
    expect(exportFailedRowsJson([])).toBe('[]');
  });

  it('returns pretty-printed JSON', () => {
    const errors: BulkImportError[] = [{ row: 1, field: 'period', error: 'Invalid' }];
    const json = exportFailedRowsJson(errors);
    const parsed = JSON.parse(json);
    expect(parsed).toEqual([{ row: 1, field: 'period', error: 'Invalid' }]);
    // Verify it's pretty-printed (contains newlines)
    expect(json).toContain('\n');
  });

  it('preserves all fields in output', () => {
    const errors: BulkImportError[] = [
      { row: 2, field: 'value', error: 'Negative' },
      { row: 5, field: 'status', error: 'Unknown status' },
    ];
    const parsed = JSON.parse(exportFailedRowsJson(errors));
    expect(parsed).toHaveLength(2);
    expect(parsed[0]).toEqual({ row: 2, field: 'value', error: 'Negative' });
    expect(parsed[1]).toEqual({ row: 5, field: 'status', error: 'Unknown status' });
  });
});
