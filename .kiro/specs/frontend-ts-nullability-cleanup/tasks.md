# Frontend TypeScript Nullability Cleanup

## Context

Pre-existing TypeScript strict-null errors in `frontend/src/App.tsx` and test files.
These were present before the pricing-consistency-fixes sprint and are not caused by it.
Identified during the pricing bugfix as technical debt to clean up before production.

## Scope

- Fix `result possibly null` errors in `frontend/src/App.tsx` (~19 instances)
- Fix `Property 'meta' does not exist` error in `frontend/src/App.tsx` (1 instance)
- Fix pre-existing test file type errors in `frontend/src/market-prices/__tests__/` (~65 instances)
- Do NOT change pricing formulas or calculation logic
- Do NOT modify backend pricing engine, models, or router
- Keep all 107 backend pricing tests passing

## Tasks

- [ ] 1. Fix `result possibly null` errors in App.tsx
  - Add null guards (`result?.` or early returns) for ~19 locations
  - Lines ~1978, 2222, 2224, 2249, 2257, 2262, 2264, 2269, 2274, 2277, 2280, 2285, 2289, 2307, 2310, 2312
  - Do not change any calculation logic

- [ ] 2. Fix `Property 'meta' does not exist` on extraction type
  - Add `meta?: { tariff_group_guess?: string }` to `AnalyzeResponse.extraction` in `api.ts`
  - Or use optional chaining at the call site

- [ ] 3. Fix test file type errors in market-prices tests
  - `PriceFilters.test.tsx` — 6 errors (toBeInTheDocument, toHaveAttribute, toHaveValue)
  - `PriceListTable.test.tsx` — 42 errors (toBeInTheDocument, toHaveTextContent, toBeDisabled, toHaveValue)
  - `SkeletonLoader.test.tsx` — 1 error (toHaveAttribute)
  - `StatusBadge.test.tsx` — 2 errors (toBeInTheDocument)
  - `ToastNotification.test.tsx` — 3 errors (toBeInTheDocument)
  - Root cause: likely missing `@testing-library/jest-dom` type augmentation in vitest setup

- [ ] 4. Verify zero new TypeScript errors introduced
  - Run `npx tsc --noEmit` and confirm error count is 0 (or only unrelated pre-existing)
  - Run backend pricing tests: `pytest tests/ -k pricing` → 107/107 pass
