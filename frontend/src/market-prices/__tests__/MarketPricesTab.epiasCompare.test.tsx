// =============================================================================
// MarketPricesTab + EPİAŞ karşılaştırma bölümü — entegrasyon (ağsız)
// =============================================================================
// Amaç: yeni bölüm eklenirken MEVCUT fiyat işlemlerinin bozulmadığını ve
// sayfa açılışında karşılaştırma sorgusunun BAŞLAMADIĞINI doğrulamak.
// =============================================================================

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock('../../api', () => ({ adminApi: { get: getMock, post: postMock } }));

import { MarketPricesTab } from '../MarketPricesTab';

/** GET /admin/market-prices yanıtı (MarketPricesListResponse sözleşmesi). */
const kayit = (period: string, ptf: number, yekdem: number) => ({
  period,
  ptf_tl_per_mwh: ptf,
  yekdem_tl_per_mwh: yekdem,
  status: 'provisional' as const,
  price_type: 'PTF',
  captured_at: '2026-08-18T15:53:02Z',
  updated_at: '2026-08-18T18:42:55Z',
  updated_by: '',
  source: 'manual_override',
  source_note: '',
  change_reason: '',
  is_locked: false,
});

const LISTE = {
  status: 'ok',
  total: 2,
  page: 1,
  page_size: 20,
  items: [kayit('2026-07', 2699.61, 486.31), kayit('2026-05', 590.9, 1306.1)],
};

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  getMock.mockImplementation((yol: string) => {
    if (yol === '/admin/market-prices') return Promise.resolve({ data: LISTE });
    return Promise.reject(new Error('beklenmeyen cagri: ' + yol));
  });
});

describe('MarketPricesTab — EPİAŞ bölümü eklendikten sonra', () => {
  it('mevcut fiyat işlemleri korunur (Toplu Import ve Yeni Kayıt yerinde)', async () => {
    render(<MarketPricesTab />);
    expect(await screen.findByRole('button', { name: 'Toplu Import' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Yeni Kayıt' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Piyasa Fiyatları' })).toBeInTheDocument();
  });

  it('karşılaştırma bölümü görünür ama sayfa açılışında SORGU BAŞLATMAZ', async () => {
    render(<MarketPricesTab />);
    await screen.findByTestId('epias-compare');
    await waitFor(() => expect(getMock).toHaveBeenCalled());
    const cagrilanYollar = getMock.mock.calls.map((c) => c[0]);
    expect(cagrilanYollar).toContain('/admin/market-prices');
    expect(cagrilanYollar).not.toContain('/admin/market-prices/epias-compare');
    expect(screen.getByTestId('kars-bosta')).toBeInTheDocument();
  });

  it('dönem aralığı listedeki kayıtlardan türetilir', async () => {
    render(<MarketPricesTab />);
    expect(await screen.findByTestId('kars-aralik')).toHaveTextContent('2026-05');
    expect(screen.getByTestId('kars-aralik')).toHaveTextContent('2026-07');
  });

  it('hiçbir yazma isteği (POST) yapılmaz', async () => {
    render(<MarketPricesTab />);
    await screen.findByTestId('epias-compare');
    expect(postMock).not.toHaveBeenCalled();
  });
});
