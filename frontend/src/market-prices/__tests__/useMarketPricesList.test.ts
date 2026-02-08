import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import type { ListParams, MarketPricesListResponse } from '../types';

// ---------------------------------------------------------------------------
// Mock the API module
// ---------------------------------------------------------------------------

const mockListMarketPrices = vi.fn<
  [ListParams, AbortSignal?],
  Promise<MarketPricesListResponse>
>();

vi.mock('../marketPricesApi', () => ({
  listMarketPrices: (...args: [ListParams, AbortSignal?]) =>
    mockListMarketPrices(...args),
}));

// Import hook AFTER mock setup
import { useMarketPricesList } from '../hooks/useMarketPricesList';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const DEFAULT_PARAMS: ListParams = {
  page: 1,
  page_size: 20,
  sort_by: 'period',
  sort_order: 'desc',
  price_type: 'PTF',
};

function makeResponse(
  overrides: Partial<MarketPricesListResponse> = {},
): MarketPricesListResponse {
  return {
    status: 'ok',
    total: 1,
    page: 1,
    page_size: 20,
    items: [
      {
        period: '2025-01',
        ptf_tl_per_mwh: 2508.8,
        status: 'provisional',
        price_type: 'PTF',
        captured_at: '2025-01-15T10:00:00Z',
        updated_at: '2025-01-15T10:00:00Z',
        updated_by: 'admin',
        source: 'epias_manual',
        source_note: '',
        change_reason: '',
        is_locked: false,
        yekdem_tl_per_mwh: 0,
      },
    ],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useMarketPricesList', () => {
  beforeEach(() => {
    mockListMarketPrices.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // =========================================================================
  // Initial fetch on mount
  // =========================================================================

  describe('initial fetch', () => {
    it('fetches data on mount and sets loading → data', async () => {
      const response = makeResponse({ total: 42 });
      mockListMarketPrices.mockResolvedValueOnce(response);

      const { result } = renderHook(() => useMarketPricesList(DEFAULT_PARAMS));

      // Should start loading
      expect(result.current.loading).toBe(true);

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(result.current.data).toEqual(response.items);
      expect(result.current.pagination).toEqual({
        page: 1,
        pageSize: 20,
        total: 42,
      });
      expect(result.current.error).toBeNull();
    });

    it('passes params and AbortSignal to listMarketPrices', async () => {
      mockListMarketPrices.mockResolvedValueOnce(makeResponse());

      const params: ListParams = {
        ...DEFAULT_PARAMS,
        page: 3,
        status: 'final',
        from_period: '2024-01',
      };

      renderHook(() => useMarketPricesList(params));

      await waitFor(() => {
        expect(mockListMarketPrices).toHaveBeenCalledTimes(1);
      });

      const [calledParams, calledSignal] = mockListMarketPrices.mock.calls[0];
      expect(calledParams).toEqual(params);
      expect(calledSignal).toBeInstanceOf(AbortSignal);
    });
  });

  // =========================================================================
  // Re-fetch on params change
  // =========================================================================

  describe('re-fetch on params change', () => {
    it('fetches again when params change', async () => {
      const response1 = makeResponse({ total: 10 });
      const response2 = makeResponse({ total: 25, page: 2 });
      mockListMarketPrices
        .mockResolvedValueOnce(response1)
        .mockResolvedValueOnce(response2);

      const { result, rerender } = renderHook(
        (props: { params: ListParams }) => useMarketPricesList(props.params),
        { initialProps: { params: DEFAULT_PARAMS } },
      );

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(result.current.pagination.total).toBe(10);

      // Change page
      rerender({ params: { ...DEFAULT_PARAMS, page: 2 } });

      await waitFor(() => {
        expect(mockListMarketPrices).toHaveBeenCalledTimes(2);
      });

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(result.current.pagination.total).toBe(25);
    });
  });

  // =========================================================================
  // Error handling
  // =========================================================================

  describe('error handling', () => {
    it('sets error state on API failure', async () => {
      // Create a proper AxiosError-like object that axios.isAxiosError recognises
      const { default: axiosLib } = await import('axios');
      const fakeAxiosError = new axiosLib.AxiosError(
        'Request failed',
        'ERR_BAD_RESPONSE',
        undefined,
        undefined,
        {
          data: {
            status: 'error',
            error_code: 'INTERNAL_ERROR',
            message: 'Something went wrong',
          },
          status: 500,
          statusText: 'Internal Server Error',
          headers: {},
          config: {} as never,
        } as never,
      );

      mockListMarketPrices.mockRejectedValueOnce(fakeAxiosError);

      const { result } = renderHook(() => useMarketPricesList(DEFAULT_PARAMS));

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(result.current.error).toEqual({
        status: 'error',
        error_code: 'INTERNAL_ERROR',
        message: 'Something went wrong',
      });
    });

    it('sets generic network error when no response data', async () => {
      mockListMarketPrices.mockRejectedValueOnce(new Error('Network Error'));

      const { result } = renderHook(() => useMarketPricesList(DEFAULT_PARAMS));

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(result.current.error).toEqual({
        status: 'error',
        error_code: 'NETWORK_ERROR',
        message: 'Bağlantı hatası',
      });
    });

    it('clears error on successful fetch after error', async () => {
      mockListMarketPrices.mockRejectedValueOnce(new Error('fail'));

      const { result, rerender } = renderHook(
        (props: { params: ListParams }) => useMarketPricesList(props.params),
        { initialProps: { params: DEFAULT_PARAMS } },
      );

      await waitFor(() => {
        expect(result.current.error).not.toBeNull();
      });

      // Now succeed
      mockListMarketPrices.mockResolvedValueOnce(makeResponse());
      rerender({ params: { ...DEFAULT_PARAMS, page: 2 } });

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(result.current.error).toBeNull();
    });
  });

  // =========================================================================
  // AbortController — silent cancellation (Design Decision #3)
  // =========================================================================

  describe('AbortController cancellation', () => {
    it('silently ignores AbortError (no error state, no toast)', async () => {
      const abortError = new DOMException('The operation was aborted.', 'AbortError');
      mockListMarketPrices.mockRejectedValueOnce(abortError);

      const { result } = renderHook(() => useMarketPricesList(DEFAULT_PARAMS));

      // Give time for the effect to settle
      await act(async () => {
        await new Promise((r) => setTimeout(r, 50));
      });

      // Error should NOT be set for abort
      expect(result.current.error).toBeNull();
    });

    it('aborts previous request when params change', async () => {
      // First call: never resolves (simulates slow request)
      let firstSignal: AbortSignal | undefined;
      mockListMarketPrices.mockImplementationOnce(
        (_params, signal) => {
          firstSignal = signal;
          return new Promise(() => {}); // never resolves
        },
      );

      // Second call: resolves immediately
      mockListMarketPrices.mockResolvedValueOnce(makeResponse({ total: 99 }));

      const { result, rerender } = renderHook(
        (props: { params: ListParams }) => useMarketPricesList(props.params),
        { initialProps: { params: DEFAULT_PARAMS } },
      );

      // Wait for first call to be made
      await waitFor(() => {
        expect(mockListMarketPrices).toHaveBeenCalledTimes(1);
      });

      // Change params → should abort first request
      rerender({ params: { ...DEFAULT_PARAMS, page: 2 } });

      await waitFor(() => {
        expect(mockListMarketPrices).toHaveBeenCalledTimes(2);
      });

      // First request's signal should be aborted
      expect(firstSignal?.aborted).toBe(true);

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(result.current.pagination.total).toBe(99);
    });

    it('aborts in-flight request on unmount', async () => {
      let capturedSignal: AbortSignal | undefined;
      mockListMarketPrices.mockImplementationOnce(
        (_params, signal) => {
          capturedSignal = signal;
          return new Promise(() => {}); // never resolves
        },
      );

      const { unmount } = renderHook(() => useMarketPricesList(DEFAULT_PARAMS));

      await waitFor(() => {
        expect(mockListMarketPrices).toHaveBeenCalledTimes(1);
      });

      unmount();

      expect(capturedSignal?.aborted).toBe(true);
    });
  });

  // =========================================================================
  // Refetch
  // =========================================================================

  describe('refetch', () => {
    it('re-fetches data when refetch() is called', async () => {
      const response1 = makeResponse({ total: 5 });
      const response2 = makeResponse({ total: 10 });
      mockListMarketPrices
        .mockResolvedValueOnce(response1)
        .mockResolvedValueOnce(response2);

      const { result } = renderHook(() => useMarketPricesList(DEFAULT_PARAMS));

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(result.current.pagination.total).toBe(5);

      // Trigger refetch
      act(() => {
        result.current.refetch();
      });

      await waitFor(() => {
        expect(mockListMarketPrices).toHaveBeenCalledTimes(2);
      });

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(result.current.pagination.total).toBe(10);
    });

    it('refetch uses the same params', async () => {
      mockListMarketPrices
        .mockResolvedValueOnce(makeResponse())
        .mockResolvedValueOnce(makeResponse());

      const params: ListParams = {
        ...DEFAULT_PARAMS,
        status: 'final',
        from_period: '2024-06',
      };

      const { result } = renderHook(() => useMarketPricesList(params));

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      act(() => {
        result.current.refetch();
      });

      await waitFor(() => {
        expect(mockListMarketPrices).toHaveBeenCalledTimes(2);
      });

      // Both calls should use the same params
      expect(mockListMarketPrices.mock.calls[0][0]).toEqual(params);
      expect(mockListMarketPrices.mock.calls[1][0]).toEqual(params);
    });
  });

  // =========================================================================
  // Loading state
  // =========================================================================

  describe('loading state', () => {
    it('loading is true during fetch, false after success', async () => {
      mockListMarketPrices.mockResolvedValueOnce(makeResponse());

      const { result } = renderHook(() => useMarketPricesList(DEFAULT_PARAMS));

      expect(result.current.loading).toBe(true);

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });
    });

    it('loading is true during fetch, false after error', async () => {
      mockListMarketPrices.mockRejectedValueOnce(new Error('fail'));

      const { result } = renderHook(() => useMarketPricesList(DEFAULT_PARAMS));

      expect(result.current.loading).toBe(true);

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });
    });
  });
});
