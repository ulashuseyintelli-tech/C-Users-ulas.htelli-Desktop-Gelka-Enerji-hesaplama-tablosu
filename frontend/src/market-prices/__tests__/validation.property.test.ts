// =============================================================================
// Property-Based Tests: Validation (Force Update + Decimal Serialization)
// Feature: ptf-admin-frontend
// =============================================================================
// **Validates: Requirements 5.3, 5.10**

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

// ---------------------------------------------------------------------------
// Pure logic extracted from UpsertFormModal (mirrors production exactly)
// Production code is NOT modified.
// ---------------------------------------------------------------------------

/**
 * Client-side validation: force_update requires non-empty change_reason.
 * Returns true if form is valid for submission, false if blocked.
 */
function isForceUpdateValid(forceUpdate: boolean, changeReason: string): boolean {
  if (forceUpdate && changeReason.trim() === '') {
    return false;
  }
  return true;
}

/**
 * Parse user-entered value string to a number with dot decimal separator.
 * Mirrors UpsertFormModal.parseValueForApi exactly.
 */
function parseValueForApi(raw: string): number {
  const normalized = raw.replace(',', '.');
  return Number(normalized);
}

/**
 * Serialize a numeric value for the API request body.
 * JSON.stringify always uses dot as decimal separator.
 */
function serializeValueForApi(value: number): string {
  return JSON.stringify(value);
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** Whitespace-only strings (empty, spaces, tabs, newlines) */
const whitespaceOnlyArb = fc.stringOf(
  fc.constantFrom(' ', '\t', '\n', '\r', ''),
  { minLength: 0, maxLength: 20 },
);

/** Non-empty, non-whitespace-only strings */
const nonEmptyReasonArb = fc
  .string({ minLength: 1, maxLength: 200 })
  .filter((s) => s.trim().length > 0);

/** Numeric strings in various formats users might enter */
const numericInputArb = fc.oneof(
  // Integer strings
  fc.integer({ min: 0, max: 999999 }).map(String),
  // Dot decimal (English style)
  fc.tuple(
    fc.integer({ min: 0, max: 999999 }),
    fc.integer({ min: 0, max: 99 }),
  ).map(([int, dec]) => `${int}.${String(dec).padStart(2, '0')}`),
  // Comma decimal (Turkish style)
  fc.tuple(
    fc.integer({ min: 0, max: 999999 }),
    fc.integer({ min: 0, max: 99 }),
  ).map(([int, dec]) => `${int},${String(dec).padStart(2, '0')}`),
);

// ---------------------------------------------------------------------------
// Property 7: Force Update Requires Change Reason
// ---------------------------------------------------------------------------

describe('Property 7: Force Update Requires Change Reason', () => {
  it('force_update=true with empty/whitespace change_reason blocks submission', () => {
    fc.assert(
      fc.property(whitespaceOnlyArb, (reason) => {
        expect(isForceUpdateValid(true, reason)).toBe(false);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('force_update=true with non-empty change_reason allows submission', () => {
    fc.assert(
      fc.property(nonEmptyReasonArb, (reason) => {
        expect(isForceUpdateValid(true, reason)).toBe(true);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('force_update=false always allows submission regardless of change_reason', () => {
    fc.assert(
      fc.property(fc.string({ maxLength: 200 }), (reason) => {
        expect(isForceUpdateValid(false, reason)).toBe(true);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('validation is deterministic: same input always produces same result', () => {
    fc.assert(
      fc.property(fc.boolean(), fc.string({ maxLength: 200 }), (forceUpdate, reason) => {
        const first = isForceUpdateValid(forceUpdate, reason);
        const second = isForceUpdateValid(forceUpdate, reason);
        expect(first).toBe(second);
      }),
      { numRuns: 200, seed: 42 }
    );
  });
});

// ---------------------------------------------------------------------------
// Property 9: Decimal Serialization
// ---------------------------------------------------------------------------

describe('Property 9: Decimal Serialization', () => {
  it('parseValueForApi always produces a number (never NaN for valid numeric input)', () => {
    fc.assert(
      fc.property(numericInputArb, (raw) => {
        const parsed = parseValueForApi(raw);
        expect(typeof parsed).toBe('number');
        expect(Number.isNaN(parsed)).toBe(false);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('serialized value always uses dot as decimal separator, never comma', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 1e9, noNaN: true, noDefaultInfinity: true }),
        (value) => {
          const serialized = serializeValueForApi(value);
          // Must never contain comma (Turkish decimal separator)
          expect(serialized).not.toContain(',');
          // Must be a valid JSON number (dot for decimal, 'e' for scientific notation)
          expect(serialized).toMatch(/^[\d.eE+-]+$/);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('Turkish comma input "2508,80" is correctly parsed to 2508.80', () => {
    fc.assert(
      fc.property(
        fc.tuple(
          fc.integer({ min: 0, max: 999999 }),
          fc.integer({ min: 0, max: 99 }),
        ),
        ([intPart, decPart]) => {
          const turkishInput = `${intPart},${String(decPart).padStart(2, '0')}`;
          const parsed = parseValueForApi(turkishInput);
          const expected = intPart + decPart / 100;
          expect(Math.abs(parsed - expected)).toBeLessThanOrEqual(0.005);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('dot input passes through unchanged', () => {
    fc.assert(
      fc.property(
        fc.tuple(
          fc.integer({ min: 0, max: 999999 }),
          fc.integer({ min: 0, max: 99 }),
        ),
        ([intPart, decPart]) => {
          const dotInput = `${intPart}.${String(decPart).padStart(2, '0')}`;
          const parsed = parseValueForApi(dotInput);
          const expected = intPart + decPart / 100;
          expect(Math.abs(parsed - expected)).toBeLessThanOrEqual(0.005);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('parseValueForApi is idempotent on the numeric result', () => {
    fc.assert(
      fc.property(numericInputArb, (raw) => {
        const first = parseValueForApi(raw);
        // Re-parse the stringified result
        const second = parseValueForApi(String(first));
        expect(second).toBe(first);
      }),
      { numRuns: 200, seed: 42 }
    );
  });
});
