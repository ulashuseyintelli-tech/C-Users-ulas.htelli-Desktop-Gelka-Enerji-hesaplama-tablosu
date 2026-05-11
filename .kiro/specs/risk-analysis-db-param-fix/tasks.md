# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** — Missing `db` parameter in `generate_hourly_consumption()` call
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface a counterexample demonstrating the `TypeError` caused by the missing `db` argument
  - **Scoped PBT Approach**: Scope the property to the concrete failing case: `use_template=True`, `template_name="3_vardiya_sanayi"`, `template_monthly_kwh=100000`, `period="2026-01"`
  - Create test file `backend/tests/test_risk_analysis_db_param_fix.py`
  - Set up an in-memory SQLite database with a `ProfileTemplate` row for `"3_vardiya_sanayi"` (24 hourly weights)
  - Call `_get_or_generate_consumption(db, "2026-01", None, True, "3_vardiya_sanayi", 100000.0)`
  - Assert it returns a `list[ParsedConsumptionRecord]` with `len == 744` (31 days × 24 hours)
  - Assert `sum(r.consumption_kwh for r in result) ≈ 100000.0`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS with `TypeError: generate_hourly_consumption() missing 1 required positional argument: 'db'` — this proves the bug exists
  - Document the counterexample and failure message
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 2.1, 2.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** — Non-template code paths unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Observe on UNFIXED code: `_get_or_generate_consumption(db, period, customer_id, False, None, None)` returns DB records when records exist
  - Observe on UNFIXED code: `_get_or_generate_consumption(db, period, None, False, None, None)` raises `HTTPException(422)` with `"missing_consumption_data"`
  - Write property-based tests in the same test file `backend/tests/test_risk_analysis_db_param_fix.py`:
    - For any valid `customer_id` with existing DB records, the function returns those records unchanged
    - For any input with no customer records and `use_template=False`, the function raises HTTP 422
  - Verify tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS — confirms baseline behavior to preserve
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2_

- [x] 3. Fix: Add `db` parameter to `generate_hourly_consumption()` call

  - [x] 3.1 Implement the fix
    - In `backend/app/pricing/router.py`, function `_get_or_generate_consumption`, change:
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
    - This is a single-line change — no other files need modification
    - _Bug_Condition: isBugCondition(input) where use_template=True AND template_name IS NOT None AND template_monthly_kwh IS NOT None_
    - _Expected_Behavior: generate_hourly_consumption receives all 4 args and returns list[ParsedConsumptionRecord]_
    - _Preservation: Non-template paths (customer_id lookup, 422 error) must remain unchanged_
    - _Requirements: 1.1, 1.2, 2.1, 2.2, 3.1, 3.2, 3.3, 3.4_

  - [x] 3.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** — Template consumption generation works with `db` parameter
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES — confirms the bug is fixed (744 records, total ≈ 100000 kWh)
    - _Requirements: 2.1, 2.2_

  - [x] 3.3 Verify preservation tests still pass
    - **Property 2: Preservation** — Non-template code paths unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS — confirms no regressions
    - _Requirements: 3.1, 3.2_

  - [x] 3.4 Add regression integration test for `/analyze` endpoint
    - Add a test to `backend/tests/test_risk_analysis_db_param_fix.py` that calls the `/analyze` endpoint via `TestClient` with:
      - `use_template=True`
      - `template_name="3_vardiya_sanayi"`
      - `template_monthly_kwh=100000`
      - `period="2026-01"`
    - Assert: response status code is `200`
    - Assert: response contains consumption data with `744` records
    - Assert: total consumption ≈ `100000` kWh
    - This ensures the full endpoint works end-to-end with template-based consumption
    - _Requirements: 2.1, 2.2_

- [x] 4. Checkpoint — Ensure all tests pass
  - Run full test suite for the test file: `pytest backend/tests/test_risk_analysis_db_param_fix.py -v`
  - Ensure all tests pass, ask the user if questions arise
