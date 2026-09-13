// =============================================================================
// TaslakTeklifPaneli — taslak PDF + açık kesinleştirme (ağsız)
// =============================================================================

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const { taslakPdfMock, kesinlestirMock } = vi.hoisted(() => ({
  taslakPdfMock: vi.fn(), kesinlestirMock: vi.fn(),
}));
vi.mock('../../api', async () => {
  const gercek = await vi.importActual<typeof import('../../api')>('../../api');
  return { ...gercek, downloadDraftOfferPdf: taslakPdfMock, finalizeOfferPrice: kesinlestirMock };
});

import { FiyatKesinlestirmeHatasi, api, createOffer, downloadDraftOfferPdf, finalizeOfferPrice } from '../../api';
import { TaslakTeklifPaneli } from '../TaslakTeklifPaneli';

beforeEach(() => {
  taslakPdfMock.mockReset();
  kesinlestirMock.mockReset();
});

describe('TaslakTeklifPaneli', () => {
  it('render kesinleştirme ya da PDF isteği başlatmaz; nedenler Türkçe listelenir', () => {
    render(<TaslakTeklifPaneli offerId={7} blockingReasons={['ptf_identity_missing', 'yekdem_approval_missing']}
      yekdemSegment="st" onKesinlesti={vi.fn()} />);
    expect(kesinlestirMock).not.toHaveBeenCalled();
    expect(taslakPdfMock).not.toHaveBeenCalled();
    const nedenler = screen.getByTestId('taslak-nedenleri');
    expect(nedenler).toHaveTextContent('PTF için yetkili onaylı aylık aritmetik değer yok.');
    expect(nedenler).toHaveTextContent('Seçilen segmentte onaylı YEKDEM yok.');
    expect(screen.getByTestId('taslak-teklif-paneli')).toHaveTextContent('kendiliğinden kesinleşmez');
  });

  it('taslak PDF düğmesi yalnız taslak PDF ucunu çağırır', async () => {
    taslakPdfMock.mockResolvedValue(undefined);
    render(<TaslakTeklifPaneli offerId={7} blockingReasons={[]} yekdemSegment={null} onKesinlesti={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: "Taslak PDF'i indir" }));
    await waitFor(() => expect(taslakPdfMock).toHaveBeenCalledWith(7));
    expect(await screen.findByRole('status')).toHaveTextContent('saklanmadı');
    expect(kesinlestirMock).not.toHaveBeenCalled();
  });

  it('açık işlemle kesinleştirme: segment ve kesinleştiren gönderilir, başarıda üst bileşene bildirilir', async () => {
    kesinlestirMock.mockResolvedValue({ offer_id: 7, fiyat_durumu: 'kesin' });
    const onKesinlesti = vi.fn();
    render(<TaslakTeklifPaneli offerId={7} blockingReasons={[]} yekdemSegment="gts" onKesinlesti={onKesinlesti} />);
    fireEvent.change(screen.getByLabelText('Kesinleştiren'), { target: { value: 'Satış' } });
    fireEvent.click(screen.getByRole('button', { name: 'Fiyatı yeniden doğrula ve kesinleştir' }));
    await waitFor(() => expect(onKesinlesti).toHaveBeenCalledWith(7));
    expect(kesinlestirMock).toHaveBeenCalledWith(7, { yekdemSegment: 'gts', kesinlestiren: 'Satış' });
  });

  it('yeniden doğrulama reddinde nedenler gösterilir, kesinleşmiş sayılmaz', async () => {
    kesinlestirMock.mockRejectedValue(new FiyatKesinlestirmeHatasi('price_unverified', 'Fiyat doğrulanmadı.',
      ['yekdem_approval_missing']));
    const onKesinlesti = vi.fn();
    render(<TaslakTeklifPaneli offerId={7} blockingReasons={[]} yekdemSegment="st" onKesinlesti={onKesinlesti} />);
    fireEvent.click(screen.getByRole('button', { name: 'Fiyatı yeniden doğrula ve kesinleştir' }));
    const h = await screen.findByTestId('kesinlestirme-hatasi');
    expect(h).toHaveTextContent('Fiyat doğrulanmadı.');
    expect(h).toHaveTextContent('Seçilen segmentte onaylı YEKDEM yok.');
    expect(onKesinlesti).not.toHaveBeenCalled();
  });
});

describe('api — taslak kayıt / taslak PDF / kesinleştirme sözleşmesi', () => {
  afterEach(() => { vi.restoreAllMocks(); });
  const params = { weighted_ptf_tl_per_mwh: 2500, yekdem_tl_per_mwh: 50, agreement_multiplier: 1.01 };

  it('fiyat_taslak yalnız açıkça istenince gönderilir', async () => {
    const spy = vi.spyOn(api, 'post').mockResolvedValue({ data: { id: 1, fiyat_durumu: 'taslak' } } as any);
    await createOffer({} as any, {} as any, params, undefined, { invoice_total_raw: 2880, yekdem_mode: 'included' });
    expect((spy.mock.calls[0] as any[])[2].params).not.toHaveProperty('fiyat_taslak');
    const yanit = await createOffer({} as any, {} as any, params, undefined,
      { invoice_total_raw: 2880, yekdem_mode: 'included', yekdem_segment: 'st', fiyat_taslak: true });
    expect((spy.mock.calls[1] as any[])[2].params).toMatchObject({ fiyat_taslak: true, yekdem_segment: 'st' });
    expect(yanit.fiyat_durumu).toBe('taslak');
  });

  it('taslak PDF: sunucu taslak başlığı yoksa dosya kaydedilmez', async () => {
    const gercek = await vi.importActual<typeof import('../../api')>('../../api');
    vi.spyOn(api, 'post').mockResolvedValue({ data: new Blob(['%PDF']), headers: {} } as any);
    await expect(gercek.downloadDraftOfferPdf(3)).rejects.toThrow('taslak PDF işaretini döndürmedi');
    expect(downloadDraftOfferPdf).toBe(taslakPdfMock);
  });

  it('taslak PDF: taslak başlığıyla generate-draft-pdf POST edilir', async () => {
    const gercek = await vi.importActual<typeof import('../../api')>('../../api');
    const spy = vi.spyOn(api, 'post').mockResolvedValue(
      { data: new Blob(['%PDF']), headers: { 'x-fiyat-durumu': 'taslak' } } as any);
    const olustur = vi.fn(() => 'blob:taslak');
    const iptal = vi.fn();
    const onceki = { createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL };
    Object.assign(URL, { createObjectURL: olustur, revokeObjectURL: iptal });
    const tikla = vi.fn();
    const gercekOlustur = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation(((ad: string) => {
      const el = gercekOlustur(ad);
      if (ad === 'a') el.click = tikla;  // jsdom gezinmesi tetiklenmez
      return el;
    }) as typeof document.createElement);
    try {
      await gercek.downloadDraftOfferPdf(3);
    } finally {
      Object.assign(URL, onceki);
    }
    expect(tikla).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith('/offers/3/generate-draft-pdf', null, { responseType: 'blob' });
    expect(iptal).toHaveBeenCalledWith('blob:taslak');
  });

  it('kesinleştirme 422 ve 409 gövdeleri tipli hataya çevrilir', async () => {
    const gercek = await vi.importActual<typeof import('../../api')>('../../api');
    const spy = vi.spyOn(api, 'post');
    spy.mockRejectedValueOnce({ response: { status: 422, data: { error: {
      code: 'price_unverified', message: 'Fiyat doğrulanmadı.', blocking_reasons: ['ptf_identity_missing'] } } } });
    await expect(gercek.finalizeOfferPrice(5, { yekdemSegment: 'st', kesinlestiren: ' K ' }))
      .rejects.toMatchObject({ kod: 'price_unverified', blockingReasons: ['ptf_identity_missing'] });
    expect(spy.mock.calls[0]).toEqual(['/offers/5/finalize-price', null,
      { params: { yekdem_segment: 'st', kesinlestiren: 'K' } }]);
    spy.mockRejectedValueOnce({ response: { status: 409, data: { detail: { error: 'zaten_kesin', message: 'kesin' } } } });
    await expect(gercek.finalizeOfferPrice(5, {})).rejects.toBeInstanceOf(gercek.FiyatKesinlestirmeHatasi);
    expect(finalizeOfferPrice).toBe(kesinlestirMock);
  });
});
