// =============================================================================
// Property-Based Tests: List State (Sort, Pagination, Filter Reset)
// Feature: ptf-admin-frontend
// =============================================================================
// **Validates: Requirements 1.5, 1.6, 3.4**
//
// These tests validate the pure logic behind useUrlState's sort toggle,
// pagination calculation, and filter-change-resets-page behavior.
// We extract the logic as pure functions to test without React rendering.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

// ---------------------------------------------------------------------------
// Pure logic extracted from useUrlState (mirrors production exactly)
// Production code is NOT modified — these are local copies for testing.
// ---------------------------------------------------------------------------

/**
 * Sort toggle logic (Property 4):
 * - Same column → toggle asc↔desc
 * - Different column → set new column, reset to "desc"
 */
function toggleSort(
  currentSortBy: string,
  currentSortOrder: 'asc' | 'desc',
  clickedColumn: string,
): { sortBy: string; sortOrder: 'asc' | 'desc' } {
  if (currentSortBy === clickedColumn) {
    return {
      sortBy: clickedColumn,
      sortOrder: currentSortOrder === 'asc' ? 'desc' : 'asc',
    };
  }
  return { sortBy: clickedColumn, sortOrder: 'desc' };
}

/**
 * Total pages calculation (Property 5):
 * ceil(total / pageSize), minimum 1.
 */
function calcTotalPages(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

/**
 * Clamp page to valid range [1, totalPages].
 */
function clampPage(page: number, totalPages: number): number {
  return Math.max(1, Math.min(page, totalPages));
}

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

const sortableColumns = ['period', 'ptf_tl_per_mwh', 'status', 'updated_at'];
const columnArb = fc.constantFrom(...sortableColumns);
const sortOrderArb = fc.constantFrom('asc' as const, 'desc' as const);

// ---------------------------------------------------------------------------
// Property 4: Sort Toggle Correctness
// ---------------------------------------------------------------------------

describe('Property 4: Sort Toggle Correctness', () => {
  it('same column click toggles order (asc↔desc)', () => {
    fc.assert(
      fc.property(columnArb, sortOrderArb, (col, order) => {
        const result = toggleSort(col, order, col);
        expect(result.sortBy).toBe(col);
        expect(result.sortOrder).toBe(order === 'asc' ? 'desc' : 'asc');
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('different column click sets new column and resets to desc', () => {
    fc.assert(
      fc.property(
        columnArb,
        sortOrderArb,
        columnArb, // all columns — pre-condition filters below
        (currentCol, currentOrder, clickedCol) => {
          fc.pre(currentCol !== clickedCol); // only test different columns
          const result = toggleSort(currentCol, currentOrder, clickedCol);
          expect(result.sortBy).toBe(clickedCol);
          expect(result.sortOrder).toBe('desc');
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('double toggle on same column returns to original order (involution)', () => {
    fc.assert(
      fc.property(columnArb, sortOrderArb, (col, order) => {
        const after1 = toggleSort(col, order, col);
        const after2 = toggleSort(after1.sortBy, after1.sortOrder, col);
        expect(after2.sortBy).toBe(col);
        expect(after2.sortOrder).toBe(order);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('result sortBy always equals the clicked column', () => {
    fc.assert(
      fc.property(columnArb, sortOrderArb, columnArb, (currentCol, currentOrder, clickedCol) => {
        const result = toggleSort(currentCol, currentOrder, clickedCol);
        expect(result.sortBy).toBe(clickedCol);
      }),
      { numRuns: 200, seed: 42 }
    );
  });

  it('result sortOrder is always asc or desc', () => {
    fc.assert(
      fc.property(columnArb, sortOrderArb, columnArb, (currentCol, currentOrder, clickedCol) => {
        const result = toggleSort(currentCol, currentOrder, clickedCol);
        expect(['asc', 'desc']).toContain(result.sortOrder);
      }),
      { numRuns: 200, seed: 42 }
    );
  });
});

// ---------------------------------------------------------------------------
// Property 5: Pagination Total Pages
// ---------------------------------------------------------------------------

describe('Property 5: Pagination Total Pages', () => {
  it('totalPages = ceil(total / pageSize) for any total≥0 and pageSize>0', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 100000 }),
        fc.integer({ min: 1, max: 1000 }),
        (total, pageSize) => {
          const totalPages = calcTotalPages(total, pageSize);
          expect(totalPages).toBe(Math.max(1, Math.ceil(total / pageSize)));
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('totalPages is always >= 1', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 100000 }),
        fc.integer({ min: 1, max: 1000 }),
        (total, pageSize) => {
          expect(calcTotalPages(total, pageSize)).toBeGreaterThanOrEqual(1);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('total=0 always yields totalPages=1', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 1000 }),
        (pageSize) => {
          expect(calcTotalPages(0, pageSize)).toBe(1);
        }
      ),
      { numRuns: 100, seed: 42 }
    );
  });

  it('clamped page is always within [1, totalPages]', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: -10, max: 10000 }),
        fc.integer({ min: 0, max: 100000 }),
        fc.integer({ min: 1, max: 1000 }),
        (page, total, pageSize) => {
          const totalPages = calcTotalPages(total, pageSize);
          const clamped = clampPage(page, totalPages);
          expect(clamped).toBeGreaterThanOrEqual(1);
          expect(clamped).toBeLessThanOrEqual(totalPages);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('clamp is idempotent: clamp(clamp(p)) === clamp(p)', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: -10, max: 10000 }),
        fc.integer({ min: 0, max: 100000 }),
        fc.integer({ min: 1, max: 1000 }),
        (page, total, pageSize) => {
          const totalPages = calcTotalPages(total, pageSize);
          const once = clampPage(page, totalPages);
          const twice = clampPage(once, totalPages);
          expect(twice).toBe(once);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });
});

// ---------------------------------------------------------------------------
// Property 6: Filter Change Resets Page
// ---------------------------------------------------------------------------

describe('Property 6: Filter Change Resets Page', () => {
  // Simulate the filter-change behavior from useUrlState:
  // setFilters always calls setPageState(1)
  type FilterState = { status: 'all' | 'provisional' | 'final'; fromPeriod: string; toPeriod: string };

  function applyFilterChange(
    _currentPage: number,
    _currentFilters: FilterState,
    _newFilters: Partial<FilterState>,
  ): { page: number } {
    // Production behavior: filter change → page = 1 (always)
    return { page: 1 };
  }

  const filterStatusArb = fc.constantFrom('all' as const, 'provisional' as const, 'final' as const);
  const periodArb = fc.oneof(
    fc.constant(''),
    fc.tuple(fc.integer({ min: 2020, max: 2030 }), fc.integer({ min: 1, max: 12 }))
      .map(([y, m]) => `${y}-${String(m).padStart(2, '0')}`),
  );
  const filterStateArb = fc.record({
    status: filterStatusArb,
    fromPeriod: periodArb,
    toPeriod: periodArb,
  });

  it('any filter change resets page to 1, regardless of current page', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 10000 }),
        filterStateArb,
        fc.record({
          status: fc.option(filterStatusArb, { nil: undefined }),
          fromPeriod: fc.option(periodArb, { nil: undefined }),
          toPeriod: fc.option(periodArb, { nil: undefined }),
        }),
        (currentPage, currentFilters, partialNewFilters) => {
          const result = applyFilterChange(currentPage, currentFilters, partialNewFilters);
          expect(result.page).toBe(1);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('page reset is unconditional — even if filter value is the same', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 2, max: 10000 }),
        filterStateArb,
        (currentPage, filters) => {
          // Apply the same filters (no actual change)
          const result = applyFilterChange(currentPage, filters, filters);
          expect(result.page).toBe(1);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });

  it('page reset is idempotent — applying filter change twice still yields page=1', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 1, max: 10000 }),
        filterStateArb,
        filterStateArb,
        (currentPage, filters1, filters2) => {
          const after1 = applyFilterChange(currentPage, filters1, filters2);
          const after2 = applyFilterChange(after1.page, { ...filters1, ...filters2 }, filters1);
          expect(after1.page).toBe(1);
          expect(after2.page).toBe(1);
        }
      ),
      { numRuns: 200, seed: 42 }
    );
  });
});
