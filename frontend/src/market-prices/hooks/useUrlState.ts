// =============================================================================
// PTF Admin Frontend — useUrlState Hook
// =============================================================================
// Synchronizes filter, pagination, and sort state with the browser URL.
//
// Key behaviors:
// - Parses window.location.search on mount to derive initial state
// - Updates URL via window.history.replaceState on state change
//   (replaceState is intentional — see Design Decision #6: avoids flooding
//    browser history with every filter tweak; "back" goes to previous page,
//    not previous filter state)
// - Listens to popstate for browser back/forward support
// - clearFilters resets everything to defaults
// - Filter changes always reset page to 1 (Design Decision #2, Property 6)
// =============================================================================

import { useState, useEffect, useCallback, useRef } from 'react';
import type { FilterState } from '../types';
import { DEFAULT_LIST_PARAMS } from '../constants';
import { parseUrlParams, serializeUrlParams } from '../utils';

// ---------------------------------------------------------------------------
// Default filter state (no filters applied)
// ---------------------------------------------------------------------------

const DEFAULT_FILTERS: FilterState = {
  status: 'all',
  fromPeriod: '',
  toPeriod: '',
};

// ---------------------------------------------------------------------------
// Helpers: convert between URL ListParams and hook state
// ---------------------------------------------------------------------------

function filtersFromUrlParams(parsed: ReturnType<typeof parseUrlParams>): FilterState {
  return {
    status: parsed.status ?? 'all',
    fromPeriod: parsed.from_period ?? '',
    toPeriod: parsed.to_period ?? '',
  };
}

function buildUrlParams(
  filters: FilterState,
  page: number,
  pageSize: number,
  sortBy: string,
  sortOrder: 'asc' | 'desc',
) {
  return {
    page,
    page_size: pageSize,
    sort_by: sortBy,
    sort_order: sortOrder,
    ...(filters.status !== 'all' ? { status: filters.status as 'provisional' | 'final' } : {}),
    ...(filters.fromPeriod ? { from_period: filters.fromPeriod } : {}),
    ...(filters.toPeriod ? { to_period: filters.toPeriod } : {}),
  };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useUrlState() {
  // Parse initial state from URL on first render
  const initialParsed = useRef(parseUrlParams(window.location.search));

  const [filters, setFiltersState] = useState<FilterState>(
    () => filtersFromUrlParams(initialParsed.current),
  );
  const [page, setPageState] = useState<number>(
    () => initialParsed.current.page ?? DEFAULT_LIST_PARAMS.page,
  );
  const [pageSize, setPageSizeState] = useState<number>(
    () => initialParsed.current.page_size ?? DEFAULT_LIST_PARAMS.page_size,
  );
  const [sortBy, setSortByState] = useState<string>(
    () => initialParsed.current.sort_by ?? DEFAULT_LIST_PARAMS.sort_by,
  );
  const [sortOrder, setSortOrderState] = useState<'asc' | 'desc'>(
    () => initialParsed.current.sort_order ?? DEFAULT_LIST_PARAMS.sort_order,
  );

  // Track whether we're handling a popstate to avoid writing back to URL
  const isPopstateRef = useRef(false);

  // -------------------------------------------------------------------------
  // Sync state → URL (replaceState, NOT pushState — intentional)
  // -------------------------------------------------------------------------

  useEffect(() => {
    // Skip URL update when we're reacting to a popstate event
    if (isPopstateRef.current) {
      isPopstateRef.current = false;
      return;
    }

    const qs = serializeUrlParams(buildUrlParams(filters, page, pageSize, sortBy, sortOrder));
    const newUrl = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;

    // Only update if URL actually changed
    if (newUrl !== `${window.location.pathname}${window.location.search}`) {
      window.history.replaceState(null, '', newUrl);
    }
  }, [filters, page, pageSize, sortBy, sortOrder]);

  // -------------------------------------------------------------------------
  // popstate listener — back/forward support
  // -------------------------------------------------------------------------

  useEffect(() => {
    const handlePopstate = () => {
      isPopstateRef.current = true;
      const parsed = parseUrlParams(window.location.search);

      setFiltersState(filtersFromUrlParams(parsed));
      setPageState(parsed.page ?? DEFAULT_LIST_PARAMS.page);
      setPageSizeState(parsed.page_size ?? DEFAULT_LIST_PARAMS.page_size);
      setSortByState(parsed.sort_by ?? DEFAULT_LIST_PARAMS.sort_by);
      setSortOrderState(parsed.sort_order ?? DEFAULT_LIST_PARAMS.sort_order);
    };

    window.addEventListener('popstate', handlePopstate);
    return () => window.removeEventListener('popstate', handlePopstate);
  }, []);

  // -------------------------------------------------------------------------
  // Public setters
  // -------------------------------------------------------------------------

  /** Merge partial filter changes; always resets page to 1 (Property 6). */
  const setFilters = useCallback((f: Partial<FilterState>) => {
    setFiltersState((prev) => ({ ...prev, ...f }));
    setPageState(1); // Design Decision #2: filter change → page reset
  }, []);

  const setPage = useCallback((p: number) => {
    setPageState(p);
  }, []);

  const setPageSize = useCallback((size: number) => {
    setPageSizeState(size);
    setPageState(1); // Changing page size resets to first page
  }, []);

  /**
   * Sort toggle logic (Property 4):
   * - Same column clicked → toggle asc↔desc
   * - Different column clicked → set new column, reset to "desc"
   */
  const setSort = useCallback((column: string) => {
    setSortByState((prevSortBy) => {
      if (prevSortBy === column) {
        // Same column — toggle order
        setSortOrderState((prev) => (prev === 'asc' ? 'desc' : 'asc'));
      } else {
        // Different column — reset to desc
        setSortOrderState('desc');
      }
      return column;
    });
  }, []);

  /** Reset all filters, pagination, and sort to defaults. */
  const clearFilters = useCallback(() => {
    setFiltersState(DEFAULT_FILTERS);
    setPageState(DEFAULT_LIST_PARAMS.page);
    setPageSizeState(DEFAULT_LIST_PARAMS.page_size);
    setSortByState(DEFAULT_LIST_PARAMS.sort_by);
    setSortOrderState(DEFAULT_LIST_PARAMS.sort_order);
  }, []);

  return {
    filters,
    pagination: { page, pageSize },
    sortBy,
    sortOrder,
    setFilters,
    setPage,
    setPageSize,
    setSort,
    clearFilters,
  };
}
