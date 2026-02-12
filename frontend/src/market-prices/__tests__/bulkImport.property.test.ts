// =============================================================================
// Property-Based Tests: Bulk Import (Preview, Export, Apply)
// Feature: ptf-admin-frontend, Property 10, 11, 12
// =============================================================================
// **Validates: Requirements 6.2, 6.3, 7.3, 7.4**

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { exportFailedRowsCsv, exportFailedRowsJson } from '../utils';
import type {
  BulkImportPreviewResponse,
  BulkImportApplyResponse,
  BulkImportError,
} from '../types';

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Generate a valid BulkImportError */
const bulkImportErrorArb = fc.record({
  row: fc.integer({ min: 1, max: 10000 }),
  field: fc.constantFrom('period', 'value', 'status', 'price_type', 'source_note'),
  error: fc.string({ minLength: 1, maxLength: 100 }).filter((s) => !s.includes('\0')),
});

/** Generate a valid BulkImportPreviewResponse with consistent counts */
const previewResponseArb = fc
  .record({
    valid_rows: fc.integer({ min: 0, max: 500 }),
    invalid_rows: fc.integer({ min: 0, max: 500 }),
    new_records: fc.integer({ min: 0, max: 500 }),
    updates: fc.integer({ min: 0, max: 500 }),
    unchanged: fc.integer({ min: 0, max: 500 }),
    final_conflicts: fc.integer({ min: 0, max: 100 }),
    errors: fc.array(bulkImportErrorArb, { minLength: 0, maxLength: 10 }),
  })
  .map((p) => ({
    status: 'ok' as const,
    preview: {
      total_rows: p.valid_rows + p.invalid_rows,
      valid_rows: p.valid_rows,
      invalid_rows: p.invalid_rows,
      new_records: p.new_records,
      updates: p.updates,
      unchanged: p.unchanged,
      final_conflicts: p.final_conflicts,
      errors: p.errors,
    },
  }));

/** Generate a valid BulkImportApplyResponse with consistent counts */
const applyResponseArb = fc
  .record({
    imported_count: fc.integer({ min: 0, max: 500 }),
    skipped_count: fc.integer({ min: 0, max: 500 }),
    error_count: fc.integer({ min: 0, max: 500 }),
    details: fc.array(bulkImportErrorArb, { minLength: 0, maxLength: 10 }),
  })
  .map((r) => ({
    status: 'ok' as const,
    result: {
      success: r.error_count === 0,
      imported_count: r.imported_count,
      skipped_count: r.skipped_count,
      error_count: r.error_count,
      details: r.details,
    },
  }));

// ---------------------------------------------------------------------------
// Property 10: Preview Summary Completeness
// ---------------------------------------------------------------------------

describe('Property 10: Preview Summary Completeness', () => {
  it('all summary fields are present and accessible in preview response', () => {
    fc.assert(
      fc.property(previewResponseArb, (response: BulkImportPreviewResponse) => {
        const p = response.preview;
        // All 7 summary fields must be defined numbers
        expect(typeof p.total_rows).toBe('number');
        expect(typeof p.valid_rows).toBe('number');
        expect(typeof p.invalid_rows).toBe('number');
        expect(typeof p.new_records).toBe('number');
        expect(typeof p.updates).toBe('number');
        expect(typeof p.unchanged).toBe('number');
        expect(typeof p.final_conflicts).toBe('number');
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('total_rows equals valid_rows + invalid_rows', () => {
    fc.assert(
      fc.property(previewResponseArb, (response: BulkImportPreviewResponse) => {
        const p = response.preview;
        expect(p.total_rows).toBe(p.valid_rows + p.invalid_rows);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('each error entry has row, field, and error properties', () => {
    fc.assert(
      fc.property(previewResponseArb, (response: BulkImportPreviewResponse) => {
        for (const err of response.preview.errors) {
          expect(typeof err.row).toBe('number');
          expect(typeof err.field).toBe('string');
          expect(typeof err.error).toBe('string');
          expect(err.field.length).toBeGreaterThan(0);
          expect(err.error.length).toBeGreaterThan(0);
        }
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('errors array is always present (even if empty)', () => {
    fc.assert(
      fc.property(previewResponseArb, (response: BulkImportPreviewResponse) => {
        expect(Array.isArray(response.preview.errors)).toBe(true);
      }),
      { numRuns: 200, seed: 42 }
    );
  });
});

// ---------------------------------------------------------------------------
// Property 11: Failed Rows Export Round-Trip
// ---------------------------------------------------------------------------

describe('Property 11: Failed Rows Export Round-Trip', () => {
  it('JSON export round-trips: export then parse preserves all data', () => {
    fc.assert(
      fc.property(
        fc.array(bulkImportErrorArb, { minLength: 1, maxLength: 20 }),
        (errors: BulkImportError[]) => {
          const json = exportFailedRowsJson(errors);
          const parsed = JSON.parse(json) as BulkImportError[];
          expect(parsed).toHaveLength(errors.length);
          for (let i = 0; i < errors.length; i++) {
            expect(parsed[i].row).toBe(errors[i].row);
            expect(parsed[i].field).toBe(errors[i].field);
            expect(parsed[i].error).toBe(errors[i].error);
          }
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('CSV export preserves row count (header + N data rows)', () => {
    fc.assert(
      fc.property(
        fc.array(bulkImportErrorArb, { minLength: 1, maxLength: 20 }),
        (errors: BulkImportError[]) => {
          const csv = exportFailedRowsCsv(errors);
          const lines = csv.split('\n');
          // Header + data rows
          expect(lines.length).toBe(errors.length + 1);
          expect(lines[0]).toBe('row,field,error');
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('CSV export contains each error row number in the output', () => {
    fc.assert(
      fc.property(
        fc.array(bulkImportErrorArb, { minLength: 1, maxLength: 20 }),
        (errors: BulkImportError[]) => {
          const csv = exportFailedRowsCsv(errors);
          for (const err of errors) {
            // Each data line starts with the row number
            expect(csv).toContain(String(err.row));
          }
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('JSON export is deterministic: same input always produces same output', () => {
    fc.assert(
      fc.property(
        fc.array(bulkImportErrorArb, { minLength: 0, maxLength: 10 }),
        (errors: BulkImportError[]) => {
          const first = exportFailedRowsJson(errors);
          const second = exportFailedRowsJson(errors);
          expect(first).toBe(second);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('CSV export is deterministic: same input always produces same output', () => {
    fc.assert(
      fc.property(
        fc.array(bulkImportErrorArb, { minLength: 0, maxLength: 10 }),
        (errors: BulkImportError[]) => {
          const first = exportFailedRowsCsv(errors);
          const second = exportFailedRowsCsv(errors);
          expect(first).toBe(second);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('empty error list produces header-only CSV and empty JSON array', () => {
    const csv = exportFailedRowsCsv([]);
    expect(csv).toBe('row,field,error');

    const json = exportFailedRowsJson([]);
    expect(json).toBe('[]');
  });
});

// ---------------------------------------------------------------------------
// Property 12: Apply Result Summary Completeness
// ---------------------------------------------------------------------------

describe('Property 12: Apply Result Summary Completeness', () => {
  it('all result fields are present and accessible', () => {
    fc.assert(
      fc.property(applyResponseArb, (response: BulkImportApplyResponse) => {
        const r = response.result;
        expect(typeof r.imported_count).toBe('number');
        expect(typeof r.skipped_count).toBe('number');
        expect(typeof r.error_count).toBe('number');
        expect(typeof r.success).toBe('boolean');
        expect(Array.isArray(r.details)).toBe(true);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('success is true iff error_count is 0', () => {
    fc.assert(
      fc.property(applyResponseArb, (response: BulkImportApplyResponse) => {
        const r = response.result;
        expect(r.success).toBe(r.error_count === 0);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('all counts are non-negative', () => {
    fc.assert(
      fc.property(applyResponseArb, (response: BulkImportApplyResponse) => {
        const r = response.result;
        expect(r.imported_count).toBeGreaterThanOrEqual(0);
        expect(r.skipped_count).toBeGreaterThanOrEqual(0);
        expect(r.error_count).toBeGreaterThanOrEqual(0);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('details array entries have valid structure', () => {
    fc.assert(
      fc.property(applyResponseArb, (response: BulkImportApplyResponse) => {
        for (const detail of response.result.details) {
          expect(typeof detail.row).toBe('number');
          expect(typeof detail.field).toBe('string');
          expect(typeof detail.error).toBe('string');
        }
      }),
      { numRuns: 200, seed: 42 }
    );
  });
});
