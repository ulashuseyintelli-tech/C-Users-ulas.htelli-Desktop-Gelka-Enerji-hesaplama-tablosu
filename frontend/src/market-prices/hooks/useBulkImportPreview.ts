// =============================================================================
// PTF Admin Frontend — useBulkImportPreview Hook
// =============================================================================
// Mutation hook for previewing a bulk import file.
//
// Key behaviors:
// - preview(file, priceType, forceUpdate) calls previewBulkImport from API
// - During preview: loading=true → double-submit guard (button disabled)
// - On success: return response, clear error
// - On error: parse ApiErrorResponse, set error state, throw
// - Generic "Bağlantı hatası" fallback for network errors
// - No console.log on preview (only apply needs it per Req 11.3)
// =============================================================================

import { useState, useCallback } from 'react';
import axios from 'axios';
import { previewBulkImport } from '../marketPricesApi';
import type { BulkImportPreviewResponse, ApiErrorResponse } from '../types';

export function useBulkImportPreview() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiErrorResponse | null>(null);

  const preview = useCallback(
    async (
      file: File,
      priceType: string,
      forceUpdate: boolean,
    ): Promise<BulkImportPreviewResponse> => {
      setLoading(true);
      setError(null);

      try {
        const response = await previewBulkImport(file, priceType, forceUpdate);
        setError(null);
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
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  return { preview, loading, error };
}
