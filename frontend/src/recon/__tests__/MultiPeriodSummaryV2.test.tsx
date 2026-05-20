/**
 * MultiPeriodSummaryV2 — vitest suite.
 *
 * Coverage (Task 12):
 *   - All 4 v2 totals render
 *   - Coverage disclosure ("X / Y dönem hesaplanabildi")
 *   - partial_period_count > 0 → amber italic note
 *   - Annual projection card renders when summary.annual_projection !== null
 *   - Annual projection label (literal "tahmini yıllık projeksiyon") visible
 *   - based_on_periods disclaimer visible
 *   - Annual projection card omitted when summary.annual_projection === null
 *   - null totals render "—" + "hesaplanamadı"
 *   - No forbidden terminology in surface text
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { MultiPeriodSummaryV2 } from '../MultiPeriodSummaryV2';
import { makeSummary } from './fixtures';

describe('MultiPeriodSummaryV2 — totals block', () => {
  it('renders all four total stat titles', () => {
    render(<MultiPeriodSummaryV2 summary={makeSummary()} />);

    expect(screen.getByText('Toplam fatura')).toBeInTheDocument();
    expect(screen.getByText('Toplam referans')).toBeInTheDocument();
    expect(screen.getByText('Toplam referans maliyet farkı')).toBeInTheDocument();
    expect(screen.getByText('Toplam olası tasarruf')).toBeInTheDocument();
  });

  it('renders coverage disclosure with valid_period_count / total_period_count', () => {
    render(
      <MultiPeriodSummaryV2
        summary={makeSummary({
          valid_period_count: 3,
          partial_period_count: 1,
          total_period_count: 4,
        })}
      />,
    );

    expect(screen.getByText(/3 \/ 4 dönem hesaplanabildi/)).toBeInTheDocument();
    expect(
      screen.getByText(/· 1 dönem için PTF\/YEKDEM eksik/),
    ).toBeInTheDocument();
  });

  it('omits the partial-period note when partial_period_count is 0', () => {
    render(<MultiPeriodSummaryV2 summary={makeSummary()} />);

    expect(screen.getByText(/4 \/ 4 dönem hesaplanabildi/)).toBeInTheDocument();
    expect(screen.queryByText(/PTF\/YEKDEM eksik/)).not.toBeInTheDocument();
  });

  it('renders dash placeholder when v2 totals are null', () => {
    const { container } = render(
      <MultiPeriodSummaryV2
        summary={makeSummary({
          total_reference_energy_cost_tl: null,
          total_supplier_markup_tl: null,
          total_gelka_estimate_tl: null,
          total_potential_savings_tl: null,
          annual_projection: null,
        })}
      />,
    );

    // Three v2 totals (referans, fark, savings) go null; the v1 total_invoice_tl
    // is not null in the fixture so it still renders as a TL value. The dash
    // therefore appears at least 3 times in the value lines.
    const dashHits = (container.textContent ?? '').match(/—/g) ?? [];
    expect(dashHits.length).toBeGreaterThanOrEqual(3);

    // hesaplanamadı hint visible on each null cell.
    expect(screen.getAllByText('hesaplanamadı').length).toBeGreaterThanOrEqual(3);
  });
});

describe('MultiPeriodSummaryV2 — annual projection disclaimer', () => {
  it('renders the projection card title from the locked label literal', () => {
    render(<MultiPeriodSummaryV2 summary={makeSummary()} />);

    // The literal "tahmini yıllık projeksiyon" comes from backend; assert
    // the FE renders it AS-IS (case-insensitive heading match works because
    // CSS uppercases via tracking-wide).
    expect(
      screen.getByText('tahmini yıllık projeksiyon'),
    ).toBeInTheDocument();
  });

  it('renders the based_on_periods disclaimer text', () => {
    render(
      <MultiPeriodSummaryV2
        summary={makeSummary({
          annual_projection: {
            based_on_periods: 4,
            label: 'tahmini yıllık projeksiyon',
            annualized_reference_cost_tl: 7797855.21,
            annualized_supplier_markup_tl: 1559571.06,
            annualized_potential_savings_tl: 1169678.25,
          },
        })}
      />,
    );

    expect(
      screen.getByText(
        '4 dönem verisinden ekstrapole edilmiştir. Gerçek yıllık değer değildir.',
      ),
    ).toBeInTheDocument();
  });

  it('renders all three annualized stat titles', () => {
    render(<MultiPeriodSummaryV2 summary={makeSummary()} />);

    expect(
      screen.getByText('Yıllık referans maliyet (tahmini)'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Yıllık referans maliyet farkı (tahmini)'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Yıllık olası tasarruf (tahmini)'),
    ).toBeInTheDocument();
  });

  it('omits the projection card when summary.annual_projection is null', () => {
    render(
      <MultiPeriodSummaryV2
        summary={makeSummary({
          valid_period_count: 0,
          partial_period_count: 4,
          total_period_count: 4,
          total_reference_energy_cost_tl: null,
          total_supplier_markup_tl: null,
          total_gelka_estimate_tl: null,
          total_potential_savings_tl: null,
          annual_projection: null,
        })}
      />,
    );

    expect(
      screen.queryByText('tahmini yıllık projeksiyon'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/dönem verisinden ekstrapole edilmiştir/),
    ).not.toBeInTheDocument();
  });

  it('renders dash when an annualized stat is null', () => {
    render(
      <MultiPeriodSummaryV2
        summary={makeSummary({
          annual_projection: {
            based_on_periods: 1,
            label: 'tahmini yıllık projeksiyon',
            annualized_reference_cost_tl: 1000.0,
            annualized_supplier_markup_tl: null,
            annualized_potential_savings_tl: null,
          },
        })}
      />,
    );

    // Two annualized stats are null → at least two "hesaplanamadı" hints.
    expect(screen.getAllByText('hesaplanamadı').length).toBeGreaterThanOrEqual(
      2,
    );
  });
});

describe('MultiPeriodSummaryV2 — terminology guard', () => {
  it('contains no forbidden surface terminology', () => {
    const { container } = render(
      <MultiPeriodSummaryV2 summary={makeSummary()} />,
    );

    const text = (container.textContent ?? '').toLowerCase();
    expect(text).not.toContain('gerçek maliyet');
    expect(text).not.toContain('actual cost');
    expect(text).not.toContain('true cost');
    expect(text).not.toContain('ticari marj');
    expect(text).not.toContain('tedarikçinin kâr');
    // Critical: backend annual extrapolation must NOT be presented as real.
    expect(text).not.toContain('gerçek yıllık tasarruf');
  });
});
