// =============================================================================
// FiyatOnayPaneli — kullanıcı akışı (ağsız; adminApi sahte)
// =============================================================================
// Aday al → dönem/değer/yöntem/TAM versiyon/segment göster → kanıtı incele →
// yetkili onaya gönder. Eksik yetki/kimlik bilgisi açık gösterilir; sır tarayıcıya gelmez.
// =============================================================================

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));
vi.mock('../../../api', () => ({ adminApi: { get: getMock, post: postMock } }));

import { FiyatOnayPaneli, yekdemTakipNotu } from '../FiyatOnayPaneli';
import { onayHatasiniSiniflandir } from '../fiyatOnayApi';

const V1 = '2026-07-01T00:00:00+03:00';
const V2 = '2026-07-20T00:00:00+03:00';

const saatlik = (ek: Record<string, unknown> = {}) => ({
  beklenen_saat: 744, donen_kalem: 744, tekil_gecerli_saat: 744, eksik_saat: 0, eksik_ornek: [],
  tekrarlanan_saat: 0, donem_disi_saat: 0, gecersiz_saat_damgasi: 0, gecersiz_fiyat: 0,
  ilk_saat: '2026-07-01T00:00:00+03:00', son_saat: '2026-07-31T23:00:00+03:00', sayfa_toplam: null,
  saatlik_ortalama: '2699.6149999', yayimlanan_price_avg: '2699.61', ortalama_farki: '0.0049999',
  yuvarlama_toleransi: '0.005', saatlik_veri_sha256: 'h'.repeat(64), onaylanabilir: true, nedenler: [],
  ...ek,
});

const ptfAdayi = (ek: Record<string, unknown> = {}) => ({
  kalem: 'PTF', period: '2026-07', value: 2699.6149999, basis: 'mcp_avg', segment: null, version: null,
  birim: 'TL/MWh', kaynak_kanit_sha256: 'a'.repeat(64),
  kaynak_kanit: {
    kalem: 'PTF', kaynak: 'EPİAŞ Şeffaflık Platformu', servis: '/electricity-service/v1/markets/dam/data/mcp',
    istek: { startDate: '2026-07-01T00:00:00+03:00', endDate: '2026-07-31T23:00:00+03:00' },
    period: '2026-07', alan: 'statistic.priceAvg', priceAvg: '2699.6149999', ptfWeightedAvg: '2753.01',
    yontem: 'mcp_avg', saatlik_dogrulama: saatlik(),
  },
  yontem_etiketi: 'Aylık aritmetik PTF (EPİAŞ)', onaylanabilir: true, onaylanamama_nedenleri: [],
  aday_parmak_izi: 'p'.repeat(64), beklenen_revision: 0, beklenen_kayit_parmak_izi: 'k'.repeat(64),
  mevcut_kayit: { id: 1, ptf_tl_per_mwh: 2699.61, status: 'provisional', source: 'manual_override' },
  gecerli_onay: null,
  ...ek,
});

const yekdemAdayi = (ek: Record<string, unknown> = {}) => ({
  kalem: 'YEKDEM', period: '2026-07', value: 0, basis: null, segment: 'gts', version: V2, birim: 'TL/MWh',
  kaynak_kanit_sha256: 'b'.repeat(64),
  kaynak_kanit: {
    kalem: 'YEKDEM', kaynak: 'EPİAŞ Şeffaflık Platformu', servis: '/electricity-service/v1/renewables/data/unit-cost',
    istek: { startDate: '2026-07-01T00:00:00+03:00', endDate: '2026-07-31T23:00:00+03:00' }, period: '2026-07',
    version: V2, ayni_donem_versiyonlari: [V1, V2], supplierUnitCost: '486.314', unitCost: '0.0',
    segment: 'gts', segment_alani: 'unitCost', resmi_kesinlesme: 'BELIRSIZ',
  },
  segment_etiketi: 'GTŞ-K1', onaylanabilir: true, onaylanamama_nedenleri: [],
  aday_parmak_izi: 'y'.repeat(64), beklenen_revision: 0, gecerli_onay: null,
  ...ek,
});

const hata = (status: number, detail: unknown) => ({ response: { status, data: { detail } } });

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
});

function adayIste(yanit: unknown) {
  getMock.mockImplementation((yol: string) => {
    if (yol === '/admin/market-prices/approval-candidate') return Promise.resolve({ data: yanit });
    if (yol === '/admin/market-prices/approvals') {
      return Promise.resolve({ data: { period: '2026-07', altyapi: true, ptf: [], yekdem: [] } });
    }
    return Promise.reject(new Error('beklenmeyen: ' + yol));
  });
}

describe('FiyatOnayPaneli — kullanıcı akışı', () => {
  it('açılışta hiçbir istek başlatmaz ve parola/TGT alanı yoktur', () => {
    const { container } = render(<FiyatOnayPaneli varsayilanDonem="2026-07" />);
    expect(getMock).not.toHaveBeenCalled();
    expect(postMock).not.toHaveBeenCalled();
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(container.textContent).not.toMatch(/EPIAS_PASSWORD|TGT-/);
  });

  it('PTF adayı: değer tam hassasiyet, yöntem ve saatlik kanıt gösterilir; onay değer göndermez', async () => {
    adayIste(ptfAdayi());
    postMock.mockResolvedValue({ data: { kalem: 'PTF', period: '2026-07', revision: 1, value: 2699.6149999,
      basis: 'mcp_avg', kaynak_kanit_sha256: 'a'.repeat(64), onaylayan_beyan: 'Yetkili',
      onaylayan_dogrulandi: false, dogrulanan_yetki: 'paylasilan_yonetici_anahtari' } });
    render(<FiyatOnayPaneli varsayilanDonem="2026-07" />);
    fireEvent.click(screen.getByRole('button', { name: 'Resmî adayı getir' }));

    const aday = await screen.findByTestId('onay-adayi');
    expect(getMock).toHaveBeenCalledWith('/admin/market-prices/approval-candidate',
      { params: { period: '2026-07', kalem: 'PTF' } });
    expect(within(aday).getByTestId('aday-degeri').textContent).toContain('2699.6149999');
    expect(aday.textContent).toContain('Aylık aritmetik PTF (EPİAŞ)');
    const kanit = within(aday).getByTestId('kaynak-kaniti');
    expect(kanit.textContent).toContain('744/744 saat');
    expect(kanit.textContent).toContain('statistic.priceAvg');
    expect(kanit.textContent).toContain('a'.repeat(64));

    expect(within(aday).getByTestId('beyan-notu')).toHaveTextContent('kişiyi doğrulamaz');
    const gonder = screen.getByRole('button', { name: 'Yetkili onaya gönder' });
    expect(gonder).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Onaylayan adı (beyan)'), { target: { value: 'Yetkili' } });
    expect(gonder).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Onay gerekçesi'), { target: { value: 'Temmuz resmî değer' } });
    expect(gonder).toBeEnabled();
    fireEvent.click(gonder);

    const sonuc = await screen.findByTestId('onay-sonucu');
    expect(sonuc).toHaveTextContent('revizyon 1');
    expect(sonuc).toHaveTextContent('beyan: Yetkili · yetki: paylaşılan yönetici anahtarı doğrulandı; kişi doğrulanmadı');
    expect(screen.queryByTestId('beyan-notu')).toBeNull();
    const [yol, govde] = postMock.mock.calls[0];
    expect(yol).toBe('/admin/market-prices/approve');
    expect(govde).toEqual({
      period: '2026-07', kalem: 'PTF', segment: null, aday_parmak_izi: 'p'.repeat(64), beklenen_revision: 0,
      beklenen_kayit_parmak_izi: 'k'.repeat(64), onaylayan_beyan: 'Yetkili', change_reason: 'Temmuz resmî değer',
    });
    expect(govde).not.toHaveProperty('value');
    expect(govde).not.toHaveProperty('dogrulanan_yetki');  // yetki istemciden gönderilmez
    expect(govde).not.toHaveProperty('approved_by');
    await waitFor(() => expect(getMock).toHaveBeenCalledWith('/admin/market-prices/approvals',
      { params: { period: '2026-07' } }));
  });

  it('YEKDEM: segment seçilmeden aday istenemez; TAM versiyon ve gerçek 0 gösterilir; takip yalnız bilgi', async () => {
    adayIste(yekdemAdayi());
    render(<FiyatOnayPaneli varsayilanDonem="2026-07" />);
    fireEvent.change(screen.getByLabelText('Onay kalemi'), { target: { value: 'YEKDEM' } });
    const getir = screen.getByRole('button', { name: 'Resmî adayı getir' });
    expect(getir).toBeDisabled();
    expect(screen.getByTestId('yekdem-takip-notu').textContent).toMatch(/yalnız bilgilendirmedir/);
    expect(getMock).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Onay segmenti'), { target: { value: 'gts' } });
    fireEvent.click(getir);
    const aday = await screen.findByTestId('onay-adayi');
    expect(getMock).toHaveBeenCalledWith('/admin/market-prices/approval-candidate',
      { params: { period: '2026-07', kalem: 'YEKDEM', segment: 'gts' } });
    expect(within(aday).getByTestId('aday-versiyonu').textContent).toBe(V2);
    expect(within(aday).getByTestId('aday-degeri').textContent).toMatch(/^0 /);
    expect(aday.textContent).toContain('GTŞ-K1');
    expect(within(aday).getByTestId('kaynak-kaniti').textContent).toContain(`${V1} · ${V2}`);
  });

  it('onaylanamaz aday nedenleriyle gösterilir ve gönderilemez', async () => {
    adayIste(ptfAdayi({ onaylanabilir: false, onaylanamama_nedenleri: ['eksik_saat', 'ortalama_yuvarlama_tutarsiz'] }));
    render(<FiyatOnayPaneli varsayilanDonem="2026-07" />);
    fireEvent.click(screen.getByRole('button', { name: 'Resmî adayı getir' }));
    const engel = await screen.findByTestId('onaylanamaz');
    expect(engel.textContent).toContain('Dönemin bazı saatleri eksik.');
    expect(engel.textContent).toContain('0,005');
    fireEvent.change(screen.getByLabelText('Onaylayan adı (beyan)'), { target: { value: 'Y' } });
    fireEvent.change(screen.getByLabelText('Onay gerekçesi'), { target: { value: 'g' } });
    expect(screen.getByRole('button', { name: 'Yetkili onaya gönder' })).toBeDisabled();
  });

  it('409 aday_degisti: hiçbir şey yazılmadı mesajı + yeni aday gösterilir, otomatik yeniden gönderim yok', async () => {
    adayIste(yekdemAdayi({ version: V1, kaynak_kanit: { ...yekdemAdayi().kaynak_kanit, version: V1 } }));
    postMock.mockRejectedValue(hata(409, { error: 'aday_degisti', message: 'x', yeni_aday: yekdemAdayi() }));
    render(<FiyatOnayPaneli varsayilanDonem="2026-07" />);
    fireEvent.change(screen.getByLabelText('Onay kalemi'), { target: { value: 'YEKDEM' } });
    fireEvent.change(screen.getByLabelText('Onay segmenti'), { target: { value: 'gts' } });
    fireEvent.click(screen.getByRole('button', { name: 'Resmî adayı getir' }));
    expect((await screen.findByTestId('aday-versiyonu')).textContent).toBe(V1);
    fireEvent.change(screen.getByLabelText('Onaylayan adı (beyan)'), { target: { value: 'Y' } });
    fireEvent.change(screen.getByLabelText('Onay gerekçesi'), { target: { value: 'g' } });
    fireEvent.click(screen.getByRole('button', { name: 'Yetkili onaya gönder' }));

    expect(await screen.findByTestId('onay-hatasi')).toHaveTextContent('Hiçbir şey yazılmadı');
    await waitFor(() => expect(screen.getByTestId('aday-versiyonu').textContent).toBe(V2));
    expect(postMock).toHaveBeenCalledTimes(1);
  });

  it.each([
    [hata(503, { error: 'kimlik_bilgisi_eksik', message: 'm' }), 'EPİAŞ kimlik bilgisi tanımlı değil'],
    [hata(503, { error: 'feature_disabled', message: 'm' }), 'EPIAS_COMPARE_ENABLED'],
    [hata(401, { error: 'admin_unauthorized' }), 'anahtarı gönderilmedi'],
  ])('aday hatası ekranda açık gösterilir (%#)', async (h, beklenen) => {
    getMock.mockRejectedValue(h);
    render(<FiyatOnayPaneli varsayilanDonem="2026-07" />);
    fireEvent.click(screen.getByRole('button', { name: 'Resmî adayı getir' }));
    expect(await screen.findByTestId('onay-hatasi')).toHaveTextContent(beklenen);
    expect(screen.queryByTestId('onay-adayi')).toBeNull();
  });

  it('onay geçmişi geçerli/eski revizyonları ve incelenebilir kanıtı gösterir', async () => {
    getMock.mockResolvedValue({ data: {
      period: '2026-07', altyapi: true, resmi_kesinlesme: 'BELIRSIZ',
      ptf: [
        { id: 2, period: '2026-07', revision: 2, value: 2699.61, basis: 'mcp_avg', kaynak_kanit_sha256: 'a'.repeat(64),
          kaynak_kanit: ptfAdayi().kaynak_kanit, onaylayan_beyan: 'B', dogrulanan_yetki: 'paylasilan_yonetici_anahtari', approved_at: '2026-09-13T10:00:00', change_reason: 'g', gecerli: true },
        { id: 1, period: '2026-07', revision: 1, value: 2600, basis: 'mcp_avg', kaynak_kanit_sha256: 'c'.repeat(64),
          kaynak_kanit: ptfAdayi().kaynak_kanit, onaylayan_beyan: 'A', dogrulanan_yetki: 'paylasilan_yonetici_anahtari', approved_at: '2026-09-12T10:00:00', change_reason: 'g', gecerli: false },
      ],
      yekdem: [{ id: 1, period: '2026-07', segment: 'st', revision: 1, value: 0, version: V2, kaynak_kanit_sha256: 'b'.repeat(64),
        kaynak_kanit: yekdemAdayi().kaynak_kanit, onaylayan_beyan: 'C', dogrulanan_yetki: 'paylasilan_yonetici_anahtari', approved_at: '2026-09-13T11:00:00', change_reason: 'g', guncel: true }],
    } });
    render(<FiyatOnayPaneli varsayilanDonem="2026-07" />);
    fireEvent.click(screen.getByRole('button', { name: 'Onay geçmişi' }));
    const g = await screen.findByTestId('onay-gecmisi');
    expect(g.textContent).toContain('geçerli');
    expect(g.textContent).toContain('geçersiz/eski');
    expect(g.textContent).toContain(`YEKDEM · Serbest Tüketici · revizyon 1 · 0 · ${V2} · beyan: C · yetki: paylaşılan`);
    expect(within(g).getAllByTestId('kaynak-kaniti')).toHaveLength(3);
  });

  it('onay tabloları yoksa geçmiş bunu açıkça söyler', async () => {
    getMock.mockResolvedValue({ data: { period: '2026-07', altyapi: false, ptf: [], yekdem: [] } });
    render(<FiyatOnayPaneli varsayilanDonem="2026-07" />);
    fireEvent.click(screen.getByRole('button', { name: 'Onay geçmişi' }));
    expect(await screen.findByTestId('onay-gecmisi')).toHaveTextContent('Onay tabloları bu veritabanında yok');
  });
});

describe('onayHatasiniSiniflandir', () => {
  it.each([
    [hata(403, { error: 'approval_not_configured' }), 'onay_yapilandirilmamis'],
    [hata(401, { error: 'approval_unauthorized' }), 'yetkisiz'],
    [hata(403, { error: 'approval_forbidden' }), 'yasak'],
    [hata(503, { error: 'kimlik_bilgisi_eksik' }), 'kimlik_bilgisi_eksik'],
    [hata(503, { error: 'feature_disabled' }), 'ozellik_kapali'],
    [hata(503, { error: 'onay_altyapisi_yok' }), 'altyapi_yok'],
    [hata(409, { error: 'onay_yarisi', message: 'Önce tamamlandı.' }), 'yeniden_onay'],
    [hata(422, { error: 'aday_dogrulanamadi', message: 'eksik' }), 'dogrulanamadi'],
    [hata(502, { error: 'resmi_veri_alinamadi', message: 'EPİAŞ yanıt vermedi' }), 'sunucu'],
    [{ message: 'Network Error' }, 'ag'],
  ])('%#: doğru türe ayrılır', (h, tur) => {
    expect(onayHatasiniSiniflandir(h).tur).toBe(tur);
  });

  it('YEKDEM takip notu tarih üzerinden hiçbir işlem önermez', () => {
    expect(yekdemTakipNotu('2026-07')).toMatch(/hiçbir teklifi kesinleştirmez/);
    expect(yekdemTakipNotu('gecersiz')).toBe('');
  });
});
