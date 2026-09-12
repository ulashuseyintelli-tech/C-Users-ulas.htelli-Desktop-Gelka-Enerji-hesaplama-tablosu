// =============================================================================
// PriceListTable — FİYAT HÜCRESİ testleri (yanıt sözleşmesi düzeltmesi)
// =============================================================================
// Gerçek pakette gözlenen kusur: API `ptf_value` döndürüyor, tablo
// `ptf_tl_per_mwh` okuyordu → hücrede "NaN". API düzeltildi; burada tablonun
// GERÇEK yanıt alanlarıyla doğru gösterdiği ve EKSİK değeri SIFIR
// GÖSTERMEDİĞİ kilitlenir.
// =============================================================================

import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { PriceListTable } from '../PriceListTable';
import type { MarketPriceRecord, PaginationState } from '../types';

function kayit(o: Partial<MarketPriceRecord> = {}): MarketPriceRecord {
  return {
    period: '2019-03',
    ptf_tl_per_mwh: 1234.56,
    status: 'provisional',
    price_type: 'PTF',
    captured_at: '2026-09-12T14:10:35Z',
    updated_at: '2026-09-12T14:10:35Z',
    updated_by: 'admin',
    source: 'manual_override',
    source_note: '',
    change_reason: '',
    is_locked: false,
    yekdem_tl_per_mwh: 78.9,
    ...o,
  };
}

const sayfalama: PaginationState = { page: 1, pageSize: 20, total: 1 };

function ciz(data: MarketPriceRecord[]) {
  return render(
    <PriceListTable
      data={data}
      loading={false}
      pagination={sayfalama}
      sortBy="period"
      sortOrder="desc"
      onSort={vi.fn()}
      onPageChange={vi.fn()}
      onPageSizeChange={vi.fn()}
      onEdit={vi.fn()}
      onHistory={vi.fn()}
      onClearFilters={vi.fn()}
      isEmpty={false}
    />,
  );
}

describe('PriceListTable — fiyat hücreleri', () => {
  it('gerçek liste yanıtı 1.234,56 ve 78,90 gösterir (NaN YOK)', () => {
    ciz([kayit()]);
    const satir = screen.getByText('2019-03').closest('tr') as HTMLElement;
    expect(within(satir).getByText('1.234,56')).toBeInTheDocument();
    expect(within(satir).getByText('78,90')).toBeInTheDocument();
    expect(satir.textContent).not.toContain('NaN');
  });

  it('YEKDEM sütunu tabloda vardır', () => {
    ciz([kayit()]);
    expect(screen.getByText('YEKDEM (TL/MWh)')).toBeInTheDocument();
  });

  it('değer null ise SIFIR değil "—" gösterir', () => {
    ciz([kayit({ ptf_tl_per_mwh: null, yekdem_tl_per_mwh: null })]);
    const satir = screen.getByText('2019-03').closest('tr') as HTMLElement;
    expect(within(satir).getAllByText('—').length).toBeGreaterThanOrEqual(2);
    expect(satir.textContent).not.toContain('0,00');
    expect(satir.textContent).not.toContain('NaN');
  });

  it('alan hiç yoksa (eski sözleşme) NaN yerine "—" gösterir', () => {
    const eksik = kayit();
    // Eski API sözleşmesinin benzetimi: alanlar yanıtta YOK.
    delete (eksik as unknown as Record<string, unknown>).ptf_tl_per_mwh;
    delete (eksik as unknown as Record<string, unknown>).yekdem_tl_per_mwh;
    ciz([eksik]);
    const satir = screen.getByText('2019-03').closest('tr') as HTMLElement;
    expect(satir.textContent).not.toContain('NaN');
    expect(within(satir).getAllByText('—').length).toBeGreaterThanOrEqual(2);
  });

  it('gerçek 0 değeri "—" DEĞİL, 0,00 gösterilir', () => {
    ciz([kayit({ yekdem_tl_per_mwh: 0 })]);
    const satir = screen.getByText('2019-03').closest('tr') as HTMLElement;
    expect(within(satir).getByText('0,00')).toBeInTheDocument();
  });
});

describe('PriceListTable — ek sözleşme alanları ve şema varsayılanı', () => {
  it('YEKDEM 0 değeri gösterilir ama "doğrulanmamış olabilir" işaretlenir', () => {
    ciz([kayit({ yekdem_tl_per_mwh: 0 })]);
    const satir = screen.getByText('2019-03').closest('tr') as HTMLElement;
    expect(within(satir).getByText('0,00')).toBeInTheDocument();
    expect(within(satir).getByTestId('sifir-belirsiz')).toBeInTheDocument();
  });

  it('PTF 0 değeri İŞARETLENMEZ (PTF kolonunda varsayılan yok)', () => {
    ciz([kayit({ ptf_tl_per_mwh: 0, yekdem_tl_per_mwh: 78.9 })]);
    const satir = screen.getByText('2019-03').closest('tr') as HTMLElement;
    expect(within(satir).queryByTestId('sifir-belirsiz')).not.toBeInTheDocument();
  });

  it('kaynak ve değişiklik nedeni gerçek yanıt alanlarından gösterilir', () => {
    ciz([kayit({ source: 'manual_override', change_reason: 'izole kabul' })]);
    const satir = screen.getByText('2019-03').closest('tr') as HTMLElement;
    expect(within(satir).getByText('manual_override')).toBeInTheDocument();
    expect(within(satir).getByText('izole kabul')).toBeInTheDocument();
  });
});
