// =============================================================================
// PTF Admin Frontend — useBulkImportApply Hook
// =============================================================================
// Mutation hook for applying a bulk import file.
//
// Key behaviors:
// - apply(file, priceType, forceUpdate, strictMode) calls applyBulkImport
// - During apply: loading=true → double-submit guard (button disabled)
// - On success: return response, clear error
// - On error: parse ApiErrorResponse, set error state, throw
// - Generic "Bağlantı hatası" fallback for network errors
// - console.log('[PTF Bulk Apply]', ...) on apply for debug (Req 11.3)
// =============================================================================

import { useState, useCallback } from 'react';
import axios from 'axios';
import { applyBulkImport } from '../marketPricesApi';
import { trackEvent } from '../telemetry';
import type { BulkImportApplyResponse, ApiErrorResponse } from '../types';

export function useBulkImportApply() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiErrorResponse | null>(null);

  const apply = useCallback(
    async (
      file: File,
      priceType: string,
      forceUpdate: boolean,
      strictMode: boolean,
    ): Promise<BulkImportApplyResponse> => {
      // Operational debug log — Requirement 11.3
      console.log('[PTF Bulk Apply]', {
        file: file.name,
        priceType,
        forceUpdate,
        strictMode,
      });

      setLoading(true);
      setError(null);

      try {
        const response = await applyBulkImport(
          file,
          priceType,
          forceUpdate,
          strictMode,
        );
        setError(null);

        // Telemetry: bulk import complete — Requirement 5.5
        trackEvent('ptf_admin.bulk_import_complete', {
          imported_count: response.result.imported_count,
          skipped_count: response.result.skipped_count,
          error_count: response.result.error_count,
        });

        return response;
      } catch (err: unknown) {
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

        setError(apiError);

        // Telemetry: bulk import error — Requirement 5.6
        trackEvent('ptf_admin.bulk_import_error', { error_code: apiError.error_code });

        throw err;
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  return { apply, loading, error };
}
