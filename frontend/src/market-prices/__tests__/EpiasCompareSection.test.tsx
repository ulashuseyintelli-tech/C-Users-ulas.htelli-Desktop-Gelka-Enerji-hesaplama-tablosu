// =============================================================================
// EpiasCompareSection — ağsız testler
// =============================================================================
// Fikstürler 2026-09-12 yerel kabul verilerinden TÜRETİLMİŞTİR; sır içermez
// (parola, TGT, kullanıcı adı ve yerel dosya yolu YOK).
// =============================================================================

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

// vi.mock dosya başına hoist edilir; değişken vi.hoisted ile tanımlanmalı.
const { getMock } = vi.hoisted(() => ({ getMock: vi.fn() }));
vi.mock('../../api', () => ({ adminApi: { get: getMock, post: vi.fn() } }));

import { EpiasCompareSection } from '../epiasCompare/EpiasCompareSection';
import type { KarsilastirmaSatiri, KarsilastirmaYaniti } from '../epiasCompare/types';

const ALANLAR = (o: Partial<KarsilastirmaSatiri['alanlar']> = {}) => ({
  donem: 'eslesti' as const,
  birim: 'eslesti' as const,
  yontem: 'uygulanamaz' as const,
  versiyon: 'uygulanamaz' as const,
  segment: 'uygulanamaz' as const,
  ...o,
});

const gelka = (o: Partial<KarsilastirmaSatiri['gelka']> = {}): KarsilastirmaSatiri['gelka'] => ({
  deger: 590.9, birim: 'TL/MWh', yontem: null, versiyon: null, segment: null,
  kaynak_jetonu: 'manual_override', durum: 'provisional', kayit_id: 20, ...o,
});

/** Kimlik doğrulanmış ve DEĞER FARKI olan satır (2026-05 gerçek rakamları). */
const SATIR_DEGER_FARKI: KarsilastirmaSatiri = {
  donem: '2026-05', kalem: 'PTF', tip: 'DEGER_FARKI', nedenler: [],
  alanlar: ALANLAR({ yontem: 'eslesti' }), fark: -204.8,
  gelka: gelka({ deger: 590.9, yontem: 'mcp_avg', kaynak_jetonu: 'epias_api.mcp_avg' }),
  epias: { donem: '2026-05', birim: 'TL/MWh', yontem: 'mcp_avg', versiyon: 'uygulanamaz',
           segment: 'uygulanamaz', deger: 795.7, kesinlik: 'BELIRSIZ' },
};

/** Kimliği BİLİNMEYEN satır (2026-07 canlı kabulündeki gerçek durum). */
const SATIR_KIMLIK_YOK: KarsilastirmaSatiri = {
  donem: '2026-07', kalem: 'PTF', tip: 'KARSILASTIRILAMAZ', nedenler: ['yontem_bilinmiyor'],
  alanlar: ALANLAR({ yontem: 'bilinmiyor' }), fark: null,
  gelka: gelka({ deger: 2699.61, kayit_id: 5 }),
  epias: { donem: '2026-07', birim: 'TL/MWh', yontem: 'mcp_avg', versiyon: 'uygulanamaz',
           segment: 'uygulanamaz', deger: 2699.61, kesinlik: 'BELIRSIZ' },
};

const SATIR_YONTEM_UYUSMAZ: KarsilastirmaSatiri = {
  donem: '2026-06', kalem: 'PTF', tip: 'YONTEM_UYUSMAZLIGI', nedenler: ['yontem_uyusmuyor'],
  alanlar: ALANLAR({ yontem: 'uyusmuyor' }), fark: null,
  gelka: gelka({ deger: 1240.16, yontem: 'mcp_wavg', kaynak_jetonu: 'epias_api.mcp_wavg' }),
  epias: { donem: '2026-06', birim: 'TL/MWh', yontem: 'mcp_avg', versiyon: 'uygulanamaz',
           segment: 'uygulanamaz', deger: 1240.16, kesinlik: 'BELIRSIZ' },
};

const SATIR_VERSIYON_UYUSMAZ: KarsilastirmaSatiri = {
  donem: '2026-05', kalem: 'YEKDEM', tip: 'VERSIYON_UYUSMAZLIGI', nedenler: ['versiyon_uyusmuyor'],
  alanlar: ALANLAR({ yontem: 'uygulanamaz', versiyon: 'uyusmuyor', segment: 'eslesti' }), fark: null,
  gelka: gelka({ deger: 1306.1, versiyon: '2026-05', segment: 'st' }),
  epias: { donem: '2026-05', birim: 'TL/MWh', yontem: 'uygulanamaz', versiyon: '2026-08',
           segment: 'st', deger: 1317.236, kesinlik: 'BELIRSIZ' },
};

const SATIR_SEGMENT_UYUSMAZ: KarsilastirmaSatiri = {
  donem: '2026-04', kalem: 'YEKDEM', tip: 'SEGMENT_UYUSMAZLIGI', nedenler: ['segment_uyusmuyor'],
  alanlar: ALANLAR({ yontem: 'uygulanamaz', versiyon: 'eslesti', segment: 'uyusmuyor' }), fark: null,
  gelka: gelka({ deger: 1038.34, versiyon: '2026-04', segment: 'st' }),
  epias: { donem: '2026-04', birim: 'TL/MWh', yontem: 'uygulanamaz', versiyon: '2026-04',
           segment: 'gts', deger: 1038.343, kesinlik: 'BELIRSIZ' },
};

const SATIR_API_HATASI: KarsilastirmaSatiri = {
  donem: '2026-03', kalem: 'PTF', tip: 'KARSILASTIRILAMAZ', nedenler: ['api_hatasi'],
  alanlar: ALANLAR({ yontem: 'bilinmiyor' }), fark: null,
  gelka: gelka({ deger: 1620.32 }), epias: null,
};

function yanit(satirlar: KarsilastirmaSatiri[], uyarilar: KarsilastirmaYaniti['uyarilar'] = []): KarsilastirmaYaniti {
  return {
    kayit: 'EPIAS-KARSILASTIRMA', olusturuldu_utc: '2026-09-12T10:46:15+00:00',
    evaluated_at: '2026-09-12', gecmis_tarih_kaniti: 'uygulanamaz', tolerans_tl_mwh: 0.005,
    donemler: ['2026-03', '2026-04', '2026-05', '2026-06', '2026-07'],
    uyarilar, satirlar, ozet: {}, not: 'Salt okunur. EPİAŞ adayı kesinleşmiş SAYILMAZ.',
  };
}

const hata = (status: number, detail?: unknown) => ({ response: { status, data: { detail } } });

beforeEach(() => { getMock.mockReset(); });

async function karsilastirDugmesineBas() {
  await userEvent.click(screen.getByRole('button', { name: 'Karşılaştır' }));
}

describe('EpiasCompareSection — sorgu davranışı', () => {
  it('sayfa açılışında OTOMATİK sorgu başlatmaz', () => {
    render(<EpiasCompareSection fromPeriod="2026-01" toPeriod="2026-07" />);
    expect(getMock).not.toHaveBeenCalled();
    expect(screen.getByTestId('kars-bosta')).toBeInTheDocument();
  });

  it('dönem aralığı yoksa düğme devre dışıdır ve istek atılmaz', () => {
    render(<EpiasCompareSection fromPeriod={null} toPeriod={null} />);
    expect(screen.getByRole('button', { name: 'Karşılaştır' })).toBeDisabled();
    expect(getMock).not.toHaveBeenCalled();
  });

  it('düğmeye basınca doğru uç ve parametrelerle tek istek atar', async () => {
    getMock.mockResolvedValue({ data: yanit([SATIR_DEGER_FARKI]) });
    render(<EpiasCompareSection fromPeriod="2026-05" toPeriod="2026-05" />);
    await karsilastirDugmesineBas();
    await screen.findByTestId('kars-sonuc');
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    expect(getMock.mock.calls[0][0]).toBe('/admin/market-prices/epias-compare');
    expect(getMock.mock.calls[0][1].params).toMatchObject({
      from_period: '2026-05', to_period: '2026-05',
    });
  });
});

describe('EpiasCompareSection — yetki ve özellik durumu', () => {
  it('401: yönetici anahtarı gönderilmedi mesajı', async () => {
    getMock.mockRejectedValue(hata(401));
    render(<EpiasCompareSection fromPeriod="2026-05" toPeriod="2026-05" />);
    await karsilastirDugmesineBas();
    expect(await screen.findByTestId('kars-hata')).toHaveTextContent('Yönetici anahtarı gönderilmedi');
    expect(screen.getByTestId('kars-hata')).toHaveTextContent('Mevcut fiyat kayıtları değişmedi');
  });

  it('403: yetki yok mesajı', async () => {
    getMock.mockRejectedValue(hata(403));
    render(<EpiasCompareSection fromPeriod="2026-05" toPeriod="2026-05" />);
    await karsilastirDugmesineBas();
    expect(await screen.findByTestId('kars-hata')).toHaveTextContent('geçersiz');
  });

  it('503 feature_disabled: özellik kapalı olarak anlatılır', async () => {
    getMock.mockRejectedValue(hata(503, { error: 'feature_disabled', message: 'kapalı' }));
    render(<EpiasCompareSection fromPeriod="2026-05" toPeriod="2026-05" />);
    await karsilastirDugmesineBas();
    const kutu = await screen.findByTestId('kars-hata');
    expect(kutu).toHaveTextContent('Karşılaştırma kapalı');
    expect(kutu).toHaveTextContent('EPIAS_COMPARE_ENABLED');
    expect(kutu.className).toContain('bg-gray-50');
  });

  it('422: geçersiz aralık mesajı sunucudan gelir', async () => {
    getMock.mockRejectedValue(hata(422, { error: 'invalid_range', message: 'En fazla 12 dönem sorgulanabilir.' }));
    render(<EpiasCompareSection fromPeriod="2024-01" toPeriod="2026-07" />);
    await karsilastirDugmesineBas();
    expect(await screen.findByTestId('kars-hata')).toHaveTextContent('En fazla 12 dönem');
  });

  it('ağ hatası: bağlantı mesajı', async () => {
    getMock.mockRejectedValue(new Error('Network Error'));
    render(<EpiasCompareSection fromPeriod="2026-05" toPeriod="2026-05" />);
    await karsilastirDugmesineBas();
    expect(await screen.findByTestId('kars-hata')).toHaveTextContent('Sunucuya ulaşılamadı');
  });
});

describe('EpiasCompareSection — satır gösterimi', () => {
  it('kimliği doğrulanmış satırda fiyat farkı GÖSTERİLİR', async () => {
    getMock.mockResolvedValue({ data: yanit([SATIR_DEGER_FARKI]) });
    render(<EpiasCompareSection fromPeriod="2026-05" toPeriod="2026-05" />);
    await karsilastirDugmesineBas();
    const satir = await screen.findByTestId('kars-satir-2026-05-PTF');
    expect(satir).toHaveTextContent('590,90');
    expect(satir).toHaveTextContent('795,70');
    expect(satir).toHaveTextContent('-204,80');
    expect(satir).toHaveTextContent('Değer farkı');
  });

  it('kimliği BİLİNMEYEN satırda fark GÖSTERİLMEZ, neden ve ÖNERİ görünür', async () => {
    getMock.mockResolvedValue({ data: yanit([SATIR_KIMLIK_YOK]) });
    render(<EpiasCompareSection fromPeriod="2026-07" toPeriod="2026-07" />);
    await karsilastirDugmesineBas();
    const satir = await screen.findByTestId('kars-satir-2026-07-PTF');
    expect(satir).toHaveTextContent('Karşılaştırılamaz');
    expect(satir).toHaveTextContent('Kayıt hangi PTF ortalamasıyla üretildiğini taşımıyor');
    expect(satir).toHaveTextContent('ÖNERİ — hipotez, kanıt değil');
    expect(satir).toHaveTextContent('Aritmetik ortalama');
  });

  it('ÖNERİ kutusu hipotez dilini ve dayanak/belirsizlik ayrımını taşır', async () => {
    getMock.mockResolvedValue({ data: yanit([SATIR_KIMLIK_YOK]) });
    render(<EpiasCompareSection fromPeriod="2026-07" toPeriod="2026-07" />);
    await karsilastirDugmesineBas();
    const satir = await screen.findByTestId('kars-satir-2026-07-PTF');
    expect(satir).toHaveTextContent('Kayda yazılmamıştır');
    expect(satir).toHaveTextContent('geçmiş girişin kaynağını kanıtlamaz');
    expect(satir).toHaveTextContent('Dayanak');
    expect(satir).toHaveTextContent('Belirsizlik');
    expect(satir).toHaveTextContent('yöntem jetonu YOK');
    // Dil denetimi: "kanıtlandı" / "dışlandı" iddiası KULLANILMAZ.
    expect(satir.textContent).not.toMatch(/MEKANİZMA kanıtıdır|kanıt destekli|hipotezi DIŞLANIYOR|dışlanıyor/);
  });

  it('yöntem uyuşmazlığında fark gösterilmez', async () => {
    getMock.mockResolvedValue({ data: yanit([SATIR_YONTEM_UYUSMAZ]) });
    render(<EpiasCompareSection fromPeriod="2026-06" toPeriod="2026-06" />);
    await karsilastirDugmesineBas();
    const satir = await screen.findByTestId('kars-satir-2026-06-PTF');
    expect(satir).toHaveTextContent('Yöntem uyuşmuyor');
    expect(satir).toHaveTextContent('Ağırlıklı ortalama');
  });

  it('versiyon uyuşmazlığı versiyonlarla birlikte gösterilir', async () => {
    getMock.mockResolvedValue({ data: yanit([SATIR_VERSIYON_UYUSMAZ]) });
    render(<EpiasCompareSection fromPeriod="2026-05" toPeriod="2026-05" />);
    await karsilastirDugmesineBas();
    const satir = await screen.findByTestId('kars-satir-2026-05-YEKDEM');
    expect(satir).toHaveTextContent('Versiyon uyuşmuyor');
    expect(satir).toHaveTextContent('2026-08');
  });

  it('segment uyuşmazlığı okunur etiketlerle gösterilir', async () => {
    getMock.mockResolvedValue({ data: yanit([SATIR_SEGMENT_UYUSMAZ]) });
    render(<EpiasCompareSection fromPeriod="2026-04" toPeriod="2026-04" />);
    await karsilastirDugmesineBas();
    const satir = await screen.findByTestId('kars-satir-2026-04-YEKDEM');
    expect(satir).toHaveTextContent('Segment uyuşmuyor');
    expect(satir).toHaveTextContent('Serbest Tüketici');
    expect(satir).toHaveTextContent('GTŞ-K1');
  });

  it('API hatası olan satır ve uyarı listesi anlaşılır gösterilir', async () => {
    getMock.mockResolvedValue({
      data: yanit([SATIR_API_HATASI], [{ kod: 'ptf_api_hatasi', donem: '2026-03', mesaj: 'EPİAŞ yanıtı HTTP 500.' }]),
    });
    render(<EpiasCompareSection fromPeriod="2026-03" toPeriod="2026-03" />);
    await karsilastirDugmesineBas();
    expect(await screen.findByTestId('kars-uyarilar')).toHaveTextContent('HTTP 500');
    expect(screen.getByTestId('kars-satir-2026-03-PTF')).toHaveTextContent('EPİAŞ servisinden veri alınamadı');
  });
});

describe('EpiasCompareSection — salt okunurluk', () => {
  it('yazma/onay/kesinleştirme düğmesi İÇERMEZ', async () => {
    getMock.mockResolvedValue({ data: yanit([SATIR_DEGER_FARKI, SATIR_KIMLIK_YOK]) });
    render(<EpiasCompareSection fromPeriod="2026-05" toPeriod="2026-07" />);
    await karsilastirDugmesineBas();
    await screen.findByTestId('kars-sonuc');
    const dugmeler = screen.getAllByRole('button').map((b) => b.textContent ?? '');
    expect(dugmeler.sort()).toEqual(['Karşılaştır', 'Temizle']);
    for (const yasak of ['Kaydet', 'Onayla', 'Kesinleştir', 'Uygula', 'Güncelle', 'Sil']) {
      expect(dugmeler.join(' ')).not.toContain(yasak);
    }
  });
});

describe('EpiasCompareSection — aday koşulu ve dönem tutarlılığı', () => {
  it('kayıt DEĞERİ değişmişse eski öneri taşınmaz', async () => {
    const degismis: KarsilastirmaSatiri = {
      ...SATIR_KIMLIK_YOK,
      gelka: { ...SATIR_KIMLIK_YOK.gelka, deger: 2750.0 },
    };
    getMock.mockResolvedValue({ data: yanit([degismis]) });
    render(<EpiasCompareSection fromPeriod="2026-07" toPeriod="2026-07" />);
    await karsilastirDugmesineBas();
    const satir = await screen.findByTestId('kars-satir-2026-07-PTF');
    expect(satir).not.toHaveTextContent('ÖNERİ');
    expect(screen.getByTestId('kars-oneri-yok-2026-07-PTF')).toHaveTextContent(
      'Eski öneri otomatik taşınmaz',
    );
  });

  it('kayıt KAYNAK jetonu değişmişse eski öneri taşınmaz', async () => {
    const degismis: KarsilastirmaSatiri = {
      ...SATIR_KIMLIK_YOK,
      gelka: { ...SATIR_KIMLIK_YOK.gelka, kaynak_jetonu: 'epias_manual' },
    };
    getMock.mockResolvedValue({ data: yanit([degismis]) });
    render(<EpiasCompareSection fromPeriod="2026-07" toPeriod="2026-07" />);
    await karsilastirDugmesineBas();
    expect(await screen.findByTestId('kars-oneri-yok-2026-07-PTF')).toBeInTheDocument();
  });

  it('koşullar eşleşiyorsa öneri gösterilir (kontrol)', async () => {
    getMock.mockResolvedValue({ data: yanit([SATIR_KIMLIK_YOK]) });
    render(<EpiasCompareSection fromPeriod="2026-07" toPeriod="2026-07" />);
    await karsilastirDugmesineBas();
    expect(await screen.findByTestId('kars-satir-2026-07-PTF')).toHaveTextContent('ÖNERİ');
    expect(screen.queryByTestId('kars-oneri-yok-2026-07-PTF')).not.toBeInTheDocument();
  });

  it('dönem aralığı değişince ESKİ sonuç yeni başlık altında gösterilmez', async () => {
    getMock.mockResolvedValue({ data: yanit([SATIR_DEGER_FARKI]) });
    const { rerender } = render(<EpiasCompareSection fromPeriod="2026-05" toPeriod="2026-05" />);
    await karsilastirDugmesineBas();
    await screen.findByTestId('kars-sonuc');
    expect(screen.getByTestId('kars-satir-2026-05-PTF')).toBeInTheDocument();

    rerender(<EpiasCompareSection fromPeriod="2026-01" toPeriod="2026-02" />);
    expect(screen.queryByTestId('kars-sonuc')).not.toBeInTheDocument();
    expect(screen.queryByTestId('kars-satir-2026-05-PTF')).not.toBeInTheDocument();
    expect(screen.getByTestId('kars-aralik')).toHaveTextContent('2026-01');
    expect(screen.getByTestId('kars-bosta')).toBeInTheDocument();
  });

  it('dönem aralığı değişince eski HATA da taşınmaz', async () => {
    getMock.mockRejectedValue(hata(503, { error: 'feature_disabled', message: 'kapalı' }));
    const { rerender } = render(<EpiasCompareSection fromPeriod="2026-05" toPeriod="2026-05" />);
    await karsilastirDugmesineBas();
    await screen.findByTestId('kars-hata');
    rerender(<EpiasCompareSection fromPeriod="2026-06" toPeriod="2026-06" />);
    expect(screen.queryByTestId('kars-hata')).not.toBeInTheDocument();
  });
});
