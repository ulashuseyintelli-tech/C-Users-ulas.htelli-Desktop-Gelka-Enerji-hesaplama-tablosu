# Requirements Document

## Introduction

Bu özellik, mevcut AdminPanel.tsx monolitindeki market-prices sekmesini tamamen yeniden yazarak, backend PTF Admin Management API'sine uyumlu modern bir React frontend oluşturur. Kapsam: PTF fiyat listesi (pagination + filtre + URL state), tekil upsert formu, toplu import (preview → apply) akışı, hata/uyarı UX kontratı. Mevcut diğer sekmeler (distribution-tariffs, tariff-lookup, incidents) değiştirilmez.

## Glossary

- **MarketPricesTab**: PTF fiyat yönetimi ana bileşeni; AdminPanel içindeki market-prices sekmesinin yerine geçer
- **PriceListTable**: Sayfalanmış, sıralanabilir PTF fiyat tablosu bileşeni
- **PriceFilters**: Durum, dönem aralığı filtre bileşeni; URL query parametreleriyle senkronize çalışır
- **UpsertForm**: Tekil PTF kaydı oluşturma/güncelleme modal formu
- **BulkImportWizard**: CSV/JSON dosya yükleme, önizleme ve uygulama adımlarından oluşan toplu import bileşeni
- **StatusBadge**: provisional ("Ön Değer", amber) ve final ("Kesinleşmiş", yeşil) durumlarını gösteren etiket bileşeni
- **ToastNotification**: Başarı, bilgi, uyarı ve hata mesajlarını gösteren global bildirim bileşeni
- **SkeletonLoader**: Veri yüklenirken shimmer efektiyle gösterilen iskelet UI bileşeni
- **API_Client**: Backend PTF Admin API'sine axios tabanlı istek gönderen servis katmanı
- **URL_State_Manager**: Filtre ve sayfalama parametrelerini URL query string ile senkronize eden yardımcı modül

## Requirements

### Requirement 1: PTF Fiyat Listesi Görüntüleme

**User Story:** As an admin user, I want to view PTF market prices in a paginated, filterable table, so that I can browse and manage price records efficiently.

#### Acceptance Criteria

1. WHEN the MarketPricesTab loads, THE PriceListTable SHALL fetch PTF records from GET /admin/market-prices with default parameters (page=1, page_size=20, sort_by="period", sort_order="desc", price_type="PTF")
2. THE PriceListTable SHALL display columns: period, ptf_tl_per_mwh (formatted in Turkish locale as "2.508,80"), status badge, captured_at/updated_at (Europe/Istanbul timezone), source, updated_by, change_reason
3. WHEN data is loading, THE PriceListTable SHALL display a skeleton shimmer UI with the same column layout instead of a spinner
4. WHEN no records match the current filters, THE PriceListTable SHALL display an empty state message with a "Filtreleri Temizle" call-to-action button
5. WHEN the user clicks a table column header, THE PriceListTable SHALL toggle sort order for that column and re-fetch data from the API
6. THE PriceListTable SHALL display pagination controls showing current page, total pages, and page navigation buttons
7. WHEN the user changes page or page size, THE PriceListTable SHALL fetch the corresponding page from the API

### Requirement 2: URL State Senkronizasyonu

**User Story:** As an admin user, I want filters and pagination state reflected in the URL, so that I can share and bookmark specific views.

#### Acceptance Criteria

1. WHEN the user changes any filter or pagination parameter, THE URL_State_Manager SHALL update the browser URL query string to reflect the current state
2. WHEN the page loads with query parameters in the URL, THE URL_State_Manager SHALL parse those parameters and apply them as initial filter and pagination values
3. THE URL_State_Manager SHALL serialize filter parameters (status, from_period, to_period) and pagination parameters (page, page_size, sort_by, sort_order) to URL query string
4. THE URL_State_Manager SHALL deserialize URL query string back to typed filter and pagination objects
5. WHEN the user clicks the browser back/forward buttons, THE URL_State_Manager SHALL update the UI state to match the URL

### Requirement 3: Filtre Bileşeni

**User Story:** As an admin user, I want to filter PTF records by status and period range, so that I can find specific records quickly.

#### Acceptance Criteria

1. THE PriceFilters SHALL provide a status dropdown with options: "Tümü" (no filter), "Ön Değer" (provisional), "Kesinleşmiş" (final)
2. THE PriceFilters SHALL provide from_period and to_period inputs in YYYY-MM format for date range filtering
3. WHEN the user changes any filter value, THE PriceFilters SHALL debounce the change by 300ms before triggering a data fetch
4. WHEN the user changes a filter, THE PriceFilters SHALL reset pagination to page 1
5. WHEN a filter-triggered API request is in flight and a new filter change occurs, THE API_Client SHALL cancel the previous request using AbortController before sending the new one

### Requirement 4: Durum Rozeti (Status Badge)

**User Story:** As an admin user, I want to see clear visual indicators for record status, so that I can quickly distinguish provisional from final records.

#### Acceptance Criteria

1. WHEN status is "provisional", THE StatusBadge SHALL display "Ön Değer" text with amber/yellow background using Tailwind color tokens (bg-amber-100 text-amber-700)
2. WHEN status is "final", THE StatusBadge SHALL display "Kesinleşmiş" text with green background using Tailwind color tokens (bg-green-100 text-green-700)

### Requirement 5: Tekil PTF Kaydı Oluşturma/Güncelleme (Upsert)

**User Story:** As an admin user, I want to create or update individual PTF records through a form, so that I can manually manage market prices.

#### Acceptance Criteria

1. WHEN the user clicks "Yeni Kayıt" or a row's edit action, THE UpsertForm SHALL open as a modal dialog
2. THE UpsertForm SHALL contain fields: period (YYYY-MM select), value (numeric input with dot separator), status (provisional/final dropdown), change_reason (text input), source_note (text input), force_update (checkbox)
3. WHEN the user enables force_update, THE UpsertForm SHALL display a confirmation dialog asking "Emin misiniz?" and require change_reason to be non-empty before proceeding
4. WHEN the form is submitted, THE API_Client SHALL send a POST request to /admin/market-prices with JSON body containing period, value, price_type ("PTF"), status, source_note, change_reason, and force_update
5. WHILE a submission is in progress, THE UpsertForm SHALL disable the submit button to prevent double submission
6. WHEN the backend returns a success response, THE UpsertForm SHALL close the modal, show a success toast, and refresh the price list
7. WHEN the backend returns a validation error, THE UpsertForm SHALL display the error message inline next to the relevant field based on the error's "field" property
8. WHEN the backend returns a warning in the success response, THE UpsertForm SHALL display the warning in a toast notification
9. WHEN the user presses Escape, THE UpsertForm SHALL close the modal dialog
10. THE UpsertForm SHALL send the value field using dot (.) as decimal separator to the API, regardless of display format

### Requirement 6: Toplu Import Önizleme

**User Story:** As an admin user, I want to preview bulk import results before applying, so that I can verify data quality and catch errors.

#### Acceptance Criteria

1. WHEN the user selects a CSV or JSON file, THE BulkImportWizard SHALL upload it to POST /admin/market-prices/import/preview as multipart/form-data with price_type and force_update parameters
2. WHEN the preview response is received, THE BulkImportWizard SHALL display summary counts: total_rows, valid_rows, invalid_rows, new_records, updates, unchanged, final_conflicts
3. WHEN the preview contains errors, THE BulkImportWizard SHALL display each error with row number, field name, and error message
4. WHEN the preview contains final_conflicts greater than zero, THE BulkImportWizard SHALL display a warning about protected final records
5. THE BulkImportWizard SHALL display parsed row values with will_insert, will_update, or will_skip indicators for each row

### Requirement 7: Toplu Import Uygulama

**User Story:** As an admin user, I want to apply a previewed bulk import, so that I can efficiently load multiple PTF records.

#### Acceptance Criteria

1. WHEN the user clicks "Uygula" after preview, THE BulkImportWizard SHALL send the file to POST /admin/market-prices/import/apply with price_type, force_update, and strict_mode parameters
2. WHILE the apply request is in progress, THE BulkImportWizard SHALL disable the "Uygula" button to prevent double submission
3. WHEN the apply response is received, THE BulkImportWizard SHALL display result summary: imported_count, skipped_count, error_count
4. WHEN the apply result contains failed rows, THE BulkImportWizard SHALL provide a download button to export failed rows as CSV or JSON
5. WHEN the apply is successful, THE BulkImportWizard SHALL refresh the price list table

### Requirement 8: Hata ve Uyarı UX Kontratı

**User Story:** As an admin user, I want clear and consistent error/warning feedback, so that I can understand and resolve issues.

#### Acceptance Criteria

1. THE ToastNotification SHALL support four message types: success (green), info (blue), warning (amber), error (red)
2. WHEN the backend returns an error response, THE API_Client SHALL extract error_code and message fields and pass them to the appropriate UI component
3. WHEN a backend error has a "field" property, THE UpsertForm SHALL display the error inline next to that specific field
4. WHEN a backend error has no "field" property, THE ToastNotification SHALL display the error as a global toast message
5. THE ToastNotification SHALL display backend error_code values verbatim in a small monospace font for debugging purposes

### Requirement 9: API İstemci Katmanı

**User Story:** As a developer, I want a clean API client layer for the new PTF endpoints, so that I can maintain consistent API communication.

#### Acceptance Criteria

1. THE API_Client SHALL provide functions: listMarketPrices, upsertMarketPrice, previewBulkImport, applyBulkImport matching the new backend JSON API contracts
2. THE API_Client SHALL use JSON Content-Type for upsert requests instead of the deprecated multipart/form-data format
3. THE API_Client SHALL support AbortController for cancelling in-flight requests
4. THE API_Client SHALL include the X-Admin-Key header on all admin API requests using the existing adminApi axios instance
5. THE API_Client SHALL define TypeScript interfaces for all request and response types matching the backend API contracts

### Requirement 10: Bileşen Mimarisi ve Monolith Ayrıştırma

**User Story:** As a developer, I want the market-prices tab extracted from the AdminPanel monolith into isolated components, so that the codebase is maintainable.

#### Acceptance Criteria

1. THE MarketPricesTab SHALL be implemented as a standalone component tree imported by AdminPanel.tsx
2. WHEN the MarketPricesTab is integrated into AdminPanel, THE AdminPanel SHALL replace the existing inline MarketPricesTab function with the new component import
3. THE MarketPricesTab SHALL not modify or affect the distribution-tariffs, tariff-lookup, or incidents tabs in AdminPanel.tsx
4. THE MarketPricesTab SHALL use custom hooks (useMarketPricesList, useUpsertMarketPrice, useBulkImportPreview, useBulkImportApply) for data fetching and state management
5. THE MarketPricesTab SHALL compose sub-components: PriceListTable, PriceFilters, UpsertForm, BulkImportWizard

### Requirement 11: Operasyonel Gereksinimler

**User Story:** As a developer, I want consistent non-functional behavior across all mutation operations, so that the admin UI is reliable and debuggable.

#### Acceptance Criteria

1. THE API_Client SHALL not use optimistic UI updates for any mutation operation
2. WHILE a mutation request (upsert or bulk import apply) is in progress, THE UpsertForm and BulkImportWizard SHALL disable all submit buttons to prevent double submission
3. THE MarketPricesTab SHALL log import apply and upsert submit events to console for operational debugging
4. WHEN filter inputs change, THE PriceFilters SHALL debounce API calls by 300ms
5. WHEN a new API request is triggered while a previous request is still pending, THE API_Client SHALL cancel the previous request using AbortController
