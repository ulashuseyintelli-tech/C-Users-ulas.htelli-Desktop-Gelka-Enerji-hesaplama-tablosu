# Risk Analysis DB Parameter Fix — Bugfix Design

## Overview

`_get_or_generate_consumption()` in `backend/app/pricing/router.py` calls `generate_hourly_consumption(template_name, template_monthly_kwh, period)` with only three positional arguments. The callee signature in `profile_templates.py` requires four: `(template_name, total_monthly_kwh, period, db)`. The missing `db: Session` argument causes a `TypeError` at runtime whenever template-based consumption is requested, which surfaces as the frontend hanging on "hesaplanıyor…".

The fix is a single-token change: append `db` as the fourth argument in the call site.

## Glossary

- **Bug_Condition (C)**: The call path where `use_template=True` and valid `template_name` / `template_monthly_kwh` are provided, triggering `generate_hourly_consumption` without the `db` parameter
- **Property (P)**: `generate_hourly_consumption` receives all four required arguments and returns a valid `list[ParsedConsumptionRecord]`
- **Preservation**: All non-template code paths (`customer_id` lookup, 422 error) and all existing `generate_hourly_consumption` validation (bad period format, missing template) must remain unchanged
- **`_get_or_generate_consumption`**: Helper in `router.py` that resolves consumption data — either from DB records or by generating from a profile template
- **`generate_hourly_consumption`**: Function in `profile_templates.py` that produces 24 × days-in-month hourly consumption records from a named template stored in the database

## Bug Details

### Bug Condition

The bug manifests when a user requests risk analysis with `use_template=True`, a valid `template_name`, and a valid `template_monthly_kwh`. The `_get_or_generate_consumption` function calls `generate_hourly_consumption` with only three positional arguments, omitting the required `db: Session` parameter. This causes a `TypeError` (missing positional argument) or, depending on Python version details, passes `period` (a string) where `db` (a `Session`) is expected.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type AnalyzeRequest with db: Session context
  OUTPUT: boolean

  RETURN input.use_template == True
         AND input.template_name IS NOT None
         AND input.template_monthly_kwh IS NOT None
END FUNCTION
```

### Examples

- **Example 1**: `analyze(use_template=True, template_name="3_vardiya_sanayi", template_monthly_kwh=50000, period="2025-01")` → **Expected**: returns risk analysis result. **Actual**: `TypeError: generate_hourly_consumption() missing 1 required positional argument: 'db'`
- **Example 2**: `analyze(use_template=True, template_name="ofis", template_monthly_kwh=12000, period="2025-06")` → **Expected**: returns risk analysis result. **Actual**: runtime error, frontend stuck on "hesaplanıyor…"
- **Example 3**: `analyze(customer_id="C-001", period="2025-01")` → **Expected**: loads from DB, works fine. **Actual**: works fine (bug condition not met)
- **Edge case**: `analyze(use_template=True, template_name=None, template_monthly_kwh=None, period="2025-01")` → **Expected**: falls through to customer_id path or 422. **Actual**: works fine (guard clause prevents entering the buggy branch)

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Loading consumption records from the database via `_load_consumption_records(db, customer_id, period)` when `customer_id` is provided must continue to work exactly as before
- Raising HTTP 422 with `"missing_consumption_data"` when neither template nor customer records are available must remain unchanged
- `generate_hourly_consumption` validation: `ValueError` on invalid period format (`"Geçersiz dönem formatı"`) must remain unchanged
- `generate_hourly_consumption` validation: `ValueError` on non-existent template name (`"Profil şablonu bulunamadı"`) must remain unchanged
- All other pricing endpoints (upload, simulate, compare, YEKDEM, report) must remain unaffected

**Scope:**
All inputs that do NOT satisfy `use_template=True AND template_name IS NOT None AND template_monthly_kwh IS NOT None` should be completely unaffected by this fix. This includes:
- Requests with `customer_id` and real DB consumption records
- Requests missing template parameters (fall-through to 422)
- All non-analyze endpoints

## Hypothesized Root Cause

Based on the code inspection, the root cause is confirmed (not merely hypothesized):

1. **Missing positional argument**: In `router.py` line ~204, the call reads:
   ```python
   return generate_hourly_consumption(
       template_name, template_monthly_kwh, period,
   )
   ```
   The function signature requires four arguments: `(template_name, total_monthly_kwh, period, db)`. The `db` parameter — which is already available in the enclosing `_get_or_generate_consumption(db, ...)` scope — is simply not passed.

2. **No other contributing factors**: The `db` session is correctly received by `_get_or_generate_consumption` from its caller (`analyze`), and `generate_hourly_consumption` correctly uses `db` internally to call `get_template_by_name(db, template_name)`. The only issue is the missing argument at the call site.

## Correctness Properties

Property 1: Bug Condition — Template consumption generation receives all required arguments

_For any_ input where `use_template=True` and both `template_name` and `template_monthly_kwh` are provided (isBugCondition returns true), the fixed `_get_or_generate_consumption` function SHALL call `generate_hourly_consumption` with four arguments `(template_name, template_monthly_kwh, period, db)` and return a valid `list[ParsedConsumptionRecord]` without raising a `TypeError`.

**Validates: Requirements 2.1, 2.2**

Property 2: Preservation — Non-template code paths unchanged

_For any_ input where the bug condition does NOT hold (isBugCondition returns false), the fixed `_get_or_generate_consumption` function SHALL produce the same result as the original function — either returning DB-loaded consumption records for a valid `customer_id`, or raising HTTP 422 when no data source is available — preserving all existing behavior for non-template paths.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

## Fix Implementation

### Changes Required

**File**: `backend/app/pricing/router.py`

**Function**: `_get_or_generate_consumption`

**Specific Changes**:
1. **Add `db` as fourth argument**: Change the `generate_hourly_consumption` call from:
   ```python
   return generate_hourly_consumption(
       template_name, template_monthly_kwh, period,
   )
   ```
   to:
   ```python
   return generate_hourly_consumption(
       template_name, template_monthly_kwh, period, db,
   )
   ```

No other files or functions require changes.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm the root cause is the missing `db` argument.

**Test Plan**: Write a unit test that calls `_get_or_generate_consumption` with `use_template=True` and valid template parameters. Run on UNFIXED code to observe the `TypeError`.

**Test Cases**:
1. **Direct call test**: Call `_get_or_generate_consumption(db, "2025-01", None, True, "3_vardiya_sanayi", 50000.0)` — will raise `TypeError` on unfixed code
2. **Argument count inspection**: Verify `generate_hourly_consumption` expects 4 positional args but the call site provides only 3

**Expected Counterexamples**:
- `TypeError: generate_hourly_consumption() missing 1 required positional argument: 'db'`
- Possible cause: confirmed — the `db` argument is omitted from the call

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := _get_or_generate_consumption_fixed(db, period, None, True, template_name, template_monthly_kwh)
  ASSERT result IS list[ParsedConsumptionRecord]
  ASSERT len(result) == days_in_month(period) * 24
  ASSERT all(r.consumption_kwh >= 0 for r in result)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT _get_or_generate_consumption_original(input) = _get_or_generate_consumption_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many combinations of `customer_id`, `period`, and template flag states automatically
- It catches edge cases in the guard-clause logic (`use_template` with partial parameters)
- It provides strong guarantees that the non-template paths are completely unaffected

**Test Plan**: Observe behavior on UNFIXED code for non-template paths (customer_id lookups, 422 errors), then write property-based tests capturing that behavior.

**Test Cases**:
1. **Customer ID path preservation**: Verify `_get_or_generate_consumption(db, period, customer_id, False, None, None)` returns DB records identically before and after fix
2. **422 error preservation**: Verify missing data still raises HTTP 422 with the same detail message
3. **Partial template params preservation**: Verify `use_template=True` with `template_name=None` falls through correctly

### Unit Tests

- Test that `_get_or_generate_consumption` with template params calls `generate_hourly_consumption` with exactly 4 arguments including `db`
- Test that the returned records have correct structure (date, hour, consumption_kwh fields)
- Test that invalid period format still raises `ValueError`
- Test that non-existent template name still raises `ValueError`

### Property-Based Tests

- Generate random valid `(template_name, monthly_kwh, period)` tuples and verify the fixed function returns `days × 24` records with non-negative consumption values
- Generate random non-template inputs and verify the function behavior matches the original (either returns DB records or raises 422)
- Generate random period strings and verify period validation is unchanged

### Integration Tests

- Test full `/analyze` endpoint with `use_template=True` returns a complete risk analysis JSON response
- Test `/analyze` endpoint with `customer_id` continues to work after the fix
- Test `/analyze` endpoint with missing data still returns 422
