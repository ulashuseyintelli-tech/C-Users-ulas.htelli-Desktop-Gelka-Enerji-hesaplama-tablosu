import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type {
  BulkImportPreviewResponse,
  BulkImportApplyResponse,
} from '../types';

// ---------------------------------------------------------------------------
// Mock the API module
// ---------------------------------------------------------------------------

const mockPreviewBulkImport = vi.fn<
  [File, string, boolean],
  Promise<BulkImportPreviewResponse>
>();

const mockApplyBulkImport = vi.fn<
  [File, string, boolean, boolean],
  Promise<BulkImportApplyResponse>
>();

vi.mock('../marketPricesApi', () => ({
  previewBulkImport: (...args: [File, string, boolean]) =>
    mockPreviewBulkImport(...args),
  applyBulkImport: (...args: [File, string, boolean, boolean]) =>
    mockApplyBulkImport(...args),
}));

// Import hooks AFTER mock setup
import { useBulkImportPreview } from '../hooks/useBulkImportPreview';
import { useBulkImportApply } from '../hooks/useBulkImportApply';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeFile(name = 'prices.csv'): File {
  return new File(['period,value\n2025-01,100'], name, { type: 'text/csv' });
}

function makePreviewResponse(
  overrides: Partial<BulkImportPreviewResponse['preview']> = {},
): BulkImportPreviewResponse {
  return {
    status: 'ok',
    preview: {
      total_rows: 10,
      valid_rows: 8,
      invalid_rows: 2,
      new_records: 5,
      updates: 3,
      unchanged: 0,
      final_conflicts: 0,
      errors: [],
      ...overrides,
    },
  };
}

function makeApplyResponse(
  overrides: Partial<BulkImportApplyResponse['result']> = {},
): BulkImportApplyResponse {
  return {
    status: 'ok',
    result: {
      success: true,
      imported_count: 8,
      skipped_count: 0,
      error_count: 2,
      details: [],
      ...overrides,
    },
  };
}

async function createAxiosError(data: Record<string, unknown>, status = 400) {
  const { default: axiosLib } = await import('axios');
  return new axiosLib.AxiosError(
    'Request failed',
    'ERR_BAD_RESPONSE',
    undefined,
    undefined,
    {
      data,
      status,
      statusText: 'Bad Request',
      headers: {},
      config: {} as never,
    } as never,
  );
}

// ---------------------------------------------------------------------------
// useBulkImportPreview Tests
// ---------------------------------------------------------------------------

describe('useBulkImportPreview', () => {
  beforeEach(() => {
    mockPreviewBulkImport.mockReset();
  });

  // =========================================================================
  // Initial state
  // =========================================================================

  describe('initial state', () => {
    it('returns correct initial values', () => {
      const { result } = renderHook(() => useBulkImportPreview());

      expect(result.current.loading).toBe(false);
      expect(result.current.error).toBeNull();
      expect(typeof result.current.preview).toBe('function');
    });
  });

  // =========================================================================
  // Successful preview
  // =========================================================================

  describe('successful preview', () => {
    it('returns response on success and clears error', async () => {
      const response = makePreviewResponse({ total_rows: 20 });
      mockPreviewBulkImport.mockResolvedValueOnce(response);

      const { result } = renderHook(() => useBulkImportPreview());
      const file = makeFile();

      let previewResult: BulkImportPreviewResponse | undefined;
      await act(async () => {
        previewResult = await result.current.preview(file, 'PTF', false);
      });

      expect(previewResult).toEqual(response);
      expect(result.current.loading).toBe(false);
      expect(result.current.error).toBeNull();
    });

    it('passes correct arguments to the API function', async () => {
      mockPreviewBulkImport.mockResolvedValueOnce(makePreviewResponse());

      const { result } = renderHook(() => useBulkImportPreview());
      const file = makeFile('data.json');

      await act(async () => {
        await result.current.preview(file, 'PTF', true);
      });

      expect(mockPreviewBulkImport).toHaveBeenCalledTimes(1);
      expect(mockPreviewBulkImport).toHaveBeenCalledWith(file, 'PTF', true);
    });

    it('clears previous error on successful preview', async () => {
      // First call: fail
      mockPreviewBulkImport.mockRejectedValueOnce(new Error('Network Error'));

      const { result } = renderHook(() => useBulkImportPreview());

      await act(async () => {
        try {
          await result.current.preview(makeFile(), 'PTF', false);
        } catch {
          // expected
        }
      });

      expect(result.current.error).not.toBeNull();

      // Second call: succeed
      mockPreviewBulkImport.mockResolvedValueOnce(makePreviewResponse());

      await act(async () => {
        await result.current.preview(makeFile(), 'PTF', false);
      });

      expect(result.current.error).toBeNull();
    });
  });

  // =========================================================================
  // Loading state (double-submit guard)
  // =========================================================================

  describe('loading state', () => {
    it('sets loading=true during preview and false after', async () => {
      let resolvePromise: (value: BulkImportPreviewResponse) => void;
      const pendingPromise = new Promise<BulkImportPreviewResponse>((resolve) => {
        resolvePromise = resolve;
      });
      mockPreviewBulkImport.mockReturnValueOnce(pendingPromise);

      const { result } = renderHook(() => useBulkImportPreview());

      let previewPromise: Promise<BulkImportPreviewResponse>;
      act(() => {
        previewPromise = result.current.preview(makeFile(), 'PTF', false);
      });

      expect(result.current.loading).toBe(true);

      await act(async () => {
        resolvePromise!(makePreviewResponse());
        await previewPromise;
      });

      expect(result.current.loading).toBe(false);
    });

    it('sets loading=false after error', async () => {
      mockPreviewBulkImport.mockRejectedValueOnce(new Error('fail'));

      const { result } = renderHook(() => useBulkImportPreview());

      await act(async () => {
        try {
          await result.current.preview(makeFile(), 'PTF', false);
        } catch {
          // expected
        }
      });

      expect(result.current.loading).toBe(false);
    });
  });

  // =========================================================================
  // Error handling
  // =========================================================================

  describe('error handling', () => {
    it('sets ApiErrorResponse from axios error', async () => {
      const axiosError = await createAxiosError({
        status: 'error',
        error_code: 'PARSE_ERROR',
        message: 'File parse error',
      });
      mockPreviewBulkImport.mockRejectedValueOnce(axiosError);

      const { result } = renderHook(() => useBulkImportPreview());

      await act(async () => {
        try {
          await result.current.preview(makeFile(), 'PTF', false);
        } catch {
          // expected
        }
      });

      expect(result.current.error).toEqual({
        status: 'error',
        error_code: 'PARSE_ERROR',
        message: 'File parse error',
      });
    });

    it('sets generic network error when no response data', async () => {
      mockPreviewBulkImport.mockRejectedValueOnce(new Error('Network Error'));

      const { result } = renderHook(() => useBulkImportPreview());

      await act(async () => {
        try {
          await result.current.preview(makeFile(), 'PTF', false);
        } catch {
          // expected
        }
      });

      expect(result.current.error).toEqual({
        status: 'error',
        error_code: 'NETWORK_ERROR',
        message: 'Bağlantı hatası',
      });
    });

    it('throws the original error so the component can handle it', async () => {
      const originalError = new Error('API failure');
      mockPreviewBulkImport.mockRejectedValueOnce(originalError);

      const { result } = renderHook(() => useBulkImportPreview());

      let caughtError: unknown;
      await act(async () => {
        try {
          await result.current.preview(makeFile(), 'PTF', false);
        } catch (err) {
          caughtError = err;
        }
      });

      expect(caughtError).toBe(originalError);
    });
  });
});

// ---------------------------------------------------------------------------
// useBulkImportApply Tests
// ---------------------------------------------------------------------------

describe('useBulkImportApply', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let consoleSpy: any;

  beforeEach(() => {
    mockApplyBulkImport.mockReset();
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
      const { result } = renderHook(() => useBulkImportApply());

      expect(result.current.loading).toBe(false);
      expect(result.current.error).toBeNull();
      expect(typeof result.current.apply).toBe('function');
    });
  });

  // =========================================================================
  // Successful apply
  // =========================================================================

  describe('successful apply', () => {
    it('returns response on success and clears error', async () => {
      const response = makeApplyResponse({ imported_count: 15 });
      mockApplyBulkImport.mockResolvedValueOnce(response);

      const { result } = renderHook(() => useBulkImportApply());
      const file = makeFile();

      let applyResult: BulkImportApplyResponse | undefined;
      await act(async () => {
        applyResult = await result.current.apply(file, 'PTF', false, true);
      });

      expect(applyResult).toEqual(response);
      expect(result.current.loading).toBe(false);
      expect(result.current.error).toBeNull();
    });

    it('passes correct arguments to the API function', async () => {
      mockApplyBulkImport.mockResolvedValueOnce(makeApplyResponse());

      const { result } = renderHook(() => useBulkImportApply());
      const file = makeFile('data.json');

      await act(async () => {
        await result.current.apply(file, 'PTF', true, false);
      });

      expect(mockApplyBulkImport).toHaveBeenCalledTimes(1);
      expect(mockApplyBulkImport).toHaveBeenCalledWith(file, 'PTF', true, false);
    });

    it('clears previous error on successful apply', async () => {
      // First call: fail
      mockApplyBulkImport.mockRejectedValueOnce(new Error('Network Error'));

      const { result } = renderHook(() => useBulkImportApply());

      await act(async () => {
        try {
          await result.current.apply(makeFile(), 'PTF', false, true);
        } catch {
          // expected
        }
      });

      expect(result.current.error).not.toBeNull();

      // Second call: succeed
      mockApplyBulkImport.mockResolvedValueOnce(makeApplyResponse());

      await act(async () => {
        await result.current.apply(makeFile(), 'PTF', false, true);
      });

      expect(result.current.error).toBeNull();
    });
  });

  // =========================================================================
  // Loading state (double-submit guard)
  // =========================================================================

  describe('loading state', () => {
    it('sets loading=true during apply and false after', async () => {
      let resolvePromise: (value: BulkImportApplyResponse) => void;
      const pendingPromise = new Promise<BulkImportApplyResponse>((resolve) => {
        resolvePromise = resolve;
      });
      mockApplyBulkImport.mockReturnValueOnce(pendingPromise);

      const { result } = renderHook(() => useBulkImportApply());

      let applyPromise: Promise<BulkImportApplyResponse>;
      act(() => {
        applyPromise = result.current.apply(makeFile(), 'PTF', false, true);
      });

      expect(result.current.loading).toBe(true);

      await act(async () => {
        resolvePromise!(makeApplyResponse());
        await applyPromise;
      });

      expect(result.current.loading).toBe(false);
    });

    it('sets loading=false after error', async () => {
      mockApplyBulkImport.mockRejectedValueOnce(new Error('fail'));

      const { result } = renderHook(() => useBulkImportApply());

      await act(async () => {
        try {
          await result.current.apply(makeFile(), 'PTF', false, true);
        } catch {
          // expected
        }
      });

      expect(result.current.loading).toBe(false);
    });
  });

  // =========================================================================
  // Error handling
  // =========================================================================

  describe('error handling', () => {
    it('sets ApiErrorResponse from axios error', async () => {
      const axiosError = await createAxiosError({
        status: 'error',
        error_code: 'BATCH_VALIDATION_FAILED',
        message: 'Batch validation failed',
      });
      mockApplyBulkImport.mockRejectedValueOnce(axiosError);

      const { result } = renderHook(() => useBulkImportApply());

      await act(async () => {
        try {
          await result.current.apply(makeFile(), 'PTF', false, true);
        } catch {
          // expected
        }
      });

      expect(result.current.error).toEqual({
        status: 'error',
        error_code: 'BATCH_VALIDATION_FAILED',
        message: 'Batch validation failed',
      });
    });

    it('sets generic network error when no response data', async () => {
      mockApplyBulkImport.mockRejectedValueOnce(new Error('Network Error'));

      const { result } = renderHook(() => useBulkImportApply());

      await act(async () => {
        try {
          await result.current.apply(makeFile(), 'PTF', false, true);
        } catch {
          // expected
        }
      });

      expect(result.current.error).toEqual({
        status: 'error',
        error_code: 'NETWORK_ERROR',
        message: 'Bağlantı hatası',
      });
    });

    it('throws the original error so the component can handle it', async () => {
      const originalError = new Error('API failure');
      mockApplyBulkImport.mockRejectedValueOnce(originalError);

      const { result } = renderHook(() => useBulkImportApply());

      let caughtError: unknown;
      await act(async () => {
        try {
          await result.current.apply(makeFile(), 'PTF', false, true);
        } catch (err) {
          caughtError = err;
        }
      });

      expect(caughtError).toBe(originalError);
    });
  });

  // =========================================================================
  // console.log on apply (Requirement 11.3)
  // =========================================================================

  describe('console.log on apply', () => {
    it('logs "[PTF Bulk Apply]" with details on apply', async () => {
      mockApplyBulkImport.mockResolvedValueOnce(makeApplyResponse());

      const { result } = renderHook(() => useBulkImportApply());
      const file = makeFile('import.csv');

      await act(async () => {
        await result.current.apply(file, 'PTF', true, false);
      });

      expect(consoleSpy).toHaveBeenCalledWith('[PTF Bulk Apply]', {
        file: 'import.csv',
        priceType: 'PTF',
        forceUpdate: true,
        strictMode: false,
      });
    });

    it('logs before API call even if API fails', async () => {
      mockApplyBulkImport.mockRejectedValueOnce(new Error('fail'));

      const { result } = renderHook(() => useBulkImportApply());
      const file = makeFile('data.json');

      await act(async () => {
        try {
          await result.current.apply(file, 'PTF', false, true);
        } catch {
          // expected
        }
      });

      expect(consoleSpy).toHaveBeenCalledWith('[PTF Bulk Apply]', {
        file: 'data.json',
        priceType: 'PTF',
        forceUpdate: false,
        strictMode: true,
      });
    });

    it('does NOT log on preview (only apply)', async () => {
      // This test verifies that useBulkImportPreview does NOT console.log
      mockPreviewBulkImport.mockResolvedValueOnce(makePreviewResponse());

      const { result } = renderHook(() => useBulkImportPreview());

      await act(async () => {
        await result.current.preview(makeFile(), 'PTF', false);
      });

      expect(consoleSpy).not.toHaveBeenCalled();
    });
  });
});
