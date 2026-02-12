// =============================================================================
// Property-Based Tests: formatPrice & formatDateTime
// Feature: ptf-admin-frontend
// =============================================================================
// **Validates: Requirements 1.2**

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { formatPrice, formatDateTime } from '../utils';

// ---------------------------------------------------------------------------
// Property 2: Price Formatting
// ---------------------------------------------------------------------------
// For any non-negative number, formatPrice produces a Turkish locale string
// with exactly 2 decimal places, and parsing that string back (replacing dots,
// swapping comma for dot) yields the original value within float tolerance.

describe('Property 2: Price Formatting', () => {
  it('always produces exactly 2 decimal places for any non-negative number', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 1e12, noNaN: true, noDefaultInfinity: true }),
        (value) => {
          const formatted = formatPrice(value);
          // Turkish locale uses comma as decimal separator
          const commaIdx = formatted.lastIndexOf(',');
          expect(commaIdx).toBeGreaterThan(-1);
          const decimals = formatted.slice(commaIdx + 1);
          expect(decimals).toHaveLength(2);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('uses dot as thousands separator and comma as decimal separator', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 1e12, noNaN: true, noDefaultInfinity: true }),
        (value) => {
          const formatted = formatPrice(value);
          // Must contain comma (decimal separator)
          expect(formatted).toContain(',');
          // Must not contain any character other than digits, dots, and comma
          expect(formatted).toMatch(/^[\d.,]+$/);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('round-trips within floating-point tolerance', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 1e9, noNaN: true, noDefaultInfinity: true }),
        (value) => {
          const formatted = formatPrice(value);
          // Reverse: remove dots (thousands), replace comma with dot (decimal)
          const normalized = formatted.replace(/\./g, '').replace(',', '.');
          const parsed = parseFloat(normalized);
          // Intl rounds to 2 decimals, so compare against the rounded value
          const rounded = Math.round(value * 100) / 100;
          expect(Math.abs(parsed - rounded)).toBeLessThanOrEqual(0.005);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('is idempotent: formatting the parsed-back value produces the same string', () => {
    fc.assert(
      fc.property(
        fc.double({ min: 0, max: 1e9, noNaN: true, noDefaultInfinity: true }),
        (value) => {
          const first = formatPrice(value);
          // Parse back
          const parsed = parseFloat(first.replace(/\./g, '').replace(',', '.'));
          const second = formatPrice(parsed);
          expect(second).toBe(first);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });
});

// ---------------------------------------------------------------------------
// Property 3: DateTime Formatting
// ---------------------------------------------------------------------------
// For any valid ISO 8601 UTC timestamp, formatDateTime produces a string in
// "DD.MM.YYYY HH:mm" format representing the Europe/Istanbul timezone, and
// the output always contains exactly 16 characters.

describe('Property 3: DateTime Formatting', () => {
  // Generator: valid ISO 8601 UTC timestamps within a reasonable range
  // Range: 2000-01-01 to 2099-12-31 (avoids edge cases with very old/future dates)
  const isoTimestampArb = fc
    .date({
      min: new Date('2000-01-01T00:00:00Z'),
      max: new Date('2099-12-31T23:59:59Z'),
    })
    .map((d) => d.toISOString());

  it('always produces exactly 16 characters in DD.MM.YYYY HH:mm format', () => {
    fc.assert(
      fc.property(isoTimestampArb, (iso) => {
        const result = formatDateTime(iso);
        expect(result).toHaveLength(16);
        expect(result).toMatch(/^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$/);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('day is between 01-31, month between 01-12, hour 00-23, minute 00-59', () => {
    fc.assert(
      fc.property(isoTimestampArb, (iso) => {
        const result = formatDateTime(iso);
        const [datePart, timePart] = result.split(' ');
        const [day, month, year] = datePart.split('.').map(Number);
        const [hour, minute] = timePart.split(':').map(Number);

        expect(day).toBeGreaterThanOrEqual(1);
        expect(day).toBeLessThanOrEqual(31);
        expect(month).toBeGreaterThanOrEqual(1);
        expect(month).toBeLessThanOrEqual(12);
        expect(year).toBeGreaterThanOrEqual(2000);
        expect(year).toBeLessThanOrEqual(2100); // day rollover at year boundary
        expect(hour).toBeGreaterThanOrEqual(0);
        expect(hour).toBeLessThanOrEqual(23);
        expect(minute).toBeGreaterThanOrEqual(0);
        expect(minute).toBeLessThanOrEqual(59);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('is deterministic: same input always produces same output', () => {
    fc.assert(
      fc.property(isoTimestampArb, (iso) => {
        const first = formatDateTime(iso);
        const second = formatDateTime(iso);
        expect(first).toBe(second);
      }),
      { numRuns: 200, seed: 42 }
    );
  });
});
