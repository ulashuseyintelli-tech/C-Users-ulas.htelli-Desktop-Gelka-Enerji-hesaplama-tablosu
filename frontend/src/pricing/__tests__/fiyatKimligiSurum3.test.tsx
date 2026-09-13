import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {
  evaluatePriceReadiness,
  priceSourceLabel,
  type PriceProvenance,
  type PriceReadinessInput,
} from '../priceReadiness';
import { YekdemSegmentSelector } from '../YekdemSegmentSelector';
import { buildPeriodPricesUrl } from '../usePeriodPriceFetch';
import { api, createOffer } from '../../api';

// Fiyat kimliği (sürüm 3, OWNER-KARARI-01/02): istemci kapısı sunucunun yansımasıdır.
// Kesin teklif: yetkili onaylı aylık aritmetik PTF + (dahilse) açıkça seçilmiş segmentin
// onaylı YEKDEM'i. Segment varsayılanı YOK; eşitlik otomatik seçim gerekçesi değil.

const onayliPtf = {
  status: 'matched', trusted: true, final: true, system_verified: true,
  source: 'monthly_arithmetic:mcp_avg', approval: { revision: 1, basis: 'mcp_avg' },
};

const kesinV3: PriceProvenance = {
  version: 3,
  ptf: onayliPtf,
  yekdem: { mode: 'included', segment: 'st', status: 'matched', value: 486.314, system_verified: true },
};

const temel: PriceReadinessInput = {
  ptf: 2699.61,
  yekdem: 486.314,
  yekdemMode: 'included',
  provenance: kesinV3,
  valuesEditedByUser: false,
  yekdemSegment: 'st',
};

describe('evaluatePriceReadiness — sürüm 3', () => {
  it('onaylı PTF + seçilen segmentte onaylı YEKDEM → hazır', () => {
    expect(evaluatePriceReadiness(temel)).toMatchObject({ ready: true, reasons: [] });
  });

  it('kimliksiz kesin PTF kesin teklife yetmez', () => {
    const r = evaluatePriceReadiness({
      ...temel,
      provenance: { ...kesinV3, ptf: { status: 'matched', trusted: true, final: true, system_verified: false } },
    });
    expect(r.ready).toBe(false);
    expect(r.reasons.join(' ')).toContain('yetkili onay yok');
  });

  it('segment seçilmeden hazır değil (değerler eşit olsa bile)', () => {
    const r = evaluatePriceReadiness({ ...temel, yekdemSegment: null });
    expect(r.ready).toBe(false);
    expect(r.reasons.join(' ')).toContain('segmenti seçilmedi');
  });

  it('seçilen segmentte onay yoksa taslak; hariç seçimine dönmez', () => {
    const r = evaluatePriceReadiness({
      ...temel,
      provenance: { ...kesinV3, yekdem: { mode: 'included', segment: 'gts', status: 'approval_missing', value: 470 } },
      yekdemSegment: 'gts',
      yekdem: 470,
    });
    expect(r.ready).toBe(false);
    expect(r.reasons.join(' ')).toContain('onaylı YEKDEM yok');
  });

  it('onaylı değerden farklı YEKDEM engellenir', () => {
    const r = evaluatePriceReadiness({ ...temel, yekdem: 470.0 });
    expect(r.ready).toBe(false);
  });

  it('hariç seçiminde segment istenmez', () => {
    const r = evaluatePriceReadiness({
      ...temel, yekdemMode: 'excluded', yekdem: null, yekdemSegment: null,
      provenance: { ...kesinV3, yekdem: { mode: 'excluded', status: 'excluded' } },
    });
    expect(r.ready).toBe(true);
  });

  it('yöntem etiketi aritmetiği ağırlıklı diye sunmaz', () => {
    expect(priceSourceLabel('monthly_arithmetic:mcp_avg')).toContain('Aylık aritmetik PTF');
    expect(priceSourceLabel('monthly_arithmetic:mcp_avg')).not.toContain('ağırlıklı');
  });
});

describe('YekdemSegmentSelector', () => {
  it('varsayılan seçim yok; kullanıcı açıkça seçer', () => {
    const onChange = vi.fn();
    render(<YekdemSegmentSelector value={null} onChange={onChange} />);
    expect(screen.getByText('(seçilmedi)')).toBeTruthy();
    const radyolar = screen.getAllByRole('radio') as HTMLInputElement[];
    expect(radyolar.every((r) => !r.checked)).toBe(true);
    fireEvent.click(screen.getByLabelText('GTŞ-K1'));
    expect(onChange).toHaveBeenCalledWith('gts');
  });
});

describe('segment istek parametreleri', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('dönem fiyatı isteği segmenti taşır; verilmezse eklemez', () => {
    expect(buildPeriodPricesUrl('2026-07', 'duz', undefined, undefined, 'st')).toContain('yekdem_segment=st');
    expect(buildPeriodPricesUrl('2026-07', 'duz')).not.toContain('yekdem_segment');
  });

  it('teklif kaydı segmenti query param olarak gönderir; seçilmemişse göndermez', async () => {
    const spy = vi.spyOn(api, 'post').mockResolvedValue({ data: { id: 1 } } as any);
    const params = { weighted_ptf_tl_per_mwh: 2699.61, yekdem_tl_per_mwh: 486.314, agreement_multiplier: 1.01 };
    await createOffer({} as any, {} as any, params, undefined,
      { invoice_total_raw: 2880, yekdem_mode: 'included', yekdem_segment: 'gts' });
    expect((spy.mock.calls[0] as any[])[2].params).toMatchObject({ yekdem_mode: 'included', yekdem_segment: 'gts' });
    await createOffer({} as any, {} as any, params, undefined,
      { invoice_total_raw: 2880, yekdem_mode: 'included', yekdem_segment: null });
    expect((spy.mock.calls[1] as any[])[2].params).not.toHaveProperty('yekdem_segment');
  });
});
