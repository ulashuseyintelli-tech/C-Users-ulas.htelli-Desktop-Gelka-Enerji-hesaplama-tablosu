# Implementation Plan: Audit History

## Overview

Mevcut `MarketPriceAdminService` içine history write entegrasyonu, yeni SQLAlchemy model, REST endpoint ve frontend history panel eklenmesi. Backend-first yaklaşım: model → service → API → frontend.

## Tasks

- [x] 1. PriceChangeHistory SQLAlchemy model oluştur
  - [x] 1.1 `backend/app/database.py` dosyasına `PriceChangeHistory` model class ekle
    - Columns: id, price_record_id (FK), price_type, period, action, old_value, new_value, old_status, new_status, change_reason, updated_by, source, created_at
    - `price_record_id` → `market_reference_prices.id` foreign key (ON DELETE RESTRICT)
    - `init_db()` zaten `Base.metadata.create_all()` kullandığı için tablo otomatik oluşur
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 2. Service layer — history write ve query
  - [x] 2.1 `MarketPriceAdminService` içine `_write_history()` private method ekle
    - Append-only write, try/except ile sarılı, hata durumunda log + silent fail
    - Parameters: db, record, action, old_value, new_value, old_status, new_status, change_reason, updated_by, source
    - _Requirements: 1.1, 1.2, 1.5_

  - [x] 2.2 `_handle_insert()` metoduna history write entegre et
    - `db.commit()` ve `db.refresh()` sonrası, return öncesi `_write_history()` çağır
    - action="INSERT", old_value=None, old_status=None
    - _Requirements: 1.1_

  - [x] 2.3 `_handle_update()` metoduna history write entegre et
    - Commit sonrası başarılı path'te `_write_history()` çağır
    - action="UPDATE", old_value ve old_status commit öncesi yakalanmalı (zaten mevcut: `old_value`, `old_status` local variables)
    - No-op path'te (erken return) history yazılmaz — mevcut kod yapısı bunu zaten sağlıyor
    - _Requirements: 1.2, 1.3_

  - [x] 2.4 `get_history()` public method ekle
    - period + price_type ile sorgula, `created_at` DESC sırala
    - Önce `market_reference_prices` tablosunda kayıt var mı kontrol et → yoksa None döndür (→ 404)
    - Kayıt var ama history yok → boş liste döndür (→ 200)
    - _Requirements: 3.1, 3.3, 3.4_

  - [x] 2.5 Property test: Upsert history write correctness
    - **Property 1: Upsert history write correctness**
    - **Validates: Requirements 1.1, 1.2**
    - Hypothesis ile random valid inputs generate et, upsert yap, history kaydını doğrula

  - [x] 2.6 Property test: No-op produces no history
    - **Property 2: No-op produces no history**
    - **Validates: Requirements 1.3**
    - Hypothesis ile random existing records generate et, aynı value+status ile update yap, history count değişmediğini doğrula

- [x] 3. Checkpoint — Backend service tests
  - 183/183 tests passed (including 160 existing + 23 new audit history tests)

- [x] 4. History API endpoint
  - [x] 4.1 `backend/app/main.py` dosyasına `GET /admin/market-prices/history` endpoint ekle
    - Query params: `period` (required), `price_type` (default "PTF")
    - `require_admin_key` dependency
    - `get_history()` → None ise 404, değilse 200 with history array
    - Response format: `{ status: "ok", period, price_type, history: [...] }`
    - Period format validation (YYYY-MM regex)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 4.2 Unit tests: History API endpoint
    - 404 when record doesn't exist
    - 200 with empty history when record exists but no changes
    - 200 with history data ordered descending
    - 400 invalid period format
    - Default price_type="PTF" when omitted
    - Response field completeness check
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 4.3 Property test: History ordering invariant — covered in test_audit_history_properties.py

- [x] 5. Checkpoint — Backend API tests
  - 183/183 all market price tests passed

- [x] 6. Frontend — types, API client, hook
  - [x] 6.1 `frontend/src/market-prices/types.ts` dosyasına `AuditHistoryEntry` ve `AuditHistoryResponse` interface ekle
    - _Requirements: 4.2_

  - [x] 6.2 `frontend/src/market-prices/marketPricesApi.ts` dosyasına `fetchHistory()` fonksiyonu ekle
    - `adminApi.get('/admin/market-prices/history', { params: { period, price_type }, signal })`
    - _Requirements: 3.1_

  - [x] 6.3 `frontend/src/market-prices/hooks/useAuditHistory.ts` hook oluştur
    - `useMarketPricesList` pattern'ini takip et: useState + useEffect + AbortController
    - `period` null ise fetch yapma
    - Returns: `{ history, loading, error, refetch }`
    - _Requirements: 4.1, 4.3_

- [x] 7. Frontend — HistoryPanel component
  - [x] 7.1 `frontend/src/market-prices/HistoryPanel.tsx` component oluştur
    - Modal with backdrop close + Esc close
    - Her entry: action badge (INSERT=yeşil, UPDATE=mavi), old→new value, status değişikliği, change_reason, updated_by, timestamp
    - Loading state: skeleton rows
    - Error state: "Geçmiş yüklenemedi" + "Tekrar Dene" butonu
    - Empty state: "Bu kayıt için değişiklik geçmişi bulunmamaktadır"
    - All labels Turkish
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [x] 8. Frontend — PriceListTable ve MarketPricesTab entegrasyonu
  - [x] 8.1 `PriceListTable` — "Geçmiş" butonu ekle
    - `renderCell()` fonksiyonundaki action sütununa "Geçmiş" butonu ekle ("Düzenle" yanına)
    - `PriceListTableProps`'a `onHistory?: (record: MarketPriceRecord) => void` callback ekle
    - _Requirements: 4.1_

  - [x] 8.2 `MarketPricesTab` — History panel state yönetimi ekle
    - `historyModal` state: `{ open: boolean; period: string | null }`
    - `handleHistory` callback → `setHistoryModal({ open: true, period: record.period })`
    - `HistoryPanel` component'ini wire et
    - _Requirements: 4.1_

- [x] 9. Final checkpoint — Tüm testler geçiyor mu
  - Backend: 183/183 passed (market price tests)
  - Frontend: 188/188 passed (all existing tests, zero regression)
  - Backward compatibility: `onHistory` prop optional, existing PriceListTable usage unaffected
  - Existing `test_audit_trail_properties.py` fixed: `call_args_list[0]` for multi-add compatibility

## Notes

- `bulk_upsert()` ayrı entegrasyon gerektirmez — `upsert_price()` üzerinden çalıştığı için history otomatik yazılır
- History write hata durumunda parent upsert'ü etkilemez (silent fail + log)
- Property tests Hypothesis kütüphanesi ile yazılır (proje zaten kullanıyor)
- Frontend testleri vitest ile yazılır
- Existing test fix: `test_audit_trail_properties.py` — `mock_db.add.call_args` → `mock_db.add.call_args_list[0]` (history write adds second `db.add` call)
