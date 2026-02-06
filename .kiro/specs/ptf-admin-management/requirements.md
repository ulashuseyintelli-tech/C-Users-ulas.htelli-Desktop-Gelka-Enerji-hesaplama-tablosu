# Requirements Document

## Introduction

Bu özellik, EPİAŞ PTF (Piyasa Takas Fiyatı) verilerinin Admin Panel üzerinden yönetilmesini sağlar. Mevcut `MarketReferencePrice` tablosuna `status`, `captured_at`, `price_type` ve audit alanları eklenerek veri kalitesi, genişletilebilirlik ve izlenebilirlik artırılacaktır. Admin kullanıcıları tekil veya toplu (bulk) veri girişi yapabilecek, provisional/final durumlarını yönetebilecektir.

## Glossary

- **PTF_Admin_Service**: PTF verilerinin CRUD operasyonlarını yöneten servis katmanı
- **PTF_Validator**: Period formatı ve değer aralıklarını doğrulayan bileşen
- **Bulk_Importer**: CSV/JSON formatında toplu veri yüklemesini işleyen bileşen
- **Status**: Verinin kesinlik durumu (provisional: geçici, final: kesinleşmiş)
- **Period**: YYYY-MM formatında ay dönemi
- **PTF**: Piyasa Takas Fiyatı (TL/MWh)
- **Price_Type**: Fiyat serisi tipi (PTF, SMF, YEKDEM vb. - gelecek genişleme için)
- **Captured_At**: Verinin EPİAŞ'tan alındığı tarih (UTC)
- **Source**: Verinin kaynağı (epias_manual: manuel giriş, epias_api: API'den çekildi, migration: backfill, seed: ilk yükleme)
- **Force_Update**: Final kayıtları güncellemek için gereken özel yetki parametresi

## Requirements

### Requirement 1: Veritabanı Şema Genişletmesi

**User Story:** As a system administrator, I want the database schema to support status, captured_at, price_type and audit fields, so that I can track data quality, enable future extensibility, and maintain audit trail.

#### Acceptance Criteria

1. THE PTF_Admin_Service SHALL store a status field with values "provisional" or "final" for each market price record
2. THE PTF_Admin_Service SHALL store a captured_at timestamp (UTC, NOT NULL) indicating when the data was retrieved from EPİAŞ
3. THE PTF_Admin_Service SHALL store a price_type field defaulting to "PTF" for future extensibility (SMF, YEKDEM vb.)
4. THE PTF_Admin_Service SHALL enforce unique constraint on (price_type, period) combination
5. WHEN a new record is created without explicit status THEN THE PTF_Admin_Service SHALL default status to "provisional"
6. THE PTF_Admin_Service SHALL maintain backward compatibility with existing records by treating null status as "final"
7. THE PTF_Admin_Service SHALL store updated_by field to track which user made the last modification
8. THE PTF_Admin_Service SHALL store change_reason field (optional) for audit purposes when admin edits a record
9. THE PTF_Admin_Service SHALL store source field with values: epias_manual, epias_api, migration, seed
10. THE PTF_Admin_Service SHALL store ptf_tl_per_mwh as DECIMAL(12,2) with explicit unit TL/MWh

### Requirement 2: Tekil PTF Veri Girişi

**User Story:** As an admin user, I want to enter individual PTF values with period and status, so that I can manually update market prices.

#### Acceptance Criteria

1. WHEN an admin submits a PTF entry with period, ptf_value, and status THEN THE PTF_Admin_Service SHALL validate and store the record
2. WHEN the period already exists THEN THE PTF_Admin_Service SHALL perform an upsert operation updating the existing record
3. WHEN a locked period is updated THEN THE PTF_Admin_Service SHALL reject the update with an appropriate error message
4. WHEN a final status record is updated without force_update flag THEN THE PTF_Admin_Service SHALL reject the update with a warning
5. WHEN force_update is true THEN THE PTF_Admin_Service SHALL allow updating final status records
6. THE PTF_Admin_Service SHALL record the captured_at timestamp as the current UTC time when manually entered
7. THE PTF_Admin_Service SHALL require updated_by field for all modifications

### Requirement 3: Period ve Değer Validasyonu

**User Story:** As an admin user, I want input validation with clear feedback, so that I can enter correct data.

#### Acceptance Criteria

1. THE PTF_Validator SHALL validate period format against regex pattern ^\d{4}-(0[1-9]|1[0-2])$
2. THE PTF_Validator SHALL reject ptf_value that is zero or negative
3. THE PTF_Validator SHALL reject ptf_value greater than 100000 TL/MWh as data entry error protection
4. WHEN ptf_value is outside 1000-5000 TL/MWh range THEN THE PTF_Validator SHALL return a warning but allow the entry
5. IF period format is invalid THEN THE PTF_Validator SHALL return a descriptive error message
6. THE PTF_Validator SHALL validate status value is either "provisional" or "final"
7. THE PTF_Validator SHALL reject future periods (periods after current month) with clear error message

### Requirement 4: PTF Veri Listeleme ve Düzenleme

**User Story:** As an admin user, I want to view and edit existing PTF records, so that I can manage market price data.

#### Acceptance Criteria

1. THE PTF_Admin_Service SHALL return a paginated list of PTF records with configurable page size
2. THE PTF_Admin_Service SHALL support sorting by period (default: descending), ptf_value, status, or updated_at
3. WHEN listing records THE PTF_Admin_Service SHALL include period, ptf_value, status, captured_at, is_locked, updated_by, and updated_at fields
4. WHEN an admin edits a record THEN THE PTF_Admin_Service SHALL update the record and set updated_at to current UTC time
5. THE PTF_Admin_Service SHALL support filtering by status (provisional/final), date range (from_period/to_period), and source

### Requirement 5: Bulk Import (CSV/JSON)

**User Story:** As an admin user, I want to import multiple PTF records from CSV or JSON files, so that I can efficiently load historical data.

#### Acceptance Criteria

1. WHEN a CSV file is uploaded THEN THE Bulk_Importer SHALL parse rows with columns: period, ptf_value, status
2. WHEN a JSON file is uploaded THEN THE Bulk_Importer SHALL parse an array of objects with period, ptf_value, status fields
3. THE Bulk_Importer SHALL validate each row and collect all validation errors with row numbers
4. THE Bulk_Importer SHALL use row-level accept/reject strategy by default (valid rows imported, invalid rows skipped)
5. WHEN strict_mode is enabled THEN THE Bulk_Importer SHALL reject the entire batch if any row fails validation
6. THE Bulk_Importer SHALL perform upsert for each valid record in the batch
7. THE Bulk_Importer SHALL return a detailed report with success count, error count, and per-row error reasons
8. WHEN importing over final status records THEN THE Bulk_Importer SHALL require force_update flag or skip those rows

### Requirement 6: Import Preview

**User Story:** As an admin user, I want to preview import results before committing, so that I can verify the data before it's saved.

#### Acceptance Criteria

1. WHEN preview is requested THEN THE Bulk_Importer SHALL return counts of new records, updates, and unchanged records
2. THE Bulk_Importer SHALL identify which existing records will be modified and show before/after values
3. WHEN preview shows conflicts THEN THE Bulk_Importer SHALL highlight locked periods that cannot be updated
4. THE Bulk_Importer SHALL allow the admin to confirm or cancel the import after preview

### Requirement 7: Hesaplama İçin Status Önceliği

**User Story:** As a calculation engine, I want deterministic rules for PTF lookup with clear fallback behavior, so that calculations are consistent and traceable.

#### Acceptance Criteria

1. WHEN retrieving PTF for calculation THEN THE PTF_Admin_Service SHALL return final status record if available for the requested period
2. WHEN no final record exists for the period THEN THE PTF_Admin_Service SHALL return provisional status record if available
3. WHEN provisional data is used THEN THE PTF_Admin_Service SHALL set is_provisional_used flag to true in the response
4. THE PTF_Admin_Service SHALL log when provisional data is used for calculations with period and context
5. WHEN requested period does not exist (neither final nor provisional) THEN THE PTF_Admin_Service SHALL return error (not fallback to nearest period)
6. WHEN a future period is requested (after current month) THEN THE PTF_Admin_Service SHALL return error with clear message
7. THE PTF_Admin_Service SHALL never silently use a different period than requested

### Requirement 8: Seed Data Yükleme

**User Story:** As a system administrator, I want to load historical EPİAŞ PTF data, so that the system has accurate reference prices.

#### Acceptance Criteria

1. THE PTF_Admin_Service SHALL support loading seed data for periods 2024-01 through 2026-02
2. WHEN loading seed data THEN THE PTF_Admin_Service SHALL use the provided EPİAŞ values with appropriate status
3. THE PTF_Admin_Service SHALL mark 2026-02 as provisional (month not complete) and all prior complete months as final
4. THE Bulk_Importer SHALL parse and validate seed data in the same format as regular imports
5. THE PTF_Admin_Service SHALL set captured_at to the seed data load timestamp for all seed records


### Requirement 9: Veri Formatı ve Hassasiyet

**User Story:** As a system administrator, I want clear data format standards, so that data entry and parsing is consistent.

#### Acceptance Criteria

1. THE PTF_Admin_Service SHALL store ptf_value as DECIMAL(12,2) with 2 decimal precision
2. THE PTF_Admin_Service SHALL use TL/MWh as the explicit unit for all PTF values
3. THE Bulk_Importer SHALL parse numeric values using dot (.) as decimal separator
4. THE Bulk_Importer SHALL reject values with comma (,) as decimal separator with clear error message
5. THE PTF_Admin_Service SHALL not use thousand separators in stored or exported values

### Requirement 10: Status Transition Kuralları

**User Story:** As an admin user, I want clear rules for status transitions, so that data integrity is maintained.

#### Acceptance Criteria

1. WHEN updating a provisional record to final THEN THE PTF_Admin_Service SHALL allow the transition without force_update (upgrade allowed)
2. WHEN updating a final record to provisional THEN THE PTF_Admin_Service SHALL reject the transition (downgrade not allowed)
3. WHEN updating a final record to final with different value THEN THE PTF_Admin_Service SHALL require force_update flag
4. THE PTF_Admin_Service SHALL log all status transitions with previous and new status values
