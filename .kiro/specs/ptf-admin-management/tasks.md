# Implementation Plan: PTF Admin Management

## Overview

EPİAŞ PTF verilerinin Admin Panel üzerinden yönetilmesi için gerekli veritabanı şema değişiklikleri, servis katmanı, API endpoint'leri ve bulk import özelliklerinin implementasyonu.

## Tasks

- [x] 1. Database Migration ve Seed Data
  - [x] 1.1 Alembic migration oluştur: MarketReferencePrice tablosuna yeni alanlar ekle
    - price_type (String, default="PTF", index)
    - status (String, default="provisional")
    - captured_at (DateTime, NOT NULL)
    - change_reason (String, nullable)
    - source (String, default="epias_manual") - epias_manual | epias_api | migration | seed
    - Unique constraint: (price_type, period)
    - ptf_tl_per_mwh tipi: DECIMAL(12,2), birim: TL/MWh
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.7, 1.8, 1.9, 1.10_
  
  - [x] 1.2 Data migration/backfill: Mevcut kayıtları yeni şemaya uyumlu hale getir
    - Mevcut kayıtlar için status belirleme:
      - period == current_period(TR) → status="provisional"
      - period < current_period(TR) → status="final"
    - captured_at yoksa updated_at fallback
    - updated_by yoksa "system_migration"
    - price_type="PTF" default
    - _Requirements: 1.6_
  
  - [x] 1.3 Seed data loader oluştur: 2024-01 → 2026-02 PTF verileri
    - Status kuralı (Europe/Istanbul timezone):
      - period == current_period(TR) → status="provisional"
      - period < current_period(TR) → status="final"
    - 2026-02 provisional (ay devam ediyor), diğerleri final
    - source="seed" for all seed records
    - captured_at: seed run timestamp (UTC)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

- [x] 2. Checkpoint - Migration ve seed data doğrulama
  - Ensure migration runs successfully, seed data is loaded correctly

- [x] 3. MarketPriceValidator Component
  - [x] 3.1 backend/app/market_price_validator.py oluştur
    - ValidationResult dataclass
    - Period regex validation (^\d{4}-(0[1-9]|1[0-2])$)
    - Value bounds validation (<=0 reject, >100000 reject, 1000-5000 warning)
    - Status enum validation (provisional/final)
    - Future period rejection (Europe/Istanbul timezone)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_
  
  - [x] 3.2 Property test: Period format validation
    - **Property 1: Period Format Validation**
    - **Validates: Requirements 3.1, 3.7**
  
  - [x] 3.3 Property test: PTF value bounds validation
    - **Property 2: PTF Value Bounds Validation**
    - **Validates: Requirements 3.2, 3.3, 3.4**
  
  - [x] 3.4 Property test: Status enum validation
    - **Property 3: Status Enum Validation**
    - **Validates: Requirements 3.6**

- [x] 4. MarketPriceAdminService Component
  - [x] 4.1 backend/app/market_price_admin_service.py oluştur
    - MarketPriceEntry, MarketPriceLookupResult dataclasses
    - upsert() method with status transition rules
    - get_for_calculation() method with final > provisional priority
    - list_prices() method with pagination, sorting, filtering
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 4.1, 4.2, 4.3, 4.4, 4.5, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_
  
  - [x] 4.2 Property test: Status transition rules
    - **Property 7: Status Transition Rules**
    - **Validates: Requirements 2.4, 2.5, 10.1, 10.2, 10.3**
  
  - [x] 4.3 Property test: force_update policy
    - final overwrite without force_update → reject
    - provisional→final upgrade without force_update → allow
    - **Validates: Requirements 2.4, 2.5, 10.1, 10.3**
  
  - [x] 4.4 Property test: Calculation lookup priority
    - **Property 8: Calculation Lookup Priority**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.5, 7.6, 7.7**
  
  - [x] 4.5 Property test: Pagination correctness
    - **Property 9: Pagination Correctness**
    - **Validates: Requirements 4.1, 4.2**
  
  - [x] 4.6 Property test: Filter correctness
    - **Property 10: Filter Correctness**
    - **Validates: Requirements 4.5**

- [x] 5. Checkpoint - Service layer doğrulama
  - Ensure all service methods work correctly, ask the user if questions arise

- [x] 6. BulkImporter Component
  - [x] 6.1 backend/app/bulk_importer.py oluştur
    - ImportRow, ImportPreview, ImportResult dataclasses
    - parse_csv() method (dot decimal only)
    - parse_json() method
    - preview() method (new/update/unchanged/final_conflicts counts)
    - apply() method (row-level default, strict_mode option)
    - Result contract: accepted_count, rejected_count, rejected_rows with error details
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 6.1, 6.2, 6.3_
  
  - [x] 6.2 Property test: Decimal parsing
    - **Property 11: Decimal Parsing**
    - **Validates: Requirements 9.3, 9.4, 9.1**
  
  - [x] 6.3 Property test: Bulk import mode behavior
    - **Property 12: Bulk Import Mode Behavior**
    - **Validates: Requirements 5.4, 5.5, 5.7**
  
  - [x] 6.4 Property test: Import preview accuracy
    - **Property 13: Import Preview Accuracy**
    - **Validates: Requirements 6.1, 6.2, 6.3**

- [x] 7. API Endpoints
  - [x] 7.1 GET /admin/market-prices endpoint (list)
    - Query params: page, page_size, sort_by, sort_order, price_type, status, from_period, to_period
    - Pagination response with total count
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_
  
  - [x] 7.2 POST /admin/market-prices endpoint (upsert)
    - JSON body: period, value, price_type, status, source_note, change_reason, force_update
    - Validation + status transition enforcement
    - Error response with standard schema
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_
  
  - [x] 7.3 POST /admin/market-prices/import/preview endpoint
    - Multipart form: file, price_type, force_update
    - Preview response with counts and errors
    - _Requirements: 6.1, 6.2, 6.3, 6.4_
  
  - [x] 7.4 POST /admin/market-prices/import/apply endpoint
    - Multipart form: file, price_type, force_update, strict_mode
    - Import result: accepted_count, rejected_count, rejected_rows (row_index, error_code, field, message)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_
  
  - [x] 7.5 GET /api/market-prices/{price_type}/{period} endpoint (calculation lookup)
    - Path params: price_type (PTF, SMF, etc.), period (YYYY-MM)
    - Calculation lookup with is_provisional_used flag
    - Future-proof: price_type in path for SMF/YEKDEM support
    - _Requirements: 7.1, 7.2, 7.3, 7.5, 7.6, 7.7_

- [x] 8. Checkpoint - API endpoints doğrulama
  - Ensure all endpoints work correctly, ask the user if questions arise

- [x] 9. Integration ve Backward Compatibility
  - [x] 9.1 Mevcut market_prices.py fonksiyonlarını güncelle
    - get_market_prices() → status field desteği
    - upsert_market_prices() → yeni alanlar desteği
    - Backward compatibility: null status = final
    - _Requirements: 1.6_
  
  - [x] 9.2 Deprecation: Mevcut /admin/market-prices endpoint'lerini yeni yapıya yönlendir
    - Mevcut POST /admin/market-prices (Form-based) → yeni JSON-based endpoint'e alias
    - Mevcut GET /admin/market-prices → yeni pagination destekli endpoint
    - Deprecation headers: Deprecation, Sunset (veya doc notu)
    - Metric: alias_usage_total (eski endpoint kullanım sayısı)
    - Plan: 2 release sonra kaldırılır
    - _Requirements: 1.6_
  
  - [x] 9.3 Property test: Audit trail completeness
    - **Property 14: Audit Trail Completeness**
    - **Validates: Requirements 1.7, 1.8, 2.7, 4.4**

- [x] 10. Observability (Opsiyonel)
  - [x] 10.1 Metrics ekle
    - import_apply_duration_seconds
    - import_rows_total{outcome=accepted|rejected}
    - upsert_total{status=provisional|final}
    - lookup_total{hit=true|false, status=provisional|final}

- [x] 11. Final Checkpoint
  - Ensure all tests pass, ask the user if questions arise

## Notes

- Property-based testler (hypothesis) zorunlu - tasarımın bel kemiği
- Tasks marked with `*` are optional observability enhancements
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- API Endpoints:
  - GET /admin/market-prices (list with pagination)
  - POST /admin/market-prices (upsert)
  - POST /admin/market-prices/import/preview
  - POST /admin/market-prices/import/apply
  - GET /api/market-prices/{price_type}/{period} (calculation lookup)
KDVYİ YANLIŞ HESAPLIYORSUN
