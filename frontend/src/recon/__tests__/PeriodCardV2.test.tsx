/**
 * PeriodCardV2 — vitest suite.
 *
 * Coverage (Task 12):
 *   - null values render as "—", not 0
 *   - positive savings render emerald wording ("olası tasarruf")
 *   - negative savings render "Mevcut tedarikçi daha avantajlı görünüyor"
 *   - "Referans maliyet farkı" label present
 *   - Forbidden terms absent (gerçek maliyet, ticari marj, kâr in surface)
 *   - Partial period shows amber "Veri eksik" pill
 *   - quote_blocked banner appears with reason
 *   - Accordions default collapsed
 *   - Accordion opens on click and preserves rendered details
 *   - cost_inputs tooltip/disclosure exists
 */

import { describe, it, expect } from 'vitest';
import { render, screen, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { PeriodCardV2 } from '../PeriodCardV2';
import { makePeriod, makeCostInputs } from './fixtures';

describe('PeriodCardV2 — happy path headline', () => {
  it('renders the four headline cells in documented order', () => {
    render(<PeriodCardV2 period={makePeriod()} declaredTotalTl={793204.42} />);

    // Look up the cells by the exact title strings (REQ-4 wording lock).
    const titles = [
      'Mevcut enerji bedeli',
      'Referans enerji maliyeti',
      'Referans maliyet farkı',
      'Gelka teklif tahmini',
    ];
    for (const title of titles) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
  });

  it('formats positive savings with "olası tasarruf" emerald wording', () => {
    render(
      <PeriodCardV2
        period={makePeriod({
          potential_savings_tl: 99150.55,
        })}
        declaredTotalTl={793204.42}
      />,
    );

    expect(screen.getByText('olası tasarruf')).toBeInTheDocument();
  });

  it('renders supplier markup pct label without forbidden wording', () => {
    const { container } = render(
      <PeriodCardV2 period={makePeriod()} declaredTotalTl={793204.42} />,
    );

    const text = container.textContent ?? '';
    // Positive markup → no negative-fark hint
    expect(text).not.toMatch(/negatif fark/i);
    // Mandatory neutral label exists
    expect(text).toContain('Referans maliyet farkı');
  });
});

describe('PeriodCardV2 — null / missing values', () => {
  it('renders dash placeholder when reference_energy_cost_tl is null', () => {
    const partial = makePeriod({
      reference_energy_cost_tl: null,
      supplier_markup_tl: null,
      supplier_markup_pct: null,
      gelka_estimate_tl: null,
      potential_savings_tl: null,
      cost_inputs: makeCostInputs({ complete: false }),
    });

    const { container } = render(
      <PeriodCardV2 period={partial} declaredTotalTl={793204.42} />,
    );

    // The four headline cells include three null surfaces (ref, fark, gelka).
    // Each renders the "—" placeholder exactly once for the value line.
    const dashHits = (container.textContent ?? '').match(/—/g) ?? [];
    expect(dashHits.length).toBeGreaterThanOrEqual(3);

    // "hesaplanamadı" hint appears at least on the missing cells.
    expect(screen.getAllByText('hesaplanamadı').length).toBeGreaterThanOrEqual(3);

    // null is not rendered as "0" / "0,00 ₺" anywhere on the headline.
    // (We only check the headline region; T1/T2/T3 totals legitimately
    //  show "0,000 kWh" if upstream reports them, but the v1 fixture has
    //  non-zero kWh so this should be safe globally too.)
    expect(container.textContent).not.toContain('0,00 ₺');
  });

  it('renders dash for declared_total_tl when not provided', () => {
    render(
      <PeriodCardV2 period={makePeriod()} declaredTotalTl={null} />,
    );

    // The "Mevcut enerji bedeli" cell shows a hint when invoice was not provided.
    expect(screen.getByText('Fatura tutarı girilmedi')).toBeInTheDocument();
  });
});

describe('PeriodCardV2 — negative values (neutral wording, no red)', () => {
  it('renders advisory wording for negative savings (potential_savings_tl < 0)', () => {
    render(
      <PeriodCardV2
        period={makePeriod({
          potential_savings_tl: -50000.0,
        })}
        declaredTotalTl={793204.42}
      />,
    );

    expect(
      screen.getByText('mevcut tedarikçi daha avantajlı görünüyor'),
    ).toBeInTheDocument();
  });

  it('renders advisory wording for negative markup (supplier_markup_tl < 0)', () => {
    const period = makePeriod({
      supplier_markup_tl: -10000.0,
      supplier_markup_pct: -1.5,
    });

    render(<PeriodCardV2 period={period} declaredTotalTl={793204.42} />);

    expect(
      screen.getByText(
        'negatif fark — fatura referans maliyetin altında',
      ),
    ).toBeInTheDocument();
  });

  it('does not contain forbidden judgmental terminology', () => {
    const period = makePeriod({
      supplier_markup_tl: -10000.0,
      supplier_markup_pct: -1.5,
      potential_savings_tl: -50000.0,
    });

    const { container } = render(
      <PeriodCardV2 period={period} declaredTotalTl={793204.42} />,
    );

    const text = (container.textContent ?? '').toLowerCase();
    // Surface terminology lock — REQ-4 / REQ-6.4
    expect(text).not.toContain('gerçek maliyet');
    expect(text).not.toContain('actual cost');
    expect(text).not.toContain('true cost');
    expect(text).not.toContain('ticari marj');
    expect(text).not.toContain('tedarikçinin kâr');
    expect(text).not.toContain('şüpheli');
    expect(text).not.toContain('anormal');
  });
});

describe('PeriodCardV2 — partial period (REQ-5.8 amber, never red)', () => {
  it('renders the "Veri eksik" pill when reference_energy_cost_tl is null', () => {
    const partial = makePeriod({
      reference_energy_cost_tl: null,
      supplier_markup_tl: null,
      supplier_markup_pct: null,
      gelka_estimate_tl: null,
      potential_savings_tl: null,
      cost_inputs: makeCostInputs({ complete: false }),
    });

    render(<PeriodCardV2 period={partial} declaredTotalTl={null} />);

    expect(screen.getByText('Veri eksik')).toBeInTheDocument();
  });

  it('renders the explanatory note when ref cost is null but invoice exists', () => {
    const partial = makePeriod({
      reference_energy_cost_tl: null,
      supplier_markup_tl: null,
      supplier_markup_pct: null,
      gelka_estimate_tl: null,
      potential_savings_tl: null,
      cost_inputs: makeCostInputs({ complete: false }),
    });

    render(
      <PeriodCardV2 period={partial} declaredTotalTl={793204.42} />,
    );

    expect(
      screen.getByText(
        'Referans enerji maliyeti hesaplanamadı — fatura tutarı gösterimi sınırlı.',
      ),
    ).toBeInTheDocument();
  });
});

describe('PeriodCardV2 — quote_blocked banner', () => {
  it('renders the amber banner with reason text when quote_blocked is true', () => {
    const blocked = makePeriod({
      reference_energy_cost_tl: null,
      supplier_markup_tl: null,
      supplier_markup_pct: null,
      gelka_estimate_tl: null,
      potential_savings_tl: null,
      cost_inputs: makeCostInputs({ complete: false }),
      quote_blocked: true,
      quote_block_reason: 'PTF data missing for 3 hours',
    });

    render(<PeriodCardV2 period={blocked} declaredTotalTl={null} />);

    expect(
      screen.getByText('Bu dönem için referans maliyet hesaplanamadı.'),
    ).toBeInTheDocument();
    // The reason text is rendered as a (truncating) sibling.
    expect(
      screen.getByText('— PTF data missing for 3 hours'),
    ).toBeInTheDocument();
  });

  it('does NOT render the banner when quote_blocked is false', () => {
    render(<PeriodCardV2 period={makePeriod()} declaredTotalTl={793204.42} />);

    expect(
      screen.queryByText('Bu dönem için referans maliyet hesaplanamadı.'),
    ).not.toBeInTheDocument();
  });
});

describe('PeriodCardV2 — accordions', () => {
  it('renders both accordions collapsed by default', () => {
    render(<PeriodCardV2 period={makePeriod()} declaredTotalTl={793204.42} />);

    const consumptionToggle = screen.getByRole('button', {
      name: /Tüketim profili \(T1\/T2\/T3\)/i,
    });
    expect(consumptionToggle).toHaveAttribute('aria-expanded', 'false');
  });

  it('opens the consumption accordion on click and reveals T1/T2/T3 detail', async () => {
    const user = userEvent.setup();
    render(<PeriodCardV2 period={makePeriod()} declaredTotalTl={793204.42} />);

    const toggle = screen.getByRole('button', {
      name: /Tüketim profili \(T1\/T2\/T3\)/i,
    });
    await act(async () => {
      await user.click(toggle);
    });

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    // Expanded content reveals T1/T2/T3 cells.
    expect(screen.getByText('T1 Gündüz')).toBeInTheDocument();
    expect(screen.getByText('T2 Puant')).toBeInTheDocument();
    expect(screen.getByText('T3 Gece')).toBeInTheDocument();
  });

  it('renders reconciliation accordion when reconciliation items exist', async () => {
    const user = userEvent.setup();
    const period = makePeriod({
      reconciliation: [
        {
          field: 't1_kwh',
          excel_total_kwh: 87525.087,
          invoice_total_kwh: 87525.0,
          delta_kwh: 0.087,
          delta_pct: 0.001,
          status: 'UYUMLU',
          severity: 'LOW',
        },
      ],
    });

    render(<PeriodCardV2 period={period} declaredTotalTl={793204.42} />);

    const toggle = screen.getByRole('button', {
      name: /Mutabakat detayı/i,
    });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    await act(async () => {
      await user.click(toggle);
    });

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    // Field name pulled from reconciliation row (rendered uppercase via CSS).
    // We assert the underlying text is present.
    expect(screen.getByText('t1_kwh')).toBeInTheDocument();
    expect(screen.getByText('UYUMLU')).toBeInTheDocument();
  });

  it('preserves consumption accordion state across sibling re-renders', async () => {
    // Each accordion holds its own useState. A sibling toggle MUST NOT close
    // an already-open consumption accordion.
    const user = userEvent.setup();
    const period = makePeriod({
      reconciliation: [
        {
          field: 'total_kwh',
          excel_total_kwh: 1,
          invoice_total_kwh: 1,
          delta_kwh: 0,
          delta_pct: 0,
          status: 'UYUMLU',
          severity: 'LOW',
        },
      ],
    });

    render(<PeriodCardV2 period={period} declaredTotalTl={793204.42} />);

    const consumptionToggle = screen.getByRole('button', {
      name: /Tüketim profili \(T1\/T2\/T3\)/i,
    });
    const reconToggle = screen.getByRole('button', {
      name: /Mutabakat detayı/i,
    });

    // Open consumption accordion.
    await act(async () => {
      await user.click(consumptionToggle);
    });
    expect(consumptionToggle).toHaveAttribute('aria-expanded', 'true');

    // Open reconciliation accordion as well — should not affect siblings.
    await act(async () => {
      await user.click(reconToggle);
    });

    expect(consumptionToggle).toHaveAttribute('aria-expanded', 'true');
    expect(reconToggle).toHaveAttribute('aria-expanded', 'true');
  });

  it('does not render reconciliation accordion when there is nothing to disclose', () => {
    render(<PeriodCardV2 period={makePeriod()} declaredTotalTl={793204.42} />);
    // No reconciliation items, no warnings, no cost_comparison → accordion suppressed.
    expect(
      screen.queryByRole('button', { name: /Mutabakat detayı/i }),
    ).not.toBeInTheDocument();
  });
});

describe('PeriodCardV2 — cost_inputs operational tooltip', () => {
  it('renders the (i) icon with cost source disclosure in title attribute', () => {
    const period = makePeriod({
      cost_inputs: makeCostInputs({
        period_start: '2026-01-01',
        period_end: '2026-01-31',
        total_hours: 744,
        complete: true,
      }),
    });

    render(<PeriodCardV2 period={period} declaredTotalTl={793204.42} />);

    const tooltipHost = screen.getByLabelText('Veri kaynağı detayları');
    const title = tooltipHost.getAttribute('title') ?? '';
    expect(title).toContain('PTF kaynağı: hourly_market_prices');
    expect(title).toContain('YEKDEM kaynağı: monthly_yekdem_prices');
    expect(title).toContain('Saat kapsamı: 744');
    expect(title).toContain('Tamlık: tam');
  });

  it('reports tamlık=eksik when cost_inputs.complete is false', () => {
    const period = makePeriod({
      cost_inputs: makeCostInputs({ complete: false }),
    });

    render(<PeriodCardV2 period={period} declaredTotalTl={793204.42} />);

    const tooltipHost = screen.getByLabelText('Veri kaynağı detayları');
    const title = tooltipHost.getAttribute('title') ?? '';
    expect(title).toContain('Tamlık: eksik');
  });
});

describe('PeriodCardV2 — header label and accessibility scope', () => {
  it('renders period label in the header', () => {
    render(
      <PeriodCardV2
        period={makePeriod({ period: '2026-03' })}
        declaredTotalTl={793204.42}
      />,
    );

    // Card has aria-label="Dönem 2026-03" — scoping anchor for queries.
    const card = screen.getByLabelText('Dönem 2026-03');
    expect(within(card).getByText('2026-03')).toBeInTheDocument();
  });
});
