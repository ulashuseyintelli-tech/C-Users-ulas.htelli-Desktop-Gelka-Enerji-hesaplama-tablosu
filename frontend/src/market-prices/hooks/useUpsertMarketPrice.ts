// =============================================================================
// PTF Admin Frontend — useUpsertMarketPrice Hook
// =============================================================================
// Mutation hook for creating/updating a single market price record.
//
// Key behaviors:
// - submit(req) calls upsertMarketPrice(req) from marketPricesApi.ts
// - During submit: loading=true → double-submit guard (button disabled)
// - On success: return response, clear error/fieldErrors
// - On error: parse ApiErrorResponse, use parseFieldErrors() to map field errors
// - console.log('[PTF Upsert]', req) on submit for operational debug (Req 11.3)
// - Error with field mapping → fieldErrors record
// - Error without field → error state (for global toast)
// =============================================================================

import { useState, useCallback } from 'react';
import axios from 'axios';
import { upsertMarketPrice } from '../marketPricesApi';
import { parseFieldErrors } from '../utils';
import { trackEvent } from '../telemetry';
import type {
  UpsertMarketPriceRequest,
  UpsertMarketPriceResponse,
  ApiErrorResponse,
} from '../types';

export function useUpsertMarketPrice() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiErrorResponse | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const submit = useCallback(
    async (req: UpsertMarketPriceRequest): Promise<UpsertMarketPriceResponse> => {
      // Operational debug log — Requirement 11.3
      console.log('[PTF Upsert]', req);

      // Telemetry: upsert submit — Requirement 5.1
      trackEvent('ptf_admin.upsert_submit', {
        period: req.period,
        price_type: req.price_type,
        status: req.status,
      });

      setLoading(true);
      setError(null);
      setFieldErrors({});

      try {
        const response = await upsertMarketPrice(req);
        // Success — clear all error state
        setError(null);
        setFieldErrors({});

        // Telemetry: upsert success — Requirement 5.2
        trackEvent('ptf_admin.upsert_success', { action: response.action });

        return response;
      } catch (err: unknown) {
        // Extract ApiErrorResponse from axios error
        let apiError: ApiErrorResponse;

        if (axios.isAxiosError(err) && err.response?.data) {
          apiError = err.response.data as ApiErrorResponse;
        } else {
          apiError = {
            status: 'error',
            error_code: 'NETWORK_ERROR',
            message: 'Bağlantı hatası',
          };
        }

        // Parse field-level errors from error_code mapping
        const parsed = parseFieldErrors(apiError);

        if (Object.keys(parsed).length > 0) {
          // Error maps to specific field(s) → inline form errors
          setFieldErrors(parsed);
        }

        // Always set the global error state so the component can decide
        // whether to show a toast (for errors without field mapping)
        setError(apiError);

        // Telemetry: upsert error — Requirement 5.3
        trackEvent('ptf_admin.upsert_error', { error_code: apiError.error_code });

        throw err;
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  return { submit, loading, error, fieldErrors };
}
