# Implementation Plan: PTF Admin Frontend

## Overview

Mevcut AdminPanel.tsx monolitindeki market-prices sekmesini modüler React bileşen ağacına dönüştürür. Sıralama: types → utils → API client → hooks → bileşenler → entegrasyon. Her adım önceki adımın üzerine inşa edilir.

## Tasks

- [x] 1. Proje altyapısı ve temel tipler
  - [x] 1.1 Create `frontend/src/market-prices/types.ts` with all TypeScript interfaces: MarketPriceRecord, MarketPricesListResponse, UpsertMarketPriceRequest, UpsertMarketPriceResponse, ApiErrorResponse, BulkImportPreviewResponse, BulkImportError, BulkImportApplyResponse, PaginationState, ListParams, FilterState, ToastMessage, UpsertFormState, BulkImportStep
    - _Requirements: 9.5_
  - [x] 1.2 Create `frontend/src/market-prices/constants.ts` with ERROR_CODE_MAP (error_code → {message, field?} mapping for all 13 backend error codes), default ListParams, status label map (provisional→"Ön Değer", final→"Kesinleşmiş")
    - _Requirements: 8.2, 4.1, 4.2_
  - [x] 1.3 Add vitest, fast-check, @testing-library/react, @testing-library/jest-dom, @testing-library/user-event, jsdom to devDependencies in `frontend/package.json` and create `frontend/vitest.config.ts`
    - _Requirements: Testing infrastructure_

- [x] 2. Utility fonksiyonları ve property testleri
  - [x] 2.1 Create `frontend/src/market-prices/utils.ts` with: formatPrice (number → Turkish locale "2.508,80"), formatDateTime (ISO UTC → "DD.MM.YYYY HH:mm" Europe/Istanbul), parseUrlParams, serializeUrlParams, parseFieldErrors, exportFailedRowsCsv, exportFailedRowsJson
    - _Requirements: 1.2, 2.3, 2.4, 5.7, 7.4_
  - [ ]* 2.2 Write property tests in `frontend/src/market-prices/__tests__/formatters.property.test.ts`
    - **Property 2: Price Formatting** — for any non-negative number, formatPrice produces Turkish locale string with 2 decimals, reversible within float tolerance
    - **Property 3: DateTime Formatting** — for any valid ISO 8601 UTC string, formatDateTime produces "DD.MM.YYYY HH:mm" in Europe/Istanbul timezone
    - **Validates: Requirements 1.2**
  - [ ]* 2.3 Write property tests in `frontend/src/market-prices/__tests__/urlState.property.test.ts`
    - **Property 1: URL State Round-Trip** — for any valid FilterState + pagination, serialize then parse produces equivalent state
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4**

- [x] 3. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. API client katmanı
  - [x] 4.1 Create `frontend/src/market-prices/marketPricesApi.ts` with functions: listMarketPrices(params, signal?), upsertMarketPrice(req), previewBulkImport(file, priceType, forceUpdate), applyBulkImport(file, priceType, forceUpdate, strictMode). Use existing adminApi axios instance from api.ts. JSON Content-Type for upsert, multipart/form-data for bulk import. AbortController signal support on list endpoint.
    - _Requirements: 9.1, 9.2, 9.3, 9.4_
  - [ ]* 4.2 Write unit tests in `frontend/src/market-prices/__tests__/marketPricesApi.test.ts`
    - Test request format (JSON body for upsert, multipart for bulk)
    - Test X-Admin-Key header inclusion
    - Test AbortController cancellation
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [x] 5. Custom hooks
  - [x] 5.1 Create `frontend/src/market-prices/hooks/useUrlState.ts` — parse window.location.search on mount, replaceState on state change, popstate listener for back/forward, clearFilters resets to defaults
    - _Requirements: 2.1, 2.2, 2.5_
  - [x] 5.2 Create `frontend/src/market-prices/hooks/useMarketPricesList.ts` — fetch on params change, AbortController for cancellation, loading/error/data state, refetch function
    - _Requirements: 1.1, 1.7, 3.5, 11.1_
  - [x] 5.3 Create `frontend/src/market-prices/hooks/useUpsertMarketPrice.ts` — submit function, loading state for double-submit guard, error parsing with fieldErrors via parseFieldErrors, console.log on submit for debug
    - _Requirements: 5.4, 5.5, 5.7, 11.2, 11.3_
  - [x] 5.4 Create `frontend/src/market-prices/hooks/useBulkImportPreview.ts` and `frontend/src/market-prices/hooks/useBulkImportApply.ts` — preview/apply functions, loading state, error handling, console.log on apply for debug
    - _Requirements: 6.1, 7.1, 7.2, 11.2, 11.3_
  - [ ]* 5.5 Write property tests in `frontend/src/market-prices/__tests__/listState.property.test.ts`
    - **Property 4: Sort Toggle Correctness** — for any sort state and clicked column, toggle logic is correct
    - **Property 5: Pagination Total Pages** — for any total≥0 and pageSize>0, totalPages = ceil(total/pageSize)
    - **Property 6: Filter Change Resets Page** — for any page>1, filter change resets to page 1
    - **Validates: Requirements 1.5, 1.6, 3.4**

- [x] 6. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Temel UI bileşenleri
  - [x] 7.1 Create `frontend/src/market-prices/StatusBadge.tsx` — provisional: "Ön Değer" bg-amber-100 text-amber-700, final: "Kesinleşmiş" bg-green-100 text-green-700
    - _Requirements: 4.1, 4.2_
  - [x] 7.2 Create `frontend/src/market-prices/SkeletonLoader.tsx` — table skeleton with shimmer animation matching PriceListTable column layout
    - _Requirements: 1.3_
  - [x] 7.3 Create `frontend/src/market-prices/ToastNotification.tsx` — toast container with success/info/warning/error variants, auto-close (5s default), error_code in monospace, dismiss button
    - _Requirements: 8.1, 8.5_
  - [x] 7.4 Create `frontend/src/market-prices/PriceFilters.tsx` — status dropdown (Tümü/Ön Değer/Kesinleşmiş), from_period and to_period YYYY-MM inputs, 300ms debounce on change, reset page to 1 on filter change
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 11.4_

- [x] 8. Tablo ve sayfalama bileşeni
  - [x] 8.1 Create `frontend/src/market-prices/PriceListTable.tsx` — sortable columns (period, ptf_tl_per_mwh, status, updated_at), Turkish locale price formatting, Europe/Istanbul datetime, pagination controls (page nav, page size selector), empty state with "Filtreleri Temizle" CTA, skeleton loading state, row edit action button
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

- [x] 9. Upsert form modal
  - [x] 9.1 Create `frontend/src/market-prices/UpsertFormModal.tsx` — modal with Esc to close, form fields (period select, value numeric input, status dropdown, change_reason, source_note, force_update checkbox), force_update confirmation dialog with change_reason required validation, field-level inline errors from backend error codes, submit button disabled during loading, success → close + toast + refetch, dot decimal separator for API value
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10_
  - [ ]* 9.2 Write property tests in `frontend/src/market-prices/__tests__/validation.property.test.ts`
    - **Property 7: Force Update Requires Change Reason** — for any form state with force_update=true, empty/whitespace change_reason blocks submission
    - **Property 9: Decimal Serialization** — for any numeric value, serialized request body uses dot separator
    - **Validates: Requirements 5.3, 5.10**
  - [ ]* 9.3 Write property tests in `frontend/src/market-prices/__tests__/errorMapping.property.test.ts`
    - **Property 8: Error Code to Field Routing** — for any known error_code with field mapping, parseFieldErrors returns correct field; for codes without field, error routes to global toast
    - **Validates: Requirements 5.7, 8.2, 8.3, 8.4**

- [x] 10. Bulk import wizard
  - [x] 10.1 Create `frontend/src/market-prices/BulkImportWizard.tsx` — 3-step wizard (upload → preview → result), file input for CSV/JSON, force_update checkbox, preview summary counts display, per-row error list with row/field/message, final_conflicts warning, "Uygula" button with double-submit guard, result summary (imported/skipped/error counts), failed rows download as CSV/JSON, success → refetch price list
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2, 7.3, 7.4, 7.5_
  - [ ]* 10.2 Write property tests in `frontend/src/market-prices/__tests__/bulkImport.property.test.ts`
    - **Property 10: Preview Summary Completeness** — for any valid BulkImportPreviewResponse, all summary fields and error details are present in rendered output
    - **Property 11: Failed Rows Export Round-Trip** — for any non-empty BulkImportError list, CSV export and parse-back preserves all data
    - **Property 12: Apply Result Summary Completeness** — for any valid BulkImportApplyResponse, all result fields are present
    - **Validates: Requirements 6.2, 6.3, 7.3, 7.4**

- [x] 11. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. MarketPricesTab orkestratör ve AdminPanel entegrasyonu
  - [x] 12.1 Create `frontend/src/market-prices/MarketPricesTab.tsx` — compose PriceFilters, PriceListTable, UpsertFormModal, BulkImportWizard, ToastNotification. Wire useUrlState, useMarketPricesList, useUpsertMarketPrice hooks. Manage modal open/close state, toast state, debounced filter → API flow with AbortController cancellation.
    - _Requirements: 10.4, 10.5, 3.5, 11.5_
  - [x] 12.2 Update `frontend/src/AdminPanel.tsx` — remove inline MarketPricesTab function (lines ~410-616), import new MarketPricesTab from `./market-prices/MarketPricesTab`, replace usage in render. Remove old market price state variables (marketPrices, loadingPrices, showAddForm, etc.) and handler functions (handleAddPrice, handleLock, handleUnlock) that are no longer needed. Keep all other tabs untouched.
    - _Requirements: 10.1, 10.2, 10.3_
  - [x] 12.3 Update `frontend/src/api.ts` — keep old getMarketPrices, upsertMarketPrice, lockMarketPrice, unlockMarketPrice functions for backward compatibility but add deprecation comments. No functional changes needed since new API client is in marketPricesApi.ts.
    - _Requirements: 9.1_

- [x] 13. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties (fast-check, min 100 iterations)
- Unit tests validate specific examples and edge cases (vitest + RTL)
- Mevcut AdminPanel.tsx'deki diğer sekmeler (distribution-tariffs, tariff-lookup, incidents) hiç değiştirilmez
- Eski api.ts fonksiyonları backward compat için korunur
