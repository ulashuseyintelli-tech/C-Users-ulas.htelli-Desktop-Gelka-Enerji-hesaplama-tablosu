// =============================================================================
// PTF Admin Frontend — Market Prices API Client
// =============================================================================
//
// JSON-based API client for the new PTF Admin Management endpoints.
// Uses the shared adminApi axios instance (X-Admin-Key interceptor included).
// =============================================================================

import { adminApi } from '../api';
import type {
  ListParams,
  MarketPricesListResponse,
  UpsertMarketPriceRequest,
  UpsertMarketPriceResponse,
  BulkImportPreviewResponse,
  BulkImportApplyResponse,
  AuditHistoryResponse,
} from './types';

/**
 * Fetch paginated, filtered, sorted list of market price records.
 *
 * @param params  Query parameters (page, page_size, sort_by, sort_order, filters)
 * @param signal  Optional AbortSignal for cancelling in-flight requests
 * @returns       Paginated list response
 */
export async function listMarketPrices(
  params: ListParams,
  signal?: AbortSignal,
): Promise<MarketPricesListResponse> {
  const response = await adminApi.get<MarketPricesListResponse>(
    '/admin/market-prices',
    {
      params: {
        page: params.page,
        page_size: params.page_size,
        sort_by: params.sort_by,
        sort_order: params.sort_order,
        price_type: params.price_type,
        ...(params.status ? { status: params.status } : {}),
        ...(params.from_period ? { from_period: params.from_period } : {}),
        ...(params.to_period ? { to_period: params.to_period } : {}),
      },
      signal,
    },
  );
  return response.data;
}

/**
 * Create or update a single market price record (JSON body).
 *
 * @param req  Upsert request payload
 * @returns    Upsert result with action ('created' | 'updated') and optional warnings
 */
export async function upsertMarketPrice(
  req: UpsertMarketPriceRequest,
): Promise<UpsertMarketPriceResponse> {
  const response = await adminApi.post<UpsertMarketPriceResponse>(
    '/admin/market-prices',
    req,
  );
  return response.data;
}

/**
 * Preview a bulk import file without applying changes.
 * Sends the file as multipart/form-data.
 *
 * @param file        CSV or JSON file to preview
 * @param priceType   Price type identifier (e.g. "PTF")
 * @param forceUpdate Whether to allow overwriting final records
 * @returns           Preview summary with row-level validation results
 */
export async function previewBulkImport(
  file: File,
  priceType: string,
  forceUpdate: boolean,
): Promise<BulkImportPreviewResponse> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('price_type', priceType);
  formData.append('force_update', String(forceUpdate));

  const response = await adminApi.post<BulkImportPreviewResponse>(
    '/admin/market-prices/import/preview',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return response.data;
}

/**
 * Apply a bulk import file (actually persist changes).
 * Sends the file as multipart/form-data.
 *
 * @param file        CSV or JSON file to apply
 * @param priceType   Price type identifier (e.g. "PTF")
 * @param forceUpdate Whether to allow overwriting final records
 * @param strictMode  If true, abort entire batch on any row error
 * @returns           Apply result with imported/skipped/error counts
 */
export async function applyBulkImport(
  file: File,
  priceType: string,
  forceUpdate: boolean,
  strictMode: boolean,
): Promise<BulkImportApplyResponse> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('price_type', priceType);
  formData.append('force_update', String(forceUpdate));
  formData.append('strict_mode', String(strictMode));

  const response = await adminApi.post<BulkImportApplyResponse>(
    '/admin/market-prices/import/apply',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return response.data;
}

/**
 * Fetch audit history for a specific period and price type.
 *
 * @param period    Period in YYYY-MM format
 * @param priceType Price type (default: "PTF")
 * @param signal    Optional AbortSignal for cancellation
 * @returns         History response with entries ordered by created_at DESC
 */
export async function fetchHistory(
  period: string,
  priceType: string = 'PTF',
  signal?: AbortSignal,
): Promise<AuditHistoryResponse> {
  const response = await adminApi.get<AuditHistoryResponse>(
    '/admin/market-prices/history',
    { params: { period, price_type: priceType }, signal },
  );
  return response.data;
}
