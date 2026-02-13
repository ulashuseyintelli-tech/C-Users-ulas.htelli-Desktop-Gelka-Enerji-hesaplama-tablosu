// =============================================================================
// Audit History — useAuditHistory Hook
// =============================================================================
// Fetches change history for a specific period+priceType.
// Follows the same pattern as useMarketPricesList:
// - useState + useEffect + AbortController
// - Null period → no fetch
// - AbortError silently ignored
// - Refetch via fetchTrigger counter
//
// Feature: audit-history, Requirements: 4.1, 4.3
// =============================================================================

import { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { fetchHistory } from '../marketPricesApi';
import { trackEvent } from '../telemetry';
import type { AuditHistoryEntry, ApiErrorResponse } from '../types';

export function useAuditHistory(period: string | null, priceType: string = 'PTF') {
  const [history, setHistory] = useState<AuditHistoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiErrorResponse | null>(null);

  const abortControllerRef = useRef<AbortController | null>(null);
  const [fetchTrigger, setFetchTrigger] = useState(0);

  useEffect(() => {
    // No fetch when period is null (modal closed)
    if (!period) {
      setHistory([]);
      setLoading(false);
      setError(null);
      return;
    }

    // Telemetry: history open — Requirement 5.7
    trackEvent('ptf_admin.history_open', { period, price_type: priceType });

    // Abort previous request
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;
    let cancelled = false;

    const doFetch = async () => {
      setLoading(true);
      setError(null);

      try {
        const response = await fetchHistory(period, priceType, controller.signal);
        if (!cancelled) {
          setHistory(response.history);
        }
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        if (axios.isCancel(err)) return;

        if (!cancelled) {
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

    doFetch();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [period, priceType, fetchTrigger]);

  const refetch = useCallback(() => {
    setFetchTrigger((prev) => prev + 1);
  }, []);

  return { history, loading, error, refetch };
}
