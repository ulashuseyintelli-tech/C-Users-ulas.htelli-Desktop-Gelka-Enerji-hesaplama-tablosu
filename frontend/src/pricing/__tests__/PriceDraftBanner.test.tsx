import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PriceDraftBanner } from '../PriceDraftBanner';
import { YekdemModeSelector } from '../YekdemModeSelector';
import { evaluatePriceReadiness, type PriceProvenance, type PriceReadinessInput } from '../priceReadiness';

// Fiyat Doğruluğu Faz 1 (owner teyidi): provisional ya da doğrulanmamış fiyatla
// hesap AÇIKÇA "TASLAK" işaretlenir ve onay kutusu yoktur. YEKDEM seçimi
// dahil / hariç olarak açıkça yapılır; doğrulanmış muafiyet kuralı olmadığından
// "muaf" seçeneği yoktur.

const provisionalKayit: PriceProvenance = {
  version: 2,
  ptf: { status: 'matched', trusted: true, final: false, system_verified: false },
  yekdem: {
    mode: 'included', period_status: 'known', period_value: 300,
    period_record_status: 'provisional', period_trusted: true, period_verified: false,
  },
};

const kesin: PriceProvenance = {
  version: 2,
  ptf: { status: 'matched', trusted: true, final: true, system_verified: true },
  yekdem: {
    mode: 'included', period_status: 'known', period_value: 300,
    period_record_status: 'final', period_trusted: true, period_verified: true,
  },
};

const hazirlik = (provenance: PriceProvenance | null, ek: Partial<PriceReadinessInput> = {}) =>
  evaluatePriceReadiness({
    ptf: 2500, yekdem: 300, yekdemMode: 'included', provenance, valuesEditedByUser: false, ...ek,
  });

describe('PriceDraftBanner', () => {
  it('provisional fiyatla hesap TASLAK işaretlenir; kesin teklif/PDF yapılamaz der', () => {
    render(<PriceDraftBanner readiness={hazirlik(provisionalKayit)} period="2099-01" />);
    const afis = screen.getByTestId('price-draft-banner');
    expect(afis).toHaveTextContent('TASLAK HESAP');
    expect(afis).toHaveTextContent('fiyat kesinleşmedi (provisional)');
    expect(afis).toHaveTextContent('PDF oluşturulamaz');
    expect(screen.queryByRole('checkbox')).toBeNull(); // kullanıcı onayı yolu yok
    expect(screen.queryByTestId('price-final-badge')).toBeNull();
  });

  it('kaynağı doğrulanmamış fiyat da TASLAK işaretlenir', () => {
    render(<PriceDraftBanner readiness={hazirlik(null)} />);
    expect(screen.getByTestId('price-draft-banner')).toHaveTextContent('fiyat doğrulanmadı');
  });

  it('sunucuda kesin doğrulanmış fiyatta TASLAK işareti yoktur', () => {
    render(<PriceDraftBanner readiness={hazirlik(kesin)} period="2099-01" />);
    expect(screen.queryByTestId('price-draft-banner')).toBeNull();
    expect(screen.getByTestId('price-final-badge')).toHaveTextContent('2099-01');
  });

  it('elle değiştirilen fiyatta "Kayıtlı dönem fiyatına dön" düğmesi çalışır', () => {
    const geriDon = vi.fn();
    render(
      <PriceDraftBanner readiness={hazirlik(kesin, { valuesEditedByUser: true })} onRestore={geriDon} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Kayıtlı dönem fiyatına dön' }));
    expect(geriDon).toHaveBeenCalledTimes(1);
  });
});

describe('YekdemModeSelector', () => {
  it('yalnız dahil ve hariç seçeneklerini sunar (muaf yok); seçim yoksa hiçbiri işaretli değildir', () => {
    render(<YekdemModeSelector value={null} onChange={vi.fn()} />);
    const secenekler = screen.getAllByRole('radio') as HTMLInputElement[];
    expect(secenekler.map((s) => s.value)).toEqual(['included', 'excluded']);
    expect(secenekler.every((s) => !s.checked)).toBe(true);
    expect(screen.queryByLabelText('Muaf')).toBeNull();
    expect(screen.getByText('(seçilmedi)')).toBeInTheDocument();
  });

  it('hariç seçimi ayrı değer olarak bildirilir', () => {
    const degisti = vi.fn();
    render(<YekdemModeSelector value="included" onChange={degisti} />);
    fireEvent.click(screen.getByLabelText('Hariç'));
    expect(degisti.mock.calls.map((c) => c[0])).toEqual(['excluded']);
    expect((screen.getByLabelText('Dahil') as HTMLInputElement).checked).toBe(true);
  });
});
