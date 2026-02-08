# Requirements Document

## Introduction

Piyasa fiyat kayıtları (MarketReferencePrice) üzerinde yapılan tüm değişikliklerin (oluşturma ve güncelleme) tarihçesini tutan bir audit history özelliği. Her değişiklik ayrı bir satır olarak `price_change_history` tablosunda saklanır. Kullanıcılar frontend üzerinden belirli bir dönem+fiyat tipi için geçmiş değişiklikleri kronolojik sırayla görüntüleyebilir.

## Glossary

- **History_Service**: `MarketPriceAdminService` içinde geçmiş kaydı yazan iç servis katmanı
- **History_API**: Geçmiş kayıtlarını döndüren REST endpoint (`GET /admin/market-prices/history`)
- **History_Table**: `price_change_history` SQLAlchemy ORM modeli
- **History_Panel**: Frontend'de değişiklik geçmişini gösteren UI bileşeni
- **Price_Record**: `MarketReferencePrice` tablosundaki bir fiyat kaydı
- **Change_Event**: Bir fiyat kaydı üzerinde yapılan tek bir oluşturma veya güncelleme işlemi

## Requirements

### Requirement 1: Değişiklik Geçmişi Kaydetme

**User Story:** As an admin, I want every price record change to be automatically recorded, so that I can trace who changed what and when.

#### Acceptance Criteria

1. WHEN a new Price_Record is created via `_handle_insert()`, THE History_Service SHALL write a Change_Event with `action = "INSERT"`, the new value, new status, and the `updated_by` field
2. WHEN an existing Price_Record is updated via `_handle_update()`, THE History_Service SHALL write a Change_Event with `action = "UPDATE"`, both old and new values, both old and new statuses, and the `change_reason`
3. WHEN a no-op update is detected (same value and same status), THE History_Service SHALL NOT write a Change_Event
4. WHEN `bulk_upsert()` processes multiple rows, THE History_Service SHALL write a Change_Event for each row that results in an actual change, because `bulk_upsert()` calls `upsert_price()` internally
5. IF a database error occurs during history write, THEN THE History_Service SHALL log the error and NOT fail the parent upsert operation

### Requirement 2: Geçmiş Kayıt Veri Modeli

**User Story:** As a developer, I want a well-structured history table, so that audit records are complete and queryable.

#### Acceptance Criteria

1. THE History_Table SHALL be a SQLAlchemy ORM model class named `PriceChangeHistory` registered with `Base`
2. THE History_Table SHALL store: `id`, `price_type`, `period`, `action` (INSERT/UPDATE), `old_value`, `new_value`, `old_status`, `new_status`, `change_reason`, `updated_by`, `source`, `created_at`
3. THE History_Table SHALL use a foreign key relationship to `market_reference_prices.id` via a `price_record_id` column
4. WHEN `init_db()` is called, THE History_Table SHALL be auto-created by `Base.metadata.create_all()`

### Requirement 3: Geçmiş Sorgulama API

**User Story:** As an admin, I want to query the change history for a specific period and price type, so that I can review past modifications.

#### Acceptance Criteria

1. WHEN a `GET /admin/market-prices/history` request is received with `period` and `price_type` query parameters, THE History_API SHALL return all Change_Events for that combination ordered by `created_at` descending
2. WHEN the `price_type` parameter is omitted, THE History_API SHALL default to `"PTF"`
3. WHEN the specified period+price_type combination does not exist in the Price_Record table, THE History_API SHALL return HTTP 404
4. WHEN the specified period+price_type exists but has no history records, THE History_API SHALL return HTTP 200 with an empty `history` array
5. THE History_API SHALL require `X-Admin-Key` header authentication consistent with existing admin endpoints

### Requirement 4: Frontend Geçmiş Görüntüleme

**User Story:** As an admin, I want to view the change history from the price list table, so that I can quickly check what happened to a record.

#### Acceptance Criteria

1. WHEN a user clicks the "Geçmiş" button on a Price_Record row, THE History_Panel SHALL open and display the change history for that record's period and price_type
2. WHEN the History_Panel is open, THE History_Panel SHALL display each Change_Event with: action type, old value, new value, old status, new status, change_reason, updated_by, and timestamp
3. WHEN the history is loading, THE History_Panel SHALL display a loading indicator
4. IF the History_API returns an error, THEN THE History_Panel SHALL display a user-friendly error message
5. WHEN the History_Panel has no history records, THE History_Panel SHALL display "Bu kayıt için değişiklik geçmişi bulunmamaktadır" message
