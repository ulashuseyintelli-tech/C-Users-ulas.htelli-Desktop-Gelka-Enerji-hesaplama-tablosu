import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type {
  UpsertMarketPriceRequest,
  UpsertMarketPriceResponse,
} from '../types';

// ---------------------------------------------------------------------------
// Mock the API module
// ---------------------------------------------------------------------------

const mockUpsertMarketPrice = vi.fn<
  [UpsertMarketPriceRequest],
  Promise<UpsertMarketPriceResponse>
>();

vi.mock('../marketPricesApi', () => ({
  upsertMarketPrice: (...args: [UpsertMarketPriceRequest]) =>
    mockUpsertMarketPrice(...args),
}));

// Import hook AFTER mock setup
import { useUpsertMarketPrice } from '../hooks/useUpsertMarketPrice';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRequest(
  overrides: Partial<UpsertMarketPriceRequest> = {},
): UpsertMarketPriceRequest {
  return {
    period: '2025-01',
    value: 2508.8,
    price_type: 'PTF',
    status: 'provisional',
    source_note: 'test',
    change_reason: 'test reason',
    force_update: false,
    ...overrides,
  };
}

function makeSuccessResponse(
  overrides: Partial<UpsertMarketPriceResponse> = {},
): UpsertMarketPriceResponse {
  return {
    status: 'ok',
    action: 'created',
    period: '2025-01',
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useUpsertMarketPrice', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let consoleSpy: any;

  beforeEach(() => {
    mockUpsertMarketPrice.mockReset();
    consoleSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
  });

  afterEach(() => {
    consoleSpy.mockRestore();
    vi.restoreAllMocks();
  });

  // =========================================================================
  // Initial state
  // =========================================================================

  describe('initial state', () => {
    it('returns correct initial values', () => {
      const { result } = renderHook(() => useUpsertMarketPrice());

      expect(result.current.loading).toBe(false);
      expect(result.current.error).toBeNull();
      expect(result.current.fieldErrors).toEqual({});
      expect(typeof result.current.submit).toBe('function');
    });
  });

  // =========================================================================
  // Successful submission
  // =========================================================================

  describe('successful submission', () => {
    it('returns response on success and clears error state', async () => {
      const response = makeSuccessResponse({ action: 'updated', period: '2025-03' });
      mockUpsertMarketPrice.mockResolvedValueOnce(response);

      const { result } = renderHook(() => useUpsertMarketPrice());
      const req = makeRequest({ period: '2025-03' });

      let submitResult: UpsertMarketPriceResponse | undefined;
      await act(async () => {
        submitResult = await result.current.submit(req);
      });

      expect(submitResult).toEqual(response);
      expect(result.current.loading).toBe(false);
      expect(result.current.error).toBeNull();
      expect(result.current.fieldErrors).toEqual({});
    });

    it('passes the request to the API function', async () => {
      mockUpsertMarketPrice.mockResolvedValueOnce(makeSuccessResponse());

      const { result } = renderHook(() => useUpsertMarketPrice());
      const req = makeRequest({ period: '2025-06', value: 1234.56 });

      await act(async () => {
        await result.current.submit(req);
      });

      expect(mockUpsertMarketPrice).toHaveBeenCalledTimes(1);
      expect(mockUpsertMarketPrice).toHaveBeenCalledWith(req);
    });

    it('clears previous errors on successful submit', async () => {
      // First call: fail
      const { default: axiosLib } = await import('axios');
      const fakeAxiosError = new axiosLib.AxiosError(
        'Request failed',
        'ERR_BAD_RESPONSE',
        undefined,
        undefined,
        {
          data: {
            status: 'error',
            error_code: 'INVALID_PTF_VALUE',
            message: 'Invalid value',
          },
          status: 400,
          statusText: 'Bad Request',
          headers: {},
          config: {} as never,
        } as never,
      );
      mockUpsertMarketPrice.mockRejectedValueOnce(fakeAxiosError);

      const { result } = renderHook(() => useUpsertMarketPrice());

      // Submit and expect failure
      await act(async () => {
        try {
          await result.current.submit(makeRequest());
        } catch {
          // expected
        }
      });

      expect(result.current.error).not.toBeNull();
      expect(Object.keys(result.current.fieldErrors).length).toBeGreaterThan(0);

      // Second call: succeed
      mockUpsertMarketPrice.mockResolvedValueOnce(makeSuccessResponse());

      await act(async () => {
        await result.current.submit(makeRequest());
      });

      expect(result.current.error).toBeNull();
      expect(result.current.fieldErrors).toEqual({});
    });
  });

  // =========================================================================
  // Loading state (double-submit guard)
  // =========================================================================

  describe('loading state (double-submit guard)', () => {
    it('sets loading=true during submission and false after', async () => {
      let resolvePromise: (value: UpsertMarketPriceResponse) => void;
      const pendingPromise = new Promise<UpsertMarketPriceResponse>((resolve) => {
        resolvePromise = resolve;
      });
      mockUpsertMarketPrice.mockReturnValueOnce(pendingPromise);

      const { result } = renderHook(() => useUpsertMarketPrice());

      // Start submit (don't await)
      let submitPromise: Promise<UpsertMarketPriceResponse>;
      act(() => {
        submitPromise = result.current.submit(makeRequest());
      });

      // Loading should be true while in-flight
      expect(result.current.loading).toBe(true);

      // Resolve the API call
      await act(async () => {
        resolvePromise!(makeSuccessResponse());
        await submitPromise;
      });

      expect(result.current.loading).toBe(false);
    });

    it('sets loading=false after error', async () => {
      mockUpsertMarketPrice.mockRejectedValueOnce(new Error('Network Error'));

      const { result } = renderHook(() => useUpsertMarketPrice());

      await act(async () => {
        try {
          await result.current.submit(makeRequest());
        } catch {
          // expected
        }
      });

      expect(result.current.loading).toBe(false);
    });
  });

  // =========================================================================
  // Error parsing with fieldErrors
  // =========================================================================

  describe('error parsing with fieldErrors', () => {
    it('sets fieldErrors for error codes with field mapping', async () => {
      const { default: axiosLib } = await import('axios');
      const fakeAxiosError = new axiosLib.AxiosError(
        'Request failed',
        'ERR_BAD_RESPONSE',
        undefined,
        undefined,
        {
          data: {
            status: 'error',
            error_code: 'INVALID_PERIOD_FORMAT',
            message: 'Invalid period format',
          },
          status: 400,
          statusText: 'Bad Request',
          headers: {},
          config: {} as never,
        } as never,
      );
      mockUpsertMarketPrice.mockRejectedValueOnce(fakeAxiosError);

      const { result } = renderHook(() => useUpsertMarketPrice());

      await act(async () => {
        try {
          await result.current.submit(makeRequest());
        } catch {
          // expected
        }
      });

      expect(result.current.fieldErrors).toEqual({
        period: 'Geçersiz dönem formatı (YYYY-MM bekleniyor)',
      });
      expect(result.current.error).toEqual({
        status: 'error',
        error_code: 'INVALID_PERIOD_FORMAT',
        message: 'Invalid period format',
      });
    });

    it('sets empty fieldErrors for error codes without field mapping', async () => {
      const { default: axiosLib } = await import('axios');
      const fakeAxiosError = new axiosLib.AxiosError(
        'Request failed',
        'ERR_BAD_RESPONSE',
        undefined,
        undefined,
        {
          data: {
            status: 'error',
            error_code: 'PERIOD_NOT_FOUND',
            message: 'Period not found',
          },
          status: 404,
          statusText: 'Not Found',
          headers: {},
          config: {} as never,
        } as never,
      );
      mockUpsertMarketPrice.mockRejectedValueOnce(fakeAxiosError);

      const { result } = renderHook(() => useUpsertMarketPrice());

      await act(async () => {
        try {
          await result.current.submit(makeRequest());
        } catch {
          // expected
        }
      });

      // No field mapping for PERIOD_NOT_FOUND → empty fieldErrors
      expect(result.current.fieldErrors).toEqual({});
      // But error state should still be set (for global toast)
      expect(result.current.error).toEqual({
        status: 'error',
        error_code: 'PERIOD_NOT_FOUND',
        message: 'Period not found',
      });
    });

    it('sets fieldErrors for CHANGE_REASON_REQUIRED', async () => {
      const { default: axiosLib } = await import('axios');
      const fakeAxiosError = new axiosLib.AxiosError(
        'Request failed',
        'ERR_BAD_RESPONSE',
        undefined,
        undefined,
        {
          data: {
            status: 'error',
            error_code: 'CHANGE_REASON_REQUIRED',
            message: 'Change reason is required',
          },
          status: 400,
          statusText: 'Bad Request',
          headers: {},
          config: {} as never,
        } as never,
      );
      mockUpsertMarketPrice.mockRejectedValueOnce(fakeAxiosError);

      const { result } = renderHook(() => useUpsertMarketPrice());

      await act(async () => {
        try {
          await result.current.submit(makeRequest());
        } catch {
          // expected
        }
      });

      expect(result.current.fieldErrors).toEqual({
        change_reason: 'Değişiklik nedeni zorunlu',
      });
    });

    it('sets generic network error when no response data', async () => {
      mockUpsertMarketPrice.mockRejectedValueOnce(new Error('Network Error'));

      const { result } = renderHook(() => useUpsertMarketPrice());

      await act(async () => {
        try {
          await result.current.submit(makeRequest());
        } catch {
          // expected
        }
      });

      expect(result.current.error).toEqual({
        status: 'error',
        error_code: 'NETWORK_ERROR',
        message: 'Bağlantı hatası',
      });
      expect(result.current.fieldErrors).toEqual({});
    });
  });

  // =========================================================================
  // console.log on submit (Requirement 11.3)
  // =========================================================================

  describe('console.log on submit', () => {
    it('logs "[PTF Upsert]" with request on submit', async () => {
      mockUpsertMarketPrice.mockResolvedValueOnce(makeSuccessResponse());

      const { result } = renderHook(() => useUpsertMarketPrice());
      const req = makeRequest({ period: '2025-04', value: 999.99 });

      await act(async () => {
        await result.current.submit(req);
      });

      expect(consoleSpy).toHaveBeenCalledWith('[PTF Upsert]', req);
    });

    it('logs before API call even if API fails', async () => {
      mockUpsertMarketPrice.mockRejectedValueOnce(new Error('fail'));

      const { result } = renderHook(() => useUpsertMarketPrice());
      const req = makeRequest();

      await act(async () => {
        try {
          await result.current.submit(req);
        } catch {
          // expected
        }
      });

      expect(consoleSpy).toHaveBeenCalledWith('[PTF Upsert]', req);
    });
  });

  // =========================================================================
  // Submit throws on error (component handles it)
  // =========================================================================

  describe('submit throws on error', () => {
    it('throws the original error so the component can handle it', async () => {
      const originalError = new Error('API failure');
      mockUpsertMarketPrice.mockRejectedValueOnce(originalError);

      const { result } = renderHook(() => useUpsertMarketPrice());

      let caughtError: unknown;
      await act(async () => {
        try {
          await result.current.submit(makeRequest());
        } catch (err) {
          caughtError = err;
        }
      });

      expect(caughtError).toBe(originalError);
    });
  });
});
