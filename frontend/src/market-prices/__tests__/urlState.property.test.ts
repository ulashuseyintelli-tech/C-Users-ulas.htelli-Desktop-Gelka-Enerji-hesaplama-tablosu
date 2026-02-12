// =============================================================================
// Property-Based Tests: URL State Round-Trip
// Feature: ptf-admin-frontend, Property 1: URL State Round-Trip
// =============================================================================
// **Validates: Requirements 2.1, 2.2, 2.3, 2.4**

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { parseUrlParams, serializeUrlParams } from '../utils';
import { DEFAULT_LIST_PARAMS } from '../constants';
import type { ListParams } from '../types';

// ---------------------------------------------------------------------------
// Smart Generators — constrained to the valid input space
// ---------------------------------------------------------------------------

/** Generates a valid page number (≥ 1, integer) */
const pageArb = fc.integer({ min: 1, max: 10000 });

/** Generates a valid page_size (≥ 1, integer) */
const pageSizeArb = fc.integer({ min: 1, max: 1000 });

/** Generates a valid sort_by column name (non-empty, no special URL chars) */
const sortByArb = fc.constantFrom(
  'period',
  'ptf_tl_per_mwh',
  'status',
  'updated_at',
  'captured_at'
);

/** Generates a valid sort_order */
const sortOrderArb = fc.constantFrom('asc' as const, 'desc' as const);

/** Generates a valid status filter value (only provisional/final for URL) */
const statusArb = fc.constantFrom('provisional' as const, 'final' as const);

/** Generates a valid YYYY-MM period string */
const periodArb = fc
  .tuple(fc.integer({ min: 2000, max: 2099 }), fc.integer({ min: 1, max: 12 }))
  .map(([y, m]) => `${y}-${String(m).padStart(2, '0')}`);

/**
 * Generates a Partial<ListParams> with only non-default values.
 * This mirrors what serializeUrlParams actually writes to the URL —
 * default values are omitted, so round-trip only works for non-defaults.
 */
const nonDefaultListParamsArb = fc.record(
  {
    page: pageArb.filter((p) => p !== DEFAULT_LIST_PARAMS.page),
    page_size: pageSizeArb.filter((ps) => ps !== DEFAULT_LIST_PARAMS.page_size),
    sort_by: sortByArb.filter((sb) => sb !== DEFAULT_LIST_PARAMS.sort_by),
    sort_order: sortOrderArb.filter((so) => so !== DEFAULT_LIST_PARAMS.sort_order),
    status: statusArb,
    from_period: periodArb,
    to_period: periodArb,
  },
  { requiredKeys: [] }
);

// ---------------------------------------------------------------------------
// Property 1: URL State Round-Trip
// ---------------------------------------------------------------------------

describe('Property 1: URL State Round-Trip', () => {
  it('serialize then parse produces equivalent state (round-trip)', () => {
    fc.assert(
      fc.property(nonDefaultListParamsArb, (params) => {
        const serialized = serializeUrlParams(params);
        const parsed = parseUrlParams(serialized ? `?${serialized}` : '');

        // Each key present in params must survive the round-trip
        if (params.page !== undefined) expect(parsed.page).toBe(params.page);
        if (params.page_size !== undefined) expect(parsed.page_size).toBe(params.page_size);
        if (params.sort_by !== undefined) expect(parsed.sort_by).toBe(params.sort_by);
        if (params.sort_order !== undefined) expect(parsed.sort_order).toBe(params.sort_order);
        if (params.status !== undefined) expect(parsed.status).toBe(params.status);
        if (params.from_period !== undefined) expect(parsed.from_period).toBe(params.from_period);
        if (params.to_period !== undefined) expect(parsed.to_period).toBe(params.to_period);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('encode is idempotent: serialize(parse(serialize(state))) === serialize(state)', () => {
    fc.assert(
      fc.property(nonDefaultListParamsArb, (params) => {
        const first = serializeUrlParams(params);
        const parsed = parseUrlParams(first ? `?${first}` : '');
        const second = serializeUrlParams(parsed);
        expect(second).toBe(first);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('param order does not affect parse result', () => {
    fc.assert(
      fc.property(nonDefaultListParamsArb, (params) => {
        const serialized = serializeUrlParams(params);
        if (!serialized) return; // empty — nothing to reorder

        // Reverse the param order
        const reversed = serialized.split('&').reverse().join('&');
        const parsedOriginal = parseUrlParams(`?${serialized}`);
        const parsedReversed = parseUrlParams(`?${reversed}`);

        expect(parsedReversed).toEqual(parsedOriginal);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('unknown params are silently ignored by parse', () => {
    fc.assert(
      fc.property(
        nonDefaultListParamsArb,
        fc.string({ minLength: 1, maxLength: 20 }).filter(
          (s) =>
            /^[a-z_]+$/.test(s) &&
            !['page', 'page_size', 'sort_by', 'sort_order', 'status', 'from_period', 'to_period'].includes(s)
        ),
        fc.string({ minLength: 1, maxLength: 50 }),
        (params, unknownKey, unknownValue) => {
          const serialized = serializeUrlParams(params);
          const withNoise = serialized
            ? `${serialized}&${unknownKey}=${encodeURIComponent(unknownValue)}`
            : `${unknownKey}=${encodeURIComponent(unknownValue)}`;

          const parsedClean = parseUrlParams(serialized ? `?${serialized}` : '');
          const parsedNoisy = parseUrlParams(`?${withNoise}`);

          expect(parsedNoisy).toEqual(parsedClean);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('empty/default values are omitted from serialization', () => {
    fc.assert(
      fc.property(
        fc.record(
          {
            page: fc.constant(DEFAULT_LIST_PARAMS.page),
            page_size: fc.constant(DEFAULT_LIST_PARAMS.page_size),
            sort_by: fc.constant(DEFAULT_LIST_PARAMS.sort_by),
            sort_order: fc.constant(DEFAULT_LIST_PARAMS.sort_order),
            from_period: fc.constant(''),
            to_period: fc.constant(''),
          },
          { requiredKeys: [] }
        ),
        (defaults) => {
          const serialized = serializeUrlParams(defaults);
          expect(serialized).toBe('');
        }
      ),
      { numRuns: 50, seed: 42 }
    );
  });

  it('reserved URL characters in period values survive round-trip', () => {
    // Periods are YYYY-MM format with hyphen — test that the hyphen
    // and URLSearchParams encoding don't corrupt the value
    fc.assert(
      fc.property(periodArb, periodArb, (from, to) => {
        const params: Partial<ListParams> = { from_period: from, to_period: to };
        const serialized = serializeUrlParams(params);
        const parsed = parseUrlParams(`?${serialized}`);
        expect(parsed.from_period).toBe(from);
        expect(parsed.to_period).toBe(to);
      }),
      { numRuns: 200, seed: 42 }
    );
  });
});
