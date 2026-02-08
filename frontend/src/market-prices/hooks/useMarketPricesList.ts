// =============================================================================
// PTF Admin Frontend — useMarketPricesList Hook
// =============================================================================
// Fetches paginated market price records from the API.
//
// Key behaviors:
// - useEffect with params dependency → fetch on params change
// - AbortController: new controller per fetch, aborts previous on new fetch
//   or unmount
// - AbortError / axios cancel → silently ignored (Design Decision #3)
// - Loading state: true while fetching, false on success or real error
// - Error state: set on real errors, cleared on successful fetch
// - Refetch: manually re-trigger fetch with current params
// - No optimistic UI updates (Requirement 11.1)
// =============================================================================

import { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { listMarketPrices } from '../marketPricesApi';
import type {
  ListParams,
  MarketPriceRecord,
  PaginationState,
  ApiErrorResponse,
} from '../types';

export function useMarketPricesList(params: ListParams) {
  const [data, setData] = useState<MarketPriceRecord[]>([]);
  const [pagination, setPagination] = useState<PaginationState>({
    page: params.page,
    pageSize: params.page_size,
    total: 0,
  });
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<ApiErrorResponse | null>(null);

  // Ref to hold the current AbortController so we can abort on new fetch / unmount
  const abortControllerRef = useRef<AbortController | null>(null);

  // Counter to force re-fetch when refetch() is called
  const [fetchTrigger, setFetchTrigger] = useState(0);

  useEffect(() => {
    // Abort any previous in-flight request
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    let cancelled = false;

    const fetchData = async () => {
      setLoading(true);

      try {
        const response = await listMarketPrices(params, controller.signal);

        if (!cancelled) {
          setData(response.items);
          setPagination({
            page: response.page,
            pageSize: response.page_size,
            total: response.total,
          });
          setError(null);
        }
      } catch (err: unknown) {
        // Silently ignore abort / cancel — Design Decision #3
        if (err instanceof DOMException && err.name === 'AbortError') {
          return;
        }
        if (axios.isCancel(err)) {
          return;
        }

        if (!cancelled) {
          // Attempt to extract ApiErrorResponse from axios error
          if (axios.isAxiosError(err) && err.response?.data) {
            setError(err.response.data as ApiErrorResponse);
          } else {
            setError({
              status: 'error',
              error_code: 'NETWORK_ERROR',
              message: 'Bağlantı hatası',
            });
          }
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    fetchData();

    // Cleanup: abort on unmount or before next effect run
    return () => {
      cancelled = true;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    params.page,
    params.page_size,
    params.sort_by,
    params.sort_order,
    params.price_type,
    params.status,
    params.from_period,
    params.to_period,
    fetchTrigger,
  ]);

  /** Manually re-trigger a fetch with the current params. */
  const refetch = useCallback(() => {
    setFetchTrigger((prev) => prev + 1);
  }, []);

  return { data, pagination, loading, error, refetch };
}
