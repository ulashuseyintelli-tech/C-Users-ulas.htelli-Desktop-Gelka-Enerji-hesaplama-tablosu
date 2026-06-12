import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ConsumptionAnalysisPanel from './ConsumptionAnalysisPanel';
import * as api from './api';

// ./api mock — customerSlug gerçeğe yakın, ağ fonksiyonları vi.fn
vi.mock('./api', () => ({
  customerSlug: (s: string) =>
    (s || '').trim().toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, ''),
  pricingGetPeriods: vi.fn(),
  uploadConsumption: vi.fn(),
  uploadMarketData: vi.fn(),
  pricingAnalyze: vi.fn(),
  pricingDownloadPdf: vi.fn(),
  pricingDownloadExcel: vi.fn(),
}));

const analyzeResult: any = {
  status: 'ok',
  period: '2026-01',
  weighted_prices: {
    weighted_ptf_tl_per_mwh: 3000, weighted_smf_tl_per_mwh: 2800,
    arithmetic_avg_ptf: 2800, total_consumption_kwh: 194412, total_cost_tl: 0, hours_count: 744,
  },
  supplier_cost: { weighted_ptf_tl_per_mwh: 3000, yekdem_tl_per_mwh: 160, imbalance_tl_per_mwh: 2, total_cost_tl_per_mwh: 3162 },
  pricing: {
    multiplier: 1.05, sales_price_tl_per_mwh: 0, gross_margin_tl_per_mwh: 0, dealer_commission_tl_per_mwh: 0,
    net_margin_tl_per_mwh: -900, total_sales_tl: 0, total_cost_tl: 0, total_gross_margin_tl: 0,
    total_dealer_commission_tl: 0, total_net_margin_tl: 0,
    risk_flags: [{ type: 'LOSS_RISK', priority: 1, message: 'Net marj negatif — teklif zarar üretir' }],
  },
  loss_map: {
    total_loss_hours: 333, total_loss_tl: -1000, by_time_zone: {},
    worst_hours: [{ date: '2026-01-01', hour: 17, ptf: 3400, sales_price: 431, loss_tl: -47 }],
  },
  warnings: [],
  cache_hit: false,
};

describe('ConsumptionAnalysisPanel — uçtan uca akış', () => {
  beforeEach(() => {
    (api.pricingGetPeriods as any).mockResolvedValue({
      status: 'ok', market_data_periods: ['2026-01'], yekdem_periods: ['2026-01'],
    });
    (api.uploadConsumption as any).mockResolvedValue({
      status: 'success', customer_id: 'cansu',
      profiles: [{ period: '2026-01', total_rows: 744, total_kwh: 194412, negative_hours: 0, quality_score: 100, profile_id: 1, version: 1 }],
    });
    (api.pricingAnalyze as any).mockResolvedValue(analyzeResult);
    (api.pricingDownloadPdf as any).mockResolvedValue(new Blob(['pdf']));
    (api.pricingDownloadExcel as any).mockResolvedValue(new Blob(['xlsx']));
    // jsdom: blob indirme için stub
    (URL as any).createObjectURL = vi.fn(() => 'blob:x');
    (URL as any).revokeObjectURL = vi.fn();
  });
  afterEach(() => { vi.clearAllMocks(); });

  it('header → upload → analiz → weighted PTF / PTF üstü / worst hours / PDF', async () => {
    const user = userEvent.setup();
    render(<ConsumptionAnalysisPanel onBack={vi.fn()} />);
    await waitFor(() => expect(api.pricingGetPeriods).toHaveBeenCalled());

    // Firma + dosya yükle
    await user.type(screen.getByPlaceholderText('Örn: Cansu Enerji'), 'Cansu');
    const file = new File([new Uint8Array([1, 2, 3])], 'cansu.xlsx');
    await user.upload(screen.getByLabelText('Tüketim Excel'), file);
    await user.click(screen.getByRole('button', { name: 'Yükle' }));

    await waitFor(() => expect(api.uploadConsumption).toHaveBeenCalled());
    const [, custId] = (api.uploadConsumption as any).mock.calls[0];
    expect(custId).toBe('cansu');
    // Yüklenen dönem profil listesinde (satır metni benzersiz)
    expect(await screen.findByText(/744 saat/)).toBeInTheDocument();

    // Analiz
    await user.click(screen.getByRole('button', { name: /Analiz Et/ }));
    await waitFor(() => expect(api.pricingAnalyze).toHaveBeenCalled());

    // Verdict: weighted (3000) > arithmetic (2800) → ÜSTÜNDE
    expect(screen.getByTestId('verdict-direction')).toHaveTextContent('ÜSTÜNDE');
    expect(screen.getByTestId('weighted-ptf')).toHaveTextContent('3.000');
    // worst hour satırı
    expect(screen.getByText('17:00')).toBeInTheDocument();
    // satış-altı saat
    expect(screen.getByText('333 / 744')).toBeInTheDocument();

    // PDF rapor
    await user.click(screen.getByRole('button', { name: /PDF Rapor/ }));
    await waitFor(() => expect(api.pricingDownloadPdf).toHaveBeenCalled());
  });

  it('seçili dönemde piyasa verisi yoksa amber uyarı + market upload açılır', async () => {
    (api.uploadConsumption as any).mockResolvedValue({
      status: 'success', customer_id: 'x',
      profiles: [{ period: '2026-09', total_rows: 720, total_kwh: 1000, negative_hours: 0, quality_score: 100, profile_id: 2, version: 1 }],
    });
    const user = userEvent.setup();
    render(<ConsumptionAnalysisPanel onBack={vi.fn()} />);
    await waitFor(() => expect(api.pricingGetPeriods).toHaveBeenCalled());

    await user.type(screen.getByPlaceholderText('Örn: Cansu Enerji'), 'X');
    await user.upload(screen.getByLabelText('Tüketim Excel'), new File([new Uint8Array([1])], 'x.xlsx'));
    await user.click(screen.getByRole('button', { name: 'Yükle' }));
    await waitFor(() => expect(api.uploadConsumption).toHaveBeenCalled());

    // 2026-09 market listesinde yok → market upload bölümü görünür
    expect(await screen.findByLabelText('Piyasa Excel')).toBeInTheDocument();
    // Analiz denenince pricingAnalyze ÇAĞRILMAZ (proaktif guard)
    await user.click(screen.getByRole('button', { name: /Analiz Et/ }));
    expect(api.pricingAnalyze).not.toHaveBeenCalled();
  });
});
