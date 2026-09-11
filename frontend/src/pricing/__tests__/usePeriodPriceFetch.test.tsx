import { describe, it, expect, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { usePeriodPriceFetch, buildPeriodPricesUrl } from '../usePeriodPriceFetch';

// Fiyat Doğruluğu Faz 1 — dönem yarışı: gecikmiş yanıt yeni dönemi EZMEZ; dönem
// değişiminde ve hatada önceki fiyat kullanılmaz; EPİAŞ otomatik çekimi istenmez.

type Ertelenmis = { promise: Promise<Response>; resolve: (r: Response) => void };

function ertelenmis(): Ertelenmis {
  let resolve!: (r: Response) => void;
  const promise = new Promise<Response>((res) => { resolve = res; });
  return { promise, resolve };
}

function yanit(period: string, ptf: number | null, ok = true): Response {
  return {
    ok,
    status: ok ? 200 : 500,
    json: async () => (ok
      ? { period, ptf_tl_per_mwh: ptf, yekdem_tl_per_mwh: null, source: ptf ? 'db' : 'not_found',
          source_description: '', weighted_ptf_tl_per_mwh: ptf }
      : { detail: 'sunucu hatası' }),
  } as unknown as Response;
}

function kurulum() {
  const istekler: Record<string, Ertelenmis> = {};
  const fetchImpl = vi.fn((url: string) => {
    const donem = /prices\/(\d{4}-\d{2})/.exec(url)![1];
    istekler[donem] = ertelenmis();
    return istekler[donem].promise;
  });
  const cb = { onStart: vi.fn(), onResult: vi.fn(), onError: vi.fn(), onSettled: vi.fn() };
  return { istekler, fetchImpl, cb };
}

describe('buildPeriodPricesUrl', () => {
  it('auto_fetch GÖNDERMEZ; profil/tarife/firma sorguya girer', () => {
    const url = buildPeriodPricesUrl('2026-07', 'duz', 'Sanayi OG', 'cansu');
    expect(url).toContain('/api/epias/prices/2026-07?');
    expect(url).not.toContain('auto_fetch');
    expect(url).toContain('profile=duz');
    expect(url).toContain('tariff_group=Sanayi+OG');
    expect(url).toContain('customer_id=cansu');
  });
});

describe('usePeriodPriceFetch', () => {
  it('dönem değişince önce temizler; gecikmiş eski yanıt yeni dönemi ezmez', async () => {
    const { istekler, fetchImpl, cb } = kurulum();
    const { rerender } = renderHook(
      ({ period }: { period: string }) =>
        usePeriodPriceFetch({ enabled: true, period, profile: 'duz', ...cb,
                              fetchImpl: fetchImpl as unknown as typeof fetch }),
      { initialProps: { period: '2026-06' } },
    );
    expect(cb.onStart).toHaveBeenCalledTimes(1);

    rerender({ period: '2026-07' });
    expect(cb.onStart).toHaveBeenCalledTimes(2); // yeni dönem: önceki fiyat temizlenir

    // Yarış: YENİ dönem önce, ESKİ dönem SONRA yanıt verir.
    await act(async () => { istekler['2026-07'].resolve(yanit('2026-07', 2699.61)); });
    await act(async () => { istekler['2026-06'].resolve(yanit('2026-06', 1240.16)); });

    await waitFor(() => expect(cb.onResult).toHaveBeenCalledTimes(1));
    expect(cb.onResult.mock.calls[0][0].period).toBe('2026-07');
    expect(cb.onError).not.toHaveBeenCalled();
  });

  it('hata → onError; sonuç uygulanmaz (eski değer kalmaz, onStart temizledi)', async () => {
    const { istekler, fetchImpl, cb } = kurulum();
    renderHook(() =>
      usePeriodPriceFetch({ enabled: true, period: '2026-08', profile: 'duz', ...cb,
                            fetchImpl: fetchImpl as unknown as typeof fetch }));
    await act(async () => { istekler['2026-08'].resolve(yanit('2026-08', null, false)); });
    await waitFor(() => expect(cb.onError).toHaveBeenCalledWith('sunucu hatası'));
    expect(cb.onResult).not.toHaveBeenCalled();
    expect(cb.onStart).toHaveBeenCalledTimes(1);
    expect(cb.onSettled).toHaveBeenCalled();
  });

  it('kapalıyken (manuel mod değil / dönem yok) istek atılmaz', () => {
    const { fetchImpl, cb } = kurulum();
    renderHook(() =>
      usePeriodPriceFetch({ enabled: false, period: '2026-08', profile: 'duz', ...cb,
                            fetchImpl: fetchImpl as unknown as typeof fetch }));
    renderHook(() =>
      usePeriodPriceFetch({ enabled: true, period: '', profile: 'duz', ...cb,
                            fetchImpl: fetchImpl as unknown as typeof fetch }));
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(cb.onStart).not.toHaveBeenCalled();
  });

  it('refreshKey değişince aynı dönem yeniden çekilir (kayıt sonrası kaynak tazelenir)', async () => {
    const { istekler, fetchImpl, cb } = kurulum();
    const { rerender } = renderHook(
      ({ k }: { k: number }) =>
        usePeriodPriceFetch({ enabled: true, period: '2026-07', profile: 'duz', refreshKey: k, ...cb,
                              fetchImpl: fetchImpl as unknown as typeof fetch }),
      { initialProps: { k: 0 } },
    );
    rerender({ k: 1 });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    await act(async () => { istekler['2026-07'].resolve(yanit('2026-07', 2699.61)); });
    await waitFor(() => expect(cb.onResult).toHaveBeenCalledTimes(1));
  });
});
