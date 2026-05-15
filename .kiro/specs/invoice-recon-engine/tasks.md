# Implementation Plan: Invoice Reconciliation Engine (Phase 1)

## Overview

Fatura Mutabakat Motoru — saatlik tüketim Excel dosyalarını parse ederek fatura doğrulaması ve PTF/YEKDEM bazlı maliyet karşılaştırması yapan modüler backend pipeline. Modül yapısı: `backend/app/recon/` altında bağımsız fonksiyonlar (parser → splitter → classifier → reconciler → cost_engine → comparator → report_builder) ve FastAPI router.

**Dil:** Python (FastAPI + Pydantic + openpyxl + Hypothesis)
**Canonical SoT:** `hourly_market_prices` (PTF), `monthly_yekdem_prices` (YEKDEM)
**Yasak importlar:** `market_reference_prices`, `app.invoice.validation`

## Tasks

- [ ] 1. Backend recon schemas (Pydantic models, enums)
  - [ ] 1.1 Create `backend/app/recon/__init__.py` and `backend/app/recon/schemas.py`
    - Create the `backend/app/recon/` package directory with `__init__.py`
    - Implement all Pydantic models and enums from the design: `ExcelFormat`, `Severity`, `ReconciliationStatus`, `HourlyRecord`, `ParseError`, `ParseResult`, `PeriodStats`, `TimeZoneSummary`, `InvoiceInput`, `ToleranceConfig`, `ComparisonConfig`, `ReconRequest`, `ReconciliationItem`, `PtfCostResult`, `YekdemCostResult`, `CostComparison`, `PeriodResult`, `ReconReport`, `ErrorResponse`
    - Ensure `multiplier` field on `HourlyRecord` is `Optional[float] = None` (metadata only, never applied)
    - Add Field validators: `hour` in [0,23], `pct_tolerance >= 0`, `gelka_margin_multiplier >= 1.0`
    - _Requirements: 1.4, 1.5, 2.7, 5.1, 5.5, 6.3, 6.4, 6.5, 8.4, 9.1, 9.2, 10.7_

  - [ ]* 1.2 Write property tests for schema invariants
    - **Property 5: Parse statistics invariant** — total_rows == successful_rows + failed_rows
    - **Property 6: Parsed hour range invariant** — hour in [0, 23]
    - **Validates: Requirements 2.6, 2.7**

- [ ] 2. Excel parser (format detection, date/value parsing, Format A + Format B)
  - [ ] 2.1 Implement `backend/app/recon/parser.py` — format detection
    - Implement `detect_format(workbook)` scanning first 10 rows for column headers
    - Format A detection: "Profil Tarihi" AND "Tüketim (Çekiş)" columns present
    - Format B detection: "Tarih" AND "Aktif Çekiş" columns present
    - Raise `UnknownFormatError` with descriptive message listing expected columns
    - Handle multi-sheet workbooks: use first sheet or auto-detect sheet with consumption data
    - Skip empty rows and pre-header metadata rows
    - _Requirements: 1.2, 1.3, 1.6, 1.7_

  - [ ] 2.2 Implement date and value parsing functions in `parser.py`
    - `parse_datetime(value)`: handle DD/MM/YYYY HH:MM:SS strings AND native Excel datetime objects
    - `parse_kwh_value(value)`: handle Turkish locale (dot thousands separator, comma decimal separator, e.g. "1.234,56" → 1234.56)
    - Validate hour in [0, 23] range after parsing
    - Return `ParseError` for unparseable values (don't crash)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ] 2.3 Implement Format A and Format B parsers
    - `parse_format_a(sheet)`: extract "Profil Tarihi", "Tüketim (Çekiş)", "Çarpan" (metadata only — NEVER apply to values)
    - `parse_format_b(sheet)`: extract "Tarih", "Aktif Çekiş"
    - `parse_excel(file_bytes)`: main entry point — detect format, dispatch to correct parser, return `ParseResult`
    - Handle negative consumption: use absolute value, add warning
    - Track parse statistics: total_rows, successful_rows, failed_rows
    - Enforce 50 MB file size limit
    - Handle empty file: return error "Dosya boş veya tüketim verisi bulunamadı"
    - _Requirements: 1.1, 1.4, 1.5, 2.5, 2.6, 10.1, 10.4, 10.5_

  - [ ]* 2.4 Write property tests for parser
    - **Property 1: Format detection is deterministic and correct**
    - **Validates: Requirements 1.2, 1.3**

  - [ ]* 2.5 Write property test for multiplier invariant
    - **Property 2: Multiplier is never applied to consumption values**
    - **Validates: Requirements 1.4, 1.5**

  - [ ]* 2.6 Write property tests for date/value parsing
    - **Property 3: Date parsing round-trip** — DD/MM/YYYY HH:MM:SS format → parse → original datetime
    - **Property 4: kWh value parsing round-trip** — Turkish locale format → parse → original value ±0.01
    - **Validates: Requirements 2.1, 2.3, 2.4**

  - [ ]* 2.7 Write property test for negative consumption handling
    - **Property 23: Negative consumption absolute value handling**
    - **Validates: Requirements 10.4**

- [ ] 3. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Month splitter (monthly grouping, completeness validation)
  - [ ] 4.1 Implement `backend/app/recon/splitter.py`
    - `split_by_month(records)`: group records by YYYY-MM period
    - `validate_period_completeness(period, records)`: calculate expected_hours (days_in_month × 24), find missing hours, detect duplicates
    - Sort records chronologically if out of order (add warning)
    - Return periods in ascending chronological order
    - Report duplicate hours as warnings (use first occurrence)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 10.3, 10.6_

  - [ ]* 4.2 Write property tests for monthly splitter
    - **Property 7: Monthly split correctness** — all records in group have same period, no records lost
    - **Property 8: Period completeness calculation** — expected_hours = days_in_month × 24
    - **Property 9: Chronological period ordering** — periods sorted ascending
    - **Property 24: Order-independence of results** — results identical regardless of input order
    - **Validates: Requirements 3.1, 3.3, 3.4, 3.6, 10.6**

- [ ] 5. T1/T2/T3 classifier (classify_hour wrapper, period summaries)
  - [ ] 5.1 Implement `backend/app/recon/classifier.py`
    - `classify_period_records(records)`: classify each record using `pricing.time_zones.classify_hour()` and sum T1/T2/T3
    - Calculate percentages: t1_pct, t2_pct, t3_pct
    - Ensure T1 + T2 + T3 == total_kwh (±0.01 tolerance)
    - Return `TimeZoneSummary` model
    - Import from `app.pricing.time_zones` — NOT from `app.invoice.validation`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 5.2 Write property test for T1/T2/T3 partition
    - **Property 10: T1/T2/T3 partition property** — T1+T2+T3 == total ±0.01, percentages sum to 100 ±0.1
    - **Validates: Requirements 4.4, 4.5**

- [ ] 6. Invoice input reconciliation (tolerance config, severity classification, match/mismatch)
  - [ ] 6.1 Implement `backend/app/recon/reconciler.py`
    - `reconcile_consumption(calculated, invoice_declared, config)`: compare T1/T2/T3/total values
    - Implement dual tolerance: both `pct_tolerance` (±1%) AND `abs_tolerance_kwh` (±1 kWh) must be satisfied for MATCH
    - `classify_severity(diff_pct, diff_kwh)`: CRITICAL (>5% or >20 kWh), WARNING (>2% or >5 kWh), LOW (otherwise)
    - Handle NOT_CHECKED status when declared value is None
    - Calculate effective price: `unit_price × (1 - discount_pct / 100)`
    - Return per-field `ReconciliationItem` list with severity
    - _Requirements: 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [ ]* 6.2 Write property tests for reconciliation
    - **Property 11: Effective price calculation**
    - **Property 12: Reconciliation difference correctness** — diff_kwh = C - D, diff_pct = (C-D)/D × 100
    - **Property 13: Tolerance-based match classification** — MATCH iff |diff_pct| ≤ P AND |diff_kwh| ≤ A
    - **Property 14: Severity classification thresholds** — CRITICAL/WARNING/LOW boundaries
    - **Validates: Requirements 5.5, 6.1, 6.2, 6.3, 6.4**

- [ ] 7. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. PTF/YEKDEM cost engine (hourly PTF lookup, YEKDEM lookup, fail-closed logic)
  - [ ] 8.1 Implement `backend/app/recon/cost_engine.py`
    - `calculate_ptf_cost(records, period, db)`: lookup PTF from `hourly_market_prices` (canonical SoT), NOT `market_reference_prices`
    - Build (date, hour) → ptf_tl_per_mwh index from DB
    - Hourly cost formula: `consumption_kwh × (ptf_tl_per_mwh / 1000)`
    - Calculate weighted average PTF: `total_cost / total_matched_kwh × 1000`
    - Track missing PTF hours; warn if missing_pct > 10%
    - `get_yekdem_cost(period, total_kwh, db)`: lookup from `monthly_yekdem_prices` (canonical SoT)
    - YEKDEM formula: `total_kwh × (yekdem_tl_per_mwh / 1000)`
    - **Fail-closed logic**: if PTF completely missing OR YEKDEM unavailable → set `quote_blocked=True`, `quote_block_reason` filled, but still return parse+recon report
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9_

  - [ ]* 8.2 Write property tests for cost engine
    - **Property 15: Hourly PTF cost formula** — hour_cost = C × (P / 1000), weighted_avg = total_cost / total_kwh × 1000
    - **Property 16: Missing PTF detection and threshold warning** — warning iff missing_pct > 10%
    - **Property 17: YEKDEM cost formula** — yekdem_cost = T × (Y / 1000)
    - **Property 18: Fail-closed quote blocking** — quote_blocked=true when PTF/YEKDEM missing, report still valid
    - **Validates: Requirements 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9**

- [ ] 9. Quote comparator (fatura vs Gelka teklifi, margin multiplier)
  - [ ] 9.1 Implement `backend/app/recon/comparator.py`
    - `compare_costs(invoice_cost, gelka_cost, config)`: calculate invoice_energy, invoice_distribution, gelka_energy
    - Invoice energy: `total_kwh × effective_price`
    - Invoice distribution: `total_kwh × distribution_unit_price`
    - Gelka energy: `(ptf_cost + yekdem_cost) × gelka_margin_multiplier`
    - Gelka distribution: same as invoice distribution (same EPDK tariff)
    - Margin multiplier stored as config metadata, default 1.05 (5% margin)
    - Generate comparison message: "Tasarruf potansiyeli: X TL (%Y)" or "Mevcut tedarikçi avantajlı: X TL (%Y)"
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [ ]* 9.2 Write property tests for quote comparator
    - **Property 19: Invoice and Gelka cost formulas** — invoice_energy = T × EP, gelka_energy = (PC + YC) × M
    - **Property 20: Comparison message direction** — "Tasarruf" when invoice > gelka, "Mevcut tedarikçi avantajlı" otherwise
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.6, 8.7**

- [ ] 10. Report builder (JSON report assembly, multi-period summary, rounding)
  - [ ] 10.1 Implement `backend/app/recon/report_builder.py`
    - `build_report(parse_stats, period_results)`: assemble final `ReconReport`
    - Include parse statistics, format detected, multiplier metadata (info only)
    - Generate multi-period summary: sum of all period totals
    - Apply rounding: TL values → 2 decimal places, kWh values → 3 decimal places
    - Collect all warnings from sub-components into top-level warnings list
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [ ]* 10.2 Write property tests for report builder
    - **Property 21: Multi-period summary consistency** — summary total_kwh == sum of period totals
    - **Property 22: Rounding precision invariant** — TL ≤ 2 decimals, kWh ≤ 3 decimals
    - **Validates: Requirements 9.5, 9.6**

- [ ] 11. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. API endpoint (FastAPI router, file upload, request/response wiring, main.py registration)
  - [ ] 12.1 Implement `backend/app/recon/router.py` — FastAPI router
    - Create `POST /api/recon/analyze` endpoint accepting multipart file upload + JSON body (ReconRequest)
    - Validate file size (50 MB limit), file extension (.xlsx, .xls)
    - Wire pipeline: parse_excel → split_by_month → classify_period_records → reconcile → calculate_ptf_cost → get_yekdem_cost → compare_costs → build_report
    - Return `ReconReport` as JSON response
    - Handle errors with consistent `ErrorResponse` schema (HTTP 400 for client errors, 500 for server errors)
    - Inject DB session via FastAPI `Depends(get_db)`
    - _Requirements: 1.1, 9.2, 10.5, 10.7_

  - [ ] 12.2 Register recon router in `backend/app/main.py`
    - Add `from .recon.router import recon_router` import
    - Add `app.include_router(recon_router)` after existing router registrations
    - Verify endpoint is accessible at `/api/recon/analyze`
    - _Requirements: 9.2_

- [ ] 13. Frontend upload + manual invoice entry UI (Phase 1 — basic upload form)
  - [ ] 13.1 Create basic recon upload form component
    - Create `frontend/src/recon/ReconUploadForm.tsx` with file input (.xlsx/.xls) and invoice parameter fields
    - Invoice fields: period (YYYY-MM), supplier_name, tariff_group, unit_price, discount_pct, distribution_unit_price, declared T1/T2/T3/total kWh, declared total TL
    - Tolerance config fields: pct_tolerance (default 1.0), abs_tolerance_kwh (default 1.0)
    - Gelka margin multiplier field (default 1.05)
    - Submit button calling `POST /api/recon/analyze` with multipart form data
    - Display JSON response in formatted view (period summaries, reconciliation status, cost comparison)
    - _Requirements: 1.1, 5.1, 5.2, 5.3, 5.4, 6.5, 8.4_

- [ ] 14. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 15. Unit tests and integration tests (example-based)
  - [ ] 15.1 Write unit tests for parser in `backend/tests/test_recon_parser.py`
    - Test Format A detection with sample column headers
    - Test Format B detection with sample column headers
    - Test unknown format error with descriptive message
    - Test multi-sheet workbook (first sheet selection)
    - Test empty file handling
    - Test 50 MB file size limit
    - Test native datetime passthrough
    - Test Turkish locale kWh parsing: "1.234,56" → 1234.56
    - Test negative consumption → absolute value + warning
    - Test chronological sorting of out-of-order records
    - _Requirements: 1.1, 1.2, 1.3, 1.7, 2.1, 2.2, 2.3, 2.4, 10.1, 10.4, 10.5, 10.6_

  - [ ] 15.2 Write unit tests for reconciler in `backend/tests/test_recon_reconciler.py`
    - Test MATCH when within both tolerances
    - Test MISMATCH with LOW severity
    - Test MISMATCH with WARNING severity
    - Test MISMATCH with CRITICAL severity
    - Test NOT_CHECKED when declared value is None
    - Test effective price calculation with discount
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ] 15.3 Write unit tests for cost engine in `backend/tests/test_recon_cost_engine.py`
    - Test PTF cost calculation with known hourly values
    - Test missing PTF hours detection
    - Test >10% missing PTF warning
    - Test YEKDEM cost calculation
    - Test fail-closed: quote_blocked when PTF completely missing
    - Test fail-closed: quote_blocked when YEKDEM unavailable
    - Verify DB queries use `hourly_market_prices` NOT `market_reference_prices`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9_

  - [ ]* 15.4 Write integration test in `backend/tests/test_recon_integration.py`
    - Full pipeline test: Excel upload → parse → split → classify → reconcile → cost → compare → report
    - Test with sample Format A Excel (multi-month)
    - Test with sample Format B Excel (single month)
    - Test API endpoint with FastAPI TestClient
    - Test error response schema consistency
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 10.7_

- [ ] 16. Sample validation with Cansu Excel/fatura data (integration smoke test)
  - [ ] 16.1 Create smoke test with realistic data patterns in `backend/tests/test_recon_smoke.py`
    - Generate synthetic Excel file matching Format A structure (Profil Tarihi + Tüketim (Çekiş) + Çarpan)
    - Generate synthetic Excel file matching Format B structure (Tarih + Aktif Çekiş)
    - Populate with realistic hourly consumption data (24h × 30 days)
    - Verify full pipeline produces valid ReconReport with all fields populated
    - Verify multiplier is stored in metadata but NOT applied to calculations
    - Verify T1+T2+T3 == total invariant holds
    - Verify rounding: TL → 2 decimals, kWh → 3 decimals
    - _Requirements: 1.4, 1.5, 4.4, 9.5, 9.6_

- [ ] 17. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties (24 properties from design)
- Unit tests validate specific examples and edge cases
- **SoT compliance**: All PTF lookups use `hourly_market_prices`, all YEKDEM lookups use `monthly_yekdem_prices`. `market_reference_prices` is NEVER used.
- **Dead stack avoidance**: No imports from `app.invoice.validation` — that stack is DEAD per SoT matrix
- **Multiplier/Çarpan**: Stored as metadata on `HourlyRecord`, displayed in report, NEVER applied to any calculation
- **Fail-closed**: Missing PTF/YEKDEM blocks quote generation but parse+recon report still returns successfully

### Implementation Constraints (design.md IC-1..IC-5 — zorunlu)

- **IC-1**: Tüm iç hesaplamalar `Decimal` ile yapılır, `float` yalnızca API serialization'da kullanılır
- **IC-2**: Tüm timestamp'lar `Europe/Istanbul` timezone'una normalize edilir (T1/T2/T3 öncesi)
- **IC-3**: Month split beklenen saat sayısı DST-aware olmalı (23h/25h günler)
- **IC-4**: Reconciliation output zorunlu alanlar: excel_total_kwh, invoice_total_kwh, delta_kwh, delta_pct, severity
- **IC-5**: Parser pluggable provider mimarisi — `BaseFormatProvider` + registry pattern

### Stratejik Kapsam Notu

Phase 1 odağı: **Excel → normalize → reconcile → quote pipeline** tamamen stabil.
Aşağıdakiler Phase 1 kapsamında DEĞİLDİR:
- OCR / PDF parser / AI extraction
- Frontend polish (sadece basic upload form)
- Otomatik fatura görsel okuma

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["2.3"] },
    { "id": 4, "tasks": ["2.4", "2.5", "2.6", "2.7", "4.1"] },
    { "id": 5, "tasks": ["4.2", "5.1"] },
    { "id": 6, "tasks": ["5.2", "6.1"] },
    { "id": 7, "tasks": ["6.2", "8.1"] },
    { "id": 8, "tasks": ["8.2", "9.1"] },
    { "id": 9, "tasks": ["9.2", "10.1"] },
    { "id": 10, "tasks": ["10.2", "12.1"] },
    { "id": 11, "tasks": ["12.2", "13.1"] },
    { "id": 12, "tasks": ["15.1", "15.2", "15.3"] },
    { "id": 13, "tasks": ["15.4", "16.1"] }
  ]
}
```
