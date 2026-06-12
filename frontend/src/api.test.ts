import { describe, it, expect, vi, afterEach } from 'vitest';
import { buildPdfFormFields, downloadPdf, PdfMismatchError, pdfErrorFromElectronResult, getEpiasPrices, fullProcess, api } from './api';

const extraction: any = {
  consumption_kwh: { value: 1000 },
  current_active_unit_price_tl_per_kwh: { value: 2 },
  distribution_unit_price_tl_per_kwh: { value: 0 },
  invoice_total_with_vat_tl: { value: 8000 },
  vendor: 'enerjisa',
  invoice_period: '2026-05',
};

const calculation: any = {
  current_energy_tl: 2000,
  current_distribution_tl: 0,
  current_btv_tl: 20,
  current_vat_tl: 400,
  current_vat_matrah_tl: 2020,
  current_total_with_vat_tl: 99999, // türetilmiş — invoice_total_raw'a ASLA gitmemeli
  offer_energy_tl: 1000,
  offer_distribution_tl: 0,
  offer_btv_tl: 10,
  offer_vat_tl: 200,
  offer_vat_matrah_tl: 1010,
  offer_total_with_vat_tl: 1210,
  difference_incl_vat_tl: 5,
  savings_ratio: 0.2,
};

const params = {
  weighted_ptf_tl_per_mwh: 590.9,
  yekdem_tl_per_mwh: 563.78,
  agreement_multiplier: 1.01,
};

describe('buildPdfFormFields — R2 ham toplam + onay alanları', () => {
  it('invoice_total_raw params.invoice_total_raw değerinden gelir (calculation.current_total DEĞİL)', () => {
    const f = buildPdfFormFields(extraction, calculation, { ...params, invoice_total_raw: 8000 });
    expect(f.invoice_total_raw).toBe('8000');
    expect(f.invoice_total_raw).not.toBe('99999'); // güvenlik: türetilmiş değer sızmaz
  });

  it('operator_confirmed_warnings boolean -> "true"/"false" string', () => {
    expect(buildPdfFormFields(extraction, calculation, { ...params, operator_confirmed_warnings: true }).operator_confirmed_warnings).toBe('true');
    expect(buildPdfFormFields(extraction, calculation, { ...params, operator_confirmed_warnings: false }).operator_confirmed_warnings).toBe('false');
  });

  it('invoice_total_raw verilmezse "0" (guard kıyası atlar)', () => {
    expect(buildPdfFormFields(extraction, calculation, params).invoice_total_raw).toBe('0');
  });
});

describe('getEpiasPrices — SoT-X weighted PTF query', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  const body = {
    period: '2025-01', ptf_tl_per_mwh: 2500, yekdem_tl_per_mwh: 300,
    source: 'db', source_description: 'x',
    weighted_ptf_tl_per_mwh: 1550, weighted_ptf_profile: 'puant_agir',
    weighted_ptf_source: 'hourly_weighted:puant_agir', ptf_source_warning: null,
  };

  it('profile + tariff_group query string olarak gönderilir', async () => {
    const spy = vi.spyOn(api, 'get').mockResolvedValue({ data: body } as any);
    const res = await getEpiasPrices('2025-01', true, 'puant_agir', 'Sanayi OG');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('/api/epias/prices/2025-01?');
    expect(url).toContain('auto_fetch=true');
    expect(url).toContain('profile=puant_agir');
    expect(url).toContain('tariff_group=Sanayi+OG');
    expect(res.weighted_ptf_tl_per_mwh).toBe(1550);
  });

  it('profile/tariff_group verilmezse query parametre eklenmez (geriye-uyum)', async () => {
    const spy = vi.spyOn(api, 'get').mockResolvedValue({ data: body } as any);
    await getEpiasPrices('2025-01');
    const url = spy.mock.calls[0][0] as string;
    expect(url).toContain('auto_fetch=true');
    expect(url).not.toContain('profile=');
    expect(url).not.toContain('tariff_group=');
  });

  it('Seviye 2-b: customerId verilince customer_id query eklenir; verilmeyince eklenmez', async () => {
    const spy = vi.spyOn(api, 'get').mockResolvedValue({ data: body } as any);
    await getEpiasPrices('2025-01', true, 'puant_agir', undefined, 'cansu');
    expect(spy.mock.calls[0][0] as string).toContain('customer_id=cansu');
    await getEpiasPrices('2025-01', true, 'puant_agir');
    expect(spy.mock.calls[1][0] as string).not.toContain('customer_id=');
  });
});

describe('fullProcess — C2 customer_id query', () => {
  afterEach(() => { vi.restoreAllMocks(); });
  const file = new File([new Uint8Array([1])], 'fatura.pdf');

  it('customer_id verilince /full-process query string içine girer', async () => {
    const spy = vi.spyOn(api, 'post').mockResolvedValue({ data: {} } as any);
    await fullProcess(file, { agreement_multiplier: 1.05, customer_id: 'cansu' });
    expect(spy.mock.calls[0][0] as string).toContain('customer_id=cansu');
  });

  it('customer_id verilmeyince query eklenmez (geriye-uyum)', async () => {
    const spy = vi.spyOn(api, 'post').mockResolvedValue({ data: {} } as any);
    await fullProcess(file, { agreement_multiplier: 1.05 });
    expect(spy.mock.calls[0][0] as string).not.toContain('customer_id=');
  });
});

function mockJsonResponse(status: number, body: any) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (h: string) => (h.toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: async () => body,
  } as any;
}

describe('downloadPdf — 422 extraction_mismatch yapısal hata', () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it('confirmable mismatch -> PdfMismatchError (requires_operator_confirmation=true)', async () => {
    const contract = {
      code: 'extraction_mismatch',
      blocking_errors: [],
      confirmable_warnings: [{ field: 'invoice_total_raw', delta_pct: 20, kind: 'total' }],
      requires_operator_confirmation: true,
      message: 'fark var',
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockJsonResponse(422, { error: contract })));
    await expect(downloadPdf(extraction, calculation, params, 'x.pdf')).rejects.toMatchObject({
      name: 'PdfMismatchError',
    });
    try {
      await downloadPdf(extraction, calculation, params, 'x.pdf');
    } catch (e: any) {
      expect(e).toBeInstanceOf(PdfMismatchError);
      expect(e.contract.requires_operator_confirmation).toBe(true);
    }
  });

  it('blocking mismatch -> PdfMismatchError (requires_operator_confirmation=false)', async () => {
    const contract = {
      code: 'extraction_mismatch',
      blocking_errors: [{ field: 'invoice_total_raw', delta_pct: 62, kind: 'total' }],
      confirmable_warnings: [],
      requires_operator_confirmation: false,
      message: 'büyük sapma',
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockJsonResponse(422, { error: contract })));
    try {
      await downloadPdf(extraction, calculation, params, 'x.pdf');
      throw new Error('beklenen hata atılmadı');
    } catch (e: any) {
      expect(e).toBeInstanceOf(PdfMismatchError);
      expect(e.contract.requires_operator_confirmation).toBe(false);
      expect(e.contract.blocking_errors.length).toBeGreaterThan(0);
    }
  });

  it('mismatch olmayan JSON hata -> generic Error (PdfMismatchError DEĞİL)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockJsonResponse(422, { error: { code: 'invalid_ptf', message: 'PTF zorunlu' } })));
    try {
      await downloadPdf(extraction, calculation, params, 'x.pdf');
      throw new Error('beklenen hata atılmadı');
    } catch (e: any) {
      expect(e).not.toBeInstanceOf(PdfMismatchError);
      expect(e.message).toContain('PTF');
    }
  });
});

describe('pdfErrorFromElectronResult — Electron IPC contract → hata tipi', () => {
  it('mismatch confirmable -> PdfMismatchError (req_confirm=true)', () => {
    const e = pdfErrorFromElectronResult({
      ok: false,
      mismatch: { blocking_errors: [], confirmable_warnings: [{ field: 'invoice_total_raw', delta_pct: 20, kind: 'total' }], requires_operator_confirmation: true, message: 'fark var' },
    });
    expect(e).toBeInstanceOf(PdfMismatchError);
    expect((e as PdfMismatchError).contract.requires_operator_confirmation).toBe(true);
  });

  it('mismatch blocking -> PdfMismatchError (req_confirm=false)', () => {
    const e = pdfErrorFromElectronResult({
      ok: false,
      mismatch: { blocking_errors: [{ field: 'invoice_total_raw', delta_pct: 62, kind: 'total' }], confirmable_warnings: [], requires_operator_confirmation: false, message: 'büyük sapma' },
    });
    expect(e).toBeInstanceOf(PdfMismatchError);
    expect((e as PdfMismatchError).contract.requires_operator_confirmation).toBe(false);
    expect((e as PdfMismatchError).contract.blocking_errors.length).toBeGreaterThan(0);
  });

  it('mismatch yok -> generic Error', () => {
    const e = pdfErrorFromElectronResult({ ok: false, error: 'genel hata' });
    expect(e).not.toBeInstanceOf(PdfMismatchError);
    expect(e.message).toBe('genel hata');
  });

  it('retry_after -> generic "meşgul" Error', () => {
    const e = pdfErrorFromElectronResult({ ok: false, retry_after: 5 });
    expect(e).not.toBeInstanceOf(PdfMismatchError);
    expect(e.message).toContain('meşgul');
  });
});
