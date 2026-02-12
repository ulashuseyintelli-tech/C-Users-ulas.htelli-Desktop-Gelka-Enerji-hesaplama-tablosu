// =============================================================================
// Property-Based Tests: Error Code to Field Routing
// Feature: ptf-admin-frontend, Property 8
// =============================================================================
// **Validates: Requirements 5.7, 8.2, 8.3, 8.4**

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { parseFieldErrors } from '../utils';
import { ERROR_CODE_MAP } from '../constants';
import type { ApiErrorResponse } from '../types';

// ---------------------------------------------------------------------------
// Derived data from ERROR_CODE_MAP for generators
// ---------------------------------------------------------------------------

const allErrorCodes = Object.keys(ERROR_CODE_MAP);
const codesWithField = allErrorCodes.filter((code) => ERROR_CODE_MAP[code].field);
const codesWithoutField = allErrorCodes.filter((code) => !ERROR_CODE_MAP[code].field);

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Known error code with field mapping */
const fieldErrorCodeArb = fc.constantFrom(...codesWithField);

/** Known error code without field mapping (global toast) */
const globalErrorCodeArb = fc.constantFrom(...codesWithoutField);

/** Any known error code */
const knownErrorCodeArb = fc.constantFrom(...allErrorCodes);

/** Unknown error code (not in ERROR_CODE_MAP) */
const unknownErrorCodeArb = fc
  .string({ minLength: 3, maxLength: 40 })
  .filter((s) => /^[A-Z_]+$/.test(s) && !allErrorCodes.includes(s));

/** Build an ApiErrorResponse from an error code */
function makeError(errorCode: string): ApiErrorResponse {
  return {
    status: 'error',
    error_code: errorCode,
    message: `Backend message for ${errorCode}`,
  };
}

// ---------------------------------------------------------------------------
// Property 8: Error Code to Field Routing
// ---------------------------------------------------------------------------

describe('Property 8: Error Code to Field Routing', () => {
  it('known codes with field mapping return exactly one field entry', () => {
    fc.assert(
      fc.property(fieldErrorCodeArb, (code) => {
        const result = parseFieldErrors(makeError(code));
        const keys = Object.keys(result);
        expect(keys).toHaveLength(1);
      }),
      { numRuns: 100, seed: 42 }
    );
  });

  it('known codes with field mapping route to the correct field', () => {
    fc.assert(
      fc.property(fieldErrorCodeArb, (code) => {
        const result = parseFieldErrors(makeError(code));
        const expectedField = ERROR_CODE_MAP[code].field!;
        expect(result).toHaveProperty(expectedField);
        expect(result[expectedField]).toBe(ERROR_CODE_MAP[code].message);
      }),
      { numRuns: 100, seed: 42 }
    );
  });

  it('known codes without field mapping return empty object (→ global toast)', () => {
    fc.assert(
      fc.property(globalErrorCodeArb, (code) => {
        const result = parseFieldErrors(makeError(code));
        expect(Object.keys(result)).toHaveLength(0);
      }),
      { numRuns: 100, seed: 42 }
    );
  });

  it('unknown error codes return empty object (→ global toast)', () => {
    fc.assert(
      fc.property(unknownErrorCodeArb, (code) => {
        const result = parseFieldErrors(makeError(code));
        expect(Object.keys(result)).toHaveLength(0);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('parseFieldErrors is deterministic: same error always produces same result', () => {
    fc.assert(
      fc.property(knownErrorCodeArb, (code) => {
        const error = makeError(code);
        const first = parseFieldErrors(error);
        const second = parseFieldErrors(error);
        expect(first).toEqual(second);
      }),
      { numRuns: 100, seed: 42 }
    );
  });

  it('parseFieldErrors is idempotent on the mapping (no side effects)', () => {
    fc.assert(
      fc.property(knownErrorCodeArb, (code) => {
        const error = makeError(code);
        // Call multiple times — result should be identical
        const results = Array.from({ length: 5 }, () => parseFieldErrors(error));
        for (let i = 1; i < results.length; i++) {
          expect(results[i]).toEqual(results[0]);
        }
      }),
      { numRuns: 100, seed: 42 }
    );
  });

  it('field-mapped codes cover all expected fields from ERROR_CODE_MAP', () => {
    // Exhaustive check: every code with a field in the map produces that field
    for (const code of codesWithField) {
      const result = parseFieldErrors(makeError(code));
      const expectedField = ERROR_CODE_MAP[code].field!;
      expect(result).toHaveProperty(expectedField);
    }
  });

  it('global-routed codes cover all expected codes from ERROR_CODE_MAP', () => {
    // Exhaustive check: every code without a field produces empty object
    for (const code of codesWithoutField) {
      const result = parseFieldErrors(makeError(code));
      expect(Object.keys(result)).toHaveLength(0);
    }
  });
});
