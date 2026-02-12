// =============================================================================
// Unit Tests: marketPricesApi
// Feature: ptf-admin-frontend
// =============================================================================
// **Validates: Requirements 9.1, 9.2, 9.3, 9.4**

import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { ListParams, UpsertMarketPriceRequest } from '../types';

// ---------------------------------------------------------------------------
// Mock the adminApi axios instance
// ---------------------------------------------------------------------------

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock('../../api', () => ({
  adminApi: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  },
}));

// Import AFTER mock setup
import {
  listMarketPrices,
  upsertMarketPrice,
  previewBulkImport,
  applyBulkImport,
} from '../marketPricesApi';

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

// ---------------------------------------------------------------------------
// listMarketPrices
// ---------------------------------------------------------------------------

describe('listMarketPrices', () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  it('calls GET /admin/market-prices with correct query params', async () => {
    const responseData = { status: 'ok', total: 0, page: 1, page_size: 20, items: [] };
    mockGet.mockResolvedValueOnce({ data: responseData });

    await listMarketPrices(DEFAULT_PARAMS);

    expect(mockGet).toHaveBeenCalledTimes(1);
    const [url, config] = mockGet.mock.calls[0];
    expect(url).toBe('/admin/market-prices');
    expect(config.params).toEqual({
      page: 1,
      page_size: 20,
      sort_by: 'period',
      sort_order: 'desc',
      price_type: 'PTF',
    });
  });

  it('includes optional filter params when provided', async () => {
    mockGet.mockResolvedValueOnce({ data: { status: 'ok', total: 0, page: 1, page_size: 20, items: [] } });

    const params: ListParams = {
      ...DEFAULT_PARAMS,
      status: 'final',
      from_period: '2024-01',
      to_period: '2025-06',
    };

    await listMarketPrices(params);

    const config = mockGet.mock.calls[0][1];
    expect(config.params.status).toBe('final');
    expect(config.params.from_period).toBe('2024-01');
    expect(config.params.to_period).toBe('2025-06');
  });

  it('omits status/from_period/to_period when not set', async () => {
    mockGet.mockResolvedValueOnce({ data: { status: 'ok', total: 0, page: 1, page_size: 20, items: [] } });

    await listMarketPrices(DEFAULT_PARAMS);

    const config = mockGet.mock.calls[0][1];
    expect(config.params).not.toHaveProperty('status');
    expect(config.params).not.toHaveProperty('from_period');
    expect(config.params).not.toHaveProperty('to_period');
  });

  it('passes AbortSignal to the request config', async () => {
    mockGet.mockResolvedValueOnce({ data: { status: 'ok', total: 0, page: 1, page_size: 20, items: [] } });

    const controller = new AbortController();
    await listMarketPrices(DEFAULT_PARAMS, controller.signal);

    const config = mockGet.mock.calls[0][1];
    expect(config.signal).toBe(controller.signal);
  });

  it('works without AbortSignal (signal is undefined)', async () => {
    mockGet.mockResolvedValueOnce({ data: { status: 'ok', total: 0, page: 1, page_size: 20, items: [] } });

    await listMarketPrices(DEFAULT_PARAMS);

    const config = mockGet.mock.calls[0][1];
    expect(config.signal).toBeUndefined();
  });

  it('returns response.data directly', async () => {
    const responseData = { status: 'ok', total: 42, page: 2, page_size: 20, items: [{ period: '2025-01' }] };
    mockGet.mockResolvedValueOnce({ data: responseData });

    const result = await listMarketPrices({ ...DEFAULT_PARAMS, page: 2 });
    expect(result).toEqual(responseData);
  });

  it('propagates errors from adminApi', async () => {
    const error = new Error('Network Error');
    mockGet.mockRejectedValueOnce(error);

    await expect(listMarketPrices(DEFAULT_PARAMS)).rejects.toThrow('Network Error');
  });
});

// ---------------------------------------------------------------------------
// upsertMarketPrice
// ---------------------------------------------------------------------------

describe('upsertMarketPrice', () => {
  beforeEach(() => {
    mockPost.mockReset();
  });

  it('sends POST /admin/market-prices with JSON body', async () => {
    const responseData = { status: 'ok', action: 'created', period: '2025-01' };
    mockPost.mockResolvedValueOnce({ data: responseData });

    const req: UpsertMarketPriceRequest = {
      period: '2025-01',
      value: 2508.80,
      price_type: 'PTF',
      status: 'provisional',
      source_note: 'manual entry',
      change_reason: 'initial',
      force_update: false,
    };

    await upsertMarketPrice(req);

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body] = mockPost.mock.calls[0];
    expect(url).toBe('/admin/market-prices');
    // Body is plain JSON object, NOT FormData
    expect(body).toEqual(req);
    expect(body).not.toBeInstanceOf(FormData);
  });

  it('sends value with dot decimal separator (JSON serialization)', async () => {
    mockPost.mockResolvedValueOnce({ data: { status: 'ok', action: 'updated', period: '2025-03' } });

    const req: UpsertMarketPriceRequest = {
      period: '2025-03',
      value: 1234.56,
      price_type: 'PTF',
      status: 'final',
      force_update: true,
      change_reason: 'correction',
    };

    await upsertMarketPrice(req);

    const body = mockPost.mock.calls[0][1];
    expect(body.value).toBe(1234.56);
    expect(typeof body.value).toBe('number');
  });

  it('returns response.data directly', async () => {
    const responseData = { status: 'ok', action: 'updated', period: '2025-01', warnings: ['test warning'] };
    mockPost.mockResolvedValueOnce({ data: responseData });

    const result = await upsertMarketPrice({
      period: '2025-01',
      value: 100,
      price_type: 'PTF',
      status: 'provisional',
      force_update: false,
    });

    expect(result).toEqual(responseData);
  });

  it('propagates errors from adminApi', async () => {
    mockPost.mockRejectedValueOnce(new Error('400 Bad Request'));

    await expect(
      upsertMarketPrice({
        period: '2025-01',
        value: -1,
        price_type: 'PTF',
        status: 'provisional',
        force_update: false,
      }),
    ).rejects.toThrow('400 Bad Request');
  });
});

// ---------------------------------------------------------------------------
// previewBulkImport
// ---------------------------------------------------------------------------

describe('previewBulkImport', () => {
  beforeEach(() => {
    mockPost.mockReset();
  });

  it('sends POST to /admin/market-prices/import/preview with multipart/form-data', async () => {
    const responseData = {
      status: 'ok',
      preview: { total_rows: 5, valid_rows: 4, invalid_rows: 1, new_records: 3, updates: 1, unchanged: 0, final_conflicts: 0, errors: [] },
    };
    mockPost.mockResolvedValueOnce({ data: responseData });

    const file = new File(['test'], 'prices.csv', { type: 'text/csv' });
    await previewBulkImport(file, 'PTF', false);

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body, config] = mockPost.mock.calls[0];
    expect(url).toBe('/admin/market-prices/import/preview');
    expect(body).toBeInstanceOf(FormData);
    expect(config.headers['Content-Type']).toBe('multipart/form-data');
  });

  it('includes file, price_type, and force_update in FormData', async () => {
    mockPost.mockResolvedValueOnce({
      data: { status: 'ok', preview: { total_rows: 0, valid_rows: 0, invalid_rows: 0, new_records: 0, updates: 0, unchanged: 0, final_conflicts: 0, errors: [] } },
    });

    const file = new File(['data'], 'import.json', { type: 'application/json' });
    await previewBulkImport(file, 'PTF', true);

    const formData: FormData = mockPost.mock.calls[0][1];
    expect(formData.get('file')).toBeInstanceOf(File);
    expect(formData.get('price_type')).toBe('PTF');
    expect(formData.get('force_update')).toBe('true');
  });

  it('returns response.data directly', async () => {
    const responseData = {
      status: 'ok',
      preview: { total_rows: 2, valid_rows: 2, invalid_rows: 0, new_records: 2, updates: 0, unchanged: 0, final_conflicts: 0, errors: [] },
    };
    mockPost.mockResolvedValueOnce({ data: responseData });

    const result = await previewBulkImport(new File(['x'], 'f.csv'), 'PTF', false);
    expect(result).toEqual(responseData);
  });
});

// ---------------------------------------------------------------------------
// applyBulkImport
// ---------------------------------------------------------------------------

describe('applyBulkImport', () => {
  beforeEach(() => {
    mockPost.mockReset();
  });

  it('sends POST to /admin/market-prices/import/apply with multipart/form-data', async () => {
    const responseData = {
      status: 'ok',
      result: { success: true, imported_count: 3, skipped_count: 0, error_count: 0, details: [] },
    };
    mockPost.mockResolvedValueOnce({ data: responseData });

    const file = new File(['test'], 'prices.csv', { type: 'text/csv' });
    await applyBulkImport(file, 'PTF', false, true);

    expect(mockPost).toHaveBeenCalledTimes(1);
    const [url, body, config] = mockPost.mock.calls[0];
    expect(url).toBe('/admin/market-prices/import/apply');
    expect(body).toBeInstanceOf(FormData);
    expect(config.headers['Content-Type']).toBe('multipart/form-data');
  });

  it('includes file, price_type, force_update, and strict_mode in FormData', async () => {
    mockPost.mockResolvedValueOnce({
      data: { status: 'ok', result: { success: true, imported_count: 0, skipped_count: 0, error_count: 0, details: [] } },
    });

    const file = new File(['data'], 'import.csv', { type: 'text/csv' });
    await applyBulkImport(file, 'PTF', true, false);

    const formData: FormData = mockPost.mock.calls[0][1];
    expect(formData.get('file')).toBeInstanceOf(File);
    expect(formData.get('price_type')).toBe('PTF');
    expect(formData.get('force_update')).toBe('true');
    expect(formData.get('strict_mode')).toBe('false');
  });

  it('returns response.data directly', async () => {
    const responseData = {
      status: 'ok',
      result: { success: true, imported_count: 5, skipped_count: 1, error_count: 0, details: [] },
    };
    mockPost.mockResolvedValueOnce({ data: responseData });

    const result = await applyBulkImport(new File(['x'], 'f.csv'), 'PTF', false, true);
    expect(result).toEqual(responseData);
  });

  it('propagates errors from adminApi', async () => {
    mockPost.mockRejectedValueOnce(new Error('500 Internal Server Error'));

    await expect(
      applyBulkImport(new File(['x'], 'f.csv'), 'PTF', false, true),
    ).rejects.toThrow('500 Internal Server Error');
  });
});
