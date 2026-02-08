import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useUrlState } from '../hooks/useUrlState';

// =============================================================================
// Helpers
// =============================================================================

/** Set window.location.search and pathname for testing. */
function setUrl(search: string) {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: { ...window.location, search, pathname: '/' },
  });
}

// =============================================================================
// Setup / Teardown
// =============================================================================

describe('useUrlState', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let replaceStateSpy: any;

  beforeEach(() => {
    setUrl('');
    replaceStateSpy = vi.spyOn(window.history, 'replaceState').mockImplementation(() => {});
  });

  afterEach(() => {
    replaceStateSpy.mockRestore();
    vi.restoreAllMocks();
  });

  // ===========================================================================
  // Initial state from URL
  // ===========================================================================

  describe('initial state parsing', () => {
    it('returns defaults when URL has no query params', () => {
      setUrl('');
      const { result } = renderHook(() => useUrlState());

      expect(result.current.filters).toEqual({
        status: 'all',
        fromPeriod: '',
        toPeriod: '',
      });
      expect(result.current.pagination).toEqual({ page: 1, pageSize: 20 });
      expect(result.current.sortBy).toBe('period');
      expect(result.current.sortOrder).toBe('desc');
    });

    it('parses page and page_size from URL', () => {
      setUrl('?page=3&page_size=50');
      const { result } = renderHook(() => useUrlState());

      expect(result.current.pagination).toEqual({ page: 3, pageSize: 50 });
    });

    it('parses sort params from URL', () => {
      setUrl('?sort_by=ptf_tl_per_mwh&sort_order=asc');
      const { result } = renderHook(() => useUrlState());

      expect(result.current.sortBy).toBe('ptf_tl_per_mwh');
      expect(result.current.sortOrder).toBe('asc');
    });

    it('parses filter params from URL', () => {
      setUrl('?status=final&from_period=2024-01&to_period=2025-06');
      const { result } = renderHook(() => useUrlState());

      expect(result.current.filters).toEqual({
        status: 'final',
        fromPeriod: '2024-01',
        toPeriod: '2025-06',
      });
    });

    it('parses all params together from URL', () => {
      setUrl('?page=2&page_size=10&sort_by=status&sort_order=asc&status=provisional&from_period=2024-06');
      const { result } = renderHook(() => useUrlState());

      expect(result.current.pagination).toEqual({ page: 2, pageSize: 10 });
      expect(result.current.sortBy).toBe('status');
      expect(result.current.sortOrder).toBe('asc');
      expect(result.current.filters.status).toBe('provisional');
      expect(result.current.filters.fromPeriod).toBe('2024-06');
    });
  });

  // ===========================================================================
  // replaceState on state change
  // ===========================================================================

  describe('URL update via replaceState', () => {
    it('calls replaceState when page changes', () => {
      setUrl('');
      const { result } = renderHook(() => useUrlState());

      act(() => {
        result.current.setPage(3);
      });

      expect(replaceStateSpy).toHaveBeenCalledWith(null, '', '/?page=3');
    });

    it('calls replaceState when filters change', () => {
      setUrl('');
      const { result } = renderHook(() => useUrlState());

      act(() => {
        result.current.setFilters({ status: 'final' });
      });

      expect(replaceStateSpy).toHaveBeenCalledWith(null, '', '/?status=final');
    });

    it('produces clean URL when all values are defaults', () => {
      setUrl('?page=3');
      const { result } = renderHook(() => useUrlState());

      act(() => {
        result.current.clearFilters();
      });

      expect(replaceStateSpy).toHaveBeenCalledWith(null, '', '/');
    });

    it('omits default values from URL', () => {
      setUrl('?page=5');
      const { result } = renderHook(() => useUrlState());

      expect(result.current.pagination.page).toBe(5);

      // Set page back to default
      act(() => {
        result.current.setPage(1);
      });

      // replaceState should be called with clean URL (no query params)
      expect(replaceStateSpy).toHaveBeenCalledWith(null, '', '/');
    });
  });

  // ===========================================================================
  // setFilters — resets page to 1 (Property 6)
  // ===========================================================================

  describe('setFilters resets page to 1', () => {
    it('resets page when status filter changes', () => {
      setUrl('?page=5');
      const { result } = renderHook(() => useUrlState());

      expect(result.current.pagination.page).toBe(5);

      act(() => {
        result.current.setFilters({ status: 'provisional' });
      });

      expect(result.current.pagination.page).toBe(1);
    });

    it('resets page when fromPeriod filter changes', () => {
      setUrl('?page=3');
      const { result } = renderHook(() => useUrlState());

      act(() => {
        result.current.setFilters({ fromPeriod: '2024-01' });
      });

      expect(result.current.pagination.page).toBe(1);
    });

    it('merges partial filter updates', () => {
      setUrl('?status=final&from_period=2024-01');
      const { result } = renderHook(() => useUrlState());

      act(() => {
        result.current.setFilters({ toPeriod: '2025-06' });
      });

      expect(result.current.filters).toEqual({
        status: 'final',
        fromPeriod: '2024-01',
        toPeriod: '2025-06',
      });
    });
  });

  // ===========================================================================
  // setPageSize — resets page to 1
  // ===========================================================================

  describe('setPageSize', () => {
    it('updates page size and resets page to 1', () => {
      setUrl('?page=3&page_size=20');
      const { result } = renderHook(() => useUrlState());

      act(() => {
        result.current.setPageSize(50);
      });

      expect(result.current.pagination.pageSize).toBe(50);
      expect(result.current.pagination.page).toBe(1);
    });
  });

  // ===========================================================================
  // setSort — toggle logic (Property 4)
  // ===========================================================================

  describe('setSort toggle logic', () => {
    it('toggles sort order when same column is clicked', () => {
      setUrl('');
      const { result } = renderHook(() => useUrlState());

      // Default: sortBy=period, sortOrder=desc
      expect(result.current.sortBy).toBe('period');
      expect(result.current.sortOrder).toBe('desc');

      act(() => {
        result.current.setSort('period');
      });

      expect(result.current.sortBy).toBe('period');
      expect(result.current.sortOrder).toBe('asc');
    });

    it('toggles back to desc on second click of same column', () => {
      setUrl('?sort_order=asc');
      const { result } = renderHook(() => useUrlState());

      expect(result.current.sortOrder).toBe('asc');

      act(() => {
        result.current.setSort('period');
      });

      expect(result.current.sortOrder).toBe('desc');
    });

    it('resets sort order to desc when different column is clicked', () => {
      setUrl('?sort_by=period&sort_order=asc');
      const { result } = renderHook(() => useUrlState());

      act(() => {
        result.current.setSort('ptf_tl_per_mwh');
      });

      expect(result.current.sortBy).toBe('ptf_tl_per_mwh');
      expect(result.current.sortOrder).toBe('desc');
    });

    it('sets new column and desc when switching from desc to different column', () => {
      setUrl('');
      const { result } = renderHook(() => useUrlState());

      // Default: period, desc
      act(() => {
        result.current.setSort('status');
      });

      expect(result.current.sortBy).toBe('status');
      expect(result.current.sortOrder).toBe('desc');
    });
  });

  // ===========================================================================
  // clearFilters — resets to defaults
  // ===========================================================================

  describe('clearFilters', () => {
    it('resets all state to defaults', () => {
      setUrl('?page=5&page_size=50&sort_by=status&sort_order=asc&status=final&from_period=2024-01&to_period=2025-06');
      const { result } = renderHook(() => useUrlState());

      // Verify non-default state
      expect(result.current.pagination.page).toBe(5);
      expect(result.current.filters.status).toBe('final');

      act(() => {
        result.current.clearFilters();
      });

      expect(result.current.filters).toEqual({
        status: 'all',
        fromPeriod: '',
        toPeriod: '',
      });
      expect(result.current.pagination).toEqual({ page: 1, pageSize: 20 });
      expect(result.current.sortBy).toBe('period');
      expect(result.current.sortOrder).toBe('desc');
    });
  });

  // ===========================================================================
  // popstate listener — back/forward support
  // ===========================================================================

  describe('popstate listener', () => {
    it('updates state when popstate event fires', () => {
      setUrl('');
      const { result } = renderHook(() => useUrlState());

      // Simulate browser back/forward: change URL then fire popstate
      setUrl('?page=4&status=provisional&sort_by=updated_at&sort_order=asc');

      act(() => {
        window.dispatchEvent(new PopStateEvent('popstate'));
      });

      expect(result.current.pagination.page).toBe(4);
      expect(result.current.filters.status).toBe('provisional');
      expect(result.current.sortBy).toBe('updated_at');
      expect(result.current.sortOrder).toBe('asc');
    });

    it('resets to defaults when popstate fires with empty URL', () => {
      setUrl('?page=3&status=final');
      const { result } = renderHook(() => useUrlState());

      expect(result.current.pagination.page).toBe(3);

      // Simulate navigating back to clean URL
      setUrl('');

      act(() => {
        window.dispatchEvent(new PopStateEvent('popstate'));
      });

      expect(result.current.pagination.page).toBe(1);
      expect(result.current.filters.status).toBe('all');
      expect(result.current.sortBy).toBe('period');
      expect(result.current.sortOrder).toBe('desc');
    });

    it('cleans up popstate listener on unmount', () => {
      setUrl('');
      const removeEventListenerSpy = vi.spyOn(window, 'removeEventListener');

      const { unmount } = renderHook(() => useUrlState());
      unmount();

      expect(removeEventListenerSpy).toHaveBeenCalledWith('popstate', expect.any(Function));
      removeEventListenerSpy.mockRestore();
    });
  });
});
